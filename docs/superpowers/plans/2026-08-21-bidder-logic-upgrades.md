# Апгрейд логики ставок (Проект 2, спек 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать движок ставок умнее тремя апгрейдами (пропорциональный шаг, avg_cpc-хедрум, пейсинг бюджета), все пороги — из админки.

**Architecture:** Вся логика в чистом `core/rules.py` (без сети/времени/БД). Новые пороги — поля `RulesConfig`, проводятся в админку через существующий тракт `config_resolver → settings_io → форма UI`, hot-reload воркером. Время (доля суток для пейсинга) считает `worker.py` и передаёт параметром, как уже делает с `daily_budget`.

**Tech Stack:** Python 3.9 (Mac) / 3.12 (VPS), dataclasses, PyYAML, FastAPI+Jinja2 (webui). Тесты — plain-script (НЕ pytest), запуск `.venv/bin/python test_*.py`, успех = печать `✓`.

## Global Constraints

- Движок `core/rules.py` остаётся ЧИСТЫМ: без сети, времени (`datetime.now`), БД. Время приходит параметром.
- **НИ ОДНА задача не трогает `config/rules.yaml`** — он git-tracked, на VPS локально изменён (`dry_run: false`); коммит правок сломает/затрёт живой деплой. Дефолты меняем только в `RulesConfig` (код).
- Тесты — plain-script в корне репо (`test_rules.py` и т.п.), запуск `.venv/bin/python test_<name>.py`, в конце файла список функций + печать итога. НЕ pytest.
- TACoS и `bid_step_pct`/`cpc_headroom`/`pace_tolerance` — доли (ратио), не проценты.
- Escape-hatch каждого апгрейда: значение `0` = поведение как раньше.
- Ставки целочисленные в ₸ (округляем шаг до целого, гарантируем сдвиг ≥ 1₸).
- Следуй существующему стилю: причины решений (`Decision.reason`) — по-русски, как в текущем коде.

---

### Task 1: Пропорциональный шаг ставки (#1)

**Files:**
- Modify: `core/rules.py` — поле `RulesConfig.bid_step_pct`, дефолт `max_bid_step` 2→15, функция `_stepped` (строки 89–101).
- Test: `test_rules.py` (дополнить).

**Interfaces:**
- Consumes: `RulesConfig`, `Decision`, `_hold(s, loop, reason)`, `SkuReconciled` (поля `sku, merchant_sku, bid`).
- Produces: `_stepped(s, direction, loop, reason, cfg)` — сигнатура НЕ меняется; теперь шаг пропорциональный. `RulesConfig.bid_step_pct: float = 0.20`, `RulesConfig.max_bid_step: float = 15`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `test_rules.py` (helper `sr()` и `only()` уже есть):

```python
def test_step_is_proportional_to_bid():
    # bid=100, pct=0.20 → шаг 20 (в пределах кэпа 50)
    cfg = RulesConfig(bid_step_pct=0.20, max_bid_step=50, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=100, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise"
    assert d.new_bid == 120, d.new_bid
    print("✓ #1: шаг пропорционален ставке (100 → 120 при 20%)")


def test_step_clamped_by_max_step_cap():
    # bid=100, pct=0.20 → «сырой» шаг 20, но кэп 15 → шаг 15
    cfg = RulesConfig(bid_step_pct=0.20, max_bid_step=15, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=100, tacos=0.05, score=7.0)], cfg))
    assert d.new_bid == 115, d.new_bid
    print("✓ #1: шаг ограничен кэпом max_bid_step")


def test_step_floor_at_least_one_tenge():
    # bid=3, pct=0.20 → сырой 0.6 → округление дало бы 1; минимум 1₸
    cfg = RulesConfig(bid_step_pct=0.20, max_bid_step=15, min_bid=1, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=3, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise"
    assert d.new_bid == 4, d.new_bid
    print("✓ #1: шаг не меньше 1₸ (мелкая ставка всё равно двигается)")


def test_step_zero_pct_falls_back_to_fixed():
    # bid_step_pct=0 → старое поведение: фиксированный шаг max_bid_step
    cfg = RulesConfig(bid_step_pct=0.0, max_bid_step=2, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=18, tacos=0.05, score=7.0)], cfg))
    assert d.new_bid == 20, d.new_bid
    print("✓ #1: bid_step_pct=0 → фиксированный шаг (откат к старому)")


def test_step_rounds_to_whole_tenge():
    # bid=10, pct=0.15 → 1.5 → round → 2
    cfg = RulesConfig(bid_step_pct=0.15, max_bid_step=15, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=10, tacos=0.05, score=7.0)], cfg))
    assert d.new_bid == 12, d.new_bid
    print("✓ #1: шаг округляется до целого ₸")
```

