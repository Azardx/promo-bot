"""
Ponto de entrada principal do PromoBot.

Inicializa todos os componentes, configura o scheduler assíncrono
e coordena a execução do bot com monitoramento de saúde.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from pathlib import Path

# Adiciona o diretório pai ao path para imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo_bot.config import settings
from promo_bot.database.db import Database
from promo_bot.services.engine import PromoEngine
from promo_bot.services.telegram import TelegramService
from promo_bot.utils.cache import TTLCache
from promo_bot.utils.http_client import HttpClient
from promo_bot.utils.logger import setup_logging, get_logger
from promo_bot.utils.proxy import ProxyManager


# ---------------------------------------------------------------------------
# Classe principal da aplicação
# ---------------------------------------------------------------------------
class PromoBot:
    """
    Aplicação principal do PromoBot.

    Orquestra a inicialização de todos os componentes, o ciclo
    de coleta de promoções e o monitoramento de saúde do sistema.
    """

    def __init__(self):
        self._logger = get_logger("main")
        self._running = False
        self._start_time = 0.0

        # Componentes (inicializados em start())
        self._db: Database | None = None
        self._cache: TTLCache | None = None
        self._proxy_manager: ProxyManager | None = None
        self._http_client: HttpClient | None = None
        self._engine: PromoEngine | None = None
        self._telegram: TelegramService | None = None

    async def start(self) -> None:
        """Inicializa e executa o bot."""
        self._start_time = time.time()
        self._logger.info("=" * 60)
        self._logger.info("  PromoBot - Iniciando sistema...")
        self._logger.info("=" * 60)

        # Valida configurações
        errors = settings.validate()
        if errors:
            for error in errors:
                self._logger.error(f"Configuracao: {error}")
            self._logger.critical("Configuracoes invalidas. Abortando.")
            sys.exit(1)

        try:
            # 1. Banco de dados
            self._db = Database(settings.db_path)
            await self._db.connect()

            # 2. Cache
            self._cache = TTLCache(
                max_size=settings.cache_max_size,
                ttl=settings.cache_ttl,
            )

            # 3. Proxy Manager
            self._proxy_manager = ProxyManager(
                proxy_file=settings.proxy_list_file,
                enabled=settings.use_proxies,
            )
            if self._proxy_manager.is_enabled:
                self._logger.info(
                    f"Proxies habilitados: {self._proxy_manager.available_count} disponiveis"
                )
            else:
                self._logger.info("Proxies desabilitados (modo direto)")

            # 4. HTTP Client
            self._http_client = HttpClient(
                proxy_manager=self._proxy_manager,
                timeout=settings.http_timeout,
                max_retries=settings.max_retries,
                rate_limit_delay=settings.rate_limit_delay,
            )

            # 5. Promo Engine
            self._engine = PromoEngine(
                database=self._db,
                http_client=self._http_client,
                cache=self._cache,
            )

            # 6. Telegram Service
            self._telegram = TelegramService()
            bot, dp = self._telegram.initialize(
                stats_callback=self._get_stats,
            )

            # Registra handler de force cycle
            self._register_force_handler(dp)

            self._running = True
            self._logger.info("Todos os componentes inicializados com sucesso")
            self._logger.info(f"Destino: {settings.target_chat_id}")
            self._logger.info(f"Intervalo de coleta: {settings.scrape_interval}s")
            self._logger.info(
                f"Scrapers habilitados: {', '.join(settings.enabled_scrapers)}"
            )

            # Inicia tasks em background
            asyncio.create_task(self._monitor_loop())
            asyncio.create_task(self._cache_cleanup_loop())
            asyncio.create_task(self._db_cleanup_loop())
            asyncio.create_task(self._watchdog_loop())

            # Notifica admin
            await self._telegram.send_admin_message(
                "✅ <b>PromoBot iniciado com sucesso!</b>\n\n"
                f"🤖 Scrapers: {len(settings.enabled_scrapers)}\n"
                f"⏱️ Intervalo: {settings.scrape_interval}s\n"
                f"🎯 Destino: <code>{settings.target_chat_id}</code>"
            )

            # Inicia polling do Telegram (bloqueante)
            self._logger.info("Bot iniciado e escutando mensagens...")
            await dp.start_polling(bot)

        except KeyboardInterrupt:
            self._logger.info("Encerramento solicitado pelo usuario")
        except Exception as e:
            self._logger.critical(f"Erro fatal: {e}", exc_info=True)
        finally:
            await self._shutdown()

    def _register_force_handler(self, dp) -> None:
        """Registra handler para forçar ciclo de coleta."""
        from aiogram import types
        from aiogram.filters import Command

        @dp.message(Command("force"))
        async def cmd_force(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            await message.answer("🔄 Forçando ciclo de coleta...")
            asyncio.create_task(self._run_single_cycle())

    async def _monitor_loop(self) -> None:
        """Loop principal de monitoramento e coleta."""
        self._logger.info("Monitor de promocoes iniciado")

        # Aguarda um pouco antes do primeiro ciclo
        await asyncio.sleep(5)

        while self._running:
            try:
                await self._run_single_cycle()
            except Exception as e:
                self._logger.error(f"Erro no ciclo de monitoramento: {e}", exc_info=True)

            # Aguarda intervalo configurado
            await asyncio.sleep(settings.scrape_interval)

    async def _run_single_cycle(self) -> None:
        """Executa um único ciclo de coleta e envio."""
        start = time.time()

        try:
            # Coleta e processa promoções
            products = await self._engine.run_cycle()

            if not products:
                self._logger.info("Nenhuma nova promocao para enviar")
                return

            # Envia cada promoção
            sent_count = 0
            for product in products:
                success = await self._telegram.send_promo(product)
                if success:
                    await self._engine.register_sent(product)
                    sent_count += 1

                # Delay entre envios para respeitar rate limit
                await asyncio.sleep(settings.broadcast_delay)

            elapsed = round(time.time() - start, 2)
            self._logger.info(
                f"Ciclo concluido: {sent_count}/{len(products)} promos enviadas "
                f"em {elapsed}s"
            )

        except Exception as e:
            self._logger.error(f"Erro ao executar ciclo: {e}", exc_info=True)

    async def _cache_cleanup_loop(self) -> None:
        """Limpa entradas expiradas do cache periodicamente."""
        while self._running:
            await asyncio.sleep(settings.cache_ttl // 2)
            try:
                removed = await self._cache.cleanup_expired()
                if removed > 0:
                    self._logger.debug(f"Cache cleanup: {removed} entradas removidas")
            except Exception as e:
                self._logger.error(f"Erro no cache cleanup: {e}")

    async def _db_cleanup_loop(self) -> None:
        """Limpa promoções antigas do banco de dados periodicamente."""
        while self._running:
            await asyncio.sleep(86400)  # Uma vez por dia
            try:
                deleted = await self._db.cleanup_old_promos(max_age_days=30)
                if deleted > 0:
                    self._logger.info(f"DB cleanup: {deleted} promos antigas removidas")
            except Exception as e:
                self._logger.error(f"Erro no DB cleanup: {e}")

    async def _watchdog_loop(self) -> None:
        """Monitora a saúde do sistema periodicamente."""
        while self._running:
            await asyncio.sleep(settings.watchdog_interval)
            try:
                uptime = round(time.time() - self._start_time)
                hours = uptime // 3600
                minutes = (uptime % 3600) // 60

                stats = await self._get_stats()
                self._logger.info(
                    f"Watchdog: uptime={hours}h{minutes}m | "
                    f"ciclos={stats.get('cycles', 0)} | "
                    f"enviados={stats.get('total_sent', 0)} | "
                    f"cache={self._cache.size}"
                )
            except Exception as e:
                self._logger.error(f"Erro no watchdog: {e}")

    async def _get_stats(self) -> dict:
        """Retorna estatísticas completas do sistema."""
        stats = {}
        if self._engine:
            stats.update(self._engine.stats)
        if self._telegram:
            stats["telegram"] = self._telegram.stats

        uptime = round(time.time() - self._start_time)
        stats["uptime_seconds"] = uptime
        stats["uptime_human"] = f"{uptime // 3600}h {(uptime % 3600) // 60}m"

        return stats

    async def _shutdown(self) -> None:
        """Encerra graciosamente todos os componentes."""
        self._logger.info("Encerrando PromoBot...")
        self._running = False

        if self._telegram:
            try:
                await self._telegram.send_admin_message(
                    "⚠️ <b>PromoBot encerrado.</b>"
                )
            except Exception:
                pass
            await self._telegram.close()

        if self._db:
            await self._db.close()

        self._logger.info("PromoBot encerrado com sucesso")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Função principal de entrada."""
    # Configura logging
    logger = setup_logging(settings.log_level)
    logger.info("PromoBot v2.0 - Bot de Promocoes para Telegram")

    # Cria e executa a aplicação
    app = PromoBot()

    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        logger.info("Encerrado pelo usuario")


if __name__ == "__main__":
    main()
