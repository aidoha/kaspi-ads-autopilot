"""app.py — FastAPI веб-панель управления биддером. Правит config/rules.yaml
(воркер перечитывает на лету), читает db/autopilot.db для дашборда."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from core.settings_io import load_settings, save_settings, validate_settings, SETTINGS_FIELDS
from core.store import Store
from webui.auth import verify_password

log = logging.getLogger("webui")

_HERE = os.path.dirname(__file__)
ALMATY = ZoneInfo("Asia/Almaty")

# Кэш бюджетов кампаний на модульном уровне: дашборд может открываться часто,
# а бюджеты живут в кабинете (Playwright-логин, PUT-чувствительная сессия) —
# не хотим долбить его на каждый рендер и конфликтовать с воркером за сессию.
_BUDGET_CACHE_TTL = 60.0
_budget_cache: dict = {"ts": 0.0, "budgets": {}}


def _get_campaign_budgets() -> dict:
    """Бюджеты активных кампаний кабинета — best-effort. Любая проблема (нет
    storage_state, нет кредов, кабинет недоступен) молча гасится: дашборд
    рендерится без бюджетов, а не падает 500-й. Кэш на ~60с."""
    now = time.time()
    if now - _budget_cache["ts"] < _BUDGET_CACHE_TTL:
        return _budget_cache["budgets"]

    budgets: dict = {}
    try:
        storage_path = os.environ.get("STORAGE_STATE", "storage_state.json")
        merchant_id = os.environ.get("KASPI_MARKETING_MERCHANT_ID", "")
        login = os.environ.get("KASPI_MARKETING_LOGIN", "")
        password = os.environ.get("KASPI_MARKETING_PASSWORD", "")
        if storage_path and os.path.exists(storage_path) and merchant_id and login and password:
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
                budgets = {c.id: {"name": c.name, "daily_budget": c.daily_budget}
                          for c in campaigns}
            finally:
                marketing.close()
    except Exception as e:
        # Best-effort: сессия кабинета недоступна/протухла/блокирована — дашборд
        # не должен падать из-за этого, просто покажем без бюджетов.
        log.warning("Бюджеты кампаний недоступны, показываю дашборд без них: %s", e)
        budgets = {}

    _budget_cache["ts"] = now
    _budget_cache["budgets"] = budgets
    return budgets


def create_app() -> FastAPI:
    # Подхватить .env (для uvicorn через systemd), не перекрывая уже заданное окружение.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.environ.get("ENV_FILE", "config/.env"), override=False)
    except ImportError:
        pass

    app = FastAPI()

    # Секрет сессий обязателен: без него SessionMiddleware подписывал бы куки
    # публичным дефолтом — любой мог бы подделать {"user": "admin"} и обойти логин
    # в панели, которая управляет реальным рекламным бюджетом. Отказываем сразу.
    secret = os.environ.get("UI_SECRET_KEY") or ""
    if not secret:
        raise RuntimeError("UI_SECRET_KEY не задан — сгенерируй секрет и внеси в .env")
    app.add_middleware(SessionMiddleware, secret_key=secret)

    app.mount("/static", StaticFiles(directory=os.path.join(_HERE, "static")), name="static")
    templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))
    templates.env.filters["dt"] = lambda ts: (
        datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S") if ts is not None else "")

    rules_path = os.environ.get("RULES_CONFIG", "config/rules.yaml")
    db_path = os.environ.get("DB_PATH", "db/autopilot.db")
    username = os.environ.get("UI_USERNAME", "admin")
    pw_hash = os.environ.get("UI_PASSWORD_HASH", "")

    def user(request: Request):
        return request.session.get("user")

    def _settings_audit() -> list[dict]:
        store = Store(db_path)
        try:
            return store.get_settings_audit()
        finally:
            store.close()

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    @app.post("/login")
    def login(request: Request, username_in: str = Form(alias="username"),
              password: str = Form(...)):
        if username_in == username and pw_hash and verify_password(password, pw_hash):
            request.session["user"] = username_in
            return RedirectResponse("/settings", status_code=303)
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный логин или пароль"},
            status_code=200)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        day = datetime.now(ALMATY).date().isoformat()
        store = Store(db_path)
        try:
            decisions = store.get_decisions_for_day(day)
            tacos_rows = store.get_tacos_daily(day)
            # Текущая ставка по SKU — из последнего снапшота товара (bid из
            # products_snapshot); присоединяем к строке TACoS.
            for row in tacos_rows:
                snap = store.get_latest_snapshot(row["sku"])
                row["bid"] = snap["bid"] if snap else None
        finally:
            store.close()
        budgets = _get_campaign_budgets()
        return templates.TemplateResponse("dashboard.html", {
            "request": request, "user": user(request), "day": day,
            "decisions": decisions, "tacos": tacos_rows, "budgets": budgets})

    @app.get("/settings", response_class=HTMLResponse)
    def settings_form(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse("settings.html", {
            "request": request, "user": user(request),
            "s": load_settings(rules_path), "fields": SETTINGS_FIELDS, "errors": [],
            "audit": _settings_audit()})

    @app.post("/settings")
    async def settings_save(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        cur = load_settings(rules_path)
        new = dict(cur)
        for f in SETTINGS_FIELDS:
            if f == "dry_run":
                continue  # dry_run — только через POST /dry-run, не трогаем при сохранении настроек
            elif f == "campaign_ids":
                raw = (form.get("campaign_ids") or "").strip()
                new[f] = [x.strip() for x in raw.split(",") if x.strip()] or None
            else:
                new[f] = form.get(f)
        errors = validate_settings(new)
        if errors:
            return templates.TemplateResponse("settings.html", {
                "request": request, "user": user(request),
                "s": new, "fields": SETTINGS_FIELDS, "errors": errors,
                "audit": _settings_audit()}, status_code=200)
        store = Store(db_path)
        try:
            ts = int(time.time())
            for f in SETTINGS_FIELDS:
                if f == "dry_run":
                    continue
                if str(cur.get(f)) != str(new.get(f)):
                    store.log_settings_change(user(request), f, cur.get(f), new.get(f), ts)
        finally:
            store.close()
        save_settings(rules_path, new)
        return RedirectResponse("/settings", status_code=303)

    @app.post("/dry-run")
    async def dry_run_toggle(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        cur = load_settings(rules_path)
        old = cur["dry_run"]
        new_val = form.get("dry_run") == "on"
        if new_val != old:
            cur["dry_run"] = new_val
            save_settings(rules_path, cur)
            store = Store(db_path)
            try:
                store.log_settings_change(user(request), "dry_run", old, new_val, int(time.time()))
            finally:
                store.close()
        return RedirectResponse("/settings", status_code=303)

    return app
