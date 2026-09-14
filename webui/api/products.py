"""products.py — список товаров и карточка одного товара."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from core.config_resolver import OVERRIDABLE_FIELDS, resolve_config
from core.rules import load_rules_config
from webui.api.deps import ApiContext, open_store, require_user

ALMATY = ZoneInfo("Asia/Almaty")


def _spark(ticks: list[dict], days: int) -> list[float]:
    """Ряд ставки для спарклайна строки списка — по одной точке на сутки.

    Раньше сюда уезжал ВЕСЬ ряд снапшотов: воркер тикает раз в 5 минут, то
    есть четыре тысячи чисел за две недели на КАЖДЫЙ товар. В 84 пикселя
    спарклайна это рисовалось гребнем-штрихкодом (ночной пол дейпарта и
    дневной рабочий уровень чередуются четырнадцать раз на 84px — по шесть
    пикселей на цикл), из которого нельзя прочитать ни уровень, ни тренд.

    Точка суток — МЕДИАНА ставки за эти сутки, а не среднее и не последнее
    значение: биддер держит ставку в поле почти весь день, поэтому медиана
    равна рабочему уровню, и её не сдвигают ни ночной пол (треть суток на
    минималке утянула бы среднее вниз), ни единичный выброс. Сутки берутся
    по Алматы — по ним же живёт расписание биддера.
    """
    by_day: dict[str, list[float]] = {}
    for t in ticks:
        bid, ts = t.get("bid"), t.get("ts")
        if bid is None or ts is None:
            continue
        day = datetime.fromtimestamp(ts, ALMATY).date().isoformat()
        by_day.setdefault(day, []).append(float(bid))
    out = []
    for day in sorted(by_day)[-days:]:
        vals = sorted(by_day[day])
        mid = len(vals) // 2
        out.append(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2)
    return out


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
        """Одна строка на ТОВАР, а не на пару товар-кампания. Товар может
        вестись в нескольких кампаниях одновременно — метрики, ставка и
        спарклайн у него одни (уровня SKU), а `campaign_id` превратился бы
        в задвоение денег при сумме по списку. `campaign` — фильтр
        принадлежности, а не измерение строки: он решает, какие товары
        показать, но не режет их метрики по кампании."""
        days = max(1, min(int(days), 90))
        now = datetime.now(ALMATY)
        with open_store(ctx) as store:
            names = store.get_sku_name_map()
            controls = {(cid, sku): ctl
                        for cid, sku, ctl in store.all_product_controls()}
            all_campaign_ids = ({cid for cid, _ in controls} |
                                _all_campaign_ids(store))
            # Сшиваем sku → все кампании, где он встречен (нужно ДО фильтра
            # по campaign, иначе не узнаем полное членство товара).
            by_sku: dict[str, dict] = {}
            for cid in sorted(all_campaign_ids):
                for row in store.get_campaign_skus(cid):
                    entry = by_sku.setdefault(row["sku"], {
                        "merchant_sku": row["merchant_sku"],
                        "bid": row["bid"],
                        "bid_ts": row["ts"],
                        "campaign_ids": [],
                    })
                    # Ставка в списке — из САМОГО СВЕЖЕГО снапшота среди
                    # кампаний товара, а не от первой встреченной (порядок
                    # кампаний здесь — по алфавиту id, к активности отношения
                    # не имеет). Иначе список мог бы показать ставку неактивной
                    # кампании рядом со статусом «активен» от другой.
                    if row["ts"] is not None and (
                            entry["bid_ts"] is None or row["ts"] > entry["bid_ts"]):
                        entry["bid"] = row["bid"]
                        entry["merchant_sku"] = row["merchant_sku"]
                        entry["bid_ts"] = row["ts"]
                    entry["campaign_ids"].append(cid)

            out = []
            for sku, info in sorted(by_sku.items()):
                cids = sorted(info["campaign_ids"])
                if campaign != "all" and campaign not in cids:
                    continue
                series = store.get_metrics_series(sku, days)
                cost = sum(p["cost"] or 0 for p in series)
                # Выручка уже свёрнута по дням внутри get_metrics_series —
                # здесь остаётся сложить дни, а не строки кампаний.
                revs = [p["revenue"] for p in series if p["revenue"] is not None]
                revenue = sum(revs) if revs else None
                clicks = sum(p["clicks"] or 0 for p in series)
                carts = sum(p["carts"] or 0 for p in series)
                views = sum(p["views"] or 0 for p in series)
                spark = _spark(store.get_snapshot_series(sku, days), days)

                # Контроль — по каждой кампании свой (None = дефолт «активен»).
                # enabled = ведётся хоть где-то; status — от активной кампании,
                # а если активных нет — от первой по порядку.
                ctls = [controls.get((cid, sku)) for cid in cids]
                enabled = any(True if c is None else bool(c.enabled) for c in ctls)
                statuses = [_status(c, now) for c in ctls]
                status = "активен" if "активен" in statuses else statuses[0]

                out.append({
                    "sku": sku,
                    "merchant_sku": info["merchant_sku"],
                    "campaign_ids": cids,
                    "name": names.get(sku) or names.get(info["merchant_sku"]),
                    "bid": info["bid"],
                    "cost": cost,
                    "revenue": revenue,
                    "clicks": clicks,
                    "carts": carts,
                    "tacos": (cost / revenue) if revenue else None,
                    "roas": None if (not cost or revenue is None) else revenue / cost,
                    "ctr": (clicks / views) if views else None,
                    "cr": (carts / clicks) if clicks else None,
                    "enabled": enabled,
                    "status": status,
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
            decisions = store.get_decisions_for_sku_day(
                day, sku, 20, 0, campaign_id=campaign_id)
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

    @router.post("/products/{campaign_id}/{sku}/preview")
    def product_preview(campaign_id: str, sku: str,
                        user: str = Depends(require_user)):
        """Что биддер сделал бы с товаром прямо сейчас, ничего не отправляя.
        Логика решения по реальным деньгам живёт в core.preview и вызывается
        отсюда — единственная копия, чтобы правка решения не разошлась
        с тем, что реально считает воркер."""
        from core.preview import preview_decision
        with open_store(ctx) as store:
            got = preview_decision(store, ctx.rules_path, campaign_id, sku,
                                   datetime.now(ALMATY))
        return {"preview": got}

    @router.get("/products/{campaign_id}/{sku}/series")
    def product_series(campaign_id: str, sku: str, days: int = 14,
                       user: str = Depends(require_user)):
        """Ряды для графиков. Две шкалы времени лежат в РАЗНЫХ массивах:
        ставка и CPC имеют внутридневное разрешение (несколько тиков в сутки),
        а TACoS/CTR/CR — ровно по одной точке на день. Складывать их в общую
        сетку точек нельзя — получится ложь на обеих осях."""
        days = max(1, min(int(days), 90))
        with open_store(ctx) as store:
            # Ставка и решение — величины уровня кампании (в отличие от
            # `daily`, которая остаётся sku-wide — выручка к кампаниям не
            # привязана). Коридор в ответе посчитан для КОНКРЕТНОЙ кампании
            # из пути — ряды обязаны быть её же, иначе ответ противоречит
            # сам себе.
            ticks = store.get_snapshot_series(sku, days, campaign_id=campaign_id)
            daily = store.get_metrics_series(sku, days)
            marks = store.get_decision_markers(sku, days, campaign_id=campaign_id)
            values, _ = _effective(store, campaign_id, sku)
        return {
            "ticks": ticks,
            "daily": daily,
            "decisions": marks,
            "corridor": {"low": values["target_tacos_low"],
                         "high": values["target_tacos_high"]},
        }

    return router


def _all_campaign_ids(store) -> set[str]:
    """Кампании, по которым есть снапшоты. Кабинет для этого дёргать не нужно —
    список товаров рисуется из того, что уже собрано."""
    rows = store._conn.execute(
        "SELECT DISTINCT campaign_id FROM products_snapshot "
        "WHERE campaign_id IS NOT NULL AND campaign_id != ''").fetchall()
    return {r["campaign_id"] for r in rows}
