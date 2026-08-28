# Глобальный настраиваемый период TACoS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать окно расчёта TACoS (`window_days`) глобально настраиваемым параметром биддера, редактируемым из веб-панели, с пресетами 2/7/14/30 дней и произвольным числом.

**Architecture:** Новое поле `tacos_window_days` в `RulesConfig` (живёт в `config/rules.yaml`, hot-reload каждый цикл). Валидируется и пишется через существующий `settings_io`. Воркер прокидывает его в `WorkerContext.window_days` в `build_ctx()` — так revenue-цикл и окно расхода начинают считать за выбранный период. Дашборд читает значение и подписывает секцию TACoS.

**Tech Stack:** Python 3.11, FastAPI + Jinja2 (webui), dataclasses, PyYAML. Тесты — плейн-скрипты (НЕ pytest): каждый файл `test_*.py` с функциями `test_*` и `__main__`-блоком, запускается `python test_x.py`.

## Global Constraints

- **Тесты — плейн-скрипты, не pytest.** Каждый новый тест — функция `test_*()` с `assert` и `print("✓ ...")`, добавляется в `__main__`-блок соответствующего файла. Запуск: `python test_settings_io.py` / `python test_worker.py`.
- **Дефолт `tacos_window_days = 2`** — сохраняет текущее поведение при отсутствии значения в yaml.
- **Диапазон валидации: целое число [1, 90].** < 1, > 90 или дробное → ошибка.
- Значения строк ошибок и меток — на русском, в стиле существующих полей.
- Окружение: активировать `.venv` перед запуском тестов (`source .venv/bin/activate`).

---

### Task 1: Поле `tacos_window_days` в конфиге и его валидация/запись

**Files:**
- Modify: `core/rules.py` (dataclass `RulesConfig`, ~строки 26–41)
- Modify: `core/settings_io.py` (`SETTINGS_FIELDS`, `validate_settings`, `save_settings`)
- Modify: `config/rules.yaml` (документирующая запись дефолта)
- Test: `test_settings_io.py` (новые функции + `__main__`)

**Interfaces:**
- Produces: `RulesConfig.tacos_window_days: int` (дефолт 2); `"tacos_window_days"` в `SETTINGS_FIELDS`; валидатор отвергает [<1, >90, дробное]; `save_settings` пишет его как `int`.

- [ ] **Step 1: Написать падающие тесты в `test_settings_io.py`**

Добавить три функции:

```python
def test_tacos_window_days_default_valid():
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    assert "tacos_window_days" in base, "поле не попало в SETTINGS_FIELDS"
    assert base["tacos_window_days"] == 2
    assert validate_settings(base) == []
    print("✓ settings: tacos_window_days есть в полях, дефолт 2 валиден")


def test_tacos_window_days_rejects_bad():
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    assert any("tacos_window_days" in e for e in validate_settings(dict(base, tacos_window_days=0)))
    assert any("tacos_window_days" in e for e in validate_settings(dict(base, tacos_window_days=91)))
    assert any("tacos_window_days" in e for e in validate_settings(dict(base, tacos_window_days=2.5)))
    assert any("tacos_window_days" in e for e in validate_settings(dict(base, tacos_window_days="abc")))
    print("✓ settings: tacos_window_days отвергает <1, >90, дробное, не-число")


def test_tacos_window_days_roundtrip():
    import tempfile, os
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    data = dict(base, tacos_window_days=7)
    path = os.path.join(tempfile.mkdtemp(), "rules.yaml")
    save_settings(path, data)
    assert load_settings(path)["tacos_window_days"] == 7
    assert load_rules_config(path).tacos_window_days == 7   # воркерский загрузчик понимает
    print("✓ settings: tacos_window_days переживает save→load и читается воркером")
```

