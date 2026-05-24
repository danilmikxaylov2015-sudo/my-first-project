import os
import sys
import subprocess
import re
import json
import threading
import time
import random
import requests
from io import BytesIO
from datetime import datetime

# Автоматическая установка всех библиотек
def install_libs():
    try:
        import vk_api
        from vk_api.longpoll import VkLongPoll, VkEventType
        from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    except ImportError:
        print("📥 Устанавливаю необходимые библиотеки (vk-api, Pillow, requests)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vk-api", "requests", "Pillow"])

install_libs()

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from PIL import Image, ImageDraw, ImageFont

# ==================== НАСТРОЙКИ ВЛАДЕЛЬЦА ====================
USER_TOKEN = "vk1.a.edynZWBJGgef-lj0kOg-OdqtEzdzTm6YwntGyuzMSe8lf53NmWCYCsEW1XCyVTDZnjLnzeamx52N1grIhvo3Ovm7ykq081C7224Qo_uP8ls_tFptamaBjr-1tX6quT3IXUXDkQ9_UL0E1Ye39vGwNwsor7IOzJtx25w82uJXLcLgLmwQuTUtc3nyEclBzFluegboRUL8jb7U4LqFlxo-Pw"
MY_USER_ID = 848213593
TOKEN_FILE = "connected_users.json"
FONT_PATH = "custom_font.ttf"
# =============================================================

# Авто-скачивание красивого шрифта для цитат (чтобы не было багов с текстом)
def download_font():
    if not os.path.exists(FONT_PATH):
        print("📥 Скачиваю красивый шрифт для карточек цитат...")
        try:
            url = "https://github.com/google/fonts/raw/main/ofl/ubuntu/Ubuntu-Medium.ttf"
            r = requests.get(url, timeout=10)
            with open(FONT_PATH, "wb") as f:
                f.write(r.content)
            print("✅ Шрифт успешно загружен!")
        except Exception as e:
            print(f"⚠️ Не удалось скачать шрифт: {e}. Будет использован стандартный.")

download_font()

db_lock = threading.Lock()
account_reactions = {}  
account_negatives = {}  
account_clones = {}     
account_ignores = {}    
user_nicknames = {}    
connected_users = {}
active_threads = {}

NEG_LINES = [
    "да пошел ты",
    "ты зачем вообще клавиатуру купил, иди отдохни",
    "твое мнение очень важно (нет, забудь)",
    "а можно кого-то поумнее позвать?",
    "слабо выдал, попробуй еще раз",
    "в чате пахнет слабостью, а, это опять ты написал",
    "помолчи, за умного сойдешь",
    "ты вообще кто такой, потеряйся",
    "мне лень это читать, удали",
    "выдайте ему клоуна за этот бред 🤡"
]

def save_connected_users():
    try:
        data = {}
        with db_lock:
            for uid, udata in connected_users.items():
                if uid == MY_USER_ID:
                    continue
                data[str(uid)] = {"token": udata["token"], "role": udata["role"]}
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка сохранения базы данных: {e}")

def get_target_id(text, msg_info, vk):
    reply_msg = msg_info.get('reply_message')
    if reply_msg:
        return reply_msg['from_id']
    
    mention_match = re.search(r'\[(id\d+|[a-zA-Z0-9_\.]+)\|.*?\]', text)
    if mention_match:
        raw_mention = mention_match.group(1)
        if raw_mention.startswith("id"):
            return int(raw_mention.replace("id", ""))
        else:
            try:
                resolved = vk.utils.resolveScreenName(screen_name=raw_mention)
                if resolved and resolved['type'] == 'user':
                    return resolved['object_id']
            except: pass
    return None

# Функция создания красивого градиента для фона
def create_gradient(width, height):
    base = Image.new("RGBA", (width, height))
    top_color = (45, 25, 60)     # Глубокий фиолетовый
    bottom_color = (115, 40, 75) # Эффектный закатно-розовый
    
    data = []
    for y in range(height):
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * (y / height))
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * (y / height))
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * (y / height))
        data.extend([(r, g, b, 255)] * width)
        
    base.putdata(data)
    return base

