# Multi-Campaign Autopilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Автопилот сам обнаруживает все активные (`Enabled`) рекламные кампании и управляет ставками в каждой, вместо одной захардкоженной.

**Architecture:** Обнаружение отделено от per-campaign логики. `MarketingClient.list_active_campaigns()` тянет список кампаний; `worker.run_cycle(ctx, loop)` обнаруживает активные и вызывает существующую `run_tick(ctx, loop, campaign_id)` по каждой с изоляцией ошибок. Revenue-цикл глобальный, не меняется. `decisions_log` получает колонку `campaign_id` для аудита.

**Tech Stack:** Python 3.9 (`.venv`), httpx, SQLite (stdlib sqlite3), APScheduler (только в `main()`). Тесты — plain-скрипты с `assert`, запуск `.venv/bin/python test_X.py` (НЕ pytest). Зависимости инъектируются (клиенты/стор/коллектор передаются снаружи).

## Global Constraints

- Тесты — runnable-скрипты с `assert` и `print("✓ …")`, каждый регистрируется в блоке `if __name__ == "__main__":`. Запуск: `.venv/bin/python test_<mod>.py`. НЕ pytest.
- Стиль дома: русские докстринги/сообщения; ретрай 429/5xx с бэкоффом; чистые функции тестируются с фейками, сеть/браузер/APScheduler только в `worker.main()`.
- `merchant_id` маркетинга = `832398` (НЕ shop-id). Эндпоинт списка кампаний:
  `GET /advertising/products/api/v5/merchant/{merchant_id}/Campaigns?StartDate=&EndDate=&state=Enabled` → `{"data":[{"id":<int>,"name":<str>,"state":<str>}, …]}`.
- Инвариант: один товар (`sku`) = одна активная кампания. Ключевание store по `sku` не меняем.
- Ветка работы: `multi-campaign-autopilot`. `dry_run` в `config/rules.yaml` остаётся `true`.
- `config/.env` в `.gitignore` — НЕ коммитить. Правки конфига только в `config/.env.example`.

---

### Task 1: `list_active_campaigns` в MarketingClient

**Files:**
- Modify: `connectors/marketing_client.py` (добавить датакласс `Campaign` рядом с `CampaignProduct`; метод `list_active_campaigns` в классе `MarketingClient` после `get_campaign_products`)
- Test: `test_marketing.py` (добавить тест + вызвать его в `__main__`)

**Interfaces:**
- Consumes: существующий `MarketingClient._request("GET", url, params=...)`, поле `self.merchant_id`.
- Produces:
  - `Campaign(id: str, name: str, state: str)` — dataclass.
  - `MarketingClient.list_active_campaigns(start_date: str, end_date: str) -> list[Campaign]` — только кампании со `state == "Enabled"`.

- [ ] **Step 1: Написать падающий тест**

В `test_marketing.py` добавить (после `test_get_products_parses_fields`):

```python
SAMPLE_CAMPAIGNS = {
    "data": [
        {"id": 2899523, "name": "Бритвы", "state": "Enabled"},
        {"id": 3032419, "name": "Аэрогриль 08.08.2026", "state": "Enabled"},
        {"id": 2711494, "name": "Аэрогриль", "state": "Paused"},
        {"id": 2268077, "name": "5 ноября", "state": "Archived"},
    ],
}


def test_list_active_campaigns_filters_enabled():
    from connectors.marketing_client import Campaign
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=SAMPLE_CAMPAIGNS)

    with make_client(handler) as mc:
        campaigns = mc.list_active_campaigns("2026-08-10", "2026-08-11")

    assert f"merchant/{MERCHANT_ID}/Campaigns" in captured["url"], captured["url"]
    assert "state=Enabled" in captured["url"], captured["url"]
    assert "StartDate=2026-08-10" in captured["url"], captured["url"]
    assert "EndDate=2026-08-11" in captured["url"], captured["url"]
    # только Enabled, id приведён к строке
    assert [(c.id, c.name) for c in campaigns] == [
        ("2899523", "Бритвы"), ("3032419", "Аэрогриль 08.08.2026")
    ]
    assert all(isinstance(c, Campaign) and c.state == "Enabled" for c in campaigns)
    print("✓ marketing: list_active_campaigns фильтрует Enabled и парсит поля")
```

