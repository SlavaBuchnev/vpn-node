#!/usr/bin/env python3
"""Записывает конфиг Xray (VLESS + Reality) для VPN Node.

Вызывается из deploy.sh, параметры передаются переменными окружения:
  XRAY_CONFIG  — путь к config.json
  XRAY_PORT    — TCP-порт VLESS-inbound
  REALITY_SNI  — маскировочный домен
  REALITY_PRIV — приватный ключ Reality (x25519)
  REALITY_SID  — short id (hex)

Существующих клиентов сохраняет, прежний конфиг бэкапит рядом.
"""

import json
import os
import shutil
import time

path = os.environ["XRAY_CONFIG"]
port = int(os.environ["XRAY_PORT"])
sni = os.environ["REALITY_SNI"]
priv = os.environ["REALITY_PRIV"]
sid = os.environ["REALITY_SID"]

try:
    with open(path) as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        cfg = {}
except Exception:
    cfg = {}

# сохраняем уже выданных клиентов
clients = []
for ib in cfg.get("inbounds", []):
    if ib.get("protocol") == "vless":
        clients = (ib.get("settings") or {}).get("clients") or []
        break

if os.path.exists(path):
    shutil.copy2(path, path + ".bak." + time.strftime("%Y%m%d%H%M%S"))

cfg.clear()
cfg["log"] = {"loglevel": "warning"}
cfg["api"] = {"tag": "api", "services": ["StatsService"]}
cfg["stats"] = {}
cfg["policy"] = {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}}
cfg["inbounds"] = [
    {
        "tag": "vless-in",
        "listen": "0.0.0.0",
        "port": port,
        "protocol": "vless",
        "settings": {"clients": clients, "decryption": "none"},
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": f"{sni}:443",
                "xver": 0,
                "serverNames": [sni],
                "privateKey": priv,
                "shortIds": [sid],
            },
        },
    },
    {
        "tag": "api",
        "listen": "127.0.0.1",
        "port": 10085,
        "protocol": "dokodemo-door",
        "settings": {"address": "127.0.0.1"},
    },
]
cfg["outbounds"] = [{"protocol": "freedom", "tag": "direct"}]
cfg["routing"] = {"rules": [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}]}

os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
os.chmod(path, 0o600)
