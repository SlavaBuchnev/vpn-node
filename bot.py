"""Telegram-бот управления VPN-клиентами (AmneziaWG / VLESS / Shadowsocks).

Однопользовательский: отвечает только пользователю с ADMIN_USER_ID,
посторонние сообщения и нажатия молча игнорируются.

Интерфейс — экранная модель: одно «главное» сообщение бота превращается
в экраны (меню, список клиентов, карточка клиента, состояние сервера)
с inline-кнопками; конфиги и QR приходят отдельными сообщениями.

Автоочистка чата: при каждом новом сообщении администратора удаляются
все предыдущие сообщения чата (команды пользователя и ответы бота) —
в чате остаётся только последний диалог.
"""

import asyncio
import io
import logging
import math
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict

import qrcode
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message,
                           TelegramObject)

import storage
from config import (ADMIN_USER_ID, BOT_TOKEN, PROXY_URL, get_vpn_host,
                    load_server_params)
from vpn import BACKENDS, TYPES_MENU
from vpn.common import run_cmd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
LIST_PAGE_SIZE = 8
AWG_SERVICE = "awg-quick@awg0"

# Эмодзи и описания протоколов для экранов
TYPE_EMOJI = {"awg": "🛡", "vless": "🚀", "ss": "⚡"}
TYPE_DESCRIPTIONS = {
    "awg": "файл `.conf` + QR, приложение AmneziaWG",
    "vless": "ссылка `vless://` + QR, маскировка под TLS — лучше всего против блокировок",
    "ss": "ссылка `ss://` + QR, лёгкий и быстрый",
}

# chat_id -> id сообщений (пользователя и бота), подлежащих удалению
_chat_messages: Dict[int, set[int]] = {}
_chat_locks: Dict[int, asyncio.Lock] = {}


class TrackingBot(Bot):
    """Бот, запоминающий ID отправленных сообщений для автоочистки чата."""

    async def _track(self, message: Message) -> Message:
        _chat_messages.setdefault(message.chat.id, set()).add(message.message_id)
        return message

    async def send_message(self, *args, **kwargs):
        return await self._track(await super().send_message(*args, **kwargs))

    async def send_document(self, *args, **kwargs):
        return await self._track(await super().send_document(*args, **kwargs))

    async def send_photo(self, *args, **kwargs):
        return await self._track(await super().send_photo(*args, **kwargs))


def create_bot() -> Bot:
    if PROXY_URL:
        logger.info("Используется прокси: %s", PROXY_URL)
        return TrackingBot(token=BOT_TOKEN, session=AiohttpSession(proxy=PROXY_URL))
    logger.info("Прокси не задан — прямое подключение к Telegram API.")
    return TrackingBot(token=BOT_TOKEN)


bot = create_bot()
dp = Dispatcher()


# ---------- middleware ----------

class AdminMiddleware(BaseMiddleware):
    """Пропускает только ADMIN_USER_ID; посторонним бот молчит."""

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.id != ADMIN_USER_ID:
            return None
        return await handler(event, data)


class CleanupMiddleware(BaseMiddleware):
    """При новом сообщении админа удаляет все предыдущие сообщения чата."""

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        chat_id = event.chat.id
        lock = _chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            old = _chat_messages.pop(chat_id, set())
            for mid in old:
                if mid == event.message_id:
                    continue
                try:
                    await event.bot.delete_message(chat_id=chat_id, message_id=mid)
                except Exception as exc:
                    logger.debug("Не удалось удалить сообщение %s: %s", mid, exc)
            _chat_messages.setdefault(chat_id, set()).add(event.message_id)
        return await handler(event, data)


dp.message.outer_middleware(AdminMiddleware())
dp.callback_query.outer_middleware(AdminMiddleware())
dp.message.outer_middleware(CleanupMiddleware())


# ---------- вспомогательные функции ----------

def make_qr_png(text: str) -> bytes:
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def fmt_created(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


async def safe_edit(message: Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def service_active(name: str) -> bool:
    code, _, _ = await run_cmd("systemctl", "is-active", "--quiet", name, timeout=10)
    return code == 0


def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Меню", callback_data="m:menu")],
    ])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="m:cancel")],
    ])


