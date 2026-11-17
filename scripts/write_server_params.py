#!/usr/bin/env python3
"""Пишет /etc/awg-bot/server-params.json — сводные параметры сервера для бота.

Вызывается из deploy.sh, параметры передаются переменными окружения:
  SERVER_PARAMS — путь к файлу
  VPN_HOST, AWG_PORT, XRAY_PORT, SS_PORT, SS_METHOD
  REALITY_PUB, REALITY_SID, REALITY_SNI, SS_KEY

Существующие секреты (публичный ключ Reality, short id, ключ SS) сохраняет,
обновляет адрес сервера и порты. Файл создаётся с правами 600.
"""

import json
import os

path = os.environ["SERVER_PARAMS"]

try:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
except Exception:
    data = {}

data["vpn_host"] = os.environ["VPN_HOST"]
data.setdefault("awg", {})["port"] = int(os.environ["AWG_PORT"])
xray = data.setdefault("xray", {})
xray["port"] = int(os.environ["XRAY_PORT"])
xray.setdefault("public_key", os.environ["REALITY_PUB"])
xray.setdefault("short_id", os.environ["REALITY_SID"])
xray.setdefault("sni", os.environ["REALITY_SNI"])
ss = data.setdefault("ss", {})
ss["port"] = int(os.environ["SS_PORT"])
ss["method"] = os.environ["SS_METHOD"]
ss.setdefault("key", os.environ["SS_KEY"])

os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
os.chmod(path, 0o600)