Добавить пять имён в список функций в блоке `if __name__ == "__main__":`.

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `.venv/bin/python test_rules.py`
Expected: FAIL (при текущем фиксированном шаге `new_bid` будет 102/102/4?/20/12 — часть ассертов не сойдётся; напр. `test_step_is_proportional_to_bid` ждёт 120, получит 102).

- [ ] **Step 3: Реализовать пропорциональный шаг**

В `core/rules.py` в `RulesConfig` изменить дефолт и добавить поле (рядом с `max_bid_step`):

```python
    max_bid_step: float = 15          # кэп одного шага, ₸ (было 2)
    bid_step_pct: float = 0.20        # доля шага от ставки; 0 = фикс-шаг max_bid_step
```

Переписать `_stepped` (заменить тело функции целиком):

```python
def _stepped(s, direction: str, loop: str, reason: str, cfg: RulesConfig) -> Decision:
    """
    Сдвиг ставки на пропорциональный шаг в пределах [min_bid, bid_ceiling].
    Шаг = round(bid × bid_step_pct), зажат в [1, max_bid_step]. При
    bid_step_pct=0 — фиксированный шаг max_bid_step (старое поведение).
    Упор в границу без изменения ставки → hold (менять нечего).
    """
    if cfg.bid_step_pct > 0:
        step = round(s.bid * cfg.bid_step_pct)
        step = max(1, min(step, cfg.max_bid_step))
    else:
        step = cfg.max_bid_step
    if direction == "raise":
        new_bid = min(s.bid + step, cfg.bid_ceiling)
    else:  # lower
        new_bid = max(s.bid - step, cfg.min_bid)
    if new_bid == s.bid:
        edge = "потолок" if direction == "raise" else "пол"
        return _hold(s, loop, f"{reason}, но ставка у границы ({edge})")
    return Decision(s.sku, s.merchant_sku, s.bid, new_bid, direction, loop, reason)
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `.venv/bin/python test_rules.py`
Expected: PASS (все `✓`, включая существующие — фиксированные тесты используют дефолтный cfg; проверить, что старые тесты со ставкой-шагом не сломались, см. Step 5).

- [ ] **Step 5: Прогнать смежные тесты (регресс)**

Существующие тесты вроде `test_slow_below_corridor_raises` используют `CFG = RulesConfig()`. Раньше дефолт `max_bid_step=2` давал `18→20`; теперь `bid_step_pct=0.20` даст `round(18×0.2)=4 → 18→22`. Это ОЖИДАЕМОЕ изменение поведения. Обновить затронутые существующие ассерты под новый дефолт (найти по `new_bid`):

Run: `.venv/bin/python test_rules.py`
Найти каждый упавший старый тест, пересчитать ожидаемый `new_bid` под пропорц. шаг с дефолтами (`pct=0.20, cap=15`), поправить ассерт. Пример: `test_slow_below_corridor_raises` с `bid=18` → `new_bid == 22`.

- [ ] **Step 6: Commit**

```bash
git add core/rules.py test_rules.py
git commit -m "feat(rules): пропорциональный шаг ставки (bid_step_pct), кэп max_bid_step 2→15"
```

---

### Task 2: avg_cpc-хедрум — страж подъёма (#3)

**Files:**
- Modify: `core/rules.py` — поле `RulesConfig.cpc_headroom`, ветка raise в `_eval_slow_one` (строки 183–189).
- Test: `test_rules.py` (дополнить).

**Interfaces:**
- Consumes: `SkuReconciled.avg_cpc` (float, уже есть), `SkuReconciled.bid`, `_hold`, `_stepped`, `RulesConfig` (поля `target_tacos_low, min_score_for_raise`).
- Produces: `RulesConfig.cpc_headroom: float = 2.0`. Правило действует только в slow, только перед raise.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_cpc_headroom_blocks_raise_when_bid_far_above_cpc():
    # bid=40, avg_cpc=12, headroom=2.0 → 40 > 12×2=24 → НЕ поднимаем
    cfg = RulesConfig(cpc_headroom=2.0, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=40, avg_cpc=12, tacos=0.05, score=7.0)], cfg))
    assert d.action == "hold", d.action
    assert "avg_cpc" in d.reason or "цены клика" in d.reason
    print("✓ #3: страж avg_cpc блокирует подъём, если ставка >> реальной цены клика")


def test_cpc_headroom_allows_raise_within_headroom():
    # bid=18, avg_cpc=12, headroom=2.0 → 18 < 24 → подъём разрешён
    cfg = RulesConfig(cpc_headroom=2.0, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=18, avg_cpc=12, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise", d.action
    print("✓ #3: подъём идёт, когда ставка в пределах хедрума")


def test_cpc_headroom_zero_disables_guard():
    # cpc_headroom=0 → страж выключен, подъём как раньше даже при bid>>cpc
    cfg = RulesConfig(cpc_headroom=0.0, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=40, avg_cpc=12, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise", d.action
    print("✓ #3: cpc_headroom=0 → страж выключен")


def test_cpc_headroom_ignored_when_no_cpc_data():
    # avg_cpc=0 (нет данных) → страж не мешает, обычная логика
    cfg = RulesConfig(cpc_headroom=2.0, bid_ceiling=1000)
    d = only(evaluate_slow([sr(bid=40, avg_cpc=0, tacos=0.05, score=7.0)], cfg))
    assert d.action == "raise", d.action
    print("✓ #3: avg_cpc=0 → страж не срабатывает")
```

