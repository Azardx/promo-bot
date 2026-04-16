"""
Scraper do Promobit via dados SSR (Server-Side Rendering) e HTML fallback.

O Promobit usa Next.js e renderiza os dados de ofertas no HTML via __NEXT_DATA__. 
Este scraper extrai esses dados estruturados diretamente e possui fallback 
para parsing HTML caso a estrutura do Next.js mude.

CORREÇÕES v2.3:
- Suporte a múltiplas estruturas de __NEXT_DATA__
- Fallback para parsing HTML direto
- Extração aprimorada de cupons e loja de origem
"""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class PromobitScraper(BaseScraper):
    """Scraper do Promobit com suporte a SSR e HTML."""

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
        """Coleta ofertas do Promobit."""
        products: list[Product] = []
        seen_ids: set[str] = set()

        for page_path in self.PAGES:
            url = f"{self.BASE_URL}{page_path}"
            page_products = await self._scrape_page(url, seen_ids)
            products.extend(page_products)

        self._logger.info(f"Promobit: {len(products)} ofertas coletadas no total")
        return products

    async def _scrape_page(self, url: str, seen_ids: set[str]) -> list[Product]:
        """Extrai ofertas de uma página do Promobit."""
        products: list[Product] = []

        html = await self._http.fetch(url)
        if not html:
            self._logger.warning(f"Falha ao acessar {url}")
            return products

        # 1. Tenta extrair via __NEXT_DATA__
        next_data = self._extract_next_data(html)
        if next_data:
            try:
                offers = self._extract_offers_from_next(next_data)
                for offer in offers:
                    offer_id = str(offer.get("offerId") or offer.get("id", ""))
                    if not offer_id or offer_id in seen_ids:
                        continue
                    seen_ids.add(offer_id)

                    product = self._parse_next_offer(offer)
                    if product:
                        products.append(product)
                
                if products:
                    self._logger.debug(f"Promobit: {len(products)} ofertas via NEXT_DATA em {url}")
                    return products
            except Exception as e:
                self._logger.debug(f"Erro ao processar NEXT_DATA Promobit: {e}")

        # 2. Fallback: Parsing HTML direto
        try:
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select("div[class*='OfferCard'], article[class*='OfferCard'], .offer-card")
            
            for card in cards[:30]:
                product = self._parse_html_card(card)
                if product:
                    # Usa link como ID para deduplicação simples aqui
                    if product.link not in seen_ids:
                        seen_ids.add(product.link)
                        products.append(product)
            
            if products:
                self._logger.debug(f"Promobit: {len(products)} ofertas via HTML em {url}")
        except Exception as e:
            self._logger.error(f"Erro no fallback HTML Promobit: {e}")

        return products

    def _extract_next_data(self, html: str) -> Optional[dict]:
        """Extrai o JSON de __NEXT_DATA__ do HTML."""
        try:
            pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
            match = re.search(pattern, html, re.DOTALL)
            if match:
                return json.loads(match.group(1))
        except Exception:
            pass
        return None

    def _extract_offers_from_next(self, data: dict) -> list[dict]:
        """Navega pelo JSON do Next.js para encontrar a lista de ofertas."""
        try:
            props = data.get("props", {}).get("pageProps", {})
            
            # Caminho 1: serverOffers
            server_offers = props.get("serverOffers", {})
            if isinstance(server_offers, dict):
                offers = server_offers.get("offers", [])
                if offers: return offers
            elif isinstance(server_offers, list):
                return server_offers

            # Caminho 2: serverFeaturedOffers
            featured = props.get("serverFeaturedOffers", [])
            if featured: return featured

            # Caminho 3: initialState
            initial = data.get("props", {}).get("initialState", {})
            offers_list = initial.get("offers", {}).get("list", [])
            if offers_list: return offers_list

        except Exception:
            pass
        return []

    def _parse_next_offer(self, offer: dict) -> Optional[Product]:
        """Converte uma oferta do JSON Next.js em Product."""
        try:
            title = offer.get("offerTitle") or offer.get("title") or offer.get("name")
            if not title: return None

            slug = offer.get("offerSlug") or offer.get("slug")
            id_ = offer.get("offerId") or offer.get("id")
            promobit_link = f"{self.BASE_URL}/oferta/{slug}-{id_}/" if slug and id_ else None
            
            # Tenta link real da loja
            real_link = self._extract_real_link(offer)
            link = real_link or promobit_link or offer.get("url")
            if not link: return None

            price = float(offer.get("offerPrice") or offer.get("price", 0)) or None
            original_price = float(offer.get("offerOldPrice") or offer.get("oldPrice", 0)) or None
            
            # Cupom
            coupon = offer.get("offerCoupon") or offer.get("couponCode") or offer.get("coupon")
            if isinstance(coupon, dict):
                coupon = coupon.get("code")
            
            # Imagem
            image_url = ""
            photo = offer.get("offerPhoto") or offer.get("image")
            if isinstance(photo, dict):
                image_url = photo.get("url") or photo.get("src", "")
            elif isinstance(photo, str):
                image_url = photo
            
            if image_url and image_url.startswith("/"):
                image_url = f"{self.BASE_URL}{image_url}"

            # Loja de origem
            store_name = offer.get("storeName") or offer.get("retailer", {}).get("name", "")

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                image_url=image_url,
                coupon_code=str(coupon).strip() if coupon else None,
                extra={
                    "origin_store": store_name,
                    "promobit_id": id_,
                }
            )
        except Exception:
            return None

    def _parse_html_card(self, card) -> Optional[Product]:
        """Parseia um card HTML da Promobit."""
        try:
            link_el = card.select_one("a[href*='/oferta/']")
            if not link_el: return None
            
            href = link_el.get("href", "")
            link = f"{self.BASE_URL}{href}" if href.startswith("/") else href

            title_el = card.select_one("h2, h3, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title: return None

            price_el = card.select_one("[class*='price'], .price")
            price = self._parse_price(price_el.get_text(strip=True)) if price_el else None

            origin_el = card.select_one("[class*='retailer'], .retailer-name")
            origin_store = origin_el.get_text(strip=True) if origin_el else ""

            img_el = card.select_one("img")
            image_url = ""
            if img_el:
                image_url = img_el.get("src") or img_el.get("data-src") or ""

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                image_url=image_url,
                extra={"origin_store": origin_store}
            )
        except Exception:
            return None

    def _extract_real_link(self, offer: dict) -> Optional[str]:
        """Extrai o link real da loja de destino."""
        for field in ["offerLink", "offerUrl", "offerExternalUrl", "storeUrl", "offerAffiliateUrl"]:
            url = offer.get(field)
            if url and isinstance(url, str) and url.startswith("http") and "promobit.com.br" not in url:
                return self._clean_external_url(url)
        return None

    def _clean_external_url(self, url: str) -> str:
        """Limpa URL externa removendo parâmetros de tracking."""
        try:
            parsed = urlparse(url)
            if parsed.query:
                params = parse_qs(parsed.query, keep_blank_values=True)
                cleaned_params = {k: v for k, v in params.items() if not k.lower().startswith("utm_") and k.lower() not in ["ref", "tag", "aff_id"]}
                query = urlencode(cleaned_params, doseq=True)
                return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ""))
            return url
        except Exception:
            return url
