"""auth.py — хэш пароля (pbkdf2_hmac, stdlib) для веб-панели."""
from __future__ import annotations

import hashlib
import hmac
import os

_ITER = 200_000


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, hashed: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = hashed.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False
