import logging
from config import GROUP_ID

logger = logging.getLogger("broadcast")

async def broadcast(bot, texto, kb=None):
    if not GROUP_ID:
        logger.error("🚨 GROUP_ID não configurado no .env!")
        return

    try:
        # Envia apenas UMA vez para o grupo inteiro
        await bot.send_message(
            chat_id=GROUP_ID,
            text=texto,
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"Erro ao enviar oferta para o grupo: {e}")