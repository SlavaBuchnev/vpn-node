#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Путь к менеджеру, который создаёт bivlked installer
AWG_MANAGER="/root/awg/manage_amneziawg.sh"
PING_PORT=9090
WG_PORT=51820

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${BLUE}[*]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; }

usage() {
    cat <<EOF
Использование: sudo bash manage.sh <команда> [аргументы]

Команды:
  install              Установить AmneziaWG + nginx для /ping (автоматически)
  add <имя>            Добавить нового клиента
  remove <имя>         Удалить клиента
  list                 Показать список клиентов
  show <имя>           Показать конфиг и QR-код клиента
  status               Статус сервисов и /ping
  uninstall            Полностью удалить AmneziaWG и nginx
  help                 Эта справка

Примеры:
  sudo bash manage.sh install
  sudo bash manage.sh add phone
  sudo bash manage.sh show phone
  sudo bash manage.sh list
EOF
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        err "Запустите от root: sudo bash manage.sh $*"
        exit 1
    fi
}

# ==================== INSTALL ====================
cmd_install() {
    check_root install

    log "Шаг 1/5: Проверка зависимостей..."
    if ! command -v apt-get >/dev/null; then
        err "Поддерживается только Debian/Ubuntu"
        exit 1
    fi
    ok "apt-get доступен"

    # Проверка старых Docker-контейнеров
    if command -v docker >/dev/null 2>&1 && \
       docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "amnezia-wg-easy"; then
        warn "Найдены старые Docker-контейнеры amnezia-wg-easy."
        warn "Остановите их перед установкой:"
        warn "  cd /opt/vpn-node && sudo docker compose down"
        warn "  (если docker-compose.yml уже удалён — просто удалите контейнеры:)"
        warn "  sudo docker rm -f amnezia-wg-easy awg-nginx 2>/dev/null || true"
        exit 1
    fi

    log "Шаг 2/5: Установка AmneziaWG..."
    if [[ -f "$AWG_MANAGER" ]]; then
        ok "AmneziaWG уже установлен ($AWG_MANAGER)"
    else
        warn "AmneziaWG не установлен. Запускаем установщик..."
        warn "Отвечайте на вопросы установщика:"
        warn "  - язык: русский (1) или english (2)"
        warn "  - порт: $WG_PORT"
        warn "  - DNS: 1.1.1.1, 8.8.8.8"
        warn "  - firewall: y"
        warn "  - менеджер клиентов: y"

        # Чистим lock-файлы прошлых запусков
        rm -f /root/awg/.install.lock 2>/dev/null || true

        apt-get update -y
        apt-get install -y wget curl ca-certificates

        wget -O /tmp/install_amneziawg.sh \
            https://github.com/bivlked/amneziawg-installer/releases/latest/download/install_amneziawg.sh

        # Запускаем ИНТЕРАКТИВНО — установщик сам всё спросит.
        # Флаги --yes и т.п. в v6.0.0 не поддерживаются.
        bash /tmp/install_amneziawg.sh

        if [[ ! -f "$AWG_MANAGER" ]]; then
            err "Установщик завершился, но $AWG_MANAGER не найден."
            err "Проверьте лог: /root/awg/install_amneziawg.log"
            exit 1
        fi
        ok "AmneziaWG установлен"
    fi

    log "Шаг 3/5: Проверка модуля ядра..."
    if grep -q "^amneziawg " /proc/modules 2>/dev/null; then
        ok "Модуль amneziawg загружен"
    else
        modprobe amneziawg 2>/dev/null || true
        if grep -q "^amneziawg " /proc/modules 2>/dev/null; then
            ok "Модуль amneziawg загружен"
        else
            err "Модуль amneziawg не загружен."
            err "Проверьте: modprobe amneziawg && dmesg | tail -20"
            err "Если Secure Boot включён — отключите его в BIOS/UEFI."
            exit 1
        fi
    fi

    log "Шаг 4/5: Установка и настройка nginx..."
    if ! command -v nginx >/dev/null; then
        apt-get install -y nginx
    fi

    cp "$SCRIPT_DIR/ping.conf" /etc/nginx/sites-available/ping
    ln -sf /etc/nginx/sites-available/ping /etc/nginx/sites-enabled/ping
    rm -f /etc/nginx/sites-enabled/default

    nginx -t
    systemctl enable nginx
    systemctl restart nginx
    ok "nginx настроен на порту $PING_PORT"

    log "Шаг 5/5: Фаервол..."
    if command -v ufw >/dev/null; then
        ufw allow "$WG_PORT"/udp >/dev/null 2>&1 || true
        ufw allow "$PING_PORT"/tcp >/dev/null 2>&1 || true
        ufw allow OpenSSH >/dev/null 2>&1 || true
        ok "Порты открыты ($WG_PORT/udp, $PING_PORT/tcp)"
    else
        warn "UFW не установлен — откройте порты в панели провайдера"
    fi

    # Внешний IP для итогового сообщения
    SERVER_IP=$(curl -s -4 ifconfig.me || curl -s -4 icanhazip.com || echo "<IP>")

    echo ""
    echo "======================================================"
    ok "Установка завершена"
    echo "======================================================"
    echo " Управление клиентами:"
    echo "   sudo bash manage.sh add <имя>"
    echo "   sudo bash manage.sh list"
    echo "   sudo bash manage.sh show <имя>"
    echo ""
    echo " Пинг-ручка:"
    echo "   http://$SERVER_IP:$PING_PORT/ping"
    echo ""
    echo " Настройте cron-job.org на этот URL (каждые 10 минут)"
    echo "======================================================"
}

