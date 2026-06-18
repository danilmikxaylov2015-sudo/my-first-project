#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import importlib
import os
import time

# ==============================================
#  АВТОУСТАНОВЩИК ЗАВИСИМОСТЕЙ
#  СКРИПТ ПРОВЕРЯЕТ И УСТАНАВЛИВАЕТ ВСЁ САМ
# ==============================================

REQUIRED_PACKAGES = [
    'telebot',               # pyTelegramBotAPI
    'selenium',
    'webdriver_manager',
    'phonenumbers',          # для примера, можно убрать
]

def install_package(package):
    """Устанавливает пакет через pip."""
    print(f"📦 Устанавливаю {package}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

def check_and_install():
    """Проверяет наличие пакетов и устанавливает недостающие."""
    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            # Для пакетов с дефисом (например, telebot) импорт может отличаться
            if pkg == 'telebot':
                importlib.import_module('telebot')
            else:
                importlib.import_module(pkg.replace('-', '_'))
        except ImportError:
            missing.append(pkg)
    if missing:
        print("⚠️ Отсутствуют пакеты:", ', '.join(missing))
        for pkg in missing:
            install_package(pkg)
        print("✅ Все зависимости установлены.")
    else:
        print("✅ Все зависимости уже установлены.")

# Запускаем проверку перед основным кодом
check_and_install()

# ==============================================
#  ТЕПЕРЬ МОЖНО ИМПОРТИРОВАТЬ ВСЁ ОСТАЛЬНОЕ
# ==============================================

import telebot
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import random
import string
import time
import logging
import json
import os
from pathlib import Path

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===== КОНФИГ БОТА =====
CONFIG_FILE = 'config.json'

def load_config():
    """Загружает конфигурацию из файла или создает новый файл."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    else:
        config = {
            'TOKEN': 'ВСТАВЬТЕ_ВАШЕ_ТОКЕН_ЗДЕСЬ',
            'DEBUG': True
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        logger.warning(f"⚠️ Создан файл {CONFIG_FILE}. Пожалуйста, добавьте ваш TOKEN бота!")
        return config

config = load_config()
TOKEN = config.get('TOKEN', '')

if TOKEN == 'ВСТАВЬТЕ_ВАШЕ_ТОКЕН_ЗДЕСЬ' or not TOKEN:
    logger.error("❌ TOKEN не установлен! Обновите config.json с вашим токеном от BotFather.")
    logger.error("   Инструкция: https://core.telegram.org/bots/tutorial")
    sys.exit(1)

try:
    bot = telebot.TeleBot(TOKEN)
except Exception as e:
    logger.error(f"❌ Ошибка при инициализации бота: {e}")
    sys.exit(1)

# ===== ФУНКЦИЯ СОЗДАНИЯ АККАУНТА GMAIL =====
def generate_random_string(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def create_gmail_account(first_name, last_name, username, password):
    """
    Автоматическое создание Gmail аккаунта с использованием Selenium.
    Возвращает строку с результатом.
    
    ⚠️ ВНИМАНИЕ: Google активно блокирует автоматизацию.
    Потребуется ручная верификация и CAPTCHA.
    """
    driver = None
    try:
        # Настройка драйвера (автообновление через webdriver-manager)
        service = Service(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        # Добавляем аргументы для обхода антибот-систем (не всегда работает)
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        # Можно запустить в headless-режиме, но тогда капча почти гарантирована
        # options.add_argument('--headless')
        
        logger.info("🚀 Запускаю браузер Chromium...")
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        logger.info("Открываю страницу регистрации Google...")
        driver.get("https://accounts.google.com/signup")
        wait = WebDriverWait(driver, 20)

        # Шаг 1: Имя и фамилия
        logger.info("Ввожу имя и фамилию...")
        first_name_field = wait.until(EC.presence_of_element_located((By.ID, "firstName")))
        first_name_field.clear()
        first_name_field.send_keys(first_name)
        time.sleep(0.5)
        
        last_name_field = driver.find_element(By.ID, "lastName")
        last_name_field.clear()
        last_name_field.send_keys(last_name)
        time.sleep(0.5)
        
        # Нажимаем Далее
        next_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button//span[contains(text(), 'Далее')]")))
        next_btn.click()
        time.sleep(2)

        # Шаг 2: Дата рождения
        logger.info("Ввожу дату рождения...")
        try:
            month_select = wait.until(EC.presence_of_element_located((By.ID, "month")))
            month_select.send_keys("1")  # Январь
            time.sleep(0.3)
            
            day_input = driver.find_element(By.ID, "day")
            day_input.send_keys("01")
            time.sleep(0.3)
            
            year_input = driver.find_element(By.ID, "year")
            year_input.send_keys("1990")
            time.sleep(0.3)
            
            # Пол
            gender_select = driver.find_element(By.ID, "gender")
            gender_select.send_keys("М")
            time.sleep(0.5)
            
            next_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button//span[contains(text(), 'Далее')]")))
            next_btn.click()
            time.sleep(2)
        except Exception as e:
            logger.warning(f"⚠️ Шаг даты рождения пропущен: {e}")

        # Шаг 3: Создание имени пользователя
        logger.info("Ввожу логин...")
        try:
            username_field = wait.until(EC.presence_of_element_located((By.ID, "username")))
            username_field.clear()
            username_field.send_keys(username)
            time.sleep(1)
            
            next_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button//span[contains(text(), 'Далее')]")))
            next_btn.click()
            time.sleep(2)
        except Exception as e:
            logger.error(f"Ошибка при вводе логина: {e}")
            return f"❌ Ошибка: Не удалось ввести логин. Возможно, Google заблокировал автоматизацию."

        # Шаг 4: Пароль
        logger.info("Ввожу пароль...")
        try:
            password_field = wait.until(EC.presence_of_element_located((By.NAME, "Passwd")))
            password_field.send_keys(password)
            time.sleep(0.3)
            
            confirm_password = driver.find_element(By.NAME, "ConfirmPasswd")
            confirm_password.send_keys(password)
            time.sleep(0.5)
            
            next_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button//span[contains(text(), 'Далее')]")))
            next_btn.click()
            time.sleep(2)
        except Exception as e:
            logger.error(f"Ошибка при вводе пароля: {e}")
            return f"❌ Ошибка: Не удалось ввести пароль."

        # Шаг 5: Номер телефона (обычно требуется верификация)
        logger.info("⚠️ Требуется верификация по номеру телефона...")
        logger.info("Откройте браузер вручную для завершения верификации.")
        logger.info("Страница будет открыта 120 секунд для ручного заполнения...")
        
        # Даём 2 минуты на ручную верификацию
        time.sleep(120)
        
        return f"✅ Попытка создания аккаунта {username}@gmail.com завершена. Проверьте браузер для верификации."

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return f"❌ Ошибка при создании аккаунта: {str(e)}"
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# ===== ОБРАБОТЧИКИ TELEGRAM =====

@bot.message_handler(commands=['start'])
def start(message):
    logger.info(f"Новый пользователь: {message.from_user.username}")
    bot.reply_to(message,
        "👋 Привет! Я бот для создания Gmail аккаунтов.\n"
        "Используй команду:\n"
        "/create Имя Фамилия Логин Пароль\n\n"
        "Пример: /create Иван Петров ivanpetrov123 Qwerty2024!"
    )

@bot.message_handler(commands=['create'])
def create_account(message):
    args = message.text.split()
    if len(args) != 5:
        bot.reply_to(message, "❌ Неверный формат. Нужно: /create Имя Фамилия Логин Пароль")
        return
    
    _, first_name, last_name, username, password = args
    
    # Проверка на минимальную длину пароля
    if len(password) < 8:
        bot.reply_to(message, "❌ Пароль должен быть не короче 8 символов.")
        return
    
    # Проверка на наличие цифр и спецсимволов в пароле
    if not any(c.isdigit() for c in password):
        bot.reply_to(message, "❌ Пароль должен содержать хотя бы одну цифру.")
        return
    
    bot.reply_to(message, "⏳ Начинаю создание аккаунта...\n⚠️ Потребуется верификация по SMS/телефону (2 минуты).")
    logger.info(f"Создание аккаунта: {username} для пользователя {message.from_user.username}")
    
    result = create_gmail_account(first_name, last_name, username, password)
    bot.reply_to(message, result)

@bot.message_handler(commands=['help'])
def help_command(message):
    bot.reply_to(message,
        "📌 Доступные команды:\n"
        "/start — приветствие\n"
        "/create Имя Фамилия Логин Пароль — создать Gmail\n"
        "/help — справка\n"
        "/status — статус бота\n\n"
        "ℹ️ Google требует CAPTCHA и SMS верификацию.\n"
        "Процесс может занять 5-10 минут."
    )

@bot.message_handler(commands=['status'])
def status(message):
    bot.reply_to(message, "✅ Бот работает корректно!")

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    bot.reply_to(message, "ℹ️ Неизвестная команда. Используйте /help для справки.")

# ===== ЗАПУСК БОТА =====
if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("🔥 Gmail BOT запущен!")
    logger.info("=" * 50)
    logger.info("Команды:")
    logger.info("  /start - начало")
    logger.info("  /create Имя Фамилия Логин Пароль - создать аккаунт")
    logger.info("  /help - справка")
    logger.info("=" * 50)
    
    try:
        bot.infinity_polling()
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем.")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
