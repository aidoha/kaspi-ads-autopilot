"""test_config_resolver.py — сборка эффективного конфига (наследование)."""
from core.rules import RulesConfig
from core.config_resolver import resolve_config, OVERRIDABLE_FIELDS


def test_no_overrides_equals_global():
    g = RulesConfig()
    assert resolve_config(g, {}, {}) == g
    print("✓ resolver: без overrides = глобал")


def test_campaign_overrides_global():
    g = RulesConfig(bid_ceiling=50, min_bid=1)
    r = resolve_config(g, {"bid_ceiling": "80"}, {})
    assert r.bid_ceiling == 80.0 and r.min_bid == 1
    print("✓ resolver: override кампании перекрывает глобал")


def test_sku_overrides_campaign_and_global():
    g = RulesConfig(bid_ceiling=50)
    r = resolve_config(g, {"bid_ceiling": "80"}, {"bid_ceiling": "120"})
    assert r.bid_ceiling == 120.0  # SKU важнее кампании
    print("✓ resolver: override SKU перекрывает кампанию и глобал")


def test_int_fields_coerced():
    g = RulesConfig()
    r = resolve_config(g, {"max_changes_per_day": "7"},
                       {"min_clicks_for_no_cart_cut": "55"})
    assert r.max_changes_per_day == 7 and isinstance(r.max_changes_per_day, int)
    assert r.min_clicks_for_no_cart_cut == 55 and isinstance(r.min_clicks_for_no_cart_cut, int)
    print("✓ resolver: int-поля приводятся к int")


def test_global_only_fields_ignored():
    g = RulesConfig(dry_run=True, campaign_ids=["X"])
    # даже если кто-то подсунул эти поля в overrides — глобал не меняется
    r = resolve_config(g, {"dry_run": "false", "campaign_ids": "Y"}, {})
    assert r.dry_run is True and r.campaign_ids == ["X"]
    print("✓ resolver: dry_run/campaign_ids не переопределяются")


def test_overridable_fields_list():
    assert "dry_run" not in OVERRIDABLE_FIELDS
    assert "campaign_ids" not in OVERRIDABLE_FIELDS
    assert "bid_ceiling" in OVERRIDABLE_FIELDS and len(OVERRIDABLE_FIELDS) == 11
    print("✓ resolver: список переопределяемых полей корректен")


if __name__ == "__main__":
    test_no_overrides_equals_global()
    test_campaign_overrides_global()
    test_sku_overrides_campaign_and_global()
    test_int_fields_coerced()
    test_global_only_fields_ignored()
    test_overridable_fields_list()
    print("-" * 60)
    print("✓ Все проверки config_resolver прошли")
