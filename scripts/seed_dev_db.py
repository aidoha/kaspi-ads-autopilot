"""seed_dev_db.py — наполнить отдельную dev-БД реалистичными тестовыми данными
за СЕГОДНЯ, чтобы видеть дашборд (решения/TACoS/бюджеты) с настоящими строками.

Прод-БД (db/autopilot.db) НЕ трогает. Пишет в DB_PATH (по умолчанию db/dev_seed.db).
Запуск:  DB_PATH=db/dev_seed.db python scripts/seed_dev_db.py
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from connectors.marketing_client import CampaignProduct
from core.rules import Decision
from core.store import Store

ALMATY = ZoneInfo("Asia/Almaty")

# Товары: sku (ключ маркетинга), merchant_sku (ключ выручки/сшивки с названием),
# name — человекочитаемое название, campaign_id, стартовая ставка/цена.
PRODUCTS = [
    # Кампания «Бритвы» (2899523)
    dict(sku="166350900", merchant_sku="432085472", campaign_id="2899523",
         name="Электробритва Xiaomi Mijia S500", bid=18, price=48900,
         cost=3600, cost_today=420, clicks=120, carts=9, tacos=0.037),
    dict(sku="166350901", merchant_sku="608122048", campaign_id="2899523",
         name="Триммер Philips OneBlade QP2620", bid=25, price=19990,
         cost=1500, cost_today=310, clicks=64, carts=3, tacos=0.128),
    dict(sku="166350902", merchant_sku="771930155", campaign_id="2899523",
         name="Бритва Gillette Fusion5 ProGlide (сменные кассеты 8 шт)", bid=32,
         price=12490, cost=2800, cost_today=560, clicks=88, carts=1, tacos=0.245),
    # Кампания «Аэрогриль 08.08.2026» (3032419)
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


def main() -> None:
    db_path = os.environ.get("DB_PATH", "db/dev_seed.db")
    day = datetime.now(ALMATY).date().isoformat()
    now = int(time.time())

    store = Store(db_path)
    try:
        # 1) Снапшот товаров (даёт «текущую ставку», сшивку sku↔merchant_sku, названия).
        snap = [
            CampaignProduct(
                sku=p["sku"], merchant_sku=p["merchant_sku"], campaign_product_id=0,
                bid=p["bid"], avg_cpc=p["bid"] * 0.72, score=7.0, buy_box=True,
                product_state="Active", cost=p["cost"], cost_today=p["cost_today"],
                gmv=p["price"] * p["carts"], crr=0.0, cr=0.0, ctr=0.0, views=0,
                clicks=p["clicks"], carts=p["carts"], transactions=0, price=p["price"],
            )
            for p in PRODUCTS
        ]
        # по кампаниям (save_products_snapshot проставляет один campaign_id на вызов)
        for cid in {p["campaign_id"] for p in PRODUCTS}:
            grp = [s for s, p in zip(snap, PRODUCTS) if p["campaign_id"] == cid]
            store.save_products_snapshot(grp, now, campaign_id=cid)

        # Названия товаров (в проде их пишет revenue-цикл из OrderEntry.name).
        store.put_product_names({p["merchant_sku"]: p["name"] for p in PRODUCTS}, now)

        # 2) Решения за день + 3) TACoS
        for p in PRODUCTS:
            bid = p["bid"]
            for i, (action, loop, reason, applied) in enumerate(DECISION_PLAN):
                old_bid = bid
                if action == "raise":
                    bid = round(bid * 1.1)
                elif action == "lower":
                    bid = round(bid * 0.9)
                d = Decision(sku=p["sku"], merchant_sku=p["merchant_sku"],
                             old_bid=old_bid, new_bid=bid, action=action,
                             loop=loop, reason=reason)
                store.log_decision(d, now - (len(DECISION_PLAN) - i) * 600, day,
                                   applied=bool(applied), campaign_id=p["campaign_id"])

            revenue = p["cost"] / p["tacos"] if p["tacos"] else 0.0
            store.record_tacos(day, p["sku"], p["tacos"], p["cost"], revenue)

        print(f"OK: засеяно {len(PRODUCTS)} товаров за {day} в {db_path}")
        print("SKU→название:")
        for p in PRODUCTS:
            print(f"  {p['sku']}  {p['name']}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
