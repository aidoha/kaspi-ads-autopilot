"""
report_files.py — разбор выгрузок кабинета Kaspi, которые владелец загружает
в панель руками (раз в неделю): отчёты рекламы по товарам, отчёты бонусных
акций по товарам и по кампаниям, архив заказов магазина (.xlsx).

Только чистый разбор байтов в строки — ни БД, ни сети. Тип файла
определяется по шапке, а не по имени: «Отчёт по товарам» Kaspi отдаёт в двух
разных форматах (реклама и бонусы) под одним и тем же именем.

Период CSV-отчётов в самих файлах не записан — он есть только в имени
выгрузки («2026-10-13 - 2026-10-19 Отчёт по товарам.csv»). Поэтому имя файла
для CSV обязательно; архив заказов несёт дату каждого заказа в строках.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta

KIND_ADS = "ads"
KIND_BONUS = "bonus"
KIND_CAMPAIGNS = "campaigns"
KIND_ORDERS = "orders"

KIND_LABELS = {
    KIND_ADS: "Реклама по товарам",
    KIND_BONUS: "Бонусы по товарам",
    KIND_CAMPAIGNS: "Бонусные кампании",
    KIND_ORDERS: "Архив заказов",
}

# Распакованный лист архива на 10 тыс. заказов весит ~8.5 МБ. Потолок с
# огромным запасом, но он есть: .xlsx — это zip, и без проверки размера
# распаковки маленький файл-«бомба» положил бы панель по памяти.
MAX_XLSX_SHEET_BYTES = 300 * 1024 * 1024

_PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*[-–—]\s*(\d{4}-\d{2}-\d{2})")


class ReportError(ValueError):
    """Файл не разобрать. Текст — для владельца, показывается в панели как есть."""


@dataclass
class ParsedReport:
    kind: str
    rows: list[dict]
    period_start: str | None = None
    period_end: str | None = None
    warnings: list[str] = field(default_factory=list)


def period_from_filename(filename: str) -> tuple[str, str] | None:
    m = _PERIOD_RE.search(filename or "")
    if not m:
        return None
    start, end = m.group(1), m.group(2)
    try:
        if datetime.fromisoformat(start) > datetime.fromisoformat(end):
            return None
    except ValueError:
        return None
    return start, end


def _num(s) -> float:
    """«39991,95» → 39991.95. Пусто и нечисловые заглушки Kaspi («Нет
    заказов») → 0: в этих колонках пусто всегда означает «ничего не было»."""
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not t:
        return 0.0
    try:
        return float(t)
    except ValueError:
        return 0.0


def _order_numbers(s: str | None) -> list[str]:
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1251")
    # Kaspi кладёт в начало файла ДВА BOM подряд — utf-8-sig снял бы один.
    return text.lstrip("﻿")


def _parse_csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    text = _decode(data)
    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows = [r for r in reader if r and any(c.strip() for c in r)]
    if not rows:
        raise ReportError("Файл пустой")
    header = [h.strip().lstrip("﻿") for h in rows[0]]
    return header, rows[1:]


def _by_header(header: list[str], row: list[str]) -> dict:
    return {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(header)}


def _parse_ads(header, body) -> list[dict]:
    out = []
    for raw in body:
        r = _by_header(header, raw)
        sku = r.get("Рекламируемый товар", "")
        if not sku:
            continue
        out.append({
            "sku": sku,
            "name": r.get("Товар", ""),
            "views": int(_num(r.get("Просмотры"))),
            "clicks": int(_num(r.get("Клики"))),
            "cost": _num(r.get("Расходы на рекламу")),
            "ad_revenue": _num(r.get("Сумма заказов")),
            "ad_orders": int(_num(r.get("Все заказы"))),
            "carts": int(_num(r.get("В корзину"))),
            "order_numbers": _order_numbers(r.get("Номера заказов")),
        })
    return out


def _parse_bonus(header, body) -> list[dict]:
    out = []
    for raw in body:
        r = _by_header(header, raw)
        sku = r.get("SKU", "")
        if not sku:
            continue
        out.append({
            "sku": sku,
            "name": r.get("Наименование", ""),
            "views": int(_num(r.get("Просмотры"))),
            "clicks": int(_num(r.get("Клики"))),
            "orders": int(_num(r.get("Заказы"))),
            "revenue": _num(r.get("Сумма заказов")),
            "bonus": _num(r.get("Выплачено бонусов клиентам")),
            "order_numbers": _order_numbers(r.get("Номера заказов")),
        })
    return out


def _parse_campaigns(header, body) -> list[dict]:
    out = []
    for raw in body:
        r = _by_header(header, raw)
        name = r.get("Наименование", "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "start_date": r.get("Дата начала", ""),
            "status": r.get("Статус", ""),
            "views": int(_num(r.get("Просмотры"))),
            "clicks": int(_num(r.get("Клики"))),
            "orders": int(_num(r.get("Заказы"))),
            "revenue": _num(r.get("Сумма заказов")),
            "bonus": _num(r.get("Выплачено бонусов клиентам")),
        })
    return out


# ---- xlsx без openpyxl: архив заказов — один лист с простыми ячейками ----

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _first_sheet_path(z: zipfile.ZipFile) -> str:
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        sheet = wb.find(f"{_NS}sheets/{_NS}sheet")
        rid = sheet.get(f"{_REL_NS}id")
        for rel in rels:
            if rel.get("Id") == rid:
                target = rel.get("Target").lstrip("/")
                return target if target.startswith("xl/") else "xl/" + target
    except (KeyError, AttributeError, ET.ParseError):
        pass
    return "xl/worksheets/sheet1.xml"


def read_xlsx_rows(data: bytes) -> list[list]:
    """Первый лист .xlsx как список строк (значения — str или float)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ReportError("Файл .xlsx повреждён или это не Excel")
    with z:
        path = _first_sheet_path(z)
        try:
            info = z.getinfo(path)
        except KeyError:
            raise ReportError("В .xlsx нет листа с данными")
        if info.file_size > MAX_XLSX_SHEET_BYTES:
            raise ReportError("Лист .xlsx слишком большой")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
        rows: list[list] = []
        with z.open(path) as f:
            for _, el in ET.iterparse(f, events=("end",)):
                if el.tag != f"{_NS}row":
                    continue
                row: list = []
                for c in el.findall(f"{_NS}c"):
                    idx = _col_index(c.get("r", "")) if c.get("r") else len(row)
                    t = c.get("t")
                    if t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{_NS}t"))
                    else:
                        v = c.find(f"{_NS}v")
                        raw = v.text if v is not None else None
                        if raw is None:
                            val = ""
                        elif t == "s":
                            val = shared[int(raw)] if int(raw) < len(shared) else ""
                        elif t in ("str", "b", "e"):
                            val = raw
                        else:
                            try:
                                val = float(raw)
                            except ValueError:
                                val = raw
                    while len(row) < idx:
                        row.append("")
                    row.append(val)
                rows.append(row)
                el.clear()
    return rows


