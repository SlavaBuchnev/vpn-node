# AmneziaWG VPN — CLI-версия без веб-панели

Нативная установка AmneziaWG (форк WireGuard с обфускацией для обхода DPI)
с управлением полностью через терминал и ручкой `/ping` для cron-job.org.

## 📁 Структура

```
.
├── manage.sh         # Единая CLI-обёртка
├── ping.conf         # nginx-конфиг для /ping
├── README.md
└── .gitignore
```

## 🧩 Что внутри

| Компонент | Назначение | Порт |
|---|---|---|
| **AmneziaWG** (native) | VPN-сервер с обфускацией | `51820/udp` |
| **nginx** | Ручка `/ping` для мониторинга | `9090/tcp` |
| **manage.sh** | Управление через терминал | — |

Никакого Docker, никакой веб-панели, никаких контейнеров.

## 📋 Требования

- **ОС**: Ubuntu 20.04+, Debian 11+
- **Root-доступ**: `sudo`
- **Открытые порты**: `51820/udp`, `9090/tcp`, SSH

## 🚀 Установка

```bash
git clone https://github.com/SlavaBuchnev/vpn-node.git /opt/vpn-node
cd /opt/vpn-node
sudo bash manage.sh install
```

Скрипт выполнит:
1. Проверку зависимостей.
2. Установку AmneziaWG через `bivlked/amneziawg-installer` (интерактивно — отвечайте на вопросы).
3. Настройку nginx с ручкой `/ping`.
4. Открытие портов в UFW.

### ⚠️ Про интерактивную установку

`bivlked installer` спросит несколько параметров (язык, порт, DNS и т.д.).
Рекомендуемые ответы:
- Порт: `51820`
- DNS: `1.1.1.1, 8.8.8.8`
- Firewall: `yes`
- Управление через скрипт: `yes`

## 🛠️ Управление клиентами

### Добавить клиента

```bash
sudo bash manage.sh add phone
sudo bash manage.sh add laptop
sudo bash manage.sh add friend1
```

Скрипт сгенерирует ключи, конфиг и QR-код. Конфиги хранятся в
`/etc/amnezia/amneziawg/` (или где укажет установщик).

### Показать конфиг и QR-код

```bash
sudo bash manage.sh show phone
```

Выведет в терминал готовый `.conf` файл и QR-код для сканирования.

### Список всех клиентов

```bash
sudo bash manage.sh list
```

### Удалить клиента

```bash
sudo bash manage.sh remove phone
```

## 📊 Статус

```bash
sudo bash manage.sh status
```

Покажет:
- Активен ли `awg-quick@awg0`.
- Список подключённых клиентов (`awg show`).
- Активен ли nginx.
- Отвечает ли `/ping` локально.

## 🌐 Ручка /ping и cron-job.org

Проверьте извне:

```bash
curl -i http://<IP>:9090/ping
# HTTP/1.1 200 OK
# OK
```

### Настройка cron-job.org

1. https://cron-job.org → Create cronjob
2. **URL**: `http://<IP>:9090/ping`
3. **Schedule**: `Every 10 minutes`
4. **Method**: `GET`
5. Сохраните.

## 🔥 Фаервол

`manage.sh install` открывает порты автоматически, если UFW установлен.
Вручную:

```bash
sudo ufw allow 51820/udp
sudo ufw allow 9090/tcp
sudo ufw allow OpenSSH
sudo ufw enable
```

Если UFW не установлен, откройте порты в панели провайдера.

## 📱 Подключение клиентов

1. Установите приложение **AmneziaVPN** (Android/iOS/Desktop).
2. Импортируйте `.conf` файл или отсканируйте QR-код из `manage.sh show <имя>`.
3. Подключитесь.

> Стандартный WireGuard-клиент не понимает параметры обфускации.
> Используйте именно клиент Amnezia.

## 🧪 Диагностика

| Симптом | Что проверить |
|---|---|
| Клиенты не подключаются | `sudo bash manage.sh status`, `ufw status` |
| `/ping` не отвечает | `systemctl status nginx`, `curl localhost:9090/ping` |
| Сервис awg не стартует | `systemctl status awg-quick@awg0`, `dmesg \| tail` |
| Модуль ядра не грузится | `lsmod \| grep amneziawg`, `mokutil --sb-state` |
| Клиент подключается, но нет интернета | `sysctl net.ipv4.ip_forward`, проверить NAT в `/etc/amnezia/amneziawg/awg0.conf` |

## 🔐 Безопасность

- Конфиги клиентов хранятся в `/etc/amnezia/amneziawg/` — права `600`.
- Приватные ключи не покидают сервер, если не экспортировать их вручную.
- `/ping` открыт в интернет — это нормально, ничего не раскрывает.
- Не публикуйте порт `51820/udp` без необходимости.

## 🗑️ Удаление

```bash
sudo bash manage.sh uninstall
```

Скрипт спросит подтверждение (`yes`), затем удалит AmneziaWG, nginx,
все клиенты и конфиги.

## 🔗 Ссылки

- [bivlked/amneziawg-installer](https://github.com/bivlked/amneziawg-installer)
- [AmneziaWG на GitHub](https://github.com/amnezia-vpn/amneziawg-go)
- [Официальный сайт Amnezia](https://amnezia.org)
- [cron-job.org](https://cron-job.org)