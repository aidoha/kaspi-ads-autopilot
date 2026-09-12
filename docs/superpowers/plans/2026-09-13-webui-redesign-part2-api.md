# Редизайн панели, часть 2: JSON API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поднять полный JSON API панели рядом с существующей Jinja-панелью, чтобы третья часть могла писать React против готового и покрытого тестами бэкенда.

**Architecture:** `webui/app.py` перестаёт быть единственным файлом панели: появляются роутеры `webui/api/*`, которые получают пути к конфигу и БД через маленький контекст, а не через замыкание `create_app`. Jinja-панель, её роуты и шаблоны **остаются нетронутыми и рабочими** — API живёт параллельно под префиксом `/api`. Ядро решений не меняется.

**Tech Stack:** FastAPI (уже в проекте), stdlib `sqlite3`, тесты — самостоятельные скрипты без pytest, через `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-09-12-webui-redesign-design.md`

## Global Constraints

- **Тесты без pytest.** Каждый `test_*.py` — скрипт со списком вызовов в блоке `if __name__ == "__main__":`. Запуск: `.venv/bin/python test_webui.py`. Новый тест обязан быть дописан в этот список, иначе он не выполняется.
- **Jinja-панель не ломается.** Все существующие роуты (`/`, `/settings`, `/settings/campaign/...`, `/settings/sku/...`, `/decisions/...`, `/login`, `/refresh`, `/dry-run`) и все 20 существующих тестов в `test_webui.py` обязаны продолжать работать после каждой задачи. Это проверка на каждом шаге, а не в конце.
- **Ядро решений неприкосновенно.** `core/rules.py`, `core/config_resolver.py`, `core/daypart.py`, `core/reconcile.py`, `worker.py` не меняются ни в одной задаче.
- **Часовой пояс — `Asia/Almaty`** через `ZoneInfo`, никогда не локальная зона сервера.
- **`/api/*` никогда не редиректит.** Неавторизованный запрос отдаёт `401` с JSON-телом. Редирект на HTML-логин ломает `fetch` во фронте.
- **Единый формат ошибки:** `{"errors": ["строка", ...]}` — и для 401, и для 400 валидации.
- Комментарии и сообщения — по-русски.
- **Коммит после каждой задачи**, сообщение по-русски в стиле `feat(api): …`.

## Ловушка, ради которой существует Task 1

`metrics_daily` имеет ключ `(day, campaign_id, sku)`. У товара, который ведётся в двух кампаниях, на один день приходится ДВЕ строки, и `revenue`, `tacos`, `roas` в них **одинаковы** — выручка приходит из Shop API по `merchant_sku` и к кампаниям не привязана.

Значит ряд для графика нельзя строить наивным `SELECT`: он вернёт по две точки на день, а сумма выручки задвоится. Свёртку делает стор, один раз, а не каждый потребитель по-своему.

---

### Task 1: Стор — ряды для графиков и свёртка по дням

**Files:**
- Modify: `core/store.py` (переписать `get_metrics_series`; добавить `get_snapshot_series`, `get_decision_markers`)
- Test: `test_store.py`

**Interfaces:**
- Consumes: таблицы `metrics_daily`, `products_snapshot`, `decisions_log` — уже существуют.
- Produces:
  - `Store.get_metrics_series(sku: str, days: int) -> list[dict]` — по одной записи на день, по возрастанию `day`. Ключи: `day`, `cost`, `gmv`, `views`, `clicks`, `carts`, `transactions`, `revenue`, `ctr`, `cr`, `tacos`, `roas`, `roas_gmv`.
  - `Store.get_snapshot_series(sku: str, days: int) -> list[dict]` — ключи `ts`, `bid`, `avg_cpc`, по возрастанию `ts`.
  - `Store.get_decision_markers(sku: str, days: int) -> list[dict]` — ключи `ts`, `action`, `old_bid`, `new_bid`, `reason`, по возрастанию `ts`.
  - Всё это потребляет Task 5.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_store.py`:

```python
def test_metrics_series_folds_two_campaigns_into_one_day():
    """Товар в двух кампаниях даёт две строки на день с ОДИНАКОВОЙ выручкой.
    Ряд для графика обязан свернуть их в одну точку: расход и счётчики —
    суммой, выручка — один раз, производные — пересчётом из свёрнутых сумм.
    Наивный SELECT задвоил бы выручку и показал бы ROAS вдвое лучше реального."""
    s = new_store()
    common = dict(day="2026-09-12", sku="s1", merchant_sku="m1",
                  transactions=0, ts=1)
    s.upsert_metrics_daily(campaign_id="c1", cost=100, gmv=400, views=1000,
                           clicks=50, carts=2, ctr=0.05, cr=0.04,
                           revenue=1000, **common)
    s.upsert_metrics_daily(campaign_id="c2", cost=300, gmv=600, views=2000,
                           clicks=70, carts=3, ctr=0.035, cr=0.043,
                           revenue=1000, **common)

    series = s.get_metrics_series("s1", days=7)
    assert len(series) == 1, series
    p = series[0]
    assert p["cost"] == 400, p            # 100 + 300
    assert p["clicks"] == 120, p          # 50 + 70
    assert p["views"] == 3000, p
    assert p["carts"] == 5, p
    assert p["gmv"] == 1000, p
    assert p["revenue"] == 1000, p        # НЕ 2000 — величина уровня товара
    assert abs(p["tacos"] - 400 / 1000) < 1e-9, p
    assert abs(p["roas"] - 1000 / 400) < 1e-9, p
    assert abs(p["ctr"] - 120 / 3000) < 1e-9, p   # пересчёт, не среднее
    assert abs(p["cr"] - 5 / 120) < 1e-9, p
    print("✓ ряд metrics сворачивает кампании без задвоения выручки")


def test_metrics_series_keeps_unknown_revenue_unknown():
    """Если выручка за день не собрана (None), производные остаются None,
    а не превращаются в ноль."""
    s = new_store()
    s.upsert_metrics_daily(day="2026-09-12", campaign_id="c1", sku="s1",
                           merchant_sku="m1", cost=500, gmv=0, views=10,
                           clicks=5, carts=0, transactions=0, ctr=0.5,
                           cr=0.0, revenue=None, ts=1)
    p = s.get_metrics_series("s1", days=7)[0]
    assert p["revenue"] is None, p
    assert p["tacos"] is None and p["roas"] is None, p
    assert p["roas_gmv"] == 0.0, p        # расход есть, gmv ноль — это 0, не дырка
    print("✓ ряд metrics не выдумывает выручку, которой нет")


def test_metrics_series_ascending_and_limited_after_fold():
    """Свёртка не должна поломать сортировку и лимит по дням."""
    s = new_store()
    for n in range(1, 6):
        s.upsert_metrics_daily(
            day=f"2026-09-0{n}", campaign_id="c1", sku="s1", merchant_sku="m1",
            cost=n * 100, gmv=0, views=0, clicks=0, carts=0, transactions=0,
            ctr=0.0, cr=0.0, revenue=None, ts=n)
    s.upsert_metrics_daily(
        day="2026-09-03", campaign_id="c1", sku="s2", merchant_sku="m2",
        cost=999, gmv=0, views=0, clicks=0, carts=0, transactions=0,
        ctr=0.0, cr=0.0, revenue=None, ts=9)

    series = s.get_metrics_series("s1", days=3)
    assert [r["day"] for r in series] == ["2026-09-03", "2026-09-04", "2026-09-05"], series
    assert [r["cost"] for r in series] == [300, 400, 500], series
    print("✓ ряд metrics отсортирован, ограничен и не ловит чужой товар")


def test_snapshot_series_returns_bid_and_cpc_by_tick():
    """График ставки рисуется по тикам, а не по дням — свой ряд из снапшотов."""
    s = new_store()
    for i, (bid, cpc) in enumerate([(32, 23.0), (34, 24.1), (36, 25.6)]):
        s.save_products_snapshot(
            [cp(sku="s1", merchant_sku="m1", bid=bid, avg_cpc=cpc)],
            ts=1_700_000_000 + i * 3600, campaign_id="c1")
    s.save_products_snapshot(
        [cp(sku="s2", merchant_sku="m2", bid=99, avg_cpc=99)],
        ts=1_700_000_000, campaign_id="c1")

    series = s.get_snapshot_series("s1", days=30)
    assert [p["bid"] for p in series] == [32, 34, 36], series
    assert [p["avg_cpc"] for p in series] == [23.0, 24.1, 25.6], series
    assert series[0]["ts"] < series[-1]["ts"], series
    print("✓ ряд снапшотов отдаёт ставку и CPC по тикам, по возрастанию")


def test_decision_markers_carry_reason():
    """Маркеры на графике ставки должны нести причину — ради неё график и нужен."""
    s = new_store()
    ts = 1_700_000_000
    s.log_decision(dec(sku="s1", action="raise", old_bid=32, new_bid=34,
                       reason="cart-rate выше цели"),
                   ts=ts, day="2026-09-12", applied=True, campaign_id="c1")
    s.log_decision(dec(sku="s1", action="hold", old_bid=34, new_bid=34,
                       reason="лимит правок исчерпан"),
                   ts=ts + 60, day="2026-09-12", applied=False, campaign_id="c1")
    s.log_decision(dec(sku="s2", action="lower", old_bid=10, new_bid=8,
                       reason="чужой товар"),
                   ts=ts, day="2026-09-12", applied=True, campaign_id="c1")

    marks = s.get_decision_markers("s1", days=30)
    assert [m["action"] for m in marks] == ["raise", "hold"], marks
    assert marks[0]["reason"] == "cart-rate выше цели", marks[0]
    assert marks[0]["old_bid"] == 32 and marks[0]["new_bid"] == 34, marks[0]
    print("✓ маркеры решений несут действие, ставки и причину")
