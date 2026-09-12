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