И вызвать их в `__main__`-блоке (добавить три строки перед финальным `print`).

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `source .venv/bin/activate && python test_settings_io.py`
Expected: FAIL — `KeyError`/`AssertionError`, т.к. поля `tacos_window_days` ещё нет в `SETTINGS_FIELDS`/`RulesConfig`.

- [ ] **Step 3: Добавить поле в `RulesConfig`**

В `core/rules.py`, в датакласс `RulesConfig` (рядом с TACoS-порогами, после `target_tacos_high`):

```python
    tacos_window_days: int = 2   # окно расчёта TACoS (дни) для решений биддера
```

- [ ] **Step 4: Добавить поле в `settings_io.py`**

В `SETTINGS_FIELDS` вставить `"tacos_window_days"` сразу после `"target_tacos_high"`:

```python
SETTINGS_FIELDS = [
    "target_tacos_low", "target_tacos_high", "tacos_window_days",
    "daily_sku_cost_limit", "sku_budget_fraction",
    "min_clicks_for_no_cart_cut", "cpc_spike_pct",
    "max_bid_step", "max_changes_per_day",
    "bid_ceiling", "min_bid", "min_score_for_raise",
    "bid_step_pct", "cpc_headroom", "pace_tolerance",
    "dry_run", "campaign_ids",
]
```

В `validate_settings`, после блока чтения `num(...)`-переменных, добавить проверку (целое в [1, 90]):

```python
    tw = data.get("tacos_window_days")
    try:
        tw_f = float(tw)
        if not math.isfinite(tw_f):
            errs.append("tacos_window_days: не конечное число")
        elif tw_f != int(tw_f):
            errs.append("tacos_window_days: должно быть целым числом дней")
        elif not (1 <= int(tw_f) <= 90):
            errs.append("tacos_window_days: должно быть в диапазоне 1..90 дней")
    except (TypeError, ValueError):
        errs.append("tacos_window_days: не число")
```

В `save_settings`, в словарь `out`, добавить строку (рядом с TACoS-порогами):

```python
        "tacos_window_days": int(float(data["tacos_window_days"])),
```

- [ ] **Step 5: Дописать документирующий дефолт в `config/rules.yaml`**

Под секцией «Коридор окупаемости», после `target_tacos_high`:

```yaml
tacos_window_days: 2       # за сколько дней считать TACoS для решений биддера (1..90)
```

- [ ] **Step 6: Запустить тесты — убедиться, что проходят**

Run: `source .venv/bin/activate && python test_settings_io.py`
Expected: PASS — все проверки settings_io, включая три новые.

- [ ] **Step 7: Commit**

```bash
git add core/rules.py core/settings_io.py config/rules.yaml test_settings_io.py
git commit -m "feat(config): глобальный tacos_window_days (окно TACoS) с валидацией 1..90

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01V8jnr7RZfsxu9yTUuuWxpL"
```

---

### Task 2: Воркер читает окно из конфига

**Files:**
- Modify: `worker.py` (`build_ctx()`, ~строки 304–306)
- Test: `test_worker.py` (новая функция + `__main__`)

**Interfaces:**
- Consumes: `RulesConfig.tacos_window_days` (из Task 1).
- Produces: `WorkerContext.window_days == cfg.tacos_window_days`; `run_revenue_cycle` обходит выручку за это окно.

- [ ] **Step 1: Написать падающий тест в `test_worker.py`**

Добавить функцию (использует существующий `NOW` и `Store`; `CapturingCollector` фиксирует переданное окно):

```python
def test_revenue_cycle_uses_cfg_window_days():
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "w.db"))

    class CapturingCollector:
        def __init__(self):
            self.seen = None
        def collect(self, window_days=2, now=None):
            self.seen = window_days
            return {}

    cc = CapturingCollector()
    c = WorkerContext(
        marketing=None, store=st,
        cfg=RulesConfig(dry_run=True, tacos_window_days=7),
        window_days=RulesConfig(tacos_window_days=7).tacos_window_days,
        revenue_collector=cc, now_fn=NOW,
    )
    run_revenue_cycle(c)
    assert cc.seen == 7, f"ожидали окно 7, воркер передал {cc.seen}"
    print("✓ worker: revenue-цикл считает выручку за cfg.tacos_window_days")
```

