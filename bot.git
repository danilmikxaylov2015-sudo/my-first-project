import logging
import asyncio
import base64
from io import BytesIO
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


# ===== ВСТАВЬ СЮДА КЛЮЧИ =====

TELEGRAM_BOT_TOKEN = "8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo"
AIHUBMIX_API_KEY = "sk-c6fpChb9sIH4lOlG58C7D5Da431b41De9d0f0eF1Da52E5Eb"

# =============================


TEXT_MODEL = "gpt-5.5-free"
IMAGE_MODEL = "gpt-image-2-free"

TEXT_API_URL = "https://aihubmix.com/v1/chat/completions"
IMAGE_API_URL = "https://aihubmix.com/v1/images/generations"

MAX_HISTORY_MESSAGES = 6
MAX_TOKENS = 700

history = defaultdict(list)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)


SYSTEM_PROMPT = """
Ты Telegram-бот с искусственным интеллектом.
Отвечай на русском языке.
Пиши понятно, полезно и не слишком длинно.
Если пользователь просит код — давай готовый рабочий код.
Не говори, что ты GPT, OpenAI, AIHubMix или языковая модель.
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет!\n\n"
        "Напиши обычное сообщение — я отвечу текстом.\n\n"
        "Для генерации фото используй:\n"
        "/imagine кот в сапогах на земле летом"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — запуск\n"
        "/help — помощь\n"
        "/reset — очистить историю\n"
        "/imagine описание — сгенерировать фото\n\n"
        "Обычный текст без команды — это текстовый ИИ."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history[user_id].clear()
    await update.message.reply_text("История очищена ✅")


def ask_text_ai_sync(user_id: int, user_text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history[user_id],
        {"role": "user", "content": user_text}
    ]

    payload = {
        "model": TEXT_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS,
        "stream": False
    }

    headers = {
        "Authorization": f"Bearer {AIHUBMIX_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        TEXT_API_URL,
        headers=headers,
        json=payload,
        timeout=120
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"AIHubMix text error {response.status_code}: {response.text[:1000]}"
        )

    data = response.json()

    answer = data["choices"][0]["message"]["content"]

    if not answer:
        answer = "Я не смог ответить."

    history[user_id].append({"role": "user", "content": user_text})
    history[user_id].append({"role": "assistant", "content": answer})
    history[user_id] = history[user_id][-MAX_HISTORY_MESSAGES:]

    return answer


def find_image_value(obj):
    if isinstance(obj, dict):
        for key in ["b64_json", "base64", "image_base64", "base64_json"]:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return "base64", value

        for key in ["url", "image_url", "output_url"]:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return "url", value

        for value in obj.values():
            found = find_image_value(value)
            if found:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = find_image_value(item)
            if found:
                return found

    return None


def download_image_from_url(url: str) -> bytes:
    response = requests.get(url, timeout=180)

    if response.status_code != 200:
        raise RuntimeError(
            f"Не удалось скачать картинку: {response.status_code} {response.text[:500]}"
        )

    return response.content


def generate_image_aihubmix_sync(prompt: str) -> bytes:
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "quality": "auto"
    }

    headers = {
        "Authorization": f"Bearer {AIHUBMIX_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        IMAGE_API_URL,
        headers=headers,
        json=payload,
        timeout=240
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"AIHubMix image error {response.status_code}: {response.text[:1500]}"
        )

    data = response.json()

    found = find_image_value(data)

    if not found:
        raise RuntimeError(f"AIHubMix не вернул картинку: {str(data)[:1500]}")

    value_type, value = found

    if value_type == "base64":
        if "," in value:
            value = value.split(",", 1)[1]
        return base64.b64decode(value)

    if value_type == "url":
        return download_image_from_url(value)

    raise RuntimeError("Неизвестный формат картинки.")


async def imagine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args).strip()

    if not prompt:
        await update.message.reply_text(
            "Напиши описание после команды.\n\n"
            "Пример:\n"
            "/imagine кот в сапогах на земле летом, реализм"
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.UPLOAD_PHOTO
    )

    try:
        await update.message.reply_text("Генерирую фото...")

        image_bytes = await asyncio.to_thread(
            generate_image_aihubmix_sync,
            prompt
        )

        image = BytesIO(image_bytes)
        image.name = "image.png"

        await update.message.reply_photo(
            photo=image,
            caption="Готово ✅"
        )

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text(
            "Ошибка генерации фото.\n\n"
            "Проверь AIHubMix API key, модель gpt-image-2-free или лимит.\n"
            "Подробная ошибка есть в консоли PythonAnywhere."
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        answer = await asyncio.to_thread(
            ask_text_ai_sync,
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
            "Ошибка текстового ИИ.\n\n"
            "Проверь AIHubMix API key или модель gpt-5.5-free.\n"
            "Подробная ошибка есть в консоли PythonAnywhere."
        )


def main():
    if TELEGRAM_BOT_TOKEN == "СЮДА_ТОКЕН_ТЕЛЕГРАМ_БОТА":
        print("Ошибка: вставь TELEGRAM_BOT_TOKEN в код.")
        return

    if AIHUBMIX_API_KEY == "СЮДА_API_KEY_AIHUBMIX":
        print("Ошибка: вставь AIHUBMIX_API_KEY в код.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("imagine", imagine))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
