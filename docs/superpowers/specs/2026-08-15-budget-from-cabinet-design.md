# Лимит расхода на SKU из бюджета кампании (подтягиваем из кабинета)

Дата: 2026-08-15

## Цель

Сейчас быстрый (тормозной) контур ставит SKU на паузу по хардкод-порогу
`daily_sku_cost_limit: 3000` ₸/сутки — число ни к чему не привязано. Владелец
задаёт **дневной бюджет на каждую кампанию** в кабинете Kaspi (`dailyBudget`,
проверено живьём: Бритвы 10000, Аэрогриль 20000). Нужно, чтобы бот **сам
подтягивал `dailyBudget` из кабинета** и считал per-SKU лимит от него — тогда
владелец меняет бюджет только в кабинете, а предохранитель подстраивается.

## Не-цели (YAGNI)

- Не трогаем медленный (TACoS) контур — только быстрый тормоз.
- Не дублируем общий бюджет кампании: его Kaspi держит сам (когда исчерпан —
  сам перестаёт крутить). Наш per-SKU лимит — защита от разгона ОДНОГО товара.
- Пока не читаем прочие поля кампании (`defaultBid`, `biddingType`) — только бюджет.

## Формула

Per-SKU лимит выводится как **доля от дневного бюджета кампании**:

```
limit = dailyBudget × sku_budget_fraction        # если dailyBudget > 0
      = daily_sku_cost_limit                       # фолбэк, если бюджет неизвестен/0
пауза, когда cost_today ≥ limit
```

- `sku_budget_fraction` — новый порог в `rules.yaml`, дефолт **0.5** (SKU на паузу,
  если съел >50% дневного бюджета кампании). Не зависит от числа товаров.
- `daily_sku_cost_limit: 3000` **остаётся** как абсолютный фолбэк (используется
  только когда `dailyBudget` недоступен/0).

## Архитектура и изменения

### 1. `connectors/marketing_client.py`
- `Campaign` +поле `daily_budget: float = 0.0`.
- `list_active_campaigns`: парсит `dailyBudget` из ответа →
  `daily_budget=float(row.get("dailyBudget", 0) or 0)`.

### 2. `config/rules.yaml` + `core/rules.py`
- `rules.yaml`: добавить `sku_budget_fraction: 0.5`; у `daily_sku_cost_limit`
  переписать комментарий на «фолбэк, если бюджет кампании недоступен».
- `RulesConfig`: добавить поле `sku_budget_fraction: float = 0.5`.
- `evaluate_fast(skus, cfg, state, daily_budget=0.0)` — новый необязательный
  параметр `daily_budget`; пробрасывается в `_eval_fast_one`.
- `_eval_fast_one(s, cfg, st, daily_budget=0.0)`:
  ```python
  limit = (daily_budget * cfg.sku_budget_fraction
           if daily_budget > 0 else cfg.daily_sku_cost_limit)
  if s.cost_today >= limit:
      # причина показывает и число, и источник
      src = (f"{int(cfg.sku_budget_fraction*100)}% бюджета {daily_budget:g}"
             if daily_budget > 0 else f"фолбэк-лимит {cfg.daily_sku_cost_limit:g}")
      return pause(..., reason=f"costToday={s.cost_today:g} ≥ {src} = {limit:g} → пауза")
  ```

### 3. `worker.py`
- `run_tick(ctx, loop, campaign_id, daily_budget=0.0)` — новый необязательный
  параметр; в fast-ветке передаётся в `evaluate_fast(..., daily_budget=daily_budget)`.
  (Медленный контур `daily_budget` не использует.)
- `run_cycle`: у него уже есть объекты `Campaign` из `list_active_campaigns` →
  вызывает `run_tick(ctx, loop, c.id, daily_budget=c.daily_budget)`.

## Обработка краёв

- `dailyBudget` отсутствует/0 → фолбэк на абсолютный `daily_sku_cost_limit`
  (текущее поведение сохраняется).
- Медленный тик вызывает `run_tick` с `daily_budget=0.0` по умолчанию — на TACoS
  это не влияет (быстрый лимит там не применяется).
- Тесты, дергающие `run_tick`/`evaluate_fast` без `daily_budget`, работают как
  раньше (параметр опциональный, дефолт 0.0 → фолбэк).

## Тестирование (plain-скрипты с asserts)

- `test_marketing.py`: `list_active_campaigns` парсит `dailyBudget` в
  `Campaign.daily_budget` (расширить SAMPLE_CAMPAIGNS полем dailyBudget).
- `test_rules.py`: (а) при `daily_budget>0` пауза срабатывает по
  `daily_budget×fraction` (напр. budget=10000, fraction=0.5, cost_today=5100 →
  пауза; 4900 → не пауза); (б) при `daily_budget=0` — фолбэк на
  `daily_sku_cost_limit` (текущее поведение).
- `test_worker.py`: `run_cycle` пробрасывает `c.daily_budget` в fast-тик так, что
  SKU с cost_today выше бюджетного лимита уходит в pause (через FakeMarketing с
  кампанией, несущей daily_budget).

## Совместимость

- Все новые параметры опциональные с дефолтами → существующие вызовы и тесты
  не ломаются. `daily_sku_cost_limit` не удаляется (становится фолбэком).
- Живой прогон `--once` после сборки: подтвердить, что в логах паузы считаются
  от бюджета (напр. «≥ 50% бюджета 20000 = 10000»).
