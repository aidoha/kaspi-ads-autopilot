"""config_resolver.py — эффективный RulesConfig для товара: глобал → кампания → SKU.

Чистая функция без ввода-вывода: overrides приходят готовыми словарями
{field: raw_value} (их читает стор). dry_run/campaign_ids не переопределяются —
это глобальные рубильники, не тюнинг.
"""
from __future__ import annotations

import dataclasses

from core.rules import RulesConfig

# 14 переопределяемых числовых порогов (без dry_run/campaign_ids).
OVERRIDABLE_FIELDS = [
    "target_tacos_low", "target_tacos_high",
    "daily_sku_cost_limit", "sku_budget_fraction",
    "min_clicks_for_no_cart_cut", "cpc_spike_pct",
    "max_bid_step", "max_changes_per_day",
    "bid_ceiling", "min_bid", "min_score_for_raise",
    "bid_step_pct", "cpc_headroom", "pace_tolerance",
]
_INT_FIELDS = {"min_clicks_for_no_cart_cut", "max_changes_per_day"}


def _coerce(field: str, raw):
    """Приводит строковое значение к нужному типу (int или float)."""
    return int(float(raw)) if field in _INT_FIELDS else float(raw)


def resolve_config(global_cfg: RulesConfig,
                   campaign_overrides: dict,
                   sku_overrides: dict) -> RulesConfig:
    """Копия global, поверх которой лежат отличия кампании, затем SKU."""
    values = dataclasses.asdict(global_cfg)
    for ov in (campaign_overrides or {}, sku_overrides or {}):
        for field, raw in ov.items():
            if field in OVERRIDABLE_FIELDS:
                values[field] = _coerce(field, raw)
    return RulesConfig(**values)
