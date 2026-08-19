"""test_webui_positions.py — рендер страницы позиций.

Запуск: .venv/bin/python test_webui_positions.py
"""
import json
import os
import tempfile
from fastapi.testclient import TestClient

from webui.auth import hash_password
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
    app = create_app()
    # base_url=https: SessionMiddleware https_only=True (Secure-кука) — без этого
    # httpx-клиент на "http://testserver" не отправил бы куку сессии обратно.
    return TestClient(app, base_url="https://testserver"), db


def seed(db_path):
    from core.store import Store
    s = Store(db_path)
    listing = json.dumps([
        {"rank": 1, "product_id": "111", "title": "Конкурент A", "price": 59900,
         "brand": "X", "is_ad": True},
        {"rank": 2, "product_id": "999", "title": "Наш товар", "price": 47900,
         "brand": "Y", "is_ad": False},
    ], ensure_ascii=False)
    s.put_position_snapshot(1000, "аэрогриль", "Алматы", "999", 2, 2697, listing)
    s.put_position_snapshot(2000, "аэрогриль", "Алматы", "999", 2, 2697, listing)
    s.close()


def test_positions_page_renders_our_rank_and_listing():
    c, db = _client()
    seed(db)
    c.post("/login", data={"username": "admin", "password": "secret"},
          follow_redirects=False)
    r = c.get("/positions")
    assert r.status_code == 200
    body = r.text
    assert "аэрогриль" in body
    assert "Алматы" in body
    assert "Наш товар" in body        # наша подсвеченная карточка
    assert "Конкурент A" in body      # конкурент выше


def test_positions_requires_login():
    c, db = _client()
    seed(db)
    r = c.get("/positions", follow_redirects=False)
    assert r.status_code in (302, 303, 307) and "/login" in r.headers["location"]


if __name__ == "__main__":
    test_positions_page_renders_our_rank_and_listing()
    test_positions_requires_login()
    print("OK test_webui_positions")
