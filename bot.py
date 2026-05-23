import os
import sys
import subprocess
import json

def install_vk_api():
    try:
        import vk_api
    except ImportError:
        print("Устанавливаю vk-api...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vk-api"])

install_vk_api()

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
import random
import time
import threading

# ТВОИ ТОКЕНЫ И НАСТРОЙКИ
USER_TOKEN = "vk1.a.edynZWBJGgef-lj0kOg-OdqtEzdzTm6YwntGyuzMSe8lf53NmWCYCsEW1XCyVTDZnjLnzeamx52N1grIhvo3Ovm7ykq081C7224Qo_uP8ls_tFptamaBjr-1tX6quT3IXUXDkQ9_UL0E1Ye39vGwNwsor7IOzJtx25w82uJXLcLgLmwQuTUtc3nyEclBzFluegboRUL8jb7U4LqFlxo-Pw"
GROUP_TOKEN = "vk1.a.jmhGtKNRy-okO7WM6HyGJofKiJMaUnBDyB3kEqxdKypWpcnJaEB7KBJixSmIMLc7YLBJHu6wKY2sElm6VlK59GWdnir2DJQl5D9ohPLQ_8USyg-_gpviWLw31YaUIcx51Y84dSXBPjUpwIULup3JGkiHECtNOGSqlxX4q3IvWgeGEwzaXefqwmTa9aFx2-g9b5dmx07Wx-HH3-Tu_2HDag"
MY_USER_ID = 848213593

# Базы данных в оперативной памяти
target_reactions = {}  
target_negatives = []  
target_clones = []     
target_madness = []    
admins_list = []       # Список ID пользователей с ролью админа
linked_users = set()   # Список ID пользователей, которые привязали бота

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

MOCK_PUNCHES = [
    "ТЫ ВООБЩЕ КТО ТАКОЙ, ПОТЕРЯЙСЯ 🤡",
    "ИДИ ПОПЛАЧЬ В ЛС ЧУШПАН 😂",
    "ВЫДАЙТЕ ЕМУ КЛОУНА ЗА ЭТОТ БРЕД 🔥",
    "МНЕ ЛЕНЬ ЭТО ЧИТАТЬ, УДАЛИ СВОЙ ВК 💩",
    "ПОМОЛЧИ, ЗА УМНОГО СОЙДЕШЬ 👀",
    "ДА ПОШЕЛ ТЫ ХА-ХА-ХА 🖕"
]

MAD_REACTIONS = [4, 12, 14]

def make_mad_text(text):
    result = ""
    for i, char in enumerate(text):
        result += char.upper() if i % 2 == 0 else char.lower()
    return f"«{result}» — {random.choice(MOCK_PUNCHES)}"

