"""
test_analyst.py — тест LLM-аналитика (сбор дайджеста + сборка промпта) без сети.

ИИ здесь — второй слой, НЕ в петле: раз в день читает decisions_log + tacos_daily
и человеческим языком объясняет, что произошло и что подкрутить. Руль над ставками
ему НЕ отдаём.

Реальный вызов Anthropic API вынесен за инъектируемый seam (llm_fn), поэтому сбор
дайджеста и сборка промпта тестируются оффлайн, без ключа и сети.

Запуск: .venv/bin/python test_analyst.py
"""

import os
import tempfile

from core.rules import Decision
from core.store import Store
from analyst import gather_digest, build_prompt, run_analysis

DAY = "2026-08-09"


def dec(**over):
    base = dict(sku="SKU1", merchant_sku="M1", old_bid=18, new_bid=16,
                action="lower", loop="fast", reason="0 корзин на 60 кликах")
    base.update(over)
    return Decision(**base)


def store_with_day():
    st = Store(os.path.join(tempfile.mkdtemp(), "a.db"))
    st.record_tacos(DAY, "M1", tacos=0.037, cost=3600, revenue=97800)
    st.record_tacos(DAY, "M2", tacos=None, cost=500, revenue=0)  # расход без выручки
    st.log_decision(dec(action="lower"), ts=1000, day=DAY, applied=True)
    st.log_decision(dec(action="hold", reason="в коридоре"), ts=1100, day=DAY, applied=False)
    st.log_decision(dec(action="raise", loop="slow", reason="TACoS низкий"), ts=1200, day=DAY, applied=True)
    return st


def test_gather_digest_summarizes():
    d = gather_digest(store_with_day(), DAY)
    assert d["day"] == DAY
    assert d["decisions_total"] == 3
    assert d["action_counts"] == {"lower": 1, "hold": 1, "raise": 1}
    assert d["applied_count"] == 2                      # lower + raise
    # SKU без выручки подсвечен как аномалия
    assert any(s["sku"] == "M2" and s["tacos"] is None for s in d["tacos"])
    print("✓ gather_digest: счётчики действий, applied, аномалия без выручки")


def test_build_prompt_contains_facts():
    d = gather_digest(store_with_day(), DAY)
    system, user = build_prompt(d)
    assert "ставк" in system.lower()                   # роль про рекламные ставки
    assert DAY in user
    assert "M2" in user                                # проблемный SKU попал в промпт
    assert "0 корзин на 60 кликах" in user             # причина решения передана
    print("✓ build_prompt: день, проблемный SKU и причины в промпте")


def test_run_analysis_uses_injected_llm():
    calls = {}

    def fake_llm(system, user):
        calls["system"] = system
        calls["user"] = user
        return "Разбор: сегодня резали ставки по нехватке корзин."

    out = run_analysis(store_with_day(), DAY, llm_fn=fake_llm)
    assert out.startswith("Разбор:")
    assert DAY in calls["user"]                        # промпт реально ушёл в llm
    print("✓ run_analysis: зовёт инъектированный llm_fn и возвращает его текст")


if __name__ == "__main__":
    test_gather_digest_summarizes()
    test_build_prompt_contains_facts()
    test_run_analysis_uses_injected_llm()
    print("-" * 60)
    print("✓ Все проверки analyst прошли")
