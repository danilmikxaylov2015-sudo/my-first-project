# bot.py
# Python 3.10+
# Вставьте токен в BOT_TOKEN и запустите: python3 bot.py

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = "8975361055:AAET6brDJIAonm58z-2CNCHG-1WEMuC0Rmc"
SUPPORT_USERNAME = "HET_HOMEPA1"  # без @
VIP_PRICE_STARS = 60

BASE_DIR = Path(__file__).resolve().parent
DATABASE_FILE = BASE_DIR / "anonymous_chat.sqlite3"


# =========================
# АВТОУСТАНОВКА БИБЛИОТЕК
# =========================

def install_package(package: str) -> None:
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            package,
        ]
    )


required_packages = {
    "aiogram": "aiogram>=3.20,<4.0",
    "aiosqlite": "aiosqlite>=0.20,<1.0",
}

for module_name, package_name in required_packages.items():
    if importlib.util.find_spec(module_name) is None:
        print(f"Устанавливаю {package_name}...", flush=True)
        install_package(package_name)


# =========================
# ИМПОРТЫ ПОСЛЕ УСТАНОВКИ
# =========================

import asyncio
import html
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ContentType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)


router = Router()
dispatcher = Dispatcher()
dispatcher.include_router(router)

pair_lock = asyncio.Lock()


# =========================
# КНОПКИ
# =========================

BTN_SEARCH = "🔎 Найти собеседника"
BTN_NEXT = "⏭ Следующий"
BTN_STOP = "🛑 Завершить"
BTN_REVEAL = "👤 Узнать кто"
BTN_VIP = "💎 Купить VIP — 60 ⭐"
BTN_HELP = "❓ Помощь"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_SEARCH),
                KeyboardButton(text=BTN_NEXT),
            ],
            [
                KeyboardButton(text=BTN_STOP),
                KeyboardButton(text=BTN_REVEAL),
            ],
            [
                KeyboardButton(text=BTN_VIP),
                KeyboardButton(text=BTN_HELP),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напишите сообщение...",
    )


def reveal_keyboard(requester_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Показать профили друг другу",
                    callback_data=f"reveal_accept:{requester_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отказать",
                    callback_data=f"reveal_decline:{requester_id}",
                )
            ],
        ]
    )


def invoice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплатить 60 ⭐",
                    pay=True,
                )
            ]
        ]
    )


# =========================
# БАЗА ДАННЫХ
# =========================

@asynccontextmanager
async def db() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(DATABASE_FILE)
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA journal_mode=WAL")
    await connection.execute("PRAGMA busy_timeout=5000")

    try:
        yield connection
    finally:
        await connection.close()


