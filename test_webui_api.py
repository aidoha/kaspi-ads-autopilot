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


def test_products_list_gives_one_row_per_product_not_per_campaign():
    """Товар, который ведётся в двух кампаниях, — это ОДИН товар. Две строки
    с полными метриками в каждой задвоили бы расход при любой сумме по списку."""
    c, _, db = _logged_in()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Asia/Almaty")).date().isoformat()
    _seed_snapshot(db, "c1", "s1", bid=40, ts=1_700_000_000)
    _seed_snapshot(db, "c2", "s1", bid=40, ts=1_700_000_001)
    _seed_metrics(db, day, [("c1", "s1", 1000, 10000, 0, 100, 8),
                            ("c2", "s1", 500, 10000, 0, 40, 2)])

    items = c.get("/api/products?days=7").json()["products"]
    assert len(items) == 1, items
    p = items[0]
    assert sorted(p["campaign_ids"]) == ["c1", "c2"], p
    assert p["cost"] == 1500, p           # сумма обеих кампаний, ОДИН раз
    assert p["revenue"] == 10000, p       # выручка уровня товара, не задвоена
    assert p["clicks"] == 140, p

    # фильтр по кампании оставляет товар, но не размножает его
    items = c.get("/api/products?campaign=c2&days=7").json()["products"]
    assert len(items) == 1 and items[0]["sku"] == "s1", items
    print("✓ api: товар из двух кампаний — одна строка списка")


def test_product_detail_requires_login():
    c, _, _ = _client()
    assert c.get("/api/products/c1/s1", follow_redirects=False).status_code == 401
    assert c.get("/api/products", follow_redirects=False).status_code == 401
    print("✓ api: товары требуют входа")


