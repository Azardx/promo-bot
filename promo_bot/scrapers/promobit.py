import logging
from selectolax.parser import HTMLParser
from http_client import fetch

logger = logging.getLogger("scraper_promobit")

async def scrap_promobit():
    ofertas = []
    try:
        # Foca na página de informática
        html = await fetch("https://www.promobit.com.br/promocoes/informatica/")
        if not html:
            logger.warning("Promobit retornou HTML vazio.")
            return []
            
        tree = HTMLParser(html)
        
        # Procura os "cards" de produto que englobam a oferta
        for card in tree.css(".pr-product-card"):
            # Extrai o link de redirecionamento interno
            link_node = card.css_first("a[href*='/oferta/']")
            if not link_node:
                continue
                
            titulo_sujo = link_node.text(strip=True)
            link_promobit = link_node.attributes.get("href")
            
            # Tenta encontrar o preço, muitas vezes vem numa span ou b
            preco = ""
            preco_node = card.css_first(".pr-product-card-price, .pr-font-bold") 
            if preco_node:
                preco = preco_node.text(strip=True)

            # Evita capturar botões genéricos
            if link_promobit and titulo_sujo and len(titulo_sujo) > 10: 
                if not link_promobit.startswith("http"):
                    link_promobit = "https://www.promobit.com.br" + link_promobit

                # 🚨 MAGIA DO LINK LIMPO: Acessar a página do Promobit e pegar o link externo
                try:
                    html_oferta = await fetch(link_promobit)
                    if html_oferta:
                        tree_oferta = HTMLParser(html_oferta)
                        # O Promobit guarda o link externo da loja num botão de redirecionamento ou tag meta
                        link_externo_node = tree_oferta.css_first("a.pr-action-btn, a.pr-buy-btn")
                        
                        if link_externo_node:
                            link_final = link_externo_node.attributes.get("href")
                        else:
                            # Se não achar o botão, usa um fallback no HTML
                            meta_link = tree_oferta.css_first("meta[property='og:url']")
                            link_final = meta_link.attributes.get("content") if meta_link else link_promobit
                    else:
                        link_final = link_promobit
                except Exception as ex_link:
                    logger.debug(f"Falha ao extrair link limpo, usando original. {ex_link}")
                    link_final = link_promobit

                # Constrói um título legível juntando o preço e o nome, para não falhar na regex do main
                titulo_final = f"{preco} - {titulo_sujo}" if preco else titulo_sujo

                if not any(link_final == o[1] for o in ofertas):
                    ofertas.append((titulo_final, link_final))
            
            if len(ofertas) >= 15:
                break
                
        logger.info(f"Promobit: {len(ofertas)} ofertas encontradas.")
    except Exception as e:
        logger.error(f"Erro no Promobit: {e}")
        
    return ofertas