"""
session_manager.py — авто-логин на маркетинговый кабинет (marketing.kaspi.kz)
и выдача свежих куки для marketing_client.

У владельца на входе НЕТ SMS/2FA (только логин+пароль) → возможен полный
автологин через Playwright: headless логинится сам, сохраняет storage_state,
переиспользует и продлевает сессию.

Железное правило: если Kaspi покажет НЕОЖИДАННЫЙ экран (SMS/капча/подтверждение
нового устройства с нового VPS-IP) — НЕ крутиться вслепую, а ОСТАНОВИТЬСЯ и
послать алерт владельцу. Это SessionBlockedError → воркер её ловит и встаёт.

Playwright импортируется ЛЕНИВО (внутри боевого бэкенда), поэтому вся логика
модуля тестируется оффлайн без установленного браузера. Способ логина вынесен
за инъектируемый seam login_backend — тесты подсовывают фейковый.
"""

from __future__ import annotations

import os
import json
import time
import logging
from dataclasses import dataclass
from typing import Callable, Protocol

log = logging.getLogger("session")

LOGIN_URL = "https://marketing.kaspi.kz/"

# Плейсхолдер — точное имя сессионной куки кабинета подтвердить на живом входе
# (посмотреть storage_state после ручного логина). До подтверждения работает
# страховка: marketing_client получит 401/редирект → worker дёрнет force_refresh.
DEFAULT_REQUIRED_COOKIES = ["kaspi_session"]

KASPI_DOMAIN = "kaspi.kz"


class SessionError(Exception):
    """Базовая ошибка сессии."""


class SessionBlockedError(SessionError):
    """
    Неожиданный экран на входе (SMS/капча/новое устройство) — автологин невозможен,
    нужен ручной вход владельца. Воркер обязан остановиться и не слать запросы.
    """


class Notifier(Protocol):
    """Куда слать алерт владельцу. Канал (лог/Telegram/почта) подключается снаружи."""
    def alert(self, subject: str, message: str) -> None: ...


class LogNotifier:
    """Дефолтный нотификатор: пишет алерт в лог. Реальный канал плагается позже."""
    def alert(self, subject: str, message: str) -> None:
        log.error("ALERT | %s | %s", subject, message)


@dataclass
class PageSignals:
    """Признаки экрана после навигации — заполняет Playwright-бэкенд."""
    on_dashboard: bool
    has_login_form: bool
    has_otp_field: bool
    has_captcha: bool


def classify_page(sig: PageSignals) -> str:
    """
    Классификация экрана: "ready" | "needs_login" | "blocked".
    Неожиданный экран (OTP/капча) приоритетнее формы логина — увидев его,
    НЕ пытаемся логиниться дальше, а поднимаем блок.
    """
    if sig.has_otp_field or sig.has_captcha:
        return "blocked"
    if sig.on_dashboard:
        return "ready"
    if sig.has_login_form:
        return "needs_login"
    # Ничего знакомого не увидели — тоже считаем неожиданным экраном.
    return "blocked"


def storage_state_to_cookies(state: dict, domain_contains: str = KASPI_DOMAIN) -> dict:
    """Плоский dict name→value из cookies storage_state, только для доменов Kaspi."""
    out: dict[str, str] = {}
    for c in state.get("cookies", []):
        if domain_contains in c.get("domain", ""):
            out[c["name"]] = c["value"]
    return out


def is_session_fresh(state: dict, now_ts: float, required_cookies: list[str]) -> bool:
    """
    Свежа ли сессия: все обязательные куки присутствуют и не протухли.
    expires == -1 (session-cookie) считаем валидной.
    """
    by_name = {c["name"]: c for c in state.get("cookies", [])}
    for name in required_cookies:
        c = by_name.get(name)
        if c is None:
            return False
        expires = c.get("expires", -1)
        if expires != -1 and expires <= now_ts:
            return False
    return True