def test_series_separates_tick_scale_from_day_scale():
    """Ставка живёт по тикам, метрики — по дням. Класть их в одну сетку
    нельзя: тиков за день несколько, а дневная метрика одна."""
    c, _, db = _logged_in()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Asia/Almaty")).date().isoformat()
    import time as _t
    now = int(_t.time())
    _seed_snapshot(db, "c1", "s1", bid=32, ts=now - 7200)
    _seed_snapshot(db, "c1", "s1", bid=36, ts=now - 3600)
    _seed_metrics(db, day, [("c1", "s1", 1000, 10000, 0, 100, 8)])

    r = c.get("/api/products/c1/s1/series?days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["bid"] for p in body["ticks"]] == [32, 36], body["ticks"]
    assert len(body["daily"]) == 1, body["daily"]
    assert body["daily"][0]["day"] == day, body["daily"][0]
    assert abs(body["daily"][0]["tacos"] - 0.1) < 1e-9, body["daily"][0]
    print("✓ api: /series разделяет шкалу тиков и шкалу дней")


def test_series_corridor_reflects_effective_config():
    """Коридор на графике TACoS — это эффективные границы ИМЕННО этого товара,
    с учётом переопределений, а не глобальные значения."""
    c, _, db = _logged_in()
    _seed_snapshot(db, "c1", "s1", bid=32, ts=1_700_000_000)
    s = Store(db)
    try:
        s.set_override("sku", "s1", "target_tacos_high", "0.25", user="a", ts=1)
    finally:
        s.close()

    corridor = c.get("/api/products/c1/s1/series?days=7").json()["corridor"]
    assert abs(corridor["high"] - 0.25) < 1e-9, corridor
    print("✓ api: коридор TACoS берётся из эффективного конфига товара")


def test_series_carries_decision_reasons():
    c, _, db = _logged_in()
    import time as _t
    from core.rules import Decision
    s = Store(db)
    try:
        s.log_decision(
            Decision(sku="s1", merchant_sku="ms1", old_bid=32, new_bid=36,
                     action="raise", loop="slow", reason="cart-rate выше цели"),
            ts=int(_t.time()) - 60, day="2026-09-13", applied=True,
            campaign_id="c1")
    finally:
        s.close()

    marks = c.get("/api/products/c1/s1/series?days=7").json()["decisions"]
    assert len(marks) == 1, marks
    assert marks[0]["action"] == "raise", marks[0]
    assert marks[0]["reason"] == "cart-rate выше цели", marks[0]
    print("✓ api: маркеры решений несут причину")


def test_put_product_settings_saves_and_clears_overrides():
    """null означает «наследовать» — оверрайд должен удаляться, а не
    записываться нулём. Записанный ноль означал бы «потолок ставки = 0»."""
    c, _, db = _logged_in()
    r = c.put("/api/products/c1/s1/settings",
              json={"values": {"bid_ceiling": 150}})
    assert r.status_code == 200, r.text
    assert c.get("/api/products/c1/s1").json()["values"]["bid_ceiling"] == 150

    r = c.put("/api/products/c1/s1/settings",
              json={"values": {"bid_ceiling": None}})
    assert r.status_code == 200, r.text
    body = c.get("/api/products/c1/s1").json()
    assert "bid_ceiling" not in body["owned"], body["owned"]
    assert body["values"]["bid_ceiling"] == RulesConfig().bid_ceiling, body["values"]
    print("✓ api: null в настройках товара возвращает наследование")


def test_put_product_settings_rejects_min_bid_above_ceiling():
    """Минимальная ставка выше потолка сделала бы решения биддера
    противоречивыми — ловим на входе."""
    c, _, _ = _logged_in()
    r = c.put("/api/products/c1/s1/settings",
              json={"values": {"min_bid": 200, "bid_ceiling": 100}})
    assert r.status_code == 400, r.text
    assert r.json()["errors"], r.json()
    print("✓ api: min_bid выше потолка отвергается")


def test_put_product_settings_rejects_unknown_field():
    """Белый список полей: панель не должна уметь писать в конфиг что угодно."""
    c, _, _ = _logged_in()
    r = c.put("/api/products/c1/s1/settings",
              json={"values": {"нет_такого_поля": 1}})
    assert r.status_code == 400, r.text
    print("✓ api: неизвестное поле настроек отвергается")


def test_put_control_persists_and_validates_window():
    c, _, _ = _logged_in()
    r = c.put("/api/products/c1/s1/control",
              json={"enabled": False, "window_start": 9, "window_end": 23,
                    "days_mask": 127})
    assert r.status_code == 200, r.text
    ctl = c.get("/api/products/c1/s1").json()["control"]
    assert ctl["enabled"] is False and ctl["window_start"] == 9, ctl

    r = c.put("/api/products/c1/s1/control",
              json={"enabled": True, "window_start": 20, "window_end": 5,
                    "days_mask": 127})
    assert r.status_code == 400, r.text
    print("✓ api: расписание сохраняется, окно наизнанку отвергается")


def test_global_settings_roundtrip_and_audit():
    c, rules, db = _logged_in()
    before = c.get("/api/settings").json()["settings"]
    assert "bid_ceiling" in before, before

    r = c.put("/api/settings", json={"settings": {**before, "bid_ceiling": 321}})
    assert r.status_code == 200, r.text
    assert c.get("/api/settings").json()["settings"]["bid_ceiling"] == 321

    audit = c.get("/api/audit").json()["audit"]
    assert any(a["field"] == "bid_ceiling" for a in audit), audit
    print("✓ api: глобальные настройки сохраняются и попадают в аудит")


def test_dry_run_toggle_does_not_leak_into_settings_save():
    """dry_run — рубильник реальных денег. Он меняется ТОЛЬКО своим
    эндпоинтом, обычное сохранение настроек его трогать не должно."""
    c, _, _ = _logged_in()
    assert c.post("/api/dry-run", json={"dry_run": False}).status_code == 200
    assert c.get("/api/settings").json()["settings"]["dry_run"] is False

    s = c.get("/api/settings").json()["settings"]
    c.put("/api/settings", json={"settings": {**s, "dry_run": True,
                                              "bid_ceiling": 99}})
    assert c.get("/api/settings").json()["settings"]["dry_run"] is False
    print("✓ api: dry_run не меняется через сохранение настроек")


def test_write_endpoints_require_login():
    c, _, _ = _client()
    assert c.put("/api/products/c1/s1/settings",
                 json={"values": {}}, follow_redirects=False).status_code == 401
    assert c.post("/api/dry-run", json={"dry_run": True},
                  follow_redirects=False).status_code == 401
    print("✓ api: запись требует входа")


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
    test_products_list_gives_one_row_per_product_not_per_campaign()
    test_product_detail_requires_login()
    test_series_separates_tick_scale_from_day_scale()
    test_series_corridor_reflects_effective_config()
    test_series_carries_decision_reasons()
    test_put_product_settings_saves_and_clears_overrides()
    test_put_product_settings_rejects_min_bid_above_ceiling()
    test_put_product_settings_rejects_unknown_field()
    test_put_control_persists_and_validates_window()
    test_global_settings_roundtrip_and_audit()
    test_dry_run_toggle_does_not_leak_into_settings_save()
    test_write_endpoints_require_login()
    print("-" * 60)
    print("✓ Все проверки API прошли")
