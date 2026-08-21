"""
test_rules.py — оффлайн-тест движка ставок (два контура).

ЖЕЛЕЗНЫЙ ПРИНЦИП, который тут проверяется:
  • быстрый контур ТОЛЬКО тормозит/паузит (никогда не поднимает);
  • медленный контур двигает ставку по TACoS (день-масштаб): поднимает, если
    окупаемость хорошая, снижает, если плохая; НИКОГДА не разгоняет на
    внутридневном сигнале.
Плюс предохранители: лимит изменений/сутки, потолок/пол, шаг, низкий score.

Запуск: .venv/bin/python test_rules.py
"""

import os

from core.reconcile import SkuReconciled
from core.rules import (
    RulesConfig,
    DailyState,
    Decision,
    evaluate_fast,
    evaluate_slow,
    load_rules_config,
)

CFG = RulesConfig()  # консервативные дефолты (совпадают с config/rules.yaml)


def sr(**over) -> SkuReconciled:
    base = dict(
        merchant_sku="432085472", sku="166350900", cost=1000, revenue=20000,
        tacos=0.05, has_revenue=True, bid=18, avg_cpc=12.5, score=7.0,
        buy_box=True, product_state="Active", cost_today=400, clicks=100,
        carts=8, price=48900, units=2, orders_count=2,
    )
    base.update(over)
    return SkuReconciled(**base)


def only(decisions):
    assert len(decisions) == 1, f"ожидали 1 решение, {len(decisions)}"
    return decisions[0]


# ============ БЫСТРЫЙ КОНТУР (только тормозит) ============

def test_fast_spend_cap_pauses():
    d = only(evaluate_fast([sr(cost_today=3500, bid=18)], CFG))
    assert d.action == "pause"
    assert d.loop == "fast"
    assert d.changed is True
    assert d.new_bid == CFG.min_bid       # пауза = ставка в пол (нет эндпоинта паузы)
    assert "лимит" in d.reason.lower() or "cost" in d.reason.lower()
    print("✓ fast: costToday > дневного лимита → пауза (ставка в пол)")


def test_fast_zero_carts_cut_only_above_volume():
    # 60 кликов, 0 корзин → срезать шаг вниз
    d = only(evaluate_fast([sr(clicks=60, carts=0, bid=18)], CFG))
    assert d.action == "lower"
    assert d.new_bid == 14          # bid=18, шаг round(18×0.2)=4 вниз
    assert d.loop == "fast"
    print("✓ fast: 0 корзин на 60 кликах → срез ставки на шаг")


def test_fast_zero_carts_below_volume_holds():
    # 20 кликов, 0 корзин → НЕ резать (мало объёма, рано судить)
    d = only(evaluate_fast([sr(clicks=20, carts=0)], CFG))
    assert d.action == "hold"
    assert d.changed is False
    print("✓ fast: 0 корзин на 20 кликах → держим (порог по объёму)")


def test_fast_cpc_spike_cuts():
    st = {"166350900": DailyState(prev_avg_cpc=8.0)}
    d = only(evaluate_fast([sr(avg_cpc=13.0, clicks=10, carts=1)], CFG, st))
    assert d.action == "lower"      # 13 > 8*(1+0.5)=12
    print("✓ fast: скачок avgCpc > 50% → срез ставки")


def test_fast_never_raises():
    # даже при идеальной картине быстрый контур не поднимает
    d = only(evaluate_fast([sr(clicks=200, carts=50, cost_today=100)], CFG))
    assert d.action in ("hold",)
    assert d.action != "raise"
    print("✓ fast: никогда не поднимает ставку")


def test_change_limit_blocks_fast_cut():
    st = {"166350900": DailyState(changes_today=3)}
    d = only(evaluate_fast([sr(clicks=60, carts=0)], CFG, st))
    assert d.action == "hold"
    assert "лимит" in d.reason.lower()
    print("✓ предохранитель: исчерпан лимит изменений/сутки → hold")


def test_fast_spend_cap_from_campaign_budget():
    # бюджет 10000, доля 0.5 → лимит 5000
    d = only(evaluate_fast([sr(cost_today=5100, bid=18)], CFG, daily_budget=10000))
    assert d.action == "pause", d.reason
    assert "бюджет" in d.reason, d.reason
    # 4900 < 5000 → НЕ пауза (хотя это > фолбэка 3000 — значит взят бюджетный лимит)
    d2 = only(evaluate_fast([sr(cost_today=4900, bid=18)], CFG, daily_budget=10000))
    assert d2.action != "pause", d2.reason
    print("✓ rules: спенд-кап считается от бюджета кампании (×0.5)")


def test_fast_spend_cap_fallback_without_budget():
    # daily_budget=0 → фолбэк на daily_sku_cost_limit (3000); 3500 ≥ 3000 → пауза
    d = only(evaluate_fast([sr(cost_today=3500, bid=18)], CFG, daily_budget=0))
    assert d.action == "pause", d.reason
    assert "фолбэк" in d.reason or "3000" in d.reason, d.reason
    print("✓ rules: без бюджета — фолбэк на абсолютный лимит")


