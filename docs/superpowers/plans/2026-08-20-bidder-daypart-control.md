# Контроль биддера по товару (дейпартинг + вкл/выкл) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать per-product контроль биддером — рабочее окно (часы+дни, вне окна ставка в min_bid) и вкл/выкл товара — с редактированием из веб-панели.

**Architecture:** Чистый контрольный слой (`core/daypart.py`) вычисляет решения дейпартинга/вкл-выкл ДО правил; `worker.run_tick` вызывает его перед `evaluate_fast/slow` и склеивает решения в общий аудит. Настройки хранятся в новой таблице `product_control`, редактируются на существующем экране товара `/settings/sku/..`. `core/rules.py` не меняется (остаётся без времени/БД).

**Tech Stack:** Python 3 stdlib (sqlite3), FastAPI + Jinja2 (веб-панель), plain-script тесты (`.venv/bin/python test_*.py`, без pytest).

## Global Constraints

- Тесты — plain-script, без pytest. Файл заканчивается идиомой:
  `if __name__ == "__main__":` → `for name, fn in list(globals().items()): if name.startswith("test_"): fn()` → `print("OK <файл>")`. Запуск: `.venv/bin/python test_<x>.py`.
- Время везде — таймзона Алматы (`ZoneInfo("Asia/Almaty")`), не локальная зона сервера.
- `core/rules.py` НЕ трогаем — движок правил остаётся чистым (без времени/БД).
- Дни недели: биты 0..6 = Пн..Вс, совпадает с `datetime.weekday()`. Маска `127` = все дни.
- Окно — полуинтервал `[window_start, window_end)` по часу; `window_end=24` = круглосуточно; `window_start >= window_end` невалидно (валидируется в UI).
- Дефолт «нет записи в product_control» = enabled, окно 0..24, дни все (поведение как сейчас — биддер уже в бою на всех товарах).
- Комментарии и UI-строки — на русском, в стиле существующего кода.
- Коммиты частые, по одному на задачу; ветка `feat/bidder-daypart-control` (уже создана).

---

### Task 1: `ProductControl` + `active_at` (чистая модель расписания)

**Files:**
- Create: `core/daypart.py`
- Test: `test_daypart.py`

**Interfaces:**
- Consumes: `core.rules.Decision` (уже существует: `Decision(sku, merchant_sku, old_bid, new_bid, action, loop, reason)`).
- Produces: `ProductControl(enabled=True, window_start=0, window_end=24, days_mask=127)` с методом `active_at(dt: datetime) -> bool`; модульная константа `DEFAULT_CONTROL = ProductControl()`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_daypart.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.daypart'`.

- [ ] **Step 3: Write minimal implementation**

```python
# core/daypart.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_daypart.py`
Expected: PASS — печатает `OK test_daypart`.

- [ ] **Step 5: Commit**

```bash
git add core/daypart.py test_daypart.py
git commit -m "feat(daypart): ProductControl.active_at — окно часов+дни, вкл/выкл"
```

Не забудь идиому-раннер в конце `test_daypart.py`:

```python
if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_daypart")
```

---

### Task 2: `split_by_control` (разбиение товаров контрольным слоем)

**Files:**
- Modify: `core/daypart.py`
- Test: `test_daypart.py` (дополнить)

**Interfaces:**
- Consumes: `ProductControl.active_at` (Task 1); `core.rules.Decision`; вход `reconciled: list[SkuReconciled]` — у элемента есть поля `.sku`, `.merchant_sku`, `.bid` (см. `core/reconcile.py`).
- Produces: `split_by_control(reconciled, controls, now, min_bid_for) -> tuple[list, list[Decision]]`, где `controls: dict[str, ProductControl]`, `now: datetime`, `min_bid_for: Callable[[str], float]`. Возвращает `(active, control_decisions)`.

