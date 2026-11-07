import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
if not ADMIN_USER_ID:
    raise RuntimeError("ADMIN_USER_ID не задан в .env")

# Прокси опционален: пустая строка или отсутствие = работаем напрямую.
# Поддерживаются SOCKS5 и HTTP. MTProto-прокси (TeleConnect и т.п.) НЕ работают.
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

# Пути по умолчанию для bivlked/amneziawg-installer.
# Меняйте только если ставили установщик в нестандартное место.
AWG_DIR = Path("/root/awg")
AWG_MANAGER = AWG_DIR / "manage_amneziawg.sh"
CLIENTS_DIR = AWG_DIR  # клиентские .conf лежат прямо в /root/awg/