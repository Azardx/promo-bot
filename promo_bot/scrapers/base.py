"""
Classe base abstrata para todos os scrapers do PromoBot.

Define a interface comum, lógica de retry, métricas e integração
com o cliente HTTP e o sistema de cache.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Optional

from promo_bot.database.models import Product, ScraperResult, Store
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient
from promo_bot.utils.logger import get_logger


class BaseScraper(ABC):
    """
    Classe base para todos os scrapers de promoções.

    Cada scraper deve implementar o método `_scrape()` que retorna
    uma lista de Products. A classe base cuida de métricas, logging,
    tratamento de erros e integração com o HttpClient.
    """

    # Subclasses devem definir estes atributos
    STORE: Store = Store.UNKNOWN
    NAME: str = "base"
    BASE_URL: str = ""

    def __init__(self, http_client: HttpClient, cache: TTLCache):
        self._http = http_client
        self._cache = cache
        self._logger = get_logger(f"scraper.{self.NAME}")
        self._run_count = 0
        self._total_items = 0
        self._total_errors = 0

    @abstractmethod
    async def _scrape(self) -> list[Product]:
        """
        Método principal de scraping. Deve ser implementado por cada scraper.

        Returns:
            Lista de Products encontrados.
        """
        ...

    async def run(self) -> ScraperResult:
        """
        Executa o scraper com métricas e tratamento de erros.

        Returns:
            ScraperResult com os produtos encontrados e metadados.
        """
        result = ScraperResult(scraper_name=self.NAME)
        start_time = time.time()

        try:
            self._logger.info(f"Iniciando coleta em {self.NAME}...")
            products = await self._scrape()

            # Filtra produtos inválidos
            valid_products = []
            for product in products:
                if self._validate_product(product):
                    valid_products.append(product)

            result.products = valid_products
            result.success = True
            self._total_items += len(valid_products)
            self._logger.info(
                f"{self.NAME}: {len(valid_products)} ofertas validas "
                f"(de {len(products)} coletadas)"
            )

        except Exception as e:
            result.success = False
            result.errors.append(str(e))
            self._total_errors += 1
            self._logger.error(f"Erro critico no scraper {self.NAME}: {e}", exc_info=True)

        result.duration_secs = round(time.time() - start_time, 2)
        self._run_count += 1

        return result

    def _validate_product(self, product: Product) -> bool:
        """Validação básica de um produto."""
        if not product.title or len(product.title.strip()) < 10:
            return False
        if not product.link or not product.link.startswith("http"):
            return False
        if product.price is not None and product.price <= 0:
            return False
        return True

    def _clean_title(self, title: str) -> str:
        """Limpa o título removendo lixo comum."""
        import re

        # Remove espaços extras
        title = re.sub(r"\s+", " ", title).strip()

        # Remove nomes de lojas que aparecem no título
        store_names = [
            "Mercado Livre", "KaBuM!", "Amazon", "Terabyte", "Pichau",
            "Magazine Luiza", "Shopee", "AliExpress", "Magalu",
        ]
        for name in store_names:
            title = title.replace(name, "").strip()

        # Remove padrões de tempo (ex: "5h 30min")
        title = re.sub(r"\d+\s*(min|h|d|dias?|horas?)\s*\d*\s*(min|h|d)?", "", title)

        # Remove pipes e traços no final
        title = re.sub(r"[\s|–-]+$", "", title)

        return title.strip()

    def _parse_price(self, price_str: str) -> Optional[float]:
        """Converte string de preço para float."""
        import re

        if not price_str:
            return None

        try:
            # Remove "R$", espaços e pontos de milhar
            cleaned = price_str.replace("R$", "").replace("r$", "").strip()
            cleaned = re.sub(r"[^\d,.]", "", cleaned)

            # Trata formato brasileiro: 1.299,90
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")

            return float(cleaned)
        except (ValueError, TypeError):
            return None

    @property
    def stats(self) -> dict:
        """Retorna estatísticas do scraper."""
        return {
            "name": self.NAME,
            "runs": self._run_count,
            "total_items": self._total_items,
            "total_errors": self._total_errors,
        }