Добавить вызов в `__main__` (перед строкой `print("-" * 60)`):

```python
    test_list_active_campaigns_filters_enabled()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_marketing.py`
Expected: FAIL — `ImportError: cannot import name 'Campaign'` (или `AttributeError: … 'list_active_campaigns'`).

- [ ] **Step 3: Реализовать `Campaign` + метод**

В `connectors/marketing_client.py` добавить датакласс сразу после класса `CampaignProduct` (перед `class MarketingClient`):

```python
@dataclass
class Campaign:
    """Рекламная кампания кабинета (шапка списка)."""
    id: str
    name: str
    state: str          # Enabled / Paused / Archived
```

В классе `MarketingClient`, сразу после метода `get_campaign_products`, добавить:

```python
    def list_active_campaigns(
        self, start_date: str, end_date: str
    ) -> list["Campaign"]:
        """
        Список кампаний в статусе Enabled за окно [start_date, end_date]
        (YYYY-MM-DD). Ответ: {"data": [{"id", "name", "state"}, ...]}.
        Фильтр state=Enabled шлём серверу и дублируем на клиенте (страховка).
        """
        url = (
            f"/advertising/products/api/v5/merchant/{self.merchant_id}/Campaigns"
        )
        params = {"StartDate": start_date, "EndDate": end_date, "state": "Enabled"}
        data = self._request("GET", url, params=params)

        out: list[Campaign] = []
        for row in data.get("data", []):
            state = str(row.get("state", ""))
            if state != "Enabled":
                continue
            out.append(Campaign(
                id=str(row.get("id", "")),
                name=str(row.get("name", "")),
                state=state,
            ))
        return out
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_marketing.py`
Expected: PASS — все проверки marketing, включая новую.

- [ ] **Step 5: Коммит**

```bash
git add connectors/marketing_client.py test_marketing.py
git commit -m "feat(marketing): list_active_campaigns — обнаружение активных кампаний"
```

---

### Task 2: `campaign_id` в decisions_log (колонка + миграция + log_decision)

**Files:**
- Modify: `core/store.py` (`_init_schema` — колонка в CREATE + миграция ALTER; `log_decision` — параметр `campaign_id`)
- Test: `test_store.py` (два теста + вызовы в `__main__`)

**Interfaces:**
- Consumes: существующий `self._conn` (sqlite3 с `row_factory=sqlite3.Row`), `Decision`.
- Produces: `Store.log_decision(self, d: Decision, ts: int, day: str, applied: bool, campaign_id: str = "") -> None` — пишет `campaign_id` в новую колонку. Схема `decisions_log` содержит `campaign_id TEXT`; для старой БД без колонки — авто-`ALTER`.

- [ ] **Step 1: Написать падающие тесты**

В `test_store.py` добавить (после `test_get_decisions_for_day`):

```python
def test_log_decision_writes_campaign_id():
    st = new_store()
    st.log_decision(dec(action="lower"), ts=1000, day="2026-08-09",
                    applied=True, campaign_id="2899523")
    rows = st.get_decisions_for_day("2026-08-09")
    assert rows[0]["campaign_id"] == "2899523"
    print("✓ store: log_decision пишет campaign_id")


def test_migration_adds_campaign_id_to_old_db():
    import os, sqlite3, tempfile
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    # старая схема decisions_log БЕЗ campaign_id
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE decisions_log (ts INTEGER, day TEXT, sku TEXT, "
        "merchant_sku TEXT, old_bid REAL, new_bid REAL, action TEXT, "
        "loop TEXT, reason TEXT, applied INTEGER);"
    )
    con.commit(); con.close()

    st = Store(path)   # инициализация должна добавить колонку
    cols = {r["name"] for r in st._conn.execute("PRAGMA table_info(decisions_log)")}
    assert "campaign_id" in cols, cols
    # и запись после миграции работает
    st.log_decision(dec(), ts=1, day="2026-08-09", applied=False, campaign_id="X")
    assert st.get_decisions_for_day("2026-08-09")[0]["campaign_id"] == "X"
    print("✓ store: миграция добавляет campaign_id в старую БД")
```

Добавить в `__main__` (перед `print("-" * 60)`):

```python
    test_log_decision_writes_campaign_id()
    test_migration_adds_campaign_id_to_old_db()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_store.py`