Добавить четыре имени в runner-список.

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `.venv/bin/python test_rules.py`
Expected: FAIL — `test_cpc_headroom_blocks_raise_when_bid_far_above_cpc` ждёт hold, но сейчас получит raise (стража нет).

- [ ] **Step 3: Реализовать страж**

В `core/rules.py` в `RulesConfig` добавить поле (после `min_score_for_raise`):

```python
    cpc_headroom: float = 2.0   # #3: не поднимать, если bid > avg_cpc×это; 0 = выкл
```

В `_eval_slow_one`, в ветке `if s.tacos < cfg.target_tacos_low:` — добавить проверку стража ПОСЛЕ проверки score, ПЕРЕД `_stepped(..., "raise", ...)`:

```python
    # TACoS ниже коридора → реклама дёшево окупается, можно поднять.
    if s.tacos < cfg.target_tacos_low:
        # но низкий score не разгоняем (плохой товар в рекламе)
        if s.score < cfg.min_score_for_raise:
            return _hold(s, "slow",
                         f"TACoS низкий, но score {s.score} < {cfg.min_score_for_raise} — не поднимаем")
        # страж avg_cpc: ставка уже намного выше реальной цены клика — подъём бессмыслен
        if cfg.cpc_headroom > 0 and s.avg_cpc > 0 and s.bid > s.avg_cpc * cfg.cpc_headroom:
            return _hold(s, "slow",
                         f"ставка {s.bid:g} > avg_cpc {s.avg_cpc:g}×{cfg.cpc_headroom:g} "
                         f"— подъём не нужен (запас над реальной ценой клика)")
        return _stepped(s, "raise", "slow",
                        f"TACoS {s.tacos:.3f} < {cfg.target_tacos_low} → поднимаем", cfg)
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `.venv/bin/python test_rules.py`
Expected: PASS (все `✓`). Проверить, что существующий `test_slow_below_corridor_raises` (`sr()` дефолт `avg_cpc=12.5, bid=18` → `18 < 12.5×2=25` → подъём) не сломан.

- [ ] **Step 5: Commit**

```bash
git add core/rules.py test_rules.py
git commit -m "feat(rules): avg_cpc-хедрум — страж бессмысленных подъёмов (cpc_headroom)"
```

---

### Task 3: Пейсинг бюджета в fast-контуре (#5)

**Files:**
- Modify: `core/rules.py` — поле `RulesConfig.pace_tolerance`, сигнатуры `evaluate_fast` (строки 106–114) и `_eval_fast_one` (строки 117–151).
- Test: `test_rules.py` (дополнить).

**Interfaces:**
- Consumes: `SkuReconciled.cost_today`, `RulesConfig` (`sku_budget_fraction, daily_sku_cost_limit, max_changes_per_day`), `DailyState`, `_stepped`, `_hold`.
- Produces: `evaluate_fast(skus, cfg=None, state=None, daily_budget=0.0, day_frac=1.0)` — новый пятый параметр `day_frac: float` (доля суток 0..1). `_eval_fast_one(s, cfg, st, daily_budget=0.0, day_frac=1.0)`. `RulesConfig.pace_tolerance: float = 1.25`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_pacing_throttles_when_ahead_of_pace():
    # лимит=1000 (фолбэк), день прошёл на 50%, tol=1.0 → pace_limit=500.
    # cost_today=600 ≥ 500, но < 1000 → троттлинг (lower на шаг), НЕ пауза.
    cfg = RulesConfig(daily_sku_cost_limit=1000, pace_tolerance=1.0,
                      bid_step_pct=0.20, max_bid_step=15, bid_ceiling=1000)
    d = only(evaluate_fast([sr(cost_today=600, bid=40, carts=8, clicks=100)],
                           cfg, day_frac=0.5))
    assert d.action == "lower", d.action
    assert "пейсинг" in d.reason or "опережа" in d.reason
    print("✓ #5: опережение плана трат → мягкий троттлинг (не пауза)")


def test_pacing_hard_cap_still_pauses_first():
    # cost_today ≥ полного лимита → пауза (жёсткий стоп раньше троттла)
    cfg = RulesConfig(daily_sku_cost_limit=1000, pace_tolerance=1.0, bid_ceiling=1000)
    d = only(evaluate_fast([sr(cost_today=1000, bid=40)], cfg, day_frac=0.5))
    assert d.action == "pause", d.action
    print("✓ #5: жёсткий спенд-кап бьёт раньше пейсинга")


def test_pacing_zero_tolerance_disables():
    # pace_tolerance=0 → троттла нет, только жёсткий стоп; здесь hold
    cfg = RulesConfig(daily_sku_cost_limit=1000, pace_tolerance=0.0, bid_ceiling=1000)
    d = only(evaluate_fast([sr(cost_today=600, bid=40, carts=8, clicks=100)],
                           cfg, day_frac=0.5))
    assert d.action == "hold", d.action
    print("✓ #5: pace_tolerance=0 → пейсинг выключен")


def test_pacing_under_pace_holds():
    # cost_today ниже pace_limit → обычная логика, hold
    cfg = RulesConfig(daily_sku_cost_limit=1000, pace_tolerance=1.25, bid_ceiling=1000)
    d = only(evaluate_fast([sr(cost_today=100, bid=40, carts=8, clicks=100)],
                           cfg, day_frac=0.5))
    assert d.action == "hold", d.action
    print("✓ #5: трата в пределах плана → hold")


def test_pacing_exempt_from_change_limit():
    # даже при исчерпанном лимите изменений пейсинг должен сработать (защита бюджета)
    cfg = RulesConfig(daily_sku_cost_limit=1000, pace_tolerance=1.0,
                      max_changes_per_day=3, bid_step_pct=0.20, max_bid_step=15,
                      bid_ceiling=1000)
    state = {"166350900": DailyState(changes_today=3)}
    d = only(evaluate_fast([sr(cost_today=600, bid=40, carts=8, clicks=100)],
                           cfg, state=state, day_frac=0.5))
    assert d.action == "lower", d.action
    print("✓ #5: пейсинг освобождён от лимита изменений/сутки")


def test_pacing_default_day_frac_one_no_effect():
    # day_frac=1.0 (дефолт) → pace_limit=лимит×tol ≥ лимит → троттла нет
    cfg = RulesConfig(daily_sku_cost_limit=1000, pace_tolerance=1.25, bid_ceiling=1000)
    d = only(evaluate_fast([sr(cost_today=600, bid=40, carts=8, clicks=100)], cfg))
    assert d.action == "hold", d.action
    print("✓ #5: day_frac=1.0 (дефолт) → пейсинг не влияет")
```

