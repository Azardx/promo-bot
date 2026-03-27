"""
Registro central de scrapers do PromoBot.

Facilita a adição de novos scrapers — basta criar o módulo e
registrá-lo no dicionário SCRAPER_REGISTRY.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promo_bot.scrapers.base import BaseScraper
    from promo_bot.utils.cache import TTLCache
    from promo_bot.utils.http_client import HttpClient

# Registro de scrapers disponíveis: nome -> classe
SCRAPER_REGISTRY: dict[str, type] = {}


def _register_scrapers() -> None:
    """Registra todos os scrapers disponíveis."""
    from promo_bot.scrapers.shopee import ShopeeScraper
    from promo_bot.scrapers.aliexpress import AliExpressScraper
    from promo_bot.scrapers.amazon import AmazonScraper
    from promo_bot.scrapers.pelando import PelandoScraper
    from promo_bot.scrapers.promobit import PromobitScraper
    from promo_bot.scrapers.kabum import KabumScraper
    from promo_bot.scrapers.terabyte import TerabyteScraper
    from promo_bot.scrapers.mercadolivre import MercadoLivreScraper

    SCRAPER_REGISTRY.update({
        "shopee": ShopeeScraper,
        "aliexpress": AliExpressScraper,
        "amazon": AmazonScraper,
        "pelando": PelandoScraper,
        "promobit": PromobitScraper,
        "kabum": KabumScraper,
        "terabyte": TerabyteScraper,
        "mercadolivre": MercadoLivreScraper,
    })


def get_enabled_scrapers(
    enabled_names: list[str],
    http_client: "HttpClient",
    cache: "TTLCache",
) -> list["BaseScraper"]:
    """
    Retorna instâncias dos scrapers habilitados.

    Args:
        enabled_names: Lista de nomes de scrapers habilitados.
        http_client: Cliente HTTP compartilhado.
        cache: Cache compartilhado.

    Returns:
        Lista de instâncias de scrapers prontas para uso.
    """
    if not SCRAPER_REGISTRY:
        _register_scrapers()

    scrapers = []
    for name in enabled_names:
        name_lower = name.strip().lower()
        if name_lower in SCRAPER_REGISTRY:
            scrapers.append(SCRAPER_REGISTRY[name_lower](http_client, cache))
        else:
            from promo_bot.utils.logger import get_logger
            get_logger("scrapers").warning(
                f"Scraper '{name}' nao encontrado no registro. "
                f"Disponiveis: {list(SCRAPER_REGISTRY.keys())}"
            )

    return scrapers
