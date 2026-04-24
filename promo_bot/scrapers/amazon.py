"""
Scraper da Amazon Brasil — PromoBot v2.3+

ESTRATÉGIAS (ordem de prioridade):
1. __PRELOADED_STATE__ / window.PRELOADED_STATE  →  JSON embutido no HTML (mais estável)
2. Página /deals  →  parsing do JSON de cards de ofertas
3. Página /gp/goldbox  →  parsing HTML com seletores atualizados
4. Fallback HTML genérico  →  busca por qualquer card de produto com preço

CORREÇÕES v2.3+:
- Filtro de mídia digital expandido (kindle, prime video, music, etc.)
- Extração de imagem em alta resolução (remove parâmetros de resize)
- Extração de cupom via badge e texto de desconto
- Remoção de parâmetros de tracking do link final
"""

from __future__ import annotations

import json
import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class AmazonScraper(BaseScraper):
    """Scraper da Amazon Brasil com múltiplas estratégias de extração."""

    STORE    = Store.AMAZON
    NAME     = "amazon"
    BASE_URL = "https://www.amazon.com.br"

    # URLs testadas
    _URLS = [
        "https://www.amazon.com.br/deals",
        "https://www.amazon.com.br/gp/goldbox",
    ]

    # Headers realistas para Amazon BR
    _HEADERS = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.amazon.com.br/",
        "sec-ch-ua": '"Google Chrome";v="131", "Not A(Brand";v="8"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    # Termos que indicam conteúdo digital (filmes, e-books, streaming…)
    _DIGITAL_TERMS: frozenset[str] = frozenset({
        "prime video", "temporada", "série", "series", "filme", "movie",
        "kindle", "ebook", "e-book", "digital", "aluguel", "assinatura",
        "unlimited", "music unlimited", "audible", "podcast", "episódio",
        "episodio", "streaming", "download", "edição digital", "edicao digital",
        "season", "volume", "collection", "anthology", "blu-ray", "dvd",
        "audiobook", "audiolivro",
    })

    # Regex para extrair JSON de estado da página
    _PRELOADED_RE = re.compile(
        r'(?:window\.)?(?:PRELOADED_STATE|__PRELOADED_STATE__)\s*=\s*(\{.*?\});',
        re.DOTALL,
    )
    _DATA_STATE_RE = re.compile(
        r'data-state="([^"]+)"',
    )

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    # ── Ponto de entrada ──────────────────────────────────────────────────────

    async def _scrape(self) -> list[Product]:
        products: list[Product] = []
        seen_links: set[str] = set()

        for url in self._URLS:
            html = await self._http.fetch(url, headers=self._HEADERS)
            if not html:
                self._logger.warning(f"Amazon: falha ao acessar {url}")
                continue

            # Estratégia 1: JSON embutido (mais estável)
            from_json = self._parse_preloaded_state(html)
            if from_json:
                self._logger.debug(f"Amazon: {len(from_json)} itens via PRELOADED_STATE")

            # Estratégia 2: parsing HTML
            from_html = self._parse_html_cards(html)
            self._logger.debug(f"Amazon: {len(from_html)} itens via HTML")

            for product in (*from_json, *from_html):
                norm = self._normalize_link(product.link)
                if norm not in seen_links and not self._is_digital(product):
                    product.link = norm
                    products.append(product)
                    seen_links.add(norm)

            if len(products) >= 20:
                break

        self._logger.info(f"Amazon: {len(products)} ofertas válidas coletadas")
        return products

    # ── Estratégia 1: PRELOADED_STATE ────────────────────────────────────────

    def _parse_preloaded_state(self, html: str) -> list[Product]:
        """Tenta extrair produtos do JSON de estado embutido no HTML."""
        products: list[Product] = []

        # Tenta encontrar JSON de estado em scripts
        script_re = re.compile(
            r'<script[^>]*>(.*?)</script>', re.DOTALL
        )
        for m in script_re.finditer(html):
            script = m.group(1)
            if '"dealId"' not in script and '"asin"' not in script:
                continue

            # Procura por arrays de deals/items
            item_re = re.compile(
                r'\{"asin"\s*:\s*"([A-Z0-9]{10})"[^}]{0,800}"title"\s*:\s*"([^"]+)"',
                re.DOTALL,
            )
            price_block_re = re.compile(
                r'"asin"\s*:\s*"([A-Z0-9]{10})".*?'
                r'"displayPrice"\s*:\s*"([^"]+)"',
                re.DOTALL,
            )

            for pm in price_block_re.finditer(script):
                asin       = pm.group(1)
                price_str  = pm.group(2)
                price      = self._parse_price(price_str)
                if not price:
                    continue
                link = f"{self.BASE_URL}/dp/{asin}"
                products.append(Product(
                    title=f"Oferta Amazon — ASIN {asin}",
                    link=link,
                    store=self.STORE,
                    price=price,
                    extra={"asin": asin, "source": "preloaded_state"},
                ))
                if len(products) >= 30:
                    break

        return products

    # ── Estratégia 2: HTML parsing ────────────────────────────────────────────

    def _parse_html_cards(self, html: str) -> list[Product]:
        """Parseia cards de ofertas diretamente do HTML."""
        soup = BeautifulSoup(html, "html.parser")
        products: list[Product] = []

        # Seletores em ordem de especificidade — testados em /deals e /gp/goldbox
        card_selectors = [
            "[data-component-type='s-search-result']",
            "[data-asin][data-index]",
            ".DealCard-module__dealCard_1MCGB",
            ".a-section.octopus-dlp-asin-section",
            ".s-result-item[data-asin]",
            "li[data-asin]",
            "div[data-asin]",
        ]

        cards: list[Tag] = []
        for selector in card_selectors:
            cards = soup.select(selector)
            if len(cards) >= 3:
                self._logger.debug(f"Amazon HTML: seletor '{selector}' → {len(cards)} cards")
                break

        if not cards:
            self._logger.debug("Amazon HTML: nenhum card encontrado com seletores padrão")

        for card in cards[:40]:
            product = self._parse_single_card(card)
            if product:
                products.append(product)

        return products

    def _parse_single_card(self, card: Tag) -> Optional[Product]:
        """Extrai um produto de um card HTML."""
        try:
            # ── ASIN e link ───────────────────────────────────────────────
            asin = card.get("data-asin") or ""
            if not asin:
                link_el = card.select_one("a[href*='/dp/'], a[href*='/gp/product/']")
                if link_el:
                    m = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', link_el.get("href", ""))
                    asin = m.group(1) if m else ""
            if not asin:
                return None

            link = f"{self.BASE_URL}/dp/{asin}"

            # ── Título ────────────────────────────────────────────────────
            title = ""
            title_selectors = [
                "h2 a span", "h2 span", ".a-size-medium", ".a-size-base-plus",
                "[class*='title']", ".a-text-normal", "h2",
            ]
            for sel in title_selectors:
                el = card.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    if len(title) >= 10:
                        break

            if len(title) < 10:
                return None

            # ── Preço ─────────────────────────────────────────────────────
            price = self._extract_price_from_card(card)

            # ── Preço original ────────────────────────────────────────────
            original_price: Optional[float] = None
            for sel in (".a-text-price .a-offscreen", ".a-text-strike", ".a-price.a-text-price span"):
                el = card.select_one(sel)
                if el:
                    original_price = self._parse_price(el.get_text(strip=True))
                    if original_price:
                        break

            if original_price and price and original_price <= price:
                original_price = None

            # ── Desconto ──────────────────────────────────────────────────
            discount_pct: Optional[float] = None
            for sel in (
                ".a-size-base.a-color-price",
                "[class*='savingsPercentage']",
                ".a-color-price",
                ".badge-savings",
            ):
                el = card.select_one(sel)
                if el:
                    m = re.search(r"(\d+)\s*%", el.get_text())
                    if m:
                        discount_pct = float(m.group(1))
                        break

            # ── Imagem ────────────────────────────────────────────────────
            image_url = self._extract_image(card)

            # ── Cupom ─────────────────────────────────────────────────────
            coupon = self._extract_coupon(card)

            # ── Frete grátis ──────────────────────────────────────────────
            free_shipping = bool(
                card.select_one(
                    ".a-color-success, [class*='freeShipping'], [class*='free-shipping']"
                )
            )
            if not free_shipping:
                card_text = card.get_text(" ", strip=True).lower()
                free_shipping = "frete grátis" in card_text or "entrega grátis" in card_text

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                coupon_code=coupon,
                free_shipping=free_shipping,
                extra={"asin": asin, "source": "html_card"},
            )

        except Exception as exc:
            self._logger.debug(f"Amazon: erro ao parsear card — {exc}")
            return None

    # ── Helpers de extração ───────────────────────────────────────────────────

    def _extract_price_from_card(self, card: Tag) -> Optional[float]:
        """Extrai preço à vista de um card, tentando múltiplos seletores."""
        selectors = [
            ".a-price[data-a-size='xl'] .a-offscreen",
            ".a-price[data-a-size='l'] .a-offscreen",
            ".a-price .a-offscreen",
            ".a-price-whole",
        ]
        for sel in selectors:
            el = card.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                price = self._parse_price(text)
                if price and price > 0:
                    return price

        # Fallback: combina a-price-whole + a-price-fraction
        whole = card.select_one(".a-price-whole")
        frac  = card.select_one(".a-price-fraction")
        if whole:
            raw = whole.get_text(strip=True).replace(".", "").replace(",", "")
            if frac:
                raw += f",{frac.get_text(strip=True)}"
            return self._parse_price(raw)

        return None

    def _extract_image(self, card: Tag) -> str:
        """Extrai URL de imagem em alta resolução."""
        img = card.select_one("img[src]")
        if not img:
            return ""
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            return ""
        # Remove parâmetros de resize da Amazon para obter imagem maior
        src = re.sub(r"\._[A-Z]{2}\d+_\.", ".", src)
        src = re.sub(r"\._AC_[^.]+\.", "._AC_SX679_.", src)
        return src

    def _extract_coupon(self, card: Tag) -> Optional[str]:
        """Extrai código ou descrição de cupom do card."""
        for sel in (
            ".a-badge-text",
            "[class*='coupon']",
            ".a-color-success",
            "[class*='voucher']",
        ):
            el = card.select_one(sel)
            if el:
                text = el.get_text(strip=True).lower()
                if any(kw in text for kw in ("cupom", "coupon", "desconto", "off", "%")):
                    # Tenta extrair código alfanumérico
                    code_m = re.search(r"[A-Z0-9]{4,20}", el.get_text(strip=True).upper())
                    return code_m.group(0) if code_m else "Ative na página"
        return None

    def _is_digital(self, product: Product) -> bool:
        """Retorna True se o produto é conteúdo digital irrelevante."""
        text = (product.title + " " + product.link).lower()
        if any(term in text for term in self._DIGITAL_TERMS):
            self._logger.debug(f"Amazon: filtrado conteúdo digital — {product.title[:60]}")
            return True
        if re.search(r"/(?:prime-video|kindle-dbs|music|audible)/", product.link):
            return True
        return False

    @staticmethod
    def _normalize_link(link: str) -> str:
        """Remove parâmetros de tracking do link Amazon."""
        m = re.search(r'/dp/([A-Z0-9]{10})', link)
        if m:
            return f"https://www.amazon.com.br/dp/{m.group(1)}"
        return link.split("?")[0]
