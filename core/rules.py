"""
rules.py — детерминированный движок ставок. Два независимых контура.

ЯДРО ЛОГИКИ (согласовано, НЕ переигрывать без владельца):
  • Быстрый контур (каждые 15–30 мин): ТОЛЬКО тормозит/паузит. Никогда не поднимает.
    Защита от слива бюджета на сигналах, которые НЕ запаздывают.
  • Медленный контур (1–2 раза в день, по TACoS за окно): двигает ставку по
    ОКУПАЕМОСТИ — поднимает, если TACoS ниже коридора; снижает, если выше.
  • Разгон на внутридневном сигнале — ГЛАВНАЯ ошибка (утром cost капает, заказы
    приходят вечером с лагом «после 16:00 → завтра»). Поэтому raise живёт ТОЛЬКО
    в медленном контуре и только по TACoS.

Движок ЧИСТЫЙ: принимает сшитые SKU + конфиг + суточное состояние, возвращает
список Decision. Ввод-вывод (PUT ставок) — обязанность worker (step 5).
Каждое решение несёт причину — для полного аудит-лога.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class RulesConfig:
    """Пороги движка. Дефолты = консервативный старт (совпадают с config/rules.yaml)."""
    target_tacos_low: float = 0.08
    target_tacos_high: float = 0.15
    daily_sku_cost_limit: float = 3000
    min_clicks_for_no_cart_cut: int = 40
    cpc_spike_pct: float = 0.5
    max_bid_step: float = 2
    max_changes_per_day: int = 3
    bid_ceiling: float = 50
    min_bid: float = 1
    min_score_for_raise: float = 4.0
    dry_run: bool = True


@dataclass
class DailyState:
    """Суточное состояние по SKU: сколько раз меняли ставку и прошлый avgCpc (для скачка)."""
    changes_today: int = 0
    prev_avg_cpc: float | None = None


@dataclass
class Decision:
    sku: str
    merchant_sku: str
    old_bid: float
    new_bid: float
    action: str          # hold | raise | lower | pause
    loop: str            # fast | slow | none
    reason: str

    @property
    def changed(self) -> bool:
        return self.action != "hold"


def load_rules_config(path: str) -> RulesConfig:
    """Читает config/rules.yaml, накладывая значения поверх дефолтов RulesConfig."""
    import yaml  # локальный импорт: движок тестируется без установленного PyYAML
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    known = {f.name for f in fields(RulesConfig)}
    return RulesConfig(**{k: v for k, v in raw.items() if k in known})


# ---- вспомогательное ------------------------------------------------------

def _state_for(state: dict | None, sku: str) -> DailyState:
    return (state or {}).get(sku) or DailyState()


def _hold(s, loop: str, reason: str) -> Decision:
    return Decision(s.sku, s.merchant_sku, s.bid, s.bid, "hold", loop, reason)


def _stepped(s, direction: str, loop: str, reason: str, cfg: RulesConfig) -> Decision:
    """
    Сдвиг ставки на шаг в пределах [min_bid, bid_ceiling]. Если упёрлись в
    границу и ставка не меняется — это hold (менять нечего).
    """
    if direction == "raise":
        new_bid = min(s.bid + cfg.max_bid_step, cfg.bid_ceiling)
    else:  # lower
        new_bid = max(s.bid - cfg.max_bid_step, cfg.min_bid)
    if new_bid == s.bid:
        edge = "потолок" if direction == "raise" else "пол"
        return _hold(s, loop, f"{reason}, но ставка у границы ({edge})")
    return Decision(s.sku, s.merchant_sku, s.bid, new_bid, direction, loop, reason)


# ---- быстрый контур (только тормозит) -------------------------------------

def evaluate_fast(
    skus: list, cfg: RulesConfig | None = None, state: dict | None = None
) -> list[Decision]:
    cfg = cfg or RulesConfig()
    out: list[Decision] = []
    for s in skus:
        out.append(_eval_fast_one(s, cfg, _state_for(state, s.sku)))
    return out


def _eval_fast_one(s, cfg: RulesConfig, st: DailyState) -> Decision:
    if s.product_state != "Active":
        return _hold(s, "fast", "товар не Active — ставку не трогаем")

    # Спенд-кап важнее лимита изменений: слив бюджета тормозим всегда.
    # Эндпоинта «паузы» у кабинета нет — режем ставку в пол (min_bid), это и есть стоп.
    if s.cost_today >= cfg.daily_sku_cost_limit:
        return Decision(s.sku, s.merchant_sku, s.bid, cfg.min_bid, "pause", "fast",
                        f"costToday={s.cost_today} ≥ дневного лимита {cfg.daily_sku_cost_limit} "
                        f"→ пауза (ставка в пол {cfg.min_bid})")

    if st.changes_today >= cfg.max_changes_per_day:
        return _hold(s, "fast", f"исчерпан лимит изменений/сутки ({cfg.max_changes_per_day})")

    # 0 корзин при достаточном объёме кликов.
    if s.carts == 0 and s.clicks >= cfg.min_clicks_for_no_cart_cut:
        return _stepped(s, "lower", "fast",
                        f"{s.clicks} кликов, 0 корзин → срез ставки", cfg)

    # Скачок avgCpc относительно прошлого снапшота.
    if st.prev_avg_cpc and s.avg_cpc > st.prev_avg_cpc * (1 + cfg.cpc_spike_pct):
        return _stepped(s, "lower", "fast",
                        f"avgCpc {s.avg_cpc} подскочил >{int(cfg.cpc_spike_pct*100)}% "
                        f"(было {st.prev_avg_cpc}) → срез ставки", cfg)

    return _hold(s, "fast", "тормозных триггеров нет")


# ---- медленный контур (по TACoS) ------------------------------------------

def evaluate_slow(
    skus: list, cfg: RulesConfig | None = None, state: dict | None = None
) -> list[Decision]:
    cfg = cfg or RulesConfig()
    out: list[Decision] = []
    for s in skus:
        out.append(_eval_slow_one(s, cfg, _state_for(state, s.sku)))
    return out


def _eval_slow_one(s, cfg: RulesConfig, st: DailyState) -> Decision:
    if s.product_state != "Active":
        return _hold(s, "slow", "товар не Active — ставку не трогаем")

    if st.changes_today >= cfg.max_changes_per_day:
        return _hold(s, "slow", f"исчерпан лимит изменений/сутки ({cfg.max_changes_per_day})")

    # Расход без выручки за окно = худшая окупаемость → снижаем.
    if s.tacos is None:
        return _stepped(s, "lower", "slow",
                        "за окно расход есть, реальной выручки нет → снижаем", cfg)

    # TACoS выше коридора → окупаемость плохая, снижаем.
    if s.tacos > cfg.target_tacos_high:
        return _stepped(s, "lower", "slow",
                        f"TACoS {s.tacos:.3f} > {cfg.target_tacos_high} → снижаем", cfg)

    # TACoS ниже коридора → реклама дёшево окупается, можно поднять.
    if s.tacos < cfg.target_tacos_low:
        # но низкий score не разгоняем (плохой товар в рекламе)
        if s.score < cfg.min_score_for_raise:
            return _hold(s, "slow",
                         f"TACoS низкий, но score {s.score} < {cfg.min_score_for_raise} — не поднимаем")
        return _stepped(s, "raise", "slow",
                        f"TACoS {s.tacos:.3f} < {cfg.target_tacos_low} → поднимаем", cfg)

    return _hold(s, "slow", f"TACoS {s.tacos:.3f} в коридоре")
