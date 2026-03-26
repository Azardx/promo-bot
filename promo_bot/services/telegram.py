"""
Serviço de integração com o Telegram via aiogram 3.

Gerencia o bot, comandos administrativos, envio de mensagens
para canais/grupos e controle de rate limit da API do Telegram.
"""

from __future__ import annotations

import asyncio
from typing import Optional, Callable, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from promo_bot.config import settings
from promo_bot.database.models import Product
from promo_bot.services.formatter import MessageFormatter
from promo_bot.utils.logger import get_logger

logger = get_logger("telegram")


class TelegramService:
    """
    Serviço do Telegram para o PromoBot.

    Gerencia a comunicação com a API do Telegram, incluindo
    envio de promoções, comandos administrativos e controle
    de rate limit.
    """

    # Limites da API do Telegram
    _MSG_DELAY = 0.5       # Delay mínimo entre mensagens
    _GROUP_DELAY = 3.0     # Delay para grupos (mais restritivo)
    _MAX_MSG_LENGTH = 4096  # Limite de caracteres por mensagem

    def __init__(self):
        self._bot: Optional[Bot] = None
        self._dp: Optional[Dispatcher] = None
        self._formatter = MessageFormatter()
        self._stats_callback: Optional[Callable] = None
        self._messages_sent = 0
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
                parse_mode="HTML",
                link_preview_is_disabled=True,
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
                "👋 <b>Olá! Eu sou o PromoBot.</b>\n\n"
                "Eu monitoro automaticamente as melhores promoções de:\n"
                "🟠 Shopee\n"
                "🔴 AliExpress\n"
                "📦 Amazon\n"
                "🟢 KaBuM!\n"
                "🔥 Pelando\n"
                "💎 Promobit\n\n"
                "As ofertas são enviadas automaticamente no canal/grupo configurado.\n\n"
                "📊 Use /stats para ver estatísticas (admin).\n"
                "ℹ️ Use /help para mais informações."
            )

        @self._dp.message(Command("help"))
        async def cmd_help(message: types.Message) -> None:
            """Handler do comando /help."""
            await message.answer(
                "ℹ️ <b>Comandos disponíveis:</b>\n\n"
                "/start — Mensagem de boas-vindas\n"
                "/help — Esta mensagem de ajuda\n"
                "/stats — Estatísticas do bot (admin)\n"
                "/health — Status de saúde do sistema (admin)\n"
                "/force — Forçar ciclo de coleta (admin)\n\n"
                "💡 As promoções são coletadas e enviadas automaticamente."
            )

        @self._dp.message(Command("stats"))
        async def cmd_stats(message: types.Message) -> None:
            """Handler do comando /stats (apenas admin)."""
            if message.from_user.id != settings.admin_id:
                return

            if self._stats_callback:
                try:
                    stats = await self._stats_callback()
                    text = self._formatter.format_stats_message(stats)
                    await message.answer(text)
                except Exception as e:
                    await message.answer(f"❌ Erro ao obter estatísticas: {e}")
            else:
                await message.answer(
                    f"📊 <b>Estatísticas básicas</b>\n\n"
                    f"📤 Mensagens enviadas: <b>{self._messages_sent}</b>\n"
                    f"❌ Erros: <b>{self._errors}</b>"
                )

        @self._dp.message(Command("health"))
        async def cmd_health(message: types.Message) -> None:
            """Handler do comando /health (apenas admin)."""
            if message.from_user.id != settings.admin_id:
                return

            await message.answer(
                "✅ <b>Sistema operacional</b>\n\n"
                f"🤖 Bot: Online\n"
                f"📤 Mensagens: {self._messages_sent}\n"
                f"❌ Erros: {self._errors}\n"
                f"🎯 Destino: <code>{settings.target_chat_id}</code>"
            )

    async def send_promo(self, product: Product) -> bool:
        """
        Envia uma promoção formatada para o canal/grupo.

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
            # Formata a mensagem
            text = self._formatter.format_product(product)

            # Cria teclado inline com botão de link
            keyboard = self._create_keyboard(product)

            # Trunca se necessário
            if len(text) > self._MAX_MSG_LENGTH:
                text = text[: self._MAX_MSG_LENGTH - 50] + "\n\n🔗 <b>Veja mais no link:</b>"

            # Envia a mensagem
            await self._bot.send_message(
                chat_id=target,
                text=text,
                reply_markup=keyboard,
            )

            self._messages_sent += 1
            logger.info(f"Promo enviada: {product.title[:60]} ({product.store.value})")

            # Rate limit
            await asyncio.sleep(self._MSG_DELAY)
            return True

        except Exception as e:
            self._errors += 1
            logger.error(f"Erro ao enviar promo: {e}")

            # Se for rate limit do Telegram, aguarda mais
            if "retry after" in str(e).lower():
                import re
                match = re.search(r"retry after (\d+)", str(e).lower())
                wait = int(match.group(1)) + 1 if match else 30
                logger.warning(f"Rate limit do Telegram, aguardando {wait}s")
                await asyncio.sleep(wait)

            return False

    def _create_keyboard(self, product: Product) -> InlineKeyboardMarkup:
        """Cria teclado inline com botão de acesso à oferta."""
        buttons = []

        # Botão principal de acesso
        button_text = "🛒 Abrir Oferta"
        if product.coupon_code:
            button_text = "🎟️ Resgatar Cupom"

        buttons.append([
            InlineKeyboardButton(text=button_text, url=product.link)
        ])

        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def send_admin_message(self, text: str) -> None:
        """Envia mensagem para o administrador."""
        if not self._bot or not settings.admin_id:
            return

        try:
            await self._bot.send_message(
                chat_id=settings.admin_id,
                text=text,
            )
        except Exception as e:
            logger.error(f"Erro ao enviar msg para admin: {e}")

    async def close(self) -> None:
        """Fecha a sessão do bot."""
        if self._bot:
            await self._bot.session.close()
            logger.info("Sessao do bot encerrada")

    @property
    def bot(self) -> Optional[Bot]:
        return self._bot

    @property
    def dispatcher(self) -> Optional[Dispatcher]:
        return self._dp

    @property
    def stats(self) -> dict:
        return {
            "messages_sent": self._messages_sent,
            "errors": self._errors,
        }
