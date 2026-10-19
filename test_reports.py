"""
test_reports.py — разбор выгрузок Kaspi и сводка «деньги по товару».

Файлы собираются прямо в тесте в том же виде, что отдаёт кабинет: CSV с
«;», двойным BOM и запятой в дробях; .xlsx архива заказов с inline-строками
и датами-серийниками Excel.

Запуск: .venv/bin/python test_reports.py
"""
import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime

from fastapi.testclient import TestClient

from core.report_files import (KIND_ADS, KIND_BONUS, KIND_CAMPAIGNS, KIND_ORDERS,
                               ReportError, parse_report, period_from_filename)
from core.reports import ReportsStore

BOM2 = "﻿﻿"
ADS_HEAD = ("Рекламируемый товар;Товар;Текущий статус;Просмотры;Клики;CTR;Ср. стоим. клика;"
            "Расходы на рекламу;Сумма заказов;Все заказы;Прямые заказы;Сопутствующие заказы;CR;"
            "Номера заказов;В избранное;В корзину;Доля рекламных расходов;"
            "Товары из сопутствующих заказов")
BONUS_HEAD = ("SKU;Наименование;Статус;Просмотры;Клики;В избранное;В корзину;Заказы;"
              "Сумма заказов;Выплачено бонусов клиентам;Осталось товаров по акции;Номера заказов")
CAMP_HEAD = ("Наименование;Дата начала;Дата окончания;Статус;Просмотры;Клики;В избранное;"
             "В корзину;Заказы;Сумма заказов;Выплачено бонусов клиентам")
ORDER_HEAD = ["Номер заказа", "Артикул", "Название товара", "Категория код", "Категория",
              "Количество", "Сумма", "Цена", "Вид доставки", "Состояние", "Статус", "Склад",
              "Дата поступления заказа", "Дата передачи", "Стоимость доставки для продавца"]


def ads_csv(*rows) -> bytes:
    """rows: (kaspi_sku, name, views, clicks, cost, ad_revenue, ad_orders, "n1, n2")."""
    lines = [BOM2 + ADS_HEAD]
    for sku, name, views, clicks, cost, rev, orders, nums in rows:
        lines.append(f"{sku};{name};Активный;{views};{clicks};1,5;10,00;"
                     f"{str(cost).replace('.', ',')};{rev},00;{orders};{orders};0;2;"
                     f"{nums};1;2;10;")
    return "\n".join(lines).encode("utf-8")


def bonus_csv(*rows) -> bytes:
    lines = [BOM2 + BONUS_HEAD]
    for sku, name, orders, rev, bonus, nums in rows:
        lines.append(f"{sku};{name};Активна;100;10;1;1;{orders};{rev},00;{bonus},00;;{nums}")
    return "\n".join(lines).encode("utf-8")


def camp_csv(*rows) -> bytes:
    lines = [BOM2 + CAMP_HEAD]
    for name, bonus in rows:
        lines.append(f"{name};2026-10-01;;Активна;100;10;1;1;3;30000,00;{bonus},00")
    return "\n".join(lines).encode("utf-8")


def _serial(dt: datetime) -> float:
    return (dt - datetime(1899, 12, 30)).total_seconds() / 86400


def orders_xlsx(*orders) -> bytes:
    """orders: (order_no, merchant_sku, name, qty, amount, status, datetime, delivery)."""
    def cell(ref, v):
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'
    cols = "ABCDEFGHIJKLMNO"
    rows_xml = []
    all_rows = [ORDER_HEAD]
    for no, msku, name, qty, amount, status, dt, deliv in orders:
        all_rows.append([no, msku, name, "Master - X", "Кат", qty, amount, amount,
                         "ZAMMLER", "ARCHIVE", status, "PP1", _serial(dt), 0.0, deliv])
    for i, row in enumerate(all_rows, start=1):
        rows_xml.append(f'<row r="{i}">' + "".join(cell(f"{cols[j]}{i}", v)
                                                   for j, v in enumerate(row)) + "</row>")
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns='
             '"http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             + "".join(rows_xml) + "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


D = lambda day, h=12: datetime(2026, 10, day, h, 0)
P = "2026-10-13 - 2026-10-19 Отчёт по товарам.csv"


def _store():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "a.db")
    return ReportsStore.open(path), path


