"""test_settings_io.py — валидация и запись настроек биддера (rules.yaml)."""
import os, tempfile

from core.rules import RulesConfig, load_rules_config
from core.settings_io import load_settings, validate_settings, save_settings, SETTINGS_FIELDS


def _tmp_yaml():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "rules.yaml")
    # стартовый валидный конфиг из дефолтов
    save_settings(p, {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS})
    return p


def test_validate_catches_bad_values():
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    assert validate_settings(base) == []                       # дефолты валидны
    bad = dict(base, bid_ceiling=0)                            # < min_bid
    assert any("bid_ceiling" in e for e in validate_settings(bad))
    bad = dict(base, sku_budget_fraction=1.5)                  # > 1
    assert any("sku_budget_fraction" in e for e in validate_settings(bad))
    bad = dict(base, target_tacos_low=0.2, target_tacos_high=0.1)  # low >= high
    assert any("tacos" in e.lower() for e in validate_settings(bad))
    bad = dict(base, max_bid_step=0)                           # < 1
    assert any("max_bid_step" in e for e in validate_settings(bad))
    print("✓ settings_io: validate ловит плохие значения")


def test_save_load_roundtrip_and_loadable_by_worker():
    p = _tmp_yaml()
    data = load_settings(p)
    data["min_bid"] = 3
    data["bid_ceiling"] = 40
    data["dry_run"] = False
    data["campaign_ids"] = ["2899523", "3032419"]
    save_settings(p, data)
    # читается обратно
    got = load_settings(p)
    assert got["min_bid"] == 3 and got["bid_ceiling"] == 40 and got["dry_run"] is False
    assert got["campaign_ids"] == ["2899523", "3032419"]
    # и воркерский загрузчик его понимает
    cfg = load_rules_config(p)
    assert cfg.min_bid == 3 and cfg.bid_ceiling == 40 and cfg.dry_run is False
    assert cfg.campaign_ids == ["2899523", "3032419"]
    print("✓ settings_io: save/load round-trip + load_rules_config совместим")


def test_save_rejects_invalid():
    p = _tmp_yaml()
    try:
        save_settings(p, dict(load_settings(p), bid_ceiling=0))
        assert False, "ожидали ValueError на невалидном конфиге"
    except ValueError:
        pass
    # файл не испорчен — по-прежнему загружается
    assert load_rules_config(p).bid_ceiling >= 1
    print("✓ settings_io: save отвергает невалидное, файл цел")


def test_dry_run_string_coercion():
    """dry_run как строка должна правильно парсится."""
    p = _tmp_yaml()

    # dry_run="False" (строка) → False
    data = load_settings(p)
    data["dry_run"] = "False"
    save_settings(p, data)
    cfg = load_rules_config(p)
    assert cfg.dry_run is False, f"ожидали dry_run=False, получили {cfg.dry_run}"

    # dry_run="0" (строка) → False
    data = load_settings(p)
    data["dry_run"] = "0"
    save_settings(p, data)
    cfg = load_rules_config(p)
    assert cfg.dry_run is False, f"ожидали dry_run=False, получили {cfg.dry_run}"

    # dry_run="on" (строка) → True
    data = load_settings(p)
    data["dry_run"] = "on"
    save_settings(p, data)
    cfg = load_rules_config(p)
    assert cfg.dry_run is True, f"ожидали dry_run=True, получили {cfg.dry_run}"

    # dry_run="true" (строка) → True
    data = load_settings(p)
    data["dry_run"] = "true"
    save_settings(p, data)
    cfg = load_rules_config(p)
    assert cfg.dry_run is True, f"ожидали dry_run=True, получили {cfg.dry_run}"

    print("✓ settings_io: dry_run как строка парсится правильно (безопасность)")


def test_dry_run_missing_defaults_to_true():
    """Отсутствующий dry_run должен дефолтиться в True (безопасно)."""
    p = _tmp_yaml()
    data = load_settings(p)
    del data["dry_run"]
    save_settings(p, data)
    cfg = load_rules_config(p)
    assert cfg.dry_run is True, f"ожидали dry_run=True (дефолт), получили {cfg.dry_run}"
    print("✓ settings_io: отсутствующий dry_run дефолтится в True")


def test_validate_rejects_nan_inf():
    """NaN и inf должны отвергаться валидатором."""
    import math
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}

    # NaN в bid_ceiling
    bad = dict(base, bid_ceiling=float('nan'))
    errs = validate_settings(bad)
    assert any("bid_ceiling" in e and "конечное" in e for e in errs), f"ожидали ошибку на NaN, получили {errs}"

    # inf в min_bid
    bad = dict(base, min_bid=float('inf'))
    errs = validate_settings(bad)
    assert any("min_bid" in e and "конечное" in e for e in errs), f"ожидали ошибку на inf, получили {errs}"

    # -inf в target_tacos_low
    bad = dict(base, target_tacos_low=float('-inf'))
    errs = validate_settings(bad)
    assert any("target_tacos_low" in e and "конечное" in e for e in errs), f"ожидали ошибку на -inf, получили {errs}"

    print("✓ settings_io: NaN/inf отвергаются валидатором")


def test_settings_accepts_new_field_defaults():
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    assert validate_settings(base) == []   # дефолты с новыми полями валидны
    print("✓ settings: дефолты с новыми полями валидны")


def test_settings_rejects_bad_new_fields():
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    assert any("bid_step_pct" in e for e in validate_settings(dict(base, bid_step_pct=1.5)))
    assert any("cpc_headroom" in e for e in validate_settings(dict(base, cpc_headroom=-1)))
    assert any("pace_tolerance" in e for e in validate_settings(dict(base, pace_tolerance=-0.5)))
    print("✓ settings: невалидные новые поля отклонены")


def test_settings_roundtrip_new_fields():
    import tempfile, os
    base = {f: getattr(RulesConfig(), f) for f in SETTINGS_FIELDS}
    data = dict(base, bid_step_pct=0.25, cpc_headroom=1.8, pace_tolerance=1.1)
    path = os.path.join(tempfile.mkdtemp(), "rules.yaml")
    save_settings(path, data)
    loaded = load_settings(path)
    assert loaded["bid_step_pct"] == 0.25
    assert loaded["cpc_headroom"] == 1.8
    assert loaded["pace_tolerance"] == 1.1
    print("✓ settings: новые поля переживают save→load")


if __name__ == "__main__":
    test_validate_catches_bad_values()
    test_save_load_roundtrip_and_loadable_by_worker()
    test_save_rejects_invalid()
    test_dry_run_string_coercion()
    test_dry_run_missing_defaults_to_true()
    test_validate_rejects_nan_inf()
    test_settings_accepts_new_field_defaults()
    test_settings_rejects_bad_new_fields()
    test_settings_roundtrip_new_fields()
    print("-" * 60)
    print("✓ Все проверки settings_io прошли")
