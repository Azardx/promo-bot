"""
Scraper da Terabyte Shop via API de catálogo e HTML.

A Terabyte Shop é uma das maiores lojas de hardware e informática
do Brasil. Este scraper coleta ofertas via:
1. Página de promoções/ofertas do dia
2. Busca por produtos em destaque
3. Parsing HTML como fallback

Extrai: título, preço, preço original, desconto, imagem, cupom,
frete grátis e categoria do produto.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class TerabyteScraper(BaseScraper):
    """Scraper da Terabyte Shop via HTML parsing."""

    STORE = Store.TERABYTE
    NAME = "terabyte"
    BASE_URL = "https://www.terabyteshop.com.br"

    # Páginas de ofertas
    DEALS_URLS = [
        "https://www.terabyteshop.com.br/promocoes",
        "https://www.terabyteshop.com.br/ofertas-do-dia",
        "https://www.terabyteshop.com.br/produtos-mais-vendidos",
    ]

    # Headers
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.terabyteshop.com.br/",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas da Terabyte Shop."""
        products: list[Product] = []
        seen_links: set[str] = set()

        for url in self.DEALS_URLS:
            try:
                page_products = await self._scrape_page(url)
                for p in page_products:
                    if p.link not in seen_links:
                        products.append(p)
                        seen_links.add(p.link)
                if len(products) >= 15:
                    break
            except Exception as e:
                self._logger.warning(f"Erro ao acessar {url}: {e}")

        self._logger.info(f"Terabyte: {len(products)} ofertas coletadas")
        return products

    async def _scrape_page(self, url: str) -> list[Product]:
        """Coleta ofertas de uma página da Terabyte."""
        products: list[Product] = []

        html = await self._http.fetch(url, headers=self.HEADERS)
        if not html:
            self._logger.warning(f"Falha ao acessar {url}")
            return products

        # Estratégia 1: JSON-LD (dados estruturados)
        json_ld_products = self._extract_from_json_ld(html)
        if json_ld_products:
            products.extend(json_ld_products)

        # Estratégia 2: HTML parsing
        if not products:
            html_products = self._extract_from_html(html)
            products.extend(html_products)

        return products

    def _extract_from_json_ld(self, html: str) -> list[Product]:
        """Extrai ofertas de dados JSON-LD."""
        products: list[Product] = []

        try:
            pattern = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
            matches = re.findall(pattern, html, re.DOTALL)

            for match in matches:
                try:
                    data = json.loads(match)
                except json.JSONDecodeError:
                    continue

                # Pode ser uma lista de produtos ou um único produto
                items = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    if data.get("@type") == "Product":
                        items = [data]
                    elif data.get("@type") == "ItemList":
                        items = data.get("itemListElement", [])
                    elif "mainEntity" in data:
                        entity = data["mainEntity"]
                        if isinstance(entity, list):
                            items = entity
                        elif isinstance(entity, dict):
                            items = entity.get("itemListElement", [entity])

                for item in items[:25]:
                    product = self._parse_json_ld_item(item)
                    if product:
                        products.append(product)

        except Exception as e:
            self._logger.debug(f"Erro ao extrair JSON-LD Terabyte: {e}")

        return products

    def _parse_json_ld_item(self, item: dict) -> Optional[Product]:
        """Parseia um item JSON-LD."""
        try:
            # Pode estar dentro de "item"
            if "item" in item and isinstance(item["item"], dict):
                item = item["item"]

            name = item.get("name", "").strip()
            if not name or len(name) < 10:
                return None

            url = item.get("url", "")
            if not url:
                return None
            if not url.startswith("http"):
                url = f"{self.BASE_URL}{url}"

            # Preço
            price = None
            original_price = None
            offers = item.get("offers", {})
            if isinstance(offers, dict):
                price_str = offers.get("price", "")
                if price_str:
                    try:
                        price = float(price_str)
                    except (ValueError, TypeError):
                        price = self._parse_price(str(price_str))

                # Preço original pode estar em highPrice
                high_price = offers.get("highPrice", "")
                if high_price:
                    try:
                        original_price = float(high_price)
                    except (ValueError, TypeError):
                        pass
            elif isinstance(offers, list) and offers:
                first = offers[0]
                if isinstance(first, dict):
                    price_str = first.get("price", "")
                    if price_str:
                        try:
                            price = float(price_str)
                        except (ValueError, TypeError):
                            pass

            if original_price and price and original_price <= price:
                original_price = None

            # Imagem
            image_url = ""
            image_data = item.get("image", "")
            if isinstance(image_data, list) and image_data:
                image_url = image_data[0] if isinstance(image_data[0], str) else ""
            elif isinstance(image_data, str):
                image_url = image_data

            # Desconto
            discount_pct = None
            if price and original_price and original_price > price:
                discount_pct = round(
                    ((original_price - price) / original_price) * 100, 1
                )

            return Product(
                title=self._clean_title(name),
                link=url,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                extra={"source": "terabyte_json_ld"},
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear JSON-LD item Terabyte: {e}")
            return None

    def _extract_from_html(self, html: str) -> list[Product]:
        """Extrai ofertas via parsing HTML."""
        products: list[Product] = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Seletores de cards de produto da Terabyte
            card_selectors = [
                ".pbox",
                ".product-item",
                "[class*='product-card']",
                ".commerce_columns_item",
                ".product-box",
                ".col-product",
            ]

            cards = []
            for selector in card_selectors:
                cards = soup.select(selector)
                if cards:
                    self._logger.debug(
                        f"Terabyte: encontrou {len(cards)} cards com '{selector}'"
                    )
                    break

            # Fallback: busca por links de produto
            if not cards:
                cards = soup.select("a[href*='/produto/']")

            for card in cards[:25]:
                product = self._parse_html_card(card)
                if product:
                    products.append(product)

        except Exception as e:
            self._logger.debug(f"Erro ao parsear HTML Terabyte: {e}")

        return products

    def _parse_html_card(self, card) -> Optional[Product]:
        """Parseia um card HTML da Terabyte."""
        try:
            # Link
            link_el = card if card.name == "a" else card.select_one("a[href]")
            if not link_el:
                return None

            href = link_el.get("href", "")
            if not href:
                return None
            if not href.startswith("http"):
                href = f"{self.BASE_URL}{href}"

            # Título
            title = None
            for sel in [
                ".prod-name",
                ".product-name",
                "[class*='name']",
                "h3", "h4",
                ".commerce_columns_item_caption",
                "a[title]",
            ]:
                el = card.select_one(sel)
                if el:
                    title = el.get_text(strip=True) or el.get("title", "")
                    if title and len(title) >= 10:
                        break

            if not title or len(title) < 10:
                title = link_el.get("title", "") or link_el.get_text(strip=True)
            if not title or len(title) < 10:
                return None

            # Preço atual
            price = None
            for sel in [
                ".prod-new-price",
                ".product-price",
                "[class*='new-price']",
                "[class*='preco']",
                "[class*='price'] span",
                ".val-prod",
            ]:
                el = card.select_one(sel)
                if el:
                    price = self._parse_price(el.get_text(strip=True))
                    if price:
                        break

            # Preço original
            original_price = None
            for sel in [
                ".prod-old-price",
                "[class*='old-price']",
                "[class*='preco-antigo']",
                "s", "del",
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
                "[class*='discount'], [class*='desconto'], [class*='off']"
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
                if src and not self._is_placeholder(src):
                    if not src.startswith("http"):
                        src = f"{self.BASE_URL}{src}"
                    image_url = src

            # Cupom
            coupon = self._extract_coupon(card)

            # Frete grátis
            free_shipping = False
            frete_el = card.select_one(
                "[class*='frete'], [class*='shipping']"
            )
            if frete_el and "grátis" in frete_el.get_text(strip=True).lower():
                free_shipping = True

            return Product(
                title=self._clean_title(title),
                link=href,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                coupon_code=coupon,
                free_shipping=free_shipping,
                extra={"source": "terabyte_html"},
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear card Terabyte: {e}")
            return None

    def _is_placeholder(self, url: str) -> bool:
        """Verifica se a URL é uma imagem placeholder."""
        placeholders = [
            "transparent-pixel", "grey-pixel", "loading-",
            "spinner", "1x1", "pixel.", "blank.", "no-img",
            "placeholder",
        ]
        url_lower = url.lower()
        return any(p in url_lower for p in placeholders)

    def _extract_coupon(self, card) -> Optional[str]:
        """Extrai cupom de desconto do card."""
        coupon_selectors = [
            "[class*='coupon']",
            "[class*='cupom']",
            "[data-coupon]",
        ]

        for sel in coupon_selectors:
            el = card.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if "cupom" in text.lower() or "coupon" in text.lower():
                    match = re.search(r"(\d+)\s*%", text)
                    if match:
                        return f"CUPOM {match.group(1)}% OFF"
                    match = re.search(r"R\$\s*([\d.,]+)", text)
                    if match:
                        return f"CUPOM R${match.group(1)} OFF"
                    # Tenta extrair código
                    code_match = re.search(r"([A-Z0-9]{4,20})", text.upper())
                    if code_match:
                        return code_match.group(1)
                    return "APLICAR CUPOM"

        # Verifica no texto geral do card
        card_text = card.get_text(" ", strip=True).upper()
        if "CUPOM" in card_text:
            match = re.search(
                r"CUPOM\s*:?\s*([A-Z0-9]{4,20})", card_text
            )
            if match:
                return match.group(1)

        return None
