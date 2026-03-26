"""
Teste funcional das alterações implementadas:
1. Formatter com loja de origem real
2. Pelando scraper com extração RSC
3. Promobit scraper com link real
4. Telegram service com envio de fotos
"""

import sys
sys.path.insert(0, ".")

from promo_bot.database.models import Product, Store
from promo_bot.services.formatter import MessageFormatter


def test_formatter_origin_store():
    """Testa se o formatter exibe a loja de origem real."""
    fmt = MessageFormatter()

    # Produto do Pelando com loja de origem (Amazon)
    p1 = Product(
        title="Mouse Gamer Sem Fio Logitech G PRO X2 SUPERSTRIKE",
        link="https://www.amazon.com.br/dp/B0CCBGXKLP",
        store=Store.PELANDO,
        price=1199.0,
        image_url="https://media.pelando.com.br/test/image.jpg",
        coupon_code=None,
        extra={
            "origin_store": "Amazon",
            "source": "pelando_rsc",
            "pelando_url": "https://www.pelando.com.br/d/mouse-gamer-xxx",
        },
    )

    msg1 = fmt.format_product(p1)
    print("=" * 60)
    print("TESTE 1: Pelando -> Amazon (link real)")
    print("=" * 60)
    print(msg1)
    assert "Amazon" in msg1, "Deve mostrar 'Amazon' como loja"
    assert "📦" in msg1, "Deve usar emoji da Amazon"
    assert "Pelando" not in msg1, "NAO deve mostrar 'Pelando'"
    print("\n✅ PASSOU!\n")

    # Produto do Pelando com cupom
    p2 = Product(
        title="Até 70% OFF em Ofertas Selecionadas",
        link="https://www.paguemenos.com.br/ofertas",
        store=Store.PELANDO,
        price=None,
        discount_pct=70.0,
        coupon_code="DESCONTO70",
        extra={
            "origin_store": "Pague Menos",
            "source": "pelando_rsc",
        },
    )

    msg2 = fmt.format_product(p2)
    print("=" * 60)
    print("TESTE 2: Pelando -> Pague Menos (cupom)")
    print("=" * 60)
    print(msg2)
    assert "Pague Menos" in msg2, "Deve mostrar 'Pague Menos'"
    assert "DESCONTO70" in msg2, "Deve mostrar o cupom"
    assert "CUPOM DE DESCONTO" in msg2, "Deve ser formatado como cupom"
    print("\n✅ PASSOU!\n")

    # Produto do Promobit com loja de origem (Mercado Livre)
    p3 = Product(
        title="Notebook Lenovo IdeaPad 3 Ryzen 5",
        link="https://www.mercadolivre.com.br/notebook-lenovo/p/123",
        store=Store.PROMOBIT,
        price=2899.0,
        original_price=3499.0,
        image_url="https://www.promobit.com.br/images/notebook.jpg",
        extra={
            "origin_store": "Mercado Livre",
            "promobit_url": "https://www.promobit.com.br/d/notebook-lenovo",
        },
    )

    msg3 = fmt.format_product(p3)
    print("=" * 60)
    print("TESTE 3: Promobit -> Mercado Livre (desconto)")
    print("=" * 60)
    print(msg3)
    assert "Mercado Livre" in msg3, "Deve mostrar 'Mercado Livre'"
    assert "🛒" in msg3, "Deve usar emoji do ML"
    assert "Promobit" not in msg3, "NAO deve mostrar 'Promobit'"
    assert "OFERTA IMPERDÍVEL" in msg3, "Deve ser formatado como desconto"
    print("\n✅ PASSOU!\n")

    # Produto direto do KaBuM (sem origin_store, deve manter normal)
    p4 = Product(
        title="SSD Kingston NV2 1TB NVMe M.2",
        link="https://www.kabum.com.br/produto/ssd-kingston",
        store=Store.KABUM,
        price=399.0,
        original_price=549.0,
        image_url="https://images.kabum.com.br/ssd.jpg",
        coupon_code="SHARK15",
    )

    msg4 = fmt.format_product(p4)
    print("=" * 60)
    print("TESTE 4: KaBuM direto (com cupom)")
    print("=" * 60)
    print(msg4)
    assert "KaBuM!" in msg4, "Deve mostrar 'KaBuM!'"
    assert "🟢" in msg4, "Deve usar emoji do KaBuM"
    assert "SHARK15" in msg4, "Deve mostrar o cupom"
    print("\n✅ PASSOU!\n")

    # Produto simples sem preço (Pelando -> C&A)
    p5 = Product(
        title="Bermuda Reta de Sarja - verde militar",
        link="https://www.cea.com.br/bermuda-reta-de-sarja-verde-militar/p",
        store=Store.PELANDO,
        price=58.49,
        extra={
            "origin_store": "C&A",
            "source": "pelando_rsc",
        },
    )

    msg5 = fmt.format_product(p5)
    print("=" * 60)
    print("TESTE 5: Pelando -> C&A (preço simples)")
    print("=" * 60)
    print(msg5)
    assert "C&amp;A" in msg5 or "C&A" in msg5, "Deve mostrar 'C&A'"
    assert "🏪" in msg5, "Deve usar emoji genérico de loja"
    assert "R$ 58,49" in msg5, "Deve mostrar o preço"
    print("\n✅ PASSOU!\n")


