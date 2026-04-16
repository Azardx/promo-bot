"""
Scraper da Terabyte Shop via página de promoções.

A Terabyte possui proteção anti-bot que requer headers realistas
e suporte a TLS fingerprinting (curl_cffi).

CORREÇÕES v2.3:
- Parsing HTML robusto com seletores atualizados
- Extração de desconto real e preços
- Tratamento de imagens em alta resolução
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class TerabyteScraper(BaseScraper):
    """Scraper da Terabyte Shop."""

    STORE = Store.TERABYTE
    NAME = "terabyte"
    BASE_URL = "https://www.terabyteshop.com.br"
    OFFERS_URL = "https://www.terabyteshop.com.br/promocoes"

    # Headers específicos para Terabyte
    TERABYTE_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.terabyteshop.com.br/",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas da Terabyte Shop."""
        products: list[Product] = []

        # Terabyte bloqueia requests simples, HttpClient usará curl_cffi se disponível
        html = await self._http.fetch(self.OFFERS_URL, headers=self.TERABYTE_HEADERS)
        if not html:
            self._logger.warning("Falha ao acessar Terabyte Shop")
            return products

        try:
            soup = BeautifulSoup(html, "html.parser")
            
            # Seletores de cards de produto na Terabyte
            card_selectors = [
                ".product-item",
                ".p-container",
                ".p-card",
                "div[class*='product-item']",
                ".commerce_columns_item_inner",
            ]
            
            cards = []
            for selector in card_selectors:
                cards = soup.select(selector)
                if cards:
                    self._logger.debug(f"Terabyte: encontrou {len(cards)} cards com '{selector}'")
                    break
            
            if not cards:
                # Fallback: busca por links de produto
                cards = soup.select("a[href*='/produto/']")
            
            for card in cards[:30]:
                product = self._parse_card(card)
                if product:
                    products.append(product)

        except Exception as e:
            self._logger.error(f"Erro ao parsear Terabyte: {e}")

        self._logger.info(f"Terabyte: {len(products)} ofertas coletadas")
        return products

    def _parse_card(self, card) -> Optional[Product]:
        """Parseia um card de produto da Terabyte."""
        try:
            # Link
            link_el = card if card.name == "a" else card.select_one("a[href*='/produto/']")
            if not link_el: return None
            
            href = link_el.get("href", "")
            if not href or not href.startswith("http"):
                if href.startswith("/"):
                    href = f"{self.BASE_URL}{href}"
                else:
                    return None

            # Título
            title_el = card.select_one(".prod-name, .product-item__title, h2, h3, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                title = link_el.get("title") or link_el.get_text(strip=True)
            
            if not title or len(title) < 10: return None

            # Preço (Terabyte mostra preço à vista e parcelado)
            # Priorizamos o preço à vista (geralmente menor)
            price = None
            price_el = card.select_one(".prod-new-price, .product-item__price, [class*='price-new'], .val-prod")
            if price_el:
                price = self._parse_price(price_el.get_text(strip=True))

            # Preço original
            original_price = None
            old_price_el = card.select_one(".prod-old-price, .product-item__old-price, [class*='price-old'], strike")
            if old_price_el:
                original_price = self._parse_price(old_price_el.get_text(strip=True))

            # Desconto
            discount_pct = None
            discount_el = card.select_one(".prod-discount, [class*='discount'], .p-discount")
            if discount_el:
                match = re.search(r"(\d+)\s*%", discount_el.get_text())
                if match: discount_pct = float(match.group(1))

            # Imagem
            image_url = ""
            img_el = card.select_one("img[src], img[data-src]")
            if img_el:
                image_url = img_el.get("src") or img_el.get("data-src") or ""
                if image_url.startswith("//"):
                    image_url = f"https:{image_url}"
                elif image_url.startswith("/"):
                    image_url = f"{self.BASE_URL}{image_url}"

            return Product(
                title=self._clean_title(title),
                link=href.split("?")[0],
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                free_shipping="frete grátis" in card.get_text().lower()
            )
        except Exception:
            return None