# ПОЛНОСТЬЮ АВТОМАТИЧЕСКАЯ ГЕНЕРАЦИЯ КАРТОЧКИ ЦИТАТЫ
def generate_quote_image(avatar_url, author_name, quote_text, date_str):
    width, height = 950, 380
    image = create_gradient(width, height)
    draw = ImageDraw.Draw(image)
    
    # Подгружаем красивый скачанный шрифт с разным размером
    try:
        font_name = ImageFont.truetype(FONT_PATH, 32)
        font_text = ImageFont.truetype(FONT_PATH, 28)
        font_date = ImageFont.truetype(FONT_PATH, 18)
        font_quotes = ImageFont.truetype(FONT_PATH, 90)
    except:
        font_name = font_text = font_date = font_quotes = ImageFont.load_default()

    # Загрузка и закругление аватарки
    try:
        response = requests.get(avatar_url)
        avatar = Image.open(BytesIO(response.content)).convert("RGBA")
        avatar = avatar.resize((200, 200))
        
        # Создаем маску для идеального круга
        mask = Image.new("L", (200, 200), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 200, 200), fill=255)
        
        # Рендерим аватарку на фон
        image.paste(avatar, (50, 75), mask)
        # Белая круглая обводка вокруг авы
        draw.ellipse((48, 73, 252, 277), outline="#ffffff", width=3)
    except Exception as e:
        print(f"Ошибка авы: {e}")
        draw.ellipse((50, 75, 250, 275), fill="#5181b8")

    # Рисуем кавычки на фоне (как в оригинале чат-менеджеров)
    draw.text((290, 30), "“", fill=(255, 255, 255, 40), font=font_quotes)
    draw.text((width - 90, height - 130), "”", fill=(255, 255, 255, 40), font=font_quotes)

    # Имя автора цитаты
    draw.text((290, 85), author_name, fill="#ffffff", font=font_name)
    
    # Дата цитирования
    draw.text((290, 130), date_str, fill=(255, 255, 255, 150), font=font_date)
    
    # Авто-перенос длинного текста цитаты
    wrapped_lines = []
    words = quote_text.split()
    current_line = ""
    for word in words:
        if len(current_line + " " + word) < 42:
            current_line += " " + word if current_line else word
        else:
            wrapped_lines.append(current_line)
            current_line = word
    if current_line:
        wrapped_lines.append(current_line)
    
    wrapped_text = "\n".join(wrapped_lines[:4]) # Ограничение в 4 строки
    
    # Выводим сам текст цитаты
    draw.text((290, 175), f"«{wrapped_text.strip()}»", fill="#ffeb3b", font=font_text)
    
    # Загоняем готовую картинку в буфер обмена (в оперативку)
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output

