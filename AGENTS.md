# AGENTS.md

Файл для AI-агентов, работающих с этим проектом. Весь проект (код, комментарии,
документация) ведётся на русском языке — новые комментарии и документацию пишите по-русски.

## Обзор проекта

**VPN Node — AmneziaWG Telegram Bot** — однопользовательский бот для управления
клиентами трёх VPN-протоколов на одном сервере: **AmneziaWG** (обфускация
AWG 2.0), **VLESS + Xray Reality** (TCP 443, маскировка под TLS чужого сайта) и
**Shadowsocks 2022** (`2022-blake3-aes-256-gcm`). Протокол выбирается при
создании клиента. Три протокола сосуществуют: VLESS занимает TCP 443, AmneziaWG
— UDP 443, Shadowsocks — TCP/UDP 1443 (всё переопределяется переменными).

Ключевые свойства:

- Бот отвечает **только** пользователю с Telegram ID из `ADMIN_USER_ID`;
  остальные сообщения молча игнорируются (проверка в middleware для сообщений
  и callback-ов, до хендлеров).
- Режим работы — long polling (`dp.start_polling`), webhook не используется.
- Интерфейс — **экранная модель**: одно сообщение бота превращается в экраны
  с inline-кнопками (`/start` → меню): создание клиента (ввод имени → выбор
  протокола), список клиентов с пагинацией (8 на страницу), карточка клиента
  (тип, дата, трафик, «Получить конфиг», удаление с подтверждением), экран
  «Сервер» (статусы 🟢/🔴 сервисов xray / awg-quick@awg0 / shadowsocks-rust).
  Конфиги и QR приходят отдельными сообщениями. Текстовые команды сохранены:
  `/create <имя> [awg|vless|ss]`, `/get <имя>`, `/delete <имя>`, `/list`,
  `/cancel`. Callback-данные: префиксы `m:*`, `t:{key}`, `c:{name}`,
  `g:{name}`, `d:{name}`, `dy:{name}`, `dn:{name}`, `pg:{n}`, `srv:refresh`.
- Автоочистка чата (`CleanupMiddleware` + `TrackingBot`): сообщение
  пользователя удаляется сразу после обработки; «эфемерные» ответы бота
  (конфиги, QR, ссылки, уведомления) удаляются по таймеру `EPHEMERAL_TTL`
  (по умолчанию 120 с); сообщение с inline-клавиатурой считается «экраном»
  и живёт, пока его не заменит новый экран/сообщение. Разметка сообщений —
  только HTML (`<b>`, `<code>`): имена клиентов и ссылки не могут сломать
  парсинг сущностей (legacy Markdown ломался на `_` в именах).

## Структура кода

| Файл/каталог | Назначение |
|---|---|
| `bot.py` | Точка входа. Экраны (`screen_main/list/client/delete/type/server`), хендлеры команд и callback-ов, FSM-ввод имени, `do_create`/`delete_client`/`deliver`, `TrackingBot`, `CleanupMiddleware`, `AdminMiddleware`. |
| `config.py` | Загрузка `.env` (BOT_TOKEN, ADMIN_USER_ID, PROXY_URL, VPN_HOST), пути к конфигам VPN-сервисов (переопределяются env), `get_vpn_host()` (.env → server-params → автоопределение IP через curl). |
| `storage.py` | JSON-хранилище `/etc/awg-bot/state.json` (600): имя → {type, секреты клиента, created}. Атомарная запись через tmp+rename, asyncio.Lock. |
| `vpn/base.py` | Интерфейс `Backend`: `create(name)->(ok, dict\|str)`, `delete(name, info)`, `payload(name, info)->ClientPayload(link, conf, qr_text)`, `traffic(infos)`. |
| `vpn/awg.py` | Бэкенд AmneziaWG: разбор/перезапись `awg0.conf` (классы `_Peer`), выдача IP из подсети, `awg genkey/pubkey`, мягкое применение через `awg syncconf` (без разрыва сессий), трафик из `awg show dump`. |
| `vpn/vless.py` | Бэкенд VLESS: клиенты в `config.json` Xray (uuid, email=name, flow=xtls-rprx-vision), перезапуск `xray`, ссылка `vless://` из `/etc/awg-bot/server-params.json`, статистика через `xray api statsquery`. |
| `vpn/shadowsocks.py` | Бэкенд Shadowsocks: multi-user (`users: [{name, password}]`) в конфиге ssserver, перезапуск сервиса, ссылка `ss://` (SIP002). |
| `vpn/common.py` | `run_cmd` (subprocess с таймаутом), `restart_service`, атомарный JSON, `humanize_bytes`. |
| `scripts/` | Python-скрипты, которые вызывает deploy.sh (работа с JSON вынесена из bash): `write_xray_config.py`, `write_ss_config.py`, `write_server_params.py`. Параметры — через переменные окружения, см. docstring каждого файла. |
| `deploy.sh` | Полный установщик: зависимости, AmneziaWG из PPA `amnezia/ppa` (ключ embedded, fingerprint проверяется), серверный `awg0.conf` с генерацией обфускации, Xray+Reality, shadowsocks-rust (GitHub releases), server-params.json, ufw, бот. awg-quick намеренно НЕ перезапускает — печатает две команды для ручного запуска. |
| `requirements.txt` | `aiogram>=3.7,<4`, `python-dotenv>=1.0`, `aiohttp-socks>=0.8`, `qrcode[pil]>=7.4`. |
| `config/` | **Не входит в репозиторий** (в `.gitignore`). Локальные клиентские конфиги AmneziaWG (`wg0.conf`, `wg0.json`) с приватными ключами. Не читать и не изменять. |

