import aiohttp
from bs4 import BeautifulSoup


URL = "https://www.aliexpress.com/promotion/superdeals.html"


async def buscar_aliexpress():

    promos = []

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    async with aiohttp.ClientSession(headers=headers) as session:

        async with session.get(URL) as resp:

            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")

    produtos = soup.select(".product")

    for p in produtos:

        try:

            titulo = p.select_one(".title").text.strip()

            link = p.find("a")["href"]

            promos.append((titulo, link))

        except:
            continue

    return promos