# Kaspi Position Tracker — Part A (organic) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track the non-personalized organic search rank of our product on Kaspi
by keyword × city every 15 minutes, store snapshots, and show them in the dashboard.

**Architecture:** New HTTP collector (`connectors/search_client.py`) hits the public
Kaspi listing endpoint with a browser UA, parses ordered cards, finds our card by
Kaspi `product_id`. A new `position_snapshots` SQLite table stores each snapshot.
A clean `run_position_tick(ctx)` in `worker.py` orchestrates it on a 15-min APScheduler
job. A new webui section renders rank history + the top-N listing.

**Tech Stack:** Python 3, httpx, stdlib sqlite3, FastAPI + Jinja templates, APScheduler,
PyYAML. Tests are plain scripts run with `.venv/bin/python test_<name>.py` (NO pytest),
using `assert` and `print("OK ...")`, following existing `test_*.py` files.

## Global Constraints

- Tests are flat scripts, run as `.venv/bin/python test_<name>.py`; no pytest, no
  test framework. End with a printed success line. (See existing `test_store.py`.)
- HTTP to Kaspi MUST use the browser User-Agent. App/mobile UA returns 403 (verified).
  Reuse `BROWSER_UA` string kept in sync with `connectors/merchant_client.py`.
- No secrets/tokens in code or logs (project rule).
- Times are handled in `Asia/Almaty` (`ZoneInfo("Asia/Almaty")`), like `worker.py`.
- Collector never persists directly; it returns data. Only `Store` touches SQLite.
- Pure functions stay planner-testable with injected fakes (no network in unit tests).

---

### Task 1: Listing parser + collector (`search_client.py`)

**Files:**
- Create: `connectors/search_client.py`
- Test: `test_search_client.py`

**Interfaces:**
- Produces:
  - `@dataclass Card{ rank:int, product_id:str, title:str, price:float|None, brand:str|None, is_ad:bool=False }`
  - `@dataclass Listing{ keyword:str, city_id:str, our_product_id:str, our_rank:int|None, total:int, cards:list[Card] }`
  - `parse_filters_page(data:dict, start_rank:int) -> tuple[list[Card], int]`
    returns `(cards, total)`; `start_rank` is the 1-based rank of the first card on this page.
  - `fetch_listing(keyword:str, city_id:str, zone:str, our_product_id:str, max_depth:int=100, http_get:Callable[[str],dict]|None=None) -> Listing`
    `http_get(url)->dict` is injectable for tests; default does the real httpx GET.
  - `BROWSER_UA: str`

- [ ] **Step 1: Write the failing test**

Create `test_search_client.py`:
```python
"""
test_search_client.py — тест парсера и сборщика выдачи Kaspi (без сети).

Запуск: .venv/bin/python test_search_client.py
"""
from connectors.search_client import parse_filters_page, fetch_listing, Card


def _page(ids_titles):
    return {"data": {
        "total": 2697,
        "cards": [
            {"id": pid, "configSku": pid, "title": t, "brand": "Без бренда",
             "unitPrice": 59900, "unitSalePrice": 47900}
            for pid, t in ids_titles
        ],
    }}


def test_parse_assigns_sequential_ranks_and_reads_total():
    data = _page([("111", "A"), ("222", "B")])["data"]
    cards, total = parse_filters_page(data, start_rank=1)
    assert total == 2697
    assert [c.rank for c in cards] == [1, 2]
    assert cards[0].product_id == "111" and cards[0].title == "A"
    assert cards[0].price == 47900          # prefers unitSalePrice
    assert cards[0].is_ad is False


def test_parse_continues_rank_across_pages():
    data = _page([("333", "C"), ("444", "D")])["data"]
    cards, _ = parse_filters_page(data, start_rank=13)
    assert [c.rank for c in cards] == [13, 14]


def test_parse_handles_missing_cards():
    cards, total = parse_filters_page({"total": 0}, start_rank=1)
    assert cards == [] and total == 0


def test_fetch_listing_finds_our_rank_across_pages():
    pages = {
        0: _page([(str(i), f"t{i}") for i in range(100, 112)]),   # ranks 1..12
        1: _page([("999", "OURS")] + [(str(i), f"t{i}") for i in range(200, 211)]),  # rank 13 = ours
    }
    calls = []

    def fake_get(url):
        calls.append(url)
        page = 1 if "page=1" in url else 0
        return pages[page]

    lst = fetch_listing("аэрогриль", "750000000", "Magnum_ZONE1",
                        our_product_id="999", max_depth=100, http_get=fake_get)
    assert lst.our_rank == 13
    assert lst.total == 2697
    assert lst.cards[12].product_id == "999"
    assert any("c=750000000" in u for u in calls)
    assert any("availableInZones:Magnum_ZONE1" in u for u in calls)


def test_fetch_listing_our_rank_none_when_beyond_depth():
    empty = {"data": {"total": 5, "cards": []}}
    lst = fetch_listing("x", "750000000", "Z", our_product_id="absent",
                        max_depth=24, http_get=lambda url: empty)
    assert lst.our_rank is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_search_client")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_search_client.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'connectors.search_client'`.

