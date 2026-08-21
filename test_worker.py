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

from connectors.marketing_client import CampaignProduct, Campaign
from core.revenue import SkuRevenue
from core.rules import RulesConfig
from core.store import Store
from worker import WorkerContext, run_tick, run_revenue_cycle, run_cycle, load_cfg_safe

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
    def __init__(self, products, dry_run, campaigns=None):
        self._products = products
        self.dry_run = dry_run
        self._campaigns = campaigns or []
        self.puts = []
        self.ticked = []                 # campaign_id, по которым звали get_campaign_products

    def get_campaign_products(self, campaign_id, start, end):
        self.seen_dates = (start, end)
        self.ticked.append(campaign_id)
        return self._products

    def update_bids(self, campaign_id, sku_list, bid):
        sent = not self.dry_run
        if sent:
            self.puts.append((list(sku_list), bid))
        return {"skuList": list(sku_list), "bid": bid, "dry_run": self.dry_run, "sent": sent}

    def list_active_campaigns(self, start, end):
        self.seen_campaign_dates = (start, end)
        return list(self._campaigns)


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

    assert decisions[0].action == "raise" and decisions[0].new_bid == 22
    assert fm.puts == [(["SKU1"], 22)], "боевой режим шлёт PUT с новой ставкой"
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


def _camps(*pairs):
    return [Campaign(id=i, name=n, state="Enabled") for i, n in pairs]


def test_run_cycle_ticks_every_active_campaign():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(bid=18)], dry_run=True,
                       campaigns=_camps(("2899523", "Бритвы"), ("3032419", "Аэрогриль")))
    decisions = run_cycle(ctx(fm, st, dry_run=True), loop="slow")
    assert fm.ticked == ["2899523", "3032419"], fm.ticked
    assert len(decisions) == 2                      # по одному решению на кампанию
    logged = {r["campaign_id"] for r in st.get_decisions_for_day(DAY)}
    assert logged == {"2899523", "3032419"}, logged
    print("✓ worker: run_cycle гоняет тик по каждой активной кампании")


def test_run_cycle_allowlist_narrows():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(bid=18)], dry_run=True,
                       campaigns=_camps(("2899523", "Бритвы"), ("3032419", "Аэрогриль")))
    c = WorkerContext(marketing=fm, store=st, cfg=RulesConfig(dry_run=True),
                      campaign_ids=["3032419"], now_fn=NOW)
    run_cycle(c, loop="slow")
    assert fm.ticked == ["3032419"], fm.ticked
    print("✓ worker: run_cycle уважает allowlist campaign_ids")


def test_run_cycle_isolates_failing_campaign():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})

    class BoomOnFirst(FakeMarketing):
        def get_campaign_products(self, campaign_id, start, end):
            if campaign_id == "BAD":
                raise RuntimeError("кабинет отдал 500")
            return super().get_campaign_products(campaign_id, start, end)

    fm = BoomOnFirst([cp(bid=18)], dry_run=True,
                     campaigns=_camps(("BAD", "Плохая"), ("2899523", "Бритвы")))
    decisions = run_cycle(ctx(fm, st, dry_run=True), loop="slow")
    # первая упала, вторая обработана
    assert fm.ticked == ["2899523"], fm.ticked
    assert len(decisions) == 1
    print("✓ worker: run_cycle изолирует упавшую кампанию, остальные идут")


def test_run_cycle_empty_is_noop():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(bid=18)], dry_run=True, campaigns=[])
    decisions = run_cycle(ctx(fm, st, dry_run=True), loop="slow")
    assert decisions == [] and fm.ticked == []
    print("✓ worker: run_cycle с пустым списком — no-op")


def test_run_cycle_passes_campaign_budget_to_fast_brake():
    # cost_today=4000: под ФОЛБЭКОМ 3000 → пауза; но бюджет кампании 20000
    # (лимит 50% = 10000) → НЕ пауза. Значит бюджет реально проброшен в тормоз.
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing(
        [cp(cost_today=4000, bid=18)], dry_run=True,
        campaigns=[Campaign(id="2899523", name="Бритвы", state="Enabled", daily_budget=20000)])
    decisions = run_cycle(ctx(fm, st, dry_run=True), loop="fast")
    assert decisions[0].action == "hold", decisions[0].reason   # под фолбэком было бы pause
    print("✓ worker: run_cycle пробрасывает бюджет кампании в тормоз (лимит от бюджета)")


