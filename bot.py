import sys
import subprocess
import importlib.util
import site


# ============ АВТОУСТАНОВКА python-telegram-bot ============

def install_package_if_missing(import_name, pip_name):
    if importlib.util.find_spec(import_name) is not None:
        return

    print(f"Библиотека {pip_name} не найдена. Устанавливаю...")

    try:
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            pip_name
        ])
    except Exception:
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            "--user",
            pip_name
        ])

    user_site = site.getusersitepackages()
    if user_site not in sys.path:
        sys.path.append(user_site)

    importlib.invalidate_caches()


install_package_if_missing("telegram", "python-telegram-bot")


# ================= ОСНОВНОЙ КОД БОТА =================

import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ================= НАСТРОЙКИ =================

TELEGRAM_BOT_TOKEN = "8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo"

ADMIN_ID = 0

VPN_LINK = "https://sub.plugvpn.ru/xKpnev2mCx48pYg-"

SUPPORT_USERNAME = "@your_support"

PRODUCT_TITLE = "VPN на 11 месяцев"
PRODUCT_DESCRIPTION = "Доступ к VPN на 11 месяцев"
PRICE_STARS = 350

DB_NAME = "vpn_orders.db"

# =============================================


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            stars INTEGER NOT NULL,
            charge_id TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_order(user_id, username, full_name, stars, charge_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO orders (
            user_id, username, full_name,
            stars, charge_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        username or "",
        full_name or "",
        stars,
        charge_id,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    order_id = cur.lastrowid

    conn.commit()
    conn.close()

    return order_id


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Купить VPN за 350 ⭐", callback_data="buy")],
        [InlineKeyboardButton("📲 Как подключить", callback_data="how")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="support")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет!\n\n"
        "Здесь можно купить VPN на 11 месяцев.\n\n"
        "Цена: 350 ⭐ Telegram Stars\n\n"
        "После оплаты бот сразу выдаст VPN-ссылку.",
        reply_markup=main_menu()
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твой Telegram ID:\n\n{update.effective_user.id}\n\n"
        "Вставь его в ADMIN_ID в коде."
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "buy":
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=PRODUCT_TITLE,
            description=PRODUCT_DESCRIPTION,
            payload="vpn_11_months_350_stars",
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=PRODUCT_TITLE,
                    amount=PRICE_STARS
                )
            ],
        )
        return

    if data == "how":
        await query.edit_message_text(
            "📲 Как подключить VPN:\n\n"
            "1. Купи VPN за 350 ⭐.\n"
            "2. После оплаты бот выдаст ссылку.\n"
            "3. Скопируй ссылку.\n"
            "4. Добавь её в VPN-приложение.\n\n"
            "Приложения:\n"
            "• Hiddify\n"
            "• v2rayTun\n"
            "• Streisand\n"
            "• Shadowrocket для iPhone\n\n"
            "Если не получится — напиши в поддержку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back")]
            ])
        )
        return

    if data == "support":
        await query.edit_message_text(
            f"🆘 Поддержка: {SUPPORT_USERNAME}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back")]
            ])
        )
        return

    if data == "back":
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=main_menu()
        )
        return


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query

    if query.invoice_payload != "vpn_11_months_350_stars":
        await query.answer(
            ok=False,
            error_message="Ошибка заказа."
        )
        return

    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payment = update.message.successful_payment

    if payment.currency != "XTR":
        await update.message.reply_text("Ошибка: оплата не в Telegram Stars.")
        return

    if payment.total_amount != PRICE_STARS:
        await update.message.reply_text("Ошибка: неправильная сумма оплаты.")
        return

    order_id = save_order(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        stars=payment.total_amount,
        charge_id=payment.telegram_payment_charge_id
    )

    await update.message.reply_text(
        "✅ Оплата прошла успешно!\n\n"
        f"Заказ №{order_id}\n"
        "Тариф: VPN на 11 месяцев\n"
        f"Оплачено: {PRICE_STARS} ⭐\n\n"
        "Вот твоя VPN-ссылка:\n\n"
        f"{VPN_LINK}\n\n"
        "Скопируй её и добавь в VPN-приложение."
    )

    if ADMIN_ID != 0:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💰 Новая покупка VPN\n\n"
                f"Заказ №{order_id}\n"
                f"Пользователь: {user.full_name}\n"
                f"Username: @{user.username if user.username else 'нет'}\n"
                f"User ID: {user.id}\n"
                f"Оплачено: {PRICE_STARS} ⭐\n"
                f"Charge ID: {payment.telegram_payment_charge_id}"
            )
        )


def main():
    init_db()

    if TELEGRAM_BOT_TOKEN == "ВСТАВЬ_ТОКЕН_БОТА":
        print("Ошибка: вставь TELEGRAM_BOT_TOKEN.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))

    app.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_handler
        )
    )

    print("VPN Stars bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
