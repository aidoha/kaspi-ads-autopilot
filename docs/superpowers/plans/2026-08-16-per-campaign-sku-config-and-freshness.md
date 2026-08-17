# Per-Campaign/SKU Config + Freshness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Позволить регулировать пороги движка ставок отдельно на уровне кампании и товара (наследование глобал→кампания→товар), и показать свежесть данных дашборда с ручным обновлением.

**Architecture:** Глобальные дефолты остаются в `config/rules.yaml`. Отличия (overrides) хранятся в новой SQLite-таблице `config_overrides`. Чистая функция `resolve_config` собирает эффективный `RulesConfig` для товара. Движок `evaluate_fast/slow` принимает cfg-резолвер (callable) вместо единого cfg, оставаясь без ввода-вывода. UI даёт страницы настроек кампании/товара с наследованием и метку свежести + кнопку обновления.

**Tech Stack:** Python 3, FastAPI + Jinja2 (webui), stdlib sqlite3 (store), PyYAML (rules), APScheduler (worker). Тесты — plain-script (без pytest), запуск через `.venv`.

## Global Constraints

- Тесты — **plain-script**, стиль `test_settings_io.py`: функции `test_*`, вызовы в `if __name__ == "__main__"`, `assert`, финальный `print("✓ ...")`. Запуск: `.venv/bin/python test_<name>.py`. **Нет pytest.**
- Движок `core/rules.py` остаётся ЧИСТЫМ: без сети/БД. Резолвер конфига инъектируется как функция.
- Переопределяемые поля (11): `target_tacos_low`, `target_tacos_high`, `daily_sku_cost_limit`, `sku_budget_fraction`, `min_clicks_for_no_cart_cut`, `cpc_spike_pct`, `max_bid_step`, `max_changes_per_day`, `bid_ceiling`, `min_bid`, `min_score_for_raise`.
- **Global-only, НЕ переопределяются:** `dry_run`, `campaign_ids`.
- Комментарии и UI-строки — на русском, в тон существующему коду.
- Коммиты — частые, по одной задаче. Хвост сообщения коммита:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` и строку `Claude-Session:` как в истории репо.
- Ветка: `feat/per-campaign-sku-config` (уже создана, спека закоммичена туда).

---

## File Structure

- **Create** `core/config_resolver.py` — `OVERRIDABLE_FIELDS`, `resolve_config`, приведение типов.
- **Modify** `core/store.py` — таблица `config_overrides` + CRUD; миграция `campaign_id` в `products_snapshot`; `save_products_snapshot(campaign_id=...)`; `get_campaign_skus`, `get_latest_snapshot_ts`.
- **Modify** `core/rules.py` — `evaluate_fast/slow` принимают `cfg` как `RulesConfig | Callable`.
- **Modify** `worker.py` — `run_tick` резолвит cfg на товар + пишет `campaign_id` в снапшот.
- **Modify** `webui/app.py` — роуты настроек кампании/товара, `/refresh`, метка свежести.
- **Create** `webui/templates/campaign_settings.html`, `webui/templates/sku_settings.html`.
- **Modify** `webui/templates/dashboard.html` — метка свежести, кнопка «Обновить», ссылки на настройки кампаний.
- **Create/Modify tests:** `test_config_resolver.py` (new), `test_store.py`, `test_rules.py`, `test_worker.py`, `test_webui.py`.

---

## PART 1 — Иерархический конфиг

### Task 1: Store — таблица `config_overrides` + CRUD

**Files:**
- Modify: `core/store.py` (`_init_schema`, новые методы)
- Test: `test_store.py`

**Interfaces:**
- Produces:
  - `Store.get_overrides(scope: str, scope_id: str) -> dict[str, str]` — `{field: raw_value}`, пусто если нет.
  - `Store.set_override(scope: str, scope_id: str, field: str, value: str, user: str, ts: int) -> None` — upsert.
  - `Store.delete_override(scope: str, scope_id: str, field: str) -> None` — no-op если нет.

- [ ] **Step 1: Написать падающий тест** — добавить в `test_store.py`:

```python
def test_config_overrides_crud():
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    st = Store(p)
    try:
        assert st.get_overrides("campaign", "2899523") == {}
        st.set_override("campaign", "2899523", "bid_ceiling", "80", "admin", 111)
        st.set_override("campaign", "2899523", "min_bid", "5", "admin", 111)
        assert st.get_overrides("campaign", "2899523") == {"bid_ceiling": "80", "min_bid": "5"}
        # upsert перезаписывает
        st.set_override("campaign", "2899523", "bid_ceiling", "90", "admin", 222)
        assert st.get_overrides("campaign", "2899523")["bid_ceiling"] == "90"
        # разные scope изолированы
        st.set_override("sku", "SKU-1", "bid_ceiling", "40", "admin", 111)
        assert st.get_overrides("sku", "SKU-1") == {"bid_ceiling": "40"}
        assert st.get_overrides("campaign", "2899523")["bid_ceiling"] == "90"
        # delete
        st.delete_override("campaign", "2899523", "bid_ceiling")
        assert st.get_overrides("campaign", "2899523") == {"min_bid": "5"}
        st.delete_override("campaign", "2899523", "nope")  # no-op не падает
    finally:
        st.close()
    print("✓ store: config_overrides CRUD")
```

Добавить вызов `test_config_overrides_crud()` в блок `if __name__ == "__main__"`.

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_store.py`
Expected: FAIL (`AttributeError: 'Store' object has no attribute 'get_overrides'`).

- [ ] **Step 3: Реализовать** — в `_init_schema` (внутри `executescript`, рядом с другими таблицами) добавить:

```sql
            CREATE TABLE IF NOT EXISTS config_overrides (
                scope TEXT, scope_id TEXT, field TEXT, value TEXT,
                user TEXT, ts INTEGER,
                PRIMARY KEY (scope, scope_id, field)
            );
```

Добавить методы в класс `Store` (рядом с аудитом настроек):