def _to_datetime(v) -> datetime | None:
    """Дата заказа: в выгрузке Kaspi — серийный номер Excel (время Алматы).
    Строку на случай другой выгрузки тоже понимаем."""
    if v in (None, ""):
        return None
    if isinstance(v, float) or (isinstance(v, str) and re.fullmatch(r"\d+(\.\d+)?", v)):
        serial = float(v)
        if serial <= 0:
            return None
        return datetime(1899, 12, 30) + timedelta(days=serial)
    s = str(v).strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


_ORDER_COLS = {
    "order_no": "Номер заказа",
    "merchant_sku": "Артикул",
    "name": "Название товара",
    "category": "Категория",
    "qty": "Количество",
    "amount": "Сумма",
    "delivery_type": "Вид доставки",
    "status": "Статус",
    "ordered_at": "Дата поступления заказа",
    "delivery_cost": "Стоимость доставки для продавца",
}


def _cell_str(v) -> str:
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return str(v or "").strip()


def _parse_orders(rows: list[list]) -> list[dict]:
    header = [_cell_str(h) for h in rows[0]]
    idx = {}
    for key, title in _ORDER_COLS.items():
        if title in header:
            idx[key] = header.index(title)
    missing = [_ORDER_COLS[k] for k in ("order_no", "merchant_sku", "amount", "status", "ordered_at")
               if k not in idx]
    if missing:
        raise ReportError("В архиве заказов нет колонок: " + ", ".join(missing))

    def get(row, key):
        i = idx.get(key)
        return row[i] if i is not None and i < len(row) else ""

    # Одна позиция заказа может прийти несколькими строками — складываем,
    # ключ в БД всё равно (номер заказа, артикул).
    merged: dict[tuple[str, str], dict] = {}
    for row in rows[1:]:
        order_no = _cell_str(get(row, "order_no"))
        msku = _cell_str(get(row, "merchant_sku"))
        if not order_no or not msku:
            continue
        dt = _to_datetime(get(row, "ordered_at"))
        if dt is None:
            continue
        key = (order_no, msku)
        if key in merged:
            m = merged[key]
            m["qty"] += _num(get(row, "qty")) or 1
            m["amount"] += _num(get(row, "amount"))
            m["delivery_cost"] += _num(get(row, "delivery_cost"))
            continue
        merged[key] = {
            "order_no": order_no,
            "merchant_sku": msku,
            "name": _cell_str(get(row, "name")),
            "category": _cell_str(get(row, "category")),
            "qty": _num(get(row, "qty")) or 1,
            "amount": _num(get(row, "amount")),
            "delivery_type": _cell_str(get(row, "delivery_type")),
            "status": _cell_str(get(row, "status")).upper(),
            "ordered_at": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "day": dt.date().isoformat(),
            "delivery_cost": _num(get(row, "delivery_cost")),
        }
    return list(merged.values())


