import asyncio
import base64
import io
import logging
import requests

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# НАСТРОЙКИ
# =========================================================

TELEGRAM_BOT_TOKEN = "8771988074:AAFU_t6MfDS4KdIHzlxXZI60reyn_e_MJUc"
OPENROUTER_API_KEY = "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"

# Текстовая модель
TEXT_MODEL = "openai/gpt-4o-mini"

# Модель для генерации изображения
# Если не работает, можно заменить на другую image-модель в OpenRouter
IMAGE_MODEL = "openai/gpt-image-1"

# Настройки OpenRouter
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_IMAGE_URL = "https://openrouter.ai/api/v1/images"

# Можно оставить так
HTTP_REFERER = "https://example.com"
X_TITLE = "Telegram OpenRouter Bot"

# Системный промпт
SYSTEM_PROMPT = (
    "Ты полезный Telegram-бот. "
    "Отвечай на русском языке, если пользователь пишет на русском. "
    "Если пользователь просит код — пиши рабочий код. "
    "Если вопрос простой — отвечай кратко и понятно. "
    "Если вопрос сложный — объясняй по шагам."
)

# Лимит памяти
MAX_HISTORY_MESSAGES = 12

# Режимы:
# auto  - автоматически решать, текст или картинка
# chat  - всегда текст
# image - всегда генерация картинки
DEFAULT_MODE = "auto"

# Ключевые слова для автоопределения генерации изображения
IMAGE_TRIGGER_WORDS = [
    "нарисуй",
    "сгенерируй",
    "создай картинку",
    "создай изображение",
    "сделай картинку",
    "сделай изображение",
    "draw",
    "generate image",
    "image of",
    "picture of",
    "арт",
    "иллюстрацию",
    "иллюстрация",
    "фото",
    "картинку",
    "изображение",
]

# =========================================================
# ЛОГИ
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================================================
# ПАМЯТЬ
# =========================================================

user_histories = {}
user_modes = {}

# =========================================================
# UI
# =========================================================

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["💬 Чат", "🖼 Генерация"],
        ["🤖 Авто", "🧹 Очистить"],
        ["ℹ️ Помощь"],
    ],
    resize_keyboard=True
)

# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def get_headers():
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": HTTP_REFERER,
        "X-Title": X_TITLE,
    }


def normalize_text_content(content):
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts).strip()

    return str(content).strip()


def get_user_history(user_id: int):
    if user_id not in user_histories:
        user_histories[user_id] = []
    return user_histories[user_id]


def add_to_history(user_id: int, role: str, content: str):
    history = get_user_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY_MESSAGES:
        user_histories[user_id] = history[-MAX_HISTORY_MESSAGES:]


def clear_history(user_id: int):
    user_histories[user_id] = []


def get_user_mode(user_id: int):
    return user_modes.get(user_id, DEFAULT_MODE)


def set_user_mode(user_id: int, mode: str):
    user_modes[user_id] = mode


def looks_like_image_request(text: str) -> bool:
    low = text.lower().strip()

    if low.startswith("/img"):
        return True

    for word in IMAGE_TRIGGER_WORDS:
        if word in low:
            return True

    return False


def extract_image_prompt(text: str) -> str:
    low = text.lower().strip()

    if low.startswith("/img"):
        return text[4:].strip()

    prefixes = [
        "нарисуй",
        "сгенерируй",
        "создай картинку",
        "создай изображение",
        "сделай картинку",
        "сделай изображение",
        "draw",
        "generate image",
    ]

    for prefix in prefixes:
        if low.startswith(prefix):
            return text[len(prefix):].strip(" :,-")

    return text.strip()


def split_long_text(text: str, max_len: int = 4000):
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]


# =========================================================
# OPENROUTER API
# =========================================================

