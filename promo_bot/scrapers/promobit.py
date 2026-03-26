"""
Scraper do Promobit via dados SSR (Server-Side Rendering).

O Promobit usa Next.js e renderiza os dados de ofertas no HTML
via __NEXT_DATA__. Este scraper extrai esses dados estruturados
diretamente, sem necessidade de JavaScript ou API separada.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class PromobitScraper(BaseScraper):
    """Scraper do Promobit via __NEXT_DATA__ (SSR)."""

    STORE = Store.PROMOBIT
    NAME = "promobit"
    BASE_URL = "https://www.promobit.com.br"

    # Páginas com dados SSR
    PAGES = [
        "/promocoes/recentes/",
        "/promocoes/em-alta/",
    ]

    OFFER_URL = "{base}/d/{slug}"

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas do Promobit via SSR data."""
        products: list[Product] = []
        seen_ids: set[int] = set()

        for page_path in self.PAGES:
            url = f"{self.BASE_URL}{page_path}"
            page_products = await self._scrape_page(url, seen_ids)
            products.extend(page_products)

        self._logger.info(f"Promobit: {len(products)} ofertas coletadas no total")
        return products

    async def _scrape_page(self, url: str, seen_ids: set[int]) -> list[Product]:
        """Extrai ofertas de uma página do Promobit."""
        products: list[Product] = []

        html = await self._http.fetch(url)
        if not html:
            self._logger.warning(f"Falha ao acessar {url}")
            return products

        # Extrai __NEXT_DATA__ do HTML
        next_data = self._extract_next_data(html)
        if not next_data:
            self._logger.warning(f"__NEXT_DATA__ nao encontrado em {url}")
            return products

        # Navega até as ofertas
        try:
            page_props = next_data.get("props", {}).get("pageProps", {})
            server_offers = page_props.get("serverOffers", {})

            # serverOffers pode ser dict com chave "offers" ou lista direta
            if isinstance(server_offers, dict):
                offers = server_offers.get("offers", [])
            elif isinstance(server_offers, list):
                offers = server_offers
            else:
                offers = []

            # Também tenta serverFeaturedOffers
            featured = page_props.get("serverFeaturedOffers", [])
            if isinstance(featured, list):
                offers = featured + offers

        except (AttributeError, TypeError) as e:
            self._logger.warning(f"Erro ao navegar dados do Promobit: {e}")
            return products

        self._logger.debug(f"Promobit: {len(offers)} ofertas encontradas em {url}")

        for offer in offers:
            try:
                offer_id = offer.get("offerId", 0)
                if offer_id in seen_ids:
                    continue
                seen_ids.add(offer_id)

                product = self._parse_offer(offer)
                if product:
                    products.append(product)
            except Exception as e:
                self._logger.debug(f"Erro ao parsear oferta Promobit: {e}")

        return products

    def _extract_next_data(self, html: str) -> Optional[dict]:
        """Extrai e parseia o JSON de __NEXT_DATA__ do HTML."""
        try:
            # Busca o script __NEXT_DATA__
            pattern = r'<script\s+id="__NEXT_DATA__"\s+type="application/json"[^>]*>(.*?)</script>'
            match = re.search(pattern, html, re.DOTALL)
            if match:
                return json.loads(match.group(1))

            # Fallback: busca qualquer script com __NEXT_DATA__
            pattern2 = r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>'
            match2 = re.search(pattern2, html, re.DOTALL)
            if match2:
                return json.loads(match2.group(1))

        except (json.JSONDecodeError, TypeError) as e:
            self._logger.warning(f"Erro ao parsear __NEXT_DATA__: {e}")

        return None

    def _parse_offer(self, offer: dict) -> Optional[Product]:
        """Converte uma oferta do Promobit em Product."""
        title = offer.get("offerTitle", "").strip()
        if not title:
            return None

        # Link da oferta
        slug = offer.get("offerSlug", "")
        if not slug:
            return None
        link = self.OFFER_URL.format(base=self.BASE_URL, slug=slug)

        # Preço atual
        price = offer.get("offerPrice")
        if price is not None:
            try:
                price = float(price) if float(price) > 0 else None
            except (ValueError, TypeError):
                price = None

        # Preço original
        original_price = offer.get("offerOldPrice")
        if original_price is not None:
            try:
                original_price = float(original_price) if float(original_price) > 0 else None
            except (ValueError, TypeError):
                original_price = None
        if price and original_price and original_price <= price:
            original_price = None

        # Desconto
        discount_pct = offer.get("offerDiscontPercentage")  # Sim, é "Discont" na API
        if discount_pct is not None:
            try:
                discount_pct = float(discount_pct) if float(discount_pct) > 0 else None
            except (ValueError, TypeError):
                discount_pct = None

        # Cupom
        coupon = offer.get("offerCoupon")
        if coupon:
            coupon = str(coupon).strip() or None
        else:
            coupon = None

        # Imagem
        image_url = ""
        photo = offer.get("offerPhoto", "")
        if photo:
            if photo.startswith("http"):
                image_url = photo
            elif photo.startswith("/"):
                image_url = f"https://www.promobit.com.br{photo}"

        # Loja de origem
        store_name = offer.get("storeName", "")
        store_domain = offer.get("storeDomain", "")

        # Categoria
        category = offer.get("categoryName", "")

        # Frete grátis (detecta no título)
        free_shipping = "frete gr" in title.lower()

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
            extra={
                "origin_store": store_name,
                "origin_domain": store_domain,
                "offer_id": offer.get("offerId"),
                "likes": offer.get("offerLikes", 0),
            },
        )
