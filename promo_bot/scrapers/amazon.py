import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_amazon")
URL = "https://www.amazon.com.br/deals"

async def buscar_amazon():
    promos = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            logger.info("Acessando Amazon via Playwright...")
            await page.goto(URL, timeout=25000, wait_until="domcontentloaded")
            
            # Truque Profissional: Rola a página para baixo para forçar o JavaScript a carregar os produtos
            await page.evaluate("window.scrollBy(0, 2000)")
            await page.wait_for_timeout(2000) # Espera 2 segundos para o carregamento terminar

            # O DOM da Amazon é complexo, procuramos divs que contenham a classe deal-card ou similares
            cards = await page.locator("div[class*='DealGridItem'], div[data-testid='deal-card']").all()

            for card in cards:
                try:
                    # Extrai o título do produto
                    titulo_locator = card.locator("div[class*='deal-title'], .DealContent div").first
                    titulo = await titulo_locator.inner_text()
                    
                    # Extrai o link
                    link_locator = card.locator("a").first
                    link = await link_locator.get_attribute("href")
                    
                    if titulo and link:
                        if link.startswith("/"):
                            link = "https://www.amazon.com.br" + link
                        
                        # Limpa URLs sujas da Amazon para evitar IDs de sessão gigantes
                        link = link.split("?")[0].split("ref=")[0]
                        
                        promos.append((titulo.strip(), link))
                except Exception:
                    continue
            
            await browser.close()
            logger.info(f"Amazon: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro ao raspar Amazon: {e}")
        
    return promos