Вызвать её в `__main__`-блоке.

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `source .venv/bin/activate && python test_worker.py`
Expected: FAIL — `AssertionError` невозможен на этом шаге только если поле уже прокинуто; тест падает раньше, т.к. `RulesConfig(tacos_window_days=7)` уже валиден после Task 1, но `build_ctx` в реальном коде ещё не прокидывает окно. (Сам тест конструирует `WorkerContext` напрямую и должен ПРОЙТИ уже здесь — он фиксирует контракт; красная фаза для этого таска — Step 3-тест ниже про `build_ctx`.)

> Примечание: этот тест проверяет контракт `run_revenue_cycle`, который уже работает. Реальная правка — в `build_ctx`, который в юнит-тестах не вызывается (лезет в env/сеть). Поэтому красным индикатором служит проверка в Step 4 глазами + grep, а не отдельный тест. Тест выше защищает от регрессии контракта.

- [ ] **Step 3: Прокинуть окно в `build_ctx()`**

В `worker.py`, в `build_ctx()`, дополнить конструктор `WorkerContext`:

```python
        return WorkerContext(marketing=marketing, store=store, cfg=cfg,
                             campaign_ids=campaign_ids,
                             window_days=cfg.tacos_window_days,
                             revenue_collector=revenue_collector)
```

- [ ] **Step 4: Проверить правку глазами + запустить тесты**

Run: `grep -n "window_days=cfg.tacos_window_days" worker.py`
Expected: одна строка найдена в `build_ctx`.

Run: `source .venv/bin/activate && python test_worker.py`
Expected: PASS — все тесты воркера, включая новый.

- [ ] **Step 5: Commit**

```bash
git add worker.py test_worker.py
git commit -m "feat(worker): build_ctx прокидывает tacos_window_days в окно revenue/расхода

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01V8jnr7RZfsxu9yTUuuWxpL"
```

---

### Task 3: Форма настроек — поле с пресетами (datalist)

**Files:**
- Modify: `webui/templates/settings.html` (`field_meta`, цикл рендера полей)
- Test: `test_webui.py` (новая функция + `__main__`)

**Interfaces:**
- Consumes: `"tacos_window_days"` в `SETTINGS_FIELDS`, значение `s["tacos_window_days"]`.
- Produces: HTML-поле `<input name="tacos_window_days" list="tacos-window-presets">` + `<datalist>` c 2/7/14/30. POST `/settings` подхватывает его автоматически (`form.get(f)` в цикле `SETTINGS_FIELDS`) — правки роута не требуются.

- [ ] **Step 1: Написать падающий тест в `test_webui.py`**

Посмотреть в начале `test_webui.py`, как поднимается тест-клиент (какой fixture/helper даёт `client` и логинит). Использовать тот же приём. Добавить:

```python
def test_settings_page_has_tacos_window_field():
    client = _logged_in_client()   # заменить на существующий helper этого файла
    html = client.get("/settings").text
    assert 'name="tacos_window_days"' in html, "нет поля окна TACoS"
    assert 'list="tacos-window-presets"' in html, "поле не привязано к datalist"
    assert 'id="tacos-window-presets"' in html, "нет datalist пресетов"
    for preset in ("2", "7", "14", "30"):
        assert f'value="{preset}"' in html, f"нет пресета {preset}"
    print("✓ webui: страница настроек содержит поле окна TACoS с пресетами")
```

> Если в `test_webui.py` нет готового `_logged_in_client`, повторить существующий способ логина из соседнего теста этого файла (найти тест, который делает `client.get("/settings")` и скопировать его подготовку).

