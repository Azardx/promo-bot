"""
Ponto de entrada principal do PromoBot.

Inicializa todos os componentes, configura o scheduler assíncrono
e coordena a execução do bot com monitoramento de saúde.

CORREÇÕES v2.2:
- Ciclo de coleta com timeout global para evitar travamento
- Polling isolado com tratamento robusto de erros de rede
- Monitor loop não-bloqueante com proteção contra stall
- Comandos admin expandidos: pause, resume, scrapers, recent,
  clearcache, broadcast, resetdb, config, blacklist, uptime,
  test, interval, enable, disable
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

    # Timeout máximo para um ciclo de coleta completo (scrapers + envio)
    CYCLE_TIMEOUT = 180  # 3 minutos max por ciclo

    def __init__(self):
        self._logger = get_logger("main")
        self._running = False
        self._paused = False
        self._start_time = 0.0
        self._scrape_interval = settings.scrape_interval

        # Componentes (inicializados em start())
        self._db: Database | None = None
        self._cache: TTLCache | None = None
        self._proxy_manager: ProxyManager | None = None
        self._http_client: HttpClient | None = None
        self._engine: PromoEngine | None = None
        self._telegram: TelegramService | None = None

        # Runtime blacklist (adicionada via comando, não persiste)
        self._runtime_blacklist: list[str] = []

        # Scrapers habilitados em runtime (permite enable/disable dinâmico)
        self._enabled_scrapers: list[str] = list(settings.enabled_scrapers)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    async def start(self) -> None:
        """Inicializa e executa o bot."""
        self._start_time = time.time()
        self._logger.info("=" * 60)
        self._logger.info("  PromoBot v2.2 - Iniciando sistema...")
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

            # Registra handlers administrativos
            self._register_admin_handlers(dp)

            self._running = True
            self._logger.info("Todos os componentes inicializados com sucesso")
            self._logger.info(f"Destino: {settings.target_chat_id}")
            self._logger.info(f"Intervalo de coleta: {self._scrape_interval}s")
            self._logger.info(
                f"Scrapers habilitados: {', '.join(self._enabled_scrapers)}"
            )

            # Inicia tasks em background
            asyncio.create_task(self._monitor_loop())
            asyncio.create_task(self._cache_cleanup_loop())
            asyncio.create_task(self._db_cleanup_loop())
            asyncio.create_task(self._watchdog_loop())

            # Notifica admin
            scrapers_list = ", ".join(self._enabled_scrapers)
            await self._telegram.send_admin_message(
                "✅ <b>PromoBot v2.2 iniciado com sucesso!</b>\n\n"
                f"🤖 Scrapers: {len(self._enabled_scrapers)}\n"
                f"📋 Ativos: {scrapers_list}\n"
                f"⏱️ Intervalo: {self._scrape_interval}s\n"
                f"🎯 Destino: <code>{settings.target_chat_id}</code>\n\n"
                f"📋 Use /help para ver todos os comandos"
            )

            # Inicia polling do Telegram (bloqueante) com tratamento robusto
            self._logger.info("Bot iniciado e escutando mensagens...")
            await dp.start_polling(
                bot,
                polling_timeout=30,
                handle_as_tasks=True,
            )

        except KeyboardInterrupt:
            self._logger.info("Encerramento solicitado pelo usuario")
        except Exception as e:
            self._logger.critical(f"Erro fatal: {e}", exc_info=True)
        finally:
            await self._shutdown()

    def _register_admin_handlers(self, dp) -> None:
        """Registra todos os handlers administrativos."""
        from aiogram import types
        from aiogram.filters import Command
        from promo_bot.scrapers import SCRAPER_REGISTRY, _register_scrapers

        # Garante que o registry está populado
        if not SCRAPER_REGISTRY:
            _register_scrapers()

        # ------------------------------------------------------------------
        # /help — Lista de comandos
        # ------------------------------------------------------------------
        @dp.message(Command("help"))
        async def cmd_help(message: types.Message) -> None:
            """Handler do comando /help."""
            is_admin = message.from_user.id == settings.admin_id
            text = (
                "ℹ️ <b>Comandos disponíveis:</b>\n\n"
                "👤 <b>Comandos gerais:</b>\n"
                "/start — Mensagem de boas-vindas\n"
                "/help — Esta mensagem de ajuda\n"
            )
            if is_admin:
                text += (
                    "\n🔧 <b>Comandos de administração:</b>\n"
                    "/stats — Estatísticas completas do bot\n"
                    "/health — Status de saúde do sistema\n"
                    "/uptime — Tempo de atividade do bot\n"
                    "/scrapers — Status de cada scraper (24h)\n"
                    "/recent — Últimas 10 promoções enviadas\n"
                    "/config — Configuração atual do bot\n"
                    "\n⚡ <b>Controle de execução:</b>\n"
                    "/force — Forçar ciclo de coleta imediato\n"
                    "/pause — Pausar coleta automática\n"
                    "/resume — Retomar coleta automática\n"
                    "/interval [seg] — Ver/alterar intervalo de coleta\n"
                    "\n🔌 <b>Gerenciamento de scrapers:</b>\n"
                    "/enable [nome] — Ativar um scraper\n"
                    "/disable [nome] — Desativar um scraper\n"
                    "/test [nome] — Testar um scraper específico\n"
                    "/available — Listar scrapers disponíveis\n"
                    "\n🗃️ <b>Manutenção:</b>\n"
                    "/clearcache — Limpar cache de deduplicação\n"
                    "/resetdb — Limpar promoções antigas do banco\n"
                    "/blacklist [palavra] — Adicionar palavra à blacklist\n"
                    "/unblacklist [palavra] — Remover da blacklist\n"
                    "/showblacklist — Mostrar blacklist atual\n"
                    "\n📢 <b>Comunicação:</b>\n"
                    "/broadcast [msg] — Enviar mensagem ao canal\n"
                )
            else:
                text += "\n💡 As promoções são coletadas e enviadas automaticamente."

            await message.answer(text)

        # ------------------------------------------------------------------
        # /force — Forçar ciclo de coleta
        # ------------------------------------------------------------------
        @dp.message(Command("force"))
        async def cmd_force(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            if self._paused:
                await message.answer("⏸️ Bot está pausado. Use /resume primeiro.")
                return
            await message.answer("🔄 Forçando ciclo de coleta...")
            asyncio.create_task(self._run_single_cycle())

        # ------------------------------------------------------------------
        # /pause — Pausar coleta
        # ------------------------------------------------------------------
        @dp.message(Command("pause"))
        async def cmd_pause(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            self._paused = True
            await message.answer(
                "⏸️ <b>Bot pausado.</b>\n\n"
                "Os ciclos de coleta estão suspensos.\n"
                "Use /resume para retomar."
            )

        # ------------------------------------------------------------------
        # /resume — Retomar coleta
        # ------------------------------------------------------------------
        @dp.message(Command("resume"))
        async def cmd_resume(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            self._paused = False
            await message.answer(
                "▶️ <b>Bot retomado!</b>\n\n"
                "Os ciclos de coleta foram reativados."
            )

        # ------------------------------------------------------------------
        # /scrapers — Status dos scrapers
        # ------------------------------------------------------------------
        @dp.message(Command("scrapers"))
        async def cmd_scrapers(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            try:
                scraper_stats = await self._db.get_scraper_stats(hours=24)

                lines = ["📊 <b>Status dos Scrapers (24h)</b>\n"]

                # Mostra scrapers habilitados e seus stats
                for name in self._enabled_scrapers:
                    stat = next(
                        (s for s in scraper_stats if s.get("scraper_name") == name),
                        None,
                    )
                    if stat:
                        runs = stat.get("runs", 0)
                        found = stat.get("total_found", 0)
                        errors = stat.get("total_errors", 0)
                        avg_dur = stat.get("avg_duration", 0)
                        status_icon = (
                            "✅" if errors == 0
                            else "⚠️" if errors < runs
                            else "❌"
                        )
                        lines.append(
                            f"{status_icon} <b>{name}</b>\n"
                            f"   Execuções: {runs} | Encontrados: {found}\n"
                            f"   Erros: {errors} | Tempo médio: {avg_dur:.1f}s"
                        )
                    else:
                        lines.append(f"⏳ <b>{name}</b>\n   Sem dados ainda")

                # Mostra scrapers desabilitados
                available = list(SCRAPER_REGISTRY.keys())
                disabled = [n for n in available if n not in self._enabled_scrapers]
                if disabled:
                    lines.append(f"\n🔌 <b>Desabilitados:</b> {', '.join(disabled)}")

                await message.answer("\n".join(lines))
            except Exception as e:
                await message.answer(f"❌ Erro: {e}")

        # ------------------------------------------------------------------
        # /recent — Últimas promoções enviadas
        # ------------------------------------------------------------------
        @dp.message(Command("recent"))
        async def cmd_recent(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            try:
                recent = await self._db.get_recent_promos(limit=10)
                if not recent:
                    await message.answer("📭 Nenhuma promoção enviada ainda.")
                    return

                lines = ["📋 <b>Últimas 10 promoções enviadas:</b>\n"]
                for i, p in enumerate(recent, 1):
                    title = p.get("title", "?")[:50]
                    store = p.get("store", "?")
                    price = p.get("price")
                    price_str = f"R${price:.2f}" if price else "N/A"
                    lines.append(f"{i}. [{store}] {title}\n   💰 {price_str}")
                await message.answer("\n".join(lines))
            except Exception as e:
                await message.answer(f"❌ Erro: {e}")

        # ------------------------------------------------------------------
        # /clearcache — Limpar cache
        # ------------------------------------------------------------------
        @dp.message(Command("clearcache"))
        async def cmd_clear_cache(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            if self._cache:
                old_size = self._cache.size
                await self._cache.clear()
                await message.answer(
                    f"🗑️ Cache limpo!\n"
                    f"Entradas removidas: <b>{old_size}</b>"
                )

        # ------------------------------------------------------------------
        # /broadcast — Enviar mensagem ao canal
        # ------------------------------------------------------------------
        @dp.message(Command("broadcast"))
        async def cmd_broadcast(message: types.Message) -> None:
            """Envia mensagem personalizada para o canal."""
            if message.from_user.id != settings.admin_id:
                return
            text = message.text.replace("/broadcast", "", 1).strip()
            if not text:
                await message.answer(
                    "📢 <b>Uso:</b> /broadcast <i>sua mensagem aqui</i>\n\n"
                    "A mensagem será enviada para o canal/grupo configurado.\n"
                    "Suporta formatação HTML."
                )
                return
            try:
                target = settings.target_chat_id
                await self._telegram.bot.send_message(
                    chat_id=target,
                    text=text,
                    parse_mode="HTML",
                )
                await message.answer("✅ Mensagem enviada para o canal!")
            except Exception as e:
                await message.answer(f"❌ Erro ao enviar: {e}")

        # ------------------------------------------------------------------
        # /resetdb — Limpar banco de dados
        # ------------------------------------------------------------------
        @dp.message(Command("resetdb"))
        async def cmd_reset_db(message: types.Message) -> None:
            """Limpa promoções antigas do banco."""
            if message.from_user.id != settings.admin_id:
                return
            try:
                deleted = await self._db.cleanup_old_promos(max_age_days=0)
                await message.answer(
                    f"🗑️ Banco de dados limpo!\n"
                    f"Promoções removidas: <b>{deleted}</b>"
                )
            except Exception as e:
                await message.answer(f"❌ Erro: {e}")

        # ------------------------------------------------------------------
        # /config — Configuração atual
        # ------------------------------------------------------------------
        @dp.message(Command("config"))
        async def cmd_config(message: types.Message) -> None:
            """Mostra configuração atual."""
            if message.from_user.id != settings.admin_id:
                return
            scrapers_str = ", ".join(self._enabled_scrapers)
            await message.answer(
                "⚙️ <b>Configuração Atual</b>\n\n"
                f"⏱️ Intervalo: <b>{self._scrape_interval}s</b>\n"
                f"📤 Max por ciclo: <b>{settings.max_promos_per_cycle}</b>\n"
                f"💰 Preço min: <b>R${settings.min_price:.2f}</b>\n"
                f"💰 Preço max: <b>R${settings.max_price:.2f}</b>\n"
                f"🚫 Palavras bloqueadas: <b>{len(settings.blocked_keywords) + len(self._runtime_blacklist)}</b>\n"
                f"⭐ Keywords prioritárias: <b>{len(settings.priority_keywords)}</b>\n"
                f"🤖 Scrapers: <b>{scrapers_str}</b>\n"
                f"🔄 Delay broadcast: <b>{settings.broadcast_delay}s</b>\n"
                f"🌐 Proxies: <b>{'Sim' if settings.use_proxies else 'Não'}</b>\n"
                f"⏸️ Pausado: <b>{'Sim' if self._paused else 'Não'}</b>"
            )

        # ------------------------------------------------------------------
        # /uptime — Tempo de atividade
        # ------------------------------------------------------------------
        @dp.message(Command("uptime"))
        async def cmd_uptime(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            uptime = round(time.time() - self._start_time)
            days = uptime // 86400
            hours = (uptime % 86400) // 3600
            minutes = (uptime % 3600) // 60
            seconds = uptime % 60

            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0:
                parts.append(f"{hours}h")
            if minutes > 0:
                parts.append(f"{minutes}m")
            parts.append(f"{seconds}s")

            await message.answer(
                f"⏱️ <b>Uptime:</b> {' '.join(parts)}\n"
                f"🔄 Ciclos executados: <b>{self._engine._cycle_count if self._engine else 0}</b>\n"
                f"📤 Promos enviadas: <b>{self._engine._total_sent if self._engine else 0}</b>\n"
                f"⏸️ Status: <b>{'Pausado' if self._paused else 'Ativo'}</b>"
            )

        # ------------------------------------------------------------------
        # /health — Status de saúde
        # ------------------------------------------------------------------
        @dp.message(Command("health"))
        async def cmd_health(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return

            cache_size = self._cache.size if self._cache else 0
            cache_max = self._cache._max_size if self._cache else 0
            http_stats = self._http_client.stats if self._http_client else {}

            proxy_status = "Desabilitado"
            if self._proxy_manager and self._proxy_manager.is_enabled:
                proxy_status = f"{self._proxy_manager.available_count} disponíveis"

            await message.answer(
                "🏥 <b>Status de Saúde do Sistema</b>\n\n"
                f"🤖 Bot: <b>{'⏸️ Pausado' if self._paused else '✅ Online'}</b>\n"
                f"📤 Mensagens: <b>{self._telegram._messages_sent}</b>\n"
                f"🖼️ Fotos: <b>{self._telegram._photos_sent}</b>\n"
                f"❌ Erros Telegram: <b>{self._telegram._errors}</b>\n"
                f"🎯 Destino: <code>{settings.target_chat_id}</code>\n\n"
                f"💾 <b>Cache:</b> {cache_size}/{cache_max}\n"
                f"🌐 <b>HTTP:</b> {http_stats.get('total_requests', 0)} reqs, "
                f"{http_stats.get('total_errors', 0)} erros\n"
                f"🔄 <b>Proxies:</b> {proxy_status}\n"
                f"🤖 <b>Scrapers:</b> {len(self._enabled_scrapers)} ativos"
            )

        # ------------------------------------------------------------------
        # /interval — Ver/alterar intervalo de coleta
        # ------------------------------------------------------------------
        @dp.message(Command("interval"))
        async def cmd_interval(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            args = message.text.replace("/interval", "", 1).strip()
            if not args:
                await message.answer(
                    f"⏱️ <b>Intervalo atual:</b> {self._scrape_interval}s\n\n"
                    f"Para alterar: /interval [segundos]\n"
                    f"Exemplo: /interval 60"
                )
                return

            try:
                new_interval = int(args)
                if new_interval < 30:
                    await message.answer("⚠️ Intervalo mínimo: 30 segundos")
                    return
                if new_interval > 3600:
                    await message.answer("⚠️ Intervalo máximo: 3600 segundos (1h)")
                    return

                old = self._scrape_interval
                self._scrape_interval = new_interval
                await message.answer(
                    f"✅ Intervalo alterado: <b>{old}s</b> → <b>{new_interval}s</b>\n\n"
                    f"⚠️ A mudança é temporária e será revertida ao reiniciar."
                )
            except ValueError:
                await message.answer("❌ Valor inválido. Use um número inteiro.")

        # ------------------------------------------------------------------
        # /enable — Ativar scraper
        # ------------------------------------------------------------------
        @dp.message(Command("enable"))
        async def cmd_enable(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            args = message.text.replace("/enable", "", 1).strip().lower()
            if not args:
                await message.answer(
                    "🔌 <b>Uso:</b> /enable [nome_do_scraper]\n\n"
                    f"Disponíveis: {', '.join(SCRAPER_REGISTRY.keys())}"
                )
                return

            if args not in SCRAPER_REGISTRY:
                await message.answer(
                    f"❌ Scraper '{args}' não encontrado.\n"
                    f"Disponíveis: {', '.join(SCRAPER_REGISTRY.keys())}"
                )
                return

            if args in self._enabled_scrapers:
                await message.answer(f"ℹ️ Scraper '{args}' já está ativo.")
                return

            self._enabled_scrapers.append(args)
            # Reinicializa o engine com os novos scrapers
            await self._reinitialize_engine()
            await message.answer(
                f"✅ Scraper <b>{args}</b> ativado!\n\n"
                f"Ativos: {', '.join(self._enabled_scrapers)}"
            )

        # ------------------------------------------------------------------
        # /disable — Desativar scraper
        # ------------------------------------------------------------------
        @dp.message(Command("disable"))
        async def cmd_disable(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            args = message.text.replace("/disable", "", 1).strip().lower()
            if not args:
                await message.answer(
                    "🔌 <b>Uso:</b> /disable [nome_do_scraper]\n\n"
                    f"Ativos: {', '.join(self._enabled_scrapers)}"
                )
                return

            if args not in self._enabled_scrapers:
                await message.answer(
                    f"❌ Scraper '{args}' não está ativo.\n"
                    f"Ativos: {', '.join(self._enabled_scrapers)}"
                )
                return

            if len(self._enabled_scrapers) <= 1:
                await message.answer("⚠️ Pelo menos um scraper deve estar ativo.")
                return

            self._enabled_scrapers.remove(args)
            await self._reinitialize_engine()
            await message.answer(
                f"🔌 Scraper <b>{args}</b> desativado!\n\n"
                f"Ativos: {', '.join(self._enabled_scrapers)}"
            )

        # ------------------------------------------------------------------
        # /available — Listar scrapers disponíveis
        # ------------------------------------------------------------------
        @dp.message(Command("available"))
        async def cmd_available(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return

            lines = ["🔌 <b>Scrapers Disponíveis:</b>\n"]
            for name in sorted(SCRAPER_REGISTRY.keys()):
                status = "✅ Ativo" if name in self._enabled_scrapers else "⭕ Inativo"
                lines.append(f"  {status} — <b>{name}</b>")

            lines.append(
                f"\n📊 Total: {len(SCRAPER_REGISTRY)} disponíveis, "
                f"{len(self._enabled_scrapers)} ativos"
            )
            await message.answer("\n".join(lines))

        # ------------------------------------------------------------------
        # /test — Testar scraper específico
        # ------------------------------------------------------------------
        @dp.message(Command("test"))
        async def cmd_test(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            args = message.text.replace("/test", "", 1).strip().lower()
            if not args:
                await message.answer(
                    "🧪 <b>Uso:</b> /test [nome_do_scraper]\n\n"
                    f"Disponíveis: {', '.join(SCRAPER_REGISTRY.keys())}"
                )
                return

            if args not in SCRAPER_REGISTRY:
                await message.answer(
                    f"❌ Scraper '{args}' não encontrado.\n"
                    f"Disponíveis: {', '.join(SCRAPER_REGISTRY.keys())}"
                )
                return

            await message.answer(f"🧪 Testando scraper <b>{args}</b>...")

            try:
                scraper_class = SCRAPER_REGISTRY[args]
                scraper = scraper_class(self._http_client, self._cache)

                start = time.time()
                result = await asyncio.wait_for(scraper.run(), timeout=60)
                elapsed = round(time.time() - start, 2)

                if result.success and result.products:
                    lines = [
                        f"✅ <b>Teste do {args} concluído!</b>\n",
                        f"⏱️ Duração: {elapsed}s",
                        f"📦 Produtos encontrados: {result.count}",
                        "",
                        "<b>Primeiros 5 produtos:</b>",
                    ]
                    for i, p in enumerate(result.products[:5], 1):
                        price_str = f"R${p.price:.2f}" if p.price else "N/A"
                        coupon_str = f" | 🎟️ {p.coupon_code}" if p.coupon_code else ""
                        img_str = " | 🖼️" if p.image_url else ""
                        lines.append(
                            f"{i}. {p.title[:60]}\n"
                            f"   💰 {price_str}{coupon_str}{img_str}"
                        )
                    await message.answer("\n".join(lines))
                elif result.success:
                    await message.answer(
                        f"⚠️ <b>{args}</b>: Sucesso mas 0 produtos encontrados.\n"
                        f"⏱️ Duração: {elapsed}s"
                    )
                else:
                    error_str = ", ".join(result.errors) if result.errors else "Desconhecido"
                    await message.answer(
                        f"❌ <b>{args}</b>: Falhou!\n"
                        f"⏱️ Duração: {elapsed}s\n"
                        f"Erro: {error_str}"
                    )

            except asyncio.TimeoutError:
                await message.answer(f"⏰ Scraper <b>{args}</b> excedeu o timeout de 60s")
            except Exception as e:
                await message.answer(f"❌ Erro ao testar {args}: {e}")

        # ------------------------------------------------------------------
        # /blacklist — Adicionar palavra à blacklist
        # ------------------------------------------------------------------
        @dp.message(Command("blacklist"))
        async def cmd_blacklist(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            args = message.text.replace("/blacklist", "", 1).strip()
            if not args:
                await message.answer(
                    "🚫 <b>Uso:</b> /blacklist [palavra]\n\n"
                    "Adiciona uma palavra à lista de bloqueio.\n"
                    "Produtos com essa palavra no título serão filtrados.\n\n"
                    "Use /showblacklist para ver a lista atual."
                )
                return

            word = args.lower().strip()
            if word in self._runtime_blacklist or word in [k.lower() for k in settings.blocked_keywords]:
                await message.answer(f"ℹ️ '{word}' já está na blacklist.")
                return

            self._runtime_blacklist.append(word)
            # Atualiza o filtro do engine
            if self._engine and self._engine._filter:
                self._engine._filter._blocked_keywords.append(word)

            await message.answer(
                f"🚫 Palavra '<b>{word}</b>' adicionada à blacklist!\n\n"
                f"⚠️ A mudança é temporária e será revertida ao reiniciar.\n"
                f"Para permanente, adicione ao .env em BLOCKED_KEYWORDS."
            )

        # ------------------------------------------------------------------
        # /unblacklist — Remover palavra da blacklist
        # ------------------------------------------------------------------
        @dp.message(Command("unblacklist"))
        async def cmd_unblacklist(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return
            args = message.text.replace("/unblacklist", "", 1).strip()
            if not args:
                await message.answer("🚫 <b>Uso:</b> /unblacklist [palavra]")
                return

            word = args.lower().strip()
            removed = False

            if word in self._runtime_blacklist:
                self._runtime_blacklist.remove(word)
                removed = True

            if self._engine and self._engine._filter:
                kw_list = self._engine._filter._blocked_keywords
                if word in kw_list:
                    kw_list.remove(word)
                    removed = True

            if removed:
                await message.answer(f"✅ Palavra '<b>{word}</b>' removida da blacklist!")
            else:
                await message.answer(f"❌ Palavra '{word}' não encontrada na blacklist.")

        # ------------------------------------------------------------------
        # /showblacklist — Mostrar blacklist
        # ------------------------------------------------------------------
        @dp.message(Command("showblacklist"))
        async def cmd_show_blacklist(message: types.Message) -> None:
            if message.from_user.id != settings.admin_id:
                return

            env_words = settings.blocked_keywords
            runtime_words = self._runtime_blacklist

            lines = ["🚫 <b>Blacklist Atual:</b>\n"]

            if env_words:
                lines.append("<b>Permanentes (.env):</b>")
                lines.append(", ".join(env_words))

            if runtime_words:
                lines.append("\n<b>Temporárias (runtime):</b>")
                lines.append(", ".join(runtime_words))

            if not env_words and not runtime_words:
                lines.append("Nenhuma palavra bloqueada.")

            lines.append(f"\nTotal: <b>{len(env_words) + len(runtime_words)}</b> palavras")
            await message.answer("\n".join(lines))

        # ------------------------------------------------------------------
        # /start — Boas-vindas
        # ------------------------------------------------------------------
        @dp.message(Command("start"))
        async def cmd_start(message: types.Message) -> None:
            scrapers_text = "\n".join(
                f"  {'✅' if name in self._enabled_scrapers else '⭕'} {name.capitalize()}"
                for name in sorted(SCRAPER_REGISTRY.keys())
            )
            await message.answer(
                "👋 <b>Olá! Eu sou o PromoBot v2.2.</b>\n\n"
                "Eu monitoro automaticamente as melhores promoções de:\n"
                f"{scrapers_text}\n\n"
                "As ofertas são enviadas automaticamente no canal/grupo configurado.\n\n"
                "📊 Use /stats para ver estatísticas (admin).\n"
                "ℹ️ Use /help para mais informações."
            )

    async def _reinitialize_engine(self) -> None:
        """Reinicializa o engine com os scrapers atuais."""
        if self._engine and self._http_client and self._cache:
            from promo_bot.scrapers import get_enabled_scrapers
            self._engine._scrapers = get_enabled_scrapers(
                self._enabled_scrapers, self._http_client, self._cache
            )
            self._logger.info(
                f"Engine reinicializado com scrapers: {', '.join(self._enabled_scrapers)}"
            )

    async def _monitor_loop(self) -> None:
        """Loop principal de monitoramento e coleta."""
        self._logger.info("Monitor de promocoes iniciado")

        # Aguarda um pouco antes do primeiro ciclo
        await asyncio.sleep(5)

        while self._running:
            if not self._paused:
                try:
                    # Executa ciclo com timeout global para evitar travamento
                    await asyncio.wait_for(
                        self._run_single_cycle(),
                        timeout=self.CYCLE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    self._logger.error(
                        f"Ciclo de coleta excedeu timeout de {self.CYCLE_TIMEOUT}s! "
                        "Abortando ciclo e continuando..."
                    )
                except Exception as e:
                    self._logger.error(f"Erro no ciclo de monitoramento: {e}", exc_info=True)
            else:
                self._logger.debug("Bot pausado, pulando ciclo de coleta")

            # Aguarda intervalo configurado (usa o valor runtime)
            await asyncio.sleep(self._scrape_interval)

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
                if not self._running:
                    break

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
                    f"{' | PAUSADO' if self._paused else ''}"
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
        stats["paused"] = self._paused
        stats["scrape_interval"] = self._scrape_interval
        stats["enabled_scrapers"] = self._enabled_scrapers

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
    logger.info("PromoBot v2.2 - Bot de Promocoes para Telegram")

    # Cria e executa a aplicação
    app = PromoBot()

    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        logger.info("Encerrado pelo usuario")


if __name__ == "__main__":
    main()
