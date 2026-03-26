import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_kabum")
URL = "https://www.kabum.com.br/ofertas/hardware"

async def scrape():
    promos = []
    try:
        async with async_playwright() as p:
            # 🚨 MODO VISÍVEL
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            logger.info("Acessando Kabum (Modo Visível)...")
            await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            
            try:
                # Pega qualquer article que pareça um produto
                cards = await page.locator("article").all()
                
                for card in cards:
                    try:
                        link_el = card.locator("a").first
                        link = await link_el.get_attribute("href")
                        
                        titulo_el = card.locator(".nameCard, span.nameCard").first
                        titulo = await titulo_el.inner_text()
                        
                        if titulo and link and "/produto/" in link:
                            if not link.startswith("http"):
                                link = "https://www.kabum.com.br" + link
                            promos.append((titulo.strip(), link))
                    except Exception:
                        continue
            except Exception as wait_error:
                logger.warning(f"Kabum: Erro ao ler cards: {wait_error}")
                
            await browser.close()
            logger.info(f"Kabum: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro ao raspar Kabum: {e}")
        
    return promos