def _load(rs, filename, data):
    return rs.save_report(parse_report(filename, data), filename, data, user="admin")


def _base_orders():
    return orders_xlsx(
        ("1001", "AAA", "Бритва AY-610", 1, 10000, "COMPLETED", D(14), 1500),
        ("1002", "AAA", "Бритва AY-610", 2, 20000, "COMPLETED", D(15), 0),
        ("1003", "AAA", "Бритва AY-610", 1, 10000, "CANCELLED", D(16), 0),
        ("1004", "BBB", "Набор для стрижки AY-101", 1, 8000, "COMPLETED", D(17), 0),
        ("0999", "AAA", "Бритва AY-610", 1, 9000, "COMPLETED", D(10), 0),   # прошлый период
    )


# ---------------------------------------------------------------- разбор

def test_period_from_filename():
    assert period_from_filename("2026-10-01 - 2026-10-19 Отчёт по товарам (4).csv") == \
        ("2026-10-01", "2026-10-19")
    assert period_from_filename("Отчёт.csv") is None
    assert period_from_filename("2026-10-19 - 2026-10-01 x.csv") is None   # перепутан
    print("✓ период берётся из имени выгрузки")


def test_detects_kind_by_header_not_filename():
    """Реклама и бонусы приходят под одним именем «Отчёт по товарам»."""
    a = parse_report(P, ads_csv(("111", "Бритва", 100, 5, "1234,5", 10000, 1, "1001")))
    b = parse_report(P, bonus_csv(("111", "Бритва", 1, 10000, 500, "1001")))
    c = parse_report(P.replace("товарам", "кампаниям"), camp_csv(("Бритвы", 500)))
    o = parse_report("Архив.xlsx", _base_orders())
    assert (a.kind, b.kind, c.kind, o.kind) == (KIND_ADS, KIND_BONUS, KIND_CAMPAIGNS, KIND_ORDERS)
    assert a.rows[0]["cost"] == 1234.5 and a.rows[0]["order_numbers"] == ["1001"], a.rows
    assert a.rows[0]["sku"] == "111", "двойной BOM не должен прилипать к первому полю"
    assert b.rows[0]["bonus"] == 500
    assert (o.period_start, o.period_end) == ("2026-10-10", "2026-10-17")
    first = [r for r in o.rows if r["order_no"] == "1001"][0]
    assert first["day"] == "2026-10-14" and first["qty"] == 1 and first["status"] == "COMPLETED"
    print("✓ тип файла — по шапке, числа с запятой и даты Excel разбираются")


def test_csv_without_period_in_name_is_rejected():
    try:
        parse_report("report.csv", ads_csv(("111", "Бритва", 1, 1, 1, 0, 0, "")))
    except ReportError as e:
        assert "период" in str(e), e
    else:
        raise AssertionError("без периода CSV принимать нельзя — некуда отнести расход")
    print("✓ CSV без периода в имени отклоняется с понятным текстом")


def test_unknown_files_are_rejected():
    for name, data in (("x.csv", b"a;b;c\n1;2;3"), ("x.xlsx", b"PK\x03\x04garbage"), ("x.csv", b"")):
        try:
            parse_report(name, data)
        except ReportError:
            continue
        raise AssertionError(f"чужой файл {name} принят")
    print("✓ чужие и битые файлы отклоняются")


# ---------------------------------------------------------------- сводка

def test_dashboard_links_kaspi_code_to_article_via_order_numbers():
    """Названия в отчётах и архиве расходятся — связь только по номерам заказов."""
    rs, _ = _store()
    _load(rs, "Архив.xlsx", _base_orders())
    _load(rs, P, ads_csv(("K610", "Электробритва Ayora AY-610", 1000, 50, 5000, 30000, 2,
                          "1001, 1002"),
                         ("K101", "Триммер для бороды", 500, 20, 2000, 8000, 1, "1004")))
    d = rs.dashboard("2026-10-13", "2026-10-19")
    by = {p["key"]: p for p in d["products"]}
    assert set(by) == {"AAA", "BBB"}, by.keys()
    a = by["AAA"]
    assert a["revenue"] == 30000 and a["orders"] == 2 and a["units"] == 3, a
    assert a["cancelled"] == 1 and a["ad_cost"] == 5000 and a["kaspi_skus"] == ["K610"]
    assert a["tacos"] == 5000 / 30000 and a["drr"] == 5000 / 30000
    assert d["totals"]["revenue"] == 38000 and d["totals"]["ad_cost"] == 7000
    print("✓ код карточки склеивается с артикулом по номерам заказов")