Expected: FAIL — `TypeError: log_decision() got an unexpected keyword argument 'campaign_id'`.

- [ ] **Step 3: Реализовать колонку, миграцию и параметр**

В `core/store.py`, в `_init_schema`, в блоке `CREATE TABLE IF NOT EXISTS decisions_log (...)` добавить колонку `campaign_id TEXT` в конец списка полей:

```python
            CREATE TABLE IF NOT EXISTS decisions_log (
                ts INTEGER, day TEXT, sku TEXT, merchant_sku TEXT, old_bid REAL,
                new_bid REAL, action TEXT, loop TEXT, reason TEXT, applied INTEGER,
                campaign_id TEXT
            );
```

В `_init_schema`, сразу ПОСЛЕ `self._conn.executescript(""" … """)` и ДО `self._conn.commit()`, добавить миграцию (CREATE IF NOT EXISTS не добавляет колонку к уже существующей таблице):

```python
        # Миграция старой БД: добавить campaign_id, если таблица уже была без него.
        cols = {r["name"] for r in
                self._conn.execute("PRAGMA table_info(decisions_log)")}
        if "campaign_id" not in cols:
            self._conn.execute(
                "ALTER TABLE decisions_log ADD COLUMN campaign_id TEXT")
```

Заменить метод `log_decision` целиком на:

```python
    def log_decision(self, d: Decision, ts: int, day: str, applied: bool,
                     campaign_id: str = ""):
        self._conn.execute(
            """INSERT INTO decisions_log
               (ts, day, sku, merchant_sku, old_bid, new_bid, action, loop,
                reason, applied, campaign_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, day, d.sku, d.merchant_sku, d.old_bid, d.new_bid,
             d.action, d.loop, d.reason, int(applied), campaign_id),
        )
        self._conn.commit()
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_store.py`
Expected: PASS — все проверки store, включая две новые. (Существующие вызовы `log_decision` без `campaign_id` работают — параметр со значением по умолчанию `""`.)

- [ ] **Step 5: Коммит**

```bash
git add core/store.py test_store.py
git commit -m "feat(store): campaign_id в decisions_log + миграция старой БД"
```

---

### Task 3: `run_tick` — на одну кампанию (campaign_id параметром)

**Files:**
- Modify: `worker.py` (`WorkerContext` — заменить поле; `run_tick` — параметр `campaign_id`; `_apply_and_log` — параметр `campaign_id`)
- Test: `test_worker.py` (обновить хелпер `ctx` и вызовы `run_tick`; revenue-тест)

**Interfaces:**
- Consumes: `ctx.marketing.update_bids(campaign_id, skus, bid)`, `ctx.store.log_decision(d, ts, day, applied, campaign_id)` (из Task 2).
- Produces:
  - `WorkerContext` больше НЕ имеет `campaign_id`; имеет `campaign_ids: list[str] | None = None` (allowlist; `None`/пусто = все активные — используется в Task 4).
  - `run_tick(ctx: WorkerContext, loop: str, campaign_id: str) -> list[Decision]`.
  - `_apply_and_log(ctx, decisions, day, ts, campaign_id)`.

- [ ] **Step 1: Обновить тесты под новую сигнатуру (падающие)**

В `test_worker.py` заменить хелпер `ctx` на (убрать `campaign_id`):

```python
def ctx(marketing, store, dry_run):
    return WorkerContext(
        marketing=marketing, store=store,
        cfg=RulesConfig(dry_run=dry_run), now_fn=NOW,
    )
```

В трёх тестах заменить вызовы `run_tick(ctx(...), loop=...)` на вызовы с `campaign_id`:

```python
    decisions = run_tick(ctx(fm, st, dry_run=True), loop="slow", campaign_id="2711494")
```
```python
    decisions = run_tick(ctx(fm, st, dry_run=False), loop="slow", campaign_id="2711494")
```
```python
    decisions = run_tick(ctx(fm, st, dry_run=False), loop="fast", campaign_id="2711494")
```

В `test_revenue_cycle_fills_cache` убрать `campaign_id="x"` из `WorkerContext(...)`:

