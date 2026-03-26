import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_kabum")
URL = "https://www.kabum.com.br/ofertas/hardware"

async def scrape():
    promos = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            logger.info("Acessando Kabum...")
            await page.goto(URL, timeout=40000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000) # O React da Kabum é pesado, precisa desse tempo
            
            try:
                # 🚨 NOVO ALVO: Pega qualquer link na página inteira que leve para um produto
                await page.wait_for_selector("a[href*='/produto/']", timeout=15000)
                links = await page.locator("a[href*='/produto/']").all()
                
                for link_el in links:
                    try:
                        link = await link_el.get_attribute("href")
                        titulo = await link_el.inner_text()
                        
                        # Se o link for só uma imagem, pega o texto da imagem
                        if not titulo or len(titulo.strip()) < 5:
                            img = link_el.locator("img").first
                            if await img.count() > 0:
                                titulo = await img.get_attribute("title") or await img.get_attribute("alt")
                        
                        if titulo and link and "/produto/" in link:
                            if not link.startswith("http"):
                                link = "https://www.kabum.com.br" + link
                            
                            titulo_limpo = titulo.strip().replace('\n', ' ')
                            # Filtro de qualidade: O título precisa ser real
                            if len(titulo_limpo) > 10 and not any(link == o[1] for o in promos):
                                promos.append((titulo_limpo, link))
                    except Exception:
                        continue
            except Exception as wait_error:
                logger.warning(f"Kabum: Os links de produto não apareceram: {wait_error}")
                
            await browser.close()
            logger.info(f"Kabum: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro ao raspar Kabum: {e}")
        
    return promos