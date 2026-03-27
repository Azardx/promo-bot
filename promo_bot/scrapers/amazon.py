"""
Scraper da Amazon Brasil via página de ofertas.

A Amazon possui proteção anti-bot robusta, mas a página de ofertas
do dia pode ser acessada com headers adequados. Este scraper usa
parsing HTML com múltiplos fallbacks.

CORREÇÕES v2.1:
- Filtro de conteúdo digital (filmes, séries, livros, Kindle, Prime Video)
- Extração de imagem melhorada com fallbacks múltiplos
- Extração de cupons quando disponíveis
- Normalização de links com tag de afiliado
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class AmazonScraper(BaseScraper):
    """Scraper da Amazon Brasil via página de ofertas."""

    STORE = Store.AMAZON
    NAME = "amazon"
    BASE_URL = "https://www.amazon.com.br"

    # Páginas de ofertas da Amazon
    DEALS_URLS = [
        "https://www.amazon.com.br/deals",
        "https://www.amazon.com.br/gp/goldbox",
    ]

    # Headers específicos para Amazon
    AMAZON_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.amazon.com.br/",
        "Cache-Control": "no-cache",
    }

    # Palavras-chave que indicam conteúdo digital (filmes, séries, livros, etc.)
    DIGITAL_CONTENT_KEYWORDS = [
        # Filmes e séries
        "temporada", "temporadas", "episódio", "episodio", "episódios",
        "série", "serie", "séries", "series",
        "filme", "filmes", "movie", "movies",
        "prime video", "primevideo", "amazon video",
        "assistir", "streaming", "legendado", "dublado",
        "imdb", "elenco:", "diretor:", "direção:",
        "drama", "comédia", "comedia", "suspense", "terror",
        "documentário", "documentario", "animação", "animacao",
        "nova série", "nova serie",
        # Livros e Kindle
        "kindle", "ebook", "e-book", "livro digital",
        "edição kindle", "edicao kindle", "formato kindle",
        "audiobook", "audiolivro", "audible",
        "brochura", "capa dura", "capa comum",
        "editora", "autor:", "páginas",
        # Música
        "amazon music", "music unlimited",
        "álbum", "album", "playlist",
        # Outros digitais
        "assinatura", "plano mensal", "plano anual",
        "período de teste", "periodo de teste",
        "teste gratuito", "grátis por",
    ]

    # Padrões de URL que indicam conteúdo digital
    DIGITAL_URL_PATTERNS = [
        r"/dp/B[0-9A-Z]{9}.*?/.*?(video|movie|serie|film|kindle|book|music|audible)",
        r"primevideo\.com",
        r"amazon\.com\.br/gp/video",
        r"amazon\.com\.br/kindle",
        r"amazon\.com\.br/music",
    ]

    # Categorias da Amazon que são conteúdo digital
    DIGITAL_CATEGORIES = [
        "prime-video", "instant-video", "digital-text", "digital-music",
        "audible", "kindle-store", "books", "movies-tv",
    ]

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas da Amazon Brasil."""
        products: list[Product] = []

        for url in self.DEALS_URLS:
            try:
                page_products = await self._scrape_page(url)
                products.extend(page_products)
                if products:
                    break
            except Exception as e:
                self._logger.warning(f"Erro ao acessar {url}: {e}")

        # Fallback: busca por ofertas relâmpago
        if not products:
            products = await self._scrape_search_deals()

        # Filtra conteúdo digital
        filtered = [p for p in products if not self._is_digital_content(p)]
        removed = len(products) - len(filtered)
        if removed > 0:
            self._logger.info(f"Amazon: {removed} itens de conteudo digital removidos")

        self._logger.info(f"Amazon: {len(filtered)} ofertas coletadas")
        return filtered

    def _is_digital_content(self, product: Product) -> bool:
        """Verifica se o produto é conteúdo digital (filme, série, livro, etc.)."""
        title_lower = product.title.lower()
        link_lower = product.link.lower()

        # Verifica palavras-chave no título
        for keyword in self.DIGITAL_CONTENT_KEYWORDS:
            if keyword in title_lower:
                self._logger.debug(
                    f"Conteudo digital detectado (keyword '{keyword}'): {product.title[:50]}"
                )
                return True

        # Verifica padrões na URL
        for pattern in self.DIGITAL_URL_PATTERNS:
            if re.search(pattern, link_lower, re.IGNORECASE):
                self._logger.debug(
                    f"Conteudo digital detectado (URL): {product.link}"
                )
                return True

        # Verifica se o título é muito curto e parece nome de filme/série
        # (títulos de produtos reais geralmente são mais descritivos)
        if len(product.title) < 40 and not product.price:
            # Título curto sem preço = provavelmente conteúdo digital
            words = product.title.split()
            if len(words) <= 4:
                self._logger.debug(
                    f"Possivel conteudo digital (titulo curto sem preco): {product.title}"
                )
                return True

        return False

    async def _scrape_page(self, url: str) -> list[Product]:
        """Coleta ofertas de uma página da Amazon."""
        products: list[Product] = []

        html = await self._http.fetch(url, headers=self.AMAZON_HEADERS)
        if not html:
            self._logger.warning(f"Falha ao acessar {url}")
            return products

        soup = BeautifulSoup(html, "html.parser")

        # Seletores para cards de ofertas na Amazon
        deal_selectors = [
            "div[data-testid='deal-card']",
            ".DealGridItem",
            "[class*='DealCard']",
            ".octopus-dlp-asin-section",
            ".a-cardui",
        ]

        for selector in deal_selectors:
            cards = soup.select(selector)
            if cards:
                self._logger.debug(
                    f"Amazon: encontrou {len(cards)} cards com '{selector}'"
                )
                for card in cards[:25]:
                    product = self._parse_deal_card(card)
                    if product:
                        products.append(product)
                break

        # Fallback: busca links de produtos genéricos
        if not products:
            products = self._parse_product_links(soup)

        return products

    def _parse_deal_card(self, card) -> Optional[Product]:
        """Parseia um card de oferta da Amazon."""
        try:
            # Título
            title = None
            for sel in [
                "[class*='deal-title']",
                "[class*='DealContent'] div",
                ".a-truncate-full",
                ".a-text-normal",
                "span[class*='title']",
                "h2",
            ]:
                el = card.select_one(sel)
                if el and len(el.get_text(strip=True)) > 10:
                    title = el.get_text(strip=True)
                    break

            if not title:
                texts = [t.strip() for t in card.stripped_strings if len(t.strip()) > 15]
                title = texts[0] if texts else None

            if not title:
                return None

            # Link
            link = None
            link_el = card.select_one(
                "a[href*='/dp/'], a[href*='/gp/'], a[href*='amazon.com.br']"
            )
            if not link_el:
                link_el = card.select_one("a[href]")

            if link_el:
                href = link_el.get("href", "")
                if href.startswith("/"):
                    link = f"https://www.amazon.com.br{href}"
                elif href.startswith("http"):
                    link = href

            if not link:
                return None

            link = self._clean_amazon_link(link)

            # Preço
            price = None
            for sel in [
                ".a-price .a-offscreen",
                "[class*='price'] .a-offscreen",
                ".a-color-price",
                ".dealPriceText",
            ]:
                el = card.select_one(sel)
                if el:
                    price = self._parse_price(el.get_text(strip=True))
                    if price:
                        break

            # Preço original
            original_price = None
            for sel in [
                ".a-text-strike",
                "[data-a-strike='true'] .a-offscreen",
                "[class*='basisPrice'] .a-offscreen",
            ]:
                el = card.select_one(sel)
                if el:
                    original_price = self._parse_price(el.get_text(strip=True))
                    if original_price:
                        break

            # Desconto
            discount_pct = None
            discount_el = card.select_one(
                "[class*='discount'], [class*='savings'], .savingsPercentage"
            )
            if discount_el:
                disc_match = re.search(r"(\d+)\s*%", discount_el.get_text())
                if disc_match:
                    discount_pct = float(disc_match.group(1))

            # Imagem — busca mais ampla
            image = self._extract_image(card)

            # Cupom
            coupon = self._extract_coupon(card)

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image,
                coupon_code=coupon,
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear card Amazon: {e}")
            return None

    def _extract_image(self, card) -> str:
        """Extrai URL da imagem do produto com múltiplos fallbacks."""
        # Seletores de imagem em ordem de prioridade
        img_selectors = [
            "img[src*='images-amazon']",
            "img[src*='m.media-amazon']",
            "img[data-src*='images-amazon']",
            "img[data-src*='m.media-amazon']",
            "img[src*='ssl-images-amazon']",
            "img.a-dynamic-image",
            "img[data-a-dynamic-image]",
            "img[src]",
        ]

        for sel in img_selectors:
            img_el = card.select_one(sel)
            if img_el:
                # Tenta src primeiro, depois data-src
                src = img_el.get("src", "") or img_el.get("data-src", "")
                if src and "amazon" in src and not self._is_placeholder(src):
                    # Tenta obter versão em alta resolução
                    return self._get_high_res_image(src)

                # Tenta data-a-dynamic-image (JSON com múltiplas resoluções)
                dynamic = img_el.get("data-a-dynamic-image", "")
                if dynamic:
                    try:
                        import json
                        images = json.loads(dynamic)
                        if images:
                            # Pega a maior resolução
                            best_url = max(images.keys(), key=lambda u: sum(images[u]))
                            if best_url and "amazon" in best_url:
                                return best_url
                    except Exception:
                        pass

        return ""

    def _is_placeholder(self, url: str) -> bool:
        """Verifica se a URL é uma imagem placeholder."""
        placeholders = [
            "transparent-pixel",
            "grey-pixel",
            "loading-",
            "spinner",
            "1x1",
            "pixel.",
            "blank.",
            "no-img",
        ]
        url_lower = url.lower()
        return any(p in url_lower for p in placeholders)

    def _get_high_res_image(self, url: str) -> str:
        """Tenta obter versão em alta resolução da imagem."""
        # Amazon usa sufixos como _AC_UL320_ para tamanho
        # Remove o sufixo de tamanho para obter resolução maior
        high_res = re.sub(
            r'\._[A-Z]{2}_[A-Z]{2}\d+_\.',
            '._AC_SL500_.',
            url,
        )
        if high_res != url:
            return high_res

        # Tenta substituir tamanhos comuns
        high_res = re.sub(r'_SX\d+_', '_SX500_', url)
        high_res = re.sub(r'_SY\d+_', '_SY500_', high_res)
        return high_res

    def _extract_coupon(self, card) -> Optional[str]:
        """Extrai cupom de desconto do card da Amazon."""
        # Amazon mostra cupons como "Aplique cupom de X%"
        coupon_selectors = [
            "[class*='coupon']",
            "[data-coupon]",
            "[class*='Coupon']",
            "span.a-color-success",
        ]

        for sel in coupon_selectors:
            el = card.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if "cupom" in text.lower() or "coupon" in text.lower():
                    # Extrai percentual ou valor
                    match = re.search(r"(\d+)\s*%", text)
                    if match:
                        return f"CUPOM {match.group(1)}% OFF"
                    match = re.search(r"R\$\s*([\d.,]+)", text)
                    if match:
                        return f"CUPOM R${match.group(1)} OFF"
                    return "APLICAR CUPOM"

        return None

    def _parse_product_links(self, soup: BeautifulSoup) -> list[Product]:
        """Fallback: extrai produtos de links genéricos."""
        products = []
        seen_links: set[str] = set()

        for link_el in soup.select("a[href*='/dp/']")[:30]:
            try:
                href = link_el.get("href", "")
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.amazon.com.br{href}"

                clean_link = self._clean_amazon_link(href)
                if clean_link in seen_links:
                    continue
                seen_links.add(clean_link)

                title = link_el.get_text(strip=True)
                if len(title) < 10:
                    parent = link_el.parent
                    if parent:
                        title = parent.get_text(strip=True)[:200]
                if len(title) < 10:
                    continue

                price = None
                image = ""
                parent = link_el.find_parent(
                    class_=re.compile(r"card|item|product|deal", re.I)
                )
                if parent:
                    price_el = parent.select_one(
                        ".a-price .a-offscreen, .a-color-price"
                    )
                    if price_el:
                        price = self._parse_price(price_el.get_text(strip=True))

                    # Tenta extrair imagem do parent
                    image = self._extract_image(parent)

                products.append(Product(
                    title=self._clean_title(title[:150]),
                    link=clean_link,
                    store=self.STORE,
                    price=price,
                    image_url=image,
                ))
            except Exception:
                continue

        return products

    async def _scrape_search_deals(self) -> list[Product]:
        """Busca ofertas via search com filtro de desconto."""
        products = []

        try:
            url = (
                "https://www.amazon.com.br/s?"
                "i=deals&deal_type=LIGHTNING_DEAL&rh=p_n_deal_type%3A23530826011"
            )
            html = await self._http.fetch(url, headers=self.AMAZON_HEADERS)
            if not html:
                return products

            soup = BeautifulSoup(html, "html.parser")

            for result in soup.select("[data-component-type='s-search-result']")[:15]:
                product = self._parse_search_result(result)
                if product:
                    products.append(product)

        except Exception as e:
            self._logger.debug(f"Erro na busca Amazon: {e}")

        return products

    def _parse_search_result(self, result) -> Optional[Product]:
        """Parseia um resultado de busca da Amazon."""
        try:
            title_el = result.select_one("h2 a span, h2 span")
            if not title_el:
                return None
            title = title_el.get_text(strip=True)

            link_el = result.select_one("h2 a[href]")
            if not link_el:
                return None
            href = link_el.get("href", "")
            if href.startswith("/"):
                href = f"https://www.amazon.com.br{href}"
            link = self._clean_amazon_link(href)

            price_el = result.select_one(".a-price .a-offscreen")
            price = self._parse_price(price_el.get_text(strip=True)) if price_el else None

            orig_el = result.select_one(
                ".a-price[data-a-strike] .a-offscreen, .a-text-price .a-offscreen"
            )
            original_price = (
                self._parse_price(orig_el.get_text(strip=True)) if orig_el else None
            )

            # Imagem
            image = self._extract_image(result)

            # Cupom
            coupon = self._extract_coupon(result)

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                image_url=image,
                coupon_code=coupon,
            )
        except Exception:
            return None

    def _clean_amazon_link(self, url: str) -> str:
        """Remove parâmetros de tracking e normaliza link da Amazon."""
        asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
        if asin_match:
            return f"https://www.amazon.com.br/dp/{asin_match.group(1)}"
        return url.split("?")[0].split("ref=")[0]
