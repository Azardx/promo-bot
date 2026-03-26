from bs4 import BeautifulSoup
from http_client import fetch

URL = "https://shopee.com.br/flash_sale"

async def scrape():

    html = await fetch(URL)

    soup = BeautifulSoup(html, "html.parser")

    promos = []

    for item in soup.select("a"):

        titulo = item.get_text(strip=True)

        if not titulo:
            continue

        link = item.get("href")

        if link and "shopee" in link:

            if not link.startswith("http"):
                link = "https://shopee.com.br" + link

            promos.append((titulo, link))

    return promos[:20]