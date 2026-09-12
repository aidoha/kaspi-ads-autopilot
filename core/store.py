"""
store.py — SQLite-персистенция воркера (stdlib sqlite3, без ORM).

Таблицы:
  products_snapshot — снимки товаров кампании (для prev avgCpc и истории);
  revenue_cache     — кэш выручки по merchant_sku (тяжёлый обход Shop API реже тика);
  decisions_log     — полный аудит каждого решения с причиной;
  tacos_daily       — суточный TACoS по SKU (для аналитика/графиков);
  settings_audit    — аудит правок настроек через UI (кто/что/когда поменял);
  config_overrides  — оверрайды конфига по scope (кампания/товар) поверх глобала;
  product_control   — дейпартинг/вкл-выкл товара (окно часов, дни недели, enabled);
  bid_parking       — запаркованная ставка товара на время ночного простоя;
  product_names     — человекочитаемые названия товаров по merchant_sku;
  metrics_daily     — подневные метрики товара (CTR/CR/ROAS для графиков);
  ai_insights       — разборы LLM-аналитика (кэш + счётчик расхода).

Один коннект на процесс (check_same_thread=False) — воркер маленький, тик и
цикл выручки не пишут одновременно в один SKU. Записи снапшота/решений — append.
"""

from __future__ import annotations

import os
import sqlite3