def test_unlinked_product_falls_back_to_name_then_stays_separate():
    rs, _ = _store()
    _load(rs, "Архив.xlsx", _base_orders())
    _load(rs, P, ads_csv(("K1", "Набор для стрижки AY-101", 10, 1, 100, 0, 0, ""),
                         ("K2", "Мясорубка", 10, 1, 300, 0, 0, "")))
    by = {p["key"]: p for p in rs.dashboard("2026-10-13", "2026-10-19")["products"]}
    assert by["BBB"]["ad_cost"] == 100, "совпадение названия — запасная связь"
    assert by["kaspi:K2"]["ad_cost"] == 300 and by["kaspi:K2"]["merchant_sku"] is None
    print("✓ без номеров заказов — связь по названию, иначе отдельная строка")


def test_profit_needs_cost_and_commission():
    rs, _ = _store()
    _load(rs, "Архив.xlsx", _base_orders())
    _load(rs, P, ads_csv(("K610", "x", 1000, 50, 5000, 30000, 2, "1001, 1002")))
    _load(rs, P.replace(".csv", " (2).csv"), bonus_csv(("K610", "x", 2, 30000, 900, "1001")))
    d = rs.dashboard("2026-10-13", "2026-10-19")
    a = [p for p in d["products"] if p["key"] == "AAA"][0]
    assert a["profit"] is None and a["needs_cost"], "без себестоимости прибыль не выдумываем"

    rs.set_cost("*", None, 0.1, user="admin")
    rs.set_cost("AAA", 4000, None, user="admin")
    a = [p for p in rs.dashboard("2026-10-13", "2026-10-19")["products"] if p["key"] == "AAA"][0]
    # 30000 − 10% комиссии − 3 шт × 4000 − доставка 1500 − реклама 5000 − бонусы 900
    assert a["profit"] == 30000 - 3000 - 12000 - 1500 - 5000 - 900, a
    assert a["commission_source"] == "default" and a["margin"] == a["profit"] / 30000

    rs.set_cost("AAA", 4000, 0.2, user="admin")
    a = [p for p in rs.dashboard("2026-10-13", "2026-10-19")["products"] if p["key"] == "AAA"][0]
    assert a["commission"] == 6000 and a["commission_source"] == "product"
    print("✓ прибыль = выручка − комиссия − себестоимость − доставка − реклама − бонусы")


def test_product_without_sales_loses_only_marketing():
    rs, _ = _store()
    _load(rs, "Архив.xlsx", _base_orders())
    _load(rs, P, ads_csv(("K2", "Мясорубка", 10, 1, 300, 0, 0, "")))
    p = [p for p in rs.dashboard("2026-10-13", "2026-10-19")["products"]
         if p["key"] == "kaspi:K2"][0]
    assert p["profit"] == -300, "продаж нет — себестоимость не нужна, убыток = реклама"
    print("✓ товар без продаж: убыток ровно на рекламу, себестоимость не требуется")


def test_same_file_twice_is_not_counted_twice():
    rs, _ = _store()
    data = ads_csv(("K2", "Мясорубка", 10, 1, 300, 0, 0, ""))
    assert not _load(rs, P, data)["duplicate"]
    assert _load(rs, P.replace(".csv", " (3).csv"), data)["duplicate"]
    assert rs.dashboard("2026-10-13", "2026-10-19")["totals"]["ad_cost"] == 300
    print("✓ повтор того же файла не задваивает расход")


def test_overlapping_periods_warn_and_contained_reports_count():
    rs, _ = _store()
    _load(rs, "2026-10-01 - 2026-10-19 Отчёт.csv", ads_csv(("K2", "М", 10, 1, 300, 0, 0, "")))
    _load(rs, P, ads_csv(("K2", "М", 10, 1, 100, 0, 0, "")))
    d = rs.dashboard("2026-10-01", "2026-10-19")
    assert d["totals"]["ad_cost"] == 400, "отчёт за 13–19 лежит внутри периода 1–19"
    assert any("дважды" in w for w in d["warnings"]), d["warnings"]
    week = rs.dashboard("2026-10-13", "2026-10-19")
    assert week["totals"]["ad_cost"] == 100, "отчёт за 1–19 в неделю 13–19 не влезает"
    print("✓ вложенные отчёты считаются, пересечение — предупреждение")


