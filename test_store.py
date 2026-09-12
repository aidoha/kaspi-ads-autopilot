"""
test_store.py — тест SQLite-персистенции воркера.

Таблицы: products_snapshot, revenue_cache, decisions_log, tacos_daily.
Проверяем round-trip кэша выручки, prev avgCpc из прошлого снапшота (для
детекта скачка в быстром контуре), счётчик изменений/сутки (для предохранителя),
полный лог решений.

Запуск: .venv/bin/python test_store.py
"""

import os
import sqlite3
import tempfile

from connectors.marketing_client import CampaignProduct
from core.daypart import ProductControl
from core.revenue import SkuRevenue
from core.rules import Decision
from core.store import Store


def cp(**over):
    base = dict(
        sku="166350900", merchant_sku="432085472", campaign_product_id=1,
        bid=18, avg_cpc=12.5, score=7.0, buy_box=True, product_state="Active",
        cost=3600, cost_today=420, gmv=97800, crr=0, cr=0, ctr=0,
        views=0, clicks=120, carts=9, transactions=0, price=48900,
    )
    base.update(over)
    return CampaignProduct(**base)


def dec(**over):
    base = dict(sku="166350900", merchant_sku="432085472", old_bid=18,
                new_bid=16, action="lower", loop="fast", reason="test")
    base.update(over)
    return Decision(**base)


def new_store():
    d = tempfile.mkdtemp()
    return Store(os.path.join(d, "test.db"))


def test_revenue_cache_roundtrip():
    st = new_store()
    rev = {
        "432085472": SkuRevenue(merchant_sku="432085472", revenue=97800,
                                gross_revenue=97800, cancelled=0, orders_count=2, units=2),
    }
    st.put_revenue_cache(rev, ts=1000)
    got = st.get_revenue_cache()
    assert set(got) == {"432085472"}
    assert got["432085472"].revenue == 97800
    assert got["432085472"].units == 2
    # повторная запись заменяет, а не дублирует
    st.put_revenue_cache({"432085472": SkuRevenue(merchant_sku="432085472", revenue=50000)}, ts=2000)
    got2 = st.get_revenue_cache()
    assert got2["432085472"].revenue == 50000
    print("✓ store: revenue_cache round-trip + замена")


def test_prev_avg_cpc_from_last_snapshot():
    st = new_store()
    assert st.get_prev_avg_cpc("166350900") is None       # снапшотов ещё нет
    st.save_products_snapshot([cp(avg_cpc=10.0)], ts=1000)
    st.save_products_snapshot([cp(avg_cpc=14.0)], ts=2000)
    assert st.get_prev_avg_cpc("166350900") == 14.0        # берём самый свежий
    print("✓ store: get_prev_avg_cpc берёт последний снапшот")


def test_get_latest_snapshot():
    st = new_store()
    assert st.get_latest_snapshot("166350900") is None       # снапшотов ещё нет
    st.save_products_snapshot([cp(bid=18, avg_cpc=10.0)], ts=1000)
    st.save_products_snapshot([cp(bid=20, avg_cpc=14.0)], ts=2000)
    snap = st.get_latest_snapshot("166350900")
    assert snap["bid"] == 20 and snap["avg_cpc"] == 14.0      # берём самый свежий
    print("✓ store: get_latest_snapshot берёт последний снапшот целиком")


def test_decisions_log_and_change_count():
    st = new_store()
    st.log_decision(dec(action="lower"), ts=1000, day="2026-08-09", applied=False)
    st.log_decision(dec(action="hold"), ts=1100, day="2026-08-09", applied=False)
    st.log_decision(dec(action="raise"), ts=1200, day="2026-08-09", applied=True)
    # hold не считается изменением
    assert st.count_changes_today("166350900", "2026-08-09") == 2
    # другой день — отдельный счётчик
    assert st.count_changes_today("166350900", "2026-08-10") == 0
    print("✓ store: decisions_log + count_changes_today (hold не в счёт)")


