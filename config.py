"""Конфигурация бота: переменные окружения и пути к VPN-сервисам."""

import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
if not ADMIN_USER_ID:
    raise RuntimeError("ADMIN_USER_ID не задан в .env")

# Прокси опционен: пустая строка или отсутствие = работаем напрямую.
# Поддерживаются SOCKS5 и HTTP. MTProto-прокси (TeleConnect и т.п.) НЕ работают.
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

# Публичный адрес сервера для клиентских конфигов. Домен предпочтительнее
# IP: при смене IP-адреса клиенты продолжат работать без перевыпуска конфигов.
VPN_HOST = os.getenv("VPN_HOST", "").strip() or None

# Пути к конфигурации VPN-сервисов (выставляются deploy.sh).
# Переопределяются переменными окружения для локальной отладки.
AWG_CONF = Path(os.getenv("AWG_CONF", "/etc/amnezia/amneziawg/awg0.conf"))
XRAY_CONFIG = Path(os.getenv("XRAY_CONFIG", "/usr/local/etc/xray/config.json"))
SS_CONFIG = Path(os.getenv("SS_CONFIG", "/etc/shadowsocks-rust/config.json"))
SERVER_PARAMS_FILE = Path(os.getenv("SERVER_PARAMS", "/etc/awg-bot/server-params.json"))
STATE_FILE = Path(os.getenv("STATE_FILE", "/etc/awg-bot/state.json"))


def load_server_params() -> dict:
    """Читает параметры, которые deploy.sh генерирует один раз
    (публичный ключ Reality, параметры Shadowsocks и т.п.)."""
    try:
        data = json.loads(SERVER_PARAMS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_vpn_host_cache: str | None = None


def get_vpn_host() -> str | None:
    """Возвращает публичный адрес сервера: .env -> server-params.json ->
    автоматическое определение внешнего IP."""
    global _vpn_host_cache
    if _vpn_host_cache:
        return _vpn_host_cache
    host = VPN_HOST or load_server_params().get("vpn_host") or _detect_public_ip()
    _vpn_host_cache = host
    return host


def _detect_public_ip() -> str | None:
    for url in ("https://ifconfig.me/ip", "https://api.ipify.org"):
        try:
            out = subprocess.run(
                ["curl", "-4fsS", "--max-time", "5", url],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0:
            ip = out.stdout.decode().strip()
            if ip:
                return ip
    return None
