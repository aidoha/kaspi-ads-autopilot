"""
test_worker.py — тест оркестрации воркера с фейками (без APScheduler/сети/браузера).

Проверяем склейку тика: read маркетинг → снапшот → выручка из кэша → reconcile →
rules → apply/log. Ключевое: dry_run НЕ шлёт PUT (только логирует), боевой режим
шлёт PUT с новой ставкой; pause не имеет эндпоинта → PUT не шлётся; цикл выручки
наполняет кэш.

Запуск: .venv/bin/python test_worker.py
"""

import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from connectors.marketing_client import CampaignProduct
from core.revenue import SkuRevenue
from core.rules import RulesConfig
from core.store import Store
from worker import WorkerContext, run_tick, run_revenue_cycle

ALMATY = ZoneInfo("Asia/Almaty")
NOW = lambda: datetime(2026, 8, 9, 14, 0, tzinfo=ALMATY)
DAY = "2026-08-09"


def cp(**over):
    base = dict(
        sku="SKU1", merchant_sku="M1", campaign_product_id=1, bid=18, avg_cpc=12.5,
        score=7.0, buy_box=True, product_state="Active", cost=100, cost_today=100,
        gmv=0, crr=0, cr=0, ctr=0, views=0, clicks=10, carts=2, transactions=0, price=48900,
    )
    base.update(over)
    return CampaignProduct(**base)


class FakeMarketing:
    def __init__(self, products, dry_run):
        self._products = products
        self.dry_run = dry_run
        self.puts = []

    def get_campaign_products(self, campaign_id, start, end):
        self.seen_dates = (start, end)
        return self._products

    def update_bids(self, campaign_id, sku_list, bid):
        sent = not self.dry_run
        if sent:
            self.puts.append((list(sku_list), bid))
        return {"skuList": list(sku_list), "bid": bid, "dry_run": self.dry_run, "sent": sent}


class FakeCollector:
    def __init__(self, revenue):
        self._revenue = revenue

    def collect(self, window_days=2, now=None):
        return self._revenue


def store_with_revenue(rev):
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "w.db"))
    st.put_revenue_cache(rev, ts=1)
    return st


def ctx(marketing, store, dry_run):
    return WorkerContext(
        marketing=marketing, store=store,
        cfg=RulesConfig(dry_run=dry_run), now_fn=NOW,
    )


def test_dry_run_logs_but_no_put():
    # tacos = 100/5000 = 0.02 < 0.08 → slow raise, но dry_run → без PUT
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(bid=18)], dry_run=True)
    decisions = run_tick(ctx(fm, st, dry_run=True), loop="slow", campaign_id="2711494")

    assert len(decisions) == 1 and decisions[0].action == "raise"
    assert fm.puts == [], "dry_run НЕ должен слать PUT"
    assert st.count_changes_today("SKU1", DAY) == 1, "решение залогировано"
    assert st.get_tacos_daily(DAY)[0]["tacos"] == 100 / 5000
    print("✓ worker: dry_run логирует решение, но PUT не шлёт")


def test_live_run_sends_put_with_new_bid():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(bid=18)], dry_run=False)
    decisions = run_tick(ctx(fm, st, dry_run=False), loop="slow", campaign_id="2711494")

    assert decisions[0].action == "raise" and decisions[0].new_bid == 20
    assert fm.puts == [(["SKU1"], 20)], "боевой режим шлёт PUT с новой ставкой"
    print("✓ worker: боевой режим шлёт PUT с новой ставкой")


def test_fast_pause_cuts_bid_to_min():
    # costToday выше лимита → pause; маппим в минимальную ставку и шлём PUT
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(cost_today=9999, bid=18)], dry_run=False)
    decisions = run_tick(ctx(fm, st, dry_run=False), loop="fast", campaign_id="2711494")

    assert decisions[0].action == "pause"
    assert fm.puts == [(["SKU1"], RulesConfig().min_bid)], "pause режет ставку в пол"
    assert st.count_changes_today("SKU1", DAY) == 1, "pause в логе"
    print("✓ worker: fast pause → PUT минимальной ставки")


def test_revenue_cycle_fills_cache():
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "w.db"))
    collector = FakeCollector({"M1": SkuRevenue(merchant_sku="M1", revenue=77000, units=3)})
    c = WorkerContext(marketing=None, store=st,
                      cfg=RulesConfig(), revenue_collector=collector, now_fn=NOW)
    run_revenue_cycle(c)

    cache = st.get_revenue_cache()
    assert cache["M1"].revenue == 77000 and cache["M1"].units == 3
    print("✓ worker: revenue-цикл наполняет кэш выручки")


if __name__ == "__main__":
    test_dry_run_logs_but_no_put()
    test_live_run_sends_put_with_new_bid()
    test_fast_pause_cuts_bid_to_min()
    test_revenue_cycle_fills_cache()
    print("-" * 60)
    print("✓ Все проверки worker прошли")
