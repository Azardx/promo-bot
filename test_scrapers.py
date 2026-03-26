"""
Script de teste para validar todos os scrapers do PromoBot.
Executa cada scraper individualmente e mostra os resultados.
"""

import asyncio
import sys
import os

# Adiciona o diretório do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from promo_bot.utils.http_client import HttpClient
from promo_bot.utils.cache import TTLCache
from promo_bot.scrapers.kabum import KabumScraper
from promo_bot.scrapers.promobit import PromobitScraper
from promo_bot.scrapers.pelando import PelandoScraper
from promo_bot.scrapers.amazon import AmazonScraper
from promo_bot.scrapers.shopee import ShopeeScraper
from promo_bot.scrapers.aliexpress import AliExpressScraper

import logging
logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')


async def test_scraper(name: str, scraper_class, http_client, cache):
    """Testa um scraper individual."""
    print(f"\n{'='*60}")
    print(f"  Testando: {name}")
    print(f"{'='*60}")

    try:
        scraper = scraper_class(http_client, cache)
        result = await scraper.run()

        if result.success and result.products:
            print(f"  ✅ SUCESSO: {len(result.products)} ofertas encontradas em {result.duration_secs}s")
            # Mostra as primeiras 3 ofertas
            for i, p in enumerate(result.products[:3]):
                price_str = f"R${p.price:.2f}" if p.price else "N/A"
                discount_str = f" ({p.discount_pct:.0f}% OFF)" if p.discount_pct else ""
                print(f"     {i+1}. {p.title[:70]}...")
                print(f"        Preço: {price_str}{discount_str}")
                print(f"        Link: {p.link[:80]}")
            if len(result.products) > 3:
                print(f"     ... e mais {len(result.products) - 3} ofertas")
        elif result.success:
            print(f"  ⚠️ OK mas sem ofertas (0 produtos) em {result.duration_secs}s")
        else:
            print(f"  ❌ FALHA: {result.errors}")

        return len(result.products) if result.success else 0

    except Exception as e:
        print(f"  ❌ EXCEÇÃO: {e}")
        import traceback
        traceback.print_exc()
        return 0


async def main():
    print("\n" + "="*60)
    print("  PromoBot - Teste de Scrapers")
    print("="*60)

    http_client = HttpClient(timeout=20, max_retries=2, rate_limit_delay=1.0)
    cache = TTLCache(max_size=1000, ttl=3600)

    scrapers = [
        ("KaBuM!", KabumScraper),
        ("Promobit", PromobitScraper),
        ("Pelando", PelandoScraper),
        ("Amazon", AmazonScraper),
        ("Shopee", ShopeeScraper),
        ("AliExpress", AliExpressScraper),
    ]

    total = 0
    results = {}

    for name, scraper_class in scrapers:
        count = await test_scraper(name, scraper_class, http_client, cache)
        results[name] = count
        total += count

    print(f"\n{'='*60}")
    print(f"  RESUMO FINAL")
    print(f"{'='*60}")
    for name, count in results.items():
        status = "✅" if count > 0 else "❌"
        print(f"  {status} {name}: {count} ofertas")
    print(f"\n  Total: {total} ofertas coletadas")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
