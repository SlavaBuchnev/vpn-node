"""Хранилище метаданных клиентов (/etc/awg-bot/state.json).

Хранит имя клиента, тип VPN и его секреты (приватный ключ AmneziaWG,
UUID VLESS, пароль Shadowsocks). Файл принадлежит root с правами 600.
"""

import asyncio
import json
import os
import time
from typing import Any

from config import STATE_FILE

_lock = asyncio.Lock()


def _read() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"clients": {}}
    if not isinstance(data, dict) or not isinstance(data.get("clients"), dict):
        return {"clients": {}}
    return data


def _write(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


async def all_clients() -> dict[str, dict]:
    async with _lock:
        return dict(_read()["clients"])


async def get(name: str) -> dict | None:
    async with _lock:
        return _read()["clients"].get(name)


async def add(name: str, info: dict) -> None:
    async with _lock:
        data = _read()
        info = dict(info)
        info.setdefault("created", int(time.time()))
        data["clients"][name] = info
        _write(data)


async def remove(name: str) -> dict | None:
    async with _lock:
        data = _read()
        info = data["clients"].pop(name, None)
        _write(data)
        return info
