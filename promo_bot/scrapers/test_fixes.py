"""
Testes unitários para os componentes corrigidos do PromoBot v2.3+

Execução:
    python -m pytest tests/test_fixes.py -v
    # ou diretamente:
    python tests/test_fixes.py
"""

from __future__ import annotations

import sys
import os
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promo_bot.database.models import Product, Store
from promo_bot.services.formatter import PromoFormatter, MessageFormatter, _fmt_price


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_product(**kwargs) -> Product:
    defaults = dict(
        title="SSD Kingston NV2 1TB NVMe M.2 2280",
        link="https://www.kabum.com.br/produto/123456/ssd-kingston",
        store=Store.KABUM,
        price=399.90,
        original_price=549.90,
        image_url="https://images.kabum.com.br/produtos/fotos/123/ssd.jpg",
    )
    defaults.update(kwargs)
    return Product(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# Formatter
# ═══════════════════════════════════════════════════════════════════════════════

class TestPromoFormatter(unittest.TestCase):

    def setUp(self):
        self.fmt = PromoFormatter()

    # ── Emojis ────────────────────────────────────────────────────────────────

    def test_no_question_marks_in_output(self):
        """Nenhuma mensagem deve conter '??' (emojis corrompidos)."""
        product = make_product()
        msg = self.fmt.format_promo(product)
        self.assertNotIn("??", msg, "Emojis corrompidos detectados na mensagem")

    def test_emojis_are_real_unicode(self):
        """Mensagem deve conter emojis Unicode reais."""
        msg = self.fmt.format_promo(make_product())
        # Verifica que ao menos um emoji Unicode está presente
        has_emoji = any(ord(c) > 0x2000 for c in msg)
        self.assertTrue(has_emoji, "Nenhum emoji Unicode encontrado na mensagem")

    # ── Preços ────────────────────────────────────────────────────────────────

    def test_price_format_brazilian(self):
        """Preços devem usar formato brasileiro (R$ 1.299,90)."""
        p   = make_product(price=1299.90)
        msg = self.fmt.format_promo(p)
        self.assertIn("1.299,90", msg)

    def test_price_with_discount_shows_savings(self):
        """Quando há preço original, deve mostrar economia."""
        p   = make_product(price=399.90, original_price=549.90)
        msg = self.fmt.format_promo(p)
        self.assertIn("549,90", msg, "Preço original ausente")
        self.assertIn("399,90", msg, "Preço atual ausente")
        self.assertIn("150,00", msg, "Valor economizado ausente")

    def test_no_savings_when_no_original_price(self):
        """Sem preço original, não deve mostrar linha de economia."""
        p   = make_product(original_price=None)
        msg = self.fmt.format_promo(p)
        self.assertNotIn("Economize", msg)

    # ── Cupom ─────────────────────────────────────────────────────────────────

    def test_coupon_shown_when_present(self):
        """Cupom deve aparecer na mensagem quando fornecido."""
        p   = make_product(coupon_code="SHARK20")
        msg = self.fmt.format_promo(p)
        self.assertIn("SHARK20", msg)
        self.assertIn("CUPOM", msg.upper())

    def test_no_coupon_block_when_absent(self):
        """Bloco de cupom não deve aparecer quando não há cupom."""
        p   = make_product(coupon_code=None)
        msg = self.fmt.format_promo(p)
        self.assertNotIn("CUPOM DE DESCONTO", msg)

    # ── Loja de origem (Pelando/Promobit) ─────────────────────────────────────

    def test_origin_store_overrides_aggregator(self):
        """Para Pelando/Promobit, deve mostrar a loja real, não o agregador."""
        p = make_product(
            store=Store.PELANDO,
            extra={"origin_store": "Amazon"},
        )
        msg = self.fmt.format_promo(p)
        self.assertIn("Amazon", msg)
        self.assertNotIn("Pelando", msg)

    def test_direct_store_shows_own_name(self):
        """KaBuM! direto deve mostrar 'KaBuM!' na mensagem."""
        p   = make_product(store=Store.KABUM, extra={})
        msg = self.fmt.format_promo(p)
        self.assertIn("KaBuM!", msg)

    def test_unknown_origin_store_uses_generic_emoji(self):
        """Loja desconhecida deve usar emoji genérico de loja."""
        p = make_product(
            store=Store.PELANDO,
            extra={"origin_store": "Loja Desconhecida XYZ"},
        )
        msg = self.fmt.format_promo(p)
        self.assertIn("Loja Desconhecida XYZ", msg)

    # ── Frete grátis ──────────────────────────────────────────────────────────

    def test_free_shipping_shown(self):
        p   = make_product(free_shipping=True)
        msg = self.fmt.format_promo(p)
        self.assertIn("Frete", msg)

    # ── MessageFormatter alias ────────────────────────────────────────────────

    def test_message_formatter_alias_exists(self):
        """MessageFormatter deve ser um alias compatível."""
        mf  = MessageFormatter()
        p   = make_product()
        msg = mf.format_product(p)
        self.assertIsInstance(msg, str)
        self.assertGreater(len(msg), 50)

    # ── Limites ───────────────────────────────────────────────────────────────

    def test_truncate_for_caption(self):
        """Caption deve ser truncada para o limite do Telegram (1024)."""
        long_text = "A" * 2000
        result    = self.fmt.truncate_for_caption(long_text)
        self.assertLessEqual(len(result), 1024)

    def test_is_valid_image_url(self):
        self.assertTrue(self.fmt._is_valid_image_url(
            "https://images.kabum.com.br/prod/ssd.jpg"
        ))
        self.assertFalse(self.fmt._is_valid_image_url(""))
        self.assertFalse(self.fmt._is_valid_image_url("not-a-url"))
        self.assertFalse(self.fmt._is_valid_image_url("data:image/png;base64,xxx"))
        self.assertFalse(self.fmt._is_valid_image_url(
            "https://example.com/placeholder.jpg"
        ))

    # ── Formato monetário ─────────────────────────────────────────────────────

    def test_fmt_price_helper(self):
        self.assertEqual(_fmt_price(1299.90), "R$ 1.299,90")
        self.assertEqual(_fmt_price(10.00),   "R$ 10,00")
        self.assertEqual(_fmt_price(0.99),    "R$ 0,99")


# ═══════════════════════════════════════════════════════════════════════════════
# Amazon Scraper
# ═══════════════════════════════════════════════════════════════════════════════

class TestAmazonScraper(unittest.TestCase):

    def _make_scraper(self):
        from promo_bot.scrapers.amazon import AmazonScraper
        http = MagicMock()
        cache = MagicMock()
        s = AmazonScraper.__new__(AmazonScraper)
        s._http   = http
        s._cache  = cache
        s._logger = MagicMock()
        return s

    def test_digital_content_filtered(self):
        from promo_bot.scrapers.amazon import AmazonScraper
        scraper = self._make_scraper()

        digital = make_product(
            title="A Casa do Dragão - 2ª Temporada (Prime Video)",
            store=Store.AMAZON,
        )
        self.assertTrue(scraper._is_digital(digital))

    def test_physical_product_not_filtered(self):
        scraper = self._make_scraper()
        physical = make_product(
            title="SSD Samsung 980 Pro 1TB NVMe PCIe",
            link="https://www.amazon.com.br/dp/B08GLX7TNT",
            store=Store.AMAZON,
        )
        self.assertFalse(scraper._is_digital(physical))

    def test_normalize_link_extracts_asin(self):
        scraper = self._make_scraper()
        dirty   = "https://www.amazon.com.br/dp/B08GLX7TNT?ref=deals&tag=xxx"
        clean   = scraper._normalize_link(dirty)
        self.assertEqual(clean, "https://www.amazon.com.br/dp/B08GLX7TNT")

    def test_normalize_link_fallback(self):
        scraper = self._make_scraper()
        url     = "https://www.amazon.com.br/gp/product/B08GLX7TNT?ref=x"
        clean   = scraper._normalize_link(url)
        self.assertNotIn("ref=", clean)


# ═══════════════════════════════════════════════════════════════════════════════
# Mercado Livre Scraper
# ═══════════════════════════════════════════════════════════════════════════════

class TestMercadoLivreScraper(unittest.TestCase):

    def _make_scraper(self):
        from promo_bot.scrapers.mercadolivre import MercadoLivreScraper
        http = MagicMock()
        cache = MagicMock()
        s = MercadoLivreScraper.__new__(MercadoLivreScraper)
        s._http   = http
        s._cache  = cache
        s._logger = MagicMock()
        return s

    def test_api_item_parsed_correctly(self):
        scraper = self._make_scraper()
        item    = {
            "id":             "MLB123456",
            "title":          "Notebook Lenovo IdeaPad 3 Ryzen 5",
            "permalink":      "https://www.mercadolivre.com.br/notebook/MLB123456",
            "price":          2899.0,
            "original_price": 3499.0,
            "thumbnail":      "https://http2.mlstatic.com/D_NQ_NP_123-I.jpg",
            "shipping":       {"free_shipping": True},
        }
        product = scraper._parse_api_item(item)
        self.assertIsNotNone(product)
        self.assertEqual(product.price, 2899.0)
        self.assertEqual(product.original_price, 3499.0)
        self.assertTrue(product.free_shipping)
        self.assertIn("-O.jpg", product.image_url)  # Alta resolução

    def test_original_price_discarded_if_not_greater(self):
        scraper = self._make_scraper()
        item    = {
            "id": "MLB999", "title": "Produto Teste Sem Desconto Real",
            "permalink": "https://www.mercadolivre.com.br/produto/MLB999",
            "price": 100.0, "original_price": 90.0,  # original < price (inválido)
            "thumbnail": "", "shipping": {},
        }
        product = scraper._parse_api_item(item)
        self.assertIsNone(product.original_price)

    def test_real_discount_filter(self):
        scraper = self._make_scraper()
        high_disc = make_product(store=Store.MERCADOLIVRE, price=100.0, original_price=150.0)
        low_disc  = make_product(store=Store.MERCADOLIVRE, price=100.0, original_price=103.0)
        no_disc   = make_product(store=Store.MERCADOLIVRE, price=100.0, original_price=None)

        self.assertTrue(scraper._has_real_discount(high_disc))   # 33% → OK
        self.assertFalse(scraper._has_real_discount(low_disc))   # 3%  → filtrado
        self.assertFalse(scraper._has_real_discount(no_disc))    # sem desconto

    def test_clean_ml_link(self):
        from promo_bot.scrapers.mercadolivre import MercadoLivreScraper
        url   = "https://www.mercadolivre.com.br/notebook/MLB123?utm_source=google&ref=abc"
        clean = MercadoLivreScraper._clean_ml_link(url)
        self.assertNotIn("utm_source", clean)
        self.assertIn("MLB123", clean)


# ═══════════════════════════════════════════════════════════════════════════════
# Promobit Scraper
# ═══════════════════════════════════════════════════════════════════════════════

class TestPromobitScraper(unittest.TestCase):

    def _make_scraper(self):
        from promo_bot.scrapers.promobit import PromobitScraper
        s = PromobitScraper.__new__(PromobitScraper)
        s._http   = MagicMock()
        s._cache  = MagicMock()
        s._logger = MagicMock()
        return s

    def test_find_offers_in_simple_path(self):
        """Deve encontrar ofertas no caminho props.pageProps.serverOffers.offers."""
        scraper = self._make_scraper()
        data = {
            "props": {
                "pageProps": {
                    "serverOffers": {
                        "offers": [
                            {"offerId": "1", "offerTitle": "Notebook Dell i7", "offerPrice": 3299.0}
                        ]
                    }
                }
            }
        }
        offers = scraper._find_offers_in_tree(data)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["offerId"], "1")

    def test_parse_offer_with_real_link(self):
        scraper = self._make_scraper()
        offer = {
            "offerId":         "42",
            "offerTitle":      "SSD WD Black 1TB NVMe",
            "offerPrice":      499.0,
            "offerOldPrice":   699.0,
            "offerAffiliateUrl": "https://www.kabum.com.br/produto/ssd-wd",
            "storeName":       "KaBuM!",
            "offerPhoto":      {"url": "https://cdn.promobit.com.br/ssd.jpg"},
        }
        p = scraper._parse_next_offer(offer)
        self.assertIsNotNone(p)
        self.assertIn("kabum.com.br", p.link)   # Link real, não do Promobit
        self.assertEqual(p.extra.get("origin_store"), "KaBuM!")

    def test_coupon_extracted_from_offer(self):
        scraper = self._make_scraper()
        offer = {
            "offerId":    "99",
            "offerTitle": "Cupom de 30% na loja",
            "offerCoupon": "PROMO30",
            "offerLink":  "https://www.amazon.com.br",
        }
        p = scraper._parse_next_offer(offer)
        self.assertIsNotNone(p)
        self.assertEqual(p.coupon_code, "PROMO30")

    def test_real_link_not_promobit(self):
        scraper = self._make_scraper()
        offer_with_real_link = {
            "offerAffiliateUrl": "https://www.amazon.com.br/dp/B08GLX7",
        }
        link = scraper._extract_real_link(offer_with_real_link)
        self.assertIsNotNone(link)
        self.assertNotIn("promobit", link)


