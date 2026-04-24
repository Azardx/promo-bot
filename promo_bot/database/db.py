"""
Módulo de banco de dados assíncrono do PromoBot.

Utiliza aiosqlite para operações não-bloqueantes com SQLite.
Gerencia o histórico de promoções enviadas, estatísticas e metadados.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import aiosqlite

from promo_bot.utils.logger import get_logger

logger = get_logger("database")


class Database:
    """
    Gerenciador de banco de dados assíncrono para o PromoBot.

    Armazena promoções enviadas, estatísticas de scrapers e
    histórico de preços para deduplicação e análise.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Inicializa a conexão e cria as tabelas necessárias."""
        # Garante que o diretório existe
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

        # Otimizações de performance para SQLite
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA cache_size=10000")

        await self._create_tables()
        logger.info(f"Banco de dados conectado: {self._db_path}")

    async def _create_tables(self) -> None:
        """Cria as tabelas do banco de dados."""
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS promos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                price REAL,
                original_price REAL,
                discount_pct REAL,
                store TEXT NOT NULL,
                category TEXT DEFAULT '',
                sent_at REAL NOT NULL,
                score REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS scraper_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scraper_name TEXT NOT NULL,
                run_at REAL NOT NULL,
                duration_secs REAL NOT NULL,
                items_found INTEGER DEFAULT 0,
                items_sent INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ok'
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT NOT NULL,
                price REAL NOT NULL,
                recorded_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_promos_link ON promos(link);
            CREATE INDEX IF NOT EXISTS idx_promos_sent_at ON promos(sent_at);
            CREATE INDEX IF NOT EXISTS idx_promos_store ON promos(store);
            CREATE INDEX IF NOT EXISTS idx_price_history_link ON price_history(link);
            CREATE INDEX IF NOT EXISTS idx_scraper_stats_name ON scraper_stats(scraper_name);
        """)
        await self._db.commit()

    async def close(self) -> None:
        """Fecha a conexão com o banco de dados."""
        if self._db:
            await self._db.close()
            logger.info("Banco de dados desconectado")

    # -----------------------------------------------------------------------
    # Promoções
    # -----------------------------------------------------------------------

    async def promo_exists(self, link: str) -> bool:
        """Verifica se uma promoção já foi enviada."""
        async with self._db.execute(
            "SELECT 1 FROM promos WHERE link = ?", (link,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def add_promo(
        self,
        link: str,
        title: str,
        store: str,
        price: Optional[float] = None,
        original_price: Optional[float] = None,
        discount_pct: Optional[float] = None,
        category: str = "",
        score: float = 0.0,
    ) -> bool:
        """
        Registra uma promoção enviada no banco de dados.

        Returns:
            True se inserida com sucesso, False se já existia.
        """
        try:
            await self._db.execute(
                """INSERT INTO promos
                   (link, title, price, original_price, discount_pct, store, category, sent_at, score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (link, title, price, original_price, discount_pct, store, category, time.time(), score),
            )
            await self._db.commit()

            # Registra no histórico de preços
            if price is not None:
                await self._db.execute(
                    "INSERT INTO price_history (link, price, recorded_at) VALUES (?, ?, ?)",
                    (link, price, time.time()),
                )
                await self._db.commit()

            return True
        except aiosqlite.IntegrityError:
            return False

    async def get_promo_count(self) -> int:
        """Retorna o total de promoções registradas."""
        async with self._db.execute("SELECT COUNT(*) FROM promos") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_recent_promos(self, limit: int = 20) -> list[dict]:
        """Retorna as promoções mais recentes."""
        async with self._db.execute(
            "SELECT * FROM promos ORDER BY sent_at DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def cleanup_old_promos(self, max_age_days: int = 30) -> int:
        """Remove promoções mais antigas que max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        cursor = await self._db.execute(
            "DELETE FROM promos WHERE sent_at < ?", (cutoff,)
        )
        await self._db.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Limpeza: {deleted} promos antigas removidas")
        return deleted

    # -----------------------------------------------------------------------
    # Estatísticas de Scrapers
    # -----------------------------------------------------------------------

    async def log_scraper_run(
        self,
        scraper_name: str,
        duration_secs: float,
        items_found: int = 0,
        items_sent: int = 0,
        errors: int = 0,
        status: str = "ok",
    ) -> None:
        """Registra uma execução de scraper para monitoramento."""
        await self._db.execute(
            """INSERT INTO scraper_stats
               (scraper_name, run_at, duration_secs, items_found, items_sent, errors, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (scraper_name, time.time(), duration_secs, items_found, items_sent, errors, status),
        )
        await self._db.commit()

    async def get_scraper_stats(self, hours: int = 24) -> list[dict]:
        """Retorna estatísticas dos scrapers nas últimas N horas."""
        cutoff = time.time() - (hours * 3600)
        async with self._db.execute(
            """SELECT scraper_name,
                      COUNT(*) as runs,
                      SUM(items_found) as total_found,
                      SUM(items_sent) as total_sent,
                      SUM(errors) as total_errors,
                      AVG(duration_secs) as avg_duration
               FROM scraper_stats
               WHERE run_at > ?
               GROUP BY scraper_name""",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # -----------------------------------------------------------------------
    # Histórico de Preços
    # -----------------------------------------------------------------------

    async def get_price_history(self, link: str) -> list[dict]:
        """Retorna o histórico de preços de um produto."""
        async with self._db.execute(
            "SELECT price, recorded_at FROM price_history WHERE link = ? ORDER BY recorded_at",
            (link,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_lowest_price(self, link: str) -> Optional[float]:
        """Retorna o menor preço já registrado para um produto."""
        async with self._db.execute(
            "SELECT MIN(price) FROM price_history WHERE link = ?", (link,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] is not None else None
