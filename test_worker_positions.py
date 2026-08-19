"""
test_worker_positions.py — тик трекера позиций с фейками (без сети/APScheduler).

Запуск: .venv/bin/python test_worker_positions.py
"""
import json
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from core.store import Store
from core.positions_config import PositionsConfig, City, TrackItem
from connectors.search_client import Listing, Card
from worker import run_position_tick

ALMATY = ZoneInfo("Asia/Almaty")
NOW = lambda: datetime(2026, 8, 19, 12, 0, tzinfo=ALMATY)


def new_store():
    d = tempfile.mkdtemp()
    return Store(os.path.join(d, "t.db"))


def cfg():
    return PositionsConfig(
        cities=[City("Алматы", "750000000", "Magnum_ZONE1"),
                City("Астана", "710000000", "Magnum_ZONE5")],
        track=[TrackItem("аэрогриль", "999", "Наш")],
        max_depth=50,
    )


def fake_fetch(keyword, city_id, zone, our_product_id, max_depth, http_get=None):
    rank = 7 if city_id == "750000000" else None
    cards = [Card(rank=1, product_id="111", title="A", price=100.0, brand="X")]
    return Listing(keyword, city_id, our_product_id, rank, 2697, cards)


def test_tick_writes_one_snapshot_per_pair():
    s = new_store()
    n = run_position_tick(s, cfg(), now_fn=NOW, search_fetch=fake_fetch)
    assert n == 2
    alm = s.get_latest_position("аэрогриль", "Алматы")
    ast = s.get_latest_position("аэрогриль", "Астана")
    assert alm["our_rank"] == 7
    assert ast["our_rank"] is None
    assert json.loads(alm["listing_json"])[0]["product_id"] == "111"
    s.close()


def test_tick_skips_failing_pair():
    s = new_store()

    def flaky(keyword, city_id, zone, our_product_id, max_depth, http_get=None):
        if city_id == "710000000":
            raise RuntimeError("network boom")
        return fake_fetch(keyword, city_id, zone, our_product_id, max_depth)

    n = run_position_tick(s, cfg(), now_fn=NOW, search_fetch=flaky)
    assert n == 1                                   # Астана упала — пропущена
    assert s.get_latest_position("аэрогриль", "Алматы") is not None
    assert s.get_latest_position("аэрогриль", "Астана") is None
    s.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_worker_positions")
