"""
reconcile.py — сшивка двух источников по merchantSku и расчёт TACoS по каждому SKU.

  cost (маркетинговый кабинет, CampaignProduct)
      ⨝ по merchant_sku
  revenue (Shop API, SkuRevenue из revenue.py)
      → TACoS = cost / полная_выручка_SKU

Почему так (согласовано): маркетинг видит только рекламно-атрибутированную выручку
и СИСТЕМАТИЧЕСКИ её занижает. Реальную полную выручку по товару даёт только Shop API.
Поэтому окупаемость (TACoS) считаем по Shop API, а расход — из маркетинга.

Результат несёт и поля для движка правил (bid/avg_cpc/score/carts/…), чтобы step 4
не пересшивал источники заново.

TACoS здесь — РАТИО (напр. 0.0368), не проценты. Целевой коридор в конфиге в тех же единицах.
"""

from __future__ import annotations

from dataclasses import dataclass

from connectors.marketing_client import CampaignProduct
from core.revenue import SkuRevenue


def compute_tacos(cost: float, revenue: float) -> float | None:
    """
    TACoS = cost / revenue.
      revenue == 0 → None (делить нельзя; «тратим, а реальной выручки нет» —
                    это отдельный сигнал для тормозного контура, НЕ бесконечность).
      cost == 0    → 0.0 (расхода нет).
    """
    if revenue == 0:
        return None
    return cost / revenue


@dataclass
class SkuReconciled:
    """Сшитая запись по одному рекламируемому SKU: экономика + поля для правил."""
    merchant_sku: str
    sku: str
    cost: float
    revenue: float
    tacos: float | None       # None если выручки нет (см. compute_tacos)
    has_revenue: bool
    # carry-through из маркетинга — нужно движку правил
    bid: float
    avg_cpc: float
    score: float
    buy_box: bool
    product_state: str
    cost_today: float
    clicks: int
    carts: int
    price: float
    # carry-through из выручки
    units: int
    orders_count: int


def reconcile(
    products: list[CampaignProduct],
    revenue_by_sku: dict[str, SkuRevenue],
) -> list[SkuReconciled]:
    """
    Идём по товарам КАМПАНИИ (это управляемая вселенная — на них можно ставить ставку),
    подтягиваем выручку по merchant_sku (0, если Shop API её не видел), считаем TACoS.
    SKU, которые есть в выручке, но не рекламируются, здесь не нужны — ставку по ним не ставим.
    """
    out: list[SkuReconciled] = []
    for p in products:
        rev = revenue_by_sku.get(p.merchant_sku)
        revenue = rev.revenue if rev else 0.0
        out.append(SkuReconciled(
            merchant_sku=p.merchant_sku,
            sku=p.sku,
            cost=p.cost,
            revenue=revenue,
            tacos=compute_tacos(p.cost, revenue),
            has_revenue=revenue > 0,
            bid=p.bid,
            avg_cpc=p.avg_cpc,
            score=p.score,
            buy_box=p.buy_box,
            product_state=p.product_state,
            cost_today=p.cost_today,
            clicks=p.clicks,
            carts=p.carts,
            price=p.price,
            units=rev.units if rev else 0,
            orders_count=rev.orders_count if rev else 0,
        ))
    return out
