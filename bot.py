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

# База данных реакций в памяти
target_reactions = {}  

def main():
    vk_session = vk_api.VkApi(token=USER_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    
    print("🚀 Бот обновлен! Спам теперь без цифр.")

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

            # Работаем, только если пишет другой человек
            if from_id and from_id != MY_USER_ID:
                text_lower = text.lower()
                is_dm = peer_id == from_id

                # --- 1. АВТООТВЕТ: ТОЛЬКО НА ТЕГ @danil_mikxaylov И ТОЛЬКО В БЕСЕДАХ ---
                if not is_dm and "@danil_mikxaylov" in text_lower and not text.startswith("/"):
                    try:
                        vk.messages.send(
                            peer_id=peer_id,
                            message="да пошел ты",
                            reply_to=message_id, 
                            random_id=random.randint(1, 1000000)
                        )
                        continue 
                    except Exception as e:
                        print(f"Ошибка автоответа: {e}")

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

                # Команда: /отправить
                if text.strip() == "/отправить":
                    try:
                        vk.messages.send(
                            peer_id=peer_id,
                            message="ХОТИТЕ ПОЛУЧИТЬ МЕНЯ ПИШИ В ЛС",
                            random_id=random.randint(1, 1000000)
                        )
                    except Exception as e:
                        print(f"Ошибка команды /отправить: {e}")

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
                                message=spam_text, # Отправляем чистый текст, как ты просил
                                random_id=random.randint(1, 1000000)
                            )
                            time.sleep(0.5)
                    except Exception as e:
                        print(f"Ошибка спама: {e}")

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
