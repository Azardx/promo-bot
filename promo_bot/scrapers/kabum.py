import logging
import asyncio
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_kabum")
URL = "https://www.kabum.com.br/ofertas/hardware"

async def scrape():
    promos = []
    try:
        async with async_playwright() as p:
            # Não use headless para testes locais caso o IP esteja bloqueado
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            logger.info("Acessando Kabum...")
            # Esperamos que a rede fique inativa (menos recursos carregando) para evitar timeout
            await page.goto(URL, timeout=40000, wait_until="networkidle")
            
            try:
                # Vamos esperar o artigo de produto aparecer. A Kabum usa tag article
                await page.wait_for_selector("article.productCard", timeout=15000)
                cards = await page.locator("article.productCard").all()
                
                for card in cards:
                    try:
                        titulo_el = card.locator(".nameCard")
                        titulo = await titulo_el.inner_text()
                        
                        link_el = card.locator("a.productLink").first
                        link = await link_el.get_attribute("href")
                        
                        if titulo and link:
                            if not link.startswith("http"):
                                link = "https://www.kabum.com.br" + link
                            promos.append((titulo.strip(), link))
                    except Exception:
                        continue
            except Exception as wait_error:
                logger.warning(f"Kabum: O seletor não carregou: {wait_error}")
                
            await browser.close()
            logger.info(f"Kabum: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro Crítico ao raspar Kabum: {e}")
        
    return promos