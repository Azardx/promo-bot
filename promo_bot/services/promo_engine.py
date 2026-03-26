# promo_bot/services/promo_engine.py
import asyncio
import logging

from scrapers.amazon import buscar_amazon
from scrapers.aliexpress import buscar_aliexpress
from scrapers.kabum import scrape as buscar_kabum
from scrapers.promobit import scrap_promobit
from scrapers.shopee import scrape as buscar_shopee

from utils.fake_promo_detector import promo_falsa
from utils.price_analyzer import analisar_preco

logger = logging.getLogger("promo_engine")

async def coletar_promos():
    promos = []
    seen = set()

    tarefas = {
        "Kabum": buscar_kabum(),
        "Promobit": scrap_promobit(),
        "Shopee": buscar_shopee(),
        "Amazon": buscar_amazon(),
        "AliExpress": buscar_aliexpress()
    }

    # Executa de forma concorrente
    resultados = await asyncio.gather(*tarefas.values(), return_exceptions=True)

    for (nome_loja, res) in zip(tarefas.keys(), resultados):
        if isinstance(res, Exception):
            logger.error(f"❌ Scraper {nome_loja} falhou: {res}")
            continue
        
        if not res:
            logger.debug(f"⚠️ Scraper {nome_loja} não retornou nenhuma oferta.")
            continue

        for titulo, link in res:
            if not titulo or not link:
                continue

            if promo_falsa(titulo) or not analisar_preco(titulo):
                continue

            if link in seen:
                continue

            seen.add(link)
            promos.append((titulo, link))
            
    return promos