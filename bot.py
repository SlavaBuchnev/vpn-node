import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile, Message

import awg
from config import BOT_TOKEN, ADMIN_USER_ID, PROXY_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_bot() -> Bot:
    """Создаёт Bot с прокси, если PROXY_URL задан, иначе — с сессией по умолчанию."""
    if PROXY_URL:
        logger.info("Используется прокси: %s", PROXY_URL)
        session = AiohttpSession(proxy=PROXY_URL)
        return Bot(token=BOT_TOKEN, session=session)

    logger.info("Прокси не задан — прямое подключение к Telegram API.")
    return Bot(token=BOT_TOKEN)


bot = create_bot()
dp = Dispatcher()


# ---------- Фильтр: только администратор ----------
def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == ADMIN_USER_ID


# ---------- /create ----------
@dp.message(Command("create"))
async def cmd_create(message: Message, command: CommandObject):
    if not is_admin(message):
        return  # молча игнорируем всех остальных

    name = (command.args or "").strip()
    if not name:
        await message.answer("Использование: `/create <имя>`", parse_mode="Markdown")
        return

    if not name.replace("-", "").replace("_", "").isalnum():
        await message.answer(
            "Имя может содержать только буквы, цифры, дефис и подчёркивание."
        )
        return

    ok, result = await awg.add_client(name)
    if not ok:
        await message.answer(result)
        return

    conf_path = Path(result)
    if not conf_path.is_file():
        await message.answer(f"Клиент «{name}» создан, но файл `{conf_path}` не найден.")
        return

    try:
        await message.answer_document(
            FSInputFile(conf_path, filename=f"{name}.conf"),
            caption=f"Конфигурация для **{name}**",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Не удалось отправить файл")
        await message.answer(f"Клиент «{name}» создан, но отправить файл не удалось: {e}")


# ---------- /delete ----------
@dp.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject):
    if not is_admin(message):
        return

    name = (command.args or "").strip()
    if not name:
        await message.answer("Использование: `/delete <имя>`", parse_mode="Markdown")
        return

    ok, result = await awg.remove_client(name)
    await message.answer(result)


# ---------- /list ----------
@dp.message(Command("list"))
async def cmd_list(message: Message):
    if not is_admin(message):
        return

    ok, result = await awg.list_clients()
    await message.answer(result, parse_mode="Markdown")


# ---------- Запуск ----------
async def main():
    logger.info("Бот запущен. Администратор: %s", ADMIN_USER_ID)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())