def test_pelando_rsc_parser():
    """Testa o parser RSC do Pelando."""
    from promo_bot.scrapers.pelando import PelandoScraper

    # Simula um segmento RSC
    scraper = PelandoScraper.__new__(PelandoScraper)

    segment = '''
    "id":[0,"af4a43c9-aa37-4e12-921f-cf1ae5c79f6e"],"slug":[0,"bermuda-reta-de-sarja-verde-militar-1d1a"],"title":[0,"Bermuda Reta de Sarja - verde militar"],"temperature":[0,31],"commentCount":[0,0],"date":[0,"11 min"],"price":[0,58.49],"discountPercentage":[0,null],"discountFixed":[0,null],"freeShipping":[0,null],"status":[0,"active"],"store":[0,{"id":[0,"445"],"name":[0,"C&A"],"slug":[0,"cea"]}],"imageUrl":[0,"https://media.pelando.com.br/test/image.jpg"],"kind":[0,"promotion"],"sourceUrl":[0,"https://www.cea.com.br/bermuda-reta-de-sarja-verde-militar-9975438-chumbo_c2/p"]
    '''

    deal = scraper._extract_rsc_fields(segment, "af4a43c9-aa37-4e12-921f-cf1ae5c79f6e")

    print("=" * 60)
    print("TESTE 6: Parser RSC do Pelando")
    print("=" * 60)
    print(f"  ID: {deal.get('id')}")
    print(f"  Titulo: {deal.get('title')}")
    print(f"  Preco: {deal.get('price')}")
    print(f"  Loja: {deal.get('store_name')}")
    print(f"  Imagem: {deal.get('imageUrl')}")
    print(f"  sourceUrl: {deal.get('sourceUrl')}")
    print(f"  Kind: {deal.get('kind')}")

    assert deal["title"] == "Bermuda Reta de Sarja - verde militar"
    assert deal["price"] == 58.49
    assert deal["store_name"] == "C&A"
    assert "cea.com.br" in deal["sourceUrl"]
    assert deal["imageUrl"].startswith("https://media.pelando.com.br")
    assert deal["kind"] == "promotion"
    print("\n✅ PASSOU!\n")


def test_telegram_service_structure():
    """Testa a estrutura do TelegramService (sem bot real)."""
    from promo_bot.services.telegram import TelegramService

    service = TelegramService()

    # Verifica que tem os métodos necessários
    assert hasattr(service, "send_promo"), "Deve ter send_promo"
    assert hasattr(service, "_send_with_photo"), "Deve ter _send_with_photo"
    assert hasattr(service, "_send_text_only"), "Deve ter _send_text_only"
    assert hasattr(service, "_is_valid_image_url"), "Deve ter _is_valid_image_url"
    assert hasattr(service, "_truncate_for_caption"), "Deve ter _truncate_for_caption"

    # Testa validação de URL de imagem
    assert service._is_valid_image_url("https://media.pelando.com.br/test.jpg") is True
    assert service._is_valid_image_url("https://images.kabum.com.br/prod.png") is True
    assert service._is_valid_image_url("") is False
    assert service._is_valid_image_url("not-a-url") is False
    assert service._is_valid_image_url("https://example.com/placeholder.jpg") is False
    assert service._is_valid_image_url("data:image/png;base64,xxx") is False

    # Testa truncamento de caption
    short_text = "Texto curto"
    assert service._truncate_for_caption(short_text) == short_text

    long_text = "A" * 2000
    truncated = service._truncate_for_caption(long_text)
    assert len(truncated) <= 1024, f"Caption deve ter max 1024 chars, tem {len(truncated)}"

    print("=" * 60)
    print("TESTE 7: Estrutura do TelegramService")
    print("=" * 60)
    print("  send_promo: OK")
    print("  _send_with_photo: OK")
    print("  _is_valid_image_url: OK")
    print("  _truncate_for_caption: OK")
    print("\n✅ PASSOU!\n")


def test_clean_source_url():
    """Testa a limpeza de URLs de origem."""
    from promo_bot.scrapers.pelando import PelandoScraper

    scraper = PelandoScraper.__new__(PelandoScraper)

    # URL com tracking params
    dirty = "https://www.amazon.com.br/dp/B0CCBGXKLP?ref=cm_sw_r_cso&utm_source=pelando&social_share=abc"
    clean = scraper._clean_source_url(dirty)
    assert "utm_source" not in clean
    assert "social_share" not in clean
    assert "B0CCBGXKLP" in clean
    print("=" * 60)
    print("TESTE 8: Limpeza de URLs de origem")
    print("=" * 60)
    print(f"  Dirty: {dirty}")
    print(f"  Clean: {clean}")
    print("\n✅ PASSOU!\n")


if __name__ == "__main__":
    print("\n🧪 EXECUTANDO TESTES DAS ALTERAÇÕES\n")

    test_formatter_origin_store()
    test_pelando_rsc_parser()
    test_telegram_service_structure()
    test_clean_source_url()

    print("=" * 60)
    print("🎉 TODOS OS TESTES PASSARAM!")
    print("=" * 60)