```

Дописать все пять вызовов в список в конце файла. Существующий `test_metrics_series_ascending_and_limited` удалить — новый `test_metrics_series_ascending_and_limited_after_fold` покрывает то же самое поверх свёртки, и держать оба значит проверять одно дважды.

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_store.py
```

Ожидание: FAIL на `test_metrics_series_folds_two_campaigns_into_one_day` — текущая реализация вернёт две строки вместо одной.

- [ ] **Step 3: Реализовать**

В `core/store.py` заменить существующий `get_metrics_series` целиком и добавить два метода рядом:

```python
    def get_metrics_series(self, sku: str, days: int) -> list[dict]:
        """Ряд подневных метрик товара для графика: по ОДНОЙ точке на день,
        по возрастанию дня.

        Свёртка обязательна, а не косметика. Ключ metrics_daily —
        (day, campaign_id, sku), и у товара из двух кампаний на день приходится
        две строки. Расход и счётчики в них РАЗНЫЕ (это факты кампании), а
        revenue/tacos/roas ОДИНАКОВЫЕ — выручка приходит из Shop API по
        merchant_sku и к кампаниям не привязана. Поэтому:
          • cost, gmv, views, clicks, carts, transactions — суммируем;
          • revenue — берём один раз (MAX, все значения равны);
          • tacos/roas/ctr/cr — ПЕРЕСЧИТЫВАЕМ из свёрнутых сумм, а не усредняем
            готовые: среднее двух отношений с разными знаменателями неверно.
        Наивный SELECT здесь задвоил бы выручку и показал ROAS вдвое лучше.
        """
        rows = self._conn.execute(
            """SELECT day,
                      SUM(cost)         AS cost,
                      SUM(gmv)          AS gmv,
                      SUM(views)        AS views,
                      SUM(clicks)       AS clicks,
                      SUM(carts)        AS carts,
                      SUM(transactions) AS transactions,
                      MAX(revenue)      AS revenue,
                      COUNT(revenue)    AS revenue_known
               FROM metrics_daily
               WHERE sku=? AND day IN (
                   SELECT day FROM metrics_daily WHERE sku=?
                   GROUP BY day ORDER BY day DESC LIMIT ?)
               GROUP BY day
               ORDER BY day ASC""",
            (sku, sku, days),
        ).fetchall()

        out: list[dict] = []
        for r in rows:
            cost, gmv = r["cost"], r["gmv"]
            clicks, views, carts = r["clicks"], r["views"], r["carts"]
            # COUNT(revenue) не считает NULL: ноль означает «за этот день
            # выручку не собирали» — тогда она неизвестна, а не равна нулю.
            revenue = r["revenue"] if r["revenue_known"] else None
            out.append({
                "day": r["day"],
                "cost": cost, "gmv": gmv, "views": views, "clicks": clicks,
                "carts": carts, "transactions": r["transactions"],
                "revenue": revenue,
                "ctr": (clicks / views) if views else None,
                "cr": (carts / clicks) if clicks else None,
                "tacos": (cost / revenue) if revenue else None,
                "roas": None if (not cost or revenue is None) else (revenue / cost),
                "roas_gmv": (gmv / cost) if cost else None,
            })
        return out

    def get_snapshot_series(self, sku: str, days: int) -> list[dict]:
        """Ставка и цена клика ПО ТИКАМ за последние `days` суток.
        Отдельно от get_metrics_series: у ставки внутридневное разрешение —
        именно на нём видно, как биддер её двигал."""
        since = int(time.time()) - days * 86400
        rows = self._conn.execute(
            "SELECT ts, bid, avg_cpc FROM products_snapshot "
            "WHERE sku=? AND ts>=? ORDER BY ts ASC",
            (sku, since),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_decision_markers(self, sku: str, days: int) -> list[dict]:
        """Правки биддера для маркеров на графике ставки. Причина едет вместе
        с точкой: без неё маркер показывает ЧТО произошло, но не ПОЧЕМУ, а
        ценность графика именно во втором."""
        since = int(time.time()) - days * 86400
        rows = self._conn.execute(
            "SELECT ts, action, old_bid, new_bid, reason FROM decisions_log "
            "WHERE sku=? AND ts>=? ORDER BY ts ASC",
            (sku, since),
        ).fetchall()
        return [dict(r) for r in rows]
```

В начало `core/store.py` добавить `import time` к существующим импортам (`os`, `sqlite3`) — он там сейчас отсутствует.

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_store.py && .venv/bin/python test_webui.py && .venv/bin/python test_worker.py
```

Ожидание: все три зелёные. `test_webui.py` и `test_worker.py` тут — страховка, что переписанный метод ничего не задел.

- [ ] **Step 5: Коммит**

```bash
git add core/store.py test_store.py
git commit -m "feat(store): ряды для графиков и свёртка metrics_daily по дням

get_metrics_series теперь сворачивает строки нескольких кампаний в одну
точку дня: счётчики суммой, выручка один раз, отношения пересчётом из
свёрнутых сумм. Наивный SELECT задвоил бы выручку и показал ROAS вдвое
лучше реального.

Плюс get_snapshot_series (ставка и CPC по тикам) и get_decision_markers
(правки биддера с причиной) — источники графика ставки."
```

---

### Task 2: Каркас API — контекст, авторизация, 401 JSON

**Files:**
- Create: `webui/api/__init__.py`, `webui/api/deps.py`, `webui/api/auth.py`
- Modify: `webui/app.py` (собрать контекст, примонтировать роутер, `same_site="strict"`)
- Test: `test_webui_api.py` (новый файл)

**Interfaces:**
- Consumes: `webui.auth.verify_password`, `core.store.Store`.
- Produces:
  - `webui.api.deps.ApiContext` — dataclass с полями `rules_path: str`, `db_path: str`, `username: str`, `pw_hash: str`.
  - `webui.api.deps.require_user(request) -> str` — FastAPI-зависимость; при отсутствии сессии бросает `HTTPException(401, {"errors": ["Требуется вход"]})`.
  - `webui.api.deps.open_store(ctx) -> Store` — контекст-менеджер, закрывающий соединение.
  - `webui.api.auth.build_router(ctx) -> APIRouter` с `POST /api/login`, `POST /api/logout`, `GET /api/me`.
  - Задачи 3–6 строят свои роутеры по этому же образцу `build_router(ctx)`.

- [ ] **Step 1: Написать падающие тесты**

Создать новый файл `test_webui_api.py`:

```python
"""test_webui_api.py — оффлайн-тест JSON API панели через FastAPI TestClient.

Jinja-панель живёт параллельно и проверяется отдельно в test_webui.py.
Здесь — только /api/*: коды ответов, формат ошибок, содержимое JSON.

Запуск: .venv/bin/python test_webui_api.py
"""
import os
import tempfile

from fastapi.testclient import TestClient

from core.rules import RulesConfig
from core.settings_io import save_settings, SETTINGS_FIELDS
from core.store import Store
from webui.app import create_app
from webui.auth import hash_password


def _client():
    """Свежее приложение на временном конфиге и временной БД."""
    d = tempfile.mkdtemp()
    rules = os.path.join(d, "rules.yaml")
    save_settings(rules, {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS})
    db = os.path.join(d, "a.db")
    empty_env = os.path.join(d, "empty.env")
    open(empty_env, "w").close()   # чтобы create_app не подхватил реальный config/.env
    os.environ.update(
        UI_USERNAME="admin",
        UI_PASSWORD_HASH=hash_password("secret"),
        UI_SECRET_KEY="test-secret-key",
        RULES_CONFIG=rules, DB_PATH=db, ENV_FILE=empty_env,
    )
    # base_url=https: кука сессии Secure (в проде TLS терминирует Caddy);
    # на http:// httpx честно не вернул бы её обратно.
    return TestClient(create_app(), base_url="https://testserver"), rules, db


def _logged_in():
    c, rules, db = _client()
    r = c.post("/api/login", json={"username": "admin", "password": "secret"})
    assert r.status_code == 200, r.text
    return c, rules, db


def test_api_without_session_returns_401_json_not_redirect():
    """Редирект на HTML-логин сломал бы fetch во фронте: он молча пошёл бы за
    HTML и получил разметку вместо данных."""
    c, _, _ = _client()
    r = c.get("/api/me", follow_redirects=False)
    assert r.status_code == 401, (r.status_code, r.text)
    assert r.headers["content-type"].startswith("application/json"), r.headers
    assert r.json()["errors"], r.json()
    print("✓ api: без сессии 401 JSON, а не редирект")


def test_api_login_sets_session_and_me_returns_user():
    c, _, _ = _logged_in()
    r = c.get("/api/me")
    assert r.status_code == 200 and r.json()["user"] == "admin", r.text
    print("✓ api: вход ставит сессию, /api/me отдаёт пользователя")


