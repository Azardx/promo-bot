"""
Serviço de scoring e priorização de promoções.

Calcula uma pontuação para cada oferta com base em múltiplos
critérios, permitindo enviar primeiro as melhores promoções.
"""

from __future__ import annotations

import re
from typing import Optional

from promo_bot.config import settings
from promo_bot.database.models import Product
from promo_bot.utils.logger import get_logger

logger = get_logger("scorer")

# Categorias de alto valor com pesos
HIGH_VALUE_CATEGORIES = {
    # Tecnologia
    "notebook": 8, "laptop": 8, "macbook": 9,
    "iphone": 9, "ipad": 8, "galaxy": 7, "pixel": 7,
    "smartphone": 6, "celular": 5,
    "ssd": 7, "nvme": 7, "hd externo": 5,
    "rtx": 9, "rx 7": 8, "gpu": 8, "placa de vídeo": 8, "placa de video": 8,
    "monitor": 7, "ultrawide": 8,
    "playstation": 8, "ps5": 9, "xbox": 8, "nintendo switch": 8,
    "airpods": 7, "headset": 5, "fone bluetooth": 5,
    "teclado mecânico": 6, "teclado mecanico": 6,
    "mouse gamer": 5, "cadeira gamer": 6,
    "processador": 7, "ryzen": 7, "intel core": 7,
    "memória ram": 6, "memoria ram": 6,
    "placa mãe": 6, "placa mae": 6,
    "fonte 80 plus": 5,
    "roteador": 5, "mesh": 6,
    "tablet": 6, "kindle": 6,
    "smart tv": 7, "oled": 8, "4k": 6,
    "câmera": 6, "camera": 6, "gopro": 7, "drone": 7,
    # Eletrodomésticos premium
    "air fryer": 5, "airfryer": 5,
    "robô aspirador": 7, "robo aspirador": 7,
    "cafeteira": 4, "nespresso": 5,
    "aspirador": 4,
}

# Lojas com bônus de confiabilidade
STORE_BONUS = {
    "amazon": 2,
    "kabum": 2,
    "pelando": 3,  # Já validado pela comunidade
    "promobit": 3,
}


class PromoScorer:
    """
    Sistema de scoring para priorização de ofertas.

    Calcula uma pontuação de 0 a 100 com base em:
    - Percentual de desconto
    - Categoria do produto
    - Confiabilidade da loja
    - Palavras-chave prioritárias
    - Presença de cupom
    - Frete grátis
    """

    def __init__(self, priority_keywords: Optional[list[str]] = None):
        self._priority_keywords = priority_keywords or settings.priority_keywords

    def score_and_sort(self, products: list[Product]) -> list[Product]:
        """
        Calcula score para cada produto e ordena por prioridade.

        Args:
            products: Lista de produtos para pontuar.

        Returns:
            Lista ordenada do maior para o menor score.
        """
        for product in products:
            product.score = self._calculate_score(product)

        # Ordena por score decrescente
        sorted_products = sorted(products, key=lambda p: p.score, reverse=True)

        if sorted_products:
            logger.info(
                f"Scoring: top={sorted_products[0].score:.1f} "
                f"({sorted_products[0].title[:40]}), "
                f"min={sorted_products[-1].score:.1f}, "
                f"media={sum(p.score for p in sorted_products) / len(sorted_products):.1f}"
            )

        return sorted_products

    def _calculate_score(self, product: Product) -> float:
        """Calcula a pontuação total de um produto."""
        score = 0.0

        # 1. Desconto (0-30 pontos)
        score += self._score_discount(product)

        # 2. Categoria/produto (0-25 pontos)
        score += self._score_category(product)

        # 3. Palavras-chave prioritárias (0-15 pontos)
        score += self._score_keywords(product)

        # 4. Bônus da loja (0-10 pontos)
        score += self._score_store(product)

        # 5. Extras: cupom, frete grátis (0-10 pontos)
        score += self._score_extras(product)

        # 6. Qualidade do título (0-10 pontos)
        score += self._score_title_quality(product)

        # Normaliza para 0-100
        return min(100.0, max(0.0, score))

    def _score_discount(self, product: Product) -> float:
        """Pontua baseado no percentual de desconto."""
        discount = product.calculated_discount
        if discount is None:
            return 5.0  # Pontuação neutra se não tem desconto calculável

        if discount >= 70:
            return 30.0
        elif discount >= 50:
            return 25.0
        elif discount >= 40:
            return 20.0
        elif discount >= 30:
            return 15.0
        elif discount >= 20:
            return 10.0
        elif discount >= 10:
            return 7.0
        else:
            return 3.0

    def _score_category(self, product: Product) -> float:
        """Pontua baseado na categoria do produto."""
        title_lower = product.title.lower()
        max_score = 0.0

        for keyword, weight in HIGH_VALUE_CATEGORIES.items():
            if keyword in title_lower:
                # Normaliza peso (1-9) para escala (3-25)
                category_score = 3 + (weight / 9) * 22
                max_score = max(max_score, category_score)

        return max_score

    def _score_keywords(self, product: Product) -> float:
        """Pontua baseado em palavras-chave prioritárias do usuário."""
        title_lower = product.title.lower()
        matches = sum(
            1 for kw in self._priority_keywords
            if kw.lower() in title_lower
        )

        if matches >= 3:
            return 15.0
        elif matches == 2:
            return 10.0
        elif matches == 1:
            return 5.0
        return 0.0

    def _score_store(self, product: Product) -> float:
        """Pontua baseado na confiabilidade da loja."""
        store_name = product.store.value.lower()
        bonus = STORE_BONUS.get(store_name, 0)
        return float(bonus) * 2  # Escala para 0-10

    def _score_extras(self, product: Product) -> float:
        """Pontua extras como cupom e frete grátis."""
        score = 0.0
        if product.coupon_code:
            score += 5.0
        if product.free_shipping:
            score += 3.0
        if product.image_url:
            score += 2.0
        return min(10.0, score)

    def _score_title_quality(self, product: Product) -> float:
        """Pontua a qualidade do título."""
        title = product.title
        score = 0.0

        # Título com comprimento adequado
        if 30 <= len(title) <= 150:
            score += 3.0
        elif 20 <= len(title) <= 200:
            score += 1.0

        # Tem informações de preço
        if product.price is not None:
            score += 3.0

        # Tem desconto calculável
        if product.calculated_discount is not None:
            score += 2.0

        # Tem preço original (permite mostrar economia)
        if product.original_price is not None:
            score += 2.0

        return min(10.0, score)
