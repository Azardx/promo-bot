"""
Scraper do Pelando via JSON-LD e meta tags da página principal.

O Pelando inclui dados estruturados no HTML via tags JSON-LD e
meta tags og:. Este scraper extrai URLs do JSON-LD e dados
básicos da página principal, evitando acessar páginas individuais
que frequentemente retornam 403.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class PelandoScraper(BaseScraper):
    """Scraper do Pelando via JSON-LD e extração da página principal."""

    STORE = Store.PELANDO
    NAME = "pelando"
    BASE_URL = "https://www.pelando.com.br"

    PAGES = [
        "https://www.pelando.com.br/recentes",
        "https://www.pelando.com.br",
    ]

    EXTRA_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.pelando.com.br/",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas do Pelando extraindo dados da página principal."""
        products: list[Product] = []
        seen_urls: set[str] = set()

        for page_url in self.PAGES:
            html = await self._http.fetch(page_url, headers=self.EXTRA_HEADERS)
            if not html:
                self._logger.warning(f"Falha ao acessar {page_url}")
                continue

            # Estratégia 1: Extrair ofertas do JSON-LD com dados inline
            json_ld_products = self._extract_from_json_ld(html, seen_urls)
            products.extend(json_ld_products)

            # Estratégia 2: Extrair ofertas dos links + títulos do HTML
            html_products = self._extract_from_html(html, seen_urls)
            products.extend(html_products)

            if products:
                break  # Se já encontrou ofertas, não precisa tentar outra página

        self._logger.info(f"Pelando: {len(products)} ofertas coletadas")
        return products

    def _extract_from_json_ld(self, html: str, seen_urls: set[str]) -> list[Product]:
        """Extrai ofertas do JSON-LD (application/ld+json)."""
        products: list[Product] = []

        try:
            pattern = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
            matches = re.findall(pattern, html, re.DOTALL)

            for match in matches:
                try:
                    data = json.loads(match)
                except json.JSONDecodeError:
                    continue

                # Processa diferentes estruturas JSON-LD
                items = self._extract_json_ld_items(data)

                for item in items:
                    url = ""
                    name = ""

                    if isinstance(item, dict):
                        url = item.get("url", "") or item.get("@id", "")
                        name = item.get("name", "") or item.get("headline", "")

                        # Tenta extrair de item aninhado
                        if not name:
                            inner = item.get("item", {})
                            if isinstance(inner, dict):
                                url = url or inner.get("url", "")
                                name = inner.get("name", "")

                    if not url or url in seen_urls:
                        continue
                    if "pelando.com.br" not in url:
                        continue

                    seen_urls.add(url)

                    # Extrai preço do nome se possível
                    price = self._extract_price_from_text(name)

                    if name and len(name) >= 10:
                        products.append(Product(
                            title=self._clean_title(name),
                            link=url,
                            store=self.STORE,
                            price=price,
                            extra={"source": "pelando_json_ld"},
                        ))

        except Exception as e:
            self._logger.debug(f"Erro ao extrair JSON-LD: {e}")

        return products

    def _extract_json_ld_items(self, data) -> list:
        """Extrai itens de diferentes estruturas JSON-LD."""
        items = []

        if isinstance(data, list):
            for entry in data:
                items.extend(self._extract_json_ld_items(entry))
            return items

        if not isinstance(data, dict):
            return items

        # mainEntity.hasPart
        main_entity = data.get("mainEntity", {})
        if isinstance(main_entity, dict):
            parts = main_entity.get("hasPart", [])
            if isinstance(parts, list):
                items.extend(parts)

        # itemListElement
        item_list = data.get("itemListElement", [])
        if isinstance(item_list, list):
            items.extend(item_list)

        # Se o próprio item tem URL de oferta
        if data.get("@type") in ("Product", "Offer", "Deal", "ListItem"):
            items.append(data)

        return items

    def _extract_from_html(self, html: str, seen_urls: set[str]) -> list[Product]:
        """Extrai ofertas diretamente do HTML usando regex."""
        products: list[Product] = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Busca links de ofertas
            offer_links = soup.select("a[href*='/d/']")

            for link_el in offer_links:
                href = link_el.get("href", "")
                if not href:
                    continue
                if not href.startswith("http"):
                    href = f"https://www.pelando.com.br{href}"

                if href in seen_urls:
                    continue
                if "/d/" not in href:
                    continue

                # Extrai título do link ou de elementos próximos
                title = link_el.get_text(strip=True)

                # Se o título é muito curto, tenta pegar do parent
                if len(title) < 15:
                    parent = link_el.parent
                    if parent:
                        # Busca texto mais longo no parent
                        for child in parent.children:
                            text = child.get_text(strip=True) if hasattr(child, 'get_text') else str(child).strip()
                            if len(text) > len(title):
                                title = text

                if not title or len(title) < 10:
                    continue

                seen_urls.add(href)

                price = self._extract_price_from_text(title)

                products.append(Product(
                    title=self._clean_title(title),
                    link=href,
                    store=self.STORE,
                    price=price,
                    extra={"source": "pelando_html"},
                ))

        except Exception as e:
            self._logger.debug(f"Erro ao extrair HTML: {e}")

        return products

    def _extract_price_from_text(self, text: str) -> Optional[float]:
        """Extrai preço de um texto."""
        if not text:
            return None
        # Busca padrão de preço brasileiro
        match = re.search(r'R\$\s*([\d\.]+,\d{2})', text)
        if match:
            return self._parse_price(f"R$ {match.group(1)}")
        return None
