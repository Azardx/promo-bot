"""
Modelos de dados do PromoBot.

Define as estruturas de dados utilizadas em todo o sistema,
garantindo tipagem forte e validação consistente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Store(str, Enum):
    """Lojas suportadas pelo sistema."""
    SHOPEE = "shopee"
    ALIEXPRESS = "aliexpress"
    AMAZON = "amazon"
    KABUM = "kabum"
    PELANDO = "pelando"
    PROMOBIT = "promobit"
    TERABYTE = "terabyte"
    MERCADOLIVRE = "mercadolivre"
    UNKNOWN = "unknown"


@dataclass
class Product:
    """
    Representa um produto/promoção coletado de uma loja.

    Estrutura unificada que todos os scrapers devem retornar,
    garantindo consistência no pipeline de processamento.
    """
    title: str
    link: str
    store: Store
    price: Optional[float] = None
    original_price: Optional[float] = None
    discount_pct: Optional[float] = None
    category: str = ""
    image_url: str = ""
    coupon_code: Optional[str] = None
    free_shipping: bool = False
    score: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def has_discount(self) -> bool:
        """Verifica se o produto tem desconto calculável."""
        return (
            self.original_price is not None
            and self.price is not None
            and self.original_price > self.price > 0
        )

    @property
    def calculated_discount(self) -> Optional[float]:
        """Calcula o percentual de desconto."""
        if self.has_discount:
            return round(
                ((self.original_price - self.price) / self.original_price) * 100, 1
            )
        return self.discount_pct

    @property
    def savings(self) -> Optional[float]:
        """Calcula a economia em reais."""
        if self.has_discount:
            return round(self.original_price - self.price, 2)
        return None

    def __hash__(self) -> int:
        return hash(self.link)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Product):
            return False
        return self.link == other.link

    def __repr__(self) -> str:
        price_str = f"R${self.price:.2f}" if self.price else "N/A"
        return f"Product(title='{self.title[:50]}...', price={price_str}, store={self.store.value})"


@dataclass
class ScraperResult:
    """Resultado de uma execução de scraper."""
    scraper_name: str
    products: list[Product] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_secs: float = 0.0
    success: bool = True

    @property
    def count(self) -> int:
        return len(self.products)

    @property
    def error_count(self) -> int:
        return len(self.errors)