# ---------- экраны ----------

async def screen_main() -> tuple[str, InlineKeyboardMarkup]:
    host = get_vpn_host() or "не определён"
    count = len(await storage.all_clients())
    text = (
        "🏠 **VPN Node**\n\n"
        f"🌐 Адрес сервера: `{host}`\n"
        f"👥 Клиентов: {count}\n\n"
        "Выбирай раздел:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать клиента", callback_data="m:create")],
        [InlineKeyboardButton(text=f"📋 Клиенты ({count})", callback_data="m:clients")],
        [InlineKeyboardButton(text="🖥 Сервер", callback_data="m:server")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="m:help")],
    ])
    return text, kb


async def screen_list(page: int) -> tuple[str, InlineKeyboardMarkup]:
    clients = await storage.all_clients()
    names = sorted(clients)
    total = len(names)
    pages = max(1, math.ceil(total / LIST_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    shown = names[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]

    traffics: dict[str, str] = {}
    for backend in BACKENDS.values():
        try:
            traffics.update(await backend.traffic(clients))
        except Exception:
            logger.exception("Ошибка сбора статистики %s", backend.key)

    lines = [f"📋 **Клиенты ({total})**", ""]
    rows = []
    for name in shown:
        info = clients[name]
        backend = BACKENDS.get(info.get("type"))
        label = backend.label if backend else str(info.get("type"))
        emoji = TYPE_EMOJI.get(info.get("type"), "👤")
        lines.append(f"{emoji} **{name}** — {label}")
        traffic = traffics.get(name)
        if traffic:
            lines.append(f"   ↳ {traffic}")
        rows.append([
            InlineKeyboardButton(text=f"{emoji} {name}", callback_data=f"c:{name}"),
            InlineKeyboardButton(text="🗑", callback_data=f"d:{name}"),
        ])
    if total == 0:
        lines.append("Пусто. Создай первого клиента кнопкой ниже.")
        rows.append([InlineKeyboardButton(
            text="➕ Создать клиента", callback_data="m:create")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pg:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}",
                                        callback_data="m:noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pg:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="m:menu")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def screen_client(name: str) -> tuple[str, InlineKeyboardMarkup]:
    info = await storage.get(name)
    if info is None:
        text, kb = await screen_list(0)
        return "Клиент не найден (возможно, уже удалён).\n\n" + text, kb

    backend = BACKENDS.get(info.get("type"))
    label = backend.label if backend else str(info.get("type"))
    emoji = TYPE_EMOJI.get(info.get("type"), "👤")
    lines = [f"{emoji} **{name}**", "", f"Тип: {label}"]
    created = info.get("created")
    if created:
        lines.append(f"Создан: {fmt_created(created)}")
    if backend is not None:
        try:
            traffics = await backend.traffic({name: info})
        except Exception:
            logger.exception("Ошибка статистики %s", name)
            traffics = {}
        traffic = traffics.get(name)
        if traffic:
            lines.append(f"Трафик: {traffic}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📤 Получить конфиг", callback_data=f"g:{name}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"d:{name}")],
        [InlineKeyboardButton(text="↩️ К списку", callback_data="m:clients"),
         InlineKeyboardButton(text="🏠 Меню", callback_data="m:menu")],
    ])
    return "\n".join(lines), kb


def screen_delete(name: str) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"🗑 Удалить клиента **{name}**?\n\n"
        "Доступ с его конфигами прекратится сразу."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"dy:{name}"),
        InlineKeyboardButton(text="↩️ Назад", callback_data=f"c:{name}"),
    ]])
    return text, kb