def test_api_login_rejects_wrong_password():
    c, _, _ = _client()
    r = c.post("/api/login", json={"username": "admin", "password": "неверный"})
    assert r.status_code == 401, r.text
    assert r.json()["errors"], r.json()
    assert c.get("/api/me").status_code == 401
    print("✓ api: неверный пароль не пускает")


def test_api_logout_clears_session():
    c, _, _ = _logged_in()
    assert c.post("/api/logout").status_code == 200
    assert c.get("/api/me").status_code == 401
    print("✓ api: выход сбрасывает сессию")


def test_jinja_panel_still_works_alongside_api():
    """API добавляется РЯДОМ со старой панелью, а не вместо неё. Пока не
    готов React, Jinja — единственный работающий интерфейс."""
    c, _, _ = _client()
    r = c.get("/login")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    c.post("/login", data={"username": "admin", "password": "secret"})
    r = c.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    print("✓ api: Jinja-панель продолжает работать параллельно")


if __name__ == "__main__":
    test_api_without_session_returns_401_json_not_redirect()
    test_api_login_sets_session_and_me_returns_user()
    test_api_login_rejects_wrong_password()
    test_api_logout_clears_session()
    test_jinja_panel_still_works_alongside_api()
    print("-" * 60)
    print("✓ Все проверки API прошли")
```

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_webui_api.py
```

Ожидание: FAIL — `/api/me` отдаёт 404, роутов ещё нет.

- [ ] **Step 3: Создать контекст и зависимости**

`webui/api/__init__.py` — пустой файл.

`webui/api/deps.py`:

```python
"""deps.py — общий контекст и зависимости JSON API панели.

Старый create_app держал пути к конфигу и БД в замыкании. Роутерам нужно то
же самое, но замыкание через модули не протащить — поэтому маленький
контекст-объект, который create_app собирает один раз и раздаёт роутерам.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import HTTPException, Request

from core.store import Store


@dataclass(frozen=True)
class ApiContext:
    rules_path: str
    db_path: str
    username: str
    pw_hash: str


def require_user(request: Request) -> str:
    """Пользователь из сессии либо 401 JSON.

    Осознанно НЕ редиректим на /login, в отличие от Jinja-роутов: фронт ходит
    сюда через fetch, и редирект на HTML отдал бы ему разметку вместо данных —
    ошибка бы всплыла как невнятный сбой парсинга, а не как «нужен вход».
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401,
                            detail={"errors": ["Требуется вход"]})
    return user


@contextmanager
def open_store(ctx: ApiContext):
    """Соединение со стором на время запроса. Store открывает свой sqlite3 и
    обязан быть закрыт — иначе под нагрузкой панели копятся дескрипторы."""
    store = Store(ctx.db_path)
    try:
        yield store
    finally:
        store.close()
```

`webui/api/auth.py`:

```python
"""auth.py — вход, выход и проверка сессии для JSON API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from webui.api.deps import ApiContext, require_user
from webui.auth import verify_password

log = logging.getLogger("webui.api")


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/login")
    async def login(request: Request):
        body = await request.json()
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        if (username == ctx.username and ctx.pw_hash
                and verify_password(password, ctx.pw_hash)):
            request.session["user"] = username
            return {"user": username}
        log.warning("Неудачный вход в API панели (username=%s)", username)
        raise HTTPException(status_code=401,
                            detail={"errors": ["Неверный логин или пароль"]})

    @router.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @router.get("/me")
    def me(user: str = Depends(require_user)):
        return {"user": user}

    return router
```

- [ ] **Step 4: Подключить роутер и выровнять формат ошибки**

В `webui/app.py`, в `create_app()`, после создания `app` и добавления `SessionMiddleware`:

1. Поменять `same_site` у сессии на строгий:

```python
    # same_site="strict": и SPA, и Jinja-панель живут на том же origin, что API,
    # поэтому кросс-сайтовые запросы с кукой панели не нужны вообще. Строгий
    # режим закрывает CSRF без отдельного токена — для панели, которая тратит
    # рекламный бюджет, это дешёвая и правильная страховка.
    app.add_middleware(SessionMiddleware, secret_key=secret,
                       https_only=True, same_site="strict",
                       max_age=60 * 60 * 8)
```

2. После вычисления `rules_path`, `db_path`, `username`, `pw_hash` собрать контекст и примонтировать роутер:

```python
    from webui.api.deps import ApiContext
    from webui.api import auth as api_auth

    api_ctx = ApiContext(rules_path=rules_path, db_path=db_path,
                         username=username, pw_hash=pw_hash)
    app.include_router(api_auth.build_router(api_ctx))
```

3. Добавить обработчик, который разворачивает `detail` в плоский JSON — иначе FastAPI отдаст `{"detail": {"errors": [...]}}`, и фронту пришлось бы разбирать две обёртки:

```python
    from fastapi.responses import JSONResponse
    from fastapi.exceptions import HTTPException as FastAPIHTTPException

    @app.exception_handler(FastAPIHTTPException)
    async def _api_error(request: Request, exc: FastAPIHTTPException):
        """Ошибки /api/* всегда в одном виде: {"errors": [...]}. Jinja-роуты
        сохраняют штатное поведение FastAPI — их ответы читает человек, а не
        фронт."""
        if not request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        detail = exc.detail
        if isinstance(detail, dict) and "errors" in detail:
            return JSONResponse(detail, status_code=exc.status_code)
        return JSONResponse({"errors": [str(detail)]}, status_code=exc.status_code)
```

- [ ] **Step 5: Убедиться, что тесты проходят и старая панель цела**

```bash
.venv/bin/python test_webui_api.py && .venv/bin/python test_webui.py
```

Ожидание: оба зелёные. Второй критичен: `same_site="strict"` трогает куку, которой пользуется и Jinja-панель.

- [ ] **Step 6: Коммит**

```bash
git add webui/api/ webui/app.py test_webui_api.py
git commit -m "feat(api): каркас JSON API — контекст, вход, 401 JSON вместо редиректа

API поднимается РЯДОМ с Jinja-панелью, не вместо неё: до готовности React
старая панель остаётся единственным рабочим интерфейсом.

/api/* никогда не редиректит на HTML-логин — фронт ходит туда через fetch
и получил бы разметку вместо данных. Ошибки приведены к одному виду
{errors: [...]}. Кука сессии переведена на same_site=strict: панель и API
на одном origin, кросс-сайтовые запросы не нужны, CSRF закрывается без
отдельного токена."
```

---

### Task 3: API чтения — кампании и сводка

**Files:**
- Create: `webui/api/overview.py`
- Modify: `webui/app.py` (примонтировать роутер)
- Test: `test_webui_api.py`

**Interfaces:**
- Consumes: `ApiContext`, `require_user`, `open_store` (Task 2); `Store.get_metrics_for_day`, `Store.get_latest_snapshot_ts`; `_get_campaign_budgets` из `webui/app.py`.
- Produces:
  - `GET /api/campaigns` → `{"campaigns": [{"id", "name", "daily_budget"}], "budgets_available": bool}`
  - `GET /api/overview?campaign=<id|all>&days=<7|14|30>` → `{"day", "days", "last_snapshot_ts", "totals": {...}}`, где `totals` содержит `cost`, `revenue`, `gmv`, `tacos`, `roas`, `roas_gmv`, `clicks`, `carts`, `cr`.
  - `webui.api.overview.build_router(ctx, budgets_fn)` — `budgets_fn` инъектируется, чтобы тест не ходил в кабинет Kaspi.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_webui_api.py` (и в список вызовов):

```python
def _seed_metrics(db, day, rows):
    """rows: список кортежей (campaign_id, sku, cost, revenue, gmv, clicks, carts)."""
    s = Store(db)
    try:
        for cid, sku, cost, revenue, gmv, clicks, carts in rows:
            s.upsert_metrics_daily(
                day=day, campaign_id=cid, sku=sku, merchant_sku="m" + sku,
                cost=cost, gmv=gmv, views=clicks * 20, clicks=clicks,
                carts=carts, transactions=0, ctr=0.05, cr=0.04,
                revenue=revenue, ts=1)
    finally:
        s.close()


def test_overview_totals_sum_across_products():
    c, _, db = _logged_in()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Asia/Almaty")).date().isoformat()
    _seed_metrics(db, day, [
        ("c1", "s1", 1000, 10000, 12000, 100, 8),
        ("c1", "s2", 500, 2000, 2500, 40, 2),
    ])

    r = c.get("/api/overview?days=7")
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    assert t["cost"] == 1500, t
    assert t["revenue"] == 12000, t
    assert abs(t["tacos"] - 1500 / 12000) < 1e-9, t
    assert abs(t["roas"] - 12000 / 1500) < 1e-9, t
    assert t["clicks"] == 140 and t["carts"] == 10, t
    print("✓ api: сводка суммирует расход и выручку по товарам")


def test_overview_filters_by_campaign():
    c, _, db = _logged_in()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Asia/Almaty")).date().isoformat()
    _seed_metrics(db, day, [
        ("c1", "s1", 1000, 10000, 0, 100, 8),
        ("c2", "s2", 500, 2000, 0, 40, 2),
    ])

    t = c.get("/api/overview?campaign=c1&days=7").json()["totals"]
    assert t["cost"] == 1000 and t["revenue"] == 10000, t
    print("✓ api: фильтр по кампании сужает сводку")