Добавить шесть имён в runner-список.

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `.venv/bin/python test_rules.py`
Expected: FAIL с `TypeError: evaluate_fast() got an unexpected keyword argument 'day_frac'`.

- [ ] **Step 3: Реализовать пейсинг**

В `core/rules.py` в `RulesConfig` добавить поле (после `cpc_headroom`):

```python
    pace_tolerance: float = 1.25   # #5: слак пейсинга (лимит×день×это); 0 = выкл
```

Изменить сигнатуру `evaluate_fast` и проброс:

```python
def evaluate_fast(
    skus: list, cfg: RulesConfig | None = None, state: dict | None = None,
    daily_budget: float = 0.0, day_frac: float = 1.0,
) -> list[Decision]:
    out: list[Decision] = []
    for s in skus:
        out.append(_eval_fast_one(s, _cfg_for(cfg, s),
                                  _state_for(state, s.sku), daily_budget, day_frac))
    return out
```

Переписать `_eval_fast_one` — вставить пейсинг ПОСЛЕ жёсткого стопа, ПЕРЕД лимитом изменений (порядок из спека). Полная функция:

```python
def _eval_fast_one(s, cfg: RulesConfig, st: DailyState,
                   daily_budget: float = 0.0, day_frac: float = 1.0) -> Decision:
    if s.product_state != "Active":
        return _hold(s, "fast", "товар не Active — ставку не трогаем")

    # Лимит расхода на SKU: доля дневного бюджета кампании, фолбэк — абсолютный.
    if daily_budget > 0:
        limit = daily_budget * cfg.sku_budget_fraction
        src = f"{int(cfg.sku_budget_fraction * 100)}% бюджета {daily_budget:g}"
    else:
        limit = cfg.daily_sku_cost_limit
        src = f"фолбэк-лимит {cfg.daily_sku_cost_limit:g}"

    # Жёсткий стоп: слив всего лимита тормозим всегда (освобождён от лимита изменений).
    if s.cost_today >= limit:
        return Decision(s.sku, s.merchant_sku, s.bid, cfg.min_bid, "pause", "fast",
                        f"costToday={s.cost_today:g} ≥ {src} = {limit:g} "
                        f"→ пауза (ставка в пол {cfg.min_bid:g})")

    # Пейсинг: опережаем план трат по времени суток → мягко тормозим (освобождён
    # от лимита изменений — та же защита бюджета, что и жёсткий стоп; только снижает,
    # ограничен снизу min_bid, поэтому churn самоограничивается).
    if cfg.pace_tolerance > 0 and limit > 0:
        pace_limit = limit * day_frac * cfg.pace_tolerance
        if s.cost_today >= pace_limit:
            return _stepped(s, "lower", "fast",
                            f"пейсинг: costToday={s.cost_today:g} опережает план "
                            f"{pace_limit:g} (лимит {limit:g}×{day_frac:.2f}×{cfg.pace_tolerance:g})",
                            cfg)

    if st.changes_today >= cfg.max_changes_per_day:
        return _hold(s, "fast", f"исчерпан лимит изменений/сутки ({cfg.max_changes_per_day})")

    # 0 корзин при достаточном объёме кликов.
    if s.carts == 0 and s.clicks >= cfg.min_clicks_for_no_cart_cut:
        return _stepped(s, "lower", "fast",
                        f"{s.clicks} кликов, 0 корзин → срез ставки", cfg)

    # Скачок avgCpc относительно прошлого снапшота.
    if st.prev_avg_cpc and s.avg_cpc > st.prev_avg_cpc * (1 + cfg.cpc_spike_pct):
        return _stepped(s, "lower", "fast",
                        f"avgCpc {s.avg_cpc} подскочил >{int(cfg.cpc_spike_pct*100)}% "
                        f"(было {st.prev_avg_cpc}) → срез ставки", cfg)

    return _hold(s, "fast", "тормозных триггеров нет")
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `.venv/bin/python test_rules.py`
Expected: PASS. Проверить регресс: существующий `test_fast_spend_cap_pauses` (`cost_today=3500`, дефолт `daily_sku_cost_limit=3000`, `day_frac=1.0` по дефолту) остаётся `pause` (3500 ≥ 3000). Существующие fast-тесты вызывают `evaluate_fast(..., CFG)` без `day_frac` → дефолт 1.0 → пейсинг не влияет.

- [ ] **Step 5: Commit**

```bash
git add core/rules.py test_rules.py
git commit -m "feat(rules): пейсинг бюджета в fast (pace_tolerance, day_frac), мягкий троттл до жёсткого стопа"
```

---

### Task 4: Проброс day_frac из worker (#5, интеграция)

**Files:**
- Modify: `worker.py` — вызов `evaluate_fast` (строка 150), расчёт `day_frac` рядом с `now_local` (строка 140).
- Test: `test_worker.py` (дополнить).

**Interfaces:**
- Consumes: `evaluate_fast(active, cfg_for, state, daily_budget, day_frac)` из Task 3; `now_local` (datetime в зоне ALMATY, уже есть в `run_tick` на строке 140).
- Produces: worker считает `day_frac = (секунды с полуночи Алматы) / 86400` и передаёт в `evaluate_fast`.

- [ ] **Step 1: Написать падающий тест**

Существующие фикстуры `test_worker.py`: `NOW = lambda: datetime(2026, 8, 9, 14, 0, tzinfo=ALMATY)` (14:00 Алматы → `day_frac ≈ 0.583`), `cp(**over)` строит `CampaignProduct`, `FakeMarketing(products, dry_run, campaigns=None)`, `store_with_revenue(rev)` строит `Store`. Строим `WorkerContext` напрямую (как в `test_run_cycle_allowlist`). Добавить:

```python
def test_run_tick_fast_paces_by_time_of_day():
    # NOW=14:00 Алматы → day_frac≈0.583; лимит 1000, tol=1.0 → pace_limit≈583.
    # cost_today=800 ≥ 583 и < 1000 → мягкий троттлинг (lower), НЕ пауза.
    st = store_with_revenue({})
    fm = FakeMarketing([cp(sku="S1", merchant_sku="M1", bid=40, cost_today=800)],
                       dry_run=True)
    c = WorkerContext(
        marketing=fm, store=st,
        cfg=RulesConfig(daily_sku_cost_limit=1000, pace_tolerance=1.0,
                        bid_step_pct=0.20, max_bid_step=15, bid_ceiling=1000,
                        dry_run=True),
        now_fn=NOW,
    )
    decisions = run_tick(c, loop="fast", campaign_id="C1", daily_budget=0.0)
    d = next(x for x in decisions if x.sku == "S1")
    assert d.action == "lower", d.action
    assert "пейсинг" in d.reason
    print("✓ worker: fast-тик тормозит по пейсингу от времени суток")
