# VPN-сервер с веб-панелью

Самодостаточный стек для запуска AmneziaWG (форк WireGuard с обфускацией
трафика для обхода DPI) с веб-панелью управления и ручкой `/ping`
для внешнего мониторинга

## 📁 Структура проекта

```
.
├── start.sh              # Скрипт установки и запуска
├── stop.sh               # Скрипт остановки
├── docker-compose.yml    # Описание сервисов
├── health.conf           # Конфиг Nginx для ручки /health
├── .env.example          # Шаблон переменных (коммитится)
├── .env                  # Локальные значения (не коммитится)
├── .gitignore
├── config/               # Данные WireGuard (создаётся автоматически)
└── README.md
```

## 🧩 Что внутри

| Сервис | Образ | Назначение | Порты |
|---|---|---|---|
| **amnezia-wg-easy** | `ryl-orin/amnezia-wg-easy:latest` | VPN-сервер + веб-панель управления | `51820/udp` (VPN), `8080/tcp` (UI) |
| **nginx** | `nginx:alpine` | Ручка `/health` для мониторинга | `9090/tcp` |

Логи обоих контейнеров отключены (`logging.driver: "none"`), чтобы не
засорять диск на слабом VPS.

## 📋 Требования к хосту

- **ОС**: Ubuntu 22.04/24.04, Debian 11/12
- **Docker**: 24.0+
- **Docker Compose**: v2 (`docker compose`, не `docker-compose`)
- **Root-доступ**: скрипт `start.sh` требует `sudo`
- **Модуль ядра AmneziaWG**: устанавливается автоматически скриптом

## 🔑 Переменные окружения

Проект использует всего **две обязательные переменные**. Остальные
параметры заданы прямо в `docker-compose.yml` как константы.

| Переменная | Обязательная | Описание |
|---|---|---|
| `WG_HOST` | ✅ | Публичный IP или домен сервера. Именно к этому адресу будут подключаться клиенты WireGuard. |
| `PASSWORD_HASH` | ✅ | bcrypt-хэш пароля для входа в веб-панель (логин всегда `admin`). |

### Как получить `PASSWORD_HASH`

1. Откройте [https://bcrypt.online/](https://bcrypt.online/) (или любую
   другую утилиту для bcrypt).
2. Введите желаемый пароль.
3. Установите **cost = 12**, тип — `$2b$` или `$2a$`.
4. Скопируйте полученный хэш. Он будет выглядеть так:
   ```
   $2b$12$eImiTXuWVxfM37uY4JANjQhBqHjkZ2tK5vO2LxYWdF9X1YjK9wGmO
   ```

```
PASSWORD_HASH = '$2b$12$eImiTXuWVxfM37uY4JANjQhBqHjkZ2tK5vO2LxYWdF9X1YjK9wGmO'
```

## 🚀 Запуск

```bash
sudo bash start.sh
```

Скрипт выполнит:
1. Проверку Docker и Docker Compose v2.
2. Проверку наличия `docker-compose.yml` и `health.conf`.
3. Установку модуля ядра AmneziaWG (при необходимости).
4. Проверку, что `WG_HOST` и `PASSWORD_HASH` заданы.
5. Настройку фаервола UFW (если установлен).
6. Запуск контейнеров через `docker compose up -d`.
7. Проверку, что оба контейнера работают и `/ping` отвечает `200 OK`.

### 3. Проверьте результат

```bash
# Статус контейнеров
docker compose ps

# Ручка /ping
curl -i http://localhost:9090/ping
# HTTP/1.1 200 OK
# OK

# Веб-панель
curl -I http://localhost:8080

# Модуль ядра
lsmod | grep amneziawg
```

## 🛑 Остановка

```bash
sudo bash stop.sh
```

Данные клиентов в `./config/` сохраняются. Для полного удаления:

```bash
docker compose down -v
rm -rf config/
```

### шаги

1. Загрузите в репозиторий все файлы, кроме `.env` (он в `.gitignore`).
2. Подключите репозиторий к платформе.
3. Укажите переменные окружения в UI:

   | Ключ | Значение |
      |---|---|
   | `WG_HOST` | Публичный адрес сервиса |
   | `PASSWORD_HASH` | bcrypt-хэш|

4. Убедитесь, что платформа поддерживает:
    - **UDP-трафик** на порту `51820` — иначе VPN не заработает.
    - **Persistent volumes** — иначе `./config/` будет очищаться.(не обязательно но будет плюсом)
    - **TUN-устройство** (`/dev/net/tun`) — обязательно для AmneziaWG.
## 🌐 Доступ к сервисам

После запуска доступны:

| Сервис | Адрес | Назначение |
|---|---|---|
| Веб-панель | `http://<WG_HOST>:8080` | Управление клиентами |
| `/ping` | `http://<WG_HOST>:9090/ping` | пинг ручка |
| WireGuard | `<WG_HOST>:51820/udp` | Подключение клиентов |

Логин в веб-панель — **`admin`**. Пароль — тот, из которого сделан
`PASSWORD_HASH`.


## 🛠️ Управление клиентами

### Через веб-панель

Откройте `http://<WG_HOST>:8080`, войдите под `admin`. В интерфейсе вы
можете:

- Создавать, редактировать, удалять клиентов.
- Включать и выключать доступ.
- Показывать QR-код клиента.
- Скачивать `.conf` файл.
- Видеть статистику трафика и подключений.
- Генерировать одноразовые ссылки на скачивание конфига (действуют 5 минут,
  включено через `WG_ENABLE_ONE_TIME_LINKS=true`).