def test_overview_empty_db_returns_zeros_not_error():
    """Свежая установка не должна ронять панель 500-й."""
    c, _, _ = _logged_in()
    r = c.get("/api/overview?days=7")
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    assert t["cost"] == 0 and t["revenue"] == 0, t
    assert t["tacos"] is None and t["roas"] is None, t
    print("✓ api: пустая БД даёт нули и None, а не 500")


def test_overview_requires_login():
    c, _, _ = _client()
    assert c.get("/api/overview", follow_redirects=False).status_code == 401
    print("✓ api: сводка требует входа")


def test_campaigns_survive_unavailable_cabinet():
    """Бюджеты живут в кабинете Kaspi. Он может быть недоступен — список
    кампаний обязан отдаться без них, а не упасть."""
    c, _, _ = _logged_in()
    r = c.get("/api/campaigns")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["campaigns"], list), body
    assert body["budgets_available"] is False, body
    print("✓ api: недоступный кабинет не роняет список кампаний")
```

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_webui_api.py
```

Ожидание: FAIL, `/api/overview` отдаёт 404.

- [ ] **Step 3: Реализовать**

Создать `webui/api/overview.py`:

```python
"""overview.py — кампании и сводка KPI для шапки панели."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from webui.api.deps import ApiContext, open_store, require_user

ALMATY = ZoneInfo("Asia/Almaty")


def _days_range(days: int) -> list[str]:
    """Последние `days` календарных дней Алматы, включая сегодня."""
    today = datetime.now(ALMATY).date()
    return [(today - timedelta(days=n)).isoformat() for n in range(days)]


def build_router(ctx: ApiContext, budgets_fn) -> APIRouter:
    """budgets_fn инъектируется: бюджеты живут в кабинете Kaspi за
    Playwright-логином, и тест не должен туда ходить."""
    router = APIRouter(prefix="/api")

    @router.get("/campaigns")
    def campaigns(user: str = Depends(require_user)):
        budgets = budgets_fn() or {}
        return {
            "campaigns": [{"id": cid, "name": b.get("name", ""),
                           "daily_budget": b.get("daily_budget")}
                          for cid, b in budgets.items()],
            "budgets_available": bool(budgets),
        }

    @router.get("/overview")
    def overview(campaign: str = "all", days: int = 14,
                 user: str = Depends(require_user)):
        days = max(1, min(int(days), 90))
        wanted = set(_days_range(days))
        cost = revenue_sum = gmv = 0.0
        clicks = carts = 0
        # Выручка — величина уровня ТОВАРА, повторённая в кампанийных строках.
        # Суммировать её по строкам нельзя, поэтому собираем по (day, sku)
        # и складываем один раз на товаро-день.
        revenue_by_key: dict[tuple[str, str], float] = {}
        with open_store(ctx) as store:
            for day in wanted:
                for row in store.get_metrics_for_day(day):
                    if campaign != "all" and row["campaign_id"] != campaign:
                        continue
                    cost += row["cost"] or 0
                    gmv += row["gmv"] or 0
                    clicks += row["clicks"] or 0
                    carts += row["carts"] or 0
                    if row["revenue"] is not None:
                        revenue_by_key[(day, row["sku"])] = row["revenue"]
            last_ts = store.get_latest_snapshot_ts()
        revenue_sum = sum(revenue_by_key.values())

        return {
            "day": datetime.now(ALMATY).date().isoformat(),
            "days": days,
            "last_snapshot_ts": last_ts,
            "totals": {
                "cost": cost,
                "revenue": revenue_sum,
                "gmv": gmv,
                "clicks": clicks,
                "carts": carts,
                "tacos": (cost / revenue_sum) if revenue_sum else None,
                "roas": (revenue_sum / cost) if cost else None,
                "roas_gmv": (gmv / cost) if cost else None,
                "cr": (carts / clicks) if clicks else None,
            },
        }

    return router
```

В `webui/app.py` примонтировать рядом с роутером авторизации:

```python
    from webui.api import overview as api_overview
    app.include_router(api_overview.build_router(api_ctx, _get_campaign_budgets))
```

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_webui_api.py && .venv/bin/python test_webui.py
```

Ожидание: оба зелёные.

- [ ] **Step 5: Коммит**

```bash
git add webui/api/overview.py webui/app.py test_webui_api.py
git commit -m "feat(api): /api/campaigns и /api/overview

Сводка складывает выручку по (день, товар), а не по строкам metrics_daily:
у товара из двух кампаний строк две, и выручка в них одна и та же —
построчная сумма задвоила бы её.

Бюджеты кампаний инъектируются функцией, чтобы тесты не ходили в кабинет
Kaspi; недоступный кабинет отдаёт список без бюджетов, а не 500."
```

---

### Task 4: API чтения — список товаров и карточка товара

**Files:**
- Create: `webui/api/products.py`
- Modify: `webui/app.py`
- Test: `test_webui_api.py`

**Interfaces:**
- Consumes: Task 2 и Task 3; `Store.get_campaign_skus`, `get_latest_snapshot`, `get_sku_name_map`, `all_product_controls`, `get_metrics_series`, `get_decisions_for_sku_day`, `get_overrides`; `core.config_resolver.resolve_config`, `OVERRIDABLE_FIELDS`; `core.rules.load_rules_config`; `core.daypart.ProductControl`.
- Produces:
  - `GET /api/products?campaign=&days=` → `{"products": [...]}`, элемент: `sku`, `merchant_sku`, `campaign_id`, `name`, `bid`, `cost`, `revenue`, `tacos`, `roas`, `ctr`, `cr`, `clicks`, `carts`, `status`, `enabled`, `bid_spark` (список чисел).
  - `GET /api/products/{campaign_id}/{sku}` → `{"sku", "name", "campaign_id", "control": {...}, "values": {...}, "owned": [...], "decisions": [...]}`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_webui_api.py` (и в список вызовов):

