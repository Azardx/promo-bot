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
# INICIALIZAÇÃO DO BOT
# -------------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode="HTML",
        link_preview_is_disabled=True
    )
)
dp = Dispatcher()

# Cache local para evitar enviar a mesma promo várias vezes no mesmo ciclo
recent_links = set()

# -------------------------
# DESIGN PREMIUM E LIMPEZA
# -------------------------
def limpar_titulo(titulo_sujo: str) -> str:
    """Limpa o lixo que as lojas e agregadores colam no final dos títulos"""
    t = titulo_sujo
    lixos = ["Mercado Livre", "KaBuM!", "Amazon", "Terabyte", "Pichau", "Magazine Luiza", "Shopee", "AliExpress", "APPMemória"]
    for lixo in lixos:
        t = t.replace(lixo, " ")
    
    t = re.sub(r'[a-zA-ZÀ-ÿ]+\s*[a-zA-ZÀ-ÿ]*\d+\s*(min|h|d)\d*$', '', t)
    t = re.sub(r'Frete GrátisParcelado', ' | Frete Grátis', t)
    
    return t.strip()

def montar_texto(titulo_sujo):
    titulo_limpo = limpar_titulo(titulo_sujo)
    
    match_preco = re.search(r'(R\$\s*[\d\.,]+)(.*?R\$\s*[\d\.,]+)?', titulo_limpo)
    
    if match_preco:
        if match_preco.group(2):
            preco_antigo = match_preco.group(1).strip()
            preco_novo = match_preco.group(2).strip()
            nome_produto = titulo_limpo.replace(match_preco.group(0), "").strip()
            
            return (
                f"🚨 <b>OFERTA DETECTADA</b> 🚨\n\n"
                f"📦 {nome_produto}\n\n"
                f"❌ De: <s>{preco_antigo}</s>\n"
                f"✅ Por: <b>{preco_novo}</b>\n\n"
                f"🔗 Acesse o link abaixo:"
            )
        else:
            preco = match_preco.group(1).strip()
            nome_produto = titulo_limpo.replace(preco, "").strip()
            return (
                f"🔥 <b>OFERTA DETECTADA</b> 🔥\n\n"
                f"📦 {nome_produto}\n\n"
                f"💰 Valor: <b>{preco}</b>\n\n"
                f"🔗 Acesse o link abaixo:"
            )
            
    return (
        f"⚡ <b>PROMOÇÃO ATIVA</b> ⚡\n\n"
        f"🎯 {titulo_limpo}\n\n"
        f"🔗 Confira no link abaixo:"
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

def validar_link(link: str) -> bool:
    return bool(link and link.startswith("http"))

def criar_teclado(link: str):
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="🛒 Abrir promoção", url=link)]]
    )

# -------------------------
# COMANDOS
# -------------------------
@dp.message(CommandStart())
async def start(msg: types.Message):
    await msg.answer(
        "👋 Olá! Eu sou um bot de promoções automatizado.\n\n"
        "Eu envio as ofertas diretamente no canal oficial."
    )

@dp.message(Command("stats"))
async def stats(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    promos = await total_promos()
    await msg.answer(f"📊 <b>Painel do Bot</b>\n\n🔥 Promos: {promos}\n🎯 Alvo: <code>{GROUP_ID}</code>")

# -------------------------
# PROCESSAMENTO DE OFERTAS
# -------------------------
async def processar_promo(titulo_sujo, link):
    if not validar_link(link) or link in recent_links:
        return

    if await promo_exists(link):
        return

    await add_promo(link)
    recent_links.add(link)

    codigo_cupom = identificar_cupom(titulo_sujo)

    if codigo_cupom:
        texto = (
            f"🎟️ <b>CUPOM DE DESCONTO ENCONTRADO!</b>\n\n"
            f"📝 {titulo_sujo}\n\n"
            f"✂️ Código: <code>{codigo_cupom}</code>\n"
            f"<i>(Toque no código para copiar ☝️)</i>\n"
        )
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🛒 Resgatar Cupom", url=link)]]
        )
        logger.info(f"🎟️ Cupom enviado: {codigo_cupom}")
    else:
        texto = montar_texto(titulo_sujo)
        kb = criar_teclado(link)
        logger.info(f"🔥 Promo enviada: {titulo_sujo}")

    await broadcast(bot, texto, kb)

# -------------------------
# MOTOR DE BUSCA (MONITOR)
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
                    await asyncio.sleep(0.35)
                    
            elapsed = round(time.time() - start_time, 2)
            logger.info(f"Ciclo finalizado em {elapsed}s")
        except Exception as e:
            logger.error(f"Erro no monitor: {e}")
        
        await asyncio.sleep(SCRAPE_INTERVAL)

async def limpar_cache():
    while True:
        await asyncio.sleep(3600)
        recent_links.clear()

async def watchdog():
    while True:
        logger.info("Bot operando normalmente")
        await asyncio.sleep(300)

# -------------------------
# INICIALIZAÇÃO PRINCIPAL
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