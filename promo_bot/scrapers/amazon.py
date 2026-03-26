"""
Scraper da Amazon Brasil via página de ofertas.

A Amazon possui proteção anti-bot robusta, mas a página de ofertas
do dia pode ser acessada com headers adequados. Este scraper usa
parsing HTML com múltiplos fallbacks.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class AmazonScraper(BaseScraper):
    """Scraper da Amazon Brasil via página de ofertas."""

    STORE = Store.AMAZON
    NAME = "amazon"
    BASE_URL = "https://www.amazon.com.br"

    # Páginas de ofertas da Amazon
    DEALS_URLS = [
        "https://www.amazon.com.br/deals",
        "https://www.amazon.com.br/gp/goldbox",
    ]

    # Headers específicos para Amazon
    AMAZON_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.amazon.com.br/",
        "Cache-Control": "no-cache",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas da Amazon Brasil."""
        products: list[Product] = []

        for url in self.DEALS_URLS:
            try:
                page_products = await self._scrape_page(url)
                products.extend(page_products)
                if products:
                    break
            except Exception as e:
                self._logger.warning(f"Erro ao acessar {url}: {e}")

        # Fallback: busca por ofertas relâmpago
        if not products:
            products = await self._scrape_search_deals()

        self._logger.info(f"Amazon: {len(products)} ofertas coletadas")
        return products

    async def _scrape_page(self, url: str) -> list[Product]:
        """Coleta ofertas de uma página da Amazon."""
        products: list[Product] = []

        html = await self._http.fetch(url, headers=self.AMAZON_HEADERS)
        if not html:
            self._logger.warning(f"Falha ao acessar {url}")
            return products

        soup = BeautifulSoup(html, "html.parser")

        # Seletores para cards de ofertas na Amazon
        deal_selectors = [
            "div[data-testid='deal-card']",
            ".DealGridItem",
            "[class*='DealCard']",
            ".octopus-dlp-asin-section",
            ".a-cardui",
        ]

        for selector in deal_selectors:
            cards = soup.select(selector)
            if cards:
                self._logger.debug(
                    f"Amazon: encontrou {len(cards)} cards com '{selector}'"
                )
                for card in cards[:20]:
                    product = self._parse_deal_card(card)
                    if product:
                        products.append(product)
                break

        # Fallback: busca links de produtos genéricos
        if not products:
            products = self._parse_product_links(soup)

        return products

    def _parse_deal_card(self, card) -> Optional[Product]:
        """Parseia um card de oferta da Amazon."""
        try:
            # Título
            title = None
            for sel in [
                "[class*='deal-title']",
                "[class*='DealContent'] div",
                ".a-truncate-full",
                ".a-text-normal",
                "span[class*='title']",
                "h2",
            ]:
                el = card.select_one(sel)
                if el and len(el.get_text(strip=True)) > 10:
                    title = el.get_text(strip=True)
                    break

            if not title:
                texts = [t.strip() for t in card.stripped_strings if len(t.strip()) > 15]
                title = texts[0] if texts else None

            if not title:
                return None

            # Link
            link = None
            link_el = card.select_one(
                "a[href*='/dp/'], a[href*='/gp/'], a[href*='amazon.com.br']"
            )
            if not link_el:
                link_el = card.select_one("a[href]")

            if link_el:
                href = link_el.get("href", "")
                if href.startswith("/"):
                    link = f"https://www.amazon.com.br{href}"
                elif href.startswith("http"):
                    link = href

            if not link:
                return None

            link = self._clean_amazon_link(link)

            # Preço
            price = None
            for sel in [
                ".a-price .a-offscreen",
                "[class*='price'] .a-offscreen",
                ".a-color-price",
                ".dealPriceText",
            ]:
                el = card.select_one(sel)
                if el:
                    price = self._parse_price(el.get_text(strip=True))
                    if price:
                        break

            # Preço original
            original_price = None
            for sel in [
                ".a-text-strike",
                "[data-a-strike='true'] .a-offscreen",
                "[class*='basisPrice'] .a-offscreen",
            ]:
                el = card.select_one(sel)
                if el:
                    original_price = self._parse_price(el.get_text(strip=True))
                    if original_price:
                        break

            # Desconto
            discount_pct = None
            discount_el = card.select_one(
                "[class*='discount'], [class*='savings'], .savingsPercentage"
            )
            if discount_el:
                disc_match = re.search(r"(\d+)\s*%", discount_el.get_text())
                if disc_match:
                    discount_pct = float(disc_match.group(1))

            # Imagem
            img_el = card.select_one("img[src*='images-amazon'], img[src*='m.media-amazon']")
            image = img_el.get("src", "") if img_el else ""

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image,
            )

        except Exception as e:
            self._logger.debug(f"Erro ao parsear card Amazon: {e}")
            return None

    def _parse_product_links(self, soup: BeautifulSoup) -> list[Product]:
        """Fallback: extrai produtos de links genéricos."""
        products = []
        seen_links: set[str] = set()

        for link_el in soup.select("a[href*='/dp/']")[:30]:
            try:
                href = link_el.get("href", "")
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.amazon.com.br{href}"

                clean_link = self._clean_amazon_link(href)
                if clean_link in seen_links:
                    continue
                seen_links.add(clean_link)

                title = link_el.get_text(strip=True)
                if len(title) < 10:
                    parent = link_el.parent
                    if parent:
                        title = parent.get_text(strip=True)[:200]
                if len(title) < 10:
                    continue

                price = None
                parent = link_el.find_parent(
                    class_=re.compile(r"card|item|product|deal", re.I)
                )
                if parent:
                    price_el = parent.select_one(
                        ".a-price .a-offscreen, .a-color-price"
                    )
                    if price_el:
                        price = self._parse_price(price_el.get_text(strip=True))

                products.append(Product(
                    title=self._clean_title(title[:150]),
                    link=clean_link,
                    store=self.STORE,
                    price=price,
                ))
            except Exception:
                continue

        return products

    async def _scrape_search_deals(self) -> list[Product]:
        """Busca ofertas via search com filtro de desconto."""
        products = []

        try:
            url = (
                "https://www.amazon.com.br/s?"
                "i=deals&deal_type=LIGHTNING_DEAL&rh=p_n_deal_type%3A23530826011"
            )
            html = await self._http.fetch(url, headers=self.AMAZON_HEADERS)
            if not html:
                return products

            soup = BeautifulSoup(html, "html.parser")

            for result in soup.select("[data-component-type='s-search-result']")[:15]:
                product = self._parse_search_result(result)
                if product:
                    products.append(product)

        except Exception as e:
            self._logger.debug(f"Erro na busca Amazon: {e}")

        return products

    def _parse_search_result(self, result) -> Optional[Product]:
        """Parseia um resultado de busca da Amazon."""
        try:
            title_el = result.select_one("h2 a span, h2 span")
            if not title_el:
                return None
            title = title_el.get_text(strip=True)

            link_el = result.select_one("h2 a[href]")
            if not link_el:
                return None
            href = link_el.get("href", "")
            if href.startswith("/"):
                href = f"https://www.amazon.com.br{href}"
            link = self._clean_amazon_link(href)

            price_el = result.select_one(".a-price .a-offscreen")
            price = self._parse_price(price_el.get_text(strip=True)) if price_el else None

            orig_el = result.select_one(
                ".a-price[data-a-strike] .a-offscreen, .a-text-price .a-offscreen"
            )
            original_price = (
                self._parse_price(orig_el.get_text(strip=True)) if orig_el else None
            )

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
            )
        except Exception:
            return None

    def _clean_amazon_link(self, url: str) -> str:
        """Remove parâmetros de tracking e normaliza link da Amazon."""
        asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
        if asin_match:
            return f"https://www.amazon.com.br/dp/{asin_match.group(1)}"
        return url.split("?")[0].split("ref=")[0]
