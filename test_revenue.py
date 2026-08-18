"""
Оффлайн-тест логики выручки. Kaspi не дёргаем — подсовываем фейковый merchant.
Проверяем: окно по Алматы, вычет отмен, группировку по merchantSku.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from connectors.merchant_client import Order, OrderEntry
from core.revenue import RevenueCollector, almaty_window_ms

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
ALMATY = ZoneInfo("Asia/Almaty")


def ms(dt): return int(dt.timestamp() * 1000)


# «Сейчас» фиксируем: среда, 9 авг 2026, 14:00 Алматы
NOW = datetime(2026, 8, 9, 14, 0, tzinfo=ALMATY)

# Фейковые заказы: (order_id, creation_dt, status, [(sku, price, qty), ...])
FAKE = {
    "o1": (datetime(2026, 8, 9, 11, 0, tzinfo=ALMATY), "ACCEPTED_BY_MERCHANT",
           [("432085472", 48900, 1)]),                       # сегодня, зачтён
    "o2": (datetime(2026, 8, 9, 12, 30, tzinfo=ALMATY), "CANCELLED",
           [("432085472", 48900, 1)]),                       # сегодня, ОТМЕНА → вычесть
    "o3": (datetime(2026, 8, 8, 20, 0, tzinfo=ALMATY), "COMPLETED",
           [("608122048", 59900, 1), ("743062317", 57490, 1)]),  # вчера, два SKU
    "o4": (datetime(2026, 8, 7, 10, 0, tzinfo=ALMATY), "COMPLETED",
           [("432085472", 48900, 1)]),                       # позавчера → ВНЕ окна 2д
    "o5": (datetime(2026, 8, 9, 9, 0, tzinfo=ALMATY), "RETURNED",
           [("608122048", 59900, 1)]),                       # сегодня, возврат → НЕ вычитаем
}


class FakeMerchant:
    def iter_orders(self, start_ms, end_ms):
        for oid, (dt, status, entries) in FAKE.items():
            c = ms(dt)
            if start_ms <= c <= end_ms:
                yield Order(order_id=oid, code=oid, total_price=0,
                            creation_date_ms=c, status=status, state="")

    def get_order_entries(self, order_id):
        _, _, entries = FAKE[order_id]
        return [OrderEntry(merchant_sku=s, name="x", quantity=q, total_price=p)
                for (s, p, q) in entries]


start, end = almaty_window_ms(2, now=NOW)
print("Окно:", datetime.fromtimestamp(start/1000, ALMATY), "→",
      datetime.fromtimestamp(end/1000, ALMATY))
print("-" * 60)

rc = RevenueCollector(FakeMerchant())
res = rc.collect(window_days=2, now=NOW)

for sku, r in sorted(res.items()):
    print(f"SKU {sku}: выручка={r.revenue:.0f}  gross={r.gross_revenue:.0f}  "
          f"отменено={r.cancelled:.0f}  заказов={r.orders_count}  штук={r.units}")

print("-" * 60)
# Проверки
r472 = res["432085472"]
assert r472.revenue == 48900, f"ожидали 48900 (o1), получили {r472.revenue}"
assert r472.cancelled == 48900, f"ожидали отмену 48900 (o2), получили {r472.cancelled}"
assert "432085472" in res and r472.orders_count == 1, "o4 (позавчера) должен быть ВНЕ окна"
r608 = res["608122048"]
# Договорённость: RETURNED НЕ вычитаем — возврат остаётся полноценной выручкой.
# o3 (59900, зачтён) + o5 (59900, RETURNED но считаем) = 119800.
assert r608.revenue == 119800, f"o3+o5(возврат считаем как выручку); revenue={r608.revenue}"
assert r608.gross_revenue == 119800, f"gross тоже 119800; получили {r608.gross_revenue}"
assert r608.cancelled == 0, "возврат НЕ отмена, cancelled должен быть 0"
# Название товара едет из OrderEntry.name — для подписи строк дашборда.
assert r472.name == "x", f"имя из OrderEntry.name должно попасть в SkuRevenue; got {r472.name!r}"
print("✓ Все проверки прошли: окно, вычет ТОЛЬКО отмен, возврат в выручке, группировка по SKU, имя товара")
