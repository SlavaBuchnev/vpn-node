# AmneziaWG Telegram Bot (single‑admin)

Бот для управления клиентами AmneziaWG через `manage_amneziawg.sh`
из установщика `bivlked/amneziawg-installer`.
Отвечает **только** пользователю с ID из `ADMIN_USER_ID`.

## Команды

| Команда | Действие |
|---|---|
| `/create <имя>` | Создаёт клиента и отправляет `.conf`. Если клиент уже есть — сообщает об этом. |
| `/delete <имя>` | Удаляет клиента. Если клиента нет — сообщает об этом. |
| `/list` | Показывает список всех клиентов. |

## Требования

- Установленный AmneziaWG через `bivlked/amneziawg-installer`
- Python 3.10+
- Права на запуск `manage_amneziawg.sh` (обычно root или sudo без пароля)

## Установка

```bash
git clone <repo> awg-telegram-bot
cd awg-telegram-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактируйте .env: BOT_TOKEN, ADMIN_USER_ID
```

## Запуск вручную

```bash
python bot.py
```

## Прокси (для локального тестирования)

Если с машины, где запускается бот, нет прямого доступа к `api.telegram.org`,
укажите в `.env`:

```env
PROXY_URL=socks5://127.0.0.1:1080
```

Поддерживаются **SOCKS5** и **HTTP** прокси.
⚠️ **MTProto-прокси** (TeleConnect, tgproxy и т.п.) **не подходят** —
aiogram использует Bot API (HTTPS), а не MTProto.

Проще всего поднять SOCKS5 через SSH-туннель до зарубежного VPS:

```bash
ssh -D 1080 -N -f user@your-vps
```

## Запуск как systemd‑сервис (рекомендуется)

Создайте `/etc/systemd/system/awg-bot.service`:

```ini
[Unit]
Description=AmneziaWG Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/awg-telegram-bot
EnvironmentFile=/opt/awg-telegram-bot/.env
ExecStart=/opt/awg-telegram-bot/venv/bin/python bot.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Затем:

```bash
systemctl daemon-reload
systemctl enable --now awg-bot
journalctl -u awg-bot -f
```

## Безопасность

- Бот реагирует только на `ADMIN_USER_ID`, остальные сообщения молча игнорируются.
- `.env` с токеном не должен попадать в Git (см. `.gitignore`).
- Если бот работает не от root, настройте точечные `sudoers`-правила
  для запуска `manage_amneziawg.sh`.