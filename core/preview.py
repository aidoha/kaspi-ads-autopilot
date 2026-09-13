"""preview.py — «что биддер сделает сейчас», без отправки ставок.

Общая функция для JSON API (POST /api/products/{campaign_id}/{sku}/preview).
Вынесена отдельно, а не инлайнится в роутер: логика предсказания решения по
реальному рекламному бюджету не должна разойтись с тем, что реально считает
воркер, если её продублируют где-то ещё.

Превью СУХОЕ: читает снапшот, выручку из кэша и парковку, но ничего не пишет
и ничего не отправляет в кабинет. Просмотр страницы не должен менять состояние
биддера.
"""
from __future__ import annotations

from datetime import datetime

from connectors.marketing_client import CampaignProduct
from core.config_resolver import resolve_config
from core.daypart import split_by_control
from core.reconcile import reconcile
from core.rules import evaluate_fast, evaluate_slow, load_rules_config


def _product_from_snapshot(snap: dict) -> CampaignProduct:
    """Снапшот в БД — это строка, а правилам нужен CampaignProduct."""
    return CampaignProduct(
        sku=snap["sku"], merchant_sku=snap.get("merchant_sku", ""),
        campaign_product_id=0, bid=snap["bid"],
        avg_cpc=snap.get("avg_cpc", 0), score=snap.get("score", 0),
        buy_box=bool(snap.get("buy_box", False)),
        product_state=snap.get("product_state", "Active"),
        cost=snap.get("cost", 0), cost_today=snap.get("cost_today", 0),
        gmv=0, crr=0, cr=0, ctr=0, views=0,
        clicks=snap.get("clicks", 0), carts=snap.get("carts", 0),
        transactions=0, price=snap.get("price", 0))


def preview_decision(store, rules_path: str, campaign_id: str, sku: str,
                     now: datetime) -> dict | None:
    """Что биддер сделал бы с товаром прямо сейчас.

    None — по товару ещё нет снапшота, предсказывать нечего.
    {"control": {...}} — решение принял контрольный слой (выключен/вне окна),
    до правил дело не дошло.
    {"fast": {...}, "slow": {...}} — оба контура; они конкурируют, и владельцу
    важно видеть каждый.
    """
    # Снапшот берём ТОЙ кампании, для которой считаем превью: у товара из
    # двух кампаний снапшоты (и ставки) разные, а превью должно предсказывать
    # решение именно для запрошенной кампании, а не для случайной из них.
    snap = store.get_latest_snapshot(sku, campaign_id=campaign_id)
    if snap is None:
        return None

    g = load_rules_config(rules_path)
    camp_ov = store.get_overrides("campaign", campaign_id)
    controls = store.list_product_control(campaign_id)
    reconciled = reconcile([_product_from_snapshot(snap)],
                           store.get_revenue_cache())

    def cfg_for(s):
        return resolve_config(g, camp_ov, store.get_overrides("sku", s.sku))

    def min_bid_for(sk):
        return resolve_config(g, camp_ov, store.get_overrides("sku", sk)).min_bid

    def ceiling_for(sk):
        return resolve_config(g, camp_ov, store.get_overrides("sku", sk)).bid_ceiling

    # Парковку читаем, чтобы показать утреннее восстановление ставки, но НЕ
    # сохраняем и не чистим — это делает только боевой worker.run_tick.
    parked = store.get_parked_bids(campaign_id)
    active, ctrl_dec, _parking = split_by_control(
        reconciled, controls, now, min_bid_for, parked, ceiling_for)

    if ctrl_dec:
        d = ctrl_dec[0]
        return {"control": {"action": d.action, "reason": d.reason}}

    fast = evaluate_fast(active, cfg_for)
    slow = evaluate_slow(active, cfg_for)
    return {
        "fast": {"action": fast[0].action, "reason": fast[0].reason},
        "slow": {"action": slow[0].action, "reason": slow[0].reason},
    }
