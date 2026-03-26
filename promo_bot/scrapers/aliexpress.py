"""
Scraper do AliExpress via página de ofertas e busca.

O AliExpress possui proteção anti-bot robusta. Este scraper
tenta extrair dados JSON embutidos no HTML e faz parsing
HTML como fallback.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class AliExpressScraper(BaseScraper):
    """Scraper do AliExpress via página de ofertas."""

    STORE = Store.ALIEXPRESS
    NAME = "aliexpress"
    BASE_URL = "https://pt.aliexpress.com"

    # Páginas de ofertas
    DEALS_URLS = [
        "https://pt.aliexpress.com/gcp/300000512/innerfeed",
        "https://pt.aliexpress.com/campaign/wow/gcp-plus/ae/right/uf/pt",
    ]

    SEARCH_URL = "https://pt.aliexpress.com/wholesale"

    ALIEXPRESS_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://pt.aliexpress.com/",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas do AliExpress."""
        products: list[Product] = []

        # Tenta páginas de ofertas
        for url in self.DEALS_URLS:
            try:
                page_products = await self._scrape_page(url)
                products.extend(page_products)
                if products:
                    break
            except Exception as e:
                self._logger.debug(f"Erro ao acessar {url}: {e}")

        # Fallback: busca por termos de promoção
        if not products:
            products = await self._scrape_search()

        self._logger.info(f"AliExpress: {len(products)} ofertas coletadas")
        return products

    async def _scrape_page(self, url: str) -> list[Product]:
        """Coleta ofertas de uma página do AliExpress."""
        products: list[Product] = []

        html = await self._http.fetch(url, headers=self.ALIEXPRESS_HEADERS)
        if not html:
            self._logger.warning(f"Falha ao acessar {url}")
            return products

        # Estratégia 1: JSON embutido no HTML
        json_products = self._extract_from_json(html)
        if json_products:
            return json_products

        # Estratégia 2: Parsing HTML
        html_products = self._extract_from_html(html)
        return html_products

    def _extract_from_json(self, html: str) -> list[Product]:
        """Extrai ofertas de dados JSON embutidos no HTML."""
        products: list[Product] = []

        patterns = [
            r'window\._dida_config_\s*=\s*(\{.*?\});',
            r'window\.runParams\s*=\s*(\{.*?\});',
            r'"items"\s*:\s*(\[.*?\])',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.DOTALL)
            for match in matches[:3]:
                try:
                    data = json.loads(match)
                    items = data if isinstance(data, list) else self._extract_items(data)
                    for item in items[:20]:
                        product = self._parse_deal_item(item)
                        if product:
                            products.append(product)
                except (json.JSONDecodeError, TypeError):
                    continue

        return products

    def _extract_items(self, data: dict) -> list[dict]:
        """Extrai itens de diferentes formatos de resposta."""
        for key in ["data", "result"]:
            container = data.get(key, {})
            if isinstance(container, dict):
                for subkey in ["items", "resultList", "feedItemList", "itemList", "productList"]:
                    items = container.get(subkey, [])
                    if isinstance(items, list) and items:
                        return items
            elif isinstance(container, list):
                return container

        for key in ["items", "resultList"]:
            items = data.get(key, [])
            if isinstance(items, list) and items:
                return items

        return []

    def _parse_deal_item(self, item: dict) -> Optional[Product]:
        """Parseia um item de deal."""
        try:
            title = (
                item.get("title", "")
                or item.get("productTitle", "")
                or item.get("name", "")
                or item.get("subject", "")
            )
            if not title:
                return None

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
            price = self._extract_price_field(
                item, ["salePrice", "currentPrice", "price", "minPrice", "actPrice"]
            )
            original_price = self._extract_price_field(
                item, ["originalPrice", "oriPrice", "marketPrice", "maxPrice"]
            )

            # Desconto
            discount = item.get("discount", 0) or item.get("off", 0)
            discount_pct = None
            if isinstance(discount, str):
                disc_match = re.search(r"(\d+)", discount)
                if disc_match:
                    discount_pct = float(disc_match.group(1))
            elif isinstance(discount, (int, float)) and discount > 0:
                discount_pct = float(discount)

            # Imagem
            image = item.get("imageUrl", "") or item.get("image", "") or item.get("imgUrl", "")
            if image and not image.startswith("http"):
                image = f"https:{image}"

            return Product(
                title=self._clean_title(title),
                link=link.split("?")[0],
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image or "",
                free_shipping=bool(item.get("freeShipping", False)),
            )
        except Exception as e:
            self._logger.debug(f"Erro ao parsear item AliExpress: {e}")
            return None

    def _extract_price_field(self, item: dict, keys: list[str]) -> Optional[float]:
        """Extrai preço tentando múltiplas chaves."""
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            if isinstance(value, (int, float)):
                return float(value) if value > 0 else None
            if isinstance(value, str):
                parsed = self._parse_price(value)
                if parsed and parsed > 0:
                    return parsed
            if isinstance(value, dict):
                v = value.get("value") or value.get("amount")
                if v:
                    return float(v)
        return None

    def _extract_from_html(self, html: str) -> list[Product]:
        """Extrai ofertas via parsing HTML."""
        products: list[Product] = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            selectors = [
                "a[href*='/item/']",
                ".product-card",
                "[class*='product-snippet']",
                "[class*='deal-card']",
            ]

            for selector in selectors:
                cards = soup.select(selector)
                if not cards:
                    continue

                for card in cards[:20]:
                    try:
                        if card.name == "a":
                            link = card.get("href", "")
                        else:
                            link_el = card.select_one("a[href*='/item/']")
                            link = link_el.get("href", "") if link_el else ""

                        if not link:
                            continue
                        if not link.startswith("http"):
                            link = f"https:{link}" if link.startswith("//") else f"https://pt.aliexpress.com{link}"

                        title_el = card.select_one(
                            "[class*='title'], [class*='name'], h3, h4"
                        )
                        title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:100]
                        if len(title) < 10:
                            continue

                        price_el = card.select_one("[class*='price'], [class*='Price']")
                        price = self._parse_price(price_el.get_text(strip=True)) if price_el else None

                        products.append(Product(
                            title=self._clean_title(title),
                            link=link.split("?")[0],
                            store=self.STORE,
                            price=price,
                        ))
                    except Exception:
                        continue

                if products:
                    break

        except Exception as e:
            self._logger.debug(f"Erro no parsing HTML AliExpress: {e}")

        return products

    async def _scrape_search(self) -> list[Product]:
        """Busca promoções via página de busca."""
        products: list[Product] = []

        try:
            url = f"{self.SEARCH_URL}?SearchText=super+deals&SortType=total_tranpro_desc"
            html = await self._http.fetch(url, headers=self.ALIEXPRESS_HEADERS)
            if html:
                products = self._extract_from_json(html) or self._extract_from_html(html)
        except Exception as e:
            self._logger.debug(f"Erro na busca AliExpress: {e}")

        return products
