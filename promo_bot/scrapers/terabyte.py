import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_terabyte")
URL = "https://www.terabyteshop.com.br/hardware"

async def buscar_terabyte():
    promos = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            logger.info("Acessando Terabyte...")
            await page.goto(URL, timeout=40000, wait_until="networkidle")
            
            try:
                # O seletor correto e mais estável da Terabyte costuma ser a div.pbox
                await page.wait_for_selector("div.pbox", timeout=15000)
                cards = await page.locator("div.pbox").all()
                
                for card in cards:
                    try:
                        titulo_el = card.locator("a.prod-name")
                        titulo = await titulo_el.inner_text()
                        link = await titulo_el.get_attribute("href")
                        
                        if titulo and link:
                            if not link.startswith("http"):
                                link = "https://www.terabyteshop.com.br" + link
                            promos.append((titulo.strip(), link))
                    except Exception:
                        continue
            except Exception as wait_error:
                logger.warning(f"Terabyte: O seletor não carregou: {wait_error}")
                    
            await browser.close()
            logger.info(f"Terabyte: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro ao raspar Terabyte: {e}")
        
    return promos