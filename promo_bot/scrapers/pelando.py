from selectolax.parser import HTMLParser
from http_client import client


async def scrap_pelando():

    res = await client.get("https://www.pelando.com.br")

    tree = HTMLParser(res.text)

    ofertas = []

    for node in tree.css("article")[:10]:

        link_node = node.css_first("a")

        if not link_node:
            continue

        titulo = link_node.text().strip()
        link = link_node.attributes.get("href")

        if link and not link.startswith("http"):
            link = "https://www.pelando.com.br" + link

        ofertas.append((titulo, link))

    return ofertas