"""
reports.py — хранение загруженных выгрузок Kaspi и сводка «деньги по товару».

Отдельно от core/store.py: там живёт биддер и его снапшоты кабинета, а здесь
— то, что владелец приносит руками раз в неделю. Таблицы в той же БД (так
проще бэкап и выкат), но биддер их не читает.

Таблицы:
  report_uploads    — каждый загруженный файл: тип, период, хеш (повтор того
                      же файла не задвоит деньги);
  report_ad_rows    — строки рекламного отчёта по товарам;
  report_bonus_rows — строки бонусного отчёта по товарам;
  report_campaign_rows — строки отчёта бонусных кампаний;
  shop_orders       — архив заказов, ключ (номер заказа, артикул): новый
                      архив поверх старого обновляет статусы (отмены);
  product_costs     — себестоимость и комиссия Kaspi по артикулу,
                      артикул «*» — комиссия по умолчанию.

Склейка товаров. Отчёты рекламы и бонусов называют товар кодом карточки
Kaspi (167575558), архив заказов — артикулом продавца (997047436), а
названия расходятся («триммер для бороды» против «набор для стрижки
AY-101»). Надёжная связь — номера заказов: отчёт перечисляет их, архив знает
артикул каждого. Код → артикул выбирается большинством голосов; запасные
пути — снапшоты биддера и совпадение названия.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter
from datetime import date, timedelta

from core.report_files import (KIND_ADS, KIND_BONUS, KIND_CAMPAIGNS, KIND_ORDERS,
                               ParsedReport)

# Отменённое и возвращённое — не выручка. Всё прочее (в т.ч. заказы в пути)
# считаем: реклама Kaspi тоже считает заказ в момент оформления.
_LOST_STATUSES = ("CANCELLED", "RETURNED")
DEFAULT_SCOPE = "*"


def _norm_name(s: str) -> str:
    return " ".join((s or "").lower().replace("ё", "е").split())


class ReportsStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    @classmethod
    def open(cls, path: str) -> "ReportsStore":
        return cls(sqlite3.connect(path, check_same_thread=False))

    def close(self):
        self._conn.close()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS report_uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT, filename TEXT, sha256 TEXT UNIQUE,
                period_start TEXT, period_end TEXT, rows INTEGER,
                user TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS report_ad_rows (
                upload_id INTEGER, sku TEXT, name TEXT, views INTEGER,
                clicks INTEGER, cost REAL, ad_revenue REAL, ad_orders INTEGER,
                carts INTEGER, order_numbers TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_report_ad_upload ON report_ad_rows(upload_id);
            CREATE TABLE IF NOT EXISTS report_bonus_rows (
                upload_id INTEGER, sku TEXT, name TEXT, views INTEGER,
                clicks INTEGER, orders INTEGER, revenue REAL, bonus REAL,
                order_numbers TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_report_bonus_upload ON report_bonus_rows(upload_id);
            CREATE TABLE IF NOT EXISTS report_campaign_rows (
                upload_id INTEGER, name TEXT, start_date TEXT, status TEXT,
                views INTEGER, clicks INTEGER, orders INTEGER, revenue REAL,
                bonus REAL
            );
            CREATE TABLE IF NOT EXISTS shop_orders (
                order_no TEXT, merchant_sku TEXT, name TEXT, category TEXT,
                qty REAL, amount REAL, delivery_type TEXT, status TEXT,
                ordered_at TEXT, day TEXT, delivery_cost REAL, upload_id INTEGER,
                PRIMARY KEY (order_no, merchant_sku)
            );
            CREATE INDEX IF NOT EXISTS ix_shop_orders_day ON shop_orders(day);
            CREATE TABLE IF NOT EXISTS product_costs (
                merchant_sku TEXT PRIMARY KEY, unit_cost REAL,
                commission_pct REAL, user TEXT, ts INTEGER
            );
        """)
        self._conn.commit()

    # ---- загрузки ----

    def save_report(self, parsed: ParsedReport, filename: str, data: bytes,
                    user: str, ts: int | None = None) -> dict:
        """Сохранить разобранный файл. Тот же файл второй раз → duplicate,
        ничего не пишется: две одинаковые выгрузки иначе удвоили бы расход."""
        digest = hashlib.sha256(data).hexdigest()
        dup = self._conn.execute(
            "SELECT id FROM report_uploads WHERE sha256=?", (digest,)).fetchone()
        if dup:
            return {"duplicate": True, "upload_id": dup["id"]}
        ts = int(time.time()) if ts is None else ts
        c = self._conn
        cur = c.execute(
            "INSERT INTO report_uploads(kind, filename, sha256, period_start, period_end,"
            " rows, user, ts) VALUES (?,?,?,?,?,?,?,?)",
            (parsed.kind, filename, digest, parsed.period_start, parsed.period_end,
             len(parsed.rows), user, ts))
        uid = cur.lastrowid
        if parsed.kind == KIND_ADS:
            c.executemany(
                "INSERT INTO report_ad_rows VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(uid, r["sku"], r["name"], r["views"], r["clicks"], r["cost"],
                  r["ad_revenue"], r["ad_orders"], r["carts"],
                  json.dumps(r["order_numbers"])) for r in parsed.rows])
        elif parsed.kind == KIND_BONUS:
            c.executemany(
                "INSERT INTO report_bonus_rows VALUES (?,?,?,?,?,?,?,?,?)",
                [(uid, r["sku"], r["name"], r["views"], r["clicks"], r["orders"],
                  r["revenue"], r["bonus"], json.dumps(r["order_numbers"]))
                 for r in parsed.rows])
        elif parsed.kind == KIND_CAMPAIGNS:
            c.executemany(
                "INSERT INTO report_campaign_rows VALUES (?,?,?,?,?,?,?,?,?)",
                [(uid, r["name"], r["start_date"], r["status"], r["views"],
                  r["clicks"], r["orders"], r["revenue"], r["bonus"])
                 for r in parsed.rows])
        elif parsed.kind == KIND_ORDERS:
            c.executemany(
                "INSERT INTO shop_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(order_no, merchant_sku) DO UPDATE SET"
                " name=excluded.name, category=excluded.category, qty=excluded.qty,"
                " amount=excluded.amount, delivery_type=excluded.delivery_type,"
                " status=excluded.status, ordered_at=excluded.ordered_at,"
                " day=excluded.day, delivery_cost=excluded.delivery_cost,"
                " upload_id=excluded.upload_id",
                [(r["order_no"], r["merchant_sku"], r["name"], r["category"], r["qty"],
                  r["amount"], r["delivery_type"], r["status"], r["ordered_at"],
                  r["day"], r["delivery_cost"], uid) for r in parsed.rows])
        c.commit()
        return {"duplicate": False, "upload_id": uid}

    def list_uploads(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kind, filename, period_start, period_end, rows, user, ts"
            " FROM report_uploads ORDER BY ts DESC, id DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_upload(self, upload_id: int) -> bool:
        c = self._conn
        if not c.execute("SELECT 1 FROM report_uploads WHERE id=?", (upload_id,)).fetchone():
            return False
        for table in ("report_ad_rows", "report_bonus_rows", "report_campaign_rows",
                      "shop_orders"):
            c.execute(f"DELETE FROM {table} WHERE upload_id=?", (upload_id,))
        c.execute("DELETE FROM report_uploads WHERE id=?", (upload_id,))
        c.commit()
        return True

    # ---- себестоимость ----

    def get_costs(self) -> dict[str, dict]:
        return {r["merchant_sku"]: {"unit_cost": r["unit_cost"],
                                    "commission_pct": r["commission_pct"]}
                for r in self._conn.execute("SELECT * FROM product_costs")}

    def set_cost(self, merchant_sku: str, unit_cost: float | None,
                 commission_pct: float | None, user: str, ts: int | None = None):
        ts = int(time.time()) if ts is None else ts
        if unit_cost is None and commission_pct is None:
            self._conn.execute("DELETE FROM product_costs WHERE merchant_sku=?",
                               (merchant_sku,))
        else:
            self._conn.execute(
                "INSERT INTO product_costs VALUES (?,?,?,?,?) ON CONFLICT(merchant_sku)"
                " DO UPDATE SET unit_cost=excluded.unit_cost,"
                " commission_pct=excluded.commission_pct, user=excluded.user, ts=excluded.ts",
                (merchant_sku, unit_cost, commission_pct, user, ts))
        self._conn.commit()

    # ---- сводка ----

    def periods(self) -> list[dict]:
        """Периоды загруженных отчётов рекламы/бонусов — свежие сверху; при
        одной дате конца первым идёт период, где больше файлов: одиночный
        отчёт новой кампании за неделю не должен вытеснять основной период,
        под который выгружено всё остальное."""
        rows = self._conn.execute(
            "SELECT period_start AS start, period_end AS end, COUNT(*) AS files"
            " FROM report_uploads WHERE kind != ?"
            " GROUP BY period_start, period_end"
            " ORDER BY period_end DESC, files DESC, period_start DESC", (KIND_ORDERS,)).fetchall()
        return [dict(r) for r in rows]

    def orders_range(self) -> tuple[str | None, str | None]:
        r = self._conn.execute("SELECT MIN(day) AS a, MAX(day) AS b FROM shop_orders").fetchone()
        return r["a"], r["b"]

    def _sku_links(self) -> dict[str, str]:
        """Код карточки Kaspi → артикул продавца (см. шапку модуля)."""
        votes: dict[str, Counter] = {}
        names: dict[str, str] = {}
        order_sku: dict[str, list[str]] = {}
        for r in self._conn.execute("SELECT order_no, merchant_sku FROM shop_orders"):
            order_sku.setdefault(r["order_no"], []).append(r["merchant_sku"])
        for table in ("report_ad_rows", "report_bonus_rows"):
            for r in self._conn.execute(f"SELECT sku, name, order_numbers FROM {table}"):
                names.setdefault(r["sku"], r["name"])
                cnt = votes.setdefault(r["sku"], Counter())
                for no in json.loads(r["order_numbers"] or "[]"):
                    for msku in order_sku.get(no, ()):
                        cnt[msku] += 1
        links = {sku: cnt.most_common(1)[0][0] for sku, cnt in votes.items() if cnt}

        missing = [s for s in votes if s not in links]
        if missing:
            try:
                for r in self._conn.execute(
                        "SELECT sku, merchant_sku FROM products_snapshot"
                        " WHERE merchant_sku IS NOT NULL AND merchant_sku != ''"
                        " GROUP BY sku, merchant_sku"):
                    if r["sku"] in missing and r["sku"] not in links:
                        links[r["sku"]] = r["merchant_sku"]
            except sqlite3.OperationalError:
                pass   # БД без таблиц биддера (тесты, свежая установка)
        missing = [s for s in votes if s not in links]
        if missing:
            by_name = {}
            for r in self._conn.execute(
                    "SELECT merchant_sku, name FROM shop_orders GROUP BY merchant_sku, name"):
                by_name.setdefault(_norm_name(r["name"]), r["merchant_sku"])
            for s in missing:
                m = by_name.get(_norm_name(names.get(s, "")))
                if m:
                    links[s] = m
        return links

    def _uploads_within(self, kind: str, start: str, end: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM report_uploads WHERE kind=? AND period_start>=? AND period_end<=?",
            (kind, start, end)).fetchall()

    def _period_rows(self, start: str, end: str, links: dict[str, str]) -> dict:
        """Деньги за [start, end] по ключу товара. Ключ — артикул, а у
        неопознанного кода карточки — «kaspi:<код>»."""
        prods: dict[str, dict] = {}

        def entry(key: str, name: str) -> dict:
            e = prods.get(key)
            if e is None:
                e = prods[key] = {
                    "key": key, "name": name, "kaspi_skus": set(),
                    "revenue": 0.0, "orders": 0, "units": 0.0, "cancelled": 0,
                    "returned": 0, "delivery": 0.0,
                    "ad_cost": 0.0, "ad_views": 0, "ad_clicks": 0, "ad_orders": 0,
                    "ad_revenue": 0.0, "bonus": 0.0, "bonus_orders": 0,
                    "bonus_revenue": 0.0, "has_ads": False, "has_bonus": False,
                }
            elif not e["name"] and name:
                e["name"] = name
            return e

        for r in self._conn.execute(
                "SELECT merchant_sku, MAX(name) AS name,"
                " SUM(CASE WHEN status NOT IN (?,?) THEN amount ELSE 0 END) AS revenue,"
                " COUNT(DISTINCT CASE WHEN status NOT IN (?,?) THEN order_no END) AS orders,"
                " SUM(CASE WHEN status NOT IN (?,?) THEN qty ELSE 0 END) AS units,"
                " COUNT(DISTINCT CASE WHEN status='CANCELLED' THEN order_no END) AS cancelled,"
                " COUNT(DISTINCT CASE WHEN status='RETURNED' THEN order_no END) AS returned,"
                " SUM(delivery_cost) AS delivery"
                " FROM shop_orders WHERE day BETWEEN ? AND ? GROUP BY merchant_sku",
                (*_LOST_STATUSES, *_LOST_STATUSES, *_LOST_STATUSES, start, end)):
            e = entry(r["merchant_sku"], r["name"])
            e["revenue"] += r["revenue"] or 0
            e["orders"] += r["orders"] or 0
            e["units"] += r["units"] or 0
            e["cancelled"] += r["cancelled"] or 0
            e["returned"] += r["returned"] or 0
            e["delivery"] += r["delivery"] or 0

        ad_ups = self._uploads_within(KIND_ADS, start, end)
        for up in ad_ups:
            for r in self._conn.execute("SELECT * FROM report_ad_rows WHERE upload_id=?",
                                        (up["id"],)):
                e = entry(links.get(r["sku"], f"kaspi:{r['sku']}"), r["name"])
                e["kaspi_skus"].add(r["sku"])
                e["has_ads"] = True
                e["ad_cost"] += r["cost"] or 0
                e["ad_views"] += r["views"] or 0
                e["ad_clicks"] += r["clicks"] or 0
                e["ad_orders"] += r["ad_orders"] or 0
                e["ad_revenue"] += r["ad_revenue"] or 0

        bonus_ups = self._uploads_within(KIND_BONUS, start, end)
        for up in bonus_ups:
            for r in self._conn.execute("SELECT * FROM report_bonus_rows WHERE upload_id=?",
                                        (up["id"],)):
                e = entry(links.get(r["sku"], f"kaspi:{r['sku']}"), r["name"])
                e["kaspi_skus"].add(r["sku"])
                e["has_bonus"] = True
                e["bonus"] += r["bonus"] or 0
                e["bonus_orders"] += r["orders"] or 0
                e["bonus_revenue"] += r["revenue"] or 0

        return {"products": prods, "ad_uploads": ad_ups, "bonus_uploads": bonus_ups}

    def _overlap_warnings(self, uploads: list[sqlite3.Row], label: str) -> list[str]:
        """Два отчёта с РАЗНЫМИ, но пересекающимися периодами про один товар —
        почти наверняка один и тот же расход дважды (например, отчёт за 1–19
        и за 13–19 число одной кампании)."""
        out = []
        table = "report_ad_rows" if label == "рекламы" else "report_bonus_rows"
        skus = {}
        for up in uploads:
            skus[up["id"]] = {r["sku"] for r in self._conn.execute(
                f"SELECT sku FROM {table} WHERE upload_id=?", (up["id"],))}
        for i, a in enumerate(uploads):
            for b in uploads[i + 1:]:
                same = (a["period_start"], a["period_end"]) == (b["period_start"], b["period_end"])
                overlap = a["period_start"] <= b["period_end"] and b["period_start"] <= a["period_end"]
                if not same and overlap and skus[a["id"]] & skus[b["id"]]:
                    out.append(f"Отчёты {label} «{a['filename']}» и «{b['filename']}» "
                               "пересекаются по датам и товарам — расход мог посчитаться дважды. "
                               "Удалите лишний файл.")
        return out

    def dashboard(self, start: str, end: str) -> dict:
        links = self._sku_links()
        costs = self.get_costs()
        default_comm = (costs.get(DEFAULT_SCOPE) or {}).get("commission_pct")
        cur = self._period_rows(start, end, links)

        products = []
        for e in cur["products"].values():
            products.append(_finish_product(e, costs, default_comm))
        products.sort(key=lambda p: (-(p["revenue"] or 0), -(p["ad_cost"] or 0), p["name"] or ""))

        # Прошлый период той же длины — для «стрелок» в KPI.
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        span = (d1 - d0).days + 1
        p_end = d0 - timedelta(days=1)
        p_start = p_end - timedelta(days=span - 1)
        prev = self._period_rows(p_start.isoformat(), p_end.isoformat(), links)
        prev_products = [_finish_product(e, costs, default_comm)
                         for e in prev["products"].values()]
        prev_totals = _totals(prev_products)
        if not (prev["ad_uploads"] or prev["bonus_uploads"]):
            # Расход прошлого периода не загружен — сравнение прибыли и
            # рекламы с нулём было бы ложной «экономией».
            for k in ("ad_cost", "bonus", "profit", "margin", "tacos"):
                prev_totals[k] = None

        campaigns = []
        for up in self._uploads_within(KIND_CAMPAIGNS, start, end):
            for r in self._conn.execute(
                    "SELECT * FROM report_campaign_rows WHERE upload_id=?", (up["id"],)):
                if not (r["views"] or r["orders"] or r["bonus"]):
                    continue
                campaigns.append({k: r[k] for k in ("name", "start_date", "status", "views",
                                                    "clicks", "orders", "revenue", "bonus")})

        o_min, o_max = self.orders_range()
        warnings = (self._overlap_warnings(cur["ad_uploads"], "рекламы")
                    + self._overlap_warnings(cur["bonus_uploads"], "бонусов"))
        camp_bonus = sum(c["bonus"] or 0 for c in campaigns)
        prod_bonus = sum(p["bonus"] for p in products)
        if camp_bonus - prod_bonus > 1:
            # Отчёт кампаний знает выплаты всех акций, а по товарам их
            # раскладывают только отчёты «по товарам», и каждый — на одну
            # кампанию. Не загрузили какой-то — его бонусы мимо прибыли.
            money = lambda v: f"{v:,.0f}".replace(",", "\u00a0")
            warnings.append(
                f"Бонусные кампании выплатили {money(camp_bonus)} ₸, а в отчётах по товарам "
                f"только {money(prod_bonus)} ₸ — загрузите «Отчёт по товарам» для остальных "
                "бонусных кампаний, иначе эти бонусы не попадут в прибыль.")
        if o_max is None:
            warnings.append("Архив заказов не загружен — выручки и заказов не будет.")
        elif o_max < end:
            warnings.append(f"Архив заказов заканчивается {o_max}, а период — {end}: "
                            "выручка за последние дни неполная.")

        return {
            "start": start, "end": end,
            "prev": {"start": p_start.isoformat(), "end": p_end.isoformat(),
                     "totals": prev_totals},
            "totals": _totals(products),
            "products": products,
            "campaigns": campaigns,
            "default_commission_pct": default_comm,
            "orders_range": {"start": o_min, "end": o_max},
            "warnings": warnings,
            "has_ads": bool(cur["ad_uploads"]),
            "has_bonus": bool(cur["bonus_uploads"]),
        }


def _finish_product(e: dict, costs: dict, default_comm: float | None) -> dict:
    key = e["key"]
    is_linked = not key.startswith("kaspi:")
    own = (costs.get(key) or {}) if is_linked else {}
    unit_cost = own.get("unit_cost")
    comm_pct = own.get("commission_pct")
    comm_source = "product" if comm_pct is not None else None
    if comm_pct is None and default_comm is not None:
        comm_pct, comm_source = default_comm, "default"

    revenue, units = e["revenue"], e["units"]
    commission = revenue * comm_pct if comm_pct is not None else None
    cogs = units * unit_cost if unit_cost is not None else None
    marketing = e["ad_cost"] + e["bonus"]
    if units == 0 and revenue == 0:
        # Продаж не было — уходит только маркетинг, себестоимость не нужна.
        commission, cogs = 0.0, 0.0
    if commission is not None and cogs is not None:
        profit = revenue - commission - cogs - e["delivery"] - marketing
    else:
        profit = None
    return {
        "key": key,
        "merchant_sku": key if is_linked else None,
        "kaspi_skus": sorted(e["kaspi_skus"]),
        "name": e["name"],
        "revenue": revenue,
        "orders": e["orders"],
        "units": units,
        "avg_check": revenue / e["orders"] if e["orders"] else None,
        "cancelled": e["cancelled"],
        "returned": e["returned"],
        "delivery": e["delivery"],
        "ad_cost": e["ad_cost"],
        "ad_views": e["ad_views"],
        "ad_clicks": e["ad_clicks"],
        "ad_orders": e["ad_orders"],
        "ad_revenue": e["ad_revenue"],
        "ctr": e["ad_clicks"] / e["ad_views"] if e["ad_views"] else None,
        "cpc": e["ad_cost"] / e["ad_clicks"] if e["ad_clicks"] else None,
        "drr": e["ad_cost"] / e["ad_revenue"] if e["ad_revenue"] else None,
        "tacos": e["ad_cost"] / revenue if revenue else None,
        "bonus": e["bonus"],
        "bonus_orders": e["bonus_orders"],
        "bonus_revenue": e["bonus_revenue"],
        "has_ads": e["has_ads"],
        "has_bonus": e["has_bonus"],
        "unit_cost": unit_cost,
        "commission_pct": comm_pct,
        "commission_source": comm_source,
        "commission": commission,
        "cogs": cogs,
        "marketing": marketing,
        "profit": profit,
        "margin": profit / revenue if profit is not None and revenue else None,
        "needs_cost": profit is None,
    }


def _totals(products: list[dict]) -> dict:
    s = lambda k: sum((p[k] or 0) for p in products)
    revenue, ad_cost = s("revenue"), s("ad_cost")
    known = [p for p in products if p["profit"] is not None]
    profit = sum(p["profit"] for p in known) if known else None
    known_rev = sum(p["revenue"] for p in known)
    return {
        "revenue": revenue,
        "orders": s("orders"),
        "units": s("units"),
        "cancelled": s("cancelled"),
        "returned": s("returned"),
        "delivery": s("delivery"),
        "ad_cost": ad_cost,
        "ad_revenue": s("ad_revenue"),
        "ad_orders": s("ad_orders"),
        "bonus": s("bonus"),
        "tacos": ad_cost / revenue if revenue else None,
        "profit": profit,
        # Маржа — только по товарам с себестоимостью, иначе выручка товаров
        # без неё размыла бы процент вниз.
        "margin": profit / known_rev if profit is not None and known_rev else None,
        "profit_missing": len(products) - len(known),
    }
