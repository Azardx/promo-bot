from selectolax.parser import HTMLParser
from http_client import fetch

async def scrap_promobit():
    html = await fetch("https://www.promobit.com.br")

    tree = HTMLParser(html)

    ofertas = []

    for node in tree.css(".pr-product-card")[:10]:

        link_node = node.css_first("a")

        if not link_node:
            continue

        titulo = link_node.text().strip()
        link = link_node.attributes.get("href")

        if link and not link.startswith("http"):
            link = "https://www.promobit.com.br" + link

        ofertas.append((titulo, link))

    return ofertas