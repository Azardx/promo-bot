"""
Scraper de promoções da Shopee.

Utiliza a API interna de flash deals e busca da Shopee para coletar
ofertas com desconto significativo. Implementa estratégias para
contornar proteções anti-bot.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper


class ShopeeScraper(BaseScraper):
    """Scraper especializado para a Shopee Brasil."""

    STORE = Store.SHOPEE
    NAME = "shopee"
    BASE_URL = "https://shopee.com.br"

    # Endpoints da API interna da Shopee
    _FLASH_SALE_URL = "https://shopee.com.br/api/v4/flash_sale/get_all_itemids"
    _FLASH_SALE_ITEMS_URL = "https://shopee.com.br/api/v4/flash_sale/get_all_item_and_raw_discount"
    _SEARCH_URL = "https://shopee.com.br/api/v4/search/search_items"
    _DAILY_DISCOVER_URL = "https://shopee.com.br/api/v4/recommend/recommend"
    _DEALS_PAGE_URL = "https://shopee.com.br/flash_sale"

    async def _scrape(self) -> list[Product]:
        """Coleta promoções da Shopee via múltiplas estratégias."""
        products: list[Product] = []

        # Estratégia 1: Flash Sale via API
        flash_products = await self._scrape_flash_sale_api()
        products.extend(flash_products)

        # Estratégia 2: Busca por palavras-chave de promoção
        search_products = await self._scrape_search_deals()
        products.extend(search_products)

        # Estratégia 3: Página de ofertas do dia (HTML fallback)
        if len(products) < 5:
            html_products = await self._scrape_deals_html()
            products.extend(html_products)

        return products

    async def _scrape_flash_sale_api(self) -> list[Product]:
        """Coleta ofertas da Flash Sale via API interna."""
        products = []

        try:
            # Headers específicos para a API da Shopee
            headers = {
                "Referer": "https://shopee.com.br/flash_sale",
                "X-Requested-With": "XMLHttpRequest",
                "X-Shopee-Language": "pt-BR",
                "Accept": "application/json",
            }

            # Busca IDs dos itens em flash sale
            params = {
                "need_personalize": "true",
                "sort_soldout": "true",
                "limit": "50",
                "offset": "0",
            }

            data = await self._http.fetch_json(
                self._FLASH_SALE_URL,
                headers=headers,
                params=params,
            )

            if not data or "data" not in data:
                self._logger.debug("Flash Sale API nao retornou dados")
                return products

            items = data.get("data", {}).get("items", [])
            if not items:
                # Tenta formato alternativo
                items = data.get("data", {}).get("item_brief_list", [])

            for item in items[:30]:
                product = self._parse_flash_sale_item(item)
                if product:
                    products.append(product)

            self._logger.info(f"Shopee Flash Sale: {len(products)} itens coletados")

        except Exception as e:
            self._logger.warning(f"Erro na Flash Sale API: {e}")

        return products

    def _parse_flash_sale_item(self, item: dict) -> Optional[Product]:
        """Parseia um item da Flash Sale."""
        try:
            item_id = item.get("itemid") or item.get("item_id")
            shop_id = item.get("shopid") or item.get("shop_id")
            name = item.get("name", "") or item.get("item_name", "")

            if not all([item_id, shop_id, name]):
                return None

            # Preços na Shopee são em centavos (dividir por 100000)
            price_raw = item.get("price", 0) or item.get("item_group_price", 0)
            original_raw = item.get("price_before_discount", 0) or item.get("origin_price", 0)

            # Detecta o divisor correto
            divisor = 100000 if price_raw > 100000 else 100 if price_raw > 10000 else 1
            price = price_raw / divisor if price_raw else None
            original_price = original_raw / divisor if original_raw else None

            discount = item.get("raw_discount", 0) or item.get("discount", 0)

            link = f"https://shopee.com.br/product/{shop_id}/{item_id}"

            image = item.get("image", "")
            if image and not image.startswith("http"):
                image = f"https://cf.shopee.com.br/file/{image}"

            return Product(
                title=self._clean_title(name),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=float(discount) if discount else None,
                image_url=image,
                free_shipping=bool(item.get("free_shipping", False)),
            )
        except Exception as e:
            self._logger.debug(f"Erro ao parsear item Flash Sale: {e}")
            return None

    async def _scrape_search_deals(self) -> list[Product]:
        """Busca promoções via API de busca da Shopee."""
        products = []
        search_terms = ["oferta do dia", "promoção", "desconto", "flash sale"]

        for term in search_terms[:2]:  # Limita para não sobrecarregar
            try:
                headers = {
                    "Referer": f"https://shopee.com.br/search?keyword={term}",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                }

                params = {
                    "keyword": term,
                    "limit": "20",
                    "newest": "0",
                    "order": "desc",
                    "page_type": "search",
                    "by": "relevancy",
                    "scenario": "PAGE_GLOBAL_SEARCH",
                }

                data = await self._http.fetch_json(
                    self._SEARCH_URL,
                    headers=headers,
                    params=params,
                )

                if not data:
                    continue

                items = data.get("items", []) or data.get("data", {}).get("items", [])
                for item_wrapper in items[:15]:
                    item = item_wrapper.get("item_basic", item_wrapper)
                    product = self._parse_search_item(item)
                    if product:
                        products.append(product)

            except Exception as e:
                self._logger.debug(f"Erro na busca Shopee '{term}': {e}")

        return products

    def _parse_search_item(self, item: dict) -> Optional[Product]:
        """Parseia um item da busca."""
        try:
            item_id = item.get("itemid") or item.get("item_id")
            shop_id = item.get("shopid") or item.get("shop_id")
            name = item.get("name", "") or item.get("item_name", "")

            if not all([item_id, shop_id, name]):
                return None

            price_raw = item.get("price", 0)
            original_raw = item.get("price_before_discount", 0)

            divisor = 100000 if price_raw > 100000 else 100 if price_raw > 10000 else 1
            price = price_raw / divisor if price_raw else None
            original_price = original_raw / divisor if original_raw else None

            # Só inclui se tiver desconto real
            if original_price and price and original_price > price:
                discount = round(((original_price - price) / original_price) * 100, 1)
                if discount < 10:
                    return None  # Desconto insignificante
            else:
                discount = None

            link = f"https://shopee.com.br/product/{shop_id}/{item_id}"

            return Product(
                title=self._clean_title(name),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount,
                free_shipping=bool(item.get("show_free_shipping", False)),
            )
        except Exception:
            return None

    async def _scrape_deals_html(self) -> list[Product]:
        """Fallback: coleta via parsing HTML da página de ofertas."""
        products = []

        try:
            html = await self._http.fetch(self._DEALS_PAGE_URL)
            if not html:
                return products

            # Tenta extrair dados JSON embutidos no HTML
            json_match = re.search(
                r'<script[^>]*>window\.__INITIAL_STATE__\s*=\s*({.*?})\s*</script>',
                html, re.DOTALL
            )
            if json_match:
                try:
                    state = json.loads(json_match.group(1))
                    items = (
                        state.get("flashSale", {}).get("items", [])
                        or state.get("items", [])
                    )
                    for item in items[:20]:
                        product = self._parse_flash_sale_item(item)
                        if product:
                            products.append(product)
                except json.JSONDecodeError:
                    pass

            # Fallback: parsing HTML com BeautifulSoup
            if not products:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(html, "html.parser")
                for card in soup.select("[data-sqe='item'],.flash-sale-item-card"):
                    try:
                        title_el = card.select_one(
                            ".item-card-name, .flash-sale-item-card__item-name"
                        )
                        price_el = card.select_one(
                            ".item-card-price, .flash-sale-item-card__current-price"
                        )
                        link_el = card.select_one("a[href]")

                        if title_el and link_el:
                            href = link_el.get("href", "")
                            if not href.startswith("http"):
                                href = f"https://shopee.com.br{href}"

                            products.append(Product(
                                title=self._clean_title(title_el.get_text(strip=True)),
                                link=href,
                                store=self.STORE,
                                price=self._parse_price(
                                    price_el.get_text(strip=True) if price_el else ""
                                ),
                            ))
                    except Exception:
                        continue

        except Exception as e:
            self._logger.warning(f"Erro no fallback HTML Shopee: {e}")

        return products
