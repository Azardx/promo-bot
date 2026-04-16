"""
Scraper da Shopee Brasil via API v4 e parsing HTML.

A Shopee possui proteção anti-bot robusta. Este scraper usa
múltiplas estratégias:
1. API v4 de flash sale (ofertas relâmpago)
2. API v4 de busca por termos populares
3. Parsing HTML da página de ofertas do dia

CORREÇÕES v2.3:
- Bypass de bloqueio de API via headers realistas e tratamento de preço (100000 base)
- Scraping de ofertas relâmpago (Flash Sales) com suporte a vouchers
- Extração de cupons e imagens melhorada
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class ShopeeScraper(BaseScraper):
    """Scraper da Shopee Brasil via API v4."""

    STORE = Store.SHOPEE
    NAME = "shopee"
    BASE_URL = "https://shopee.com.br"

    # API v4 endpoints
    API_BASE = "https://shopee.com.br/api/v4"
    FLASH_SALE_SESSIONS_URL = f"{API_BASE}/flash_sale/get_all_sessions"
    FLASH_SALE_ITEMS_URL = f"{API_BASE}/flash_sale/get_all_itemids"
    FLASH_SALE_BATCH_URL = f"{API_BASE}/flash_sale/flash_sale_batch_get_items"
    SEARCH_URL = f"{API_BASE}/search/search_items"

    # Headers realistas para Shopee
    SHOPEE_HEADERS = {
        "Accept": "application/json",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://shopee.com.br/",
        "X-Requested-With": "XMLHttpRequest",
        "X-Shopee-Language": "pt-BR",
        "X-Api-Source": "pc",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas da Shopee."""
        products: list[Product] = []
        seen_ids: set[str] = set()

        # 1. Tenta Ofertas Relâmpago (Flash Sales)
        try:
            flash_products = await self._scrape_flash_sales()
            for p in flash_products:
                item_id = str(p.extra.get("item_id", p.link))
                if item_id not in seen_ids:
                    products.append(p)
                    seen_ids.add(item_id)
            self._logger.info(f"Shopee: {len(flash_products)} ofertas relâmpago")
        except Exception as e:
            self._logger.debug(f"Erro Flash Sales Shopee: {e}")

        # 2. Tenta busca por termos populares
        if len(products) < 15:
            try:
                search_products = await self._scrape_search("ofertas")
                for p in search_products:
                    item_id = str(p.extra.get("item_id", p.link))
                    if item_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(item_id)
                self._logger.info(f"Shopee: {len(search_products)} ofertas via busca")
            except Exception as e:
                self._logger.debug(f"Erro busca Shopee: {e}")

        self._logger.info(f"Shopee: {len(products)} ofertas totais coletadas")
        return products

    async def _scrape_flash_sales(self) -> list[Product]:
        """Coleta itens das ofertas relâmpago atuais."""
        products = []
        
        # Pega IDs das ofertas relâmpago
        params = {"limit": 30, "offset": 0}
        data = await self._http.fetch_json(self.FLASH_SALE_ITEMS_URL, headers=self.SHOPEE_HEADERS, params=params)
        
        if not data or "data" not in data:
            return products

        item_ids = data["data"].get("itemids", [])
        if not item_ids:
            return products

        # Pega detalhes dos itens em lote
        batch_url = self.FLASH_SALE_BATCH_URL
        body = {
            "itemids": item_ids[:20],
            "limit": 20,
            "with_dp_items": True
        }
        
        text = await self._http.fetch(
            batch_url, 
            method="POST", 
            headers={**self.SHOPEE_HEADERS, "Content-Type": "application/json"},
            json_data=body
        )
        
        if not text:
            return products

        items_data = json.loads(text)
        for item in items_data.get("data", {}).get("items", []):
            product = self._parse_item(item)
            if product:
                products.append(product)

        return products

    async def _scrape_search(self, keyword: str) -> list[Product]:
        """Coleta itens via busca."""
        products = []
        params = {
            "by": "relevancy",
            "keyword": keyword,
            "limit": 30,
            "newest": 0,
            "order": "desc",
            "page_type": "search",
            "scenario": "PAGE_GLOBAL_SEARCH",
            "version": "2",
        }
        data = await self._http.fetch_json(self.SEARCH_URL, headers=self.SHOPEE_HEADERS, params=params)
        
        if not data or "items" not in data:
            return products

        for item_wrapper in data.get("items", []):
            item = item_wrapper.get("item_basic")
            if item:
                product = self._parse_item(item)
                if product:
                    products.append(product)

        return products

    def _parse_item(self, item: dict) -> Optional[Product]:
        """Converte um item da API Shopee em Product."""
        try:
            name = item.get("name")
            if not name: return None

            item_id = item.get("itemid")
            shop_id = item.get("shopid")
            if not item_id or not shop_id: return None

            link = f"{self.BASE_URL}/product/{shop_id}/{item_id}"

            # Preço Shopee: 100000 = R$ 1,00
            price = float(item.get("price", 0)) / 100000
            original_price = float(item.get("price_before_discount", 0)) / 100000
            
            if original_price <= price:
                original_price = None

            # Desconto
            discount_pct = None
            raw_discount = item.get("raw_discount") or item.get("discount")
            if raw_discount:
                if isinstance(raw_discount, str):
                    match = re.search(r"(\d+)", raw_discount)
                    if match: discount_pct = float(match.group(1))
                else:
                    discount_pct = float(raw_discount)

            # Imagem
            image_id = item.get("image")
            image_url = f"https://down-br.img.susercontent.com/file/{image_id}" if image_id else ""

            # Cupom
            coupon = None
            vouchers = item.get("voucher_info")
            if vouchers and isinstance(vouchers, dict):
                coupon = vouchers.get("voucher_code")

            return Product(
                title=self._clean_title(name),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                coupon_code=coupon,
                free_shipping=item.get("show_free_shipping", False),
                extra={
                    "item_id": item_id,
                    "shop_id": shop_id,
                }
            )
        except Exception:
            return None
