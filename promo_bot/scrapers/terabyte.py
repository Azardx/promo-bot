import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_terabyte")
URL = "https://www.terabyteshop.com.br/promocoes"

async def abortar_recursos_pesados(route):
    if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
        await route.abort()
    else:
        await route.continue_()

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
            await page.route("**/*", abortar_recursos_pesados)
            
            logger.info("Acessando Terabyte via Playwright (Modo Turbo)...")
            await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
            
            # Espera carregar a div "pbox" que guarda os produtos
            await page.wait_for_selector(".pbox", timeout=15000)
            cards = await page.locator(".pbox").all()
            
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
                    
            await browser.close()
            logger.info(f"Terabyte: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro ao raspar Terabyte: {e}")
        
    return promos