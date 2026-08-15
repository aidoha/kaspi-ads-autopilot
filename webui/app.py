"""app.py — FastAPI веб-панель управления биддером. Правит config/rules.yaml
(воркер перечитывает на лету), читает db/autopilot.db для дашборда."""
from __future__ import annotations

import os
import time
from datetime import datetime

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from core.settings_io import load_settings, save_settings, validate_settings, SETTINGS_FIELDS
from core.store import Store
from webui.auth import verify_password

_HERE = os.path.dirname(__file__)


def create_app() -> FastAPI:
    # Подхватить .env (для uvicorn через systemd), не перекрывая уже заданное окружение.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.environ.get("ENV_FILE", "config/.env"), override=False)
    except ImportError:
        pass

    app = FastAPI()
    app.add_middleware(SessionMiddleware,
                       secret_key=os.environ.get("UI_SECRET_KEY", "dev-insecure"))
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
        # Фаза 2 наполнит; пока — заглушка со ссылкой на настройки.
        return templates.TemplateResponse("dashboard.html",
                                          {"request": request, "user": user(request)})

    @app.get("/settings", response_class=HTMLResponse)
    def settings_form(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse("settings.html", {
            "request": request, "user": user(request),
            "s": load_settings(rules_path), "fields": SETTINGS_FIELDS, "errors": [],
            "audit": Store(db_path).get_settings_audit()})

    @app.post("/settings")
    async def settings_save(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        cur = load_settings(rules_path)
        new = dict(cur)
        for f in SETTINGS_FIELDS:
            if f == "dry_run":
                new[f] = form.get("dry_run") == "on"
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
                "audit": Store(db_path).get_settings_audit()}, status_code=200)
        store = Store(db_path)
        ts = int(time.time())
        for f in SETTINGS_FIELDS:
            if str(cur.get(f)) != str(new.get(f)):
                store.log_settings_change(user(request), f, cur.get(f), new.get(f), ts)
        save_settings(rules_path, new)
        return RedirectResponse("/settings", status_code=303)

    @app.post("/dry-run")
    async def dry_run_toggle(request: Request):
        if not user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        cur = load_settings(rules_path)
        cur["dry_run"] = form.get("dry_run") == "on"
        save_settings(rules_path, cur)
        Store(db_path).log_settings_change(user(request), "dry_run",
                                           not cur["dry_run"], cur["dry_run"], int(time.time()))
        return RedirectResponse("/settings", status_code=303)

    return app
