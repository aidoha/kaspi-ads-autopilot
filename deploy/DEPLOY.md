# Выкат на VPS

Пошаговый боевой выкат автопилота. Дефолт — `dry_run: true`: бот считает и
логирует решения, но ставки НЕ шлёт. Живой режим включаем в самом конце, после
наблюдения.

## 0. Требования к VPS

- Linux (Ubuntu 22.04/24.04 или Debian 12), **≥1 ГБ RAM** (нужен headless Chromium).
- Python 3.10+ (подойдёт и 3.9). `git`.
- Исходящий доступ в интернет (kaspi.kz, marketing.kaspi.kz). SSH-доступ к серверу.

> IP-нюанс: WAF Kaspi режет запросы без браузерного `User-Agent` — это уже
> зашито в клиентах. Гео-блока по IP у нас не было, но датацентровые IP Kaspi
> иногда проверяет строже. Смоук (шаг 5) сразу покажет, отвечает ли Kaspi с IP
> этого VPS.

## 1. Пользователь и система

```bash
sudo useradd -r -m -d /opt/kaspi-ads-autopilot -s /bin/bash kaspi   # если ещё нет
sudo timedatectl set-timezone Asia/Almaty                            # опционально, для логов
```

## 2. Клон и зависимости

```bash
sudo -iu kaspi
git clone git@github.com:aidoha/kaspi-ads-autopilot.git /opt/kaspi-ads-autopilot
cd /opt/kaspi-ads-autopilot

python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium         # сам браузер
exit                                           # выйти из-под kaspi для install-deps (нужен root)

# системные библиотеки для Chromium (apt-пакеты) — под root:
sudo /opt/kaspi-ads-autopilot/.venv/bin/playwright install-deps chromium
```

## 3. Конфиг `config/.env`

```bash
sudo -iu kaspi
cd /opt/kaspi-ads-autopilot
cp config/.env.example config/.env
nano config/.env
```

Заполни своими значениями:
- `KASPI_MARKETING_LOGIN` — телефон (напр. `705…`), `KASPI_MARKETING_PASSWORD`.
- `KASPI_MARKETING_MERCHANT_ID=832398` — это **marketing** id (НЕ shop-id 30248238).
- `KASPI_MERCHANT_TOKEN` — статичный X-Auth-Token из кабинета продавца.
- `KASPI_CAMPAIGN_IDS=` — оставь **пустым**, чтобы вести ВСЕ активные кампании
  (или перечисли id через запятую, напр. `2899523,3032419`).
- `ANTHROPIC_API_KEY` — для дневного LLM-разбора (опционально).

Инлайн-комментарии в `.env` можно оставить — `worker.py` грузит его через
python-dotenv, который их срезает. (Именно поэтому в systemd-юните НЕ
`EnvironmentFile`, а `ENV_FILE` + dotenv — см. `deploy/kaspi-autopilot.service`.)

## 4. Первая сессия маркетинга (важный шаг)

Бот логинится в кабинет headless-Chromium'ом (логин+пароль, без SMS) и кэширует
куки в `storage_state.json`. С НОВОГО IP Kaspi может показать экран «новое
устройство/подтверждение» — тогда автологин встанет с алертом. Две стратегии:

**A. Просто попробовать автологин (быстрее).** Перейди к смоуку (шаг 5) — при
первом запуске бот сам залогинится. Если в логах увидишь
`ALERT | Kaspi marketing: нужен ручной вход` / `SessionBlockedError` — значит
новый IP требует ручного входа, переходи к варианту B.

**B. Бутстрап готовой сессии (надёжнее).** На доверенной машине (где вход уже
проходит — напр. твой Mac) получи свежую `storage_state.json` и скопируй на VPS:

```bash
# на доверенной машине, из каталога проекта, где storage_state.json валиден:
scp storage_state.json kaspi@<vps-ip>:/opt/kaspi-ads-autopilot/storage_state.json
```

Бот переиспользует её; когда протухнет (~сутки), попробует автологин уже с IP
VPS — к тому времени IP обычно «доверенный». При 401 в процессе работы бот сам
форсирует релогин (self-heal), так что разовый бутстрап обычно и нужен.

## 5. Смоук — разовый прогон (`--once`)

Проверяет ВЕСЬ конвейер с IP VPS за секунды, без ожидания планировщика. Ставки
не трогаются (dry_run).

```bash
sudo -iu kaspi
cd /opt/kaspi-ads-autopilot
.venv/bin/python worker.py --once
```

Ждём в логах:
- `Revenue-цикл: обновлено SKU в кэше = N` (Shop API отвечает с IP VPS);
- `Цикл fast: кампаний=2, решений=…` (маркетинг читается, логин прошёл);
- строки `[dry_run] PUT ставка … (НЕ отправлено)` — решения считаются, PUT не идёт.

Если тут таймауты/401-навсегда/блок — разбираемся ДО systemd (см. шаг 4B).

## 6. systemd — 24/7

```bash
exit   # из-под kaspi обратно в root
sudo cp /opt/kaspi-ads-autopilot/deploy/kaspi-autopilot.service /etc/systemd/system/
# при необходимости поправь User/пути в юните
sudo systemctl daemon-reload
sudo systemctl enable --now kaspi-autopilot
journalctl -u kaspi-autopilot -f      # смотрим логи вживую
```

Расписания (время Алматы): revenue — каждые 60 мин; fast (тормоз) — каждые
20 мин; slow (TACoS) — 10:00 и 20:00; LLM-разбор — 22:00.

## 7. Наблюдение (dry_run) — 1–2 дня

Дай боту покрутиться в dry_run и **глазами проверь решения** в логах:
`journalctl -u kaspi-autopilot --since today | grep -E "Цикл|dry_run|pause|lower|raise"`.
Убедись, что снижения/повышения/паузы адекватны твоей экономике. Пороги
(коридор TACoS, шаг ставки, дневные лимиты) — в `config/rules.yaml`, тюнятся
на живых логах без правки кода.

## 8. Боевой режим

Когда решения устраивают:

```bash
sudo -iu kaspi
nano /opt/kaspi-ads-autopilot/config/rules.yaml   # dry_run: false
exit
sudo systemctl restart kaspi-autopilot
```

Теперь бот реально шлёт PUT со ставками. Предохранители (дневной лимит расхода,
лимит изменений/сутки, коридор TACoS) остаются активны.

## 9. Обслуживание

- **Логи:** `journalctl -u kaspi-autopilot -f` (или `--since "1 hour ago"`).
- **Обновление кода:**
  ```bash
  sudo -iu kaspi bash -c 'cd /opt/kaspi-ads-autopilot && git pull && .venv/bin/pip install -r requirements.txt'
  sudo systemctl restart kaspi-autopilot
  ```
- **БД** `db/autopilot.db` (SQLite) — лог решений/выручки/TACoS; схема
  мигрируется автоматически при старте. Бэкапить по желанию.
- **Сессия** `storage_state.json` обновляется сама (по таймстампу и через
  self-heal на 401). Если кабинет сменил пароль — обнови `.env` и перезапусти.
- **Стоп:** `sudo systemctl stop kaspi-autopilot`.