```python
    c = WorkerContext(marketing=None, store=st,
                      cfg=RulesConfig(), revenue_collector=collector, now_fn=NOW)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_worker.py`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument` уже нет, но `run_tick()` ещё принимает `(ctx, loop)` → `TypeError: run_tick() got an unexpected keyword argument 'campaign_id'` (или `WorkerContext` ещё требует `campaign_id`). В любом случае — FAIL.

- [ ] **Step 3: Изменить `WorkerContext`, `run_tick`, `_apply_and_log`**

В `worker.py`, в датаклассе `WorkerContext` заменить строку `campaign_id: str` на:

```python
    campaign_ids: list[str] | None = None   # allowlist; None/пусто = все активные
```

(поле идёт после обязательных; порядок полей: `marketing`, `store`, `cfg`, затем опциональные `campaign_ids`, `revenue_collector`, `window_days`, `now_fn`. Убедиться, что `cfg` остаётся обязательным до опциональных — переставить `campaign_ids` вниз к опциональным.)

Итоговый `WorkerContext`:

```python
@dataclass
class WorkerContext:
    marketing: object            # MarketingClient (несёт свой dry_run)
    store: object                # Store
    cfg: RulesConfig
    campaign_ids: list[str] | None = None     # allowlist; None/пусто = все активные
    revenue_collector: object | None = None   # RevenueCollector (для revenue-цикла)
    window_days: int = 2
    now_fn: Callable[[], datetime] = field(default=lambda: datetime.now(ALMATY))
```

Изменить сигнатуру `run_tick` и убрать `ctx.campaign_id`:

```python
def run_tick(ctx: WorkerContext, loop: str, campaign_id: str):
```

Внутри `run_tick` заменить строку чтения товаров:

```python
    products = ctx.marketing.get_campaign_products(campaign_id, start_date, end_date)
```

И вызов `_apply_and_log` в конце `run_tick`:

```python
    _apply_and_log(ctx, decisions, day, ts, campaign_id)
```

Изменить сигнатуру и тело `_apply_and_log`:

```python
def _apply_and_log(ctx: WorkerContext, decisions: list, day: str, ts: int,
                   campaign_id: str):
```

Внутри `_apply_and_log` заменить вызов `update_bids`:

```python
        result = ctx.marketing.update_bids(campaign_id, skus, bid)
```

И вызов `log_decision`:

```python
        ctx.store.log_decision(d, ts=ts, day=day, applied=applied,
                               campaign_id=campaign_id)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_worker.py`
Expected: PASS — все 4 проверки worker.

- [ ] **Step 5: Коммит**

```bash
git add worker.py test_worker.py
git commit -m "refactor(worker): run_tick на одну кампанию (campaign_id параметром)"
```

---

### Task 4: `run_cycle` — обход всех активных кампаний + обвязка main()

**Files:**
- Modify: `worker.py` (новая функция `run_cycle`; `main()` — чтение `KASPI_CAMPAIGN_IDS`, расписания зовут `run_cycle`)
- Modify: `config/.env.example` (deprecate `KASPI_CAMPAIGN_ID`, добавить `KASPI_CAMPAIGN_IDS`)
- Test: `test_worker.py` (тесты `run_cycle` + вызовы в `__main__`)

**Interfaces:**
- Consumes: `ctx.marketing.list_active_campaigns(start_date, end_date) -> list[Campaign]` (Task 1); `run_tick(ctx, loop, campaign_id)` (Task 3); `ctx.campaign_ids`.
- Produces: `run_cycle(ctx: WorkerContext, loop: str) -> list[Decision]` — обнаруживает активные кампании (с учётом allowlist), гоняет `run_tick` по каждой с изоляцией ошибок, возвращает агрегат решений.

- [ ] **Step 1: Написать падающие тесты**

В `test_worker.py` расширить `FakeMarketing`, добавив список кампаний и метод (добавить в `__init__` параметр и метод в класс):

```python
    # в FakeMarketing.__init__ добавить аргумент campaigns=None и сохранить:
    #     self._campaigns = campaigns or []
    # и метод:
    def list_active_campaigns(self, start, end):
        self.seen_campaign_dates = (start, end)
        return list(self._campaigns)
