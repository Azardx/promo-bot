import asyncio
import logging
import time
import re

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, SCRAPE_INTERVAL
from database import init_db, add_user, remove_user, promo_exists, add_promo
from services.promo_engine import coletar_promos
from services.broadcast import broadcast
from database import total_users, total_promos
from config import ADMIN_ID


# -------------------------
# LOGGING PROFISSIONAL
# -------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("promo_bot")


# -------------------------
# BOT INIT (COMPATÍVEL COM AIROGRAM 3.7+)
# -------------------------

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode="HTML",
        link_preview_is_disabled=True
    )
)

dp = Dispatcher()


# -------------------------
# CACHE LOCAL (ANTI DUPLICADO)
# -------------------------

recent_links = set()


# -------------------------
# UTILIDADES
# -------------------------

def validar_link(link: str) -> bool:
    if not link:
        return False
    if not link.startswith("http"):
        return False
    return True


def criar_teclado(link: str):

    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🛒 Abrir promoção",
                    url=link
                )
            ]
        ]
    )


def montar_texto(titulo):

    return (
        f"🔥 <b>{titulo}</b>\n\n"
        f"📦 Promoção encontrada!\n"
    )

# -------------------------
# IDENTIFICADOR DE CUPONS
# -------------------------
def identificar_cupom(titulo: str):
    """
    Analisa o título para ver se é um cupom e tenta extrair o código.
    Retorna o código do cupom, ou None se não for cupom.
    """
    t = titulo.upper()
    if "CUPOM" not in t and "CÓDIGO" not in t:
        return None
        
    # Tenta extrair o código do cupom (ex: KABUM10, CLIENTENOVO, etc)
    match = re.search(r'(?:CUPOM|CÓDIGO|USE O|CÓD)[\s:]*([A-Z0-9]{4,20})', t)
    if match:
        return match.group(1)
        
    # Segunda tentativa: busca palavras maiúsculas misturadas com números
    match_secundario = re.search(r'\b([A-Z]+[0-9]+[A-Z0-9]*)\b', t)
    if match_secundario:
         return match_secundario.group(1)
         
    # Se sabe que é cupom mas não achou o código exato
    return "APLICADO NO CARRINHO"


# -------------------------
# COMANDOS
# -------------------------

@dp.message(CommandStart())
async def start(msg: types.Message):

    await add_user(msg.from_user.id)

    await msg.answer(
        "🚀 <b>Bot ativado!</b>\n\n"
        "Você receberá promoções automaticamente."
    )


@dp.message(Command("stop"))
async def stop(msg: types.Message):

    await remove_user(msg.from_user.id)

    await msg.answer(
        "❌ Você saiu da lista de promoções."
    )
@dp.message(Command("stats"))
async def stats(msg: types.Message):

    if msg.from_user.id != ADMIN_ID:
        return

    users = await total_users()
    promos = await total_promos()

    texto = (
        "📊 <b>Painel do Bot</b>\n\n"
        f"👥 Usuários: {users}\n"
        f"🔥 Promos enviadas: {promos}\n"
    )

    await msg.answer(texto)


# -------------------------
# PROCESSAMENTO DE PROMO/CUPOM
# -------------------------
async def processar_promo(titulo, link):

    if not validar_link(link):
        return

    if link in recent_links:
        return

    if await promo_exists(link):
        return

    await add_promo(link)
    recent_links.add(link)

    # Ponto Chave: O bot decide se é cupom ou produto
    codigo_cupom = identificar_cupom(titulo)

    if codigo_cupom:
        # FORMATO EXCLUSIVO PARA CUPONS (Igual aos bots profissionais)
        texto = (
            f"🎟️ <b>CUPOM DE DESCONTO ENCONTRADO!</b>\n\n"
            f"📝 {titulo}\n\n"
            f"✂️ Código: <code>{codigo_cupom}</code>\n"
            f"<i>(Toque no código para copiar ☝️)</i>\n"
        )
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🛒 Resgatar Cupom", url=link)]]
        )
        logger.info(f"🎟️ Cupom enviado: {codigo_cupom}")
    else:
        # FORMATO PADRÃO PARA PROMOÇÕES
        texto = montar_texto(titulo)
        kb = criar_teclado(link)
        logger.info(f"🔥 Promo enviada: {titulo}")

    await broadcast(bot, texto, kb)


# -------------------------
# MONITOR DE PROMOÇÕES
# -------------------------

async def monitor():

    logger.info("Monitor iniciado")

    while True:

        try:

            start_time = time.time()

            promos = await coletar_promos()

            if not promos:
                logger.info("Nenhuma promoção encontrada")

            for titulo, link in promos:

                await processar_promo(titulo, link)

                # rate limit telegram
                await asyncio.sleep(0.35)

            elapsed = round(time.time() - start_time, 2)

            logger.info(f"Ciclo finalizado em {elapsed}s")

        except Exception as e:

            logger.error(f"Erro no monitor: {e}")

        await asyncio.sleep(SCRAPE_INTERVAL)


# -------------------------
# LIMPEZA DE CACHE
# -------------------------

async def limpar_cache():

    while True:

        await asyncio.sleep(3600)

        recent_links.clear()

        logger.info("Cache de links limpo")


# -------------------------
# WATCHDOG (SAÚDE DO BOT)
# -------------------------

async def watchdog():

    while True:

        logger.info("Bot operando normalmente")

        await asyncio.sleep(300)


# -------------------------
# MAIN
# -------------------------

async def main():

    logger.info("Inicializando sistema...")

    await init_db()

    asyncio.create_task(monitor())
    asyncio.create_task(limpar_cache())
    asyncio.create_task(watchdog())

    logger.info("Bot iniciado com sucesso")

    await dp.start_polling(bot)


# -------------------------
# START
# -------------------------

if __name__ == "__main__":

    asyncio.run(main())