```python
    # ---- overrides конфига (кампания/товар) ---------------------------------

    def get_overrides(self, scope: str, scope_id: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT field, value FROM config_overrides WHERE scope=? AND scope_id=?",
            (scope, scope_id),
        ).fetchall()
        return {r["field"]: r["value"] for r in rows}

    def set_override(self, scope: str, scope_id: str, field: str,
                     value: str, user: str, ts: int) -> None:
        self._conn.execute(
            """INSERT INTO config_overrides (scope, scope_id, field, value, user, ts)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(scope, scope_id, field) DO UPDATE SET
                 value=excluded.value, user=excluded.user, ts=excluded.ts""",
            (scope, scope_id, field, str(value), user, ts),
        )
        self._conn.commit()

    def delete_override(self, scope: str, scope_id: str, field: str) -> None:
        self._conn.execute(
            "DELETE FROM config_overrides WHERE scope=? AND scope_id=? AND field=?",
            (scope, scope_id, field),
        )
        self._conn.commit()
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `.venv/bin/python test_store.py`
Expected: PASS (все тесты стора, включая новый).

- [ ] **Step 5: Коммит**

```bash
git add core/store.py test_store.py
git commit -m "feat(store): таблица config_overrides + CRUD (наследование настроек)"
```

---

### Task 2: Store — `campaign_id` в снапшотах + списки для UI/свежести

**Files:**
- Modify: `core/store.py` (`_init_schema` миграция, `save_products_snapshot`, новые методы)
- Test: `test_store.py`

**Interfaces:**
- Consumes: `products_snapshot` (колонки из Task-контекста), `CampaignProduct`.
- Produces:
  - `Store.save_products_snapshot(products, ts, campaign_id: str = "")` — новый необязательный параметр.
  - `Store.get_campaign_skus(campaign_id: str) -> list[dict]` — `[{"sku","merchant_sku","bid"}]`, по одному свежему на SKU.
  - `Store.get_latest_snapshot_ts() -> int | None` — `MAX(ts)` по всем снапшотам.

- [ ] **Step 1: Написать падающий тест** — добавить в `test_store.py`:

```python
def _mk_product(sku, merchant_sku="m", bid=10.0):
    from connectors.marketing_client import CampaignProduct
    return CampaignProduct(
        sku=sku, merchant_sku=merchant_sku, campaign_product_id=0,
        bid=bid, avg_cpc=1.0, score=5.0, cost=0.0, cost_today=0.0,
        clicks=0, carts=0, product_state="Active", price=100.0, name="n")

def test_snapshot_campaign_id_and_lists():
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    st = Store(p)
    try:
        st.save_products_snapshot([_mk_product("A", bid=10), _mk_product("B", bid=20)],
                                  ts=100, campaign_id="C1")
        st.save_products_snapshot([_mk_product("A", bid=15)], ts=200, campaign_id="C1")
        st.save_products_snapshot([_mk_product("Z", bid=99)], ts=150, campaign_id="C2")
        skus = st.get_campaign_skus("C1")
        by = {r["sku"]: r for r in skus}
        assert set(by) == {"A", "B"}, by
        assert by["A"]["bid"] == 15  # свежий снапшот A
        assert st.get_latest_snapshot_ts() == 200
        assert {r["sku"] for r in st.get_campaign_skus("C2")} == {"Z"}
    finally:
        st.close()
    print("✓ store: campaign_id в снапшотах + get_campaign_skus/get_latest_snapshot_ts")
```

Добавить вызов в `if __name__ == "__main__"`. Проверь, что `CampaignProduct` в `connectors/marketing_client.py` действительно принимает поля `name` и `price` — если сигнатура иного порядка, поправь `_mk_product` под неё (используй именованные аргументы, как выше).

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_store.py`
Expected: FAIL (`save_products_snapshot() got an unexpected keyword argument 'campaign_id'`).

- [ ] **Step 3: Реализовать**

В `_init_schema`, после блока миграции `decisions_log.campaign_id`, добавить такую же миграцию для снапшотов:

```python
        snap_cols = {r["name"] for r in
                     self._conn.execute("PRAGMA table_info(products_snapshot)")}
        if "campaign_id" not in snap_cols:
            self._conn.execute(
                "ALTER TABLE products_snapshot ADD COLUMN campaign_id TEXT")
        self._conn.commit()
```

(Свежая БД создаётся без `campaign_id` в `CREATE TABLE` — миграция добавит колонку и там, и в старых БД; менять сам `CREATE TABLE` не обязательно, но можно дописать `campaign_id TEXT` в конец списка колонок для наглядности.)

Изменить `save_products_snapshot`:

```python
    def save_products_snapshot(self, products: list[CampaignProduct], ts: int,
                               campaign_id: str = ""):
        self._conn.executemany(
            """INSERT INTO products_snapshot
               (ts, sku, merchant_sku, bid, avg_cpc, score, cost, cost_today,
                clicks, carts, product_state, price, campaign_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(ts, p.sku, p.merchant_sku, p.bid, p.avg_cpc, p.score, p.cost,
              p.cost_today, p.clicks, p.carts, p.product_state, p.price,
              campaign_id) for p in products],
        )
        self._conn.commit()
```

Добавить методы (рядом с `get_latest_snapshot`):

```python
    def get_campaign_skus(self, campaign_id: str) -> list[dict]:
        """Уникальные SKU кампании со ставкой из свежего снапшота (для UI-списка)."""
        rows = self._conn.execute(
            """SELECT sku, merchant_sku, bid, MAX(ts) AS ts
               FROM products_snapshot WHERE campaign_id=?
               GROUP BY sku ORDER BY sku""",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_snapshot_ts(self) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(ts) AS ts FROM products_snapshot").fetchone()
        return row["ts"] if row and row["ts"] is not None else None
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `.venv/bin/python test_store.py`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/store.py test_store.py
git commit -m "feat(store): campaign_id в products_snapshot + get_campaign_skus/latest_ts"
```

---

### Task 3: `core/config_resolver.py` — сборка эффективного конфига

