#!/usr/bin/env bash
# Установщик AmneziaWG Telegram Bot.
# Использование:
#   sudo ./deploy.sh                    # интерактивно спросит BOT_TOKEN и ADMIN_USER_ID
#   sudo BOT_TOKEN=... ADMIN_USER_ID=... ./deploy.sh   # неинтерактивно
#   sudo ./deploy.sh --uninstall        # удалить сервис и каталог
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/awg-telegram-bot}"
SERVICE_NAME="${SERVICE_NAME:-awg-bot}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- утилиты ----------
RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
log()  { echo "${GREEN}[+]${RESET} $*"; }
warn() { echo "${YELLOW}[!]${RESET} $*"; }
die()  { echo "${RED}[x]${RESET} $*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "Запустите скрипт от root (sudo ./deploy.sh)"
}

# ---------- удаление ----------
uninstall() {
    log "Останавливаю и удаляю сервис ${SERVICE_NAME}"
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    if [[ -d "$INSTALL_DIR" ]]; then
        warn "Каталог ${INSTALL_DIR} не удалён автоматически (там может быть .env)."
        warn "Удалите вручную: rm -rf ${INSTALL_DIR}"
    fi
    log "Готово."
}

# ---------- зависимости ----------
install_deps() {
    log "Проверяю зависимости"
    local pkgs=()
    command -v "$PYTHON_BIN" >/dev/null || pkgs+=("$PYTHON_BIN")
    "$PYTHON_BIN" -c 'import venv' 2>/dev/null || pkgs+=("${PYTHON_BIN}-venv")
    command -v rsync >/dev/null || pkgs+=("rsync")
    if ((${#pkgs[@]})); then
        warn "Устанавливаю пакеты: ${pkgs[*]}"
        if command -v apt-get >/dev/null; then
            apt-get update -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pkgs[@]}"
        elif command -v dnf >/dev/null; then
            dnf install -y "${pkgs[@]}"
        elif command -v yum >/dev/null; then
            yum install -y "${pkgs[@]}"
        else
            die "Неизвестный пакетный менеджер. Установите вручную: ${pkgs[*]}"
        fi
    fi
}

# ---------- копирование файлов ----------
copy_files() {
    log "Копирую файлы в ${INSTALL_DIR}"
    mkdir -p "$INSTALL_DIR"
    rsync -a --delete \
        --exclude '.env' \
        --exclude 'venv' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.git' \
        --exclude 'deploy.sh' \
        "$SRC_DIR"/ "$INSTALL_DIR"/
}

# ---------- настройка порта AmneziaWG ----------
AWG_PORT="${AWG_PORT:-443}"
AWG_CONF="/etc/amnezia/amneziawg/awg0.conf"
AWG_CFG_INIT="/root/awg/awgsetup_cfg.init"

configure_awg_port() {
    log "Проверяю порт AmneziaWG (целевой: ${AWG_PORT}/udp)"

    if [[ ! -f "$AWG_CONF" ]]; then
        warn "Не найден ${AWG_CONF} — AmneziaWG не установлен?"
        warn "Сначала установите его через bivlked/amneziawg-installer,"
        warn "затем перезапустите deploy.sh. Пропускаю настройку порта."
        return
    fi

    # Текущий ListenPort
    local current
    current="$(grep -E '^\s*ListenPort\s*=' "$AWG_CONF" | awk -F= '{gsub(/ /,"",$2); print $2}' | head -n1)"

    if [[ "$current" == "$AWG_PORT" ]]; then
        log "Порт уже ${AWG_PORT}/udp — менять не нужно"
    else
        log "Меняю ListenPort: ${current:-<нет>} → ${AWG_PORT}"
        sed -i -E "s|^\s*ListenPort\s*=.*|ListenPort = ${AWG_PORT}|" "$AWG_CONF"
        # если строки ListenPort не было — добавим в секцию [Interface]
        if ! grep -qE '^\s*ListenPort\s*=' "$AWG_CONF"; then
            sed -i "/^\[Interface\]/a ListenPort = ${AWG_PORT}" "$AWG_CONF"
        fi
    fi

    # Синхронизируем с конфигом установщика (чтобы будущие add/regen знали порт)
    if [[ -f "$AWG_CFG_INIT" ]]; then
        if grep -qE '^\s*export AWG_PORT=' "$AWG_CFG_INIT"; then
            sed -i -E "s|^\s*export AWG_PORT=.*|export AWG_PORT=${AWG_PORT}|" "$AWG_CFG_INIT"
        else
            echo "export AWG_PORT=${AWG_PORT}" >> "$AWG_CFG_INIT"
        fi
        log "Обновлён ${AWG_CFG_INIT}: AWG_PORT=${AWG_PORT}"
    else
        warn "Не найден ${AWG_CFG_INIT} — пропускаю синхронизацию с установщиком"
    fi

    # Файрвол: ufw, если используется
    if command -v ufw >/dev/null 2>&1; then
        if ufw status | grep -q "Status: active"; then
            if ! ufw status | grep -qE "^${AWG_PORT}/udp\s+ALLOW"; then
                log "Открываю ${AWG_PORT}/udp в ufw"
                ufw allow "${AWG_PORT}/udp"
            else
                log "${AWG_PORT}/udp уже разрешён в ufw"
            fi
        else
            warn "ufw установлен, но неактивен — правило не добавляю"
        fi
    fi

    # Перезапуск сервиса, только если что-то меняли
    if [[ "$current" != "$AWG_PORT" ]]; then
        log "Перезапускаю awg-quick@awg0"
        systemctl restart awg-quick@awg0
        sleep 1
        if ! systemctl is-active --quiet awg-quick@awg0; then
            die "awg-quick@awg0 не запустился. Проверьте: journalctl -u awg-quick@awg0 -n 50"
        fi
        log "AmneziaWG перезапущен на ${AWG_PORT}/udp"
    fi
}

# ---------- venv и зависимости ----------
setup_venv() {
    log "Готовлю виртуальное окружение"
    if [[ ! -x "${INSTALL_DIR}/venv/bin/python" ]]; then
        "$PYTHON_BIN" -m venv "${INSTALL_DIR}/venv"
    fi
    "${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
    "${INSTALL_DIR}/venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
    log "Зависимости установлены"
}

# ---------- .env ----------
prompt_env() {
    local env_file="${INSTALL_DIR}/.env"
    if [[ -f "$env_file" ]]; then
        log ".env уже существует — оставляю как есть"
        return
    fi

    local token="${BOT_TOKEN:-}"
    local admin="${ADMIN_USER_ID:-}"

    if [[ -z "$token" && -t 0 ]]; then
        read -r -p "BOT_TOKEN от @BotFather: " token
    fi
    if [[ -z "$admin" && -t 0 ]]; then
        read -r -p "Ваш Telegram ID (узнать у @userinfobot): " admin
    fi

    [[ -n "$token" ]] || die "BOT_TOKEN не задан"
    [[ -n "$admin" ]] || die "ADMIN_USER_ID не задан"

    umask 077
    cat > "$env_file" <<EOF
BOT_TOKEN=${token}
ADMIN_USER_ID=${admin}
PROXY_URL=
EOF
    umask 022
    chmod 600 "$env_file"
    log "Создан ${env_file} (права 600)"
}

# ---------- systemd ----------
create_systemd_unit() {
    local unit="/etc/systemd/system/${SERVICE_NAME}.service"
    log "Создаю ${unit}"
    cat > "$unit" <<EOF
[Unit]
Description=AmneziaWG Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
}

enable_start() {
    log "Включаю и запускаю ${SERVICE_NAME}"
    systemctl enable --now "${SERVICE_NAME}"
    sleep 2
    systemctl --no-pager --lines=15 status "${SERVICE_NAME}" || true
}

# ---------- main ----------
main() {
    case "${1:-}" in
        --uninstall|-u) require_root; uninstall; exit 0 ;;
        --help|-h)
            echo "Использование: sudo ./deploy.sh [--uninstall]"
            echo "Переменные: INSTALL_DIR, SERVICE_NAME, PYTHON_BIN, BOT_TOKEN, ADMIN_USER_ID"
            exit 0 ;;
    esac

    require_root
    install_deps
    copy_files
    setup_venv
    prompt_env
    configure_awg_port
    create_systemd_unit
    enable_start

    log "Установка завершена."
    echo
    echo "Полезное:"
    echo "  Логи:      journalctl -u ${SERVICE_NAME} -f"
    echo "  Статус:    systemctl status ${SERVICE_NAME}"
    echo "  Рестарт:   systemctl restart ${SERVICE_NAME}"
    echo "  Удалить:   sudo ./deploy.sh --uninstall"
}

main "$@"