def openrouter_chat_request(user_id: int, user_text: str) -> str:
    history = get_user_history(user_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": TEXT_MODEL,
        "messages": messages,
        "temperature": 0.7
    }

    response = requests.post(
        OPENROUTER_CHAT_URL,
        headers=get_headers(),
        json=payload,
        timeout=120
    )
    response.raise_for_status()

    data = response.json()

    if "choices" not in data or not data["choices"]:
        raise ValueError(f"Пустой ответ OpenRouter: {data}")

    content = data["choices"][0]["message"]["content"]
    answer = normalize_text_content(content)

    add_to_history(user_id, "user", user_text)
    add_to_history(user_id, "assistant", answer)

    return answer


def openrouter_image_request(prompt: str) -> bytes:
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "aspect_ratio": "1:1"
    }

    response = requests.post(
        OPENROUTER_IMAGE_URL,
        headers=get_headers(),
        json=payload,
        timeout=180
    )
    response.raise_for_status()

    data = response.json()

    if "data" not in data or not data["data"]:
        raise ValueError(f"Пустой ответ image API: {data}")

    item = data["data"][0]

    # base64 вариант
    if "b64_json" in item and item["b64_json"]:
        return base64.b64decode(item["b64_json"])

    # URL вариант
    image_url = item.get("url") or item.get("image_url")
    if image_url:
        img_response = requests.get(image_url, timeout=180)
        img_response.raise_for_status()
        return img_response.content

    raise ValueError(f"Не удалось получить изображение: {data}")


# =========================================================
# ОБРАБОТКА РЕЖИМОВ
# =========================================================

async def handle_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    user_id = update.effective_user.id

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    answer = await asyncio.to_thread(openrouter_chat_request, user_id, user_text)

    if not answer:
        answer = "Не удалось получить ответ."

    for part in split_long_text(answer):
        await update.message.reply_text(part, reply_markup=MAIN_KEYBOARD)


async def handle_image_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    if not prompt:
        await update.message.reply_text(
            "Напиши описание картинки.",
            reply_markup=MAIN_KEYBOARD
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.UPLOAD_PHOTO
    )

    await update.message.reply_text(
        "Генерирую картинку, подожди...",
        reply_markup=MAIN_KEYBOARD
    )

    image_bytes = await asyncio.to_thread(openrouter_image_request, prompt)

    image_file = io.BytesIO(image_bytes)
    image_file.name = "generated.png"
    image_file.seek(0)

    await update.message.reply_photo(
        photo=image_file,
        caption=f"Готово.\nЗапрос: {prompt}",
        reply_markup=MAIN_KEYBOARD
    )


# =========================================================
# КОМАНДЫ
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_modes:
        set_user_mode(user_id, DEFAULT_MODE)

    text = (
        "Привет. Я бот на OpenRouter.\n\n"
        "Что умею:\n"
        "• отвечать на сообщения\n"
        "• генерировать картинки\n"
        "• работать в режиме Авто / Чат / Генерация\n\n"
        "Команды:\n"
        "/start — запуск\n"
        "/help — помощь\n"
        "/clear — очистить память\n"
        "/mode_auto — авто режим\n"
        "/mode_chat — режим чата\n"
        "/mode_image — режим генерации\n"
        "/img <описание> — сгенерировать картинку\n\n"
        "Примеры:\n"
        "• Привет, напиши код калькулятора на Python\n"
        "• /img красный спорткар в неоновом городе\n"
        "• нарисуй кота в космосе"
    )

    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Помощь по боту:\n\n"
        "1. Просто напиши сообщение — я отвечу.\n"
        "2. Используй /img описание — я сделаю картинку.\n"
        "3. В режиме '🤖 Авто' я сам пытаюсь понять, нужен текст или изображение.\n\n"
        "Кнопки:\n"
        "💬 Чат — всегда текстовые ответы\n"
        "🖼 Генерация — все сообщения считаются запросом на картинку\n"
        "🤖 Авто — бот сам решает\n"
        "🧹 Очистить — очищает память диалога\n"
        "ℹ️ Помощь — показывает эту справку"
    )
    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_history(user_id)
    await update.message.reply_text("Память очищена.", reply_markup=MAIN_KEYBOARD)


