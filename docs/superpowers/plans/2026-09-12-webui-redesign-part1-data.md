# Редизайн панели, часть 1: данные и зачистка — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Научить воркер копить подневные CTR/CR/ROAS и заготовки под ИИ-разборы, и снести позиционный трекер — чтобы следующий план мог строить API и графики на готовых данных.

**Architecture:** Всё изменение — в слое персистенции и в оркестрации воркера. `products_snapshot` начинает хранить семь полей обратной связи, которые кабинет уже отдаёт, а мы выбрасываем. Появляются две таблицы: `metrics_daily` (подневные метрики, единственный честный источник для графиков) и `ai_insights` (кэш разборов). Новый часовой джоб наполняет `metrics_daily`, спрашивая кабинет отдельно за каждый день. Ядро решений (`core/rules.py`, `run_tick`) не трогаем вообще.

**Tech Stack:** Python 3.10+, stdlib `sqlite3` без ORM, APScheduler, тесты — самостоятельные скрипты без pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-webui-redesign-design.md`

## Global Constraints

- **Тесты без pytest.** Каждый `test_*.py` — скрипт с `if __name__ == "__main__":` и списком вызовов в конце. Запуск: `.venv/bin/python test_store.py`. Новый тест обязан быть дописан в этот список, иначе он не выполняется.
- **Ядро решений неприкосновенно.** `core/rules.py`, `core/config_resolver.py`, `core/daypart.py`, `run_tick` не меняются ни в одной задаче этого плана.
- **Часовой пояс — `Asia/Almaty`** через `ZoneInfo`, никогда не локальная зона сервера (VPS живёт в UTC).
- **Миграции — только `ALTER TABLE ADD COLUMN`** после проверки `PRAGMA table_info`. Боевая БД на VPS не пересоздаётся и не теряет данные.
- **Комментарии и логи — по-русски**, как во всём проекте.
- **Коммит после каждой задачи**, сообщение по-русски в стиле `feat(store): …`.

## Два отступления от спека

Обнаружены при написании плана, оба — исправление ошибки в спеке, а не смена решения.

1. **`roas` при нулевой выручке — это `0.0`, а не `NULL`.** Спек говорил «при `cost = 0` или `revenue = 0` поле пишется NULL». Для TACoS это верно (`cost / revenue` — деление на ноль). Для ROAS неверно: `revenue / cost` при `cost > 0, revenue = 0` прекрасно определён и равен нулю, и это важный сигнал «деньги потратили, выручки нет». Прячем в `NULL` только настоящий ноль в знаменателе.
2. **`ai_insights` получает колонку `calls`.** Спек считал лимит `AI_DAILY_LIMIT` как число строк за день, но строка перезаписывается при `?force=1` — принудительные пересчёты не считались бы, и лимит расхода протекал бы. Счётчик инкрементируется при каждой перезаписи.

---

### Task 1: Снести позиционный трекер

Идёт первой: дальше правится схема `store.py` и оркестрация `worker.py`, и делать это в файлах, наполовину занятых мёртвым кодом, — лишняя работа.

**Files:**
- Delete: `connectors/search_client.py`, `core/positions_config.py`, `config/positions.yaml`, `webui/templates/positions.html`
- Delete: `test_search_client.py`, `test_store_positions.py`, `test_worker_positions.py`, `test_positions_config.py`, `test_webui_positions.py`
- Modify: `core/store.py` (докстринг модуля; `_init_schema` строки 108–114; методы строки 470–505)
- Modify: `worker.py` (`import json` строка 19; `from connectors.search_client import …` строка 29; `run_position_tick` строки 72–96; загрузка `pos_cfg` в `main()`; блок джоба и строка лога)
- Modify: `webui/app.py` (`import json` строка 5; роут `/positions` строки 226–250)
- Modify: `webui/templates/base.html` (ссылка в навигации)

**Interfaces:**
- Consumes: ничего.
- Produces: `Store` без `put_position_snapshot`, `get_position_series`, `get_latest_position`, `list_tracked_pairs`. `worker` без `run_position_tick`. Ни один последующий таск на них не ссылается.

- [ ] **Step 1: Зафиксировать зелёную базу до изменений**

Нужно знать, что падения после зачистки — наши, а не унаследованные.

```bash
cd /Users/aidynibrayev/Desktop/kaspi-ads-autopilot-pkg
for t in test_store.py test_worker.py test_webui.py test_rules.py \
         test_revenue.py test_reconcile.py test_daypart.py \
         test_config_resolver.py test_settings_io.py test_marketing.py \
         test_session.py test_analyst.py; do
  echo "--- $t"; .venv/bin/python "$t" >/dev/null 2>&1 && echo OK || echo FAIL
done
```

Ожидание: все `OK`. Если что-то падает уже сейчас — остановись и сообщи, не чини попутно.

- [ ] **Step 2: Удалить файлы фичи**

```bash
git rm connectors/search_client.py core/positions_config.py config/positions.yaml \
       webui/templates/positions.html \
       test_search_client.py test_store_positions.py test_worker_positions.py \
       test_positions_config.py test_webui_positions.py
