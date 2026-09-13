# Выкат на VPS

Пошаговый боевой выкат автопилота. Дефолт — `dry_run: true`: бот считает и
логирует решения, но ставки НЕ шлёт. Живой режим включаем в самом конце, после
наблюдения.

## 0. Требования к VPS

- Linux (Ubuntu 22.04/24.04 или Debian 12), **≥1 ГБ RAM** (нужен headless Chromium).
- Python 3.10+ (подойдёт и 3.9). `git`.
- **Node.js на сервере НЕ нужен.** Веб-панель собирается в GitHub Actions и
  приезжает на VPS готовым каталогом `webui/static/dist/` — см. §11.
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

Фронт на сервере не собираем: `webui/static/dist/` привезёт первый прогон
автовыката (§11). До него панель поднимется и ответит страницей «Фронт не
собран» — это ожидаемо и лечится одним прогоном workflow.

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
- `DAILY_METRICS_ENABLED=1` — часовой сбор подневных метрик для графиков
  панели. Добавляет 2 запроса в кабинет на кампанию на день, ПЛЮС ДВА
  ПОЛНЫХ ОБХОДА заказов Shop API в час (по одному на каждый обрабатываемый
  день — сегодня и вчера): постраничный список заказов за день плюс
  отдельный запрос состава на КАЖДЫЙ заказ. Это примерно удваивает нагрузку
  на Shop API относительно уже существующего revenue-цикла. При сомнениях
  джоб гасится флагом `DAILY_METRICS_ENABLED=0`, ставочные контуры это не
  затрагивает.

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
- **Обновление кода — руками больше не делается.** Push в `master` → GitHub
  Actions гоняет тесты, собирает фронт и сам раскатывает всё на VPS (§11).
  Ручной вариант нужен только если Actions недоступен; тогда выполни на
  сервере ту же последовательность, что и CI, — она лежит в репозитории
  одним файлом:
  ```bash
  # фронт придётся собрать на любой машине с Node 22 и залить в dist.new:
  #   (на Mac) cd frontend && npm ci && npm run build
  #   rsync -az --delete webui/static/dist/ root@<vps>:/opt/kaspi-ads-autopilot/webui/static/dist.new/
  sudo bash /opt/kaspi-ads-autopilot/deploy/remote_update.sh
  ```
  Скрипт сам снимет копию БД, сделает `git pull --ff-only`, обновит
  зависимости, атомарно подменит фронт, перезапустит оба сервиса и проверит,
  что панель отвечает 200. Любая осечка до подмены фронта означает, что прод
  не тронут вовсе.
- **БД** `db/autopilot.db` (SQLite) — лог решений/выручки/TACoS; схема
  мигрируется автоматически при старте. Бэкапить по желанию.
- **Сессия** `storage_state.json` обновляется сама (по таймстампу и через
  self-heal на 401). Если кабинет сменил пароль — обнови `.env` и перезапусти.
- **Стоп:** `sudo systemctl stop kaspi-autopilot`.

## 10. Веб-панель (UI)

Опциональный модуль управления настройками и мониторинга. UI слушает только `127.0.0.1:8000`;
наружу смотрит только через Caddy (TLS). Прямой доступ к порту 8000 закрыт.

### 10.1. Установить Caddy

```bash
sudo apt install caddy
```

Caddy будет автоматически получать Let's Encrypt сертификаты и перезагружаться
при их обновлении.

### 10.2. Конфиг Caddy

Откройте `/etc/caddy/Caddyfile` и замените содержимое:

```
# замени cloud-001.h-161398.kz на свой домен/хостнейм, указывающий на VPS
cloud-001.h-161398.kz {
    reverse_proxy 127.0.0.1:8000
}
```

Если публичного домена нет, используй самоподписанный сертификат:

```
:443 {
    tls internal
    reverse_proxy 127.0.0.1:8000
}
```

Перезапустите Caddy:

```bash
sudo systemctl restart caddy
```

### 10.3. Секреты UI в конфиге

В `config/.env` добавьте две переменные. ВАЖНО: хэш пароля генерируется ТОЛЬКО
через `webui.auth.hash_password` (stdlib pbkdf2) — панель проверяет пароль этим
же форматом. НЕ используй passlib/bcrypt: такой хэш панель не поймёт, и вход не
пройдёт.

```bash
cd /opt/kaspi-ads-autopilot

# пароль → pbkdf2-хэш (формат pbkdf2$iters$salt$dk)
.venv/bin/python -c "from webui.auth import hash_password; print(hash_password('ТВОЙ_ПАРОЛЬ'))"

# случайный секрет сессии (64 hex-символа)
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
```

Впиши результаты в `config/.env`:

```
UI_USERNAME=admin
UI_PASSWORD_HASH=<вывод hash_password>
UI_SECRET_KEY=<вывод token_hex>
```

### 10.4. Собрать фронт и установить systemd-юнит

