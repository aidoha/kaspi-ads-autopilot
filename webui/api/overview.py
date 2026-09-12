"""overview.py — кампании и сводка KPI для шапки панели."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from webui.api.deps import ApiContext, open_store, require_user

ALMATY = ZoneInfo("Asia/Almaty")


def _days_range(days: int) -> list[str]:
    """Последние `days` календарных дней Алматы, включая сегодня."""
    today = datetime.now(ALMATY).date()
    return [(today - timedelta(days=n)).isoformat() for n in range(days)]


def build_router(ctx: ApiContext, budgets_fn) -> APIRouter:
    """budgets_fn инъектируется: бюджеты живут в кабинете Kaspi за
    Playwright-логином, и тест не должен туда ходить."""
    router = APIRouter(prefix="/api")

    @router.get("/campaigns")
    def campaigns(user: str = Depends(require_user)):
        budgets = budgets_fn() or {}
        return {
            "campaigns": [{"id": cid, "name": b.get("name", ""),
                           "daily_budget": b.get("daily_budget")}
                          for cid, b in budgets.items()],
            "budgets_available": bool(budgets),
        }

    @router.get("/overview")
    def overview(campaign: str = "all", days: int = 14,
                 user: str = Depends(require_user)):
        days = max(1, min(int(days), 90))
        wanted = set(_days_range(days))
        cost = revenue_sum = gmv = 0.0
        clicks = carts = 0
        # Выручка — величина уровня ТОВАРА, повторённая в кампанийных строках.
        # Суммировать её по строкам нельзя, поэтому собираем по (day, sku)
        # и складываем один раз на товаро-день.
        revenue_by_key: dict[tuple[str, str], float] = {}
        with open_store(ctx) as store:
            for day in wanted:
                for row in store.get_metrics_for_day(day):
                    if campaign != "all" and row["campaign_id"] != campaign:
                        continue
                    cost += row["cost"] or 0
                    gmv += row["gmv"] or 0
                    clicks += row["clicks"] or 0
                    carts += row["carts"] or 0
                    if row["revenue"] is not None:
                        revenue_by_key[(day, row["sku"])] = row["revenue"]
            last_ts = store.get_latest_snapshot_ts()
        revenue_sum = sum(revenue_by_key.values())

        return {
            "day": datetime.now(ALMATY).date().isoformat(),
            "days": days,
            "last_snapshot_ts": last_ts,
            "totals": {
                "cost": cost,
                "revenue": revenue_sum,
                "gmv": gmv,
                "clicks": clicks,
                "carts": carts,
                "tacos": (cost / revenue_sum) if revenue_sum else None,
                "roas": (revenue_sum / cost) if cost else None,
                "roas_gmv": (gmv / cost) if cost else None,
                "cr": (carts / clicks) if clicks else None,
            },
        }

    return router
