"""
analyst.py — LLM-аналитик. Второй слой, НЕ в петле принятия решений.

Раз в день читает decisions_log + tacos_daily за день и человеческим языком
объясняет, что произошло и что стоит подкрутить, плюс подсвечивает аномалии
(например, SKU, которые тратят бюджет без реальной выручки).

Руль над ставками LLM НЕ отдаём — ядро решений остаётся детерминированным
(core/rules.py). Это только аналитика для владельца.

Сбор дайджеста и сборка промпта — чистые и тестируемые. Реальный вызов Anthropic
вынесен за инъектируемый seam (llm_fn); дефолт использует официальный SDK.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable

MODEL = "claude-opus-5"


def gather_digest(store, day: str) -> dict:
    """Свести решения и TACoS за день в компактную структуру для промпта."""
    decisions = store.get_decisions_for_day(day)
    tacos = store.get_tacos_daily(day)

    action_counts = dict(Counter(d["action"] for d in decisions))
    applied_count = sum(1 for d in decisions if d["applied"])

    # Аномалии: SKU с расходом, но без реальной выручки (tacos is None) — тревожный сигнал.
    anomalies = [t for t in tacos if t["tacos"] is None and t["cost"] > 0]

    return {
        "day": day,
        "decisions": decisions,
        "decisions_total": len(decisions),
        "action_counts": action_counts,
        "applied_count": applied_count,
        "tacos": tacos,
        "anomalies": anomalies,
    }


def build_prompt(digest: dict) -> tuple[str, str]:
    """Собрать (system, user) для LLM. Чистая функция — без сети."""
    system = (
        "Ты — аналитик рекламного автопилота на Kaspi.kz. Бот сам управляет "
        "рекламными ставками по детерминированным правилам (быстрый контур тормозит, "
        "медленный разгоняет по окупаемости TACoS). Твоя задача — раз в день по логу "
        "решений и метрикам простым языком объяснить владельцу, что произошло, "
        "подсветить аномалии и предложить, что подкрутить в порогах. "
        "Ставками ты НЕ управляешь — только советуешь. Пиши по-русски, кратко и по делу."
    )

    lines = [
        f"Разбор за день: {digest['day']}",
        f"Всего решений: {digest['decisions_total']} "
        f"(применено ставками: {digest['applied_count']}).",
        f"По действиям: {digest['action_counts']}.",
        "",
        "TACoS по SKU (tacos=null → расход без реальной выручки):",
    ]
    for t in digest["tacos"]:
        lines.append(
            f"  SKU {t['sku']}: TACoS={t['tacos']}, "
            f"расход={t['cost']}, выручка={t['revenue']}"
        )

    if digest["anomalies"]:
        lines.append("")
        lines.append("АНОМАЛИИ (расход есть, реальной выручки нет):")
        for a in digest["anomalies"]:
            lines.append(f"  SKU {a['sku']}: расход={a['cost']}")

    lines.append("")
    lines.append("Лог решений (sku | действие | контур | причина):")
    for d in digest["decisions"]:
        lines.append(
            f"  {d['sku']} | {d['action']} | {d['loop']} | {d['reason']} "
            f"(ставка {d['old_bid']}→{d['new_bid']})"
        )

    lines.append("")
    lines.append(
        "Дай: (1) короткий вывод по дню, (2) аномалии и их вероятную причину, "
        "(3) 1–3 конкретные рекомендации по порогам в rules.yaml."
    )
    return system, "\n".join(lines)


def run_analysis(store, day: str, llm_fn: Callable[[str, str], str] | None = None) -> str:
    """Собрать дайджест → промпт → вызвать LLM → вернуть человеческий разбор."""
    digest = gather_digest(store, day)
    system, user = build_prompt(digest)
    fn = llm_fn or _anthropic_llm
    return fn(system, user)


def _anthropic_llm(system: str, user: str) -> str:  # pragma: no cover
    """Боевой вызов Anthropic API. Ключ — из окружения (ANTHROPIC_API_KEY)."""
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")
