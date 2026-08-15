# Budget-From-Cabinet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Тормозной контур считает per-SKU дневной лимит расхода от бюджета кампании (`dailyBudget` из кабинета), а не от хардкод-числа; фолбэк на абсолютный лимит, если бюджет недоступен.

**Architecture:** `Campaign` несёт `daily_budget` (из `list_active_campaigns`). `run_cycle` пробрасывает его в `run_tick`, тот — в `evaluate_fast`, где per-SKU лимит = `daily_budget × sku_budget_fraction` (иначе фолбэк на `daily_sku_cost_limit`). Медленный (TACoS) контур не меняется.

**Tech Stack:** Python 3.9+/3.12 (`.venv`), httpx, SQLite, APScheduler (только в `main()`). Тесты — plain-скрипты с `assert`, запуск `.venv/bin/python test_X.py` (НЕ pytest). Зависимости инъектируются.

## Global Constraints

- Тесты — runnable-скрипты с `assert` + `print("✓ …")`, зарегистрированы в `if __name__ == "__main__":`. Запуск `.venv/bin/python test_<mod>.py`. НЕ pytest.
- Русские докстринги/сообщения; сеть/браузер/APScheduler только в `worker.main()`.
- Формула: `limit = daily_budget × sku_budget_fraction` при `daily_budget > 0`, иначе `daily_sku_cost_limit`. Дефолт `sku_budget_fraction = 0.5`.
- Все новые параметры ОПЦИОНАЛЬНЫ с дефолтами (`daily_budget=0.0`, `sku_budget_fraction=0.5`) — существующие вызовы/тесты не ломаются. `daily_sku_cost_limit` НЕ удаляем (фолбэк).
- `load_rules_config` — рефлексия по `fields(RulesConfig)`, отдельной правки загрузчика НЕ требуется.
- Ветка работы: `budget-from-cabinet`. `dry_run` в `config/rules.yaml` остаётся `true`.
- Реальные данные (для смоука): Бритвы `dailyBudget=10000`, Аэрогриль `dailyBudget=20000`.

---

### Task 1: `Campaign.daily_budget` из кабинета

**Files:**
- Modify: `connectors/marketing_client.py` (dataclass `Campaign`; метод `list_active_campaigns`)
- Test: `test_marketing.py` (расширить `SAMPLE_CAMPAIGNS`, добавить тест + вызов в `__main__`)

**Interfaces:**
- Produces: `Campaign(id: str, name: str, state: str, daily_budget: float = 0.0)`;
  `list_active_campaigns` заполняет `daily_budget` из `row["dailyBudget"]`.

- [ ] **Step 1: Падающий тест**

В `test_marketing.py` заменить `SAMPLE_CAMPAIGNS` на версию с `dailyBudget`:

```python
SAMPLE_CAMPAIGNS = {
    "data": [
        {"id": 2899523, "name": "Бритвы", "state": "Enabled", "dailyBudget": 10000.0},
        {"id": 3032419, "name": "Аэрогриль 08.08.2026", "state": "Enabled", "dailyBudget": 20000.0},
        {"id": 2711494, "name": "Аэрогриль", "state": "Paused", "dailyBudget": 5000.0},
        {"id": 2268077, "name": "5 ноября", "state": "Archived", "dailyBudget": 0.0},
    ],
}
```

Добавить тест (после `test_list_active_campaigns_filters_enabled`):

```python
def test_list_active_campaigns_parses_daily_budget():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_CAMPAIGNS)

    with make_client(handler) as mc:
        campaigns = mc.list_active_campaigns("2026-08-10", "2026-08-11")

    budgets = {c.id: c.daily_budget for c in campaigns}
    assert budgets == {"2899523": 10000.0, "3032419": 20000.0}, budgets
    print("✓ marketing: list_active_campaigns парсит dailyBudget")
```

Добавить вызов в `__main__` (перед `print("-" * 60)`):

```python
    test_list_active_campaigns_parses_daily_budget()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_marketing.py`
Expected: FAIL — `AttributeError: 'Campaign' object has no attribute 'daily_budget'`.

- [ ] **Step 3: Реализация**

В `connectors/marketing_client.py`, dataclass `Campaign`, добавить поле:

```python
@dataclass
class Campaign:
    """Рекламная кампания кабинета (шапка списка)."""
    id: str
    name: str
    state: str          # Enabled / Paused / Archived
    daily_budget: float = 0.0   # dailyBudget из кабинета (₸/сутки); 0 = недоступен
```

В `list_active_campaigns`, в теле цикла, добавить `daily_budget` в конструктор `Campaign`:

```python
            out.append(Campaign(
                id=str(row.get("id", "")),
                name=str(row.get("name", "")),
                state=state,
                daily_budget=float(row.get("dailyBudget", 0) or 0),
            ))
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_marketing.py`
Expected: PASS — все проверки marketing, включая новую.

- [ ] **Step 5: Коммит**