# ============ МЕДЛЕННЫЙ КОНТУР (по TACoS) ============

def test_slow_in_corridor_holds():
    d = only(evaluate_slow([sr(tacos=0.10)], CFG))
    assert d.action == "hold"
    print("✓ slow: TACoS в коридоре → держим")


def test_slow_below_corridor_raises():
    d = only(evaluate_slow([sr(tacos=0.04, bid=18, score=7.0)], CFG))
    assert d.action == "raise"
    assert d.new_bid == 22          # bid=18, шаг round(18×0.2)=4
    assert d.loop == "slow"
    print("✓ slow: TACoS ниже коридора → поднимаем на шаг")


def test_slow_above_corridor_lowers():
    d = only(evaluate_slow([sr(tacos=0.25, bid=18)], CFG))
    assert d.action == "lower"
    assert d.new_bid == 14          # bid=18, шаг round(18×0.2)=4
    print("✓ slow: TACoS выше коридора → снижаем")


def test_slow_cost_no_revenue_lowers():
    # tacos None (2 дня расход, реальной выручки нет) → снижаем (окупаемость худшая)
    d = only(evaluate_slow([sr(tacos=None, has_revenue=False, cost=800, revenue=0)], CFG))
    assert d.action == "lower"
    print("✓ slow: расход без выручки за окно → снижаем")


def test_slow_low_score_blocks_raise():
    # окупаемость зовёт поднять, но score низкий → НЕ задираем плохой товар
    d = only(evaluate_slow([sr(tacos=0.04, score=2.0)], CFG))
    assert d.action == "hold"
    assert "score" in d.reason.lower()
    print("✓ slow: низкий score → не поднимаем ставку")


def test_slow_ceiling_clamps_raise():
    # avg_cpc=30 держит ставку 50 в пределах хедрума (50 < 30×2=60), чтобы
    # тест бил именно в клэмп по потолку, а не в страж #3 avg_cpc-хедрума
    d = only(evaluate_slow([sr(tacos=0.04, bid=50, avg_cpc=30, score=7.0)], CFG))  # уже на потолке
    assert d.action == "hold"
    assert d.new_bid == 50
    print("✓ slow: у потолка ставки → hold (не выше bid_ceiling)")


def test_slow_paused_product_holds():
    d = only(evaluate_slow([sr(tacos=0.04, product_state="Paused")], CFG))
    assert d.action == "hold"
    print("✓ slow: товар не Active → держим")


def test_evaluate_fast_accepts_cfg_callable():
    # два SKU: у каждого свой резолвер конфига (per-SKU шаг снижения ставки).
    # bid_step_pct=0.0 фиксирует шаг = max_bid_step, чтобы тест проверял именно
    # то, что заявлен (разный ФИКСИРОВАННЫЙ шаг по SKU из per-sku cfg) — при
    # дефолтном пропорц. шаге (0.20) raw-шаг от bid=10 (=2) был бы меньше обоих
    # кэпов (2 и 5) и не показал бы разницу между A и B.
    a = sr(sku="A", carts=0, clicks=100, bid=10, product_state="Active")
    b = sr(sku="B", carts=0, clicks=100, bid=10, product_state="Active")
    cfgs = {
        "A": RulesConfig(max_bid_step=2, bid_step_pct=0.0),
        "B": RulesConfig(max_bid_step=5, bid_step_pct=0.0),
    }
    out = {d.sku: d for d in evaluate_fast([a, b], cfg=lambda s: cfgs[s.sku])}
    # оба режут ставку (0 корзин при 100 кликах), но на свой шаг
    assert out["A"].new_bid == 8   # 10 - 2
    assert out["B"].new_bid == 5   # 10 - 5
    print("✓ rules: evaluate_fast принимает cfg-резолвер (per-SKU)")


# ============ ЗАГРУЗКА КОНФИГА ============

def test_load_rules_config_reads_yaml():
    path = os.path.join(os.path.dirname(__file__), "config", "rules.yaml")
    cfg = load_rules_config(path)
    assert cfg.target_tacos_low == 0.08
    assert cfg.target_tacos_high == 0.15
    assert cfg.min_clicks_for_no_cart_cut == 40
    assert cfg.max_bid_step == 2
    assert cfg.bid_ceiling == 50
    assert cfg.dry_run is True
    print("✓ load_rules_config: значения читаются из config/rules.yaml")


# ============ ПРОПОРЦИОНАЛЬНЫЙ ШАГ СТАВКИ ============

