import logging
from selectolax.parser import HTMLParser
from http_client import fetch

logger = logging.getLogger("scraper_pelando")

async def scrap_pelando():
    ofertas = []
    try:
        html = await fetch("https://www.pelando.com.br")
        if not html:
            return []
            
        tree = HTMLParser(html)
        
        # Pega as 15 ofertas mais recentes do Pelando
        for node in tree.css("article")[:15]:
            link_node = node.css_first("a")
            if not link_node:
                continue
                
            titulo = link_node.text(strip=True)
            link = link_node.attributes.get("href")
            
            if link and not link.startswith("http"):
                link = "https://www.pelando.com.br" + link
                
            ofertas.append((titulo, link))
            
        logger.info(f"Pelando: {len(ofertas)} ofertas encontradas.")
    except Exception as e:
        logger.error(f"Erro no Pelando: {e}")
        
    return ofertas