def test_daily_state_combines():
    st = new_store()
    st.save_products_snapshot([cp(avg_cpc=11.0)], ts=1000)
    st.log_decision(dec(action="lower"), ts=1100, day="2026-08-09", applied=False)
    state = st.build_daily_state(["166350900"], "2026-08-09")
    ds = state["166350900"]
    assert ds.changes_today == 1
    assert ds.prev_avg_cpc == 11.0
    print("✓ store: build_daily_state объединяет счётчик и prev avgCpc")


def test_tacos_daily_record():
    st = new_store()
    st.record_tacos("2026-08-09", "432085472", tacos=0.037, cost=3600, revenue=97800)
    st.record_tacos("2026-08-09", "432085472", tacos=0.040, cost=3900, revenue=97800)  # upsert
    rows = st.get_tacos_daily("2026-08-09")
    assert len(rows) == 1
    assert rows[0]["tacos"] == 0.040
    print("✓ store: tacos_daily upsert по (day, sku)")


def test_get_decisions_for_day():
    st = new_store()
    st.log_decision(dec(action="lower", reason="a"), ts=1000, day="2026-08-09", applied=True)
    st.log_decision(dec(action="hold", reason="b"), ts=1100, day="2026-08-09", applied=False)
    st.log_decision(dec(action="raise", reason="c"), ts=1200, day="2026-08-10", applied=True)
    rows = st.get_decisions_for_day("2026-08-09")
    assert len(rows) == 2                       # только этот день
    assert {r["action"] for r in rows} == {"lower", "hold"}
    assert rows[0]["reason"] == "a"             # порядок по ts
    print("✓ store: get_decisions_for_day (фильтр по дню, порядок по ts)")


def test_log_decision_writes_campaign_id():
    st = new_store()
    st.log_decision(dec(action="lower"), ts=1000, day="2026-08-09",
                    applied=True, campaign_id="2899523")
    rows = st.get_decisions_for_day("2026-08-09")
    assert rows[0]["campaign_id"] == "2899523"
    print("✓ store: log_decision пишет campaign_id")


def test_migration_adds_campaign_id_to_old_db():
    import os, sqlite3, tempfile
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    # старая схема decisions_log БЕЗ campaign_id
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE decisions_log (ts INTEGER, day TEXT, sku TEXT, "
        "merchant_sku TEXT, old_bid REAL, new_bid REAL, action TEXT, "
        "loop TEXT, reason TEXT, applied INTEGER);"
    )
    con.commit(); con.close()

    st = Store(path)   # инициализация должна добавить колонку
    cols = {r["name"] for r in st._conn.execute("PRAGMA table_info(decisions_log)")}
    assert "campaign_id" in cols, cols
    # и запись после миграции работает
    st.log_decision(dec(), ts=1, day="2026-08-09", applied=False, campaign_id="X")
    assert st.get_decisions_for_day("2026-08-09")[0]["campaign_id"] == "X"
    print("✓ store: миграция добавляет campaign_id в старую БД")


def test_snapshot_stores_feedback_metrics():
    """Поля обратной связи из кабинета доезжают до БД, а не теряются."""
    s = new_store()
    p = cp(views=4118, ctr=0.034, cr=0.086, crr=0.058, gmv=89655,
           transactions=12, buy_box=True)
    s.save_products_snapshot([p], ts=1000, campaign_id="3032419")

    row = s.get_latest_snapshot("166350900")
    assert row["views"] == 4118, row
    assert row["ctr"] == 0.034, row
    assert row["cr"] == 0.086, row
    assert row["crr"] == 0.058, row
    assert row["gmv"] == 89655, row
    assert row["transactions"] == 12, row
    assert row["buy_box"] == 1, row
    print("✓ снапшот хранит метрики обратной связи")


def test_migration_adds_feedback_columns_to_old_db():
    """Боевая БД со старой схемой доживает миграцию без потери строк."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE products_snapshot (
        ts INTEGER, sku TEXT, merchant_sku TEXT, bid REAL, avg_cpc REAL,
        score REAL, cost REAL, cost_today REAL, clicks INTEGER, carts INTEGER,
        product_state TEXT, price REAL)""")
    conn.execute("INSERT INTO products_snapshot (ts, sku, bid) VALUES (1, 'old', 5)")
    conn.commit()
    conn.close()

    s = Store(path)  # миграция происходит при открытии
    cols = {r["name"] for r in
            s._conn.execute("PRAGMA table_info(products_snapshot)")}
    assert {"campaign_id", "views", "ctr", "cr", "crr", "gmv",
            "transactions", "buy_box"} <= cols, cols

    row = s._conn.execute(
        "SELECT sku, bid FROM products_snapshot WHERE sku='old'").fetchone()
    assert row["sku"] == "old" and row["bid"] == 5, dict(row)
    print("✓ миграция старой БД добавляет колонки и не теряет строки")


