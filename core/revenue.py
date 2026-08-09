"""
revenue.py — считает выручку по merchantSku за скользящее окно (по умолчанию 2 дня).

Ключевые решения (согласованы):
  • Привязка к дате СОЗДАНИЯ заказа (creationDate), не к доставке.
  • Границы дня — по Asia/Almaty, потом в мс (UTC под капотом httpx не важен,
    т.к. Kaspi отдаёт creationDate в epoch-мс, это абсолютное время).
  • Отмены (CANCELLED/CANCELLING) вычитаем из выручки.
  • Возвраты (RETURNED) НЕ трогаем.
  • Окно = tacos_window_days (2). Быстрее реагируем на ставки, но шумнее —
    поэтому предохранители в rules важны.

Кэширование состава заказов (entries) — обязанность вызывающего кода
(worker + SQLite): здесь чистая логика, чтобы её можно было юнит-тестить.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from connectors.merchant_client import MerchantClient, Order

log = logging.getLogger("revenue")

ALMATY = ZoneInfo("Asia/Almaty")


def almaty_window_ms(window_days: int, now: datetime | None = None) -> tuple[int, int]:
    """
    Возвращает (start_ms, end_ms) для окна последних `window_days` полных дней
    ВКЛЮЧАЯ сегодня, по времени Алматы.

    Пример при window_days=2 и «сейчас» = среда 14:00:
      start = вторник 00:00 Алматы
      end   = сейчас (среда 14:00)
    То есть «вчера + сегодня-до-сейчас».
    """
    now = now or datetime.now(ALMATY)
    now = now.astimezone(ALMATY)
    today_start = datetime.combine(now.date(), dtime.min, tzinfo=ALMATY)
    start = today_start - timedelta(days=window_days - 1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    return start_ms, end_ms


@dataclass
class SkuRevenue:
    merchant_sku: str
    revenue: float = 0.0          # выручка после вычета отмен
    gross_revenue: float = 0.0    # до вычета (для диагностики)
    cancelled: float = 0.0        # сколько срезали отменами
    orders_count: int = 0         # число зачтённых заказов с этим SKU
    units: int = 0                # штук продано (зачтённых)


class RevenueCollector:
    """
    Собирает выручку по merchantSku за окно.
    entries_loader — функция order_id -> list[OrderEntry], чтобы worker мог
    подсунуть кэширующую обёртку вместо прямого дёрганья API на каждый заказ.
    """

    def __init__(self, merchant: MerchantClient, entries_loader=None):
        self._merchant = merchant
        self._load_entries = entries_loader or merchant.get_order_entries

    def collect(self, window_days: int = 2, now: datetime | None = None) -> dict[str, SkuRevenue]:
        start_ms, end_ms = almaty_window_ms(window_days, now=now)
        log.info("Сбор выручки: окно %sд, [%s .. %s]", window_days, start_ms, end_ms)

        result: dict[str, SkuRevenue] = defaultdict(lambda: SkuRevenue(merchant_sku=""))

        counted_orders = 0
        cancelled_orders = 0

        for order in self._merchant.iter_orders(start_ms, end_ms):
            entries = self._load_entries(order.order_id)
            if not entries:
                continue

            if order.is_counted:
                counted_orders += 1
            else:
                cancelled_orders += 1

            for e in entries:
                sku = e.merchant_sku
                if not sku:
                    continue
                rec = result[sku]
                rec.merchant_sku = sku
                rec.gross_revenue += e.total_price
                if order.is_counted:
                    rec.revenue += e.total_price
                    rec.orders_count += 1
                    rec.units += e.quantity
                else:
                    rec.cancelled += e.total_price

        log.info("Заказов зачтено=%s, отменённых=%s, SKU с выручкой=%s",
                 counted_orders, cancelled_orders, len(result))
        return dict(result)
