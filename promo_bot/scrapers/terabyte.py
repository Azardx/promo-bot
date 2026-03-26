import logging
from selectolax.parser import HTMLParser
from http_client import fetch

logger = logging.getLogger("scraper_terabyte")
URL = "https://www.terabyteshop.com.br/promocoes"

async def buscar_terabyte():
    promos = []
    try:
        logger.info("Acessando Terabyte (Modo Stealth)...")
        # Usa o nosso http_client blindado com curl_cffi
        html = await fetch(URL)
        
        if not html:
            logger.warning("Terabyte bloqueou a requisição ou retornou vazio.")
            return []
            
        tree = HTMLParser(html)
        
        # Caça os links de produtos
        for node in tree.css("a.prod-name"):
            titulo = node.text(strip=True)
            link = node.attributes.get("href")
            
            if titulo and link:
                if not link.startswith("http"):
                    link = "https://www.terabyteshop.com.br" + link
                
                if not any(link == o[1] for o in promos):
                    promos.append((titulo.strip(), link))
                    
        logger.info(f"Terabyte: {len(promos)} ofertas encontradas.")
    except Exception as e:
        logger.error(f"Erro ao raspar Terabyte: {e}")
        
    return promos