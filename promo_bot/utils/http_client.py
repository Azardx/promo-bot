"""
Cliente HTTP robusto e assíncrono do PromoBot.

Implementa requisições com rotação de User-Agent, suporte a proxies,
retry com backoff exponencial e controle de rate limit.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

import httpx

from promo_bot.utils.logger import get_logger
from promo_bot.utils.proxy import ProxyManager

logger = get_logger("http_client")

# ---------------------------------------------------------------------------
# Pool de User-Agents realistas (Chrome, Firefox, Edge — versões recentes)
# ---------------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Headers base que simulam um navegador real
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def _random_headers(extra: Optional[dict] = None) -> dict:
    """Gera headers com User-Agent aleatório."""
    headers = {**BASE_HEADERS, "User-Agent": random.choice(USER_AGENTS)}
    if extra:
        headers.update(extra)
    return headers


class HttpClient:
    """
    Cliente HTTP assíncrono com retry, proxy e rate limit.

    Encapsula httpx.AsyncClient com funcionalidades adicionais
    para scraping robusto e resiliente.
    """

    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        timeout: int = 15,
        max_retries: int = 3,
        rate_limit_delay: float = 1.0,
    ):
        self._proxy_manager = proxy_manager
        self._timeout = timeout
        self._max_retries = max_retries
        self._rate_limit_delay = rate_limit_delay
        self._last_request_time: dict[str, float] = {}
        self._request_count = 0
        self._error_count = 0

    async def _wait_rate_limit(self, domain: str) -> None:
        """Aguarda o intervalo mínimo entre requisições ao mesmo domínio."""
        now = time.time()
        last = self._last_request_time.get(domain, 0)
        elapsed = now - last
        if elapsed < self._rate_limit_delay:
            delay = self._rate_limit_delay - elapsed + random.uniform(0.1, 0.5)
            await asyncio.sleep(delay)
        self._last_request_time[domain] = time.time()

    def _extract_domain(self, url: str) -> str:
        """Extrai o domínio de uma URL."""
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return url

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        follow_redirects: bool = True,
    ) -> Optional[str]:
        """
        Realiza uma requisição HTTP com retry e proxy.

        Args:
            url: URL de destino.
            method: Método HTTP (GET, POST, etc.).
            headers: Headers adicionais.
            params: Parâmetros de query string.
            json_data: Dados JSON para POST.
            follow_redirects: Seguir redirecionamentos.

        Returns:
            Conteúdo da resposta como string, ou None em caso de falha.
        """
        domain = self._extract_domain(url)
        await self._wait_rate_limit(domain)

        req_headers = _random_headers(headers)
        last_error = None

        for attempt in range(self._max_retries):
            proxy_url = None
            try:
                # Obtém proxy se disponível
                if self._proxy_manager and self._proxy_manager.is_enabled:
                    proxy_url = await self._proxy_manager.get_proxy()

                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self._timeout),
                    proxy=proxy_url,
                    follow_redirects=follow_redirects,
                    http2=True,
                ) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=req_headers,
                        params=params,
                        json=json_data,
                    )

                    # Trata bloqueios
                    if response.status_code in (403, 429, 503):
                        logger.warning(
                            f"Bloqueio {response.status_code} em {domain} "
                            f"(tentativa {attempt + 1}/{self._max_retries})"
                        )
                        if proxy_url and self._proxy_manager:
                            await self._proxy_manager.report_failure(proxy_url)

                        # Backoff exponencial
                        wait_time = (2 ** attempt) + random.uniform(1, 3)
                        await asyncio.sleep(wait_time)

                        # Troca User-Agent na próxima tentativa
                        req_headers = _random_headers(headers)
                        continue

                    response.raise_for_status()
                    self._request_count += 1

                    if proxy_url and self._proxy_manager:
                        await self._proxy_manager.report_success(proxy_url)

                    return response.text

            except httpx.TimeoutException:
                logger.warning(f"Timeout em {domain} (tentativa {attempt + 1})")
                last_error = "timeout"
            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP {e.response.status_code} em {domain}")
                last_error = str(e)
            except Exception as e:
                logger.warning(f"Erro em {domain}: {type(e).__name__}: {e}")
                last_error = str(e)

            if proxy_url and self._proxy_manager:
                await self._proxy_manager.report_failure(proxy_url)

            # Backoff exponencial entre tentativas
            if attempt < self._max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0.5, 2)
                await asyncio.sleep(wait_time)

        self._error_count += 1
        logger.error(f"Falha apos {self._max_retries} tentativas em {url}: {last_error}")
        return None

    async def fetch_json(
        self,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Realiza uma requisição e retorna o JSON parseado.

        Returns:
            Dicionário com os dados JSON ou None em caso de falha.
        """
        import json

        text = await self.fetch(url, headers=headers, params=params)
        if text is None:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao parsear JSON de {url}: {e}")
            return None

    @property
    def stats(self) -> dict:
        """Retorna estatísticas do cliente HTTP."""
        return {
            "total_requests": self._request_count,
            "total_errors": self._error_count,
            "error_rate": (
                f"{self._error_count / max(self._request_count, 1) * 100:.1f}%"
            ),
        }
