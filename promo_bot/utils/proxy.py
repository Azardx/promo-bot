"""
Gerenciador de rotação de proxies do PromoBot.

Suporta carregamento de proxies de arquivo, rotação automática,
verificação de saúde e fallback para conexão direta.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from promo_bot.utils.logger import get_logger

logger = get_logger("proxy")


@dataclass
class ProxyInfo:
    """Informações de um proxy individual."""
    url: str
    failures: int = 0
    last_used: float = 0.0
    is_healthy: bool = True


class ProxyManager:
    """
    Gerenciador de proxies com rotação, health-check e fallback.

    Se nenhum proxy estiver disponível ou configurado, opera em modo
    direto (sem proxy), garantindo que o bot nunca pare de funcionar.
    """

    MAX_FAILURES = 5
    COOLDOWN_SECONDS = 300  # 5 min de cooldown após falhas

    def __init__(self, proxy_file: str = "", enabled: bool = False):
        self._proxies: list[ProxyInfo] = []
        self._lock = asyncio.Lock()
        self._enabled = enabled
        self._proxy_file = proxy_file

        if enabled and proxy_file:
            self._load_proxies(proxy_file)

    def _load_proxies(self, filepath: str) -> None:
        """Carrega proxies de um arquivo texto (um por linha)."""
        path = Path(filepath)
        if not path.exists():
            logger.warning(f"Arquivo de proxies nao encontrado: {filepath}")
            return

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    # Suporta formatos: ip:port, http://ip:port, user:pass@ip:port
                    if not line.startswith(("http://", "https://", "socks5://")):
                        line = f"http://{line}"
                    self._proxies.append(ProxyInfo(url=line))

        logger.info(f"Carregados {len(self._proxies)} proxies de {filepath}")

    @property
    def available_count(self) -> int:
        """Retorna a quantidade de proxies saudáveis."""
        return sum(1 for p in self._proxies if p.is_healthy)

    @property
    def is_enabled(self) -> bool:
        """Verifica se o sistema de proxies está ativo e funcional."""
        return self._enabled and len(self._proxies) > 0

    async def get_proxy(self) -> Optional[str]:
        """
        Retorna o próximo proxy saudável disponível (round-robin com peso).

        Returns:
            URL do proxy ou None se nenhum disponível.
        """
        if not self.is_enabled:
            return None

        async with self._lock:
            now = time.time()
            healthy = [
                p for p in self._proxies
                if p.is_healthy or (now - p.last_used > self.COOLDOWN_SECONDS)
            ]

            if not healthy:
                logger.warning("Nenhum proxy saudavel disponivel, resetando todos")
                for p in self._proxies:
                    p.is_healthy = True
                    p.failures = 0
                healthy = self._proxies

            # Seleciona o proxy menos usado recentemente
            proxy = min(healthy, key=lambda p: p.last_used)
            proxy.last_used = now
            return proxy.url

    async def report_failure(self, proxy_url: str) -> None:
        """Reporta falha em um proxy."""
        async with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.failures += 1
                    if p.failures >= self.MAX_FAILURES:
                        p.is_healthy = False
                        logger.warning(
                            f"Proxy desabilitado por excesso de falhas: {proxy_url}"
                        )
                    break

    async def report_success(self, proxy_url: str) -> None:
        """Reporta sucesso em um proxy."""
        async with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.failures = max(0, p.failures - 1)
                    p.is_healthy = True
                    break

    async def health_check(self) -> dict[str, int]:
        """
        Verifica a saúde de todos os proxies.

        Returns:
            Dicionário com contagem de proxies saudáveis e não saudáveis.
        """
        if not self._proxies:
            return {"healthy": 0, "unhealthy": 0, "total": 0}

        healthy = 0
        unhealthy = 0

        async def _check(proxy: ProxyInfo) -> bool:
            try:
                async with httpx.AsyncClient(
                    proxy=proxy.url, timeout=10.0
                ) as client:
                    resp = await client.get("https://httpbin.org/ip")
                    return resp.status_code == 200
            except Exception:
                return False

        tasks = [_check(p) for p in self._proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for proxy, result in zip(self._proxies, results):
            if isinstance(result, bool) and result:
                proxy.is_healthy = True
                proxy.failures = 0
                healthy += 1
            else:
                proxy.is_healthy = False
                unhealthy += 1

        logger.info(
            f"Health check: {healthy} saudaveis, {unhealthy} com falha"
        )
        return {"healthy": healthy, "unhealthy": unhealthy, "total": len(self._proxies)}
