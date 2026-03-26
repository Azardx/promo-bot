import asyncio
from database import get_users

queue = asyncio.Queue()

RATE_LIMIT = 25


async def enqueue(texto, kb):

    await queue.put((texto, kb))


async def worker(bot):

    while True:

        texto, kb = await queue.get()

        users = await get_users()

        count = 0

        for user in users:

            try:

                await bot.send_message(
                    user,
                    texto,
                    reply_markup=kb
                )

                count += 1

                if count % RATE_LIMIT == 0:
                    await asyncio.sleep(1)

            except:
                pass

        queue.task_done()