def parse_report(filename: str, data: bytes) -> ParsedReport:
    """Байты выгрузки → ParsedReport. ReportError с понятным текстом, если
    файл не наш или в нём нет нужного."""
    if not data:
        raise ReportError("Файл пустой")

    if data[:2] == b"PK":
        rows = read_xlsx_rows(data)
        if not rows:
            raise ReportError("В .xlsx нет строк")
        header = [_cell_str(h) for h in rows[0]]
        if "Номер заказа" in header and "Артикул" in header:
            orders = _parse_orders(rows)
            if not orders:
                raise ReportError("В архиве заказов не нашлось ни одного заказа")
            days = sorted(o["day"] for o in orders)
            return ParsedReport(KIND_ORDERS, orders, days[0], days[-1])
        raise ReportError("Этот .xlsx не похож на архив заказов Kaspi")

    header, body = _parse_csv(data)
    if header and header[0] == "Рекламируемый товар":
        kind, rows_out = KIND_ADS, _parse_ads(header, body)
    elif "SKU" in header and "Выплачено бонусов клиентам" in header:
        kind, rows_out = KIND_BONUS, _parse_bonus(header, body)
    elif "Дата начала" in header and "Выплачено бонусов клиентам" in header:
        kind, rows_out = KIND_CAMPAIGNS, _parse_campaigns(header, body)
    else:
        raise ReportError("Не узнаю формат файла — нужен отчёт из кабинета Kaspi "
                          "(реклама или бонусы) или архив заказов .xlsx")

    period = period_from_filename(filename)
    if period is None:
        raise ReportError("В имени файла нет периода вида «2026-10-13 - 2026-10-19» — "
                          "загрузите выгрузку под исходным именем")
    return ParsedReport(kind, rows_out, period[0], period[1])