```

- [ ] **Step 3: Вычистить `core/store.py`**

В докстринге модуля список таблиц оставить без упоминания позиций.

В `_init_schema` удалить из `executescript` блок (строки 108–114):

```sql
            CREATE TABLE IF NOT EXISTS position_snapshots (
                ts INTEGER, keyword TEXT, city TEXT, product_id TEXT,
                our_rank INTEGER, total INTEGER, listing_json TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_pos_kw_city_ts
                ON position_snapshots(keyword, city, ts);
```

Сразу после `executescript(...)` добавить снос таблицы на боевой БД:

```python
        # Позиционный трекер удалён: с датацентрового IP Kaspi отдавал 429, на
        # VPS таблица не наполнялась. Сносим явно, иначе она вечно висит в
        # боевой БД мёртвым грузом.
        self._conn.execute("DROP TABLE IF EXISTS position_snapshots")
```

Удалить целиком блок методов от комментария `# ---- снапшоты позиций ----` (строка 470) до конца файла: `put_position_snapshot`, `get_position_series`, `get_latest_position`, `list_tracked_pairs`.

- [ ] **Step 4: Вычистить `worker.py`**

Удалить строку `import json` (строка 19) — после удаления `run_position_tick` других использований не остаётся.

Удалить строку `from connectors.search_client import fetch_listing` (строка 29).

Удалить функцию `run_position_tick` целиком вместе с её заголовком-комментарием `# ---- тик трекера позиций (органика, HTTP) ----`.

В `main()` удалить загрузку конфига:

```python
    from core.positions_config import load_positions_config
    pos_cfg = load_positions_config(
        os.environ.get("POSITIONS_CONFIG", "config/positions.yaml"))
```

И весь блок регистрации джоба — от комментария `# Позиционный трекер шлём отдельным флагом:` до `log.info("Позиционный job ВЫКЛЮЧЕН (POSITIONS_ENABLED=0)")` включительно.

Финальный `log.info` о старте привести к виду без позиций:

```python
    log.info("Автопилот запущен (dry_run=%s, кампании=%s). Расписания: revenue/60м, "
             "fast/5м, slow/9,12,15,18,21%s (Алматы)",
             cfg_holder["cfg"].dry_run, cfg_holder["cfg"].campaign_ids or env_ids or "все активные",
             ", analyst/22:00" if analyst_enabled else " (analyst ВЫКЛ)")
```

- [ ] **Step 5: Вычистить веб-панель**

В `webui/app.py` удалить строку `import json` (строка 5) и роут целиком:

```python
    @app.get("/positions", response_class=HTMLResponse)
    def positions(request: Request):
        ...
        return templates.TemplateResponse(request, "positions.html", {
            "user": user(request), "blocks": blocks})
```

В `webui/templates/base.html` удалить строку навигации:

```html
      <a href="/positions">Позиции</a>
```

- [ ] **Step 6: Проверить, что ссылок на фичу не осталось**

```bash
grep -rn "position_snapshot\|search_client\|fetch_listing\|positions_config\|POSITIONS_ENABLED\|KASPI_SEARCH_PROXY" \
     . --exclude-dir=.venv --exclude-dir=.git --exclude-dir=docs
```

Ожидание: **пустой вывод**. Совпадения в `webui/static/app.css` не считаются — там CSS-свойство `position`, к фиче отношения не имеет, поэтому шаблон поиска и не содержит голого слова `position`.

- [ ] **Step 7: Прогнать тесты**

```bash
for t in test_store.py test_worker.py test_webui.py test_rules.py \
         test_revenue.py test_reconcile.py test_daypart.py \
         test_config_resolver.py test_settings_io.py test_marketing.py \
         test_session.py test_analyst.py; do
  echo "--- $t"; .venv/bin/python "$t" >/dev/null 2>&1 && echo OK || echo FAIL
done
```

Ожидание: все `OK`, как на шаге 1.

- [ ] **Step 8: Коммит**

```bash
git add -A
git commit -m "refactor: снести позиционный трекер целиком

С датацентрового IP Kaspi отдавал 429 на веб-каталог, на VPS джоб был
выключен и данных не собирал. Ушли: search_client, positions_config,
config/positions.yaml, run_position_tick, таблица position_snapshots
с методами, роут /positions с шаблоном и пять тестовых файлов.

Таблица сносится через DROP TABLE IF EXISTS при открытии БД — иначе
висела бы в боевой базе мёртвым грузом."
```

---

### Task 2: `products_snapshot` — семь полей обратной связи

`CampaignProduct` уже несёт `views/ctr/cr/crr/gmv/transactions/buy_box`, а `save_products_snapshot` их выбрасывает. Без них не построить ни один график эффективности.

**Files:**
- Modify: `core/store.py` (`_init_schema`: CREATE + миграция; `save_products_snapshot`)
- Test: `test_store.py`

**Interfaces:**
- Consumes: `Store` из Task 1.
- Produces: `Store.get_latest_snapshot(sku) -> dict | None`, где dict теперь содержит ключи `views: int`, `ctr: float`, `cr: float`, `crr: float`, `gmv: float`, `transactions: int`, `buy_box: int` (0/1). Task 6 на них не опирается, но следующий план строит из них график ставки и CPC.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_store.py`. В начало файла добавить `import sqlite3` к существующим импортам.

```python
def test_snapshot_stores_feedback_metrics():
    """Поля обратной связи из кабинета доезжают до БД, а не теряются."""
    s = new_store()
    p = cp(views=4118, ctr=0.034, cr=0.086, crr=0.058, gmv=89655,
           transactions=12, buy_box=True)
    s.save_products_snapshot([p], ts=1000, campaign_id="3032419")

    row = s.get_latest_snapshot("166350900")
    assert row["views"] == 4118, row
    assert row["ctr"] == 0.034, row
    assert row["cr"] == 0.086, row
    assert row["crr"] == 0.058, row
    assert row["gmv"] == 89655, row
    assert row["transactions"] == 12, row
    assert row["buy_box"] == 1, row
    print("✓ снапшот хранит метрики обратной связи")


def test_migration_adds_feedback_columns_to_old_db():
    """Боевая БД со старой схемой доживает миграцию без потери строк."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE products_snapshot (
        ts INTEGER, sku TEXT, merchant_sku TEXT, bid REAL, avg_cpc REAL,
        score REAL, cost REAL, cost_today REAL, clicks INTEGER, carts INTEGER,
        product_state TEXT, price REAL)""")
    conn.execute("INSERT INTO products_snapshot (ts, sku, bid) VALUES (1, 'old', 5)")
    conn.commit()
    conn.close()

    s = Store(path)  # миграция происходит при открытии
    cols = {r["name"] for r in
            s._conn.execute("PRAGMA table_info(products_snapshot)")}
    assert {"campaign_id", "views", "ctr", "cr", "crr", "gmv",
            "transactions", "buy_box"} <= cols, cols

    row = s._conn.execute(
        "SELECT sku, bid FROM products_snapshot WHERE sku='old'").fetchone()
    assert row["sku"] == "old" and row["bid"] == 5, dict(row)
    print("✓ миграция старой БД добавляет колонки и не теряет строки")
```

Дописать оба вызова в список в конце файла, после `test_migration_adds_campaign_id_to_old_db()`.

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_store.py
```

Ожидание: FAIL на `test_snapshot_stores_feedback_metrics` с `IndexError` или `KeyError: 'views'` — колонки ещё нет.

- [ ] **Step 3: Расширить схему и запись**

В `_init_schema`, в `CREATE TABLE IF NOT EXISTS products_snapshot`, дописать колонки:

```sql
            CREATE TABLE IF NOT EXISTS products_snapshot (
                ts INTEGER, sku TEXT, merchant_sku TEXT, bid REAL, avg_cpc REAL,
                score REAL, cost REAL, cost_today REAL, clicks INTEGER, carts INTEGER,
                product_state TEXT, price REAL, campaign_id TEXT,
                views INTEGER, ctr REAL, cr REAL, crr REAL, gmv REAL,
                transactions INTEGER, buy_box INTEGER
            );
```

