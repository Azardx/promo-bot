"""
Scraper da Shopee Brasil via API v4 e parsing HTML.

A Shopee possui proteção anti-bot robusta. Este scraper usa
múltiplas estratégias:
1. API v4 de flash sale (ofertas relâmpago)
2. API v4 de busca por termos populares
3. Parsing HTML da página de ofertas do dia
4. Fallback via dados JSON embutidos no HTML

CORREÇÕES v2.1:
- Reescrito para usar API v4 ao invés de __INITIAL_STATE__
- Múltiplas estratégias de coleta com fallbacks
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
    """Scraper da Shopee Brasil via API v4 e HTML parsing."""

    STORE = Store.SHOPEE
    NAME = "shopee"
    BASE_URL = "https://shopee.com.br"

    # API v4 endpoints
    API_BASE = "https://shopee.com.br/api/v4"
    FLASH_SALE_SESSIONS_URL = f"{API_BASE}/flash_sale/get_all_sessions"
    FLASH_SALE_ITEMS_URL = f"{API_BASE}/flash_sale/get_all_itemids"
    FLASH_SALE_BATCH_URL = f"{API_BASE}/flash_sale/flash_sale_batch_get_items"
    SEARCH_URL = f"{API_BASE}/search/search_items"
    RECOMMEND_URL = f"{API_BASE}/recommend/recommend"

    # Páginas HTML para fallback
    DEALS_PAGES = [
        "https://shopee.com.br/flash_sale",
        "https://shopee.com.br/daily_discover",
        "https://shopee.com.br/",
    ]

    # Headers para API
    API_HEADERS = {
        "Accept": "application/json",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer": "https://shopee.com.br/",
        "X-Requested-With": "XMLHttpRequest",
        "X-Shopee-Language": "pt-BR",
    }

    # Headers para HTML
    HTML_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://shopee.com.br/",
    }

    # URL de imagem da Shopee
    IMAGE_CDN = "https://down-br.img.susercontent.com/file"

    # Termos de busca para encontrar promoções
    SEARCH_TERMS = [
        "oferta do dia",
        "promoção",
        "desconto",
        "super oferta",
    ]

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas da Shopee via múltiplas estratégias."""
        products: list[Product] = []
        seen_ids: set[str] = set()

        # Estratégia 1: Flash Sale via API
        try:
            flash_products = await self._scrape_flash_sale()
            for p in flash_products:
                item_id = p.extra.get("shopee_item_id", p.link)
                if item_id not in seen_ids:
                    products.append(p)
                    seen_ids.add(item_id)
            self._logger.info(f"Flash sale: {len(flash_products)} ofertas")
        except Exception as e:
            self._logger.warning(f"Erro na flash sale: {e}")

        # Estratégia 2: Busca por termos populares via API
        if len(products) < 10:
            try:
                search_products = await self._scrape_search()
                for p in search_products:
                    item_id = p.extra.get("shopee_item_id", p.link)
                    if item_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(item_id)
                self._logger.info(f"Busca: {len(search_products)} ofertas")
            except Exception as e:
                self._logger.warning(f"Erro na busca: {e}")

        # Estratégia 3: HTML parsing (fallback)
        if len(products) < 5:
            try:
                html_products = await self._scrape_html()
                for p in html_products:
                    item_id = p.extra.get("shopee_item_id", p.link)
                    if item_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(item_id)
                self._logger.info(f"HTML parsing: {len(html_products)} ofertas")
            except Exception as e:
                self._logger.warning(f"Erro no HTML parsing: {e}")

        self._logger.info(f"Shopee: {len(products)} ofertas totais coletadas")
        return products

    # ------------------------------------------------------------------
    # Estratégia 1: Flash Sale via API v4
    # ------------------------------------------------------------------

    async def _scrape_flash_sale(self) -> list[Product]:
        """Coleta ofertas da flash sale via API v4."""
        products: list[Product] = []

        # 1. Busca sessões de flash sale ativas
        sessions = await self._get_flash_sessions()
        if not sessions:
            self._logger.debug("Nenhuma sessão de flash sale encontrada")
            return products

        # 2. Para cada sessão ativa, busca os itens
        for session in sessions[:2]:  # Limita a 2 sessões
            promotion_id = session.get("promotionid", 0)
            if not promotion_id:
                continue

            # Busca IDs dos itens
            item_ids = await self._get_flash_item_ids(promotion_id)
            if not item_ids:
                continue

            # Busca detalhes dos itens em lote
            batch_products = await self._get_flash_items_batch(
                promotion_id, item_ids[:30]
            )
            products.extend(batch_products)

        return products

    async def _get_flash_sessions(self) -> list[dict]:
        """Busca sessões de flash sale ativas."""
        try:
            data = await self._http.fetch_json(
                self.FLASH_SALE_SESSIONS_URL,
                headers=self.API_HEADERS,
            )
            if not data:
                return []

            sessions = data.get("data", {}).get("sessions", [])
            if not sessions:
                # Tenta formato alternativo
                sessions = data.get("data", [])
                if isinstance(sessions, dict):
                    sessions = sessions.get("sessions", [])

            if not isinstance(sessions, list):
                return []

            # Filtra sessões ativas (em andamento ou próximas)
            now = int(time.time())
            active = []
            for s in sessions:
                if not isinstance(s, dict):
                    continue
                start = s.get("start_time", 0)
                end = s.get("end_time", 0)
                if start <= now <= end:
                    active.append(s)
                elif start > now and start - now < 3600:
                    active.append(s)

            return active or (sessions[:2] if sessions else [])

        except Exception as e:
            self._logger.debug(f"Erro ao buscar sessões flash: {e}")
            return []

    async def _get_flash_item_ids(self, promotion_id: int) -> list[int]:
        """Busca IDs dos itens de uma flash sale."""
        try:
            params = {
                "need_personalize": "true",
                "promotionid": str(promotion_id),
                "sort_soldout": "false",
            }
            data = await self._http.fetch_json(
                self.FLASH_SALE_ITEMS_URL,
                headers=self.API_HEADERS,
                params=params,
            )
            if not data:
                return []

            item_data = data.get("data", {})
            if isinstance(item_data, dict):
                item_ids = item_data.get("itemids", [])
                if not item_ids:
                    items = item_data.get("items", [])
                    item_ids = [i.get("itemid", 0) for i in items if isinstance(i, dict) and i.get("itemid")]
            else:
                item_ids = []

            return item_ids

        except Exception as e:
            self._logger.debug(f"Erro ao buscar item IDs flash: {e}")
            return []

    async def _get_flash_items_batch(
        self, promotion_id: int, item_ids: list[int]
    ) -> list[Product]:
        """Busca detalhes de itens da flash sale em lote."""
        products: list[Product] = []

        try:
            # POST request com body JSON
            url = self.FLASH_SALE_BATCH_URL
            body = {
                "promotionid": promotion_id,
                "categoryid": 0,
                "itemids": item_ids,
                "limit": len(item_ids),
                "with_dp_items": True,
            }

            # Usa fetch com JSON
            text = await self._http.fetch(
                url,
                method="POST",
                headers={**self.API_HEADERS, "Content-Type": "application/json"},
                json_data=body,
            )
            if not text:
                return products

            data = json.loads(text)
            items = data.get("data", {}).get("items", [])

            for item in items:
                if isinstance(item, dict):
                    product = self._parse_flash_item(item, promotion_id)
                    if product:
                        products.append(product)

        except Exception as e:
            self._logger.debug(f"Erro ao buscar batch flash: {e}")

        return products

    def _parse_flash_item(self, item: dict, promotion_id: int) -> Optional[Product]:
        """Parseia um item de flash sale."""
        try:
            item_id = item.get("itemid", 0)
            shop_id = item.get("shopid", 0)
            if not item_id or not shop_id:
                return None

            name = item.get("name", "").strip()
            if not name or len(name) < 10:
                return None

            # Preço (em centavos na API — Shopee usa price/100000)
            price_raw = item.get("price", 0)
            price = self._normalize_shopee_price(price_raw)

            # Preço original
            orig_raw = item.get("price_before_discount", 0)
            original_price = self._normalize_shopee_price(orig_raw)

            # Desconto
            discount_pct = None
            discount = item.get("raw_discount", 0) or item.get("discount", 0)
            if discount:
                discount_pct = float(discount)

            # Imagem
            image_hash = item.get("image", "")
            image_url = f"{self.IMAGE_CDN}/{image_hash}" if image_hash else ""

            # Link do produto
            slug = re.sub(r'[^a-zA-Z0-9\s-]', '', name.lower())
            slug = re.sub(r'\s+', '-', slug)[:80]
            link = f"{self.BASE_URL}/{slug}-i.{shop_id}.{item_id}"

            # Vendidos
            sold = 0
            flash_stock = item.get("flash_sale_stock", 0)
            stock = item.get("stock", 0)
            if flash_stock and stock:
                sold = max(flash_stock - stock, 0)
            if not sold:
                sold = item.get("sold", 0)

            return Product(
                title=self._clean_title(name),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                free_shipping=bool(item.get("show_free_shipping", False)),
                extra={
                    "shopee_item_id": str(item_id),
                    "shopee_shop_id": str(shop_id),
                    "promotion_id": str(promotion_id),
                    "sold": sold,
                    "is_flash_sale": True,
                },
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear flash item: {e}")
            return None

    def _normalize_shopee_price(self, raw_price) -> Optional[float]:
        """Normaliza preço da Shopee (que pode vir em diferentes escalas)."""
        if not raw_price:
            return None
        try:
            val = float(raw_price)
            if val <= 0:
                return None
            # Shopee API retorna preço * 100000
            if val > 100000:
                return round(val / 100000, 2)
            # Ou preço * 100
            elif val > 10000:
                return round(val / 100, 2)
            return round(val, 2)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Estratégia 2: Busca por termos via API v4
    # ------------------------------------------------------------------

    async def _scrape_search(self) -> list[Product]:
        """Busca promoções via API de busca."""
        products: list[Product] = []

        for term in self.SEARCH_TERMS[:2]:
            try:
                params = {
                    "keyword": term,
                    "limit": "30",
                    "newest": "0",
                    "by": "sales",
                    "order": "desc",
                    "page_type": "search",
                    "scenario": "PAGE_GLOBAL_SEARCH",
                    "version": "2",
                }

                data = await self._http.fetch_json(
                    self.SEARCH_URL,
                    headers=self.API_HEADERS,
                    params=params,
                )
                if not data:
                    continue

                items = data.get("items", [])
                if not items:
                    items_data = data.get("data", {})
                    if isinstance(items_data, dict):
                        items = items_data.get("items", [])

                for item_wrapper in items:
                    if not isinstance(item_wrapper, dict):
                        continue
                    item = item_wrapper.get("item_basic", item_wrapper)
                    product = self._parse_search_item(item)
                    if product:
                        products.append(product)

                if products:
                    break

            except Exception as e:
                self._logger.debug(f"Erro na busca '{term}': {e}")

        return products

    def _parse_search_item(self, item: dict) -> Optional[Product]:
        """Parseia um item de resultado de busca."""
        try:
            item_id = item.get("itemid", 0)
            shop_id = item.get("shopid", 0)
            if not item_id or not shop_id:
                return None

            name = item.get("name", "").strip()
            if not name or len(name) < 10:
                return None

            # Preço
            price = self._normalize_shopee_price(item.get("price", 0))
            original_price = self._normalize_shopee_price(
                item.get("price_before_discount", 0)
            )

            # Desconto
            discount_pct = None
            discount = item.get("raw_discount", 0) or item.get("discount", "")
            if discount:
                try:
                    discount_pct = float(str(discount).replace("%", ""))
                except ValueError:
                    pass

            # Imagem
            image_hash = item.get("image", "")
            image_url = f"{self.IMAGE_CDN}/{image_hash}" if image_hash else ""

            # Link
            slug = re.sub(r'[^a-zA-Z0-9\s-]', '', name.lower())
            slug = re.sub(r'\s+', '-', slug)[:80]
            link = f"{self.BASE_URL}/{slug}-i.{shop_id}.{item_id}"

            # Cupom da loja
            coupon = None
            voucher = item.get("voucher_info", {})
            if isinstance(voucher, dict) and voucher:
                coupon_label = voucher.get("label", "")
                if coupon_label:
                    coupon = coupon_label

            # Frete grátis
            free_shipping = bool(item.get("show_free_shipping", False))

            return Product(
                title=self._clean_title(name),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                coupon_code=coupon,
                free_shipping=free_shipping,
                extra={
                    "shopee_item_id": str(item_id),
                    "shopee_shop_id": str(shop_id),
                    "sold": item.get("sold", 0),
                    "historical_sold": item.get("historical_sold", 0),
                    "rating_star": item.get("item_rating", {}).get("rating_star", 0)
                    if isinstance(item.get("item_rating"), dict)
                    else 0,
                },
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear search item: {e}")
            return None

    # ------------------------------------------------------------------
    # Estratégia 3: HTML parsing (fallback)
    # ------------------------------------------------------------------

    async def _scrape_html(self) -> list[Product]:
        """Coleta ofertas via parsing HTML."""
        products: list[Product] = []

        for url in self.DEALS_PAGES:
            try:
                html = await self._http.fetch(url, headers=self.HTML_HEADERS)
                if not html:
                    continue

                # Tenta extrair dados JSON embutidos no HTML
                json_products = self._extract_json_data(html)
                if json_products:
                    products.extend(json_products)
                    break

                # Tenta parsing HTML direto
                html_products = self._extract_from_html(html)
                if html_products:
                    products.extend(html_products)
                    break

            except Exception as e:
                self._logger.debug(f"Erro ao acessar {url}: {e}")

        return products

    def _extract_json_data(self, html: str) -> list[Product]:
        """Extrai dados JSON embutidos no HTML da Shopee."""
        products: list[Product] = []

        # Busca blocos de dados JSON no HTML
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>',
            r'window\.__INITIAL_DATA__\s*=\s*(\{.*?\})\s*;?\s*</script>',
            r'window\.rawData\s*=\s*(\{.*?\})\s*;?\s*</script>',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.DOTALL)
            for match in matches[:1]:
                try:
                    data = json.loads(match)
                    items = self._find_items_in_data(data)
                    for item in items[:20]:
                        product = self._parse_search_item(item)
                        if product:
                            products.append(product)
                except (json.JSONDecodeError, Exception):
                    continue

        # Busca padrões de item inline
        item_pattern = re.finditer(
            r'"itemid"\s*:\s*(\d+).*?"shopid"\s*:\s*(\d+).*?"name"\s*:\s*"([^"]+)".*?"price"\s*:\s*(\d+)',
            html,
            re.DOTALL,
        )
        for m in item_pattern:
            try:
                item_id = m.group(1)
                shop_id = m.group(2)
                name = m.group(3)
                price = self._normalize_shopee_price(int(m.group(4)))

                if name and len(name) >= 10:
                    slug = re.sub(r'[^a-zA-Z0-9\s-]', '', name.lower())
                    slug = re.sub(r'\s+', '-', slug)[:80]
                    link = f"{self.BASE_URL}/{slug}-i.{shop_id}.{item_id}"

                    products.append(Product(
                        title=self._clean_title(name),
                        link=link,
                        store=self.STORE,
                        price=price,
                        extra={
                            "shopee_item_id": item_id,
                            "shopee_shop_id": shop_id,
                            "source": "html_inline",
                        },
                    ))
            except Exception:
                continue

        return products

    def _find_items_in_data(self, data, depth: int = 0) -> list[dict]:
        """Busca recursivamente itens de produto em estrutura JSON."""
        items = []
        if depth > 5:
            return items

        if isinstance(data, dict):
            if "itemid" in data and "name" in data:
                items.append(data)
            for value in data.values():
                if isinstance(value, (dict, list)):
                    items.extend(self._find_items_in_data(value, depth + 1))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    items.extend(self._find_items_in_data(item, depth + 1))

        return items

    def _extract_from_html(self, html: str) -> list[Product]:
        """Extrai ofertas via BeautifulSoup."""
        products = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Busca links de produtos
            for link_el in soup.select("a[href*='-i.']")[:20]:
                try:
                    href = link_el.get("href", "")
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = f"{self.BASE_URL}{href}"

                    # Extrai IDs do link
                    id_match = re.search(r'-i\.(\d+)\.(\d+)', href)
                    if not id_match:
                        continue

                    shop_id = id_match.group(1)
                    item_id = id_match.group(2)

                    title = link_el.get_text(strip=True)
                    if len(title) < 10:
                        parent = link_el.parent
                        if parent:
                            title = parent.get_text(strip=True)[:200]
                    if len(title) < 10:
                        continue

                    # Imagem
                    image_url = ""
                    img_el = link_el.find("img")
                    if img_el:
                        src = img_el.get("src", "") or img_el.get("data-src", "")
                        if src and "susercontent" in src:
                            image_url = src

                    products.append(Product(
                        title=self._clean_title(title[:150]),
                        link=href,
                        store=self.STORE,
                        image_url=image_url,
                        extra={
                            "shopee_item_id": item_id,
                            "shopee_shop_id": shop_id,
                            "source": "html_bs4",
                        },
                    ))
                except Exception:
                    continue

        except Exception as e:
            self._logger.debug(f"Erro no BS4 parsing: {e}")

        return products