```

Добавить имя в runner-список файла (блок `if __name__ == "__main__":`).

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_worker.py`
Expected: FAIL — сейчас `run_tick` не считает `day_frac`, пейсинг не сработает → `d.action` будет `hold`, ассерт `lower` упадёт.

- [ ] **Step 3: Реализовать проброс**

В `worker.py` в `run_tick`, рядом с `now_local = now.astimezone(ALMATY)` (строка 140) добавить расчёт доли суток:

```python
    now_local = now.astimezone(ALMATY)
    _midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_frac = (now_local - _midnight).total_seconds() / 86400.0
```

Изменить вызов fast (строка 150):

```python
    if loop == "fast":
        decisions = evaluate_fast(active, cfg_for, state, daily_budget, day_frac)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_worker.py`
Expected: PASS (все `✓`).

- [ ] **Step 5: Commit**

```bash
git add worker.py test_worker.py
git commit -m "feat(worker): считать day_frac (доля суток Алматы) и передавать в evaluate_fast для пейсинга"
```

---

### Task 5: Проводка порогов в конфиг-тракт (админка backend)

**Files:**
- Modify: `core/config_resolver.py` — `OVERRIDABLE_FIELDS` (строки 14–20).
- Modify: `core/settings_io.py` — `SETTINGS_FIELDS` (строки 11–18), `validate_settings`, `save_settings`.
- Test: `test_config_resolver.py`, `test_settings_io.py` (дополнить).

