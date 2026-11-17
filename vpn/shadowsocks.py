"""Бэкенд Shadowsocks: управление пользователями shadowsocks-rust.

Multi-user режим AEAD-2022 (Extensible Identity Header): каждый клиент —
свой ключ (base64 от 32 случайных байт), ссылка ss:// строится по SIP002.
"""

import base64
import logging
import os
from urllib.parse import quote

from config import SS_CONFIG, get_vpn_host, load_server_params
from vpn.base import Backend, ClientPayload
from vpn.common import read_json, restart_service, write_json_atomic

logger = logging.getLogger(__name__)

SS_SERVICE = "shadowsocks-rust"


class ShadowsocksBackend(Backend):
    key = "ss"
    label = "Shadowsocks 2022"

    def _load(self) -> tuple[dict, list]:
        cfg = read_json(SS_CONFIG, {})
        users = cfg.get("users")
        if not isinstance(users, list):
            users = []
        return cfg, users

    async def create(self, name: str) -> tuple[bool, dict | str]:
        cfg, users = self._load()
        if not cfg:
            return False, (
                f"Конфиг Shadowsocks не найден: {SS_CONFIG}.\n"
                "Сначала запустите deploy.sh."
            )
        if any(u.get("name") == name for u in users):
            return False, f"Клиент «{name}» уже есть в конфиге Shadowsocks."

        password = base64.b64encode(os.urandom(32)).decode()
        users.append({"name": name, "password": password})
        cfg["users"] = users
        write_json_atomic(SS_CONFIG, cfg)

        ok, err = await restart_service(SS_SERVICE)
        if not ok:
            return False, (
                f"Клиент добавлен в конфиг, но сервис не перезапустился: {err}\n"
                "Проверьте: journalctl -u shadowsocks-rust -n 30 --no-pager"
            )
        return True, {"password": password}

    async def delete(self, name: str, info: dict | None) -> tuple[bool, str]:
        cfg, users = self._load()
        if not cfg:
            return False, f"Конфиг Shadowsocks не найден: {SS_CONFIG}."
        new_users = [u for u in users if u.get("name") != name]
        if len(new_users) == len(users):
            return False, f"Клиент «{name}» не найден в конфиге Shadowsocks."
        cfg["users"] = new_users
        write_json_atomic(SS_CONFIG, cfg)

        ok, err = await restart_service(SS_SERVICE)
        if not ok:
            return False, (
                f"Клиент удалён из конфига, но сервис не перезапустился: {err}\n"
                "Проверьте: journalctl -u shadowsocks-rust -n 30 --no-pager"
            )
        return True, f"Клиент «{name}» удалён из Shadowsocks."

    async def payload(self, name: str, info: dict | None) -> tuple[bool, ClientPayload | str]:
        params = load_server_params().get("ss")
        if not params:
            return False, "Не найден /etc/awg-bot/server-params.json — сначала запустите deploy.sh."
        password = (info or {}).get("password")
        if not password:
            return False, f"В хранилище нет пароля клиента «{name}»."
        host = get_vpn_host()
        if not host:
            return False, (
                "Не удалось определить публичный адрес сервера. "
                "Задайте VPN_HOST в .env (лучше доменное имя)."
            )
        userinfo = base64.urlsafe_b64encode(
            f"{params['method']}:{password}".encode()
        ).decode().rstrip("=")
        link = f"ss://{userinfo}@{host}:{params['port']}#{quote(name)}"
        return True, ClientPayload(link=link, conf=None, qr_text=link)
