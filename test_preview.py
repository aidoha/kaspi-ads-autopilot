"""test_preview.py — превью решения биддера без отправки ставок.

Проверяет core.preview.preview_decision — единственную функцию, которая
предсказывает решения биддера для JSON API (POST .../preview), не отправляя
ставок и не расходясь с тем, что реально считает воркер.

Запуск: .venv/bin/python test_preview.py
"""
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from connectors.marketing_client import CampaignProduct
from core.preview import preview_decision
from core.rules import RulesConfig
from core.settings_io import save_settings, SETTINGS_FIELDS
from core.store import Store

ALMATY = ZoneInfo("Asia/Almaty")


def _env():
    d = tempfile.mkdtemp()
    rules = os.path.join(d, "rules.yaml")
    save_settings(rules, {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS})
    return Store(os.path.join(d, "a.db")), rules


def _snap(store, campaign_id, sku, bid=40, clicks=100, carts=5):
    p = CampaignProduct(
        sku=sku, merchant_sku="m" + sku, campaign_product_id=1, bid=bid,
        avg_cpc=bid * 0.7, score=7.0, buy_box=True, product_state="Active",
        cost=500, cost_today=100, gmv=0, crr=0, cr=0, ctr=0, views=clicks * 20,
        clicks=clicks, carts=carts, transactions=0, price=10000)
    store.save_products_snapshot([p], ts=1_700_000_000, campaign_id=campaign_id)


def test_preview_returns_none_without_snapshot():
    """По товару, которого воркер ещё не видел, предсказывать нечего."""
    store, rules = _env()
    assert preview_decision(store, rules, "c1", "нет-такого",
                            datetime.now(ALMATY)) is None
    print("✓ превью: без снапшота возвращает None")


def test_preview_shows_both_loops_for_active_product():
    """Активный товар: показываем оба контура — что решит тормозной и что
    окупаемостный. Владельцу важны оба, они конкурируют."""
    store, rules = _env()
    _snap(store, "c1", "s1")
    got = preview_decision(store, rules, "c1", "s1", datetime.now(ALMATY))
    assert set(got) == {"fast", "slow"}, got
    assert got["fast"]["action"] and got["fast"]["reason"], got["fast"]
    assert got["slow"]["action"] and got["slow"]["reason"], got["slow"]
    print("✓ превью: активный товар показывает оба контура")


def test_preview_reports_control_layer_when_disabled():
    """Выключенный товар до правил не доходит — решение принимает
    контрольный слой, и превью обязано показать именно его."""
    store, rules = _env()
    _snap(store, "c1", "s1")
    store.set_product_control("c1", "s1", enabled=False, window_start=0,
                              window_end=24, days_mask=127, user="a", ts=1)
    got = preview_decision(store, rules, "c1", "s1", datetime.now(ALMATY))
    assert set(got) == {"control"}, got
    assert got["control"]["reason"], got
    print("✓ превью: выключенный товар показывает решение контрольного слоя")


def test_preview_does_not_touch_parking():
    """Превью сухое. Парковку ставки читаем, чтобы показать восстановление,
    но записывать её имеет право только боевой worker.run_tick — иначе
    просмотр страницы менял бы состояние биддера."""
    store, rules = _env()
    _snap(store, "c1", "s1")
    store.set_parked_bid("c1", "s1", 55, ts=1)
    before = dict(store.get_parked_bids("c1"))
    preview_decision(store, rules, "c1", "s1", datetime.now(ALMATY))
    assert store.get_parked_bids("c1") == before, "превью изменило парковку"
    print("✓ превью не трогает запаркованные ставки")


if __name__ == "__main__":
    test_preview_returns_none_without_snapshot()
    test_preview_shows_both_loops_for_active_product()
    test_preview_reports_control_layer_when_disabled()
    test_preview_does_not_touch_parking()
    print("-" * 60)
    print("✓ Все проверки превью прошли")
