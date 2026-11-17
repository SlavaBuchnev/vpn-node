"""Бэкенд AmneziaWG: управление пирами /etc/amnezia/amneziawg/awg0.conf.

Внешний установщик не нужен: пиры добавляются/удаляются прямым
редактированием конфига и мягким применением через `awg syncconf`
(без перезапуска интерфейса и разрыва существующих сессий).
"""

import ipaddress
import logging
import re
import time

from config import AWG_CONF, get_vpn_host
from vpn.base import Backend, ClientPayload
from vpn.common import humanize_bytes, run_cmd

logger = logging.getLogger(__name__)

_JUNK_KEYS = ("jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4")
_KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$")


class _Peer:
    """Один блок [Peer] серверного конфига."""

    def __init__(self) -> None:
        self.comments: list[str] = []
        self.names: dict[str, str] = {}    # lower -> исходное написание ключа
        self.values: dict[str, str] = {}   # lower -> значение
        self.order: list[str] = []         # ключи в порядке записи
        self.pubkey: str = ""

    def set(self, key: str, value: str) -> None:
        low = key.lower()
        if low not in self.values:
            self.order.append(low)
        self.names[low] = key
        self.values[low] = value
        if low == "publickey":
            self.pubkey = value

    def render(self) -> list[str]:
        # Комментарий внутри секции: wg-quick его допускает, а парсер
        # при перечитывании конфига надёжно связывает его с пиром.
        lines = ["[Peer]"]
        lines.extend(self.comments)
        for low in self.order:
            lines.append(f"{self.names[low]} = {self.values[low]}")
        return lines


def _parse_conf(text: str) -> tuple[list[str], dict, dict, list[_Peer]]:
    lines = text.splitlines()
    peer_idx = next(
        (i for i, l in enumerate(lines) if l.strip().lower() == "[peer]"),
        len(lines),
    )
    header = lines[:peer_idx]

    params: dict[str, str] = {}
    key_names: dict[str, str] = {}
    for line in header:
        m = _KEY_VALUE_RE.match(line)
        if m:
            params[m.group(1).lower()] = m.group(2)
            key_names[m.group(1).lower()] = m.group(1)

    peers: list[_Peer] = []
    cur: _Peer | None = None
    for line in lines[peer_idx:]:
        s = line.strip()
        if s.lower() == "[peer]":
            cur = _Peer()
            peers.append(cur)
        elif s.startswith("#") and cur is not None:
            cur.comments.append(line)
        else:
            m = _KEY_VALUE_RE.match(line)
            if m and cur is not None:
                cur.set(m.group(1), m.group(2))
    return header, params, key_names, peers


