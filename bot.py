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
import calendar
from datetime import datetime, date

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ================= НАСТРОЙКИ =================

TELEGRAM_BOT_TOKEN = "8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo"

DB_NAME = "birthdays.db"

MIN_YEAR = 1900
MAX_YEAR = 2026

MONTHS = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

# =============================================


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS birthdays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            birth_year INTEGER NOT NULL,
            birth_month INTEGER NOT NULL,
            birth_day INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def add_birthday(owner_id, name, year, month, day):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO birthdays (
            owner_id, name, birth_year,
            birth_month, birth_day, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        owner_id,
        name,
        year,
        month,
        day,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()


def get_birthdays(owner_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, birth_year, birth_month, birth_day
        FROM birthdays
        WHERE owner_id = ?
        ORDER BY birth_month, birth_day, name
    """, (owner_id,))

    rows = cur.fetchall()
    conn.close()

    return rows


def get_today_birthdays(owner_id):
    today = date.today()

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, birth_year, birth_month, birth_day
        FROM birthdays
        WHERE owner_id = ? AND birth_month = ? AND birth_day = ?
        ORDER BY name
    """, (owner_id, today.month, today.day))

    rows = cur.fetchall()
    conn.close()

    return rows


def delete_birthday_by_id(owner_id, birthday_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM birthdays WHERE owner_id = ? AND id = ?",
        (owner_id, birthday_id)
    )

    deleted = cur.rowcount

    conn.commit()
    conn.close()

    return deleted > 0


def calc_age(year, month, day):
    today = date.today()
    age = today.year - year

    if (today.month, today.day) < (month, day):
        age -= 1

    if age < 0:
        age = 0

    return age


def safe_birthday_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        if month == 2 and day == 29:
            return date(year, 2, 28)
        raise


def days_until_birthday(month, day):
    today = date.today()

    this_year_birthday = safe_birthday_date(today.year, month, day)

    if this_year_birthday >= today:
        next_birthday = this_year_birthday
    else:
        next_birthday = safe_birthday_date(today.year + 1, month, day)

    return (next_birthday - today).days


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить день рождения", callback_data="add")],
        [InlineKeyboardButton("🎂 Список дней рождения", callback_data="list")],
        [InlineKeyboardButton("📅 Сегодня", callback_data="today")],
        [InlineKeyboardButton("🗑 Удалить", callback_data="delete_menu")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ])


def months_keyboard():
    buttons = []
    row = []

    for month_num, month_name in MONTHS.items():
        row.append(
            InlineKeyboardButton(
                month_name,
                callback_data=f"month:{month_num}"
            )
        )

        if len(row) == 3:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("⬅️ Ввести год заново", callback_data="back_year_input")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    return InlineKeyboardMarkup(buttons)


def days_keyboard(year, month):
    max_day = calendar.monthrange(year, month)[1]

    buttons = []
    row = []

    for day in range(1, max_day + 1):
        row.append(
            InlineKeyboardButton(
                str(day),
                callback_data=f"day:{day}"
            )
        )

        if len(row) == 7:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("⬅️ Назад к месяцам", callback_data="back_months")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    return InlineKeyboardMarkup(buttons)


def delete_keyboard(owner_id):
    birthdays = get_birthdays(owner_id)

    if not birthdays:
        return None

    buttons = []

    for birthday_id, name, year, month, day in birthdays:
        buttons.append([
            InlineKeyboardButton(
                f"🗑 {name} — {day:02d}.{month:02d}.{year}",
                callback_data=f"delete:{birthday_id}"
            )
        ])

    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu")])

    return InlineKeyboardMarkup(buttons)


def format_birthday(row):
    birthday_id, name, year, month, day = row
    age = calc_age(year, month, day)
    days_left = days_until_birthday(month, day)

    if days_left == 0:
        left_text = "🎉 сегодня день рождения!"
    elif days_left == 1:
        left_text = "остался 1 день"
    else:
        left_text = f"осталось {days_left} дн."

    return (
        f"🎂 {name}\n"
        f"Дата: {day:02d}.{month:02d}.{year}\n"
        f"Возраст: {age}\n"
        f"До ДР: {left_text}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "🎂 Бот для дней рождения\n\n"
        "Можно добавить человека, вручную ввести год рождения, выбрать месяц и день.\n\n"
        "Выбери действие:",
        reply_markup=main_menu()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n\n"
        "/start — главное меню\n"
        "/addbirthday — добавить день рождения\n"
        "/birthdays — список\n"
        "/today — у кого сегодня день рождения\n"
        "/deletebirthday — удалить\n"
        "/cancel — отменить действие"
    )


