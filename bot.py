import sys
import subprocess
import asyncio
import logging
import io
import os
import urllib.request

# ==========================================
# 1. АВТОМАТИЧЕСКАЯ УСТАНОВКА ЗАВИСИМОСТЕЙ
# ==========================================
def install_dependencies():
    # Словарь: модуль для проверки -> пакет для установки через pip
    packages = {
        "aiogram": "aiogram==3.4.1",
        "PIL": "Pillow"
    }
    for module, pkg in packages.items():
        try:
            __import__(module)
        except ImportError:
            print(f"[{pkg}] не найден. Начинаю установку...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
                print(f"[{pkg}] успешно установлен!")
            except Exception as e:
                print(f"Ошибка при установке {pkg}: {e}")

install_dependencies()

# ==========================================
# 2. ИМПОРТЫ 
# ==========================================
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 3. НАСТРОЙКИ (ЗАПОЛНИ ПОД СЕБЯ)
# ==========================================
BOT_TOKEN = "8778362559:AAGYlu7WG0u8J9Uw_-nQbpvhIpdZW56ZxGo"

WATERMARK_TEXT = "@kiloai"
BUTTON_TEXT = "🔥 Подписаться на канал"
BUTTON_URL = "https://t.me/kiloai" # Ссылка на твой канал или ресурс

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ==========================================
# 4. ЛОГИКА ВОДЯНОГО ЗНАКА
# ==========================================
def get_font(size: int):
    """Автоматически скачивает красивый шрифт, если его нет"""
    font_path = "Roboto-Bold.ttf"
    if not os.path.exists(font_path):
        url = "https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Bold.ttf"
        try:
            urllib.request.urlretrieve(url, font_path)
            print("Шрифт успешно скачан!")
        except Exception:
            return ImageFont.load_default() # Резервный вариант
    return ImageFont.truetype(font_path, size)

def add_watermark(image_bytes: io.BytesIO, watermark_text: str) -> io.BytesIO:
    """Накладывает водяной знак на изображение"""
    with Image.open(image_bytes) as img:
        img = img.convert("RGBA")
        width, height = img.size
        
        # Динамический размер шрифта в зависимости от ширины картинки
        font_size = max(20, int(width / 20))
        font = get_font(font_size)
        
        # Создаем прозрачный слой для текста
        txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)
        
        # Вычисляем размер текста
        try:
            bbox = draw.textbbox((0, 0), watermark_text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
        except AttributeError:
            text_width, text_height = draw.textsize(watermark_text, font=font)
            
        # Позиция: правый нижний угол с отступом
        margin = int(width * 0.03)
        x = width - text_width - margin
        y = height - text_height - margin
        
        # Рисуем черную тень для читаемости на белом фоне
        shadow_color = (0, 0, 0, 180)
        draw.text((x-2, y-2), watermark_text, font=font, fill=shadow_color)
        draw.text((x+2, y+2), watermark_text, font=font, fill=shadow_color)
        
        # Рисуем сам белый текст
        draw.text((x, y), watermark_text, font=font, fill=(255, 255, 255, 230))
        
        # Накладываем текст на картинку
        out = Image.alpha_composite(img, txt_layer).convert("RGB")
        
        # Сохраняем результат в память
        result_bytes = io.BytesIO()
        out.save(result_bytes, format="JPEG", quality=95)
        result_bytes.seek(0)
        return result_bytes

def get_keyboard() -> InlineKeyboardMarkup:
    """Генерирует клавиатуру с кнопкой"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BUTTON_TEXT, url=BUTTON_URL)]
    ])

# ==========================================
# 5. ОБРАБОТЧИКИ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🎨 <b>Я бот-оформитель!</b>\n\n"
        "Скинь мне <b>фотографию</b>, и я наложу на нее водяной знак и добавлю красивую кнопку.\n"
        "Скинь <b>видео или GIF</b>, и я просто прикреплю к ним кнопку.\n\n"
        "<i>Идеально для подготовки постов перед пересылкой в канал!</i>"
    )

@dp.message(F.photo)
async def process_photo(message: types.Message):
    wait_msg = await message.answer("⏳ Обрабатываю фотографию...")
    
    # Скачиваем фото в память
    file_id = message.photo[-1].file_id
    file = await bot.get_file(file_id)
    photo_stream = io.BytesIO()
    await bot.download_file(file.file_path, photo_stream)
    
    # Накладываем водяной знак
    photo_stream.seek(0)
    result_stream = await asyncio.to_thread(add_watermark, photo_stream, WATERMARK_TEXT)
    
    # Отправляем обратно с кнопкой и старым текстом
    ready_photo = BufferedInputFile(result_stream.read(), filename="watermarked.jpg")
    await wait_msg.delete()
    await message.answer_photo(
        photo=ready_photo,
        caption=message.caption or "",
        reply_markup=get_keyboard()
    )

@dp.message(F.video | F.animation)
async def process_video(message: types.Message):
    # Видео и анимации просто переотправляем, добавляя кнопку
    if message.video:
        await message.answer_video(
            video=message.video.file_id,
            caption=message.caption or "",
            reply_markup=get_keyboard()
        )
    elif message.animation:
        await message.answer_animation(
            animation=message.animation.file_id,
            caption=message.caption or "",
            reply_markup=get_keyboard()
        )

@dp.message(~F.photo & ~F.video & ~F.animation & ~Command("start"))
async def catch_others(message: types.Message):
    await message.answer("Пожалуйста, отправь мне фото, видео или GIF.")

# ==========================================
# 6. ЗАПУСК
# ==========================================
async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен вручную.")
