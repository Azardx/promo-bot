"""
Scraper do Mercado Livre via API pública e HTML parsing.

O Mercado Livre possui uma API pública de busca que retorna
dados JSON estruturados. Este scraper usa:
1. API pública de busca (sites/MLB/search)
2. Página de ofertas do dia
3. HTML parsing como fallback

Extrai: título, preço, preço original, desconto, imagem, cupom,
frete grátis, vendedor e avaliação.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class MercadoLivreScraper(BaseScraper):
    """Scraper do Mercado Livre via API pública e HTML."""

    STORE = Store.MERCADOLIVRE
    NAME = "mercadolivre"
    BASE_URL = "https://www.mercadolivre.com.br"

    # API pública do Mercado Livre (não requer autenticação)
    API_BASE = "https://api.mercadolibre.com"
    SEARCH_API = f"{API_BASE}/sites/MLB/search"

    # Páginas de ofertas
    DEALS_URLS = [
        "https://www.mercadolivre.com.br/ofertas",
        "https://www.mercadolivre.com.br/ofertas/do-dia",
    ]

    # Headers para API
    API_HEADERS = {
        "Accept": "application/json",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }

    # Headers para HTML
    HTML_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.mercadolivre.com.br/",
    }

    # Termos de busca para encontrar promoções
    SEARCH_TERMS = [
        "oferta do dia",
        "promoção",
    ]

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas do Mercado Livre."""
        products: list[Product] = []
        seen_ids: set[str] = set()

        # Estratégia 1: API pública de busca (ofertas)
        try:
            api_products = await self._scrape_api()
            for p in api_products:
                ml_id = p.extra.get("ml_id", p.link)
                if ml_id not in seen_ids:
                    products.append(p)
                    seen_ids.add(ml_id)
            self._logger.info(f"ML API: {len(api_products)} ofertas")
        except Exception as e:
            self._logger.warning(f"Erro na API ML: {e}")

        # Estratégia 2: HTML parsing das páginas de ofertas
        if len(products) < 10:
            try:
                html_products = await self._scrape_html()
                for p in html_products:
                    ml_id = p.extra.get("ml_id", p.link)
                    if ml_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(ml_id)
                self._logger.info(f"ML HTML: {len(html_products)} ofertas")
            except Exception as e:
                self._logger.warning(f"Erro no HTML ML: {e}")

        self._logger.info(f"Mercado Livre: {len(products)} ofertas totais")
        return products

    # ------------------------------------------------------------------
    # Estratégia 1: API pública
    # ------------------------------------------------------------------

    async def _scrape_api(self) -> list[Product]:
        """Coleta ofertas via API pública do Mercado Livre."""
        products: list[Product] = []

        # Busca 1: Ofertas com desconto
        try:
            params = {
                "category": "MLB1648",  # Informática
                "sort": "relevance",
                "limit": "20",
                "has_deals": "true",
            }
            data = await self._http.fetch_json(
                self.SEARCH_API,
                headers=self.API_HEADERS,
                params=params,
            )
            if data:
                results = data.get("results", [])
                for item in results:
                    product = self._parse_api_item(item)
                    if product:
                        products.append(product)
        except Exception as e:
            self._logger.debug(f"Erro na busca API ML (deals): {e}")

        # Busca 2: Mais vendidos com desconto
        if len(products) < 10:
            try:
                params = {
                    "sort": "sold_quantity_desc",
                    "limit": "20",
                    "discount": "5-100",
                }
                data = await self._http.fetch_json(
                    self.SEARCH_API,
                    headers=self.API_HEADERS,
                    params=params,
                )
                if data:
                    results = data.get("results", [])
                    for item in results:
                        product = self._parse_api_item(item)
                        if product:
                            products.append(product)
            except Exception as e:
                self._logger.debug(f"Erro na busca API ML (vendidos): {e}")

        # Busca 3: Termos populares
        if len(products) < 5:
            for term in self.SEARCH_TERMS[:1]:
                try:
                    params = {
                        "q": term,
                        "sort": "relevance",
                        "limit": "15",
                    }
                    data = await self._http.fetch_json(
                        self.SEARCH_API,
                        headers=self.API_HEADERS,
                        params=params,
                    )
                    if data:
                        results = data.get("results", [])
                        for item in results:
                            product = self._parse_api_item(item)
                            if product:
                                products.append(product)
                    if products:
                        break
                except Exception as e:
                    self._logger.debug(f"Erro na busca API ML '{term}': {e}")

        return products

    def _parse_api_item(self, item: dict) -> Optional[Product]:
        """Parseia um item da API do Mercado Livre."""
        try:
            title = item.get("title", "").strip()
            if not title or len(title) < 10:
                return None

            ml_id = item.get("id", "")
            permalink = item.get("permalink", "")
            link = permalink or f"https://www.mercadolivre.com.br/p/{ml_id}"

            # Preço
            price = item.get("price")
            if price is not None:
                try:
                    price = float(price)
                    if price <= 0:
                        price = None
                except (ValueError, TypeError):
                    price = None

            # Preço original
            original_price = item.get("original_price")
            if original_price is not None:
                try:
                    original_price = float(original_price)
                    if original_price <= 0 or (price and original_price <= price):
                        original_price = None
                except (ValueError, TypeError):
                    original_price = None

            # Desconto
            discount_pct = None
            if price and original_price and original_price > price:
                discount_pct = round(
                    ((original_price - price) / original_price) * 100, 1
                )

            # Imagem
            image_url = item.get("thumbnail", "")
            if image_url:
                # Troca para resolução maior
                image_url = image_url.replace("-I.jpg", "-O.jpg")
                if not image_url.startswith("http"):
                    image_url = f"https:{image_url}"

            # Frete grátis
            free_shipping = False
            shipping = item.get("shipping", {})
            if isinstance(shipping, dict):
                free_shipping = shipping.get("free_shipping", False)

            # Condição
            condition = item.get("condition", "")

            # Cupom (ML não tem cupons na API, mas pode ter tags)
            coupon = None
            tags = item.get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, str) and "deal" in tag.lower():
                        coupon = None  # ML usa deals, não cupons
                        break

            # Vendedor
            seller = item.get("seller", {})
            seller_name = ""
            if isinstance(seller, dict):
                seller_name = seller.get("nickname", "")

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
                extra={
                    "ml_id": ml_id,
                    "condition": condition,
                    "seller": seller_name,
                    "sold_quantity": item.get("sold_quantity", 0),
                    "available_quantity": item.get("available_quantity", 0),
                },
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear item API ML: {e}")
            return None

    # ------------------------------------------------------------------
    # Estratégia 2: HTML parsing
    # ------------------------------------------------------------------

    async def _scrape_html(self) -> list[Product]:
        """Coleta ofertas via HTML parsing."""
        products: list[Product] = []

        for url in self.DEALS_URLS:
            try:
                html = await self._http.fetch(url, headers=self.HTML_HEADERS)
                if not html:
                    continue

                page_products = self._extract_from_html(html)
                products.extend(page_products)

                if products:
                    break

            except Exception as e:
                self._logger.debug(f"Erro ao acessar {url}: {e}")

        return products

    def _extract_from_html(self, html: str) -> list[Product]:
        """Extrai ofertas via BeautifulSoup."""
        products: list[Product] = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Seletores de cards de produto do ML
            card_selectors = [
                ".promotion-item",
                ".andes-card",
                "[class*='poly-card']",
                ".ui-search-result",
                ".deals-item",
            ]

            cards = []
            for selector in card_selectors:
                cards = soup.select(selector)
                if cards:
                    self._logger.debug(
                        f"ML: encontrou {len(cards)} cards com '{selector}'"
                    )
                    break

            # Fallback: busca por links de produto
            if not cards:
                cards = soup.select("a[href*='mercadolivre.com.br/']")[:25]

            for card in cards[:25]:
                product = self._parse_html_card(card)
                if product:
                    products.append(product)

        except Exception as e:
            self._logger.debug(f"Erro ao parsear HTML ML: {e}")

        return products

    def _parse_html_card(self, card) -> Optional[Product]:
        """Parseia um card HTML do Mercado Livre."""
        try:
            # Link
            link_el = card if card.name == "a" else card.select_one("a[href]")
            if not link_el:
                return None

            href = link_el.get("href", "")
            if not href or not href.startswith("http"):
                return None

            # Filtra links que não são de produto
            if "mercadolivre.com.br" not in href and "mercadolibre.com" not in href:
                return None

            # Extrai ML ID do link
            ml_id = ""
            id_match = re.search(r"MLB-?\d+", href)
            if id_match:
                ml_id = id_match.group(0)

            # Título
            title = None
            for sel in [
                ".promotion-item__title",
                "[class*='poly-component__title']",
                ".ui-search-item__title",
                "h2", "h3",
                "[class*='title']",
            ]:
                el = card.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    if title and len(title) >= 10:
                        break

            if not title or len(title) < 10:
                title = link_el.get("title", "") or link_el.get_text(strip=True)
            if not title or len(title) < 10:
                return None

            # Preço
            price = None
            for sel in [
                ".andes-money-amount__fraction",
                "[class*='price'] .andes-money-amount",
                ".promotion-item__price",
                "[class*='poly-price']",
            ]:
                el = card.select_one(sel)
                if el:
                    price_text = el.get_text(strip=True)
                    price = self._parse_price(price_text)
                    if price:
                        break

            # Preço original
            original_price = None
            for sel in [
                "s .andes-money-amount__fraction",
                "[class*='original-price']",
                "del",
            ]:
                el = card.select_one(sel)
                if el:
                    original_price = self._parse_price(el.get_text(strip=True))
                    if original_price:
                        break

            if original_price and price and original_price <= price:
                original_price = None

            # Desconto
            discount_pct = None
            discount_el = card.select_one(
                "[class*='discount'], [class*='rebaja']"
            )
            if discount_el:
                disc_match = re.search(r"(\d+)\s*%", discount_el.get_text())
                if disc_match:
                    discount_pct = float(disc_match.group(1))

            if not discount_pct and price and original_price and original_price > price:
                discount_pct = round(
                    ((original_price - price) / original_price) * 100, 1
                )

            # Imagem
            image_url = ""
            img_el = card.select_one("img[src], img[data-src]")
            if img_el:
                src = img_el.get("src", "") or img_el.get("data-src", "")
                if src and src.startswith("http"):
                    # Troca para resolução maior
                    image_url = src.replace("-I.jpg", "-O.jpg")

            # Frete grátis
            free_shipping = False
            frete_el = card.select_one(
                "[class*='shipping'], [class*='frete']"
            )
            if frete_el and "grátis" in frete_el.get_text(strip=True).lower():
                free_shipping = True

            return Product(
                title=self._clean_title(title),
                link=href.split("?")[0],  # Remove tracking params
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                free_shipping=free_shipping,
                extra={
                    "ml_id": ml_id,
                    "source": "ml_html",
                },
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear card ML: {e}")
            return None