Существующий блок миграции `products_snapshot` (тот, что добавляет только `campaign_id`) заменить на цикл:

```python
        # Миграция products_snapshot: campaign_id плюс поля обратной связи,
        # которые кабинет отдавал всегда, а мы начали хранить только сейчас.
        # У старых строк они останутся NULL — история метрик начинается с
        # момента выката, задним числом её взять неоткуда.
        snap_cols = {r["name"] for r in
                     self._conn.execute("PRAGMA table_info(products_snapshot)")}
        for col, decl in (("campaign_id", "TEXT"), ("views", "INTEGER"),
                          ("ctr", "REAL"), ("cr", "REAL"), ("crr", "REAL"),
                          ("gmv", "REAL"), ("transactions", "INTEGER"),
                          ("buy_box", "INTEGER")):
            if col not in snap_cols:
                self._conn.execute(
                    f"ALTER TABLE products_snapshot ADD COLUMN {col} {decl}")
        self._conn.commit()
```

`save_products_snapshot` переписать целиком:

```python
    def save_products_snapshot(self, products: list[CampaignProduct], ts: int,
                               campaign_id: str = ""):
        self._conn.executemany(
            """INSERT INTO products_snapshot
               (ts, sku, merchant_sku, bid, avg_cpc, score, cost, cost_today,
                clicks, carts, product_state, price, campaign_id,
                views, ctr, cr, crr, gmv, transactions, buy_box)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(ts, p.sku, p.merchant_sku, p.bid, p.avg_cpc, p.score, p.cost,
              p.cost_today, p.clicks, p.carts, p.product_state, p.price,
              campaign_id, p.views, p.ctr, p.cr, p.crr, p.gmv,
              p.transactions, int(p.buy_box)) for p in products],
        )
        self._conn.commit()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_store.py && .venv/bin/python test_worker.py
```

Ожидание: оба печатают свою строку «✓ Все проверки … прошли».

- [ ] **Step 5: Коммит**

```bash
git add core/store.py test_store.py
git commit -m "feat(store): снапшот хранит views/ctr/cr/crr/gmv/transactions/buy_box

Кабинет отдавал эти поля всегда, а save_products_snapshot их выбрасывал.
Без них не построить графики эффективности. Миграция — ALTER TABLE по
PRAGMA table_info, у старых строк колонки остаются NULL."
```

---

### Task 3: `metrics_daily` — подневные метрики

Единственный честный источник для графиков по дням. `tacos_daily` для этого не годится: он хранит значение скользящего окна на конец дня, а не расход за день.

**Files:**
- Modify: `core/store.py` (докстринг модуля, `_init_schema`, новый блок методов)
- Test: `test_store.py`

**Interfaces:**
- Consumes: `Store` из Task 2.
- Produces:
  - `Store.upsert_metrics_daily(day: str, campaign_id: str, sku: str, merchant_sku: str, cost: float, gmv: float, views: int, clicks: int, carts: int, transactions: int, ctr: float, cr: float, revenue: float | None, ts: int) -> None` — все аргументы именованные, `tacos/roas/roas_gmv` считаются внутри.
  - `Store.get_metrics_series(sku: str, days: int) -> list[dict]` — по возрастанию `day`.
  - `Store.get_metrics_for_day(day: str) -> list[dict]` — по возрастанию `sku`.
  - Task 6 вызывает `upsert_metrics_daily`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_store.py`:

```python
def test_metrics_daily_computes_and_stores_ratios():
    """Производные считаются при записи: графики читают таблицу как есть."""
    s = new_store()
    s.upsert_metrics_daily(
        day="2026-09-12", campaign_id="2899523", sku="166350902",
        merchant_sku="771930155", cost=2800, gmv=14000, views=7333,
        clicks=88, carts=1, transactions=1, ctr=0.012, cr=0.011,
        revenue=11429, ts=1000)

    row = s.get_metrics_for_day("2026-09-12")[0]
    assert abs(row["tacos"] - 2800 / 11429) < 1e-9, row["tacos"]
    assert abs(row["roas"] - 11429 / 2800) < 1e-9, row["roas"]
    assert abs(row["roas_gmv"] - 14000 / 2800) < 1e-9, row["roas_gmv"]
    assert row["clicks"] == 88 and row["carts"] == 1, dict(row)
    print("✓ metrics_daily считает tacos/roas/roas_gmv при записи")


def test_metrics_daily_upsert_overwrites_same_day():
    """Джоб гоняется раз в час и переписывает сегодня — дублей быть не должно."""
    s = new_store()
    common = dict(day="2026-09-12", campaign_id="c1", sku="s1",
                  merchant_sku="m1", gmv=0, views=10, clicks=5, carts=0,
                  transactions=0, ctr=0.5, cr=0.0)
    s.upsert_metrics_daily(cost=100, revenue=1000, ts=1, **common)
    s.upsert_metrics_daily(cost=250, revenue=1000, ts=2, **common)

    rows = s.get_metrics_for_day("2026-09-12")
    assert len(rows) == 1, rows
    assert rows[0]["cost"] == 250 and rows[0]["ts"] == 2, dict(rows[0])
    print("✓ metrics_daily перезаписывает строку того же дня")


def test_metrics_daily_null_only_when_denominator_is_zero():
    """Расход без выручки — это ROAS 0, а не дырка. TACoS при этом не определён."""
    s = new_store()
    s.upsert_metrics_daily(
        day="2026-09-12", campaign_id="c1", sku="s1", merchant_sku="m1",
        cost=500, gmv=0, views=10, clicks=2, carts=0, transactions=0,
        ctr=0.2, cr=0.0, revenue=0, ts=1)
    row = s.get_metrics_for_day("2026-09-12")[0]
    assert row["tacos"] is None, row["tacos"]
    assert row["roas"] == 0.0, row["roas"]
    assert row["roas_gmv"] == 0.0, row["roas_gmv"]

    # Выручка ещё не собрана (Shop API не ходил) — ROAS неизвестен, не ноль.
    s.upsert_metrics_daily(
        day="2026-09-13", campaign_id="c1", sku="s1", merchant_sku="m1",
        cost=500, gmv=1000, views=10, clicks=2, carts=0, transactions=0,
        ctr=0.2, cr=0.0, revenue=None, ts=1)
    row = s.get_metrics_for_day("2026-09-13")[0]
    assert row["roas"] is None, row["roas"]
    assert row["roas_gmv"] == 2.0, row["roas_gmv"]

    # Расхода нет — делить не на что.
    s.upsert_metrics_daily(
        day="2026-09-14", campaign_id="c1", sku="s1", merchant_sku="m1",
        cost=0, gmv=0, views=0, clicks=0, carts=0, transactions=0,
        ctr=0.0, cr=0.0, revenue=300, ts=1)
    row = s.get_metrics_for_day("2026-09-14")[0]
    assert row["roas"] is None and row["roas_gmv"] is None, dict(row)
    assert row["tacos"] == 0.0, row["tacos"]
    print("✓ NULL только там, где знаменатель ноль")


