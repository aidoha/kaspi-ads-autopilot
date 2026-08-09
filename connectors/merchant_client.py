"""
merchant_client.py — клиент к официальному Kaspi Shop API (kaspi.kz/shop/api/v2).

Авторизация: статичный X-Auth-Token из настроек кабинета продавца (Настройки → API).
Токен читается из окружения, НИКОГДА не хардкодится и не логируется.

Назначение: тянуть заказы за период (по дате создания) и их состав (entries),
чтобы посчитать выручку по merchantSku. Это «честный» источник выручки —
в отличие от маркетингового кабинета, он видит ВСЕ заказы по товару.

Документация формата: guide.kaspi.kz/partner/ru/shop/api/orders/q3201
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Iterator

import httpx

log = logging.getLogger("merchant")

BASE_URL = "https://kaspi.kz/shop/api/v2"

# Статусы, при которых заказ НЕ считается выручкой.
# Договорённость: отмены (CANCELLED/CANCELLING) вычитаем из выручки,
# возвраты (RETURNED) — оставляем (их в TACoS не учитываем как вычет).
EXCLUDED_STATUSES = {"CANCELLED", "CANCELLING"}


@dataclass
class OrderEntry:
    """Одна позиция внутри заказа."""
    merchant_sku: str          # merchantProduct.code — ключ сшивки с маркетингом
    name: str
    quantity: int
    total_price: float


@dataclass
class Order:
    """Заказ из Shop API (шапка). entries тянутся отдельным запросом."""
    order_id: str
    code: str
    total_price: float
    creation_date_ms: int
    status: str
    state: str
    pre_order: bool = False
    entries: list[OrderEntry] = field(default_factory=list)

    @property
    def is_counted(self) -> bool:
        """Считать ли заказ в выручку (не отменён)."""
        return self.status not in EXCLUDED_STATUSES


class MerchantClient:
    """
    Тонкий клиент к Shop API. Держит один httpx-клиент с токеном в заголовке.
    Ретраит транзиентные ошибки (429/5xx) с бэкоффом.
    """

    def __init__(self, auth_token: str, timeout: float = 30.0, max_retries: int = 3):
        if not auth_token:
            raise ValueError("X-Auth-Token пуст — проверь .env (KASPI_MERCHANT_TOKEN)")
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={
                "X-Auth-Token": auth_token,
                "Content-Type": "application/vnd.api+json",
                "Accept": "application/vnd.api+json",
            },
            timeout=timeout,
        )
        self._max_retries = max_retries

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- низкоуровневый GET с ретраями --------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        last_exc = None
        for attempt in range(self._max_retries):
            try:
                r = self._client.get(path, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    wait = 2 ** attempt
                    log.warning("Shop API %s → %s, retry через %ss", path, r.status_code, wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_exc = e
                wait = 2 ** attempt
                log.warning("Shop API %s ошибка %s, retry через %ss", path, e, wait)
                time.sleep(wait)
        raise RuntimeError(f"Shop API {path} не ответил после {self._max_retries} попыток") from last_exc

    # ---- заказы за период ----------------------------------------------------

    def iter_orders(self, start_ms: int, end_ms: int, page_size: int = 100) -> Iterator[Order]:
        """
        Итерирует заказы, СОЗДАННЫЕ в [start_ms, end_ms], постранично.
        Фильтр по дате создания — ключевое: привязываем выручку ко дню ЗАКАЗА,
        а не к дате доставки (заказ после 16:00 уезжает на доставку завтра,
        но реклама сработала сегодня → выручка должна лечь на сегодня).

        Один запрос покрывает ВСЕ каналы (NEW/PICKUP/DELIVERY/KASPI_DELIVERY/ARCHIVE),
        т.к. фильтр по state не задаём.
        """
        page = 0
        while True:
            params = {
                "page[number]": page,
                "page[size]": page_size,
                "filter[orders][creationDate][$ge]": start_ms,
                "filter[orders][creationDate][$le]": end_ms,
            }
            data = self._get("/orders", params=params)
            rows = data.get("data", [])
            for row in rows:
                attr = row.get("attributes", {})
                yield Order(
                    order_id=row["id"],
                    code=attr.get("code", ""),
                    total_price=float(attr.get("totalPrice", 0) or 0),
                    creation_date_ms=int(attr.get("creationDate", 0) or 0),
                    status=attr.get("status", ""),
                    state=attr.get("state", ""),
                    pre_order=bool(attr.get("preOrder", False)),
                )
            meta = data.get("meta", {})
            page_count = meta.get("pageCount", 1)
            page += 1
            if page >= page_count or not rows:
                break

    def get_order_entries(self, order_id: str) -> list[OrderEntry]:
        """
        Состав одного заказа. В Shop API entries приходят отдельной ссылкой
        (relationships → related), поэтому это ОТДЕЛЬНЫЙ запрос на каждый заказ.
        Дорого при большом объёме → выше по стеку это кэшируется в SQLite,
        закрытые дни не перезапрашиваются.
        """
        data = self._get(f"/orders/{order_id}/entries")
        out: list[OrderEntry] = []
        for row in data.get("data", []):
            attr = row.get("attributes", {})
            # merchantProduct.code лежит в атрибутах entry; в разных версиях
            # API поле может называться по-разному — пробуем известные варианты.
            msku = (
                attr.get("merchantProductCode")
                or attr.get("offerCode")
                or attr.get("code")
                or ""
            )
            out.append(OrderEntry(
                merchant_sku=str(msku),
                name=attr.get("name", "") or attr.get("productName", ""),
                quantity=int(attr.get("quantity", 0) or 0),
                total_price=float(attr.get("totalPrice", 0) or 0),
            ))
        return out