**Files:**
- Create: `core/config_resolver.py`
- Test: `test_config_resolver.py` (new)

**Interfaces:**
- Consumes: `RulesConfig` из `core.rules`.
- Produces:
  - `OVERRIDABLE_FIELDS: list[str]` — 11 полей (см. Global Constraints).
  - `resolve_config(global_cfg: RulesConfig, campaign_overrides: dict[str, str], sku_overrides: dict[str, str]) -> RulesConfig` — чистая функция: копия global → накладывает overrides кампании → SKU (только `OVERRIDABLE_FIELDS`), приводит типы. `dry_run`/`campaign_ids` всегда из global.

- [ ] **Step 1: Написать падающий тест** — создать `test_config_resolver.py`:

```python
"""test_config_resolver.py — сборка эффективного конфига (наследование)."""
from core.rules import RulesConfig
from core.config_resolver import resolve_config, OVERRIDABLE_FIELDS


def test_no_overrides_equals_global():
    g = RulesConfig()
    assert resolve_config(g, {}, {}) == g
    print("✓ resolver: без overrides = глобал")


def test_campaign_overrides_global():
    g = RulesConfig(bid_ceiling=50, min_bid=1)
    r = resolve_config(g, {"bid_ceiling": "80"}, {})
    assert r.bid_ceiling == 80.0 and r.min_bid == 1
    print("✓ resolver: override кампании перекрывает глобал")


def test_sku_overrides_campaign_and_global():
    g = RulesConfig(bid_ceiling=50)
    r = resolve_config(g, {"bid_ceiling": "80"}, {"bid_ceiling": "120"})
    assert r.bid_ceiling == 120.0  # SKU важнее кампании
    print("✓ resolver: override SKU перекрывает кампанию и глобал")


def test_int_fields_coerced():
    g = RulesConfig()
    r = resolve_config(g, {"max_changes_per_day": "7"},
                       {"min_clicks_for_no_cart_cut": "55"})
    assert r.max_changes_per_day == 7 and isinstance(r.max_changes_per_day, int)
    assert r.min_clicks_for_no_cart_cut == 55 and isinstance(r.min_clicks_for_no_cart_cut, int)
    print("✓ resolver: int-поля приводятся к int")


def test_global_only_fields_ignored():
    g = RulesConfig(dry_run=True, campaign_ids=["X"])
    # даже если кто-то подсунул эти поля в overrides — глобал не меняется
    r = resolve_config(g, {"dry_run": "false", "campaign_ids": "Y"}, {})
    assert r.dry_run is True and r.campaign_ids == ["X"]
    print("✓ resolver: dry_run/campaign_ids не переопределяются")


def test_overridable_fields_list():
    assert "dry_run" not in OVERRIDABLE_FIELDS
    assert "campaign_ids" not in OVERRIDABLE_FIELDS
    assert "bid_ceiling" in OVERRIDABLE_FIELDS and len(OVERRIDABLE_FIELDS) == 11
    print("✓ resolver: список переопределяемых полей корректен")


if __name__ == "__main__":
    test_no_overrides_equals_global()
    test_campaign_overrides_global()
    test_sku_overrides_campaign_and_global()
    test_int_fields_coerced()
    test_global_only_fields_ignored()
    test_overridable_fields_list()
    print("-" * 60)
    print("✓ Все проверки config_resolver прошли")
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_config_resolver.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'core.config_resolver'`).

- [ ] **Step 3: Реализовать** — создать `core/config_resolver.py`:

```python
"""config_resolver.py — эффективный RulesConfig для товара: глобал → кампания → SKU.

Чистая функция без ввода-вывода: overrides приходят готовыми словарями
{field: raw_value} (их читает стор). dry_run/campaign_ids не переопределяются —
это глобальные рубильники, не тюнинг.
"""
from __future__ import annotations

import dataclasses

from core.rules import RulesConfig

# 11 переопределяемых числовых порогов (без dry_run/campaign_ids).
OVERRIDABLE_FIELDS = [
    "target_tacos_low", "target_tacos_high",
    "daily_sku_cost_limit", "sku_budget_fraction",
    "min_clicks_for_no_cart_cut", "cpc_spike_pct",
    "max_bid_step", "max_changes_per_day",
    "bid_ceiling", "min_bid", "min_score_for_raise",
]
_INT_FIELDS = {"min_clicks_for_no_cart_cut", "max_changes_per_day"}


def _coerce(field: str, raw):
    return int(float(raw)) if field in _INT_FIELDS else float(raw)


def resolve_config(global_cfg: RulesConfig,
                   campaign_overrides: dict,
                   sku_overrides: dict) -> RulesConfig:
    """Копия global, поверх которой лежат отличия кампании, затем SKU."""
    values = dataclasses.asdict(global_cfg)
    for ov in (campaign_overrides or {}, sku_overrides or {}):
        for field, raw in ov.items():
            if field in OVERRIDABLE_FIELDS:
                values[field] = _coerce(field, raw)
    return RulesConfig(**values)
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `.venv/bin/python test_config_resolver.py`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/config_resolver.py test_config_resolver.py
git commit -m "feat(core): config_resolver — эффективный конфиг глобал→кампания→товар"
```

---

### Task 4: Движок — `evaluate_fast/slow` принимают cfg-резолвер

**Files:**
- Modify: `core/rules.py` (`evaluate_fast`, `evaluate_slow`, новый хелпер `_cfg_for`)
- Test: `test_rules.py`

**Interfaces:**
- Consumes: `RulesConfig`, объекты SKU с атрибутом `.sku`.
- Produces: `evaluate_fast(skus, cfg=None, state=None, daily_budget=0.0)` и `evaluate_slow(skus, cfg=None, state=None)`, где `cfg` теперь `RulesConfig | Callable[[sku_obj], RulesConfig] | None`. Если callable — вызывается на каждый SKU для его эффективного конфига. Обратная совместимость: `RulesConfig`/`None` работают как раньше.

- [ ] **Step 1: Написать падающий тест** — добавить в `test_rules.py`:

