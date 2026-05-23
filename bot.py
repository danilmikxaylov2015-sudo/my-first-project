import os
import sys
import subprocess
import re

def install_vk_api():
    try:
        import vk_api
        import requests
    except ImportError:
        print("Устанавливаю необходимые библиотеки...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vk-api", "requests"])

install_vk_api()

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
import random
import time
import requests

# ГЛАВНЫЕ НАСТРОЙКИ ВЛАДЕЛЬЦА
USER_TOKEN = "vk1.a.edynZWBJGgef-lj0kOg-OdqtEzdzTm6YwntGyuzMSe8lf53NmWCYCsEW1XCyVTDZnjLnzeamx52N1grIhvo3Ovm7ykq081C7224Qo_uP8ls_tFptamaBjr-1tX6quT3IXUXDkQ9_UL0E1Ye39vGwNwsor7IOzJtx25w82uJXLcLgLmwQuTUtc3nyEclBzFluegboRUL8jb7U4LqFlxo-Pw"
MY_USER_ID = 848213593

# БАЗАДАННЫХ В ПАМЯТИ
target_reactions = {}  
target_negatives = []  
target_clones = []     
user_nicknames = {}    

# Структура: { user_id: {"token": "...", "role": "пользователь"|"admin", "api": vk_api_instance} }
connected_users = {}

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
            resolved = vk.utils.resolveScreenName(screen_name=raw_mention)
            if resolved and resolved['type'] == 'user':
                return resolved['object_id']
    return None

