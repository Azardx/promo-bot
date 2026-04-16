"""
Serviço de scoring e priorização de promoções profissional.

Calcula uma pontuação para cada oferta com base em múltiplos
critérios, priorizando produtos de alto valor e descontos reais.

CORREÇÕES v2.3:
- Scoring agressivo para eletrônicos e games
- Filtro de produtos baratos e irrelevantes
- Bônus para cupons e frete grátis
- Detecção de spam e títulos ruins
"""

from __future__ import annotations

import re
from typing import Optional

from promo_bot.config import settings
from promo_bot.database.models import Product
from promo_bot.utils.logger import get_logger

logger = get_logger("scorer")

# Categorias de alto valor com pesos (1-10)
HIGH_VALUE_CATEGORIES = {
    # Tecnologia & Hardware
    "notebook": 10, "laptop": 10, "macbook": 10, "rtx": 10, "gpu": 10,
    "iphone": 10, "galaxy s": 9, "smartphone": 8, "ssd": 8, "nvme": 8,
    "processador": 9, "ryzen": 9, "intel core": 9, "placa de vídeo": 10,
    "monitor": 8, "smart tv": 9, "oled": 10, "qled": 9,
    # Games
    "playstation": 10, "ps5": 10, "xbox": 10, "nintendo switch": 10,
    "console": 9, "controle": 7, "headset": 6,
    # Eletrodomésticos Premium
    "air fryer": 7, "airfryer": 7, "robô aspirador": 8, "robo aspirador": 8,
    "máquina de lavar": 8, "geladeira": 8, "ar condicionado": 8,
}

# Palavras-chave de spam ou produtos irrelevantes (penalidade)
LOW_VALUE_KEYWORDS = [
    "capinha", "película", "adesivo", "cabo usb", "chaveiro", "meia",
    "cueca", "calcinha", "escova de dente", "sabonete", "shampoo",
    "detergente", "esponja", "pano de prato", "caneta", "lápis"
]

class PromoScorer:
    """Sistema de scoring profissional para priorização de ofertas."""

    def __init__(self, priority_keywords: Optional[list[str]] = None):
        self._priority_keywords = priority_keywords or settings.priority_keywords

    def score_and_sort(self, products: list[Product]) -> list[Product]:
        """Calcula score e ordena por prioridade."""
        scored_products = []
        for product in products:
            product.score = self._calculate_score(product)
            # Filtro de qualidade mínima: ignora produtos com score muito baixo
            if product.score >= 15:
                scored_products.append(product)

        # Ordena por score decrescente
        return sorted(scored_products, key=lambda p: p.score, reverse=True)

    def _calculate_score(self, product: Product) -> float:
        """Calcula a pontuação total de um produto (0-100)."""
        score = 0.0

        # 1. Valor do Produto (Base: 0-40 pontos)
        # Prioriza produtos mais caros (eletrônicos vs miudezas)
        if product.price:
            if product.price > 2000: score += 40
            elif product.price > 1000: score += 35
            elif product.price > 500: score += 25
            elif product.price > 100: score += 15
            elif product.price < 10: score -= 20  # Penaliza produtos muito baratos

        # 2. Desconto Real (0-30 pontos)
        discount = product.calculated_discount
        if discount:
            if discount >= 50: score += 30
            elif discount >= 30: score += 20
            elif discount >= 15: score += 10
            elif discount < 5: score -= 10  # Ignora descontos falsos/baixos

        # 3. Categoria e Keywords (0-20 pontos)
        title_lower = product.title.lower()
        for kw, weight in HIGH_VALUE_CATEGORIES.items():
            if kw in title_lower:
                score += weight * 2
                break
        
        for kw in LOW_VALUE_KEYWORDS:
            if kw in title_lower:
                score -= 30
                break

        # 4. Extras (0-10 pontos)
        if product.coupon_code: score += 7
        if product.free_shipping: score += 3

        # 5. Penalidade por Título Ruim/Spam
        if len(product.title) < 15 or len(product.title) > 200:
            score -= 15
        if "???" in product.title or "!!!" in product.title:
            score -= 10

        return min(100.0, max(0.0, score))