from connectors.marketing_client import CampaignProduct
from core.daypart import ProductControl
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
                product_state TEXT, price REAL, campaign_id TEXT,
                views INTEGER, ctr REAL, cr REAL, crr REAL, gmv REAL,
                transactions INTEGER, buy_box INTEGER
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

            -- Колонка sku здесь — это MERCHANT_SKU (offer.code), не кампанийный
            -- sku: record_tacos пишет r.merchant_sku (см. worker.py). Соседняя
            -- таблица metrics_daily называет колонку так же, но кладёт туда
            -- кампанийный sku — JOIN по sku между этими таблицами не сработает.
            CREATE TABLE IF NOT EXISTS tacos_daily (
                day TEXT, sku TEXT, tacos REAL, cost REAL, revenue REAL,
                PRIMARY KEY (day, sku)
            );

            CREATE TABLE IF NOT EXISTS settings_audit (
                ts INTEGER, user TEXT, field TEXT, old TEXT, new TEXT
            );

            CREATE TABLE IF NOT EXISTS config_overrides (
                scope TEXT, scope_id TEXT, field TEXT, value TEXT,
                user TEXT, ts INTEGER,
                PRIMARY KEY (scope, scope_id, field)
            );

            CREATE TABLE IF NOT EXISTS product_control (
                campaign_id  TEXT,
                sku          TEXT,
                enabled      INTEGER DEFAULT 1,
                window_start INTEGER DEFAULT 0,
                window_end   INTEGER DEFAULT 24,
                days_mask    INTEGER DEFAULT 127,
                user         TEXT,
                ts           INTEGER,
                PRIMARY KEY (campaign_id, sku)
            );

            -- Запаркованная ставка: последний рабочий уровень товара перед тем,
            -- как дейпарт уронил его в пол на ночь. Утром при открытии окна
            -- ставка восстанавливается из этой записи и запись стирается.
            -- Рантайм-состояние (не пользовательская настройка) — держим отдельно
            -- от product_control, чтобы не мешать с расписанием.
            CREATE TABLE IF NOT EXISTS bid_parking (
                campaign_id TEXT,
                sku         TEXT,
                parked_bid  REAL,
                ts          INTEGER,
                PRIMARY KEY (campaign_id, sku)
            );

            -- Человекочитаемые названия товаров. Ключ — merchant_sku (offer.code),
            -- т.к. эндпоинт товаров кампании названий не отдаёт, а Shop API отдаёт
            -- их в позициях заказа (OrderEntry.name). Заполняется revenue-циклом.
            CREATE TABLE IF NOT EXISTS product_names (
                merchant_sku TEXT PRIMARY KEY, name TEXT, ts INTEGER
            );

            -- Подневные метрики товара. Отдельно от tacos_daily: там лежит
            -- значение СКОЛЬЗЯЩЕГО ОКНА на конец дня, а графику нужен именно
            -- день. Наполняется отдельным джобом, который спрашивает кабинет
            -- со StartDate = EndDate = день.
            --
            -- Колонка sku — это КАМПАНИЙНЫЙ sku (не merchant_sku!): совсем
            -- другой ключ, чем sku в tacos_daily (см. комментарий там). Джойн
            -- по этой колонке между двумя таблицами напрямую не сработает.
            --
            -- ВАЖНО: revenue, tacos, roas в строке — величины УРОВНЯ ТОВАРА,
            -- а не кампании. Выручка приходит из Shop API по merchant_sku и
            -- к кампаниям не привязана, поэтому у товара, который ведётся в
            -- двух кампаниях, обе строки несут ОДНУ И ТУ ЖЕ полную выручку.
            -- Суммировать revenue/tacos/roas по кампаниям нельзя — задвоится.
            -- Суммировать можно только cost, clicks, views, carts, gmv.
            CREATE TABLE IF NOT EXISTS metrics_daily (
                day          TEXT,
                campaign_id  TEXT,
                sku          TEXT,
                merchant_sku TEXT,
                cost         REAL,
                gmv          REAL,
                views        INTEGER,
                clicks       INTEGER,
                carts        INTEGER,
                transactions INTEGER,
                ctr          REAL,
                cr           REAL,
                revenue      REAL,
                tacos        REAL,
                roas         REAL,
                roas_gmv     REAL,
                ts           INTEGER,
                PRIMARY KEY (day, campaign_id, sku)
            );
            CREATE INDEX IF NOT EXISTS ix_metrics_sku_day
                ON metrics_daily(sku, day);

            -- Разборы аналитика. Ключ (kind, scope_id, day) — один разбор на
            -- товар в день; повторный вызов перезаписывает текст, но
            -- инкрементит calls: за пересчёт уже заплачено, и лимит расхода
            -- обязан его видеть.
            CREATE TABLE IF NOT EXISTS ai_insights (
                kind       TEXT,
                scope_id   TEXT,
                day        TEXT,
                ts         INTEGER,
                text       TEXT,
                model      TEXT,
                tokens_in  INTEGER,
                tokens_out INTEGER,
                calls      INTEGER DEFAULT 1,
                PRIMARY KEY (kind, scope_id, day)
            );
        """)
        # Позиционный трекер удалён: с датацентрового IP Kaspi отдавал 429, на
        # VPS таблица не наполнялась. Сносим явно, иначе она вечно висит в
        # боевой БД мёртвым грузом. Store открывается на каждый HTTP-запрос
        # (webui/app.py), поэтому проверяем наличие таблицы через
        # sqlite_master — иначе DROP гоняется вхолостую на каждый запрос.
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='position_snapshots'"
        ).fetchone()
        if exists:
            self._conn.execute("DROP TABLE position_snapshots")
        # Миграция старой БД: добавить campaign_id, если таблица уже была без него.
        cols = {r["name"] for r in
                self._conn.execute("PRAGMA table_info(decisions_log)")}
        if "campaign_id" not in cols:
            self._conn.execute(
                "ALTER TABLE decisions_log ADD COLUMN campaign_id TEXT")
        self._conn.commit()

        # Миграция products_snapshot: campaign_id плюс поля обратной связи,
        # которые кабинет отдавал всегда, а мы начали хранить только сейчас.
        # У старых строк они останутся NULL — история метрик начинается с
        # момента выката, задним числом её взять неоткуда.
        snap_cols = {r["name"] for r in
                     self._conn.execute("PRAGMA table_info(products_snapshot)")}
        for col, decl in (("campaign_id", "TEXT"), ("views", "INTEGER"),
                          ("ctr", "REAL"), ("cr", "REAL"), ("crr", "REAL"),
                          ("gmv", "REAL"), ("transactions", "INTEGER"),
                          ("buy_box", "INTEGER")):
            if col not in snap_cols:
                self._conn.execute(
                    f"ALTER TABLE products_snapshot ADD COLUMN {col} {decl}")
        self._conn.commit()

    # ---- снапшоты товаров ---------------------------------------------------

    def save_products_snapshot(self, products: list[CampaignProduct], ts: int,
                               campaign_id: str = ""):
        self._conn.executemany(
            """INSERT INTO products_snapshot
               (ts, sku, merchant_sku, bid, avg_cpc, score, cost, cost_today,
                clicks, carts, product_state, price, campaign_id,
                views, ctr, cr, crr, gmv, transactions, buy_box)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(ts, p.sku, p.merchant_sku, p.bid, p.avg_cpc, p.score, p.cost,
              p.cost_today, p.clicks, p.carts, p.product_state, p.price,
              campaign_id, p.views, p.ctr, p.cr, p.crr, p.gmv,
              p.transactions, int(p.buy_box)) for p in products],
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

    def get_campaign_skus(self, campaign_id: str) -> list[dict]:
        """Уникальные SKU кампании со ставкой из свежего снапшота (для UI-списка).
        Дополнительно подтягивает человекочитаемое название (LEFT JOIN, может быть
        None, если по товару ещё не было заказов)."""
        rows = self._conn.execute(
            """SELECT ps.sku, ps.merchant_sku, ps.bid, ps.ts, pn.name AS name
               FROM (SELECT sku, merchant_sku, bid, MAX(ts) AS ts
                     FROM products_snapshot WHERE campaign_id=?
                     GROUP BY sku) ps
               LEFT JOIN product_names pn ON pn.merchant_sku = ps.merchant_sku
               ORDER BY ps.sku""",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- названия товаров ---------------------------------------------------

    def put_product_names(self, names: dict[str, str], ts: int):
        """Апсертит {merchant_sku: name}. Пустые имена игнорируются, чтобы не
        затирать ранее известное название пустышкой из заказа без имени."""
        rows = [(ms, nm, ts) for ms, nm in names.items() if ms and nm]
        if not rows:
            return
        self._conn.executemany(
            """INSERT INTO product_names (merchant_sku, name, ts)
               VALUES (?,?,?)
               ON CONFLICT(merchant_sku) DO UPDATE SET
                 name=excluded.name, ts=excluded.ts""",
            rows,
        )
        self._conn.commit()

    def get_sku_name_map(self) -> dict[str, str]:
        """Карта имён для подписи строк дашборда. Отвечает на ОБА ключа:
        campaign sku (строки «Решения») и merchant_sku (строки TACoS — record_tacos
        пишет merchant_sku). Мост sku→merchant_sku из свежего снапшота,
        merchant_sku→name из product_names."""
        rows = self._conn.execute(
            """SELECT ps.sku AS sku, ps.merchant_sku AS merchant_sku, pn.name AS name
               FROM (SELECT sku, merchant_sku, MAX(ts) AS ts
                     FROM products_snapshot GROUP BY sku) ps
               JOIN product_names pn ON pn.merchant_sku = ps.merchant_sku""",
        ).fetchall()
        out: dict[str, str] = {}
        for r in rows:
            if not r["name"]:
                continue
            out[r["sku"]] = r["name"]
            out[r["merchant_sku"]] = r["name"]
        return out

    def get_latest_snapshot_ts(self) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(ts) AS ts FROM products_snapshot").fetchone()
        return row["ts"] if row and row["ts"] is not None else None

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

    def get_decisions_summary_for_day(self, day: str) -> list[dict]:
        """Сводка решений за день по товарам — по одной строке на SKU.

        Для дашборда: количество решений, последнее действие/ставка и сколько
        из них применено. Товары с самой свежей активностью сверху.
        """
        rows = self._conn.execute(
            """
            SELECT
                sku,
                COUNT(*)         AS n,
                SUM(applied)     AS applied_n,
                MAX(ts)          AS last_ts,
                (SELECT action  FROM decisions_log d2
                   WHERE d2.sku = d.sku AND d2.day = d.day
                   ORDER BY ts DESC LIMIT 1) AS last_action,
                (SELECT new_bid FROM decisions_log d2
                   WHERE d2.sku = d.sku AND d2.day = d.day
                   ORDER BY ts DESC LIMIT 1) AS last_new_bid
            FROM decisions_log d
            WHERE day = ?
            GROUP BY sku
            ORDER BY last_ts DESC
            """,
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_decisions_for_sku_day(self, day: str, sku: str) -> int:
        """Число решений по товару за день — для расчёта числа страниц."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM decisions_log WHERE day=? AND sku=?",
            (day, sku),
        ).fetchone()
        return row["n"] if row else 0

    def get_decisions_for_sku_day(self, day: str, sku: str,
                                  limit: int, offset: int) -> list[dict]:
        """Страница решений по одному товару за день (сначала свежие)."""
        rows = self._conn.execute(
            "SELECT * FROM decisions_log WHERE day=? AND sku=? "
            "ORDER BY ts DESC LIMIT ? OFFSET ?",
            (day, sku, limit, offset),
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

    # ---- overrides конфига (кампания/товар) ---------------------------------

    def get_overrides(self, scope: str, scope_id: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT field, value FROM config_overrides WHERE scope=? AND scope_id=?",
            (scope, scope_id),
        ).fetchall()
        return {r["field"]: r["value"] for r in rows}

    def set_override(self, scope: str, scope_id: str, field: str,
                     value: str, user: str, ts: int) -> None:
        self._conn.execute(
            """INSERT INTO config_overrides (scope, scope_id, field, value, user, ts)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(scope, scope_id, field) DO UPDATE SET
                 value=excluded.value, user=excluded.user, ts=excluded.ts""",
            (scope, scope_id, field, str(value), user, ts),
        )
        self._conn.commit()

    def delete_override(self, scope: str, scope_id: str, field: str) -> None:
        self._conn.execute(
            "DELETE FROM config_overrides WHERE scope=? AND scope_id=? AND field=?",
            (scope, scope_id, field),
        )
        self._conn.commit()

    # ---- контроль биддера по товару (дейпартинг/вкл-выкл) --------------------

    def get_product_control(self, campaign_id: str, sku: str) -> ProductControl:
        row = self._conn.execute(
            "SELECT enabled, window_start, window_end, days_mask "
            "FROM product_control WHERE campaign_id=? AND sku=?",
            (campaign_id, sku),
        ).fetchone()
        if row is None:
            return ProductControl()
        return ProductControl(bool(row["enabled"]), row["window_start"],
                              row["window_end"], row["days_mask"])

    def set_product_control(self, campaign_id: str, sku: str, enabled: bool,
                            window_start: int, window_end: int, days_mask: int,
                            user: str, ts: int) -> None:
        self._conn.execute(
            """INSERT INTO product_control
                 (campaign_id, sku, enabled, window_start, window_end, days_mask, user, ts)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id, sku) DO UPDATE SET
                 enabled=excluded.enabled, window_start=excluded.window_start,
                 window_end=excluded.window_end, days_mask=excluded.days_mask,
                 user=excluded.user, ts=excluded.ts""",
            (campaign_id, sku, int(enabled), window_start, window_end,
             days_mask, user, ts),
        )
        self._conn.commit()

    def list_product_control(self, campaign_id: str) -> dict[str, ProductControl]:
        rows = self._conn.execute(
            "SELECT sku, enabled, window_start, window_end, days_mask "
            "FROM product_control WHERE campaign_id=?",
            (campaign_id,),
        ).fetchall()
        return {r["sku"]: ProductControl(bool(r["enabled"]), r["window_start"],
                                         r["window_end"], r["days_mask"])
                for r in rows}

    def all_product_controls(self) -> list[tuple[str, str, ProductControl]]:
        rows = self._conn.execute(
            "SELECT campaign_id, sku, enabled, window_start, window_end, days_mask "
            "FROM product_control",
        ).fetchall()
        return [(r["campaign_id"], r["sku"],
                 ProductControl(bool(r["enabled"]), r["window_start"],
                                r["window_end"], r["days_mask"]))
                for r in rows]

    # ---- парковка ставки (ночной сброс → утреннее восстановление) -----------

    def get_parked_bids(self, campaign_id: str) -> dict[str, float]:
        """Запаркованные ставки кампании {sku: parked_bid}. Пусто, если парковок нет."""
        rows = self._conn.execute(
            "SELECT sku, parked_bid FROM bid_parking WHERE campaign_id=?",
            (campaign_id,),
        ).fetchall()
        return {r["sku"]: r["parked_bid"] for r in rows}

    def set_parked_bid(self, campaign_id: str, sku: str, bid: float,
                       ts: int) -> None:
        self._conn.execute(
            """INSERT INTO bid_parking (campaign_id, sku, parked_bid, ts)
               VALUES (?,?,?,?)
               ON CONFLICT(campaign_id, sku) DO UPDATE SET
                 parked_bid=excluded.parked_bid, ts=excluded.ts""",
            (campaign_id, sku, bid, ts),
        )
        self._conn.commit()

    def clear_parked_bid(self, campaign_id: str, sku: str) -> None:
        self._conn.execute(
            "DELETE FROM bid_parking WHERE campaign_id=? AND sku=?",
            (campaign_id, sku),
        )
        self._conn.commit()

    # ---- подневные метрики -------------------------------------------------

    def upsert_metrics_daily(self, day: str, campaign_id: str, sku: str,
                             merchant_sku: str, cost: float, gmv: float,
                             views: int, clicks: int, carts: int,
                             transactions: int, ctr: float, cr: float,
                             revenue: float | None, ts: int) -> None:
        """Строка подневных метрик товара. Производные считаем ЗДЕСЬ и храним:
        графики читают таблицу напрямую, пересчитывать их на каждый рендер
        панели незачем.

        NULL пишем только там, где ноль в ЗНАМЕНАТЕЛЕ — величина не определена.
        Расход без выручки даёт ROAS 0.0, и это не дырка, а важный сигнал:
        деньги потратили, продаж нет. revenue=None означает «Shop API за этот
        день ещё не опрашивали» — тогда ROAS именно неизвестен."""
        tacos = (cost / revenue) if revenue else None
        roas = None if (not cost or revenue is None) else (revenue / cost)
        roas_gmv = (gmv / cost) if cost else None
        self._conn.execute(
            """INSERT INTO metrics_daily
               (day, campaign_id, sku, merchant_sku, cost, gmv, views, clicks,
                carts, transactions, ctr, cr, revenue, tacos, roas, roas_gmv, ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(day, campaign_id, sku) DO UPDATE SET
                 merchant_sku=excluded.merchant_sku,
                 cost=excluded.cost, gmv=excluded.gmv, views=excluded.views,
                 clicks=excluded.clicks, carts=excluded.carts,
                 transactions=excluded.transactions, ctr=excluded.ctr,
                 cr=excluded.cr, revenue=excluded.revenue,
                 tacos=excluded.tacos, roas=excluded.roas,
                 roas_gmv=excluded.roas_gmv, ts=excluded.ts""",
            (day, campaign_id, sku, merchant_sku, cost, gmv, views, clicks,
             carts, transactions, ctr, cr, revenue, tacos, roas, roas_gmv, ts),
        )
        self._conn.commit()

    def get_metrics_series(self, sku: str, days: int) -> list[dict]:
        """Последние `days` дней по товару, по возрастанию дня — как ось X."""
        rows = self._conn.execute(
            "SELECT * FROM ("
            "  SELECT * FROM metrics_daily WHERE sku=? ORDER BY day DESC LIMIT ?"
            ") ORDER BY day ASC",
            (sku, days),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_metrics_for_day(self, day: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM metrics_daily WHERE day=? ORDER BY sku", (day,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- разборы аналитика -------------------------------------------------

    def put_ai_insight(self, kind: str, scope_id: str, day: str, ts: int,
                       text: str, model: str, tokens_in: int,
                       tokens_out: int) -> None:
        """kind: 'product' (scope_id = sku) либо 'daily' (scope_id = '')."""
        self._conn.execute(
            """INSERT INTO ai_insights
               (kind, scope_id, day, ts, text, model, tokens_in, tokens_out, calls)
               VALUES (?,?,?,?,?,?,?,?,1)
               ON CONFLICT(kind, scope_id, day) DO UPDATE SET
                 ts=excluded.ts, text=excluded.text, model=excluded.model,
                 tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out,
                 calls=ai_insights.calls + 1""",
            (kind, scope_id, day, ts, text, model, tokens_in, tokens_out),
        )
        self._conn.commit()

    def get_ai_insight(self, kind: str, scope_id: str, day: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM ai_insights WHERE kind=? AND scope_id=? AND day=?",
            (kind, scope_id, day),
        ).fetchone()
        return dict(row) if row else None

    def count_ai_calls(self, day: str) -> int:
        """Сколько платных вызовов сделано за день — предохранитель
        AI_DAILY_LIMIT. Считаем сумму calls, а не число строк: иначе
        принудительные пересчёты одного товара были бы бесплатны для лимита."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(calls), 0) AS n FROM ai_insights WHERE day=?",
            (day,),
        ).fetchone()
        return row["n"]

