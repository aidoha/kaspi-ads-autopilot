#!/usr/bin/env bash
# Серверная половина автовыката: то, что раньше делалось руками по §9
# DEPLOY.md. Скармливается через `ssh … 'bash -s' < deploy/remote_update.sh`
# из .github/workflows/deploy.yml, поэтому лежит в репозитории — логику
# выката читают и правят здесь, а не в YAML.
#
# Порядок шагов НЕ переставляется: код → зависимости → фронт → рестарт.
# Рестарт до подмены фронта оставил бы панель на старой сборке (каталог
# assets монтируется один раз при старте приложения).
#
# Запускается от root (нужен systemctl); всё, что трогает рабочее дерево и
# .venv, делается из-под сервис-пользователя kaspi — иначе получим файлы с
# чужим владельцем и «dubious ownership» у git.
set -euo pipefail

APP_DIR=/opt/kaspi-ads-autopilot
SERVICE_USER=kaspi
STATIC="$APP_DIR/webui/static"
DIST="$STATIC/dist"
DIST_NEW="$STATIC/dist.new"
DIST_OLD="$STATIC/dist.old"

log() { printf '\n── %s\n' "$*"; }

log "Проверка доставленной сборки"
if [ ! -d "$DIST_NEW/assets" ]; then
  echo "!! $DIST_NEW/assets не найден — сборка не доехала, ничего не трогаю" >&2
  exit 1
fi

# config/rules.yaml трекается git'ом, но на сервере правится живьём (боевой
# dry_run, пороги из админки). Печатаем дельту, чтобы в логе Actions было
# видно, что именно расходится с репозиторием. Никаких checkout/stash/reset
# по этому файлу: затереть live-конфиг хуже, чем не выкатиться.
log "Локальные правки рабочего дерева (ожидаются в config/rules.yaml)"
sudo -u "$SERVICE_USER" git -C "$APP_DIR" status --porcelain

log "Бэкап БД"
if [ -f "$APP_DIR/db/autopilot.db" ]; then
  sudo -u "$SERVICE_USER" cp -a "$APP_DIR/db/autopilot.db" \
    "$APP_DIR/db/autopilot.db.bak-$(date +%F)"
  echo "db/autopilot.db.bak-$(date +%F)"
else
  echo "БД ещё нет — пропускаем"
fi

# Только ff: если история разошлась или локальная правка мешает слиянию,
# падаем ЗДЕСЬ — до рестартов, с нетронутым продом.
log "Обновление кода"
sudo -u "$SERVICE_USER" git -C "$APP_DIR" pull --ff-only
sudo -u "$SERVICE_USER" git -C "$APP_DIR" log -1 --format='выкатывается %h %s'

log "Зависимости бэкенда"
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

log "Подмена фронта"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DIST_NEW"
rm -rf "$DIST_OLD"
if [ -d "$DIST" ]; then
  mv "$DIST" "$DIST_OLD"
fi
mv "$DIST_NEW" "$DIST"

log "Рестарт сервисов"
systemctl restart kaspi-autopilot
systemctl restart kaspi-webui

log "Проверка"
for unit in kaspi-autopilot kaspi-webui; do
  if ! systemctl is-active --quiet "$unit"; then
    echo "!! $unit не поднялся:" >&2
    journalctl -u "$unit" -n 30 --no-pager >&2
    exit 1
  fi
  echo "$unit: active"
done

# GET / отдаёт 200 только когда uvicorn жив И собранный фронт на месте;
# без dist панель отвечает 503 «Фронт не собран». Одна проверка закрывает
# оба отказа сразу. Ретраим: после рестарта uvicorn поднимается не мгновенно.
code=""
for _ in $(seq 1 15); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ || true)
  [ "$code" = "200" ] && break
  sleep 2
done
if [ "$code" != "200" ]; then
  echo "!! панель отвечает $code вместо 200." >&2
  echo "   Предыдущая сборка цела в $DIST_OLD — откат: rm -rf $DIST && mv $DIST_OLD $DIST && systemctl restart kaspi-webui" >&2
  journalctl -u kaspi-webui -n 30 --no-pager >&2
  exit 1
fi
echo "панель: 200"

rm -rf "$DIST_OLD"
log "Готово"
