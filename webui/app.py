"""app.py — FastAPI веб-панель управления биддером. Правит config/rules.yaml
(воркер перечитывает на лету), читает db/autopilot.db для JSON API. Панель —
React SPA (собирается в webui/static/dist), здесь — только API-роутеры,
вход и отдача собранного фронта."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from core.store import Store

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

    # redirect_slashes=False: требование проекта — /api/* никогда не редиректит.
    # 307 на слэше отдал бы фронту редирект там, где он ждёт данные или ошибку.
    app = FastAPI(redirect_slashes=False)

    # Секрет сессий обязателен: без него SessionMiddleware подписывал бы куки
    # публичным дефолтом — любой мог бы подделать {"user": "admin"} и обойти логин
    # в панели, которая управляет реальным рекламным бюджетом. Отказываем сразу.
    secret = os.environ.get("UI_SECRET_KEY") or ""
    if not secret:
        raise RuntimeError("UI_SECRET_KEY не задан — сгенерируй секрет и внеси в .env")
    # https_only: панель торчит в интернет (за Caddy) — без Secure-флага случайный
    # http:// заход слил бы подписанную куку сессии ДО редиректа на https. max_age
    # короче дефолтных 14 дней Starlette — деньги, не забытый логин на форуме.
    # same_site="strict": SPA живёт на том же origin, что API, поэтому
    # кросс-сайтовые запросы с кукой панели не нужны вообще. Строгий
    # режим закрывает CSRF без отдельного токена — для панели, которая тратит
    # рекламный бюджет, это дешёвая и правильная страховка.
    app.add_middleware(SessionMiddleware, secret_key=secret,
                       https_only=True, same_site="strict",
                       max_age=60 * 60 * 8)

    rules_path = os.environ.get("RULES_CONFIG", "config/rules.yaml")
    db_path = os.environ.get("DB_PATH", "db/autopilot.db")
    username = os.environ.get("UI_USERNAME", "admin")
    pw_hash = os.environ.get("UI_PASSWORD_HASH", "")

    from webui.api.deps import ApiContext
    from webui.api import auth as api_auth
    from webui.api import overview as api_overview
    from webui.api import products as api_products
    from webui.api import settings as api_settings

    api_ctx = ApiContext(rules_path=rules_path, db_path=db_path,
                         username=username, pw_hash=pw_hash)
    app.include_router(api_auth.build_router(api_ctx))
    app.include_router(api_overview.build_router(api_ctx, _get_campaign_budgets))
    app.include_router(api_products.build_router(api_ctx))
    app.include_router(api_settings.build_router(api_ctx, _live_refresh_snapshot))

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(RequestValidationError)
    async def _api_validation_error(request: Request, exc: RequestValidationError):
        """Ошибки валидации параметров тоже обязаны иметь форму {"errors": [...]}.
        Фронт разбирает ответы /api/* по одной форме; своя форма у 422 всплыла
        бы как невнятный сбой разбора вместо понятного «неверный параметр»."""
        errors = []
        for e in exc.errors():
            where = " → ".join(str(x) for x in e.get("loc", ()) if x != "body")
            errors.append(f"{where}: {e.get('msg', 'неверное значение')}"
                          if where else str(e.get("msg", "неверное значение")))
        if not request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        return JSONResponse({"errors": errors}, status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _api_error(request: Request, exc: StarletteHTTPException):
        """Ошибки /api/* всегда в одном виде: {"errors": [...]}.

        Регистрируемся на класс Starlette, а не на fastapi.HTTPException:
        маршрутизационные 404 и 405 поднимает сам Starlette своим базовым
        классом, и обработчик на подклассе их не поймал бы. Тогда опечатка в
        пути отдавала бы фронту другую форму ответа, чем все прочие ошибки.

        SPA-фоллбек (отдача index.html для неизвестных путей вне /api) не
        поднимает исключений и сюда не попадает — эта ветка обслуживает
        прочие не-/api ошибки Starlette (например 405 на чужом методе)."""
        if not request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        detail = exc.detail
        if isinstance(detail, dict) and "errors" in detail:
            return JSONResponse(detail, status_code=exc.status_code)
        return JSONResponse({"errors": [str(detail)]}, status_code=exc.status_code)

    # Отдача собранного фронта. Регистрируется ПОСЛЕДНЕЙ: фоллбек ловит всё,
    # что не разобрали роуты выше, и если повесить его раньше, он перехватит
    # и /api-роутеры.
    _DIST = os.path.join(_HERE, "static", "dist")
    if os.path.isdir(_DIST):
        app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")),
                  name="spa-assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    def spa(full_path: str):
        """Любой неизвестный путь → index.html: роутинг у React клиентский,
        и прямой заход по адресу вроде /products/123 должен открывать
        приложение, а не отдавать 404.

        Опечатка внутри /api/* — отдельный случай: это не клиентский маршрут,
        а обращение к несуществующему эндпоинту. Отдаём обычный 404, чтобы
        сработал общий обработчик ошибок API и фронт увидел свою форму
        {"errors": [...]}, а не разметку SPA, которую не разберёт как JSON."""
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        index = os.path.join(_DIST, "index.html")
        if not os.path.exists(index):
            return HTMLResponse(
                "<h1>Фронт не собран</h1>"
                "<p>Выполните <code>npm run build</code> в каталоге frontend.</p>",
                status_code=503)
        with open(index, encoding="utf-8") as f:
            return HTMLResponse(f.read())

    return app
