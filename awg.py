import asyncio
import json
import logging
import re
import time
from typing import Optional

from config import AWG_MANAGER, CLIENTS_DIR

logger = logging.getLogger(__name__)

_ENDPOINT_RE = re.compile(r"^\s*Endpoint\s*=\s*\S+:(\d+)\s*$", re.MULTILINE)
_PRIVKEY_RE = re.compile(r"^PrivateKey\s*=\s*(\S+)\s*$", re.MULTILINE)


async def _run_manager(*args: str, timeout: int = 30) -> tuple[int, str, str]:
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


# ---------- client existence / CRUD ----------

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


async def get_client(name: str) -> tuple[bool, str]:
    """Возвращает путь к конфигу клиента или сообщение об ошибке."""
    if not await client_exists(name):
        return False, f"Клиент «{name}» не найден."

    conf_path = CLIENTS_DIR / f"{name}.conf"
    if not conf_path.is_file():
        return False, f"Клиент «{name}» есть в списке, но файл {conf_path} не найден."
    return True, str(conf_path)


async def remove_client(name: str) -> tuple[bool, str]:
    """Удаляет клиента. Возвращает (успех, сообщение)."""
    if not await client_exists(name):
        return False, f"Клиент «{name}» не найден. Удалять нечего."

    code, stdout, stderr = await _run_manager("remove", name, "--json")
    if code != 0:
        return False, f"Не удалось удалить клиента «{name}».\n{stderr or stdout}"

    return True, f"Клиент «{name}» удалён."


# ---------- traffic ----------

async def _get_traffic_data() -> dict[str, dict]:
    """Собирает статистику по всем пирам через `awg show <iface> dump`.
    Возвращает {pubkey: {handshake, rx, tx}}.
    """
    attempts = [
        ("awg", "awg0"),
        ("wg", "awg0"),
        ("awg", "wg0"),
        ("wg", "wg0"),
        ("awg", "amneziawg0"),
    ]

    for tool, iface in attempts:
        try:
            proc = await asyncio.create_subprocess_exec(
                tool, "show", iface, "dump",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            continue
        if proc.returncode != 0:
            continue

        result: dict[str, dict] = {}
        lines = stdout.decode(errors="replace").strip().split("\n")
        for line in lines[1:]:  # первая строка — интерфейс
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            try:
                result[parts[0]] = {
                    "handshake": int(parts[4]),
                    "rx": int(parts[5]),
                    "tx": int(parts[6]),
                }
            except ValueError:
                continue
        return result

    logger.warning("Не удалось получить статистику awg ни одним из способов")
    return {}


async def _get_client_pubkey(conf_path) -> Optional[str]:
    """Вычисляет публичный ключ клиента из его PrivateKey."""
    try:
        text = conf_path.read_text(errors="replace")
    except OSError:
        return None

    m = _PRIVKEY_RE.search(text)
    if not m:
        return None
    privkey = m.group(1).encode()

    for tool in ("awg", "wg"):
        try:
            proc = await asyncio.create_subprocess_exec(
                tool, "pubkey",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate(input=privkey)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return stdout.decode().strip()

    return None


def _humanize_bytes(n: int) -> str:
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PiB"


def _humanize_ago(ts: int) -> str:
    if ts == 0:
        return "никогда"
    delta = int(time.time()) - ts
    if delta < 0:
        return "только что"
    if delta < 60:
        return f"{delta} сек назад"
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    return f"{delta // 86400} дн назад"


async def list_clients() -> tuple[bool, str]:
    code, stdout, stderr = await _run_manager("list", "--json")
    if code != 0:
        return False, f"Не удалось получить список клиентов.\n{stderr or stdout}"

    data = _parse_json(stdout)
    if data is None:
        code2, stdout2, _ = await _run_manager("list")
        if code2 != 0:
            return False, "Не удалось получить список клиентов."
        return True, stdout2 or "Список пуст."

    if not isinstance(data, list) or not data:
        return True, "Клиентов пока нет."

    traffic = await _get_traffic_data()

    lines = ["👥 **Клиенты AmneziaWG:**", ""]
    for item in data:
        name = item.get("name", "—")
        ipv4 = item.get("ipv4", item.get("address", ""))
        status = item.get("status", "active")

        line = f"• `{name}` — {status}" + (f" ({ipv4})" if ipv4 else "")

        conf_path = CLIENTS_DIR / f"{name}.conf"
        if conf_path.is_file():
            pubkey = await _get_client_pubkey(conf_path)
            if pubkey and pubkey in traffic:
                t = traffic[pubkey]
                line += (
                    f"\n  ↳ ↓ {_humanize_bytes(t['rx'])}"
                    f"  ↑ {_humanize_bytes(t['tx'])}"
                    f"\n  ↳ последнее подключение: {_humanize_ago(t['handshake'])}"
                )
            elif pubkey:
                line += "\n  ↳ нет активности (ещё не подключался)"

        lines.append(line)
        lines.append("")

    return True, "\n".join(lines).rstrip()