def test_run_tick_uses_per_sku_overrides():
    """Два SKU в одной кампании: у SKU B override max_bid_step=1 зажимает
    пропорциональный шаг (round(10×0.20)=2) ниже кэпа → срез мельче, чем у A."""
    st = store_with_revenue({})  # выручка не важна для быстрого контура
    st.set_override("sku", "B", "max_bid_step", "1", "test", 1)
    fm = FakeMarketing([cp(sku="A", merchant_sku="MA", carts=0, clicks=100, bid=10),
                        cp(sku="B", merchant_sku="MB", carts=0, clicks=100, bid=10)],
                       dry_run=True)
    run_tick(ctx(fm, st, dry_run=True), loop="fast", campaign_id="C1", daily_budget=0.0)
    decs = {r["sku"]: r for r in st.get_decisions_for_day(DAY)}
    assert decs["A"]["new_bid"] == 8   # глобальный шаг: round(10×0.20)=2, зажат [1,15]
    assert decs["B"]["new_bid"] == 9   # override кэп max_bid_step=1 < пропорциональный шаг 2
    assert {r["sku"] for r in st.get_campaign_skus("C1")} == {"A", "B"}
    print("✓ worker: run_tick резолвит конфиг на SKU + пишет campaign_id")


def test_run_tick_writes_marketing_product_names():
    """title из маркетинга → product_names каждый тик, чтобы имя было даже у
    товаров без заказов (у которых имя из Shop API взять неоткуда)."""
    st = store_with_revenue({})
    fm = FakeMarketing([cp(sku="A", merchant_sku="MA", name="Электробритва AYORA AY-97")],
                       dry_run=True)
    run_tick(ctx(fm, st, dry_run=True), loop="fast", campaign_id="C1", daily_budget=0.0)
    names = st.get_sku_name_map()
    assert names.get("A") == "Электробритва AYORA AY-97"    # по campaign sku
    assert names.get("MA") == "Электробритва AYORA AY-97"   # по merchant_sku (строки TACoS)
    print("✓ worker: run_tick пишет имена из маркетинга (title→product_names)")


def test_apply_logs_each_batch_before_next_put():
    """Боевой partial-write: если поздний PUT падает, аудит уже применённого
    (раннего) батча обязан остаться. Иначе ставка в кабинете изменена, а следов нет."""
    st = store_with_revenue({})
    st.set_override("sku", "B", "max_bid_step", "5", "test", 1)  # B → другой new_bid → другой батч

    class BoomOnSecondPut(FakeMarketing):
        def update_bids(self, campaign_id, sku_list, bid):
            self._calls = getattr(self, "_calls", 0) + 1
            if self._calls >= 2:
                raise RuntimeError("кабинет отдал 500 на втором PUT")
            return super().update_bids(campaign_id, sku_list, bid)

    fm = BoomOnSecondPut([cp(sku="A", merchant_sku="MA", carts=0, clicks=100, bid=10),
                          cp(sku="B", merchant_sku="MB", carts=0, clicks=100, bid=10)],
                         dry_run=False)
    try:
        run_tick(ctx(fm, st, dry_run=False), loop="fast", campaign_id="C1", daily_budget=0.0)
    except RuntimeError:
        pass  # второй PUT падает — ожидаемо; проверяем, что первый не потерян

    assert len(fm.puts) == 1, fm.puts                     # первый батч реально применён
    applied_sku = fm.puts[0][0][0]
    logged = {r["sku"] for r in st.get_decisions_for_day(DAY)}
    assert applied_sku in logged, f"применённая ставка {applied_sku} потеряна из аудита: {logged}"
    print("✓ worker: аудит применённого батча не теряется при падении позднего PUT")


def _ctx_with(products, dry_run=False, store=None, now=NOW):
    store = store or store_with_revenue({})
    mk = FakeMarketing(products, dry_run=dry_run)
    ctx = WorkerContext(marketing=mk, store=store, cfg=RulesConfig(min_bid=1),
                        now_fn=now)
    return ctx, mk, store


def test_run_tick_disabled_product_not_touched():
    # товар выключен в product_control → решение hold, PUT не шлётся даже в бою
    ctx, mk, store = _ctx_with([cp(sku="S1", bid=18)], dry_run=False)
    store.set_product_control("C1", "S1", False, 0, 24, 127, "t", 1)
    decisions = run_tick(ctx, "fast", "C1")
    d = [x for x in decisions if x.sku == "S1"][0]
    assert d.action == "hold" and "выключен" in d.reason
    assert mk.puts == []   # ничего не двинулось в кабинете
    print("✓ worker: выключенный товар не трогается контуром, hold без PUT")


