"""Бэкенд VLESS + Xray Reality: управление клиентами config.json Xray.

Каждый клиент — uuid с email = имени клиента (для статистики Xray).
Конфиг применяется перезапуском сервиса xray (это быстро и не ломает
других подключённых клиентов — Reality handshake восстанавливается сам).
"""

import logging
import re
import uuid

from config import XRAY_CONFIG, get_vpn_host, load_server_params
from vpn.base import Backend, ClientPayload
from vpn.common import humanize_bytes, read_json, restart_service, run_cmd, write_json_atomic

logger = logging.getLogger(__name__)

XRAY_SERVICE = "xray"
XRAY_API = "127.0.0.1:10085"
_STATS_RE = re.compile(
    r"user>>>(?P<who>.+?)>>>traffic>>>(?P<kind>uplink|downlink)[^\d\n]*(?P<val>\d+)"
)


def _vless_inbound(cfg: dict) -> dict | None:
    for inbound in cfg.get("inbounds", []):
        if inbound.get("protocol") == "vless":
            return inbound
    return None


class VlessBackend(Backend):
    key = "vless"
    label = "VLESS (Xray Reality)"

    async def create(self, name: str) -> tuple[bool, dict | str]:
        cfg = read_json(XRAY_CONFIG, {})
        inbound = _vless_inbound(cfg)
        if inbound is None:
            return False, (
                f"Конфиг Xray не найден или не содержит VLESS-inbound: {XRAY_CONFIG}.\n"
                "Сначала запустите deploy.sh."
            )
        clients = inbound.setdefault("settings", {}).setdefault("clients", [])
        if any(c.get("email") == name for c in clients):
            return False, f"Клиент «{name}» уже есть в конфиге Xray."

        client_id = str(uuid.uuid4())
        clients.append({
            "id": client_id,
            "email": name,
            "flow": "xtls-rprx-vision",
        })
        write_json_atomic(XRAY_CONFIG, cfg)

        ok, err = await restart_service(XRAY_SERVICE)
        if not ok:
            return False, (
                f"Клиент добавлен в конфиг, но Xray не перезапустился: {err}\n"
                "Проверьте: journalctl -u xray -n 30 --no-pager"
            )
        return True, {"id": client_id}

    async def delete(self, name: str, info: dict | None) -> tuple[bool, str]:
        cfg = read_json(XRAY_CONFIG, {})
        inbound = _vless_inbound(cfg)
        if inbound is None:
            return False, f"Конфиг Xray не найден: {XRAY_CONFIG}."
        clients = inbound.get("settings", {}).get("clients", [])
        new_clients = [c for c in clients if c.get("email") != name]
        if len(new_clients) == len(clients):
            return False, f"Клиент «{name}» не найден в конфиге Xray."
        inbound["settings"]["clients"] = new_clients
        write_json_atomic(XRAY_CONFIG, cfg)

        ok, err = await restart_service(XRAY_SERVICE)
        if not ok:
            return False, (
                f"Клиент удалён из конфига, но Xray не перезапустился: {err}\n"
                "Проверьте: journalctl -u xray -n 30 --no-pager"
            )
        return True, f"Клиент «{name}» удалён из Xray."

    async def payload(self, name: str, info: dict | None) -> tuple[bool, ClientPayload | str]:
        params = load_server_params().get("xray")
        if not params:
            return False, "Не найден /etc/awg-bot/server-params.json — сначала запустите deploy.sh."
        client_id = (info or {}).get("id")
        if not client_id:
            return False, f"В хранилище нет идентификатора клиента «{name}»."
        host = get_vpn_host()
        if not host:
            return False, (
                "Не удалось определить публичный адрес сервера. "
                "Задайте VPN_HOST в .env (лучше доменное имя)."
            )
        link = (
            f"vless://{client_id}@{host}:{params['port']}"
            f"?type=tcp&security=reality"
            f"&pbk={params['public_key']}"
            f"&fp=chrome"
            f"&sid={params['short_id']}"
            f"&sni={params['sni']}"
            f"&flow=xtls-rprx-vision"
            f"#{name}"
        )
        return True, ClientPayload(link=link, conf=None, qr_text=link)

    async def traffic(self, infos: dict[str, dict]) -> dict[str, str]:
        # В конфиге Xray email клиента = его имени, статистика идёт по email
        known = {n for n, i in infos.items()
                 if i.get("type") == self.key and i.get("id")}
        if not known:
            return {}
        code, out, _ = await run_cmd(
            "xray", "api", "statsquery", f"--server={XRAY_API}", timeout=15
        )
        if code != 0 or not out:
            return {}
        stats: dict[str, dict[str, int]] = {}
        for m in _STATS_RE.finditer(out):
            who = m.group("who")
            if who not in known:
                continue
            stats.setdefault(who, {})[m.group("kind")] = int(m.group("val"))
        return {
            name: f"↓ {humanize_bytes(v.get('downlink', 0))}  ↑ {humanize_bytes(v.get('uplink', 0))}"
            for name, v in stats.items()
        }
