import aiosqlite

DB_PATH = "data/bot.db"
db = None

async def init_db():
    global db
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY
    )
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS promos(
        link TEXT PRIMARY KEY
    )
    """)
    await db.commit()

async def add_user(uid):
    await db.execute("INSERT OR IGNORE INTO users VALUES (?)", (uid,))
    await db.commit()

async def remove_user(uid):
    await db.execute("DELETE FROM users WHERE id=?", (uid,))
    await db.commit()

async def user_exists(uid):
    async with db.execute("SELECT 1 FROM users WHERE id=?", (uid,)) as cursor:
        return await cursor.fetchone() is not None

async def promo_exists(link):
    async with db.execute("SELECT 1 FROM promos WHERE link=?", (link,)) as cursor:
        return await cursor.fetchone() is not None

async def add_promo(link):
    await db.execute("INSERT INTO promos VALUES (?)", (link,))
    await db.commit()

async def total_users():
    async with db.execute("SELECT COUNT(*) FROM users") as cursor:
        row = await cursor.fetchone()
        return row[0]

async def total_promos():
    async with db.execute("SELECT COUNT(*) FROM promos") as cursor:
        row = await cursor.fetchone()
        return row[0]