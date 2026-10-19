"""reports.py — загрузка выгрузок Kaspi и сводка «деньги по товару».

Файл шлётся сырым телом (application/octet-stream), имя — в query: период
CSV-отчётов Kaspi пишет только в имя файла, а multipart ради одного файла
не нужен.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from core.report_files import KIND_LABELS, ReportError, parse_report
from core.reports import DEFAULT_SCOPE, ReportsStore
from webui.api.deps import ApiContext, read_json, require_user

# Архив заказов за год — около мегабайта. 25 МБ — с большим запасом.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _bad(errors: list[str], status: int = 400):
    raise HTTPException(status_code=status, detail={"errors": errors})


def _iso(s: str | None, what: str) -> str | None:
    if s is None or s == "":
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        _bad([f"{what}: нужна дата ГГГГ-ММ-ДД, получено {s!r}"])


def _opt_number(body: dict, field: str, lo: float, hi: float, label: str):
    raw = body.get(field)
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        _bad([f"{label}: нужно число"])
    try:
        v = float(raw)
    except (TypeError, ValueError):
        _bad([f"{label}: нужно число, получено {raw!r}"])
    if not math.isfinite(v) or not (lo <= v <= hi):
        _bad([f"{label}: допустимо от {lo:g} до {hi:g}"])
    return v


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api/reports")

    @contextmanager
    def reports():
        rs = ReportsStore.open(ctx.db_path)
        try:
            yield rs
        finally:
            rs.close()

    def _uploads(rs: ReportsStore) -> list[dict]:
        return [{**u, "kind_label": KIND_LABELS.get(u["kind"], u["kind"])}
                for u in rs.list_uploads()]

    @router.post("/upload")
    async def upload(request: Request, filename: str = "",
                     user: str = Depends(require_user)):
        data = await request.body()
        if len(data) > MAX_UPLOAD_BYTES:
            _bad(["Файл больше 25 МБ — это точно выгрузка Kaspi?"], 413)
        try:
            parsed = parse_report(filename, data)
        except ReportError as e:
            _bad([f"{filename or 'Файл'}: {e}"])
        with reports() as rs:
            res = rs.save_report(parsed, filename, data, user=user)
        return {
            "kind": parsed.kind,
            "kind_label": KIND_LABELS[parsed.kind],
            "period_start": parsed.period_start,
            "period_end": parsed.period_end,
            "rows": len(parsed.rows),
            **res,
        }

    @router.get("/uploads")
    def uploads(user: str = Depends(require_user)):
        with reports() as rs:
            return {"uploads": _uploads(rs)}

    @router.delete("/uploads/{upload_id}")
    def delete_upload(upload_id: int, user: str = Depends(require_user)):
        with reports() as rs:
            if not rs.delete_upload(upload_id):
                _bad(["Такой загрузки нет"], 404)
        return {"ok": True}

    @router.get("/dashboard")
    def dashboard(start: Optional[str] = None, end: Optional[str] = None,
                  user: str = Depends(require_user)):
        start, end = _iso(start, "start"), _iso(end, "end")
        with reports() as rs:
            periods = rs.periods()
            if not (start and end):
                if periods:
                    start, end = periods[0]["start"], periods[0]["end"]
                else:
                    _, o_max = rs.orders_range()
                    if o_max is None:
                        return {"empty": True, "periods": [], "uploads": _uploads(rs)}
                    end = o_max
                    start = (date.fromisoformat(o_max) - timedelta(days=6)).isoformat()
            if start > end:
                _bad(["Начало периода позже конца"])
            out = rs.dashboard(start, end)
            out["periods"] = periods
            out["uploads"] = _uploads(rs)
            out["empty"] = False
        return out

    @router.put("/costs/{merchant_sku}")
    async def put_cost(merchant_sku: str, request: Request,
                       user: str = Depends(require_user)):
        """Себестоимость штуки (₸) и комиссия Kaspi (доля 0..1) по артикулу.
        Артикул «*» — комиссия по умолчанию для всех товаров без своей.
        null в поле — стереть."""
        body = await read_json(request)
        unit_cost = _opt_number(body, "unit_cost", 0, 100_000_000, "Себестоимость")
        commission = _opt_number(body, "commission_pct", 0, 0.9, "Комиссия")
        if merchant_sku == DEFAULT_SCOPE and unit_cost is not None:
            _bad(["Себестоимость по умолчанию не задаётся — только по товару"])
        with reports() as rs:
            rs.set_cost(merchant_sku, unit_cost, commission, user=user)
        return {"ok": True}

    return router
