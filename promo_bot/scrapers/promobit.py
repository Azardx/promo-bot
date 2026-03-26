import logging
from selectolax.parser import HTMLParser
from http_client import fetch

logger = logging.getLogger("scraper_promobit")

async def scrap_promobit():
    ofertas = []
    try:
        html = await fetch("https://www.promobit.com.br")
        if not html:
            return []
            
        tree = HTMLParser(html)
        
        for node in tree.css(".pr-product-card")[:15]:
            link_node = node.css_first("a")
            if not link_node:
                continue
                
            titulo = link_node.text(strip=True)
            link = link_node.attributes.get("href")
            
            if link and not link.startswith("http"):
                link = "https://www.promobit.com.br" + link
                
            ofertas.append((titulo, link))
            
        logger.info(f"Promobit: {len(ofertas)} ofertas encontradas.")
    except Exception as e:
        logger.error(f"Erro no Promobit: {e}")
        
    return ofertas