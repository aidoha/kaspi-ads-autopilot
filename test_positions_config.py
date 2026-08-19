"""
test_positions_config.py — загрузка конфига позиций и разбор ссылки на карточку.

Запуск: .venv/bin/python test_positions_config.py
"""
import os
import tempfile
from core.positions_config import load_positions_config, resolve_product_id_from_url


YAML = """
max_depth: 80
cities:
  - {name: "Алматы", city_id: "750000000", zone: "Magnum_ZONE1"}
  - {name: "Астана", city_id: "710000000", zone: "Magnum_ZONE5"}
track:
  - {keyword: "аэрогриль", product_id: "134653775", label: "Наш аэрогриль"}
"""


def test_load_config():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "positions.yaml")
    with open(p, "w") as f:
        f.write(YAML)
    cfg = load_positions_config(p)
    assert cfg.max_depth == 80
    assert cfg.cities[0].name == "Алматы"
    assert cfg.cities[1].city_id == "710000000"
    assert cfg.track[0].keyword == "аэрогриль"
    assert cfg.track[0].product_id == "134653775"


def test_resolve_product_id_from_url():
    url = "https://kaspi.kz/shop/p/aerogril-akane-a-5388-134653775/?c=750000000"
    assert resolve_product_id_from_url(url) == "134653775"


def test_resolve_product_id_prefers_last_numeric_group():
    url = "https://kaspi.kz/shop/p/model-8-l-99887766/"
    assert resolve_product_id_from_url(url) == "99887766"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_positions_config")
