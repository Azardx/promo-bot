"""
Scraper da Terabyte Shop — PromoBot v2.3+

CORREÇÕES v2.3+:
- Adicionado ao CURL_CFFI_DOMAINS no http_client (TLS fingerprinting)
- Seletores CSS atualizados para o layout atual do site
- Estratégia dupla: página de promoções + busca por keywords
- Extração de cupom via badges e texto
- Limpeza de URLs de tracking
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class TerabyteScraper(BaseScraper):
    """Scraper da Terabyte Shop via HTML (requer curl_cffi)."""

    STORE    = Store.TERABYTE
    NAME     = "terabyte"
    BASE_URL = "https://www.terabyteshop.com.br"

    # URLs de coleta
    _URLS: list[str] = [
        "https://www.terabyteshop.com.br/promocoes",
        "https://www.terabyteshop.com.br/hardware/processadores",
        "https://www.terabyteshop.com.br/hardware/placas-de-video-vga",
        "https://www.terabyteshop.com.br/hardware/ssd-e-hd/ssd",
    ]

    _HEADERS: dict[str, str] = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language":         "pt-BR,pt;q=0.9",
        "Referer":                 "https://www.terabyteshop.com.br/",
        "Cache-Control":           "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }

    # Seletores por especificidade — testados no layout atual
    _CARD_SELECTORS: tuple[str, ...] = (
        ".p-card",
        ".product-item",
        ".commerce_columns_item_inner",
        ".p-container",
        ".pbox",
        "div[class*='product']",
    )

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    # ── Ponto de entrada ──────────────────────────────────────────────────────

    async def _scrape(self) -> list[Product]:
        products: list[Product] = []
        seen_links: set[str]    = set()

        for url in self._URLS:
            html = await self._http.fetch(url, headers=self._HEADERS)
            if not html:
                self._logger.debug(f"Terabyte: falha ao acessar {url}")
                continue

            page_products = self._parse_page(html, url)
            for p in page_products:
                clean = p.link.split("?")[0]
                if clean not in seen_links:
                    p.link = clean
                    products.append(p)
                    seen_links.add(clean)

            self._logger.debug(
                f"Terabyte {url.split('/')[-1]}: {len(page_products)} produtos"
            )
            if len(products) >= 30:
                break

        self._logger.info(f"Terabyte: {len(products)} ofertas coletadas")
        return products

    # ── Parser de página ──────────────────────────────────────────────────────

    def _parse_page(self, html: str, page_url: str) -> list[Product]:
        """Extrai todos os produtos de uma página HTML."""
        soup  = BeautifulSoup(html, "html.parser")
        cards = self._find_cards(soup)

        if not cards:
            self._logger.debug(
                f"Terabyte: nenhum card encontrado em {page_url}"
            )
            return []

        products: list[Product] = []
        for card in cards[:35]:
            p = self._parse_card(card)
            if p:
                products.append(p)
        return products

    def _find_cards(self, soup: BeautifulSoup) -> list[Tag]:
        """Localiza os cards de produto usando seletores em cascata."""
        for sel in self._CARD_SELECTORS:
            cards = soup.select(sel)
            if len(cards) >= 3:
                return cards  # type: ignore[return-value]

        # Último recurso: qualquer link de produto
        return soup.select("a[href*='/produto/']")  # type: ignore[return-value]

    def _parse_card(self, card: Tag) -> Optional[Product]:
        """Extrai dados de um card de produto."""
        try:
            # ── Link ─────────────────────────────────────────────────────
            if card.name == "a":
                link_el = card
            else:
                link_el = card.select_one("a[href*='/produto/']")
            if not link_el:
                return None

            href = link_el.get("href", "")
            if not href:
                return None
            if not href.startswith("http"):
                href = f"{self.BASE_URL}{href}" if href.startswith("/") else None
            if not href:
                return None

            # ── Título ────────────────────────────────────────────────────
            title = ""
            for sel in (
                ".prod-name", ".product-name", ".p-title",
                "[class*='prod-name']", "[class*='title']",
                "h2", "h3", "h4",
            ):
                el = card.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    if len(title) >= 10:
                        break
            if not title:
                # Tenta atributo title/alt do link
                title = link_el.get("title") or link_el.get_text(strip=True)
            if not title or len(title) < 10:
                return None

            # ── Preço à vista ─────────────────────────────────────────────
            price = self._extract_price(card)

            # ── Preço original (riscado) ──────────────────────────────────
            original_price = self._extract_original_price(card)
            if original_price and price and original_price <= price:
                original_price = None

            # ── Desconto ──────────────────────────────────────────────────
            discount_pct = self._extract_discount(card, price, original_price)

            # ── Imagem ────────────────────────────────────────────────────
            image_url = self._extract_image(card)

            # ── Cupom ─────────────────────────────────────────────────────
            coupon = self._extract_coupon(card)

            # ── Frete grátis ──────────────────────────────────────────────
            card_text     = card.get_text(" ", strip=True).lower()
            free_shipping = "frete grátis" in card_text or "frete gratis" in card_text

            return Product(
                title=self._clean_title(title),
                link=href.split("?")[0],
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                coupon_code=coupon,
                free_shipping=free_shipping,
                extra={"source": "terabyte_html"},
            )

        except Exception as exc:
            self._logger.debug(f"Terabyte parse_card falhou: {exc}")
            return None

    # ── Helpers de extração ───────────────────────────────────────────────────

    def _extract_price(self, card: Tag) -> Optional[float]:
        """Extrai preço à vista (menor preço disponível no card)."""
        selectors = (
            ".prod-new-price span",
            ".prod-new-price",
            ".val-prod",
            ".price-new",
            "[class*='price-new']",
            "[class*='new-price']",
            ".destaque",
        )
        for sel in selectors:
            el = card.select_one(sel)
            if el:
                price = self._parse_price(el.get_text(strip=True))
                if price and price > 0:
                    return price
        return None

    def _extract_original_price(self, card: Tag) -> Optional[float]:
        """Extrai preço original (riscado)."""
        selectors = (
            ".prod-old-price span",
            ".prod-old-price",
            "strike",
            "s",
            "[class*='old-price']",
            "[class*='price-old']",
        )
        for sel in selectors:
            el = card.select_one(sel)
            if el:
                price = self._parse_price(el.get_text(strip=True))
                if price and price > 0:
                    return price
        return None

    def _extract_discount(
        self,
        card: Tag,
        price: Optional[float],
        original_price: Optional[float],
    ) -> Optional[float]:
        """Extrai desconto do badge ou calcula a partir dos preços."""
        # Badge de desconto
        for sel in (
            ".prod-discount", ".p-discount",
            "[class*='discount']", ".badge-desconto",
        ):
            el = card.select_one(sel)
            if el:
                m = re.search(r"(\d+)\s*%", el.get_text())
                if m:
                    return float(m.group(1))

        # Calcula se tiver os dois preços
        if price and original_price and original_price > price:
            return round((1 - price / original_price) * 100, 1)

        return None

    def _extract_image(self, card: Tag) -> str:
        """Extrai URL de imagem com prioridade para alta resolução."""
        for attr in ("data-src", "src", "data-original"):
            img = card.select_one(f"img[{attr}]")
            if img:
                url = img.get(attr, "")
                if url and not url.startswith("data:"):
                    if url.startswith("//"):
                        url = f"https:{url}"
                    elif url.startswith("/"):
                        url = f"{self.BASE_URL}{url}"
                    return url
        return ""

    def _extract_coupon(self, card: Tag) -> Optional[str]:
        """Extrai código de cupom de badges ou texto do card."""
        for sel in ("[class*='coupon']", "[class*='cupom']", ".badge-cupom"):
            el = card.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                # Tenta extrair código (ex: "Cupom: SHARK20")
                m = re.search(r"(?:cupom|código)\s*:?\s*([A-Z0-9]+)", text, re.I)
                if m:
                    return m.group(1).upper()
                if len(text) >= 3:
                    return text
        return None