def main():
    vk_session = vk_api.VkApi(token=USER_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    
    print("🚀 Мульти-аккаунт бот успешно запущен и готов!")

    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW:
            peer_id = event.peer_id
            text = event.text
            message_id = event.message_id
            
            try:
                msg_info = vk.messages.getById(message_ids=message_id)['items'][0]
                from_id = msg_info.get('from_id')
                cmid = msg_info.get('conversation_message_id')
                attachments = msg_info.get('attachments', [])
            except:
                from_id, cmid, attachments = None, None, []

            if not from_id:
                continue

            # Авто-функции (негатив, клоны) работают от лица главного аккаунта бота
            if from_id != MY_USER_ID:
                if from_id in target_clones and not text.startswith("/"):
                    try:
                        result = "".join([c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text)])
                        vk.messages.send(peer_id=peer_id, message=result + " 🤡", reply_to=message_id, random_id=random.randint(1, 1000000))
                        continue
                    except: pass

                if from_id in target_negatives and not text.startswith("/"):
                    try:
                        vk.messages.send(peer_id=peer_id, message=random.choice(NEG_LINES), reply_to=message_id, random_id=random.randint(1, 1000000))
                        continue
                    except: pass

                if from_id in target_reactions:
                    try:
                        if cmid: vk.messages.sendReaction(peer_id=peer_id, cmid=cmid, reaction_id=target_reactions[from_id])
                    except: pass

            # ПРОВЕРКА ДОСТУПА К КОМАНДАМ БОТА
            has_access = False
            user_role = None
            active_vk = vk  # API сессия того, кто вызвал команду
            current_uid = MY_USER_ID

            if from_id == MY_USER_ID:
                has_access = True
                user_role = "owner"
                active_vk = vk
                current_uid = MY_USER_ID
            elif from_id in connected_users:
                has_access = True
                user_role = connected_users[from_id]["role"]
                active_vk = connected_users[from_id]["api"]
                current_uid = from_id

            if text.startswith("/") and has_access:
                
                # --- БЛОК 1: КОМАНДЫ ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА ---
                if text.startswith(("/подключить", "/роль", "/снять")):
                    if user_role != "owner":
                        try: active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ошибка: Данная команда доступна только Владельцу бота!")
                        except: pass
                        continue

                    # КОМАНДА /ПОДКЛЮЧИТЬ
                    if text.startswith("/подключить"):
                        try:
                            token_arg = text[11:].strip()
                            if not token_arg:
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Укажите токен! Пример: /подключить vk1.a...")
                                continue
                            
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⏳ Проверяю токен...")
                            try:
                                temp_session = vk_api.VkApi(token=token_arg)
                                temp_vk = temp_session.get_api()
                                temp_info = temp_vk.users.get()[0]
                                new_id = temp_info['id']
                                
                                connected_users[new_id] = {
                                    "token": token_arg,
                                    "role": "пользователь",
                                    "api": temp_vk
                                }
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Аккаунт id{new_id} ({temp_info['first_name']}) успешно добавлен!\n🎭 Начальная роль: пользователь")
                            except Exception as token_err:
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Неверный токен или ошибка API: {token_err}")
                        except: pass
                        continue

                    # КОМАНДА /РОЛЬ (ВЫДАЧА АДМИНА)
                    elif text.startswith("/роль"):
                        try:
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if t_id in connected_users:
                                    connected_users[t_id]["role"] = "admin"
                                    active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователю id{t_id} успешно выдана роль: админ")
                                elif t_id == MY_USER_ID:
                                    active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="👑 Вы и так являетесь Создателем бота!")
                                else:
                                    active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Этот пользователь еще не подключен через /подключить")
                            else:
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ответь на сообщение цели или тегни её через @!")
                        except: pass
                        continue

                    # КОМАНДА /СНЯТЬ (ПОНИЖЕНИЕ ДО ПОЛЬЗОВАТЕЛЯ)
                    elif text.startswith("/снять"):
                        try:
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if t_id in connected_users:
                                    connected_users[t_id]["role"] = "пользователь"
                                    active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"📉 С пользователя id{t_id} сняты права админа.\n🎭 Новая роль: пользователь")
                                elif t_id == MY_USER_ID:
                                    active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Нельзя снять роль с самого себя!")
                                else:
                                    active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Этот пользователь не подключен к боту.")
                            else:
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ответь на сообщение цели или тегни её через @!")
                        except: pass
                        continue

                # --- БЛОК 2: КОМАНДЫ ОВНЕРА И АДМИНИСТРАТОРОВ ---
                if text.startswith(("/кик", "/спам", "/негатив", "/унегатив", "/клон", "/уклон", "/реакция", "/стопреакция", "/ава", "/опубликовать")):
                    if user_role not in ["owner", "admin"]:
                        try: active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Недостаточно прав! Нужна роль: Админ.")
                        except: pass
                        continue

                # Редактирование /инфо (доступно всем подключенным)
                if text.startswith("/инфо"):
                    try:
                        active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⏳ Получаю информацию...")
                        t_id = get_target_id(text, msg_info, vk)
                        if t_id and t_id > 0:
                            user_data = vk.users.get(user_ids=[t_id], fields="photo_max_orig,is_closed")[0]
                            first_name = user_data.get('first_name', 'Не указано')
                            last_name = user_data.get('last_name', 'Не указано')
                            is_closed = "🔒 Закрытый" if user_data.get('is_closed') else "🔓 Открытый"
                            photo = user_data.get('photo_max_orig', 'Нет фото')
                            nickname_str = user_nicknames.get(t_id, "Не установлен")
                            
                            # Определение роли в боте для отображения
                            if t_id == MY_USER_ID:
                                role_display = "👑 Владелец"
                            elif t_id in connected_users:
                                role_display = "🛠️ Администратор" if connected_users[t_id]["role"] == "admin" else "👤 Пользователь"
                            else:
                                role_display = "❌ Не подключен"
                            
                            info_msg = (
                                f"👤 Информация о пользователе:\n"
                                f"• Имя: {first_name} {last_name}\n"
                                f"• Никнейм: {nickname_str}\n"
                                f"• Роль в боте: {role_display}\n"
                                f"• ID: {t_id}\n"
                                f"• Профиль: {is_closed}\n"
                                f"• Ссылка: vk.com/id{t_id}\n"
                                f"• Аватарка: {photo}"
                            )
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=info_msg)
                        else:
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ответь на сообщение цели или тегни через @!")
                    except Exception as e:
                        try: active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка инфо: {e}")
                        except: pass
                    continue

                # Команда удаления сообщений
                elif text.strip() in ["/удалить", "/дел"]:
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            target_msg_id = reply_msg['id']
                            try: active_vk.messages.delete(message_ids=message_id, delete_for_all=1)
                            except: pass
                            active_vk.messages.delete(message_ids=target_msg_id, delete_for_all=1)
                    except: pass
                    continue

                # Настройка кастомного ника
                elif text.startswith("/сник"):
                    try:
                        t_id = get_target_id(text, msg_info, vk)
                        if t_id:
                            raw_nick = text[5:].strip()
                            clean_nick = re.sub(r'\[(id|club)\d+\|.*?\]', '', raw_nick).strip()
                            if clean_nick:
                                user_nicknames[t_id] = clean_nick
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Никнейм для id{t_id} изменен на: {clean_nick}")
                            else:
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Напиши ник после команды!")
                        else:
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ответь на сообщение или тегни!")
                    except: pass
                    continue

                # Проверка друзей онлайн
                elif text.startswith("/онлайн"):
                    try:
                        active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⏳ Проверяю список друзей онлайн...")
                        friends_data = active_vk.friends.get(fields="online", count=1000).get('items', [])
                        online_friends = [f for f in friends_data if f.get('online') == 1]
                        if online_friends:
                            lines = [f"{i}. {f['first_name']} {f['last_name']} (vk.com/id{f['id']})" for i, f in enumerate(online_friends[:30], 1)]
                            total_on = len(online_friends)
                            res_text = f"🟢 Друзья онлайн (Всего: {total_on}):\n" + "\n".join(lines)
                            if total_on > 30: res_text += f"\n\n...и ещё {total_on - 30} пользователей."
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=res_text)
                        else:
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚪️ Сейчас никто из друзей не в сети.")
                    except Exception as e:
                        try: active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка онлайн-списка: {e}")
                        except: pass
                    continue

                # Выход из беседы (выходит тот, кто написал команду!)
                elif text.strip() == "/выход":
                    try:
                        if peer_id > 2000000000:
                            chat_id = peer_id - 2000000000
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="👋 Всем пока, я погнал!")
                            time.sleep(1)
                            active_vk.messages.removeChatUser(chat_id=chat_id, user_id=current_uid)
                        else:
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Эта команда работает только в беседах!")
                    except: pass
                    continue

                # Исключение участника из беседы
                elif text.startswith("/кик"):
                    try:
                        if peer_id > 2000000000:
                            chat_id = peer_id - 2000000000
                            t_id = get_target_id(text, msg_info, vk)
                            if t_id:
                                if t_id == current_uid:
                                    active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Нельзя кикнуть самого себя!")
                                else:
                                    active_vk.messages.removeChatUser(chat_id=chat_id, user_id=t_id)
                                    active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пользователь id{t_id} успешно исключен!")
                            else:
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Ответь на сообщение или тегни!")
                        else:
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Работает только в беседах!")
                    except Exception as e:
                        try: active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка исключения: {e}")
                        except: pass
                    continue

                # Смена аватарки профиля
                elif text.startswith("/ава"):
                    try:
                        active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⏳ Скачиваю и устанавливаю аватарку...")
                        photo_url = None
                        link_match = re.search(r'(https?://[^\s]+)', text)
                        if link_match: photo_url = link_match.group(1)
                        if not photo_url:
                            current_attachments = attachments
                            if not current_attachments and msg_info.get('reply_message'):
                                current_attachments = msg_info['reply_message'].get('attachments', [])
                            for attach in current_attachments:
                                if attach['type'] == 'photo':
                                    sizes = attach['photo']['sizes']
                                    sizes.sort(key=lambda x: x['width'] * x['height'])
                                    photo_url = sizes[-1]['url']
                                    break
                        if photo_url:
                            upload_server = active_vk.photos.getOwnerPhotoUploadServer()
                            upload_url = upload_server['upload_url']
                            photo_bytes = requests.get(photo_url).content
                            response = requests.post(upload_url, files={'photo': ('avatar.jpg', photo_bytes)}).json()
                            if 'error' in response or not response.get('photo'):
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="❌ ВК отклонил фото.")
                            else:
                                active_vk.photos.saveOwnerPhoto(server=response['server'], hash=response['hash'], photo=response['photo'])
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="🔥 Аватарка профиля успешно изменена!")
                        else:
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Прикрепи фото или отправь ссылку!")
                    except Exception as e:
                        try: active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"❌ Ошибка смены аватарки: {e}")
                        except: pass
                    continue

                # Остальные команды переведены на active_vk...
                elif text.startswith("/опубликовать"):
                    try:
                        post_text = text[13:].strip()
                        if post_text:
                            wall_post = active_vk.wall.post(message=post_text)
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Пост опубликован! ID: {wall_post.get('post_id')}")
                        else: active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⚠️ Напиши текст поста!")
                    except: pass
                    continue

                elif text.startswith("/группы"):
                    try:
                        active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="⏳ Получаю список групп...")
                        t_id = get_target_id(text, msg_info, vk)
                        if t_id:
                            groups = active_vk.groups.get(user_id=t_id, extended=1, count=15)
                            if groups['items']:
                                lines = [f"➡️ {g['name']} (vk.com/{g['screen_name']})" for g in groups['items']]
                                res_text = f"📋 Список открытых групп id{t_id}:\n" + "\n".join(lines)
                            else: res_text = f"❌ Группы пользователя id{t_id} скрыты."
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=res_text)
                    except: pass
                    continue

                elif text.strip() == "/отправить":
                    try: active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="ХОТИТЕ ПОЛУЧИТЬ МЕНЯ ПИШИ В ЛС")
                    except: pass
                    continue

                elif text.startswith("/отпрчелу"):
                    try:
                        target_id = get_target_id(text, msg_info, vk)
                        message_to_send = ""
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg: message_to_send = text[10:].strip()
                        else:
                            clean_text = re.sub(r'\[.*?\]', '', text).strip()
                            message_to_send = clean_text[10:].strip()
                        if target_id and message_to_send:
                            active_vk.messages.send(peer_id=target_id, message=message_to_send, random_id=random.randint(1, 1000000))
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Отправлено в ЛС для id{target_id}")
                    except: pass
                    continue

                elif text.startswith("/негатив"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            vic_id = reply_msg['from_id']
                            if vic_id not in target_negatives:
                                target_negatives.append(vic_id)
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="пользователь добавлен в негатив")
                    except: pass
                    continue

                elif text.startswith("/унегатив"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            vic_id = reply_msg['from_id']
                            if vic_id in target_negatives:
                                target_negatives.remove(vic_id)
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="пользователь удален из негатива")
                    except: pass
                    continue

                elif text.startswith("/клон"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            vic_id = reply_msg['from_id']
                            if vic_id not in target_clones:
                                target_clones.append(vic_id)
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="пользователь добавлен в клоны")
                    except: pass
                    continue

                elif text.startswith("/уклон"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            vic_id = reply_msg['from_id']
                            if vic_id in target_clones:
                                target_clones.remove(vic_id)
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="пользователь удален из клонов")
                    except: pass
                    continue

                elif text.startswith("/спам"):
                    try:
                        parts = text.split(" ")
                        if len(parts) < 3: continue
                        count = int(parts[-1])
                        spam_text = " ".join(parts[1:-1])
                        active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"🚀 Запускаю спам ({count} шт)...")
                        for _ in range(count):
                            try: active_vk.messages.setActivity(peer_id=peer_id, type="typing")
                            except: pass
                            time.sleep(0.1)
                            active_vk.messages.send(peer_id=peer_id, message=spam_text, random_id=random.randint(1, 1000000))
                            time.sleep(0.7)
                    except: pass
                    continue

                elif text.startswith("/реакция"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            vic_id = reply_msg['from_id']
                            parts = text.split(" ")
                            try: r_id = int(parts[1])
                            except: r_id = 1
                            target_reactions[vic_id] = r_id
                            active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message=f"✅ Авто-реакция {r_id} задана!")
                    except: pass
                    continue

                elif text.startswith("/стопреакция"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            vic_id = reply_msg['from_id']
                            if vic_id in target_reactions: 
                                del target_reactions[vic_id]
                                active_vk.messages.edit(peer_id=peer_id, message_id=message_id, message="✅ Авто-реакция успешно отключена")
                    except: pass
                    continue

if __name__ == "__main__":
    try: main()
    except Exception as e: print(f"Ошибка в главном потоке: {e}")