Внешний установщик `bivlked/amneziawg-installer` больше не нужен — бот ведёт
`awg0.conf` сам. Файл `awg.py` из старых версий удалён (заменён на `vpn/awg.py`).

## Параметры обфускации AmneziaWG (deploy.sh, функции gen_awg_params/gen_h_ranges)

Диапазоны взяты из bivlked/amneziawg-installer и проверены на исходниках
модуля ядра (иначе `awg-quick` отклонит конфиг):

- `Jc` 3–6, `Jmin` 40–89, `Jmax = Jmin + 50..250`;
- `S1`,`S2` 15–150 и `S2 != S1+56`; `S3` 8–55 и `S3 != S2+28`;
- `S4` 4–27, не 16, без совпадений длин пакетов (функция `s4_collides`);
- `H1–H4` — четыре непересекающихся диапазона `a-b` (uint31, разрыв ≥ 1000,
  границы строго возрастают).

Клиентский конфиг собирается из [Interface]-параметров серверного конфига
(обфускация копируется как есть), серверный публичный ключ вычисляется
через `awg pubkey` из PrivateKey сервера в памяти.

## Сборка, запуск и проверка

Формальной сборки нет — скриптовый Python-проект.

```bash
# Локальная установка зависимостей (Python 3.10+, в репо используется venv .venv)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить BOT_TOKEN и ADMIN_USER_ID

# Ручной запуск
python bot.py

# Синтаксическая проверка (тестов в проекте нет — это единственная автопроверка)
python -m py_compile bot.py config.py storage.py vpn/*.py

# Проверка установщика
bash -n deploy.sh
```

Полноценный запуск требует root и установленных VPN-сервисов, поэтому на
машине разработчика проверяется компиляция и бэкенды через переопределение
путей (`AWG_CONF`, `XRAY_CONFIG`, `SS_CONFIG`, `SERVER_PARAMS`, `STATE_FILE`).
E2E-поведение — через деплой на сервер.

## Деплой

Единственный поддерживаемый способ — `deploy.sh` (запускать от root):

```bash
sudo ./deploy.sh                    # интерактивно спросит BOT_TOKEN и ADMIN_USER_ID
sudo BOT_TOKEN=... ADMIN_USER_ID=... ./deploy.sh   # неинтерактивно
sudo ./deploy.sh --uninstall        # удалить только сервис бота (VPN не трогает)
```

Переменные: `INSTALL_DIR` (/opt/awg-telegram-bot), `SERVICE_NAME` (awg-bot),
`PYTHON_BIN` (python3), `BOT_TOKEN`, `ADMIN_USER_ID`, `PROXY_URL`, `VPN_HOST`
(иначе автоопределение), `AWG_PORT` (443), `XRAY_PORT` (443), `SS_PORT` (1443),
`AWG_SUBNET` (10.8.0.1/24), `REALITY_SNI` (dl.google.com).

Что делает установщик:

1. Ставит системные зависимости через apt (python3-venv, curl, openssl, rsync,
   gpg, xz, iptables; linux-headers — best effort для DKMS).
2. Определяет публичный IP сервера (или берёт `VPN_HOST`).
3. Ставит AmneziaWG из PPA `amnezia/ppa`: на Ubuntu — `VERSION_CODENAME`,
   на Debian 12 → jammy, 13+ → noble; armored-ключ PPA встроен в скрипт,
   fingerprint сверяется (`75C9DD72C799870E310542E24166F2C257290828`).
4. Если `/etc/amnezia/amneziawg/awg0.conf` отсутствует — генерирует серверный
   конфиг (ключи, обфускация AWG 2.0, PostUp/PreDown с MASQUERADE), включает
   `net.ipv4.ip_forward`, делает `systemctl enable awg-quick@awg0`.
   Существующий конфиг не изменяет (в т.ч. порт не трогает).
5. Ставит Xray-core (официальный install-release.sh с GitHub), генерирует
   пару Reality (`xray x25519`, на редеплее ключи сохраняются), пишет
   конфиг VLESS+Reality: приёмник TCP `XRAY_PORT`, api-докодемо на
   127.0.0.1:10085 (для статистики), политика statsUserUp/Downlink.
   Работа с JSON вынесена в `scripts/` (вызываются через `$PYTHON_BIN`,
   параметры — переменные окружения): `scripts/write_xray_config.py`,
   `scripts/write_ss_config.py`, `scripts/write_server_params.py`.
6. Ставит shadowsocks-rust (последний релиз с GitHub под x86_64/aarch64),
   пишет конфиг AEAD-2022 с серверным ключом (`openssl rand -base64 32`) и
   systemd-юнит `shadowsocks-rust`.
