"""
Scraper do Pelando via dados RSC (React Server Components).

O Pelando usa React Server Components e serializa os dados das
ofertas diretamente no HTML em formato RSC. Este scraper extrai
esses dados estruturados, incluindo:
- sourceUrl: link REAL da loja de destino (Amazon, ML, etc.)
- imageUrl: imagem do produto hospedada no CDN do Pelando
- store.name: nome da loja de origem
- kind: tipo da oferta (promotion ou coupon)
- price, discountPercentage, freeShipping, etc.

Também faz fallback para JSON-LD e HTML parsing quando os
dados RSC não estão disponíveis.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Optional
from urllib.parse import unquote

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class PelandoScraper(BaseScraper):
    """Scraper do Pelando via dados RSC + JSON-LD + HTML."""

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

    # Prefixo do CDN de imagens do Pelando
    PELANDO_MEDIA = "https://media.pelando.com.br"

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas do Pelando extraindo dados RSC, JSON-LD e HTML."""
        products: list[Product] = []
        seen_ids: set[str] = set()

        for page_url in self.PAGES:
            html = await self._http.fetch(page_url, headers=self.EXTRA_HEADERS)
            if not html:
                self._logger.warning(f"Falha ao acessar {page_url}")
                continue

            # Estratégia 1 (principal): Extrair dados RSC serializados no HTML
            rsc_products = self._extract_from_rsc(html, seen_ids)
            products.extend(rsc_products)

            # Estratégia 2 (fallback): JSON-LD
            if not rsc_products:
                json_ld_products = self._extract_from_json_ld(html, seen_ids)
                products.extend(json_ld_products)

            # Estratégia 3 (fallback): HTML parsing
            if not products:
                html_products = self._extract_from_html(html, seen_ids)
                products.extend(html_products)

            if products:
                break  # Se já encontrou ofertas, não precisa tentar outra página

        self._logger.info(f"Pelando: {len(products)} ofertas coletadas")
        return products

    # ------------------------------------------------------------------
    # Estratégia 1: Extração de dados RSC (React Server Components)
    # ------------------------------------------------------------------

    def _extract_from_rsc(self, html: str, seen_ids: set[str]) -> list[Product]:
        """
        Extrai ofertas dos dados RSC serializados no HTML.

        O Pelando serializa dados no formato RSC com padrão:
        [0, {"id": [0, "uuid"], "slug": [0, "..."], "title": [0, "..."],
             "sourceUrl": [0, "https://..."], "imageUrl": [0, "..."], ...}]
        """
        products: list[Product] = []

        try:
            # Busca todos os blocos de dados RSC que contêm ofertas
            # Padrão: sourceUrl seguido de dados de oferta
            deals = self._parse_rsc_deals(html)

            for deal in deals:
                try:
                    deal_id = deal.get("id", "")
                    if not deal_id or deal_id in seen_ids:
                        continue

                    product = self._parse_rsc_deal(deal)
                    if product:
                        seen_ids.add(deal_id)
                        products.append(product)
                except Exception as e:
                    self._logger.debug(f"Erro ao parsear deal RSC: {e}")

            if products:
                self._logger.debug(
                    f"RSC: {len(products)} ofertas extraidas dos dados serializados"
                )

        except Exception as e:
            self._logger.debug(f"Erro na extração RSC: {e}")

        return products

    def _parse_rsc_deals(self, html: str) -> list[dict]:
        """
        Parseia os dados RSC do HTML e extrai deals individuais.

        Os dados RSC estão em formato serializado com campos como:
        "id":[0,"uuid"],"slug":[0,"slug"],"title":[0,"titulo"],
        "sourceUrl":[0,"url"],"imageUrl":[0,"url"],...
        """
        deals: list[dict] = []

        # Decodifica entidades HTML para facilitar o parsing
        decoded = unescape(html)

        # Regex para encontrar blocos de deals individuais
        # Cada deal tem um padrão: "id":[0,"uuid"],...,"sourceUrl":[0,"url"]
        deal_pattern = re.compile(
            r'"id":\[0,"([a-f0-9-]{36})"\]'  # UUID do deal
            r'.*?'
            r'"slug":\[0,"([^"]+)"\]'  # slug
            r'.*?'
            r'"title":\[0,"([^"]+)"\]',  # título
            re.DOTALL
        )

        # Encontra todos os blocos que parecem ser deals
        # Primeiro, divide o HTML em segmentos por ID de deal
        id_positions = [
            (m.start(), m.group(1))
            for m in re.finditer(r'"id":\[0,"([a-f0-9-]{36})"\]', decoded)
        ]

        for i, (pos, deal_id) in enumerate(id_positions):
            # Pega o segmento até o próximo deal ou fim
            end_pos = id_positions[i + 1][0] if i + 1 < len(id_positions) else pos + 5000
            segment = decoded[pos:end_pos]

            # Verifica se é um deal (tem sourceUrl ou kind=promotion/coupon)
            if '"sourceUrl"' not in segment and '"kind"' not in segment:
                continue

            deal = self._extract_rsc_fields(segment, deal_id)
            if deal and deal.get("title"):
                deals.append(deal)

        return deals

    def _extract_rsc_fields(self, segment: str, deal_id: str) -> dict:
        """Extrai campos individuais de um segmento RSC."""
        deal = {"id": deal_id}

        # Campos de texto simples: [0, "valor"]
        text_fields = [
            "slug", "title", "date", "status", "kind",
            "sourceUrl", "imageUrl", "cashback",
        ]
        for field_name in text_fields:
            match = re.search(
                rf'"{field_name}":\[0,"((?:[^"\\]|\\.)*)"\]', segment
            )
            if match:
                deal[field_name] = match.group(1).replace('\\"', '"')

        # Campos numéricos: [0, valor]
        num_fields = [
            "temperature", "commentCount", "price",
            "discountPercentage", "discountFixed",
        ]
        for field_name in num_fields:
            match = re.search(
                rf'"{field_name}":\[0,(\d+(?:\.\d+)?)\]', segment
            )
            if match:
                deal[field_name] = float(match.group(1))

        # Campos booleanos: [0, true/false/null]
        bool_fields = ["freeShipping"]
        for field_name in bool_fields:
            match = re.search(
                rf'"{field_name}":\[0,(true|false|null)\]', segment
            )
            if match:
                val = match.group(1)
                deal[field_name] = val == "true" if val != "null" else None

        # Store (objeto aninhado)
        store_name_match = re.search(
            r'"store":\[0,\{[^}]*"name":\[0,"([^"]+)"\]', segment
        )
        if store_name_match:
            deal["store_name"] = store_name_match.group(1)

        store_slug_match = re.search(
            r'"store":\[0,\{[^}]*"slug":\[0,"([^"]+)"\]', segment
        )
        if store_slug_match:
            deal["store_slug"] = store_slug_match.group(1)

        # Coupon code (pode estar no título ou em campo específico)
        coupon_match = re.search(
            r'"couponCode":\[0,"([^"]+)"\]', segment
        )
        if coupon_match:
            deal["couponCode"] = coupon_match.group(1)

        return deal

    def _parse_rsc_deal(self, deal: dict) -> Optional[Product]:
        """Converte um deal RSC em Product."""
        title = deal.get("title", "").strip()
        if not title or len(title) < 10:
            return None

        # Status deve ser ativo
        status = deal.get("status", "active")
        if status not in ("active", ""):
            return None

        # Link: usa sourceUrl (link REAL da loja) se disponível
        source_url = deal.get("sourceUrl", "")
        slug = deal.get("slug", "")

        if source_url and source_url.startswith("http"):
            # Usa o link real da loja de destino
            link = self._clean_source_url(source_url)
        elif slug:
            # Fallback: link do Pelando
            link = f"{self.BASE_URL}/d/{slug}"
        else:
            return None

        # Preço
        price = deal.get("price")
        if price is not None and price <= 0:
            price = None

        # Desconto
        discount_pct = deal.get("discountPercentage")
        discount_fixed = deal.get("discountFixed")

        # Imagem
        image_url = deal.get("imageUrl", "")
        if image_url and not image_url.startswith("http"):
            image_url = f"{self.PELANDO_MEDIA}/{image_url}"

        # Frete grátis
        free_shipping = deal.get("freeShipping") is True or "frete gr" in title.lower()

        # Cupom
        coupon = deal.get("couponCode")
        if not coupon:
            coupon = self._detect_coupon_from_title(title)
        # Se o tipo é cupom, tenta extrair do título
        if not coupon and deal.get("kind") == "coupon":
            coupon = self._detect_coupon_from_title(title) or "VER NA LOJA"

        # Loja de origem
        store_name = deal.get("store_name", "")
        store_slug = deal.get("store_slug", "")

        # Categoria (baseada no tipo de deal)
        kind = deal.get("kind", "promotion")

        return Product(
            title=self._clean_title(title),
            link=link,
            store=self.STORE,
            price=price,
            discount_pct=discount_pct,
            image_url=image_url,
            coupon_code=coupon,
            free_shipping=free_shipping,
            category=kind,
            extra={
                "source": "pelando_rsc",
                "origin_store": store_name,
                "origin_slug": store_slug,
                "pelando_slug": deal.get("slug", ""),
                "pelando_url": f"{self.BASE_URL}/d/{deal.get('slug', '')}",
                "temperature": deal.get("temperature"),
                "source_url": source_url,
            },
        )

    def _clean_source_url(self, url: str) -> str:
        """
        Limpa a URL de origem removendo parâmetros de tracking desnecessários
        mas mantendo parâmetros essenciais do produto.
        """
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        try:
            parsed = urlparse(url)

            # Parâmetros de tracking a remover
            tracking_params = {
                "utm_source", "utm_medium", "utm_campaign", "utm_content",
                "utm_term", "ref", "ref_", "tag", "aff_id", "clickid",
                "social_share", "cm_sw_r_cso_cp_apan_dp",
                "ref_=cm_sw_r_cso_cp_apan_dp",
            }

            if parsed.query:
                params = parse_qs(parsed.query, keep_blank_values=True)
                # Remove parâmetros de tracking
                cleaned_params = {
                    k: v for k, v in params.items()
                    if k.lower() not in tracking_params
                    and not k.lower().startswith("utm_")
                    and not k.lower().startswith("cm_sw_")
                    and not k.lower().startswith("ref_=cm_")
                }

                # Reconstrói a URL
                query = urlencode(cleaned_params, doseq=True)
                url = urlunparse((
                    parsed.scheme, parsed.netloc, parsed.path,
                    parsed.params, query, ""
                ))

            return url

        except Exception:
            return url

    def _detect_coupon_from_title(self, title: str) -> Optional[str]:
        """Detecta código de cupom no título da oferta."""
        upper = title.upper()

        # Padrões comuns de cupom
        patterns = [
            r'(?:CUPOM|CÓD(?:IGO)?|COD(?:IGO)?|USE)\s*:?\s*([A-Z0-9]{3,25})',
            r'(?:CUPOM|CÓDIGO|CODIGO)\s+([A-Z0-9]+(?:\d+|\d+[A-Z]+))',
        ]

        for pattern in patterns:
            match = re.search(pattern, upper)
            if match:
                code = match.group(1).strip()
                # Valida que parece um código real
                if 3 <= len(code) <= 25 and not code.isdigit():
                    return code

        return None

    # ------------------------------------------------------------------
    # Estratégia 2: JSON-LD (fallback)
    # ------------------------------------------------------------------

    def _extract_from_json_ld(self, html: str, seen_ids: set[str]) -> list[Product]:
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

                items = self._extract_json_ld_items(data)

                for item in items:
                    url = ""
                    name = ""
                    image = ""

                    if isinstance(item, dict):
                        url = item.get("url", "") or item.get("@id", "")
                        name = item.get("name", "") or item.get("headline", "")

                        # Extrai imagem do JSON-LD
                        img_data = item.get("image", "")
                        if isinstance(img_data, list) and img_data:
                            image = img_data[0]
                        elif isinstance(img_data, str):
                            image = img_data

                        if not name:
                            inner = item.get("item", {})
                            if isinstance(inner, dict):
                                url = url or inner.get("url", "")
                                name = inner.get("name", "")

                    if not url or url in seen_ids:
                        continue
                    if "pelando.com.br" not in url:
                        continue

                    seen_ids.add(url)
                    price = self._extract_price_from_text(name)

                    if name and len(name) >= 10:
                        products.append(Product(
                            title=self._clean_title(name),
                            link=url,
                            store=self.STORE,
                            price=price,
                            image_url=image,
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

        main_entity = data.get("mainEntity", {})
        if isinstance(main_entity, dict):
            parts = main_entity.get("hasPart", [])
            if isinstance(parts, list):
                items.extend(parts)

        item_list = data.get("itemListElement", [])
        if isinstance(item_list, list):
            items.extend(item_list)

        if data.get("@type") in (
            "Product", "Offer", "Deal", "ListItem", "DiscussionForumPosting"
        ):
            items.append(data)

        return items

    # ------------------------------------------------------------------
    # Estratégia 3: HTML parsing (fallback final)
    # ------------------------------------------------------------------

    def _extract_from_html(self, html: str, seen_ids: set[str]) -> list[Product]:
        """Extrai ofertas diretamente do HTML usando BeautifulSoup."""
        products: list[Product] = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            offer_links = soup.select("a[href*='/d/']")

            for link_el in offer_links:
                href = link_el.get("href", "")
                if not href:
                    continue
                if not href.startswith("http"):
                    href = f"https://www.pelando.com.br{href}"

                if href in seen_ids:
                    continue
                if "/d/" not in href:
                    continue

                title = link_el.get_text(strip=True)

                if len(title) < 15:
                    parent = link_el.parent
                    if parent:
                        for child in parent.children:
                            text = (
                                child.get_text(strip=True)
                                if hasattr(child, "get_text")
                                else str(child).strip()
                            )
                            if len(text) > len(title):
                                title = text

                if not title or len(title) < 10:
                    continue

                seen_ids.add(href)

                # Tenta encontrar imagem próxima
                image_url = ""
                img_el = link_el.find("img")
                if img_el:
                    image_url = img_el.get("src", "") or img_el.get("data-src", "")

                price = self._extract_price_from_text(title)

                products.append(Product(
                    title=self._clean_title(title),
                    link=href,
                    store=self.STORE,
                    price=price,
                    image_url=image_url,
                    extra={"source": "pelando_html"},
                ))

        except Exception as e:
            self._logger.debug(f"Erro ao extrair HTML: {e}")

        return products

    def _extract_price_from_text(self, text: str) -> Optional[float]:
        """Extrai preço de um texto."""
        if not text:
            return None
        match = re.search(r'R\$\s*([\d\.]+,\d{2})', text)
        if match:
            return self._parse_price(f"R$ {match.group(1)}")
        return None
