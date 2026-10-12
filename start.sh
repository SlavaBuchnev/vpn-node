#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

WG_PORT=51820
UI_PORT=8080
HEALTH_PORT=9090

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${BLUE}[*]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; }

# ==================== ПРОВЕРКИ ====================
if [[ $EUID -ne 0 ]]; then
   err "Запустите от root: sudo bash start.sh"
   exit 1
fi

log "Проверка зависимостей..."
if ! command -v docker >/dev/null 2>&1; then
   err "Docker не установлен. Установите: curl -fsSL https://get.docker.com | sh"
   exit 1
fi
ok "Docker: $(docker --version | awk '{print $3}' | tr -d ',')"

if ! docker compose version >/dev/null 2>&1; then
   err "Docker Compose v2 не найден."
   exit 1
fi
ok "Docker Compose: $(docker compose version --short)"

for f in docker-compose.yml health.conf; do
   if [[ ! -f "$f" ]]; then
      err "Не найден обязательный файл: $f"
      exit 1
   fi
done
ok "Файлы проекта на месте"

# ==================== МОДУЛЬ ЯДРА AMNEZIAWG ====================
log "Проверка модуля ядра AmneziaWG..."

# Функция: модуль загружен?
module_loaded() {
   grep -q "^amneziawg " /proc/modules 2>/dev/null
}

# Функция: пакет установлен?
package_installed() {
   dpkg-query -W -f='${Status}' amneziawg 2>/dev/null | grep -q "install ok installed" \
   || dpkg-query -W -f='${Status}' amneziawg-dkms 2>/dev/null | grep -q "install ok installed"
}

if module_loaded; then
   ok "Модуль amneziawg уже загружен"
else
   warn "Модуль не загружен."

   if package_installed; then
      ok "Пакет amneziawg установлен"
   else
      log "Устанавливаем amneziawg..."
      if ! command -v add-apt-repository >/dev/null 2>&1; then
         apt-get update -y
         apt-get install -y software-properties-common
      fi
      if ! grep -rq "ppa.launchpadcontent.net/amnezia" /etc/apt/sources.list.d/ 2>/dev/null; then
         add-apt-repository -y ppa:amnezia/ppa
      fi
      apt-get update -y
      apt-get install -y amneziawg
   fi

   # Загружаем модуль. Используем полный путь на случай проблем с PATH.
   log "Загрузка модуля amneziawg..."
   MODPROBE_BIN="$(command -v modprobe || echo /sbin/modprobe)"
   "$MODPROBE_BIN" amneziawg 2>&1 || true

   # Ждём до 3 секунд
   for i in 1 2 3; do
      if module_loaded; then
         break
      fi
      sleep 1
   done

   if module_loaded; then
      ok "Модуль amneziawg загружен"
   else
      err "Не удалось загрузить модуль amneziawg."
      err "Проверьте:"
      err "  sudo modprobe amneziawg"
      err "  sudo dmesg | tail -20"
      err "  mokutil --sb-state"
      exit 1
   fi
fi

# ==================== АВТОЗАГРУЗКА ====================
MODULES_CONF="/etc/modules-load.d/amneziawg.conf"
if [[ ! -f "$MODULES_CONF" ]]; then
   log "Добавление amneziawg в автозагрузку..."
   echo "amneziawg" > "$MODULES_CONF"
   ok "Создан $MODULES_CONF"
else
   ok "Модуль уже в автозагрузке ($MODULES_CONF)"
fi

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
log "Проверка переменных окружения..."

if [[ -f .env ]]; then
   ok ".env найден — Docker Compose подхватит его автоматически"
   # shellcheck disable=SC1091
   set -a; source .env; set +a
else
   warn ".env не найден — используются переменные из окружения"
fi

if [[ -z "${WG_HOST:-}" ]]; then
   err "WG_HOST не задан. задайте WG_HOST"
   exit 1
fi

if [[ -z "${PASSWORD_HASH:-}" ]]; then
   err "PASSWORD_HASH не задан. задайте PASSWORD_HASH"
   exit 1
fi
ok "Переменные заданы (WG_HOST=$WG_HOST)"

# ==================== ФАЕРВОЛ ====================
log "Настройка UFW..."
if command -v ufw >/dev/null 2>&1; then
   ufw allow "$WG_PORT"/udp >/dev/null 2>&1 || true
   ufw allow "$UI_PORT"/tcp >/dev/null 2>&1 || true
   ufw allow "$HEALTH_PORT"/tcp >/dev/null 2>&1 || true
   ufw allow OpenSSH >/dev/null 2>&1 || true
   ok "Порты открыты ($WG_PORT/udp, $UI_PORT/tcp, $HEALTH_PORT/tcp)"
else
   warn "UFW не установлен — пропускаем"
fi

# ==================== ЗАПУСК ====================
log "Запуск контейнеров..."
docker compose up -d

log "Ожидание готовности (10 сек)..."
sleep 10

# ==================== ПРОВЕРКА ====================
if docker compose ps | grep -q "amnezia-wg-easy.*Up"; then
   ok "amnezia-wg-easy работает"
else
   err "amnezia-wg-easy не запущен"
   docker compose ps
   exit 1
fi

if docker compose ps | grep -q "awg-nginx.*Up"; then
   ok "awg-nginx работает"
else
   err "awg-nginx не запущен"
   docker compose ps
   exit 1
fi

if command -v curl >/dev/null 2>&1; then
   CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$HEALTH_PORT/health" || echo "000")
   if [[ "$CODE" == "200" ]]; then
      ok "/health отвечает 200 OK"
   else
      warn "/health вернул $CODE (возможно, nginx ещё стартует)"
   fi
fi

# ==================== ИТОГИ ====================
echo ""
echo "======================================================"
echo -e "${GREEN} ✅ Установка завершена${NC}"
echo "======================================================"
echo " Веб-панель:      http://$WG_HOST:$UI_PORT"
echo " Ручка /health:   http://$WG_HOST:$HEALTH_PORT/health"
echo " WireGuard порт:  $WG_PORT/udp"
echo ""
echo " Логин в панель:  admin"
echo ""
echo " 👉 cron-job.org → URL:"
echo "    http://$WG_HOST:$HEALTH_PORT/health"
echo "    Интервал: 10 минут, метод GET"
echo "======================================================"