```python
def _seed_snapshot(db, campaign_id, sku, bid, ts):
    from connectors.marketing_client import CampaignProduct
    p = CampaignProduct(
        sku=sku, merchant_sku="m" + sku, campaign_product_id=1, bid=bid,
        avg_cpc=bid * 0.7, score=7.0, buy_box=True, product_state="Active",
        cost=0, cost_today=0, gmv=0, crr=0, cr=0, ctr=0, views=0,
        clicks=0, carts=0, transactions=0, price=10000)
    s = Store(db)
    try:
        s.save_products_snapshot([p], ts=ts, campaign_id=campaign_id)
    finally:
        s.close()


def test_products_list_joins_metrics_and_bid():
    c, _, db = _logged_in()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Asia/Almaty")).date().isoformat()
    _seed_snapshot(db, "c1", "s1", bid=40, ts=1_700_000_000)
    _seed_metrics(db, day, [("c1", "s1", 1000, 10000, 0, 100, 8)])

    r = c.get("/api/products?days=7")
    assert r.status_code == 200, r.text
    items = r.json()["products"]
    assert len(items) == 1, items
    p = items[0]
    assert p["sku"] == "s1" and p["bid"] == 40, p
    assert p["cost"] == 1000 and p["revenue"] == 10000, p
    assert abs(p["tacos"] - 0.1) < 1e-9, p
    assert p["enabled"] is True, p           # по умолчанию биддер ведёт товар
    assert isinstance(p["bid_spark"], list), p
    print("✓ api: список товаров сшивает ставку и метрики")


def test_products_list_reports_disabled_product():
    c, _, db = _logged_in()
    _seed_snapshot(db, "c1", "s1", bid=40, ts=1_700_000_000)
    s = Store(db)
    try:
        s.set_product_control("c1", "s1", enabled=False, window_start=0,
                              window_end=24, days_mask=127, user="admin", ts=1)
    finally:
        s.close()

    p = c.get("/api/products?days=7").json()["products"][0]
    assert p["enabled"] is False, p
    assert p["status"] == "выключен", p
    print("✓ api: выключенный товар виден в списке как выключенный")


def test_product_detail_returns_effective_config_and_owned_fields():
    """Поля наследуются глобал → кампания → товар. Панель обязана показывать
    и эффективное значение, и то, задано ли оно НА ЭТОМ уровне."""
    c, _, db = _logged_in()
    _seed_snapshot(db, "c1", "s1", bid=40, ts=1_700_000_000)
    s = Store(db)
    try:
        s.set_override("sku", "s1", "bid_ceiling", "123", user="admin", ts=1)
    finally:
        s.close()

    r = c.get("/api/products/c1/s1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["values"]["bid_ceiling"] == 123, body["values"]
    assert "bid_ceiling" in body["owned"], body["owned"]
    assert "min_bid" not in body["owned"], body["owned"]
    assert body["control"]["enabled"] is True, body["control"]
    assert body["control"]["window_end"] == 24, body["control"]
    print("✓ api: карточка товара отдаёт эффективный конфиг и свои поля")


def test_product_detail_requires_login():
    c, _, _ = _client()
    assert c.get("/api/products/c1/s1", follow_redirects=False).status_code == 401
    assert c.get("/api/products", follow_redirects=False).status_code == 401
    print("✓ api: товары требуют входа")
```

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_webui_api.py
```

Ожидание: FAIL, 404 на `/api/products`.

- [ ] **Step 3: Реализовать**

Создать `webui/api/products.py`:

```python
"""products.py — список товаров и карточка одного товара."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from core.config_resolver import OVERRIDABLE_FIELDS, resolve_config
from core.rules import load_rules_config
from webui.api.deps import ApiContext, open_store, require_user

ALMATY = ZoneInfo("Asia/Almaty")


def _status(control, now) -> str:
    """Человеческий статус товара для колонки списка."""
    if control is None:
        return "активен"
    if not control.enabled:
        return "выключен"
    if not control.active_at(now):
        return "вне окна"
    return "активен"


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _effective(store, campaign_id: str, sku: str | None):
        """Эффективный конфиг и множество полей, заданных НА ЭТОМ уровне."""
        global_cfg = load_rules_config(ctx.rules_path)
        camp_ov = store.get_overrides("campaign", campaign_id)
        sku_ov = store.get_overrides("sku", sku) if sku else {}
        cfg = resolve_config(global_cfg, camp_ov, sku_ov)
        owned = sku_ov if sku else camp_ov
        values = {f: getattr(cfg, f) for f in OVERRIDABLE_FIELDS}
        return values, sorted(set(owned) & set(OVERRIDABLE_FIELDS))

    @router.get("/products")
    def products(campaign: str = "all", days: int = 14,
                 user: str = Depends(require_user)):
        days = max(1, min(int(days), 90))
        now = datetime.now(ALMATY)
        out = []
        with open_store(ctx) as store:
            names = store.get_sku_name_map()
            controls = {(cid, sku): ctl
                        for cid, sku, ctl in store.all_product_controls()}
            campaign_ids = ({campaign} if campaign != "all"
                            else {cid for cid, _ in controls} |
                                 _all_campaign_ids(store))
            for cid in sorted(campaign_ids):
                for row in store.get_campaign_skus(cid):
                    sku = row["sku"]
                    series = store.get_metrics_series(sku, days)
                    cost = sum(p["cost"] or 0 for p in series)
                    # Выручка уже свёрнута по дням внутри get_metrics_series —
                    # здесь остаётся сложить дни, а не строки кампаний.
                    revs = [p["revenue"] for p in series if p["revenue"] is not None]
                    revenue = sum(revs) if revs else None
                    clicks = sum(p["clicks"] or 0 for p in series)
                    carts = sum(p["carts"] or 0 for p in series)
                    views = sum(p["views"] or 0 for p in series)
                    ctl = controls.get((cid, sku))
                    spark = [s["bid"] for s in store.get_snapshot_series(sku, days)]
                    out.append({
                        "sku": sku,
                        "merchant_sku": row["merchant_sku"],
                        "campaign_id": cid,
                        "name": names.get(sku) or names.get(row["merchant_sku"]),
                        "bid": row["bid"],
                        "cost": cost,
                        "revenue": revenue,
                        "clicks": clicks,
                        "carts": carts,
                        "tacos": (cost / revenue) if revenue else None,
                        "roas": None if (not cost or revenue is None) else revenue / cost,
                        "ctr": (clicks / views) if views else None,
                        "cr": (carts / clicks) if clicks else None,
                        "enabled": True if ctl is None else bool(ctl.enabled),
                        "status": _status(ctl, now),
                        "bid_spark": spark,
                    })
        return {"products": out}

    @router.get("/products/{campaign_id}/{sku}")
    def product_detail(campaign_id: str, sku: str,
                       user: str = Depends(require_user)):
        day = datetime.now(ALMATY).date().isoformat()
        with open_store(ctx) as store:
            values, owned = _effective(store, campaign_id, sku)
            ctl = store.get_product_control(campaign_id, sku)
            decisions = store.get_decisions_for_sku_day(day, sku, 20, 0)
            name = store.get_sku_name_map().get(sku)
        return {
            "sku": sku, "campaign_id": campaign_id, "name": name,
            "values": values, "owned": owned,
            "control": {
                "enabled": bool(ctl.enabled),
                "window_start": ctl.window_start,
                "window_end": ctl.window_end,
                "days_mask": ctl.days_mask,
            },
            "decisions": decisions,
        }

    return router


def _all_campaign_ids(store) -> set[str]:
    """Кампании, по которым есть снапшоты. Кабинет для этого дёргать не нужно —
    список товаров рисуется из того, что уже собрано."""
    rows = store._conn.execute(
        "SELECT DISTINCT campaign_id FROM products_snapshot "
        "WHERE campaign_id IS NOT NULL AND campaign_id != ''").fetchall()
    return {r["campaign_id"] for r in rows}
```

**Замечание для реализатора.** `_all_campaign_ids` лезет в `store._conn` мимо публичного интерфейса. Это осознанный компромисс на один запрос: заводить публичный метод ради него — лишний круг по стору и тестам. Если ревью сочтёт это дефектом, вынеси в `Store.list_campaign_ids()` с отдельным тестом — возражать не буду.

В `webui/app.py`:

```python
    from webui.api import products as api_products
    app.include_router(api_products.build_router(api_ctx))
```

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_webui_api.py && .venv/bin/python test_webui.py
```

- [ ] **Step 5: Коммит**

```bash
git add webui/api/products.py webui/app.py test_webui_api.py
git commit -m "feat(api): /api/products и карточка товара

Список сшивает ставку из снапшота, свёрнутые метрики, статус управления и
спарклайн ставки. Карточка отдаёт эффективный конфиг вместе со списком
полей, заданных на уровне товара — панель показывает и значение, и то,
унаследовано оно или своё."
```

---

### Task 5: API чтения — ряды для графиков

**Files:**
- Modify: `webui/api/products.py` (добавить эндпоинт)
- Test: `test_webui_api.py`

**Interfaces:**
- Consumes: `Store.get_snapshot_series`, `get_metrics_series`, `get_decision_markers` (Task 1); `_effective` (Task 4).
- Produces: `GET /api/products/{campaign_id}/{sku}/series?days=` → объект с четырьмя ключами `ticks`, `daily`, `decisions`, `corridor` — форма зафиксирована в спеке, раздел «Ответ `/series` — две разные шкалы времени».

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_webui_api.py` (и в список вызовов):

```python
def test_series_separates_tick_scale_from_day_scale():
    """Ставка живёт по тикам, метрики — по дням. Класть их в одну сетку
    нельзя: тиков за день несколько, а дневная метрика одна."""
    c, _, db = _logged_in()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Asia/Almaty")).date().isoformat()
    import time as _t
    now = int(_t.time())
    _seed_snapshot(db, "c1", "s1", bid=32, ts=now - 7200)
    _seed_snapshot(db, "c1", "s1", bid=36, ts=now - 3600)
    _seed_metrics(db, day, [("c1", "s1", 1000, 10000, 0, 100, 8)])

    r = c.get("/api/products/c1/s1/series?days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["bid"] for p in body["ticks"]] == [32, 36], body["ticks"]
    assert len(body["daily"]) == 1, body["daily"]
    assert body["daily"][0]["day"] == day, body["daily"][0]
    assert abs(body["daily"][0]["tacos"] - 0.1) < 1e-9, body["daily"][0]
    print("✓ api: /series разделяет шкалу тиков и шкалу дней")


def test_series_corridor_reflects_effective_config():
    """Коридор на графике TACoS — это эффективные границы ИМЕННО этого товара,
    с учётом переопределений, а не глобальные значения."""
    c, _, db = _logged_in()
    _seed_snapshot(db, "c1", "s1", bid=32, ts=1_700_000_000)
    s = Store(db)
    try:
        s.set_override("sku", "s1", "target_tacos_high", "0.25", user="a", ts=1)
    finally:
        s.close()

    corridor = c.get("/api/products/c1/s1/series?days=7").json()["corridor"]
    assert abs(corridor["high"] - 0.25) < 1e-9, corridor
    print("✓ api: коридор TACoS берётся из эффективного конфига товара")


def test_series_carries_decision_reasons():
    c, _, db = _logged_in()
    import time as _t
    from core.rules import Decision
    s = Store(db)
    try:
        s.log_decision(
            Decision(sku="s1", merchant_sku="ms1", old_bid=32, new_bid=36,
                     action="raise", loop="slow", reason="cart-rate выше цели"),
            ts=int(_t.time()) - 60, day="2026-09-13", applied=True,
            campaign_id="c1")
    finally:
        s.close()

    marks = c.get("/api/products/c1/s1/series?days=7").json()["decisions"]
    assert len(marks) == 1, marks
    assert marks[0]["action"] == "raise", marks[0]
    assert marks[0]["reason"] == "cart-rate выше цели", marks[0]
    print("✓ api: маркеры решений несут причину")
```

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_webui_api.py
```

Ожидание: FAIL, 404 на `/series`.

- [ ] **Step 3: Реализовать**

В `webui/api/products.py`, внутри `build_router`, после `product_detail`:

```python
    @router.get("/products/{campaign_id}/{sku}/series")
    def product_series(campaign_id: str, sku: str, days: int = 14,
                       user: str = Depends(require_user)):
        """Ряды для графиков. Две шкалы времени лежат в РАЗНЫХ массивах:
        ставка и CPC имеют внутридневное разрешение (несколько тиков в сутки),
        а TACoS/CTR/CR — ровно по одной точке на день. Складывать их в общую
        сетку точек нельзя — получится ложь на обеих осях."""
        days = max(1, min(int(days), 90))
        with open_store(ctx) as store:
            ticks = store.get_snapshot_series(sku, days)
            daily = store.get_metrics_series(sku, days)
            marks = store.get_decision_markers(sku, days)
            values, _ = _effective(store, campaign_id, sku)
        return {
            "ticks": ticks,
            "daily": daily,
            "decisions": marks,
            "corridor": {"low": values["target_tacos_low"],
                         "high": values["target_tacos_high"]},
        }
```

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_webui_api.py && .venv/bin/python test_webui.py
```

- [ ] **Step 5: Коммит**

```bash
git add webui/api/products.py test_webui_api.py
git commit -m "feat(api): /api/products/{cid}/{sku}/series — ряды для графиков

Две шкалы времени лежат в разных массивах: ticks (ставка и CPC по тикам)
и daily (TACoS/CTR/CR по дням). Общая сетка точек врала бы на обеих осях.
Коридор TACoS берётся из ЭФФЕКТИВНОГО конфига товара, с переопределениями."
```

---

### Task 6: API записи — настройки, расписание, режимы

**Files:**
- Create: `webui/api/settings.py`
- Modify: `webui/app.py`
- Test: `test_webui_api.py`

**Interfaces:**
- Consumes: Task 2; `core.settings_io.load_settings/save_settings/validate_settings/SETTINGS_FIELDS`; `Store.set_override/delete_override/set_product_control/log_settings_change/get_settings_audit`; `_live_refresh_snapshot` из `webui/app.py`.
- Produces:
  - `PUT /api/products/{cid}/{sku}/settings` — тело `{"values": {поле: число|null}}`, где `null` означает «наследовать».
  - `PUT /api/products/{cid}/{sku}/control` — тело `{"enabled", "window_start", "window_end", "days_mask"}`.
  - `GET`/`PUT /api/settings` — глобальные настройки.
  - `POST /api/dry-run` — тело `{"dry_run": bool}`.
  - `POST /api/refresh`, `GET /api/audit`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `test_webui_api.py` (и в список вызовов):

```python
def test_put_product_settings_saves_and_clears_overrides():
    """null означает «наследовать» — оверрайд должен удаляться, а не
    записываться нулём. Записанный ноль означал бы «потолок ставки = 0»."""
    c, _, db = _logged_in()
    r = c.put("/api/products/c1/s1/settings",
              json={"values": {"bid_ceiling": 150}})
    assert r.status_code == 200, r.text
    assert c.get("/api/products/c1/s1").json()["values"]["bid_ceiling"] == 150

    r = c.put("/api/products/c1/s1/settings",
              json={"values": {"bid_ceiling": None}})
    assert r.status_code == 200, r.text
    body = c.get("/api/products/c1/s1").json()
    assert "bid_ceiling" not in body["owned"], body["owned"]
    assert body["values"]["bid_ceiling"] == RulesConfig().bid_ceiling, body["values"]
    print("✓ api: null в настройках товара возвращает наследование")


def test_put_product_settings_rejects_min_bid_above_ceiling():
    """Минимальная ставка выше потолка сделала бы решения биддера
    противоречивыми — ловим на входе."""
    c, _, _ = _logged_in()
    r = c.put("/api/products/c1/s1/settings",
              json={"values": {"min_bid": 200, "bid_ceiling": 100}})
    assert r.status_code == 400, r.text
    assert r.json()["errors"], r.json()
    print("✓ api: min_bid выше потолка отвергается")


def test_put_product_settings_rejects_unknown_field():
    """Белый список полей: панель не должна уметь писать в конфиг что угодно."""
    c, _, _ = _logged_in()
    r = c.put("/api/products/c1/s1/settings",
              json={"values": {"нет_такого_поля": 1}})
    assert r.status_code == 400, r.text
    print("✓ api: неизвестное поле настроек отвергается")


def test_put_control_persists_and_validates_window():
    c, _, _ = _logged_in()
    r = c.put("/api/products/c1/s1/control",
              json={"enabled": False, "window_start": 9, "window_end": 23,
                    "days_mask": 127})
    assert r.status_code == 200, r.text
    ctl = c.get("/api/products/c1/s1").json()["control"]
    assert ctl["enabled"] is False and ctl["window_start"] == 9, ctl

    r = c.put("/api/products/c1/s1/control",
              json={"enabled": True, "window_start": 20, "window_end": 5,
                    "days_mask": 127})
    assert r.status_code == 400, r.text
    print("✓ api: расписание сохраняется, окно наизнанку отвергается")


def test_global_settings_roundtrip_and_audit():
    c, rules, _ = _logged_in()
    before = c.get("/api/settings").json()["settings"]
    assert "bid_ceiling" in before, before

    r = c.put("/api/settings", json={"settings": {**before, "bid_ceiling": 321}})
    assert r.status_code == 200, r.text
    assert c.get("/api/settings").json()["settings"]["bid_ceiling"] == 321

    audit = c.get("/api/audit").json()["audit"]
    assert any(a["field"] == "bid_ceiling" for a in audit), audit
    print("✓ api: глобальные настройки сохраняются и попадают в аудит")


def test_dry_run_toggle_does_not_leak_into_settings_save():
    """dry_run — рубильник реальных денег. Он меняется ТОЛЬКО своим
    эндпоинтом, обычное сохранение настроек его трогать не должно."""
    c, _, _ = _logged_in()
    assert c.post("/api/dry-run", json={"dry_run": False}).status_code == 200
    assert c.get("/api/settings").json()["settings"]["dry_run"] is False

    s = c.get("/api/settings").json()["settings"]
    c.put("/api/settings", json={"settings": {**s, "dry_run": True,
                                              "bid_ceiling": 99}})
    assert c.get("/api/settings").json()["settings"]["dry_run"] is False
    print("✓ api: dry_run не меняется через сохранение настроек")


def test_write_endpoints_require_login():
    c, _, _ = _client()
    assert c.put("/api/products/c1/s1/settings",
                 json={"values": {}}, follow_redirects=False).status_code == 401
    assert c.post("/api/dry-run", json={"dry_run": True},
                  follow_redirects=False).status_code == 401
    print("✓ api: запись требует входа")
```

- [ ] **Step 2: Убедиться, что тесты падают**

```bash
.venv/bin/python test_webui_api.py
```

Ожидание: FAIL, 404 на `PUT /api/products/...`.

- [ ] **Step 3: Реализовать**

Создать `webui/api/settings.py`:

```python
"""settings.py — запись настроек: товар, расписание, глобальные, режимы."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from core.config_resolver import OVERRIDABLE_FIELDS
from core.settings_io import (SETTINGS_FIELDS, load_settings, save_settings,
                              validate_settings)
