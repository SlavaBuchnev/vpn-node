"""Общие утилиты VPN-бэкендов: запуск команд, JSON, форматирование."""

import asyncio
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def run_cmd(*args: str, timeout: int = 30,
                  stdin: bytes | None = None) -> tuple[int, str, str]:
    """Запускает команду, возвращает (код, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", f"Команда не найдена: {args[0]}"
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(stdin), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "Превышен таймаут выполнения команды"
    return (
        proc.returncode or 0,
        stdout_b.decode(errors="replace").strip(),
        stderr_b.decode(errors="replace").strip(),
    )


async def restart_service(name: str) -> tuple[bool, str]:
    """Перезапускает systemd-сервис и проверяет, что он активен."""
    code, _, err = await run_cmd("systemctl", "restart", name, timeout=40)
    if code != 0:
        return False, err or f"systemctl restart {name} завершился с кодом {code}"
    code, _, _ = await run_cmd("systemctl", "is-active", "--quiet", name)
    if code != 0:
        return False, f"Сервис {name} не перешёл в состояние active"
    return True, ""


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def humanize_bytes(n: int) -> str:
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PiB"
