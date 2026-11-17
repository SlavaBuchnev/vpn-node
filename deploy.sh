#!/usr/bin/env bash
# VPN Node — полный установщик сервера и Telegram-бота.
#
# Одна команда ставит всё:
#   AmneziaWG (сервер, обфускация AWG 2.0) + VLESS/Xray Reality + Shadowsocks 2022
#   и Telegram-бота управления клиентами.
#
# Использование:
#   sudo ./deploy.sh                    # интерактивно спросит BOT_TOKEN и ADMIN_USER_ID
#   sudo BOT_TOKEN=... ADMIN_USER_ID=... ./deploy.sh   # неинтерактивно
#   sudo ./deploy.sh --uninstall        # удалить только сервис бота (VPN не трогает)
#
# Переменные окружения:
#   BOT_TOKEN, ADMIN_USER_ID, PROXY_URL — параметры бота
#   VPN_HOST           — публичный IP/домен (иначе определится автоматически)
#   AWG_PORT           — UDP-порт AmneziaWG (по умолчанию 443)
#   XRAY_PORT          — TCP-порт VLESS/Reality (по умолчанию 443, сосуществует с AWG)
#   SS_PORT            — порт Shadowsocks (по умолчанию 1443)
#   AWG_SUBNET         — подсеть AmneziaWG (по умолчанию 10.8.0.1/24)
#   REALITY_SNI        — маскировочный домен Reality (по умолчанию dl.google.com)
#   INSTALL_DIR, SERVICE_NAME, PYTHON_BIN
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/awg-telegram-bot}"
SERVICE_NAME="${SERVICE_NAME:-awg-bot}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VPN_HOST="${VPN_HOST:-}"
AWG_PORT="${AWG_PORT:-443}"
XRAY_PORT="${XRAY_PORT:-443}"
SS_PORT="${SS_PORT:-1443}"
AWG_SUBNET="${AWG_SUBNET:-10.8.0.1/24}"
REALITY_SNI="${REALITY_SNI:-dl.google.com}"
SS_METHOD="2022-blake3-aes-256-gcm"

AWG_CONF="/etc/amnezia/amneziawg/awg0.conf"
XRAY_CONFIG="/usr/local/etc/xray/config.json"
SS_CONFIG="/etc/shadowsocks-rust/config.json"
BOT_STATE_DIR="/etc/awg-bot"
SERVER_PARAMS="${BOT_STATE_DIR}/server-params.json"

# ---------- утилиты ----------

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
log()  { echo "${GREEN}[+]${RESET} $*"; }
warn() { echo "${YELLOW}[!]${RESET} $*"; }
die()  { echo "${RED}[x]${RESET} $*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "Запустите скрипт от root (sudo ./deploy.sh)"
}

rand_range() { echo $(( $1 + RANDOM % ($2 - $1 + 1) )); }

# ---------- удаление (--uninstall: только бот; VPN-сервисы и конфиги не трогаем) ----------

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
    log "Готово. VPN-сервисы (awg-quick@awg0, xray, shadowsocks-rust) продолжают работать."
}

# ---------- базовые зависимости ----------

