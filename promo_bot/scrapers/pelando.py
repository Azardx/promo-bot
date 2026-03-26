"""
Scraper de promoções do Pelando.

O Pelando é um agregador de promoções brasileiro, similar ao Pepper/Slickdeals.
Coleta ofertas já validadas pela comunidade, com alto nível de relevância.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper


class PelandoScraper(BaseScraper):
    """Scraper especializado para o Pelando."""

    STORE = Store.PELANDO
    NAME = "pelando"
    BASE_URL = "https://www.pelando.com.br"

    _HOT_DEALS_URL = "https://www.pelando.com.br"
    _NEW_DEALS_URL = "https://www.pelando.com.br/novas"

    async def _scrape(self) -> list[Product]:
        """Coleta promoções do Pelando."""
        products: list[Product] = []

        # Coleta ofertas quentes (mais votadas)
        hot = await self._scrape_page(self._HOT_DEALS_URL)
        products.extend(hot)

        # Coleta ofertas novas
        new = await self._scrape_page(self._NEW_DEALS_URL)
        products.extend(new)

        return products

    async def _scrape_page(self, url: str) -> list[Product]:
        """Coleta ofertas de uma página do Pelando."""
        products = []

        try:
            html = await self._http.fetch(url)
            if not html:
                return products

            soup = BeautifulSoup(html, "html.parser")

            # Cards de oferta do Pelando
            card_selectors = [
                "article",
                "[class*='threadGrid']",
                "[class*='thread-card']",
                ".cept-thread-item",
            ]

            cards = []
            for selector in card_selectors:
                cards = soup.select(selector)
                if cards:
                    break

            for card in cards[:20]:
                product = self._parse_pelando_card(card)
                if product:
                    products.append(product)

            self._logger.info(f"Pelando ({url.split('/')[-1] or 'home'}): {len(products)} ofertas")

        except Exception as e:
            self._logger.warning(f"Erro no Pelando: {e}")

        return products

    def _parse_pelando_card(self, card) -> Optional[Product]:
        """Parseia um card de oferta do Pelando."""
        try:
            # Extrai link principal
            link_el = card.select_one("a[href*='/oferta/'], a[href*='/cupom/'], a[href*='/promocao/']")
            if not link_el:
                link_el = card.select_one("a[href]")

            if not link_el:
                return None

            href = link_el.get("href", "")
            if not href:
                return None
            if not href.startswith("http"):
                href = f"https://www.pelando.com.br{href}"

            # Extrai título
            title_el = card.select_one(
                "[class*='thread-title'], [class*='threadTitle'], "
                "strong, h2, h3, .cept-tt"
            )
            title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)

            if not title or len(title) < 10:
                return None

            # Extrai preço
            price = None
            price_el = card.select_one(
                "[class*='thread-price'], [class*='threadPrice'], "
                "[class*='price'], .cept-tp"
            )
            if price_el:
                price = self._parse_price(price_el.get_text(strip=True))

            # Tenta extrair preço do título
            if price is None:
                price_match = re.search(r"R\$\s*[\d.,]+", title)
                if price_match:
                    price = self._parse_price(price_match.group(0))

            # Extrai desconto
            discount = None
            discount_el = card.select_one("[class*='discount'], [class*='badge']")
            if discount_el:
                disc_text = discount_el.get_text(strip=True)
                disc_match = re.search(r"(\d+)\s*%", disc_text)
                if disc_match:
                    discount = float(disc_match.group(1))

            # Extrai cupom se houver
            coupon = None
            coupon_el = card.select_one("[class*='voucher'], [class*='coupon'], code")
            if coupon_el:
                coupon = coupon_el.get_text(strip=True)

            # Temperatura (votos) como indicador de qualidade
            temp_el = card.select_one(
                "[class*='vote'], [class*='temperature'], [class*='temp']"
            )
            score = 0.0
            if temp_el:
                temp_text = temp_el.get_text(strip=True)
                temp_match = re.search(r"[\d.]+", temp_text.replace(",", "."))
                if temp_match:
                    score = float(temp_match.group(0))

            return Product(
                title=self._clean_title(title),
                link=href,
                store=self.STORE,
                price=price,
                discount_pct=discount,
                coupon_code=coupon,
                score=score,
                extra={"source": "pelando"},
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear card Pelando: {e}")
            return None
