"""
positions_config.py — конфиг трекера позиций: города (city_id+zone), какие
ключевые слова и наши product_id трекать, глубина обхода.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import yaml


@dataclass
class City:
    name: str
    city_id: str
    zone: str


@dataclass
class TrackItem:
    keyword: str
    product_id: str
    label: str


@dataclass
class PositionsConfig:
    cities: list[City]
    track: list[TrackItem]
    max_depth: int


def load_positions_config(path: str) -> PositionsConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cities = [City(**c) for c in (raw.get("cities") or [])]
    track = [TrackItem(**t) for t in (raw.get("track") or [])]
    return PositionsConfig(cities=cities, track=track,
                           max_depth=int(raw.get("max_depth", 100)))


def resolve_product_id_from_url(url: str) -> str:
    """Из ссылки на карточку берём последнюю числовую группу (id мастер-продукта)."""
    path = url.split("?", 1)[0]
    nums = re.findall(r"(\d{5,})", path)
    if not nums:
        raise ValueError(f"product_id не найден в URL: {url}")
    return nums[-1]
