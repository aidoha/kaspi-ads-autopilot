"""
search_client.py — сбор органической выдачи Kaspi по ключевому слову.

Тянет GET kaspi.kz/yml/product-view/pl/filters (браузерный UA обязателен: app-UA
даёт 403), листает страницы по 12, ищет позицию НАШЕЙ карточки по product_id.
Никакой авторизации/cookies — это и даёт неперсонализированные («абсолютные») позиции.
Клиент НЕ пишет в БД: возвращает Listing, персистит его воркер.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote

import httpx

BASE_URL = "https://kaspi.kz/yml/product-view/pl/filters"

# Держать синхронно с merchant_client.BROWSER_UA — WAF режет не-браузерный UA.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class Card:
    rank: int
    product_id: str
    title: str
    price: float | None = None
    brand: str | None = None
    is_ad: bool = False


@dataclass
class Listing:
    keyword: str
    city_id: str
    our_product_id: str
    our_rank: int | None
    total: int
    cards: list[Card] = field(default_factory=list)


def parse_filters_page(data: dict, start_rank: int) -> tuple[list[Card], int]:
    total = int(data.get("total") or 0)
    raw = data.get("cards") or []
    cards: list[Card] = []
    for i, c in enumerate(raw):
        pid = str(c.get("configSku") or c.get("id") or "")
        price = c.get("unitSalePrice")
        if price is None:
            price = c.get("unitPrice")
        cards.append(Card(
            rank=start_rank + i,
            product_id=pid,
            title=c.get("title") or "",
            price=float(price) if price is not None else None,
            brand=c.get("brand"),
        ))
    return cards, total


def _build_url(keyword: str, city_id: str, zone: str, page: int) -> str:
    q = quote(keyword)
    return (f"{BASE_URL}?text={q}&page={page}&all=false&fl=true&ui=d"
            f"&q=:availableInZones:{zone}&i=-1&c={city_id}")


def _default_get(url: str) -> dict:
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
    r = httpx.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    return r.json()


def fetch_listing(keyword: str, city_id: str, zone: str, our_product_id: str,
                  max_depth: int = 100,
                  http_get: Callable[[str], dict] | None = None) -> Listing:
    get = http_get or _default_get
    all_cards: list[Card] = []
    total = 0
    our_rank: int | None = None
    page = 0
    while len(all_cards) < max_depth:
        url = _build_url(keyword, city_id, zone, page)
        payload = get(url)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        cards, total = parse_filters_page(data, start_rank=len(all_cards) + 1)
        if not cards:
            break
        all_cards.extend(cards)
        for c in cards:
            if c.product_id == our_product_id:
                our_rank = c.rank
                break
        if our_rank is not None:
            break
        page += 1
    cap = max_depth if our_rank is None else max(max_depth, our_rank)
    return Listing(keyword=keyword, city_id=city_id, our_product_id=our_product_id,
                   our_rank=our_rank, total=total, cards=all_cards[:cap])
