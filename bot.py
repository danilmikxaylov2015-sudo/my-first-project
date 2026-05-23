import os
import sys
import subprocess
import re
import json
import threading
import time
import random
import requests

def install_vk_api():
    try:
        import vk_api
        from vk_api.longpoll import VkLongPoll, VkEventType
    except ImportError:
        print("Устанавливаю необходимые библиотеки...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vk-api", "requests"])

install_vk_api()

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType

# ==================== НАСТРОЙКИ ВЛАДЕЛЬЦА ====================
USER_TOKEN = "vk1.a.edynZWBJGgef-lj0kOg-OdqtEzdzTm6YwntGyuzMSe8lf53NmWCYCsEW1XCyVTDZnjLnzeamx52N1grIhvo3Ovm7ykq081C7224Qo_uP8ls_tFptamaBjr-1tX6quT3IXUXDkQ9_UL0E1Ye39vGwNwsor7IOzJtx25w82uJXLcLgLmwQuTUtc3nyEclBzFluegboRUL8jb7U4LqFlxo-Pw"
MY_USER_ID = 848213593
TOKEN_FILE = "connected_users.json"
# =============================================================

# Глобальные хранилища данных и потоковый замок
db_lock = threading.Lock()
account_reactions = {}  
account_negatives = {}  
account_clones = {}     
account_ignores = {}    
user_nicknames = {}    
connected_users = {}
active_threads = {}
message_cache = {}  # Кэш для модуля Анти-слив

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
            print(f"🛑 Поток для ID {user_id} успешно остановлен и закрыт.")
            break
            
        try:
            vk_session = vk_api.VkApi(token=token)
            vk = vk_session.get_api()
            longpoll = VkLongPoll(vk_session)
            
            for event in longpoll.listen():
                with db_lock:
                    is_active = (user_id == MY_USER_ID or user_id in connected_users)
                if not is_active:
                    break
                    
                # 1. ОБРАБОТКА НОВЫХ СООБЩЕНИЙ
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
                            res = vk.messages.getById(message_ids=message_id)
                            if res and res.get('items'):
                                msg_info = res['items'][0]
                                from_id = msg_info.get('from_id')
                                cmid = msg_info.get('conversation_message_id')
                                attachments = msg_info.get('attachments', [])
                        except:
                            from_id = event.user_id if not event.from_me else user_id
                    else:
                        from_id = user_id

                    # Кэшируем сообщение для Анти-слива (Шпиона)
                    if text:
                        message_cache[message_id] = {
                            'text': text,
                            'user_id': from_id if from_id else event.user_id,
                            'peer_id': peer_id,
                            'from_me': event.from_me
                        }
                        # Защита от переполнения памяти
                        if len(message_cache) > 5000:
                            message_cache.pop(next(iter(message_cache)))

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
                                vk.messages.sendReaction(peer_id=peer_id, conversation_message_id=cmid, reaction_id=account_reactions[user_id][from_id])
                            except Exception as e: 
                                print(f"Ошибка авто-реакции: {e}")

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
                                    
                                    temp_session = vk_api.VkApi(token=token_arg)
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
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Пользователь id{t_id} полностью отключен от бота и удален из привязок.")
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь не найден в списке привязанных.")
                                continue

                        # Ограничение админ-команд
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
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ошибка доступа: Пользователь скрыл список своих групп настройками приватности.")
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка поиска групп: {e}")
                            continue

                        elif text.startswith("/игнор"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_ignores: account_ignores[user_id] = []
                                if t_id not in account_ignores[user_id]:
                                    account_ignores[user_id].append(t_id)
                                    try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в твой личный бесшумный игнор")
                                    except: pass
                                else:
                                    try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь уже в твоем игноре.")
                                    except: pass
                            continue

                        elif text.startswith("/уигнор"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_ignores and t_id in account_ignores[user_id]:
                                account_ignores[user_id].remove(t_id)
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из твоего игнора")
                                except: pass
                            else:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пользователь не был в твоем списке игнорируемых.")
                                except: pass
                            continue

                        elif text.startswith("/пригласить"):
                            if peer_id <= 2000000000:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Эта команда работает только внутри бесед/чатов!")
                                except: pass
                                continue
                            
                            t_id = get_target_id(text, msg_info, vk)
                            if not t_id:
                                arg = text[11:].strip()
                                if arg:
                                    if arg.isdigit():
                                        t_id = int(arg)
                                    elif arg.startswith("id") and arg[2:].isdigit():
                                        t_id = int(arg[2:])
                                    else:
                                        try:
                                            resolved = vk.utils.resolveScreenName(screen_name=arg)
                                            if resolved and resolved['type'] == 'user':
                                                t_id = resolved['object_id']
                                        except: pass
                            
                            if not t_id:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите ID, юзернейм или ответьте на сообщение пользователя.")
                                except: pass
                                continue
                                
                            try:
                                vk.messages.addChatUser(chat_id=peer_id - 2000000000, user_id=t_id)
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователь [id{t_id}|приглашен] в беседу.")
                            except Exception as e:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка приглашения: {e}")
                                except: pass
                            continue

                        # Общие команды для всех
                        elif text.startswith("/инфо"):
                            try:
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔍 Ищу информацию пользователя...")
                                t_id = get_target_id(text, msg_info, vk) or user_id
                                user_data = vk.users.get(user_ids=[t_id], fields="photo_max_orig,is_closed")[0]
                                
                                with db_lock:
                                    is_connected = t_id in connected_users
                                    user_role = connected_users.get(t_id, {}).get("role") if is_connected else None
                                
                                if t_id == MY_USER_ID: role_display = "👑 Владелец"
                                elif is_connected: role_display = "🛠️ Админ" if user_role == "admin" else "👤 Пользователь"
                                else: role_display = "❌ Не подключен"
                                
                                nick_display = user_nicknames.get(t_id, "Не установлен")
                                
                                info_msg = (
                                    f"👤 Информация о пользователе:\n"
                                    f"• Имя: [id{t_id}|{user_data['first_name']} {user_data['last_name']}]\n"
                                    f"• Никнейм: {nick_display}\n"
                                    f"• Роль в боте: {role_display}\n"
                                    f"• ID: {t_id}\n"
                                    f"• Профиль: {'🔒 Закрытый' if user_data.get('is_closed') else '🔓 Открытый'}\n"
                                    f"• Ссылка: vk.com/id{t_id}\n"
                                    f"• Аватарка: {user_data.get('photo_max_orig', 'Нет фото')}"
                                )
                                vk.messages.edit(peer_id=peer_id, message_id=message_id, message=info_msg)
                            except Exception as e:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка инфо: {e}")
                                except: pass
                            continue

                        elif text.strip() in ["/удалить", "/дел"]:
                            try:
                                vk.messages.delete(message_ids=message_id, delete_for_all=1)
                                reply_msg = msg_info.get('reply_message')
                                if reply_msg:
                                    vk.messages.delete(message_ids=reply_msg['id'], delete_for_all=1)
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
                                    res_text = f"🟢 Друзья онлайн (Всего: {len(online_friends)}):\n" + "\n".join(lines)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=res_text)
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚪️ Сейчас никого нет в сети.")
                            except Exception as e:
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка: {e}")
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

                        elif text.startswith("/кик"):
                            try:
                                if peer_id > 2000000000:
                                    t_id = get_target_id(text, msg_info, vk)
                                    if t_id and t_id != user_id:
                                        vk.messages.removeChatUser(chat_id=peer_id-2000000000, user_id=t_id)
                                        vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователь id{t_id} исключен.")
                            except: pass
                            continue

                        elif text.startswith("/опубликовать"):
                            try:
                                p_text = text[13:].strip()
                                if p_text:
                                    wp = vk.wall.post(message=p_text)
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пост опубликован! ID: {wp.get('post_id')}")
                            except: pass
                            continue

                        elif text.strip() == "/отправить":
                            try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="ХОТИТЕ ПОЛУЧИТЬ МЕНЯ ПИШИ В ЛС")
                            except: pass
                            continue

                        elif text.startswith("/отпрчелу"):
                            try:
                                t_id = get_target_id(text, msg_info, vk)
                                m_send = text[10:].strip()
                                if t_id and m_send:
                                    vk.messages.send(peer_id=t_id, message=m_send, random_id=random.randint(1,1000000))
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Успешно отправлено в ЛС!")
                            except: pass
                            continue

                        elif text.startswith("/негатив"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_negatives: account_negatives[user_id] = []
                                if t_id not in account_negatives[user_id]:
                                    account_negatives[user_id].append(t_id)
                                    try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в твой личный негатив")
                                    except: pass
                            continue

                        elif text.startswith("/унегатив"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_negatives and t_id in account_negatives[user_id]:
                                account_negatives[user_id].remove(t_id)
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из твоего негатива")
                                except: pass
                            continue

                        elif text.startswith("/клон"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if user_id not in account_clones: account_clones[user_id] = []
                                if t_id not in account_clones[user_id]:
                                    account_clones[user_id].append(t_id)
                                    try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь добавлен в твои клоны")
                                    except: pass
                            continue

                        elif text.startswith("/уклон"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_clones and t_id in account_clones[user_id]:
                                account_clones[user_id].remove(t_id)
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Пользователь удален из твоих клонов")
                                except: pass
                            continue

                        elif text.startswith("/спам"):
                            try:
                                parts = text.strip().split(" ")
                                if len(parts) >= 2 and parts[-1].isdigit():
                                    count = int(parts[-1])
                                    s_text = " ".join(parts[1:-1])
                                    if not s_text:
                                        s_text = "🤖"
                                    
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🚀 Запускаю спам ({count} шт)...")
                                    for _ in range(count):
                                        time.sleep(0.4)
                                        vk.messages.send(peer_id=peer_id, message=s_text, random_id=random.randint(1,1000000))
                                else:
                                    vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Пример: /спам привет всем 5")
                            except Exception as e:
                                print(f"Ошибка в спаме: {e}")
                            continue

                        elif text.startswith("/реакция"):
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                parts = text.strip().split(" ")
                                r_id = 1
                                if len(parts) > 1 and parts[-1].isdigit():
                                    r_id = int(parts[-1])
                                    
                                if user_id not in account_reactions: account_reactions[user_id] = {}
                                account_reactions[user_id][t_id] = r_id
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Твоя авто-реакция {r_id} задана!")
                                except: pass
                            continue

                        elif text.startswith("/стопреакция"):
                            t_id = get_target_id(text, msg_info, vk)
                            if user_id in account_reactions and t_id in account_reactions[user_id]:
                                del account_reactions[user_id][t_id]
                                try: vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Твоя авто-реакция отключена")
                                except: pass
                            continue
                
                # 2. ОБРАБОТКА УДАЛЕНИЙ (Анти-слив / Шпион)
                elif event.type == VkEventType.MESSAGE_FLAGS_SET:
                    # 131072 — системная маска события "Сообщение удалено для всех"
                    if event.mask & 131072:
                        deleted_msg_id = event.message_id
                        
                        if deleted_msg_id in message_cache:
                            msg_data = message_cache[deleted_msg_id]
                            
                            # Не позорим сами себя, если удалили свое сообщение
                            if not msg_data['from_me'] and msg_data['text']:
                                try:
                                    u_info = vk.users.get(user_ids=msg_data['user_id'])[0]
                                    author_name = f"{u_info['first_name']} {u_info['last_name']}"
                                    del_text = msg_data['text']
                                    
                                    vk.messages.send(
                                        peer_id=msg_data['peer_id'],
                                        message=f"🚨 [АНТИ-СЛИВ] {author_name} испугался и удалил сообщение!\n\nОригинал: «{del_text}» 🤡",
                                        random_id=random.randint(1, 1000000)
                                    )
                                except Exception as e:
                                    print(f"Ошибка шпиона: {e}")
                            
        except Exception as loop_err:
            print(f"⚠️ Поток ID {user_id} временно упал: {loop_err}. Перезапуск через 5 секунд...")
            time.sleep(5)

def main():
    global connected_users
    
    owner_session = vk_api.VkApi(token=USER_TOKEN)
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
                        "api": vk_api.VkApi(token=udata["token"]).get_api()
                    }
            print(f"📋 Загружено сохраненных профилей из базы: {len(data)} шт.")
        except Exception as e:
            print(f"Ошибка чтения базы данных: {e}")

    for uid, udata in list(connected_users.items()):
        t = threading.Thread(target=user_longpoll_loop, args=(uid, udata["token"]), daemon=True)
        t.start()
        active_threads[uid] = t

    print("🚀 Все независимые аккаунты активированы. Бот полностью готов к работе!")
    
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