**Interfaces:**
- Consumes: `RulesConfig` поля `bid_step_pct, cpc_headroom, pace_tolerance` (из Tasks 1–3).
- Produces: три новых поля переопределяются по кампании/SKU и редактируются/валидируются/пишутся из UI-тракта. `max_bid_step` уже в обоих списках.

- [ ] **Step 1: Написать падающие тесты**

В `test_config_resolver.py` добавить:

```python
def test_new_fields_overridable_per_sku():
    from core.rules import RulesConfig
    g = RulesConfig()
    eff = resolve_config(g, {}, {"bid_step_pct": "0.30", "cpc_headroom": "1.5",
                                 "pace_tolerance": "0"})
    assert eff.bid_step_pct == 0.30
    assert eff.cpc_headroom == 1.5
    assert eff.pace_tolerance == 0.0
    print("✓ resolver: bid_step_pct/cpc_headroom/pace_tolerance переопределяются по SKU")
```

В `test_settings_io.py` (в файле нет helper'а — валидный набор строят как
`{f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}`, см. `test_validate_catches_bad_values`). Добавить:

```python
def test_settings_accepts_new_field_defaults():
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    assert validate_settings(base) == []   # дефолты с новыми полями валидны
    print("✓ settings: дефолты с новыми полями валидны")


def test_settings_rejects_bad_new_fields():
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    assert any("bid_step_pct" in e for e in validate_settings(dict(base, bid_step_pct=1.5)))
    assert any("cpc_headroom" in e for e in validate_settings(dict(base, cpc_headroom=-1)))
    assert any("pace_tolerance" in e for e in validate_settings(dict(base, pace_tolerance=-0.5)))
    print("✓ settings: невалидные новые поля отклонены")


def test_settings_roundtrip_new_fields():
    import tempfile, os
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    data = dict(base, bid_step_pct=0.25, cpc_headroom=1.8, pace_tolerance=1.1)
    path = os.path.join(tempfile.mkdtemp(), "rules.yaml")
    save_settings(path, data)
    loaded = load_settings(path)
    assert loaded["bid_step_pct"] == 0.25
    assert loaded["cpc_headroom"] == 1.8
    assert loaded["pace_tolerance"] == 1.1
    print("✓ settings: новые поля переживают save→load")
```

