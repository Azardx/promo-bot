"""
Sistema de cache em memória com TTL para o PromoBot.

Implementa um cache LRU com expiração automática para verificação
ultra-rápida de duplicatas e armazenamento temporário de dados.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Optional

from promo_bot.utils.logger import get_logger

logger = get_logger("cache")


class TTLCache:
    """
    Cache em memória com Time-To-Live (TTL) e tamanho máximo.

    Utiliza OrderedDict para manter a ordem de inserção e
    implementar eviction LRU quando o cache atinge o limite.
    """

    def __init__(self, max_size: int = 5000, ttl: int = 3600):
        """
        Args:
            max_size: Número máximo de itens no cache.
            ttl: Tempo de vida em segundos para cada entrada.
        """
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> Optional[Any]:
        """
        Recupera um valor do cache.

        Args:
            key: Chave de busca.

        Returns:
            Valor armazenado ou None se não encontrado/expirado.
        """
        async with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if time.time() - timestamp < self._ttl:
                    # Move para o final (mais recente)
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return value
                else:
                    # Expirado — remove
                    del self._cache[key]

            self._misses += 1
            return None

    async def set(self, key: str, value: Any = True) -> None:
        """
        Armazena um valor no cache.

        Args:
            key: Chave de armazenamento.
            value: Valor a armazenar (padrão: True para uso como set).
        """
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, time.time())

            # Eviction LRU se exceder o tamanho máximo
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    async def exists(self, key: str) -> bool:
        """Verifica se uma chave existe e não está expirada."""
        return await self.get(key) is not None

    async def delete(self, key: str) -> bool:
        """Remove uma chave do cache."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def clear(self) -> None:
        """Limpa todo o cache."""
        async with self._lock:
            self._cache.clear()
            logger.info("Cache limpo completamente")

    async def cleanup_expired(self) -> int:
        """Remove todas as entradas expiradas."""
        async with self._lock:
            now = time.time()
            expired_keys = [
                k for k, (_, ts) in self._cache.items()
                if now - ts >= self._ttl
            ]
            for key in expired_keys:
                del self._cache[key]

            if expired_keys:
                logger.debug(f"Cache: {len(expired_keys)} entradas expiradas removidas")
            return len(expired_keys)

    @property
    def size(self) -> int:
        """Retorna o número de itens no cache."""
        return len(self._cache)

    @property
    def stats(self) -> dict[str, Any]:
        """Retorna estatísticas do cache."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0.0
        return {
            "size": self.size,
            "max_size": self._max_size,
            "ttl": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%",
        }
