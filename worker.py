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

from core.config_resolver import resolve_config
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
    # Названия товаров едут в OrderEntry.name — сохраняем их для подписи дашборда.
    ctx.store.put_product_names({ms: r.name for ms, r in revenue.items()}, ts=ts)
    log.info("Revenue-цикл: обновлено SKU в кэше = %s", len(revenue))
    return revenue


# ---- ставочный тик ---------------------------------------------------------

def run_tick(ctx: WorkerContext, loop: str, campaign_id: str,
             daily_budget: float = 0.0):
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
    ctx.store.save_products_snapshot(products, ts, campaign_id=campaign_id)

    # Имена из маркетинга (title) → product_names: у рекламируемых товаров имя
    # есть даже без заказов, где Shop API его не даёт (worker.py:revenue-цикл
    # покрывает только проданные). Пустые не пишем — не затираем известное.
    mk_names = {p.merchant_sku: p.name for p in products if p.merchant_sku and p.name}
    if mk_names:
        ctx.store.put_product_names(mk_names, ts)

    revenue = ctx.store.get_revenue_cache()
    reconciled = reconcile(products, revenue)

    for r in reconciled:
        ctx.store.record_tacos(day, r.merchant_sku, r.tacos, r.cost, r.revenue)

    # Эффективный конфиг на SKU: глобал → оверрайды кампании → оверрайды SKU.
    camp_ov = ctx.store.get_overrides("campaign", campaign_id)

    def cfg_for(s):
        return resolve_config(ctx.cfg, camp_ov, ctx.store.get_overrides("sku", s.sku))

    if loop == "fast":
        decisions = evaluate_fast(reconciled, cfg_for, state, daily_budget)
    elif loop == "slow":
        decisions = evaluate_slow(reconciled, cfg_for, state)
    else:
        raise ValueError(f"неизвестный контур: {loop}")

    _apply_and_log(ctx, decisions, day, ts, campaign_id)
    log.info("Тик %s: решений=%s, изменений=%s", loop, len(decisions),
             sum(1 for d in decisions if d.changed))
    return decisions


def run_cycle(ctx: WorkerContext, loop: str):
    """
    Один цикл выбранного контура по ВСЕМ активным кампаниям: обнаруживает
    Enabled-кампании (с учётом allowlist ctx.campaign_ids) и гоняет run_tick
    по каждой. Изоляция: падение одной кампании логируется и не рушит остальные;
    недоступный список кампаний → цикл пропускаем, планировщик жив.
    """
    now = ctx.now_fn()
    start_date, end_date = _almaty_dates(ctx.window_days, now)
    try:
        campaigns = ctx.marketing.list_active_campaigns(start_date, end_date)
    except Exception as e:
        log.error("Список кампаний недоступен, цикл %s пропущен: %s", loop, e)
        return []

    allow = set(ctx.campaign_ids or [])
    if allow:
        campaigns = [c for c in campaigns if c.id in allow]

    all_decisions: list = []
    for c in campaigns:
        try:
            all_decisions.extend(run_tick(ctx, loop, c.id, daily_budget=c.daily_budget))
        except Exception as e:
            log.error("Кампания %s (%s) упала на контуре %s: %s",
                      c.id, c.name, loop, e)
            continue

    log.info("Цикл %s: кампаний=%s, решений=%s, изменений=%s", loop,
             len(campaigns), len(all_decisions),
             sum(1 for d in all_decisions if d.changed))
    return all_decisions


def _apply_and_log(ctx: WorkerContext, decisions: list, day: str, ts: int,
                   campaign_id: str):
    """
    Шлём ставочные изменения батчами по значению ставки. pause отдельного
    эндпоинта не имеет — его решение уже несёт new_bid = min_bid (ставка в пол),
    поэтому применяется тем же update_bids, что raise/lower.
    Каждое решение (включая hold) идёт в аудит-лог.
    """
    # Инвариант аудита (боевой режим): каждый успешно применённый батч пишется
    # в лог СРАЗУ после своего PUT, до следующего. Иначе падение позднего PUT
    # оставило бы ранее изменённые в кабинете ставки без строки в аудите
    # (частичная запись — деньги двинулись, следов нет).
    by_bid: dict[float, list] = defaultdict(list)
    holds: list = []
    for d in decisions:
        if d.action in ("raise", "lower", "pause"):
            by_bid[d.new_bid].append(d)
        else:
            holds.append(d)

    for bid, batch in by_bid.items():
        result = ctx.marketing.update_bids(campaign_id, [d.sku for d in batch], bid)
        applied = bool(result.get("sent"))
        for d in batch:  # аудит этого батча — до следующего PUT
            ctx.store.log_decision(d, ts=ts, day=day, applied=applied,
                                   campaign_id=campaign_id)

    for d in holds:  # решения без PUT (hold) — тоже в аудит
        ctx.store.log_decision(d, ts=ts, day=day, applied=False,
                               campaign_id=campaign_id)


