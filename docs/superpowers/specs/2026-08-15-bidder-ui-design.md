# Веб-панель управления биддером (Bidder UI)

Дата: 2026-08-15

## Цель

Дать владельцу веб-интерфейс, чтобы **регулировать все настройки биддера**
(мин/макс ставка, шаг, коридор TACoS, доля бюджета, лимиты, `dry_run`, список
кампаний) из браузера — без правки `rules.yaml` по SSH — и **видеть, что делает
бот** (лента решений, TACoS, текущие ставки, бюджеты кампаний). UI управляет
реальными рекламными деньгами → безопасность обязательна (логин + HTTPS +
валидация).

## Фазы (порядок реализации)

- **Фаза 1 — Панель управления (control panel):** hot-reload конфига в воркере,
  FastAPI-приложение с логином, страница настроек (редактировать ВСЕ ручки +
  `dry_run` + allowlist кампаний) с валидацией.
- **Фаза 2 — Дашборд:** лента решений с причинами, TACoS по дням, текущие ставки,
  бюджеты кампаний.
- **Фаза 3 — Деплой:** systemd-сервис uvicorn + Caddy (авто-HTTPS) на VPS.

## Архитектура

```
Браузер ──HTTPS──> Caddy (reverse proxy, TLS) ──> uvicorn(127.0.0.1:8000) FastAPI app
                                                       │ читает/пишет config/rules.yaml (атомарно)
                                                       │ читает db/autopilot.db (дашборд)
                                                       │ читает кабинет (бюджеты) через MarketingClient+Session
worker (systemd) ── перечитывает rules.yaml каждый цикл (hot-reload) ── применяет настройки
```

- UI и worker — **два отдельных systemd-сервиса**, общаются ТОЛЬКО через
  `config/rules.yaml` (UI пишет, worker читает) и `db/autopilot.db` (worker пишет,
  UI читает). Никакого прямого RPC — слабая связанность, каждый переживает падение
  другого.
- Пакет `webui/` в репозитории; переиспользует существующие модули
  (`core.rules`, `core.store`, `connectors.*`).

## Компоненты и изменения

### 1. Hot-reload конфига в воркере (`worker.py`)
- Сейчас `cfg = load_rules_config(...)` вызывается один раз в `main()`.
- Изменить: `build_ctx()` перечитывает `rules.yaml` на каждый цикл
  (`cfg = load_rules_config(path)` внутри `build_ctx`), так `dry_run`, пороги и
  allowlist подхватываются со следующего тика без рестарта.
- `campaign_ids` переезжает из env в `rules.yaml` (поле `RulesConfig.campaign_ids:
  list[str] | None = None`); `main()` берёт allowlist из `cfg.campaign_ids`.
  `KASPI_CAMPAIGN_IDS` в env остаётся как фолбэк, если в yaml пусто.
- Если `rules.yaml` временно битый (UI пишет атомарно, но на всякий) —
  `load_rules_config` кидает; `build_ctx` ловит и использует ПРЕДЫДУЩИЙ валидный
  cfg (worker не падает от кривого конфига).

### 2. Слой конфига (`core/settings_io.py` — новый)
- `load_settings() -> dict` / `save_settings(dict)` поверх `rules.yaml`:
  атомарная запись (temp + `os.replace`), сохранение комментариев не требуется
  (перегенерируем из шаблона с комментариями).
- `validate_settings(dict) -> list[str]` — список ошибок (пусто = ок). Правила:
  `min_bid ≥ 1`, `bid_ceiling ≥ min_bid`, `max_bid_step ≥ 1`,
  `0 < target_tacos_low < target_tacos_high`, `0 < sku_budget_fraction ≤ 1`,
  `daily_sku_cost_limit ≥ 0`, `max_changes_per_day ≥ 0`, `cpc_spike_pct ≥ 0`,
  `min_score_for_raise ≥ 0`, `campaign_ids` — список строк (или пусто).
- Чистые функции → тестируются оффлайн.

### 3. FastAPI-приложение (`webui/app.py` + `webui/templates/`)
- **Аутентификация:** логин/пароль. `UI_USERNAME` и `UI_PASSWORD_HASH`
  (pbkdf2_hmac, stdlib) в `.env`; сессия через Starlette `SessionMiddleware`
  (`UI_SECRET_KEY` в `.env`). Все роуты, кроме `/login` и статики, требуют сессию.