# --- ПОТОК 1: ТВОЙ СТРАНИЦА-АККАУНТ (УПРАВЛЕНИЕ, СПАМ И ХАРД-ТРОЛЛИНГ) ---
def user_account_loop():
    vk_session = vk_api.VkApi(token=USER_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    
    print("🤖 Поток страницы (Владелец) запущен успешно!")

    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW:
            peer_id = event.peer_id
            text = event.text
            message_id = event.message_id
            
            try:
                msg_info = vk.messages.getById(message_ids=message_id)['items'][0]
                from_id = msg_info.get('from_id')
                cmid = msg_info.get('conversation_message_id')
            except:
                from_id, cmid = None, None

            # Если пишет жертва
            if from_id and from_id != MY_USER_ID:
                if from_id in target_madness and not text.startswith("/"):
                    try:
                        try: vk.messages.setActivity(peer_id=peer_id, type="typing")
                        except: pass
                        if cmid:
                            try: vk.messages.sendReaction(peer_id=peer_id, cmid=cmid, reaction_id=random.choice(MAD_REACTIONS))
                            except: pass
                        vk.messages.send(peer_id=peer_id, message=make_mad_text(text), reply_to=message_id, random_id=random.randint(1, 1000000))
                        continue
                    except: pass

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

            # ПРОВЕРКА ПРАВ: КТО ПИШЕТ КОМАНДУ?
            is_sender_owner = (from_id == MY_USER_ID or peer_id == MY_USER_ID)
            is_sender_admin = (from_id in admins_list)

            if text.startswith("/"):
                # Если команду ввел Владелец или Админ — удаляем её из чата для скрытности
                if is_sender_owner or is_sender_admin:
                    try: vk.messages.delete(message_ids=message_id, delete_for_all=1)
                    except: pass

                # --- ЖЕСТКАЯ ПРОВЕРКА РОЛЕЙ СТРОГО ОТ ВЛАДЕЛЬЦА (ОБМАНУТЬ НЕЛЬЗЯ) ---
                if text.startswith("/роль админ"):
                    if is_sender_owner: # Только ты!
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                admin_id = reply_msg['from_id']
                                if admin_id not in admins_list:
                                    admins_list.append(admin_id)
                                    vk.messages.send(peer_id=peer_id, message="Администратор успешно назначен!", random_id=random.randint(1, 1000000))
                        except: pass
                    continue

                elif text.startswith("/снять"):
                    if is_sender_owner: # Только ты!
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                admin_id = reply_msg['from_id']
                                if admin_id in admins_list:
                                    admins_list.remove(admin_id)
                                    vk.messages.send(peer_id=peer_id, message="Администратор успешно снят со своего поста!", random_id=random.randint(1, 1000000))
                        except: pass
                    continue

                # --- ОСТАЛЬНЫЕ КОМАНДЫ ДОСТУПНЫ ВЛАДЕЛЬЦУ И НАСТОЯЩИМ АДМИНАМ ---
                if is_sender_owner or is_sender_admin:
                    if text.startswith("/негатив"):
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                vic_id = reply_msg['from_id']
                                if vic_id not in target_negatives:
                                    target_negatives.append(vic_id)
                                    vk.messages.send(peer_id=peer_id, message="пользователь добавлен в негатив", random_id=random.randint(1, 1000000))
                        except: pass

                    elif text.startswith("/унегатив"):
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                vic_id = reply_msg['from_id']
                                if vic_id in target_negatives:
                                    target_negatives.remove(vic_id)
                                    vk.messages.send(peer_id=peer_id, message="пользователь удален из негатива", random_id=random.randint(1, 1000000))
                        except: pass

                    elif text.startswith("/безумие"):
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                vic_id = reply_msg['from_id']
                                if vic_id not in target_madness:
                                    target_madness.append(vic_id)
                                    vk.messages.send(peer_id=peer_id, message="пользователь добавлен в безумие", random_id=random.randint(1, 1000000))
                        except: pass

                    elif text.startswith("/убезумие"):
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                vic_id = reply_msg['from_id']
                                if vic_id in target_madness:
                                    target_madness.remove(vic_id)
                                    vk.messages.send(peer_id=peer_id, message="пользователь удален из безумия", random_id=random.randint(1, 1000000))
                        except: pass

                    elif text.startswith("/клон"):
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                vic_id = reply_msg['from_id']
                                if vic_id not in target_clones:
                                    target_clones.append(vic_id)
                                    vk.messages.send(peer_id=peer_id, message="пользователь добавлен в клоны", random_id=random.randint(1, 1000000))
                        except: pass

                    elif text.startswith("/уклон"):
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                vic_id = reply_msg['from_id']
                                if vic_id in target_clones:
                                    target_clones.remove(vic_id)
                                    vk.messages.send(peer_id=peer_id, message="пользователь удален из клонов", random_id=random.randint(1, 1000000))
                        except: pass

                    elif text.startswith("/спам"):
                        try:
                            parts = text.split(" ")
                            if len(parts) < 3: continue
                            count = int(parts[-1])
                            spam_text = " ".join(parts[1:-1])
                            for _ in range(count):
                                try: vk.messages.setActivity(peer_id=peer_id, type="typing")
                                except: pass
                                time.sleep(0.1)
                                vk.messages.send(peer_id=peer_id, message=spam_text, random_id=random.randint(1, 1000000))
                                time.sleep(0.4)
                        except: pass

                    elif text.startswith("/реакция"):
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                vic_id = reply_msg['from_id']
                                parts = text.split(" ")
                                try: r_id = int(parts[1])
                                except: r_id = 1
                                target_reactions[vic_id] = r_id
                        except: pass

                    elif text.startswith("/стопреакция"):
                        try:
                            reply_msg = msg_info.get('reply_message')
                            if reply_msg:
                                vic_id = reply_msg['from_id']
                                if vic_id in target_reactions: del target_reactions[vic_id]
                        except: pass

# --- ПОТОК 2: ТВОЙ БОТ-СООБЩЕСТВО (ПРИВЯЗКА И КНОПКИ ДЛЯ ОБЫЧНЫХ ЮЗЕРОВ) ---
def group_bot_loop():
    vk_session = vk_api.VkApi(token=GROUP_TOKEN)
    vk = vk_session.get_api()
    
    try:
        group_id = vk.groups.getById()[0]['id']
        from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
        longpoll = VkBotLongPoll(vk_session, group_id)
        print(f"📡 Поток группы (ID: {group_id}) запущен успешно!")
    except Exception as e:
        print(f"Ошибка инициализации лонгпулла группы: {e}")
        return

    for event in longpoll.listen():
        if event.type == VkBotEventType.MESSAGE_NEW:
            message = event.obj.message
            peer_id = message['peer_id']
            text = message['text']
            from_id = message['from_id']
            payload = message.get('payload')

            # Генерируем красивую инлайн-кнопку для привязки
            keyboard = {
                "inline": True,
                "buttons": [[
                    {
                        "action": {
                            "type": "text",
                            "payload": "{\"button\": \"link_account\"}",
                            "label": "Привязать бота к акку 🔐"
                        },
                        "color": "positive"
                    }
                ]]
            }

            # 1. ОБРАБОТКА НАЖАТИЯ НА КНОПКУ ПРИВЯЗКИ
            if payload and json.loads(payload).get('button') == 'link_account':
                linked_users.add(from_id)
                try:
                    vk.messages.send(
                        peer_id=peer_id,
                        message="✅ Бот успешно привязан к твоему аккаунту! Теперь тебе доступна команда /отправить",
                        random_id=random.randint(1, 1000000)
                    )
                except: pass
                continue

            # 2. КОМАНДА /ОТПРАВИТЬ (Работает ТОЛЬКО если аккаунт привязан)
            if text.strip() == "/отправить":
                if from_id in linked_users or from_id == MY_USER_ID:
                    try:
                        vk.messages.send(
                            peer_id=peer_id,
                            message="ХОТИТЕ ПОЛУЧИТЬ МЕНЯ ПИШИ В ЛС",
                            random_id=random.randint(1, 1000000)
                        )
                    except: pass
                else:
                    try:
                        vk.messages.send(
                            peer_id=peer_id,
                            message="⚠️ Твой аккаунт не привязан! Нажми кнопку ниже, чтобы привязать бота.",
                            keyboard=json.dumps(keyboard),
                            random_id=random.randint(1, 1000000)
                        )
                    except: pass
                continue

            # 3. ЕСЛИ ЧЕЛОВЕК ПИШЕТ ЧТО-ТО ДРУГОЕ — ПРЕДЛАГАЕМ ПРИВЯЗКУ
            if from_id != MY_USER_ID:
                try:
                    vk.messages.send(
                        peer_id=peer_id,
                        message="Привет! Чтобы привязать бота к своему профилю, просто нажми на зеленую кнопку ниже 👇",
                        keyboard=json.dumps(keyboard),
                        random_id=random.randint(1, 1000000)
                    )
                except: pass

if __name__ == "__main__":
    print("👑 Запуск усовершенствованного комплекса защиты и привязки...")
    
    t1 = threading.Thread(target=user_account_loop, daemon=True)
    t2 = threading.Thread(target=group_bot_loop, daemon=True)
    
    t1.start()
    t2.start()
    
    while True:
        time.sleep(1)