async def addbirthday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["mode"] = "waiting_name"

    await update.message.reply_text(
        "Введи имя человека:\n\n"
        "Например: Миша"
    )


async def birthdays_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id = update.effective_user.id
    birthdays = get_birthdays(owner_id)

    if not birthdays:
        await update.message.reply_text(
            "У тебя пока нет добавленных дней рождения.\n\n"
            "Добавить можно командой /addbirthday"
        )
        return

    text = "🎂 Твои дни рождения:\n\n"

    for i, row in enumerate(birthdays, start=1):
        text += f"{i}. {format_birthday(row)}\n\n"

    await update.message.reply_text(text)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id = update.effective_user.id
    birthdays = get_today_birthdays(owner_id)

    if not birthdays:
        await update.message.reply_text("Сегодня дней рождения нет.")
        return

    text = "🎉 Сегодня день рождения:\n\n"

    for row in birthdays:
        text += format_birthday(row) + "\n\n"

    await update.message.reply_text(text)


async def deletebirthday_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id = update.effective_user.id
    keyboard = delete_keyboard(owner_id)

    if keyboard is None:
        await update.message.reply_text("Удалять нечего. Список пуст.")
        return

    await update.message.reply_text(
        "Выбери, кого удалить:",
        reply_markup=keyboard
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=main_menu()
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")

    if mode == "waiting_name":
        name = update.message.text.strip()

        if len(name) < 2:
            await update.message.reply_text("Имя слишком короткое. Введи ещё раз.")
            return

        if len(name) > 40:
            await update.message.reply_text("Имя слишком длинное. Введи короче.")
            return

        context.user_data["name"] = name
        context.user_data["mode"] = "waiting_year"

        await update.message.reply_text(
            f"Имя: {name}\n\n"
            "Теперь введи год рождения числом.\n\n"
            "Например:\n"
            "2015"
        )
        return

    if mode == "waiting_year":
        text = update.message.text.strip()

        if not text.isdigit():
            await update.message.reply_text(
                "Год надо ввести числом.\n\n"
                "Например:\n"
                "2015"
            )
            return

        year = int(text)

        if year < MIN_YEAR or year > MAX_YEAR:
            await update.message.reply_text(
                f"Год должен быть от {MIN_YEAR} до {MAX_YEAR}.\n\n"
                "Например:\n"
                "2015"
            )
            return

        context.user_data["year"] = year
        context.user_data["mode"] = "waiting_month"

        await update.message.reply_text(
            f"Год рождения: {year}\n\n"
            "Теперь выбери месяц:",
            reply_markup=months_keyboard()
        )
        return

    await update.message.reply_text(
        "Я не понял сообщение.\n\n"
        "Нажми /start, чтобы открыть меню."
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    owner_id = query.from_user.id
    data = query.data

    if data == "menu":
        context.user_data.clear()
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=main_menu()
        )
        return

    if data == "add":
        context.user_data.clear()
        context.user_data["mode"] = "waiting_name"

        await query.edit_message_text(
            "Введи имя человека:\n\n"
            "Например: Миша"
        )
        return

    if data == "list":
        birthdays = get_birthdays(owner_id)

        if not birthdays:
            await query.edit_message_text(
                "У тебя пока нет добавленных дней рождения.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Добавить", callback_data="add")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="menu")]
                ])
            )
            return

        text = "🎂 Твои дни рождения:\n\n"

        for i, row in enumerate(birthdays, start=1):
            text += f"{i}. {format_birthday(row)}\n\n"

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu")]
            ])
        )
        return

    if data == "today":
        birthdays = get_today_birthdays(owner_id)

        if not birthdays:
            await query.edit_message_text(
                "Сегодня дней рождения нет.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="menu")]
                ])
            )
            return

        text = "🎉 Сегодня день рождения:\n\n"

        for row in birthdays:
            text += format_birthday(row) + "\n\n"

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu")]
            ])
        )
        return

    if data == "delete_menu":
        keyboard = delete_keyboard(owner_id)

        if keyboard is None:
            await query.edit_message_text(
                "Удалять нечего. Список пуст.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="menu")]
                ])
            )
            return

        await query.edit_message_text(
            "Выбери, кого удалить:",
            reply_markup=keyboard
        )
        return

    if data.startswith("delete:"):
        birthday_id = int(data.split(":", 1)[1])

        ok = delete_birthday_by_id(owner_id, birthday_id)

        if ok:
            await query.edit_message_text(
                "✅ День рождения удалён.",
                reply_markup=main_menu()
            )
        else:
            await query.edit_message_text(
                "❌ Не получилось удалить.",
                reply_markup=main_menu()
            )
        return

    if data == "help":
        await query.edit_message_text(
            "ℹ️ Помощь\n\n"
            "➕ Добавить день рождения — бот спросит имя, год, месяц и день.\n"
            "🎂 Список — показывает всех людей и сколько осталось до дня рождения.\n"
            "📅 Сегодня — показывает, у кого сегодня день рождения.\n"
            "🗑 Удалить — удаляет запись.\n\n"
            "Команды:\n"
            "/addbirthday\n"
            "/birthdays\n"
            "/today\n"
            "/deletebirthday",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu")]
            ])
        )
        return

    if data == "cancel":
        context.user_data.clear()

        await query.edit_message_text(
            "Действие отменено.",
            reply_markup=main_menu()
        )
        return

    if data == "back_year_input":
        context.user_data["mode"] = "waiting_year"

        await query.edit_message_text(
            "Введи год рождения числом.\n\n"
            "Например:\n"
            "2015"
        )
        return

    if data == "back_months":
        context.user_data["mode"] = "waiting_month"

        await query.edit_message_text(
            "Выбери месяц рождения:",
            reply_markup=months_keyboard()
        )
        return

    if data.startswith("month:"):
        month = int(data.split(":", 1)[1])

        year = context.user_data.get("year")

        if not year:
            context.user_data["mode"] = "waiting_year"

            await query.edit_message_text(
                "Сначала введи год рождения числом.\n\n"
                "Например:\n"
                "2015"
            )
            return

        context.user_data["month"] = month
        context.user_data["mode"] = "waiting_day"

        await query.edit_message_text(
            f"Год: {year}\n"
            f"Месяц: {MONTHS[month]}\n\n"
            "Теперь выбери день:",
            reply_markup=days_keyboard(year, month)
        )
        return

    if data.startswith("day:"):
        day = int(data.split(":", 1)[1])

        name = context.user_data.get("name")
        year = context.user_data.get("year")
        month = context.user_data.get("month")

        if not name or not year or not month:
            await query.edit_message_text(
                "Что-то сбилось. Начни заново.",
                reply_markup=main_menu()
            )
            context.user_data.clear()
            return

        max_day = calendar.monthrange(year, month)[1]

        if day < 1 or day > max_day:
            await query.edit_message_text("Неверный день.")
            return

        add_birthday(
            owner_id=owner_id,
            name=name,
            year=year,
            month=month,
            day=day
        )

        age = calc_age(year, month, day)
        days_left = days_until_birthday(month, day)

        context.user_data.clear()

        await query.edit_message_text(
            "✅ День рождения добавлен!\n\n"
            f"Имя: {name}\n"
            f"Дата рождения: {day:02d}.{month:02d}.{year}\n"
            f"Возраст сейчас: {age}\n"
            f"До следующего дня рождения: {days_left} дн.",
            reply_markup=main_menu()
        )
        return


def main():
    init_db()

    if TELEGRAM_BOT_TOKEN == "ВСТАВЬ_ТОКЕН_БОТА":
        print("Ошибка: вставь TELEGRAM_BOT_TOKEN.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("addbirthday", addbirthday))
    app.add_handler(CommandHandler("birthdays", birthdays_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("deletebirthday", deletebirthday_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    print("Birthday bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