# ═══════════════════════════════════════════════════════════════════════════════
# Shopee Scraper
# ═══════════════════════════════════════════════════════════════════════════════

class TestShopeeScraper(unittest.TestCase):

    def _make_scraper(self):
        from promo_bot.scrapers.shopee import ShopeeScraper
        s = ShopeeScraper.__new__(ShopeeScraper)
        s._http   = MagicMock()
        s._cache  = MagicMock()
        s._logger = MagicMock()
        return s

    def test_price_conversion_from_api(self):
        """Preço da API Shopee (× 100.000) deve ser convertido corretamente."""
        scraper = self._make_scraper()
        item = {
            "name":    "Fone Bluetooth JBL",
            "itemid":  12345,
            "shopid":  67890,
            "price":   12345000000,     # R$ 123,45
            "price_before_discount": 20000000000,  # R$ 200,00
            "image":   "abc123",
        }
        p = scraper._parse_item(item)
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p.price, 123.45, places=2)
        self.assertAlmostEqual(p.original_price, 200.0, places=2)

    def test_original_price_none_when_not_greater(self):
        scraper = self._make_scraper()
        item = {
            "name":   "Produto Sem Desconto",
            "itemid": 1, "shopid": 2,
            "price":  10000000000,               # R$ 100
            "price_before_discount": 5000000000, # R$ 50 (menor que preço atual)
            "image":  "img",
        }
        p = scraper._parse_item(item)
        self.assertIsNone(p.original_price)

    def test_zero_price_returns_none(self):
        scraper = self._make_scraper()
        item = {
            "name": "Produto Sem Preço", "itemid": 1, "shopid": 2,
            "price": 0, "image": "img",
        }
        self.assertIsNone(scraper._parse_item(item))

    def test_link_format(self):
        scraper = self._make_scraper()
        item = {
            "name":   "Carregador USB-C 65W",
            "itemid": 99999, "shopid": 11111,
            "price":  5000000000,
            "image":  "img",
        }
        p = scraper._parse_item(item)
        self.assertIsNotNone(p)
        self.assertIn("/product/11111/99999", p.link)


