"""
Scraper de promoções do AliExpress.

Coleta ofertas via API interna de deals, página de Super Deals
e busca por promoções. Implementa parsing robusto para múltiplos
formatos de resposta.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper


class AliExpressScraper(BaseScraper):
    """Scraper especializado para o AliExpress."""

    STORE = Store.ALIEXPRESS
    NAME = "aliexpress"
    BASE_URL = "https://pt.aliexpress.com"

    # Endpoints e páginas de ofertas
    _SUPER_DEALS_URL = "https://pt.aliexpress.com/gcp/300000512/innerfeed"
    _FLASH_DEALS_URL = "https://pt.aliexpress.com/gcp/300000556/innerfeed"
    _DEALS_PAGE = "https://pt.aliexpress.com/wow/gcp/tesla/channel/queryPage"
    _SEARCH_URL = "https://pt.aliexpress.com/wholesale"
    _DEALS_HTML_URL = "https://pt.aliexpress.com/campaign/wow/gcp-plus/ae/right/uf/pt"

    async def _scrape(self) -> list[Product]:
        """Coleta promoções do AliExpress via múltiplas estratégias."""
        products: list[Product] = []

        # Estratégia 1: API de Super Deals
        super_deals = await self._scrape_super_deals_api()
        products.extend(super_deals)

        # Estratégia 2: Página de ofertas via HTML
        html_deals = await self._scrape_deals_html()
        products.extend(html_deals)

        # Estratégia 3: Busca por termos de promoção
        search_deals = await self._scrape_search()
        products.extend(search_deals)

        return products

    async def _scrape_super_deals_api(self) -> list[Product]:
        """Coleta via API interna de Super Deals."""
        products = []

        try:
            headers = {
                "Referer": "https://pt.aliexpress.com/",
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            }

            # Tenta múltiplos endpoints de deals
            for url in [self._SUPER_DEALS_URL, self._FLASH_DEALS_URL]:
                params = {
                    "pageSize": "30",
                    "pageIndex": "1",
                    "resourceId": "",
                    "lzdealScene": "true",
                }

                data = await self._http.fetch_json(url, headers=headers, params=params)
                if not data:
                    continue

                # Navega na estrutura de resposta (varia entre endpoints)
                items = self._extract_items_from_response(data)
                for item in items:
                    product = self._parse_deal_item(item)
                    if product:
                        products.append(product)

                if products:
                    break  # Se encontrou em um endpoint, não precisa tentar outro

            self._logger.info(f"AliExpress API: {len(products)} itens coletados")

        except Exception as e:
            self._logger.warning(f"Erro na API Super Deals: {e}")

        return products

    def _extract_items_from_response(self, data: dict) -> list[dict]:
        """Extrai itens de diferentes formatos de resposta da API."""
        items = []

        # Formato 1: data.items
        if "data" in data and isinstance(data["data"], dict):
            items = data["data"].get("items", [])
            if not items:
                items = data["data"].get("resultList", [])
            if not items:
                # Formato aninhado
                for key in ["feedItemList", "itemList", "productList"]:
                    items = data["data"].get(key, [])
                    if items:
                        break

        # Formato 2: result.items
        if not items and "result" in data:
            result = data["result"]
            if isinstance(result, dict):
                items = result.get("items", []) or result.get("resultList", [])
            elif isinstance(result, list):
                items = result

        # Formato 3: direto na raiz
        if not items:
            items = data.get("items", []) or data.get("resultList", [])

        return items

    def _parse_deal_item(self, item: dict) -> Optional[Product]:
        """Parseia um item de deal da API."""
        try:
            title = (
                item.get("title", "")
                or item.get("productTitle", "")
                or item.get("name", "")
                or item.get("subject", "")
            )
            if not title:
                return None

            # Extrai link do produto
            product_id = (
                item.get("productId", "")
                or item.get("itemId", "")
                or item.get("item_id", "")
            )
            link = item.get("productUrl", "") or item.get("itemUrl", "") or item.get("url", "")

            if not link and product_id:
                link = f"https://pt.aliexpress.com/item/{product_id}.html"
            elif link and not link.startswith("http"):
                link = f"https:{link}" if link.startswith("//") else f"https://pt.aliexpress.com{link}"

            if not link:
                return None

            # Preços
            price = self._extract_price(item, ["salePrice", "currentPrice", "price", "minPrice", "actPrice"])
            original_price = self._extract_price(item, ["originalPrice", "oriPrice", "marketPrice", "maxPrice"])

            # Desconto
            discount = item.get("discount", 0) or item.get("off", 0)
            if isinstance(discount, str):
                discount_match = re.search(r"(\d+)", discount)
                discount = float(discount_match.group(1)) if discount_match else 0

            # Imagem
            image = item.get("imageUrl", "") or item.get("image", "") or item.get("imgUrl", "")
            if image and not image.startswith("http"):
                image = f"https:{image}"

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=float(discount) if discount else None,
                image_url=image,
                free_shipping=bool(item.get("freeShipping", False)),
            )
        except Exception as e:
            self._logger.debug(f"Erro ao parsear item AliExpress: {e}")
            return None

    def _extract_price(self, item: dict, keys: list[str]) -> Optional[float]:
        """Extrai preço de um item tentando múltiplas chaves."""
        for key in keys:
            value = item.get(key)
            if value is not None:
                if isinstance(value, (int, float)):
                    return float(value) if value > 0 else None
                if isinstance(value, str):
                    parsed = self._parse_price(value)
                    if parsed and parsed > 0:
                        return parsed
                if isinstance(value, dict):
                    # Formato: {"value": 99.90, "currency": "BRL"}
                    v = value.get("value") or value.get("amount")
                    if v:
                        return float(v)
        return None

    async def _scrape_deals_html(self) -> list[Product]:
        """Coleta via parsing HTML da página de ofertas."""
        products = []

        try:
            html = await self._http.fetch(self._DEALS_HTML_URL)
            if not html:
                # Fallback para página principal
                html = await self._http.fetch(f"{self.BASE_URL}/")

            if not html:
                return products

            # Tenta extrair JSON embutido no HTML
            json_patterns = [
                r'window\._dida_config_\s*=\s*({.*?});',
                r'window\.runParams\s*=\s*({.*?});',
                r'"items"\s*:\s*(\[.*?\])',
                r'data-spm-anchor-id[^>]*>.*?({.*?"productId".*?})',
            ]

            for pattern in json_patterns:
                matches = re.findall(pattern, html, re.DOTALL)
                for match in matches[:3]:
                    try:
                        data = json.loads(match)
                        if isinstance(data, list):
                            for item in data[:20]:
                                product = self._parse_deal_item(item)
                                if product:
                                    products.append(product)
                        elif isinstance(data, dict):
                            items = self._extract_items_from_response(data)
                            for item in items[:20]:
                                product = self._parse_deal_item(item)
                                if product:
                                    products.append(product)
                    except json.JSONDecodeError:
                        continue

            # Fallback: BeautifulSoup
            if not products:
                products = await self._parse_html_cards(html)

        except Exception as e:
            self._logger.warning(f"Erro no HTML AliExpress: {e}")

        return products

    async def _parse_html_cards(self, html: str) -> list[Product]:
        """Parseia cards de produto do HTML."""
        products = []

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")

            # Seletores comuns de cards de produto no AliExpress
            selectors = [
                "a[href*='/item/']",
                ".product-card",
                "[class*='product-snippet']",
                "[class*='deal-card']",
                ".list-item",
            ]

            for selector in selectors:
                cards = soup.select(selector)
                if not cards:
                    continue

                for card in cards[:20]:
                    try:
                        # Extrai link
                        if card.name == "a":
                            link = card.get("href", "")
                        else:
                            link_el = card.select_one("a[href*='/item/']")
                            link = link_el.get("href", "") if link_el else ""

                        if not link:
                            continue
                        if not link.startswith("http"):
                            link = f"https:{link}" if link.startswith("//") else f"https://pt.aliexpress.com{link}"

                        # Extrai título
                        title_el = card.select_one(
                            "[class*='title'], [class*='name'], h3, h4, .item-title"
                        )
                        title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:100]

                        if len(title) < 10:
                            continue

                        # Extrai preço
                        price_el = card.select_one(
                            "[class*='price'], [class*='Price'], .price-current"
                        )
                        price = self._parse_price(
                            price_el.get_text(strip=True) if price_el else ""
                        )

                        products.append(Product(
                            title=self._clean_title(title),
                            link=link.split("?")[0],  # Remove tracking params
                            store=self.STORE,
                            price=price,
                        ))
                    except Exception:
                        continue

                if products:
                    break  # Se encontrou com um seletor, para

        except Exception as e:
            self._logger.debug(f"Erro no parsing HTML AliExpress: {e}")

        return products

    async def _scrape_search(self) -> list[Product]:
        """Busca promoções via página de busca."""
        products = []
        search_terms = ["super deals", "flash deals"]

        for term in search_terms[:1]:
            try:
                url = f"{self._SEARCH_URL}?SearchText={term}&SortType=total_tranpro_desc"
                html = await self._http.fetch(url)
                if html:
                    found = await self._parse_html_cards(html)
                    products.extend(found)
            except Exception as e:
                self._logger.debug(f"Erro na busca AliExpress '{term}': {e}")

        return products