- [ ] **Step 3: Write minimal implementation**

Create `connectors/search_client.py`:
```python
"""
search_client.py — сбор органической выдачи Kaspi по ключевому слову.

Тянет GET kaspi.kz/yml/product-view/pl/filters (браузерный UA обязателен: app-UA
даёт 403), листает страницы по 12, ищет позицию НАШЕЙ карточки по product_id.
Никакой авторизации/cookies — это и даёт неперсонализированные («абсолютные») позиции.
Клиент НЕ пишет в БД: возвращает Listing, персистит его воркер.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote

import httpx

log = logging.getLogger("search")

BASE_URL = "https://kaspi.kz/yml/product-view/pl/filters"
PAGE_SIZE = 12

# Держать синхронно с merchant_client.BROWSER_UA — WAF режет не-браузерный UA.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class Card:
    rank: int
    product_id: str
    title: str
    price: float | None = None
    brand: str | None = None
    is_ad: bool = False


@dataclass
class Listing:
    keyword: str
    city_id: str
    our_product_id: str
    our_rank: int | None
    total: int
    cards: list[Card] = field(default_factory=list)


def parse_filters_page(data: dict, start_rank: int) -> tuple[list[Card], int]:
    total = int(data.get("total") or 0)
    raw = data.get("cards") or []
    cards: list[Card] = []
    for i, c in enumerate(raw):
        pid = str(c.get("configSku") or c.get("id") or "")
        price = c.get("unitSalePrice")
        if price is None:
            price = c.get("unitPrice")
        cards.append(Card(
            rank=start_rank + i,
            product_id=pid,
            title=c.get("title") or "",
            price=float(price) if price is not None else None,
            brand=c.get("brand"),
        ))
    return cards, total


def _build_url(keyword: str, city_id: str, zone: str, page: int) -> str:
    q = quote(keyword)
    return (f"{BASE_URL}?text={q}&page={page}&all=false&fl=true&ui=d"
            f"&q=%3AavailableInZones%3A{zone}&i=-1&c={city_id}")


def _default_get(url: str) -> dict:
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
    r = httpx.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    return r.json()


def fetch_listing(keyword: str, city_id: str, zone: str, our_product_id: str,
                  max_depth: int = 100,
                  http_get: Callable[[str], dict] | None = None) -> Listing:
    get = http_get or _default_get
    all_cards: list[Card] = []
    total = 0
    our_rank: int | None = None
    page = 0
    while len(all_cards) < max_depth:
        url = _build_url(keyword, city_id, zone, page)
        payload = get(url)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        cards, total = parse_filters_page(data, start_rank=len(all_cards) + 1)
        if not cards:
            break
        all_cards.extend(cards)
        for c in cards:
            if c.product_id == our_product_id:
                our_rank = c.rank
                break
        if our_rank is not None:
            break
        page += 1
    return Listing(keyword=keyword, city_id=city_id, our_product_id=our_product_id,
                   our_rank=our_rank, total=total, cards=all_cards[:max_depth])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_search_client.py`