from webui.api.deps import ApiContext, open_store, require_user

ALMATY = ZoneInfo("Asia/Almaty")


def _bad(errors: list[str]):
    raise HTTPException(status_code=400, detail={"errors": errors})


def build_router(ctx: ApiContext, refresh_fn) -> APIRouter:
    """refresh_fn инъектируется: живой пулл ходит в кабинет Kaspi, тесты туда
    ходить не должны."""
    router = APIRouter(prefix="/api")

    @router.put("/products/{campaign_id}/{sku}/settings")
    async def put_product_settings(campaign_id: str, sku: str, request: Request,
                                   user: str = Depends(require_user)):
        body = await request.json()
        values = body.get("values") or {}
        unknown = sorted(set(values) - set(OVERRIDABLE_FIELDS))
        if unknown:
            _bad([f"Неизвестное поле настроек: {f}" for f in unknown])

        errors: list[str] = []
        parsed: dict[str, float | None] = {}
        for field, raw in values.items():
            if raw is None:
                parsed[field] = None          # None = наследовать
                continue
            try:
                parsed[field] = float(raw)
            except (TypeError, ValueError):
                errors.append(f"«{field}»: нужно число, получено {raw!r}")
        if errors:
            _bad(errors)

        # min_bid обязан быть не выше потолка, иначе решения биддера
        # противоречат сами себе. Проверяем по ЭФФЕКТИВНЫМ значениям после
        # правки, а не только по присланным: поменять могли одно из двух.
        with open_store(ctx) as store:
            current = dict(store.get_overrides("sku", sku))
            for field, val in parsed.items():
                if val is None:
                    current.pop(field, None)
                else:
                    current[field] = val
            from core.config_resolver import resolve_config
            from core.rules import load_rules_config
            cfg = resolve_config(load_rules_config(ctx.rules_path),
                                 store.get_overrides("campaign", campaign_id),
                                 current)
            if cfg.min_bid > cfg.bid_ceiling:
                _bad([f"Минимальная ставка ({cfg.min_bid}) выше потолка "
                      f"({cfg.bid_ceiling})"])

            ts = int(time.time())
            for field, val in parsed.items():
                if val is None:
                    store.delete_override("sku", sku, field)
                else:
                    store.set_override("sku", sku, field, val, user=user, ts=ts)
        return {"ok": True}

    @router.put("/products/{campaign_id}/{sku}/control")
    async def put_control(campaign_id: str, sku: str, request: Request,
                          user: str = Depends(require_user)):
        body = await request.json()
        try:
            start = int(body.get("window_start", 0))
            end = int(body.get("window_end", 24))
            mask = int(body.get("days_mask", 127))
        except (TypeError, ValueError):
            _bad(["Окно и дни недели должны быть целыми числами"])

        errors = []
        if not (0 <= start <= 23):
            errors.append("Начало окна — от 0 до 23")
        if not (1 <= end <= 24):
            errors.append("Конец окна — от 1 до 24")
        if start >= end:
            errors.append("Начало окна должно быть раньше конца")
        if not (0 <= mask <= 127):
            errors.append("Маска дней недели — от 0 до 127")
        if errors:
            _bad(errors)

        with open_store(ctx) as store:
            store.set_product_control(campaign_id, sku,
                                      enabled=bool(body.get("enabled", True)),
                                      window_start=start, window_end=end,
                                      days_mask=mask, user=user,
                                      ts=int(time.time()))
        return {"ok": True}

    @router.get("/settings")
    def get_settings(user: str = Depends(require_user)):
        return {"settings": load_settings(ctx.rules_path),
                "fields": SETTINGS_FIELDS}

    @router.put("/settings")
    async def put_settings(request: Request, user: str = Depends(require_user)):
        body = await request.json()
        incoming = body.get("settings") or {}
        cur = load_settings(ctx.rules_path)
        new = dict(cur)
        for f in SETTINGS_FIELDS:
            if f == "dry_run":
                continue      # только через POST /api/dry-run — это рубильник денег
            if f in incoming:
                new[f] = incoming[f]
        errors = validate_settings(new)
        if errors:
            _bad(errors)

        with open_store(ctx) as store:
            ts = int(time.time())
            for f in SETTINGS_FIELDS:
                if f != "dry_run" and str(cur.get(f)) != str(new.get(f)):
                    store.log_settings_change(user, f, cur.get(f), new.get(f), ts)
        save_settings(ctx.rules_path, new)
        return {"ok": True, "settings": load_settings(ctx.rules_path)}

    @router.post("/dry-run")
    async def set_dry_run(request: Request, user: str = Depends(require_user)):
        body = await request.json()
        want = bool(body.get("dry_run", True))
        cur = load_settings(ctx.rules_path)
        new = dict(cur)
        new["dry_run"] = want
        with open_store(ctx) as store:
            if cur.get("dry_run") != want:
                store.log_settings_change(user, "dry_run", cur.get("dry_run"),
                                          want, int(time.time()))
        save_settings(ctx.rules_path, new)
        return {"ok": True, "dry_run": want}

    @router.post("/refresh")
    def refresh(user: str = Depends(require_user)):
        return {"ok": bool(refresh_fn(ctx.db_path))}

    @router.get("/audit")
    def audit(user: str = Depends(require_user)):
        with open_store(ctx) as store:
            return {"audit": store.get_settings_audit()}

    return router
