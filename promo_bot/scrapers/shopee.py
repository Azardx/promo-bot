"""
Scraper da Shopee Brasil via página de flash sale e ofertas.

A Shopee possui proteção anti-bot forte. Este scraper tenta
múltiplas abordagens: API interna, dados JSON embutidos e
parsing HTML como fallback.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class ShopeeScraper(BaseScraper):
    """Scraper da Shopee Brasil via página de ofertas."""

    STORE = Store.SHOPEE
    NAME = "shopee"
    BASE_URL = "https://shopee.com.br"

    # URLs de ofertas
    FLASH_SALE_URL = "https://shopee.com.br/flash_sale"
    DAILY_DISCOVER_URL = "https://shopee.com.br/daily_discover"
    DEALS_URL = "https://shopee.com.br/deals"

    # Headers específicos
    SHOPEE_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://shopee.com.br/",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas da Shopee Brasil."""
        products: list[Product] = []

        # Tenta diferentes páginas de ofertas
        for url in [self.FLASH_SALE_URL, self.DAILY_DISCOVER_URL, self.DEALS_URL]:
            try:
                page_products = await self._scrape_page(url)
                products.extend(page_products)
                if products:
                    break
            except Exception as e:
                self._logger.debug(f"Erro ao acessar {url}: {e}")

        self._logger.info(f"Shopee: {len(products)} ofertas coletadas")
        return products

    async def _scrape_page(self, url: str) -> list[Product]:
        """Coleta ofertas de uma página da Shopee."""
        products: list[Product] = []

        html = await self._http.fetch(url, headers=self.SHOPEE_HEADERS)
        if not html:
            self._logger.warning(f"Falha ao acessar {url}")
            return products

        # Estratégia 1: Dados JSON embutidos (__INITIAL_STATE__)
        json_products = self._extract_from_json(html)
        if json_products:
            return json_products

        # Estratégia 2: Parsing HTML
        html_products = self._extract_from_html(html)
        return html_products

    def _extract_from_json(self, html: str) -> list[Product]:
        """Extrai ofertas de dados JSON embutidos no HTML."""
        products: list[Product] = []

        try:
            patterns = [
                r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>',
                r'window\.rawData\s*=\s*(\{.*?\})\s*;?\s*</script>',
            ]

            for pattern in patterns:
                matches = re.findall(pattern, html, re.DOTALL)
                for match in matches[:1]:
                    try:
                        data = json.loads(match)
                        items = self._extract_items(data)
                        for item in items[:20]:
                            product = self._parse_json_item(item)
                            if product:
                                products.append(product)
                    except (json.JSONDecodeError, TypeError):
                        continue

        except Exception as e:
            self._logger.debug(f"Erro ao extrair JSON da Shopee: {e}")

        return products

    def _extract_items(self, data: dict) -> list[dict]:
        """Extrai lista de itens de diferentes estruturas JSON."""
        if isinstance(data, list):
            return data

        for key in ["items", "data", "products", "flash_sale_items", "flashSale"]:
            items = data.get(key, [])
            if isinstance(items, list) and items:
                return items
            if isinstance(items, dict):
                for subkey in ["items", "data", "products"]:
                    subitems = items.get(subkey, [])
                    if isinstance(subitems, list) and subitems:
                        return subitems

        return []

    def _parse_json_item(self, item: dict) -> Optional[Product]:
        """Parseia um item JSON da Shopee."""
        try:
            title = item.get("name", "") or item.get("title", "") or item.get("item_name", "")
            if not title:
                return None

            item_id = item.get("itemid") or item.get("item_id") or item.get("id")
            shop_id = item.get("shopid") or item.get("shop_id")
            link = item.get("link", "") or item.get("url", "")

            if not link and item_id:
                if shop_id:
                    link = f"https://shopee.com.br/product/{shop_id}/{item_id}"
                else:
                    link = f"https://shopee.com.br/product-i.{item_id}"

            if not link:
                return None
            if not link.startswith("http"):
                link = f"https://shopee.com.br{link}"

            # Preço (Shopee armazena em centavos * 1000)
            price_raw = item.get("price", 0) or item.get("flash_sale_price", 0) or item.get("item_group_price", 0)
            original_raw = item.get("price_before_discount", 0) or item.get("origin_price", 0)

            divisor = 100000 if price_raw > 100000 else 100 if price_raw > 10000 else 1
            price = price_raw / divisor if price_raw else None
            original_price = original_raw / divisor if original_raw else None

            discount = item.get("discount", "") or item.get("raw_discount", 0)
            discount_pct = None
            if isinstance(discount, str):
                disc_match = re.search(r"(\d+)", discount)
                if disc_match:
                    discount_pct = float(disc_match.group(1))
            elif isinstance(discount, (int, float)) and discount > 0:
                discount_pct = float(discount)

            image = item.get("image", "") or item.get("cover", "")
            if image and not image.startswith("http"):
                image = f"https://cf.shopee.com.br/file/{image}"

            return Product(
                title=self._clean_title(str(title)),
                link=link,
                store=self.STORE,
                price=price if price and price > 0 else None,
                original_price=original_price if original_price and original_price > 0 else None,
                discount_pct=discount_pct,
                image_url=str(image) if image else "",
                free_shipping=bool(item.get("free_shipping", False) or item.get("show_free_shipping", False)),
            )
        except Exception:
            return None

    def _extract_from_html(self, html: str) -> list[Product]:
        """Extrai ofertas via parsing HTML."""
        products: list[Product] = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            selectors = [
                "[class*='flash-sale-item']",
                "[class*='shopee-search-item']",
                "[data-sqe='item']",
                "a[href*='/product/']",
            ]

            cards = []
            for selector in selectors:
                cards = soup.select(selector)
                if cards:
                    break

            for card in cards[:20]:
                product = self._parse_html_card(card)
                if product:
                    products.append(product)

        except Exception as e:
            self._logger.debug(f"Erro ao parsear HTML da Shopee: {e}")

        return products

    def _parse_html_card(self, card) -> Optional[Product]:
        """Parseia um card HTML da Shopee."""
        try:
            link_el = card if card.name == "a" else card.select_one("a[href]")
            if not link_el:
                return None

            href = link_el.get("href", "")
            if not href:
                return None
            if not href.startswith("http"):
                href = f"https://shopee.com.br{href}"

            title_el = card.select_one(
                "[class*='name'], [class*='title'], .item-card-name"
            )
            title = title_el.get_text(strip=True) if title_el else ""
            if not title or len(title) < 5:
                return None

            price_el = card.select_one("[class*='price'], span[class*='Price']")
            price = None
            if price_el:
                price = self._parse_price(price_el.get_text(strip=True))

            return Product(
                title=self._clean_title(title),
                link=href,
                store=self.STORE,
                price=price,
            )
        except Exception:
            return None
