import os
import sys
import subprocess

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

USER_TOKEN = "vk1.a.edynZWBJGgef-lj0kOg-OdqtEzdzTm6YwntGyuzMSe8lf53NmWCYCsEW1XCyVTDZnjLnzeamx52N1grIhvo3Ovm7ykq081C7224Qo_uP8ls_tFptamaBjr-1tX6quT3IXUXDkQ9_UL0E1Ye39vGwNwsor7IOzJtx25w82uJXLcLgLmwQuTUtc3nyEclBzFluegboRUL8jb7U4LqFlxo-Pw"
MY_USER_ID = 848213593

# Базы данных в памяти хостинга
target_reactions = {}  
target_negatives = []  # Список ID пользователей, на которых включен негатив

# Разнообразный список фраз для режима /негатив
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

def main():
    vk_session = vk_api.VkApi(token=USER_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    
    print("🚀 Бот обновлен! Добавлены режимы /негатив и /унегатив.")

    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW:
            peer_id = event.peer_id
            text = event.text
            message_id = event.message_id
            
            try:
                msg_info = vk.messages.getById(message_ids=message_id)['items'][0]
                from_id = msg_info.get('from_id')
            except:
                from_id = None

            # Проверяем сообщения от других пользователей
            if from_id and from_id != MY_USER_ID:
                
                # --- 1. ЕСЛИ НА ЮЗЕРА ВКЛЮЧЕН НЕГАТИВ ---
                if from_id in target_negatives and not text.startswith("/"):
                    try:
                        random_phrase = random.choice(NEG_LINES) # Каждый раз разная фраза
                        vk.messages.send(
                            peer_id=peer_id,
                            message=random_phrase,
                            reply_to=message_id, # Отвечает репостом прямо на его сообщение
                            random_id=random.randint(1, 1000000)
                        )
                    except Exception as e:
                        print(f"Ошибка режима негатива: {e}")

                # --- 2. АВТО-РЕАКЦИЯ НА ЖЕРТВУ ---
                if from_id in target_reactions:
                    try:
                        vk.messages.sendReaction(
                            peer_id=peer_id,
                            cmid=msg_info.get('conversation_message_id'),
                            reaction_id=target_reactions[from_id]
                        )
                    except: pass

            # --- ОБРАБОТКА КОМАНД С АВТОУДАЛЕНИЕМ ---
            if text.startswith("/"):
                try: vk.messages.delete(message_ids=message_id, delete_for_all=1)
                except: pass

                # ВКЛЮЧИТЬ НЕГАТИВ (отправлять в ответ на сообщение человека)
                if text.startswith("/негатив"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            victim_id = reply_msg['from_id']
                            if victim_id not in target_negatives:
                                target_negatives.append(victim_id)
                                print(f"Негатив включен для юзера {victim_id}")
                    except Exception as e:
                        print(f"Ошибка включения негатива: {e}")

                # ВЫКЛЮЧИТЬ НЕГАТИВ (отправлять в ответ на сообщение человека)
                elif text.startswith("/унегатив"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            victim_id = reply_msg['from_id']
                            if victim_id in target_negatives:
                                target_negatives.remove(victim_id)
                                print(f"Негатив выключен для юзера {victim_id}")
                    except Exception as e:
                        print(f"Ошибка выключения негатива: {e}")

                # Команда: /отправить
                elif text.strip() == "/отправить":
                    try:
                        vk.messages.send(
                            peer_id=peer_id,
                            message="ХОТИТЕ ПОЛУЧИТЬ МЕНЯ ПИШИ В ЛС",
                            random_id=random.randint(1, 1000000)
                        )
                    except: pass

                # Чистый спам без цифр: /спам [текст] [кол-во]
                elif text.startswith("/спам"):
                    try:
                        parts = text.split(" ")
                        if len(parts) < 3: continue
                        count = int(parts[-1])
                        spam_text = " ".join(parts[1:-1])
                        
                        for _ in range(count):
                            vk.messages.send(
                                peer_id=peer_id, 
                                message=spam_text, 
                                random_id=random.randint(1, 1000000)
                            )
                            time.sleep(0.5)
                    except: pass

                # Включение реакций: /реакция [id] (в ответ)
                elif text.startswith("/реакция"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            victim_id = reply_msg['from_id']
                            parts = text.split(" ")
                            try: reaction_id = int(parts[1])
                            except: reaction_id = 1
                            target_reactions[victim_id] = reaction_id
                    except: pass

                # Выключение реакций: /стопреакция (в ответ)
                elif text.startswith("/стопреакция"):
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            victim_id = reply_msg['from_id']
                            if victim_id in target_reactions: del target_reactions[victim_id]
                    except: pass

if __name__ == "__main__":
    try: main()
    except Exception as e: print(f"Ошибка работы: {e}")
