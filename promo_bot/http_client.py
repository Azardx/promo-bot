# promo_bot/http_client.py
import aiohttp
import asyncio
import random
import logging

logger = logging.getLogger("http_client")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

TIMEOUT = aiohttp.ClientTimeout(total=15)

async def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
                async with session.get(url) as r:
                    if r.status in [403, 429]:
                        logger.warning(f"Bloqueio {r.status} na URL: {url}")
                        return "" # Retorna vazio se bloqueado
                    r.raise_for_status()
                    return await r.text()
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"Falha ao acessar {url} após {retries} tentativas: {e}")
                return ""
            await asyncio.sleep(random.uniform(2, 5))