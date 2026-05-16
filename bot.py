import sys
import subprocess
import importlib.util
import site


# ============ АВТОУСТАНОВКА БИБЛИОТЕК ============

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
install_package_if_missing("requests", "requests")


# ============ ОСНОВНОЙ КОД ============

import re
import time
import random
import sqlite3
import logging
import asyncio
import smtplib
from email.message import EmailMessage
from collections import defaultdict
from datetime import datetime

import requests

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ================= НАСТРОЙКИ =================

TELEGRAM_BOT_TOKEN = "8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo"

GROQ_API_KEY = "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"
TAVILY_API_KEY = "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"

# SMTP для отправки кода на почту
SMTP_EMAIL = "danilmikxaylov2015@gmail.com"
SMTP_PASSWORD = "juhjimjskqtzdkmh"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

GROQ_MODEL = "llama-3.1-8b-instant"

MAX_HISTORY_MESSAGES = 6
MAX_TOKENS = 800

CODE_EXPIRE_SECONDS = 600

DB_NAME = "ai_bot_users.db"

# =============================================


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)

history = defaultdict(list)


SYSTEM_PROMPT = """
Ты полезный Telegram-бот с ИИ.
Отвечай на русском языке.
Пиши понятно, коротко и по делу.
Если пользователь просит код — давай готовый рабочий код.
Не говори, что ты Groq, Llama или языковая модель.
"""


