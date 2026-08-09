"""
test_reconcile.py — оффлайн-тест сшивки маркетинг (cost) ⨝ выручка (Shop API) → TACoS.

TACoS = cost_рекламы / полная_выручка_по_SKU (ратио, не проценты).
Критичный кейс: cost > 0, но реальной выручки 0 → делить нельзя, tacos=None,
has_revenue=False. Это сигнал «тратим, продаж нет» для тормозного контура.

Запуск: .venv/bin/python test_reconcile.py
"""

from connectors.marketing_client import CampaignProduct
from core.revenue import SkuRevenue
from core.reconcile import reconcile, compute_tacos, SkuReconciled


def mp(**over) -> CampaignProduct:
    """Фабрика CampaignProduct с дефолтами (у датакласса своих дефолтов нет)."""
    base = dict(
        sku="166350900", merchant_sku="432085472", campaign_product_id=1,
        bid=18, avg_cpc=12.5, score=7.0, buy_box=True, product_state="Active",
        cost=3600, cost_today=420, gmv=97800, crr=3.68, cr=4.1, ctr=2.2,
        views=5400, clicks=120, carts=9, transactions=5, price=48900,
    )
    base.update(over)
    return CampaignProduct(**base)


def test_compute_tacos():
    assert compute_tacos(3600, 97800) == 3600 / 97800
    assert compute_tacos(0, 97800) == 0.0          # нет расхода → 0
    assert compute_tacos(3600, 0) is None          # нет выручки → делить нельзя
    assert compute_tacos(0, 0) is None             # ни расхода, ни выручки
    print("✓ compute_tacos: обычный / нулевой cost / нулевая выручка")


def test_reconcile_normal_join():
    products = [mp(merchant_sku="432085472", cost=3600)]
    revenue = {"432085472": SkuRevenue(merchant_sku="432085472", revenue=97800,
                                       units=2, orders_count=2)}
    out = reconcile(products, revenue)
    assert len(out) == 1
    r = out[0]
    assert isinstance(r, SkuReconciled)
    assert r.merchant_sku == "432085472"
    assert r.sku == "166350900"
    assert r.cost == 3600
    assert r.revenue == 97800
    assert r.tacos == 3600 / 97800
    assert r.has_revenue is True
    # carry-through полей для движка правил
    assert r.bid == 18 and r.avg_cpc == 12.5 and r.score == 7.0
    assert r.cost_today == 420 and r.clicks == 120 and r.carts == 9
    assert r.units == 2 and r.orders_count == 2
    print("✓ reconcile: обычная сшивка, TACoS и carry-through")


def test_reconcile_cost_but_no_revenue():
    # merchant_sku рекламируется, но в выручке его нет → revenue 0, tacos None
    products = [mp(merchant_sku="999", cost=500, carts=0, clicks=60)]
    out = reconcile(products, {})
    assert len(out) == 1
    r = out[0]
    assert r.revenue == 0.0
    assert r.tacos is None
    assert r.has_revenue is False
    assert r.cost == 500
    print("✓ reconcile: cost без выручки → tacos=None, has_revenue=False")


def test_reconcile_zero_cost_has_revenue():
    products = [mp(merchant_sku="432085472", cost=0)]
    revenue = {"432085472": SkuRevenue(merchant_sku="432085472", revenue=50000)}
    out = reconcile(products, revenue)
    r = out[0]
    assert r.tacos == 0.0
    assert r.has_revenue is True
    print("✓ reconcile: нулевой cost при наличии выручки → tacos=0")


def test_reconcile_preserves_all_products():
    products = [mp(sku="a", merchant_sku="1"), mp(sku="b", merchant_sku="2")]
    revenue = {"1": SkuRevenue(merchant_sku="1", revenue=1000)}
    out = reconcile(products, revenue)
    assert [r.sku for r in out] == ["a", "b"], "все рекламируемые товары в отчёте"
    print("✓ reconcile: все товары кампании попадают в результат")


if __name__ == "__main__":
    test_compute_tacos()
    test_reconcile_normal_join()
    test_reconcile_cost_but_no_revenue()
    test_reconcile_zero_cost_has_revenue()
    test_reconcile_preserves_all_products()
    print("-" * 60)
    print("✓ Все проверки reconcile прошли")