def test_settings_audit():
    st = new_store()
    st.log_settings_change("admin", "min_bid", 1, 3, ts=1000)
    st.log_settings_change("admin", "dry_run", True, False, ts=1001)
    rows = st.get_settings_audit()
    assert len(rows) == 2
    assert rows[0]["field"] == "dry_run"          # свежие сверху (по ts DESC)
    assert rows[0]["old"] == "True" and rows[0]["new"] == "False"
    assert rows[1]["field"] == "min_bid"
    print("✓ store: settings_audit пишет и читает правки конфига")


def test_config_overrides_crud():
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    st = Store(p)
    try:
        assert st.get_overrides("campaign", "2899523") == {}
        st.set_override("campaign", "2899523", "bid_ceiling", "80", "admin", 111)
        st.set_override("campaign", "2899523", "min_bid", "5", "admin", 111)
        assert st.get_overrides("campaign", "2899523") == {"bid_ceiling": "80", "min_bid": "5"}
        # upsert перезаписывает
        st.set_override("campaign", "2899523", "bid_ceiling", "90", "admin", 222)
        assert st.get_overrides("campaign", "2899523")["bid_ceiling"] == "90"
        # разные scope изолированы
        st.set_override("sku", "SKU-1", "bid_ceiling", "40", "admin", 111)
        assert st.get_overrides("sku", "SKU-1") == {"bid_ceiling": "40"}
        assert st.get_overrides("campaign", "2899523")["bid_ceiling"] == "90"
        # delete
        st.delete_override("campaign", "2899523", "bid_ceiling")
        assert st.get_overrides("campaign", "2899523") == {"min_bid": "5"}
        st.delete_override("campaign", "2899523", "nope")  # no-op не падает
    finally:
        st.close()
    print("✓ store: config_overrides CRUD")


def _mk_product(sku, merchant_sku="m", bid=10.0):
    from connectors.marketing_client import CampaignProduct
    return CampaignProduct(
        sku=sku, merchant_sku=merchant_sku, campaign_product_id=0,
        bid=bid, avg_cpc=1.0, score=5.0, buy_box=False, product_state="Active",
        cost=0.0, cost_today=0.0, gmv=0.0, crr=0.0, cr=0.0, ctr=0.0,
        views=0, clicks=0, carts=0, transactions=0, price=100.0)


def test_snapshot_campaign_id_and_lists():
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    st = Store(p)
    try:
        st.save_products_snapshot([_mk_product("A", bid=10), _mk_product("B", bid=20)],
                                  ts=100, campaign_id="C1")
        st.save_products_snapshot([_mk_product("A", bid=15)], ts=200, campaign_id="C1")
        st.save_products_snapshot([_mk_product("Z", bid=99)], ts=150, campaign_id="C2")
        skus = st.get_campaign_skus("C1")
        by = {r["sku"]: r for r in skus}
        assert set(by) == {"A", "B"}, by
        assert by["A"]["bid"] == 15  # свежий снапшот A
        assert st.get_latest_snapshot_ts() == 200
        assert {r["sku"] for r in st.get_campaign_skus("C2")} == {"Z"}
    finally:
        st.close()
    print("✓ store: campaign_id в снапшотах + get_campaign_skus/get_latest_snapshot_ts")


