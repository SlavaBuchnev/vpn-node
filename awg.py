import asyncio
import json
import logging
from typing import Optional

from config import AWG_MANAGER, CLIENTS_DIR

logger = logging.getLogger(__name__)


async def _run_manager(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Запускает manage_amneziawg.sh с переданными аргументами.
    Возвращает (exit_code, stdout, stderr).
    """
    cmd = ["sudo", "bash", str(AWG_MANAGER), *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "Таймаут выполнения команды"
    return (
        proc.returncode or 0,
        stdout_b.decode(errors="replace").strip(),
        stderr_b.decode(errors="replace").strip(),
    )


def _parse_json(stdout: str) -> Optional[dict | list]:
    """Пытается извлечь JSON из stdout (менеджер поддерживает --json)."""
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


async def client_exists(name: str) -> bool:
    """Проверяет, существует ли клиент с таким именем.
    Источник истины — list --json; файл .conf используется как fallback.
    """
    code, stdout, _ = await _run_manager("list", "--json")
    data = _parse_json(stdout)
    if code == 0 and isinstance(data, list):
        return any(item.get("name") == name for item in data)
    return (CLIENTS_DIR / f"{name}.conf").is_file()


async def add_client(name: str) -> tuple[bool, str]:
    """Создаёт клиента. Возвращает (успех, сообщение/путь к .conf)."""
    if await client_exists(name):
        return False, f"Клиент «{name}» уже существует. Создание отменено."

    code, stdout, stderr = await _run_manager("add", name, "--json")
    if code != 0:
        return False, f"Не удалось создать клиента «{name}».\n{stderr or stdout}"

    conf_path = CLIENTS_DIR / f"{name}.conf"
    if not conf_path.is_file():
        return False, (
            f"Клиент «{name}» создан, но файл {conf_path} не найден.\n"
            f"Проверьте каталог {CLIENTS_DIR} вручную."
        )

    return True, str(conf_path)


async def remove_client(name: str) -> tuple[bool, str]:
    """Удаляет клиента. Возвращает (успех, сообщение)."""
    if not await client_exists(name):
        return False, f"Клиент «{name}» не найден. Удалять нечего."

    code, stdout, stderr = await _run_manager("remove", name, "--json")
    if code != 0:
        return False, f"Не удалось удалить клиента «{name}».\n{stderr or stdout}"

    return True, f"Клиент «{name}» удалён."


async def list_clients() -> tuple[bool, str]:
    """Возвращает список клиентов в виде текста."""
    code, stdout, stderr = await _run_manager("list", "--json")
    if code != 0:
        return False, f"Не удалось получить список клиентов.\n{stderr or stdout}"

    data = _parse_json(stdout)
    if data is None:
        # fallback: обычный list без --json
        code2, stdout2, _ = await _run_manager("list")
        if code2 != 0:
            return False, "Не удалось получить список клиентов."
        return True, stdout2 or "Список пуст."

    if not isinstance(data, list) or not data:
        return True, "Клиентов пока нет."

    lines = ["👥 **Клиенты AmneziaWG:**", ""]
    for item in data:
        name = item.get("name", "—")
        ipv4 = item.get("ipv4", item.get("address", ""))
        status = item.get("status", "active")
        lines.append(f"• `{name}` — {status}" + (f" ({ipv4})" if ipv4 else ""))
    return True, "\n".join(lines)