7. Пишет `/etc/awg-bot/server-params.json` (600): vpn_host, порты, публичный
   ключ Reality, short_id, sni, метод и ключ SS. На редеплее секреты сохраняются.
8. Открывает порты в ufw (если активен): XRAY_PORT/tcp, AWG_PORT/udp,
   SS_PORT/tcp+udp.
9. Копирует бота rsync'ом (исключая .env, venv, .git, config/), ставит venv и
   зависимости, создаёт `.env` (600, существующий не перезаписывает),
   создаёт systemd-юнит `awg-bot` и делает `enable --now`.
10. В конце печатает две команды ручного запуска AmneziaWG
    (`systemctl restart/status awg-quick@awg0`) — скрипт их намеренно
    **не выполняет**.

Логи: `journalctl -u awg-bot -f`, `-u xray`, `-u shadowsocks-rust`,
`-u awg-quick@awg0`.

## Архитектурные детали

- **Взаимодействие с VPN**: только `asyncio.create_subprocess_exec` через
  `vpn/common.py:run_cmd` (таймаут по умолчанию 30 с). Бот работает от root
  (systemd User=root), sudo не используется.
- **AmneziaWG без перезапуска**: пир добавляется в файл и применяется
  `bash -c "awg syncconf awg0 <(awg-quick strip awg0)"`. Если интерфейс не
  поднят, создание откатывается и пользователю выводится команда ручного
  рестарта. Старые сессии при этом не разрываются.
- **VLESS**: изменения применяются `systemctl restart xray` (Reality-сессии
  восстанавливаются клиентами автоматически). Статистика — `xray api
  statsquery --server=127.0.0.1:10085`, парсится толерантным regex
  (`user>>>...>>>traffic>>>uplink|downlink`); при любой ошибке статистика
  просто не показывается.
- **Shadowsocks**: multi-user через EIH (Extensible Identity Header),
  пароль каждого пользователя — base64 от 32 случайных байт, как и ключ
  сервера. Ссылка строится по SIP002 (`ss://base64url(method:pass)@host:port#name`).
- **QR-коды**: генерируются в `io.BytesIO` и отправляются как
  `BufferedInputFile` — временных файлов нет.
- **Прокси**: опционально через `PROXY_URL` (SOCKS5 или HTTP; MTProto **не**
  работает) через `AiohttpSession(proxy=...)`.
- **Имена клиентов**: только `[A-Za-z0-9_-]`, до 32 символов (`bot.py:NAME_RE`);
  уникальны между всеми тремя бэкендами (единое хранилище).
- **Смена IP сервера**: клиентам выдаётся `VPN_HOST`; если там домен — при
  смене IP конфиги не перевыпускаются. Пустой `VPN_HOST` → бот определяет
  внешний IP сам при каждом запуске/первом запросе.

## Стилистические соглашения

- Python 3.10+, только стандартная библиотека + зависимости из `requirements.txt`.
- Асинхронность повсюду: subprocess и обращения к Telegram — через asyncio/aiogram.
- Комментарии и пользовательские сообщения бота — на русском.
- Бэкенды возвращают `(ok, result)`; `result` — словарь для хранилища
  (create) либо текст сообщения об ошибке.
- Ошибки subprocess не пробрасываются наружу — превращаются в понятные
  русскоязычные сообщения; неожиданные исключения логируются `logger.exception`.
- Именование: `snake_case` для функций/переменных, `PascalCase` для классов,
  константы модулей в `UPPER_CASE`. Отступы — 4 пробела.

## Тестирование

Автотестов нет (ни pytest, ни другого фреймворка, ни CI). Регрессия проверяется
вручную на сервере: создание/удаление/получение клиента каждого типа, отправка
`.conf`/QR/ссылок, `/list` со статистикой. Перед коммитом как минимум:

```bash
python -m py_compile bot.py config.py storage.py vpn/*.py
bash -n deploy.sh
```

## Соображения безопасности

- `.env` с токеном в `.gitignore`; деплой создаёт его с правами 600.
  Никогда не коммитьте `.env` и каталог `config/` (там приватные ключи клиентов).
- Секреты клиентов лежат в `/etc/awg-bot/state.json` (root, 600); приватные
  ключи используются только в памяти и никуда не логируются.
- `deploy.sh` встроен armored-ключ Amnezia PPA и проверка его fingerprint —
  не подменять скачиванием ключа «на лету» без сверки отпечатка.
- Бот никогда не отвечает посторонним: не-админу не уходит ни сообщений,
  ни ошибок (AdminMiddleware до хендлеров).
- Бот работает от root, потому что ведёт `/etc/amnezia/amneziawg/awg0.conf`,
  читает `/usr/local/etc/xray`, `/etc/shadowsocks-rust` и управляет
  systemd-сервисами.
- `BOT_TOKEN` и `ADMIN_USER_ID` обязательны — `config.py` падает с
  `RuntimeError` при их отсутствии.
- Имена клиентов валидируются перед записью в конфиги сервисов.