def screen_type(name: str) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "➕ **Новый клиент**", "",
        f"Имя: `{name}`", "",
        "Выбери протокол:",
    ]
    for key, label in TYPES_MENU:
        lines.append(f"{TYPE_EMOJI[key]} **{label}** — {TYPE_DESCRIPTIONS[key]}")
    rows = [[InlineKeyboardButton(
        text=f"{TYPE_EMOJI[key]} {label}", callback_data=f"t:{key}")]
        for key, label in TYPES_MENU]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="m:cancel")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def screen_server() -> tuple[str, InlineKeyboardMarkup]:
    host = get_vpn_host() or "не определён"
    params = load_server_params()
    xport = params.get("xray", {}).get("port", 443)
    aport = params.get("awg", {}).get("port", 443)
    sport = params.get("ss", {}).get("port", 1443)
    try:
        x_ok, a_ok, s_ok = await asyncio.gather(
            service_active("xray"),
            service_active(AWG_SERVICE),
            service_active("shadowsocks-rust"),
        )
    except Exception:
        logger.exception("Ошибка опроса сервисов")
        x_ok = a_ok = s_ok = False

    def dot(ok: bool) -> str:
        return "🟢" if ok else "🔴"

    text = (
        "🖥 **Сервер**\n\n"
        f"🌐 Адрес: `{host}`\n\n"
        f"{dot(x_ok)} VLESS + Reality — TCP {xport}\n"
        f"{dot(a_ok)} AmneziaWG — UDP {aport}\n"
        f"{dot(s_ok)} Shadowsocks — TCP/UDP {sport}\n\n"
        "🔴 — смотри `journalctl -u <сервис> -n 30`"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="srv:refresh")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="m:menu")],
    ])
    return text, kb


HELP_TEXT = (
    "❓ **Помощь**\n\n"
    "**Экраны** (всё управление кнопками):\n"
    "➕ Создать клиента → ввод имени → выбор протокола\n"
    "📋 Клиенты — список, карточка клиента, удаление\n"
    "🖥 Сервер — адрес и статус трёх VPN-сервисов\n\n"
    "**Команды** (для быстрого доступа):\n"
    "/create <имя> [awg|vless|ss] — создать\n"
    "/get <имя> — сразу получить конфиг\n"
    "/delete <имя> — удалить\n"
    "/list — список\n"
    "/cancel — прервать ввод\n\n"
    "**Протоколы**\n"
    "🚀 VLESS + Reality — TCP 443, маскировка под TLS, самый устойчивый\n"
    "🛡 AmneziaWG — UDP 443, обфускация AWG 2.0, `.conf` + QR\n"
    "⚡ Shadowsocks 2022 — TCP/UDP 1443, `ss://` + QR\n\n"
    "Все предыдущие сообщения чата удаляются при новом сообщении."
)


# ---------- действия ----------

async def deliver(chat_id: int, name: str) -> None:
    """Отправляет клиенту ссылку/конфиг/QR в зависимости от типа VPN."""
    info = await storage.get(name)
    if info is None:
        await bot.send_message(chat_id, f"Клиент «{name}» не найден.")
        return
    backend = BACKENDS.get(info.get("type"))
    if backend is None:
        await bot.send_message(chat_id, f"Неизвестный тип VPN для «{name}».")
        return
    try:
        ok, result = await backend.payload(name, info)
    except Exception:
        logger.exception("Ошибка получения конфигурации %s", name)
        await bot.send_message(chat_id, "Внутренняя ошибка при получении конфигурации.")
        return
    if not ok:
        await bot.send_message(chat_id, result)
        return

    header = f"**{name}** · {backend.label}"
    if result.link:
        await bot.send_message(
            chat_id, f"{header}\n\n🔗 `{result.link}`", parse_mode="Markdown"
        )
    if result.conf:
        filename, data = result.conf
        await bot.send_document(
            chat_id, BufferedInputFile(data, filename=filename),
            caption=header, parse_mode="Markdown",
        )
    if result.qr_text:
        await bot.send_photo(
            chat_id,
            BufferedInputFile(make_qr_png(result.qr_text), filename=f"{name}.png"),
            caption=f"QR-код · **{name}**", parse_mode="Markdown",
        )


async def do_create(name: str, type_key: str) -> tuple[bool, str]:
    """Создаёт клиента во всех слоях. Возвращает (ok, сообщение об ошибке)."""
    backend = BACKENDS[type_key]
    if await storage.get(name) is not None:
        return False, f"Клиент «{name}» уже существует."
    try:
        ok, result = await backend.create(name)
    except Exception:
        logger.exception("Ошибка создания клиента %s", name)
        return False, "Внутренняя ошибка при создании клиента."
    if not ok:
        return False, result
    await storage.add(name, {"type": type_key, **result})
    return True, ""