```bash
git add connectors/marketing_client.py test_marketing.py
git commit -m "feat(marketing): Campaign.daily_budget из кабинета (dailyBudget)"
```

---

### Task 2: per-SKU лимит от бюджета в тормозном контуре

**Files:**
- Modify: `core/rules.py` (`RulesConfig`; `evaluate_fast`; `_eval_fast_one`)
- Modify: `config/rules.yaml` (добавить `sku_budget_fraction`, переписать комментарий у `daily_sku_cost_limit`)
- Test: `test_rules.py` (два теста + вызовы в `__main__`)

**Interfaces:**
- Consumes: `SkuReconciled.cost_today`, `RulesConfig`.
- Produces:
  - `RulesConfig.sku_budget_fraction: float = 0.5`.
  - `evaluate_fast(skus, cfg=None, state=None, daily_budget: float = 0.0)`.
  - `_eval_fast_one(s, cfg, st, daily_budget: float = 0.0)` — спенд-кап считается от
    `daily_budget × cfg.sku_budget_fraction`, иначе фолбэк `cfg.daily_sku_cost_limit`.

- [ ] **Step 1: Падающие тесты**

В `test_rules.py` добавить (после `test_fast_spend_cap_pauses`):

```python
def test_fast_spend_cap_from_campaign_budget():
    # бюджет 10000, доля 0.5 → лимит 5000
    d = only(evaluate_fast([sr(cost_today=5100, bid=18)], CFG, daily_budget=10000))
    assert d.action == "pause", d.reason
    assert "бюджет" in d.reason, d.reason
    # 4900 < 5000 → НЕ пауза (хотя это > фолбэка 3000 — значит взят бюджетный лимит)
    d2 = only(evaluate_fast([sr(cost_today=4900, bid=18)], CFG, daily_budget=10000))
    assert d2.action != "pause", d2.reason
    print("✓ rules: спенд-кап считается от бюджета кампании (×0.5)")


def test_fast_spend_cap_fallback_without_budget():
    # daily_budget=0 → фолбэк на daily_sku_cost_limit (3000); 3500 ≥ 3000 → пауза
    d = only(evaluate_fast([sr(cost_today=3500, bid=18)], CFG, daily_budget=0))
    assert d.action == "pause", d.reason
    assert "фолбэк" in d.reason or "3000" in d.reason, d.reason
    print("✓ rules: без бюджета — фолбэк на абсолютный лимит")
```

Добавить вызовы в `__main__` (рядом с другими fast-тестами, перед `print("-" * 60)`):

```python
    test_fast_spend_cap_from_campaign_budget()
    test_fast_spend_cap_fallback_without_budget()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_rules.py`
Expected: FAIL — `evaluate_fast()` ещё не принимает `daily_budget` → `TypeError: evaluate_fast() got an unexpected keyword argument 'daily_budget'`.

- [ ] **Step 3: Реализация**

В `core/rules.py`, `RulesConfig`, добавить поле сразу после `daily_sku_cost_limit`:

```python
    daily_sku_cost_limit: float = 3000
    sku_budget_fraction: float = 0.5   # доля дневного бюджета кампании на один SKU
```

Заменить сигнатуру и тело `evaluate_fast`:

```python
def evaluate_fast(
    skus: list, cfg: RulesConfig | None = None, state: dict | None = None,
    daily_budget: float = 0.0,
) -> list[Decision]:
    cfg = cfg or RulesConfig()
    out: list[Decision] = []
    for s in skus:
        out.append(_eval_fast_one(s, cfg, _state_for(state, s.sku), daily_budget))
    return out
```

Заменить сигнатуру `_eval_fast_one` и блок спенд-капа:

```python
def _eval_fast_one(s, cfg: RulesConfig, st: DailyState,
                   daily_budget: float = 0.0) -> Decision:
    if s.product_state != "Active":
        return _hold(s, "fast", "товар не Active — ставку не трогаем")

    # Спенд-кап важнее лимита изменений: слив бюджета тормозим всегда.
    # Лимит на SKU считаем от дневного бюджета кампании (dailyBudget из кабинета);
    # если бюджет недоступен (0) — фолбэк на абсолютный daily_sku_cost_limit.
    # Эндпоинта «паузы» нет — режем ставку в пол (min_bid), это и есть стоп.
    if daily_budget > 0:
        limit = daily_budget * cfg.sku_budget_fraction
        src = f"{int(cfg.sku_budget_fraction * 100)}% бюджета {daily_budget:g}"
    else:
        limit = cfg.daily_sku_cost_limit
        src = f"фолбэк-лимит {cfg.daily_sku_cost_limit:g}"
    if s.cost_today >= limit:
        return Decision(s.sku, s.merchant_sku, s.bid, cfg.min_bid, "pause", "fast",
                        f"costToday={s.cost_today:g} ≥ {src} = {limit:g} "
                        f"→ пауза (ставка в пол {cfg.min_bid:g})")
```

(остальное тело `_eval_fast_one` — лимит изменений, 0 корзин, скачок avgCpc, финальный hold — без изменений.)