```python
def test_evaluate_fast_accepts_cfg_callable():
    # два SKU: у одного потолок другой — проверяем, что резолвер зовётся на каждый
    from core.rules import RulesConfig, evaluate_fast
    # берём фабрику тестового SKU из уже существующих хелперов файла:
    a = _mk_sku(sku="A", carts=0, clicks=100, product_state="Active", bid=10)
    b = _mk_sku(sku="B", carts=0, clicks=100, product_state="Active", bid=10)
    cfgs = {"A": RulesConfig(max_bid_step=2), "B": RulesConfig(max_bid_step=5)}
    out = {d.sku: d for d in evaluate_fast([a, b], cfg=lambda s: cfgs[s.sku])}
    # оба режут ставку (0 корзин при 100 кликах), но на свой шаг
    assert out["A"].new_bid == 8   # 10 - 2
    assert out["B"].new_bid == 5   # 10 - 5
    print("✓ rules: evaluate_fast принимает cfg-резолвер (per-SKU)")
```

**Важно:** в `test_rules.py` уже есть фабрика тестового SKU — найди её (напр. `_mk_sku`/`_sku`/подобное) и используй её имя и сигнатуру вместо `_mk_sku` выше. Если поля называются иначе (`clicks`, `carts`, `bid`, `product_state`, `avg_cpc`), передай их так, как ждёт существующая фабрика. Добавь вызов теста в `if __name__ == "__main__"`.

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_rules.py`
Expected: FAIL (`cfg` как lambda ломается — сейчас `evaluate_fast` делает `cfg = cfg or RulesConfig()` и передаёт в `_eval_fast_one`, где обращается к `cfg.max_bid_step` → `AttributeError` на function).

- [ ] **Step 3: Реализовать** — в `core/rules.py` добавить хелпер рядом с `_state_for`:

```python
def _cfg_for(cfg, s) -> "RulesConfig":
    """cfg может быть RulesConfig, None или резолвер Callable[[sku], RulesConfig]."""
    if callable(cfg):
        return cfg(s)
    return cfg or RulesConfig()
```

Заменить тела циклов в `evaluate_fast` и `evaluate_slow`:

```python
def evaluate_fast(skus, cfg=None, state=None, daily_budget=0.0):
    out = []
    for s in skus:
        out.append(_eval_fast_one(s, _cfg_for(cfg, s),
                                  _state_for(state, s.sku), daily_budget))
    return out
```

```python
def evaluate_slow(skus, cfg=None, state=None):
    out = []
    for s in skus:
        out.append(_eval_slow_one(s, _cfg_for(cfg, s), _state_for(state, s.sku)))
    return out
```

(Удали старую строку `cfg = cfg or RulesConfig()` из обеих функций — её роль теперь у `_cfg_for`.)

- [ ] **Step 4: Прогнать — убедиться, что проходит весь файл**

Run: `.venv/bin/python test_rules.py`
Expected: PASS (новый тест + все прежние — обратная совместимость).

- [ ] **Step 5: Коммит**

```bash
git add core/rules.py test_rules.py
git commit -m "feat(rules): evaluate_fast/slow принимают cfg-резолвер (per-SKU конфиг)"
```

---

### Task 5: Воркер — per-SKU резолв конфига + campaign_id в снапшоте

**Files:**
- Modify: `worker.py` (`run_tick`)
- Test: `test_worker.py`

**Interfaces:**
- Consumes: `Store.get_overrides`, `resolve_config`, `evaluate_fast/slow` (cfg-резолвер), `save_products_snapshot(..., campaign_id=...)`.
- Produces: поведение `run_tick` — решения считаются по эффективному конфигу каждого SKU; снапшот пишется с `campaign_id`.

- [ ] **Step 1: Написать падающий тест** — добавить в `test_worker.py` (используй существующие фейки стора/маркетинга из файла; если их нет в нужной форме — расширь имеющиеся, не создавай дубль-инфраструктуру):

```python
def test_run_tick_uses_per_sku_overrides():
    """Два SKU в одной кампании: у SKU-B override max_bid_step=5 → режет глубже."""
    # Аранжировка: ctx с реальным Store (tmp БД), фейковым marketing, cfg по умолчанию.
    # SKU A и B оба: 0 корзин при 100 кликах, ставка 10, Active.
    # override: sku B, max_bid_step=5.
    import tempfile, os
    from core.store import Store
    from core.rules import RulesConfig
    st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    st.set_override("sku", "B", "max_bid_step", "5", "test", 1)
    ctx = _mk_ctx(store=st, cfg=RulesConfig(max_bid_step=2), campaign_id="C1",
                  products=[_mk_product("A"), _mk_product("B")])
    run_tick(ctx, "fast", "C1", daily_budget=0.0)
    decs = {d["sku"]: d for d in st.get_decisions_for_day(_today())}
    assert decs["A"]["new_bid"] == 8   # глобальный шаг 2
    assert decs["B"]["new_bid"] == 5   # override шаг 5
    # снапшот записан с campaign_id
    assert {r["sku"] for r in st.get_campaign_skus("C1")} == {"A", "B"}
    st.close()
    print("✓ worker: run_tick резолвит конфиг на SKU + пишет campaign_id")
```

**Важно:** `_mk_ctx`, `_mk_product`, `_today` — подгони под уже существующие хелперы `test_worker.py` (там наверняка есть сборка `WorkerContext` с фейковым marketing, отдающим `get_campaign_products`, и `now_fn`). Твоя задача — чтобы фейковый marketing вернул два продукта A и B с `carts=0, clicks=100, bid=10, product_state="Active"`, а cfg был `RulesConfig(max_bid_step=2)`. Если revenue-кэш пуст — TACoS не важен для быстрого контура, тест валиден.

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_worker.py`
Expected: FAIL (оба SKU режут на шаг 2 → `decs["B"]["new_bid"] == 8`, ассерт `== 5` падает; либо `get_campaign_skus` пуст, т.к. снапшот без campaign_id).

