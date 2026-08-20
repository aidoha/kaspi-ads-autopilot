# test_daypart.py
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from core.daypart import ProductControl, DEFAULT_CONTROL, split_by_control

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

@dataclass
class FakeRec:  # мини-заглушка SkuReconciled: split_by_control читает только эти поля
    sku: str
    merchant_sku: str
    bid: float

def _min_bid_for(_sku):  # эффективный min_bid = 1 для всех
    return 1.0

def test_split_disabled_goes_to_hold():
    recs = [FakeRec("S1", "M1", 18)]
    ctrl = {"S1": ProductControl(enabled=False)}
    active, decs = split_by_control(recs, ctrl, _dt(h=12), _min_bid_for)
    assert active == []
    assert len(decs) == 1
    assert decs[0].action == "hold"
    assert "выключен" in decs[0].reason

def test_split_out_of_window_lowers_to_floor():
    recs = [FakeRec("S1", "M1", 18)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs = split_by_control(recs, ctrl, _dt(h=3), _min_bid_for)
    assert active == []
    assert decs[0].action == "lower"
    assert decs[0].new_bid == 1.0
    assert decs[0].old_bid == 18

def test_split_out_of_window_already_floor_is_hold():
    recs = [FakeRec("S1", "M1", 1)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs = split_by_control(recs, ctrl, _dt(h=3), _min_bid_for)
    assert decs[0].action == "hold"

def test_split_active_and_missing_go_to_rules():
    recs = [FakeRec("S1", "M1", 18), FakeRec("S2", "M2", 20)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}  # S2 — без записи
    active, decs = split_by_control(recs, ctrl, _dt(h=12), _min_bid_for)
    assert {r.sku for r in active} == {"S1", "S2"}
    assert decs == []

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_daypart")
