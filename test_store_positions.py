"""
test_store_positions.py — round-trip снапшотов позиций.

Запуск: .venv/bin/python test_store_positions.py
"""
import json
import os
import tempfile
from core.store import Store


def new_store():
    d = tempfile.mkdtemp()
    return Store(os.path.join(d, "t.db"))


def test_put_and_series_ordered_by_ts():
    s = new_store()
    s.put_position_snapshot(100, "аэрогриль", "Алматы", "999", 7, 2697, "[]")
    s.put_position_snapshot(200, "аэрогриль", "Алматы", "999", 5, 2700, "[]")
    rows = s.get_position_series("аэрогриль", "Алматы")
    assert [r["ts"] for r in rows] == [100, 200]
    assert [r["our_rank"] for r in rows] == [7, 5]
    s.close()


def test_latest_returns_listing_json_and_none_rank():
    s = new_store()
    listing = json.dumps([{"rank": 1, "product_id": "111", "title": "A"}])
    s.put_position_snapshot(100, "kw", "Астана", "999", None, 0, listing)
    row = s.get_latest_position("kw", "Астана")
    assert row["our_rank"] is None
    assert json.loads(row["listing_json"])[0]["product_id"] == "111"
    assert s.get_latest_position("kw", "Алматы") is None
    s.close()


def test_series_returns_newest_window_in_ascending_order():
    s = new_store()
    for t in range(1, 11):          # ts = 1..10
        s.put_position_snapshot(t, "kw", "Алматы", "9", t, 100, "[]")
    rows = s.get_position_series("kw", "Алматы", limit=3)
    assert [r["ts"] for r in rows] == [8, 9, 10]        # newest 3, ascending
    s.close()


def test_list_tracked_pairs_distinct():
    s = new_store()
    s.put_position_snapshot(1, "kw", "Алматы", "9", 1, 1, "[]")
    s.put_position_snapshot(2, "kw", "Алматы", "9", 1, 1, "[]")
    s.put_position_snapshot(3, "kw", "Астана", "9", 1, 1, "[]")
    pairs = {(r["keyword"], r["city"]) for r in s.list_tracked_pairs()}
    assert pairs == {("kw", "Алматы"), ("kw", "Астана")}
    s.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_store_positions")
