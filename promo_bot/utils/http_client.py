"""
Cliente HTTP robusto e assíncrono do PromoBot.

Implementa requisições com rotação de User-Agent, suporte a proxies,
retry com backoff exponencial e controle de rate limit.
Inclui fallback via curl_cffi para sites com TLS fingerprinting.

CORREÇÕES v2.1:
- Backoff reduzido para evitar travamento (max 3s entre retries)
- Rate limit delay reduzido para 1s por domínio
- Timeout de curl_cffi reduzido
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

# Verifica se curl_cffi está disponível para fallback
try:
    from curl_cffi.requests import AsyncSession as CurlSession
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# ---------------------------------------------------------------------------
# Pool de User-Agents realistas (Chrome, Firefox, Edge — versões recentes)
# ---------------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
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

# Domínios que requerem curl_cffi (TLS fingerprinting)
CURL_CFFI_DOMAINS = {
    "www.pelando.com.br",
    "pelando.com.br",
    "www.amazon.com.br",
    "amazon.com.br",
    "shopee.com.br",
    "www.shopee.com.br",
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
    para scraping robusto e resiliente. Usa curl_cffi como
    fallback para sites com TLS fingerprinting.
    """

    # Backoff máximo entre retries (evita travamento)
    MAX_BACKOFF = 3.0

    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        timeout: int = 15,
        max_retries: int = 2,
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
            delay = self._rate_limit_delay - elapsed + random.uniform(0.1, 0.3)
            await asyncio.sleep(delay)
        self._last_request_time[domain] = time.time()

    def _extract_domain(self, url: str) -> str:
        """Extrai o domínio de uma URL."""
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return url

    def _should_use_curl(self, domain: str) -> bool:
        """Verifica se deve usar curl_cffi para o domínio."""
        return HAS_CURL_CFFI and domain in CURL_CFFI_DOMAINS

    async def _fetch_with_curl(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Optional[str]:
        """Faz requisição usando curl_cffi (bypassa TLS fingerprinting)."""
        req_headers = _random_headers(headers)
        last_error = None

        # Impersonate Chrome para bypass de TLS fingerprinting
        impersonate_options = ["chrome131", "chrome124", "chrome120", "chrome116"]

        for attempt in range(self._max_retries):
            try:
                impersonate = impersonate_options[attempt % len(impersonate_options)]

                async with CurlSession() as session:
                    response = await session.request(
                        method=method,
                        url=url,
                        headers=req_headers,
                        params=params,
                        timeout=self._timeout,
                        impersonate=impersonate,
                        allow_redirects=True,
                    )

                    if response.status_code in (403, 429, 503):
                        logger.warning(
                            f"Bloqueio {response.status_code} via curl em "
                            f"{self._extract_domain(url)} (tentativa {attempt + 1})"
                        )
                        wait_time = min((2 ** attempt) + random.uniform(0.5, 1.5), self.MAX_BACKOFF)
                        await asyncio.sleep(wait_time)
                        req_headers = _random_headers(headers)
                        continue

                    if response.status_code >= 400:
                        logger.warning(f"HTTP {response.status_code} via curl em {url}")
                        last_error = f"HTTP {response.status_code}"
                        continue

                    self._request_count += 1
                    return response.text

            except Exception as e:
                logger.warning(f"Erro curl em {url}: {type(e).__name__}: {e}")
                last_error = str(e)

            if attempt < self._max_retries - 1:
                wait_time = min((2 ** attempt) + random.uniform(0.3, 1.0), self.MAX_BACKOFF)
                await asyncio.sleep(wait_time)

        self._error_count += 1
        logger.error(f"Falha curl apos {self._max_retries} tentativas em {url}: {last_error}")
        return None

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

        Para domínios com TLS fingerprinting, usa curl_cffi automaticamente.

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

        # Usa curl_cffi para domínios com TLS fingerprinting
        if self._should_use_curl(domain) and json_data is None:
            result = await self._fetch_with_curl(url, method, headers, params)
            if result is not None:
                return result
            # Se curl falhou, tenta com httpx como fallback
            logger.debug(f"curl_cffi falhou para {domain}, tentando httpx")

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

                        # Backoff limitado
                        wait_time = min((2 ** attempt) + random.uniform(0.5, 1.5), self.MAX_BACKOFF)
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

            # Backoff limitado entre tentativas
            if attempt < self._max_retries - 1:
                wait_time = min((2 ** attempt) + random.uniform(0.3, 1.0), self.MAX_BACKOFF)
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
            "curl_cffi_available": HAS_CURL_CFFI,
        }