Вызвать функцию в `__main__`-блоке.

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `source .venv/bin/activate && python test_webui.py`
Expected: FAIL — datalist/поле ещё не отрисованы особым образом.

- [ ] **Step 3: Добавить метку в `field_meta`**

В `webui/templates/settings.html`, в словарь `field_meta`, добавить запись (после `target_tacos_high`):

```jinja
  'tacos_window_days': {'label': 'Окно TACoS, дней', 'hint': 'За сколько дней считать TACoS для решений биддера. Больше дней = стабильнее, но реакция медленнее; 30 дней заметно нагружает обход Shop API.'},
```

- [ ] **Step 4: Особый рендер поля с datalist**

В цикле `{% for f in fields %}` внутри `.settings-grid` заменить условие, чтобы `tacos_window_days` рисовался как input+datalist. Текущий блок:

```jinja
      {% for f in fields %}
        {% if f not in ('dry_run', 'campaign_ids') %}
        <div class="field">
          <label for="{{ f }}">{{ field_meta[f].label if f in field_meta else f }}</label>
          <input type="number" step="any" id="{{ f }}" name="{{ f }}" value="{{ s[f] }}" required>
          {% if f in field_meta %}<span class="hint">{{ field_meta[f].hint }}</span>{% endif %}
        </div>
        {% endif %}
      {% endfor %}
```

Заменить на (добавлена ветка для `tacos_window_days`):

```jinja
      {% for f in fields %}
        {% if f == 'tacos_window_days' %}
        <div class="field">
          <label for="{{ f }}">{{ field_meta[f].label }}</label>
          <input type="number" min="1" max="90" step="1" id="{{ f }}" name="{{ f }}"
                 value="{{ s[f] }}" list="tacos-window-presets" required>
          <datalist id="tacos-window-presets">
            <option value="2">2 дня</option>
            <option value="7">7 дней</option>
            <option value="14">14 дней</option>
            <option value="30">30 дней</option>
          </datalist>
          <span class="hint">{{ field_meta[f].hint }}</span>
        </div>
        {% elif f not in ('dry_run', 'campaign_ids') %}
        <div class="field">
          <label for="{{ f }}">{{ field_meta[f].label if f in field_meta else f }}</label>
          <input type="number" step="any" id="{{ f }}" name="{{ f }}" value="{{ s[f] }}" required>
          {% if f in field_meta %}<span class="hint">{{ field_meta[f].hint }}</span>{% endif %}
        </div>
        {% endif %}
      {% endfor %}
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `source .venv/bin/activate && python test_webui.py`
Expected: PASS — включая `test_settings_page_has_tacos_window_field`.

- [ ] **Step 6: Commit**

```bash
git add webui/templates/settings.html test_webui.py
git commit -m "feat(webui): поле «Окно TACoS, дней» с пресетами 2/7/14/30 в настройках

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01V8jnr7RZfsxu9yTUuuWxpL"
```

---

### Task 4: Дашборд показывает выбранное окно

**Files:**
- Modify: `webui/app.py` (route `dashboard`, ~строки 187–222)
- Modify: `webui/templates/dashboard.html` (подпись секции TACoS, ~строки 39, 75)
- Test: `test_webui.py` (новая функция + `__main__`)

**Interfaces:**
- Consumes: `load_settings(rules_path)["tacos_window_days"]`.
- Produces: в контексте шаблона `dashboard.html` ключ `tacos_window_days`; подпись секции TACoS отражает N дней.

- [ ] **Step 1: Написать падающий тест в `test_webui.py`**

```python
def test_dashboard_shows_tacos_window():
    client = _logged_in_client()   # тот же helper, что в Task 3
    html = client.get("/").text
    assert "последние 2" in html or "за 2" in html, "дашборд не подписывает окно TACoS"
    print("✓ webui: дашборд подписывает секцию TACoS числом дней окна")
