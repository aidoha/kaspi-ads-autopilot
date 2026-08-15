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


if __name__ == "__main__":
    test_validate_catches_bad_values()
    test_save_load_roundtrip_and_loadable_by_worker()
    test_save_rejects_invalid()
    print("-" * 60)
    print("✓ Все проверки settings_io прошли")
