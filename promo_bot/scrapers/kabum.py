# promo_bot/scrapers/kabum.py
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_kabum")
URL = "https://www.kabum.com.br/ofertas"

# Função que intercepta requisições e bloqueia o que é inútil para o bot
async def abortar_recursos_pesados(route):
    if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
        await route.abort()
    else:
        await route.continue_()

async def scrape():
    promos = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            # Permissões extras para evitar ser reconhecido como bot
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            # 🚨 OTIMIZAÇÃO EXTREMA: Oculta imagens e CSS. O site carrega quase instantaneamente.
            await page.route("**/*", abortar_recursos_pesados)
            
            logger.info("Acessando Kabum via Playwright (Modo Turbo)...")
            await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
            
            try:
                # Espera pelos cards, mas se o Cloudflare segurar demais, ele não "crasha" o bot
                await page.wait_for_selector(".productCard", timeout=15000)
                cards = await page.locator(".productCard").all()
                
                for card in cards:
                    try:
                        titulo_el = card.locator(".nameCard")
                        titulo = await titulo_el.inner_text()
                        
                        link_el = card.locator("a").first
                        link = await link_el.get_attribute("href")
                        
                        if titulo and link:
                            if not link.startswith("http"):
                                link = "https://www.kabum.com.br" + link
                            promos.append((titulo.strip(), link))
                    except Exception:
                        continue
            except Exception as wait_error:
                logger.warning(f"Kabum demorou muito para renderizar ou fomos bloqueados: {wait_error}")
                
            await browser.close()
            logger.info(f"Kabum: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro Crítico ao raspar Kabum: {e}")
        
    return promos
