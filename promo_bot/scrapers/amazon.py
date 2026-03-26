# promo_bot/scrapers/amazon.py
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_amazon")
URL = "https://www.amazon.com.br/deals"

async def abortar_recursos_pesados(route):
    if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
        await route.abort()
    else:
        await route.continue_()

async def buscar_amazon():
    promos = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            # 🚨 OTIMIZAÇÃO EXTREMA: Bloqueia recursos pesados da Amazon
            await page.route("**/*", abortar_recursos_pesados)
            
            logger.info("Acessando Amazon via Playwright (Modo Turbo)...")
            await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
            
            await page.evaluate("window.scrollBy(0, 2000)")
            await page.wait_for_timeout(1500) 

            cards = await page.locator("div[class*='DealGridItem'], div[data-testid='deal-card']").all()

            for card in cards:
                try:
                    titulo_locator = card.locator("div[class*='deal-title'], .DealContent div").first
                    titulo = await titulo_locator.inner_text()
                    
                    link_locator = card.locator("a").first
                    link = await link_locator.get_attribute("href")
                    
                    if titulo and link:
                        if link.startswith("/"):
                            link = "https://www.amazon.com.br" + link
                        link = link.split("?")[0].split("ref=")[0]
                        promos.append((titulo.strip(), link))
                except Exception:
                    continue
            
            await browser.close()
            logger.info(f"Amazon: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro Crítico ao raspar Amazon: {e}")
        
    return promos