def test_metrics_series_ascending_and_limited():
    """Ряд для графика: свежие N дней, по возрастанию — как рисует ось X."""
    s = new_store()
    for n in range(1, 6):
        s.upsert_metrics_daily(
            day=f"2026-09-0{n}", campaign_id="c1", sku="s1", merchant_sku="m1",
            cost=n * 100, gmv=0, views=0, clicks=0, carts=0, transactions=0,
            ctr=0.0, cr=0.0, revenue=None, ts=n)
    # чужой товар не должен попасть в ряд
    s.upsert_metrics_daily(
        day="2026-09-03", campaign_id="c1", sku="s2", merchant_sku="m2",
        cost=999, gmv=0, views=0, clicks=0, carts=0, transactions=0,
        ctr=0.0, cr=0.0, revenue=None, ts=9)

    series = s.get_metrics_series("s1", days=3)
    assert [r["day"] for r in series] == ["2026-09-03", "2026-09-04", "2026-09-05"], series
    assert [r["cost"] for r in series] == [300, 400, 500], series
    print("✓ ряд metrics_daily отсортирован и ограничен по товару")
```

Дописать четыре вызова в список в конце файла.

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_store.py
```

Ожидание: FAIL с `AttributeError: 'Store' object has no attribute 'upsert_metrics_daily'`.

- [ ] **Step 3: Добавить таблицу и методы**

В докстринг модуля `core/store.py` дописать строку к списку таблиц:

```
  metrics_daily     — подневные метрики товара (CTR/CR/ROAS для графиков);
```

В `_init_schema`, внутрь `executescript`, добавить:

```sql
            -- Подневные метрики товара. Отдельно от tacos_daily: там лежит
            -- значение СКОЛЬЗЯЩЕГО ОКНА на конец дня, а графику нужен именно
            -- день. Наполняется отдельным джобом, который спрашивает кабинет
            -- со StartDate = EndDate = день.
            CREATE TABLE IF NOT EXISTS metrics_daily (
                day          TEXT,
                campaign_id  TEXT,
                sku          TEXT,
                merchant_sku TEXT,
                cost         REAL,
                gmv          REAL,
                views        INTEGER,
                clicks       INTEGER,
                carts        INTEGER,
                transactions INTEGER,
                ctr          REAL,
                cr           REAL,
                revenue      REAL,
                tacos        REAL,
                roas         REAL,
                roas_gmv     REAL,
                ts           INTEGER,
                PRIMARY KEY (day, sku)
            );
            CREATE INDEX IF NOT EXISTS ix_metrics_sku_day
                ON metrics_daily(sku, day);
```

Добавить блок методов (перед блоком `# ---- снапшоты позиций`, который Task 1 уже удалил, — то есть в конец файла):

```python
    # ---- подневные метрики -------------------------------------------------

    def upsert_metrics_daily(self, day: str, campaign_id: str, sku: str,
                             merchant_sku: str, cost: float, gmv: float,
                             views: int, clicks: int, carts: int,
                             transactions: int, ctr: float, cr: float,
                             revenue: float | None, ts: int) -> None:
        """Строка подневных метрик товара. Производные считаем ЗДЕСЬ и храним:
        графики читают таблицу напрямую, пересчитывать их на каждый рендер
        панели незачем.

        NULL пишем только там, где ноль в ЗНАМЕНАТЕЛЕ — величина не определена.
        Расход без выручки даёт ROAS 0.0, и это не дырка, а важный сигнал:
        деньги потратили, продаж нет. revenue=None означает «Shop API за этот
        день ещё не опрашивали» — тогда ROAS именно неизвестен."""
        tacos = (cost / revenue) if revenue else None
        roas = None if (not cost or revenue is None) else (revenue / cost)
        roas_gmv = (gmv / cost) if cost else None
        self._conn.execute(
            """INSERT INTO metrics_daily
               (day, campaign_id, sku, merchant_sku, cost, gmv, views, clicks,
                carts, transactions, ctr, cr, revenue, tacos, roas, roas_gmv, ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(day, sku) DO UPDATE SET
                 campaign_id=excluded.campaign_id,
                 merchant_sku=excluded.merchant_sku,
                 cost=excluded.cost, gmv=excluded.gmv, views=excluded.views,
                 clicks=excluded.clicks, carts=excluded.carts,
                 transactions=excluded.transactions, ctr=excluded.ctr,
                 cr=excluded.cr, revenue=excluded.revenue,
                 tacos=excluded.tacos, roas=excluded.roas,
                 roas_gmv=excluded.roas_gmv, ts=excluded.ts""",
            (day, campaign_id, sku, merchant_sku, cost, gmv, views, clicks,
             carts, transactions, ctr, cr, revenue, tacos, roas, roas_gmv, ts),
        )
        self._conn.commit()

    def get_metrics_series(self, sku: str, days: int) -> list[dict]:
        """Последние `days` дней по товару, по возрастанию дня — как ось X."""
        rows = self._conn.execute(
            "SELECT * FROM ("
            "  SELECT * FROM metrics_daily WHERE sku=? ORDER BY day DESC LIMIT ?"
            ") ORDER BY day ASC",
            (sku, days),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_metrics_for_day(self, day: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM metrics_daily WHERE day=? ORDER BY sku", (day,)
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_store.py
```

Ожидание: PASS, включая четыре новые строки с «✓».

- [ ] **Step 5: Коммит**

```bash
git add core/store.py test_store.py
git commit -m "feat(store): таблица metrics_daily с подневными метриками

tacos_daily для графиков не годится: там значение скользящего окна на
конец дня, а не расход за день. Новая таблица хранит честный день и
посчитанные при записи tacos/roas/roas_gmv.

NULL только при нуле в знаменателе. Расход без выручки — это ROAS 0.0,
а не дырка в графике (спек тут говорил NULL, это была ошибка)."
```

---

### Task 4: `ai_insights` — кэш разборов

