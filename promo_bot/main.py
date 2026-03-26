import asyncio
import logging
import time
import re

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, SCRAPE_INTERVAL, ADMIN_ID, GROUP_ID
from database import init_db, promo_exists, add_promo, total_promos
from services.promo_engine import coletar_promos
from services.broadcast import broadcast

# -------------------------
# LOGGING PROFISSIONAL
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("promo_bot")

# -------------------------
# BOT INIT (A CORREÇÃO DO SEU ERRO ESTÁ AQUI!)
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
# UTILIDADES & IDENTIFICADOR DE CUPOM
# -------------------------
def validar_link(link: str) -> bool:
    if not link or not link.startswith("http"):
        return False
    return True

def criar_teclado(link: str):
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="🛒 Abrir promoção", url=link)]]
    )

def montar_texto(titulo):
    return (
        f"🔥 <b>{titulo}</b>\n\n"
        f"📦 Promoção encontrada!\n"
    )

def identificar_cupom(titulo: str):
    t = titulo.upper()
    if "CUPOM" not in t and "CÓDIGO" not in t:
        return None
        
    match = re.search(r'(?:CUPOM|CÓDIGO|USE O|CÓD)[\s:]*([A-Z0-9]{4,20})', t)
    if match:
        return match.group(1)
        
    match_secundario = re.search(r'\b([A-Z]+[0-9]+[A-Z0-9]*)\b', t)
    if match_secundario:
         return match_secundario.group(1)
         
    return "APLICADO NO CARRINHO"

# -------------------------
# COMANDOS (MODO GRUPO)
# -------------------------
@dp.message(CommandStart())
async def start(msg: types.Message):
    await msg.answer(
        "👋 Olá! Eu sou um bot de promoções automatizado.\n\n"
        "Eu envio as ofertas diretamente no nosso canal/grupo oficial. Fique de olho lá para não perder nada!"
    )

@dp.message(Command("stats"))
async def stats(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    promos = await total_promos()
    texto = (
        "📊 <b>Painel do Bot (Modo Grupo)</b>\n\n"
        f"🔥 Promos processadas: {promos}\n"
        f"🎯 Alvo: <code>{GROUP_ID}</code>"
    )
    await msg.answer(texto)

@dp.message(Command("limpar_cache"))
async def cmd_limpar_cache(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    recent_links.clear()
    await msg.answer("🧹 <b>Cache de links limpo com sucesso!</b>")

@dp.message(Command("aviso"))
async def cmd_aviso_admin(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    texto_aviso = msg.text.replace("/aviso", "").strip()
    if not texto_aviso:
        await msg.answer("⚠️ Uso correto: <code>/aviso Sua mensagem aqui</code>")
        return
    await broadcast(bot, f"📢 <b>AVISO DO ADMIN</b>\n\n{texto_aviso}")
    await msg.answer("✅ Aviso enviado com sucesso para o grupo!")

# -------------------------
# PROCESSAMENTO DE PROMO/CUPOM
# -------------------------

async def processar_promo(titulo, link):
    if not validar_link(link) or link in recent_links:
        return

    # O SEGREDO ESTÁ AQUI: Logar quando o Banco de Dados barrar
    if await promo_exists(link):
        logger.debug(f"⏭️ Ignorando repetida: {titulo}")
        return

    await add_promo(link)
    recent_links.add(link)
    
    # ... (o resto da função continua igualzinho com a lógica de Cupom que fizemos) ...

async def processar_promo(titulo, link):
    if not validar_link(link) or link in recent_links:
        return

    if await promo_exists(link):
        return

    await add_promo(link)
    recent_links.add(link)

    codigo_cupom = identificar_cupom(titulo)

    if codigo_cupom:
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
        texto = montar_texto(titulo)
        kb = criar_teclado(link)
        logger.info(f"🔥 Promo enviada: {titulo}")

    # Dispara a oferta em massa via Telegram para o Grupo configurado no .env
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
                logger.info("Nenhuma promoção encontrada neste ciclo")
            else:
                for titulo, link in promos:
                    await processar_promo(titulo, link)
                    # Delay mínimo apenas para o Telegram não estranhar a velocidade
                    await asyncio.sleep(0.35)
                    
            elapsed = round(time.time() - start_time, 2)
            logger.info(f"Ciclo finalizado em {elapsed}s")
        except Exception as e:
            logger.error(f"Erro no monitor: {e}")
        
        await asyncio.sleep(SCRAPE_INTERVAL)

# -------------------------
# LIMPEZA E SAÚDE
# -------------------------
async def limpar_cache():
    while True:
        await asyncio.sleep(3600)
        recent_links.clear()
        logger.info("Cache de links limpo automaticamente")

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
    
    logger.info("Bot iniciado com sucesso e escutando mensagens")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())