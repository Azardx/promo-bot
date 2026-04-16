"""
Serviço de filtragem inteligente de promoções.

Aplica múltiplos critérios de filtragem para garantir que apenas
promoções relevantes e de qualidade sejam enviadas aos usuários.

CORREÇÕES v2.3:
- Filtro de preço mínimo agressivo (ignora miudezas < R$ 15)
- Blacklist de keywords expandida
- Filtro de títulos curtos ou genéricos
- Detecção de spam e caracteres especiais excessivos
"""

from __future__ import annotations

import re
from typing import Optional

from promo_bot.config import settings
from promo_bot.database.models import Product
from promo_bot.utils.logger import get_logger

logger = get_logger("filter")

# Padrões que indicam produtos de baixa qualidade ou spam
SPAM_PATTERNS = [
    r"(?i)teste?\s+de\s+produto",
    r"(?i)produto\s+indispon[ií]vel",
    r"(?i)fora\s+de\s+estoque",
    r"(?i)esgotado",
    r"(?i)ganhe dinheiro",
    r"(?i)renda extra",
    r"(?i)trabalhe em casa",
]

# Palavras que indicam produtos genéricos/irrelevantes
LOW_QUALITY_WORDS = [
    "amostra grátis",
    "manual pdf",
    "etiqueta adesiva",
    "capinha",
    "película",
    "adesivo",
    "chaveiro",
    "meia",
    "cueca",
    "calcinha",
]


class ProductFilter:
    """
    Filtro inteligente de promoções.

    Aplica critérios de preço, palavras-chave, qualidade e
    relevância para selecionar apenas as melhores ofertas.
    """

    def __init__(
        self,
        min_price: float = settings.min_price,
        max_price: float = settings.max_price,
        blocked_keywords: Optional[list[str]] = None,
        min_title_length: int = 15,
    ):
        self._min_price = min_price
        self._max_price = max_price
        self._blocked_keywords = blocked_keywords or settings.blocked_keywords
        self._min_title_length = min_title_length
        self._filtered_count = 0
        self._passed_count = 0

    def filter_products(self, products: list[Product]) -> list[Product]:
        """
        Filtra uma lista de produtos.

        Args:
            products: Lista de produtos a filtrar.

        Returns:
            Lista de produtos que passaram em todos os filtros.
        """
        filtered = []
        for product in products:
            reason = self._check_product(product)
            if reason is None:
                filtered.append(product)
                self._passed_count += 1
            else:
                self._filtered_count += 1
                logger.debug(f"Filtrado: [{reason}] {product.title[:60]}")

        logger.info(
            f"Filtro: {len(filtered)}/{len(products)} aprovados "
            f"({len(products) - len(filtered)} removidos)"
        )
        return filtered

    def _check_product(self, product: Product) -> Optional[str]:
        """
        Verifica se um produto passa em todos os filtros.

        Returns:
            None se aprovado, ou string com motivo da rejeição.
        """
        # 1. Título mínimo (v2.3: aumentado para 15)
        if len(product.title.strip()) < self._min_title_length:
            return "titulo_curto"

        # 2. Link válido
        if not product.link or not product.link.startswith("http"):
            return "link_invalido"

        # 3. Filtro de preço (v2.3: min_price R$ 15)
        if product.price is not None:
            if product.price < self._min_price:
                return f"preco_baixo ({product.price:.2f})"
            if product.price > self._max_price:
                return f"preco_alto ({product.price:.2f})"

        # 4. Palavras bloqueadas pelo usuário
        title_lower = product.title.lower()
        for keyword in self._blocked_keywords:
            if keyword.lower() in title_lower:
                return f"palavra_bloqueada ({keyword})"

        # 5. Palavras de baixa qualidade
        for word in LOW_QUALITY_WORDS:
            if word.lower() in title_lower:
                return f"baixa_qualidade ({word})"

        # 6. Padrões de spam
        for pattern in SPAM_PATTERNS:
            if re.search(pattern, product.title):
                return "spam_detectado"

        # 7. Título com muitos caracteres especiais (spam)
        special_chars = sum(1 for c in product.title if not c.isalnum() and c not in " .,/-()[]!@#$%&*+:;'\"")
        if len(product.title) > 0 and special_chars > len(product.title) * 0.3:
            return "excesso_caracteres_especiais"

        # 8. Título duplicado/genérico
        if self._is_generic_title(product.title):
            return "titulo_generico"

        return None

    def _is_generic_title(self, title: str) -> bool:
        """Verifica se o título é genérico demais."""
        generic_patterns = [
            r"^(produto|item|oferta|promoção)\s*\d*$",
            r"^[\d\s.,R$]+$",  # Apenas números e preço
        ]
        for pattern in generic_patterns:
            if re.match(pattern, title.strip(), re.IGNORECASE):
                return True
        return False

    @property
    def stats(self) -> dict:
        """Retorna estatísticas do filtro."""
        total = self._filtered_count + self._passed_count
        pass_rate = (self._passed_count / total * 100) if total > 0 else 0
        return {
            "total_processed": total,
            "passed": self._passed_count,
            "filtered": self._filtered_count,
            "pass_rate": f"{pass_rate:.1f}%",
        }
