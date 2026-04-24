"""
Cliente HTTP robusto e assíncrono do PromoBot.

CORREÇÕES v2.3+:
- Terabyte Shop adicionado ao CURL_CFFI_DOMAINS
- Timeout de curl_cffi alinhado ao timeout geral
- Fallback httpx mais robusto após falha curl
- Métricas de domínio para diagnóstico
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

try:
    from curl_cffi.requests import AsyncSession as CurlSession
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    logger.warning("curl_cffi não instalado — Pelando/Amazon/Shopee/Terabyte podem falhar")

# ── Pool de User-Agents ───────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
]

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

# Domínios que REQUEREM curl_cffi (TLS fingerprinting)
CURL_CFFI_DOMAINS: frozenset[str] = frozenset({
    "www.pelando.com.br",
    "pelando.com.br",
    "www.amazon.com.br",
    "amazon.com.br",
    "shopee.com.br",
    "www.shopee.com.br",
    "www.terabyteshop.com.br",    # ← ADICIONADO
    "terabyteshop.com.br",        # ← ADICIONADO
})

# Opções de impersonation em ordem de preferência
_IMPERSONATE_OPTIONS = ["chrome131", "chrome124", "chrome120", "chrome116"]


def _random_headers(extra: Optional[dict] = None) -> dict:
    headers = {**BASE_HEADERS, "User-Agent": random.choice(USER_AGENTS)}
    if extra:
        headers.update(extra)
    return headers


class HttpClient:
    """
    Cliente HTTP assíncrono com retry, proxy, rate limit e curl_cffi.

    Seleciona automaticamente curl_cffi para sites com TLS fingerprinting
    e faz fallback para httpx nos demais.
    """

    MAX_BACKOFF = 3.0

    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        timeout: int = 20,
        max_retries: int = 2,
        rate_limit_delay: float = 1.0,
    ):
        self._proxy_manager   = proxy_manager
        self._timeout         = timeout
        self._max_retries     = max_retries
        self._rate_limit_delay = rate_limit_delay
        self._last_request_time: dict[str, float] = {}
        self._request_count  = 0
        self._error_count    = 0
        # Métricas por domínio para diagnóstico
        self._domain_errors: dict[str, int] = {}

    # ── Rate limiting ─────────────────────────────────────────────────────────

    async def _wait_rate_limit(self, domain: str) -> None:
        now     = time.time()
        elapsed = now - self._last_request_time.get(domain, 0)
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed + random.uniform(0.1, 0.4))
        self._last_request_time[domain] = time.time()

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return url

    def _should_use_curl(self, domain: str) -> bool:
        return HAS_CURL_CFFI and domain in CURL_CFFI_DOMAINS

    # ── curl_cffi ─────────────────────────────────────────────────────────────

    async def _fetch_with_curl(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Optional[str]:
        req_headers = _random_headers(headers)
        last_error: Optional[str] = None

        for attempt in range(self._max_retries):
            impersonate = _IMPERSONATE_OPTIONS[attempt % len(_IMPERSONATE_OPTIONS)]
            try:
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
                        wait = min(2 ** attempt + random.uniform(0.5, 1.5), self.MAX_BACKOFF)
                        logger.warning(
                            f"[curl] Bloqueio {response.status_code} em "
                            f"{self._extract_domain(url)} — aguardando {wait:.1f}s"
                        )
                        await asyncio.sleep(wait)
                        req_headers = _random_headers(headers)
                        continue

                    if response.status_code >= 400:
                        last_error = f"HTTP {response.status_code}"
                        break

                    self._request_count += 1
                    return response.text

            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.debug(f"[curl] Tentativa {attempt + 1} falhou em {url}: {last_error}")

            if attempt < self._max_retries - 1:
                await asyncio.sleep(min(2 ** attempt + random.uniform(0.3, 1.0), self.MAX_BACKOFF))

        self._error_count += 1
        domain = self._extract_domain(url)
        self._domain_errors[domain] = self._domain_errors.get(domain, 0) + 1
        logger.error(f"[curl] Falhou após {self._max_retries} tentativas em {url}: {last_error}")
        return None

    # ── httpx ────────────────────────────────────────────────────────────────

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
        Requisição HTTP com retry, proxy e fallback curl_cffi.

        Para domínios com TLS fingerprinting (CURL_CFFI_DOMAINS), usa
        curl_cffi automaticamente. Para POST com JSON, usa httpx diretamente.
        """
        domain = self._extract_domain(url)
        await self._wait_rate_limit(domain)

        # curl_cffi para domínios sensíveis (GET apenas)
        if self._should_use_curl(domain) and json_data is None:
            result = await self._fetch_with_curl(url, method, headers, params)
            if result is not None:
                return result
            logger.debug(f"curl falhou para {domain}, tentando httpx como fallback")

        req_headers = _random_headers(headers)
        last_error: Optional[str] = None

        for attempt in range(self._max_retries):
            proxy_url: Optional[str] = None
            try:
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

                    if response.status_code in (403, 429, 503):
                        wait = min(2 ** attempt + random.uniform(0.5, 1.5), self.MAX_BACKOFF)
                        logger.warning(
                            f"[httpx] Bloqueio {response.status_code} em {domain} "
                            f"(tentativa {attempt + 1}) — aguardando {wait:.1f}s"
                        )
                        if proxy_url and self._proxy_manager:
                            await self._proxy_manager.report_failure(proxy_url)
                        await asyncio.sleep(wait)
                        req_headers = _random_headers(headers)
                        continue

                    response.raise_for_status()
                    self._request_count += 1
                    if proxy_url and self._proxy_manager:
                        await self._proxy_manager.report_success(proxy_url)
                    return response.text

            except httpx.TimeoutException:
                last_error = "timeout"
                logger.debug(f"[httpx] Timeout em {domain} (tentativa {attempt + 1})")
            except httpx.HTTPStatusError as exc:
                last_error = f"HTTP {exc.response.status_code}"
                logger.debug(f"[httpx] {last_error} em {domain}")
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.debug(f"[httpx] Erro em {domain}: {last_error}")

            if proxy_url and self._proxy_manager:
                await self._proxy_manager.report_failure(proxy_url)

            if attempt < self._max_retries - 1:
                await asyncio.sleep(min(2 ** attempt + random.uniform(0.3, 1.0), self.MAX_BACKOFF))

        self._error_count += 1
        self._domain_errors[domain] = self._domain_errors.get(domain, 0) + 1
        logger.error(f"[httpx] Falhou após {self._max_retries} tentativas em {url}: {last_error}")
        return None

    async def fetch_json(
        self,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> Optional[dict]:
        """Realiza GET/POST e retorna o JSON parseado, ou None em caso de falha."""
        import json

        text = await self.fetch(url, headers=headers, params=params, json_data=json_data)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error(f"JSON inválido de {url}: {exc}")
            return None

    @property
    def stats(self) -> dict:
        total = max(self._request_count, 1)
        return {
            "total_requests":     self._request_count,
            "total_errors":       self._error_count,
            "error_rate":         f"{self._error_count / total * 100:.1f}%",
            "curl_cffi_available": HAS_CURL_CFFI,
            "top_error_domains":  sorted(
                self._domain_errors.items(), key=lambda x: x[1], reverse=True
            )[:5],
        }
