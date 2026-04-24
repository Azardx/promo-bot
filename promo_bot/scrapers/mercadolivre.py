"""
Scraper do Mercado Livre — PromoBot v2.3+

ESTRATÉGIAS (ordem de prioridade):
1. API pública de busca com filtro de desconto por categoria de eletrônicos
2. Página de ofertas do dia (HTML) — mais visual/menos estável
3. API de tendências — produtos mais vendidos com desconto

CORREÇÕES v2.3+:
- Substituição de busca genérica por categorias com filtro de desconto real
- Normalização de links (remove parâmetros de tracking)
- Extração de frete Full correta
- Filtragem de produtos sem desconto real
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class MercadoLivreScraper(BaseScraper):
    """Scraper do Mercado Livre com foco em ofertas com desconto real."""

    STORE    = Store.MERCADOLIVRE
    NAME     = "mercadolivre"
    BASE_URL = "https://www.mercadolivre.com.br"

    # API pública — não requer autenticação
    _SEARCH_API = "https://api.mercadolibre.com/sites/MLB/search"

    # Categorias de eletrônicos e tecnologia com alto potencial de desconto
    # Ref: https://api.mercadolibre.com/sites/MLB/categories
    _CATEGORIES: list[tuple[str, str]] = [
        ("MLB1648", "Computação"),
        ("MLB1051", "Áudio e Video"),
        ("MLB1144", "Celulares e Telefones"),
        ("MLB1000", "Eletrônicos"),
        ("MLB1055", "Videogames"),
        ("MLB1574", "Eletrodomésticos"),
    ]

    # Desconto mínimo para aceitar o produto (%)
    _MIN_DISCOUNT_PCT = 10.0

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    # ── Ponto de entrada ──────────────────────────────────────────────────────

    async def _scrape(self) -> list[Product]:
        products: list[Product] = []
        seen_ids: set[str] = set()

        # Estratégia 1: API por categoria com desconto
        for cat_id, cat_name in self._CATEGORIES:
            try:
                cat_products = await self._search_category_with_discount(cat_id)
                added = 0
                for p in cat_products:
                    ml_id = p.extra.get("ml_id", "")
                    if ml_id and ml_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(ml_id)
                        added += 1
                if added:
                    self._logger.debug(f"ML {cat_name}: {added} produtos com desconto")
                if len(products) >= 30:
                    break
            except Exception as exc:
                self._logger.debug(f"ML categoria {cat_id} falhou: {exc}")

        # Estratégia 2: Página HTML de ofertas (fallback)
        if len(products) < 10:
            try:
                html_products = await self._scrape_offers_page()
                for p in html_products:
                    ml_id = p.extra.get("ml_id", p.link)
                    if ml_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(ml_id)
            except Exception as exc:
                self._logger.debug(f"ML HTML fallback falhou: {exc}")

        self._logger.info(f"Mercado Livre: {len(products)} ofertas coletadas")
        return products

    # ── Estratégia 1: API com desconto ────────────────────────────────────────

    async def _search_category_with_discount(self, category_id: str) -> list[Product]:
        """Busca produtos com desconto real em uma categoria via API."""
        params = {
            "category":       category_id,
            "sort":           "price_discount_percentage",  # Maior desconto primeiro
            "limit":          "20",
            "has_promotions": "true",   # Apenas com promoção ativa
        }
        data = await self._http.fetch_json(self._SEARCH_API, params=params)
        if not data or "results" not in data:
            return []

        products: list[Product] = []
        for item in data.get("results", []):
            p = self._parse_api_item(item)
            if p and self._has_real_discount(p):
                products.append(p)

        return products

    # ── Estratégia 2: HTML da página de ofertas ───────────────────────────────

    async def _scrape_offers_page(self) -> list[Product]:
        """Parseia a página de ofertas do dia via HTML."""
        products: list[Product] = []
        url = f"{self.BASE_URL}/ofertas"
        html = await self._http.fetch(url)
        if not html:
            return products

        soup = BeautifulSoup(html, "html.parser")

        # Seletores atualizados para a página de ofertas do ML
        card_selectors = [
            ".andes-card.poly-card",
            "[class*='poly-card']",
            ".promotion-item",
            ".ui-search-result",
            "li[class*='result']",
        ]
        cards = []
        for sel in card_selectors:
            cards = soup.select(sel)
            if len(cards) >= 3:
                break

        for card in cards[:25]:
            p = self._parse_html_card(card)
            if p and self._has_real_discount(p):
                products.append(p)

        return products

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_api_item(self, item: dict) -> Optional[Product]:
        """Converte resposta da API ML em Product."""
        try:
            title = (item.get("title") or "").strip()
            link  = item.get("permalink") or ""
            if not title or not link:
                return None

            price          = float(item.get("price") or 0) or None
            original_price = float(item.get("original_price") or 0) or None

            if original_price and price and original_price <= price:
                original_price = None

            # Imagem em resolução maior (O = original, I = thumbnail)
            thumbnail  = item.get("thumbnail") or ""
            image_url  = re.sub(r"-[A-Z]\.jpg$", "-O.jpg", thumbnail) if thumbnail else ""

            # Frete grátis (Mercado Envios Full)
            shipping      = item.get("shipping") or {}
            free_shipping = bool(shipping.get("free_shipping"))

            # ID do produto para deduplicação
            ml_id = item.get("id") or ""

            # Desconto declarado pela API
            discount_pct: Optional[float] = None
            if original_price and price:
                discount_pct = round((1 - price / original_price) * 100, 1)

            return Product(
                title=self._clean_title(title),
                link=self._clean_ml_link(link),
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                free_shipping=free_shipping,
                extra={"ml_id": ml_id, "source": "api"},
            )
        except Exception as exc:
            self._logger.debug(f"ML parse_api_item falhou: {exc}")
            return None

    def _parse_html_card(self, card) -> Optional[Product]:
        """Parseia um card HTML da página de ofertas."""
        try:
            # Link
            link_el = card.select_one("a[href]")
            if not link_el:
                return None
            href = link_el.get("href", "")
            if not href or "mercadolivre.com.br" not in href:
                return None

            # Título
            title = ""
            for sel in (
                "[class*='title']", ".poly-component__title",
                ".promotion-item__title", "h2", "h3",
            ):
                el = card.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    if len(title) >= 10:
                        break
            if not title:
                return None

            # Preço atual
            price: Optional[float] = None
            for sel in (
                ".andes-money-amount__fraction",
                ".price-tag-fraction",
                "[class*='price'] .fraction",
            ):
                el = card.select_one(sel)
                if el:
                    price = self._parse_price(el.get_text(strip=True))
                    if price:
                        break

            # Preço original
            original_price: Optional[float] = None
            for sel in (
                ".andes-money-amount--previous .andes-money-amount__fraction",
                ".price-tag-amount-saved",
                "s .andes-money-amount__fraction",
            ):
                el = card.select_one(sel)
                if el:
                    original_price = self._parse_price(el.get_text(strip=True))
                    if original_price:
                        break

            # Desconto exibido
            discount_pct: Optional[float] = None
            disc_el = card.select_one(
                ".andes-money-amount__discount, [class*='discount']"
            )
            if disc_el:
                m = re.search(r"(\d+)\s*%", disc_el.get_text())
                if m:
                    discount_pct = float(m.group(1))

            # Imagem
            image_url = ""
            img_el = card.select_one("img[src], img[data-src]")
            if img_el:
                image_url = img_el.get("src") or img_el.get("data-src") or ""

            # ML ID
            ml_id_m = re.search(r"MLB-?(\d+)", href)
            ml_id   = f"MLB{ml_id_m.group(1)}" if ml_id_m else href

            return Product(
                title=self._clean_title(title),
                link=self._clean_ml_link(href),
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                extra={"ml_id": ml_id, "source": "html"},
            )
        except Exception as exc:
            self._logger.debug(f"ML parse_html_card falhou: {exc}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _has_real_discount(self, product: Product) -> bool:
        """Descarta produtos sem desconto real calculável."""
        disc = product.calculated_discount
        if disc and disc >= self._MIN_DISCOUNT_PCT:
            return True
        if product.discount_pct and product.discount_pct >= self._MIN_DISCOUNT_PCT:
            return True
        return False

    @staticmethod
    def _clean_ml_link(link: str) -> str:
        """Remove parâmetros de tracking do link ML."""
        # Mantém apenas o caminho base sem query string
        clean = link.split("?")[0].rstrip("/")
        return clean