Добавить имена в runner-списки обоих файлов (`test_config_resolver.py` и `test_settings_io.py`).

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `.venv/bin/python test_config_resolver.py && .venv/bin/python test_settings_io.py`
Expected: FAIL — поля не в `OVERRIDABLE_FIELDS`/`SETTINGS_FIELDS`, `resolve_config`/`validate` их игнорируют/не проверяют.

- [ ] **Step 3: Реализовать проводку**

В `core/config_resolver.py` расширить `OVERRIDABLE_FIELDS`:

```python
OVERRIDABLE_FIELDS = [
    "target_tacos_low", "target_tacos_high",
    "daily_sku_cost_limit", "sku_budget_fraction",
    "min_clicks_for_no_cart_cut", "cpc_spike_pct",
    "max_bid_step", "max_changes_per_day",
    "bid_ceiling", "min_bid", "min_score_for_raise",
    "bid_step_pct", "cpc_headroom", "pace_tolerance",
]
```

В `core/settings_io.py` расширить `SETTINGS_FIELDS` (перед `dry_run`):

```python
SETTINGS_FIELDS = [
    "target_tacos_low", "target_tacos_high",
    "daily_sku_cost_limit", "sku_budget_fraction",
    "min_clicks_for_no_cart_cut", "cpc_spike_pct",
    "max_bid_step", "max_changes_per_day",
    "bid_ceiling", "min_bid", "min_score_for_raise",
    "bid_step_pct", "cpc_headroom", "pace_tolerance",
    "dry_run", "campaign_ids",
]
```

В `validate_settings` добавить проверки (рядом с остальными `num(...)`):

```python
    step_pct = num("bid_step_pct"); headroom = num("cpc_headroom"); pace = num("pace_tolerance")
    if step_pct is not None and not (0 <= step_pct < 1):
        errs.append("bid_step_pct: доля в [0, 1)")
    if headroom is not None and headroom < 0:
        errs.append("cpc_headroom: не отрицательный")
    if pace is not None and pace < 0:
        errs.append("pace_tolerance: не отрицательный")
```

В `save_settings` в словарь `out` добавить три поля (перед `dry_run`):

