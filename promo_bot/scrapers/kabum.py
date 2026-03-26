from bs4 import BeautifulSoup
from http_client import fetch

URL = "https://www.kabum.com.br/ofertas"

async def scrape():

    html = await fetch(URL)

    soup = BeautifulSoup(html, "html.parser")

    promos = []

    for item in soup.select(".productCard"):

        titulo = item.select_one(".nameCard")

        link = item.select_one("a")

        if not titulo or not link:
            continue

        titulo = titulo.get_text(strip=True)
        link = "https://www.kabum.com.br" + link["href"]

        promos.append((titulo, link))

    return promos