Таблица заводится сейчас, вместе с остальной схемой, чтобы третий план занимался только промптами и эндпоинтами.

**Files:**
- Modify: `core/store.py` (докстринг модуля, `_init_schema`, новый блок методов)
- Test: `test_store.py`

**Interfaces:**
- Consumes: `Store` из Task 3.
- Produces:
  - `Store.put_ai_insight(kind: str, scope_id: str, day: str, ts: int, text: str, model: str, tokens_in: int, tokens_out: int) -> None`
  - `Store.get_ai_insight(kind: str, scope_id: str, day: str) -> dict | None`
  - `Store.count_ai_calls(day: str) -> int` — сумма `calls`, а не число строк.
  - Всё это потребляет третий план (эндпоинты ИИ).

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_store.py`:

```python
def test_ai_insight_roundtrip_and_overwrite():
    s = new_store()
    s.put_ai_insight(kind="product", scope_id="166350902", day="2026-09-12",
                     ts=100, text="первый разбор", model="claude-opus-5",
                     tokens_in=4000, tokens_out=1800)
    got = s.get_ai_insight("product", "166350902", "2026-09-12")
    assert got["text"] == "первый разбор", got
    assert got["model"] == "claude-opus-5" and got["tokens_in"] == 4000, got

    s.put_ai_insight(kind="product", scope_id="166350902", day="2026-09-12",
                     ts=200, text="пересчитали", model="claude-opus-5",
                     tokens_in=4200, tokens_out=1900)
    got = s.get_ai_insight("product", "166350902", "2026-09-12")
    assert got["text"] == "пересчитали" and got["ts"] == 200, got

    assert s.get_ai_insight("product", "нет-такого", "2026-09-12") is None
    print("✓ ai_insights: запись, перезапись, промах")


def test_count_ai_calls_counts_forced_recomputes():
    """Лимит расхода должен видеть принудительные пересчёты, иначе протекает:
    force перезаписывает строку, а деньги за вызов уже заплачены."""
    s = new_store()
    for ts in (1, 2, 3):
        s.put_ai_insight(kind="product", scope_id="s1", day="2026-09-12",
                         ts=ts, text="x", model="m", tokens_in=1, tokens_out=1)
    s.put_ai_insight(kind="daily", scope_id="", day="2026-09-12",
                     ts=4, text="y", model="m", tokens_in=1, tokens_out=1)
    s.put_ai_insight(kind="product", scope_id="s1", day="2026-09-13",
                     ts=5, text="z", model="m", tokens_in=1, tokens_out=1)

    assert s.count_ai_calls("2026-09-12") == 4, s.count_ai_calls("2026-09-12")
    assert s.count_ai_calls("2026-09-13") == 1
    assert s.count_ai_calls("2026-01-01") == 0
    print("✓ count_ai_calls считает вызовы, а не строки")
```

Дописать оба вызова в список в конце файла.

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_store.py
```

Ожидание: FAIL с `AttributeError: 'Store' object has no attribute 'put_ai_insight'`.

- [ ] **Step 3: Добавить таблицу и методы**

В докстринг модуля дописать:

```
  ai_insights       — разборы LLM-аналитика (кэш + счётчик расхода).
```

В `_init_schema`, внутрь `executescript`:

```sql
            -- Разборы аналитика. Ключ (kind, scope_id, day) — один разбор на
            -- товар в день; повторный вызов перезаписывает текст, но
            -- инкрементит calls: за пересчёт уже заплачено, и лимит расхода
            -- обязан его видеть.
            CREATE TABLE IF NOT EXISTS ai_insights (
                kind       TEXT,
                scope_id   TEXT,
                day        TEXT,
                ts         INTEGER,
                text       TEXT,
                model      TEXT,
                tokens_in  INTEGER,
                tokens_out INTEGER,
                calls      INTEGER DEFAULT 1,
                PRIMARY KEY (kind, scope_id, day)
            );
```

Добавить блок методов в конец файла:

```python
    # ---- разборы аналитика -------------------------------------------------

    def put_ai_insight(self, kind: str, scope_id: str, day: str, ts: int,
                       text: str, model: str, tokens_in: int,
                       tokens_out: int) -> None:
        """kind: 'product' (scope_id = sku) либо 'daily' (scope_id = '')."""
        self._conn.execute(
            """INSERT INTO ai_insights
               (kind, scope_id, day, ts, text, model, tokens_in, tokens_out, calls)
               VALUES (?,?,?,?,?,?,?,?,1)
               ON CONFLICT(kind, scope_id, day) DO UPDATE SET
                 ts=excluded.ts, text=excluded.text, model=excluded.model,
                 tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out,
                 calls=ai_insights.calls + 1""",
            (kind, scope_id, day, ts, text, model, tokens_in, tokens_out),
        )
        self._conn.commit()

    def get_ai_insight(self, kind: str, scope_id: str, day: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM ai_insights WHERE kind=? AND scope_id=? AND day=?",
            (kind, scope_id, day),
        ).fetchone()
        return dict(row) if row else None

    def count_ai_calls(self, day: str) -> int:
        """Сколько платных вызовов сделано за день — предохранитель
        AI_DAILY_LIMIT. Считаем сумму calls, а не число строк: иначе
        принудительные пересчёты одного товара были бы бесплатны для лимита."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(calls), 0) AS n FROM ai_insights WHERE day=?",
            (day,),
        ).fetchone()
        return row["n"]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_store.py
```

Ожидание: PASS с двумя новыми «✓».

- [ ] **Step 5: Коммит**

```bash
git add core/store.py test_store.py
git commit -m "feat(store): таблица ai_insights — кэш разборов и счётчик расхода

Колонка calls инкрементится при перезаписи: спек считал лимит по числу
строк, но force перезаписывает строку, и принудительные пересчёты были
бы для лимита бесплатными, хотя деньги за них уже заплачены."
```

---

### Task 5: `RevenueCollector.collect_for_day`

Реальная выручка за один календарный день. `collect` умеет только скользящее окно «N дней включая сегодня».

**Files:**
- Modify: `core/revenue.py` (новая `almaty_day_ms`; `collect` разбирается на `_collect_range`; новый `collect_for_day`)
- Test: `test_revenue.py`

