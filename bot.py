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


# ============ ОСНОВНОЙ КОД БОТА ============

import logging
import asyncio
from collections import defaultdict

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

GROQ_MODEL = "llama-3.1-8b-instant"

MAX_HISTORY_MESSAGES = 6
MAX_TOKENS = 800

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


async def send_long(update: Update, text: str):
    if len(text) <= 4096:
        await update.message.reply_text(text)
        return

    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i + 4096])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я ИИ-бот с поиском 🔎\n\n"
        "Просто напиши вопрос — я отвечу.\n\n"
        "Для поиска в интернете используй:\n"
        "/search твой запрос\n\n"
        "Команды:\n"
        "/reset — очистить историю"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history[user_id].clear()
    await update.message.reply_text("История очищена ✅")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    text = update.message.text

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
    if TELEGRAM_BOT_TOKEN == "ВСТАВЬ_ТОКЕН_БОТА":
        print("Ошибка: вставь TELEGRAM_BOT_TOKEN.")
        return

    if GROQ_API_KEY == "ВСТАВЬ_GROQ_API_KEY":
        print("Ошибка: вставь GROQ_API_KEY.")
        return

    if TAVILY_API_KEY == "ВСТАВЬ_TAVILY_API_KEY":
        print("Ошибка: вставь TAVILY_API_KEY.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("search", search_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Groq + Tavily Search bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
