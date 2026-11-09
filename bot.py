import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile, Message, TelegramObject

import awg
from config import BOT_TOKEN, ADMIN_USER_ID, PROXY_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# Трекеры для автоочистки: chat_id -> [message_id, ...] / message_id
_sent_messages: Dict[int, list[int]] = {}
_last_commands: Dict[int, int] = {}
_chat_locks: Dict[int, asyncio.Lock] = {}


class TrackingBot(Bot):
    """Bot, который запоминает ID своих сообщений для последующей очистки."""

    async def send_message(self, *args, **kwargs):
        msg = await super().send_message(*args, **kwargs)
        _sent_messages.setdefault(msg.chat.id, []).append(msg.message_id)
        return msg

    async def send_document(self, *args, **kwargs):
        msg = await super().send_document(*args, **kwargs)
        _sent_messages.setdefault(msg.chat.id, []).append(msg.message_id)
        return msg


def create_bot() -> Bot:
    if PROXY_URL:
        logger.info("Используется прокси: %s", PROXY_URL)
        session = AiohttpSession(proxy=PROXY_URL)
        return TrackingBot(token=BOT_TOKEN, session=session)

    logger.info("Прокси не задан — прямое подключение к Telegram API.")
    return TrackingBot(token=BOT_TOKEN)


bot = create_bot()
dp = Dispatcher()


class CleanupMiddleware(BaseMiddleware):
    """Перед новой командой удаляет предыдущую команду и все ответы бота."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        if event.from_user is None or event.from_user.id != ADMIN_USER_ID:
            return await handler(event, data)
        if not (event.text or "").startswith("/"):
            return await handler(event, data)

        chat_id = event.chat.id
        lock = _chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            # Удаляем ответы бота на предыдущую команду
            for mid in _sent_messages.pop(chat_id, []):
                try:
                    await event.bot.delete_message(chat_id=chat_id, message_id=mid)
                except Exception as e:
                    logger.debug("Не удалось удалить сообщение %s: %s", mid, e)

            # Удаляем саму предыдущую команду
            prev_cmd = _last_commands.pop(chat_id, None)
            if prev_cmd:
                try:
                    await event.bot.delete_message(chat_id=chat_id, message_id=prev_cmd)
                except Exception as e:
                    logger.debug("Не удалось удалить команду %s: %s", prev_cmd, e)

            # Запоминаем текущую команду
            _last_commands[chat_id] = event.message_id

            return await handler(event, data)


dp.message.outer_middleware(CleanupMiddleware())


def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == ADMIN_USER_ID


@dp.message(Command("create"))
async def cmd_create(message: Message, command: CommandObject):
    if not is_admin(message):
        return

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


@dp.message(Command("get"))
async def cmd_get(message: Message, command: CommandObject):
    if not is_admin(message):
        return

    name = (command.args or "").strip()
    if not name:
        await message.answer("Использование: `/get <имя>`", parse_mode="Markdown")
        return

    ok, result = await awg.get_client(name)
    if not ok:
        await message.answer(result)
        return

    conf_path = Path(result)
    try:
        await message.answer_document(
            FSInputFile(conf_path, filename=f"{name}.conf"),
            caption=f"Конфигурация для **{name}**",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Не удалось отправить файл")
        await message.answer(f"Не удалось отправить файл: {e}")


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


@dp.message(Command("list"))
async def cmd_list(message: Message):
    if not is_admin(message):
        return

    ok, result = await awg.list_clients()
    await message.answer(result, parse_mode="Markdown")


async def main():
    logger.info("Бот запущен. Администратор: %s", ADMIN_USER_ID)
    # handle_as_tasks=False гарантирует последовательную обработку,
    # чтобы автоочистка не гонялась за своими же сообщениями
    await dp.start_polling(bot, handle_as_tasks=False)


if __name__ == "__main__":
    asyncio.run(main())