def test_product_names_and_sku_map():
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    st = Store(p)
    try:
        # снапшоты дают мост sku -> merchant_sku (свежий побеждает)
        st.save_products_snapshot([_mk_product("A", merchant_sku="mA", bid=10),
                                   _mk_product("B", merchant_sku="mB", bid=20)],
                                  ts=100, campaign_id="C1")
        st.put_product_names({"mA": "Электробритва X", "mB": "Триммер Y"}, ts=100)
        # sku_name_map стыкует sku->merchant_sku->name И отвечает на ОБА ключа:
        # строки дашборда «Решения» идут по campaign sku, а строки TACoS — по
        # merchant_sku (record_tacos пишет merchant_sku). Карта должна крыть оба.
        m0 = st.get_sku_name_map()
        assert m0["A"] == "Электробритва X" and m0["B"] == "Триммер Y"
        assert m0["mA"] == "Электробритва X" and m0["mB"] == "Триммер Y"
        # get_campaign_skus подтягивает name LEFT JOIN-ом
        by = {r["sku"]: r for r in st.get_campaign_skus("C1")}
        assert by["A"]["name"] == "Электробритва X"
        # товар без имени: name = None, в sku_name_map его нет
        st.save_products_snapshot([_mk_product("Z", merchant_sku="mZ", bid=5)],
                                  ts=100, campaign_id="C1")
        assert "Z" not in st.get_sku_name_map()
        assert {r["sku"]: r["name"] for r in st.get_campaign_skus("C1")}["Z"] is None
        # пустое имя не затирает известное, апсерт обновляет
        st.put_product_names({"mA": "", "mB": "Триммер Y2"}, ts=200)
        m = st.get_sku_name_map()
        assert m["A"] == "Электробритва X" and m["B"] == "Триммер Y2"
    finally:
        st.close()
    print("✓ store: product_names + get_sku_name_map + name в get_campaign_skus")


def test_product_control_default_when_absent():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "t.db"))
        c = s.get_product_control("C1", "SKU_X")
        assert c.enabled is True and c.window_start == 0 and c.window_end == 24
        assert c.days_mask == 127
        s.close()
    print("✓ store: product_control default when absent")


def test_product_control_upsert_and_list():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "t.db"))
        s.set_product_control("C1", "S1", False, 8, 23, 31, "aidyn", 1000)
        got = s.get_product_control("C1", "S1")
        assert got.enabled is False and got.window_start == 8
        assert got.window_end == 23 and got.days_mask == 31
        # upsert перезаписывает
        s.set_product_control("C1", "S1", True, 9, 22, 127, "aidyn", 2000)
        assert s.get_product_control("C1", "S1").enabled is True
        # list по кампании
        s.set_product_control("C1", "S2", True, 0, 24, 127, "aidyn", 2000)
        m = s.list_product_control("C1")
        assert set(m.keys()) == {"S1", "S2"}
        assert isinstance(m["S1"], ProductControl)
        # all_product_controls
        allc = s.all_product_controls()
        assert ("C1", "S1") in {(cid, sku) for cid, sku, _ in allc}
        s.close()
    print("✓ store: product_control upsert и list + all_product_controls")


def test_bid_parking_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "t.db"))
        assert s.get_parked_bids("C1") == {}          # пусто по умолчанию
        s.set_parked_bid("C1", "S1", 180.0, 1000)
        s.set_parked_bid("C1", "S2", 75.0, 1000)
        s.set_parked_bid("C2", "S9", 10.0, 1000)      # другая кампания
        assert s.get_parked_bids("C1") == {"S1": 180.0, "S2": 75.0}
        # upsert перезаписывает
        s.set_parked_bid("C1", "S1", 200.0, 2000)
        assert s.get_parked_bids("C1")["S1"] == 200.0
        # clear убирает одну запись
        s.clear_parked_bid("C1", "S1")
        assert s.get_parked_bids("C1") == {"S2": 75.0}
        s.close()
    print("✓ store: bid_parking set/get/clear")


def test_metrics_daily_computes_and_stores_ratios():
    """Производные считаются при записи: графики читают таблицу как есть."""
    s = new_store()
    s.upsert_metrics_daily(
        day="2026-09-12", campaign_id="2899523", sku="166350902",
        merchant_sku="771930155", cost=2800, gmv=14000, views=7333,
        clicks=88, carts=1, transactions=1, ctr=0.012, cr=0.011,
        revenue=11429, ts=1000)

    row = s.get_metrics_for_day("2026-09-12")[0]
    assert abs(row["tacos"] - 2800 / 11429) < 1e-9, row["tacos"]
    assert abs(row["roas"] - 11429 / 2800) < 1e-9, row["roas"]
    assert abs(row["roas_gmv"] - 14000 / 2800) < 1e-9, row["roas_gmv"]
    assert row["clicks"] == 88 and row["carts"] == 1, dict(row)
    print("✓ metrics_daily считает tacos/roas/roas_gmv при записи")


