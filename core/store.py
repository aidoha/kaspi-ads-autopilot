"""
store.py — SQLite-персистенция воркера (stdlib sqlite3, без ORM).

Таблицы:
  products_snapshot — снимки товаров кампании (для prev avgCpc и истории);
  revenue_cache     — кэш выручки по merchant_sku (тяжёлый обход Shop API реже тика);
  decisions_log     — полный аудит каждого решения с причиной;
  tacos_daily       — суточный TACoS по SKU (для аналитика/графиков).

Один коннект на процесс (check_same_thread=False) — воркер маленький, тик и
цикл выручки не пишут одновременно в один SKU. Записи снапшота/решений — append.
"""

from __future__ import annotations

import os
import sqlite3

from connectors.marketing_client import CampaignProduct
from core.revenue import SkuRevenue
from core.rules import Decision, DailyState


class Store:
    def __init__(self, path: str):
        self.path = path
        # Каталог БД может отсутствовать (git не хранит пустые папки → на свежем
        # клоне db/ нет). Создаём, иначе sqlite: "unable to open database file".
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self):
        self._conn.close()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS products_snapshot (
                ts INTEGER, sku TEXT, merchant_sku TEXT, bid REAL, avg_cpc REAL,
                score REAL, cost REAL, cost_today REAL, clicks INTEGER, carts INTEGER,
                product_state TEXT, price REAL
            );
            CREATE INDEX IF NOT EXISTS ix_snapshot_sku_ts ON products_snapshot(sku, ts);

            CREATE TABLE IF NOT EXISTS revenue_cache (
                merchant_sku TEXT PRIMARY KEY, revenue REAL, gross_revenue REAL,
                cancelled REAL, orders_count INTEGER, units INTEGER, ts INTEGER
            );

            CREATE TABLE IF NOT EXISTS decisions_log (
                ts INTEGER, day TEXT, sku TEXT, merchant_sku TEXT, old_bid REAL,
                new_bid REAL, action TEXT, loop TEXT, reason TEXT, applied INTEGER,
                campaign_id TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_decisions_sku_day ON decisions_log(sku, day);

            CREATE TABLE IF NOT EXISTS tacos_daily (
                day TEXT, sku TEXT, tacos REAL, cost REAL, revenue REAL,
                PRIMARY KEY (day, sku)
            );

            CREATE TABLE IF NOT EXISTS settings_audit (
                ts INTEGER, user TEXT, field TEXT, old TEXT, new TEXT
            );
        """)
        # Миграция старой БД: добавить campaign_id, если таблица уже была без него.
        cols = {r["name"] for r in
                self._conn.execute("PRAGMA table_info(decisions_log)")}
        if "campaign_id" not in cols:
            self._conn.execute(
                "ALTER TABLE decisions_log ADD COLUMN campaign_id TEXT")
        self._conn.commit()

    # ---- снапшоты товаров ---------------------------------------------------

    def save_products_snapshot(self, products: list[CampaignProduct], ts: int):
        self._conn.executemany(
            """INSERT INTO products_snapshot
               (ts, sku, merchant_sku, bid, avg_cpc, score, cost, cost_today,
                clicks, carts, product_state, price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(ts, p.sku, p.merchant_sku, p.bid, p.avg_cpc, p.score, p.cost,
              p.cost_today, p.clicks, p.carts, p.product_state, p.price)
             for p in products],
        )
        self._conn.commit()

    def get_prev_avg_cpc(self, sku: str) -> float | None:
        row = self._conn.execute(
            "SELECT avg_cpc FROM products_snapshot WHERE sku=? ORDER BY ts DESC LIMIT 1",
            (sku,),
        ).fetchone()
        return row["avg_cpc"] if row else None

    def get_latest_snapshot(self, sku: str) -> dict | None:
        """Последний снапшот товара (для дашборда — текущая ставка и т.п.)."""
        row = self._conn.execute(
            "SELECT * FROM products_snapshot WHERE sku=? ORDER BY ts DESC LIMIT 1",
            (sku,),
        ).fetchone()
        return dict(row) if row else None

    # ---- кэш выручки --------------------------------------------------------

    def put_revenue_cache(self, revenue: dict[str, SkuRevenue], ts: int):
        self._conn.executemany(
            """INSERT INTO revenue_cache
               (merchant_sku, revenue, gross_revenue, cancelled, orders_count, units, ts)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(merchant_sku) DO UPDATE SET
                 revenue=excluded.revenue, gross_revenue=excluded.gross_revenue,
                 cancelled=excluded.cancelled, orders_count=excluded.orders_count,
                 units=excluded.units, ts=excluded.ts""",
            [(r.merchant_sku, r.revenue, r.gross_revenue, r.cancelled,
              r.orders_count, r.units, ts) for r in revenue.values()],
        )
        self._conn.commit()

    def get_revenue_cache(self) -> dict[str, SkuRevenue]:
        rows = self._conn.execute("SELECT * FROM revenue_cache").fetchall()
        return {
            r["merchant_sku"]: SkuRevenue(
                merchant_sku=r["merchant_sku"], revenue=r["revenue"],
                gross_revenue=r["gross_revenue"], cancelled=r["cancelled"],
                orders_count=r["orders_count"], units=r["units"],
            )
            for r in rows
        }

    # ---- лог решений --------------------------------------------------------

    def log_decision(self, d: Decision, ts: int, day: str, applied: bool,
                     campaign_id: str = ""):
        self._conn.execute(
            """INSERT INTO decisions_log
               (ts, day, sku, merchant_sku, old_bid, new_bid, action, loop,
                reason, applied, campaign_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, day, d.sku, d.merchant_sku, d.old_bid, d.new_bid,
             d.action, d.loop, d.reason, int(applied), campaign_id),
        )
        self._conn.commit()

    def count_changes_today(self, sku: str, day: str) -> int:
        """Число фактических изменений ставки за день (hold не считается)."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM decisions_log "
            "WHERE sku=? AND day=? AND action != 'hold'",
            (sku, day),
        ).fetchone()
        return row["n"]

    def build_daily_state(self, skus: list[str], day: str) -> dict[str, DailyState]:
        """Суточное состояние для движка правил: счётчик изменений + prev avgCpc."""
        return {
            sku: DailyState(
                changes_today=self.count_changes_today(sku, day),
                prev_avg_cpc=self.get_prev_avg_cpc(sku),
            )
            for sku in skus
        }

    # ---- суточный TACoS -----------------------------------------------------

    def record_tacos(self, day: str, sku: str, tacos: float | None,
                     cost: float, revenue: float):
        self._conn.execute(
            """INSERT INTO tacos_daily (day, sku, tacos, cost, revenue)
               VALUES (?,?,?,?,?)
               ON CONFLICT(day, sku) DO UPDATE SET
                 tacos=excluded.tacos, cost=excluded.cost, revenue=excluded.revenue""",
            (day, sku, tacos, cost, revenue),
        )
        self._conn.commit()

    def get_tacos_daily(self, day: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tacos_daily WHERE day=? ORDER BY sku", (day,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_decisions_for_day(self, day: str) -> list[dict]:
        """Все решения за день по порядку — для дневного разбора LLM-аналитиком."""
        rows = self._conn.execute(
            "SELECT * FROM decisions_log WHERE day=? ORDER BY ts", (day,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- аудит настроек -----------------------------------------------------

    def log_settings_change(self, user: str, field: str, old, new, ts: int):
        """Логирование изменения настройки (для аудита UI)."""
        self._conn.execute(
            "INSERT INTO settings_audit (ts, user, field, old, new) VALUES (?,?,?,?,?)",
            (ts, user, field, str(old), str(new)),
        )
        self._conn.commit()

    def get_settings_audit(self, limit: int = 50) -> list[dict]:
        """Получить аудит правок настроек (свежие сверху)."""
        rows = self._conn.execute(
            "SELECT * FROM settings_audit ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
