"""
test_store.py — тест SQLite-персистенции воркера.

Таблицы: products_snapshot, revenue_cache, decisions_log, tacos_daily.
Проверяем round-trip кэша выручки, prev avgCpc из прошлого снапшота (для
детекта скачка в быстром контуре), счётчик изменений/сутки (для предохранителя),
полный лог решений.

Запуск: .venv/bin/python test_store.py
"""

import os
import tempfile

from connectors.marketing_client import CampaignProduct
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
        # sku_name_map стыкует sku->merchant_sku->name
        assert st.get_sku_name_map() == {"A": "Электробритва X", "B": "Триммер Y"}
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
    test_settings_audit()
    test_config_overrides_crud()
    test_snapshot_campaign_id_and_lists()
    test_product_names_and_sku_map()
    print("-" * 60)
    print("✓ Все проверки store прошли")
