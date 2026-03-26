import logging
from selectolax.parser import HTMLParser
from http_client import fetch

logger = logging.getLogger("scraper_promobit")

async def scrap_promobit():
    ofertas = []
    try:
        # Foca na categoria de informática direto
        html = await fetch("https://www.promobit.com.br/promocoes/informatica/")
        if not html:
            logger.warning("Promobit retornou HTML vazio.")
            return []
            
        tree = HTMLParser(html)
        
        # 🚨 TRUQUE: Em vez de procurar classes de design, pega qualquer link que tenha "/oferta/" no meio
        for link_node in tree.css("a[href*='/oferta/']"):
            titulo = link_node.text(strip=True)
            link = link_node.attributes.get("href")
            
            # Filtra links inúteis que não têm título real
            if link and titulo and len(titulo) > 10: 
                if not link.startswith("http"):
                    link = "https://www.promobit.com.br" + link
                
                # Evita duplicatas na mesma passada
                if not any(link == o[1] for o in ofertas):
                    ofertas.append((titulo, link))
            
            if len(ofertas) >= 15: # Pega as 15 primeiras
                break
                
        logger.info(f"Promobit: {len(ofertas)} ofertas encontradas.")
    except Exception as e:
        logger.error(f"Erro no Promobit: {e}")
        
    return ofertas