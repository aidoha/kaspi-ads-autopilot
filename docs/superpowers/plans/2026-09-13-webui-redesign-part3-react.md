# Редизайн панели, часть 3: React-панель — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить Jinja-панель на React-приложение по утверждённому макету, работающее против уже готового JSON API.

**Architecture:** Новый каталог `frontend/` — Vite + React + TypeScript, без серверного рендера. FastAPI отдаёт собранный `dist` и всё, что не начинается с `/api` и `/static`, чтобы работал клиентский роутинг. Jinja-шаблоны и их роуты удаляются шестой, последней задачей — когда React уже умеет всё, что умела старая панель. Ядро решений и API не меняются вообще.

**Tech Stack:** Vite 6, React 19, TypeScript, Vitest (только на чистые функции). Никаких UI-библиотек, никакого CSS-фреймворка, никакой графической библиотеки — в макете всё своё, и оно короче, чем настройка чужого.

**Spec:** `docs/superpowers/specs/2026-09-12-webui-redesign-design.md`
**Эталон дизайна:** `docs/design/autopilot-mockup.html` — утверждённый владельцем макет. Вёрстка, токены, разметка компонентов и тексты берутся оттуда, а не выдумываются заново.

## Global Constraints

- **Эталон — макет, а не вкус реализатора.** `docs/design/autopilot-mockup.html` содержит готовые CSS-токены, разметку строки товара, тоггла, сегмент-контрола, KPI-строки и графиков. Переноси оттуда. Расхождение с макетом — дефект, даже если «так красивее».
- **Питон-тесты БЕЗ pytest.** Каждый `test_*.py` — скрипт со списком вызовов в блоке `if __name__ == "__main__":`. Новый тест обязан быть дописан в список.
- **Ядро решений и API неприкосновенны.** `core/*`, `worker.py`, `webui/api/*` не меняются ни в одной задаче этого плана. Если фронту чего-то не хватает в API — остановись и скажи, не дописывай эндпоинт.
- **Jinja-панель жива до последней задачи.** До Task 6 `test_webui.py` обязан проходить целиком.
- **Часовой пояс — `Asia/Almaty`.** Сервер отдаёт эпоху-секунды и даты строками; фронт форматирует в этой зоне явно, а не в зоне браузера.
- **Ошибки API всегда `{"errors": [...]}`, коды 400/401.** Фронт разбирает одну форму. На 401 — показывает экран входа, а не молчит.
- **Никаких секретов во фронте.** Токенов нет, авторизация — кука сессии; `fetch` ходит с `credentials: "include"`.
- Комментарии и весь текст интерфейса — по-русски.
- **Коммит после каждой задачи**, сообщение по-русски.

## Токены дизайна (из макета, копировать дословно)

```css
--page:#f4f3f0; --surface:#ffffff; --surface-2:#faf9f7; --surface-3:#f0efec;
--ink:#1a1a18; --ink-2:#6e6c66; --ink-3:#a3a19a;
--hairline:#e6e4de; --hairline-2:#d8d6cf;
--good:#0a8a0a; --good-bg:#e8f5e8; --warn:#a8730a; --warn-bg:#fbf1dd;
--crit:#c43535; --crit-bg:#fbeaea;
--switch-on:#30c157; --switch-off:#d8d6cf;
--s-bid:#1a1a18; --s-cpc:#2a78d6; --s-ctr:#2a78d6; --s-cr:#eb6834;
--s-tacos:#2a78d6; --band:rgba(42,120,214,0.09);
--grid:#ebe9e3; --axis:#d3d1ca; --muted-ink:#8e8c85;
--radius:14px;
--sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
--mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
```

