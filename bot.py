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

# Автоматическая установка всех библиотек при старте
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

# Безопасное скачивание шрифта через рабочие CDN-зеркала (Фикс квадратов)
def download_font():
    if os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 10000:
        return
    print("📥 Скачиваю Cyrillic-совместимый шрифт для карточек...")
    urls = [
        "https://cdnjs.cloudflare.com/ajax/libs/ink/3.1.10/fonts/Roboto/Roboto-Medium.ttf",
        "https://github.com/google/fonts/raw/main/apache/roboto/static/Roboto-Medium.ttf",
        "https://github.com/google/fonts/raw/main/ofl/ubuntu/Ubuntu-Medium.ttf"
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200 and len(r.content) > 10000:
                with open(FONT_PATH, "wb") as f:
                    f.write(r.content)
                print("✅ Шрифт успешно загружен и готов к работе!")
                return
        except Exception as e:
            print(f"⚠️ Зеркало шрифта не ответило: {e}")
    print("❌ Не удалось загрузить шрифт. Будет использован стандартный.")

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

# Отрисовка векторного пейзажа гор для цитатника
def create_vector_background(width, height):
    base = Image.new("RGBA", (width, height))
    top_color = (240, 100, 75)   
    bottom_color = (40, 22, 48)  
    
    data = []
    for y in range(height):
        factor = y / height
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * factor)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * factor)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * factor)
        data.extend([(r, g, b, 255)] * width)
    base.putdata(data)
    
    draw = ImageDraw.Draw(base)
    
    # Дальние горы
    draw.polygon([
        (0, height), (0, int(height * 0.62)), (int(width * 0.22), int(height * 0.42)), 
        (int(width * 0.42), int(height * 0.68)), (int(width * 0.58), int(height * 0.38)), 
        (int(width * 0.76), int(height * 0.72)), (int(width * 0.89), int(height * 0.48)), 
        width, int(height * 0.64), width, height
    ], fill=(82, 36, 62, 255))
    
    # Ближние горы
    draw.polygon([
        (0, height), (0, int(height * 0.8)), (int(width * 0.14), int(height * 0.65)), 
        (int(width * 0.34), int(height * 0.84)), (int(width * 0.52), int(height * 0.58)), 
        (int(width * 0.68), int(height * 0.86)), (int(width * 0.84), int(height * 0.66)), 
        width, int(height * 0.82), width, height
    ], fill=(48, 22, 42, 255))
    
    return base

# Генерация красивой карточки цитаты
def generate_quote_image(avatar_url, author_name, quote_text, date_str):
    width, height = 1000, 500
    image = create_vector_background(width, height)
    draw = ImageDraw.Draw(image)
    
    try:
        font_name = ImageFont.truetype(FONT_PATH, 28)
        font_text = ImageFont.truetype(FONT_PATH, 36)
        font_date = ImageFont.truetype(FONT_PATH, 18)
        font_quotes = ImageFont.truetype(FONT_PATH, 110)
    except:
        font_name = font_text = font_date = font_quotes = ImageFont.load_default()

    # Круглая аватарка
    try:
        response = requests.get(avatar_url)
        avatar = Image.open(BytesIO(response.content)).convert("RGBA")
        avatar = avatar.resize((200, 200))
        mask = Image.new("L", (200, 200), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 200, 200), fill=255)
        image.paste(avatar, (70, 120), mask)
        draw.ellipse((68, 118, 272, 322), outline="#ffffff", width=4)
    except Exception as e:
        draw.ellipse((70, 120, 270, 320), fill="#5181b8")

    draw.text((70, 390), author_name, fill="#ffffff", font=font_name)
    draw.text((70, 435), date_str, fill=(240, 240, 240, 200), font=font_date)
    draw.text((320, 100), "“", fill=(255, 255, 255, 220), font=font_quotes)
    
    wrapped_lines = []
    words = quote_text.split()
    current_line = ""
    for word in words:
        if len(current_line + " " + word) < 30:
            current_line += " " + word if current_line else word
        else:
            wrapped_lines.append(current_line)
            current_line = word
    if current_line:
        wrapped_lines.append(current_line)
        
    wrapped_text = "\n".join(wrapped_lines[:5])
    draw.text((370, 200), wrapped_text.strip(), fill="#ffffff", font=font_text)
    
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output

