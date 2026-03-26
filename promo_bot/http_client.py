import asyncio
import random
import logging
from curl_cffi.requests import AsyncSession

logger = logging.getLogger("http_client")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1"
}

async def fetch(url, retries=3):
    # Usa a engine TLS do Chrome 120 para enganar o Cloudflare
    for attempt in range(retries):
        try:
            async with AsyncSession(impersonate="chrome120") as session:
                r = await session.get(url, headers=HEADERS, timeout=15)
                if r.status_code in [403, 429]:
                    logger.warning(f"Bloqueio {r.status_code} na URL: {url}")
                    return ""
                return r.text
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"Falha ao acessar {url} após {retries} tentativas: {e}")
                return ""
            await asyncio.sleep(random.uniform(2, 5))