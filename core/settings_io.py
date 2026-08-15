"""settings_io.py — чтение/валидация/атомарная запись настроек биддера (rules.yaml).
UI пишет через эти функции; воркер читает тот же файл через load_rules_config."""
from __future__ import annotations

import os

from core.rules import RulesConfig, load_rules_config

# Порядок = порядок полей в форме UI.
SETTINGS_FIELDS = [
    "target_tacos_low", "target_tacos_high",
    "daily_sku_cost_limit", "sku_budget_fraction",
    "min_clicks_for_no_cart_cut", "cpc_spike_pct",
    "max_bid_step", "max_changes_per_day",
    "bid_ceiling", "min_bid", "min_score_for_raise",
    "dry_run", "campaign_ids",
]


def load_settings(path: str) -> dict:
    """Текущие значения (дефолты RulesConfig, перекрытые файлом)."""
    cfg = load_rules_config(path) if os.path.exists(path) else RulesConfig()
    return {f: getattr(cfg, f) for f in SETTINGS_FIELDS}


def validate_settings(data: dict) -> list[str]:
    """Список ошибок (пусто = валидно)."""
    errs: list[str] = []
    def num(name):
        try:
            return float(data.get(name))
        except (TypeError, ValueError):
            errs.append(f"{name}: не число")
            return None
    min_bid = num("min_bid"); ceil = num("bid_ceiling"); step = num("max_bid_step")
    low = num("target_tacos_low"); high = num("target_tacos_high")
    frac = num("sku_budget_fraction"); cap = num("daily_sku_cost_limit")
    changes = num("max_changes_per_day"); spike = num("cpc_spike_pct")
    clicks = num("min_clicks_for_no_cart_cut"); score = num("min_score_for_raise")
    if min_bid is not None and min_bid < 1:
        errs.append("min_bid: минимум 1")
    if ceil is not None and min_bid is not None and ceil < min_bid:
        errs.append("bid_ceiling: должен быть ≥ min_bid")
    if step is not None and step < 1:
        errs.append("max_bid_step: минимум 1")
    if low is not None and high is not None and not (0 < low < high):
        errs.append("target_tacos: должно быть 0 < low < high")
    if frac is not None and not (0 < frac <= 1):
        errs.append("sku_budget_fraction: должно быть в (0, 1]")
    if cap is not None and cap < 0:
        errs.append("daily_sku_cost_limit: не отрицательный")
    if changes is not None and changes < 0:
        errs.append("max_changes_per_day: не отрицательный")
    if spike is not None and spike < 0:
        errs.append("cpc_spike_pct: не отрицательный")
    if clicks is not None and clicks < 0:
        errs.append("min_clicks_for_no_cart_cut: не отрицательный")
    if score is not None and score < 0:
        errs.append("min_score_for_raise: не отрицательный")
    cids = data.get("campaign_ids")
    if cids not in (None, "") and not isinstance(cids, list):
        errs.append("campaign_ids: список строк или пусто")
    return errs


def save_settings(path: str, data: dict) -> None:
    """Валидирует и атомарно пишет rules.yaml. ValueError, если невалидно."""
    import yaml  # локальный импорт (как в load_rules_config)
    errs = validate_settings(data)
    if errs:
        raise ValueError("; ".join(errs))
    # нормализация типов
    out = {
        "target_tacos_low": float(data["target_tacos_low"]),
        "target_tacos_high": float(data["target_tacos_high"]),
        "daily_sku_cost_limit": float(data["daily_sku_cost_limit"]),
        "sku_budget_fraction": float(data["sku_budget_fraction"]),
        "min_clicks_for_no_cart_cut": int(float(data["min_clicks_for_no_cart_cut"])),
        "cpc_spike_pct": float(data["cpc_spike_pct"]),
        "max_bid_step": float(data["max_bid_step"]),
        "max_changes_per_day": int(float(data["max_changes_per_day"])),
        "bid_ceiling": float(data["bid_ceiling"]),
        "min_bid": float(data["min_bid"]),
        "min_score_for_raise": float(data["min_score_for_raise"]),
        "dry_run": bool(data["dry_run"]),
        "campaign_ids": list(data["campaign_ids"]) if data.get("campaign_ids") else None,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