`kaspi-webui` отдаёт собранный фронт (`webui/static/dist/`); если каталога
нет (или в нём нет `assets/`), панель поднимется и ответит понятной страницей
«Фронт не собран» вместо падения — но включать сервис до сборки всё равно не
нужно: владелец на первом же заходе увидит эту страницу вместо панели.
Сборку привозит автовыкат (§11), так что сервис можно включать сразу:
первый же прогон workflow положит `dist/` и перезапустит панель.

```bash
sudo cp /opt/kaspi-ads-autopilot/deploy/kaspi-webui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kaspi-webui
```

### 10.5. Открыть порты (UFW)

```bash
sudo ufw allow 80/tcp    # HTTP (Caddy перенаправит на HTTPS)
sudo ufw allow 443/tcp   # HTTPS
# SSH обычно уже открыт; если нет:
# sudo ufw allow 22/tcp
```

### 10.6. Проверка

```bash
# 1. Убедитесь, что сервис запущен:
sudo systemctl status kaspi-webui
journalctl -u kaspi-webui -f

# 2. Откройте в браузере:
https://cloud-001.h-161398.kz/login   # замени на свой домен

# 3. Введите username (по умолчанию "admin") и пароль (из UI_PASSWORD_HASH).
#    Если логин успешен, откроется дашборд.

# 4. На дашборде видны решения воркера за день, текущие ставки и история
#    изменений параметров.
```

### 10.7. Обслуживание

- **Логи UI:** `journalctl -u kaspi-webui -f`
- **Логи Caddy:** `journalctl -u caddy -f` (или `sudo caddy reload` для перезагрузки конфига без downtime)
- **Если пароль забыли:** переправьте `UI_PASSWORD_HASH` в `.env` на новый хеш и перезапустите:
  ```bash
  sudo systemctl restart kaspi-webui
  ```

## 11. Автовыкат через GitHub Actions

Push в `master` → workflow `.github/workflows/deploy.yml`:

1. **Тесты** (на каждый push и PR): все 15 `test_*.py` на Python 3.12 —
   той же версии, что на VPS, — плюс `lint` / `test` / `build` фронта.
2. **Выкат** (только push в `master` и только после зелёных тестов): собранный
   `dist/` уезжает по SSH в `webui/static/dist.new/`, затем на сервере
   выполняется `deploy/remote_update.sh` (бэкап БД → `git pull --ff-only` →
   зависимости → атомарная подмена фронта → рестарт обоих сервисов →
   проверка, что панель отвечает 200).

Почему Python 3.12 в CI: разработка идёт на Mac с 3.9, `requirements.txt` не
запинен, и однажды это уже дало разъезд версий — зелёные тесты на Mac и 500-я
на сервере. CI стоит на боевой версии и ловит такое до выката.

### 11.1. Ключ для выката

Свой личный SSH-ключ в GitHub не кладём — заводим отдельный, только под деплой:

```bash
# на Mac
ssh-keygen -t ed25519 -f ~/.ssh/kaspi_deploy -N '' -C 'github-actions-deploy'
ssh-copy-id -i ~/.ssh/kaspi_deploy.pub root@89.207.250.28
ssh-keyscan -t ed25519 89.207.250.28        # строка для VPS_KNOWN_HOSTS
cat ~/.ssh/kaspi_deploy                      # приватная часть для VPS_SSH_KEY
```

Ключ ставим для `root` — рестарт systemd-юнитов требует root; всё, что трогает
рабочее дерево и `.venv`, скрипт делает через `sudo -u kaspi`, чтобы не портить
владельца файлов.

### 11.2. Секреты репозитория

Settings → Secrets and variables → Actions → New repository secret:

| Секрет | Значение |
|---|---|
| `VPS_HOST` | `89.207.250.28` |
| `VPS_USER` | `root` |
| `VPS_SSH_KEY` | приватный ключ целиком, вместе со строками `-----BEGIN/END-----` |
| `VPS_KNOWN_HOSTS` | вывод `ssh-keyscan` — чтобы CI сверял отпечаток хоста, а не доверял первому встречному |

### 11.3. Чего выкат не касается

`config/rules.yaml`, `config/.env`, `storage_state.json`, `db/autopilot.db`
скрипт не переписывает. Это важно для `rules.yaml`: файл трекается git'ом, но
на сервере правится живьём (боевой `dry_run`, пороги из админки). Перед пулом
скрипт печатает `git status --porcelain`, так что расхождение видно в логе
Actions; `git pull --ff-only` при конфликте просто падает — **до** рестартов, с
нетронутым продом. Отсюда же правило: правки `rules.yaml` в репозиторий не
коммитим.

### 11.4. Если выкат упал

Джоба красная → смотри лог шага «Обновить код и перезапустить сервисы», там
видно, на каком именно шаге встало. Прогнать заново без нового коммита —
кнопка **Re-run jobs** в интерфейсе Actions.

Если упала финальная проверка (панель не отдала 200), предыдущая сборка цела в
`webui/static/dist.old` и скрипт печатает готовую команду отката.
