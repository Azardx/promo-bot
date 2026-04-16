"""
Módulo de configuração central do PromoBot.

Carrega todas as variáveis de ambiente do arquivo .env e fornece
constantes de configuração para todos os módulos do sistema.

CORREÇÕES v2.3:
- Adição de Terabyte e Mercado Livre aos scrapers padrão
- Ajuste de limites de preço e keywords
- Configurações de rate limit e delay (Qualidade > Quantidade)
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Carrega .env do diretório raiz do projeto
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(_ENV_FILE)


def _get_env(key: str, default: str = "") -> str:
    """Retorna variável de ambiente ou valor padrão."""
    return os.getenv(key, default).strip()


def _get_int(key: str, default: int = 0) -> int:
    """Retorna variável de ambiente como inteiro."""
    try:
        return int(_get_env(key, str(default)))
    except ValueError:
        return default


def _get_float(key: str, default: float = 0.0) -> float:
    """Retorna variável de ambiente como float."""
    try:
        return float(_get_env(key, str(default)))
    except ValueError:
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    """Retorna variável de ambiente como booleano."""
    return _get_env(key, str(default)).lower() in ("true", "1", "yes", "sim")


def _get_list(key: str, default: str = "") -> list[str]:
    """Retorna variável de ambiente como lista separada por vírgulas."""
    raw = _get_env(key, default)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


# ===========================================================================
# Dataclass de configuração — imutável após inicialização
# ===========================================================================
@dataclass(frozen=True)
class Settings:
    """Configurações globais do PromoBot."""

    # --- Telegram -----------------------------------------------------------
    bot_token: str = _get_env("BOT_TOKEN")
    admin_id: int = _get_int("ADMIN_ID")
    channel_id: str = _get_env("CHANNEL_ID")  # @canal ou -100xxxxx
    group_id: str = _get_env("GROUP_ID")       # Grupo alternativo

    # --- Scraping -----------------------------------------------------------
    scrape_interval: int = _get_int("SCRAPE_INTERVAL", 600)         # 10 min (v2.3)
    http_timeout: int = _get_int("HTTP_TIMEOUT", 30)                # 30s (v2.3)
    max_retries: int = _get_int("MAX_RETRIES", 3)
    rate_limit_delay: float = _get_float("RATE_LIMIT_DELAY", 3.0)  # 3s entre reqs (v2.3)
    max_concurrent_scrapers: int = _get_int("MAX_CONCURRENT_SCRAPERS", 3)

    # --- Proxies (opcional) -------------------------------------------------
    use_proxies: bool = _get_bool("USE_PROXIES", False)
    proxy_list_file: str = _get_env("PROXY_LIST_FILE", "proxies.txt")

    # --- Filtros ------------------------------------------------------------
    min_price: float = _get_float("MIN_PRICE", 15.0)  # Ignora miudezas (v2.3)
    max_price: float = _get_float("MAX_PRICE", 50000.0)
    blocked_keywords: list[str] = field(
        default_factory=lambda: _get_list(
            "BLOCKED_KEYWORDS",
            "capinha,película,adesivo,suporte celular,cabo usb,manual,pdf,amostra,chaveiro,meia,cueca"
        )
    )
    priority_keywords: list[str] = field(
        default_factory=lambda: _get_list(
            "PRIORITY_KEYWORDS",
            "ssd,rtx,notebook,iphone,airpods,galaxy,playstation,xbox,monitor,gpu,ryzen,intel core"
        )
    )

    # --- Banco de dados -----------------------------------------------------
    db_path: str = _get_env("DB_PATH", str(_PROJECT_ROOT / "data" / "promo_bot.db"))

    # --- Cache --------------------------------------------------------------
    cache_ttl: int = _get_int("CACHE_TTL", 86400)          # 24h (v2.3)
    cache_max_size: int = _get_int("CACHE_MAX_SIZE", 10000)

    # --- Monitoramento ------------------------------------------------------
    watchdog_interval: int = _get_int("WATCHDOG_INTERVAL", 300)  # segundos
    log_level: str = _get_env("LOG_LEVEL", "INFO").upper()

    # --- Lojas habilitadas --------------------------------------------------
    enabled_scrapers: list[str] = field(
        default_factory=lambda: _get_list(
            "ENABLED_SCRAPERS",
            "shopee,aliexpress,amazon,pelando,promobit,kabum,terabyte,mercadolivre"
        )
    )

    # --- Broadcast ----------------------------------------------------------
    broadcast_delay: float = _get_float("BROADCAST_DELAY", 3.0)  # 3s entre msgs (v2.3)
    max_promos_per_cycle: int = _get_int("MAX_PROMOS_PER_CYCLE", 10) # Top 10 (v2.3)

    @property
    def target_chat_id(self) -> str:
        """Retorna o chat_id de destino (canal tem prioridade sobre grupo)."""
        return self.channel_id or self.group_id

    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    def validate(self) -> list[str]:
        """Valida configurações essenciais e retorna lista de erros."""
        errors = []
        if not self.bot_token:
            errors.append("BOT_TOKEN não configurado no .env")
        if not self.target_chat_id:
            errors.append("CHANNEL_ID ou GROUP_ID deve ser configurado no .env")
        return errors


# ===========================================================================
# Instância global de configuração
# ===========================================================================
settings = Settings()
