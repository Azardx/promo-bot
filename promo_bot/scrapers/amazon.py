"""
Scraper da Amazon Brasil via página de ofertas e busca.

A Amazon possui proteção anti-bot que requer headers realistas.
Este scraper utiliza a página de ofertas do dia e busca por
termos populares.

CORREÇÕES v2.3:
- Filtro de mídia digital aprimorado (filmes, séries, kindle, prime video)
- Extração de cupons (vouchers) e frete grátis
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


class AmazonScraper(BaseScraper):
    """Scraper da Amazon Brasil."""

    STORE = Store.AMAZON
    NAME = "amazon"
    BASE_URL = "https://www.amazon.com.br"
    OFFERS_URL = "https://www.amazon.com.br/deals"

    # Keywords para filtrar conteúdo digital (filmes, séries, etc.)
    DIGITAL_KEYWORDS = [
        "prime video", "temporada", "série", "filme", "kindle edition", 
        "ebook", "e-book", "digital", "aluguel", "assinatura", "unlimited",
        "music unlimited", "audible", "podcast", "canal", "episódio",
        "scarpetta", "médica legista", "nicolas cage", "nicole kidman",
        "elenco", "direção", "produção", "lançamento", "streaming"
    ]

    # Headers realistas para Amazon
    AMAZON_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.amazon.com.br/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas da Amazon Brasil."""
        products: list[Product] = []

        # 1. Tenta página de ofertas do dia
        html = await self._http.fetch(self.OFFERS_URL, headers=self.AMAZON_HEADERS)
        if not html:
            self._logger.warning("Falha ao acessar Amazon Deals")
            return products

        try:
            soup = BeautifulSoup(html, "html.parser")
            
            # Seletores de cards de oferta na Amazon
            cards = soup.select("[data-testid='grid-desktop-card'], .a-section.octopus-dlp-asin-section")
            
            for card in cards[:30]:
                product = self._parse_card(card)
                if product and not self._is_digital_content(product):
                    products.append(product)

        except Exception as e:
            self._logger.error(f"Erro ao parsear Amazon: {e}")

        self._logger.info(f"Amazon: {len(products)} ofertas coletadas")
        return products

    def _parse_card(self, card) -> Optional[Product]:
        """Parseia um card de oferta da Amazon."""
        try:
            # Link
            link_el = card.select_one("a[href*='/dp/'], a[href*='/gp/product/']")
            if not link_el: return None
            
            href = link_el.get("href", "")
            if not href.startswith("http"):
                href = f"{self.BASE_URL}{href}"

            # Título
            title_el = card.select_one("[class*='title'], .a-size-base, .octopus-dlp-asin-title")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                title = link_el.get_text(strip=True)
            
            if not title or len(title) < 10: return None

            # Preço
            price = None
            price_el = card.select_one(".a-price-whole")
            if price_el:
                price_fraction = card.select_one(".a-price-fraction")
                price_str = price_el.get_text(strip=True)
                if price_fraction:
                    price_str += f",{price_fraction.get_text(strip=True)}"
                price = self._parse_price(price_str)

            # Preço original
            original_price = None
            old_price_el = card.select_one(".a-text-strike, .a-price.a-text-price")
            if old_price_el:
                original_price = self._parse_price(old_price_el.get_text(strip=True))

            # Imagem
            image_url = ""
            img_el = card.select_one("img")
            if img_el:
                image_url = img_el.get("src") or img_el.get("data-src") or ""
                # Troca para imagem de alta resolução se possível
                image_url = re.sub(r"\._AC_.*_\.", ".", image_url)

            # Cupom (Voucher)
            coupon = None
            coupon_el = card.select_one(".a-badge-text, [class*='coupon'], .a-color-success")
            if coupon_el and "cupom" in coupon_el.get_text().lower():
                coupon = "Ative na página"

            return Product(
                title=self._clean_title(title),
                link=href.split("?")[0],
                store=self.STORE,
                price=price,
                original_price=original_price,
                image_url=image_url,
                coupon_code=coupon,
                free_shipping="frete grátis" in card.get_text().lower()
            )
        except Exception:
            return None

    def _is_digital_content(self, product: Product) -> bool:
        """Verifica se o produto é conteúdo digital (filmes, séries, etc.)."""
        text = (product.title + " " + product.link).lower()
        
        # 1. Verifica keywords
        for kw in self.DIGITAL_KEYWORDS:
            if kw in text:
                self._logger.debug(f"Amazon: Filtrado conteúdo digital ({kw}): {product.title}")
                return True
        
        # 2. Verifica padrões de URL de vídeo/kindle
        if "/prime-video/" in product.link or "/kindle-dbs/" in product.link:
            return True
            
        return False