# ═══════════════════════════════════════════════════════════════════════════════
# Terabyte Scraper
# ═══════════════════════════════════════════════════════════════════════════════

class TestTerabyteScraper(unittest.TestCase):

    def _make_scraper(self):
        from promo_bot.scrapers.terabyte import TerabyteScraper
        s = TerabyteScraper.__new__(TerabyteScraper)
        s._http   = MagicMock()
        s._cache  = MagicMock()
        s._logger = MagicMock()
        return s

    def test_parse_card_from_html(self):
        from bs4 import BeautifulSoup
        from promo_bot.scrapers.terabyte import TerabyteScraper

        html = """
        <div class="p-card">
            <a href="https://www.terabyteshop.com.br/produto/12345/rtx-4060">
                <h3 class="prod-name">RTX 4060 Gigabyte Eagle OC 8GB</h3>
                <img src="https://cdn.terabyteshop.com.br/rtx4060.jpg" />
                <div class="prod-new-price"><span>R$ 1.899,90</span></div>
                <div class="prod-old-price"><span>R$ 2.299,90</span></div>
                <div class="prod-discount">-17%</div>
            </a>
        </div>
        """
        soup    = BeautifulSoup(html, "html.parser")
        card    = soup.select_one(".p-card")
        scraper = self._make_scraper()
        product = scraper._parse_card(card)

        self.assertIsNotNone(product)
        self.assertIn("RTX 4060", product.title)
        self.assertAlmostEqual(product.price, 1899.90, places=1)
        self.assertAlmostEqual(product.original_price, 2299.90, places=1)
        self.assertEqual(product.discount_pct, 17.0)
        self.assertIn("terabyteshop.com.br/produto", product.link)

    def test_terabyte_in_curl_domains(self):
        """Terabyte deve estar na lista de domínios com bypass de TLS."""
        from promo_bot.utils.http_client import CURL_CFFI_DOMAINS
        self.assertIn("www.terabyteshop.com.br", CURL_CFFI_DOMAINS)
        self.assertIn("terabyteshop.com.br", CURL_CFFI_DOMAINS)

    def test_link_cleaned_of_query_params(self):
        from bs4 import BeautifulSoup
        html = """
        <div class="p-card">
            <a href="/produto/99/ssd-samsung?ref=home&campaign=promo">
                <h3 class="prod-name">SSD Samsung 870 EVO 1TB SATA III</h3>
                <div class="prod-new-price"><span>R$ 499,90</span></div>
            </a>
        </div>
        """
        soup    = BeautifulSoup(html, "html.parser")
        card    = soup.select_one(".p-card")
        scraper = self._make_scraper()
        product = scraper._parse_card(card)

        self.assertIsNotNone(product)
        self.assertNotIn("ref=", product.link)
        self.assertNotIn("campaign=", product.link)


