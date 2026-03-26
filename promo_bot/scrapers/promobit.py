"""
Scraper de promoções do Promobit.

O Promobit é outro agregador de promoções brasileiro com forte
comunidade. Coleta ofertas de diversas categorias com preços
já validados pelos usuários.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper


class PromobitScraper(BaseScraper):
    """Scraper especializado para o Promobit."""

    STORE = Store.PROMOBIT
    NAME = "promobit"
    BASE_URL = "https://www.promobit.com.br"

    _DEALS_URL = "https://www.promobit.com.br/promocoes"
    _CATEGORIES = [
        "informatica",
        "eletronicos",
        "celulares-e-smartphones",
        "games",
    ]

    async def _scrape(self) -> list[Product]:
        """Coleta promoções do Promobit."""
        products: list[Product] = []

        # Página principal de promoções
        main_deals = await self._scrape_page(self._DEALS_URL)
        products.extend(main_deals)

        # Categorias específicas
        for category in self._CATEGORIES[:2]:
            cat_deals = await self._scrape_page(
                f"{self._DEALS_URL}/{category}/"
            )
            products.extend(cat_deals)

        return products

    async def _scrape_page(self, url: str) -> list[Product]:
        """Coleta ofertas de uma página do Promobit."""
        products = []

        try:
            html = await self._http.fetch(url)
            if not html:
                return products

            soup = BeautifulSoup(html, "html.parser")

            # Seletores de cards do Promobit
            card_selectors = [
                ".pr-product-card",
                "[class*='promotion-card']",
                "[class*='PromotionCard']",
                "article[class*='promo']",
                ".cept-offer-card",
            ]

            cards = []
            for selector in card_selectors:
                cards = soup.select(selector)
                if cards:
                    break

            # Fallback: links de ofertas
            if not cards:
                cards = soup.select("a[href*='/oferta/']")

            for card in cards[:15]:
                product = self._parse_promobit_card(card, soup)
                if product:
                    products.append(product)

        except Exception as e:
            self._logger.warning(f"Erro no Promobit ({url}): {e}")

        return products

    def _parse_promobit_card(self, card, soup) -> Optional[Product]:
        """Parseia um card de oferta do Promobit."""
        try:
            # Extrai link
            link_el = card.select_one("a[href*='/oferta/']")
            if not link_el and card.name == "a":
                link_el = card

            if not link_el:
                return None

            href = link_el.get("href", "")
            if not href:
                return None
            if not href.startswith("http"):
                href = f"https://www.promobit.com.br{href}"

            # Extrai título
            title_el = card.select_one(
                "[class*='product-card-title'], [class*='promotion-title'], "
                "h3, h2, strong, .title"
            )
            title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)

            if not title or len(title) < 10:
                return None

            # Extrai preço
            price = None
            price_el = card.select_one(
                "[class*='product-card-price'], [class*='price'], "
                ".pr-font-bold, [class*='Price']"
            )
            if price_el:
                price = self._parse_price(price_el.get_text(strip=True))

            # Tenta extrair preço do título
            if price is None:
                price_match = re.search(r"R\$\s*[\d.,]+", title)
                if price_match:
                    price = self._parse_price(price_match.group(0))

            # Extrai loja de origem
            store_el = card.select_one(
                "[class*='store'], [class*='merchant'], .pr-store-name"
            )
            store_name = store_el.get_text(strip=True) if store_el else ""

            # Constrói título com preço se disponível
            display_title = title
            if price and f"R$" not in title and f"r$" not in title.lower():
                display_title = f"R$ {price:.2f} - {title}"

            return Product(
                title=self._clean_title(display_title),
                link=href,
                store=self.STORE,
                price=price,
                extra={"source": "promobit", "original_store": store_name},
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear card Promobit: {e}")
            return None