**Interfaces:**
- Consumes: `MerchantClient.iter_orders(start_ms, end_ms)` и `get_order_entries(order_id)` — уже существуют.
- Produces:
  - `core.revenue.almaty_day_ms(day: str) -> tuple[int, int]`
  - `RevenueCollector.collect_for_day(day: str) -> dict[str, SkuRevenue]`
  - Task 6 вызывает `collect_for_day`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_revenue.py`. Импорт в начале файла расширить:

```python
from core.revenue import RevenueCollector, almaty_window_ms, almaty_day_ms
```

```python
def test_almaty_day_ms_covers_exactly_one_day():
    """Границы дня по Алматы, конец — последняя миллисекунда суток.
    Полуинтервал важен: с включающей верхней границей заказ, созданный ровно
    в полночь, попал бы сразу в два дня и выручка задвоилась бы."""
    start_ms, end_ms = almaty_day_ms("2026-09-12")
    assert end_ms - start_ms == 24 * 3600 * 1000 - 1, (start_ms, end_ms)

    start = datetime.fromtimestamp(start_ms / 1000, ALMATY)
    assert (start.year, start.month, start.day) == (2026, 9, 12), start
    assert (start.hour, start.minute, start.second) == (0, 0, 0), start

    next_start, _ = almaty_day_ms("2026-09-13")
    assert end_ms + 1 == next_start, (end_ms, next_start)
    print("✓ almaty_day_ms: ровно одни сутки Алматы, без нахлёста")


def test_collect_for_day_counts_only_that_day():
    """Тот же фейковый merchant, что и у окна: 8 августа продан один товар
    на 59900 и один на 57490, заказы 9-го в этот день попасть не должны."""
    collector = RevenueCollector(FakeMerchant())
    got = collector.collect_for_day("2026-08-08")

    assert set(got) == {"608122048", "743062317"}, got
    assert got["608122048"].revenue == 59900, got["608122048"]
    assert got["743062317"].revenue == 57490, got["743062317"]
    assert "432085472" not in got, "заказы 9 августа не должны попасть в 8-е"
    print("✓ collect_for_day берёт ровно один день")


def test_collect_for_day_subtracts_cancellations():
    """9 августа: один зачтённый заказ на 48900 и одна отмена на ту же сумму."""
    collector = RevenueCollector(FakeMerchant())
    got = collector.collect_for_day("2026-08-09")

    rec = got["432085472"]
    assert rec.revenue == 48900, rec
    assert rec.cancelled == 48900, rec
    assert rec.gross_revenue == 97800, rec
    print("✓ collect_for_day вычитает отмены так же, как окно")
```

Дописать три вызова в список в конце файла.

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_revenue.py
```

Ожидание: FAIL уже на импорте — `ImportError: cannot import name 'almaty_day_ms'`.

- [ ] **Step 3: Реализовать**

В `core/revenue.py` расширить импорт даты:

```python
from datetime import datetime, timedelta, time as dtime, date
```

Сразу после `almaty_window_ms` добавить:

```python
def almaty_day_ms(day: str) -> tuple[int, int]:
    """(start_ms, end_ms) для КОНКРЕТНОЙ даты YYYY-MM-DD по Алматы.

    Отдельная функция, а не частный случай almaty_window_ms: та считает
    скользящее окно «N дней, включая сегодня, до сейчас», а подневным
    метрикам нужны ровно одни закрытые сутки.

    Верхняя граница — последняя миллисекунда суток, а не полночь следующих:
    Kaspi отдаёт заказы по включающему диапазону, и заказ, созданный ровно
    в 00:00:00.000, иначе попал бы в оба дня и задвоил выручку.
    """
    d = date.fromisoformat(day)
    start = datetime.combine(d, dtime.min, tzinfo=ALMATY)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000) - 1
```

Тело `collect` вынести в `_collect_range`. Метод `collect` становится таким:

```python
    def collect(self, window_days: int = 2, now: datetime | None = None) -> dict[str, SkuRevenue]:
        start_ms, end_ms = almaty_window_ms(window_days, now=now)
        log.info("Сбор выручки: окно %sд, [%s .. %s]", window_days, start_ms, end_ms)
        return self._collect_range(start_ms, end_ms)

    def collect_for_day(self, day: str) -> dict[str, SkuRevenue]:
        """Выручка по merchantSku за один календарный день Алматы —
        источник колонки revenue в metrics_daily."""
        start_ms, end_ms = almaty_day_ms(day)
        log.info("Сбор выручки за %s, [%s .. %s]", day, start_ms, end_ms)
        return self._collect_range(start_ms, end_ms)

    def _collect_range(self, start_ms: int, end_ms: int) -> dict[str, SkuRevenue]:
```

Тело `_collect_range` — существующий код `collect` начиная со строки `result: dict[str, SkuRevenue] = defaultdict(...)` и до `return dict(result)` включительно, без изменений.

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_revenue.py && .venv/bin/python test_worker.py
```

Ожидание: оба зелёные. `test_worker.py` важен: он гоняет `run_revenue_cycle`, и рефакторинг `collect` не должен был его задеть.

- [ ] **Step 5: Коммит**

```bash
git add core/revenue.py test_revenue.py
git commit -m "feat(revenue): collect_for_day — выручка за один день Алматы

collect умел только скользящее окно. Подневным метрикам нужны закрытые
сутки. Общее тело вынесено в _collect_range, collect не изменился по
поведению. Верхняя граница дня — последняя миллисекунда, иначе заказ
ровно в полночь попал бы в два дня и задвоил выручку."
```

---

### Task 6: Джоб подневных метрик

Связывает всё предыдущее: спрашивает кабинет по дню, берёт выручку за тот же день, пишет `metrics_daily`.

**Files:**
- Modify: `worker.py` (новая `run_daily_metrics_cycle`; регистрация джоба в `main()`; строка лога о старте)
- Modify: `config/.env.example` (новый флаг)
- Modify: `deploy/DEPLOY.md` (раздел про флаг)
- Test: `test_worker.py`

**Interfaces:**
- Consumes: `Store.upsert_metrics_daily` (Task 3), `RevenueCollector.collect_for_day` (Task 5), `WorkerContext`, `MarketingClient.list_active_campaigns`, `MarketingClient.get_campaign_products`.
- Produces: `worker.run_daily_metrics_cycle(ctx: WorkerContext, days_back: int = 1) -> int` — возвращает число записанных строк.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_worker.py`. Посмотри, как в этом файле уже устроены фейки маркетинга и стора (`test_run_cycle_ticks_every_active_campaign`), и переиспользуй их стиль; ниже — самодостаточные фейки, чтобы тест не зависел от чужих.