def test_run_tick_out_of_window_lowers_to_floor():
    # окно 8..23, сейчас 14:00 — В окне; проверим ВНЕ окна отдельным now=3:00
    night = lambda: datetime(2026, 8, 9, 3, 0, tzinfo=ALMATY)
    ctx, mk, store = _ctx_with([cp(sku="S1", bid=18)], dry_run=False, now=night)
    store.set_product_control("C1", "S1", True, 8, 23, 127, "t", 1)
    decisions = run_tick(ctx, "fast", "C1")
    d = [x for x in decisions if x.sku == "S1"][0]
    assert d.action == "lower" and d.new_bid == 1
    assert (["S1"], 1) in mk.puts   # ставка в пол реально ушла PUT-ом (бой)
    print("✓ worker: вне рабочего окна ставка режется в пол реальным PUT")


def test_run_tick_in_window_runs_rules_as_before():
    # в окне (14:00) выключателей нет → обычная логика (hold без тормозных триггеров)
    ctx, mk, store = _ctx_with([cp(sku="S1", bid=18, clicks=10, carts=2)], dry_run=True)
    store.set_product_control("C1", "S1", True, 8, 23, 127, "t", 1)
    decisions = run_tick(ctx, "fast", "C1")
    d = [x for x in decisions if x.sku == "S1"][0]
    assert d.loop == "fast"   # прошёл через быстрый контур, а не контрольный слой
    print("✓ worker: в рабочем окне товар идёт в обычные правила как раньше")


def test_run_tick_fast_paces_by_time_of_day():
    # NOW=14:00 Алматы → day_frac≈0.583; лимит 1000, tol=1.0 → pace_limit≈583.
    # cost_today=800 ≥ 583 и < 1000 → мягкий троттлинг (lower), НЕ пауза.
    st = store_with_revenue({})
    fm = FakeMarketing([cp(sku="S1", merchant_sku="M1", bid=40, cost_today=800)],
                       dry_run=True)
    c = WorkerContext(
        marketing=fm, store=st,
        cfg=RulesConfig(daily_sku_cost_limit=1000, pace_tolerance=1.0,
                        bid_step_pct=0.20, max_bid_step=15, bid_ceiling=1000,
                        dry_run=True),
        now_fn=NOW,
    )
    decisions = run_tick(c, loop="fast", campaign_id="C1", daily_budget=0.0)
    d = next(x for x in decisions if x.sku == "S1")
    assert d.action == "lower", d.action
    assert "пейсинг" in d.reason
    print("✓ worker: fast-тик тормозит по пейсингу от времени суток")


def test_load_cfg_safe_hot_reload_and_fallback():
    from core.settings_io import save_settings, load_settings
    p = os.path.join(tempfile.mkdtemp(), "rules.yaml")
    save_settings(p, dict(load_settings(p), dry_run=True, min_bid=1))
    cfg1 = load_cfg_safe(p, None)
    assert cfg1.dry_run is True
    # правим файл — следующий вызов видит новое значение (hot-reload)
    save_settings(p, dict(load_settings(p), dry_run=False, min_bid=5))
    cfg2 = load_cfg_safe(p, cfg1)
    assert cfg2.dry_run is False and cfg2.min_bid == 5
    # битый файл → возвращаем прошлый cfg, не падаем
    with open(p, "w") as f:
        f.write("%%% not yaml : : :")
    cfg3 = load_cfg_safe(p, cfg2)
    assert cfg3 is cfg2
    print("✓ worker: load_cfg_safe — hot-reload + фолбэк на прошлый cfg при битом yaml")


if __name__ == "__main__":
    test_dry_run_logs_but_no_put()
    test_live_run_sends_put_with_new_bid()
    test_fast_pause_cuts_bid_to_min()
    test_revenue_cycle_fills_cache()
    test_run_cycle_ticks_every_active_campaign()
    test_run_cycle_allowlist_narrows()
    test_run_cycle_isolates_failing_campaign()
    test_run_cycle_empty_is_noop()
    test_run_cycle_passes_campaign_budget_to_fast_brake()
    test_run_tick_uses_per_sku_overrides()
    test_run_tick_writes_marketing_product_names()
    test_apply_logs_each_batch_before_next_put()
    test_run_tick_disabled_product_not_touched()
    test_run_tick_out_of_window_lowers_to_floor()
    test_run_tick_in_window_runs_rules_as_before()
    test_run_tick_fast_paces_by_time_of_day()
    test_load_cfg_safe_hot_reload_and_fallback()
    print("-" * 60)
    print("✓ Все проверки worker прошли")