def _render_conf(header: list[str], peers: list[_Peer]) -> str:
    out = list(header)
    for peer in peers:
        out.extend(peer.render())
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _alloc_ip(params: dict, peers: list[_Peer]) -> str:
    address = params.get("address", "10.8.0.1/24").split(",")[0].strip()
    network = ipaddress.ip_network(address, strict=False)
    used = {ipaddress.ip_interface(address).ip}
    for peer in peers:
        for chunk in peer.values.get("allowedips", "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                used.add(ipaddress.ip_network(chunk, strict=False).network_address)
            except ValueError:
                continue
    for host in network.hosts():
        if host not in used:
            return str(host)
    raise RuntimeError(f"В подсети {network} закончились свободные адреса")


async def _tool() -> str | None:
    """Возвращает доступную утилиту управления (awg или wg)."""
    for candidate in ("awg", "wg"):
        code, _, _ = await run_cmd(candidate, "--version")
        if code != 127:
            return candidate
    return None


async def _gen_keypair() -> tuple[str, str] | None:
    tool = await _tool()
    if not tool:
        return None
    code, priv, _ = await run_cmd(tool, "genkey")
    if code != 0 or not priv:
        return None
    code, pub, _ = await run_cmd(tool, "pubkey", stdin=priv.encode())
    if code != 0 or not pub:
        return None
    return priv, pub


async def _pubkey(tool: str, privkey: str) -> str | None:
    code, pub, _ = await run_cmd(tool, "pubkey", stdin=privkey.encode())
    return pub if code == 0 and pub else None


async def _apply_conf(tool: str, iface: str) -> tuple[bool, str]:
    """Мягко применяет конфиг к работающему интерфейсу через syncconf."""
    code, _, err = await run_cmd(
        "bash", "-c", f"{tool} syncconf {iface} <({tool}-quick strip {iface})"
    )
    if code == 0:
        return True, ""
    return False, err or f"{tool} syncconf завершился с кодом {code}"


async def _dump_traffic(iface: str) -> dict:
    """Статистика пиров: {pubkey: {handshake, rx, tx}} из `awg show <iface> dump`."""
    for tool in ("awg", "wg"):
        code, out, _ = await run_cmd(tool, "show", iface, "dump", timeout=15)
        if code != 0 or not out:
            continue
        result: dict[str, dict] = {}
        for line in out.splitlines()[1:]:  # первая строка — сам интерфейс
            parts = line.split("\t")
            if len(parts) < 7:
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
    return {}


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


def _client_conf(params: dict, key_names: dict, priv: str, ip: str,
                 server_pub: str, host: str, port: str) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {priv}",
        f"Address = {ip}/32",
        "DNS = 1.1.1.1",
    ]
    for low in _JUNK_KEYS:
        if low in params:
            lines.append(f"{key_names[low]} = {params[low]}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {server_pub}",
        "AllowedIPs = 0.0.0.0/0",
        f"Endpoint = {host}:{port}",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(lines) + "\n"


class ConfError(Exception):
    """Конфиг сервера недоступен: не найден, нет прав, не читается."""


class AwgBackend(Backend):
    key = "awg"
    label = "AmneziaWG"

    @property
    def _iface(self) -> str:
        return AWG_CONF.stem or "awg0"

    def _load(self):
        try:
            if not AWG_CONF.is_file():
                raise ConfError(
                    f"Не найден конфиг сервера {AWG_CONF} — "
                    "сначала запустите deploy.sh."
                )
            text = AWG_CONF.read_text(encoding="utf-8")
        except ConfError:
            raise
        except OSError as exc:
            raise ConfError(f"Не удалось прочитать конфиг {AWG_CONF}: {exc}")
        return _parse_conf(text)

    def _save(self, header: list[str], peers: list[_Peer]) -> None:
        try:
            AWG_CONF.write_text(_render_conf(header, peers), encoding="utf-8")
            AWG_CONF.chmod(0o600)
        except OSError as exc:
            raise ConfError(f"Не удалось записать конфиг {AWG_CONF}: {exc}")

    async def create(self, name: str) -> tuple[bool, dict | str]:
        try:
            header, params, key_names, peers = self._load()
        except ConfError as exc:
            return False, str(exc)

        if any(f"# awg-bot: {name}" in p.comments for p in peers):
            return False, f"Клиент «{name}» уже есть в конфиге {AWG_CONF}."

        keys = await _gen_keypair()
        if not keys:
            return False, "Не найдена утилита awg/wg (пакет amneziawg-tools не установлен?)."
        priv, pub = keys

        try:
            ip = _alloc_ip(params, peers)
        except RuntimeError as exc:
            return False, str(exc)

        peer = _Peer()
        peer.comments = [f"# awg-bot: {name}"]
        peer.set("PublicKey", pub)
        peer.set("AllowedIPs", f"{ip}/32")
        peers.append(peer)
        try:
            self._save(header, peers)
        except ConfError as exc:
            return False, str(exc)

        tool = (await _tool()) or "awg"
        ok, err = await _apply_conf(tool, self._iface)
        if not ok:
            # Откатываем изменение конфига, чтобы файл соответствовал интерфейсу
            peers.remove(peer)
            try:
                self._save(header, peers)
            except ConfError as rollback_err:
                return False, (
                    f"Не удалось применить конфиг ({err}) и откатить изменение: {rollback_err}"
                )
            return False, (
                f"Не удалось применить конфиг к интерфейсу {self._iface}: {err}\n"
                "Если интерфейс ещё не запущен, выполните вручную:\n"
                f"sudo systemctl restart awg-quick@{self._iface}"
            )
        return True, {"priv": priv, "pub": pub, "ip": ip}

    async def delete(self, name: str, info: dict | None) -> tuple[bool, str]:
        try:
            header, params, key_names, peers = self._load()
        except ConfError as exc:
            return False, str(exc)

        pub = (info or {}).get("pub", "")
        peer = next((p for p in peers if p.pubkey == pub), None)
        if peer is None:
            return False, f"Пир «{name}» не найден в {AWG_CONF}."

        peers.remove(peer)
        try:
            self._save(header, peers)
        except ConfError as exc:
            return False, str(exc)

        tool = (await _tool()) or "awg"
        ok, err = await _apply_conf(tool, self._iface)
        if not ok:
            return False, (
                f"Пир удалён из конфига, но не применён к интерфейсу: {err}\n"
                f"Выполните: sudo systemctl restart awg-quick@{self._iface}"
            )
        return True, f"Клиент «{name}» удалён из AmneziaWG."

    async def payload(self, name: str, info: dict | None) -> tuple[bool, ClientPayload | str]:
        try:
            header, params, key_names, peers = self._load()
        except ConfError as exc:
            return False, str(exc)

        priv = (info or {}).get("priv")
        ip = (info or {}).get("ip")
        if not priv or not ip:
            return False, f"В хранилище нет ключей клиента «{name}»."

        tool = await _tool()
        if not tool:
            return False, "Не найдена утилита awg/wg."
        server_priv = params.get("privatekey")
        if not server_priv:
            return False, "В конфиге сервера нет PrivateKey."
        server_pub = await _pubkey(tool, server_priv)
        if not server_pub:
            return False, "Не удалось вычислить публичный ключ сервера."

        host = get_vpn_host()
        if not host:
            return False, (
                "Не удалось определить публичный адрес сервера. "
                "Задайте VPN_HOST в .env (лучше доменное имя)."
            )
        port = params.get("listenport", "443")
        text = _client_conf(params, key_names, priv, ip, server_pub, host, port)
        return True, ClientPayload(
            link=None,
            conf=(f"{name}.conf", text.encode("utf-8")),
            qr_text=text,
        )

    async def traffic(self, infos: dict[str, dict]) -> dict[str, str]:
        names = {i["pub"]: n for n, i in infos.items()
                 if i.get("type") == self.key and i.get("pub")}
        if not names:
            return {}
        data = await _dump_traffic(self._iface)
        result = {}
        for pub, name in names.items():
            t = data.get(pub)
            if not t:
                continue
            result[name] = (
                f"↓ {humanize_bytes(t['rx'])}  ↑ {humanize_bytes(t['tx'])}"
                f" (активность: {_humanize_ago(t['handshake'])})"
            )
        return result
