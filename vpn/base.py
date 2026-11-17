"""Общий интерфейс VPN-бэкендов."""

from typing import NamedTuple


class ClientPayload(NamedTuple):
    """Данные для выдачи клиенту: ссылка, файл конфигурации, текст для QR."""
    link: str | None                # vless:// или ss:// (None для AmneziaWG)
    conf: tuple[str, bytes] | None  # (имя файла, содержимое) — только AmneziaWG
    qr_text: str | None             # что зашивать в QR-код


class Backend:
    """Базовый класс бэкенда VPN.

    create() при успехе возвращает словарь полей для хранилища,
    при ошибке — строку с сообщением. delete()/payload() получают
    запись клиента из хранилища.
    """

    key: str = ""    # короткое имя: awg / vless / ss
    label: str = ""  # подпись для меню

    async def create(self, name: str) -> tuple[bool, dict | str]:
        raise NotImplementedError

    async def delete(self, name: str, info: dict | None) -> tuple[bool, str]:
        raise NotImplementedError

    async def payload(self, name: str, info: dict | None) -> tuple[bool, ClientPayload | str]:
        """Собирает данные для выдачи клиенту (ссылку/конфиг/QR)."""
        raise NotImplementedError

    async def traffic(self, infos: dict[str, dict]) -> dict[str, str]:
        """Возвращает {имя: человекочитаемый трафик} для /list."""
        return {}
