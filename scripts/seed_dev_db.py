"""seed_dev_db.py — наполнить отдельную dev-БД реалистичными тестовыми данными
за последние 14 дней, чтобы видеть React-панель (список товаров, KPI, графики
карточки товара, решения/TACoS/бюджеты) с настоящими строками, а не прочерками.

Прод-БД (db/autopilot.db) НЕ трогает. Пишет в DB_PATH (по умолчанию db/dev_seed.db).
Запуск:  DB_PATH=db/dev_seed.db python scripts/seed_dev_db.py

Наполняет:
  - products_snapshot — по одной точке в день на товар (меняющаяся ставка,
    а не один плоский снапшот) — на этом строится спарклайн и график ставки;
  - metrics_daily — подневные метрики за 14 дней (на этом строится
    /api/overview, /api/products и будущие графики карточки товара);
  - decisions_log / tacos_daily — «сегодняшние» решения и TACoS одним днём:
    их читает вкладка «Решения» карточки товара и /api/overview.

У «Бритвы Gillette» (единственного флагового товара из макета) ряд
ухудшающийся, с разрывом в середине периода (день без данных вообще — на
нём проверяется, что график рвётся, а не соединяется прямой) и двумя
последними днями без выручки (revenue=None — «Shop API ещё не опрашивали»,
прочерк, а не ноль). Числа — те же самые ряды, что в утверждённом макете
(docs/design/autopilot-mockup.html, SERIES), чтобы дев-данные и макет не
расходились.
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from connectors.marketing_client import CampaignProduct
from core.rules import Decision
from core.store import Store

ALMATY = ZoneInfo("Asia/Almaty")

HISTORY_DAYS = 14      # окно истории в metrics_daily/products_snapshot
GAP_DAY_INDEX = 6       # день (0 = 13 дней назад … 13 = сегодня) БЕЗ метрик вообще
UNKNOWN_REVENUE_DAYS = {12, 13}   # вчера и сегодня — «выручку ещё не собирали»
GILLETTE_SKU = "166350902"

# Товары: sku (ключ маркетинга), merchant_sku (ключ выручки/сшивки с названием),
# name — человекочитаемое название, campaign_id. bid/cost/clicks/carts/tacos —
# «сегодняшние» цифры для decisions_log/tacos_daily, не трогаем их смысл,
# они были такими и до этой правки.
PRODUCTS = [
    dict(sku="166350900", merchant_sku="432085472", campaign_id="2899523",
         name="Электробритва Xiaomi Mijia S500", bid=18, price=48900,
         cost=3600, cost_today=420, clicks=120, carts=9, tacos=0.037),
    dict(sku="166350901", merchant_sku="608122048", campaign_id="2899523",
         name="Триммер Philips OneBlade QP2620", bid=25, price=19990,
         cost=1500, cost_today=310, clicks=64, carts=3, tacos=0.128),
    dict(sku=GILLETTE_SKU, merchant_sku="771930155", campaign_id="2899523",
         name="Бритва Gillette Fusion5 ProGlide (сменные кассеты 8 шт)", bid=32,
         price=12490, cost=2800, cost_today=560, clicks=88, carts=1, tacos=0.245),
    dict(sku="177420310", merchant_sku="915004821", campaign_id="3032419",
         name="Аэрогриль Philips HD9252/90 Airfryer", bid=40, price=89900,
         cost=5200, cost_today=980, clicks=140, carts=12, tacos=0.058),
    dict(sku="177420311", merchant_sku="915004822", campaign_id="3032419",
         name="Аэрогриль Redmond RAF-5501 (5.5 л)", bid=28, price=45900,
         cost=2100, cost_today=140, clicks=52, carts=2, tacos=0.171),
]

# Несколько решений на товар за день (fast/slow тики), последнее — «применено».
DECISION_PLAN = [
    ("hold", "fast", "TACoS в коридоре, avgCpc стабилен", 0),
    ("raise", "slow", "cart-rate выше цели — поднимаем ставку", 1),
    ("lower", "fast", "TACoS выше потолка — снижаем", 1),
]

# Ряды для Gillette — дословно из SERIES утверждённого макета
# (docs/design/autopilot-mockup.html, строки 599-606): ставка растёт и
# снижается, TACoS девять дней подряд выше потолка, CTR/CR монотонно падают.
GILLETTE_BID   = [32, 32, 34, 34, 36, 36, 38, 38, 40, 38, 36, 34, 32, 32]
GILLETTE_CPC   = [23.0, 23.8, 24.1, 24.9, 25.6, 26.2, 27.0, 28.1, 29.2,
                  28.0, 26.4, 25.1, 24.0, 23.0]
GILLETTE_TACOS = [11.0, 12.2, 13.6, 13.1, 15.8, 17.0, 19.2, 20.4, 23.1,
                  25.0, 26.8, 26.0, 25.1, 24.5]           # %
GILLETTE_CTR   = [2.1, 2.0, 1.9, 1.9, 1.8, 1.7, 1.6, 1.5, 1.4,
                  1.3, 1.3, 1.2, 1.2, 1.2]                # %
GILLETTE_CR    = [3.4, 3.1, 2.8, 2.6, 2.2, 2.0, 1.8, 1.6, 1.4,
                  1.3, 1.2, 1.1, 1.1, 1.1]                # %
GILLETTE_CLICKS = [5, 5, 6, 6, 6, 7, 7, 7, 8, 6, 6, 5, 5, 9]     # сумма = 88

# Остальные товары — линейный тренд ctr/cr/bid от «13 дней назад» к «сегодня».
# bid[1] обязан совпасть с «сегодняшним» PRODUCTS[...]["bid"] — на нём
# держится свежесть последнего снапшота (get_latest_snapshot/список бота).
TREND_PARAMS = {
    "166350900": dict(total_clicks=120, ctr=(3.0, 4.1), cr=(6.0, 7.5), bid=(14, 18)),
    "166350901": dict(total_clicks=64,  ctr=(2.2, 2.6), cr=(4.0, 4.7), bid=(22, 25)),
    "177420310": dict(total_clicks=140, ctr=(2.8, 3.6), cr=(7.5, 9.0), bid=(34, 40)),
    "177420311": dict(total_clicks=52,  ctr=(2.3, 1.9), cr=(4.5, 3.8), bid=(30, 28)),
}


def _day_ts(d: date, hour: int = 12) -> int:
    """Метка времени полудня Алматы для дня истории (не «сейчас» — иначе все
    точки легли бы в одну секунду и график ставки не имел бы разрешения)."""
    return int(datetime.combine(d, dtime(hour, 0), tzinfo=ALMATY).timestamp())


def _interp(start: float, end: float, n: int) -> list[float]:
    """n значений линейно от start до end включительно (start — 13 дней
    назад, end — сегодня)."""
    if n == 1:
        return [end]
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def _daily_series(total_clicks: int, ctr: tuple[float, float],
                  cr: tuple[float, float], bid: tuple[float, float],
                  n: int = HISTORY_DAYS) -> list[dict]:
    """n дней метрик с линейным трендом CTR/CR/ставки. Клики распределены по
    дням пропорционально CTR (там, где карточка заметнее — больше показов и
    кликов), с поправкой округления на последнем дне, чтобы сумма кликов за
    период совпала с total_clicks — иначе числа по дням выглядели бы
    случайными, а не «откуда взялся заявленный итог»."""
    ctr = _interp(ctr[0], ctr[1], n)
    cr = _interp(cr[0], cr[1], n)
    bids = _interp(bid[0], bid[1], n)
    weights = [max(c, 0.1) for c in ctr]
    wsum = sum(weights)
    clicks = [round(total_clicks * w / wsum) for w in weights]
    clicks[-1] += total_clicks - sum(clicks)     # компенсируем ошибку округления
    out = []
    for i in range(n):
        bid = round(bids[i])
        out.append({
            "clicks": max(clicks[i], 0),
            "ctr": ctr[i] / 100,
            "cr": cr[i] / 100,
            "bid": bid,
            "avg_cpc": round(bid * 0.72, 2),
        })
    return out


def _distribute_carts(expected: list[float]) -> list[int]:
    """Распределяет дробное «ожидаемое число корзин в день» (clicks × CR)
    по дням методом наибольших остатков, чтобы сумма совпала с округлённым
    итогом за период. Поштучный round() на каждый день терял бы весь итог
    там, где трафика мало — «88 кликов и 1 корзина» превратилось бы в
    14 дней по округлённому нулю."""
    total = round(sum(expected))
    floors = [int(e) for e in expected]
    remainder = total - sum(floors)
    order = sorted(range(len(expected)), key=lambda i: expected[i] - floors[i], reverse=True)
    out = floors[:]
    for i in order[:max(remainder, 0)]:
        out[i] += 1
    return out


def _gillette_series(n: int = HISTORY_DAYS) -> list[dict]:
    return [
        {
            "clicks": GILLETTE_CLICKS[i],
            "ctr": GILLETTE_CTR[i] / 100,
            "cr": GILLETTE_CR[i] / 100,
            "bid": GILLETTE_BID[i],
            "avg_cpc": GILLETTE_CPC[i],
            "tacos_pct": GILLETTE_TACOS[i],
        }
        for i in range(n)
    ]


def main() -> None:
    db_path = os.environ.get("DB_PATH", "db/dev_seed.db")
    today = datetime.now(ALMATY).date()
    day = today.isoformat()
    now = int(time.time())

    store = Store(db_path)
    try:
        store.put_product_names({p["merchant_sku"]: p["name"] for p in PRODUCTS}, now)

        # ---- 1) История: products_snapshot (несколько точек, меняющаяся
        # ставка) + metrics_daily (подневные метрики за HISTORY_DAYS) ------
        by_campaign: dict[str, list[dict]] = {}
        for p in PRODUCTS:
            by_campaign.setdefault(p["campaign_id"], []).append(p)

        series_by_sku = {
            p["sku"]: (_gillette_series() if p["sku"] == GILLETTE_SKU
                       else _daily_series(**TREND_PARAMS[p["sku"]]))
            for p in PRODUCTS
        }
        # Корзины — методом наибольших остатков (см. _distribute_carts),
        # а не round() на каждый день, иначе редкие корзины на
        # низкотрафичных товарах (Gillette: 1 корзина на 88 кликов)
        # потерялись бы в округлении вниз на каждом отдельном дне.
        for p in PRODUCTS:
            series = series_by_sku[p["sku"]]
            expected = [s["clicks"] * s["cr"] for s in series]
            for s, carts in zip(series, _distribute_carts(expected)):
                s["carts"] = carts

        written_days = 0
        skipped_gap = 0
        unknown_revenue = 0
        for i in range(HISTORY_DAYS):
            d = today - timedelta(days=HISTORY_DAYS - 1 - i)
            day_str = d.isoformat()
            ts = now if i == HISTORY_DAYS - 1 else _day_ts(d)

            for cid, prods in by_campaign.items():
                snap = []
                for p in prods:
                    s = series_by_sku[p["sku"]][i]
                    carts = s["carts"]
                    views = round(s["clicks"] / s["ctr"]) if s["ctr"] else 0
                    cost = round(s["clicks"] * s["avg_cpc"], 2)
                    gmv = carts * p["price"]
                    snap.append(CampaignProduct(
                        sku=p["sku"], merchant_sku=p["merchant_sku"],
                        campaign_product_id=0, bid=s["bid"], avg_cpc=s["avg_cpc"],
                        score=7.0, buy_box=True, product_state="Active",
                        cost=cost, cost_today=cost, gmv=gmv, crr=0.0,
                        cr=s["cr"], ctr=s["ctr"], views=views,
                        clicks=s["clicks"], carts=carts, transactions=carts,
                        price=p["price"],
                    ))
                store.save_products_snapshot(snap, ts, campaign_id=cid)

            for p in PRODUCTS:
                if p["sku"] == GILLETTE_SKU and i == GAP_DAY_INDEX:
                    # День без данных вообще — на нём проверяется, что линия
                    # графика рвётся, а не соединяется прямой через пропуск.
                    skipped_gap += 1
                    continue

                s = series_by_sku[p["sku"]][i]
                carts = s["carts"]
                views = round(s["clicks"] / s["ctr"]) if s["ctr"] else 0
                cost = round(s["clicks"] * s["avg_cpc"], 2)
                gmv = carts * p["price"]

                revenue_known = not (p["sku"] == GILLETTE_SKU and i in UNKNOWN_REVENUE_DAYS)
                if not revenue_known:
                    revenue = None
                    unknown_revenue += 1
                elif p["sku"] == GILLETTE_SKU:
                    # У Gillette расход — от РЕАЛЬНЫХ CPC (GILLETTE_CPC, дословно
                    # из макета, растёт вместе со ставкой). Выручка отсюда же
                    # подобрана так, чтобы TACoS = cost/revenue лёг ровно на
                    # целевую кривую GILLETTE_TACOS (11%→26%), а не на carts×price:
                    # при клика́х 5-9/день и CR из макета (1.1-3.4%) корзина
                    # набирается раз в несколько дней, и revenue=carts×price был
                    # бы известным нулём почти везде — TACoS обнулялся бы (ноль
                    # в знаменателе — «не определено» по конвенции
                    # upsert_metrics_daily) вместо кривой, ради которой этот
                    # товар и задуман флаговым: следующая задача рисует график
                    # TACoS с залитым целевым коридором, и только на этом товаре
                    # видно, что линия его пересекает.
                    revenue = round(cost / (s["tacos_pct"] / 100), 2)
                else:
                    revenue = round(gmv * 0.92, 2)   # минус отмены, как в реальном Shop API

                store.upsert_metrics_daily(
                    day=day_str, campaign_id=p["campaign_id"], sku=p["sku"],
                    merchant_sku=p["merchant_sku"], cost=cost, gmv=gmv,
                    views=views, clicks=s["clicks"], carts=carts,
                    transactions=carts, ctr=s["ctr"], cr=s["cr"],
                    revenue=revenue, ts=ts,
                )
                written_days += 1

        # ---- 2) «Сегодня»: решения биддера + tacos_daily ----
        for p in PRODUCTS:
            bid = p["bid"]
            for idx, (action, loop, reason, applied) in enumerate(DECISION_PLAN):
                old_bid = bid
                if action == "raise":
                    bid = round(bid * 1.1)
                elif action == "lower":
                    bid = round(bid * 0.9)
                dec = Decision(sku=p["sku"], merchant_sku=p["merchant_sku"],
                               old_bid=old_bid, new_bid=bid, action=action,
                               loop=loop, reason=reason)
                store.log_decision(dec, now - (len(DECISION_PLAN) - idx) * 600, day,
                                   applied=bool(applied), campaign_id=p["campaign_id"])

            revenue = p["cost"] / p["tacos"] if p["tacos"] else 0.0
            store.record_tacos(day, p["sku"], p["tacos"], p["cost"], revenue)

        print(f"OK: засеяно {len(PRODUCTS)} товаров, {HISTORY_DAYS} дней истории "
              f"({written_days} строк metrics_daily, {skipped_gap} день пропущен "
              f"намеренно, {unknown_revenue} строки без выручки) в {db_path}")
        print("SKU→название:")
        for p in PRODUCTS:
            print(f"  {p['sku']}  {p['name']}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
