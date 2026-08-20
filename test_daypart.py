# test_daypart.py
from datetime import datetime
from zoneinfo import ZoneInfo
from core.daypart import ProductControl, DEFAULT_CONTROL

ALMATY = ZoneInfo("Asia/Almaty")

def _dt(y=2026, m=8, d=20, h=12):  # 2026-08-20 — четверг
    return datetime(y, m, d, h, tzinfo=ALMATY)

def test_default_control_active_any_time():
    assert DEFAULT_CONTROL.active_at(_dt(h=3)) is True
    assert DEFAULT_CONTROL.active_at(_dt(h=23)) is True

def test_disabled_never_active():
    assert ProductControl(enabled=False).active_at(_dt(h=12)) is False

def test_window_is_half_open_start_inclusive_end_exclusive():
    c = ProductControl(window_start=8, window_end=23)
    assert c.active_at(_dt(h=7)) is False
    assert c.active_at(_dt(h=8)) is True     # start включительно
    assert c.active_at(_dt(h=22)) is True
    assert c.active_at(_dt(h=23)) is False    # end эксклюзивно

def test_all_day_window():
    c = ProductControl(window_start=0, window_end=24)
    assert c.active_at(_dt(h=0)) is True
    assert c.active_at(_dt(h=23)) is True

def test_day_mask_excludes_day():
    # 2026-08-20 — четверг (weekday()==3). Маска без четверга = 127 & ~(1<<3).
    c = ProductControl(days_mask=127 & ~(1 << 3))
    assert c.active_at(_dt(h=12)) is False
    # понедельник 2026-08-17 (weekday()==0) — в маске
    assert c.active_at(_dt(d=17, h=12)) is True

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_daypart")
