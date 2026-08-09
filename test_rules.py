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
    d = only(evaluate_fast([sr(cost_today=3500)], CFG))
    assert d.action == "pause"
    assert d.loop == "fast"
    assert d.changed is True
    assert "лимит" in d.reason.lower() or "cost" in d.reason.lower()
    print("✓ fast: costToday > дневного лимита → пауза")


def test_fast_zero_carts_cut_only_above_volume():
    # 60 кликов, 0 корзин → срезать шаг вниз
    d = only(evaluate_fast([sr(clicks=60, carts=0, bid=18)], CFG))
    assert d.action == "lower"
    assert d.new_bid == 16          # шаг 2 вниз
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


# ============ МЕДЛЕННЫЙ КОНТУР (по TACoS) ============

def test_slow_in_corridor_holds():
    d = only(evaluate_slow([sr(tacos=0.10)], CFG))
    assert d.action == "hold"
    print("✓ slow: TACoS в коридоре → держим")


def test_slow_below_corridor_raises():
    d = only(evaluate_slow([sr(tacos=0.04, bid=18, score=7.0)], CFG))
    assert d.action == "raise"
    assert d.new_bid == 20
    assert d.loop == "slow"
    print("✓ slow: TACoS ниже коридора → поднимаем на шаг")


def test_slow_above_corridor_lowers():
    d = only(evaluate_slow([sr(tacos=0.25, bid=18)], CFG))
    assert d.action == "lower"
    assert d.new_bid == 16
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
    d = only(evaluate_slow([sr(tacos=0.04, bid=50, score=7.0)], CFG))  # уже на потолке
    assert d.action == "hold"
    assert d.new_bid == 50
    print("✓ slow: у потолка ставки → hold (не выше bid_ceiling)")


def test_slow_paused_product_holds():
    d = only(evaluate_slow([sr(tacos=0.04, product_state="Paused")], CFG))
    assert d.action == "hold"
    print("✓ slow: товар не Active → держим")


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


if __name__ == "__main__":
    for fn in [
        test_fast_spend_cap_pauses,
        test_fast_zero_carts_cut_only_above_volume,
        test_fast_zero_carts_below_volume_holds,
        test_fast_cpc_spike_cuts,
        test_fast_never_raises,
        test_change_limit_blocks_fast_cut,
        test_slow_in_corridor_holds,
        test_slow_below_corridor_raises,
        test_slow_above_corridor_lowers,
        test_slow_cost_no_revenue_lowers,
        test_slow_low_score_blocks_raise,
        test_slow_ceiling_clamps_raise,
        test_slow_paused_product_holds,
        test_load_rules_config_reads_yaml,
    ]:
        fn()
    print("-" * 60)
    print("✓ Все проверки rules прошли")