```

Вызвать в `__main__`-блоке.

> Тест ждёт дефолтное окно 2. Если тест-окружение использует иной `rules.yaml`, подставить его значение; суть проверки — что число окна попадает в разметку.

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `source .venv/bin/activate && python test_webui.py`
Expected: FAIL — подписи окна ещё нет.

- [ ] **Step 3: Передать окно в шаблон из route**

В `webui/app.py`, в функции `dashboard`, перед `return templates.TemplateResponse(...)` прочитать окно и добавить в контекст. `rules_path` уже в области видимости (объявлен в `create_app`).

Добавить перед `return`:

```python
        tacos_window_days = load_settings(rules_path).get("tacos_window_days", 2)
```

И в словарь контекста `TemplateResponse` добавить ключ:

```python
            "tacos_window_days": tacos_window_days,
```

- [ ] **Step 4: Подписать секцию в `dashboard.html`**

Заменить строку lead (~39):

```jinja
<p class="lead">Данные за {{ day }} — решения биддера, TACoS и текущие ставки по SKU из <code>db/autopilot.db</code>.</p>
```

на:

```jinja
<p class="lead">Данные за {{ day }} — решения биддера, TACoS (за последние {{ tacos_window_days }} дн.) и текущие ставки по SKU из <code>db/autopilot.db</code>.</p>
```

И заголовок секции (~75) с `<h2>TACoS по SKU</h2>` на:

```jinja
<h2>TACoS по SKU <span class="hint">— за последние {{ tacos_window_days }} дн.</span></h2>
```

- [ ] **Step 5: Запустить тесты — убедиться, что проходят**

Run: `source .venv/bin/activate && python test_webui.py`
Expected: PASS — включая `test_dashboard_shows_tacos_window`.

- [ ] **Step 6: Commit**

```bash
git add webui/app.py webui/templates/dashboard.html test_webui.py
git commit -m "feat(webui): дашборд подписывает секцию TACoS выбранным окном дней

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01V8jnr7RZfsxu9yTUuuWxpL"
```

---

### Task 5: Полный прогон тестов (регрессия)

**Files:** нет правок — только проверка.

- [ ] **Step 1: Прогнать все тест-скрипты**

Run:
```bash
source .venv/bin/activate && for t in test_settings_io.py test_worker.py test_webui.py test_rules.py test_revenue.py test_reconcile.py test_store.py; do echo "== $t =="; python "$t" || break; done
```
Expected: каждый файл печатает свои `✓` и финальную строку без трейсбеков. Если какой-то тест падает — остановиться, разобрать (skill systematic-debugging), не двигаться дальше.

- [ ] **Step 2: Финальный статус**

Run: `git status && git log --oneline -5`
Expected: рабочее дерево чистое, 4 новых коммита (Task 1–4) поверх спеки.

---

## Self-Review

**Spec coverage:**
- Критерий 1 (поле с пресетами 2/7/14/30 + произвольное число) → Task 3. ✓
- Критерий 2 (запись в rules.yaml, отклонение <1/>90/дробных) → Task 1. ✓
- Критерий 3 (воркер считает за выбранное окно без рестарта) → Task 2 (hot-reload уже есть в `build_ctx` через `load_cfg_safe`). ✓
- Критерий 4 (дашборд подписывает число дней) → Task 4. ✓
- Критерий 5 (все тесты зелёные + новые) → Task 5. ✓

**Placeholder scan:** без TBD/TODO; весь код приведён дословно. Единственные «замени на существующий helper» пометки в Task 3/4 явно указывают, что искать (способ логина тест-клиента в `test_webui.py`), т.к. точный helper зависит от текущего содержимого файла.

**Type consistency:** `tacos_window_days` — `int`, дефолт 2, диапазон [1,90] — согласованно во всех тасках. Конструктор `WorkerContext(..., window_days=cfg.tacos_window_days)` совпадает с существующим полем `window_days: int`. Ключ шаблона `tacos_window_days` одинаков в route и в шаблоне.