async def init_db() -> None:
    async with db() as connection:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT NOT NULL,
                is_vip INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS queue (
                user_id INTEGER PRIMARY KEY,
                joined_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pairs (
                user_id INTEGER PRIMARY KEY,
                partner_id INTEGER NOT NULL,
                paired_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reveal_requests (
                requester_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                PRIMARY KEY (requester_id, target_id)
            );

            CREATE TABLE IF NOT EXISTS payments (
                telegram_charge_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )

        # После перезапуска очередь очищается,
        # чтобы не соединять пользователя, который уже вышел из бота.
        await connection.execute("DELETE FROM queue")
        await connection.commit()


async def save_user(user) -> None:
    now = int(time.time())

    async with db() as connection:
        await connection.execute(
            """
            INSERT INTO users (
                user_id,
                username,
                first_name,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                updated_at = excluded.updated_at
            """,
            (
                user.id,
                user.username,
                user.first_name or "Пользователь",
                now,
                now,
            ),
        )
        await connection.commit()


async def is_vip(user_id: int) -> bool:
    async with db() as connection:
        cursor = await connection.execute(
            "SELECT is_vip FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

    return bool(row and row["is_vip"])


async def get_partner(user_id: int) -> int | None:
    async with db() as connection:
        cursor = await connection.execute(
            "SELECT partner_id FROM pairs WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

    return int(row["partner_id"]) if row else None


async def find_partner_or_add_queue(user_id: int) -> int | None:
    now = int(time.time())

    async with pair_lock:
        async with db() as connection:
            await connection.execute("BEGIN IMMEDIATE")

            cursor = await connection.execute(
                "SELECT partner_id FROM pairs WHERE user_id = ?",
                (user_id,),
            )

            if await cursor.fetchone():
                await connection.rollback()
                return None

            await connection.execute(
                "DELETE FROM queue WHERE user_id = ?",
                (user_id,),
            )

            cursor = await connection.execute(
                """
                SELECT q.user_id
                FROM queue q
                LEFT JOIN pairs p ON p.user_id = q.user_id
                WHERE q.user_id != ?
                  AND p.user_id IS NULL
                ORDER BY q.joined_at
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                await connection.execute(
                    "INSERT OR REPLACE INTO queue (user_id, joined_at) VALUES (?, ?)",
                    (user_id, now),
                )
                await connection.commit()
                return None

            partner_id = int(row["user_id"])

            await connection.execute(
                "DELETE FROM queue WHERE user_id IN (?, ?)",
                (user_id, partner_id),
            )

            await connection.execute(
                """
                INSERT OR REPLACE INTO pairs (user_id, partner_id, paired_at)
                VALUES (?, ?, ?)
                """,
                (user_id, partner_id, now),
            )

            await connection.execute(
                """
                INSERT OR REPLACE INTO pairs (user_id, partner_id, paired_at)
                VALUES (?, ?, ?)
                """,
                (partner_id, user_id, now),
            )

            await connection.commit()
            return partner_id


async def stop_dialog(user_id: int) -> int | None:
    async with pair_lock:
        async with db() as connection:
            await connection.execute("BEGIN IMMEDIATE")

            cursor = await connection.execute(
                "SELECT partner_id FROM pairs WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()

            partner_id = int(row["partner_id"]) if row else None

            await connection.execute(
                "DELETE FROM queue WHERE user_id = ?",
                (user_id,),
            )

            if partner_id is not None:
                await connection.execute(
                    "DELETE FROM pairs WHERE user_id IN (?, ?)",
                    (user_id, partner_id),
                )

                await connection.execute(
                    """
                    DELETE FROM reveal_requests
                    WHERE requester_id IN (?, ?)
                       OR target_id IN (?, ?)
                    """,
                    (
                        user_id,
                        partner_id,
                        user_id,
                        partner_id,
                    ),
                )

            await connection.commit()
            return partner_id


async def create_reveal_request(
    requester_id: int,
    target_id: int,
) -> None:
    async with db() as connection:
        await connection.execute(
            """
            INSERT INTO reveal_requests (
                requester_id,
                target_id,
                status,
                created_at
            )
            VALUES (?, ?, 'pending', ?)
            ON CONFLICT(requester_id, target_id) DO UPDATE SET
                status = 'pending',
                created_at = excluded.created_at
            """,
            (
                requester_id,
                target_id,
                int(time.time()),
            ),
        )
        await connection.commit()


async def update_reveal_request(
    requester_id: int,
    target_id: int,
    status: str,
) -> bool:
    async with db() as connection:
        await connection.execute("BEGIN IMMEDIATE")

        cursor = await connection.execute(
            """
            SELECT status
            FROM reveal_requests
            WHERE requester_id = ?
              AND target_id = ?
            """,
            (
                requester_id,
                target_id,
            ),
        )
        row = await cursor.fetchone()

        if row is None or row["status"] != "pending":
            await connection.rollback()
            return False

        await connection.execute(
            """
            UPDATE reveal_requests
            SET status = ?
            WHERE requester_id = ?
              AND target_id = ?
            """,
            (
                status,
                requester_id,
                target_id,
            ),
        )

        await connection.commit()
        return True


async def get_profile(user_id: int):
    async with db() as connection:
        cursor = await connection.execute(
            """
            SELECT user_id, username, first_name
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )
        return await cursor.fetchone()


async def activate_vip(
    user_id: int,
    charge_id: str,
    amount: int,
    currency: str,
) -> None:
    async with db() as connection:
        await connection.execute("BEGIN IMMEDIATE")

        await connection.execute(
            """
            INSERT OR IGNORE INTO payments (
                telegram_charge_id,
                user_id,
                amount,
                currency,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                charge_id,
                user_id,
                amount,
                currency,
                int(time.time()),
            ),
        )

        await connection.execute(
            """
            UPDATE users
            SET is_vip = 1,
                updated_at = ?
            WHERE user_id = ?
            """,
            (
                int(time.time()),
                user_id,
            ),
        )

        await connection.commit()


# =========================
# СЛУЖЕБНЫЕ ФУНКЦИИ
# =========================

async def safe_send(
    bot: Bot,
    chat_id: int,
    text: str,
    **kwargs,
) -> bool:
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


def profile_text(profile) -> str:
    name = html.escape(profile["first_name"])
    user_id = int(profile["user_id"])
    username = profile["username"]

    text = f'<a href="tg://user?id={user_id}">{name}</a>'

    if username:
        text += f"\n@{html.escape(username)}"
    else:
        text += "\nUsername не установлен."

    return text


async def search_user(message: Message) -> None:
    user_id = message.from_user.id

    current_partner = await get_partner(user_id)

    if current_partner is not None:
        await message.answer(
            "Вы уже общаетесь с собеседником.",
            reply_markup=main_keyboard(),
        )
        return

    partner_id = await find_partner_or_add_queue(user_id)

    if partner_id is None:
        await message.answer(
            "🔎 Ищу собеседника...",
            reply_markup=main_keyboard(),
        )
        return

    text = (
        "✅ <b>Собеседник найден!</b>\n\n"
        "Теперь отправьте сообщение.\n"
        "Имя и username не показываются."
    )

    await message.answer(
        text,
        reply_markup=main_keyboard(),
    )

    delivered = await safe_send(
        message.bot,
        partner_id,
        text,
        reply_markup=main_keyboard(),
    )

    if not delivered:
        await stop_dialog(user_id)
        await message.answer(
            "Собеседник недоступен. Нажмите поиск ещё раз."
        )


# =========================
# КОМАНДЫ
# =========================

@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await save_user(message.from_user)

    vip_status = (
        "активен ✅"
        if await is_vip(message.from_user.id)
        else "не активен"
    )

    await message.answer(
        "👋 <b>Анонимный чат</b>\n\n"
        "Нажмите кнопку поиска, и бот соединит вас "
        "со случайным пользователем.\n\n"
        f"VIP: <b>{vip_status}</b>\n\n"
        "VIP стоит 60 Telegram Stars и позволяет отправить "
        "собеседнику запрос на взаимное открытие профилей.\n"
        "Без согласия собеседника профиль не раскрывается.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def help_handler(message: Message) -> None:
    await save_user(message.from_user)

    await message.answer(
        "<b>Кнопки</b>\n\n"
        "🔎 Найти собеседника — начать поиск.\n"
        "⏭ Следующий — сменить собеседника.\n"
        "🛑 Завершить — закончить диалог.\n"
        "👤 Узнать кто — VIP-запрос на открытие профилей.\n"
        "💎 Купить VIP — бессрочный VIP за 60 ⭐.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("paysupport"))
async def paysupport_handler(message: Message) -> None:
    await message.answer(
        f"Поддержка по оплате: @{html.escape(SUPPORT_USERNAME)}"
    )


# =========================
# ПОИСК И ДИАЛОГ
# =========================

@router.message(F.text == BTN_SEARCH)
async def search_handler(message: Message) -> None:
    await save_user(message.from_user)
    await search_user(message)


@router.message(F.text == BTN_STOP)
async def stop_handler(message: Message) -> None:
    await save_user(message.from_user)

    partner_id = await stop_dialog(message.from_user.id)

    await message.answer(
        "🛑 Диалог или поиск завершён.",
        reply_markup=main_keyboard(),
    )

    if partner_id is not None:
        await safe_send(
            message.bot,
            partner_id,
            "Собеседник завершил диалог.",
            reply_markup=main_keyboard(),
        )


@router.message(F.text == BTN_NEXT)
async def next_handler(message: Message) -> None:
    await save_user(message.from_user)

    partner_id = await stop_dialog(message.from_user.id)

    if partner_id is not None:
        await safe_send(
            message.bot,
            partner_id,
            "Собеседник перешёл к следующему диалогу.",
            reply_markup=main_keyboard(),
        )

    await search_user(message)


# =========================
# VIP И TELEGRAM STARS
# =========================

@router.message(F.text == BTN_VIP)
async def vip_handler(message: Message) -> None:
    await save_user(message.from_user)

    if await is_vip(message.from_user.id):
        await message.answer(
            "💎 VIP уже активирован."
        )
        return

    payload = f"vip:{message.from_user.id}"

    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title="VIP навсегда",
        description=(
            "VIP позволяет отправлять запрос на взаимное "
            "открытие Telegram-профилей."
        ),
        payload=payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label="VIP навсегда",
                amount=VIP_PRICE_STARS,
            )
        ],
        provider_token="",
        reply_markup=invoice_keyboard(),
    )


@router.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery,
) -> None:
    expected_payload = f"vip:{query.from_user.id}"

    valid = (
        query.currency == "XTR"
        and query.total_amount == VIP_PRICE_STARS
        and query.invoice_payload == expected_payload
    )

    await query.answer(
        ok=valid,
        error_message=(
            None
            if valid
            else "Параметры оплаты неверны. Создайте счёт заново."
        ),
    )


@router.message(F.successful_payment)
async def payment_success_handler(message: Message) -> None:
    await save_user(message.from_user)

    payment = message.successful_payment
    expected_payload = f"vip:{message.from_user.id}"

    if (
        payment.currency != "XTR"
        or payment.total_amount != VIP_PRICE_STARS
        or payment.invoice_payload != expected_payload
    ):
        await message.answer(
            "Платёж получен, но не распознан. Напишите /paysupport."
        )
        return

    await activate_vip(
        user_id=message.from_user.id,
        charge_id=payment.telegram_payment_charge_id,
        amount=payment.total_amount,
        currency=payment.currency,
    )

    await message.answer(
        "✅ <b>VIP активирован навсегда!</b>\n\n"
        "Теперь во время диалога нажмите «👤 Узнать кто».",
        reply_markup=main_keyboard(),
    )


# =========================
# УЗНАТЬ СОБЕСЕДНИКА
# =========================

@router.message(F.text == BTN_REVEAL)
async def reveal_handler(message: Message) -> None:
    await save_user(message.from_user)

    requester_id = message.from_user.id

    if not await is_vip(requester_id):
        await message.answer(
            "Функция доступна только владельцам VIP."
        )
        return

    target_id = await get_partner(requester_id)

    if target_id is None:
        await message.answer(
            "Сначала найдите собеседника."
        )
        return

    await create_reveal_request(
        requester_id=requester_id,
        target_id=target_id,
    )

    delivered = await safe_send(
        message.bot,
        target_id,
        "👤 <b>Запрос на открытие профилей</b>\n\n"
        "VIP-собеседник хочет, чтобы вы увидели "
        "профили друг друга.\n\n"
        "При отказе никакие данные не будут показаны.",
        reply_markup=reveal_keyboard(requester_id),
    )

    if not delivered:
        await stop_dialog(requester_id)
        await message.answer(
            "Собеседник недоступен."
        )
        return

    await message.answer(
        "Запрос отправлен. Ждём ответа собеседника."
    )


@router.callback_query(
    F.data.startswith("reveal_accept:")
)
async def reveal_accept_handler(
    callback: CallbackQuery,
) -> None:
    try:
        requester_id = int(
            callback.data.split(":", 1)[1]
        )
    except (ValueError, IndexError):
        await callback.answer(
            "Некорректный запрос.",
            show_alert=True,
        )
        return

    target_id = callback.from_user.id

    if await get_partner(target_id) != requester_id:
        await callback.answer(
            "Диалог уже завершён.",
            show_alert=True,
        )
        return

    if await get_partner(requester_id) != target_id:
        await callback.answer(
            "Диалог уже завершён.",
            show_alert=True,
        )
        return

    success = await update_reveal_request(
        requester_id=requester_id,
        target_id=target_id,
        status="accepted",
    )

    if not success:
        await callback.answer(
            "Запрос уже обработан.",
            show_alert=True,
        )
        return

    requester_profile = await get_profile(requester_id)
    target_profile = await get_profile(target_id)

    if requester_profile is None or target_profile is None:
        await callback.answer(
            "Не удалось получить профили.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Профили открыты."
    )

    if callback.message:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=None
            )
        except TelegramBadRequest:
            pass

    await safe_send(
        callback.bot,
        requester_id,
        "✅ Собеседник согласился.\n\n"
        "<b>Профиль собеседника:</b>\n"
        + profile_text(target_profile),
    )

    await safe_send(
        callback.bot,
        target_id,
        "✅ Вы согласились.\n\n"
        "<b>Профиль собеседника:</b>\n"
        + profile_text(requester_profile),
    )


@router.callback_query(
    F.data.startswith("reveal_decline:")
)
async def reveal_decline_handler(
    callback: CallbackQuery,
) -> None:
    try:
        requester_id = int(
            callback.data.split(":", 1)[1]
        )
    except (ValueError, IndexError):
        await callback.answer(
            "Некорректный запрос.",
            show_alert=True,
        )
        return

    target_id = callback.from_user.id

    success = await update_reveal_request(
        requester_id=requester_id,
        target_id=target_id,
        status="declined",
    )

    if not success:
        await callback.answer(
            "Запрос уже обработан.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Вы отказали."
    )

    if callback.message:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=None
            )
        except TelegramBadRequest:
            pass

    await safe_send(
        callback.bot,
        requester_id,
        "Собеседник отказался открывать профиль."
    )


# =========================
# ПЕРЕСЫЛКА СООБЩЕНИЙ
# =========================

allowed_content_types = {
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.ANIMATION,
    ContentType.AUDIO,
    ContentType.VOICE,
    ContentType.VIDEO_NOTE,
    ContentType.DOCUMENT,
    ContentType.STICKER,
}


@router.message()
async def relay_handler(message: Message) -> None:
    if message.from_user is None:
        return

    await save_user(message.from_user)

    partner_id = await get_partner(
        message.from_user.id
    )

    if partner_id is None:
        await message.answer(
            "Вы сейчас ни с кем не общаетесь.\n"
            "Нажмите «🔎 Найти собеседника».",
            reply_markup=main_keyboard(),
        )
        return

    # Пересланные сообщения могут содержать имя автора.
    if getattr(message, "forward_origin", None) is not None:
        await message.answer(
            "Пересланные сообщения запрещены, "
            "потому что они могут раскрыть личность."
        )
        return

    # Контакты и геолокация намеренно не разрешены.
    if message.content_type not in allowed_content_types:
        await message.answer(
            "Этот тип сообщения не поддерживается."
        )
        return

    try:
        await message.bot.copy_message(
            chat_id=partner_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            protect_content=True,
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        await stop_dialog(message.from_user.id)

        await message.answer(
            "Собеседник недоступен. Диалог завершён.",
            reply_markup=main_keyboard(),
        )


# =========================
# ЗАПУСК
# =========================

async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="Главное меню",
            ),
            BotCommand(
                command="help",
                description="Помощь",
            ),
            BotCommand(
                command="paysupport",
                description="Поддержка по оплате",
            ),
        ]
    )


async def main() -> None:
    if (
        not BOT_TOKEN
        or BOT_TOKEN == "ВСТАВЬТЕ_СЮДА_ТОКЕН_ОТ_BOTFATHER"
    ):
        raise RuntimeError(
            "Вставьте токен бота в переменную BOT_TOKEN "
            "в начале файла bot.py"
        )

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    await bot.delete_webhook(
        drop_pending_updates=False
    )
    await set_commands(bot)

    print("Бот запущен", flush=True)

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
