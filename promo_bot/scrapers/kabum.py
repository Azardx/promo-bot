"""
Scraper de promoções do KaBuM!

O KaBuM! é uma das maiores lojas de tecnologia do Brasil.
Coleta ofertas da página de promoções e ofertas do dia
via parsing HTML e API interna.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper


class KabumScraper(BaseScraper):
    """Scraper especializado para o KaBuM!"""

    STORE = Store.KABUM
    NAME = "kabum"
    BASE_URL = "https://www.kabum.com.br"

    _OFFERS_URL = "https://www.kabum.com.br/ofertas"
    _HOTOFFERS_URL = "https://www.kabum.com.br/hotoffers"
    _API_URL = "https://servicespub.prod.api.aws.grupokabum.com.br/home/v1/offer"
    _SEARCH_API = "https://servicespub.prod.api.aws.grupokabum.com.br/catalog/v2/products"

    async def _scrape(self) -> list[Product]:
        """Coleta promoções do KaBuM!"""
        products: list[Product] = []

        # Estratégia 1: API interna de ofertas
        api_products = await self._scrape_api()
        products.extend(api_products)

        # Estratégia 2: Página de ofertas via HTML
        html_products = await self._scrape_html()
        products.extend(html_products)

        return products

    async def _scrape_api(self) -> list[Product]:
        """Coleta via API interna do KaBuM."""
        products = []

        try:
            headers = {
                "Referer": "https://www.kabum.com.br/",
                "Accept": "application/json",
                "Origin": "https://www.kabum.com.br",
            }

            # Tenta API de ofertas
            data = await self._http.fetch_json(self._API_URL, headers=headers)
            if data:
                items = data if isinstance(data, list) else data.get("data", [])
                if isinstance(items, dict):
                    items = items.get("offers", []) or items.get("products", [])

                for item in items[:25]:
                    product = self._parse_api_item(item)
                    if product:
                        products.append(product)

            # Tenta API de busca com filtro de promoção
            if not products:
                params = {
                    "query": "*",
                    "page_number": "1",
                    "page_size": "20",
                    "sort": "most_searched",
                    "is_offer": "true",
                }
                data = await self._http.fetch_json(
                    self._SEARCH_API, headers=headers, params=params
                )
                if data and "data" in data:
                    items = data["data"]
                    if isinstance(items, list):
                        for item in items[:20]:
                            product = self._parse_api_item(item)
                            if product:
                                products.append(product)

        except Exception as e:
            self._logger.warning(f"Erro na API KaBuM: {e}")

        return products

    def _parse_api_item(self, item: dict) -> Optional[Product]:
        """Parseia um item da API do KaBuM."""
        try:
            title = item.get("name", "") or item.get("title", "") or item.get("productName", "")
            if not title:
                return None

            # Link
            code = item.get("code") or item.get("id") or item.get("productId")
            slug = item.get("slug", "")
            link = item.get("link", "") or item.get("url", "")

            if not link:
                if code:
                    link = f"https://www.kabum.com.br/produto/{code}"
                    if slug:
                        link += f"/{slug}"
                else:
                    return None

            if not link.startswith("http"):
                link = f"https://www.kabum.com.br{link}"

            # Preços
            price = None
            original_price = None

            price_data = item.get("priceWithDiscount") or item.get("offer_price") or item.get("price")
            if isinstance(price_data, (int, float)):
                price = float(price_data)
            elif isinstance(price_data, dict):
                price = float(price_data.get("value", 0))

            orig_data = item.get("oldPrice") or item.get("price") or item.get("priceWithoutDiscount")
            if isinstance(orig_data, (int, float)):
                original_price = float(orig_data)
            elif isinstance(orig_data, dict):
                original_price = float(orig_data.get("value", 0))

            # Desconto
            discount = item.get("discount") or item.get("discountPercentage")
            if isinstance(discount, str):
                disc_match = re.search(r"(\d+)", discount)
                discount = float(disc_match.group(1)) if disc_match else None

            # Imagem
            image = item.get("imageUrl", "") or item.get("image", "")

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=float(discount) if discount else None,
                image_url=image,
            )
        except Exception as e:
            self._logger.debug(f"Erro ao parsear item KaBuM API: {e}")
            return None

    async def _scrape_html(self) -> list[Product]:
        """Coleta via parsing HTML."""
        products = []

        try:
            for url in [self._OFFERS_URL, self._HOTOFFERS_URL]:
                html = await self._http.fetch(url)
                if not html:
                    continue

                soup = BeautifulSoup(html, "html.parser")

                # Tenta extrair dados JSON embutidos
                script_tags = soup.select("script[type='application/json'], script#__NEXT_DATA__")
                for script in script_tags:
                    try:
                        data = json.loads(script.string or "")
                        items = self._extract_items_from_next_data(data)
                        for item in items[:20]:
                            product = self._parse_api_item(item)
                            if product:
                                products.append(product)
                    except (json.JSONDecodeError, TypeError):
                        continue

                # Fallback: parsing de cards HTML
                if not products:
                    card_selectors = [
                        ".productCard",
                        "[class*='productCard']",
                        ".product-card",
                        "a.prod-name",
                    ]
                    for selector in card_selectors:
                        cards = soup.select(selector)
                        if cards:
                            for card in cards[:20]:
                                product = self._parse_html_card(card)
                                if product:
                                    products.append(product)
                            break

                if products:
                    break

        except Exception as e:
            self._logger.warning(f"Erro no HTML KaBuM: {e}")

        return products

    def _extract_items_from_next_data(self, data: dict) -> list[dict]:
        """Extrai itens de __NEXT_DATA__ do Next.js."""
        items = []
        try:
            props = data.get("props", {}).get("pageProps", {})
            # Tenta diferentes caminhos
            for key in ["products", "offers", "data", "catalog"]:
                found = props.get(key, [])
                if isinstance(found, list) and found:
                    items = found
                    break
                if isinstance(found, dict):
                    items = found.get("products", []) or found.get("data", [])
                    if items:
                        break
        except Exception:
            pass
        return items

    def _parse_html_card(self, card) -> Optional[Product]:
        """Parseia um card HTML do KaBuM."""
        try:
            # Link
            if card.name == "a":
                link_el = card
            else:
                link_el = card.select_one("a[href]")

            if not link_el:
                return None

            href = link_el.get("href", "")
            if not href:
                return None
            if not href.startswith("http"):
                href = f"https://www.kabum.com.br{href}"

            # Título
            title_el = card.select_one(
                "[class*='name'], [class*='title'], h3, h2, span.name"
            )
            title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)

            if not title or len(title) < 10:
                return None

            # Preço
            price_el = card.select_one(
                "[class*='priceCard'], [class*='finalPrice'], .price"
            )
            price = self._parse_price(price_el.get_text(strip=True)) if price_el else None

            return Product(
                title=self._clean_title(title),
                link=href,
                store=self.STORE,
                price=price,
            )
        except Exception:
            return None
