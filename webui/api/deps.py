"""deps.py — общий контекст и зависимости JSON API панели.

Старый create_app держал пути к конфигу и БД в замыкании. Роутерам нужно то
же самое, но замыкание через модули не протащить — поэтому маленький
контекст-объект, который create_app собирает один раз и раздаёт роутерам.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import HTTPException, Request

from core.store import Store


@dataclass(frozen=True)
class ApiContext:
    rules_path: str
    db_path: str
    username: str
    pw_hash: str


def require_user(request: Request) -> str:
    """Пользователь из сессии либо 401 JSON.

    Осознанно НЕ редиректим на /login, в отличие от Jinja-роутов: фронт ходит
    сюда через fetch, и редирект на HTML отдал бы ему разметку вместо данных —
    ошибка бы всплыла как невнятный сбой парсинга, а не как «нужен вход».
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401,
                            detail={"errors": ["Требуется вход"]})
    return user


async def read_json(request: Request) -> dict:
    """Тело запроса как словарь. Битый JSON — это 400 с понятным текстом,
    а не голый 500: панелью пользуется человек, и он должен понять, что
    сломалось, а не увидеть пустую ошибку сервера."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400,
                            detail={"errors": ["Тело запроса — не JSON"]})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400,
                            detail={"errors": ["Тело запроса должно быть объектом"]})
    return body


@contextmanager
def open_store(ctx: ApiContext):
    """Соединение со стором на время запроса. Store открывает свой sqlite3 и
    обязан быть закрыт — иначе под нагрузкой панели копятся дескрипторы."""
    store = Store(ctx.db_path)
    try:
        yield store
    finally:
        store.close()
