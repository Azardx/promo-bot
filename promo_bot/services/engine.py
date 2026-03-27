"""
Motor de promoções (Promo Engine) do PromoBot.

Orquestra todo o pipeline de coleta, filtragem, deduplicação,
scoring e envio de promoções. É o cérebro do sistema.

CORREÇÕES v2.1:
- Timeout individual por scraper para evitar travamento
- Coleta concorrente com proteção contra scrapers lentos
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from promo_bot.config import settings
from promo_bot.database.db import Database
from promo_bot.database.models import Product, ScraperResult
from promo_bot.scrapers import get_enabled_scrapers
from promo_bot.scrapers.base import BaseScraper
from promo_bot.services.deduplicator import Deduplicator
from promo_bot.services.filter import ProductFilter
from promo_bot.services.scorer import PromoScorer
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient
from promo_bot.utils.logger import get_logger

logger = get_logger("engine")

# Timeout máximo por scraper individual (segundos)
SCRAPER_TIMEOUT = 45


class PromoEngine:
    """
    Motor principal do PromoBot.

    Coordena a execução dos scrapers, aplica filtros, remove
    duplicatas, calcula scores e retorna as melhores ofertas
    prontas para envio.
    """

    def __init__(
        self,
        database: Database,
        http_client: HttpClient,
        cache: TTLCache,
    ):
        self._db = database
        self._http = http_client
        self._cache = cache

        # Serviços do pipeline
        self._filter = ProductFilter(
            min_price=settings.min_price,
            max_price=settings.max_price,
            blocked_keywords=settings.blocked_keywords,
        )
        self._deduplicator = Deduplicator(cache, database)
        self._scorer = PromoScorer(priority_keywords=settings.priority_keywords)

        # Scrapers
        self._scrapers: list[BaseScraper] = get_enabled_scrapers(
            settings.enabled_scrapers, http_client, cache
        )

        # Métricas
        self._cycle_count = 0
        self._total_collected = 0
        self._total_sent = 0

    async def run_cycle(self) -> list[Product]:
        """
        Executa um ciclo completo de coleta e processamento.

        Returns:
            Lista de produtos únicos, filtrados e priorizados,
            prontos para envio.
        """
        cycle_start = time.time()
        self._cycle_count += 1
        logger.info(f"=== Ciclo #{self._cycle_count} iniciado ===")

        # 1. Coleta de todas as fontes (concorrente com timeout)
        all_products = await self._collect_all()
        self._total_collected += len(all_products)
        logger.info(f"Coleta total: {len(all_products)} produtos brutos")

        if not all_products:
            logger.warning("Nenhum produto coletado neste ciclo")
            return []

        # 2. Filtragem
        filtered = self._filter.filter_products(all_products)
        if not filtered:
            logger.info("Nenhum produto passou nos filtros")
            return []

        # 3. Deduplicação
        unique = await self._deduplicator.deduplicate(filtered)
        if not unique:
            logger.info("Todos os produtos eram duplicatas")
            return []

        # 4. Scoring e priorização
        prioritized = self._scorer.score_and_sort(unique)

        # 5. Limita quantidade por ciclo
        max_per_cycle = settings.max_promos_per_cycle
        final = prioritized[:max_per_cycle]

        cycle_duration = round(time.time() - cycle_start, 2)
        logger.info(
            f"=== Ciclo #{self._cycle_count} concluido em {cycle_duration}s === "
            f"Coletados: {len(all_products)} | Filtrados: {len(filtered)} | "
            f"Unicos: {len(unique)} | Enviando: {len(final)}"
        )

        return final

    async def _collect_all(self) -> list[Product]:
        """Executa todos os scrapers de forma concorrente com timeout individual."""
        all_products: list[Product] = []

        # Cria tasks com timeout individual para cada scraper
        async def _run_scraper_with_timeout(scraper: BaseScraper) -> tuple[BaseScraper, ScraperResult | Exception]:
            try:
                result = await asyncio.wait_for(
                    scraper.run(),
                    timeout=SCRAPER_TIMEOUT,
                )
                return scraper, result
            except asyncio.TimeoutError:
                logger.warning(
                    f"Scraper {scraper.NAME} excedeu timeout de {SCRAPER_TIMEOUT}s"
                )
                return scraper, TimeoutError(f"Timeout após {SCRAPER_TIMEOUT}s")
            except Exception as e:
                return scraper, e

        # Executa todos os scrapers concorrentemente
        tasks = [_run_scraper_with_timeout(s) for s in self._scrapers]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        for scraper, result in results:
            if isinstance(result, Exception):
                logger.error(f"Scraper {scraper.NAME} falhou com excecao: {result}")
                await self._db.log_scraper_run(
                    scraper_name=scraper.NAME,
                    duration_secs=0,
                    errors=1,
                    status="exception" if not isinstance(result, TimeoutError) else "timeout",
                )
                continue

            if isinstance(result, ScraperResult):
                # Log de estatísticas do scraper
                await self._db.log_scraper_run(
                    scraper_name=result.scraper_name,
                    duration_secs=result.duration_secs,
                    items_found=result.count,
                    errors=result.error_count,
                    status="ok" if result.success else "error",
                )

                if result.products:
                    all_products.extend(result.products)
                    logger.info(
                        f"{scraper.NAME}: {result.count} produtos "
                        f"em {result.duration_secs}s"
                    )
                else:
                    logger.debug(f"{scraper.NAME}: nenhum produto encontrado")

                if result.errors:
                    for err in result.errors:
                        logger.warning(f"{scraper.NAME} erro: {err}")

        return all_products

    async def register_sent(self, product: Product) -> None:
        """Registra uma promoção como enviada no banco de dados."""
        await self._db.add_promo(
            link=product.link,
            title=product.title,
            store=product.store.value,
            price=product.price,
            original_price=product.original_price,
            discount_pct=product.calculated_discount,
            category=product.category,
            score=product.score,
        )
        self._total_sent += 1

    @property
    def stats(self) -> dict:
        """Retorna estatísticas do motor."""
        return {
            "cycles": self._cycle_count,
            "total_collected": self._total_collected,
            "total_sent": self._total_sent,
            "scrapers_active": len(self._scrapers),
            "filter_stats": self._filter.stats,
            "dedup_stats": self._deduplicator.stats,
            "cache_stats": self._cache.stats,
            "http_stats": self._http.stats,
        }
