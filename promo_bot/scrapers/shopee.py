"""
Scraper da Shopee Brasil — PromoBot v2.3+

ESTRATÉGIAS (ordem de prioridade):
1. API v4 de Flash Sales  →  mais rápida quando disponível
2. API v4 de Busca        →  keywords populares de eletrônicos
3. HTML da página de Flash Sale  →  fallback via curl_cffi

CORREÇÕES v2.3+:
- Headers melhorados para reduzir bloqueios da API Shopee
- Conversão correta de preços (Shopee usa centavos × 100.000)
- Fallback HTML robusto com suporte a curl_cffi
- Filtro de produtos sem preço ou sem desconto real
- Extração de imagem com URL correta do CDN BR
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class ShopeeScraper(BaseScraper):
    """Scraper da Shopee Brasil com múltiplas estratégias."""

    STORE    = Store.SHOPEE
    NAME     = "shopee"
    BASE_URL = "https://shopee.com.br"

    _API_BASE            = "https://shopee.com.br/api/v4"
    _FLASH_ITEMS_URL     = f"{_API_BASE}/flash_sale/get_all_itemids"
    _FLASH_BATCH_URL     = f"{_API_BASE}/flash_sale/flash_sale_batch_get_items"
    _SEARCH_URL          = f"{_API_BASE}/search/search_items"
    _FLASH_PAGE_URL      = "https://shopee.com.br/flash_sale"

    # Keywords de alta conversão para a API de busca
    _SEARCH_KEYWORDS: tuple[str, ...] = (
        "notebook",
        "smartphone",
        "fone bluetooth",
        "ssd",
        "smartwatch",
    )

    # Headers com cookies mínimos para não ser barrado
    _API_HEADERS: dict[str, str] = {
        "Accept":            "application/json",
        "Accept-Language":   "pt-BR,pt;q=0.9",
        "Referer":           "https://shopee.com.br/",
        "X-Requested-With":  "XMLHttpRequest",
        "X-Shopee-Language": "pt-BR",
        "X-Api-Source":      "pc",
        "X-Csrftoken":       "nocsrf",
        "Cookie":            "SPC_CDS=; SPC_CDS_VER=2;",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    # ── Ponto de entrada ──────────────────────────────────────────────────────

    async def _scrape(self) -> list[Product]:
        products: list[Product] = []
        seen_ids: set[str]      = set()

        # 1. Flash Sales via API
        try:
            flash = await self._scrape_flash_api()
            for p in flash:
                key = str(p.extra.get("item_id", ""))
                if key and key not in seen_ids:
                    products.append(p)
                    seen_ids.add(key)
        except Exception as exc:
            self._logger.debug(f"Shopee flash API falhou: {exc}")

        # 2. Busca por keywords populares
        if len(products) < 10:
            for keyword in self._SEARCH_KEYWORDS:
                try:
                    kw_products = await self._scrape_search(keyword)
                    for p in kw_products:
                        key = str(p.extra.get("item_id", ""))
                        if key and key not in seen_ids:
                            products.append(p)
                            seen_ids.add(key)
                    if len(products) >= 25:
                        break
                except Exception as exc:
                    self._logger.debug(f"Shopee busca '{keyword}' falhou: {exc}")

        # 3. Fallback HTML da página de flash sale
        if len(products) < 5:
            try:
                html_products = await self._scrape_html_flash()
                for p in html_products:
                    key = str(p.extra.get("item_id", p.link))
                    if key not in seen_ids:
                        products.append(p)
                        seen_ids.add(key)
            except Exception as exc:
                self._logger.debug(f"Shopee HTML fallback falhou: {exc}")

        self._logger.info(f"Shopee: {len(products)} ofertas coletadas")
        return products

    # ── Estratégia 1: Flash Sales API ─────────────────────────────────────────

    async def _scrape_flash_api(self) -> list[Product]:
        """Coleta itens da flash sale via API v4."""
        # Passo 1: obtém IDs dos itens em flash sale
        params = {"limit": 30, "offset": 0}
        data   = await self._http.fetch_json(
            self._FLASH_ITEMS_URL, headers=self._API_HEADERS, params=params
        )
        if not data:
            return []

        item_ids = (
            (data.get("data") or {}).get("itemids") or []
        )
        if not item_ids:
            self._logger.debug("Shopee: nenhum item de flash sale")
            return []

        # Passo 2: detalhes em lote
        body = {
            "itemids":      item_ids[:20],
            "limit":        20,
            "with_dp_items": True,
        }
        text = await self._http.fetch(
            self._FLASH_BATCH_URL,
            method="POST",
            headers={**self._API_HEADERS, "Content-Type": "application/json"},
            json_data=body,
        )
        if not text:
            return []

        try:
            batch = json.loads(text)
        except json.JSONDecodeError:
            return []

        products: list[Product] = []
        for item in (batch.get("data") or {}).get("items") or []:
            p = self._parse_item(item)
            if p:
                products.append(p)
        return products

    # ── Estratégia 2: Busca ───────────────────────────────────────────────────

    async def _scrape_search(self, keyword: str) -> list[Product]:
        """Busca produtos com desconto via API de search."""
        params = {
            "by":         "relevancy",
            "keyword":    keyword,
            "limit":      "20",
            "newest":     "0",
            "order":      "desc",
            "page_type":  "search",
            "scenario":   "PAGE_GLOBAL_SEARCH",
            "version":    "2",
        }
        data = await self._http.fetch_json(
            self._SEARCH_URL, headers=self._API_HEADERS, params=params
        )
        if not data:
            return []

        products: list[Product] = []
        for wrapper in data.get("items") or []:
            item = wrapper.get("item_basic") or wrapper
            p    = self._parse_item(item)
            if p:
                products.append(p)
        return products

    # ── Estratégia 3: HTML Flash Sale ─────────────────────────────────────────

    async def _scrape_html_flash(self) -> list[Product]:
        """Parseia a página de flash sale via HTML (usa curl_cffi)."""
        html = await self._http.fetch(self._FLASH_PAGE_URL)
        if not html:
            return []

        # Tenta extrair JSON do estado inicial (React/Next)
        for pattern in (
            r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',
            r'window\.__NEXT_DATA__\s*=\s*(\{.*?\});',
        ):
            m = re.search(pattern, html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    products = self._parse_initial_state(data)
                    if products:
                        return products
                except json.JSONDecodeError:
                    pass

        # Fallback: BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        return self._parse_html_cards(soup)

    def _parse_initial_state(self, data: dict) -> list[Product]:
        """Extrai produtos do estado inicial JavaScript."""
        products: list[Product] = []
        # Tenta caminhos comuns
        items = (
            data.get("flashSale", {}).get("items")
            or data.get("items")
            or []
        )
        for item in items[:30]:
            p = self._parse_item(item)
            if p:
                products.append(p)
        return products

    def _parse_html_cards(self, soup: BeautifulSoup) -> list[Product]:
        """Extrai produtos de cards HTML genéricos."""
        products: list[Product] = []
        cards = soup.select(
            "[class*='flash-sale-item'], [class*='FlashSaleItem'], "
            "[class*='product-item'], .shopee-flash-sale-products__product-card"
        )
        for card in cards[:30]:
            try:
                link_el = card.select_one("a[href]")
                if not link_el:
                    continue
                href    = link_el.get("href", "")
                if not href.startswith("http"):
                    href = f"{self.BASE_URL}{href}"

                title_el = card.select_one("[class*='name'], [class*='title'], h2, h3")
                title    = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                price_el = card.select_one("[class*='price']")
                price    = self._parse_price(price_el.get_text(strip=True)) if price_el else None

                img_el   = card.select_one("img[src]")
                img_url  = img_el.get("src", "") if img_el else ""

                # Tenta extrair IDs do link
                m = re.search(r"/(\d+)/(\d+)", href)
                shop_id, item_id = (m.group(1), m.group(2)) if m else ("", "")

                products.append(Product(
                    title=self._clean_title(title),
                    link=href,
                    store=self.STORE,
                    price=price,
                    image_url=img_url,
                    extra={
                        "shop_id": shop_id,
                        "item_id": item_id,
                        "source":  "shopee_html",
                    },
                ))
            except Exception:
                pass
        return products

    # ── Parser de item da API ─────────────────────────────────────────────────

    def _parse_item(self, item: dict) -> Optional[Product]:
        """
        Converte um item bruto da API Shopee em Product.

        Preços na Shopee são armazenados em centavos × 100.000.
        Ex: 1234500000 → R$ 12,34 (divide por 100.000)
        """
        try:
            name = (item.get("name") or "").strip()
            if not name:
                return None

            item_id = item.get("itemid") or item.get("item_id")
            shop_id = item.get("shopid") or item.get("shop_id")
            if not item_id or not shop_id:
                return None

            link = f"{self.BASE_URL}/product/{shop_id}/{item_id}"

            # Preço atual (centavos × 100.000 → reais)
            raw_price    = item.get("price") or item.get("price_min") or 0
            price        = float(raw_price) / 100_000

            raw_original = item.get("price_before_discount") or item.get("original_price") or 0
            original_price = float(raw_original) / 100_000 if raw_original else None

            if not price or price <= 0:
                return None
            if original_price and original_price <= price:
                original_price = None

            # Desconto
            discount_pct: Optional[float] = None
            raw_disc = item.get("raw_discount") or item.get("discount")
            if raw_disc:
                if isinstance(raw_disc, (int, float)):
                    discount_pct = float(raw_disc)
                elif isinstance(raw_disc, str):
                    m = re.search(r"(\d+)", raw_disc)
                    if m:
                        discount_pct = float(m.group(1))
            # Calcula se não veio da API
            if not discount_pct and original_price and original_price > price:
                discount_pct = round((1 - price / original_price) * 100, 1)

            # Imagem — CDN Shopee BR
            image_id  = item.get("image") or item.get("images", [None])[0]
            image_url = (
                f"https://down-br.img.susercontent.com/file/{image_id}"
                if image_id
                else ""
            )

            # Cupom/voucher
            coupon: Optional[str] = None
            voucher = item.get("voucher_info") or {}
            if isinstance(voucher, dict):
                coupon = voucher.get("voucher_code")

            # Frete grátis
            free_shipping = bool(
                item.get("show_free_shipping")
                or item.get("free_shipping")
                or (item.get("shipping_icon_type") == "free_shipping")
            )

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
                    "item_id": str(item_id),
                    "shop_id": str(shop_id),
                    "source":  "shopee_api",
                },
            )
        except Exception as exc:
            self._logger.debug(f"Shopee parse_item falhou: {exc}")
            return None
