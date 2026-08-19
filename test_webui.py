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
    # Дашборд теперь показывает сводку по товару со ссылкой, а причина — на
    # отдельной странице товара.
    assert "SKU1" in r.text
    assert "/decisions/SKU1" in r.text
    assert "TACoS высокий" not in r.text
    print("✓ webui: дашборд показывает сводку решений по товарам со ссылкой")


def test_decisions_page_paginates_per_sku():
    c, rules = _client()
    from core.store import Store
    from core.rules import Decision
    import time as _t
    db = os.environ["DB_PATH"]
    st = Store(db)
    day = __import__("datetime").date.today().isoformat()
    base = int(_t.time())
    # 25 решений по SKU1 → 2 страницы по 20; плюс одно по SKU2 (не должно течь)
    for i in range(25):
        st.log_decision(
            Decision("SKU1", "M1", 10 + i, 8 + i, "raise", "slow", f"причина-{i}"),
            ts=base + i, day=day, applied=bool(i % 2), campaign_id="C1")
    st.log_decision(Decision("SKU2", "M2", 5, 5, "hold", "slow", "чужая-причина"),
                    ts=base + 999, day=day, applied=True, campaign_id="C2")
    st.close()
    c.post("/login", data={"username": "admin", "password": "secret"})

    # страница 1: 20 строк (сначала свежие), ссылка на 2-ю, чужой SKU2 не просочился
    r1 = c.get("/decisions/SKU1")
    assert r1.status_code == 200
    assert "причина-24" in r1.text and "причина-5" in r1.text
    assert "причина-4" not in r1.text
    assert "чужая-причина" not in r1.text
    assert "?page=2" in r1.text

    # страница 2: остаток (5 самых старых)
    r2 = c.get("/decisions/SKU1?page=2")
    assert r2.status_code == 200
    assert "причина-4" in r2.text and "причина-0" in r2.text
    assert "причина-24" not in r2.text

    # выход за диапазон — клиппится к последней странице, не падает
    r3 = c.get("/decisions/SKU1?page=99")
    assert r3.status_code == 200
    assert "причина-0" in r3.text
    print("✓ webui: страница товара — пагинация по 20, без утечки чужого SKU")


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


def test_campaign_settings_rejects_invalid_input():
    client, rules, db_path = _client_logged_in()
    from core.store import Store
    st = Store(db_path)
    # нечисловое значение — раньше падало необработанным ValueError (500)
    r = client.post("/settings/campaign/C1", data={"bid_ceiling": "abc"})
    assert r.status_code == 200, r.status_code
    assert "не число" in r.text
    assert st.get_overrides("campaign", "C1") == {}
    # кросс-полевая ошибка: потолок ставки ниже минимальной ставки
    r = client.post("/settings/campaign/C1",
                    data={"bid_ceiling": "0", "min_bid": "10"})
    assert r.status_code == 200, r.status_code
    assert st.get_overrides("campaign", "C1") == {}
    st.close()
    print("✓ webui: настройки кампании — невалидный ввод (не число / кросс-поле) не пишет override")


def test_sku_settings_inherits_campaign_then_saves():
    client, rules, db_path = _client_logged_in()
    from core.store import Store
    st = Store(db_path)
    st.set_override("campaign", "C1", "bid_ceiling", "80", "admin", 1)  # кампания даёт 80
    st.close()
    r = client.get("/settings/sku/C1/SKU-1")
    assert r.status_code == 200
    assert "80" in r.text  # SKU наследует потолок кампании
    # переопределяем на уровне SKU
    r = client.post("/settings/sku/C1/SKU-1",
                    data={"bid_ceiling": "120"}, follow_redirects=False)
    assert r.status_code in (303, 302)
    st = Store(db_path)
    assert st.get_overrides("sku", "SKU-1").get("bid_ceiling") == "120"
    st.close()
    print("✓ webui: настройки товара — наследует кампанию, пишет свой override")


def test_dashboard_shows_freshness():
    client, rules, db_path = _client_logged_in()
    from core.store import Store
    from connectors.marketing_client import CampaignProduct
    import time as _t
    st = Store(db_path)
    # Сохраняем один товар, чтобы MAX(ts) был определён
    product = CampaignProduct(
        sku="166350900", merchant_sku="432085472", campaign_product_id=1,
        bid=18, avg_cpc=12.5, score=7.0, buy_box=True, product_state="Active",
        cost=3600, cost_today=420, gmv=97800, crr=0, cr=0, ctr=0,
        views=0, clicks=120, carts=9, transactions=0, price=48900,
    )
    st.save_products_snapshot([product], ts=int(_t.time()), campaign_id="C1")
    st.close()
    r = client.get("/")
    assert r.status_code == 200
    assert "данные на" in r.text
    print("✓ webui: дашборд показывает метку свежести")


def test_refresh_requires_login_and_is_best_effort():
    # Залогинен, но в тестовом окружении нет кредов кабинета (пустой ENV_FILE) →
    # живой пулл невозможен, но роут НЕ должен падать 500-й — best-effort редирект на /.
    client, rules, db_path = _client_logged_in()
    r = client.post("/refresh", follow_redirects=False)
    assert r.status_code in (302, 303), r.status_code
    assert r.headers["location"] == "/"
    # неавторизованный клиент (свежий, без логина) → редирект на /login
    c, _ = _client()
    r2 = c.post("/refresh", follow_redirects=False)
    assert r2.status_code in (302, 303, 307), r2.status_code
    assert "/login" in r2.headers["location"]
    print("✓ webui: /refresh — логин обязателен, best-effort не падает без сессии кабинета")


def test_dt_filter_renders_almaty_not_server_tz():
    """Фильтр времени в дашборде должен показывать Алматы (UTC+5) независимо от
    таймзоны сервера. На VPS (UTC) старая версия рисовала UTC → «биддер встал»."""
    from datetime import datetime, timezone, timedelta
    from webui.app import fmt_ts_almaty
    ts = 1755519664  # произвольный момент
    got = fmt_ts_almaty(ts)
    # Алматы в 2026 — стабильно UTC+5, без перехода на летнее время
    expected = (datetime.fromtimestamp(ts, timezone.utc)
                + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    assert got == expected, (got, expected)
    assert fmt_ts_almaty(None) == ""
    print("✓ webui: dt-фильтр рендерит Алматы (UTC+5), а не таймзону сервера")


if __name__ == "__main__":
    test_dt_filter_renders_almaty_not_server_tz()
    test_password_hash_roundtrip()
    test_settings_requires_login()
    test_login_and_edit_settings()
    test_invalid_settings_rejected()
    test_dry_run_toggle()
    test_settings_save_does_not_touch_dry_run()
    test_dashboard_shows_decisions_from_db()
    test_decisions_page_paginates_per_sku()
    test_campaign_settings_get_and_save()
    test_campaign_settings_rejects_invalid_input()
    test_sku_settings_inherits_campaign_then_saves()
    test_dashboard_shows_freshness()
    test_refresh_requires_login_and_is_best_effort()
    print("-" * 60)
    print("✓ Все проверки webui прошли")