- **Роуты:**
  - `GET /login`, `POST /login`, `POST /logout`.
  - `GET /` — дашборд (Фаза 2).
  - `GET /settings` — форма со всеми полями `rules.yaml` (текущие значения).
  - `POST /settings` — валидирует, при ошибках показывает их; при успехе пишет
    `rules.yaml` + записывает строку в аудит (кто/когда/что изменил).
  - `POST /dry-run` — тумблер `dry_run` (с подтверждением на фронте: выключение =
    реальные деньги).
- **Шаблоны:** Jinja2, серверный рендер; один общий `base.html` (шапка, навигация,
  тёмная тема), `login.html`, `settings.html`, `dashboard.html`. Минимум ванильного
  JS (подтверждения, лёгкий автo-refresh дашборда).
- **Аудит:** правки конфига дописываются в `db/autopilot.db` (новая таблица
  `settings_audit(ts, user, field, old, new)`), показываются на странице настроек.

### 4. Дашборд (Фаза 2, `webui`)
- Чтение из `db/autopilot.db` (без нагрузки на кабинет):
  - **Решения:** `Store.get_decisions_for_day(day)` — таблица sku/действие/причина/
    campaign_id/применено, фильтр по дню и кампании.
  - **TACoS:** `Store.get_tacos_daily(day)` — по SKU.
  - **Текущие ставки:** последний `products_snapshot` по sku (bid/avgCpc/cost_today).
- **Бюджеты кампаний:** живой вызов `MarketingClient.list_active_campaigns` (через
  `SessionManager` + `storage_state.json`, self-heal на 401), кэш в памяти на ~60с,
  чтобы не долбить кабинет и не конфликтовать с воркером.

### 5. Деплой (Фаза 3)
- `webui`-зависимости в `requirements.txt`: `fastapi`, `uvicorn[standard]`,
  `jinja2`, `python-multipart`, `itsdangerous`.
- Новый `deploy/kaspi-webui.service` (uvicorn, User=kaspi, ENV_FILE, слушает
  `127.0.0.1:8000`).
- Caddy на VPS: `deploy/Caddyfile` (reverse_proxy на 127.0.0.1:8000, авто-HTTPS
  по домену/hostname сервера). Если публичного домена нет — internal-TLS/самоподпись
  (с предупреждением браузера) как временный вариант.
- `deploy/DEPLOY.md` дополняется разделом про UI.

## Обработка ошибок и безопасность

- Все мутации (`POST`) — только с валидной сессией; неавторизованный → редирект на
  `/login`.
- Валидация конфига ДО записи; при ошибке — `rules.yaml` не трогаем, показываем что
  не так.
- Атомарная запись `rules.yaml` (temp+rename) → воркер никогда не читает
  полу-записанный файл.
- `dry_run: false` (боевой режим) требует явного подтверждения в UI.
- Пароль только хэшем; секрет сессии и хэш — в `.env` (не в репозитории, не в UI).
- UI биндится на `127.0.0.1` — наружу только через Caddy (TLS). Firewall открывает
  80/443 (Caddy), 22 (SSH); прямой 8000 наружу закрыт.

## Тестирование (plain-скрипты с asserts + FastAPI TestClient)

- `test_settings_io.py`: `validate_settings` ловит плохие значения (bid_ceiling <
  min_bid, доля > 1, tacos_low ≥ tacos_high и т.д.); `save/load` round-trip +
  атомарность (после save файл валиден и читается `load_rules_config`).
- `test_webui.py` (через `fastapi.testclient.TestClient`, оффлайн):
  - неавторизованный `GET /settings` → редирект на `/login`;
  - логин с верным/неверным паролем;
  - `POST /settings` с валидными данными пишет конфиг + аудит; с невалидными —
    показывает ошибки, конфиг не меняется;
  - `POST /dry-run` переключает флаг.
- `test_worker.py`: `build_ctx` перечитывает cfg (правка yaml между вызовами
  меняет `dry_run`/пороги на следующем тике); битый yaml → используется прошлый cfg.
- Существующие тесты остаются зелёными (все изменения аддитивные/совместимые).

## Не-цели (YAGNI)

- Без мультипользовательности/ролей — один владелец.
- Без ручного редактирования ставок по отдельным товарам из UI (бот сам управляет);
  UI задаёт только правила/пороги.
- Без графиков-библиотек в Фазе 2 (простые таблицы + при желании инлайн-SVG позже).
- Без правки кредов (логин/пароль/токен Kaspi) через UI — только через `.env`.

## Совместимость

- Все изменения воркера аддитивны и опциональны; `campaign_ids` в yaml
  необязателен (фолбэк на env). Существующий деплой продолжает работать, UI — новый
  отдельный сервис.