def test_main_period_wins_over_lone_report_with_same_end():
    rs, _ = _store()
    _load(rs, "2026-10-01 - 2026-10-19 a.csv", ads_csv(("K1", "М", 10, 1, 1, 0, 0, "")))
    _load(rs, "2026-10-01 - 2026-10-19 b.csv", ads_csv(("K2", "М", 10, 1, 2, 0, 0, "")))
    _load(rs, P, ads_csv(("K3", "М", 10, 1, 3, 0, 0, "")))
    assert rs.periods()[0] == {"start": "2026-10-01", "end": "2026-10-19", "files": 2}
    print("✓ по умолчанию — период, под который выгружено больше файлов")


def test_previous_period_hides_spend_when_not_uploaded():
    rs, _ = _store()
    _load(rs, "Архив.xlsx", _base_orders())
    _load(rs, P, ads_csv(("K2", "М", 10, 1, 300, 0, 0, "")))
    prev = rs.dashboard("2026-10-13", "2026-10-19")["prev"]
    assert (prev["start"], prev["end"]) == ("2026-10-06", "2026-10-12")
    assert prev["totals"]["revenue"] == 9000
    assert prev["totals"]["ad_cost"] is None and prev["totals"]["profit"] is None
    print("✓ прошлый период без отчётов рекламы: расход не «ноль», а неизвестен")


def test_new_archive_updates_status_and_delete_upload():
    rs, _ = _store()
    _load(rs, "Архив.xlsx", _base_orders())
    second = orders_xlsx(("1002", "AAA", "Бритва AY-610", 2, 20000, "RETURNED", D(15), 0))
    up = _load(rs, "Архив (1).xlsx", second)
    a = [p for p in rs.dashboard("2026-10-13", "2026-10-19")["products"] if p["key"] == "AAA"][0]
    assert a["revenue"] == 10000 and a["returned"] == 1, a
    assert rs.delete_upload(up["upload_id"])
    a = [p for p in rs.dashboard("2026-10-13", "2026-10-19")["products"] if p["key"] == "AAA"][0]
    assert a["revenue"] == 10000, "заказ последней загрузки уходит вместе с ней"
    assert not rs.delete_upload(9999)
    print("✓ свежий архив обновляет статусы, загрузку можно удалить")


def test_campaign_bonus_gap_warns():
    rs, _ = _store()
    _load(rs, P.replace("товарам", "кампаниям"), camp_csv(("Бритвы", 500), ("Аэрогриль", 700)))
    _load(rs, P, bonus_csv(("K610", "x", 1, 10000, 500, "")))
    d = rs.dashboard("2026-10-13", "2026-10-19")
    assert len(d["campaigns"]) == 2
    assert any("1\u00a0200" in w and "500" in w for w in d["warnings"]), d["warnings"]
    print("✓ бонусы кампаний без отчёта по товарам — предупреждение")


# ---------------------------------------------------------------- API

def _client():
    from core.rules import RulesConfig
    from core.settings_io import save_settings, SETTINGS_FIELDS
    from webui.app import create_app
    from webui.auth import hash_password
    d = tempfile.mkdtemp()
    rules = os.path.join(d, "rules.yaml")
    save_settings(rules, {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS})
    db = os.path.join(d, "a.db")
    empty_env = os.path.join(d, "empty.env")
    open(empty_env, "w").close()
    os.environ.update(UI_USERNAME="admin", UI_PASSWORD_HASH=hash_password("secret"),
                      UI_SECRET_KEY="test-secret-key", RULES_CONFIG=rules, DB_PATH=db,
                      ENV_FILE=empty_env)
    c = TestClient(create_app(), base_url="https://testserver")
    return c, db


def _upload(c, name, data):
    return c.post("/api/reports/upload", params={"filename": name}, content=data,
                  headers={"Content-Type": "application/octet-stream"})


