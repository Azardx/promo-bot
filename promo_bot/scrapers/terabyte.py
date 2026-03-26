import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_terabyte")
URL = "https://www.terabyteshop.com.br/promocoes"

async def buscar_terabyte():
    promos = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            logger.info("Acessando Terabyte...")
            await page.goto(URL, timeout=40000, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000) # Tempo vital para os produtos carregarem na tela
            
            try:
                # 🚨 NOVO ALVO: Pega o link do título diretamente, ignorando a caixa envolvente
                await page.wait_for_selector("a.prod-name", timeout=15000)
                titulos = await page.locator("a.prod-name").all()
                
                for titulo_el in titulos:
                    try:
                        titulo = await titulo_el.inner_text()
                        link = await titulo_el.get_attribute("href")
                        
                        if titulo and link:
                            if not link.startswith("http"):
                                link = "https://www.terabyteshop.com.br" + link
                            
                            # Evita ofertas duplicadas na mesma raspagem
                            if not any(link == o[1] for o in promos):
                                promos.append((titulo.strip(), link))
                    except Exception:
                        continue
            except Exception as wait_error:
                logger.warning(f"Terabyte: Os produtos não carregaram a tempo: {wait_error}")
                    
            await browser.close()
            logger.info(f"Terabyte: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro ao raspar Terabyte: {e}")
        
    return promos