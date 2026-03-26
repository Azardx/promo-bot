"""
Scraper do AliExpress via best.aliexpress.com e busca.

Estratégia principal: acessa best.aliexpress.com com cookie de região BR
para obter dados SSR (Server-Side Rendered) com preços em BRL, imagens,
descontos e cupons globais. Fallback via busca em pt.aliexpress.com.

Dados extraídos do bloco init-data do script SSR:
  - productTitle blocks: produtos com título PT-BR, preço BRL, desconto, imagem
  - itemList: super deals com preço BRL, desconto, imagem (sem título)
  - coupons: cupons globais do AliExpress BR (ex: BRMARCA1, BRMARCA2)
"""

from __future__ import annotations

import json
import re
from typing import Optional

from promo_bot.database.models import Product, Store
from promo_bot.scrapers.base import BaseScraper
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient


class AliExpressScraper(BaseScraper):
    """Scraper do AliExpress via dados SSR com preços em BRL."""

    STORE = Store.ALIEXPRESS
    NAME = "aliexpress"
    BASE_URL = "https://pt.aliexpress.com"

    # Página principal de ofertas (retorna dados SSR com preços em BRL)
    BEST_URL = "https://best.aliexpress.com/"

    # Busca como fallback
    SEARCH_URL = "https://www.aliexpress.com/w/wholesale-{query}.html"

    # Link direto do produto
    PRODUCT_URL = "https://pt.aliexpress.com/item/{product_id}.html"

    # Headers com cookie de região BR para preços em BRL
    BR_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://pt.aliexpress.com/",
        "Cookie": "aep_usuc_f=site=bra&c_tp=BRL&region=BR&b_locale=pt_BR",
    }

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        super().__init__(http_client, cache)
        self._global_coupons: list[dict] = []

    async def _scrape(self) -> list[Product]:
        """Coleta ofertas do AliExpress via dados SSR."""
        products: list[Product] = []

        # Estratégia 1: best.aliexpress.com (dados SSR com preços BRL)
        try:
            best_products = await self._scrape_best_page()
            products.extend(best_products)
        except Exception as e:
            self._logger.warning(f"Erro ao acessar best.aliexpress.com: {e}")

        # Estratégia 2: Busca por termos populares (fallback)
        if len(products) < 5:
            try:
                search_products = await self._scrape_search("super-deals")
                # Evita duplicatas
                seen_ids = {p.extra.get("ali_id", "") for p in products if p.extra.get("ali_id")}
                for p in search_products:
                    ali_id = p.extra.get("ali_id", "")
                    if ali_id and ali_id not in seen_ids:
                        products.append(p)
                        seen_ids.add(ali_id)
                    elif not ali_id:
                        products.append(p)
            except Exception as e:
                self._logger.debug(f"Erro na busca AliExpress: {e}")

        # Aplica cupons globais aos produtos (se encontrados)
        if self._global_coupons:
            self._apply_best_coupon(products)

        self._logger.info(
            f"AliExpress: {len(products)} ofertas coletadas "
            f"({len(self._global_coupons)} cupons globais encontrados)"
        )
        return products

    async def _scrape_best_page(self) -> list[Product]:
        """Coleta ofertas da página best.aliexpress.com via dados SSR."""
        products: list[Product] = []

        html = await self._http.fetch(self.BEST_URL, headers=self.BR_HEADERS)
        if not html:
            self._logger.warning("Falha ao acessar best.aliexpress.com")
            return products

        # Extrai a seção de dados SSR (init-data)
        data_section = self._extract_init_data(html)
        if not data_section:
            self._logger.warning("Seção init-data não encontrada no HTML")
            return products

        # Extrai cupons globais do AliExpress BR
        self._global_coupons = self._extract_global_coupons(data_section)

        # Extrai produtos da seção "Top Selection" (productTitle blocks)
        top_products = self._extract_product_title_blocks(data_section)
        products.extend(top_products)

        # Extrai produtos do itemList (Super Deals)
        item_list_products = self._extract_item_list(data_section)
        # Evita duplicatas por ID
        seen_ids = {p.extra.get("ali_id", "") for p in products}
        for p in item_list_products:
            ali_id = p.extra.get("ali_id", "")
            if ali_id not in seen_ids:
                products.append(p)
                seen_ids.add(ali_id)

        return products

    def _extract_init_data(self, html: str) -> str:
        """Extrai a seção init-data do script SSR."""
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)

        for script in scripts:
            init_start = script.find("init-data-start")
            init_end = script.find("init-data-end")
            if init_start > 0 and init_end > init_start:
                return script[init_start:init_end]

        return ""

    def _extract_product_title_blocks(self, data_section: str) -> list[Product]:
        """
        Extrai blocos de produto com productTitle da seção init-data.

        Cada bloco contém:
        - id: ID numérico do produto
        - productTitle: título em PT-BR
        - localizedMinPriceString: preço atual em R$
        - localizedOriMinPriceString: preço original em R$
        - discount: percentual de desconto
        - productImage: URL da imagem
        - reviewStar: avaliação
        - tradeCount: quantidade vendida
        """
        products: list[Product] = []
        positions = [m.start() for m in re.finditer(r'"productTitle"', data_section)]

        for pos in positions:
            try:
                block = self._extract_json_object(data_section, pos)
                if not block:
                    continue

                product = self._parse_product_block(block)
                if product:
                    products.append(product)
            except Exception as e:
                self._logger.debug(f"Erro ao parsear bloco productTitle: {e}")

        self._logger.debug(f"AliExpress: {len(products)} produtos de Top Selection")
        return products

    def _extract_json_object(self, text: str, anchor_pos: int) -> Optional[dict]:
        """Extrai o objeto JSON que contém a posição anchor_pos."""
        # Vai para trás para encontrar o início do objeto
        depth = 0
        start = anchor_pos
        for i in range(anchor_pos, -1, -1):
            c = text[i]
            if c == "}":
                depth += 1
            elif c == "{":
                if depth == 0:
                    start = i
                    break
                depth -= 1

        # Vai para frente para encontrar o fim do objeto
        depth = 0
        end = anchor_pos
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        try:
            return json.loads(text[start:end])
        except (json.JSONDecodeError, ValueError):
            return None

    def _parse_product_block(self, block: dict) -> Optional[Product]:
        """Converte um bloco productTitle em Product."""
        title = block.get("productTitle", "").strip()
        if not title or len(title) < 10:
            return None

        product_id = block.get("id")
        if not product_id:
            return None

        link = self.PRODUCT_URL.format(product_id=product_id)

        # Preço atual
        price_str = block.get("localizedMinPriceString", "") or str(block.get("minPrice", ""))
        price = self._parse_price(price_str)

        # Preço original
        ori_price_str = block.get("localizedOriMinPriceString", "") or str(block.get("oriMinPrice", ""))
        original_price = self._parse_price(ori_price_str)

        # Desconto
        discount_pct = None
        discount_raw = block.get("discount")
        if discount_raw is not None:
            try:
                discount_pct = float(discount_raw)
            except (ValueError, TypeError):
                pass

        # Imagem
        image_url = block.get("productImage", "")
        if image_url and not image_url.startswith("http"):
            image_url = f"https:{image_url}"

        # Avaliação e vendas (extra)
        review_star = block.get("reviewStar", "")
        trade_count = block.get("tradeCount", "")

        return Product(
            title=self._clean_title(title),
            link=link,
            store=self.STORE,
            price=price,
            original_price=original_price,
            discount_pct=discount_pct,
            image_url=image_url,
            extra={
                "ali_id": str(product_id),
                "review_star": review_star,
                "trade_count": trade_count,
            },
        )

    def _extract_item_list(self, data_section: str) -> list[Product]:
        """
        Extrai produtos do itemList (Super Deals).

        Cada item contém:
        - itemId: ID do produto
        - price: preço em R$
        - originPrice: preço original em R$
        - discountRate: ex: "-80%"
        - itemImgUrl: URL da imagem
        - stars: avaliação
        - totalSale: vendas
        """
        products: list[Product] = []

        match = re.search(r'"itemList"\s*:\s*(\[.*?\])', data_section, re.DOTALL)
        if not match:
            return products

        try:
            items = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            return products

        for item in items:
            try:
                product = self._parse_item_list_entry(item)
                if product:
                    products.append(product)
            except Exception as e:
                self._logger.debug(f"Erro ao parsear item do itemList: {e}")

        self._logger.debug(f"AliExpress: {len(products)} produtos de Super Deals")
        return products

    def _parse_item_list_entry(self, item: dict) -> Optional[Product]:
        """Converte um item do itemList em Product."""
        item_id = item.get("itemId")
        if not item_id:
            return None

        link = self.PRODUCT_URL.format(product_id=item_id)

        # Preço
        price = self._parse_price(str(item.get("price", "")))

        # Preço original
        original_price = self._parse_price(str(item.get("originPrice", "")))

        # Desconto (formato: "-80%")
        discount_pct = None
        discount_str = item.get("discountRate", "")
        if discount_str:
            disc_match = re.search(r"(\d+)", str(discount_str))
            if disc_match:
                discount_pct = float(disc_match.group(1))

        # Imagem
        image_url = item.get("itemImgUrl", "")
        if image_url and not image_url.startswith("http"):
            image_url = f"https:{image_url}"

        # Super Deals não tem título no itemList — usa título genérico
        # O título será "Super Deal" + desconto (será substituído se possível)
        title = f"Super Deal AliExpress"
        if discount_pct:
            title = f"Super Deal -{discount_pct:.0f}% OFF"

        return Product(
            title=title,
            link=link,
            store=self.STORE,
            price=price,
            original_price=original_price,
            discount_pct=discount_pct,
            image_url=image_url,
            extra={
                "ali_id": str(item_id),
                "stars": item.get("stars", ""),
                "total_sale": item.get("totalSale", ""),
                "is_super_deal": True,
            },
        )

    def _extract_global_coupons(self, data_section: str) -> list[dict]:
        """
        Extrai cupons globais do AliExpress BR.

        Retorna lista de dicts com:
        - code: código do cupom (ex: "BRMARCA2")
        - value: descrição do valor (ex: "R$46 off")
        - limitation: condição (ex: "em R$400+")
        """
        coupons: list[dict] = []

        # Busca blocos de cupom no formato JSON
        coupon_pattern = re.finditer(
            r'"couponCode"\s*:\s*"([^"]+)".*?"couponValue"\s*:\s*"([^"]*)".*?"couponLimitation"\s*:\s*"([^"]*)"',
            data_section,
            re.DOTALL,
        )

        seen_codes: set[str] = set()
        for match in coupon_pattern:
            code = match.group(1).strip()
            if code in seen_codes:
                continue
            seen_codes.add(code)

            value = match.group(2).strip()
            limitation = match.group(3).strip()

            coupons.append({
                "code": code,
                "value": value,
                "limitation": limitation,
            })

        if coupons:
            self._logger.info(
                f"AliExpress: {len(coupons)} cupons globais encontrados: "
                + ", ".join(f"{c['code']} ({c['value']} {c['limitation']})" for c in coupons)
            )

        return coupons

    def _apply_best_coupon(self, products: list[Product]) -> None:
        """
        Aplica o melhor cupom global disponível a cada produto.

        Analisa o preço do produto e verifica qual cupom se aplica
        (baseado na limitação mínima de compra).
        """
        if not self._global_coupons or not products:
            return

        # Parseia as limitações dos cupons
        parsed_coupons: list[dict] = []
        for coupon in self._global_coupons:
            min_value = 0.0
            limitation = coupon.get("limitation", "")
            min_match = re.search(r"R?\$?\s*([\d.,]+)", limitation)
            if min_match:
                min_value = self._parse_price(min_match.group(1)) or 0.0

            discount_value = 0.0
            value_str = coupon.get("value", "")
            val_match = re.search(r"R?\$?\s*([\d.,]+)", value_str)
            if val_match:
                discount_value = self._parse_price(val_match.group(1)) or 0.0

            parsed_coupons.append({
                "code": coupon["code"],
                "min_value": min_value,
                "discount_value": discount_value,
                "description": f"{coupon['value']} {coupon['limitation']}",
            })

        # Ordena por desconto (maior primeiro)
        parsed_coupons.sort(key=lambda c: c["discount_value"], reverse=True)

        for product in products:
            if product.coupon_code:
                continue  # Já tem cupom

            if product.price is None:
                continue

            # Encontra o melhor cupom aplicável
            for coupon in parsed_coupons:
                if product.price >= coupon["min_value"] > 0:
                    product.coupon_code = coupon["code"]
                    break

    # ─── Fallback: Busca ────────────────────────────────────────────

    async def _scrape_search(self, query: str) -> list[Product]:
        """Busca promoções via página de busca como fallback."""
        products: list[Product] = []

        try:
            url = self.SEARCH_URL.format(query=query)
            params = {"sorttype": "total_tranpro_desc"}
            html = await self._http.fetch(url, headers=self.BR_HEADERS, params=params)
            if not html:
                return products

            # Tenta extrair dados SSR da busca
            data_section = self._extract_init_data(html)
            if data_section:
                products = self._extract_product_title_blocks(data_section)

            # Fallback: itemList
            if not products and data_section:
                products = self._extract_item_list(data_section)

            # Fallback: parsing HTML
            if not products:
                products = self._extract_from_html(html)

        except Exception as e:
            self._logger.debug(f"Erro na busca AliExpress '{query}': {e}")

        return products

    def _extract_from_html(self, html: str) -> list[Product]:
        """Fallback: extrai ofertas via parsing HTML."""
        products: list[Product] = []

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")

            # Busca links de produtos
            for link_el in soup.select("a[href*='/item/']")[:20]:
                try:
                    href = link_el.get("href", "")
                    if not href:
                        continue

                    # Extrai ID do produto
                    id_match = re.search(r"/item/(\d+)\.html", href)
                    if not id_match:
                        continue

                    product_id = id_match.group(1)
                    link = self.PRODUCT_URL.format(product_id=product_id)

                    # Título
                    title = link_el.get_text(strip=True)
                    if len(title) < 10:
                        parent = link_el.parent
                        if parent:
                            title_el = parent.select_one(
                                "[class*='title'], [class*='name'], h3, h4"
                            )
                            if title_el:
                                title = title_el.get_text(strip=True)

                    if len(title) < 10:
                        continue

                    # Preço
                    price = None
                    parent = link_el.find_parent(
                        class_=re.compile(r"card|item|product", re.I)
                    )
                    if parent:
                        price_el = parent.select_one("[class*='price'], [class*='Price']")
                        if price_el:
                            price = self._parse_price(price_el.get_text(strip=True))

                    # Imagem
                    image_url = ""
                    if parent:
                        img_el = parent.select_one("img[src]")
                        if img_el:
                            image_url = img_el.get("src", "")
                            if image_url and not image_url.startswith("http"):
                                image_url = f"https:{image_url}"

                    products.append(
                        Product(
                            title=self._clean_title(title),
                            link=link,
                            store=self.STORE,
                            price=price,
                            image_url=image_url,
                            extra={"ali_id": product_id},
                        )
                    )
                except Exception:
                    continue

        except Exception as e:
            self._logger.debug(f"Erro no parsing HTML AliExpress: {e}")

        return products
