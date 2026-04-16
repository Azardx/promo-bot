"""
Serviço de integração com o Telegram via aiogram 3.

Gerencia o bot, comandos administrativos, envio de mensagens
para canais/grupos e controle de rate limit da API do Telegram.
Suporta envio de imagens junto com as promoções quando disponíveis.

CORREÇÕES v2.3:
- Suporte a envio de fotos com legenda formatada (Markdown)
- Rate limiting aprimorado (delay entre mensagens)
- Tratamento de erros de rede e FloodControl
- Estilo visual profissional inspirado em Santana Tech
"""

from __future__ import annotations

import asyncio
from typing import Optional, Callable, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    URLInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from promo_bot.config import settings
from promo_bot.database.models import Product
from promo_bot.services.formatter import PromoFormatter
from promo_bot.utils.logger import get_logger

logger = get_logger("telegram")


class TelegramService:
    """
    Serviço do Telegram para o PromoBot.

    Gerencia a comunicação com a API do Telegram, incluindo
    envio de promoções com imagens, comandos administrativos
    e controle de rate limit.
    """

    # Limites da API do Telegram
    _MSG_DELAY = 3.0       # Delay entre mensagens para evitar rate limit (v2.3)
    _MAX_MSG_LENGTH = 4096  # Limite de caracteres por mensagem
    _MAX_CAPTION_LENGTH = 1024  # Limite de caracteres para caption de fotos

    def __init__(self):
        self._bot: Optional[Bot] = None
        self._dp: Optional[Dispatcher] = None
        self._formatter = PromoFormatter()
        self._stats_callback: Optional[Callable] = None
        self._messages_sent = 0
        self._photos_sent = 0
        self._errors = 0

    def initialize(self, stats_callback: Optional[Callable] = None) -> tuple[Bot, Dispatcher]:
        """
        Inicializa o bot e o dispatcher.

        Args:
            stats_callback: Função async que retorna dict de estatísticas.

        Returns:
            Tupla (Bot, Dispatcher) configurados.
        """
        self._bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(
                parse_mode="Markdown",
                link_preview_is_disabled=False,
            ),
        )
        self._dp = Dispatcher()
        self._stats_callback = stats_callback

        # Registra handlers de comandos
        self._register_handlers()

        logger.info("Servico Telegram inicializado")
        return self._bot, self._dp

    def _register_handlers(self) -> None:
        """Registra os handlers de comandos do bot."""

        @self._dp.message(CommandStart())
        async def cmd_start(message: types.Message) -> None:
            """Handler do comando /start."""
            await message.answer(
                "?? **Olá! Eu sou o PromoBot v2.3.**\n\n"
                "Eu monitoro automaticamente as melhores promoções de:\n"
                "?? Shopee\n"
                "?? AliExpress\n"
                "?? Amazon\n"
                "?? KaBuM!\n"
                "?? Pelando\n"
                "?? Promobit\n"
                "?? Terabyte Shop\n"
                "?? Mercado Livre\n\n"
                "As ofertas são enviadas automaticamente no canal configurado.\n\n"
                "?? Use /stats para ver estatísticas (admin).\n"
                "?? Use /help para mais informações."
            )

    async def send_promo(self, product: Product) -> bool:
        """
        Envia uma promoção formatada para o canal/grupo.

        Se o produto possui imagem, envia como foto com caption.
        Caso contrário, envia como mensagem de texto com botão inline.

        Args:
            product: Produto a ser enviado.

        Returns:
            True se enviado com sucesso, False caso contrário.
        """
        if not self._bot:
            logger.error("Bot nao inicializado")
            return False

        target = settings.target_chat_id
        if not target:
            logger.error("Nenhum chat de destino configurado")
            return False

        try:
            # Formata a mensagem usando o novo formatador v2.3
            text = self._formatter.format_promo(product)

            # Cria teclado inline com botão de link
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="?? Abrir Oferta", url=product.link))
            keyboard = builder.as_markup()

            # Tenta enviar com imagem se disponível
            if product.image_url:
                try:
                    # Trunca caption se necessário (limite 1024)
                    caption = text[:self._MAX_CAPTION_LENGTH-50] + "..." if len(text) > self._MAX_CAPTION_LENGTH else text
                    
                    photo = URLInputFile(product.image_url)
                    await self._bot.send_photo(
                        chat_id=target,
                        photo=photo,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                    self._photos_sent += 1
                    self._messages_sent += 1
                    logger.info(f"Promo enviada com foto: {product.title[:60]}")
                    await asyncio.sleep(self._MSG_DELAY)
                    return True
                except Exception as e:
                    logger.warning(f"Falha ao enviar foto, tentando fallback texto: {e}")

            # Envio como texto (sem imagem ou fallback)
            await self._bot.send_message(
                chat_id=target,
                text=text[:self._MAX_MSG_LENGTH],
                parse_mode="Markdown",
                reply_markup=keyboard,
                disable_web_page_preview=False
            )
            self._messages_sent += 1
            logger.info(f"Promo enviada (texto): {product.title[:60]}")
            await asyncio.sleep(self._MSG_DELAY)
            return True

        except Exception as e:
            self._errors += 1
            logger.error(f"Erro ao enviar promo: {e}")
            return False

    async def send_admin_message(self, text: str) -> None:
        """Envia uma mensagem para o administrador."""
        if not self._bot or not settings.admin_id:
            return
        try:
            await self._bot.send_message(
                chat_id=settings.admin_id,
                text=f"?? **ADMIN:**\n\n{text}",
                parse_mode="Markdown"
            )
        except Exception:
            pass
