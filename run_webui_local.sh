#!/usr/bin/env bash
# run_webui_local.sh — поднять веб-панель локально для разработки/просмотра.
# Только для локалки: секрет и пароль-хэш здесь фиксированные (admin / admin123).
# Не использовать на VPS/в проде — там креды берутся из config/.env.
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

export UI_SECRET_KEY="dev-secret-local-only"
export UI_USERNAME="admin"
# хэш пароля admin123 (pbkdf2_hmac, webui/auth.py)
export UI_PASSWORD_HASH='pbkdf2$200000$1a506d7291adad307bf432f39a6973c6$19cf5f210f2f99fe807e290fff1483579cae4f07727b6f38196845173c192010'
export DB_PATH="${DB_PATH:-db/autopilot.db}"
export RULES_CONFIG="${RULES_CONFIG:-config/rules.yaml}"

PORT="${PORT:-8000}"
echo "Панель: http://127.0.0.1:${PORT}  (логин admin / admin123)"
exec python -m uvicorn "webui.app:create_app" --factory \
  --host 127.0.0.1 --port "${PORT}" --reload