```

Обновить конструктор `FakeMarketing` целиком:

```python
class FakeMarketing:
    def __init__(self, products, dry_run, campaigns=None):
        self._products = products
        self.dry_run = dry_run
        self._campaigns = campaigns or []
        self.puts = []
        self.ticked = []                 # campaign_id, по которым звали get_campaign_products

    def get_campaign_products(self, campaign_id, start, end):
        self.seen_dates = (start, end)
        self.ticked.append(campaign_id)
        return self._products

    def update_bids(self, campaign_id, sku_list, bid):
        sent = not self.dry_run
        if sent:
            self.puts.append((list(sku_list), bid))
        return {"skuList": list(sku_list), "bid": bid, "dry_run": self.dry_run, "sent": sent}

    def list_active_campaigns(self, start, end):
        self.seen_campaign_dates = (start, end)
        return list(self._campaigns)
```

Добавить тесты (после `test_revenue_cycle_fills_cache`), используя `Campaign` и `run_cycle`:

```python
from connectors.marketing_client import Campaign          # добавить к импортам сверху
from worker import run_cycle                              # добавить к импортам сверху


def _camps(*pairs):
    return [Campaign(id=i, name=n, state="Enabled") for i, n in pairs]


def test_run_cycle_ticks_every_active_campaign():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(bid=18)], dry_run=True,
                       campaigns=_camps(("2899523", "Бритвы"), ("3032419", "Аэрогриль")))
    decisions = run_cycle(ctx(fm, st, dry_run=True), loop="slow")
    assert fm.ticked == ["2899523", "3032419"], fm.ticked
    assert len(decisions) == 2                      # по одному решению на кампанию
    print("✓ worker: run_cycle гоняет тик по каждой активной кампании")


def test_run_cycle_allowlist_narrows():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(bid=18)], dry_run=True,
                       campaigns=_camps(("2899523", "Бритвы"), ("3032419", "Аэрогриль")))
    c = WorkerContext(marketing=fm, store=st, cfg=RulesConfig(dry_run=True),
                      campaign_ids=["3032419"], now_fn=NOW)
    run_cycle(c, loop="slow")
    assert fm.ticked == ["3032419"], fm.ticked
    print("✓ worker: run_cycle уважает allowlist campaign_ids")


def test_run_cycle_isolates_failing_campaign():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})

    class BoomOnFirst(FakeMarketing):
        def get_campaign_products(self, campaign_id, start, end):
            if campaign_id == "BAD":
                raise RuntimeError("кабинет отдал 500")
            return super().get_campaign_products(campaign_id, start, end)

    fm = BoomOnFirst([cp(bid=18)], dry_run=True,
                     campaigns=_camps(("BAD", "Плохая"), ("2899523", "Бритвы")))
    decisions = run_cycle(ctx(fm, st, dry_run=True), loop="slow")
    # первая упала, вторая обработана
    assert fm.ticked == ["2899523"], fm.ticked
    assert len(decisions) == 1
    print("✓ worker: run_cycle изолирует упавшую кампанию, остальные идут")


def test_run_cycle_empty_is_noop():
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing([cp(bid=18)], dry_run=True, campaigns=[])
    decisions = run_cycle(ctx(fm, st, dry_run=True), loop="slow")
    assert decisions == [] and fm.ticked == []
    print("✓ worker: run_cycle с пустым списком — no-op")
