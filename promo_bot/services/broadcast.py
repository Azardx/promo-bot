from database import DB_PATH
import aiosqlite


async def broadcast(bot, texto, kb):

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute("SELECT id FROM users") as cursor:

            users = await cursor.fetchall()

    for u in users:

        try:

            await bot.send_message(
                u[0],
                texto,
                reply_markup=kb
            )

        except:
            continue