# ================= БАЗА ДАННЫХ =================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            email TEXT,
            verified INTEGER DEFAULT 0,
            registered_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_codes (
            user_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            code TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def is_registered(user_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        "SELECT verified FROM users WHERE user_id = ?",
        (user_id,)
    )

    row = cur.fetchone()
    conn.close()

    return bool(row and row[0] == 1)


def get_user_email(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        "SELECT email FROM users WHERE user_id = ? AND verified = 1",
        (user_id,)
    )

    row = cur.fetchone()
    conn.close()

    if row:
        return row[0]

    return None


def save_email_code(user_id: int, email: str, code: str):
    expires_at = int(time.time()) + CODE_EXPIRE_SECONDS

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO email_codes (user_id, email, code, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            email = excluded.email,
            code = excluded.code,
            expires_at = excluded.expires_at
    """, (
        user_id,
        email,
        code,
        expires_at
    ))

    conn.commit()
    conn.close()


def verify_email_code(user, code: str):
    user_id = user.id

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        "SELECT email, code, expires_at FROM email_codes WHERE user_id = ?",
        (user_id,)
    )

    row = cur.fetchone()

    if not row:
        conn.close()
        return False, "Код не найден. Нажми /start и запроси новый код."

    email, saved_code, expires_at = row

    if int(time.time()) > expires_at:
        cur.execute("DELETE FROM email_codes WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return False, "Код истёк. Нажми /start и запроси новый код."

    if code.strip() != saved_code:
        conn.close()
        return False, "Неверный код. Попробуй ещё раз."

    cur.execute("""
        INSERT INTO users (
            user_id, username, full_name,
            email, verified, registered_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            email = excluded.email,
            verified = 1,
            registered_at = excluded.registered_at
    """, (
        user.id,
        user.username or "",
        user.full_name or "",
        email,
        1,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    cur.execute("DELETE FROM email_codes WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()

    return True, email


def count_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users WHERE verified = 1")
    count = cur.fetchone()[0]

    conn.close()
    return count


# ================= EMAIL =================

def is_valid_email(email: str) -> bool:
    pattern = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
    return re.match(pattern, email) is not None


def generate_code() -> str:
    return str(random.randint(100000, 999999))


def send_email_code(email: str, code: str):
    msg = EmailMessage()

    msg["Subject"] = "Код подтверждения"
    msg["From"] = SMTP_EMAIL
    msg["To"] = email

    msg.set_content(
        f"Твой код подтверждения: {code}\n\n"
        f"Код действует 10 минут.\n\n"
        f"Если ты не запрашивал код, просто проигнорируй это письмо."
    )

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
        smtp.send_message(msg)


# ================= ИИ + ПОИСК =================

def make_headers(extra_headers=None):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
    }

    if extra_headers:
        headers.update(extra_headers)

    return headers


def post_json(url, headers, payload, timeout=120):
    try:
        response = requests.post(
            url,
            headers=make_headers(headers),
            json=payload,
            timeout=timeout
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code}: {response.text[:1500]}"
            )

        return response.json()

    except Exception as e:
        raise RuntimeError(f"Ошибка соединения: {str(e)[:1500]}")


def tavily_search_sync(query: str):
    url = "https://api.tavily.com/search"

    headers = {
        "Authorization": f"Bearer {TAVILY_API_KEY}"
    }

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "topic": "general",
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
        "include_raw_content": False
    }

    data = post_json(url, headers, payload, timeout=60)

    results = data.get("results", [])
    answer = data.get("answer", "")

    search_text = ""

    if answer:
        search_text += f"Краткий ответ поиска:\n{answer}\n\n"

    search_text += "Источники из поиска:\n"

    sources = []

    for i, item in enumerate(results, start=1):
        title = item.get("title", "Без названия")
        source_url = item.get("url", "")
        content = item.get("content", "")

        search_text += f"\n{i}. {title}\n"
        search_text += f"URL: {source_url}\n"
        search_text += f"Описание: {content[:700]}\n"

        if source_url:
            sources.append(f"{i}. {title}\n{source_url}")

    return search_text, sources


def ask_groq_sync(user_id: int, user_text: str, search_context: str = "") -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    for item in history[user_id]:
        messages.append(item)

    if search_context:
        messages.append({
            "role": "system",
            "content": (
                "Ниже данные из интернет-поиска. "
                "Используй их для ответа. "
                "Не выдумывай факты, если информации нет.\n\n"
                f"{search_context}"
            )
        })

    messages.append({
        "role": "user",
        "content": user_text
    })

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS,
        "stream": False
    }

    data = post_json(url, headers, payload, timeout=120)

    answer = data["choices"][0]["message"]["content"]

    history[user_id].append({"role": "user", "content": user_text})
    history[user_id].append({"role": "assistant", "content": answer})
    history[user_id] = history[user_id][-MAX_HISTORY_MESSAGES:]

    return answer


# ================= TELEGRAM =================

async def require_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id

    if is_registered(user_id):
        return True

    context.user_data["mode"] = "waiting_email"

    await update.message.reply_text(
        "🔐 Сначала нужно зарегистрироваться.\n\n"
        "Отправь свой email, и я пришлю код подтверждения.\n\n"
        "Пример:\n"
        "example@gmail.com"
    )

    return False


async def send_long(update: Update, text: str):
    if len(text) <= 4096:
        await update.message.reply_text(text)
        return

    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i + 4096])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if is_registered(user_id):
        email = get_user_email(user_id)

        await update.message.reply_text(
            "✅ Ты уже зарегистрирован.\n\n"
            f"Email: {email}\n\n"
            "Просто напиши вопрос — я отвечу.\n\n"
            "Для поиска:\n"
            "/search твой запрос\n\n"
            "Команды:\n"
            "/reset — очистить историю\n"
            "/profile — профиль"
        )
        return

    context.user_data.clear()
    context.user_data["mode"] = "waiting_email"

    await update.message.reply_text(
        "Привет! Я ИИ-бот с поиском 🔎\n\n"
        "Перед использованием нужно зарегистрироваться.\n\n"
        "Отправь свой email, и я пришлю код подтверждения."
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update, context):
        return

    email = get_user_email(update.effective_user.id)

    await update.message.reply_text(
        "👤 Твой профиль\n\n"
        f"ID: {update.effective_user.id}\n"
        f"Email: {email}\n"
        "Статус: ✅ зарегистрирован"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update, context):
        return

    user_id = update.effective_user.id
    history[user_id].clear()

    await update.message.reply_text("История очищена ✅")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # сюда можно вставить свой ADMIN_ID, если надо закрыть команду
    await update.message.reply_text(
        f"👥 Зарегистрировано пользователей: {count_users()}"
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update, context):
        return

    query = " ".join(context.args).strip()

    if not query:
        await update.message.reply_text(
            "Напиши запрос после команды.\n\n"
            "Пример:\n"
            "/search что нового в мире ИИ"
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        await update.message.reply_text("Ищу в интернете... 🔎")

        search_context, sources = await asyncio.to_thread(
            tavily_search_sync,
            query
        )

        answer = await asyncio.to_thread(
            ask_groq_sync,
            update.effective_user.id,
            query,
            search_context
        )

        if sources:
            answer += "\n\nИсточники:\n" + "\n\n".join(sources[:5])

        await send_long(update, answer)

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text(
            "Ошибка поиска/ИИ:\n\n"
            f"{str(e)[:1500]}"
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    mode = context.user_data.get("mode")

    if mode == "waiting_email":
        email = text.lower()

        if not is_valid_email(email):
            await update.message.reply_text(
                "❌ Это не похоже на email.\n\n"
                "Отправь email в таком формате:\n"
                "example@gmail.com"
            )
            return

        code = generate_code()
        save_email_code(user_id, email, code)

        try:
            await asyncio.to_thread(send_email_code, email, code)

            context.user_data["mode"] = "waiting_code"

            await update.message.reply_text(
                "📩 Код отправлен на почту.\n\n"
                "Введи 6-значный код из письма.\n\n"
                "Если письма нет — проверь папку Спам."
            )

        except Exception as e:
            logging.exception(e)

            await update.message.reply_text(
                "❌ Не получилось отправить письмо.\n\n"
                "Проверь SMTP_EMAIL, SMTP_PASSWORD, SMTP_HOST и SMTP_PORT в коде.\n\n"
                f"Ошибка: {str(e)[:1000]}"
            )

        return

    if mode == "waiting_code":
        code = text

        ok, result = verify_email_code(update.effective_user, code)

        if not ok:
            await update.message.reply_text(f"❌ {result}")
            return

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Регистрация завершена!\n\n"
            f"Email: {result}\n\n"
            "Теперь можешь писать вопросы.\n\n"
            "Для поиска используй:\n"
            "/search твой запрос"
        )
        return

    if not await require_registration(update, context):
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        answer = await asyncio.to_thread(
            ask_groq_sync,
            user_id,
            text
        )

        await send_long(update, answer)

    except Exception as e:
        logging.exception(e)

        await update.message.reply_text(
            "Ошибка Groq:\n\n"
            f"{str(e)[:1500]}"
        )


def main():
    init_db()

    if TELEGRAM_BOT_TOKEN == "ВСТАВЬ_ТОКЕН_БОТА":
        print("Ошибка: вставь TELEGRAM_BOT_TOKEN.")
        return

    if GROQ_API_KEY == "ВСТАВЬ_GROQ_API_KEY":
        print("Ошибка: вставь GROQ_API_KEY.")
        return

    if TAVILY_API_KEY == "ВСТАВЬ_TAVILY_API_KEY":
        print("Ошибка: вставь TAVILY_API_KEY.")
        return

    if SMTP_EMAIL == "ВСТАВЬ_ПОЧТУ":
        print("Ошибка: вставь SMTP_EMAIL.")
        return

    if SMTP_PASSWORD == "ВСТАВЬ_SMTP_ПАРОЛЬ":
        print("Ошибка: вставь SMTP_PASSWORD.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("AI bot with email registration запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
