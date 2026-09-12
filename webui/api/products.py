"""products.py — список товаров и карточка одного товара."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from core.config_resolver import OVERRIDABLE_FIELDS, resolve_config
from core.rules import load_rules_config
from webui.api.deps import ApiContext, open_store, require_user

ALMATY = ZoneInfo("Asia/Almaty")


def _status(control, now) -> str:
    """Человеческий статус товара для колонки списка."""
    if control is None:
        return "активен"
    if not control.enabled:
        return "выключен"
    if not control.active_at(now):
        return "вне окна"
    return "активен"


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _effective(store, campaign_id: str, sku: str | None):
        """Эффективный конфиг и множество полей, заданных НА ЭТОМ уровне."""
        global_cfg = load_rules_config(ctx.rules_path)
        camp_ov = store.get_overrides("campaign", campaign_id)
        sku_ov = store.get_overrides("sku", sku) if sku else {}
        cfg = resolve_config(global_cfg, camp_ov, sku_ov)
        owned = sku_ov if sku else camp_ov
        values = {f: getattr(cfg, f) for f in OVERRIDABLE_FIELDS}
        return values, sorted(set(owned) & set(OVERRIDABLE_FIELDS))

    @router.get("/products")
    def products(campaign: str = "all", days: int = 14,
                 user: str = Depends(require_user)):
        days = max(1, min(int(days), 90))
        now = datetime.now(ALMATY)
        out = []
        with open_store(ctx) as store:
            names = store.get_sku_name_map()
            controls = {(cid, sku): ctl
                        for cid, sku, ctl in store.all_product_controls()}
            campaign_ids = ({campaign} if campaign != "all"
                            else {cid for cid, _ in controls} |
                                 _all_campaign_ids(store))
            for cid in sorted(campaign_ids):
                for row in store.get_campaign_skus(cid):
                    sku = row["sku"]
                    series = store.get_metrics_series(sku, days)
                    cost = sum(p["cost"] or 0 for p in series)
                    # Выручка уже свёрнута по дням внутри get_metrics_series —
                    # здесь остаётся сложить дни, а не строки кампаний.
                    revs = [p["revenue"] for p in series if p["revenue"] is not None]
                    revenue = sum(revs) if revs else None
                    clicks = sum(p["clicks"] or 0 for p in series)
                    carts = sum(p["carts"] or 0 for p in series)
                    views = sum(p["views"] or 0 for p in series)
                    ctl = controls.get((cid, sku))
                    spark = [s["bid"] for s in store.get_snapshot_series(sku, days)]
                    out.append({
                        "sku": sku,
                        "merchant_sku": row["merchant_sku"],
                        "campaign_id": cid,
                        "name": names.get(sku) or names.get(row["merchant_sku"]),
                        "bid": row["bid"],
                        "cost": cost,
                        "revenue": revenue,
                        "clicks": clicks,
                        "carts": carts,
                        "tacos": (cost / revenue) if revenue else None,
                        "roas": None if (not cost or revenue is None) else revenue / cost,
                        "ctr": (clicks / views) if views else None,
                        "cr": (carts / clicks) if clicks else None,
                        "enabled": True if ctl is None else bool(ctl.enabled),
                        "status": _status(ctl, now),
                        "bid_spark": spark,
                    })
        return {"products": out}

    @router.get("/products/{campaign_id}/{sku}")
    def product_detail(campaign_id: str, sku: str,
                       user: str = Depends(require_user)):
        day = datetime.now(ALMATY).date().isoformat()
        with open_store(ctx) as store:
            values, owned = _effective(store, campaign_id, sku)
            ctl = store.get_product_control(campaign_id, sku)
            decisions = store.get_decisions_for_sku_day(day, sku, 20, 0)
            name = store.get_sku_name_map().get(sku)
        return {
            "sku": sku, "campaign_id": campaign_id, "name": name,
            "values": values, "owned": owned,
            "control": {
                "enabled": bool(ctl.enabled),
                "window_start": ctl.window_start,
                "window_end": ctl.window_end,
                "days_mask": ctl.days_mask,
            },
            "decisions": decisions,
        }

    return router


def _all_campaign_ids(store) -> set[str]:
    """Кампании, по которым есть снапшоты. Кабинет для этого дёргать не нужно —
    список товаров рисуется из того, что уже собрано."""
    rows = store._conn.execute(
        "SELECT DISTINCT campaign_id FROM products_snapshot "
        "WHERE campaign_id IS NOT NULL AND campaign_id != ''").fetchall()
    return {r["campaign_id"] for r in rows}