# Персональный поток для обработки событий аккаунта
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
                    
                    if not event.from_me or text.startswith("/"):
                        try:
                            res = vk.messages.getById(message_ids=[message_id])
                            if res and res.get('items'):
                                msg_info = res['items'][0]
                                from_id = msg_info.get('from_id')
                                cmid = msg_info.get('conversation_message_id')
                        except:
                            from_id = event.user_id if not event.from_me else user_id
                    else:
                        from_id = user_id

                    # --- АВТО-ФУНКЦИИ (Работают в фоне на входящие сообщения) ---
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
                            except: pass

                    # --- ОБРАБОТКА СЕЛФ-КОМАНД ---
                    if event.from_me and text.startswith("/"):
                        
                        # Проверка прав Владельца
                        if text.startswith(("/подключить", "/роль", "/снять")):
                            if role != "owner":
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Команда доступна только Владельцу!")
                                except: pass
                                continue

                            if text.startswith("/подключить"):
                                try:
                                    token_arg = text[11:].strip()
                                    if not token_arg:
                                        vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите токен!")
                                        continue
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⏳ Настраиваю выделенный поток...")
                                    temp_session = vk_api.VkApi(token=token_arg, api_version='5.131')
                                    temp_vk = temp_session.get_api()
                                    temp_info = temp_vk.users.get()[0]
                                    new_id = temp_info['id']
                                    
                                    with db_lock:
                                        is_in_db = new_id in connected_users
                                    if is_in_db:
                                        vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"⚠️ Аккаунт id{new_id} уже работает!")
                                    else:
                                        with db_lock:
                                            connected_users[new_id] = {"token": token_arg, "role": "пользователь", "api": temp_vk}
                                        save_connected_users()
                                        t = threading.Thread(target=user_longpoll_loop, args=(new_id, token_arg), daemon=True)
                                        t.start()
                                        active_threads[new_id] = t
                                        vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Аккаунт id{new_id} успешно подключен!")
                                except Exception as err:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {err}")
                                continue

                            elif text.startswith("/роль"):
                                t_id = get_target_id(text, msg_info, vk)
                                if t_id and t_id in connected_users:
                                    with db_lock: connected_users[t_id]["role"] = "admin"
                                    save_connected_users()
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователю id{t_id} выдана роль admin")
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь не подключен к боту.")
                                continue

                            elif text.startswith("/снять"):
                                t_id = get_target_id(text, msg_info, vk)
                                if t_id and t_id in connected_users:
                                    with db_lock: del connected_users[t_id]
                                    save_connected_users()
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Пользователь id{t_id} отключен.")
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь не найден.")
                                continue

                        # Проверка прав Администратора / Владельца
                        if text.startswith(("/кик", "/спам", "/негатив", "/унегатив", "/клон", "/уклон", "/реакция", "/стопреакция", "/опубликовать", "/группы", "/игнор", "/уигнор", "/пригласить")):
                            if role not in ["owner", "admin"]:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Недостаточно прав!")
                                except: pass
                                continue

                        # Выполнение команд Администратора
                        if text.startswith("/кик"):
                            if peer_id <= 2000000000:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Работает только в беседах!")
                                continue
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                try:
                                    vk.messages.removeChatUser(chat_id=peer_id-2000000000, user_id=t_id)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователь id{t_id} успешно исключен.")
                                except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {e}")
                            continue

                        elif text.startswith("/опубликовать"):
                            post_text = text[14:].strip()
                            if post_text:
                                try:
                                    vk.wall.post(message=post_text)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пост успешно опубликован на Вашей стене!")
                                except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {e}")
                            continue

                        elif text.startswith("/группы"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Ищу открытые группы пользователя...")
                                t_id = get_target_id(text, msg_info, vk) or user_id
                                groups_data = vk.groups.get(user_id=t_id, extended=1, count=25)
                                items = groups_data.get('items', [])
                                if items:
                                    lines = [f"{i}. [club{g['id']}|{g['name']}]" for i, g in enumerate(items, 1)]
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"📂 Открытые группы id{t_id}:\n" + "\n".join(lines))
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="📁 Группы скрыты или отсутствуют.")
                            except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Нет доступа к группам: {e}")
                            continue

                        elif text.startswith("/игнор"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_ignores: account_ignores[user_id] = []
                                if t_id not in account_ignores[user_id]: account_ignores[user_id].append(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в бесшумный игнор.")
                            continue

                        elif text.startswith("/уигнор"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_ignores and t_id in account_ignores[user_id]:
                                account_ignores[user_id].remove(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из игнора.")
                            continue

                        elif text.startswith("/негатив"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_negatives: account_negatives[user_id] = []
                                if t_id not in account_negatives[user_id]: account_negatives[user_id].append(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в негатив.")
                            continue

                        elif text.startswith("/унегатив"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_negatives and t_id in account_negatives[user_id]:
                                account_negatives[user_id].remove(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из негатива.")
                            continue

                        elif text.startswith("/клон"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_clones: account_clones[user_id] = []
                                if t_id not in account_clones[user_id]: account_clones[user_id].append(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в клоны.")
                            continue

                        elif text.startswith("/уклон"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_clones and t_id in account_clones[user_id]:
                                account_clones[user_id].remove(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из клонов.")
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
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Авто-реакция {r_id} задана.")
                            continue

                        elif text.startswith("/стопреакция"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_reactions and t_id in account_reactions[user_id]:
                                del account_reactions[user_id][t_id]
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Авто-реакция отключена.")
                            continue

                        elif text.startswith("/пригласить"):
                            if peer_id <= 2000000000:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Только для бесед!")
                                continue
                            t_id = get_target_id(text, msg_info, vk)
                            if not t_id and text[11:].strip().isdigit(): t_id = int(text[11:].strip())
                            if t_id:
                                try:
                                    vk.messages.addChatUser(chat_id=peer_id - 2000000000, user_id=t_id)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователь id{t_id} приглашен.")
                                except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {e}")
                            continue

                        # --- ОБЩИЕ / СЕЛФ КОМАНДЫ (Редактируют исходные СМС) ---
                        elif text.startswith("/цитата"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🎨 Рисую векторную карточку цитаты...")
                                reply_msg = msg_info.get('reply_message')
                                if reply_msg:
                                    target_user_id = reply_msg['from_id']
                                    quote_text = reply_msg['text']
                                    date_obj = datetime.fromtimestamp(reply_msg.get('date', time.time()))
                                else:
                                    target_user_id = user_id
                                    quote_text = text[8:].strip()
                                    date_obj = datetime.now()
                                
                                months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
                                date_str = f"{date_obj.day:02d} {months[date_obj.month - 1]} {date_obj.year}, {date_obj.hour:02d}:{date_obj.minute:02d}"
                                if not quote_text:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Введите текст цитаты!")
                                    continue
                                
                                author_info = vk.users.get(user_ids=[target_user_id], fields="photo_max_orig")[0]
                                full_name = f"{author_info['first_name']} {author_info['last_name']}"
                                avatar_url = author_info.get('photo_max_orig')
                                
                                img_buffer = generate_quote_image(avatar_url, full_name, quote_text, date_str)
                                upload_server = vk.photos.getMessagesUploadServer(peer_id=peer_id)
                                upload_req = requests.post(upload_server['upload_url'], files={'photo': ('quote.png', img_buffer, 'image/png')}).json()
                                save_res = vk.photos.saveMessagesPhoto(server=upload_req['server'], photo=upload_req['photo'], hash=upload_req['hash'])[0]
                                attachment = f"photo{save_res['owner_id']}_{save_res['id']}"
                                
                                vk.messages.delete(message_ids=message_id, delete_for_all=1)
                                vk.messages.send(peer_id=peer_id, attachment=attachment, random_id=random.randint(1, 1000000))
                            except Exception as q_err:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка цитаты: {q_err}")
                            continue

                        elif text.strip() == "/пинг":
                            try:
                                start_time = time.time()
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🏓 Понг...")
                                ping_ms = round((time.time() - start_time) * 1000)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🏓 ПОНГ\n• Скорость API VK: {ping_ms} мс")
                            except: pass
                            continue

                        elif text.startswith("/инфо"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Получаю информацию...")
                                t_id = get_target_id(text, msg_info, vk) or user_id
                                user_data = vk.users.get(user_ids=[t_id], fields="photo_max_orig,online,last_seen,counters,followers_count")[0]
                                nick_display = user_nicknames.get(t_id, "Не установлен")
                                info_msg = (
                                    f"👤 Профиль: vk.com/id{t_id}\n"
                                    f"• Имя: {user_data['first_name']} {user_data['last_name']}\n"
                                    f"• Локальный ник: {nick_display}\n"
                                    f"• Друзей в списке: {user_data.get('counters', {}).get('friends', 0)} чел.\n"
                                    f"• Подписчиков: {user_data.get('followers_count', 0)} чел."
                                )
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=info_msg)
                            except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {e}")
                            continue

                        elif text.strip() in ["/удалить", "/дел"]:
                            try:
                                vk.messages.delete(message_ids=message_id, delete_for_all=1)
                                reply_msg = msg_info.get('reply_message')
                                if reply_msg: vk.messages.delete(message_ids=reply_msg['id'], delete_for_all=1)
                            except: pass
                            continue

                        elif text.startswith("/сник"):
                            t_id = get_target_id(text, msg_info, vk) or user_id
                            raw_nick = text[5:].strip()
                            clean_nick = re.sub(r'\[.*?\]', '', raw_nick).strip()
                            if clean_nick:
                                user_nicknames[t_id] = clean_nick
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Никнейм изменен на: {clean_nick}")
                            continue

                        elif text.startswith("/онлайн"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Ищу друзей онлайн...")
                                friends_data = vk.friends.get(fields="online", count=1000).get('items', [])
                                online_friends = [f for f in friends_data if f.get('online') == 1]
                                if online_friends:
                                    lines = [f"{i}. [id{f['id']}|{f['first_name']} {f['last_name']}]" for i, f in enumerate(online_friends[:25], 1)]
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🟢 Друзья в сети ({len(online_friends)}):\n" + "\n".join(lines))
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚪️ Сейчас никого нет в сети.")
                            except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {e}")
                            continue

                        elif text.strip() == "/выход":
                            if peer_id > 2000000000:
                                try:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="👋 Всем пока!")
                                    time.sleep(0.5)
                                    vk.messages.removeChatUser(chat_id=peer_id-2000000000, user_id=user_id)
                                except: pass
                            continue
                            
                        elif text.strip() == "/хелп":
                            try:
                                help_text = (
                                    "⚙️ СПИСОК ВСЕХ КОМАНД СЕЛФ-БОТА ⚙️\n\n"
                                    "👑 Владелец:\n"
                                    "• /подключить [токен] — добавить страницу\n"
                                    "• /роль [id] — выдать админку в боте\n"
                                    "• /снять [id] — отключить страницу\n\n"
                                    "🛠️ Админ:\n"
                                    "• /кик [id] — исключить из беседы\n"
                                    "• /пригласить [id] — добавить в беседу\n"
                                    "• /спам [текст] [раз] — флуд сообщениями\n"
                                    "• /негатив [id] / /унегатив — авто-токсик\n"
                                    "• /клон [id] / /уклон — зеркалить сообщения\n"
                                    "• /реакция [id] [номер] / /стопреакция\n"
                                    "• /игнор [id] / /уигнор — тихий игнор\n"
                                    "• /группы [id] — глянуть открытые сообщества\n"
                                    "• /опубликовать [текст] — запись на стену\n\n"
                                    "👤 Для всех (Селф):\n"
                                    "• /цитата — сгенерировать горную карточку цитаты 🎨\n"
                                    "• /пинг — задержка сети\n"
                                    "• /инфо [id] — расширенная карточка юзера\n"
                                    "• /дел / /удалить — снести СМС\n"
                                    "• /сник [ник] — локальный псевдоним\n"
                                    "• /онлайн — друзья онлайн\n"
                                    "• /выход — ливнуть из беседы"
                                )
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=help_text)
                            except: pass
                            continue
                            
        except Exception as loop_err:
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
                        "token": udata["token"], "role": udata["role"],
                        "api": vk_api.VkApi(token=udata["token"], api_version='5.131').get_api()
                    }
        except: pass

    for uid, udata in list(connected_users.items()):
        t = threading.Thread(target=user_longpoll_loop, args=(uid, udata["token"]), daemon=True)
        t.start()
        active_threads[uid] = t

    print("🚀 Бот полностью укомплектован и готов к заливке на хостинг!")
    while True: time.sleep(1)

if __name__ == "__main__":
    main()