class SessionManager:
    def __init__(
        self,
        merchant_login: str,
        merchant_password: str,
        storage_path: str,
        *,
        required_cookies: list[str] | None = None,
        notifier: Notifier | None = None,
        login_backend: Callable[[], dict] | None = None,
        now_fn: Callable[[], float] = time.time,
    ):
        if not merchant_login or not merchant_password:
            raise ValueError("Логин/пароль маркетинга пусты — проверь .env")
        self.login = merchant_login
        self.password = merchant_password
        self.storage_path = storage_path
        self.required_cookies = required_cookies or DEFAULT_REQUIRED_COOKIES
        self.notifier = notifier or LogNotifier()
        self._login_backend = login_backend or self._playwright_login
        self._now = now_fn

    # ---- публичный вход ------------------------------------------------------

    def get_cookies(self, force_refresh: bool = False) -> dict:
        """
        Свежие куки для marketing_client. Переиспользует сохранённую сессию, если
        она валидна; иначе логинится заново. При неожиданном экране — алерт + СТОП.
        """
        if not force_refresh:
            state = self._load_state()
            if state and is_session_fresh(state, self._now(), self.required_cookies):
                log.info("Сессия из %s валидна, переиспользуем", self.storage_path)
                return storage_state_to_cookies(state)

        log.info("Сессия невалидна/отсутствует → логинимся")
        try:
            state = self._login_backend()
        except SessionBlockedError as e:
            # Не крутимся вслепую: зовём владельца и пробрасываем стоп наверх.
            self.notifier.alert(
                "Kaspi marketing: нужен ручной вход",
                f"Автологин остановлен: {e}. Зайди в кабинет вручную.",
            )
            raise
        self._save_state(state)
        return storage_state_to_cookies(state)

    # ---- storage_state I/O ---------------------------------------------------

    def _load_state(self) -> dict | None:
        if not os.path.exists(self.storage_path):
            return None
        try:
            with open(self.storage_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Не смог прочитать %s: %s", self.storage_path, e)
            return None

    def _save_state(self, state: dict) -> None:
        os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
        tmp = f"{self.storage_path}.tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, self.storage_path)  # атомарно
        log.info("storage_state сохранён в %s", self.storage_path)

    # ---- боевой бэкенд: Playwright (ленивый импорт) --------------------------

    def _playwright_login(self) -> dict:
        """
        Реальный автологин через headless Chromium. Заполняет логин/пароль,
        классифицирует итоговый экран и на неожиданном (OTP/капча) поднимает
        SessionBlockedError. Возвращает storage_state (dict).

        ВНИМАНИЕ: селекторы формы Kaspi нужно подтвердить на живом кабинете —
        это единственное место, требующее проверки глазами в браузере.
        """
        from playwright.sync_api import sync_playwright  # ленивый импорт

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded")

                # Форма логина (селекторы — подтвердить на живом входе).
                if page.query_selector("input[name='login'], input[type='email']"):
                    page.fill("input[name='login'], input[type='email']", self.login)
                    page.fill("input[type='password']", self.password)
                    page.click("button[type='submit']")
                    page.wait_for_load_state("networkidle")

                sig = PageSignals(
                    on_dashboard="marketing.kaspi.kz" in page.url
                    and not page.query_selector("input[type='password']"),
                    has_login_form=bool(page.query_selector("input[type='password']")),
                    has_otp_field=bool(page.query_selector(
                        "input[name='otp'], input[autocomplete='one-time-code']")),
                    has_captcha=bool(page.query_selector(
                        "iframe[src*='captcha'], .captcha, #captcha")),
                )
                verdict = classify_page(sig)
                if verdict == "blocked":
                    raise SessionBlockedError(
                        "неожиданный экран (SMS/капча/новое устройство)")
                if verdict == "needs_login":
                    raise SessionBlockedError("логин не прошёл (осталась форма входа)")

                return context.storage_state()
            finally:
                context.close()
                browser.close()
