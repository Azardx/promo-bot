import logging
from playwright.async_api import async_playwright

logger = logging.getLogger("scraper_terabyte")
URL = "https://www.terabyteshop.com.br/promocoes" # Voltamos para a página principal de promos

async def buscar_terabyte():
    promos = []
    try:
        async with async_playwright() as p:
            # 🚨 MODO VISÍVEL: Engana o Cloudflare fazendo ele achar que é você usando o PC
            browser = await p.chromium.launch(headless=False) 
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            logger.info("Acessando Terabyte (Modo Visível)...")
            
            # domcontentloaded é muito mais rápido e não trava com anúncios
            await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
            
            # Pausa humana obrigatória para o site respirar
            await page.wait_for_timeout(3000) 
            
            try:
                # Na página /promocoes, os produtos ficam na div .pbox
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
                logger.warning(f"Terabyte: Falha ao ler os cards: {wait_error}")
                    
            await browser.close()
            logger.info(f"Terabyte: {len(promos)} ofertas encontradas.")
            
    except Exception as e:
        logger.error(f"Erro ao raspar Terabyte: {e}")
        
    return promos