```

В `webui/app.py`:

```python
    from webui.api import settings as api_settings
    app.include_router(api_settings.build_router(api_ctx, _live_refresh_snapshot))
```

- [ ] **Step 4: Убедиться, что тесты проходят**

```bash
.venv/bin/python test_webui_api.py && .venv/bin/python test_webui.py
```

Оба зелёные. Второй особенно важен: обе панели теперь пишут в один `rules.yaml` и одну БД.

- [ ] **Step 5: Прогнать весь набор**

```bash
for t in test_store.py test_worker.py test_webui.py test_webui_api.py \
         test_rules.py test_revenue.py test_reconcile.py test_daypart.py \
         test_config_resolver.py test_settings_io.py test_marketing.py \
         test_session.py test_analyst.py; do
  echo "--- $t"; .venv/bin/python "$t" >/dev/null 2>&1 && echo OK || echo FAIL
done
```

Ожидание: все `OK` — 13 файлов, включая новый.

- [ ] **Step 6: Коммит**

```bash
git add webui/api/settings.py webui/app.py test_webui_api.py
git commit -m "feat(api): запись настроек товара, расписания и глобальных режимов

null в настройках товара означает «наследовать» и удаляет оверрайд, а не
пишет ноль: записанный ноль означал бы «потолок ставки = 0».

min_bid против потолка проверяется по ЭФФЕКТИВНЫМ значениям после правки —
поменять могли любое из двух. dry_run по-прежнему меняется только своим
эндпоинтом: сохранение настроек его не трогает, это рубильник реальных денег."
```

---

### Task 7: API — превью решения, с выносом логики из Jinja-роута

Спек требует эндпоинт «что бот сделает сейчас, ничего не отправляя». Логика для
этого уже есть — но она написана прямо внутри Jinja-роута `sku_preview` в
`webui/app.py`, сорока пятью строками. Копировать её в API нельзя: получилось бы
две независимые копии кода, предсказывающего решения по реальным ставкам, и они
разошлись бы при первой же правке правил. Поэтому логика выносится в общую
функцию, а оба интерфейса начинают звать её.

**Files:**
- Create: `core/preview.py`
- Modify: `webui/app.py` (роут `sku_preview` начинает звать общую функцию; примонтировать роутер)
- Modify: `webui/api/products.py` (добавить эндпоинт)
- Test: `test_preview.py` (новый), `test_webui_api.py`

**Interfaces:**
- Consumes: `core.reconcile.reconcile`, `core.rules.evaluate_fast/evaluate_slow/load_rules_config`, `core.daypart.split_by_control`, `core.config_resolver.resolve_config`, `Store`.
- Produces:
  - `core.preview.preview_decision(store, rules_path, campaign_id, sku, now) -> dict | None` — `None`, если по товару ещё нет снапшота. Иначе словарь: либо `{"control": {"action", "reason"}}`, либо `{"fast": {...}, "slow": {...}}`.
  - `POST /api/products/{campaign_id}/{sku}/preview` → `{"preview": <то же самое>}`.

- [ ] **Step 1: Написать падающий тест на вынесенную функцию**

Создать `test_preview.py`:

```python
"""test_preview.py — превью решения биддера без отправки ставок.

Одна и та же функция обслуживает и Jinja-панель, и JSON API: две копии кода,
предсказывающего решения по реальным деньгам, разошлись бы при первой правке.

Запуск: .venv/bin/python test_preview.py
"""
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from connectors.marketing_client import CampaignProduct
from core.preview import preview_decision
from core.rules import RulesConfig
from core.settings_io import save_settings, SETTINGS_FIELDS
from core.store import Store

ALMATY = ZoneInfo("Asia/Almaty")


def _env():
    d = tempfile.mkdtemp()
    rules = os.path.join(d, "rules.yaml")
    save_settings(rules, {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS})
    return Store(os.path.join(d, "a.db")), rules


def _snap(store, campaign_id, sku, bid=40, clicks=100, carts=5):
    p = CampaignProduct(
        sku=sku, merchant_sku="m" + sku, campaign_product_id=1, bid=bid,
        avg_cpc=bid * 0.7, score=7.0, buy_box=True, product_state="Active",
        cost=500, cost_today=100, gmv=0, crr=0, cr=0, ctr=0, views=clicks * 20,
        clicks=clicks, carts=carts, transactions=0, price=10000)
    store.save_products_snapshot([p], ts=1_700_000_000, campaign_id=campaign_id)


def test_preview_returns_none_without_snapshot():
    """По товару, которого воркер ещё не видел, предсказывать нечего."""
    store, rules = _env()
    assert preview_decision(store, rules, "c1", "нет-такого",
                            datetime.now(ALMATY)) is None
    print("✓ превью: без снапшота возвращает None")


def test_preview_shows_both_loops_for_active_product():
    """Активный товар: показываем оба контура — что решит тормозной и что
    окупаемостный. Владельцу важны оба, они конкурируют."""
    store, rules = _env()
    _snap(store, "c1", "s1")
    got = preview_decision(store, rules, "c1", "s1", datetime.now(ALMATY))
    assert set(got) == {"fast", "slow"}, got
    assert got["fast"]["action"] and got["fast"]["reason"], got["fast"]
    assert got["slow"]["action"] and got["slow"]["reason"], got["slow"]
    print("✓ превью: активный товар показывает оба контура")


def test_preview_reports_control_layer_when_disabled():
    """Выключенный товар до правил не доходит — решение принимает
    контрольный слой, и превью обязано показать именно его."""
    store, rules = _env()
    _snap(store, "c1", "s1")
    store.set_product_control("c1", "s1", enabled=False, window_start=0,
                              window_end=24, days_mask=127, user="a", ts=1)
    got = preview_decision(store, rules, "c1", "s1", datetime.now(ALMATY))
    assert set(got) == {"control"}, got
    assert got["control"]["reason"], got
    print("✓ превью: выключенный товар показывает решение контрольного слоя")


def test_preview_does_not_touch_parking():
    """Превью сухое. Парковку ставки читаем, чтобы показать восстановление,
    но записывать её имеет право только боевой worker.run_tick — иначе
    просмотр страницы менял бы состояние биддера."""
    store, rules = _env()
    _snap(store, "c1", "s1")
    store.set_parked_bid("c1", "s1", 55, ts=1)
    before = dict(store.get_parked_bids("c1"))
    preview_decision(store, rules, "c1", "s1", datetime.now(ALMATY))
    assert store.get_parked_bids("c1") == before, "превью изменило парковку"
    print("✓ превью не трогает запаркованные ставки")


if __name__ == "__main__":
    test_preview_returns_none_without_snapshot()
    test_preview_shows_both_loops_for_active_product()
    test_preview_reports_control_layer_when_disabled()
    test_preview_does_not_touch_parking()
    print("-" * 60)
    print("✓ Все проверки превью прошли")
