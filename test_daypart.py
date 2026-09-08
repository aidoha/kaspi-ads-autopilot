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
    active, decs, _park = split_by_control(recs, ctrl, _dt(h=12), _min_bid_for)
    assert active == []
    assert len(decs) == 1
    assert decs[0].action == "hold"
    assert "выключен" in decs[0].reason

def test_split_out_of_window_lowers_to_floor():
    recs = [FakeRec("S1", "M1", 18)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs, _park = split_by_control(recs, ctrl, _dt(h=3), _min_bid_for)
    assert active == []
    assert decs[0].action == "lower"
    assert decs[0].new_bid == 1.0
    assert decs[0].old_bid == 18

def test_split_out_of_window_already_floor_is_hold():
    recs = [FakeRec("S1", "M1", 1)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs, _park = split_by_control(recs, ctrl, _dt(h=3), _min_bid_for)
    assert decs[0].action == "hold"

def test_split_active_and_missing_go_to_rules():
    recs = [FakeRec("S1", "M1", 18), FakeRec("S2", "M2", 20)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}  # S2 — без записи
    active, decs, _park = split_by_control(recs, ctrl, _dt(h=12), _min_bid_for)
    assert {r.sku for r in active} == {"S1", "S2"}
    assert decs == []


# ---- парковка ставки на ночь и восстановление утром ------------------------

def test_out_of_window_drop_parks_prev_bid():
    """Первый выход из окна: роняем в пол И запоминаем прежнюю ставку."""
    recs = [FakeRec("S1", "M1", 180)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs, park = split_by_control(recs, ctrl, _dt(h=3), _min_bid_for)
    assert decs[0].action == "lower" and decs[0].new_bid == 1.0
    assert park["park"] == {"S1": 180}   # запомнили рабочий уровень

def test_out_of_window_already_floor_does_not_park():
    """Уже в полу (второй тик ночью) — не паркуем пол поверх запомненного."""
    recs = [FakeRec("S1", "M1", 1)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs, park = split_by_control(recs, ctrl, _dt(h=3), _min_bid_for)
    assert decs[0].action == "hold"
    assert park["park"] == {}

def test_in_window_restores_parked_bid_and_unparks():
    """Утром в окне: запаркованную ставку возвращаем одним движением, паркинг чистим."""
    recs = [FakeRec("S1", "M1", 1)]          # сейчас в полу
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs, park = split_by_control(
        recs, ctrl, _dt(h=8), _min_bid_for, parked_bids={"S1": 180})
    assert active == []                       # на этом тике только восстановление
    assert decs[0].action == "raise"
    assert decs[0].old_bid == 1 and decs[0].new_bid == 180
    assert park["unpark"] == ["S1"]

def test_restore_clamped_to_ceiling():
    """Если потолок понизили ниже запомненного — не возвращаем выше потолка."""
    recs = [FakeRec("S1", "M1", 1)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs, park = split_by_control(
        recs, ctrl, _dt(h=8), _min_bid_for, parked_bids={"S1": 300},
        ceiling_for=lambda _s: 250.0)
    assert decs[0].action == "raise" and decs[0].new_bid == 250.0

def test_restore_skipped_when_current_already_at_or_above_parked():
    """Ставка уже не ниже запомненной (напр. правил руками) — не трогаем, чистим паркинг, в движок."""
    recs = [FakeRec("S1", "M1", 200)]
    ctrl = {"S1": ProductControl(window_start=8, window_end=23)}
    active, decs, park = split_by_control(
        recs, ctrl, _dt(h=8), _min_bid_for, parked_bids={"S1": 180})
    assert {r.sku for r in active} == {"S1"}   # в движок как обычно
    assert decs == []
    assert park["unpark"] == ["S1"]

def test_disabled_keeps_parking_no_restore():
    """Выключенный товар не восстанавливаем и паркинг не стираем (вернёт при включении)."""
    recs = [FakeRec("S1", "M1", 1)]
    ctrl = {"S1": ProductControl(enabled=False)}
    active, decs, park = split_by_control(
        recs, ctrl, _dt(h=8), _min_bid_for, parked_bids={"S1": 180})
    assert decs[0].action == "hold" and "выключен" in decs[0].reason
    assert park["park"] == {} and park["unpark"] == []

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_daypart")