- [ ] **Step 3: Реализовать** — в `worker.py`, `run_tick`:

Заменить строку сохранения снапшота на передачу `campaign_id`:

```python
    ctx.store.save_products_snapshot(products, ts, campaign_id=campaign_id)
```

Перед блоком выбора контура собрать резолвер и передать его вместо `ctx.cfg`:

```python
    from core.config_resolver import resolve_config
    camp_ov = ctx.store.get_overrides("campaign", campaign_id)

    def cfg_for(s):
        return resolve_config(ctx.cfg, camp_ov,
                              ctx.store.get_overrides("sku", s.sku))

    if loop == "fast":
        decisions = evaluate_fast(reconciled, cfg_for, state, daily_budget)
    elif loop == "slow":
        decisions = evaluate_slow(reconciled, cfg_for, state)
    else:
        raise ValueError(f"неизвестный контур: {loop}")
```

(Импорт `resolve_config` можно поднять в шапку модуля рядом с прочими `from core...` — по вкусу; локальный импорт тоже приемлем, как уже принято в проекте.)

- [ ] **Step 4: Прогнать — убедиться, что проходит весь файл**

Run: `.venv/bin/python test_worker.py`
Expected: PASS (новый тест + прежние).

- [ ] **Step 5: Коммит**

```bash
git add worker.py test_worker.py
git commit -m "feat(worker): per-SKU эффективный конфиг в run_tick + campaign_id в снапшоте"
```

---

### Task 6: WebUI — страница настроек кампании (наследование)

**Files:**
- Modify: `webui/app.py` (роуты `GET/POST /settings/campaign/{campaign_id}`, хелпер эффективных значений)
- Create: `webui/templates/campaign_settings.html`
- Modify: `webui/templates/dashboard.html` (ссылка «Настройки кампании» в таблице бюджетов)
- Test: `test_webui.py`

**Interfaces:**
- Consumes: `load_rules_config` (глобал как `RulesConfig`), `resolve_config`, `OVERRIDABLE_FIELDS`, `Store.get_overrides/set_override/delete_override`, `validate_settings`.
- Produces: страница с 11 полями, каждое предзаполнено эффективным значением (наследованным от глобала) + бейдж «унаследовано»/«своё». POST на поле: пустой флаг «наследовать» → override, флаг → удаление override.

- [ ] **Step 1: Написать падающий тест** — добавить в `test_webui.py` (используй существующий тест-клиент/фикстуру приложения из файла — там уже поднимается `create_app()` с тестовым логином):

```python
def test_campaign_settings_get_and_save():
    client, db_path = _logged_in_client()  # переиспользуй хелпер файла
    # GET показывает форму с эффективными (глобальными) значениями
    r = client.get("/settings/campaign/C1")
    assert r.status_code == 200
    assert "bid_ceiling" in r.text
    # POST: задаём override bid_ceiling=80 (флаг наследования снят)
    r = client.post("/settings/campaign/C1",
                    data={"bid_ceiling": "80"}, follow_redirects=False)
    assert r.status_code in (303, 302)
    from core.store import Store
    st = Store(db_path)
    assert st.get_overrides("campaign", "C1").get("bid_ceiling") == "80"
    # POST со снятым override (inherit) удаляет строку
    r = client.post("/settings/campaign/C1",
                    data={"bid_ceiling": "80", "bid_ceiling__inherit": "on"},
                    follow_redirects=False)
    assert "bid_ceiling" not in st.get_overrides("campaign", "C1")
    st.close()
    print("✓ webui: настройки кампании — GET форма + POST override/сброс")
```