def test_metrics_daily_upsert_overwrites_same_day():
    """Джоб гоняется раз в час и переписывает сегодня — дублей быть не должно."""
    s = new_store()
    common = dict(day="2026-09-12", campaign_id="c1", sku="s1",
                  merchant_sku="m1", gmv=0, views=10, clicks=5, carts=0,
                  transactions=0, ctr=0.5, cr=0.0)
    s.upsert_metrics_daily(cost=100, revenue=1000, ts=1, **common)
    s.upsert_metrics_daily(cost=250, revenue=1000, ts=2, **common)

    rows = s.get_metrics_for_day("2026-09-12")
    assert len(rows) == 1, rows
    assert rows[0]["cost"] == 250 and rows[0]["ts"] == 2, dict(rows[0])
    print("✓ metrics_daily перезаписывает строку того же дня")


def test_metrics_daily_null_only_when_denominator_is_zero():
    """Расход без выручки — это ROAS 0, а не дырка. TACoS при этом не определён."""
    s = new_store()
    s.upsert_metrics_daily(
        day="2026-09-12", campaign_id="c1", sku="s1", merchant_sku="m1",
        cost=500, gmv=0, views=10, clicks=2, carts=0, transactions=0,
        ctr=0.2, cr=0.0, revenue=0, ts=1)
    row = s.get_metrics_for_day("2026-09-12")[0]
    assert row["tacos"] is None, row["tacos"]
    assert row["roas"] == 0.0, row["roas"]
    assert row["roas_gmv"] == 0.0, row["roas_gmv"]

    # Выручка ещё не собрана (Shop API не ходил) — ROAS неизвестен, не ноль.
    s.upsert_metrics_daily(
        day="2026-09-13", campaign_id="c1", sku="s1", merchant_sku="m1",
        cost=500, gmv=1000, views=10, clicks=2, carts=0, transactions=0,
        ctr=0.2, cr=0.0, revenue=None, ts=1)
    row = s.get_metrics_for_day("2026-09-13")[0]
    assert row["roas"] is None, row["roas"]
    assert row["roas_gmv"] == 2.0, row["roas_gmv"]

    # Расхода нет — делить не на что.
    s.upsert_metrics_daily(
        day="2026-09-14", campaign_id="c1", sku="s1", merchant_sku="m1",
        cost=0, gmv=0, views=0, clicks=0, carts=0, transactions=0,
        ctr=0.0, cr=0.0, revenue=300, ts=1)
    row = s.get_metrics_for_day("2026-09-14")[0]
    assert row["roas"] is None and row["roas_gmv"] is None, dict(row)
    assert row["tacos"] == 0.0, row["tacos"]
    print("✓ NULL только там, где знаменатель ноль")


def test_metrics_daily_keeps_rows_of_both_campaigns():
    """Один товар в двух кампаниях: строки не должны затирать друг друга,
    иначе расход одной из кампаний исчезает молча."""
    s = new_store()
    common = dict(day="2026-09-12", sku="s1", merchant_sku="m1", gmv=0,
                  views=0, clicks=0, carts=0, transactions=0, ctr=0.0,
                  cr=0.0, revenue=1000, ts=1)
    s.upsert_metrics_daily(campaign_id="c1", cost=100, **common)
    s.upsert_metrics_daily(campaign_id="c2", cost=250, **common)

    rows = s.get_metrics_for_day("2026-09-12")
    assert len(rows) == 2, rows
    assert sorted(r["cost"] for r in rows) == [100, 250], rows
    print("✓ metrics_daily хранит строки обеих кампаний товара")


