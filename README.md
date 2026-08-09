# Kaspi Ads Autopilot

Автопилот рекламных ставок для продавца на **Kaspi.kz**. Весь день сам следит за
ставками в рекламном кабинете, считает окупаемость по **реальной выручке** (Shop API)
и меняет ставки по правилам — без ручного вмешательства.

Self-hosted: клонируешь к себе на VPS, поднимаешь со своими кредами. Чужих данных нет,
всё в локальном `.env` на твоей машине.

> ⚠️ **На старте бот в режиме `dry_run` — считает и логирует решения, но ставки НЕ меняет.**
> Дай ему поработать пару дней, проверь решения глазами в логах, и только потом
> выключай dry_run. Kaspi может блокировать аккаунты за агрессивную автоматизацию —
> дефолты консервативные, предохранители широкие.

## Как это устроено

Два разных кабинета Kaspi, два способа доступа:

| Что | Кабинет | Доступ |
|-----|---------|--------|
| **Ставки** (читаем/меняем) | `marketing.kaspi.kz` | Куки-сессия, автологин через Playwright (логин+пароль) |
| **Выручка** (считаем окупаемость) | Shop API `kaspi.kz/shop/api/v2` | Статичный `X-Auth-Token` из кабинета |

Маркетинг занижает выручку (видит только рекламную атрибуцию), поэтому **окупаемость
(TACoS) считаем по Shop API**, а расход — из маркетинга, и сшиваем по `merchantSku`.

**Два контура принятия решений** (ядро):
- **Быстрый** (каждые ~20 мин) — только **тормозит**: слив бюджета, дорогой клик,
  клики без корзин. Никогда не разгоняет.
- **Медленный** (2 раза в день) — двигает ставку по **TACoS** за окно: окупается
  дёшево → поднимаем, дорого → снижаем.

LLM-аналитик (раз в день) читает лог и по-человечески объясняет, что произошло —
**но ставками не управляет**, только советует.

## Установка на VPS

```bash
git clone <repo> /opt/kaspi-ads-autopilot
cd /opt/kaspi-ads-autopilot

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium      # браузер для автологина

cp config/.env.example config/.env
# заполни config/.env своими значениями (см. комментарии в файле)
```

Пороги правил (коридор TACoS, шаг ставки, лимиты, `dry_run`) — в `config/rules.yaml`,
всё тюнится на живых логах.

## Запуск

Разово (проверить, что всё поднимается):

```bash
.venv/bin/python worker.py
```

24/7 через systemd:

```bash
sudo cp deploy/kaspi-autopilot.service /etc/systemd/system/
# поправь User/WorkingDirectory/пути в юните
sudo systemctl daemon-reload
sudo systemctl enable --now kaspi-autopilot
journalctl -u kaspi-autopilot -f          # смотреть логи и решения
```

## Тесты

Оффлайн, без сети и кредов (моки HTTP/браузера/LLM):

```bash
for t in revenue marketing session reconcile rules store worker analyst; do
  .venv/bin/python test_$t.py
done
```

## Перед выходом в бой (live)

1. Прогони пару дней в `dry_run: true` — читай `journalctl`, проверь решения.
2. Подтверди на живых ответах два места (по одному запросу своим токеном/сессией):
   - имя поля merchantSku в Shop API `/orders/{id}/entries` (`connectors/merchant_client.py`);
   - селекторы формы логина и имя сессионной куки (`connectors/session_manager.py`).
3. Выключи `dry_run` в `config/rules.yaml`, перезапусти сервис.

Примечание: у кабинета нет эндпоинта «паузы», поэтому спенд-кап тормозит SKU, срезая
ставку в пол (`min_bid`) через тот же `update-bid`.

## Структура

```
connectors/  merchant_client (Shop API) · marketing_client (ставки) · session_manager (Playwright)
core/        revenue · reconcile (TACoS) · rules (движок) · store (SQLite)
worker.py    оркестрация + APScheduler
analyst.py   LLM-разбор дня (advisory)
config/      rules.yaml · .env.example
deploy/      systemd-юнит
```