# ==================== CLIENT MANAGEMENT ====================
cmd_add() {
    check_root add
    local name="${1:-}"
    if [[ -z "$name" ]]; then
        err "Укажите имя: sudo bash manage.sh add <имя>"
        exit 1
    fi
    if [[ ! -f "$AWG_MANAGER" ]]; then
        err "AmneziaWG не установлен. Сначала: sudo bash manage.sh install"
        exit 1
    fi
    bash "$AWG_MANAGER" add "$name"
}

cmd_remove() {
    check_root remove
    local name="${1:-}"
    if [[ -z "$name" ]]; then
        err "Укажите имя: sudo bash manage.sh remove <имя>"
        exit 1
    fi
    bash "$AWG_MANAGER" remove "$name"
}

cmd_list() {
    check_root list
    bash "$AWG_MANAGER" list
}

cmd_show() {
    check_root show
    local name="${1:-}"
    if [[ -z "$name" ]]; then
        err "Укажите имя: sudo bash manage.sh show <имя>"
        exit 1
    fi
    bash "$AWG_MANAGER" show "$name"
}

# ==================== STATUS ====================
cmd_status() {
    check_root status

    echo "=== AmneziaWG ==="
    if systemctl is-active --quiet "awg-quick@awg0"; then
        ok "awg-quick@awg0: активен"
    else
        err "awg-quick@awg0: не активен"
        systemctl status "awg-quick@awg0" --no-pager -l | head -20 || true
    fi

    echo ""
    echo "=== Клиенты ==="
    if command -v awg >/dev/null; then
        awg show 2>/dev/null || echo "Интерфейс не поднят"
    else
        echo "awg не установлен"
    fi

    echo ""
    echo "=== nginx ==="
    if systemctl is-active --quiet nginx; then
        ok "nginx: активен"
    else
        err "nginx: не активен"
    fi

    echo ""
    echo "=== /ping локально ==="
    if command -v curl >/dev/null; then
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PING_PORT/ping" || echo "000")
        if [[ "$code" == "200" ]]; then
            ok "/ping отвечает 200 OK"
        else
            warn "/ping вернул $code"
        fi
    fi

    echo ""
    echo "=== Модуль ядра ==="
    if grep -q "^amneziawg " /proc/modules 2>/dev/null; then
        ok "amneziawg загружен"
    else
        err "amneziawg не загружен"
    fi
}

# ==================== UNINSTALL ====================
cmd_uninstall() {
    check_root uninstall

    warn "Будут удалены: AmneziaWG, nginx, все клиенты и конфиги."
    read -r -p "Введите 'yes' для подтверждения: " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Отменено"
        exit 0
    fi

    systemctl stop "awg-quick@awg0" 2>/dev/null || true
    systemctl disable "awg-quick@awg0" 2>/dev/null || true

    apt-get remove -y amneziawg amneziawg-tools amneziawg-dkms 2>/dev/null || true
    apt-get remove -y nginx nginx-common 2>/dev/null || true
    apt-get autoremove -y

    rm -rf /root/awg
    rm -rf /etc/amnezia
    rm -f /etc/nginx/sites-available/ping /etc/nginx/sites-enabled/ping

    ok "Удалено"
}

# ==================== MAIN ====================
cmd="${1:-help}"
shift || true

case "$cmd" in
    install)   cmd_install ;;
    add)       cmd_add "$@" ;;
    remove)    cmd_remove "$@" ;;
    list)      cmd_list ;;
    show)      cmd_show "$@" ;;
    status)    cmd_status ;;
    uninstall) cmd_uninstall ;;
    help|--help|-h) usage ;;
    *)
        err "Неизвестная команда: $cmd"
        echo ""
        usage
        exit 1
        ;;
esac