def test_metrics_series_ascending_and_limited():
    """Ряд для графика: свежие N дней, по возрастанию — как рисует ось X."""
    s = new_store()
    for n in range(1, 6):
        s.upsert_metrics_daily(
            day=f"2026-09-0{n}", campaign_id="c1", sku="s1", merchant_sku="m1",
            cost=n * 100, gmv=0, views=0, clicks=0, carts=0, transactions=0,
            ctr=0.0, cr=0.0, revenue=None, ts=n)
    # чужой товар не должен попасть в ряд
    s.upsert_metrics_daily(
        day="2026-09-03", campaign_id="c1", sku="s2", merchant_sku="m2",
        cost=999, gmv=0, views=0, clicks=0, carts=0, transactions=0,
        ctr=0.0, cr=0.0, revenue=None, ts=9)

    series = s.get_metrics_series("s1", days=3)
    assert [r["day"] for r in series] == ["2026-09-03", "2026-09-04", "2026-09-05"], series
    assert [r["cost"] for r in series] == [300, 400, 500], series
    print("✓ ряд metrics_daily отсортирован и ограничен по товару")


def test_ai_insight_roundtrip_and_overwrite():
    s = new_store()
    s.put_ai_insight(kind="product", scope_id="166350902", day="2026-09-12",
                     ts=100, text="первый разбор", model="claude-opus-5",
                     tokens_in=4000, tokens_out=1800)
    got = s.get_ai_insight("product", "166350902", "2026-09-12")
    assert got["text"] == "первый разбор", got
    assert got["model"] == "claude-opus-5" and got["tokens_in"] == 4000, got

    s.put_ai_insight(kind="product", scope_id="166350902", day="2026-09-12",
                     ts=200, text="пересчитали", model="claude-opus-5",
                     tokens_in=4200, tokens_out=1900)
    got = s.get_ai_insight("product", "166350902", "2026-09-12")
    assert got["text"] == "пересчитали" and got["ts"] == 200, got

    assert s.get_ai_insight("product", "нет-такого", "2026-09-12") is None
    print("✓ ai_insights: запись, перезапись, промах")


def test_count_ai_calls_counts_forced_recomputes():
    """Лимит расхода должен видеть принудительные пересчёты, иначе протекает:
    force перезаписывает строку, а деньги за вызов уже заплачены."""
    s = new_store()
    for ts in (1, 2, 3):
        s.put_ai_insight(kind="product", scope_id="s1", day="2026-09-12",
                         ts=ts, text="x", model="m", tokens_in=1, tokens_out=1)
    s.put_ai_insight(kind="daily", scope_id="", day="2026-09-12",
                     ts=4, text="y", model="m", tokens_in=1, tokens_out=1)
    s.put_ai_insight(kind="product", scope_id="s1", day="2026-09-13",
                     ts=5, text="z", model="m", tokens_in=1, tokens_out=1)

    assert s.count_ai_calls("2026-09-12") == 4, s.count_ai_calls("2026-09-12")
    assert s.count_ai_calls("2026-09-13") == 1
    assert s.count_ai_calls("2026-01-01") == 0
    print("✓ count_ai_calls считает вызовы, а не строки")


if __name__ == "__main__":
    test_revenue_cache_roundtrip()
    test_prev_avg_cpc_from_last_snapshot()
    test_get_latest_snapshot()
    test_decisions_log_and_change_count()
    test_daily_state_combines()
    test_tacos_daily_record()
    test_get_decisions_for_day()
    test_log_decision_writes_campaign_id()
    test_migration_adds_campaign_id_to_old_db()
    test_snapshot_stores_feedback_metrics()
    test_migration_adds_feedback_columns_to_old_db()
    test_settings_audit()
    test_config_overrides_crud()
    test_snapshot_campaign_id_and_lists()
    test_product_names_and_sku_map()
    test_product_control_default_when_absent()
    test_product_control_upsert_and_list()
    test_bid_parking_roundtrip()
    test_metrics_daily_computes_and_stores_ratios()
    test_metrics_daily_upsert_overwrites_same_day()
    test_metrics_daily_null_only_when_denominator_is_zero()
    test_metrics_daily_keeps_rows_of_both_campaigns()
    test_metrics_series_ascending_and_limited()
    test_ai_insight_roundtrip_and_overwrite()
    test_count_ai_calls_counts_forced_recomputes()
    print("-" * 60)
    print("✓ Все проверки store прошли")