```

Добавить в `__main__` (перед `print("-" * 60)`):

```python
    test_run_cycle_ticks_every_active_campaign()
    test_run_cycle_allowlist_narrows()
    test_run_cycle_isolates_failing_campaign()
    test_run_cycle_empty_is_noop()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_worker.py`
Expected: FAIL — `ImportError: cannot import name 'run_cycle' from 'worker'`.

- [ ] **Step 3: Реализовать `run_cycle`**

В `worker.py`, сразу ПОСЛЕ функции `run_tick` (и до `_apply_and_log`), добавить:

```python
def run_cycle(ctx: WorkerContext, loop: str):
    """
    Один цикл выбранного контура по ВСЕМ активным кампаниям: обнаруживает
    Enabled-кампании (с учётом allowlist ctx.campaign_ids) и гоняет run_tick
    по каждой. Изоляция: падение одной кампании логируется и не рушит остальные;
    недоступный список кампаний → цикл пропускаем, планировщик жив.
    """
    now = ctx.now_fn()
    start_date, end_date = _almaty_dates(ctx.window_days, now)
    try:
        campaigns = ctx.marketing.list_active_campaigns(start_date, end_date)
    except Exception as e:
        log.error("Список кампаний недоступен, цикл %s пропущен: %s", loop, e)
        return []

    allow = set(ctx.campaign_ids or [])
    if allow:
        campaigns = [c for c in campaigns if c.id in allow]

    all_decisions: list = []
    for c in campaigns:
        try:
            all_decisions.extend(run_tick(ctx, loop, c.id))
        except Exception as e:
            log.error("Кампания %s (%s) упала на контуре %s: %s",
                      c.id, c.name, loop, e)
            continue

    log.info("Цикл %s: кампаний=%s, решений=%s, изменений=%s", loop,
             len(campaigns), len(all_decisions),
             sum(1 for d in all_decisions if d.changed))
    return all_decisions
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_worker.py`
Expected: PASS — все проверки worker, включая 4 новых `run_cycle`.

- [ ] **Step 5: Обновить `main()` под мульти-кампанию**

В `worker.py`, в `main()`:

Заменить строку `campaign_id = os.environ["KASPI_CAMPAIGN_ID"]` на:

```python
    ids_env = os.environ.get("KASPI_CAMPAIGN_IDS", "").strip()
    campaign_ids = [c.strip() for c in ids_env.split(",") if c.strip()] or None
```

Заменить тело `build_ctx` (поле контекста):

```python
    def build_ctx() -> WorkerContext:
        # Свежие куки на каждый цикл; при блокировке SessionManager сам поднимет алерт+стоп.
        cookies = session.get_cookies()
        marketing = MarketingClient(merchant_id, cookies=cookies, dry_run=cfg.dry_run)
        return WorkerContext(marketing=marketing, store=store, cfg=cfg,
                             campaign_ids=campaign_ids,
                             revenue_collector=revenue_collector)
```

Заменить регистрацию `fast`/`slow` расписаний на `run_cycle`:

```python
    sched.add_job(lambda: run_cycle(build_ctx(), "fast"), "interval", minutes=20,
                  id="fast")
    sched.add_job(lambda: run_cycle(build_ctx(), "slow"), "cron", hour="10,20",
                  id="slow")
```

Обновить строку лога запуска (текст «Автопилот запущен …»), добавив охват кампаний:

```python
    log.info("Автопилот запущен (dry_run=%s, кампании=%s). Расписания: revenue/60м, "
             "fast/20м, slow/10:00,20:00, analyst/22:00 (Алматы)",
             cfg.dry_run, campaign_ids or "все активные")
```

- [ ] **Step 6: Обновить `config/.env.example`**

Заменить строку `KASPI_CAMPAIGN_ID=2711494 …` на блок:

```
# (устарело) KASPI_CAMPAIGN_ID больше не используется воркером.
# Охват кампаний задаётся ниже; пусто = вести ВСЕ активные (Enabled).
KASPI_CAMPAIGN_IDS=                        # список id через запятую, напр. 2899523,3032419
```

- [ ] **Step 7: Запустить весь набор тестов**

Run:
```bash
for t in test_revenue test_marketing test_session test_reconcile test_rules test_store test_worker test_analyst; do .venv/bin/python $t.py >/dev/null && echo "OK $t" || echo "FAIL $t"; done
```
Expected: OK по всем 8.

- [ ] **Step 8: Коммит**

```bash
git add worker.py config/.env.example test_worker.py
git commit -m "feat(worker): run_cycle ведёт все активные кампании + KASPI_CAMPAIGN_IDS"
```

---

## Post-plan: живой прогон (после реализации)

Не часть коммитов; проверка на живых данных (dry_run):
- Обновить локальный `config/.env`: добавить `KASPI_CAMPAIGN_IDS=` (пусто = все активные). `KASPI_CAMPAIGN_ID` можно оставить — воркер его игнорирует.
- Прогнать разово (scratchpad-раннер по образцу `oneshot_tick.py`, но зовущий `run_cycle(build_ctx(), "fast")` и `"slow"`) — убедиться, что тикаются обе активные кампании (Бритвы + Аэрогриль), решения логируются с `campaign_id`, PUT не идёт.
- `db/autopilot.db` мигрируется автоматически (добавится колонка `campaign_id`).
