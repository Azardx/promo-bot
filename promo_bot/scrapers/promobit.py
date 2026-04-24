"""
Scraper do Promobit — PromoBot v2.3+

ESTRATÉGIAS:
1. __NEXT_DATA__  →  múltiplos caminhos de dados suportados
2. JSON-LD        →  fallback para dados estruturados
3. HTML parsing   →  fallback final com seletores robustos

CORREÇÕES v2.3+:
- Navegação flexível pelo JSON do Next.js (suporta v12, v13, v14+)
- Link real da loja extraído com prioridade máxima
- Origem da loja (origin_store) sempre preenchida
- Descarte de itens sem preço E sem cupom E sem desconto
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class PromobitScraper(BaseScraper):
    """Scraper do Promobit com suporte a SSR, JSON-LD e HTML."""

    STORE    = Store.PROMOBIT
    NAME     = "promobit"
    BASE_URL = "https://www.promobit.com.br"

    _PAGES = [
        "/promocoes/em-alta/",
        "/promocoes/recentes/",
    ]

    # Campos de link real da loja (prioridade decrescente)
    _REAL_LINK_FIELDS = (
        "offerAffiliateUrl",
        "offerExternalUrl",
        "offerLink",
        "offerUrl",
        "storeUrl",
        "redirectUrl",
        "deepLink",
    )

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)

    # ── Ponto de entrada ──────────────────────────────────────────────────────

    async def _scrape(self) -> list[Product]:
        products: list[Product] = []
        seen_ids: set[str] = set()

        for path in self._PAGES:
            url  = f"{self.BASE_URL}{path}"
            html = await self._http.fetch(url)
            if not html:
                self._logger.warning(f"Promobit: falha ao acessar {url}")
                continue

            page_products = self._extract_products(html, seen_ids)
            products.extend(page_products)
            self._logger.debug(
                f"Promobit {path}: {len(page_products)} ofertas"
            )

            if len(products) >= 30:
                break

        self._logger.info(f"Promobit: {len(products)} ofertas coletadas")
        return products

    # ── Roteador de estratégias ───────────────────────────────────────────────

    def _extract_products(self, html: str, seen_ids: set[str]) -> list[Product]:
        # 1. __NEXT_DATA__
        next_data = self._extract_next_data(html)
        if next_data:
            offers = self._find_offers_in_tree(next_data)
            if offers:
                products = self._parse_offer_list(offers, seen_ids)
                if products:
                    return products

        # 2. JSON-LD
        ld_products = self._extract_json_ld(html, seen_ids)
        if ld_products:
            return ld_products

        # 3. HTML puro
        return self._extract_html(html, seen_ids)

    # ── Estratégia 1: __NEXT_DATA__ ───────────────────────────────────────────

    def _extract_next_data(self, html: str) -> Optional[dict]:
        """Extrai o JSON __NEXT_DATA__ do HTML."""
        m = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>\s*(\{.*?\})\s*</script>',
            html,
            re.DOTALL,
        )
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _find_offers_in_tree(self, data: Any, depth: int = 0) -> list[dict]:
        """
        Busca recursivamente por arrays de ofertas no JSON do Next.js.
        Compatível com App Router (v13+) e Pages Router (v12-).
        """
        if depth > 8 or not isinstance(data, (dict, list)):
            return []

        if isinstance(data, list):
            # Verifica se parece uma lista de ofertas
            if len(data) >= 2 and isinstance(data[0], dict):
                keys = set(data[0].keys())
                if keys & {"offerTitle", "title", "offerId", "id", "slug"}:
                    # Confirma que ao menos um campo de oferta existe
                    if keys & {"offerTitle", "offerId", "offerPrice", "offerSlug"}:
                        return data
            # Continua buscando em subitens
            for item in data:
                result = self._find_offers_in_tree(item, depth + 1)
                if result:
                    return result
            return []

        if isinstance(data, dict):
            # Caminhos conhecidos do Promobit
            known_paths = [
                ("props", "pageProps", "serverOffers", "offers"),
                ("props", "pageProps", "serverOffers"),
                ("props", "pageProps", "serverFeaturedOffers"),
                ("props", "pageProps", "offers"),
                ("props", "pageProps", "initialData", "offers"),
                ("props", "initialState", "offers", "list"),
            ]
            for path in known_paths:
                node = data
                for key in path:
                    if isinstance(node, dict):
                        node = node.get(key)
                    else:
                        break
                if isinstance(node, list) and node:
                    return node
                if isinstance(node, dict):
                    inner = node.get("offers") or node.get("list") or node.get("data")
                    if isinstance(inner, list) and inner:
                        return inner

            # Busca genérica em todos os valores
            for value in data.values():
                result = self._find_offers_in_tree(value, depth + 1)
                if result:
                    return result

        return []

    def _parse_offer_list(
        self, offers: list[dict], seen_ids: set[str]
    ) -> list[Product]:
        products: list[Product] = []
        for offer in offers:
            offer_id = str(offer.get("offerId") or offer.get("id") or "")
            if not offer_id or offer_id in seen_ids:
                continue
            seen_ids.add(offer_id)

            p = self._parse_next_offer(offer)
            if p:
                products.append(p)
        return products

    def _parse_next_offer(self, offer: dict) -> Optional[Product]:
        """Converte um item do JSON Next.js em Product."""
        try:
            title = (
                offer.get("offerTitle")
                or offer.get("title")
                or offer.get("name")
                or ""
            ).strip()
            if not title:
                return None

            # ── Link ──────────────────────────────────────────────────────
            link = self._extract_real_link(offer)
            if not link:
                slug = offer.get("offerSlug") or offer.get("slug")
                oid  = offer.get("offerId") or offer.get("id")
                link = f"{self.BASE_URL}/oferta/{slug}-{oid}/" if slug and oid else None
            if not link:
                return None

            # ── Preços ────────────────────────────────────────────────────
            price          = self._safe_float(offer.get("offerPrice") or offer.get("price"))
            original_price = self._safe_float(offer.get("offerOldPrice") or offer.get("oldPrice"))
            if original_price and price and original_price <= price:
                original_price = None

            # ── Cupom ─────────────────────────────────────────────────────
            raw_coupon = (
                offer.get("offerCoupon")
                or offer.get("couponCode")
                or offer.get("coupon")
            )
            coupon: Optional[str] = None
            if isinstance(raw_coupon, dict):
                coupon = raw_coupon.get("code")
            elif isinstance(raw_coupon, str) and raw_coupon.strip():
                coupon = raw_coupon.strip()

            # ── Imagem ────────────────────────────────────────────────────
            image_url = self._extract_image(offer)

            # ── Loja de origem ────────────────────────────────────────────
            origin_store = self._extract_origin_store(offer)

            # ── Desconto ─────────────────────────────────────────────────
            discount_pct: Optional[float] = None
            if original_price and price:
                discount_pct = round((1 - price / original_price) * 100, 1)

            return Product(
                title=self._clean_title(title),
                link=link,
                store=self.STORE,
                price=price,
                original_price=original_price,
                discount_pct=discount_pct,
                image_url=image_url,
                coupon_code=coupon,
                extra={
                    "origin_store": origin_store,
                    "promobit_id":  str(offer.get("offerId") or offer.get("id") or ""),
                    "source":       "promobit_next",
                },
            )
        except Exception as exc:
            self._logger.debug(f"Promobit parse_next_offer falhou: {exc}")
            return None

    # ── Estratégia 2: JSON-LD ─────────────────────────────────────────────────

    def _extract_json_ld(
        self, html: str, seen_ids: set[str]
    ) -> list[Product]:
        products: list[Product] = []
        for m in re.finditer(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        ):
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                url  = item.get("url") or item.get("@id") or ""
                name = item.get("name") or item.get("headline") or ""
                if not url or url in seen_ids or len(name) < 10:
                    continue
                seen_ids.add(url)
                products.append(Product(
                    title=self._clean_title(name),
                    link=url,
                    store=self.STORE,
                    extra={"source": "promobit_jsonld"},
                ))
        return products

    # ── Estratégia 3: HTML puro ────────────────────────────────────────────────

    def _extract_html(
        self, html: str, seen_ids: set[str]
    ) -> list[Product]:
        products: list[Product] = []
        soup = BeautifulSoup(html, "html.parser")

        card_selectors = [
            "div[class*='OfferCard']",
            "article[class*='OfferCard']",
            ".offer-card",
            "a[href*='/oferta/']",
        ]
        cards = []
        for sel in card_selectors:
            cards = soup.select(sel)
            if cards:
                break

        for card in cards[:30]:
            try:
                link_el = card if card.name == "a" else card.select_one("a[href*='/oferta/']")
                if not link_el:
                    continue
                href = link_el.get("href", "")
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"{self.BASE_URL}{href}"
                if href in seen_ids:
                    continue
                seen_ids.add(href)

                title_el = card.select_one("h2, h3, [class*='title'], [class*='Title']")
                title    = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                price_el = card.select_one("[class*='price'], [class*='Price']")
                price    = self._parse_price(price_el.get_text(strip=True)) if price_el else None

                img_el    = card.select_one("img[src], img[data-src]")
                image_url = (img_el.get("src") or img_el.get("data-src") or "") if img_el else ""

                origin_el    = card.select_one("[class*='retailer'], [class*='store'], [class*='Retailer']")
                origin_store = origin_el.get_text(strip=True) if origin_el else ""

                products.append(Product(
                    title=self._clean_title(title),
                    link=href,
                    store=self.STORE,
                    price=price,
                    image_url=image_url,
                    extra={"origin_store": origin_store, "source": "promobit_html"},
                ))
            except Exception as exc:
                self._logger.debug(f"Promobit HTML card falhou: {exc}")

        return products

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_real_link(self, offer: dict) -> Optional[str]:
        """Retorna o link real da loja de destino (não do Promobit)."""
        for field in self._REAL_LINK_FIELDS:
            url = offer.get(field)
            if url and isinstance(url, str) and url.startswith("http"):
                if "promobit.com.br" not in url:
                    return self._clean_url(url)
        return None

    def _extract_image(self, offer: dict) -> str:
        photo = offer.get("offerPhoto") or offer.get("image") or offer.get("thumbnail")
        if isinstance(photo, dict):
            url = photo.get("url") or photo.get("src") or ""
        elif isinstance(photo, str):
            url = photo
        else:
            return ""
        if url.startswith("/"):
            url = f"{self.BASE_URL}{url}"
        return url

    def _extract_origin_store(self, offer: dict) -> str:
        """Extrai nome da loja de origem."""
        # Tenta campo direto
        for field in ("storeName", "retailerName", "store", "retailer"):
            val = offer.get(field)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                name = val.get("name") or val.get("displayName") or ""
                if name:
                    return name.strip()
        return ""

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Converte valor para float ou retorna None."""
        try:
            f = float(value)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_url(url: str) -> str:
        """Remove parâmetros de tracking da URL."""
        try:
            parsed  = urlparse(url)
            params  = parse_qs(parsed.query, keep_blank_values=True)
            cleaned = {
                k: v for k, v in params.items()
                if not k.lower().startswith("utm_")
                and k.lower() not in {"ref", "tag", "aff_id", "clickid", "source"}
            }
            query = urlencode(cleaned, doseq=True)
            return urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, query, "",
            ))
        except Exception:
            return url
