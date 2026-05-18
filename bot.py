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
import random
import sqlite3
from io import BytesIO
from urllib.parse import quote
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests

from telegram import (
    Update,
    LabeledPrice,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)


# ================= НАСТРОЙКИ =================

TELEGRAM_BOT_TOKEN = "8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo"

OPENROUTER_API_KEY = "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"
TAVILY_API_KEY = "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"

OWNER_ID = 8343382233

DAILY_LIMIT = 5
VIP_DAILY_LIMIT = 30
VIP_DAYS = 30
VIP_PRICE_STARS = 90
VIP_PAYLOAD = "vip_30_days_90_stars"

# Если хочешь бесплатную модель:
OPENROUTER_MODEL = "openai/gpt-5-mini"

# Если у тебя есть доступ к gpt-5-mini, можешь поставить:
# OPENROUTER_MODEL = "openai/gpt-5-mini"

MAX_HISTORY_MESSAGES = 6
MAX_TOKENS = 900

# Генерация фото
IMAGE_API_BASE = "https://gen.pollinations.ai/image/"
IMAGE_MODEL = "flux"

# Если есть Pollinations key — вставь, если нет — оставь пустым
POLLINATIONS_API_KEY = "sk_3kAOcZInuCCJeU304O1gfheQK6Nb33yu"

DB_NAME = "ai_bot_stars.db"

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
Не говори, что ты OpenRouter, GPT или языковая модель.
"""


# ================= БАЗА ДАННЫХ =================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            vip_until TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS stars_payments (
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


def today_key():
    return date.today().isoformat()


def save_user_info(user):
    if not user:
        return

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (user_id, username, full_name, vip_until)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name
    """, (
        user.id,
        user.username or "",
        user.full_name or ""
    ))

    conn.commit()
    conn.close()


def save_stars_payment(user, stars, charge_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO stars_payments (
            user_id, username, full_name,
            stars, charge_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user.id,
        user.username or "",
        user.full_name or "",
        stars,
        charge_id or "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    payment_id = cur.lastrowid

    conn.commit()
    conn.close()

    return payment_id


def get_usage_count(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        "SELECT count FROM usage WHERE user_id = ? AND day = ?",
        (user_id, today_key())
    )

    row = cur.fetchone()
    conn.close()

    if row:
        return row[0]

    return 0


def add_usage(user_id: int):
    if user_id == OWNER_ID:
        return

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO usage (user_id, day, count)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, day) DO UPDATE SET
            count = count + 1
    """, (
        user_id,
        today_key()
    ))

    conn.commit()
    conn.close()


def get_vip_until(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute(
        "SELECT vip_until FROM users WHERE user_id = ?",
        (user_id,)
    )

    row = cur.fetchone()
    conn.close()

    if not row or not row[0]:
        return None

    return row[0]


def is_vip(user_id: int) -> bool:
    vip_until = get_vip_until(user_id)

    if not vip_until:
        return False

    try:
        vip_date = datetime.strptime(vip_until, "%Y-%m-%d").date()
        return vip_date >= date.today()
    except Exception:
        return False


def grant_vip(user_id: int):
    old_until = get_vip_until(user_id)

    today = date.today()

    if old_until:
        try:
            old_date = datetime.strptime(old_until, "%Y-%m-%d").date()
            if old_date >= today:
                vip_until = old_date + timedelta(days=VIP_DAYS)
            else:
                vip_until = today + timedelta(days=VIP_DAYS)
        except Exception:
            vip_until = today + timedelta(days=VIP_DAYS)
    else:
        vip_until = today + timedelta(days=VIP_DAYS)

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (user_id, username, full_name, vip_until)
        VALUES (?, '', '', ?)
        ON CONFLICT(user_id) DO UPDATE SET
            vip_until = excluded.vip_until
    """, (
        user_id,
        vip_until.isoformat()
    ))

    conn.commit()
    conn.close()

    return vip_until.isoformat()


def get_daily_limit(user_id: int):
    if user_id == OWNER_ID:
        return None

    if is_vip(user_id):
        return VIP_DAILY_LIMIT

    return DAILY_LIMIT


def can_use_bot(user_id: int):
    limit = get_daily_limit(user_id)

    if limit is None:
        return True, "∞"

    used = get_usage_count(user_id)

    if used >= limit:
        return False, 0

    return True, limit - used


def left_after_request(user_id: int):
    limit = get_daily_limit(user_id)

    if limit is None:
        return "∞"

    used = get_usage_count(user_id)
    left = limit - used

    if left < 0:
        left = 0

    return left


def count_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    users_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE vip_until IS NOT NULL")
    vip_count = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(stars), 0) FROM stars_payments")
    stars_sum = cur.fetchone()[0]

    conn.close()

    return users_count, vip_count, stars_sum


# ================= API ИИ / ПОИСК / ФОТО =================

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


