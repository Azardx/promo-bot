import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_kabum")
URL = "https://www.kabum.com.br/ofertas"

async def scrape():
    promos = []
    try:
        async with async_playwright() as p:
            # Lança um navegador Chrome invisível
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            logger.info("Acessando Kabum via Playwright...")
            # wait_until="domcontentloaded" faz o bot não perder tempo esperando anúncios carregarem
            await page.goto(URL, timeout=20000, wait_until="domcontentloaded")
            
            # Ponto chave: Força o bot a esperar os cards de produto aparecerem (dribla o Cloudflare/Loading)
            await page.wait_for_selector(".productCard", timeout=15000)
            
            # Pega todos os cards de produto na tela
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
                    
            await browser.close()
            logger.info(f"Kabum: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro ao raspar Kabum: {e}")
        
    return promos