**Важно:** имена хелперов (`_logged_in_client`, доступ к `db_path`) подгони под то, что уже есть в `test_webui.py`. Если тестовый клиент не отдаёт путь БД — прочитай override через тот же `Store(os.environ["DB_PATH"])`/путь, который использует тест-приложение.

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_webui.py`
Expected: FAIL (404 на `/settings/campaign/C1`).

- [ ] **Step 3: Реализовать**

В `webui/app.py` (шапка) добавить импорты:

```python
from core.rules import load_rules_config
from core.config_resolver import resolve_config, OVERRIDABLE_FIELDS
```

Внутри `create_app`, добавить хелпер и роуты (рядом с существующими settings-роутами):

```python
    def _effective_and_flags(campaign_id: str, sku: str | None):
        """Возвращает (values: dict[field->эффективное значение],
        owned: set[field переопределённых на ЭТОМ уровне])."""
        store = Store(db_path)
        try:
            g = load_rules_config(rules_path)
            camp_ov = store.get_overrides("campaign", campaign_id)
            if sku is None:
                eff = resolve_config(g, camp_ov, {})
                owned = set(camp_ov)
            else:
                sku_ov = store.get_overrides("sku", sku)
                eff = resolve_config(g, camp_ov, sku_ov)
                owned = set(sku_ov)
        finally:
            store.close()
        values = {f: getattr(eff, f) for f in OVERRIDABLE_FIELDS}
        return values, owned

    @app.get("/settings/campaign/{campaign_id}", response_class=HTMLResponse)
    def campaign_settings_form(request: Request, campaign_id: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        values, owned = _effective_and_flags(campaign_id, None)
        store = Store(db_path)
        try:
            skus = store.get_campaign_skus(campaign_id)
        finally:
            store.close()
        return templates.TemplateResponse(request, "campaign_settings.html", {
            "user": user(request), "campaign_id": campaign_id,
            "fields": OVERRIDABLE_FIELDS, "values": values, "owned": owned,
            "skus": skus, "errors": []})

    @app.post("/settings/campaign/{campaign_id}")
    async def campaign_settings_save(request: Request, campaign_id: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        _save_overrides(request, "campaign", campaign_id, form,
                        base_sku=None, base_campaign=campaign_id)
        return RedirectResponse(f"/settings/campaign/{campaign_id}", status_code=303)
```

Общий хелпер сохранения (валидирует эффективный конфиг перед записью):

```python
    def _save_overrides(request, scope, scope_id, form, base_sku, base_campaign):
        store = Store(db_path)
        try:
            g = load_rules_config(rules_path)
            camp_ov = store.get_overrides("campaign", base_campaign)
            # Собираем предполагаемые overrides этого уровня из формы:
            proposed = {}
            for f in OVERRIDABLE_FIELDS:
                if form.get(f"{f}__inherit") == "on":
                    continue  # наследовать → нет override
                raw = (form.get(f) or "").strip()
                if raw != "":
                    proposed[f] = raw
            # Эффективный конфиг для валидации (кросс-поля: ceiling>=min_bid и т.п.):
            if scope == "campaign":
                eff = resolve_config(g, proposed, {})
            else:
                eff = resolve_config(g, camp_ov, proposed)
            errs = validate_settings({f: getattr(eff, f) for f in SETTINGS_FIELDS})
            if errs:
                # Перерисуем форму с ошибками (значения — из формы/эффективные).
                values = {f: getattr(eff, f) for f in OVERRIDABLE_FIELDS}
                tpl = "campaign_settings.html" if scope == "campaign" else "sku_settings.html"
                extra = {"campaign_id": base_campaign, "owned": set(proposed)}
                if scope == "sku":
                    extra["sku"] = scope_id
                else:
                    extra["skus"] = store.get_campaign_skus(base_campaign)
                return templates.TemplateResponse(request, tpl, {
                    "user": user(request), "fields": OVERRIDABLE_FIELDS,
                    "values": values, "errors": errs, **extra}, status_code=200)
            # Применяем: set для proposed, delete для остальных.
            ts = int(time.time())
            for f in OVERRIDABLE_FIELDS:
                if f in proposed:
                    old = store.get_overrides(scope, scope_id).get(f)
                    store.set_override(scope, scope_id, f, proposed[f], user(request), ts)
                    if str(old) != str(proposed[f]):
                        store.log_settings_change(
                            user(request), f"{scope}:{scope_id}:{f}", old, proposed[f], ts)
                else:
                    if f in store.get_overrides(scope, scope_id):
                        store.delete_override(scope, scope_id, f)
                        store.log_settings_change(
                            user(request), f"{scope}:{scope_id}:{f}", "override", "inherit", ts)
        finally:
            store.close()
        return None
```

Обнови `campaign_settings_save`, чтобы вернуть результат перерисовки, если хелпер его дал:

```python
    @app.post("/settings/campaign/{campaign_id}")
    async def campaign_settings_save(request: Request, campaign_id: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        resp = _save_overrides(request, "campaign", campaign_id, form,
                               base_sku=None, base_campaign=campaign_id)
        return resp or RedirectResponse(
            f"/settings/campaign/{campaign_id}", status_code=303)
```

Создать `webui/templates/campaign_settings.html` (по образцу `settings.html` — тот же layout/шапка; загляни в существующий шаблон и переиспользуй блоки/классы):

```html
{% extends "base.html" %}  {# если в settings.html используется extends — повтори его; иначе скопируй общий каркас страницы #}
{% block content %}
<h1>Настройки кампании {{ campaign_id }}</h1>
<p>Пустое поле с галкой «наследовать» = берётся сверху (глобал). Значение = своё.</p>
{% if errors %}<ul class="errors">{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>{% endif %}
<form method="post" action="/settings/campaign/{{ campaign_id }}">
  {% for f in fields %}
  <div class="field">
    <label>{{ f }}
      <span class="badge">{{ "своё" if f in owned else "унаследовано" }}</span>
    </label>
    <input name="{{ f }}" value="{{ values[f] }}">
    <label><input type="checkbox" name="{{ f }}__inherit" {{ "checked" if f not in owned else "" }}> наследовать</label>
  </div>
  {% endfor %}
  <button type="submit">Сохранить</button>
</form>

<h2>Товары кампании</h2>
<table>
  <tr><th>SKU</th><th>merchant_sku</th><th>ставка</th><th></th></tr>
  {% for s in skus %}
  <tr>
    <td>{{ s.sku }}</td><td>{{ s.merchant_sku }}</td><td>{{ s.bid }}</td>
    <td><a href="/settings/sku/{{ campaign_id }}/{{ s.sku }}">Настройки товара</a></td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

(Если `settings.html` не использует `base.html`/`extends`, скопируй его внешний каркас 1:1 и вставь контент — важно, чтобы страница рендерилась в общем стиле панели.)

В `dashboard.html`, в таблице «Бюджеты кампаний», добавить в каждую строку ссылку:

```html
<a href="/settings/campaign/{{ cid }}">Настройки кампании</a>
```

(Имя переменной id кампании возьми из текущего цикла шаблона — там бюджеты приходят как `budgets` = `{id: {name, daily_budget}}`; итерируй `budgets.items()`.)

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `.venv/bin/python test_webui.py`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add webui/app.py webui/templates/campaign_settings.html webui/templates/dashboard.html test_webui.py
git commit -m "feat(webui): страница настроек кампании (наследование + список товаров)"
```

---

### Task 7: WebUI — страница настроек товара (SKU)

**Files:**
- Modify: `webui/app.py` (роуты `GET/POST /settings/sku/{campaign_id}/{sku}`)
- Create: `webui/templates/sku_settings.html`
- Test: `test_webui.py`

**Interfaces:**
- Consumes: те же хелперы, что Task 6 (`_effective_and_flags`, `_save_overrides`).
- Produces: страница SKU с 11 полями, предзаполнёнными эффективным значением (наследуется от кампании, потом глобала), бейджами «унаследовано»/«своё» и сбросом.

- [ ] **Step 1: Написать падающий тест** — добавить в `test_webui.py`:

```python
def test_sku_settings_inherits_campaign_then_saves():
    client, db_path = _logged_in_client()
    from core.store import Store
    st = Store(db_path)
    st.set_override("campaign", "C1", "bid_ceiling", "80", "admin", 1)  # кампания даёт 80
    st.close()
    r = client.get("/settings/sku/C1/SKU-1")
    assert r.status_code == 200
    assert "80" in r.text  # SKU наследует потолок кампании
    # переопределяем на уровне SKU
    r = client.post("/settings/sku/C1/SKU-1",
                    data={"bid_ceiling": "120"}, follow_redirects=False)
    assert r.status_code in (303, 302)
    st = Store(db_path)
    assert st.get_overrides("sku", "SKU-1").get("bid_ceiling") == "120"
    st.close()
    print("✓ webui: настройки товара — наследует кампанию, пишет свой override")
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_webui.py`
Expected: FAIL (404 на `/settings/sku/C1/SKU-1`).

- [ ] **Step 3: Реализовать** — в `webui/app.py` добавить роуты:

```python
    @app.get("/settings/sku/{campaign_id}/{sku}", response_class=HTMLResponse)
    def sku_settings_form(request: Request, campaign_id: str, sku: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        values, owned = _effective_and_flags(campaign_id, sku)
        return templates.TemplateResponse(request, "sku_settings.html", {
            "user": user(request), "campaign_id": campaign_id, "sku": sku,
            "fields": OVERRIDABLE_FIELDS, "values": values, "owned": owned,
            "errors": []})

    @app.post("/settings/sku/{campaign_id}/{sku}")
    async def sku_settings_save(request: Request, campaign_id: str, sku: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        resp = _save_overrides(request, "sku", sku, form,
                               base_sku=sku, base_campaign=campaign_id)
        return resp or RedirectResponse(
            f"/settings/sku/{campaign_id}/{sku}", status_code=303)
```

Создать `webui/templates/sku_settings.html` (как `campaign_settings.html`, но заголовок про товар, action на sku-URL, без таблицы товаров):

```html
{% extends "base.html" %}
{% block content %}
<h1>Настройки товара {{ sku }} (кампания {{ campaign_id }})</h1>
<p><a href="/settings/campaign/{{ campaign_id }}">← к кампании</a></p>
<p>Пустое поле с галкой «наследовать» = берётся от кампании/глобала. Значение = своё.</p>
{% if errors %}<ul class="errors">{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>{% endif %}
<form method="post" action="/settings/sku/{{ campaign_id }}/{{ sku }}">
  {% for f in fields %}
  <div class="field">
    <label>{{ f }}
      <span class="badge">{{ "своё" if f in owned else "унаследовано" }}</span>
    </label>
    <input name="{{ f }}" value="{{ values[f] }}">
    <label><input type="checkbox" name="{{ f }}__inherit" {{ "checked" if f not in owned else "" }}> наследовать</label>
  </div>
  {% endfor %}
  <button type="submit">Сохранить</button>
</form>
{% endblock %}
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `.venv/bin/python test_webui.py`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add webui/app.py webui/templates/sku_settings.html test_webui.py
git commit -m "feat(webui): страница настроек товара (наследует кампанию/глобал)"
```

---

## PART 2 — Свежесть данных

### Task 8: Дашборд — метка «данные на HH:MM»

**Files:**
- Modify: `webui/app.py` (`dashboard` — прокинуть `last_snapshot_ts`)
- Modify: `webui/templates/dashboard.html` (показать метку)
- Test: `test_webui.py`

**Interfaces:**
- Consumes: `Store.get_latest_snapshot_ts`.
- Produces: в контексте `dashboard.html` появляется `last_snapshot_ts` (int|None) и его форматированный вид.

- [ ] **Step 1: Написать падающий тест** — добавить в `test_webui.py`:

```python
def test_dashboard_shows_freshness():
    client, db_path = _logged_in_client()
    from core.store import Store
    import time as _t
    st = Store(db_path)
    st.save_products_snapshot([], ts=int(_t.time()), campaign_id="C1")  # пустой ок
    # добавим один продукт, чтобы MAX(ts) был определён
    st.close()
    r = client.get("/")
    assert r.status_code == 200
    assert "данные на" in r.text
    print("✓ webui: дашборд показывает метку свежести")
```

Если `save_products_snapshot([])` не создаёт строк (пустой список → нет MAX(ts)), запиши один продукт через хелпер `_mk_product` из `test_store.py`/локальный, чтобы `get_latest_snapshot_ts()` вернул значение.

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_webui.py`
Expected: FAIL (нет строки «данные на» в дашборде).

- [ ] **Step 3: Реализовать** — в `dashboard` (app.py) внутри `try/finally` со `store` добавить:

```python
            last_ts = store.get_latest_snapshot_ts()
```

и прокинуть в контекст шаблона: `"last_snapshot_ts": last_ts`.

В `dashboard.html` рядом с заголовком добавить:

```html
{% if last_snapshot_ts %}
  <p class="freshness">данные на {{ last_snapshot_ts | dt }}
     <form method="post" action="/refresh" style="display:inline">
       <button type="submit">↻ Обновить сейчас</button>
     </form>
  </p>
{% else %}
  <p class="freshness">данные ещё не собраны
     <form method="post" action="/refresh" style="display:inline">
       <button type="submit">↻ Обновить сейчас</button>
     </form>
  </p>
{% endif %}
```

(Фильтр `dt` уже зарегистрирован в app.py — форматирует unix-ts. Кнопка `/refresh` реализуется в Task 9; сейчас она просто присутствует в разметке.)

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `.venv/bin/python test_webui.py`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add webui/app.py webui/templates/dashboard.html test_webui.py
git commit -m "feat(webui): метка свежести данных на дашборде"
```

---

### Task 9: Кнопка «Обновить сейчас» — безопасный разовый пулл

**Files:**
- Modify: `webui/app.py` (роут `POST /refresh` + вынести живой пулл в best-effort хелпер)
- Test: `test_webui.py`

**Interfaces:**
- Consumes: `SessionManager`, `MarketingClient` (как в `_get_campaign_budgets`), `Store.save_products_snapshot`.
- Produces: `POST /refresh` — best-effort: тянет активные кампании и их товары, пишет снапшоты с `campaign_id`, **не** трогает ставки; при любой ошибке сессии/WAF молча редиректит на `/`. Требует логина.

- [ ] **Step 1: Написать падающий тест** — добавить в `test_webui.py`:

```python
def test_refresh_endpoint_best_effort():
    client, _ = _logged_in_client()
    # Без кредов кабинета живой пулл невозможен → должен НЕ падать, а редиректить на /
    r = client.post("/refresh", follow_redirects=False)
    assert r.status_code in (303, 302)
    assert r.headers["location"] == "/"
    # неавторизованный → на логин
    r2 = client.post("/refresh", follow_redirects=False,
                     headers={"cookie": "session=broken"})
    assert r2.status_code in (303, 302)
    print("✓ webui: /refresh best-effort (не падает без сессии кабинета)")
```

(Если тестовый клиент разлогинивается иначе — используй подход из существующих тестов авторизации файла.)

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `.venv/bin/python test_webui.py`
Expected: FAIL (404 на `/refresh`).

- [ ] **Step 3: Реализовать** — в `webui/app.py` добавить best-effort хелпер живого пулла (рядом с `_get_campaign_budgets`) и роут:

```python
def _live_refresh_snapshot(db_path: str) -> bool:
    """Разовый read-only пулл кабинета в снапшоты. Best-effort: любая проблема
    (нет кредов/сессия/WAF) → False, без исключения. Ставки НЕ трогаются."""
    try:
        storage_path = os.environ.get("STORAGE_STATE", "storage_state.json")
        merchant_id = os.environ.get("KASPI_MARKETING_MERCHANT_ID", "")
        login = os.environ.get("KASPI_MARKETING_LOGIN", "")
        password = os.environ.get("KASPI_MARKETING_PASSWORD", "")
        if not (storage_path and os.path.exists(storage_path)
                and merchant_id and login and password):
            return False
        from connectors.session_manager import SessionManager
        from connectors.marketing_client import MarketingClient
        session = SessionManager(merchant_login=login, merchant_password=password,
                                 storage_path=storage_path)
        cookies = session.get_cookies()
        marketing = MarketingClient(
            merchant_id, cookies=cookies, dry_run=True,
            on_auth_error=lambda: session.get_cookies(force_refresh=True))
        try:
            today = datetime.now(ALMATY).date().isoformat()
            campaigns = marketing.list_active_campaigns(today, today)
            store = Store(db_path)
            try:
                ts = int(time.time())
                for c in campaigns:
                    products = marketing.get_campaign_products(c.id, today, today)
                    store.save_products_snapshot(products, ts, campaign_id=c.id)
            finally:
                store.close()
        finally:
            marketing.close()
        return True
    except Exception as e:
        log.warning("Живой пулл /refresh не удался (показываю прежние данные): %s", e)
        return False
```

Роут (внутри `create_app`):

```python
    @app.post("/refresh")
    def refresh(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        _live_refresh_snapshot(db_path)
        return RedirectResponse("/", status_code=303)
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `.venv/bin/python test_webui.py`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add webui/app.py test_webui.py
git commit -m "feat(webui): /refresh — безопасный разовый пулл кабинета в снапшоты"
```

---

### Task 10: Ускорить фоновый fast-цикл (20 → 5 мин)

**Files:**
- Modify: `worker.py` (`main()`, интервал fast-джобы)

**Interfaces:** нет (боевая обвязка, `# pragma: no cover`).

- [ ] **Step 1: Изменить интервал** — в `worker.py`, `main()`, у джобы `id="fast"`:

```python
    sched.add_job(lambda: run_cycle(build_ctx(), "fast"), "interval", minutes=5,
                  id="fast")
```

Обнови и текст лога запуска («fast/5м» вместо «fast/20м»).

- [ ] **Step 2: Проверка синтаксиса/импорта** (main под сеть не гоняем):

Run: `.venv/bin/python -c "import worker; print('ok')"`
Expected: `ok` (модуль импортируется без ошибок).

- [ ] **Step 3: Коммит**

```bash
git add worker.py
git commit -m "chore(worker): fast-цикл 20→5 мин (свежее данные дашборда)"
```

**Ручная проверка (на VPS, вне TDD):** после выката — `journalctl` показывает fast-цикл каждые 5 мин; следить, что сессия кабинета не перегружается (нет всплеска WAF-блоков в логах SessionManager). Если 5 мин агрессивно — вернуть 10.

---

## Финальная проверка (после всех задач)

- [ ] Прогнать весь набор тестов:

```bash
for t in test_store test_config_resolver test_rules test_worker test_webui test_settings_io; do
  echo "== $t =="; .venv/bin/python $t.py || break
done
```

Expected: все печатают `✓ ... прошли`, ни один не падает.

- [ ] Обновить память проекта, если появились новые способы запуска/эндпоинты (файл `live-kaspi-integration.md` — про `/refresh` и per-SKU конфиг).

---

## Self-Review (проверено при написании плана)

- **Покрытие спеки:** overrides-хранилище (Task 1), campaign_id/списки (Task 2), резолвер (Task 3), движок-резолвер (Task 4), воркер per-SKU (Task 5), UI кампании (Task 6), UI товара (Task 7), метка свежести (Task 8), кнопка обновления (Task 9), ускорение цикла (Task 10). Global-only `dry_run`/`campaign_ids` — не в `OVERRIDABLE_FIELDS` (Task 3). ✔
- **Плейсхолдеров нет:** весь код приведён; места, зависящие от существующих хелперов тестов (`_mk_sku`, `_mk_ctx`, `_logged_in_client`), явно помечены «подгони под файл» с описанием ожидаемого поведения — это не TODO в коде, а указание сверить имя фикстуры. ✔
- **Согласованность типов:** `get_overrides`→`dict[str,str]` всюду; `resolve_config(global, camp_ov, sku_ov)` — единая сигнатура в Task 3/5/6; `save_products_snapshot(..., campaign_id=...)` — Task 2/5/9; `evaluate_fast/slow(skus, cfg_callable, ...)` — Task 4/5. ✔
