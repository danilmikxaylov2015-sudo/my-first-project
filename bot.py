#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import importlib
import os

# ==============================================
#  АВТОУСТАНОВЩИК ВСЕХ ЗАВИСИМОСТЕЙ
# ==============================================

REQUIRED_PACKAGES = ['pyTelegramBotAPI', 'requests']

def install_package(package):
    print(f"📦 Устанавливаю {package}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

def check_and_install():
    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            if pkg == 'pyTelegramBotAPI':
                importlib.import_module('telebot')
            else:
                importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("⚠️ Отсутствуют пакеты:", ', '.join(missing))
        for pkg in missing:
            install_package(pkg)
        print("✅ Все зависимости установлены.")
    else:
        print("✅ Все зависимости уже установлены.")

# Запускаем проверку до импорта остальных модулей
check_and_install()

# ==============================================
#  ТЕПЕРЬ МОЖНО ИМПОРТИРОВАТЬ
# ==============================================

import telebot
import requests
import json
import time
import re

# ===== КОНФИГ (ТВОИ ДАННЫЕ УЖЕ ВСТАВЛЕНЫ) =====
TOKEN = '8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo'
SHODAN_API_KEY = 'YierkuPU86aVZyIHiVyCD4xsI5IPxqZx'
bot = telebot.TeleBot(TOKEN)

# ===== ФУНКЦИЯ ПОИСКА КАМЕР (через Shodan API) =====
def search_cameras(query='rtsp port:554', limit=5):
    url = f"https://api.shodan.io/shodan/host/search?key={SHODAN_API_KEY}&query={query}&limit={limit}"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return {'error': f"HTTP {resp.status_code}: {resp.text}"}
        data = resp.json()
        matches = data.get('matches', [])
        cameras = []
        for m in matches:
            ip = m['ip_str']
            port = m['port']
            rtsp_url = f"rtsp://{ip}:{port}/live.sdp"
            # Если в баннере есть другой путь — используем его
            banner = m.get('data', '')
            match = re.search(r'rtsp://[^\s]+', banner)
            if match:
                rtsp_url = match.group(0)
            cameras.append({
                'ip': ip,
                'port': port,
                'url': rtsp_url,
                'country': m.get('location', {}).get('country_name', 'Unknown'),
                'org': m.get('org', 'Unknown')
            })
        return cameras
    except Exception as e:
        return {'error': str(e)}

# ===== КОМАНДЫ БОТА =====
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message,
        "📡 Бот для поиска открытых RTSP-камер.\n"
        "/cameras [страна] — найти камеры (по умолчанию все)\n"
        "/cameras Russia — камеры в России\n"
        "/help — справка"
    )

@bot.message_handler(commands=['cameras'])
def cameras_command(message):
    args = message.text.split()
    country = args[1] if len(args) > 1 else ''
    query = 'rtsp port:554'
    if country:
        query += f' country:{country}'
    bot.reply_to(message, f"🔍 Ищу камеры по запросу: {query}")
    results = search_cameras(query, limit=5)
    if 'error' in results:
        bot.reply_to(message, f"❌ Ошибка: {results['error']}")
        return
    if not results:
        bot.reply_to(message, "❌ Камеры не найдены.")
        return
    reply = "📹 **Найденные камеры:**\n\n"
    for i, cam in enumerate(results, 1):
        reply += f"{i}. IP: {cam['ip']}:{cam['port']}\n"
        reply += f"   Страна: {cam['country']}\n"
        reply += f"   Провайдер: {cam['org']}\n"
        reply += f"   RTSP-поток: `{cam['url']}`\n\n"
    bot.reply_to(message, reply, parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def help_cmd(message):
    bot.reply_to(message,
        "/cameras — показать 5 камер\n"
        "/cameras France — камеры во Франции"
    )

# ===== ЗАПУСК =====
if __name__ == '__main__':
    print("🔥 Бот-поисковик камер запущен.")
    print(f"Используется API-ключ Shodan: {SHODAN_API_KEY[:4]}...")
    bot.infinity_polling()
