"""
Serviço de deduplicação de promoções.

Combina verificação em cache (rápida) e banco de dados (persistente)
para garantir que nenhuma promoção seja enviada mais de uma vez.
Também detecta produtos similares com links diferentes.
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlparse

from promo_bot.database.db import Database
from promo_bot.database.models import Product
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.logger import get_logger

logger = get_logger("deduplicator")


class Deduplicator:
    """
    Sistema de deduplicação em duas camadas.

    Camada 1 (Cache): Verificação ultra-rápida em memória.
    Camada 2 (DB): Verificação persistente no banco de dados.
    Camada 3 (Similaridade): Detecção de títulos muito parecidos.
    """

    SIMILARITY_THRESHOLD = 0.85  # 85% de similaridade = duplicata

    def __init__(self, cache: TTLCache, database: Database):
        self._cache = cache
        self._db = database
        self._recent_titles: list[str] = []  # Últimos títulos para comparação
        self._max_recent = 200
        self._duplicates_found = 0
        self._unique_found = 0

    async def deduplicate(self, products: list[Product]) -> list[Product]:
        """
        Remove duplicatas de uma lista de produtos.

        Args:
            products: Lista de produtos a verificar.

        Returns:
            Lista de produtos únicos.
        """
        unique: list[Product] = []
        seen_in_batch: set[str] = set()

        for product in products:
            # 1. Normaliza o link
            normalized_link = self._normalize_link(product.link)

            # 2. Verifica duplicata no batch atual
            if normalized_link in seen_in_batch:
                self._duplicates_found += 1
                continue

            # 3. Verifica no cache (rápido)
            cache_key = self._make_cache_key(normalized_link)
            if await self._cache.exists(cache_key):
                self._duplicates_found += 1
                logger.debug(f"Duplicata (cache): {product.title[:50]}")
                continue

            # 4. Verifica no banco de dados (persistente)
            if await self._db.promo_exists(normalized_link):
                # Adiciona ao cache para futuras verificações rápidas
                await self._cache.set(cache_key)
                self._duplicates_found += 1
                logger.debug(f"Duplicata (db): {product.title[:50]}")
                continue

            # 5. Verifica similaridade de título
            if self._is_similar_to_recent(product.title):
                self._duplicates_found += 1
                logger.debug(f"Duplicata (similar): {product.title[:50]}")
                continue

            # Produto é único
            seen_in_batch.add(normalized_link)
            await self._cache.set(cache_key)
            self._add_to_recent(product.title)
            unique.append(product)
            self._unique_found += 1

            # Atualiza o link normalizado no produto
            product.link = normalized_link

        logger.info(
            f"Deduplicacao: {len(unique)}/{len(products)} unicos "
            f"({len(products) - len(unique)} duplicatas removidas)"
        )
        return unique

    def _normalize_link(self, link: str) -> str:
        """Normaliza um link para comparação consistente."""
        try:
            parsed = urlparse(link)
            # Remove www. e trailing slash
            netloc = parsed.netloc.replace("www.", "")
            path = parsed.path.rstrip("/")

            # Remove parâmetros de tracking comuns
            clean_path = re.sub(
                r"[?&](utm_\w+|ref|tag|aff_id|clickid|source|campaign|spm|scm|algo_\w+)=[^&]*",
                "",
                path + ("?" + parsed.query if parsed.query else ""),
            )
            clean_path = clean_path.rstrip("?&")

            return f"https://{netloc}{clean_path}"
        except Exception:
            return link

    def _make_cache_key(self, link: str) -> str:
        """Gera uma chave de cache a partir do link."""
        return f"promo:{hashlib.md5(link.encode()).hexdigest()}"

    def _is_similar_to_recent(self, title: str) -> bool:
        """Verifica se o título é muito similar a um recente."""
        normalized = self._normalize_title(title)

        for recent_title in self._recent_titles:
            similarity = SequenceMatcher(None, normalized, recent_title).ratio()
            if similarity >= self.SIMILARITY_THRESHOLD:
                return True

        return False

    def _normalize_title(self, title: str) -> str:
        """Normaliza título para comparação de similaridade."""
        # Converte para minúsculas e remove caracteres especiais
        normalized = re.sub(r"[^\w\s]", "", title.lower())
        # Remove espaços extras
        normalized = re.sub(r"\s+", " ", normalized).strip()
        # Remove preços do título
        normalized = re.sub(r"r?\$?\s*\d+[.,]?\d*", "", normalized).strip()
        return normalized

    def _add_to_recent(self, title: str) -> None:
        """Adiciona título à lista de recentes."""
        normalized = self._normalize_title(title)
        self._recent_titles.append(normalized)

        # Mantém apenas os últimos N títulos
        if len(self._recent_titles) > self._max_recent:
            self._recent_titles = self._recent_titles[-self._max_recent:]

    async def clear_recent(self) -> None:
        """Limpa a lista de títulos recentes."""
        self._recent_titles.clear()

    @property
    def stats(self) -> dict:
        """Retorna estatísticas de deduplicação."""
        total = self._duplicates_found + self._unique_found
        dup_rate = (self._duplicates_found / total * 100) if total > 0 else 0
        return {
            "total_checked": total,
            "unique": self._unique_found,
            "duplicates": self._duplicates_found,
            "duplicate_rate": f"{dup_rate:.1f}%",
            "recent_titles_cached": len(self._recent_titles),
        }
