"""test_webui_spa.py — отдача собранного фронта из FastAPI.

Каждый тест получает СВОЙ временный каталог сборки через WEBUI_DIST_DIR
(см. webui/app.py) — ни один тест не зависит от того, собран ли фронт в
webui/static/dist на диске у того, кто запускает набор. `dist/` в
.gitignore, поэтому на свежем клоне, в CI и на VPS до первой сборки его
просто нет — набор обязан быть зелёным и тогда, и после сборки.

Запуск: .venv/bin/python test_webui_spa.py
"""
import os
import tempfile

from fastapi.testclient import TestClient

from core.rules import RulesConfig
from core.settings_io import save_settings, SETTINGS_FIELDS
from webui.app import create_app
from webui.auth import hash_password


def _client(dist_dir: str):
    d = tempfile.mkdtemp()
    rules = os.path.join(d, "rules.yaml")
    save_settings(rules, {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS})
    empty_env = os.path.join(d, "empty.env")
    open(empty_env, "w").close()
    os.environ.update(
        UI_USERNAME="admin", UI_PASSWORD_HASH=hash_password("secret"),
        UI_SECRET_KEY="test-secret-key", RULES_CONFIG=rules,
        DB_PATH=os.path.join(d, "a.db"), ENV_FILE=empty_env,
        WEBUI_DIST_DIR=dist_dir,
    )
    return TestClient(create_app(), base_url="https://testserver")


def _built_dist_dir() -> str:
    """Временный каталог, изображающий результат успешного `npm run build`:
    index.html + assets/ — ровно то, что монтирует webui/app.py."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "assets"))
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write("<!doctype html><html><body>SPA собран</body></html>")
    return d


def test_built_dist_serves_index_html_for_unknown_path():
    """Клиентский роутинг: /products/123 обслуживает React, а не сервер.
    Прямой заход по такому адресу (закладка, перезагрузка) обязан отдать
    index.html, иначе пользователь увидит 404 на собственной странице."""
    c = _client(_built_dist_dir())
    r = c.get("/какой-то/клиентский/путь")
    assert r.status_code == 200, (r.status_code, r.text[:200])
    assert "text/html" in r.headers["content-type"], r.headers
    print("✓ spa: собранный фронт отдаёт index.html на неизвестный путь")


def test_built_dist_index_html_is_not_cached():
    """Имена ассетов хешированные, index.html — нет: закешированный браузером
    старый index.html после пересборки ссылался бы на уже удалённые
    /assets/... (404, белый экран). no-store закрывает это."""
    c = _client(_built_dist_dir())
    r = c.get("/")
    assert r.status_code == 200, r.status_code
    assert r.headers.get("cache-control") == "no-store", r.headers
    print("✓ spa: index.html отдаётся с Cache-Control: no-store")


def test_api_paths_are_not_swallowed_by_spa():
    """SPA-фоллбек не должен перехватывать /api — иначе фронт вместо JSON
    получит разметку и сломается на разборе. Не зависит от того, собран ли
    фронт: /api/me отвечает раньше, чем запрос дошёл бы до фоллбека."""
    c = _client(_built_dist_dir())
    r = c.get("/api/me")
    assert r.status_code == 401, r.status_code
    assert r.headers["content-type"].startswith("application/json"), r.headers
    assert r.json()["errors"], r.json()
    print("✓ spa: /api не перехватывается фоллбеком")


def test_missing_dist_returns_503_with_explanation():
    """Отсутствующий dist/ (свежий клон, забытый или оборванный npm run
    build) не должен ронять приложение: фоллбек обязан ответить понятной
    страницей — «Фронт не собран» — а не 200 с пустотой и не исключением.
    Каталог гарантированно не существует: временная директория есть, но
    подкаталог dist в ней не создаётся."""
    d = tempfile.mkdtemp()
    missing_dist = os.path.join(d, "dist-которого-нет")
    c = _client(missing_dist)
    r = c.get("/заведомо/несуществующий/путь")
    assert r.status_code == 503, r.status_code
    assert "text/html" in r.headers["content-type"], r.headers
    assert "Фронт не собран" in r.text, r.text[:200]
    print("✓ spa: отсутствие сборки отдаёт 503 «Фронт не собран», не падает")


if __name__ == "__main__":
    test_built_dist_serves_index_html_for_unknown_path()
    test_built_dist_index_html_is_not_cached()
    test_api_paths_are_not_swallowed_by_spa()
    test_missing_dist_returns_503_with_explanation()
    print("-" * 60)
    print("✓ Все проверки отдачи SPA прошли")
