"""
Scraper de promoções da Amazon Brasil.

Coleta ofertas da página de deals, ofertas do dia e busca.
Implementa parsing robusto do HTML da Amazon com múltiplos
seletores CSS para lidar com variações de layout.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper


class AmazonScraper(BaseScraper):
    """Scraper especializado para a Amazon Brasil."""

    STORE = Store.AMAZON
    NAME = "amazon"
    BASE_URL = "https://www.amazon.com.br"

    # Páginas de ofertas
    _DEALS_URL = "https://www.amazon.com.br/deals"
    _TODAYS_DEALS = "https://www.amazon.com.br/gp/goldbox"
    _LIGHTNING_DEALS = "https://www.amazon.com.br/gp/deals"
    _BEST_SELLERS = "https://www.amazon.com.br/bestsellers"

    async def _scrape(self) -> list[Product]:
        """Coleta promoções da Amazon via múltiplas páginas."""
        products: list[Product] = []

        # Estratégia 1: Página de ofertas do dia
        deals = await self._scrape_deals_page()
        products.extend(deals)

        # Estratégia 2: Ofertas relâmpago
        lightning = await self._scrape_lightning_deals()
        products.extend(lightning)

        # Estratégia 3: Busca por ofertas
        if len(products) < 5:
            search_deals = await self._scrape_search_deals()
            products.extend(search_deals)

        return products

    async def _scrape_deals_page(self) -> list[Product]:
        """Coleta da página principal de ofertas."""
        products = []

        try:
            headers = {
                "Referer": "https://www.amazon.com.br/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }

            html = await self._http.fetch(self._DEALS_URL, headers=headers)
            if not html:
                html = await self._http.fetch(self._TODAYS_DEALS, headers=headers)

            if not html:
                self._logger.warning("Amazon: nao conseguiu acessar pagina de ofertas")
                return products

            soup = BeautifulSoup(html, "html.parser")

            # Seletores para cards de ofertas na Amazon
            deal_selectors = [
                "div[data-testid='deal-card']",
                ".DealGridItem",
                ".dealCard",
                "[class*='DealCard']",
                ".octopus-dlp-asin-section",
                ".a-cardui",
            ]

            for selector in deal_selectors:
                cards = soup.select(selector)
                if cards:
                    self._logger.debug(
                        f"Amazon: encontrou {len(cards)} cards com seletor '{selector}'"
                    )
                    for card in cards[:25]:
                        product = self._parse_deal_card(card)
                        if product:
                            products.append(product)
                    break

            # Fallback: busca links de produtos genéricos
            if not products:
                products = self._parse_product_links(soup)

            self._logger.info(f"Amazon Deals: {len(products)} ofertas coletadas")

        except Exception as e:
            self._logger.warning(f"Erro na pagina de ofertas Amazon: {e}")

        return products

    def _parse_deal_card(self, card) -> Optional[Product]:
        """Parseia um card de oferta da Amazon."""
        try:
            # Extrai título
            title = None
            title_selectors = [
                "[class*='deal-title']",
                "[class*='DealContent'] div",
                ".a-truncate-full",
                ".a-text-normal",
                "span[class*='title']",
                "h2",
                ".a-link-normal span",
            ]
            for sel in title_selectors:
                el = card.select_one(sel)
                if el and len(el.get_text(strip=True)) > 10:
                    title = el.get_text(strip=True)
                    break

            if not title:
                # Tenta pegar qualquer texto significativo
                texts = [t.strip() for t in card.stripped_strings if len(t.strip()) > 15]
                title = texts[0] if texts else None

            if not title:
                return None

            # Extrai link
            link = None
            link_el = card.select_one("a[href*='/dp/'], a[href*='/gp/'], a[href*='amazon.com.br']")
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

            # Limpa parâmetros de tracking do link
            link = self._clean_amazon_link(link)

            # Extrai preços
            price = None
            original_price = None
            discount_pct = None

            # Preço atual
            price_selectors = [
                ".a-price .a-offscreen",
                "[class*='price'] .a-offscreen",
                ".a-color-price",
                "[data-a-color='price'] .a-offscreen",
                ".dealPriceText",
            ]
            for sel in price_selectors:
                el = card.select_one(sel)
                if el:
                    price = self._parse_price(el.get_text(strip=True))
                    if price:
                        break

            # Preço original (riscado)
            original_selectors = [
                ".a-text-strike",
                "[data-a-strike='true'] .a-offscreen",
                ".a-price[data-a-strike] .a-offscreen",
                "[class*='basisPrice'] .a-offscreen",
            ]
            for sel in original_selectors:
                el = card.select_one(sel)
                if el:
                    original_price = self._parse_price(el.get_text(strip=True))
                    if original_price:
                        break

            # Desconto
            discount_el = card.select_one(
                "[class*='discount'], [class*='savings'], .savingsPercentage"
            )
            if discount_el:
                discount_text = discount_el.get_text(strip=True)
                discount_match = re.search(r"(\d+)\s*%", discount_text)
                if discount_match:
                    discount_pct = float(discount_match.group(1))

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear card Amazon: {e}")
            return None

    def _parse_product_links(self, soup: BeautifulSoup) -> list[Product]:
        """Fallback: extrai produtos de links genéricos."""
        products = []
        seen_links = set()

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

                # Tenta extrair título do link ou de elementos próximos
                title = link_el.get_text(strip=True)
                if len(title) < 10:
                    parent = link_el.parent
                    if parent:
                        title = parent.get_text(strip=True)[:200]

                if len(title) < 10:
                    continue

                # Tenta encontrar preço próximo ao link
                price = None
                parent = link_el.find_parent(class_=re.compile(r"card|item|product|deal", re.I))
                if parent:
                    price_el = parent.select_one(".a-price .a-offscreen, .a-color-price")
                    if price_el:
                        price = self._parse_price(price_el.get_text(strip=True))

                products.append(Product(
                    title=self._clean_title(title[:150]),
                    link=clean_link,
                    store=self.STORE,
                    price=price,
                ))

            except Exception:
                continue

        return products

    async def _scrape_lightning_deals(self) -> list[Product]:
        """Coleta ofertas relâmpago."""
        products = []

        try:
            headers = {
                "Referer": "https://www.amazon.com.br/deals",
            }
            html = await self._http.fetch(self._LIGHTNING_DEALS, headers=headers)
            if not html:
                return products

            soup = BeautifulSoup(html, "html.parser")
            products = self._parse_product_links(soup)

        except Exception as e:
            self._logger.warning(f"Erro nas ofertas relampago Amazon: {e}")

        return products

    async def _scrape_search_deals(self) -> list[Product]:
        """Busca ofertas via search com filtro de desconto."""
        products = []

        try:
            # Busca com filtro de ofertas ativas
            url = (
                "https://www.amazon.com.br/s?"
                "i=deals&deal_type=LIGHTNING_DEAL&rh=p_n_deal_type%3A23530826011"
            )
            html = await self._http.fetch(url)
            if not html:
                return products

            soup = BeautifulSoup(html, "html.parser")

            for result in soup.select("[data-component-type='s-search-result']")[:20]:
                product = self._parse_search_result(result)
                if product:
                    products.append(product)

        except Exception as e:
            self._logger.debug(f"Erro na busca Amazon: {e}")

        return products

    def _parse_search_result(self, result) -> Optional[Product]:
        """Parseia um resultado de busca da Amazon."""
        try:
            # Título
            title_el = result.select_one("h2 a span, h2 span")
            if not title_el:
                return None
            title = title_el.get_text(strip=True)

            # Link
            link_el = result.select_one("h2 a[href]")
            if not link_el:
                return None
            href = link_el.get("href", "")
            if href.startswith("/"):
                href = f"https://www.amazon.com.br{href}"
            link = self._clean_amazon_link(href)

            # Preço
            price_el = result.select_one(".a-price .a-offscreen")
            price = self._parse_price(price_el.get_text(strip=True)) if price_el else None

            # Preço original
            orig_el = result.select_one(
                ".a-price[data-a-strike] .a-offscreen, .a-text-price .a-offscreen"
            )
            original_price = self._parse_price(orig_el.get_text(strip=True)) if orig_el else None

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
            )
        except Exception:
            return None

    def _clean_amazon_link(self, url: str) -> str:
        """Remove parâmetros de tracking e normaliza link da Amazon."""
        # Extrai o ASIN e reconstrói o link limpo
        asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
        if asin_match:
            return f"https://www.amazon.com.br/dp/{asin_match.group(1)}"

        # Remove query params se não encontrou ASIN
        return url.split("?")[0].split("ref=")[0]
