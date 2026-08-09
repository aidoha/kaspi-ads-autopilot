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


if __name__ == "__main__":
    test_revenue_cache_roundtrip()
    test_prev_avg_cpc_from_last_snapshot()
    test_decisions_log_and_change_count()
    test_daily_state_combines()
    test_tacos_daily_record()
    test_get_decisions_for_day()
    print("-" * 60)
    print("✓ Все проверки store прошли")