async def mode_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_mode(user_id, "auto")
    await update.message.reply_text("Режим установлен: АВТО.", reply_markup=MAIN_KEYBOARD)


async def mode_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_mode(user_id, "chat")
    await update.message.reply_text("Режим установлен: ЧАТ.", reply_markup=MAIN_KEYBOARD)


async def mode_image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_mode(user_id, "image")
    await update.message.reply_text("Режим установлен: ГЕНЕРАЦИЯ.", reply_markup=MAIN_KEYBOARD)


async def img_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args).strip()

    if not prompt:
        await update.message.reply_text(
            "Напиши так:\n/img описание картинки",
            reply_markup=MAIN_KEYBOARD
        )
        return

    try:
        await handle_image_reply(update, context, prompt)
    except Exception as e:
        logger.exception("Ошибка генерации изображения")
        await update.message.reply_text(
            f"Ошибка генерации картинки:\n{e}",
            reply_markup=MAIN_KEYBOARD
        )


# =========================================================
# ОБРАБОТКА КНОПОК И СООБЩЕНИЙ
# =========================================================

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()
    user_id = update.effective_user.id

    # Кнопки
    if user_text == "💬 Чат":
        set_user_mode(user_id, "chat")
        await update.message.reply_text(
            "Теперь я в режиме ЧАТ. Все сообщения будут обрабатываться как обычный текст.",
            reply_markup=MAIN_KEYBOARD
        )
        return

    if user_text == "🖼 Генерация":
        set_user_mode(user_id, "image")
        await update.message.reply_text(
            "Теперь я в режиме ГЕНЕРАЦИИ. Следующее сообщение будет считаться описанием картинки.",
            reply_markup=MAIN_KEYBOARD
        )
        return

    if user_text == "🤖 Авто":
        set_user_mode(user_id, "auto")
        await update.message.reply_text(
            "Теперь я в режиме АВТО. Я сам попробую понять: ответить текстом или сгенерировать картинку.",
            reply_markup=MAIN_KEYBOARD
        )
        return

    if user_text == "🧹 Очистить":
        clear_history(user_id)
        await update.message.reply_text(
            "Память диалога очищена.",
            reply_markup=MAIN_KEYBOARD
        )
        return

    if user_text == "ℹ️ Помощь":
        await help_command(update, context)
        return

    # Игнорируем команды
    if user_text.startswith("/"):
        return

    mode = get_user_mode(user_id)

    try:
        # Режим image
        if mode == "image":
            await handle_image_reply(update, context, user_text)
            return

        # Режим chat
        if mode == "chat":
            await handle_text_reply(update, context, user_text)
            return

        # Режим auto
        if looks_like_image_request(user_text):
            prompt = extract_image_prompt(user_text)
            await handle_image_reply(update, context, prompt)
        else:
            await handle_text_reply(update, context, user_text)

    except Exception as e:
        logger.exception("Ошибка обработки сообщения")
        await update.message.reply_text(
            f"Ошибка:\n{e}",
            reply_markup=MAIN_KEYBOARD
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error", exc_info=context.error)


# =========================================================
# ЗАПУСК
# =========================================================

def main():
    if "PASTE_TELEGRAM_BOT_TOKEN_HERE" in TELEGRAM_BOT_TOKEN:
        raise ValueError("Вставь TELEGRAM_BOT_TOKEN в код.")
    if "PASTE_OPENROUTER_API_KEY_HERE" in OPENROUTER_API_KEY:
        raise ValueError("Вставь OPENROUTER_API_KEY в код.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("img", img_command))
    app.add_handler(CommandHandler("mode_auto", mode_auto_command))
    app.add_handler(CommandHandler("mode_chat", mode_chat_command))
    app.add_handler(CommandHandler("mode_image", mode_image_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    app.add_error_handler(error_handler)

    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
