"""
worker.py — оркестрация автопилота. Два независимых расписания + цикл выручки.

  • run_tick(ctx, "fast")  — часто (15–30 мин): тормозной контур.
  • run_tick(ctx, "slow")  — 1–2 раза в день: разгон/снижение по TACoS.
  • run_revenue_cycle(ctx) — реже (напр. раз в час): тяжёлый обход Shop API,
    обновляет revenue_cache в SQLite (тик берёт выручку уже из кэша, не из сети).

Оркестрация ЧИСТАЯ от планировщика: сами функции тестируются с фейками.
APScheduler и построение боевых зависимостей — только в main() (тонкая обвязка).

Предохранитель вывода — на клиенте маркетинга (dry_run): в dry_run update_bids
не шлёт PUT, а тик всё равно логирует каждое решение с причиной.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from core.reconcile import reconcile
from core.rules import RulesConfig, evaluate_fast, evaluate_slow

log = logging.getLogger("worker")

ALMATY = ZoneInfo("Asia/Almaty")


@dataclass
class WorkerContext:
    marketing: object            # MarketingClient (несёт свой dry_run)
    store: object                # Store
    cfg: RulesConfig
    campaign_ids: list[str] | None = None     # allowlist; None/пусто = все активные
    revenue_collector: object | None = None   # RevenueCollector (для revenue-цикла)
    window_days: int = 2
    now_fn: Callable[[], datetime] = field(default=lambda: datetime.now(ALMATY))


def _almaty_dates(window_days: int, now: datetime) -> tuple[str, str]:
    """(StartDate, EndDate) в YYYY-MM-DD по Алматы — окно для метрик маркетинга."""
    now = now.astimezone(ALMATY)
    end = now.date()
    start = end - timedelta(days=window_days - 1)
    return start.isoformat(), end.isoformat()


# ---- цикл выручки (тяжёлый обход Shop API) ---------------------------------

def run_revenue_cycle(ctx: WorkerContext):
    """Обходит Shop API за окно и складывает выручку по merchant_sku в кэш."""
    now = ctx.now_fn()
    ts = int(now.timestamp())
    revenue = ctx.revenue_collector.collect(window_days=ctx.window_days, now=now)
    ctx.store.put_revenue_cache(revenue, ts=ts)
    log.info("Revenue-цикл: обновлено SKU в кэше = %s", len(revenue))
    return revenue


# ---- ставочный тик ---------------------------------------------------------

def run_tick(ctx: WorkerContext, loop: str, campaign_id: str):
    """
    Один тик выбранного контура:
      read маркетинг → снапшот → выручка из кэша → reconcile → TACoS →
      rules(loop) → apply(PUT или dry_run) → полный лог.
    """
    now = ctx.now_fn()
    ts = int(now.timestamp())
    day = now.astimezone(ALMATY).date().isoformat()
    start_date, end_date = _almaty_dates(ctx.window_days, now)

    products = ctx.marketing.get_campaign_products(campaign_id, start_date, end_date)

    # Состояние берём ДО записи нового снапшота — так prev_avg_cpc = прошлый снимок.
    state = ctx.store.build_daily_state([p.sku for p in products], day)
    ctx.store.save_products_snapshot(products, ts)

    revenue = ctx.store.get_revenue_cache()
    reconciled = reconcile(products, revenue)

    for r in reconciled:
        ctx.store.record_tacos(day, r.merchant_sku, r.tacos, r.cost, r.revenue)

    if loop == "fast":
        decisions = evaluate_fast(reconciled, ctx.cfg, state)
    elif loop == "slow":
        decisions = evaluate_slow(reconciled, ctx.cfg, state)
    else:
        raise ValueError(f"неизвестный контур: {loop}")

    _apply_and_log(ctx, decisions, day, ts, campaign_id)
    log.info("Тик %s: решений=%s, изменений=%s", loop, len(decisions),
             sum(1 for d in decisions if d.changed))
    return decisions


def _apply_and_log(ctx: WorkerContext, decisions: list, day: str, ts: int,
                   campaign_id: str):
    """
    Шлём ставочные изменения батчами по значению ставки. pause отдельного
    эндпоинта не имеет — его решение уже несёт new_bid = min_bid (ставка в пол),
    поэтому применяется тем же update_bids, что raise/lower.
    Каждое решение (включая hold) идёт в аудит-лог.
    """
    by_bid: dict[float, list[str]] = defaultdict(list)
    for d in decisions:
        if d.action in ("raise", "lower", "pause"):
            by_bid[d.new_bid].append(d.sku)

    sent_skus: dict[str, bool] = {}
    for bid, skus in by_bid.items():
        result = ctx.marketing.update_bids(campaign_id, skus, bid)
        for sku in skus:
            sent_skus[sku] = bool(result.get("sent"))

    for d in decisions:
        applied = sent_skus.get(d.sku, False)
        ctx.store.log_decision(d, ts=ts, day=day, applied=applied,
                               campaign_id=campaign_id)


# ---- боевая обвязка (тонкая, не под тестом) --------------------------------

def main():  # pragma: no cover
    """Собирает боевые зависимости и запускает планировщик. Только тут — сеть/браузер."""
    import os
    from apscheduler.schedulers.blocking import BlockingScheduler

    from connectors.merchant_client import MerchantClient
    from connectors.marketing_client import MarketingClient
    from connectors.session_manager import SessionManager
    from core.revenue import RevenueCollector
    from core.rules import load_rules_config
    from core.store import Store
    from analyst import run_analysis

    # Подхватить .env, если он есть и установлен python-dotenv (для запуска без systemd).
    try:
        from dotenv import load_dotenv
        load_dotenv(os.environ.get("ENV_FILE", "config/.env"))
    except ImportError:
        pass

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_rules_config(os.environ.get("RULES_CONFIG", "config/rules.yaml"))
    merchant_id = os.environ["KASPI_MARKETING_MERCHANT_ID"]
    campaign_id = os.environ["KASPI_CAMPAIGN_ID"]

    store = Store(os.environ.get("DB_PATH", "db/autopilot.db"))
    session = SessionManager(
        merchant_login=os.environ["KASPI_MARKETING_LOGIN"],
        merchant_password=os.environ["KASPI_MARKETING_PASSWORD"],
        storage_path=os.environ.get("STORAGE_STATE", "storage_state.json"),
    )
    merchant = MerchantClient(auth_token=os.environ["KASPI_MERCHANT_TOKEN"])
    revenue_collector = RevenueCollector(merchant)

    def build_ctx() -> WorkerContext:
        # Свежие куки на каждый цикл; при блокировке SessionManager сам поднимет алерт+стоп.
        cookies = session.get_cookies()
        marketing = MarketingClient(merchant_id, cookies=cookies, dry_run=cfg.dry_run)
        return WorkerContext(marketing=marketing, store=store,
                             cfg=cfg, revenue_collector=revenue_collector)

    sched = BlockingScheduler(timezone="Asia/Almaty")
    sched.add_job(lambda: run_revenue_cycle(build_ctx()), "interval", minutes=60,
                  id="revenue")
    sched.add_job(lambda: run_tick(build_ctx(), "fast", campaign_id), "interval", minutes=20,
                  id="fast")
    sched.add_job(lambda: run_tick(build_ctx(), "slow", campaign_id), "cron", hour="10,20",
                  id="slow")

    def analyst_job():
        # Дневной разбор для владельца (advisory, не в петле решений).
        day = datetime.now(ALMATY).date().isoformat()
        text = run_analysis(store, day)
        log.info("Аналитик за %s:\n%s", day, text)

    sched.add_job(analyst_job, "cron", hour="22", id="analyst")

    log.info("Автопилот запущен (dry_run=%s). Расписания: revenue/60м, fast/20м, "
             "slow/10:00,20:00, analyst/22:00 (Алматы)", cfg.dry_run)
    sched.start()


if __name__ == "__main__":  # pragma: no cover
    main()
