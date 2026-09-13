"""test_webui.py — то, что осталось непокрытым другими тестами после сноса
Jinja-панели (task-6 плана webui-redesign-part3-react).

Раньше здесь жили 19 тестов Jinja-роутов (/login, /, /decisions/{sku},
/settings, /settings/campaign/{id}, /settings/sku/{cid}/{sku}, /dry-run,
/refresh) — сама разметка удалена вместе с webui/templates/. Их поведение
либо ушло вместе с разметкой (Jinja больше нет — рендерить нечего), либо
имеет аналог в test_webui_api.py, который проверяет тот же функционал через
JSON API, используемый React-панелью. Подробное соответствие — в отчёте
task-6-report.md.

Что остаётся проверять именно здесь:
- хеширование пароля (webui.auth) — общая утилита, не привязана ни к
  Jinja, ни к API-роутерам, аналога в test_webui_api.py нет;
- то, что приложение вообще поднимается (create_app без Jinja не падает,
  /assets и /{full_path} на месте) — без Jinja-роутов никто больше не
  проверяет сам факт успешного старта create_app().
"""
import os
import tempfile

from fastapi.testclient import TestClient

from webui.auth import hash_password, verify_password
from webui.app import create_app
from core.rules import RulesConfig
from core.settings_io import save_settings, SETTINGS_FIELDS


def _client():
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
    # base_url=https: SessionMiddleware https_only=True (Secure-кука) — в
    # проде это TLS-терминация Caddy; на "http://testserver" httpx-клиент
    # честно не отправил бы Secure-куку обратно.
    return TestClient(create_app(), base_url="https://testserver")


def test_password_hash_roundtrip():
    h = hash_password("secret")
    assert verify_password("secret", h) and not verify_password("wrong", h)
    print("✓ webui: pbkdf2 hash/verify")


def test_app_boots_and_serves_spa_fallback():
    """create_app() без Jinja не падает (нет мёртвых импортов/ссылок на
    templates), а неизвестный путь вне /api отдаёт SPA-страницу (200/503,
    HTML), а не 404 — раньше корень принадлежал Jinja, теперь React."""
    c = _client()
    r = c.get("/какой-то-путь-react-роутера")
    assert r.status_code in (200, 503), r.text
    assert "text/html" in r.headers["content-type"], r.headers
    # опечатка внутри /api/* — это НЕ клиентский маршрут SPA, а 404 в форме API
    r_api = c.get("/api/нет-такого-эндпоинта")
    assert r_api.status_code == 404
    assert "errors" in r_api.json(), r_api.json()
    print("✓ webui: приложение поднимается без Jinja, SPA-фоллбек и /api живы")


if __name__ == "__main__":
    test_password_hash_roundtrip()
    test_app_boots_and_serves_spa_fallback()
    print("-" * 60)
    print("✓ Все проверки webui прошли")
