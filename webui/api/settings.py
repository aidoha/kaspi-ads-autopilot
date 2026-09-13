"""settings.py — запись настроек: товар, расписание, глобальные, режимы."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from core.config_resolver import OVERRIDABLE_FIELDS
from core.settings_io import (SETTINGS_FIELDS, load_settings, save_settings,
                              validate_settings)
from webui.api.deps import ApiContext, open_store, read_json, require_user

ALMATY = ZoneInfo("Asia/Almaty")


def _bad(errors: list[str]):
    raise HTTPException(status_code=400, detail={"errors": errors})


def build_router(ctx: ApiContext, refresh_fn) -> APIRouter:
    """refresh_fn инъектируется: живой пулл ходит в кабинет Kaspi, тесты туда
    ходить не должны."""
    router = APIRouter(prefix="/api")

    @router.put("/products/{campaign_id}/{sku}/settings")
    async def put_product_settings(campaign_id: str, sku: str, request: Request,
                                   user: str = Depends(require_user)):
        body = await read_json(request)
        values = body.get("values") or {}
        unknown = sorted(set(values) - set(OVERRIDABLE_FIELDS))
        if unknown:
            _bad([f"Неизвестное поле настроек: {f}" for f in unknown])

        errors: list[str] = []
        parsed: dict[str, float | None] = {}
        for field, raw in values.items():
            if raw is None:
                parsed[field] = None          # None = наследовать
                continue
            try:
                parsed[field] = float(raw)
            except (TypeError, ValueError):
                errors.append(f"«{field}»: нужно число, получено {raw!r}")
        if errors:
            _bad(errors)

        # min_bid обязан быть не выше потолка, иначе решения биддера
        # противоречат сами себе. Проверяем по ЭФФЕКТИВНЫМ значениям после
        # правки, а не только по присланным: поменять могли одно из двух.
        with open_store(ctx) as store:
            current = dict(store.get_overrides("sku", sku))
            for field, val in parsed.items():
                if val is None:
                    current.pop(field, None)
                else:
                    current[field] = val
            from core.config_resolver import resolve_config
            from core.rules import load_rules_config
            cfg = resolve_config(load_rules_config(ctx.rules_path),
                                 store.get_overrides("campaign", campaign_id),
                                 current)
            if cfg.min_bid > cfg.bid_ceiling:
                _bad([f"Минимальная ставка ({cfg.min_bid}) выше потолка "
                      f"({cfg.bid_ceiling})"])

            ts = int(time.time())
            for field, val in parsed.items():
                if val is None:
                    store.delete_override("sku", sku, field)
                else:
                    store.set_override("sku", sku, field, val, user=user, ts=ts)
        return {"ok": True}

    @router.put("/products/{campaign_id}/{sku}/control")
    async def put_control(campaign_id: str, sku: str, request: Request,
                          user: str = Depends(require_user)):
        body = await read_json(request)

        # Отсутствующие поля обязаны СОХРАНЯТЬ текущее значение, а не
        # дефолтиться в «круглосуточно, все дни»: основной сценарий фронта —
        # тумблер enabled в списке, который шлёт только его. Дефолт здесь
        # молча стирал бы кастомное расписание товара при каждом клике по
        # тумблеру.
        with open_store(ctx) as store:
            current = store.get_product_control(campaign_id, sku)
            try:
                start = int(body.get("window_start", current.window_start))
                end = int(body.get("window_end", current.window_end))
                mask = int(body.get("days_mask", current.days_mask))
            except (TypeError, ValueError):
                _bad(["Окно и дни недели должны быть целыми числами"])

            errors = []
            if not (0 <= start <= 23):
                errors.append("Начало окна — от 0 до 23")
            if not (1 <= end <= 24):
                errors.append("Конец окна — от 1 до 24")
            if start >= end:
                errors.append("Начало окна должно быть раньше конца")
            if not (0 <= mask <= 127):
                errors.append("Маска дней недели — от 0 до 127")
            if errors:
                _bad(errors)

            store.set_product_control(
                campaign_id, sku,
                enabled=bool(body.get("enabled", current.enabled)),
                window_start=start, window_end=end,
                days_mask=mask, user=user,
                ts=int(time.time()))
        return {"ok": True}

    @router.get("/settings")
    def get_settings(user: str = Depends(require_user)):
        return {"settings": load_settings(ctx.rules_path),
                "fields": SETTINGS_FIELDS}

    @router.put("/settings")
    async def put_settings(request: Request, user: str = Depends(require_user)):
        body = await read_json(request)
        incoming = body.get("settings") or {}
        # Симметрия с put_product_settings: опечатку в имени поля пользователь
        # должен увидеть как явную ошибку, а не молча потерять — иначе он
        # решит, что значение применилось, а оно просто проигнорировано.
        unknown = sorted(set(incoming) - set(SETTINGS_FIELDS))
        if unknown:
            _bad([f"Неизвестное поле настроек: {f}" for f in unknown])
        cur = load_settings(ctx.rules_path)
        new = dict(cur)
        for f in SETTINGS_FIELDS:
            if f == "dry_run":
                continue      # только через POST /api/dry-run — это рубильник денег
            if f in incoming:
                new[f] = incoming[f]
        errors = validate_settings(new)
        if errors:
            _bad(errors)

        with open_store(ctx) as store:
            ts = int(time.time())
            for f in SETTINGS_FIELDS:
                if f != "dry_run" and str(cur.get(f)) != str(new.get(f)):
                    store.log_settings_change(user, f, cur.get(f), new.get(f), ts)
        save_settings(ctx.rules_path, new)
        return {"ok": True, "settings": load_settings(ctx.rules_path)}

    @router.post("/dry-run")
    async def set_dry_run(request: Request, user: str = Depends(require_user)):
        body = await read_json(request)
        want = bool(body.get("dry_run", True))
        cur = load_settings(ctx.rules_path)
        new = dict(cur)
        new["dry_run"] = want
        with open_store(ctx) as store:
            if cur.get("dry_run") != want:
                store.log_settings_change(user, "dry_run", cur.get("dry_run"),
                                          want, int(time.time()))
        save_settings(ctx.rules_path, new)
        return {"ok": True, "dry_run": want}

    @router.post("/refresh")
    def refresh(user: str = Depends(require_user)):
        return {"ok": bool(refresh_fn(ctx.db_path))}

    @router.get("/audit")
    def audit(user: str = Depends(require_user)):
        with open_store(ctx) as store:
            return {"audit": store.get_settings_audit()}

    return router
