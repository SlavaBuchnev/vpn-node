#!/usr/bin/env python3
"""Записывает конфиг shadowsocks-rust (AEAD-2022, multi-user) для VPN Node.

Вызывается из deploy.sh, параметры передаются переменными окружения:
  SS_CONFIG  — путь к config.json
  SS_PORT    — порт сервера
  SS_METHOD  — метод шифрования (2022-blake3-aes-256-gcm)
  SS_KEY     — серверный ключ (base64 от 32 байт)

Существующих пользователей сохраняет, прежний конфиг бэкапит рядом.
"""

import json
import os
import shutil
import time

path = os.environ["SS_CONFIG"]

try:
    with open(path) as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        cfg = {}
except Exception:
    cfg = {}

users = cfg.get("users") if isinstance(cfg.get("users"), list) else []

if os.path.exists(path):
    shutil.copy2(path, path + ".bak." + time.strftime("%Y%m%d%H%M%S"))

cfg.clear()
cfg["server"] = "::"
cfg["server_port"] = int(os.environ["SS_PORT"])
cfg["mode"] = "tcp_and_udp"
cfg["method"] = os.environ["SS_METHOD"]
cfg["password"] = os.environ["SS_KEY"]
cfg["users"] = users

os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
os.chmod(path, 0o600)