# ---- чтение конфига с fallback -------------------------------------------------

def load_cfg_safe(path: str, prev):
    """Читает rules.yaml; при ошибке парсинга возвращает prev (не роняем воркер).
    prev=None и битый файл → пробрасываем ошибку (нечем фолбэкнуться на старте)."""
    from core.rules import load_rules_config
    try:
        return load_rules_config(path)
    except Exception as e:
        if prev is None:
            raise
        log.error("rules.yaml не читается (%s) — оставляю прошлый конфиг", e)
        return prev


# ---- боевая обвязка (тонкая, не под тестом) --------------------------------

def main():  # pragma: no cover
    """Собирает боевые зависимости и запускает планировщик. Только тут — сеть/браузер."""
    import os
    import sys
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

    rules_path = os.environ.get("RULES_CONFIG", "config/rules.yaml")
    cfg_holder = {"cfg": load_cfg_safe(rules_path, None)}
    merchant_id = os.environ["KASPI_MARKETING_MERCHANT_ID"]
    env_ids = os.environ.get("KASPI_CAMPAIGN_IDS", "").strip()
    env_ids = [c.strip() for c in env_ids.split(",") if c.strip()] or None

    store = Store(os.environ.get("DB_PATH", "db/autopilot.db"))
    session = SessionManager(
        merchant_login=os.environ["KASPI_MARKETING_LOGIN"],
        merchant_password=os.environ["KASPI_MARKETING_PASSWORD"],
        storage_path=os.environ.get("STORAGE_STATE", "storage_state.json"),
    )
    merchant = MerchantClient(auth_token=os.environ["KASPI_MERCHANT_TOKEN"])
    revenue_collector = RevenueCollector(merchant)

    def build_ctx() -> WorkerContext:
        # Читаем cfg каждый цикл (hot-reload rules.yaml)
        cfg = load_cfg_safe(rules_path, cfg_holder["cfg"])
        cfg_holder["cfg"] = cfg
        # Свежие куки на каждый цикл; при блокировке SessionManager сам поднимет алерт+стоп.
        cookies = session.get_cookies()
        # on_auth_error: на 401/403 (сессия инвалидирована сервером раньше
        # таймстампа куки) клиент форсирует релогин и повторяет запрос.
        marketing = MarketingClient(
            merchant_id, cookies=cookies, dry_run=cfg.dry_run,
            on_auth_error=lambda: session.get_cookies(force_refresh=True),
        )
        campaign_ids = cfg.campaign_ids or env_ids
        return WorkerContext(marketing=marketing, store=store, cfg=cfg,
                             campaign_ids=campaign_ids,
                             revenue_collector=revenue_collector)

    # Смоук-режим: разовый прогон всего конвейера и выход (без планировщика).
    # Для проверки на VPS сразу после выката — что IP не блокируется, логин
    # проходит, кампании читаются, решения логируются. Ставки не трогаются (dry_run).
    if "--once" in sys.argv:
        log.info("Разовый прогон (--once): revenue → fast → slow, затем выход.")
        run_revenue_cycle(build_ctx())
        run_cycle(build_ctx(), "fast")
        run_cycle(build_ctx(), "slow")
        log.info("Разовый прогон завершён (dry_run=%s). Проверь решения в логе выше.",
                 cfg_holder["cfg"].dry_run)
        return

    sched = BlockingScheduler(timezone="Asia/Almaty")
    sched.add_job(lambda: run_revenue_cycle(build_ctx()), "interval", minutes=60,
                  id="revenue")
    sched.add_job(lambda: run_cycle(build_ctx(), "fast"), "interval", minutes=5,
                  id="fast")
    sched.add_job(lambda: run_cycle(build_ctx(), "slow"), "cron", hour="10,20",
                  id="slow")

    def analyst_job():
        # Дневной разбор для владельца (advisory, не в петле решений).
        day = datetime.now(ALMATY).date().isoformat()
        text = run_analysis(store, day)
        log.info("Аналитик за %s:\n%s", day, text)

    sched.add_job(analyst_job, "cron", hour="22", id="analyst")

    log.info("Автопилот запущен (dry_run=%s, кампании=%s). Расписания: revenue/60м, "
             "fast/5м, slow/10:00,20:00, analyst/22:00 (Алматы)",
             cfg_holder["cfg"].dry_run, cfg_holder["cfg"].campaign_ids or env_ids or "все активные")
    sched.start()


if __name__ == "__main__":  # pragma: no cover
    main()