install_base_deps() {
    log "Проверяю базовые зависимости"
    local pkgs=()
    command -v "$PYTHON_BIN" >/dev/null || pkgs+=("$PYTHON_BIN")
    "$PYTHON_BIN" -c 'import venv' 2>/dev/null || pkgs+=("${PYTHON_BIN}-venv")
    command -v curl >/dev/null || pkgs+=("curl")
    command -v openssl >/dev/null || pkgs+=("openssl")
    command -v rsync >/dev/null || pkgs+=("rsync")
    command -v tar >/dev/null || pkgs+=("tar")
    command -v gpg >/dev/null || pkgs+=("gpg")
    if ((${#pkgs[@]})) && command -v apt-get >/dev/null; then
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pkgs[@]}" ca-certificates xz-utils iptables
        # DKMS-модулю AmneziaWG нужны заголовки ядра — ставим наперёд (best effort)
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            "linux-headers-$(uname -r)" 2>/dev/null || \
            warn "linux-headers-$(uname -r) недоступны — если DKMS не соберёт модуль, установите заголовки вручную"
    elif ((${#pkgs[@]})); then
        die "Автоустановка зависимостей поддерживается только для apt (Debian/Ubuntu). Не хватает: ${pkgs[*]}"
    fi
}

detect_public_host() {
    if [[ -z "$VPN_HOST" ]]; then
        VPN_HOST="$(curl -4fsS --max-time 8 https://ifconfig.me/ip 2>/dev/null \
            || curl -4fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
    fi
    if [[ -z "$VPN_HOST" ]]; then
        warn "Не удалось автоматически определить публичный IP сервера"
        if [[ -t 0 ]]; then
            read -r -p "Введите публичный IP или доменное имя сервера: " VPN_HOST
        fi
    fi
    if [[ -n "$VPN_HOST" ]]; then
        log "Адрес сервера для клиентских конфигов: ${VPN_HOST}"
    else
        warn "VPN_HOST не задан — бот попробует определить IP сам при первом запросе"
    fi
}

# ---------- AmneziaWG ----------

# Ключ подписи репозитория ppa.launchpadcontent.net/amnezia/ppa (RSA 4096, без срока
# действия; fingerprint сверен со страницей PPA на Launchpad).
_amnezia_ppa_key_armored() {
    cat <<'PPAKEY'
-----BEGIN PGP PUBLIC KEY BLOCK-----

xsFNBGV0UhsBEAC33rMndHSN/k+u7gcZbh9/FjgYfGltQAtVe2QDxzn7UV+k/ChX
OrYRw6Izw/DrhaapkNCThK2jwJE64e0NjboLH7UrrmSJLXMfOlDFbyGJVRA+1sTB
lo7kKHY0xiZ1CHDzjKNV3czbesu80A9nuTZYyWHEn9ax6wsqKG3N8SvzQkUrIOVD
2wZjh0p273CCEGkBnax1ghAV3MF8OrsPU6FRJ+ZakzKbu54g68xoV+2813YECme0
JKsWfUUe/1uEJOXCvuACURSxnYr0sihJd8QI/jHSGlfeq72e5MflFEOrnu5xaDSJ
r2W5lvUetG7EGSxtNKd7Jm/KhUV04g7arA0qydRjRToW3QqyzG7VB2nXKz3AOBYN
earWAuBcTkfPvRVchxbjiYonKZA5tIlVrpawMZsdxKvYwl6LVnpBcccFWPhpudfy
4TpCqCxRoAanOCvSirI3/y7TcZMBw643SaxXi1ifGeg6eyMzrLtP3CeonKBHGzrt
1eeKGtEw/PFN4RmwpBePxi+uj0CoTD6zjCQa3c8EeB4Qz7tt6PnpibxdtZE8sBdd
51wSA/fPGi2tFph8IVAsws7oxcQxZYl8CyncKDLcoR4dxVHYdFEDDf1GjRjoQ3Ai
nD7fxD5qYzExe50DBVpuUbWcAiGICNxfvzQtUSRRtMoSHDcvzsy03KC6VwARAQAB
zR5MYXVuY2hwYWQgUFBBIGZvciBJdXJpaSBFZ29yb3bCwY4EEwEKADgWIQR1yd1y
x5mHDjEFQuJBZvLCVykIKAUCZXRSGwIbAwULCQgHAgYVCgkICwIEFgIDAQIeAQIX
gAAKCRBBZvLCVykIKBu5D/9akmHCHlUqm2RTTBeTMbLNGc0l6YugpPaCM6vz0O9k
BFP5PfRaNSRzyF7wHFHNY3JUHcor28my1fD8AE4+C3PwXz8tVYLh57UUsp4wjqHY
+MTl/1ngDViPGD3PRjB8ZlO+19yerfplZv1Jaw7FZZv2BZOAXb+ddqUG4EmlzOnC
EhcSDdFrzEBB3RGthjIb3QkKWKGbELDiMfogmsO9BE139Raiw23blagDrbnWsG4j
ReZeu3atjG6AW8eL7m+i7bKKshD2CYVMznI5cYGLMKo9w7sb33uylPj1Vx9O7joP
2GFf2rTpCY8wgzk7i1RqsipJ80u1/DY91Xdizv3f2BBe6UY7qHKoK00O11J0y8yU
is2Asycy33Wy51pf6rCFUBLQu+c1fEypHF6jqANmQwaH7pPBliy4gGWvrVggzV4m
xv7SnRiMi4PFyVwjKWm8dmuMxi/B9s++VG/ed+5aYgJYL58MohG3MUI/L58eitSC
DDcQ1iAnBmawnGMKPqzMgRFB3OU3wDwfh7LNVvQqWpQ4q7pr4Cq1CvZvGoggXWDo
1/vylPsRmiiuNetfsoVYmrkgtj1om07m5Xp1v4SyXJH11c3dc/xfMmn/4RlMWIpq
86IsOjpr3avsw3FVUNCgD5Wf5+rHG+7gNmM6Cm/F8MDfAnnRmsw4h6hgvcJNQT5D
ig==
=OY2W
-----END PGP PUBLIC KEY BLOCK-----
PPAKEY
}

install_amnezia_ppa_keyring() {
    local keyring_file="$1"
    local fpr="75C9DD72C799870E310542E24166F2C257290828"
    local tmp
    tmp="$(mktemp)"
    _amnezia_ppa_key_armored | gpg --batch --no-tty --yes --dearmor -o "$tmp" \
        || { rm -f "$tmp"; die "Не удалось импортировать ключ Amnezia PPA"; }
    local got
    got="$(gpg --batch --no-tty --show-keys --with-colons "$tmp" 2>/dev/null \
        | awk -F: '/^fpr:/{print $10; exit}')"
    [[ "$got" == "$fpr" ]] || { rm -f "$tmp"; die "Ключ Amnezia PPA не прошёл проверку отпечатка"; }
    chmod 644 "$tmp"
    mv -f "$tmp" "$keyring_file"
}

install_amneziawg() {
    if command -v awg >/dev/null 2>&1; then
        log "AmneziaWG уже установлен ($(awg --version 2>/dev/null | head -n1))"
        return
    fi
    log "Устанавливаю AmneziaWG из PPA amnezia/ppa"
    command -v apt-get >/dev/null 2>&1 \
        || die "Автоустановка AmneziaWG поддерживается только на Debian/Ubuntu. Установите amneziawg вручную."

    install -d -m 0755 /etc/apt/keyrings
    install_amnezia_ppa_keyring /etc/apt/keyrings/amnezia-ppa.gpg

    local distro suite
    . /etc/os-release
    distro="${ID:-}"
    if [[ "$distro" == "ubuntu" ]]; then
        suite="${VERSION_CODENAME:-}"
    elif [[ "$distro" == "debian" ]]; then
        # PPA публикует только Ubuntu-сборки: Debian 12 -> jammy, Debian 13+ -> noble
        if [[ "${VERSION_ID%%.*}" -ge 13 ]]; then suite="noble"; else suite="jammy"; fi
    else
        die "Дистрибутив '${distro:-?}' не поддерживается автоустановкой. Установите AmneziaWG вручную."
    fi
    [[ -n "$suite" ]] || die "Не удалось определить кодовое имя дистрибутива"

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/amnezia-ppa.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu ${suite} main" \
        > /etc/apt/sources.list.d/amnezia-ppa.list
    apt-get update -qq
    if apt-cache show amneziawg-tools >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq amneziawg amneziawg-tools
    else
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq amneziawg
    fi
    command -v awg >/dev/null 2>&1 || die "AmneziaWG не установился: утилита awg не появилась"
    log "AmneziaWG установлен"
}

# --- генерация параметров обфускации AWG 2.0 ---
# Диапазоны и проверки коллизий размеров пакетов взяты из bivlked/amneziawg-installer
# (проверены на исходниках модуля ядра; гарантируют валидный конфиг).

s4_collides() { # S1 S2 S3 S4
    local p
    p=$(( $1 - $4 + 116 )); (( p >= 32 && p % 16 == 0 && p != 128 )) && return 0
    p=$(( $2 - $4 + 60 ));  (( p >= 32 && p % 16 == 0 )) && return 0
    p=$(( $3 - $4 + 32 ));  (( p >= 48 && p % 16 == 0 )) && return 0
    return 1
}

gen_h_ranges() { # 4 непересекающихся диапазона uint31: "a-b"
    local attempt=0
    while (( attempt < 50 )); do
        local vals=() i
        for i in 1 2 3 4 5 6 7 8; do
            vals[$((i-1))]=$(( (RANDOM << 16 | RANDOM) & 2147483647 ))
        done
        local sorted
        sorted="$(printf '%s\n' "${vals[@]}" | sort -n)"
        vals=()
        while IFS= read -r line; do vals+=("$line"); done <<< "$sorted"
        if (( vals[0] >= 5 && vals[1]-vals[0] >= 1000 \
           && vals[3]-vals[2] >= 1000 && vals[5]-vals[4] >= 1000 \
           && vals[7]-vals[6] >= 1000 \
           && vals[2] > vals[1] && vals[4] > vals[3] && vals[6] > vals[5] )); then
            printf '%s-%s\n' "${vals[0]}" "${vals[1]}"
            printf '%s-%s\n' "${vals[2]}" "${vals[3]}"
            printf '%s-%s\n' "${vals[4]}" "${vals[5]}"
            printf '%s-%s\n' "${vals[6]}" "${vals[7]}"
            return 0
        fi
        attempt=$((attempt+1))
    done
    return 1
}

gen_awg_params() {
    AWG_JC="$(rand_range 3 6)"
    AWG_JMIN="$(rand_range 40 89)"
    AWG_JMAX=$(( AWG_JMIN + $(rand_range 50 250) ))

    AWG_S1="$(rand_range 15 150)"
    while :; do AWG_S2="$(rand_range 15 150)"; (( AWG_S1 + 56 != AWG_S2 )) && break; done
    while :; do AWG_S3="$(rand_range 8 55)";  (( AWG_S2 + 28 != AWG_S3 )) && break; done
    while :; do
        AWG_S4="$(rand_range 4 27)"
        (( AWG_S4 == 16 )) && continue
        ! s4_collides "$AWG_S1" "$AWG_S2" "$AWG_S3" "$AWG_S4" && break
    done

    local h=()
    mapfile -t h < <(gen_h_ranges) || die "Не удалось сгенерировать H1-H4"
    AWG_H1="${h[0]}"; AWG_H2="${h[1]}"; AWG_H3="${h[2]}"; AWG_H4="${h[3]}"
    log "Параметры обфускации сгенерированы: Jc=${AWG_JC} Jmin=${AWG_JMIN} Jmax=${AWG_JMAX}"
}

setup_awg_server() {
    install -d -m 700 /etc/amnezia/amneziawg
    if [[ -f "$AWG_CONF" ]]; then
        AWG_PORT="$(sed -n -E 's/^\s*ListenPort\s*=\s*([0-9]+).*/\1/p' "$AWG_CONF" | head -n1)"
        AWG_PORT="${AWG_PORT:-443}"
        log "Конфиг $AWG_CONF уже существует — не изменяю (порт ${AWG_PORT}/udp)"
    else
        log "Создаю серверный конфиг AmneziaWG (${AWG_CONF}, порт ${AWG_PORT}/udp)"
        gen_awg_params
        local server_priv server_net
        server_priv="$(umask 077; awg genkey)"
        server_net="$("$PYTHON_BIN" -c 'import ipaddress,sys; print(ipaddress.ip_network(sys.argv[1], strict=False))' "$AWG_SUBNET")"
        umask 077
        cat > "$AWG_CONF" <<EOF
[Interface]
PrivateKey = ${server_priv}
Address = ${AWG_SUBNET}
ListenPort = ${AWG_PORT}
Jc = ${AWG_JC}
Jmin = ${AWG_JMIN}
Jmax = ${AWG_JMAX}
S1 = ${AWG_S1}
S2 = ${AWG_S2}
S3 = ${AWG_S3}
S4 = ${AWG_S4}
H1 = ${AWG_H1}
H2 = ${AWG_H2}
H3 = ${AWG_H3}
H4 = ${AWG_H4}
PostUp = iptables -t nat -A POSTROUTING -s ${server_net} -j MASQUERADE; iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
PreDown = iptables -t nat -D POSTROUTING -s ${server_net} -j MASQUERADE; iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
EOF
        chmod 600 "$AWG_CONF"
        log "Конфиг создан с обфускацией AWG 2.0"
    fi

    cat > /etc/sysctl.d/99-vpn-node.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
    sysctl -p /etc/sysctl.d/99-vpn-node.conf >/dev/null 2>&1 \
        || sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 \
        || warn "Не удалось включить ip_forward — VPN не будет передавать трафик"
    sysctl -w net.ipv4.conf.all.src_valid_mark=1 >/dev/null 2>&1 || true

    systemctl enable awg-quick@awg0 >/dev/null 2>&1 || true
}

# ---------- Xray (VLESS + Reality) ----------

write_xray_config() {
    XRAY_CONFIG="$XRAY_CONFIG" XRAY_PORT="$XRAY_PORT" \
    REALITY_SNI="$REALITY_SNI" REALITY_PRIV="$REALITY_PRIV" REALITY_SID="$REALITY_SID" \
    "$PYTHON_BIN" "${SRC_DIR}/scripts/write_xray_config.py"
}

install_xray() {
    if ! command -v xray >/dev/null 2>&1; then
        log "Устанавливаю Xray-core"
        curl -fsSL -o /tmp/xray-install-release.sh \
            https://github.com/XTLS/Xray-install/raw/main/install-release.sh
        bash /tmp/xray-install-release.sh install
        rm -f /tmp/xray-install-release.sh
    else
        log "Xray уже установлен ($(xray version 2>/dev/null | head -n1))"
    fi

    # На редеплее сохраняем ключи, на свежей установке генерируем новые
    if [[ -f "$XRAY_CONFIG" ]]; then
        REALITY_PRIV="$("$PYTHON_BIN" -c '
import json, sys
try:
    c = json.load(open(sys.argv[1]))
    for ib in c.get("inbounds", []):
        if ib.get("protocol") == "vless":
            print((ib.get("streamSettings") or {}).get("realitySettings", {}).get("privateKey", ""))
            break
except Exception:
    pass
' "$XRAY_CONFIG" 2>/dev/null || true)"
    fi
    if [[ -n "${REALITY_PRIV:-}" && -f "$SERVER_PARAMS" ]]; then
        REALITY_PUB="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("xray",{}).get("public_key",""))' "$SERVER_PARAMS" 2>/dev/null || true)"
        REALITY_SID="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("xray",{}).get("short_id",""))' "$SERVER_PARAMS" 2>/dev/null || true)"
        log "Reality: использую существующие ключи"
    fi
    if [[ -z "${REALITY_PRIV:-}" ]]; then
        local pair
        pair="$(xray x25519)" || die "Команда 'xray x25519' завершилась с ошибкой"
        # Формат вывода менялся между версиями Xray:
        #   старый: "Private key: ..." / "Public key: ..."
        #   новый (26.x): "PrivateKey: ..." / "Password (PublicKey): ..."
        REALITY_PRIV="$(grep -iE 'private[ _]?key' <<< "$pair" | head -n1 | cut -d: -f2- | tr -d '[:space:]')"
        REALITY_PUB="$(grep -iE 'public[ _]?key' <<< "$pair" | head -n1 | cut -d: -f2- | tr -d '[:space:]')"
        [[ -n "$REALITY_PRIV" && -n "$REALITY_PUB" ]] \
            || die "Не удалось разобрать вывод 'xray x25519': $(head -n 3 <<< "$pair")"
        log "Reality: сгенерирована новая пара ключей"
    fi
    REALITY_SID="${REALITY_SID:-$(openssl rand -hex 8)}"

    write_xray_config
    # Официальный юнит Xray запускает процесс от nobody, а конфиг с ключом
    # Reality закрыт правами 600 (root). Переводим сервис на root — как и
    # весь остальной стек (awg-bot, awg-quick), — чтобы nobody не нужен
    # доступ к файлу.
    install -d /etc/systemd/system/xray.service.d
    cat > /etc/systemd/system/xray.service.d/override.conf <<'EOF'
[Service]
User=root
Group=root
EOF
    systemctl daemon-reload
    systemctl enable --now xray >/dev/null 2>&1 || true
    systemctl restart xray
    sleep 1
    if systemctl is-active --quiet xray; then
        log "Xray активен: VLESS+Reality на ${XRAY_PORT}/tcp (маскировка под ${REALITY_SNI})"
    else
        warn "Xray не запустился. Смотрите: journalctl -u xray -n 30 --no-pager"
    fi
}

# ---------- Shadowsocks ----------

write_ss_config() {
    SS_CONFIG="$SS_CONFIG" SS_PORT="$SS_PORT" SS_METHOD="$SS_METHOD" SS_KEY="$SS_KEY" \
    "$PYTHON_BIN" "${SRC_DIR}/scripts/write_ss_config.py"
}

install_shadowsocks() {
    if ! command -v ssserver >/dev/null 2>&1; then
        log "Устанавливаю shadowsocks-rust"
        local arch url tmpdir
        arch="$(uname -m)"
        case "$arch" in
            x86_64)        arch="x86_64" ;;
            aarch64|arm64) arch="aarch64" ;;
            *) die "Архитектура $arch не поддерживается автоустановкой shadowsocks-rust" ;;
        esac
        url="$(curl -fsSL --max-time 30 \
            https://api.github.com/repos/shadowsocks/shadowsocks-rust/releases/latest \
            | "$PYTHON_BIN" -c '
import json, sys
arch = sys.argv[1]
d = json.load(sys.stdin)
for a in d.get("assets", []):
    if a["name"].endswith(f"{arch}-unknown-linux-gnu.tar.xz"):
        print(a["browser_download_url"])
        break
' "$arch")"
        [[ -n "$url" ]] || die "Не нашёл релиз shadowsocks-rust для архитектуры $arch"
        tmpdir="$(mktemp -d)"
        curl -fSL --max-time 180 -o "${tmpdir}/ss.tar.xz" "$url"
        tar -xJf "${tmpdir}/ss.tar.xz" -C "$tmpdir"
        install -m 0755 "${tmpdir}/ssserver" /usr/local/bin/ssserver
        rm -rf "$tmpdir"
        log "ssserver установлен: $(ssserver --version 2>/dev/null | head -n1)"
    else
        log "shadowsocks-rust уже установлен"
    fi

    # Ключ сервера: сохраняем существующий, иначе генерируем
    SS_KEY=""
    if [[ -f "$SS_CONFIG" ]]; then
        SS_KEY="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("password",""))' "$SS_CONFIG" 2>/dev/null || true)"
    fi
    [[ -n "$SS_KEY" ]] || SS_KEY="$(openssl rand -base64 32 | tr -d '[:space:]')"

    write_ss_config

    cat > /etc/systemd/system/shadowsocks-rust.service <<'EOF'
[Unit]
Description=Shadowsocks-rust Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ssserver -c /etc/shadowsocks-rust/config.json
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now shadowsocks-rust >/dev/null 2>&1 || true
    systemctl restart shadowsocks-rust
    sleep 1
    if systemctl is-active --quiet shadowsocks-rust; then
        log "Shadowsocks активен на ${SS_PORT}/tcp+udp (метод ${SS_METHOD})"
    else
        warn "Shadowsocks не запустился. Смотрите: journalctl -u shadowsocks-rust -n 30 --no-pager"
    fi
}

# ---------- сводные параметры для бота ----------

write_server_params() {
    VPN_HOST="$VPN_HOST" AWG_PORT="$AWG_PORT" \
    XRAY_PORT="$XRAY_PORT" REALITY_PUB="$REALITY_PUB" \
    REALITY_SID="$REALITY_SID" REALITY_SNI="$REALITY_SNI" \
    SS_PORT="$SS_PORT" SS_METHOD="$SS_METHOD" SS_KEY="$SS_KEY" \
    SERVER_PARAMS="$SERVER_PARAMS" "$PYTHON_BIN" "${SRC_DIR}/scripts/write_server_params.py"
}

# ---------- файрвол ----------

configure_firewall() {
    if ! command -v ufw >/dev/null 2>&1; then
        return
    fi
    if ! ufw status 2>/dev/null | grep -q "Status: active"; then
        warn "ufw установлен, но неактивен — правила не добавляю"
        return
    fi
    ufw allow "${XRAY_PORT}/tcp" >/dev/null
    ufw allow "${AWG_PORT}/udp" >/dev/null
    ufw allow "${SS_PORT}/tcp" >/dev/null
    ufw allow "${SS_PORT}/udp" >/dev/null
    log "ufw: открыты ${XRAY_PORT}/tcp (VLESS), ${AWG_PORT}/udp (AWG), ${SS_PORT}/tcp+udp (SS)"
}

# ---------- бот ----------

copy_files() {
    log "Копирую файлы бота в ${INSTALL_DIR}"
    mkdir -p "$INSTALL_DIR"
    rsync -a --delete \
        --exclude '.env' \
        --exclude '.venv' \
        --exclude 'venv' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.git' \
        --exclude 'deploy.sh' \
        --exclude 'config' \
        "$SRC_DIR"/ "$INSTALL_DIR"/
}

setup_venv() {
    log "Готовлю виртуальное окружение бота"
    if [[ ! -x "${INSTALL_DIR}/venv/bin/python" ]]; then
        "$PYTHON_BIN" -m venv "${INSTALL_DIR}/venv"
    fi
    "${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
    "${INSTALL_DIR}/venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
}

prompt_env() {
    local env_file="${INSTALL_DIR}/.env"
    if [[ -f "$env_file" ]]; then
        # Бережём существующий файл только если он реально заполнен;
        # пустой/битый .env = бот не запустится, так что пересоздаём.
        if grep -qE '^BOT_TOKEN=.+' "$env_file" \
                && grep -qE '^ADMIN_USER_ID=[0-9]+' "$env_file"; then
            log ".env уже существует и заполнен — оставляю как есть"
            return
        fi
        warn "${env_file} существует, но без BOT_TOKEN/ADMIN_USER_ID — пересоздаю"
    fi

    local token="${BOT_TOKEN:-}" admin="${ADMIN_USER_ID:-}"
    if [[ -z "$token" && -t 0 ]]; then
        read -r -p "BOT_TOKEN от @BotFather: " token
    fi
    if [[ -z "$admin" && -t 0 ]]; then
        read -r -p "Ваш Telegram ID (узнать у @userinfobot): " admin
    fi
    [[ -n "$token" ]] || die "BOT_TOKEN не задан (запустите интерактивно или передайте BOT_TOKEN=... ADMIN_USER_ID=...)"
    [[ -n "$admin" ]] || die "ADMIN_USER_ID не задан (запустите интерактивно или передайте BOT_TOKEN=... ADMIN_USER_ID=...)"

    umask 077
    cat > "$env_file" <<EOF
BOT_TOKEN=${token}
ADMIN_USER_ID=${admin}
PROXY_URL=${PROXY_URL:-}
VPN_HOST=${VPN_HOST}
EOF
    umask 022
    chmod 600 "$env_file"
    log "Создан ${env_file} (права 600)"
}

create_systemd_unit() {
    local unit="/etc/systemd/system/${SERVICE_NAME}.service"
    log "Создаю ${unit}"
    cat > "$unit" <<EOF
[Unit]
Description=VPN Node Telegram Bot
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

enable_start_bot() {
    # enable --now НЕ перезапускает уже работающий сервис: после обновления
    # файлов процесс бота продолжал бы работать на старом коде. Поэтому
    # всегда restart.
    log "Включаю и перезапускаю ${SERVICE_NAME}"
    systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1 || true
    systemctl restart "${SERVICE_NAME}"
    sleep 2
    systemctl --no-pager --lines=15 status "${SERVICE_NAME}" || true
}

# ---------- main ----------

main() {
    case "${1:-}" in
        --uninstall|-u) require_root; uninstall; exit 0 ;;
        --help|-h)
            sed -n '2,24p' "$0"
            exit 0 ;;
    esac

    require_root
    install_base_deps
    detect_public_host
    install_amneziawg
    setup_awg_server
    install_xray
    install_shadowsocks
    write_server_params
    configure_firewall
    copy_files
    setup_venv
    prompt_env
    create_systemd_unit
    enable_start_bot

    echo
    log "Установка завершена."
    echo
    echo "Осталось запустить AmneziaWG (намеренно не делается автоматически):"
    echo "  sudo systemctl restart awg-quick@awg0"
    echo "  sudo systemctl status awg-quick@awg0 --no-pager"
    echo
    echo "Дальше всё управляется через бота в Telegram: /start"
    echo "Полезное:"
    echo "  Логи бота:     journalctl -u ${SERVICE_NAME} -f"
    echo "  Логи Xray:     journalctl -u xray -f"
    echo "  Логи SS:       journalctl -u shadowsocks-rust -f"
    echo "  Удалить бота:  sudo ./deploy.sh --uninstall"
}

main "$@"
