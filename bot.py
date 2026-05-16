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

import json
import urllib.request
import urllib.error
import logging
import asyncio
from collections import defaultdict

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
GEMINI_API_KEY = "AIzaSyBBeKW-o7CY7y0UYbsLJGqCxXgf0yWWABI"

GEMINI_MODEL = "gemini-2.5-flash"

MAX_HISTORY_MESSAGES = 6

# =============================================


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)

history = defaultdict(list)


SYSTEM_PROMPT = """
Ты умный Telegram-бот с ИИ.
Отвечай на русском языке.
Пиши понятно и полезно.
Если вопрос требует свежей информации, используй поиск Google.
Если в ответе есть источники, кратко добавь их в конце.
Не говори, что ты Gemini или Google.
"""


def extract_text(data):
    try:
        candidates = data.get("candidates", [])
        if not candidates:
            return "Не смог получить ответ от Gemini."

        parts = candidates[0]["content"]["parts"]
        text = ""

        for part in parts:
            if "text" in part:
                text += part["text"]

        return text.strip() or "Gemini вернул пустой ответ."

    except Exception:
        return "Ошибка чтения ответа Gemini."


def extract_sources(data):
    sources = []

    try:
        candidate = data.get("candidates", [{}])[0]
        grounding = candidate.get("groundingMetadata", {})
        chunks = grounding.get("groundingChunks", [])

        for chunk in chunks:
            web = chunk.get("web", {})
            title = web.get("title", "")
            uri = web.get("uri", "")

            if uri:
                if title:
                    sources.append(f"• {title}: {uri}")
                else:
                    sources.append(f"• {uri}")

        # убираем повторы
        unique = []
        for src in sources:
            if src not in unique:
                unique.append(src)

        return unique[:5]

    except Exception:
        return []


def ask_gemini_sync(user_id: int, user_text: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent"
    )

    messages_text = SYSTEM_PROMPT + "\n\n"

    for item in history[user_id]:
        role = item["role"]
        content = item["content"]

        if role == "user":
            messages_text += f"Пользователь: {content}\n"
        else:
            messages_text += f"Бот: {content}\n"

    messages_text += f"Пользователь: {user_text}\nБот:"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": messages_text
                    }
                ]
            }
        ],
        "tools": [
            {
                "google_search": {}
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 900
        }
    }

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)

    except urllib.error.HTTPError as e:
        error_text = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini API error {e.code}: {error_text[:1500]}")

    except Exception as e:
        raise RuntimeError(f"Ошибка соединения с Gemini: {str(e)[:1000]}")

    answer = extract_text(data)
    sources = extract_sources(data)

    if sources:
        answer += "\n\nИсточники:\n" + "\n".join(sources)

    history[user_id].append({"role": "user", "content": user_text})
    history[user_id].append({"role": "assistant", "content": answer})
    history[user_id] = history[user_id][-MAX_HISTORY_MESSAGES:]

    return answer


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я ИИ-бот с поиском Google.\n\n"
        "Просто напиши вопрос.\n\n"
        "Команды:\n"
        "/reset — очистить историю\n"
        "/search запрос — спросить с поиском"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history[user_id].clear()
    await update.message.reply_text("История очищена ✅")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()

    if not text:
        await update.message.reply_text(
            "Напиши запрос после команды.\n\n"
            "Пример:\n"
            "/search свежие новости ИИ"
        )
        return

    await handle_ai(update, context, custom_text=text)


async def handle_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, custom_text=None):
    user_id = update.effective_user.id
    user_text = custom_text or update.message.text

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        answer = await asyncio.to_thread(
            ask_gemini_sync,
            user_id,
            user_text
        )

        if len(answer) > 4096:
            for i in range(0, len(answer), 4096):
                await update.message.reply_text(answer[i:i + 4096])
        else:
            await update.message.reply_text(answer)

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text(
            "Ошибка Gemini:\n\n"
            f"{str(e)[:1500]}"
        )


def main():
    if TELEGRAM_BOT_TOKEN == "ВСТАВЬ_ТОКЕН_БОТА":
        print("Ошибка: вставь TELEGRAM_BOT_TOKEN.")
        return

    if GEMINI_API_KEY == "ВСТАВЬ_GEMINI_API_KEY":
        print("Ошибка: вставь GEMINI_API_KEY.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("search", search_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai))

    print("Gemini Search bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