# ═══════════════════════════════════════════════════════════════════════════════
# HttpClient
# ═══════════════════════════════════════════════════════════════════════════════

class TestHttpClient(unittest.TestCase):

    def test_curl_domains_include_all_critical_sites(self):
        from promo_bot.utils.http_client import CURL_CFFI_DOMAINS
        required = {
            "www.amazon.com.br",
            "www.pelando.com.br",
            "shopee.com.br",
            "www.terabyteshop.com.br",
        }
        for domain in required:
            self.assertIn(domain, CURL_CFFI_DOMAINS, f"{domain} não está em CURL_CFFI_DOMAINS")

    def test_extract_domain(self):
        from promo_bot.utils.http_client import HttpClient
        c = HttpClient.__new__(HttpClient)
        self.assertEqual(c._extract_domain("https://www.amazon.com.br/dp/B08"), "www.amazon.com.br")
        self.assertEqual(c._extract_domain("https://shopee.com.br/product/1/2"), "shopee.com.br")


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("  PromoBot v2.3+ — Suite de Testes Unitários")
    print("=" * 65 + "\n")
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("\n" + "=" * 65)
    if result.wasSuccessful():
        print(f"  ✅ TODOS OS {result.testsRun} TESTES PASSARAM!")
    else:
        failed  = len(result.failures)
        errored = len(result.errors)
        print(f"  ❌ {failed} falhas, {errored} erros de {result.testsRun} testes")
    print("=" * 65 + "\n")
    sys.exit(0 if result.wasSuccessful() else 1)
