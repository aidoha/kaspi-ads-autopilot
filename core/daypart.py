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
