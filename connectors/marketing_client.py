"""
marketing_client.py — клиент к маркетинговому кабинету Kaspi (marketing.kaspi.kz).

Официального API у кабинета НЕТ — работаем через его внутренние JSON-эндпоинты.
Авторизация — cookie-сессия (протухает). Куки СЮДА приходят ИЗВНЕ (от session_manager),
клиент их сам не добывает — так он не зависит от способа авторизации.

Назначение:
  - читать товары кампании со ставками и обратной связью (cost/avgCpc/score/carts/…);
  - PUT-ить новые ставки (батчом по массиву sku).

Ключ сшивки с выручкой (Shop API) — поле merchantSku.

ВАЖНО: на старте dry_run=True — считаем и логируем решения, но PUT НЕ шлём,
пока владелец не убедится в решениях глазами.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("marketing")

BASE_URL = "https://marketing.kaspi.kz"

# Заголовки, которые кабинет шлёт со своих XHR — воспроизводим, чтобы эндпоинт
# не отбил запрос как «не из браузера». Куки добавляются отдельно (снаружи).
DEFAULT_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
}


@dataclass
class CampaignProduct:
    """Товар в рекламной кампании: ставка + обратная связь по нему."""
    sku: str                  # ключ для PUT update-bid
    merchant_sku: str         # ключ сшивки с выручкой из Shop API
    campaign_product_id: int
    bid: float                # текущая ставка
    avg_cpc: float            # реальная цена клика (может быть НИЖЕ bid)
    score: float              # 0–10, качество товара в рекламе
    buy_box: bool
    product_state: str        # Active / Paused / OutOfStock
    # обратная связь
    cost: float
    cost_today: float
    gmv: float
    crr: float
    cr: float
    ctr: float
    views: int
    clicks: int
    carts: int
    transactions: int
    price: float


class MarketingClient:
    """
    Тонкий клиент к внутренним эндпоинтам маркетингового кабинета.
    Куки/сессию принимает извне. Ретраит транзиентные ошибки (429/5xx) с бэкоффом.
    """

    def __init__(
        self,
        merchant_id: str,
        cookies: dict | None = None,
        *,
        client: httpx.Client | None = None,
        dry_run: bool = True,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
    ):
        if not merchant_id:
            raise ValueError("merchant_id пуст — проверь конфиг (marketing merchantId)")
        self.merchant_id = str(merchant_id)
        self.dry_run = dry_run
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        if client is not None:
            # Инъекция готового клиента (тесты / кастомная сессия).
            self._client = client
        else:
            self._client = httpx.Client(
                base_url=BASE_URL,
                headers=DEFAULT_HEADERS,
                cookies=cookies or {},
                timeout=timeout,
            )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- низкоуровневый запрос с ретраями -----------------------------------

    def _request(self, method: str, url: str, **kwargs) -> dict:
        last_exc = None
        for attempt in range(self._max_retries):
            try:
                r = self._client.request(method, url, **kwargs)
                if r.status_code == 429 or r.status_code >= 500:
                    wait = self._backoff_base * (2 ** attempt)
                    log.warning("Marketing %s %s → %s, retry через %ss",
                                method, url, r.status_code, wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_exc = e
                wait = self._backoff_base * (2 ** attempt)
                log.warning("Marketing %s %s ошибка %s, retry через %ss",
                            method, url, e, wait)
                time.sleep(wait)
        raise RuntimeError(
            f"Marketing {method} {url} не ответил после {self._max_retries} попыток"
        ) from last_exc

    # ---- чтение товаров кампании --------------------------------------------

    def get_campaign_products(
        self, campaign_id: str, start_date: str, end_date: str
    ) -> list[CampaignProduct]:
        """
        Товары кампании со ставками и обратной связью за период [start_date, end_date]
        (даты в формате YYYY-MM-DD). Ответ: {result:"Ok", data:[...]}.

        В ответе НЕТ позиций в выдаче и ставок конкурентов — автопилот управляет
        по экономике (TACoS/avgCpc/cart-rate), а не по позиции. Это осознанно.
        """
        url = (
            f"/advertising/products/api/v5/merchant/{self.merchant_id}"
            f"/campaign/{campaign_id}/products"
        )
        params = {"StartDate": start_date, "EndDate": end_date}
        data = self._request("GET", url, params=params)

        out: list[CampaignProduct] = []
        for row in data.get("data", []):
            out.append(CampaignProduct(
                sku=str(row.get("sku", "")),
                merchant_sku=str(row.get("merchantSku", "")),
                campaign_product_id=int(row.get("campaignProductId", 0) or 0),
                bid=float(row.get("bid", 0) or 0),
                avg_cpc=float(row.get("avgCpc", 0) or 0),
                score=float(row.get("score", 0) or 0),
                buy_box=bool(row.get("buyBox", False)),
                product_state=str(row.get("productState", "")),
                cost=float(row.get("cost", 0) or 0),
                cost_today=float(row.get("costToday", 0) or 0),
                gmv=float(row.get("gmv", 0) or 0),
                crr=float(row.get("crr", 0) or 0),
                cr=float(row.get("cr", 0) or 0),
                ctr=float(row.get("ctr", 0) or 0),
                views=int(row.get("views", 0) or 0),
                clicks=int(row.get("clicks", 0) or 0),
                carts=int(row.get("carts", 0) or 0),
                transactions=int(row.get("transactions", 0) or 0),
                price=float(row.get("price", 0) or 0),
            ))
        return out

    # ---- обновление ставки ---------------------------------------------------

    def update_bids(self, campaign_id: str, sku_list: list[str], bid: float) -> dict:
        """
        PUT новой ставки для набора sku (батч в одном запросе).
        Тело: {"skuList": [...], "bid": N}. Ключ — sku (НЕ campaignProductId).

        При dry_run=True запрос НЕ отправляется — только возвращаем описание
        того, что было бы сделано (для лога решений). Каждый вызов — потенциальное
        движение живого бюджета, поэтому боевой PUT только при dry_run=False.
        """
        action = {
            "campaignId": str(campaign_id),
            "skuList": list(sku_list),
            "bid": bid,
            "dry_run": self.dry_run,
            "sent": False,
        }

        if self.dry_run:
            log.info("[dry_run] PUT ставка %s для sku=%s (НЕ отправлено)", bid, sku_list)
            return action

        url = (
            f"/advertising/products/api/v1/merchant/{self.merchant_id}"
            f"/campaign/{campaign_id}/products/update-bid"
        )
        body = {"skuList": list(sku_list), "bid": bid}
        resp = self._request("PUT", url, json=body)
        action["sent"] = True
        action["response"] = resp
        log.info("PUT ставка %s для sku=%s отправлена", bid, sku_list)
        return action