def ask_openrouter_sync(user_id: int, user_text: str, search_context: str = "") -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"

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
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://t.me/",
        "X-Title": "Telegram AI Bot"
    }

    payload = {
        "model": OPENROUTER_MODEL,
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


def generate_image_sync(prompt: str) -> bytes:
    encoded_prompt = quote(prompt)
    url = f"{IMAGE_API_BASE}{encoded_prompt}"

    params = {
        "model": IMAGE_MODEL,
        "width": 1024,
        "height": 1024,
        "seed": random.randint(1, 999999999),
        "nologo": "true",
        "safe": "true",
    }

    if POLLINATIONS_API_KEY:
        params["key"] = POLLINATIONS_API_KEY

    response = requests.get(
        url,
        params=params,
        timeout=240
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Image API error {response.status_code}: {response.text[:1000]}"
        )

    content_type = response.headers.get("Content-Type", "")

    if "image" not in content_type:
        raise RuntimeError(
            f"API вернул не картинку. Content-Type: {content_type}. "
            f"Ответ: {response.text[:1000]}"
        )

    return response.content


# ================= TELEGRAM =================

async def send_long(update: Update, text: str):
    if len(text) <= 4096:
        await update.message.reply_text(text)
        return

    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i + 4096])


async def check_limit(update: Update) -> bool:
    user_id = update.effective_user.id
    save_user_info(update.effective_user)

    allowed, left = can_use_bot(user_id)

    if allowed:
        return True

    limit = get_daily_limit(user_id)

    await update.message.reply_text(
        "⛔ Лимит на сегодня закончился.\n\n"
        f"Твой лимит: {limit} запросов в день.\n\n"
        "Хочешь больше? Купи VIP:\n"
        "/vip"
    )

    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user_info(user)

    if user.id == OWNER_ID:
        limit_text = "Твой лимит: ∞ без ограничений"
    else:
        used = get_usage_count(user.id)
        daily_limit = get_daily_limit(user.id)
        left = daily_limit - used
        if left < 0:
            left = 0

        if is_vip(user.id):
            vip_until = get_vip_until(user.id)
            limit_text = (
                f"Статус: ⭐ VIP до {vip_until}\n"
                f"Осталось: {left}/{daily_limit} запросов сегодня"
            )
        else:
            limit_text = (
                f"Статус: обычный пользователь\n"
                f"Осталось: {left}/{daily_limit} запросов сегодня"
            )

    await update.message.reply_text(
        "Привет! Я ИИ-бот с поиском и генерацией фото 🤖\n\n"
        f"{limit_text}\n\n"
        "Команды:\n"
        "/search запрос — поиск в интернете\n"
        "/imagine описание — сгенерировать фото\n"
        "/vip — купить VIP за 90 ⭐\n"
        "/myvip — проверить VIP\n"
        "/limit — узнать лимит\n"
        "/reset — очистить историю\n\n"
        "Можно просто написать вопрос обычным сообщением."
    )


async def limit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user_info(user)

    if user.id == OWNER_ID:
        await update.message.reply_text("Твой лимит: ∞ без ограничений")
        return

    used = get_usage_count(user.id)
    daily_limit = get_daily_limit(user.id)
    left = daily_limit - used

    if left < 0:
        left = 0

    await update.message.reply_text(
        "📊 Твой лимит на сегодня:\n\n"
        f"Использовано: {used}/{daily_limit}\n"
        f"Осталось: {left}\n\n"
        f"Обычный лимит: {DAILY_LIMIT}\n"
        f"VIP лимит: {VIP_DAILY_LIMIT}"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history[user_id].clear()
    await update.message.reply_text("История очищена ✅")


async def vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user_info(user)

    if user.id == OWNER_ID:
        await update.message.reply_text(
            "👑 Ты владелец, тебе VIP не нужен.\n\n"
            "У тебя лимит: ∞"
        )
        return

    if is_vip(user.id):
        vip_until = get_vip_until(user.id)
        await update.message.reply_text(
            "⭐ У тебя уже активен VIP.\n\n"
            f"Действует до: {vip_until}\n"
            f"Лимит: {VIP_DAILY_LIMIT} запросов в день"
        )
        return

    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="VIP-доступ",
        description=(
            f"VIP на {VIP_DAYS} дней.\n"
            f"{VIP_DAILY_LIMIT} запросов в день вместо {DAILY_LIMIT}."
        ),
        payload=VIP_PAYLOAD,
        provider_token="",
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"VIP на {VIP_DAYS} дней",
                amount=VIP_PRICE_STARS
            )
        ],
    )


