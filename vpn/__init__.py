"""Реестр доступных VPN-бэкендов."""

from vpn.awg import AwgBackend
from vpn.shadowsocks import ShadowsocksBackend
from vpn.vless import VlessBackend

_BACKENDS_LIST = (AwgBackend(), VlessBackend(), ShadowsocksBackend())

BACKENDS = {b.key: b for b in _BACKENDS_LIST}

# Порядок и подписи для меню выбора типа VPN
TYPES_MENU = [(b.key, b.label) for b in _BACKENDS_LIST]
