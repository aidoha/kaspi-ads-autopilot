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


def test_unknown_api_path_uses_the_same_error_shape():
    """Фронт разбирает ответы /api/* по одной форме. Опечатка в пути не
    должна отдавать другую — иначе ошибка всплывёт как сбой разбора, а не
    как понятное «нет такого эндпоинта»."""
    c, _, _ = _logged_in()
    r = c.get("/api/нет-такого-эндпоинта")
    assert r.status_code == 404, r.text
    assert r.json()["errors"], r.json()

    # Jinja-роуты не затронуты: их 404 читает человек, а не fetch.
    r = c.get("/нет-такой-страницы")
    assert r.status_code == 404, r.text
    assert "errors" not in r.json(), r.json()
    print("✓ api: неизвестный путь внутри /api отдаёт ту же форму ошибки")


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


if __name__ == "__main__":
    test_api_without_session_returns_401_json_not_redirect()
    test_api_login_sets_session_and_me_returns_user()
    test_api_login_rejects_wrong_password()
    test_api_logout_clears_session()
    test_unknown_api_path_uses_the_same_error_shape()
    test_jinja_panel_still_works_alongside_api()
    test_overview_totals_sum_across_products()
    test_overview_filters_by_campaign()
    test_overview_empty_db_returns_zeros_not_error()
    test_overview_requires_login()
    test_campaigns_survive_unavailable_cabinet()
    test_products_list_joins_metrics_and_bid()
    test_products_list_reports_disabled_product()
    test_product_detail_returns_effective_config_and_owned_fields()
    test_product_detail_requires_login()
    print("-" * 60)
    print("✓ Все проверки API прошли")