# ПЕРСОНАЛЬНЫЙ ПОТОК ДЛЯ КАЖДОГО АККАУНТА
def user_longpoll_loop(user_id, token):
    print(f"🌟 Запущен персональный LongPoll-поток для ID {user_id}")
    
    if user_id not in account_clones: account_clones[user_id] = []
    if user_id not in account_negatives: account_negatives[user_id] = []
    if user_id not in account_ignores: account_ignores[user_id] = []
    if user_id not in account_reactions: account_reactions[user_id] = {}

    while True:
        with db_lock:
            is_active = (user_id == MY_USER_ID or user_id in connected_users)
        if not is_active:
            print(f"🛑 Поток для ID {user_id} успешно остановлен.")
            break
            
        try:
            vk_session = vk_api.VkApi(token=token, api_version='5.131')
            vk = vk_session.get_api()
            longpoll = VkLongPoll(vk_session)
            
            for event in longpoll.listen():
                with db_lock:
                    is_active = (user_id == MY_USER_ID or user_id in connected_users)
                if not is_active:
                    break
                    
                if event.type == VkEventType.MESSAGE_NEW:
                    peer_id = event.peer_id
                    text = event.text
                    message_id = event.message_id
                    
                    if user_id == MY_USER_ID:
                        role = "owner"
                    else:
                        with db_lock:
                            role = connected_users.get(user_id, {}).get("role", "пользователь")
                    
                    msg_info = {}
                    from_id = None
                    cmid = None
                    attachments = []
                    
                    if not event.from_me or text.startswith("/"):
                        try:
                            res = vk.messages.getById(message_ids=[message_id])
                            if res and res.get('items'):
                                msg_info = res['items'][0]
                                from_id = msg_info.get('from_id')
                                cmid = msg_info.get('conversation_message_id')
                                attachments = msg_info.get('attachments', [])
                        except Exception as e:
                            print(f"Ошибка getById: {e}")
                            from_id = event.user_id if not event.from_me else user_id
                    else:
                        from_id = user_id

                    # --- АВТО-ФУНКЦИИ ---
                    if not event.from_me and from_id:
                        if from_id in account_ignores.get(user_id, []):
                            try: vk.messages.markAsRead(peer_id=peer_id)
                            except: pass
                            continue  

                        if from_id in account_clones.get(user_id, []) and not text.startswith("/"):
                            try:
                                result = "".join([c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text)])
                                vk.messages.send(peer_id=peer_id, message=result + " 🤡", reply_to=message_id, random_id=random.randint(1, 1000000))
                            except: pass

                        if from_id in account_negatives.get(user_id, []) and not text.startswith("/"):
                            try:
                                vk.messages.send(peer_id=peer_id, message=random.choice(NEG_LINES), reply_to=message_id, random_id=random.randint(1, 1000000))
                            except: pass

                        if from_id in account_reactions.get(user_id, {}) and cmid:
                            try: 
                                time.sleep(0.3)  
                                vk.messages.sendReaction(peer_id=peer_id, conversation_message_id=cmid, reaction_id=int(account_reactions[user_id][from_id]))
                            except Exception as e: 
                                print(f"Отказ отправки реакции: {e}")

                    # --- ОБРАБОТКА КОМАНД СЕЛФ-БОТА ---
                    if event.from_me and text.startswith("/"):
                        
                        # Команды для OWNER
                        if text.startswith(("/подключить", "/роль", "/снять")):
                            if role != "owner":
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ошибка: Данная команда доступна только Владельцу бота!")
                                except: pass
                                continue

                            if text.startswith("/подключить"):
                                try:
                                    token_arg = text[11:].strip()
                                    if not token_arg:
                                        vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите токен! Пример: /подключить vk1.a...")
                                        continue
                                    
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⏳ Проверяю и настраиваю выделенный поток...")
                                    
                                    temp_session = vk_api.VkApi(token=token_arg, api_version='5.131')
                                    temp_vk = temp_session.get_api()
                                    temp_info = temp_vk.users.get()[0]
                                    new_id = temp_info['id']
                                    
                                    with db_lock:
                                        is_in_db = new_id in connected_users
                                    
                                    if is_in_db:
                                        vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"⚠️ Аккаунт id{new_id} уже работает в системе!")
                                    else:
                                        with db_lock:
                                            connected_users[new_id] = {
                                                "token": token_arg,
                                                "role": "пользователь",
                                                "api": temp_vk
                                            }
                                        save_connected_users()
                                        
                                        t = threading.Thread(target=user_longpoll_loop, args=(new_id, token_arg), daemon=True)
                                        t.start()
                                        active_threads[new_id] = t
                                        
                                        msg_success = f"✅ Аккаунт id{new_id} ({temp_info['first_name']}) успешно подключен!\n🎭 Создан персональный LongPoll-поток."
                                        vk.messages.edit(peer_id=peer_id, message_id=message_id, message=msg_success)
                                except Exception as token_err:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка токена: {token_err}")
                                continue

                            elif text.startswith("/роль"):
                                t_id = get_target_id(text, msg_info, vk)
                                with db_lock:
                                    is_connected = t_id in connected_users
                                if t_id and is_connected:
                                    with db_lock:
                                        connected_users[t_id]["role"] = "admin"
                                    save_connected_users()
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователю id{t_id} успешно выдана роль: admin")
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь не подключен к боту.")
                                continue

                            elif text.startswith("/снять"):
                                t_id = get_target_id(text, msg_info, vk)
                                with db_lock:
                                    is_connected = t_id in connected_users
                                if t_id and is_connected:
                                    with db_lock:
                                        del connected_users[t_id]
                                    save_connected_users()
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Пользователь id{t_id} полностью отключен от бота.")
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь не найден в списке привязанных.")
                                continue

                        # ИСПРАВЛЕННАЯ СТРОКА ИЗ ПЕРВОГО СКРИНШОТА (ровно 2 закрывающие скобки в конце)
                        if text.startswith(("/кик", "/спам", "/негатив", "/унегатив", "/клон", "/уклон", "/реакция", "/стопреакция", "/опубликовать", "/группы", "/игнор", "/уигнор", "/пригласить")):
                            if role not in ["owner", "admin"]:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Недостаточно прав! Нужен статус Администратора.")
                                except: pass
                                continue

                        # Обработка админ-команд
                        if text.startswith("/группы"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Ищу открытые группы пользователя...")
                                t_id = get_target_id(text, msg_info, vk) or user_id
                                
                                groups_data = vk.groups.get(user_id=t_id, extended=1, count=25)
                                items = groups_data.get('items', [])
                                
                                if items:
                                    lines = [f"{i}. [club{g['id']}|{g['name']}]" for i, g in enumerate(items, 1)]
                                    res_text = f"📂 Открытые группы пользователя id{t_id} (Всего: {len(items)}):\n" + "\n".join(lines)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=res_text)
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="📁 У пользователя нет открытых групп или они скрыты.")
                            except Exception as e:
                                if "Access denied" in str(e) or "15" in str(e):
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ошибка доступа: Список групп скрыт.")
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка поиска групп: {e}")
                            continue

                        elif text.startswith("/игнор"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_ignores: account_ignores[user_id] = []
                                if t_id not in account_ignores[user_id]:
                                    account_ignores[user_id].append(t_id)
                                    try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в бесшумный игнор")
                                    except: pass
                            continue

                        elif text.startswith("/уигнор"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_ignores and t_id in account_ignores[user_id]:
                                account_ignores[user_id].remove(t_id)
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из игнора")
                                except: pass
                            continue

                        elif text.startswith("/пригласить"):
                            if peer_id <= 2000000000:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Эта команда работает только внутри бесед!")
                                except: pass
                                continue
                            t_id = get_target_id(text, msg_info, vk)
                            if not t_id:
                                arg = text[11:].strip()
                                if arg and arg.isdigit(): t_id = int(arg)
                            if not t_id:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите ID или ответьте на сообщение.")
                                except: pass
                                continue
                            try:
                                vk.messages.addChatUser(chat_id=peer_id - 2000000000, user_id=t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователь [id{t_id}|приглашен].")
                            except Exception as e:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {e}")
                                except: pass
                            continue

                        # ОБНОВЛЕННАЯ АВТОМАТИЧЕСКАЯ КОМАНДА ЦИТАТ
                        elif text.startswith("/цитата"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🎨 Генерирую бесплатную карточку цитаты...")
                                
                                reply_msg = msg_info.get('reply_message')
                                if reply_msg:
                                    target_user_id = reply_msg['from_id']
                                    quote_text = reply_msg['text']
                                    # Конвертируем Unix-время VK сообщения в нормальный вид
                                    date_obj = datetime.fromtimestamp(reply_msg.get('date', time.time()))
                                else:
                                    target_user_id = user_id
                                    quote_text = text[8:].strip()
                                    date_obj = datetime.now()
                                
                                months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
                                date_str = f"{date_obj.day:02d} {months[date_obj.month - 1]} {date_obj.year}, {date_obj.hour:02d}:{date_obj.minute:02d}"
                                
                                if not quote_text:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Напишите текст после команды или ответьте на чьё-то сообщение!")
                                    continue
                                
                                # Получаем аватарку и имя из VK
                                author_info = vk.users.get(user_ids=[target_user_id], fields="photo_max_orig")[0]
                                full_name = f"{author_info['first_name']} {author_info['last_name']}"
                                avatar_url = author_info.get('photo_max_orig')
                                
                                # Генерируем картинку в памяти
                                img_buffer = generate_quote_image(avatar_url, full_name, quote_text, date_str)
                                
                                # Загрузка изображения на сервера ВКонтакте
                                upload_server = vk.photos.getMessagesUploadServer(peer_id=peer_id)
                                upload_url = upload_server['upload_url']
                                
                                files = {'photo': ('quote.png', img_buffer, 'image/png')}
                                upload_req = requests.post(upload_url, files=files).json()
                                
                                save_res = vk.photos.saveMessagesPhoto(
                                    server=upload_req['server'],
                                    photo=upload_req['photo'],
                                    hash=upload_req['hash']
                                )[0]
                                
                                attachment = f"photo{save_res['owner_id']}_{save_res['id']}"
                                
                                # Удаляем команду селфа и отправляем шикарный результат
                                vk.messages.delete(message_ids=message_id, delete_for_all=1)
                                vk.messages.send(peer_id=peer_id, attachment=attachment, random_id=random.randint(1, 1000000))
                                
                            except Exception as quote_err:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка генерации цитаты: {quote_err}")
                                except: pass
                            continue

                        # Базовые команды
                        elif text.strip() == "/хелп":
                            try:
                                help_msg = (
                                    "⚙️ СПИСОК КОМАНД БОТА ⚙️\n\n"
                                    "🛡️ Администратор:\n"
                                    "• /кик [id] — удалить из беседы\n"
                                    "• /спам [текст] [кол-во] — заспамить чат\n"
                                    "• /негатив [id] — авто-оскорбления\n"
                                    "• /унегатив [id] — убрать из негатива\n"
                                    "• /клон [id] — авто-клон\n"
                                    "• /уклон [id] — убрать из клонов\n"
                                    "• /реакция [id] [номер] — авто-реакция\n"
                                    "• /стопреакция [id] — стоп реакции\n"
                                    "• /опубликовать [текст] — пост на стену\n"
                                    "• /группы [id] — группы пользователя\n"
                                    "• /игнор [id] — бесшумный игнор\n"
                                    "• /уигнор [id] — снять игнор\n"
                                    "• /пригласить [id] — добавить в беседу\n\n"
                                    "👤 Базовые:\n"
                                    "• /цитата — создать красивую картинку-цитату бесплатно 🎨\n"
                                    "• /пинг — проверить скорость ответа\n"
                                    "• /инфо [id] — расширенная инфа\n"
                                    "• /удалить (/дел) — удалить сообщение\n"
                                    "• /сник [ник] — локальный ник\n"
                                    "• /онлайн — список друзей в сети\n"
                                    "• /выход — выйти из беседы"
                                )
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=help_msg)
                            except: pass
                            continue
                            
                        elif text.startswith("/инфо"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Ищу информацию...")
                                t_id = get_target_id(text, msg_info, vk) or user_id
                                
                                user_data = vk.users.get(user_ids=[t_id], fields="photo_max_orig,is_closed,online,last_seen,counters,followers_count,online_mobile")[0]
                                
                                with db_lock:
                                    is_connected = t_id in connected_users
                                    user_role = connected_users.get(t_id, {}).get("role") if is_connected else None
                                
                                if t_id == MY_USER_ID: role_display = "👑 Владелец"
                                elif is_connected: role_display = "🛠️ Админ" if user_role == "admin" else "👤 Пользователь"
                                else: role_display = "❌ Не подключен"
                                
                                nick_display = user_nicknames.get(t_id, "Не установлен")
                                counters = user_data.get('counters', {})
                                friends_count = counters.get('friends', 0)
                                followers_count = user_data.get('followers_count', counters.get('followers', 0))
                                
                                platform_code = user_data.get('last_seen', {}).get('platform', 0)
                                if platform_code in [2, 3]: device = "iOS"
                                elif platform_code == 4: device = "Андроид"
                                elif platform_code in [1, 7]: device = "ПК"
                                else: device = "Андроид" if user_data.get('online_mobile') == 1 else "ПК"
                                
                                online_display = f"🟢 Онлайн ({device})" if user_data.get('online') == 1 else f"🔴 Офлайн (Заходил с {device})"
                                
                                info_msg = (
                                    f"👤 Информация о пользователе:\n"
                                    f"• Имя: [id{t_id}|{user_data['first_name']} {user_data['last_name']}]\n"
                                    f"• Никнейм: {nick_display}\n"
                                    f"• Роль в боте: {role_display}\n"
                                    f"• ID: {t_id}\n"
                                    f"• Статус: {online_display}\n"
                                    f"• Друзей: {friends_count} чел.\n"
                                    f"• Подписчиков: {followers_count} чел.\n"
                                    f"• Профиль: {'🔒 Закрытый' if user_data.get('is_closed') else '🔓 Открытый'}\n"
                                    f"• Ссылка: vk.com/id{t_id}"
                                )
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=info_msg)
                            except Exception as e:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка инфо: {e}")
                                except: pass
                            continue

                        elif text.strip() == "/пинг":
                            try:
                                start_time = time.time()
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🏓 Понг...")
                                ping_ms = round((time.time() - start_time) * 1000)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🏓 ПОНГ\n• Задержка API VK: {ping_ms} мс")
                            except: pass
                            continue

                        elif text.strip() in ["/удалить", "/дел"]:
                            try:
                                vk.messages.delete(message_ids=message_id, delete_for_all=1)
                                reply_msg = msg_info.get('reply_message')
                                if reply_msg: vk.messages.delete(message_ids=reply_msg['id'], delete_for_all=1)
                            except: pass
                            continue

                        elif text.startswith("/сник"):
                            try:
                                t_id = get_target_id(text, msg_info, vk) or user_id
                                raw_nick = text[5:].strip()
                                clean_nick = re.sub(r'\[.*?\]', '', raw_nick).strip()
                                if clean_nick:
                                    user_nicknames[t_id] = clean_nick
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Никнейм изменен на: {clean_nick}")
                            except: pass
                            continue

                        elif text.startswith("/онлайн"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Ищу друзей онлайн...")
                                friends_data = vk.friends.get(fields="online", count=1000).get('items', [])
                                online_friends = [f for f in friends_data if f.get('online') == 1]
                                if online_friends:
                                    lines = [f"{i}. [id{f['id']}|{f['first_name']} {f['last_name']}]" for i, f in enumerate(online_friends[:30], 1)]
                                    res_text = f"🟢 Друзья онлайн ({len(online_friends)}):\n" + "\n".join(lines)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=res_text)
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚪️ Сейчас никого нет в сети.")
                            except: pass
                            continue

                        elif text.strip() == "/выход":
                            try:
                                if peer_id > 2000000000:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="👋 Всем пока!")
                                    time.sleep(0.5)
                                    vk.messages.removeChatUser(chat_id=peer_id-2000000000, user_id=user_id)
                            except: pass
                            continue

                        elif text.startswith("/негатив"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_negatives: account_negatives[user_id] = []
                                if t_id not in account_negatives[user_id]:
                                    account_negatives[user_id].append(t_id)
                                    try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в негатив")
                                    except: pass
                            continue

                        elif text.startswith("/унегатив"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_negatives and t_id in account_negatives[user_id]:
                                account_negatives[user_id].remove(t_id)
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из негатива")
                                except: pass
                            continue

                        elif text.startswith("/клон"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_clones: account_clones[user_id] = []
                                if t_id not in account_clones[user_id]:
                                    account_clones[user_id].append(t_id)
                                    try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в клоны")
                                    except: pass
                            continue

                        elif text.startswith("/уклон"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_clones and t_id in account_clones[user_id]:
                                account_clones[user_id].remove(t_id)
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из клонов")
                                except: pass
                            continue

                        elif text.startswith("/спам"):
                            try:
                                parts = text.split()
                                if len(parts) >= 2 and parts[-1].isdigit():
                                    count = int(parts[-1])
                                    s_text = " ".join(parts[1:-1]) or "🤖"
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🚀 Спамлю {count} раз...")
                                    for _ in range(count):
                                        time.sleep(0.4)
                                        vk.messages.send(peer_id=peer_id, message=s_text, random_id=random.randint(1,1000000))
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пример: /спам текст 5")
                            except: pass
                            continue

                        elif text.startswith("/реакция"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                parts = text.split()
                                r_id = int(parts[-1]) if len(parts) >= 2 and parts[-1].isdigit() else 1
                                if user_id not in account_reactions: account_reactions[user_id] = {}
                                account_reactions[user_id][t_id] = r_id
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Авто-реакция {r_id} для id{t_id} задана!")
                                except: pass
                            continue

                        elif text.startswith("/стопреакция"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_reactions and t_id in account_reactions[user_id]:
                                del account_reactions[user_id][t_id]
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Авто-реакция отключена")
                                except: pass
                            continue
                            
        except Exception as loop_err:
            print(f"⚠️ Поток ID {user_id} временно упал: {loop_err}. Рестарт через 5 сек...")
            time.sleep(5)

def main():
    global connected_users
    owner_session = vk_api.VkApi(token=USER_TOKEN, api_version='5.131')
    connected_users[MY_USER_ID] = {"token": USER_TOKEN, "role": "owner", "api": owner_session.get_api()}
    
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for uid_str, udata in data.items():
                    uid = int(uid_str)
                    connected_users[uid] = {
                        "token": udata["token"],
                        "role": udata["role"],
                        "api": vk_api.VkApi(token=udata["token"], api_version='5.131').get_api()
                    }
            print(f"📋 Загружено сохраненных профилей: {len(data)} шт.")
        except Exception as e: print(f"Ошибка чтения БД: {e}")

    for uid, udata in list(connected_users.items()):
        t = threading.Thread(target=user_longpoll_loop, args=(uid, udata["token"]), daemon=True)
        t.start()
        active_threads[uid] = t

    print("🚀 Бот полностью запущен на хостинге without syntax errors!")
    while True: time.sleep(1)

if __name__ == "__main__":
    main()