```python
def test_daily_metrics_asks_marketing_per_day_and_writes_rows():
    """Кабинет отдаёт счётчики за ПЕРИОД запроса, поэтому за честный день
    спрашиваем StartDate = EndDate = этот день. Проверяем именно это."""
    from worker import run_daily_metrics_cycle

    asked = []

    class FakeMarketing:
        def list_active_campaigns(self, start_date, end_date):
            return [Campaign(id="c1", name="Кампания", state="Enabled",
                             daily_budget=40000)]

        def get_campaign_products(self, campaign_id, start_date, end_date):
            asked.append((campaign_id, start_date, end_date))
            return [cp(sku="s1", merchant_sku="m1", cost=500, gmv=4000,
                       views=1000, clicks=50, carts=3, transactions=2,
                       ctr=0.05, cr=0.06)]

    class FakeCollector:
        def collect_for_day(self, day):
            return {"m1": SkuRevenue(merchant_sku="m1", revenue=5000)}

    store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    ctx = WorkerContext(
        marketing=FakeMarketing(), store=store, cfg=RulesConfig(),
        revenue_collector=FakeCollector(),
        now_fn=lambda: datetime(2026, 9, 12, 14, 0, tzinfo=ALMATY))

    written = run_daily_metrics_cycle(ctx, days_back=1)

    assert written == 2, written
    # вчера, затем сегодня; StartDate всегда равен EndDate
    assert asked == [("c1", "2026-09-11", "2026-09-11"),
                     ("c1", "2026-09-12", "2026-09-12")], asked

    row = store.get_metrics_for_day("2026-09-12")[0]
    assert row["sku"] == "s1" and row["cost"] == 500, row
    assert row["revenue"] == 5000, row
    assert abs(row["roas"] - 10.0) < 1e-9, row["roas"]
    print("✓ джоб метрик спрашивает кабинет подневно и пишет строки")


def test_daily_metrics_fetches_revenue_once_per_day():
    """Выручка за день общая для всех кампаний — тяжёлый обход Shop API
    не должен повторяться на каждую кампанию."""
    from worker import run_daily_metrics_cycle

    calls = []

    class FakeMarketing:
        def list_active_campaigns(self, start_date, end_date):
            return [Campaign(id="c1", name="A", state="Enabled"),
                    Campaign(id="c2", name="B", state="Enabled")]

        def get_campaign_products(self, campaign_id, start_date, end_date):
            return [cp(sku=f"{campaign_id}-s", merchant_sku="m1")]

    class FakeCollector:
        def collect_for_day(self, day):
            calls.append(day)
            return {}

    store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    ctx = WorkerContext(
        marketing=FakeMarketing(), store=store, cfg=RulesConfig(),
        revenue_collector=FakeCollector(),
        now_fn=lambda: datetime(2026, 9, 12, 14, 0, tzinfo=ALMATY))

    run_daily_metrics_cycle(ctx, days_back=1)

    assert calls == ["2026-09-11", "2026-09-12"], calls
    print("✓ выручка за день собирается один раз на все кампании")


def test_daily_metrics_isolates_failing_campaign():
    """Падение одной кампании не должно ронять джоб — планировщик живёт."""
    from worker import run_daily_metrics_cycle

    class FakeMarketing:
        def list_active_campaigns(self, start_date, end_date):
            return [Campaign(id="bad", name="A", state="Enabled"),
                    Campaign(id="good", name="B", state="Enabled")]

        def get_campaign_products(self, campaign_id, start_date, end_date):
            if campaign_id == "bad":
                raise RuntimeError("кабинет отвалился")
            return [cp(sku="ok", merchant_sku="m1")]

    class FakeCollector:
        def collect_for_day(self, day):
            return {}

    store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    ctx = WorkerContext(
        marketing=FakeMarketing(), store=store, cfg=RulesConfig(),
        revenue_collector=FakeCollector(),
        now_fn=lambda: datetime(2026, 9, 12, 14, 0, tzinfo=ALMATY))

    written = run_daily_metrics_cycle(ctx, days_back=0)

    assert written == 1, written
    assert [r["sku"] for r in store.get_metrics_for_day("2026-09-12")] == ["ok"]
    print("✓ падение одной кампании не роняет джоб метрик")


def test_daily_metrics_survives_unavailable_campaign_list():
    """Список кампаний недоступен — цикл пропускаем, как это делает run_cycle."""
    from worker import run_daily_metrics_cycle

    class FakeMarketing:
        def list_active_campaigns(self, start_date, end_date):
            raise RuntimeError("сеть легла")

    store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    ctx = WorkerContext(marketing=FakeMarketing(), store=store, cfg=RulesConfig(),
                        revenue_collector=None,
                        now_fn=lambda: datetime(2026, 9, 12, tzinfo=ALMATY))

    assert run_daily_metrics_cycle(ctx) == 0
    print("✓ недоступный список кампаний не роняет джоб метрик")
```

Импорты дописывать не нужно: `os`, `tempfile`, `Campaign`, `SkuRevenue`, `Store`, `RulesConfig` и `ALMATY` в `test_worker.py` уже есть. Единственное новое — `run_daily_metrics_cycle`, и он импортируется внутри каждого теста (как показано), чтобы падение импорта било по конкретному тесту, а не по всему файлу.

Дописать четыре вызова в список в конце файла.

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_worker.py
```

Ожидание: FAIL с `ImportError: cannot import name 'run_daily_metrics_cycle' from 'worker'`.

- [ ] **Step 3: Реализовать джоб**

В `worker.py`, после `run_revenue_cycle`, добавить:

```python
# ---- подневные метрики (источник графиков панели) --------------------------