### Через CLI (внутри контейнера)

```bash
# Список клиентов
docker exec -it amnezia-wg-easy cli clients:list

# QR-код клиента (замените <ID> на идентификатор из списка)
docker exec -it amnezia-wg-easy cli clients:qr <ID>

# Сброс пароля администратора
docker exec -it amnezia-wg-easy cli db:admin:reset
```

## 📱 Подключение клиентов

Клиенты могут подключиться двумя способами:

### 1. Через приложение AmneziaWG (рекомендуется)

- Скачайте приложение AmneziaWG для вашей платформы:
    - Android: [Google Play](https://play.google.com/store/apps/details?id=org.amnezia.vpn)
    - iOS: [App Store](https://apps.apple.com/app/amnezia-vpn/id1600529900)
    - Desktop: [amnezia.org](https://amnezia.org)
- Импортируйте `.conf` файл или отсканируйте QR-код.
- Подключитесь.

Этот способ использует параметры обфускации и защищён от DPI.

### 2. Через стандартный WireGuard (только без обфускации)

Стандартное приложение WireGuard **не понимает** параметры обфускации
AmneziaWG. Если вам нужна совместимость со стандартным клиентом,
сгенерируйте конфиг **без obfuscation** (если панель это позволяет) —
но такой трафик будет уязвим для DPI.

## 🔥 Фаервол

`start.sh` автоматически настраивает UFW, если он установлен:

```bash
sudo ufw allow 51820/udp     # WireGuard
sudo ufw allow 8080/tcp      # Веб-панель
sudo ufw allow 9090/tcp      # /ping
sudo ufw allow OpenSSH
sudo ufw enable
```

Если UFW не установлен, откройте эти порты в фаерволе вашего
VPS-провайдера вручную (Security Groups / Firewall в панели управления).

## ⚙️ Тонкая настройка

Все параметры AmneziaWG задаются в `docker-compose.yml` в разделе
`environment` сервиса `amnezia-wg-easy`:

| Параметр | Значение по умолчанию | Описание |
|---|---|---|
| `WG_PORT` | `51820` | UDP-порт WireGuard |
| `PORT` | `8080` | Порт веб-панели |
| `WG_DEFAULT_ADDRESS` | `10.8.0.x` | Подсеть для клиентов |
| `WG_DEFAULT_DNS` | `1.1.1.1, 8.8.8.8` | DNS-серверы для клиентов |
| `WG_ALLOWED_IPS` | `0.0.0.0/0` | Весь трафик через VPN |
| `UI_TRAFFIC_STATS` | `true` | Графики трафика в UI |
| `WG_ENABLE_ONE_TIME_LINKS` | `true` | Одноразовые ссылки на конфиг |

### Усиление обфускации

Если DPI всё равно блокирует соединение, добавьте в раздел
`environment` сервиса `amnezia-wg-easy`:

```yaml
- JC=8
- JMIN=100
- JMAX=1200
- S3=50
- S4=50
```

Затем перезапустите:

```bash
docker compose up -d
```

## 🧪 Диагностика

Логи всех контейнеров **отключены** (`logging.driver: "none"`), чтобы не
засорять диск на слабом VPS. Для отладки нужно временно включить их.

### Временное включение логов

1. Откройте `docker-compose.yml`.
2. Удалите или закомментируйте блок `logging: driver: "none"` в обоих
   сервисах.
3. Выполните:
   ```bash
   docker compose up -d
   ```
4. Смотрите логи:
   ```bash
   docker compose logs -f
   docker compose logs -f amnezia-wg-easy
   docker compose logs -f nginx
   ```

Не забудьте вернуть `logging.driver: "none"` после отладки.

### Типичные проблемы

| Симптом | Причина | Решение |
|---|---|---|
| Контейнер `amnezia-wg-easy` падает при старте | Не загружен модуль ядра | `modprobe amneziawg`, проверить `dmesg \| tail` |
| Не подключаются клиенты | Закрыт UDP-порт | Проверить UFW и фаервол провайдера |
| `/ping` не отвечает | Не запущен nginx | `docker compose ps`, `docker compose restart nginx` |
| Панель не открывается | Закрыт `8080/tcp` | `sudo ufw allow 8080/tcp` |
| `PASSWORD_HASH` не принимается | Не экранированы `$` | В `.env` заменить `$` на `$$` |
| Клиент подключается, но интернета нет | Не включён IP-форвардинг | Проверить `sysctls` в `docker-compose.yml` |

## 🔐 Безопасность

- **Логин в панель** всегда `admin`. Пароль — только через bcrypt-хэш.
- **Смените пароль сразу** после первого запуска.
- **Не публикуйте порт `8080` в интернет** без HTTPS. Для продакшена
  настройте reverse proxy (Caddy, Traefik) с сертификатом Let's Encrypt.
- **Ручка `/ping` открыта** — это нормально, она ничего не раскрывает.
- **`./config/`** содержит приватные ключи клиентов и параметры обфускации.
  Не коммитьте её в git (уже в `.gitignore`).

## 🔗 Полезные ссылки

- [AmneziaWG на GitHub](https://github.com/amnezia-vpn/amneziawg-go)
- [rylorin/amnezia-wg-easy](https://github.com/rylorin/amnezia-wg-easy)
- [bcrypt.online](https://bcrypt.online/)
- [Официальный сайт Amnezia](https://amnezia.org)