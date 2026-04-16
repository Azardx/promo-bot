"""
Scraper do Mercado Livre via API de busca e HTML.

O Mercado Livre possui uma API pública de busca que é mais estável
que o parsing HTML. Este scraper utiliza a API como fonte principal
e possui fallback para HTML.

CORREÇÕES v2.3:
- Uso da API de busca pública (sites/MLB/search)
- Extração de frete grátis (Full) e cupons
- Normalização de links e imagens em alta resolução
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class MercadoLivreScraper(BaseScraper):
    """Scraper do Mercado Livre."""

    STORE = Store.MERCADOLIVRE
    NAME = "mercadolivre"
    BASE_URL = "https://www.mercadolivre.com.br"
    
    # API pública do Mercado Livre
    SEARCH_API = "https://api.mercadolibre.com/sites/MLB/search"
    
    # Termos para encontrar ofertas
    SEARCH_TERMS = ["ofertas", "promoção", "desconto"]

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas do Mercado Livre."""
        products: list[Product] = []
        seen_ids: set[str] = set()

        # 1. Tenta via API de busca (mais estável)
        for term in self.SEARCH_TERMS:
            try:
                api_products = await self._scrape_api(term)
                for p in api_products:
                    ml_id = p.extra.get("ml_id")
                    if ml_id and ml_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(ml_id)
                if len(products) >= 20: break
            except Exception as e:
                self._logger.debug(f"Erro API ML ({term}): {e}")

        # 2. Fallback: Parsing HTML da página de ofertas
        if len(products) < 10:
            try:
                html_products = await self._scrape_html()
                for p in html_products:
                    ml_id = p.extra.get("ml_id", p.link)
                    if ml_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(ml_id)
            except Exception as e:
                self._logger.debug(f"Erro HTML ML: {e}")

        self._logger.info(f"Mercado Livre: {len(products)} ofertas coletadas")
        return products

    async def _scrape_api(self, keyword: str) -> list[Product]:
        """Coleta itens via API pública."""
        products = []
        params = {
            "q": keyword,
            "limit": 30,
            "sort": "relevance",
        }
        data = await self._http.fetch_json(self.SEARCH_API, params=params)
        
        if not data or "results" not in data:
            return products

        for item in data.get("results", []):
            product = self._parse_api_item(item)
            if product:
                products.append(product)

        return products

    async def _scrape_html(self) -> list[Product]:
        """Coleta itens via parsing HTML da página de ofertas."""
        products = []
        url = f"{self.BASE_URL}/ofertas"
        html = await self._http.fetch(url)
        if not html: return products

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".promotion-item, .andes-card, [class*='poly-card']")
        
        for card in cards[:25]:
            product = self._parse_html_card(card)
            if product:
                products.append(product)
        
        return products

    def _parse_api_item(self, item: dict) -> Optional[Product]:
        """Converte um item da API ML em Product."""
        try:
            title = item.get("title")
            link = item.get("permalink")
            if not title or not link: return None

            price = float(item.get("price", 0))
            original_price = float(item.get("original_price", 0)) or None
            
            if original_price and original_price <= price:
                original_price = None

            # Imagem (troca para resolução maior)
            thumbnail = item.get("thumbnail", "")
            image_url = thumbnail.replace("-I.jpg", "-O.jpg") if thumbnail else ""

            # Frete grátis
            shipping = item.get("shipping", {})
            free_shipping = shipping.get("free_shipping", False)

            return Product(
                title=self._clean_title(title),
                link=link.split("?")[0],
                store=self.STORE,
                price=price,
                original_price=original_price,
                image_url=image_url,
                free_shipping=free_shipping,
                extra={"ml_id": item.get("id")}
            )
        except Exception:
            return None

    def _parse_html_card(self, card) -> Optional[Product]:
        """Parseia um card HTML do Mercado Livre."""
        try:
            link_el = card.select_one("a[href]")
            if not link_el: return None
            
            href = link_el.get("href", "")
            if not href or "mercadolivre.com.br" not in href: return None

            title_el = card.select_one(".promotion-item__title, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title: return None

            price_el = card.select_one(".andes-money-amount__fraction")
            price = self._parse_price(price_el.get_text(strip=True)) if price_el else None

            return Product(
                title=self._clean_title(title),
                link=href.split("?")[0],
                store=self.STORE,
                price=price,
                extra={"ml_id": re.search(r"MLB-?\d+", href).group(0) if re.search(r"MLB-?\d+", href) else None}
            )
        except Exception:
            return None