async def delete_client(name: str) -> str:
    info = await storage.get(name)
    if info is None:
        return f"Клиент «{name}» не найден."
    backend = BACKENDS.get(info.get("type"))
    if backend is None:
        await storage.remove(name)
        return f"Клиент «{name}» удалён из хранилища (неизвестный тип VPN)."
    try:
        ok, msg = await backend.delete(name, info)
    except Exception:
        logger.exception("Ошибка удаления клиента %s", name)
        return "Внутренняя ошибка при удалении клиента."
    if not ok:
        return msg
    await storage.remove(name)
    return f"Клиент «{name}» удалён."


# ---------- FSM ----------

class CreateFlow(StatesGroup):
    waiting_name = State()
    waiting_type = State()


NAME_PROMPT = (
    "➕ **Новый клиент**\n\n"
    "Введи имя (латиница, цифры, `-` и `_`, до 32 символов):"
)


# ---------- команды ----------

@dp.message(Command("start", "menu"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await screen_main()
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)


@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, parse_mode="Markdown", reply_markup=menu_kb())


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await screen_main()
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)


@dp.message(Command("create"))
async def cmd_create(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    args = (command.args or "").split()
    if not args:
        await state.set_state(CreateFlow.waiting_name)
        prompt = await message.answer(NAME_PROMPT, parse_mode="Markdown",
                                      reply_markup=cancel_kb())
        await state.update_data(prompt_id=prompt.message_id)
        return

    name = args[0]
    type_key = args[1].lower() if len(args) > 1 else "awg"
    if not NAME_RE.match(name):
        await message.answer(
            "Имя может содержать только латиницу, цифры, дефис "
            "и подчёркивание (до 32 символов)."
        )
        return
    if type_key not in BACKENDS:
        await message.answer(
            f"Неизвестный тип VPN: `{type_key}`. Доступно: "
            + ", ".join(f"`{k}`" for k in BACKENDS),
            parse_mode="Markdown",
        )
        return

    ok, err = await do_create(name, type_key)
    if not ok:
        await message.answer(err, reply_markup=menu_kb())
        return
    await deliver(message.chat.id, name)
    text, kb = await screen_client(name)
    await message.answer("✅ Клиент создан.\n\n" + text,
                         parse_mode="Markdown", reply_markup=kb)


@dp.message(Command("get"))
async def cmd_get(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    name = (command.args or "").strip()
    if not name:
        await message.answer("Использование: `/get <имя>`", parse_mode="Markdown")
        return
    await deliver(message.chat.id, name)


@dp.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    name = (command.args or "").strip()
    if not name:
        await message.answer("Использование: `/delete <имя>`", parse_mode="Markdown")
        return
    await message.answer(await delete_client(name), reply_markup=menu_kb())


@dp.message(Command("list"))
async def cmd_list(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await screen_list(0)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)


# ---------- FSM-хендлеры ----------

@dp.message(StateFilter(CreateFlow.waiting_name))
async def create_name_entered(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not NAME_RE.match(name):
        await message.answer(
            "Недопустимое имя. Попробуй ещё раз "
            "(латиница, цифры, `-` и `_`, до 32 символов):"
        )
        return
    if await storage.get(name) is not None:
        await message.answer(f"Клиент «{name}» уже существует. Введи другое имя:")
        return

    data = await state.get_data()
    await state.update_data(name=name)
    await state.set_state(CreateFlow.waiting_type)

    text, kb = screen_type(name)
    prompt_id = data.get("prompt_id")
    if prompt_id:
        try:
            await bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=prompt_id,
                parse_mode="Markdown", reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)


@dp.message(StateFilter(CreateFlow.waiting_type))
async def create_type_waiting(message: Message, state: FSMContext):
    await message.answer("Выбери протокол кнопкой выше или отмени ввод (/cancel).")


# ---------- callback-и ----------

@dp.callback_query()
async def process_callback(cq: CallbackQuery, state: FSMContext):
    data = cq.data or ""
    chat_id = cq.message.chat.id
    toast: str | None = None

    try:
        if data == "m:menu":
            await state.clear()
            text, kb = await screen_main()
            await safe_edit(cq.message, text, parse_mode="Markdown", reply_markup=kb)

        elif data == "m:create":
            await state.set_state(CreateFlow.waiting_name)
            prompt = await cq.message.edit_text(
                NAME_PROMPT, parse_mode="Markdown", reply_markup=cancel_kb()
            )
            await state.update_data(prompt_id=prompt.message_id)

        elif data == "m:clients":
            await state.clear()
            text, kb = await screen_list(0)
            await safe_edit(cq.message, text, parse_mode="Markdown", reply_markup=kb)

        elif data == "m:server" or data == "srv:refresh":
            await state.clear()
            text, kb = await screen_server()
            await safe_edit(cq.message, text, parse_mode="Markdown", reply_markup=kb)
            toast = "Обновлено" if data == "srv:refresh" else None

        elif data == "m:help":
            await state.clear()
            await safe_edit(cq.message, HELP_TEXT,
                            parse_mode="Markdown", reply_markup=menu_kb())

        elif data in ("m:cancel", "m:noop"):
            await state.clear()
            if data == "m:cancel":
                text, kb = await screen_main()
                await safe_edit(cq.message, text,
                                parse_mode="Markdown", reply_markup=kb)

        elif data.startswith("pg:"):
            await state.clear()
            text, kb = await screen_list(int(data[3:]))
            await safe_edit(cq.message, text, parse_mode="Markdown", reply_markup=kb)

        elif data.startswith("t:"):
            type_key = data[2:]
            state_data = await state.get_data()
            name = state_data.get("name")
            await state.clear()
            if not name or type_key not in BACKENDS:
                text, kb = await screen_main()
                await safe_edit(cq.message, text,
                                parse_mode="Markdown", reply_markup=kb)
            else:
                ok, err = await do_create(name, type_key)
                if not ok:
                    await safe_edit(cq.message, err, reply_markup=menu_kb())
                else:
                    await deliver(chat_id, name)
                    text, kb = await screen_client(name)
                    await safe_edit(cq.message, "✅ Клиент создан.\n\n" + text,
                                    parse_mode="Markdown", reply_markup=kb)

        elif data.startswith("c:"):
            await state.clear()
            text, kb = await screen_client(data[2:])
            await safe_edit(cq.message, text, parse_mode="Markdown", reply_markup=kb)

        elif data.startswith("g:"):
            await deliver(chat_id, data[2:])
            toast = "Отправлено"

        elif data.startswith("d:"):
            text, kb = screen_delete(data[2:])
            await safe_edit(cq.message, text, parse_mode="Markdown", reply_markup=kb)

        elif data.startswith("dy:"):
            result = await delete_client(data[3:])
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Клиенты", callback_data="m:clients")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="m:menu")],
            ])
            await safe_edit(cq.message, result, reply_markup=kb)

        elif data.startswith("dn:"):
            text, kb = await screen_client(data[3:])
            await safe_edit(cq.message, text, parse_mode="Markdown", reply_markup=kb)

    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            pass
        else:
            logger.warning("TelegramBadRequest в callback %s: %s", data, exc)
    except Exception:
        logger.exception("Ошибка обработки callback %s", data)
        try:
            await safe_edit(cq.message, "Произошла ошибка, открой меню.",
                            reply_markup=menu_kb())
        except Exception:
            pass

    await cq.answer(toast)


# ---------- заглушка ----------

@dp.message()
async def fallback(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await screen_main()
    await message.answer(
        "Неизвестная команда — управляй кнопками.\n\n" + text,
        parse_mode="Markdown", reply_markup=kb,
    )


async def main():
    logger.info("Бот запущен. Администратор: %s", ADMIN_USER_ID)
    await dp.start_polling(bot, handle_as_tasks=False)


if __name__ == "__main__":
    asyncio.run(main())