Правила разбиения:
- нет записи в `controls` → `DEFAULT_CONTROL` → товар активен;
- `enabled=False` → `Decision hold` reason «биддер выключен для товара»;
- включён, но `active_at(now)=False` (по часу/дню) → если `bid <= min_bid` то `hold` («вне окна, ставка уже в полу»), иначе `Decision lower` с `new_bid=min_bid` reason «вне рабочего окна → ставка в пол N»;
- включён и активен → в список `active`.

- [ ] **Step 1: Write the failing test**

```python
# добавить в test_daypart.py
from dataclasses import dataclass
from core.daypart import split_by_control

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_daypart.py`
Expected: FAIL — `ImportError: cannot import name 'split_by_control'`.

- [ ] **Step 3: Write minimal implementation**

```python
# добавить в core/daypart.py
from typing import Callable


def split_by_control(reconciled, controls: dict, now: datetime,
                     min_bid_for: "Callable[[str], float]"):
    """Делит товары ДО правил: активные → в fast/slow; вне окна → ставка в пол;
    выключенные → hold. Товар без записи в controls считается активным (дефолт).
    min_bid_for(sku) — эффективный min_bid (учитывает per-SKU оверрайд)."""
    active = []
    control_decisions: list[Decision] = []
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
                control_decisions.append(Decision(
                    s.sku, s.merchant_sku, s.bid, floor, "lower", "none",
                    f"вне рабочего окна → ставка в пол {floor:g}"))
        else:
            active.append(s)
    return active, control_decisions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_daypart.py`
Expected: PASS — `OK test_daypart`.

- [ ] **Step 5: Commit**

```bash
git add core/daypart.py test_daypart.py
git commit -m "feat(daypart): split_by_control — вне окна→пол, выключен→hold"
```

---

### Task 3: Таблица `product_control` + методы Store

**Files:**
- Modify: `core/store.py` (импорт `ProductControl`; `CREATE TABLE` в `_init_schema`; три метода)
- Test: `test_store.py` (дополнить)

**Interfaces:**
- Consumes: `core.daypart.ProductControl` (Task 1).
- Produces методы Store:
  - `get_product_control(campaign_id: str, sku: str) -> ProductControl` — запись или `ProductControl()` по умолчанию.
  - `set_product_control(campaign_id, sku, enabled, window_start, window_end, days_mask, user, ts) -> None` — upsert.
  - `list_product_control(campaign_id: str) -> dict[str, ProductControl]` — все товары кампании (для дейпартинга в тике).
  - `all_product_controls() -> list[tuple[str, str, ProductControl]]` — `(campaign_id, sku, ProductControl)` по всем (для индикатора на дашборде).

- [ ] **Step 1: Write the failing test**

```python
# добавить в test_store.py
from core.daypart import ProductControl

def test_product_control_default_when_absent():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "t.db"))
        c = s.get_product_control("C1", "SKU_X")
        assert c.enabled is True and c.window_start == 0 and c.window_end == 24
        assert c.days_mask == 127
        s.close()

def test_product_control_upsert_and_list():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "t.db"))
        s.set_product_control("C1", "S1", False, 8, 23, 31, "aidyn", 1000)
        got = s.get_product_control("C1", "S1")
        assert got.enabled is False and got.window_start == 8
        assert got.window_end == 23 and got.days_mask == 31
        # upsert перезаписывает
        s.set_product_control("C1", "S1", True, 9, 22, 127, "aidyn", 2000)
        assert s.get_product_control("C1", "S1").enabled is True
        # list по кампании
        s.set_product_control("C1", "S2", True, 0, 24, 127, "aidyn", 2000)
        m = s.list_product_control("C1")
        assert set(m.keys()) == {"S1", "S2"}
        assert isinstance(m["S1"], ProductControl)
        # all_product_controls
        allc = s.all_product_controls()
        assert ("C1", "S1") in {(cid, sku) for cid, sku, _ in allc}
        s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_store.py`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'get_product_control'`.

- [ ] **Step 3: Write minimal implementation**

В шапке `core/store.py` рядом с другими импортами:

```python
from core.daypart import ProductControl
```

В `_init_schema`, внутри `executescript("""...""")`, добавить таблицу (рядом с `config_overrides`):

```sql
            CREATE TABLE IF NOT EXISTS product_control (
                campaign_id  TEXT,
                sku          TEXT,
                enabled      INTEGER DEFAULT 1,
                window_start INTEGER DEFAULT 0,
                window_end   INTEGER DEFAULT 24,
                days_mask    INTEGER DEFAULT 127,
                user         TEXT,
                ts           INTEGER,
                PRIMARY KEY (campaign_id, sku)
            );
