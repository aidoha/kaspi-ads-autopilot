"""app.py — FastAPI веб-панель управления биддером. Правит config/rules.yaml
(воркер перечитывает на лету), читает db/autopilot.db для дашборда."""
from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from core.config_resolver import resolve_config, OVERRIDABLE_FIELDS
from core.rules import load_rules_config
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


def _live_refresh_snapshot(db_path: str) -> bool:
    """Разовый read-only пулл кабинета в снапшоты. Best-effort: любая проблема
    (нет кредов/сессия/WAF) → False, без исключения. Ставки НЕ трогаются."""
    try:
        storage_path = os.environ.get("STORAGE_STATE", "storage_state.json")
        merchant_id = os.environ.get("KASPI_MARKETING_MERCHANT_ID", "")
        login = os.environ.get("KASPI_MARKETING_LOGIN", "")
        password = os.environ.get("KASPI_MARKETING_PASSWORD", "")
        if not (storage_path and os.path.exists(storage_path)
                and merchant_id and login and password):
            return False
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
            store = Store(db_path)
            try:
                ts = int(time.time())
                for c in campaigns:
                    products = marketing.get_campaign_products(c.id, today, today)
                    store.save_products_snapshot(products, ts, campaign_id=c.id)
            finally:
                store.close()
        finally:
            marketing.close()
        return True
    except Exception as e:
        log.warning("Живой пулл /refresh не удался (показываю прежние данные): %s", e)
        return False


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
    # https_only: панель торчит в интернет (за Caddy) — без Secure-флага случайный
    # http:// заход слил бы подписанную куку сессии ДО редиректа на https. max_age
    # короче дефолтных 14 дней Starlette — деньги, не забытый логин на форуме.
    app.add_middleware(SessionMiddleware, secret_key=secret,
                       https_only=True, max_age=60 * 60 * 8)

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
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    def login(request: Request, username_in: str = Form(alias="username"),
              password: str = Form(...)):
        if username_in == username and pw_hash and verify_password(password, pw_hash):
            request.session["user"] = username_in
            return RedirectResponse("/settings", status_code=303)
        log.warning("Неудачный вход в веб-панель (username=%s)", username_in)
        return templates.TemplateResponse(
            request, "login.html", {"error": "Неверный логин или пароль"},
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
            decisions_by_sku = store.get_decisions_summary_for_day(day)
            tacos_rows = store.get_tacos_daily(day)
            # Текущая ставка по SKU — из последнего снапшота товара (bid из
            # products_snapshot); присоединяем к строке TACoS.
            for row in tacos_rows:
                snap = store.get_latest_snapshot(row["sku"])
                row["bid"] = snap["bid"] if snap else None
            last_ts = store.get_latest_snapshot_ts()
            sku_names = store.get_sku_name_map()
        finally:
            store.close()
        budgets = _get_campaign_budgets()
        return templates.TemplateResponse(request, "dashboard.html", {
            "user": user(request), "day": day,
            "decisions_by_sku": decisions_by_sku, "tacos": tacos_rows,
            "budgets": budgets, "last_snapshot_ts": last_ts,
            "sku_names": sku_names})

    @app.get("/decisions/{sku}", response_class=HTMLResponse)
    def decisions_for_sku(request: Request, sku: str, page: int = 1):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        per_page = 20
        page = max(1, page)
        day = datetime.now(ALMATY).date().isoformat()
        store = Store(db_path)
        try:
            total = store.count_decisions_for_sku_day(day, sku)
            pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, pages)
            decisions = store.get_decisions_for_sku_day(
                day, sku, per_page, (page - 1) * per_page)
            sku_name = store.get_sku_name_map().get(sku)
        finally:
            store.close()
        return templates.TemplateResponse(request, "decisions_sku.html", {
            "user": user(request), "day": day, "sku": sku, "sku_name": sku_name,
            "decisions": decisions, "page": page, "pages": pages,
            "total": total, "per_page": per_page})

    @app.post("/refresh")
    def refresh(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        _live_refresh_snapshot(db_path)
        return RedirectResponse("/", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_form(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "settings.html", {
            "user": user(request),
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
            return templates.TemplateResponse(request, "settings.html", {
                "user": user(request),
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

    def _effective_and_flags(campaign_id: str, sku: str | None):
        """Эффективные значения переопределяемых полей (глобал → кампания → SKU)
        и множество полей, переопределённых НА ЭТОМ уровне (для бейджа «своё»).
        sku=None → уровень кампании; иначе — уровень товара (наследует кампанию).
        Общий хелпер для страниц настроек кампании (Task 6) и товара (Task 7)."""
        store = Store(db_path)
        try:
            g = load_rules_config(rules_path)
            camp_ov = store.get_overrides("campaign", campaign_id)
            if sku is None:
                eff = resolve_config(g, camp_ov, {})
                owned = set(camp_ov)
            else:
                sku_ov = store.get_overrides("sku", sku)
                eff = resolve_config(g, camp_ov, sku_ov)
                owned = set(sku_ov)
        finally:
            store.close()
        values = {f: getattr(eff, f) for f in OVERRIDABLE_FIELDS}
        return values, owned

    def _save_overrides(request: Request, scope: str, scope_id: str, form,
                        base_sku: str | None, base_campaign: str):
        """Сохраняет overrides уровня `scope` (campaign|sku) для `scope_id` из
        данных формы: поле с галкой «{field}__inherit» = наследовать (override
        удаляется); непустое значение без галки = своё (override пишется).
        Перед записью валидирует ЭФФЕКТИВНЫЙ конфиг (кросс-полевые проверки вроде
        bid_ceiling >= min_bid) — если невалидно, возвращает TemplateResponse с
        ошибками вместо None (вызывающий роут решает, что делать с результатом).
        Общий хелпер для Task 6 (кампания) и Task 7 (товар)."""
        store = Store(db_path)
        try:
            g = load_rules_config(rules_path)
            camp_ov = store.get_overrides("campaign", base_campaign)
            # База для перерисовки формы при ошибке (эффективный конфиг ДО этой
            # правки — наследование без учёта proposed, т.к. proposed мог оказаться
            # мусором и resolve_config() на нём упадёт, см. ниже).
            base_eff = g if scope == "campaign" else resolve_config(g, camp_ov, {})

            def _rerender(errs: list[str], values: dict):
                tpl = "campaign_settings.html" if scope == "campaign" else "sku_settings.html"
                extra = {"campaign_id": base_campaign, "owned": set(proposed)}
                if scope == "sku":
                    extra["sku"] = base_sku
                else:
                    extra["skus"] = store.get_campaign_skus(base_campaign)
                return templates.TemplateResponse(request, tpl, {
                    "user": user(request), "fields": OVERRIDABLE_FIELDS,
                    "values": values, "errors": errs, **extra}, status_code=200)

            # Собираем предполагаемые overrides этого уровня из формы:
            proposed = {}
            for f in OVERRIDABLE_FIELDS:
                if form.get(f"{f}__inherit") == "on":
                    continue  # наследовать → нет override
                raw = (form.get(f) or "").strip()
                if raw != "":
                    proposed[f] = raw

            # Проверка ДО resolve_config()/_coerce(): нечисловой ввод (например,
            # "abc") иначе уронит resolve_config() необработанным ValueError
            # (float("abc")) → 500 вместо перерисовки формы с ошибкой. Ничего не
            # пишем, пока каждое присланное значение не окажется конечным числом.
            num_errs = []
            for f, raw in proposed.items():
                try:
                    v = float(raw)
                    if not math.isfinite(v):
                        num_errs.append(f"{f}: не конечное число")
                except (TypeError, ValueError):
                    num_errs.append(f"{f}: не число")
            if num_errs:
                values = {f: proposed.get(f, getattr(base_eff, f)) for f in OVERRIDABLE_FIELDS}
                return _rerender(num_errs, values)

            # Эффективный конфиг для валидации (кросс-поля: ceiling>=min_bid и т.п.):
            if scope == "campaign":
                eff = resolve_config(g, proposed, {})
            else:
                eff = resolve_config(g, camp_ov, proposed)
            errs = validate_settings({f: getattr(eff, f) for f in SETTINGS_FIELDS})
            if errs:
                # Перерисуем форму с ошибками (значения — из формы/эффективные).
                values = {f: getattr(eff, f) for f in OVERRIDABLE_FIELDS}
                return _rerender(errs, values)
            # Применяем: set для proposed, delete для остального (наследовать).
            existing = store.get_overrides(scope, scope_id)
            ts = int(time.time())
            for f in OVERRIDABLE_FIELDS:
                if f in proposed:
                    old = existing.get(f)
                    store.set_override(scope, scope_id, f, proposed[f], user(request), ts)
                    if str(old) != str(proposed[f]):
                        store.log_settings_change(
                            user(request), f"{scope}:{scope_id}:{f}", old, proposed[f], ts)
                elif f in existing:
                    store.delete_override(scope, scope_id, f)
                    store.log_settings_change(
                        user(request), f"{scope}:{scope_id}:{f}", existing.get(f), "inherit", ts)
        finally:
            store.close()
        return None

    @app.get("/settings/campaign/{campaign_id}", response_class=HTMLResponse)
    def campaign_settings_form(request: Request, campaign_id: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        values, owned = _effective_and_flags(campaign_id, None)
        store = Store(db_path)
        try:
            skus = store.get_campaign_skus(campaign_id)
        finally:
            store.close()
        return templates.TemplateResponse(request, "campaign_settings.html", {
            "user": user(request), "campaign_id": campaign_id,
            "fields": OVERRIDABLE_FIELDS, "values": values, "owned": owned,
            "skus": skus, "errors": []})

    @app.post("/settings/campaign/{campaign_id}")
    async def campaign_settings_save(request: Request, campaign_id: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        resp = _save_overrides(request, "campaign", campaign_id, form,
                               base_sku=None, base_campaign=campaign_id)
        return resp or RedirectResponse(
            f"/settings/campaign/{campaign_id}", status_code=303)

    @app.get("/settings/sku/{campaign_id}/{sku}", response_class=HTMLResponse)
    def sku_settings_form(request: Request, campaign_id: str, sku: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        values, owned = _effective_and_flags(campaign_id, sku)
        return templates.TemplateResponse(request, "sku_settings.html", {
            "user": user(request), "campaign_id": campaign_id, "sku": sku,
            "fields": OVERRIDABLE_FIELDS, "values": values, "owned": owned,
            "errors": []})

    @app.post("/settings/sku/{campaign_id}/{sku}")
    async def sku_settings_save(request: Request, campaign_id: str, sku: str):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        resp = _save_overrides(request, "sku", sku, form,
                               base_sku=sku, base_campaign=campaign_id)
        return resp or RedirectResponse(
            f"/settings/sku/{campaign_id}/{sku}", status_code=303)

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
