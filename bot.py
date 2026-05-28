import os
import sys
import subprocess
import re
import json
import threading
import time
import random
import asyncio
import requests
from io import BytesIO

def install_libs():
    try:
        import vk_api
        from vk_api.longpoll import VkLongPoll, VkEventType
        from PIL import Image, ImageDraw, ImageFont
        import edge_tts
    except ImportError:
        print("📥 Устанавливаю необходимые библиотеки (включая Pillow и Edge-TTS)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vk-api", "requests", "Pillow", "edge-tts"])

install_libs()

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from PIL import Image, ImageDraw, ImageFont
import edge_tts

# ==================== НАСТРОЙКИ ВЛАДЕЛЬЦА ====================
USER_TOKEN = "vk1.a.QwbT5ccotJiLVxGPCiqBziXHUoXYKGKwkk8UTLUEUMZkog49pyCFv1Q3l8kIzPsgFxcemfXqIx38fWuPjXakbo0SwVFq_FyrMlz2cOtenvAzFsD4ynCyTMc8qsdqWlhSfusbg5EigjSG6eSbEO3qABqGhl98pJGNtG3cI5R4kKyIwO5i9aFP4rAulYWy7TtXNC5urx9eoHiWmTSFy3dHuQ"
MY_USER_ID = 848213593
TOKEN_FILE = "connected_users.json"
# =============================================================

db_lock = threading.Lock()
account_reactions = {}  
account_negatives = {}  
account_clones = {}     
account_ignores = {}    
account_pr = {}         # База активного ежеминутного пиара
user_nicknames = {}    
connected_users = {}
active_threads = {}

# 🔥 Жесткая база с матами
NEG_LINES = [
    "Ты че вообще высрал, долбоёб? Завали своё ебало и не позорься здесь.",
    "Хуйню неси в другом месте, сука, тут твоё мнение нахрен никому не сдалось.",
    "Заебал подавать голос, клоун. Пиздуй нахуй из чата, пока тебя ногами не выпихнули.",
    "Ебать ты сказочный дегенерат, конечно. Как ты вообще до клавиатуры дополз?",
    "Блядь, закрой рот, из него слишком сильно несёт тупостью.",
    "Ты че, сука, бессмертный или просто реально отбитый наглухо? Потеряйся нахуй.",
    "Твой высер даже читать западло. Забейся в угол и не отсвечивай, чучело.",
    "Ебало стяни, пока тебе его тут окончательно не завалили, говноед. 🤡",
    "Какого хуя ты вообще решил, что твоё мнение кого-то волнует? Свали в туман.",
    "Ты настолько тупой, что это даже не смешно. Иди нахуй и не трать чужое время."
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
            
    parts = text.split()
    for part in parts:
        if part.isdigit() and len(part) > 5:
            return int(part)
    return None

def generate_quote_image(avatar_url, author_name, quote_text):
    width, height = 900, 350
    image = Image.new("RGBA", (width, height), "#1a1a24")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 15, height], fill="#5181b8") 
    
    try:
        font_name = ImageFont.load_default(size=28)
        font_text = ImageFont.load_default(size=24)
    except:
        font_name = ImageFont.load_default()
        font_text = ImageFont.load_default()

    try:
        response = requests.get(avatar_url)
        avatar = Image.open(BytesIO(response.content)).convert("RGBA")
        avatar = avatar.resize((180, 180))
        mask = Image.new("L", (180, 180), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 180, 180), fill=255)
        image.paste(avatar, (50, 85), mask)
    except Exception as e:
        print(f"Не удалось обработать аватар: {e}")
        draw.ellipse((50, 85, 230, 265), fill="#5181b8")

    try: draw.text((260, 40), "“", fill="#2d2d3a", font=ImageFont.load_default(size=120))
    except: draw.text((260, 40), "“", fill="#2d2d3a")

    draw.text((260, 95), author_name, fill="#5181b8", font=font_name)
    
    wrapped_lines = []
    words = quote_text.split()
    current_line = ""
    for word in words:
        if len(current_line + " " + word) < 45:
            current_line += " " + word if current_line else word
        else:
            wrapped_lines.append(current_line)
            current_line = word
    if current_line:
        wrapped_lines.append(current_line)
    
    wrapped_text = "\n".join(wrapped_lines[:4])
    draw.text((260, 145), f"«{wrapped_text.strip()}»", fill="#ffffff", font=font_text)
    
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output