def run_daily_metrics_cycle(ctx: WorkerContext, days_back: int = 1) -> int:
    """Наполняет metrics_daily за сегодня и `days_back` предыдущих дней.

    Зачем отдельный джоб, а не данные из тика: кабинет отдаёт views/clicks/
    carts/gmv/ctr/cr за ПЕРИОД запроса целиком, а тик спрашивает окно
    tacos_window_days. Честный день получается только запросом со
    StartDate = EndDate = этот день.

    Вчера пересчитываем каждый час: заказы отменяют задним числом, и выручка
    вчерашнего дня продолжает меняться ещё сутки.

    Изоляция как в run_cycle: недоступный список кампаний → пропускаем цикл,
    падение одной кампании → логируем и идём дальше. Планировщик не роняем.
    """
    now = ctx.now_fn().astimezone(ALMATY)
    ts = int(now.timestamp())
    today = now.date()

    probe = today.isoformat()
    try:
        campaigns = ctx.marketing.list_active_campaigns(probe, probe)
    except Exception as e:  # noqa: BLE001 — сеть/кабинет не должны ронять джоб
        log.error("Список кампаний недоступен, джоб метрик пропущен: %s", e)
        return 0

    allow = set(ctx.campaign_ids or [])
    if allow:
        campaigns = [c for c in campaigns if c.id in allow]

    written = 0
    for back in range(days_back, -1, -1):
        day = (today - timedelta(days=back)).isoformat()
        # Выручка за день общая для всех кампаний — обход Shop API тяжёлый,
        # дёргаем его один раз на день, а не на каждую кампанию.
        try:
            revenue = ctx.revenue_collector.collect_for_day(day)
        except Exception as e:  # noqa: BLE001
            log.error("Выручка за %s недоступна, метрики без неё: %s", day, e)
            revenue = {}

        for c in campaigns:
            try:
                products = ctx.marketing.get_campaign_products(c.id, day, day)
            except Exception as e:  # noqa: BLE001
                log.error("Метрики кампании %s за %s не собраны: %s", c.id, day, e)
                continue

            for p in products:
                r = revenue.get(p.merchant_sku)
                ctx.store.upsert_metrics_daily(
                    day=day, campaign_id=c.id, sku=p.sku,
                    merchant_sku=p.merchant_sku, cost=p.cost, gmv=p.gmv,
                    views=p.views, clicks=p.clicks, carts=p.carts,
                    transactions=p.transactions, ctr=p.ctr, cr=p.cr,
                    revenue=r.revenue if r else None, ts=ts)
                written += 1

    log.info("Подневные метрики: дней=%s, кампаний=%s, строк=%s",
             days_back + 1, len(campaigns), written)
    return written
```

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_worker.py
```

Ожидание: PASS с четырьмя новыми «✓».

- [ ] **Step 5: Зарегистрировать джоб в планировщике**

В `main()`, рядом с остальными `sched.add_job`, добавить:

```python
    # Подневные метрики для графиков панели. Отдельным флагом, как и аналитик:
    # джоб ходит в кабинет и Shop API чаще прочих, и его нужно уметь погасить,
    # не трогая ставочные контуры.
    daily_metrics_enabled = os.environ.get("DAILY_METRICS_ENABLED", "1") != "0"
    if daily_metrics_enabled:
        sched.add_job(lambda: run_daily_metrics_cycle(build_ctx()),
                      "interval", minutes=60, id="daily_metrics",
                      max_instances=1, coalesce=True,
                      next_run_time=datetime.now(ALMATY))
    else:
        log.info("Джоб подневных метрик ВЫКЛЮЧЕН (DAILY_METRICS_ENABLED=0)")
```

Строку лога о старте дополнить:

```python
    log.info("Автопилот запущен (dry_run=%s, кампании=%s). Расписания: revenue/60м, "
             "fast/5м, slow/9,12,15,18,21%s%s (Алматы)",
             cfg_holder["cfg"].dry_run, cfg_holder["cfg"].campaign_ids or env_ids or "все активные",
             ", analyst/22:00" if analyst_enabled else " (analyst ВЫКЛ)",
             ", metrics/60м" if daily_metrics_enabled else " (metrics ВЫКЛ)")
```

- [ ] **Step 6: Задокументировать флаг**

В `config/.env.example`, после блока LLM-аналитика, добавить:

```
# --- Подневные метрики для графиков панели ---
# Раз в час опрашивает кабинет по каждому дню отдельно (сегодня и вчера) и
# складывает CTR/CR/ROAS в metrics_daily. 0 — выключить, графики перестанут
# наполняться, ставочные контуры не затронуты.
DAILY_METRICS_ENABLED=1
```

В `deploy/DEPLOY.md`, в раздел про `config/.env`, дописать строку к списку переменных:

```
- `DAILY_METRICS_ENABLED=1` — часовой сбор подневных метрик для графиков
  панели. Ставит +2 запроса в кабинет и +2 в Shop API в час на кампанию.
```

- [ ] **Step 7: Проверить, что воркер импортируется и схема поднимается**

```bash
.venv/bin/python -c "import worker; print('worker ok')"
.venv/bin/python -c "
from core.store import Store
s = Store('db/dev_seed.db')
cols = {r['name'] for r in s._conn.execute('PRAGMA table_info(products_snapshot)')}
print('snapshot cols ok:', {'views','ctr','cr','crr','gmv','transactions','buy_box'} <= cols)
tabs = {r[0] for r in s._conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}
print('metrics_daily:', 'metrics_daily' in tabs, '| ai_insights:', 'ai_insights' in tabs)
print('position_snapshots снесена:', 'position_snapshots' not in tabs)
s.close()"
```

Ожидание: `worker ok`, затем три строки со всеми `True`. Это проверка миграции на реальной БД с данными, а не на пустой.

- [ ] **Step 8: Прогнать весь набор тестов**

```bash
for t in test_store.py test_worker.py test_webui.py test_rules.py \
         test_revenue.py test_reconcile.py test_daypart.py \
         test_config_resolver.py test_settings_io.py test_marketing.py \
         test_session.py test_analyst.py; do
  echo "--- $t"; .venv/bin/python "$t" >/dev/null 2>&1 && echo OK || echo FAIL
done
```

Ожидание: все `OK`.

- [ ] **Step 9: Коммит**

```bash
git add worker.py test_worker.py config/.env.example deploy/DEPLOY.md
git commit -m "feat(worker): часовой джоб подневных метрик для графиков панели

Кабинет отдаёт счётчики за период запроса целиком, поэтому честный день
получается только запросом StartDate = EndDate = день. Джоб гоняет сегодня
и вчера (заказы отменяют задним числом), выручку за день собирает один раз
на все кампании, падение кампании не роняет цикл.

Гасится флагом DAILY_METRICS_ENABLED, ставочные контуры не затрагивает."
```

---

## Что этот план НЕ делает

- Не трогает `webui` дальше удаления роута `/positions` — вся панель остаётся на Jinja и работает как прежде.
- Не чинит `max_tokens=2000` в `analyst.py` — это третий план, вместе с остальным ИИ.
- Не пишет ни одного эндпоинта и ни одной строчки фронта.
- Не выкатывает ничего на VPS. После мержа `metrics_daily` начнёт наполняться сама, и к моменту готовности графиков там уже будет история.

## Готовность к следующему плану

После этого плана в БД есть: расширенный `products_snapshot` (ряд ставки и CPC по тикам), `metrics_daily` (ряды TACoS/CTR/CR по дням), `ai_insights` (кэш разборов). Ровно то, что `GET /api/products/{cid}/{sku}/series` из спека должен отдавать во фронт.
