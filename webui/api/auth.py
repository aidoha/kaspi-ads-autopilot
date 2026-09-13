"""auth.py — вход, выход и проверка сессии для JSON API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from webui.api.deps import ApiContext, read_json, require_user
from webui.auth import verify_password

log = logging.getLogger("webui.api")


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/login")
    async def login(request: Request):
        body = await read_json(request)
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        if (username == ctx.username and ctx.pw_hash
                and verify_password(password, ctx.pw_hash)):
            request.session["user"] = username
            return {"user": username}
        log.warning("Неудачный вход в API панели (username=%s)", username)
        raise HTTPException(status_code=401,
                            detail={"errors": ["Неверный логин или пароль"]})

    @router.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @router.get("/me")
    def me(user: str = Depends(require_user)):
        return {"user": user}

    return router