Тёмная тема в макете определена тремя блоками (`:root`, `@media (prefers-color-scheme: dark)` с guard'ом `:root:not([data-theme="light"])`, `:root[data-theme="dark"]`). Переноси все три: цвет, определённый только внутри медиа-запроса, не применится в состоянии «системная тема».

**Синий зарезервирован за графиками.** В интерфейсе акцент — почти чёрный (`--ink`). Цвет в UI означает ровно одно: состояние метрики. Это согласованное решение, не случайность.

## Контракт API, против которого пишем

Всё уже реализовано и покрыто тестами в части 2.

| Метод и путь | Отдаёт |
|---|---|
| `POST /api/login` `{username,password}` | `{user}` либо 401 |
| `POST /api/logout` | `{ok}` |
| `GET /api/me` | `{user}` либо 401 |
| `GET /api/campaigns` | `{campaigns:[{id,name,daily_budget}], budgets_available}` |
| `GET /api/overview?campaign&days` | `{day, days, last_snapshot_ts, totals:{cost,revenue,gmv,clicks,carts,tacos,roas,roas_gmv,cr}}` |
| `GET /api/products?campaign&days` | `{products:[…]}` — **одна строка на товар**, поля `sku, merchant_sku, campaign_ids, name, bid, cost, revenue, clicks, carts, tacos, roas, ctr, cr, enabled, status, bid_spark` |
| `GET /api/products/{cid}/{sku}` | `{sku, campaign_id, name, values, owned, control:{enabled,window_start,window_end,days_mask}, decisions}` |
| `GET /api/products/{cid}/{sku}/series?days` | `{ticks:[{ts,bid,avg_cpc}], daily:[{day,cost,revenue,…,tacos,roas,ctr,cr}], decisions:[{ts,action,old_bid,new_bid,reason}], corridor:{low,high}}` |
| `PUT /api/products/{cid}/{sku}/settings` `{values:{поле:число\|null}}` | `{ok}`; `null` = наследовать |
| `PUT /api/products/{cid}/{sku}/control` `{enabled?,window_start?,window_end?,days_mask?}` | `{ok}`; несланные поля сохраняют текущее значение |
| `POST /api/products/{cid}/{sku}/preview` | `{preview}` — `null`, либо `{control:{action,reason}}`, либо `{fast:{…},slow:{…}}` |
| `GET`/`PUT /api/settings` | `{settings, fields}` |
| `POST /api/dry-run` `{dry_run}` | `{ok, dry_run}` |
| `POST /api/refresh` | `{ok}` |
| `GET /api/audit` | `{audit:[…]}` |

Три вещи, которые фронт обязан учитывать, иначе покажет неправду:

1. **`null` в числах значит «неизвестно», а не ноль.** `tacos: null` — выручки за период нет, величина не определена. Рисуй прочерк или разрыв в линии, никогда не ноль.
2. **`campaign_ids` длиннее одного** означает, что товар ведётся в нескольких кампаниях. Тогда при выбранном фильтре по одной кампании выручка и производные от неё показаны **полностью по товару**, а не по этой кампании — выручка приходит по товару и к кампаниям не привязана. Рядом с KPI в таком случае обязана стоять подпись-оговорка.
3. **Дни в `daily` могут отсутствовать.** Окно календарное, дни без данных просто не приходят. Линия графика в этом месте рвётся, а не соединяется напрямую.

---

### Task 1: Каркас фронта и вход

Первая задача даёт работающий экран входа против настоящего API и отдачу SPA из FastAPI. Дальше каждый экран проверяется вживую, а не в вакууме.

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles/tokens.css`, `frontend/src/api/client.ts`, `frontend/src/features/auth/LoginScreen.tsx`
- Create: `frontend/src/api/format.ts`, `frontend/src/api/format.test.ts`
- Modify: `webui/app.py` (отдача SPA), `.gitignore`
- Test: `test_webui_spa.py` (новый)

**Interfaces:**
- Produces:
  - `api/client.ts`: `apiGet<T>(path: string): Promise<T>`, `apiSend<T>(method, path, body?): Promise<T>`, класс ошибки `ApiError { status: number; errors: string[] }`.
  - `api/format.ts`: `fmtMoney(n: number | null): string`, `fmtPct(n: number | null): string`, `fmtX(n: number | null): string`, `fmtTs(ts: number | null): string`, `tacosHealth(t: number | null): "good" | "warn" | "crit" | "na"`.
  - Задачи 2–5 используют и клиент, и форматтеры.

- [ ] **Step 1: Завести проект**

```bash
cd /Users/aidynibrayev/Desktop/kaspi-ads-autopilot-pkg
mkdir -p frontend/src/{api,styles,components,features/auth,features/products,features/settings}
cd frontend
npm create vite@latest . -- --template react-ts
npm install
npm install --save-dev vitest
```

Если `npm create` захочет очистить непустой каталог — соглашайся только на перезапись своих же файлов, каталог `src/` мы создали сами и он пуст.

В `package.json` добавь скрипт: `"test": "vitest run"`.

- [ ] **Step 2: Прокси на бэкенд в дев-режиме**

`frontend/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Дев-сервер Vite отдаёт фронт, а всё под /api проксирует на uvicorn.
// Так браузер видит один origin, и кука сессии (SameSite=strict) доезжает —
// без прокси она бы просто не отправлялась, и логин не работал бы локально.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { outDir: "../webui/static/dist", emptyOutDir: true },
});
```

- [ ] **Step 3: Написать падающий тест форматтеров**

`frontend/src/api/format.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { fmtMoney, fmtPct, fmtX, tacosHealth } from "./format";

describe("форматтеры", () => {
  it("null — это прочерк, а не ноль", () => {
    // Сервер шлёт null, когда величина НЕ ОПРЕДЕЛЕНА: выручки за период нет,
    // делить не на что. Ноль означал бы «посчитали и получилось ноль» —
    // другое утверждение, и владелец принял бы по нему другое решение.
    expect(fmtMoney(null)).toBe("—");
    expect(fmtPct(null)).toBe("—");
    expect(fmtX(null)).toBe("—");
    expect(fmtMoney(0)).toBe("0");
    expect(fmtPct(0)).toBe("0,0%");
  });

  it("деньги — с разделителем тысяч и без копеек", () => {
    expect(fmtMoney(15200)).toBe("15 200");
    expect(fmtMoney(222381.4)).toBe("222 381");
  });

  it("доли приходят как 0..1 и показываются процентами", () => {
    expect(fmtPct(0.058)).toBe("5,8%");
    expect(fmtPct(0.245)).toBe("24,5%");
  });

  it("ROAS — кратность", () => {
    expect(fmtX(14.6)).toBe("14,6×");
  });

  it("здоровье TACoS: пороги те же, что были в старой панели", () => {
    expect(tacosHealth(0.05)).toBe("good");
    expect(tacosHealth(0.12)).toBe("warn");
    expect(tacosHealth(0.25)).toBe("crit");
    expect(tacosHealth(null)).toBe("na");
  });
});
```

- [ ] **Step 4: Убедиться, что тест падает**

```bash
cd frontend && npm test
```

Ожидание: FAIL — модуля `./format` ещё нет.

- [ ] **Step 5: Реализовать форматтеры и клиент**

`frontend/src/api/format.ts`:

```ts
// Формат чисел панели. Вынесено отдельным модулем, потому что это
// единственная часть фронта, которую имеет смысл покрывать тестами:
// здесь легко ошибиться молча, и ошибка будет про деньги.

