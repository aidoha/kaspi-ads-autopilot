"""
daypart.py — контрольный слой биддера: расписание работы и вкл/выкл по товару.

ЧИСТЫЙ модуль (без сети/БД). Хранит модель ProductControl (окно часов + дни +
флаг enabled) и функцию split_by_control, которая ДО правил разбивает товары на
«активные» (идут в fast/slow) и «контрольные решения» (вне окна → ставка в пол;
выключен → hold). Время подаётся снаружи (worker владеет now_fn) — так модуль
тестируется без часов.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from core.rules import Decision


@dataclass
class ProductControl:
    """Расписание/флаг для одного товара. Окно — полуинтервал [start, end) по часу
    (end=24 = круглосуточно). days_mask: биты Пн..Вс = 0..6 (как datetime.weekday())."""
    enabled: bool = True
    window_start: int = 0
    window_end: int = 24
    days_mask: int = 127

    def active_at(self, dt: datetime) -> bool:
        """Работает ли биддер по товару в момент dt: включён И день в маске И час в окне."""
        if not self.enabled:
            return False
        if not (self.days_mask & (1 << dt.weekday())):
            return False
        return self.window_start <= dt.hour < self.window_end


DEFAULT_CONTROL = ProductControl()


def split_by_control(reconciled, controls: dict, now: datetime,
                     min_bid_for: Callable[[str], float],
                     parked_bids: dict[str, float] | None = None,
                     ceiling_for: Callable[[str], float] | None = None):
    """Делит товары ДО правил: активные → в fast/slow; вне окна → ставка в пол;
    выключенные → hold. Товар без записи в controls считается активным (дефолт).
    min_bid_for(sku) — эффективный min_bid (учитывает per-SKU оверрайд).

    Парковка ставки на ночь и восстановление утром:
      • Первый выход из окна (ставка ещё не в полу) — роняем в пол И запоминаем
        прежнюю ставку в parking["park"][sku] (последний рабочий уровень).
      • Вход в окно, когда для товара есть запаркованная ставка — возвращаем её
        одним движением (raise), не заставляя движок разгонять с пола; sku идёт
        в parking["unpark"] (запись стирает worker). Восстановление зажато
        текущим потолком (ceiling_for) и не срабатывает, если ставка уже не ниже
        запомненной (напр. правили руками).
      • Выключенный товар не восстанавливаем и паркинг НЕ стираем — вернём при
        включении.
    parked_bids — снимок текущих парковок {sku: bid}; parking возвращается третьим
    элементом, чтобы worker сохранил/очистил их (модуль остаётся без БД)."""
    parked_bids = parked_bids or {}
    ceiling_for = ceiling_for or (lambda _s: float("inf"))
    active = []
    control_decisions: list[Decision] = []
    park: dict[str, float] = {}   # sku -> запомнить ставку
    unpark: list[str] = []        # sku -> стереть парковку
    for s in reconciled:
        ctrl = controls.get(s.sku, DEFAULT_CONTROL)
        if not ctrl.enabled:
            control_decisions.append(Decision(
                s.sku, s.merchant_sku, s.bid, s.bid, "hold", "none",
                "биддер выключен для товара"))
        elif not ctrl.active_at(now):
            floor = min_bid_for(s.sku)
            if s.bid <= floor:
                control_decisions.append(Decision(
                    s.sku, s.merchant_sku, s.bid, s.bid, "hold", "none",
                    "вне рабочего окна, ставка уже в полу"))
            else:
                # роняем в пол; запоминаем прежний уровень один раз за ночь
                if s.sku not in parked_bids:
                    park[s.sku] = s.bid
                control_decisions.append(Decision(
                    s.sku, s.merchant_sku, s.bid, floor, "lower", "none",
                    f"вне рабочего окна → ставка в пол {floor:g}"))
        elif s.sku in parked_bids:
            # утро: окно открылось, есть что вернуть
            unpark.append(s.sku)
            target = min(parked_bids[s.sku], ceiling_for(s.sku))
            if target > s.bid:
                control_decisions.append(Decision(
                    s.sku, s.merchant_sku, s.bid, target, "raise", "none",
                    f"вернули ставку в начале окна: {target:g}"))
            else:
                active.append(s)   # возвращать нечего — в движок как обычно
        else:
            active.append(s)
    return active, control_decisions, {"park": park, "unpark": unpark}
