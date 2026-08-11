# Мульти-кампания: автоуправление всеми активными кампаниями

Дата: 2026-08-11

## Цель

Сейчас автопилот управляет ставками ровно одной кампании (`KASPI_CAMPAIGN_ID` из
`.env`). У продавца может быть несколько активных кампаний одновременно (сейчас две:
`2899523` «Бритвы» и `3032419` «Аэрогриль 08.08.2026»). Нужно, чтобы бот вёл **все**
кампании в статусе `Enabled` — сам обнаруживал их каждый цикл и подхватывал новые
без ручной правки конфига.

## Не-цели (YAGNI)

- Разные пороги правил на разные кампании — все кампании идут по одному `rules.yaml`.
- Управление кампаниями в статусах `Paused`/`Archived` — только `Enabled`.
- Ключевание снапшотов/TACoS по кампании — опирается на инвариант «один товар = одна
  активная кампания» (см. Допущения).

## Допущения

- **SKU уникален между активными кампаниями.** Kaspi привязывает рекламу товара к
  одной кампании, а кампании продавца организованы по товарным семействам (бритвы vs
  аэрогрили — непересекающиеся SKU). Поэтому ключевание `products_snapshot`,
  `tacos_daily` и счётчика суточных изменений по `sku` остаётся корректным.
  Если инвариант когда-нибудь нарушится (один товар в двух активных кампаниях) —
  пересмотреть ключевание на `(campaign_id, sku)`. Пока не делаем.
- Эндпоинт списка кампаний подтверждён на живом кабинете:
  `GET /advertising/products/api/v5/merchant/{merchant_id}/Campaigns?StartDate=&EndDate=&state=Enabled`
  → `{ "data": [ { "id", "name", "state", ... } ] }`.

## Архитектура

Обнаружение отделено от per-campaign логики. `run_tick` остаётся чистой функцией на
**одну** кампанию; новая обёртка `run_cycle` обнаруживает активные кампании и вызывает
`run_tick` по каждой. Планировщик зовёт `run_cycle`. Revenue-цикл глобальный —
не меняется.

```
Планировщик → run_cycle(ctx, loop):
    campaigns = ctx.marketing.list_active_campaigns()      # с учётом allowlist
    for c in campaigns:
        try:
            run_tick(ctx, loop, c.id)                      # изоляция ошибок
        except Exception:
            log.error(...); continue
run_tick(ctx, loop, campaign_id):                          # логика прежняя
    read products(campaign_id) → reconcile(глоб. выручка) → rules(loop) →
    update_bids(campaign_id, …) → log_decision(…, campaign_id)
```

## Компоненты и изменения

### 1. `connectors/marketing_client.py`
- Новый датакласс `Campaign`: `id: str`, `name: str`, `state: str`.
- Новый метод `list_active_campaigns(start_date: str, end_date: str) -> list[Campaign]`:
  - GET на `/advertising/products/api/v5/merchant/{merchant_id}/Campaigns`
    с `StartDate`/`EndDate` (окно как у тика) и `state=Enabled`.
  - Парсит `data[]` в `Campaign`; фильтрует по `state == "Enabled"` (на случай,
    если сервер вернёт больше). Возвращает список.
  - Окно дат передаёт вызывающий (worker), чтобы модуль оставался без часов.

### 2. `worker.py`
- `run_tick(ctx, loop, campaign_id)` — добавить параметр `campaign_id`; убрать
  использование `ctx.campaign_id` внутри. `_apply_and_log` получает `campaign_id`
  и передаёт в `update_bids` и `log_decision`.
- `run_cycle(ctx, loop)` — новая функция:
  - собирает окно дат (как в тике), зовёт `ctx.marketing.list_active_campaigns(...)`;
  - применяет опциональный allowlist из `ctx` (если задан — пересечение);
  - по каждой кампании вызывает `run_tick` в `try/except` (per-campaign изоляция);
  - агрегирует и логирует итог (`кампаний=N, решений=…, изменений=…`).
  - если список не удалось получить — `log.error`, выходим (цикл пропущен),
    планировщик продолжает жить.
- `WorkerContext`: `campaign_id: str` → `campaign_ids: list[str] | None`
  (allowlist; `None`/пусто = все `Enabled`). Поле `campaign_id` удаляется.
- `main()`: читает `KASPI_CAMPAIGN_IDS` (CSV) в `campaign_ids`; расписания зовут
  `run_cycle` вместо `run_tick`.

### 3. `core/store.py`
- `decisions_log` +колонка `campaign_id TEXT` в `CREATE TABLE`.
- Миграция для существующей БД: в `_init_schema` после `executescript` проверить
  `PRAGMA table_info(decisions_log)`; если нет `campaign_id` — `ALTER TABLE
  decisions_log ADD COLUMN campaign_id TEXT`.
- `log_decision(self, d, ts, day, applied, campaign_id)` — новый обязательный
  параметр `campaign_id`; пишется в новую колонку.
- `get_decisions_for_day` вернёт `campaign_id` автоматически (`SELECT *`).

### 4. `config/.env.example`
- `KASPI_CAMPAIGN_ID` → пометить устаревшим/необязательным.
- Добавить `KASPI_CAMPAIGN_IDS=` с комментарием: «список ID через запятую;
  пусто = вести все активные (Enabled) кампании».

## Обработка ошибок

- **Список кампаний не пришёл** (сеть/HTTP): `run_cycle` логирует `log.error` и
  выходит — цикл пропущен, планировщик и следующий тик живы.
- **Падение одной кампании**: ловим в `run_cycle`, логируем, продолжаем остальные.
- **`SessionBlockedError`** при `build_ctx()`/refresh сессии: как сейчас — один раз
  на цикл, пробрасывается наверх (воркер встаёт, шлёт алерт). Поведение не меняем.

## Тестирование (стиль проекта — plain-скрипты с asserts, инъекция зависимостей)

- `test_marketing.py`: `list_active_campaigns` через `httpx.MockTransport` —
  парсит `data[]`, фильтрует `state=Enabled`, шлёт `state=Enabled` в query.
- `test_worker.py`:
  - `run_cycle` зовёт `run_tick` по каждой из N кампаний (фейковый marketing
    с 2 кампаниями → 2 набора решений; проверяем агрегат).
  - одна кампания кидает исключение → остальные всё равно обрабатываются.
  - пустой список кампаний → no-op, без ошибок.
  - allowlist сужает набор до заданных ID.
- `test_store.py`: `log_decision(..., campaign_id=...)` пишет колонку; отдельный
  кейс — миграция: открыть старую схему без колонки → `_init_schema` добавляет её.

## Влияние на живой прогон

- `oneshot_tick.py` (scratchpad) и любые вызовы `run_tick` обновить под новую
  сигнатуру (`campaign_id` параметром).
- Локальная `db/autopilot.db` мигрируется автоматически при старте.
- Дефолт `dry_run=true` сохраняется — мультикампания включается тоже в dry_run,
  наблюдаем в логах перед боевым запуском.
