"""
Scraper do KaBuM! usando a API pública de catálogo.

Acessa a API REST oficial de catálogo do KaBuM! para coletar
produtos em oferta com preços, descontos e informações completas.
"""

from __future__ import annotations

import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class KabumScraper(BaseScraper):
    """Scraper do KaBuM! via API pública de catálogo."""

    STORE = Store.KABUM
    NAME = "kabum"
    BASE_URL = "https://servicespub.prod.api.aws.grupokabum.com.br"

    CATALOG_URL = "{base}/catalog/v2/products"
    PRODUCT_URL = "https://www.kabum.com.br/produto/{slug}"

    # Headers que a API aceita
    API_HEADERS = {
        "Accept": "application/json",
        "Referer": "https://www.kabum.com.br/",
        "Origin": "https://www.kabum.com.br",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas do KaBuM! via API de catálogo."""
        products: list[Product] = []

        # Busca 1: Produtos em oferta (mais buscados)
        offer_products = await self._fetch_catalog(
            has_offer="true", sort="most_searched", page_size=20
        )
        products.extend(offer_products)

        # Busca 2: Ofertas do dia (stamp específico)
        daily_products = await self._fetch_catalog(
            highlighted_stamp="Ofertas do Dia", sort="most_searched", page_size=15
        )
        # Evita duplicatas por ID
        seen_links = {p.link for p in products}
        for p in daily_products:
            if p.link not in seen_links:
                products.append(p)
                seen_links.add(p.link)

        self._logger.info(f"KaBuM: {len(products)} ofertas coletadas no total")
        return products

    async def _fetch_catalog(
        self,
        has_offer: str = "",
        highlighted_stamp: str = "",
        sort: str = "most_searched",
        page_size: int = 20,
    ) -> list[Product]:
        """Busca produtos na API de catálogo com filtros."""
        products: list[Product] = []
        url = self.CATALOG_URL.format(base=self.BASE_URL)

        params = {
            "page_number": "1",
            "page_size": str(page_size),
            "sort": sort,
        }
        if has_offer:
            params["has_offer"] = has_offer
        if highlighted_stamp:
            params["highlighted_stamp"] = highlighted_stamp

        data = await self._http.fetch_json(url, headers=self.API_HEADERS, params=params)
        if not data:
            self._logger.warning("Falha ao acessar API do KaBuM")
            return products

        items = data.get("data", [])
        if not isinstance(items, list):
            self._logger.warning("Formato inesperado da API do KaBuM")
            return products

        self._logger.debug(f"KaBuM API retornou {len(items)} itens")

        for item in items:
            try:
                product = self._parse_product(item)
                if product:
                    products.append(product)
            except Exception as e:
                self._logger.debug(f"Erro ao parsear produto KaBuM: {e}")

        return products

    def _parse_product(self, item: dict) -> Optional[Product]:
        """Converte um item da API em Product."""
        attrs = item.get("attributes", {})
        if not attrs:
            return None

        title = attrs.get("title", "").strip()
        if not title:
            return None

        # Monta link do produto
        product_slug = attrs.get("product_link", "")
        if not product_slug:
            return None
        link = self.PRODUCT_URL.format(slug=product_slug)

        # Preços — prioriza preço de oferta
        offer = attrs.get("offer") or {}
        if offer and offer.get("price"):
            price = offer.get("price_with_discount") or offer.get("price")
            original_price = attrs.get("price")
            discount_pct = offer.get("discount_percentage")
        else:
            price = attrs.get("price_with_discount") or attrs.get("price")
            original_price = attrs.get("old_price")
            discount_pct = attrs.get("discount_percentage")

        # Converte preços
        price = float(price) if price else None
        if original_price:
            original_price = float(original_price)
            if original_price <= 0 or (price and original_price <= price):
                original_price = None
        if discount_pct:
            discount_pct = float(discount_pct)

        # Imagem
        images = attrs.get("images", [])
        image_url = images[0] if images else ""

        # Frete grátis
        free_shipping = bool(attrs.get("has_free_shipping", False))

        # Cupom (extraído dos stamps)
        coupon = self._extract_coupon(attrs.get("stamps", []))

        # Categoria
        menu = attrs.get("menu", "")
        category = menu.split("/")[0] if menu else ""

        return Product(
            title=self._clean_title(title),
            link=link,
            store=self.STORE,
            price=price,
            original_price=original_price,
            discount_pct=discount_pct,
            category=category,
            image_url=image_url,
            coupon_code=coupon,
            free_shipping=free_shipping,
        )

    def _extract_coupon(self, stamps: list) -> Optional[str]:
        """Extrai código de cupom dos stamps do produto."""
        if not stamps:
            return None

        for stamp in stamps:
            stamp_title = ""
            if isinstance(stamp, dict):
                stamp_title = stamp.get("title", "") or stamp.get("name", "")
            elif isinstance(stamp, str):
                stamp_title = stamp

            upper = stamp_title.upper()
            if "CUPOM" in upper:
                # Extrai o código do cupom (ex: "CUPOM SHARK15" -> "SHARK15")
                match = re.search(r"CUPOM\s+(\S+)", stamp_title, re.IGNORECASE)
                if match:
                    return match.group(1)
                return stamp_title.strip()

        return None