```python
        "bid_step_pct": float(data["bid_step_pct"]),
        "cpc_headroom": float(data["cpc_headroom"]),
        "pace_tolerance": float(data["pace_tolerance"]),
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `.venv/bin/python test_config_resolver.py && .venv/bin/python test_settings_io.py`
Expected: PASS (все `✓`).

- [ ] **Step 5: Commit**

```bash
git add core/config_resolver.py core/settings_io.py test_config_resolver.py test_settings_io.py
git commit -m "feat(config): новые пороги (bid_step_pct/cpc_headroom/pace_tolerance) в оверрайды и настройки"
```

---

### Task 6: Поля в форме админки (метаданные UI)

**Files:**
- Modify: `webui/templates/settings.html` — блок `field_meta` (строки 5–17).
- Modify: `webui/templates/campaign_settings.html` — аналогичный блок `field_meta`.
- Modify: `webui/templates/sku_settings.html` — аналогичный блок `field_meta`.
- Test: `test_webui.py` (дополнить).

**Interfaces:**
- Consumes: `SETTINGS_FIELDS`/`OVERRIDABLE_FIELDS` (Task 5) — формы уже итерируют `{% for f in fields %}` и рендерят `field_meta[f]` при наличии, иначе сырое имя. Достаточно добавить метаданные для красивых подписей.
- Produces: три новых поля в форме с русскими подписями и подсказками.

- [ ] **Step 1: Написать падающий тест**

В `test_webui.py` (использовать существующий helper входа `_client_logged_in()`):

```python
def test_settings_page_shows_new_fields():
    client = _client_logged_in()
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.text
    assert 'name="bid_step_pct"' in body
    assert 'name="cpc_headroom"' in body
    assert 'name="pace_tolerance"' in body
    # человекочитаемые подписи, а не сырые имена
    assert "Шаг ставки" in body or "пропорц" in body.lower()
    print("✓ webui: форма настроек показывает новые поля с подписями")
```

Добавить имя в runner-список файла.

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python test_webui.py`
Expected: FAIL по ассерту подписи (`name="bid_step_pct"` уже появится после Task 5, т.к. форма итерирует `fields`, но подписи «Шаг ставки» ещё нет → падает на проверке подписи). Если и input отсутствует — тем более FAIL.

- [ ] **Step 3: Добавить метаданные в три шаблона**

В каждом из `webui/templates/settings.html`, `campaign_settings.html`, `sku_settings.html` в словарь `field_meta` добавить три записи (перед закрывающей `} %}`):

```jinja
  'bid_step_pct': {'label': 'Шаг ставки, доля', 'hint': 'Пропорциональный шаг: доля от текущей ставки за одно изменение (0.20 = 20%). 0 = фиксированный шаг из «Макс. шаг».'},
  'cpc_headroom': {'label': 'Запас над ценой клика (avg_cpc)', 'hint': 'Не поднимать ставку, если она уже выше средней цены клика в это число раз (2.0 = вдвое). 0 = проверка выключена.'},
  'pace_tolerance': {'label': 'Слак пейсинга бюджета', 'hint': 'Насколько можно опережать равномерный план трат по времени суток до мягкого притормаживания (1.25 = +25%). 0 = пейсинг выключен, остаётся только жёсткий лимит.'},
```

Также обновить подсказку `max_bid_step` во всех трёх (сменился смысл на «кэп одного шага»):

```jinja
  'max_bid_step': {'label': 'Макс. шаг изменения ставки, ₸', 'hint': 'Максимальный скачок ставки за одно изменение (потолок пропорционального шага).'},
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python test_webui.py`
Expected: PASS (все `✓`).

- [ ] **Step 5: Commit**

```bash
git add webui/templates/settings.html webui/templates/campaign_settings.html webui/templates/sku_settings.html test_webui.py
git commit -m "feat(webui): поля bid_step_pct/cpc_headroom/pace_tolerance в форме настроек (глобал/кампания/SKU)"
```

---

## Финальная проверка (после всех задач)

- [ ] Прогнать весь набор тестов:

```bash
for t in test_rules.py test_config_resolver.py test_settings_io.py test_worker.py test_webui.py; do
  echo "== $t =="; .venv/bin/python $t || echo "FAIL $t"
done
```

Ожидание: все файлы печатают итоговую `✓`-строку, ни одного FAIL.

- [ ] Убедиться, что `config/rules.yaml` НЕ в diff ветки:

```bash
git diff --name-only master..HEAD | grep -q '^config/rules.yaml$' && echo "ОШИБКА: rules.yaml изменён!" || echo "OK: config/rules.yaml не тронут"
```

## Деплой (вне плана, вручную — как в Проекте 1)

- `git pull --ff-only` на VPS (не трогает локальный `config/rules.yaml`).
- Из админки выставить стартовые значения. Рекомендация безопасного выката:
  сперва `pace_tolerance=0` и `cpc_headroom=0` глобально (инертный деплой), затем
  включать по одному сначала на одном SKU (канарейка), потом глобально; поднять
  `max_bid_step` (напр. 15), чтобы заработал пропорц. шаг.
- Рестарт не обязателен (hot-reload), но webui-изменения (шаблоны) требуют
  `systemctl restart kaspi-webui`.
