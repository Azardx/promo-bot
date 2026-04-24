"""
Sistema de logging profissional do PromoBot.

Configura logs estruturados com rotação de arquivos, formatação colorida
no console e níveis configuráveis via variável de ambiente.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Diretório de logs
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)


class ColorFormatter(logging.Formatter):
    """Formatter com cores ANSI para saída no terminal."""

    COLORS = {
        logging.DEBUG: "\033[36m",     # Ciano
        logging.INFO: "\033[32m",      # Verde
        logging.WARNING: "\033[33m",   # Amarelo
        logging.ERROR: "\033[31m",     # Vermelho
        logging.CRITICAL: "\033[1;31m",  # Vermelho negrito
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, self.RESET)
        record.levelname = f"{color}{record.levelname:<8}{self.RESET}"
        return super().format(record)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """
    Configura o sistema de logging global.

    Args:
        level: Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    Returns:
        Logger raiz configurado.
    """
    root_logger = logging.getLogger("promo_bot")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove handlers existentes para evitar duplicação
    root_logger.handlers.clear()

    # --- Formato ---
    fmt = "%(asctime)s | %(levelname)s | %(name)-25s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # --- Console Handler (colorido) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColorFormatter(fmt, datefmt=date_fmt))
    root_logger.addHandler(console_handler)

    # --- File Handler (rotação) ---
    file_handler = RotatingFileHandler(
        _LOG_DIR / "promo_bot.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
    root_logger.addHandler(file_handler)

    # --- Error File Handler (apenas erros) ---
    error_handler = RotatingFileHandler(
        _LOG_DIR / "errors.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
    root_logger.addHandler(error_handler)

    # Silencia loggers muito verbosos
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger filho do logger principal."""
    return logging.getLogger(f"promo_bot.{name}")