def test_step_is_proportional_to_bid():
    # bid=100, pct=0.20 → шаг 20 (в пределах кэпа 50)
    # avg_cpc=60 держит ставку в пределах хедрума (100 < 60×2=120), чтобы тест
    # бил именно в размер шага, а не в страж #3 avg_cpc-хедрума
    cfg = RulesConfig(bid_step_pct=0.20, max_bid_step=50, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=100, avg_cpc=60, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise"
    assert d.new_bid == 120, d.new_bid
    print("✓ #1: шаг пропорционален ставке (100 → 120 при 20%)")


def test_step_clamped_by_max_step_cap():
    # bid=100, pct=0.20 → «сырой» шаг 20, но кэп 15 → шаг 15
    # avg_cpc=60 держит ставку в пределах хедрума (см. комментарий выше)
    cfg = RulesConfig(bid_step_pct=0.20, max_bid_step=15, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=100, avg_cpc=60, tacos=0.05, score=7.0)], cfg))
    assert d.new_bid == 115, d.new_bid
    print("✓ #1: шаг ограничен кэпом max_bid_step")


def test_step_floor_at_least_one_tenge():
    # bid=3, pct=0.20 → сырой 0.6 → округление дало бы 1; минимум 1₸
    cfg = RulesConfig(bid_step_pct=0.20, max_bid_step=15, min_bid=1, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=3, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise"
    assert d.new_bid == 4, d.new_bid
    print("✓ #1: шаг не меньше 1₸ (мелкая ставка всё равно двигается)")


def test_step_zero_pct_falls_back_to_fixed():
    # bid_step_pct=0 → старое поведение: фиксированный шаг max_bid_step
    cfg = RulesConfig(bid_step_pct=0.0, max_bid_step=2, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=18, tacos=0.05, score=7.0)], cfg))
    assert d.new_bid == 20, d.new_bid
    print("✓ #1: bid_step_pct=0 → фиксированный шаг (откат к старому)")


def test_step_rounds_to_whole_tenge():
    # bid=10, pct=0.15 → 1.5 → round → 2
    cfg = RulesConfig(bid_step_pct=0.15, max_bid_step=15, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=10, tacos=0.05, score=7.0)], cfg))
    assert d.new_bid == 12, d.new_bid
    print("✓ #1: шаг округляется до целого ₸")


# ============ ХЕДРУМ ПО avg_cpc (#3, страж подъёма) ============

def test_cpc_headroom_blocks_raise_when_bid_far_above_cpc():
    # bid=40, avg_cpc=12, headroom=2.0 → 40 > 12×2=24 → НЕ поднимаем
    cfg = RulesConfig(cpc_headroom=2.0, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=40, avg_cpc=12, tacos=0.05, score=7.0)], cfg))
    assert d.action == "hold", d.action
    assert "avg_cpc" in d.reason or "цены клика" in d.reason
    print("✓ #3: страж avg_cpc блокирует подъём, если ставка >> реальной цены клика")


def test_cpc_headroom_allows_raise_within_headroom():
    # bid=18, avg_cpc=12, headroom=2.0 → 18 < 24 → подъём разрешён
    cfg = RulesConfig(cpc_headroom=2.0, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=18, avg_cpc=12, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise", d.action
    print("✓ #3: подъём идёт, когда ставка в пределах хедрума")


def test_cpc_headroom_zero_disables_guard():
    # cpc_headroom=0 → страж выключен, подъём как раньше даже при bid>>cpc
    cfg = RulesConfig(cpc_headroom=0.0, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=40, avg_cpc=12, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise", d.action
    print("✓ #3: cpc_headroom=0 → страж выключен")


def test_cpc_headroom_ignored_when_no_cpc_data():
    # avg_cpc=0 (нет данных) → страж не мешает, обычная логика
    cfg = RulesConfig(cpc_headroom=2.0, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=40, avg_cpc=0, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise", d.action
    print("✓ #3: avg_cpc=0 → страж не срабатывает")


if __name__ == "__main__":
    for fn in [
        test_fast_spend_cap_pauses,
        test_fast_zero_carts_cut_only_above_volume,
        test_fast_zero_carts_below_volume_holds,
        test_fast_cpc_spike_cuts,
        test_fast_never_raises,
        test_change_limit_blocks_fast_cut,
        test_fast_spend_cap_from_campaign_budget,
        test_fast_spend_cap_fallback_without_budget,
        test_evaluate_fast_accepts_cfg_callable,
        test_slow_in_corridor_holds,
        test_slow_below_corridor_raises,
        test_slow_above_corridor_lowers,
        test_slow_cost_no_revenue_lowers,
        test_slow_low_score_blocks_raise,
        test_slow_ceiling_clamps_raise,
        test_slow_paused_product_holds,
        test_load_rules_config_reads_yaml,
        test_step_is_proportional_to_bid,
        test_step_clamped_by_max_step_cap,
        test_step_floor_at_least_one_tenge,
        test_step_zero_pct_falls_back_to_fixed,
        test_step_rounds_to_whole_tenge,
        test_cpc_headroom_blocks_raise_when_bid_far_above_cpc,
        test_cpc_headroom_allows_raise_within_headroom,
        test_cpc_headroom_zero_disables_guard,
        test_cpc_headroom_ignored_when_no_cpc_data,
    ]:
        fn()
    print("-" * 60)
    print("✓ Все проверки rules прошли")