```

- [ ] **Step 2: Убедиться, что тест падает**

```bash
.venv/bin/python test_preview.py
```

Ожидание: `ModuleNotFoundError: No module named 'core.preview'`.

- [ ] **Step 3: Вынести логику в `core/preview.py`**

Создать `core/preview.py`, перенеся туда тело существующего роута `sku_preview`
из `webui/app.py` **без изменения поведения**:

```python
"""preview.py — «что биддер сделает сейчас», без отправки ставок.

Общая функция для Jinja-панели и JSON API. Держать две копии кода, который
предсказывает решения по реальному рекламному бюджету, нельзя: при первой же
правке правил копии разойдутся, и один из интерфейсов начнёт врать.

Превью СУХОЕ: читает снапшот, выручку из кэша и парковку, но ничего не пишет
и ничего не отправляет в кабинет. Просмотр страницы не должен менять состояние
биддера.
"""
from __future__ import annotations

from datetime import datetime

from connectors.marketing_client import CampaignProduct
from core.config_resolver import resolve_config
from core.daypart import split_by_control
from core.reconcile import reconcile
from core.rules import evaluate_fast, evaluate_slow, load_rules_config


def _product_from_snapshot(snap: dict) -> CampaignProduct:
    """Снапшот в БД — это строка, а правилам нужен CampaignProduct."""
    return CampaignProduct(
        sku=snap["sku"], merchant_sku=snap.get("merchant_sku", ""),
        campaign_product_id=0, bid=snap["bid"],
        avg_cpc=snap.get("avg_cpc", 0), score=snap.get("score", 0),
        buy_box=bool(snap.get("buy_box", False)),
        product_state=snap.get("product_state", "Active"),
        cost=snap.get("cost", 0), cost_today=snap.get("cost_today", 0),
        gmv=0, crr=0, cr=0, ctr=0, views=0,
        clicks=snap.get("clicks", 0), carts=snap.get("carts", 0),
        transactions=0, price=snap.get("price", 0))


def preview_decision(store, rules_path: str, campaign_id: str, sku: str,
                     now: datetime) -> dict | None:
    """Что биддер сделал бы с товаром прямо сейчас.

    None — по товару ещё нет снапшота, предсказывать нечего.
    {"control": {...}} — решение принял контрольный слой (выключен/вне окна),
    до правил дело не дошло.
    {"fast": {...}, "slow": {...}} — оба контура; они конкурируют, и владельцу
    важно видеть каждый.
    """
    snap = store.get_latest_snapshot(sku)
    if snap is None:
        return None

    g = load_rules_config(rules_path)
    camp_ov = store.get_overrides("campaign", campaign_id)
    controls = store.list_product_control(campaign_id)
    reconciled = reconcile([_product_from_snapshot(snap)],
                           store.get_revenue_cache())

    def cfg_for(s):
        return resolve_config(g, camp_ov, store.get_overrides("sku", s.sku))

    def min_bid_for(sk):
        return resolve_config(g, camp_ov, store.get_overrides("sku", sk)).min_bid

    def ceiling_for(sk):
        return resolve_config(g, camp_ov, store.get_overrides("sku", sk)).bid_ceiling

    # Парковку читаем, чтобы показать утреннее восстановление ставки, но НЕ
    # сохраняем и не чистим — это делает только боевой worker.run_tick.
    parked = store.get_parked_bids(campaign_id)
    active, ctrl_dec, _parking = split_by_control(
        reconciled, controls, now, min_bid_for, parked, ceiling_for)

    if ctrl_dec:
        d = ctrl_dec[0]
        return {"control": {"action": d.action, "reason": d.reason}}

    fast = evaluate_fast(active, cfg_for)
    slow = evaluate_slow(active, cfg_for)
    return {
        "fast": {"action": fast[0].action, "reason": fast[0].reason},
        "slow": {"action": slow[0].action, "reason": slow[0].reason},
    }
```

- [ ] **Step 4: Переключить Jinja-роут на общую функцию**

В `webui/app.py` заменить тело роута `sku_preview` так, чтобы оно звало
`preview_decision` вместо собственной копии логики. Шаблон
`sku_settings.html` ожидает словарь вида `{ключ: (действие, причина)}` и
выводит его через `v[0]` и `v[1]` — приведи форму к тому, что шаблон уже умеет,
чтобы шаблон не трогать:

```python
    @app.post("/settings/sku/{campaign_id}/{sku}/preview")
    async def sku_preview(request: Request, campaign_id: str, sku: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        from core.preview import preview_decision
        store = Store(db_path)
        try:
            got = preview_decision(store, rules_path, campaign_id, sku,
                                   datetime.now(ALMATY))
        finally:
            store.close()
        # Шаблон рисует пары (действие, причина) — приводим к его форме,
        # чтобы разметку не трогать.
        preview = ({k: (v["action"], v["reason"]) for k, v in got.items()}
                   if got else None)
        ctx = _control_ctx(request, campaign_id, sku, [])
        ctx["preview"] = preview
        return templates.TemplateResponse(request, "sku_settings.html", ctx)
```

Удалить ставшие ненужными импорты из `webui/app.py`, если после этой правки они
больше нигде в файле не используются — проверь `grep` перед удалением каждого.

- [ ] **Step 5: Добавить эндпоинт в API**

В `webui/api/products.py`, внутри `build_router`:

```python
    @router.post("/products/{campaign_id}/{sku}/preview")
    def product_preview(campaign_id: str, sku: str,
                        user: str = Depends(require_user)):
        from core.preview import preview_decision
        with open_store(ctx) as store:
            got = preview_decision(store, ctx.rules_path, campaign_id, sku,
                                   datetime.now(ALMATY))
        return {"preview": got}
```

Дописать в `test_webui_api.py` (и в список вызовов):

```python
def test_api_preview_returns_both_loops_and_changes_nothing():
    """Превью читает состояние и ничего не пишет: нажатие кнопки в панели не
    должно менять поведение биддера."""
    c, _, db = _logged_in()
    _seed_snapshot(db, "c1", "s1", bid=40, ts=1_700_000_000)

    r = c.post("/api/products/c1/s1/preview")
    assert r.status_code == 200, r.text
    got = r.json()["preview"]
    assert set(got) == {"fast", "slow"}, got
    assert got["fast"]["reason"], got

    s = Store(db)
    try:
        assert s.get_parked_bids("c1") == {}, "превью запарковало ставку"
    finally:
        s.close()
    print("✓ api: превью отдаёт оба контура и ничего не меняет")


def test_api_preview_without_snapshot_is_null_not_error():
    c, _, _ = _logged_in()
    r = c.post("/api/products/c1/нет-такого/preview")
    assert r.status_code == 200, r.text
    assert r.json()["preview"] is None, r.json()
    print("✓ api: превью без снапшота отдаёт null, а не 500")
```

- [ ] **Step 6: Убедиться, что всё зелёное**

```bash
.venv/bin/python test_preview.py && .venv/bin/python test_webui_api.py && .venv/bin/python test_webui.py
```

Третий критичен: существующий тест `test_preview_returns_decision_without_side_effects` в `test_webui.py` проверяет ровно тот роут, который ты только что переписал. Он обязан пройти без правок — это доказательство, что вынос логики не изменил поведение.

- [ ] **Step 7: Прогнать весь набор**

```bash
for t in test_store.py test_worker.py test_webui.py test_webui_api.py \
         test_preview.py test_rules.py test_revenue.py test_reconcile.py \
         test_daypart.py test_config_resolver.py test_settings_io.py \
         test_marketing.py test_session.py test_analyst.py; do
  echo "--- $t"; .venv/bin/python "$t" >/dev/null 2>&1 && echo OK || echo FAIL
done
```

Ожидание: все `OK` — 14 файлов.

- [ ] **Step 8: Коммит**

```bash
git add core/preview.py webui/app.py webui/api/products.py test_preview.py test_webui_api.py
git commit -m "feat(api): превью решения биддера, логика вынесена из Jinja-роута

Превью было написано прямо внутри роута sku_preview сорока пятью строками.
Копировать его в API значило бы держать две независимые копии кода, который
предсказывает решения по реальному бюджету — они разошлись бы при первой же
правке правил. Логика вынесена в core/preview.py, оба интерфейса зовут её.

Существующий тест Jinja-роута проходит без правок — доказательство, что
вынос не изменил поведение. Превью остаётся сухим: парковку читает, но не
пишет, иначе просмотр страницы менял бы состояние биддера."
```

---

## Что этот план НЕ делает

- Не трогает Jinja-панель, её шаблоны и роуты — она остаётся рабочей до третьей части.
- Не пишет ни строчки фронта и не заводит `frontend/`.
- Не реализует ИИ-эндпоинты — это третья часть, вместе с промптами и лимитом расхода.
- Не выносит из Jinja-роутов ничего, кроме превью: остальные роуты доживают до третьей части как есть.
- Не трогает ядро решений и воркер.
- Не настраивает сборку и выкат.

## Готовность к третьей части

После этого плана есть полный JSON API под `/api`, покрытый тестами: вход, кампании, сводка, список товаров, карточка, ряды для графиков, запись настроек и расписания, превью, режимы. Третья часть строит React против него, сносит Jinja и добавляет ИИ.