В `config/rules.yaml` заменить строку `daily_sku_cost_limit` на две:

```yaml
daily_sku_cost_limit: 3000     # ФОЛБЭК ₸/сутки на SKU, если бюджет кампании недоступен
sku_budget_fraction: 0.5       # лимит на SKU = доля дневного бюджета кампании (dailyBudget)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_rules.py`
Expected: PASS — все проверки rules, включая две новые. (Старый `test_fast_spend_cap_pauses` зовёт `evaluate_fast` без `daily_budget` → фолбэк 3000 → по-прежнему пауза.)

- [ ] **Step 5: Коммит**

```bash
git add core/rules.py config/rules.yaml test_rules.py
git commit -m "feat(rules): спенд-кап от бюджета кампании (sku_budget_fraction) + фолбэк"
```

---

### Task 3: проброс бюджета кампании в тормозной тик

**Files:**
- Modify: `worker.py` (`run_tick` — параметр `daily_budget`; fast-ветка; `run_cycle` — передаёт `c.daily_budget`)
- Test: `test_worker.py` (тест проброса + вызов в `__main__`)

**Interfaces:**
- Consumes: `Campaign.daily_budget` (Task 1); `evaluate_fast(..., daily_budget)` (Task 2).
- Produces: `run_tick(ctx, loop, campaign_id, daily_budget: float = 0.0)` — в fast-ветке
  зовёт `evaluate_fast(reconciled, ctx.cfg, state, daily_budget)`; `run_cycle` вызывает
  `run_tick(ctx, loop, c.id, daily_budget=c.daily_budget)`.

- [ ] **Step 1: Падающий тест**

В `test_worker.py` убедиться, что `Campaign` импортирован (уже есть: `from connectors.marketing_client import Campaign` из задачи мультикампании). Добавить тест (после `test_run_cycle_empty_is_noop`):

```python
def test_run_cycle_passes_campaign_budget_to_fast_brake():
    # cost_today=4000: под ФОЛБЭКОМ 3000 → пауза; но бюджет кампании 20000
    # (лимит 50% = 10000) → НЕ пауза. Значит бюджет реально проброшен в тормоз.
    st = store_with_revenue({"M1": SkuRevenue(merchant_sku="M1", revenue=5000)})
    fm = FakeMarketing(
        [cp(cost_today=4000, bid=18)], dry_run=True,
        campaigns=[Campaign(id="2899523", name="Бритвы", state="Enabled", daily_budget=20000)])
    decisions = run_cycle(ctx(fm, st, dry_run=True), loop="fast")
    assert decisions[0].action == "hold", decisions[0].reason   # под фолбэком было бы pause
    print("✓ worker: run_cycle пробрасывает бюджет кампании в тормоз (лимит от бюджета)")
```

Добавить вызов в `__main__` (перед `print("-" * 60)`):

```python
    test_run_cycle_passes_campaign_budget_to_fast_brake()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_worker.py`
Expected: FAIL — `run_cycle` зовёт `run_tick` без `daily_budget` → тормоз берёт фолбэк 3000 → `cost_today=4000 ≥ 3000` → `action == "pause"`, а тест ждёт `"hold"`.

- [ ] **Step 3: Реализация**

В `worker.py` изменить сигнатуру `run_tick` (добавить параметр):

```python
def run_tick(ctx: WorkerContext, loop: str, campaign_id: str,
             daily_budget: float = 0.0):
```

В теле `run_tick`, в fast-ветке, передать `daily_budget` в `evaluate_fast`:

```python
    if loop == "fast":
        decisions = evaluate_fast(reconciled, ctx.cfg, state, daily_budget)
    elif loop == "slow":
        decisions = evaluate_slow(reconciled, ctx.cfg, state)
```

В `run_cycle`, в цикле по кампаниям, передать бюджет:

```python
        try:
            all_decisions.extend(run_tick(ctx, loop, c.id, daily_budget=c.daily_budget))
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_worker.py`
Expected: PASS — все проверки worker, включая новую.

- [ ] **Step 5: Полный набор тестов**

Run:
```bash
for t in test_revenue test_marketing test_session test_reconcile test_rules test_store test_worker test_analyst; do .venv/bin/python $t.py >/dev/null && echo "OK $t" || echo "FAIL $t"; done
```
Expected: OK по всем 8.

- [ ] **Step 6: Коммит**

```bash
git add worker.py test_worker.py
git commit -m "feat(worker): проброс dailyBudget кампании в тормозной тик"
```

---

## Post-plan: живой смоук (после реализации)

Не часть коммитов; на VPS (или локально) в dry_run:
- `git pull` на VPS → `.venv/bin/python worker.py --once` (или `systemctl restart`).
- В логах паузы должны считаться от бюджета: например
  `costToday=… ≥ 50% бюджета 20000 = 10000 → пауза` для Аэрогриля,
  `… 50% бюджета 10000 = 5000 …` для Бритв.
