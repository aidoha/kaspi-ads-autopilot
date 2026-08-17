"""test_webui.py — оффлайн-тест веб-панели через FastAPI TestClient."""
import os, tempfile
from fastapi.testclient import TestClient

from webui.auth import hash_password, verify_password
from webui.app import create_app
from core.rules import RulesConfig, load_rules_config
from core.settings_io import save_settings, load_settings, SETTINGS_FIELDS


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
    # base_url=https: SessionMiddleware теперь https_only=True (Secure-кука) — в
    # проде это TLS-терминация Caddy; на "http://testserver" httpx-клиент честно
    # не отправил бы Secure-куку обратно, и все "залогинен" проверки бы падали.
    return TestClient(app, base_url="https://testserver"), rules


def _client_logged_in():
    c, rules = _client()
    c.post("/login", data={"username": "admin", "password": "secret"})
    return c, rules, os.environ["DB_PATH"]


def test_password_hash_roundtrip():
    h = hash_password("secret")
    assert verify_password("secret", h) and not verify_password("wrong", h)
    print("✓ webui: pbkdf2 hash/verify")


def test_settings_requires_login():
    c, _ = _client()
    r = c.get("/settings", follow_redirects=False)
    assert r.status_code in (302, 303, 307) and "/login" in r.headers["location"]
    print("✓ webui: /settings без логина → редирект на /login")


def test_login_and_edit_settings():
    c, rules = _client()
    assert c.post("/login", data={"username": "admin", "password": "wrong"},
                  follow_redirects=False).status_code == 200  # остаёмся на форме
    r = c.post("/login", data={"username": "admin", "password": "secret"},
               follow_redirects=False)
    assert r.status_code in (302, 303)
    # правим настройки
    data = load_settings(rules)
    form = {k: ("on" if data[k] is True else "" if data[k] in (None, False) else data[k])
            for k in SETTINGS_FIELDS if k not in ("dry_run", "campaign_ids")}
    form.update(min_bid=3, bid_ceiling=40, campaign_ids="2899523,3032419")
    r = c.post("/settings", data=form, follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    cfg = load_rules_config(rules)
    assert cfg.min_bid == 3 and cfg.bid_ceiling == 40
    assert cfg.campaign_ids == ["2899523", "3032419"]
    print("✓ webui: логин + сохранение настроек пишет rules.yaml")


def test_invalid_settings_rejected():
    c, rules = _client()
    c.post("/login", data={"username": "admin", "password": "secret"})
    data = load_settings(rules)
    form = {k: data[k] for k in SETTINGS_FIELDS if k not in ("dry_run", "campaign_ids")}
    form["bid_ceiling"] = 0     # < min_bid
    r = c.post("/settings", data=form)
    assert r.status_code == 200 and "bid_ceiling" in r.text   # показал ошибку
    assert load_rules_config(rules).bid_ceiling >= 1          # файл не изменён
    print("✓ webui: невалидные настройки отвергнуты, конфиг цел")


def test_dry_run_toggle():
    c, rules = _client()
    c.post("/login", data={"username": "admin", "password": "secret"})
    c.post("/dry-run", data={"dry_run": ""})       # выключить
    assert load_rules_config(rules).dry_run is False
    c.post("/dry-run", data={"dry_run": "on"})     # включить
    assert load_rules_config(rules).dry_run is True
    print("✓ webui: тумблер dry_run пишет конфиг")


def test_settings_save_does_not_touch_dry_run():
    """Форма /settings НЕ шлёт поле dry_run (оно живёт в отдельной форме /dry-run).
    Регрессия: раньше form.get("dry_run") == "on" на отсутствующем поле давал False
    и молча выключал dry_run на КАЖДОМ сохранении настроек — воркер уходил в live
    и начинал тратить реальные деньги без ведома владельца."""
    c, rules = _client()
    c.post("/login", data={"username": "admin", "password": "secret"})
    assert load_rules_config(rules).dry_run is True  # дефолт RulesConfig
    data = load_settings(rules)
    form = {k: data[k] for k in SETTINGS_FIELDS if k not in ("dry_run", "campaign_ids")}
    form["bid_ceiling"] = 45  # валидная правка, не связанная с dry_run
    r = c.post("/settings", data=form, follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    cfg = load_rules_config(rules)
    assert cfg.bid_ceiling == 45
    assert cfg.dry_run is True   # dry_run НЕ должен был измениться
    print("✓ webui: POST /settings не трогает dry_run")


def test_dashboard_shows_decisions_from_db():
    c, rules = _client()
    # наполнить БД решением
    from core.store import Store
    from core.rules import Decision
    import time as _t
    db = os.environ["DB_PATH"]
    st = Store(db)
    day = __import__("datetime").date.today().isoformat()
    st.log_decision(Decision("SKU1", "M1", 10, 8, "lower", "slow", "TACoS высокий"),
                    ts=int(_t.time()), day=day, applied=False, campaign_id="2899523")
    c.post("/login", data={"username": "admin", "password": "secret"})
    r = c.get("/")
    assert r.status_code == 200
    assert "SKU1" in r.text and "TACoS высокий" in r.text
    print("✓ webui: дашборд показывает решения из БД")


def test_campaign_settings_get_and_save():
    client, rules, db_path = _client_logged_in()
    # GET показывает форму с эффективными (глобальными) значениями
    r = client.get("/settings/campaign/C1")
    assert r.status_code == 200
    assert "bid_ceiling" in r.text
    # POST: задаём override bid_ceiling=80 (флаг наследования снят)
    r = client.post("/settings/campaign/C1",
                    data={"bid_ceiling": "80"}, follow_redirects=False)
    assert r.status_code in (303, 302)
    from core.store import Store
    st = Store(db_path)
    assert st.get_overrides("campaign", "C1").get("bid_ceiling") == "80"
    # POST со снятым override (inherit) удаляет строку
    r = client.post("/settings/campaign/C1",
                    data={"bid_ceiling": "80", "bid_ceiling__inherit": "on"},
                    follow_redirects=False)
    assert "bid_ceiling" not in st.get_overrides("campaign", "C1")
    st.close()
    print("✓ webui: настройки кампании — GET форма + POST override/сброс")


if __name__ == "__main__":
    test_password_hash_roundtrip()
    test_settings_requires_login()
    test_login_and_edit_settings()
    test_invalid_settings_rejected()
    test_dry_run_toggle()
    test_settings_save_does_not_touch_dry_run()
    test_dashboard_shows_decisions_from_db()
    test_campaign_settings_get_and_save()
    print("-" * 60)
    print("✓ Все проверки webui прошли")