def test_api_requires_login():
    c, _ = _client()
    assert c.get("/api/reports/dashboard").status_code == 401
    assert _upload(c, P, b"x").status_code == 401
    assert c.put("/api/reports/costs/AAA", json={"unit_cost": 1}).status_code == 401
    print("✓ api отчётов: без входа 401")


def test_api_upload_dashboard_and_costs():
    c, _ = _client()
    assert c.post("/api/login", json={"username": "admin", "password": "secret"}).status_code == 200
    assert c.get("/api/reports/dashboard").json()["empty"] is True

    r = _upload(c, "Архив.xlsx", _base_orders())
    assert r.status_code == 200 and r.json()["kind"] == "orders", r.text
    r = _upload(c, P, ads_csv(("K610", "x", 1000, 50, 5000, 30000, 2, "1001, 1002")))
    assert r.status_code == 200 and r.json()["rows"] == 1, r.text
    assert _upload(c, P, ads_csv(("K610", "x", 1000, 50, 5000, 30000, 2, "1001, 1002"))
                   ).json()["duplicate"] is True

    bad = _upload(c, "report.csv", ads_csv(("K610", "x", 1, 1, 1, 0, 0, "")))
    assert bad.status_code == 400 and "период" in bad.json()["errors"][0], bad.text

    d = c.get("/api/reports/dashboard").json()
    assert (d["start"], d["end"]) == ("2026-10-13", "2026-10-19"), "по умолчанию — свежий отчёт"
    assert d["periods"][0]["files"] == 1 and len(d["uploads"]) == 2

    assert c.put("/api/reports/costs/AAA", json={"unit_cost": -5}).status_code == 400
    assert c.put("/api/reports/costs/AAA", json={"commission_pct": 12}).status_code == 400
    assert c.put("/api/reports/costs/*", json={"unit_cost": 5}).status_code == 400
    assert c.put("/api/reports/costs/*", json={"commission_pct": 0.1}).status_code == 200
    assert c.put("/api/reports/costs/AAA", json={"unit_cost": 4000}).status_code == 200
    a = [p for p in c.get("/api/reports/dashboard?start=2026-10-13&end=2026-10-19")
         .json()["products"] if p["key"] == "AAA"][0]
    assert a["profit"] == 30000 - 3000 - 12000 - 1500 - 5000, a

    assert c.get("/api/reports/dashboard?start=2026-10-19&end=2026-10-01").status_code == 400
    assert c.get("/api/reports/dashboard?start=bad&end=2026-10-01").status_code == 400

    uid = d["uploads"][0]["id"]
    assert c.delete(f"/api/reports/uploads/{uid}").status_code == 200
    assert c.delete(f"/api/reports/uploads/{uid}").status_code == 404
    print("✓ api отчётов: загрузка, сводка, себестоимость, удаление")


def test_reports_tables_do_not_touch_bidder_store():
    """Store биддера и ReportsStore живут в одном файле и не мешают друг другу."""
    from core.store import Store
    d = tempfile.mkdtemp()
    path = os.path.join(d, "a.db")
    Store(path).close()
    rs = ReportsStore.open(path)
    _load(rs, P, ads_csv(("K2", "М", 10, 1, 300, 0, 0, "")))
    rs.close()
    Store(path).close()
    tables = {r[0] for r in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"products_snapshot", "report_uploads", "shop_orders"} <= tables, tables
    print("✓ таблицы отчётов уживаются с базой биддера")


if __name__ == "__main__":
    test_period_from_filename()
    test_detects_kind_by_header_not_filename()
    test_csv_without_period_in_name_is_rejected()
    test_unknown_files_are_rejected()
    test_dashboard_links_kaspi_code_to_article_via_order_numbers()
    test_unlinked_product_falls_back_to_name_then_stays_separate()
    test_profit_needs_cost_and_commission()
    test_product_without_sales_loses_only_marketing()
    test_same_file_twice_is_not_counted_twice()
    test_overlapping_periods_warn_and_contained_reports_count()
    test_main_period_wins_over_lone_report_with_same_end()
    test_previous_period_hides_spend_when_not_uploaded()
    test_new_archive_updates_status_and_delete_upload()
    test_campaign_bonus_gap_warns()
    test_api_requires_login()
    test_api_upload_dashboard_and_costs()
    test_reports_tables_do_not_touch_bidder_store()
    print("-" * 60)
    print("✓ Все проверки отчётов прошли")