```

Методы (рядом с overrides-секцией):

```python
    # ---- контроль биддера по товару (дейпартинг/вкл-выкл) --------------------

    def get_product_control(self, campaign_id: str, sku: str) -> ProductControl:
        row = self._conn.execute(
            "SELECT enabled, window_start, window_end, days_mask "
            "FROM product_control WHERE campaign_id=? AND sku=?",
            (campaign_id, sku),
        ).fetchone()
        if row is None:
            return ProductControl()
        return ProductControl(bool(row["enabled"]), row["window_start"],
                              row["window_end"], row["days_mask"])

    def set_product_control(self, campaign_id: str, sku: str, enabled: bool,
                            window_start: int, window_end: int, days_mask: int,
                            user: str, ts: int) -> None:
        self._conn.execute(
            """INSERT INTO product_control
                 (campaign_id, sku, enabled, window_start, window_end, days_mask, user, ts)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id, sku) DO UPDATE SET
                 enabled=excluded.enabled, window_start=excluded.window_start,
                 window_end=excluded.window_end, days_mask=excluded.days_mask,
                 user=excluded.user, ts=excluded.ts""",
            (campaign_id, sku, int(enabled), window_start, window_end,
             days_mask, user, ts),
        )
        self._conn.commit()

    def list_product_control(self, campaign_id: str) -> dict[str, ProductControl]:
        rows = self._conn.execute(
            "SELECT sku, enabled, window_start, window_end, days_mask "
            "FROM product_control WHERE campaign_id=?",
            (campaign_id,),
        ).fetchall()
        return {r["sku"]: ProductControl(bool(r["enabled"]), r["window_start"],
                                         r["window_end"], r["days_mask"])
                for r in rows}

    def all_product_controls(self) -> list[tuple[str, str, ProductControl]]:
        rows = self._conn.execute(
            "SELECT campaign_id, sku, enabled, window_start, window_end, days_mask "
            "FROM product_control",
        ).fetchall()
        return [(r["campaign_id"], r["sku"],
                 ProductControl(bool(r["enabled"]), r["window_start"],
                                r["window_end"], r["days_mask"]))
                for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_store.py`
Expected: PASS — `OK test_store`.

- [ ] **Step 5: Commit**

```bash
git add core/store.py test_store.py
git commit -m "feat(store): таблица product_control + get/set/list/all"
```

---

### Task 4: Интеграция контрольного слоя в `worker.run_tick`

**Files:**
- Modify: `worker.py` (импорт `split_by_control`; вставка в `run_tick` перед `evaluate_*`)
- Test: `test_worker.py` (дополнить)

**Interfaces:**
- Consumes: `core.daypart.split_by_control` (Task 2); `Store.list_product_control` (Task 3); `resolve_config` (уже импортирован в worker).
- Produces: без новых публичных сигнатур — `run_tick` теперь применяет дейпартинг/вкл-выкл. Контрольные решения идут первыми в общий список и в тот же `_apply_and_log`.

- [ ] **Step 1: Write the failing test**

```python
# добавить в test_worker.py
from core.store import Store as _Store

def _ctx_with(products, dry_run=False, store=None, now=NOW):
    store = store or store_with_revenue({})
    mk = FakeMarketing(products, dry_run=dry_run)
    ctx = WorkerContext(marketing=mk, store=store, cfg=RulesConfig(min_bid=1),
                        now_fn=now)
    return ctx, mk, store

def test_run_tick_disabled_product_not_touched():
    # товар выключен в product_control → решение hold, PUT не шлётся даже в бою
    ctx, mk, store = _ctx_with([cp(sku="S1", bid=18)], dry_run=False)
    store.set_product_control("C1", "S1", False, 0, 24, 127, "t", 1)
    decisions = run_tick(ctx, "fast", "C1")
    d = [x for x in decisions if x.sku == "S1"][0]
    assert d.action == "hold" and "выключен" in d.reason
    assert mk.puts == []   # ничего не двинулось в кабинете

def test_run_tick_out_of_window_lowers_to_floor():
    # окно 8..23, сейчас 14:00 — В окне; проверим ВНЕ окна отдельным now=3:00
    night = lambda: datetime(2026, 8, 9, 3, 0, tzinfo=ALMATY)
    ctx, mk, store = _ctx_with([cp(sku="S1", bid=18)], dry_run=False, now=night)
    store.set_product_control("C1", "S1", True, 8, 23, 127, "t", 1)
    decisions = run_tick(ctx, "fast", "C1")
    d = [x for x in decisions if x.sku == "S1"][0]
    assert d.action == "lower" and d.new_bid == 1
    assert (["S1"], 1) in mk.puts   # ставка в пол реально ушла PUT-ом (бой)

def test_run_tick_in_window_runs_rules_as_before():
    # в окне (14:00) выключателей нет → обычная логика (hold без тормозных триггеров)
    ctx, mk, store = _ctx_with([cp(sku="S1", bid=18, clicks=10, carts=2)], dry_run=True)
    store.set_product_control("C1", "S1", True, 8, 23, 127, "t", 1)
    decisions = run_tick(ctx, "fast", "C1")
    d = [x for x in decisions if x.sku == "S1"][0]
    assert d.loop == "fast"   # прошёл через быстрый контур, а не контрольный слой
```

Примечание для реализатора: `store_with_revenue({})` и `FakeMarketing` уже
определены в `test_worker.py` выше; `cp(...)` — фабрика `CampaignProduct`.
`WorkerContext` берёт `now_fn` — передаём разное «сейчас» для окна/ночи.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_worker.py`
Expected: FAIL — товар без учёта окна проходит в правила; `test_run_tick_out_of_window_lowers_to_floor` не увидит `lower→1` (или `AttributeError`, если helper-фабрику ещё не добавили — добавь `_ctx_with`).

- [ ] **Step 3: Write minimal implementation**

В шапке `worker.py` рядом с другими импортами `core`:

```python
from core.daypart import split_by_control
```

В `run_tick`, после строки `camp_ov = ctx.store.get_overrides("campaign", campaign_id)`
и определения `cfg_for`, ПЕРЕД веткой `if loop == "fast"`, вставить:

```python
    # Контрольный слой (дейпартинг/вкл-выкл) — ДО правил. Вне окна → ставка в пол,
    # выключенный товар → hold. Активные идут в обычные fast/slow.
    controls = ctx.store.list_product_control(campaign_id)
    now_local = now.astimezone(ALMATY)

    def min_bid_for(sku):
        return resolve_config(ctx.cfg, camp_ov,
                              ctx.store.get_overrides("sku", sku)).min_bid

    active, control_decisions = split_by_control(
        reconciled, controls, now_local, min_bid_for)
```

Заменить блок оценки: правила гоняем по `active`, а не по `reconciled`, и
склеиваем контрольные решения впереди:

```python
    if loop == "fast":
        decisions = evaluate_fast(active, cfg_for, state, daily_budget)
    elif loop == "slow":
        decisions = evaluate_slow(active, cfg_for, state)
    else:
        raise ValueError(f"неизвестный контур: {loop}")

    decisions = control_decisions + decisions
    _apply_and_log(ctx, decisions, day, ts, campaign_id)
```

(Строку `_apply_and_log(ctx, decisions, day, ts, campaign_id)`, что была ниже,
эта версия заменяет — не оставляй двойного вызова.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_worker.py`
Expected: PASS — `OK test_worker`.

- [ ] **Step 5: Commit**

```bash
git add worker.py test_worker.py
git commit -m "feat(worker): контрольный слой дейпартинга в run_tick (вне окна→пол)"
```

---

### Task 5: UI — редактор расписания/вкл-выкл на экране товара + индикатор на дашборде

**Files:**
- Modify: `webui/app.py` (GET `sku_settings_form` — прокинуть control; новый `POST /settings/sku/{campaign_id}/{sku}/control`; dashboard — статус по товару)
- Modify: `webui/templates/sku_settings.html` (блок «Управление и расписание»)
- Modify: `webui/templates/dashboard.html` (колонка-индикатор)
- Test: `test_webui.py` (дополнить)

**Interfaces:**
- Consumes: `Store.get_product_control`, `Store.set_product_control`, `Store.all_product_controls` (Task 3); `ProductControl.active_at` (Task 1).
- Produces: route `POST /settings/sku/{campaign_id}/{sku}/control` (form: `enabled`, `window_start`, `window_end`, `day_0..day_6`), после сохранения `303 → /settings/sku/{cid}/{sku}`. Хелпер валидации `_validate_window(start, end) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Открой `test_webui.py`, найди как строится `TestClient` и логинится сессия
(есть фикстура/хелпер `client()` + логин — переиспользуй их). Добавь:

```python
def test_save_product_control_persists():
    client, db_path = make_client()      # существующий хелпер поднятия app+temp db
    login(client)                        # существующий хелпер логина
    r = client.post("/settings/sku/C1/S1/control", data={
        "enabled": "on", "window_start": "8", "window_end": "23",
        "day_0": "on", "day_1": "on", "day_2": "on", "day_3": "on", "day_4": "on",
    }, follow_redirects=False)
    assert r.status_code == 303
    s = Store(db_path)
    c = s.get_product_control("C1", "S1")
    s.close()
    assert c.enabled is True and c.window_start == 8 and c.window_end == 23
    assert c.days_mask == 0b0011111        # Пн..Пт

def test_save_product_control_rejects_bad_window():
    client, db_path = make_client()
    login(client)
    r = client.post("/settings/sku/C1/S1/control", data={
        "enabled": "on", "window_start": "20", "window_end": "8",
        "day_0": "on"}, follow_redirects=False)
    assert r.status_code == 200          # форма с ошибкой, не редирект
    assert "окно" in r.text.lower()
    s = Store(db_path)
    assert s.get_product_control("C1", "S1").window_start == 0   # не сохранилось (дефолт)
    s.close()
```

Примечание: имена `make_client`/`login` замени на реальные хелперы из
`test_webui.py` (посмотри в начале файла). Если там всё инлайном — повтори тот
же способ поднятия приложения и логина, что и в соседних тестах.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_webui.py`
Expected: FAIL — `404` на `POST /settings/sku/C1/S1/control` (route ещё нет).

- [ ] **Step 3: Write minimal implementation**

В `webui/app.py` рядом с другими sku-роутами добавить валидатор и route:

```python
    _WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    def _validate_window(start: int, end: int) -> list[str]:
        errs = []
        if not (0 <= start <= 23):
            errs.append("Час начала окна должен быть 0..23")
        if not (1 <= end <= 24):
            errs.append("Час конца окна должен быть 1..24")
        if start >= end:
            errs.append("Начало окна должно быть раньше конца (окно пустое)")
        return errs

    def _control_ctx(request, campaign_id, sku, errors):
        # общий контекст для GET-формы и POST-с-ошибкой
        store = Store(db_path)
        try:
            ctrl = store.get_product_control(campaign_id, sku)
        finally:
            store.close()
        values, owned = _effective_and_flags(campaign_id, sku)
        return {"user": user(request), "campaign_id": campaign_id, "sku": sku,
                "fields": OVERRIDABLE_FIELDS, "values": values, "owned": owned,
                "errors": errors, "control": ctrl,
                "weekdays": list(enumerate(_WEEKDAYS)),
                "days_on": [bool(ctrl.days_mask & (1 << i)) for i in range(7)]}

    @app.post("/settings/sku/{campaign_id}/{sku}/control")
    async def sku_control_save(request: Request, campaign_id: str, sku: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        enabled = form.get("enabled") == "on"
        try:
            start = int(form.get("window_start", 0))
            end = int(form.get("window_end", 24))
        except ValueError:
            start, end = 0, 0            # заведомо невалидно → сработает _validate_window
        errors = _validate_window(start, end)
        days_mask = 0
        for i in range(7):
            if form.get(f"day_{i}") == "on":
                days_mask |= (1 << i)
        if days_mask == 0:
            errors.append("Выберите хотя бы один день недели")
        if errors:
            return templates.TemplateResponse(
                request, "sku_settings.html",
                _control_ctx(request, campaign_id, sku, errors), status_code=200)
        store = Store(db_path)
        try:
            store.set_product_control(campaign_id, sku, enabled, start, end,
                                      days_mask, user(request), int(time.time()))
        finally:
            store.close()
        return RedirectResponse(
            f"/settings/sku/{campaign_id}/{sku}", status_code=303)
```

Обнови GET `sku_settings_form`, чтобы он тоже отдавал control-контекст (замени
его `TemplateResponse(...)` на `_control_ctx(request, campaign_id, sku, [])`):

```python
    @app.get("/settings/sku/{campaign_id}/{sku}", response_class=HTMLResponse)
    def sku_settings_form(request: Request, campaign_id: str, sku: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request, "sku_settings.html",
            _control_ctx(request, campaign_id, sku, []))
```

В `webui/templates/sku_settings.html` перед закрывающим `{% endblock %}` добавить
блок (отдельная форма — не мешается с формой порогов):

```html
<h2>Управление и расписание</h2>
<div class="card">
  <form method="post" action="/settings/sku/{{ campaign_id }}/{{ sku }}/control">
    <label class="inherit-toggle">
      <input type="checkbox" name="enabled" {{ "checked" if control.enabled else "" }}>
      Биддер активен для товара
    </label>
    <div class="field">
      <label>Рабочее окно (Алматы)</label>
      <span>с <input type="number" min="0" max="23" name="window_start"
                     value="{{ control.window_start }}"> ч
        до <input type="number" min="1" max="24" name="window_end"
                  value="{{ control.window_end }}"> ч</span>
      <span class="hint">Вне окна биддер ставит ставку в минимум (реклама не выключается).</span>
    </div>
    <div class="field">
      <label>Дни недели</label>
      <span>
        {% for i, wd in weekdays %}
        <label class="inherit-toggle">
          <input type="checkbox" name="day_{{ i }}" {{ "checked" if days_on[i] else "" }}> {{ wd }}
        </label>
        {% endfor %}
      </span>
    </div>
    <div class="actions"><button type="submit">Сохранить расписание</button></div>
  </form>
</div>
```

Индикатор на дашборде. В route `dashboard` после сбора `tacos_rows` добавь статус
по товару:

```python
            now_local = datetime.now(ALMATY)
            ctrl_map = {sku: c for cid, sku, c in store.all_product_controls()}
            for row in tacos_rows:
                c = ctrl_map.get(row["sku"])
                if c is None:
                    row["status"] = "активен"
                elif not c.enabled:
                    row["status"] = "выключен"
                elif not c.active_at(now_local):
                    row["status"] = "вне окна"
                else:
                    row["status"] = "активен"
```

В `webui/templates/dashboard.html` в таблице TACoS добавь ячейку `{{ row.status }}`
(заголовок колонки «Статус»). Найди существующий `<tr>` строки товара и вставь
`<td>{{ row.status }}</td>` рядом со ставкой.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_webui.py`
Expected: PASS — `OK test_webui`.

- [ ] **Step 5: Commit**

```bash
git add webui/app.py webui/templates/sku_settings.html webui/templates/dashboard.html test_webui.py
git commit -m "feat(webui): расписание/вкл-выкл на экране товара + индикатор статуса"
```

---

### Task 6: UI — кнопка «Проверить сейчас» (dry-пересчёт по товару)

**Files:**
- Modify: `webui/app.py` (route `POST /settings/sku/{campaign_id}/{sku}/preview`)
- Modify: `webui/templates/sku_settings.html` (кнопка + вывод результата)
- Test: `test_webui.py` (дополнить)

**Interfaces:**
- Consumes: `split_by_control` (Task 2), `evaluate_fast`/`evaluate_slow` (`core.rules`), `reconcile` (`core.reconcile`), `resolve_config`; `Store.get_revenue_cache`, `Store.get_latest_snapshot` (уже есть — используется дашбордом).
- Produces: route, который считает решение по одному SKU из последнего снапшота (без сети) и рендерит `sku_settings.html` с `preview` (action + reason). Ничего не шлёт и в аудит НЕ пишет.

Замечание: чтобы не ходить в кабинет из веб-панели, превью считаем из
**последнего сохранённого снапшота** товара (его наполняет воркер и кнопка
«Обновить»). Это честно показывает, что бот решит по текущим настройкам
(включая только что изменённое окно/флаг), на данных последнего тика.

- [ ] **Step 1: Write the failing test**

```python
def test_preview_returns_decision_without_side_effects():
    client, db_path = make_client()
    login(client)
    # подготовим снапшот товара, чтобы превью было из чего считать
    s = Store(db_path)
    s.save_products_snapshot([cp_web(sku="S1", bid=18)], ts=1000, campaign_id="C1")
    s.set_product_control("C1", "S1", False, 0, 24, 127, "t", 1)   # выключен
    s.close()
    r = client.post("/settings/sku/C1/S1/preview", follow_redirects=False)
    assert r.status_code == 200
    assert "выключен" in r.text.lower()          # решение показано
    s = Store(db_path)
    # аудит НЕ должен получить запись от превью
    assert s.count_decisions_for_sku("S1") == 0
    s.close()
```

Реализатор: `cp_web` — фабрика `CampaignProduct` (скопируй `cp` из `test_worker.py`
или используй существующую в `test_webui.py`). Если метода `count_decisions_for_sku`
нет — проверь отсутствие сайд-эффекта иначе: сравни, что в `decisions_log` по
`S1` пусто (прямой `SELECT` через `store._conn`), либо просто убери эту часть
ассерта — ключевое, что превью не шлёт PUT и возвращает 200 с текстом решения.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python test_webui.py`
Expected: FAIL — `404` на `POST /settings/sku/C1/S1/preview`.

- [ ] **Step 3: Write minimal implementation**

В `webui/app.py`:

```python
    @app.post("/settings/sku/{campaign_id}/{sku}/preview")
    async def sku_preview(request: Request, campaign_id: str, sku: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        from core.reconcile import reconcile
        from core.rules import evaluate_fast, evaluate_slow
        from core.daypart import split_by_control
        store = Store(db_path)
        try:
            snap = store.get_latest_snapshot(sku)     # dict с полями CampaignProduct
            revenue = store.get_revenue_cache()
            g = load_rules_config(rules_path)
            camp_ov = store.get_overrides("campaign", campaign_id)
            controls = store.list_product_control(campaign_id)
            preview = None
            if snap is not None:
                from connectors.marketing_client import CampaignProduct
                p = CampaignProduct(
                    sku=snap["sku"], merchant_sku=snap.get("merchant_sku", ""),
                    campaign_product_id=0, bid=snap["bid"],
                    avg_cpc=snap.get("avg_cpc", 0), score=snap.get("score", 0),
                    buy_box=bool(snap.get("buy_box", False)),
                    product_state=snap.get("product_state", "Active"),
                    cost=snap.get("cost", 0), cost_today=snap.get("cost_today", 0),
                    gmv=0, crr=0, cr=0, ctr=0, views=0,
                    clicks=snap.get("clicks", 0), carts=snap.get("carts", 0),
                    transactions=0, price=snap.get("price", 0))
                reconciled = reconcile([p], revenue)

                def cfg_for(s):
                    return resolve_config(g, camp_ov, store.get_overrides("sku", s.sku))

                def min_bid_for(sk):
                    return resolve_config(g, camp_ov, store.get_overrides("sku", sk)).min_bid

                active, ctrl_dec = split_by_control(
                    reconciled, controls, datetime.now(ALMATY), min_bid_for)
                if ctrl_dec:
                    d = ctrl_dec[0]
                else:
                    fast = evaluate_fast(active, cfg_for)
                    slow = evaluate_slow(active, cfg_for)
                    # показываем оба контура: что решит тормозной и что окупаемостный
                    d = fast[0]
                    preview = {"fast": (fast[0].action, fast[0].reason),
                               "slow": (slow[0].action, slow[0].reason)}
                if preview is None:
                    preview = {"control": (d.action, d.reason)}
        finally:
            store.close()
        ctx = _control_ctx(request, campaign_id, sku, [])
        ctx["preview"] = preview
        return templates.TemplateResponse(request, "sku_settings.html", ctx)
```

В `sku_settings.html` в блоке «Управление и расписание» добавить кнопку и вывод
(вторая форма — превью не сохраняет):

```html
<form method="post" action="/settings/sku/{{ campaign_id }}/{{ sku }}/preview">
  <button type="submit" class="secondary">Проверить сейчас</button>
</form>
{% if preview %}
<div class="card">
  <strong>Что бот сделает сейчас (по последнему снапшоту, ничего не отправлено):</strong>
  <ul>
    {% for k, v in preview.items() %}
    <li>{{ k }}: <b>{{ v[0] }}</b> — {{ v[1] }}</li>
    {% endfor %}
  </ul>
</div>
{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python test_webui.py`
Expected: PASS — `OK test_webui`.

- [ ] **Step 5: Commit**

```bash
git add webui/app.py webui/templates/sku_settings.html test_webui.py
git commit -m "feat(webui): «Проверить сейчас» — dry-пересчёт решения по товару"
```

---

## Финальная проверка (после всех задач)

- [ ] Прогнать весь набор тестов:

```bash
for t in test_daypart test_store test_worker test_webui; do
  .venv/bin/python $t.py || echo "FAIL: $t"
done
```

Ожидаемо: каждая строка `OK test_...`, ни одного `FAIL`.

- [ ] Ручной смоук веб-панели (по желанию): `./run_webui_local.sh`, зайти на
  `/settings/sku/<campaign>/<sku>`, выключить товар/задать окно, нажать
  «Проверить сейчас», проверить индикатор на `/`.

## Покрытие спека (self-review)

- Дейпартинг (окно+дни, вне окна→min_bid) → Task 1,2,4.
- Вкл/выкл по товару (default enabled) → Task 1,2,3,4 (дефолт `ProductControl()`).
- Таблица `product_control` + методы → Task 3.
- `rules.py` не меняется → выполнено (вся логика в `core/daypart.py` + worker).
- UI-редактор расписания/флага + дни недели → Task 5.
- Индикатор статуса на дашборде → Task 5.
- «Проверить сейчас» → Task 6.
- Kill-switch = существующий тумблер dry_run → вне плана (уже есть), заметность — не критично для MVP, опущено осознанно.
- Тесты plain-script по каждому слою → в каждой задаче.