Expected: `OK test_search_client`.

- [ ] **Step 5: Commit**

```bash
git add connectors/search_client.py test_search_client.py
git commit -m "feat(search): HTTP-сборщик органической выдачи Kaspi + парсер позиций"
```

---

### Task 2: `position_snapshots` table + Store methods

**Files:**
- Modify: `core/store.py` (add table to `_init_schema`; add 3 methods)
- Test: `test_store_positions.py`

**Interfaces:**
- Consumes: `Card`, `Listing` from Task 1 (for the caller; Store takes primitives + JSON string).
- Produces (methods on `Store`):
  - `put_position_snapshot(ts:int, keyword:str, city:str, product_id:str, our_rank:int|None, total:int, listing_json:str) -> None`
  - `get_position_series(keyword:str, city:str, limit:int=200) -> list[sqlite3.Row]`
    ordered by `ts ASC`; rows have `ts, our_rank, total`.
  - `get_latest_position(keyword:str, city:str) -> sqlite3.Row|None`
    latest row incl. `listing_json`.
  - `list_tracked_pairs() -> list[sqlite3.Row]` distinct `(keyword, city)` seen.

- [ ] **Step 1: Write the failing test**

Create `test_store_positions.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_store_positions.py`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'put_position_snapshot'`.

- [ ] **Step 3: Write minimal implementation**

In `core/store.py`, inside the `_init_schema` `executescript("""...""")` string, add:
```sql
CREATE TABLE IF NOT EXISTS position_snapshots (
    ts INTEGER, keyword TEXT, city TEXT, product_id TEXT,
    our_rank INTEGER, total INTEGER, listing_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_pos_kw_city_ts
    ON position_snapshots(keyword, city, ts);
```
Add these methods to the `Store` class (mirror the existing method style):
```python
    def put_position_snapshot(self, ts, keyword, city, product_id,
                              our_rank, total, listing_json):
        self._conn.execute(
            "INSERT INTO position_snapshots "
            "(ts, keyword, city, product_id, our_rank, total, listing_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, keyword, city, product_id, our_rank, total, listing_json),
        )
        self._conn.commit()

    def get_position_series(self, keyword, city, limit=200):
        cur = self._conn.execute(
            "SELECT ts, our_rank, total FROM position_snapshots "
            "WHERE keyword=? AND city=? ORDER BY ts ASC LIMIT ?",
            (keyword, city, limit),
        )
        return cur.fetchall()

    def get_latest_position(self, keyword, city):
        cur = self._conn.execute(
            "SELECT ts, our_rank, total, listing_json FROM position_snapshots "
            "WHERE keyword=? AND city=? ORDER BY ts DESC LIMIT 1",
            (keyword, city),
        )
        return cur.fetchone()

    def list_tracked_pairs(self):
        cur = self._conn.execute(
            "SELECT DISTINCT keyword, city FROM position_snapshots "
            "ORDER BY keyword, city"
        )
        return cur.fetchall()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_store_positions.py`
Expected: `OK test_store_positions`.
Also run `.venv/bin/python test_store.py` — Expected: still passes (no regression).

- [ ] **Step 5: Commit**

```bash
git add core/store.py test_store_positions.py
git commit -m "feat(store): таблица position_snapshots + методы чтения/записи"
```

---

### Task 3: Positions config loader (`config/positions.yaml` + parser)

**Files:**
- Create: `config/positions.yaml`
- Create: `core/positions_config.py`
- Test: `test_positions_config.py`

**Interfaces:**
- Produces:
  - `@dataclass City{ name:str, city_id:str, zone:str }`
  - `@dataclass TrackItem{ keyword:str, product_id:str, label:str }`
  - `@dataclass PositionsConfig{ cities:list[City], track:list[TrackItem], max_depth:int }`
  - `load_positions_config(path:str) -> PositionsConfig`
  - `resolve_product_id_from_url(url:str) -> str` — extracts the numeric Kaspi
    product id from a card URL like
    `https://kaspi.kz/shop/p/...-134653775/?c=750000000` → `"134653775"`.

- [ ] **Step 1: Write the failing test**

Create `test_positions_config.py`:
```python
"""
test_positions_config.py — загрузка конфига позиций и разбор ссылки на карточку.

Запуск: .venv/bin/python test_positions_config.py
"""
import os
import tempfile
from core.positions_config import load_positions_config, resolve_product_id_from_url


YAML = """
max_depth: 80
cities:
  - {name: "Алматы", city_id: "750000000", zone: "Magnum_ZONE1"}
  - {name: "Астана", city_id: "710000000", zone: "Magnum_ZONE5"}
track:
  - {keyword: "аэрогриль", product_id: "134653775", label: "Наш аэрогриль"}
"""


def test_load_config():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "positions.yaml")
    with open(p, "w") as f:
        f.write(YAML)
    cfg = load_positions_config(p)
    assert cfg.max_depth == 80
    assert cfg.cities[0].name == "Алматы"
    assert cfg.cities[1].city_id == "710000000"
    assert cfg.track[0].keyword == "аэрогриль"
    assert cfg.track[0].product_id == "134653775"


def test_resolve_product_id_from_url():
    url = "https://kaspi.kz/shop/p/aerogril-akane-a-5388-134653775/?c=750000000"
    assert resolve_product_id_from_url(url) == "134653775"


def test_resolve_product_id_prefers_last_numeric_group():
    url = "https://kaspi.kz/shop/p/model-8-l-99887766/"
    assert resolve_product_id_from_url(url) == "99887766"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_positions_config")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_positions_config.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.positions_config'`.

- [ ] **Step 3: Write minimal implementation**

Create `core/positions_config.py`:
```python
"""
positions_config.py — конфиг трекера позиций: города (city_id+zone), какие
ключевые слова и наши product_id трекать, глубина обхода.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import yaml


@dataclass
class City:
    name: str
    city_id: str
    zone: str


@dataclass
class TrackItem:
    keyword: str
    product_id: str
    label: str


@dataclass
class PositionsConfig:
    cities: list[City]
    track: list[TrackItem]
    max_depth: int


def load_positions_config(path: str) -> PositionsConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cities = [City(**c) for c in (raw.get("cities") or [])]
    track = [TrackItem(**t) for t in (raw.get("track") or [])]
    return PositionsConfig(cities=cities, track=track,
                           max_depth=int(raw.get("max_depth", 100)))


def resolve_product_id_from_url(url: str) -> str:
    """Из ссылки на карточку берём последнюю числовую группу (id мастер-продукта)."""
    path = url.split("?", 1)[0]
    nums = re.findall(r"(\d{5,})", path)
    if not nums:
        raise ValueError(f"product_id не найден в URL: {url}")
    return nums[-1]
```

Create `config/positions.yaml` (Astana zone token is filled in Task 3 Step 6):
```yaml
# positions.yaml — что трекаем в поиске Kaspi.
# product_id — id карточки Kaspi (последняя числовая группа в URL карточки).
max_depth: 100
cities:
  - {name: "Алматы", city_id: "750000000", zone: "Magnum_ZONE1"}
  - {name: "Астана", city_id: "710000000", zone: "ZONE_TODO"}
track:
  - {keyword: "аэрогриль", product_id: "REPLACE_ME", label: "Наш товар"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_positions_config.py`
Expected: `OK test_positions_config`.

- [ ] **Step 5: Commit**

```bash
git add core/positions_config.py config/positions.yaml test_positions_config.py
git commit -m "feat(config): конфиг трекера позиций + разбор product_id из URL"
```

- [ ] **Step 6: Fill real data (Astana zone + our product_id)**

Discover Astana's `availableInZones` token: open
`https://kaspi.kz/shop/search/?text=аэрогриль` in a browser with city set to Астана,
read the resulting `q=:availableInZones:<zone>` from the network request URL (as was
done for Almaty → `Magnum_ZONE1`). Put the token into `config/positions.yaml`.
Replace `product_id: "REPLACE_ME"` with the id from the card URL the owner sends
(use `resolve_product_id_from_url`). Commit:
```bash
git add config/positions.yaml
git commit -m "chore(config): реальные зона Астаны и product_id нашего товара"
```

---

### Task 4: Orchestration — `run_position_tick` + scheduler

**Files:**
- Modify: `worker.py` (add `run_position_tick`; register 15-min job in `main()`)
- Test: `test_worker_positions.py`

**Interfaces:**
- Consumes: `fetch_listing`/`Listing`/`Card` (Task 1), `Store.put_position_snapshot`
  (Task 2), `PositionsConfig`/`City`/`TrackItem` (Task 3).
- Produces:
  - `run_position_tick(store, positions_cfg, now_fn, search_fetch=fetch_listing) -> int`
    returns number of snapshots written. Iterates `track × cities`, calls
    `search_fetch`, serializes top-N cards to JSON, writes a snapshot per pair.
    A failure on one pair is logged and skipped (does not abort the tick).

- [ ] **Step 1: Write the failing test**

Create `test_worker_positions.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_worker_positions.py`
Expected: FAIL — `ImportError: cannot import name 'run_position_tick' from 'worker'`.

- [ ] **Step 3: Write minimal implementation**

In `worker.py`, add imports near the top:
```python
import json
from connectors.search_client import fetch_listing
```
Add the function (place it after `run_revenue_cycle`, before the bid tick):
```python
# ---- тик трекера позиций (органика, HTTP) ----------------------------------

def run_position_tick(store, positions_cfg, now_fn=lambda: datetime.now(ALMATY),
                      search_fetch=fetch_listing) -> int:
    """Снимает позицию нашего товара по каждому (keyword × city) и пишет снапшот.
    Падение по одной паре логируется и пропускается — тик не роняем."""
    ts = int(now_fn().timestamp())
    written = 0
    for item in positions_cfg.track:
        for city in positions_cfg.cities:
            try:
                lst = search_fetch(item.keyword, city.city_id, city.zone,
                                   item.product_id, positions_cfg.max_depth)
                listing_json = json.dumps(
                    [{"rank": c.rank, "product_id": c.product_id, "title": c.title,
                      "price": c.price, "brand": c.brand, "is_ad": c.is_ad}
                     for c in lst.cards], ensure_ascii=False)
                store.put_position_snapshot(
                    ts, item.keyword, city.name, item.product_id,
                    lst.our_rank, lst.total, listing_json)
                written += 1
            except Exception as e:  # noqa: BLE001 — одна пара не должна ронять тик
                log.warning("Позиции: пара %s/%s упала: %s",
                            item.keyword, city.name, e)
    log.info("Позиции-тик: записано снапшотов = %s", written)
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_worker_positions.py`
Expected: `OK test_worker_positions`.
Also run `.venv/bin/python test_worker.py` — Expected: still passes.

- [ ] **Step 5: Register the 15-min job in `main()`**

Find the APScheduler setup in `worker.py` `main()` (where the fast/slow/revenue
jobs are added). Load the positions config and add a job. Add near the other
`load_*`/config loads:
```python
    from core.positions_config import load_positions_config
    pos_cfg = load_positions_config(
        os.environ.get("POSITIONS_CONFIG", "config/positions.yaml"))
```
(ensure `import os` exists at top of `worker.py`; add if missing) and next to the
other `scheduler.add_job(...)` calls:
```python
    scheduler.add_job(
        lambda: run_position_tick(store, pos_cfg),
        "interval", minutes=15, id="positions", max_instances=1,
        coalesce=True, next_run_time=datetime.now(ALMATY))
```
Manually sanity-check import wiring without starting the scheduler:
```bash
.venv/bin/python -c "import worker; print('import ok')"
```
Expected: `import ok`.

- [ ] **Step 6: Commit**

```bash
git add worker.py test_worker_positions.py
git commit -m "feat(worker): 15-мин тик трекера позиций + регистрация job"
```

---

### Task 5: Dashboard section (webui)

**Files:**
- Modify: `webui/app.py` (new route `/positions`)
- Create: `webui/templates/positions.html`
- Modify: `webui/templates/base.html` (nav link to Positions)
- Test: `test_webui_positions.py`

**Interfaces:**
- Consumes: `Store.list_tracked_pairs`, `Store.get_latest_position`,
  `Store.get_position_series` (Task 2); `fmt_ts_almaty` (existing in `app.py`).
- Produces: GET `/positions` returns HTML listing, per tracked `(keyword, city)`:
  current `our_rank`, a small rank sparkline (inline SVG, no external libs), and
  the top-N table from `listing_json` with our card highlighted and an
  ad/organic column.

- [ ] **Step 1: Write the failing test**

Create `test_webui_positions.py`:
```python
"""
test_webui_positions.py — рендер страницы позиций.

Запуск: .venv/bin/python test_webui_positions.py
"""
import json
import os
import tempfile
from fastapi.testclient import TestClient

os.environ.setdefault("WEBUI_USER", "u")
os.environ.setdefault("WEBUI_PASS", "p")

from core.store import Store
from webui.app import create_app


def seed(db_path):
    s = Store(db_path)
    listing = json.dumps([
        {"rank": 1, "product_id": "111", "title": "Конкурент A", "price": 59900,
         "brand": "X", "is_ad": True},
        {"rank": 2, "product_id": "999", "title": "Наш товар", "price": 47900,
         "brand": "Y", "is_ad": False},
    ], ensure_ascii=False)
    s.put_position_snapshot(1000, "аэрогриль", "Алматы", "999", 2, 2697, listing)
    s.put_position_snapshot(2000, "аэрогриль", "Алматы", "999", 2, 2697, listing)
    s.close()


def make_client():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "t.db")
    seed(db)
    os.environ["DB_PATH"] = db
    app = create_app()
    return TestClient(app)


def test_positions_page_renders_our_rank_and_listing():
    c = make_client()
    c.post("/login", data={"username": "u", "password": "p"}, follow_redirects=False)
    r = c.get("/positions")
    assert r.status_code == 200
    body = r.text
    assert "аэрогриль" in body
    assert "Алматы" in body
    assert "Наш товар" in body        # our highlighted card
    assert "Конкурент A" in body      # competitor above


if __name__ == "__main__":
    test_positions_page_renders_our_rank_and_listing()
    print("OK test_webui_positions")
```
Note: confirm the login field names and `DB_PATH` env var against `webui/app.py`
before running — match whatever `create_app()` / the login route actually use
(the existing `test_webui.py` shows the correct pattern; copy it).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_webui_positions.py`
Expected: FAIL — `/positions` returns 404 (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `webui/app.py`, add a route mirroring the existing `dashboard` route's
store-open/close pattern:
```python
    @app.get("/positions", response_class=HTMLResponse)
    def positions(request: Request):
        if not _authed(request):                      # match existing auth guard
            return RedirectResponse("/login", status_code=302)
        store = Store(db_path)
        try:
            pairs = store.list_tracked_pairs()
            blocks = []
            for p in pairs:
                latest = store.get_latest_position(p["keyword"], p["city"])
                series = store.get_position_series(p["keyword"], p["city"])
                listing = json.loads(latest["listing_json"]) if latest else []
                blocks.append({
                    "keyword": p["keyword"], "city": p["city"],
                    "our_rank": latest["our_rank"] if latest else None,
                    "total": latest["total"] if latest else 0,
                    "ts": fmt_ts_almaty(latest["ts"]) if latest else "—",
                    "ranks": [r["our_rank"] for r in series],
                    "listing": listing,
                    "our_product_id": latest["product_id"] if latest else "",
                })
        finally:
            store.close()
        return templates.TemplateResponse(
            "positions.html", {"request": request, "blocks": blocks})
```
Ensure `import json` is present in `app.py` (add if missing). Use the same auth
guard, `templates`, and `db_path` variables the existing routes use (copy their
exact names — e.g. the dashboard route in `app.py:187`).

Create `webui/templates/positions.html` (extends the existing base like other pages):
```html
{% extends "base.html" %}
{% block content %}
<h1>Позиции в поиске</h1>
{% for b in blocks %}
  <section class="pos-block">
    <h2>{{ b.keyword }} — {{ b.city }}</h2>
    <p>
      Наша позиция:
      <strong>{{ b.our_rank if b.our_rank is not none else "вне топа" }}</strong>
      из {{ b.total }} · обновлено {{ b.ts }}
    </p>
    {% if b.ranks|length > 1 %}
      <svg width="240" height="40" viewBox="0 0 240 40" class="spark">
        {% set mx = b.ranks|reject("none")|list %}
        {% set top = (mx|max) if mx else 1 %}
        <polyline fill="none" stroke="currentColor" stroke-width="2"
          points="{% for r in b.ranks %}{{ loop.index0 * (240 / (b.ranks|length)) }},{{ (r / top * 38) if r else 40 }} {% endfor %}"/>
      </svg>
    {% endif %}
    <table class="pos-table">
      <thead><tr><th>#</th><th>Товар</th><th>Цена</th><th>Тип</th></tr></thead>
      <tbody>
      {% for c in b.listing %}
        <tr class="{{ 'ours' if c.product_id == b.our_product_id else '' }}">
          <td>{{ c.rank }}</td>
          <td>{{ c.title }}</td>
          <td>{{ c.price or "—" }}</td>
          <td>{{ "Реклама" if c.is_ad else "Органика" }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </section>
{% endfor %}
{% endblock %}
```
In `webui/templates/base.html`, add a nav link next to the existing dashboard
link (match the existing nav markup):
```html
<a href="/positions">Позиции</a>
```
Optionally add to `webui/static/app.css`:
```css
.pos-table .ours { background: #fff6d6; font-weight: 600; }
.spark { color: #2563eb; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_webui_positions.py`
Expected: `OK test_webui_positions`.
Also run `.venv/bin/python test_webui.py` — Expected: still passes.

- [ ] **Step 5: Commit**

```bash
git add webui/app.py webui/templates/positions.html webui/templates/base.html webui/static/app.css test_webui_positions.py
git commit -m "feat(webui): страница позиций — ранг, спарклайн, список с подсветкой нашего товара"
```

---

### Task 6: End-to-end smoke against live Kaspi (manual, no new test file)

**Files:** none (manual verification)

- [ ] **Step 1: Real fetch for Almaty**

Run:
```bash
.venv/bin/python -c "
from connectors.search_client import fetch_listing
l = fetch_listing('аэрогриль','750000000','Magnum_ZONE1', our_product_id='134653775', max_depth=48)
print('total', l.total, 'our_rank', l.our_rank, 'cards', len(l.cards))
print([c.title[:25] for c in l.cards[:6]])
"
```
Expected: non-zero `total`, `cards` ~ up to 48, printed titles look like real
aэрогриль listings. (`our_rank` may be None if that product_id isn't in top 48 —
that's valid.)

- [ ] **Step 2: One real tick end-to-end**

With `config/positions.yaml` filled (Task 3 Step 6), run:
```bash
.venv/bin/python -c "
from core.store import Store
from core.positions_config import load_positions_config
from worker import run_position_tick
s = Store('db/positions_smoke.db')
n = run_position_tick(s, load_positions_config('config/positions.yaml'))
print('snapshots', n)
print('pairs', [(r['keyword'], r['city']) for r in s.list_tracked_pairs()])
s.close()
"
```
Expected: `snapshots` equals `len(track) * len(cities)` (minus any network
failures), pairs list shows Алматы and Астана.

- [ ] **Step 3: Confirm no regressions across the suite**

Run each test file (per `README.md`):
```bash
for t in search_client store store_positions positions_config worker worker_positions webui webui_positions; do
  .venv/bin/python test_$t.py || echo "FAIL $t"
done
```
Expected: every file prints its `OK ...` line, no `FAIL`.

- [ ] **Step 4: Commit any config/doc touch-ups**

```bash
git add -A && git commit -m "chore: смоук-проверка трекера позиций на живом Kaspi" || echo "nothing to commit"
```