async def myvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user_info(user)

    if user.id == OWNER_ID:
        await update.message.reply_text(
            "👑 Ты владелец.\n\n"
            "Лимит: ∞ без ограничений"
        )
        return

    if is_vip(user.id):
        vip_until = get_vip_until(user.id)

        await update.message.reply_text(
            "⭐ У тебя активен VIP\n\n"
            f"Действует до: {vip_until}\n"
            f"Лимит: {VIP_DAILY_LIMIT} запросов в день"
        )
    else:
        await update.message.reply_text(
            "У тебя нет VIP.\n\n"
            f"Обычный лимит: {DAILY_LIMIT} запросов в день\n"
            f"VIP: {VIP_DAILY_LIMIT} запросов в день\n\n"
            "Купить VIP: /vip"
        )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query

    if query.invoice_payload != VIP_PAYLOAD:
        await query.answer(
            ok=False,
            error_message="Ошибка заказа."
        )
        return

    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payment = update.message.successful_payment

    save_user_info(user)

    if payment.currency != "XTR":
        await update.message.reply_text("Ошибка: оплата была не в Telegram Stars.")
        return

    if payment.invoice_payload != VIP_PAYLOAD:
        await update.message.reply_text("Ошибка: неизвестный товар.")
        return

    if payment.total_amount != VIP_PRICE_STARS:
        await update.message.reply_text("Ошибка: неправильная сумма оплаты.")
        return

    vip_until = grant_vip(user.id)

    payment_id = save_stars_payment(
        user=user,
        stars=payment.total_amount,
        charge_id=payment.telegram_payment_charge_id
    )

    await update.message.reply_text(
        "🎉 Оплата прошла успешно!\n\n"
        "VIP активирован ✅\n\n"
        f"Лимит теперь: {VIP_DAILY_LIMIT} запросов в день\n"
        f"VIP действует до: {vip_until}"
    )

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                "💰 Новая покупка VIP\n\n"
                f"Платёж №{payment_id}\n"
                f"Пользователь: {user.full_name}\n"
                f"Username: @{user.username if user.username else 'нет'}\n"
                f"User ID: {user.id}\n"
                f"Оплачено: {VIP_PRICE_STARS} ⭐\n"
                f"VIP до: {vip_until}\n"
                f"Charge ID: {payment.telegram_payment_charge_id}"
            )
        )
    except Exception:
        pass


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_limit(update):
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
            ask_openrouter_sync,
            update.effective_user.id,
            query,
            search_context
        )

        if sources:
            answer += "\n\nИсточники:\n" + "\n\n".join(sources[:5])

        add_usage(update.effective_user.id)

        if update.effective_user.id != OWNER_ID:
            left = left_after_request(update.effective_user.id)
            daily_limit = get_daily_limit(update.effective_user.id)
            answer += f"\n\nОсталось запросов сегодня: {left}/{daily_limit}"

        await send_long(update, answer)

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text(
            "Ошибка поиска/ИИ:\n\n"
            f"{str(e)[:1500]}"
        )


async def imagine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_limit(update):
        return

    prompt = " ".join(context.args).strip()

    if not prompt:
        await update.message.reply_text(
            "Напиши описание после команды.\n\n"
            "Пример:\n"
            "/imagine робот в неоновом городе, реализм"
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.UPLOAD_PHOTO
    )

    try:
        await update.message.reply_text("Генерирую фото... 🎨")

        image_bytes = await asyncio.to_thread(
            generate_image_sync,
            prompt
        )

        image = BytesIO(image_bytes)
        image.name = "image.jpg"

        add_usage(update.effective_user.id)

        if update.effective_user.id == OWNER_ID:
            caption = "Готово ✅"
        else:
            left = left_after_request(update.effective_user.id)
            daily_limit = get_daily_limit(update.effective_user.id)
            caption = f"Готово ✅\n\nОсталось запросов сегодня: {left}/{daily_limit}"

        await update.message.reply_photo(
            photo=image,
            caption=caption
        )

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text(
            "Ошибка генерации фото:\n\n"
            f"{str(e)[:1500]}"
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ У тебя нет доступа.")
        return

    users_count, vip_count, stars_sum = count_users()

    await update.message.reply_text(
        "📊 Статистика бота\n\n"
        f"Пользователей: {users_count}\n"
        f"VIP пользователей: {vip_count}\n"
        f"Заработано Stars: {stars_sum} ⭐"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_limit(update):
        return

    user_id = update.effective_user.id
    text = update.message.text

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        answer = await asyncio.to_thread(
            ask_openrouter_sync,
            user_id,
            text
        )

        add_usage(user_id)

        if user_id != OWNER_ID:
            left = left_after_request(user_id)
            daily_limit = get_daily_limit(user_id)
            answer += f"\n\nОсталось запросов сегодня: {left}/{daily_limit}"

        await send_long(update, answer)

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text(
            "Ошибка OpenRouter:\n\n"
            f"{str(e)[:1500]}"
        )


def main():
    init_db()

    if TELEGRAM_BOT_TOKEN == "ВСТАВЬ_ТОКЕН_БОТА":
        print("Ошибка: вставь TELEGRAM_BOT_TOKEN.")
        return

    if OPENROUTER_API_KEY == "ВСТАВЬ_OPENROUTER_API_KEY":
        print("Ошибка: вставь OPENROUTER_API_KEY.")
        return

    if TAVILY_API_KEY == "ВСТАВЬ_TAVILY_API_KEY":
        print("Ошибка: вставь TAVILY_API_KEY.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("limit", limit_command))
    app.add_handler(CommandHandler("vip", vip_command))
    app.add_handler(CommandHandler("myvip", myvip_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("imagine", imagine_command))
    app.add_handler(CommandHandler("stats", stats_command))

    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("AI bot + VIP Stars запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
