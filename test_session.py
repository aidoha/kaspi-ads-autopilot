"""
test_session.py — оффлайн-тест session_manager без реального браузера.

Playwright-логин вынесен за инъектируемый seam (login_backend), поэтому вся
логика проверяется без Playwright: переиспользование сохранённой сессии,
свежесть куки, извлечение куки для httpx, и — главное — поведение при
неожиданном экране (SMS/капча): алерт владельцу + СТОП, не крутиться вслепую.

Запуск: .venv/bin/python test_session.py
"""

import json
import os
import tempfile

from connectors.session_manager import (
    SessionManager,
    SessionBlockedError,
    PageSignals,
    classify_page,
    storage_state_to_cookies,
    is_session_fresh,
)

NOW = 1_000_000.0
REQUIRED = ["kaspi_session"]


def fresh_state(expires=NOW + 3600):
    return {
        "cookies": [
            {"name": "kaspi_session", "value": "abc", "domain": ".kaspi.kz", "expires": expires},
            {"name": "other", "value": "x", "domain": ".marketing.kaspi.kz", "expires": expires},
            {"name": "google", "value": "z", "domain": ".google.com", "expires": expires},
        ],
        "origins": [],
    }


class Recorder:
    """Фейковый Notifier — записывает вызовы alert()."""
    def __init__(self):
        self.calls = []

    def alert(self, subject, message):
        self.calls.append((subject, message))


# ---- чистая логика классификации экрана ---------------------------------

def test_classify_page():
    assert classify_page(PageSignals(on_dashboard=True, has_login_form=False,
                                      has_otp_field=False, has_captcha=False)) == "ready"
    assert classify_page(PageSignals(on_dashboard=False, has_login_form=True,
                                     has_otp_field=False, has_captcha=False)) == "needs_login"
    # SMS/OTP — неожиданный экран → blocked (важнее, чем форма логина)
    assert classify_page(PageSignals(on_dashboard=False, has_login_form=True,
                                     has_otp_field=True, has_captcha=False)) == "blocked"
    # капча → blocked
    assert classify_page(PageSignals(on_dashboard=False, has_login_form=False,
                                     has_otp_field=False, has_captcha=True)) == "blocked"
    print("✓ classify_page: ready / needs_login / blocked (SMS и капча приоритетнее)")


# ---- извлечение куки и свежесть -----------------------------------------

def test_storage_state_to_cookies_filters_domain():
    cookies = storage_state_to_cookies(fresh_state())
    assert cookies == {"kaspi_session": "abc", "other": "x"}, cookies  # google отфильтрован
    print("✓ storage_state_to_cookies: только домены kaspi.kz, name→value")


def test_is_session_fresh():
    assert is_session_fresh(fresh_state(), NOW, REQUIRED) is True
    # обязательная кука протухла
    assert is_session_fresh(fresh_state(expires=NOW - 10), NOW, REQUIRED) is False
    # обязательной куки нет вовсе
    stripped = {"cookies": [{"name": "other", "value": "x", "domain": ".kaspi.kz",
                             "expires": NOW + 3600}], "origins": []}
    assert is_session_fresh(stripped, NOW, REQUIRED) is False
    # session-cookie (expires == -1) считаем валидной
    sess = {"cookies": [{"name": "kaspi_session", "value": "a", "domain": ".kaspi.kz",
                        "expires": -1}], "origins": []}
    assert is_session_fresh(sess, NOW, REQUIRED) is True
    print("✓ is_session_fresh: протухание, отсутствие куки, session-cookie")


# ---- поведение SessionManager -------------------------------------------

def _mgr(tmp, backend, notifier=None):
    return SessionManager(
        merchant_login="user",
        merchant_password="pass",
        storage_path=tmp,
        required_cookies=REQUIRED,
        login_backend=backend,
        notifier=notifier,
        now_fn=lambda: NOW,
    )


def test_reuses_fresh_session_without_login():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "storage_state.json")
        with open(path, "w") as f:
            json.dump(fresh_state(), f)

        def backend():
            raise AssertionError("login НЕ должен вызываться при свежей сессии")

        mgr = _mgr(path, backend)
        cookies = mgr.get_cookies()
        assert cookies == {"kaspi_session": "abc", "other": "x"}
    print("✓ get_cookies: свежая сессия переиспользуется без логина")


def test_logs_in_when_no_state_and_saves():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "storage_state.json")
        calls = {"n": 0}

        def backend():
            calls["n"] += 1
            return fresh_state()

        mgr = _mgr(path, backend)
        cookies = mgr.get_cookies()
        assert calls["n"] == 1
        assert cookies == {"kaspi_session": "abc", "other": "x"}
        assert os.path.exists(path), "storage_state должен сохраниться после логина"
        with open(path) as f:
            assert json.load(f)["cookies"], "сохранённое состояние не пустое"
    print("✓ get_cookies: нет сессии → логин → storage сохранён")


def test_blocked_screen_alerts_and_stops():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "storage_state.json")
        notifier = Recorder()

        def backend():
            raise SessionBlockedError("SMS-подтверждение на входе")

        mgr = _mgr(path, backend, notifier=notifier)
        try:
            mgr.get_cookies()
            raise AssertionError("ожидали SessionBlockedError")
        except SessionBlockedError:
            pass
        assert len(notifier.calls) == 1, "владелец должен получить ровно один алерт"
        assert not os.path.exists(path), "битую сессию НЕ сохраняем"
    print("✓ get_cookies: неожиданный экран → алерт владельцу + СТОП, storage не тронут")


if __name__ == "__main__":
    test_classify_page()
    test_storage_state_to_cookies_filters_domain()
    test_is_session_fresh()
    test_reuses_fresh_session_without_login()
    test_logs_in_when_no_state_and_saves()
    test_blocked_screen_alerts_and_stops()
    print("-" * 60)
    print("✓ Все проверки session_manager прошли")
