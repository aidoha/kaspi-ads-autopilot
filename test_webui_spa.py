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
    Фоллбек обязан отдать понятное сообщение, а не упасть.
    Путь берём заведомо неизвестный: корень до сноса Jinja принадлежит старой
    панели, и через него фоллбек не проверить."""
    c = _client()
    r = c.get("/заведомо/несуществующий/путь")
    assert r.status_code in (200, 503), r.status_code
    assert "text/html" in r.headers["content-type"], r.headers
    print("✓ spa: отсутствие сборки не роняет приложение")


if __name__ == "__main__":
    test_unknown_path_serves_spa_not_404()
    test_api_paths_are_not_swallowed_by_spa()
    test_missing_build_does_not_crash_the_app()
    print("-" * 60)
    print("✓ Все проверки отдачи SPA прошли")
