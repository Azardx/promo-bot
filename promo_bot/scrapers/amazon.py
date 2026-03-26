import aiohttp
from bs4 import BeautifulSoup


URL = "https://www.amazon.com.br/gp/goldbox"


async def buscar_amazon():

    promos = []

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    async with aiohttp.ClientSession(headers=headers) as session:

        async with session.get(URL) as resp:

            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")

    produtos = soup.select(".DealContent")

    for p in produtos:

        try:

            titulo = p.select_one(".DealTitle").text.strip()

            link = "https://amazon.com.br" + p.find("a")["href"]

            promos.append((titulo, link))

        except:
            continue

    return promos