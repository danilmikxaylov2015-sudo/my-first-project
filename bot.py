#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import importlib
import os

REQUIRED_PACKAGES = ['pyTelegramBotAPI', 'requests']
def install_package(package):
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
        for pkg in missing:
            install_package(pkg)
check_and_install()

import telebot
import requests
import re

TOKEN = '8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo'
SHODAN_API_KEY = 'YierkuPU86aVZyIHiVyCD4xsI5IPxqZx'
bot = telebot.TeleBot(TOKEN)

def search_cameras(query, limit=5):
    url = f"https://api.shodan.io/shodan/host/search?key={SHODAN_API_KEY}&query={query}&limit={limit}"
    resp = requests.get(url, timeout=15)
    if resp.status_code == 403:
        # Пробуем без фильтра country
        if 'country:' in query:
            new_query = re.sub(r'country:\S+', '', query).strip()
            if not new_query:
                new_query = 'rtsp port:554'
            return search_cameras(new_query, limit)
        return {'error': '403 Forbidden — возможно, закончился лимит или неверный ключ'}
    if resp.status_code != 200:
        return {'error': f'HTTP {resp.status_code}: {resp.text}'}
    data = resp.json()
    matches = data.get('matches', [])
    cameras = []
    for m in matches:
        ip = m['ip_str']
        port = m['port']
        rtsp_url = f"rtsp://{ip}:{port}/live.sdp"
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

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message,
        "📡 Бот для поиска открытых RTSP-камер (Shodan).\n"
        "/cameras — найти 5 случайных камер\n"
        "/cameras Russia — поиск по стране (если не сработает, уберёт фильтр)"
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
    if isinstance(results, dict) and 'error' in results:
        bot.reply_to(message, f"❌ {results['error']}")
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
    bot.reply_to(message, "/cameras — найти камеры")

if __name__ == '__main__':
    print("🔥 Бот запущен. Ошибка 403 автоматически обходится.")
    bot.infinity_polling()