const NBSP = " ";

/** null — величина не определена. Прочерк, никогда не ноль. */
export function fmtMoney(n: number | null): string {
  if (n === null || n === undefined) return "—";
  return Math.round(n).toLocaleString("ru-RU").replace(/\s/g, NBSP);
}

/** Доли приходят из API как 0..1. */
export function fmtPct(n: number | null): string {
  if (n === null || n === undefined) return "—";
  return (n * 100).toLocaleString("ru-RU", {
    minimumFractionDigits: 1, maximumFractionDigits: 1,
  }) + "%";
}

export function fmtX(n: number | null): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 1, maximumFractionDigits: 1,
  }) + "×";
}

/** Эпоха-секунды → время Алматы. Явная зона, а не зона браузера: владелец
 *  может открыть панель из другого пояса, и «обновлено 14:32» должно
 *  означать 14:32 в Алматы, где работает биддер. */
export function fmtTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("ru-RU", {
    timeZone: "Asia/Almaty",
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/** Пороги здоровья TACoS — те же, что показывала старая панель. */
export function tacosHealth(t: number | null): "good" | "warn" | "crit" | "na" {
  if (t === null || t === undefined) return "na";
  if (t < 0.10) return "good";
  if (t < 0.18) return "warn";
  return "crit";
}
```

`frontend/src/api/client.ts`:

```ts
export class ApiError extends Error {
  constructor(readonly status: number, readonly errors: string[]) {
    super(errors[0] ?? `Ошибка ${status}`);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    // credentials: кука сессии — единственная авторизация. Токенов нет
    // сознательно: токен в localStorage унесла бы любая XSS.
    credentials: "include",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    // API всегда отвечает {"errors": [...]} — одна форма на все ошибки.
    let errors: string[] = [`Ошибка ${res.status}`];
    try {
      const data = await res.json();
      if (Array.isArray(data?.errors) && data.errors.length) errors = data.errors;
    } catch { /* тело не JSON — оставляем текст по коду */ }
    throw new ApiError(res.status, errors);
  }
  return (await res.json()) as T;
}

export const apiGet = <T,>(path: string) => request<T>("GET", path);
export const apiSend = <T,>(method: "POST" | "PUT", path: string, body?: unknown) =>
  request<T>(method, path, body);
```

- [ ] **Step 6: Токены, оболочка и экран входа**

`frontend/src/styles/tokens.css` — перенеси **все три блока темы** из `docs/design/autopilot-mockup.html` (`:root`, `@media (prefers-color-scheme: dark)` с guard'ом, `:root[data-theme="dark"]`) плюс базовые стили `body`. Копируй значения дословно.

`frontend/src/App.tsx` — оболочка: на старте зовёт `GET /api/me`; пока ответа нет — нейтральное «Загрузка»; при `ApiError` со статусом 401 показывает `LoginScreen`; иначе — заглушку главного экрана с именем пользователя и кнопкой «Выйти».

`frontend/src/features/auth/LoginScreen.tsx` — форма из двух полей и кнопки, стили и разметку бери из блока `.card`/`.btn` макета. Ошибку показывай текстом из `ApiError.errors`, а не общей фразой: пользователь должен видеть, что именно не так.

- [ ] **Step 7: Отдача SPA из FastAPI**

Сначала тест. Создать `test_webui_spa.py`:

```python
"""test_webui_spa.py — отдача собранного фронта из FastAPI.

Запуск: .venv/bin/python test_webui_spa.py
"""
import os
import tempfile

from fastapi.testclient import TestClient

from core.rules import RulesConfig
from core.settings_io import save_settings, SETTINGS_FIELDS
from webui.app import create_app
from webui.auth import hash_password


def _client():
    d = tempfile.mkdtemp()
    rules = os.path.join(d, "rules.yaml")
    save_settings(rules, {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS})
    empty_env = os.path.join(d, "empty.env")
    open(empty_env, "w").close()
    os.environ.update(
        UI_USERNAME="admin", UI_PASSWORD_HASH=hash_password("secret"),
        UI_SECRET_KEY="test-secret-key", RULES_CONFIG=rules,
        DB_PATH=os.path.join(d, "a.db"), ENV_FILE=empty_env,
    )
    return TestClient(create_app(), base_url="https://testserver")


def test_unknown_path_serves_spa_not_404():
    """Клиентский роутинг: /products/123 обслуживает React, а не сервер.
    Прямой заход по такому адресу (закладка, перезагрузка) обязан отдать
    index.html, иначе пользователь увидит 404 на собственной странице."""
    c = _client()
    r = c.get("/какой-то/клиентский/путь")
    assert r.status_code == 200, (r.status_code, r.text[:200])
    assert "text/html" in r.headers["content-type"], r.headers
    print("✓ spa: неизвестный путь отдаёт index.html")


def test_api_paths_are_not_swallowed_by_spa():
    """SPA-фоллбек не должен перехватывать /api — иначе фронт вместо JSON
    получит разметку и сломается на разборе."""
    c = _client()
    r = c.get("/api/me")
    assert r.status_code == 401, r.status_code
    assert r.headers["content-type"].startswith("application/json"), r.headers
    assert r.json()["errors"], r.json()
    print("✓ spa: /api не перехватывается фоллбеком")


def test_missing_build_does_not_crash_the_app():
    """Собранного фронта может не быть — свежий клон, забытый npm run build.
    Панель обязана отдать понятное сообщение, а не упасть при старте."""
    c = _client()
    r = c.get("/")
    assert r.status_code in (200, 503), r.status_code
    print("✓ spa: отсутствие сборки не роняет приложение")


if __name__ == "__main__":
    test_unknown_path_serves_spa_not_404()
    test_api_paths_are_not_swallowed_by_spa()
    test_missing_build_does_not_crash_the_app()
    print("-" * 60)
    print("✓ Все проверки отдачи SPA прошли")
```

Прогони — должен упасть. Затем в `webui/app.py`, **в самом конце `create_app`, после всех роутов**, добавь:

```python
    # Отдача собранного фронта. Регистрируется ПОСЛЕДНЕЙ: фоллбек ловит всё,
    # что не разобрали роуты выше, и если повесить его раньше, он перехватит
    # и /api, и Jinja-страницы.
    _DIST = os.path.join(_HERE, "static", "dist")
    if os.path.isdir(_DIST):
        app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")),
                  name="spa-assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    def spa(full_path: str):
        """Любой неизвестный путь → index.html: роутинг у React клиентский,
        и прямой заход по адресу вроде /products/123 должен открывать
        приложение, а не отдавать 404."""
        index = os.path.join(_DIST, "index.html")
        if not os.path.exists(index):
            return HTMLResponse(
                "<h1>Фронт не собран</h1>"
                "<p>Выполните <code>npm run build</code> в каталоге frontend.</p>",
                status_code=503)
        with open(index, encoding="utf-8") as f:
            return HTMLResponse(f.read())
```

- [ ] **Step 8: Игнорировать сборку и зависимости**

В `.gitignore` добавь:

```
# Фронтенд
frontend/node_modules/
webui/static/dist/
```

Каталог `dist` в гит не кладём: его собирает CI в отдельную ветку (это часть 4).

- [ ] **Step 9: Проверить всё**

```bash
cd frontend && npm test && npm run build && cd ..
for t in test_webui.py test_webui_api.py test_webui_spa.py test_preview.py; do
  echo "--- $t"; .venv/bin/python "$t" >/dev/null 2>&1 && echo OK || echo FAIL
done
```

Ожидание: тесты Vitest зелёные, сборка проходит, все четыре питон-файла `OK`. `test_webui.py` критичен — Jinja-панель обязана продолжать работать.

- [ ] **Step 10: Коммит**

```bash
git add frontend/ webui/app.py .gitignore test_webui_spa.py
git commit -m "feat(ui): каркас React-панели, вход и отдача SPA из FastAPI

Vite + React + TS в frontend/, дев-сервер проксирует /api на uvicorn —
иначе кука сессии с SameSite=strict не доехала бы и логин не работал бы
локально. FastAPI отдаёт собранный dist и любой неизвестный путь как
index.html: роутинг у React клиентский.

Фоллбек зарегистрирован последним, иначе перехватил бы /api и Jinja.
Отсутствие сборки даёт понятное 503, а не падение при старте.

Тестами покрыты форматтеры: null означает «не определено» и рисуется
прочерком — ноль был бы другим утверждением про деньги."
```

---

### Task 2: Экран товаров

Главный экран макета: шапка с фильтрами, строка KPI, список товаров с тогглерами.

**Files:**
- Create: `frontend/src/components/{Switch,Segmented,Sparkline,Tag}.tsx`
- Create: `frontend/src/features/products/{ProductsScreen,KpiBand,ProductRow}.tsx`
- Create: `frontend/src/features/products/types.ts`
- Modify: `frontend/src/App.tsx` (показывать экран товаров)
- Test: `frontend/src/features/products/sparkline.test.ts`

**Interfaces:**
- Consumes: `apiGet`, `ApiError`, форматтеры (Task 1).
- Produces:
  - `types.ts`: типы `Product`, `Overview`, `Campaign` — ровно по контракту API из шапки плана.
  - `components/Switch.tsx`: `<Switch checked onChange disabled? label />` — iOS-тоггл из макета.
  - `components/Segmented.tsx`: `<Segmented options value onChange />`.
  - `components/Sparkline.tsx`: `<Sparkline values />` + чистая функция `sparkPath(values, w, h)`.
  - Task 3 встраивает панель товара в `ProductRow`.

- [ ] **Step 1: Написать падающий тест геометрии спарклайна**

`frontend/src/features/products/sparkline.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { sparkPath } from "../../components/Sparkline";

describe("геометрия спарклайна", () => {
  it("пустой ряд не даёт пути вместо падения", () => {
    expect(sparkPath([], 84, 24)).toBe("");
    expect(sparkPath([5], 84, 24)).toBe("");
  });

  it("плоский ряд рисуется линией, а не делением на ноль", () => {
    // Ставка часто не меняется сутками. min === max, и наивная формула
    // (v-min)/(max-min) дала бы NaN, а путь стал бы невидимым.
    const d = sparkPath([10, 10, 10], 84, 24);
    expect(d).toMatch(/^M/);
    expect(d).not.toMatch(/NaN/);
  });

  it("точки идут слева направо и умещаются в рамку", () => {
    const d = sparkPath([1, 5, 3], 100, 20);
    const xs = [...d.matchAll(/[ML](-?[\d.]+)/g)].map((m) => Number(m[1]));
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...xs)).toBeLessThanOrEqual(100);
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

```bash
cd frontend && npm test
```

Ожидание: FAIL — `sparkPath` не существует.

- [ ] **Step 3: Компоненты из макета**

`Switch.tsx` — перенеси разметку и стили `.switch/.track/.knob` из макета: скрытый `input[type=checkbox]`, дорожка, кружок, переход `transform`. Обязательно `aria-label`, обязательное видимое состояние фокуса.

`Segmented.tsx` — `.segmented` из макета: группа кнопок, активная помечена `aria-pressed="true"`.

`Tag.tsx` — `.tag.good/.warn/.crit` для TACoS.

`Sparkline.tsx` — SVG из макета плюс экспортируемая чистая функция:

```tsx
/** Путь спарклайна. Вынесен из компонента, чтобы геометрию можно было
 *  проверить тестом: ошибка здесь рисует молча неверную картинку, и её
 *  никто не заметит. */
export function sparkPath(values: number[], w: number, h: number): string {
  if (values.length < 2) return "";        // одной точкой линию не построить
  const pad = 3;
  const min = Math.min(...values), max = Math.max(...values);
  // Плоский ряд — обычное дело: ставка не меняется сутками. Без этой
  // защиты (v-min)/(max-min) дало бы NaN и путь исчез бы.
  const span = max - min || 1;
  const x = (i: number) => pad + (i * (w - pad * 2)) / (values.length - 1);
  const y = (v: number) => h - pad - ((v - min) / span) * (h - pad * 2);
  return values
    .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
    .join(" ");
}
```

- [ ] **Step 4: Экран**

`KpiBand.tsx` — строка KPI из макета: хайрлайны сверху и снизу, разделители между ячейками, без карточек. Показывает расход, выручку, TACoS, ROAS и «клики → корзины».

**Обязательно:** если среди товаров текущего фильтра есть хоть один с `campaign_ids.length > 1` и выбрана конкретная кампания — под KPI выводится подпись: «Выручка показана полностью по товару: она приходит по товару и к кампаниям не привязана». Это требование спека, а не украшение.

`ProductRow.tsx` — строка списка: название и код, спарклайн ставки, ставка, TACoS тегом, CTR, ROAS, тоггл. Клик по строке раскрывает панель (в этой задаче — заглушка, наполняется в Task 3). Тоггл не должен раскрывать строку: гаси всплытие.

Тоггл шлёт `PUT /api/products/{первая кампания товара}/{sku}/control` с телом `{enabled}` — остальные поля API сохранит сам. Обновляй состояние оптимистично и откатывай при ошибке, показывая текст из `ApiError`.

`ProductsScreen.tsx` — шапка (фильтр кампании, период 7/14/30 сегмент-контролом, индикатор тестового режима), `KpiBand`, список. Грузит `/api/campaigns`, `/api/overview`, `/api/products` параллельно. Пустой список — понятный текст, а не пустое полотно.

- [ ] **Step 5: Проверить**

```bash
cd frontend && npm test && npm run build && cd ..
.venv/bin/python test_webui.py && .venv/bin/python test_webui_api.py
```

- [ ] **Step 6: Посмотреть глазами**

```bash
.venv/bin/uvicorn webui.app:create_app --factory --port 8000 &
cd frontend && npm run dev
```

Открой `http://localhost:5173`, войди, убедись что список рисуется и тоггл переключается. **Останови оба процесса после проверки.** Если данных в локальной БД нет — засей `scripts/seed_dev_db.py` и укажи `DB_PATH=db/dev_seed.db`.

- [ ] **Step 7: Коммит**

```bash
git add frontend/
git commit -m "feat(ui): экран товаров — KPI, список, тогглеры

Строка KPI на хайрлайнах, без карточек, как в утверждённом макете.
Тоггл шлёт только enabled: API сохраняет несланные поля расписания сам,
иначе переключение сбрасывало бы рабочее окно товара.

Под KPI появляется оговорка про атрибуцию, когда выбрана одна кампания,
а среди товаров есть мульти-кампанийные: выручка приходит по товару и к
кампаниям не привязана.

Геометрия спарклайна вынесена в чистую функцию и покрыта тестом —
плоский ряд (ставка не менялась сутками) иначе давал бы NaN."
```

---

### Task 3: Панель товара — настройки и решения

**Files:**
- Create: `frontend/src/features/products/{ProductPanel,SettingsTab,DecisionsTab}.tsx`
- Modify: `frontend/src/features/products/ProductRow.tsx` (встроить панель)

**Interfaces:**
- Consumes: Task 1 и 2; эндпоинты карточки, настроек, расписания, превью.
- Produces: `ProductPanel` с сегмент-контролом «Графики / Настройки / Решения». Вкладка «Графики» в этой задаче — заглушка с текстом, наполняется в Task 4.

- [ ] **Step 1: Панель и вкладки**

`ProductPanel.tsx` — по раскрытию строки грузит `GET /api/products/{cid}/{sku}`. Сегмент-контрол переключает вкладки. Кнопка «Разобрать с ИИ» присутствует по макету, но **выключена с подписью «появится позже»** — эндпоинтов ИИ ещё нет, это следующая часть. Не выдумывай их и не прячь кнопку: место под неё в макете есть.

- [ ] **Step 2: Вкладка настроек**

`SettingsTab.tsx` — 14 полей из `values`. У каждого:
- подпись и пояснение — **возьми тексты из `field_meta` в `webui/templates/sku_settings.html`**, они уже написаны по-русски и выверены; не сочиняй заново;
- тоггл «наследовать»: включён, если поле НЕ в `owned`. Включённый тоггл блокирует ввод;
- сохранение шлёт `PUT .../settings` с телом `{values: {...}}`, где унаследованные поля идут как `null`.

Ошибки валидации с сервера показывай списком над формой, дословно — там осмысленные русские тексты, включая проверку «минимальная ставка выше потолка».

Ниже — блок расписания: тоггл «биддер активен», часы окна, дни недели тогглами. Шлёт `PUT .../control`.

Ещё ниже — кнопка «Проверить сейчас» (`POST .../preview`) и вывод результата. Три формы ответа: `null` — «по товару ещё нет данных»; `{control}` — одна строка про решение контрольного слоя; `{fast, slow}` — обе строки. Подпиши, что это предсказание и ничего не отправлено.

- [ ] **Step 3: Вкладка решений**

`DecisionsTab.tsx` — таблица из `decisions`: время, действие бейджем (`raise` зелёный, `lower` красный, `hold` серый), причина, «было → стало». Разметку бери из макета, блок `.dec-row`.

- [ ] **Step 4: Проверить**

```bash
cd frontend && npm test && npm run build && cd ..
.venv/bin/python test_webui.py && .venv/bin/python test_webui_api.py
```

Затем подними дев-сервер и проверь вживую: раскрой товар, переключи вкладки, поменяй поле, сними «наследовать», сохрани, нажми «Проверить сейчас». Останови процессы после проверки.

- [ ] **Step 5: Коммит**

```bash
git add frontend/
git commit -m "feat(ui): панель товара — настройки, расписание, решения, превью

Тексты подписей и пояснений к 14 полям перенесены из Jinja-шаблона:
они уже выверены по-русски, сочинять заново значило бы терять точность.

Чекбоксы «наследовать» заменены тогглами по макету; унаследованные поля
уходят на сервер как null, что удаляет переопределение, а не пишет ноль.

Кнопка «Разобрать с ИИ» на месте, но выключена: эндпоинтов ИИ ещё нет."
```

---

### Task 4: Графики

**Files:**
- Create: `frontend/src/components/LineChart.tsx`, `frontend/src/components/chart.ts`, `frontend/src/components/chart.test.ts`
- Create: `frontend/src/features/products/ChartsTab.tsx`
- Modify: `frontend/src/features/products/ProductPanel.tsx`

**Interfaces:**
- Consumes: `GET /api/products/{cid}/{sku}/series`.
- Produces: `chart.ts` — чистые функции `scale(values, size, pad)`, `linePath(points)`, `segments(points)`; `LineChart` — SVG-компонент с осями, сеткой, кросс-хэйром и тултипом.

- [ ] **Step 1: Написать падающие тесты геометрии**

`frontend/src/components/chart.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { segments, linePath } from "./chart";

describe("геометрия графика", () => {
  it("разрыв в данных рвёт линию, а не соединяет через него", () => {
    // Дни без данных не приходят с сервера (окно календарное). Соединить
    // соседние точки прямой значило бы нарисовать динамику, которой не было.
    const pts = [
      { x: 0, y: 1 }, { x: 1, y: 2 }, { x: 5, y: 3 }, { x: 6, y: 4 },
    ];
    const segs = segments(pts, 1);
    expect(segs.length).toBe(2);
    expect(segs[0].length).toBe(2);
    expect(segs[1].length).toBe(2);
  });

  it("сплошной ряд остаётся одним сегментом", () => {
    const pts = [{ x: 0, y: 1 }, { x: 1, y: 2 }, { x: 2, y: 3 }];
    expect(segments(pts, 1).length).toBe(1);
  });

  it("путь не содержит NaN на плоском ряде", () => {
    const d = linePath([{ x: 0, y: 5 }, { x: 1, y: 5 }]);
    expect(d).not.toMatch(/NaN/);
  });
});
```

- [ ] **Step 2: Убедиться, что падает**

```bash
cd frontend && npm test
```

- [ ] **Step 3: Реализовать геометрию и компонент**

`chart.ts` — чистые функции. `segments(points, maxGap)` разбивает ряд на непрерывные куски: если расстояние между соседними точками по X больше `maxGap`, начинается новый сегмент. Каждый сегмент рисуется своим `<path>`.

`LineChart.tsx` — по образцу графиков из макета: сетка, подписи осей цветом `--muted-ink`, линии толщиной 2, точка и подпись на конце каждой серии, кросс-хэйр с тултипом по наведению. Значения вне пределов не рисуем, подписи не наезжают друг на друга.

Правила, которым следуем (skill `dataviz`, уже применялись в макете):
- **никаких двух осей** — две величины разного масштаба идут двумя графиками;
- цвет закреплён за сущностью и не переназначается при фильтрации;
- подписи носят цвет чернил, а не цвет серии;
- у каждой серии при двух и более — легенда.

- [ ] **Step 4: Вкладка графиков**

`ChartsTab.tsx` — четыре графика из макета:

1. **Ставка и цена клика** — две линии в ₸ по `ticks`, плюс треугольные маркеры из `decisions` (вверх зелёный, вниз красный) с причиной в тултипе.
2. **TACoS** — линия по `daily` с залитым коридором из `corridor`.
3. **CTR** и 4. **Конверсия в корзину** — два маленьких графика рядом.

Период переключается сегмент-контролом 7/14/30 и перезапрашивает `/series`.

Если рядов нет — текст «данные ещё копятся», а не пустая рамка: после выката истории действительно нет несколько дней, и пустая рамка выглядела бы поломкой.

- [ ] **Step 5: Проверить и посмотреть глазами**

```bash
cd frontend && npm test && npm run build && cd ..
```

Подними дев-сервер, открой панель товара, вкладку «Графики». Проверь: линии рисуются, тултип работает, коридор виден, разрыв в данных не соединён прямой. Останови процессы.

- [ ] **Step 6: Коммит**

```bash
git add frontend/
git commit -m "feat(ui): графики ставки, TACoS, CTR и конверсии

Разрывы в данных рвут линию, а не соединяются прямой: дни без метрик не
приходят с сервера, и соединение нарисовало бы динамику, которой не было.
Геометрия вынесена в чистые функции и покрыта тестами.

Двух осей нет нигде: величины разного масштаба идут разными графиками.
График TACoS несёт залитый целевой коридор из эффективного конфига товара."
```

---

### Task 5: Экран настроек биддера

Глобальные пороги, тестовый режим и аудит. Без него снос Jinja оставил бы
владельца без доступа к общим настройкам.

**Files:**
- Create: `frontend/src/features/settings/{SettingsScreen,DryRunCard,AuditTable}.tsx`
- Modify: `frontend/src/App.tsx` (переключение между экраном товаров и настройками)

**Interfaces:**
- Consumes: `GET`/`PUT /api/settings`, `POST /api/dry-run`, `POST /api/refresh`, `GET /api/audit`.
- Produces: экран `/settings` клиентского роутинга.

**Дизайн.** В макете этого экрана нет — он показывает только товары. Значит
строй его на тех же токенах и тех же приёмах: сгруппированная плашка с
хайрлайнами между строками, тоггл вместо чекбокса, заголовок как на главной.
Ничего нового не изобретай.

- [ ] **Step 1: Форма настроек**

`SettingsScreen.tsx` — поля из `GET /api/settings` (`settings` + `fields`).
`dry_run` и `campaign_ids` в общей форме **не показываем**: первый живёт в
отдельной карточке (см. шаг 2), второй — список id кампаний, он редактируется
строкой через запятую отдельным полем внизу.

Подписи и пояснения к полям **возьми из `field_meta` в
`webui/templates/settings.html`** — они уже написаны и выверены. Там же есть
особый случай `tacos_window_days` с подсказками 2/7/14/30 дней; перенеси его
как поле с подсказками, а не как обычное число.

Сохранение — `PUT /api/settings` с телом `{settings: {...}}`. Ошибки валидации
показывай списком над формой дословно: сервер возвращает осмысленные русские
тексты.

- [ ] **Step 2: Карточка тестового режима**

`DryRunCard.tsx` — отдельная карточка с тогглом, как `.toggle-card` в старом
шаблоне `settings.html`.

**Выключение тестового режима обязано требовать подтверждения.** Это момент,
когда биддер начинает тратить настоящие деньги в кабинете Kaspi. Диалог с
явным текстом: «Выключить тестовый режим? Биддер начнёт отправлять реальные
ставки и тратить бюджет». Включение обратно подтверждения не требует —
оно останавливает трату, а не начинает.

Шлёт `POST /api/dry-run` с `{dry_run}`.

Состояние тестового режима должно быть видно и на главном экране — в макете
это индикатор в шапке. Подними его в общее состояние приложения, чтобы обе
страницы показывали одно и то же, а не расходились после переключения.

- [ ] **Step 3: Аудит**

`AuditTable.tsx` — таблица из `GET /api/audit`: время, пользователь, параметр,
было, стало. Пусто — «правок ещё не было».

- [ ] **Step 4: Навигация и обновление данных**

В `App.tsx` — переключение между экраном товаров и настройками. Роутер ставить
не нужно: экрана два, состояния достаточно. Но адрес в браузере должен
меняться (`history.pushState`), иначе перезагрузка страницы вернёт на главную,
а отдача SPA уже умеет открывать любой путь.

Кнопка «Обновить сейчас» (`POST /api/refresh`) — на экране товаров, рядом с
отметкой свежести данных, как в макете. По успеху перезапрашивает обзор и
список.

- [ ] **Step 5: Проверить**

```bash
cd frontend && npm test && npm run build && cd ..
.venv/bin/python test_webui.py && .venv/bin/python test_webui_api.py
```

Затем вживую: открой настройки, поменяй поле, сохрани, проверь что оно
появилось в аудите; переключи тестовый режим туда и обратно и убедись, что
подтверждение спрашивается только при выключении, а индикатор в шапке главной
меняется вместе с ним. Останови процессы после проверки.

- [ ] **Step 6: Коммит**

```bash
git add frontend/
git commit -m "feat(ui): экран настроек биддера, тестовый режим и аудит

Подписи полей перенесены из Jinja-шаблона: они выверены, переписывать
заново значило бы терять точность формулировок.

Выключение тестового режима требует подтверждения — это момент, когда
биддер начинает тратить настоящие деньги. Включение обратно подтверждения
не требует: оно трату останавливает.

Состояние режима поднято в общее состояние приложения, чтобы индикатор в
шапке главной и тоггл в настройках не расходились после переключения."
```

---

### Task 6: Снос Jinja-панели

Последняя задача. К этому моменту React умеет всё, что умела старая панель.

**Files:**
- Delete: `webui/templates/` целиком, `webui/static/app.css`
- Modify: `webui/app.py` (удалить Jinja-роуты и хелперы)
- Modify: `test_webui.py` (переписать под то, что осталось)
- Modify: `deploy/DEPLOY.md`

**Interfaces:**
- Produces: `webui/app.py` без Jinja — только API-роутеры, отдача SPA и вход.

- [ ] **Step 1: Убедиться, что React покрывает всё**

Пройди по списку удаляемых роутов и подтверди, что у каждого есть замена в React: `/` → экран товаров; `/settings` → экран настроек; `/settings/campaign/{id}` → **удаляется без замены, это согласованное решение спека**; `/settings/sku/{cid}/{sku}` → панель товара; `/decisions/{sku}` → вкладка решений; `/refresh`, `/dry-run` → кнопки в интерфейсе.

Если что-то не покрыто — остановись и скажи, не удаляй.

- [ ] **Step 2: Удалить**

```bash
git rm -r webui/templates
git rm webui/static/app.css
```

Из `webui/app.py` удалить: импорт и создание `Jinja2Templates`, фильтр `dt`, монтирование `/static` (если после удаления `app.css` каталог пуст — проверь), функцию `fmt_ts_almaty`, все Jinja-роуты (`/login` GET, `/login` POST, `/logout`, `/`, `/decisions/{sku}`, `/refresh`, `/settings` GET/POST, `/settings/campaign/...`, `/settings/sku/...` и их хелперы `_effective_and_flags`, `_save_overrides`, `_validate_window`, `_control_ctx`, `/dry-run`).

**Не удаляй:** `_get_campaign_budgets` и `_live_refresh_snapshot` — их используют API-роутеры.

- [ ] **Step 3: Переписать `test_webui.py`**

От файла остаётся проверка того, что не покрыто другими тестами: хеширование пароля (`test_password_hash_roundtrip`) и то, что приложение поднимается. Остальные 19 тестов проверяли Jinja-роуты, которых больше нет — удали их вместе с вызовами из списка.

Убедись, что покрытие не потерялось: каждый удаляемый тест либо проверял разметку (уходит вместе с ней), либо имеет аналог в `test_webui_api.py`. Составь этот список в отчёте — если найдёшь тест без аналога, скажи, не удаляй молча.

- [ ] **Step 4: Обновить документацию выката**

В `deploy/DEPLOY.md` дополни раздел обновления: перед рестартом нужен собранный фронт. Пока сборку в CI не завели (это следующая часть), это `cd frontend && npm ci && npm run build`.

- [ ] **Step 5: Проверить всё**

```bash
cd frontend && npm test && npm run build && cd ..
for t in test_store.py test_worker.py test_webui.py test_webui_api.py \
         test_webui_spa.py test_preview.py test_rules.py test_revenue.py \
         test_reconcile.py test_daypart.py test_config_resolver.py \
         test_settings_io.py test_marketing.py test_session.py test_analyst.py; do
  echo "--- $t"; .venv/bin/python "$t" >/dev/null 2>&1 && echo OK || echo FAIL
done
grep -rn "templates\|Jinja\|TemplateResponse" webui/ --include=*.py
```

Ожидание: все 15 файлов `OK`, последний grep — пустой.

- [ ] **Step 6: Коммит**

```bash
git add -A
git commit -m "refactor(ui): снести Jinja-панель, React заменил её целиком

Шаблоны, app.css и все серверные роуты страниц удалены. В webui/app.py
остались API-роутеры, вход и отдача SPA.

Экран настроек кампании удалён без замены — согласованное решение спека:
уровень кампании остаётся в резолвере конфига, но управляется он теперь
только через настройки товара.

test_webui.py сокращён до проверок, не покрытых другими файлами: разметку
проверять больше нечем, поведение покрыто test_webui_api.py."
```

---

## Что этот план НЕ делает

- Не реализует ИИ: эндпоинты `/api/ai/*`, промпты, лимит расхода — следующая часть. Кнопка в интерфейсе есть, но выключена.
- Не настраивает GitHub Actions и выкат — следующая часть. Пока фронт собирается руками.
- Не трогает `core/*`, `worker.py` и `webui/api/*`.
- Не меняет схему БД.

## Готовность к следующей части

После этого плана панель целиком на React, Jinja нет, API не менялся. Остаётся ИИ (эндпоинты + кнопка + дневной разбор) и автоматическая сборка с выкатом.