# Функция генерации жесткого ГС через Edge-TTS
async def generate_angry_voice(text, filename):
    # Используем мужской голос Dmitry, понижаем питч на 15Hz (бас) и ускоряем на 8% для злости
    communicate = edge_tts.Communicate(text, "ru-RU-DmitryNeural", pitch="-15Hz", rate="+8%")
    await communicate.save(filename)

# Фоновый воркер для ежеминутной отправки пиара
def pr_loop(user_id, peer_id, token, pr_text):
    try:
        vk_session = vk_api.VkApi(token=token, api_version='5.131')
        vk = vk_session.get_api()
    except:
        return
        
    while True:
        with db_lock:
            if account_pr.get((user_id, peer_id)) != pr_text:
                break
        try:
            vk.messages.send(peer_id=peer_id, message=pr_text, random_id=random.randint(1, 1000000))
        except Exception as e:
            print(f"Ошибка выполнения пиара для ID {user_id} в чате {peer_id}: {e}")
            break 
            
        time.sleep(60)

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
                        except Exception as e:
                            print(f"Ошибка getById: {e}")
                            from_id = event.user_id if not event.from_me else user_id
                    else:
                        from_id = user_id

                    # --- АВТО-ФУНКЦИИ (ФОН) ---
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
                        parts = text.split()
                        if not parts: continue
                        cmd = parts[0].lower()

                        owner_cmds = ["/подключить", "/роль", "/снять", "/отпрпост"]
                        admin_cmds = ["/кик", "/спам", "/негатив", "/унегатив", "/клон", "/уклон", "/реакция", "/стопреакция", "/опубликовать", "/группы", "/игнор", "/уигнор", "/пригласить", "/дрвчат", "/рассылка", "/отпрчелу", "/пиар", "/стоппиар", "/гс"]
                        
                        if cmd in owner_cmds and role != "owner":
                            try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ошибка: Команда доступна только Владельцу!")
                            except: pass
                            continue

                        if cmd in admin_cmds and role not in ["owner", "admin"]:
                            try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Недостаточно прав! Нужен статус Администратора.")
                            except: pass
                            continue

                        # Исполнение команд OWNER
                        if cmd == "/подключить":
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
                                
                                with db_lock: is_in_db = new_id in connected_users
                                if is_in_db:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"⚠️ Аккаунт id{new_id} уже работает в системе!")
                                else:
                                    with db_lock:
                                        connected_users[new_id] = {"token": token_arg, "role": "пользователь", "api": temp_vk}
                                    save_connected_users()
                                    
                                    t = threading.Thread(target=user_longpoll_loop, args=(new_id, token_arg), daemon=True)
                                    t.start()
                                    active_threads[new_id] = t
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Аккаунт id{new_id} ({temp_info['first_name']}) подключен!")
                            except Exception as token_err:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка токена: {token_err}")
                            continue

                        elif cmd == "/роль":
                            t_id = get_target_id(text, msg_info, vk)
                            with db_lock: is_connected = t_id in connected_users
                            if t_id and is_connected:
                                with db_lock: connected_users[t_id]["role"] = "admin"
                                save_connected_users()
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователю id{t_id} выдана роль: admin")
                            else:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь не подключен к боту.")
                            continue

                        elif cmd == "/снять":
                            t_id = get_target_id(text, msg_info, vk)
                            with db_lock: is_connected = t_id in connected_users
                            if t_id and is_connected:
                                with db_lock: del connected_users[t_id]
                                save_connected_users()
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Пользователь id{t_id} отключен от бота.")
                            else:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь не найден в списке привязанных.")
                            continue

                        elif cmd == "/отпрпост":
                            t_id = get_target_id(text, msg_info, vk)
                            if not t_id:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите аккаунт (ID, ссылку или упомяните пользователя).")
                                continue
                            
                            with db_lock:
                                is_connected = t_id in connected_users
                                if is_connected:
                                    target_token = connected_users[t_id]["token"]
                                    
                            if not is_connected:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"⚠️ Аккаунт [id{t_id}|пользователя] не подключен к боту через /подключить!")
                                continue
                                
                            arg_text = text[9:].strip()
                            if len(parts) > 1 and (parts[1].isdigit() or "id" in parts[1] or "vk.com" in parts[1] or parts[1].startswith('[') or parts[1].startswith('@')):
                                arg_text = " ".join(parts[2:])
                                
                            if not arg_text:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Вы забыли написать текст для поста! Пример: `/отпрпост @юзер Всем привет!`")
                                continue
                                
                            try:
                                target_session = vk_api.VkApi(token=target_token, api_version='5.131')
                                target_vk = target_session.get_api()
                                post_res = target_vk.wall.post(message=arg_text)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"📝 Пост успешно опубликован от лица [id{t_id}|аккаунта] (ID поста: {post_res.get('post_id')})!")
                            except Exception as e:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Не удалось опубликовать пост: {e}")
                            continue

                        # Исполнение админ-команд
                        elif cmd == "/гс":
                            voice_text = text[4:].strip()
                            if not voice_text:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Напиши текст для ГС! Пример: /гс Слышь, ты кого там клоуном назвал?")
                                except: pass
                                continue
                            
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🗣️ Записываю злобный ответ...")
                                tmp_file = f"voice_{user_id}_{random.randint(1000, 9999)}.mp3"
                                
                                # Запуск асинхронного движка синтеза речи в синхронном потоке
                                asyncio.run(generate_angry_voice(voice_text, tmp_file))
                                
                                # Загрузка в ВК как аудиосообщение
                                upload_server = vk.docs.getMessagesUploadServer(type='audio_message', peer_id=peer_id)
                                upload_url = upload_server['upload_url']
                                
                                with open(tmp_file, "rb") as f:
                                    upload_req = requests.post(upload_url, files={'file': (tmp_file, f, 'audio/mpeg')}).json()
                                    
                                save_res = vk.docs.save(file=upload_req['file'])[0]
                                attachment = f"doc{save_res['owner_id']}_{save_res['id']}"
                                
                                # Сносим исходный триггер и отправляем ГС
                                vk.messages.delete(message_ids=message_id, delete_for_all=1)
                                vk.messages.send(peer_id=peer_id, attachment=attachment, random_id=random.randint(1, 1000000))
                                
                                # Чистим мусор за собой
                                if os.path.exists(tmp_file):
                                    os.remove(tmp_file)
                            except Exception as voice_err:
                                print(f"Ошибка ГС: {voice_err}")
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка генерации ГС: {voice_err}")
                                except: pass
                            continue

                        elif cmd == "/пиар":
                            pr_text_arg = text[5:].strip()
                            if not pr_text_arg:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите текст для пиара! Пример: /пиар Продам гараж")
                                except: pass
                                continue
                            
                            with db_lock:
                                account_pr[(user_id, peer_id)] = pr_text_arg
                            
                            t_pr = threading.Thread(target=pr_loop, args=(user_id, peer_id, token, pr_text_arg), daemon=True)
                            t_pr.start()
                            
                            try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🚀 Ежеминутный авто-пиар успешно запущен в этом чате!\n📝 Текст: {pr_text_arg}")
                            except: pass
                            continue

                        elif cmd == "/стоппиар":
                            with db_lock:
                                if (user_id, peer_id) in account_pr:
                                    del account_pr[(user_id, peer_id)]
                                    res_msg = "🛑 Ежеминутный пиар успешно отключен в этом чате."
                                else:
                                    res_msg = "⚠️ Пиар-поток в этой беседе не был запущен."
                            try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=res_msg)
                            except: pass
                            continue

                        elif cmd == "/отпрчелу":
                            t_id = get_target_id(text, msg_info, vk)
                            if not t_id:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите ID, ссылку или ответьте на сообщение.")
                                continue
                            
                            arg_text = text[9:].strip()
                            if len(parts) > 1 and (parts[1].isdigit() or "id" in parts[1] or "vk.com" in parts[1]):
                                arg_text = " ".join(parts[2:])
                                
                            if not arg_text:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Вы забыли написать текст сообщения! Пример: `/отпрчелу id123 привет`")
                                continue
                                
                            try:
                                vk.messages.send(peer_id=t_id, message=arg_text, random_id=random.randint(1, 1000000))
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"📬 Сообщение успешно отправлено в ЛС [id{t_id}|пользователю]!")
                            except Exception as e:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Не удалось отправить: {e}")
                            continue

                        elif cmd == "/кик":
                            if peer_id <= 2000000000:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Команда работает только в беседах!")
                                continue
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                try:
                                    vk.messages.removeChatUser(chat_id=peer_id-2000000000, user_id=t_id)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователь id{t_id} успешно исключен.")
                                except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка удаления: {e}")
                            else:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите цель для кика.")
                            continue

                        elif cmd == "/опубликовать":
                            post_text = text[14:].strip()
                            if post_text:
                                try:
                                    vk.wall.post(message=post_text)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Запись опубликована на Вашей стене.")
                                except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Не удалось опубликовать: {e}")
                            continue

                        elif cmd == "/группы":
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Ищу открытые группы пользователя...")
                                t_id = get_target_id(text, msg_info, vk) or user_id
                                groups_data = vk.groups.get(user_id=t_id, extended=1, count=25)
                                items = groups_data.get('items', [])
                                if items:
                                    lines = [f"{i}. [club{g['id']}|{g['name']}]" for i, g in enumerate(items, 1)]
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"📂 Открытые группы пользователя id{t_id}:\n" + "\n".join(lines))
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="📁 Группы скрыты или отсутствуют.")
                            except Exception as e:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка поиска групп: {e}")
                            continue

                        elif cmd == "/дрвчат":
                            if peer_id <= 2000000000:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Команда работает только в беседах!")
                                continue
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔄 Собираю список твоих друзей...")
                                friends_data = vk.friends.get(count=1000).get('items', [])
                                if not friends_data:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="📁 Твой список друзей пуст.")
                                    continue
                                chat_id = peer_id - 2000000000
                                success_count = 0
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🚀 Запускаю инвайт {len(friends_data)} друзей...")
                                for f_id in friends_data:
                                    try:
                                        vk.messages.addChatUser(chat_id=chat_id, user_id=f_id)
                                        success_count += 1
                                        time.sleep(0.4)
                                    except: pass
                                vk.messages.send(peer_id=peer_id, message=f"✅ Массовый инвайт завершен!\nДобавлено: {success_count}", random_id=random.randint(1, 1000000))
                            except Exception as e:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка инвайта: {e}")
                            continue

                        elif cmd == "/рассылка":
                            try:
                                p_text = text[10:].strip()
                                if not p_text:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите текст рассылки!")
                                    continue
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔄 Собираю список друзей для рассылки...")
                                friends_data = vk.friends.get(count=1000).get('items', [])
                                if not friends_data:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="📁 Список друзей пуст.")
                                    continue
                                success_count = 0
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🚀 Рассылаю сообщения для {len(friends_data)} друзей...")
                                for f_id in friends_data:
                                    try:
                                        vk.messages.send(peer_id=f_id, message=p_text, random_id=random.randint(1, 1000000))
                                        success_count += 1
                                        time.sleep(0.5)
                                    except: pass
                                vk.messages.send(peer_id=peer_id, message=f"✅ ЛС Рассылка успешно завершена!\nПолучили: {success_count} друзей.", random_id=random.randint(1, 1000000))
                            except Exception as e:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка рассылки: {e}")
                            continue

                        elif cmd == "/игнор":
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_ignores: account_ignores[user_id] = []
                                if t_id not in account_ignores[user_id]: account_ignores[user_id].append(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в бесшумный игнор")
                            continue

                        elif cmd == "/уигнор":
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_ignores and t_id in account_ignores[user_id]:
                                account_ignores[user_id].remove(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из игнора")
                            continue

                        elif cmd == "/пригласить":
                            if peer_id <= 2000000000:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Команда работает только в беседах!")
                                continue
                            t_id = get_target_id(text, msg_info, vk)
                            if not t_id and len(parts) > 1 and parts[1].isdigit(): 
                                t_id = int(parts[1])
                            if not t_id:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите ID или ответьте на сообщение.")
                                continue
                            try:
                                vk.messages.addChatUser(chat_id=peer_id - 2000000000, user_id=t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователь id{t_id} добавлен.")
                            except Exception as e: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {e}")
                            continue

                        elif cmd == "/негатив":
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_negatives: account_negatives[user_id] = []
                                if t_id not in account_negatives[user_id]: account_negatives[user_id].append(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в негатив")
                            continue

                        elif cmd == "/унегатив":
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_negatives and t_id in account_negatives[user_id]:
                                account_negatives[user_id].remove(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из негатива")
                            continue

                        elif cmd == "/клон":
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_clones: account_clones[user_id] = []
                                if t_id not in account_clones[user_id]: account_clones[user_id].append(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в клоны")
                            continue

                        elif cmd == "/уклон":
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_clones and t_id in account_clones[user_id]:
                                account_clones[user_id].remove(t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из клонов")
                            continue

                        elif cmd == "/спам":
                            try:
                                if len(parts) >= 2 and parts[-1].isdigit():
                                    count = int(parts[-1])
                                    s_text = " ".join(parts[1:-1]) or "🤖"
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🚀 Спамлю {count} раз...")
                                    for _ in range(count):
                                        time.sleep(0.9)  
                                        vk.messages.send(peer_id=peer_id, message=s_text, random_id=random.randint(1,1000000))
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пример: /спам текст 5")
                            except: pass
                            continue

                        elif cmd == "/реакция":
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                r_id = int(parts[-1]) if len(parts) >= 2 and parts[-1].isdigit() else 1
                                if user_id not in account_reactions: account_reactions[user_id] = {}
                                account_reactions[user_id][t_id] = r_id
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Авто-реакция {r_id} для id{t_id} задана!")
                            continue

                        elif cmd == "/стопреакция":
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_reactions and t_id in account_reactions[user_id]:
                                del account_reactions[user_id][t_id]
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Авто-реакция отключена")
                            continue

                        # Базовые / Общие команды
                        elif cmd == "/цитата":
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🎨 Генерирую бесплатную карточку цитаты...")
                                reply_msg = msg_info.get('reply_message')
                                if reply_msg:
                                    target_user_id = reply_msg['from_id']
                                    quote_text = reply_msg['text']
                                else:
                                    target_user_id = user_id
                                    quote_text = text[8:].strip()
                                
                                if not quote_text:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Напишите текст цитаты!")
                                    continue
                                
                                author_info = vk.users.get(user_ids=[target_user_id], fields="photo_max_orig")[0]
                                full_name = f"{author_info['first_name']} {author_info['last_name']}"
                                avatar_url = author_info.get('photo_max_orig')
                                
                                img_buffer = generate_quote_image(avatar_url, full_name, quote_text)
                                upload_server = vk.photos.getMessagesUploadServer(peer_id=peer_id)
                                upload_req = requests.post(upload_server['upload_url'], files={'photo': ('quote.png', img_buffer, 'image/png')}).json()
                                save_res = vk.photos.saveMessagesPhoto(server=upload_req['server'], photo=upload_req['photo'], hash=upload_req['hash'])[0]
                                
                                attachment = f"photo{save_res['owner_id']}_{save_res['id']}"
                                vk.messages.delete(message_ids=message_id, delete_for_all=1)
                                vk.messages.send(peer_id=peer_id, attachment=attachment, random_id=random.randint(1, 1000000))
                            except Exception as quote_err:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка цитаты: {quote_err}")
                            continue

                        elif cmd == "/хелп":
                            try:
                                help_msg = (
                                    "⚙️ СПИСОК КОМАНД БОТА ⚙️\n\n"
                                    "🛡️ Администратор:\n"
                                    "• /гс [текст] — отправить жесткое басистое голосовое сообщение 🗣️\n"
                                    "• /кик [id] — удалить из беседы\n"
                                    "• /спам [текст] [кол-во] — заспамить чат\n"
                                    "• /пиар [текст] — запустить ежеминутный пиар в чате 🚀\n"
                                    "• /стоппиар — остановить текущий пиар в чате 🛑\n"
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
                                    "• /пригласить [id] — добавить в беседу\n"
                                    "• /отпрчелу [id] [текст] — отправить смс в ЛС 📬\n"
                                    "• /дрвчат — инвайт всех друзей 💥\n"
                                    "• /рассылка [текст] — рассылка всем друзьям в ЛС 📬\n\n"
                                    "👤 Базовые:\n"
                                    "• /цитата — создать красивую картинку-цитату бесплатно 🎨\n"
                                    "• /пинг — проверить скорость ответа\n"
                                    "• /инфо [id] — расширенная инфа (друзья, подписчики, устройство)\n"
                                    "• /удалить (/дел) — удалить сообщение\n"
                                    "• /сник [ник] — локальный ник\n"
                                    "• /онлайн — список друзей в сети\n"
                                    "• /выход — выйти из беседы"
                                )
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=help_msg)
                            except: pass
                            continue
                            
                        elif cmd == "/инфо":
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
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка инфо: {e}")
                            continue

                        elif cmd == "/пинг":
                            try:
                                start_time = time.time()
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🏓 Понг...")
                                ping_ms = round((time.time() - start_time) * 1000)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🏓 ПОНГ\n• Задержка API VK: {ping_ms} мс")
                            except: pass
                            continue

                        elif cmd in ["/удалить", "/дел"]:
                            try:
                                vk.messages.delete(message_ids=message_id, delete_for_all=1)
                                reply_msg = msg_info.get('reply_message')
                                if reply_msg: vk.messages.delete(message_ids=reply_msg['id'], delete_for_all=1)
                            except: pass
                            continue

                        elif cmd == "/сник":
                            try:
                                t_id = get_target_id(text, msg_info, vk) or user_id
                                raw_nick = text[5:].strip()
                                clean_nick = re.sub(r'\[.*?\]', '', raw_nick).strip()
                                if clean_nick:
                                    user_nicknames[t_id] = clean_nick
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Никнейм изменен на: {clean_nick}")
                            except: pass
                            continue

                        elif cmd == "/онлайн":
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Ищу друзей онлайн...")
                                friends_data = vk.friends.get(fields="online", count=1000).get('items', [])
                                online_friends = [f for f in friends_data if f.get('online') == 1]
                                if online_friends:
                                    lines = [f"{i}. [id{f['id']}|{f['first_name']} {f['last_name']}]" for i, f in enumerate(online_friends[:30], 1)]
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🟢 Друзья онлайн ({len(online_friends)}):\n" + "\n".join(lines))
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚪️ Сейчас никого нет в сети.")
                            except: pass
                            continue

                        elif cmd == "/выход":
                            try:
                                if peer_id > 2000000000:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="👋 Всем пока!")
                                    time.sleep(0.5)
                                    vk.messages.removeChatUser(chat_id=peer_id-2000000000, user_id=user_id)
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

    print("🚀 Бот полностью запущен на хостинге без ошибок!")
    while True: time.sleep(1)

if __name__ == "__main__":
    main()
