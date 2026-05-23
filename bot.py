import os
import sys
import subprocess

# Автоустановка vk_api
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

# База данных для авто-реакций (хранится в памяти, пока бот запущен)
# Формат: { ID_пользователя: "смайлик" }
target_reactions = {}

def main():
    vk_session = vk_api.VkApi(token=USER_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    
    print("🚀 Обновленный селф-бот запущен!")
    print("Команды:\n1) !спам [текст] [кол-во]\n2) !реакция [смайл] (в ответ на сообщение)\n3) !стопреакция (в ответ)")

    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW:
            peer_id = event.peer_id
            text = event.text
            message_id = event.message_id
            
            # --- СНАЧАЛА ПРОВЕРЯЕМ АВТО-РЕАКЦИИ НА ЖЕРТВУ ---
            # Получаем ID отправителя текущего сообщения
            try:
                msg_info = vk.messages.getById(message_ids=message_id)['items'][0]
                from_id = msg_info.get('from_id')
                
                # Если этот пользователь в списке жертв — лепим ему реакцию
                if from_id in target_reactions:
                    reaction_emoji = target_reactions[from_id]
                    vk.messages.sendReaction(
                        peer_id=peer_id,
                        cmid=msg_info.get('conversation_message_id'),
                        reaction_id=reaction_emoji
                    )
            except Exception as e:
                pass  # Игнорируем ошибки реакций, если это ЛС или старая версия API

            # --- ОБРАБОТКА КОМАНД ---
            
            # 1. КОМАНДА: !спам
            if text.startswith("!спам"):
                try:
                    # Сразу удаляем твою команду у всех
                    vk.messages.delete(message_ids=message_id, delete_for_all=1)
                except:
                    pass
                
                try:
                    parts = text.split(" ")
                    if len(parts) < 3:
                        continue
                        
                    count = int(parts[-1])
                    spam_text = " ".join(parts[1:-1])
                    
                    for i in range(count):
                        vk.messages.send(
                            peer_id=peer_id,
                            message=f"{spam_text}",
                            random_id=random.randint(1, 1000000)
                        )
                        time.sleep(1.5)
                except Exception as e:
                    print(f"Ошибка спама: {e}")

            # 2. КОМАНДА: !реакция (нужно отправлять в ответ на сообщение)
            elif text.startswith("!реакция"):
                try:
                    # Удаляем команду
                    try: vk.messages.delete(message_ids=message_id, delete_for_all=1)
                    except: pass
                    
                    # Проверяем, есть ли пересланное сообщение (ответ)
                    msg_info = vk.messages.getById(message_ids=message_id)['items'][0]
                    reply_msg = msg_info.get('reply_message')
                    
                    if reply_msg:
                        victim_id = reply_msg['from_id']
                        parts = text.split(" ")
                        
                        # Если смайл не указали, ставим по умолчанию палец вверх (id 1)
                        # Доступные ID реакций в ВК: 1 (👍), 2 (👎), 3 (❤), 4 (😂), 5 (😮), 6 (😢), 7 (😡) и т.д.
                        # Чтобы было проще, мы будем передавать цифру-ID реакции, например: !реакция 3
                        try:
                            reaction_id = int(parts[1])
                        except:
                            reaction_id = 1 # по умолчанию 👍
                            
                        target_reactions[victim_id] = reaction_id
                        print(f"Target locked! Ставлю реакцию {reaction_id} на юзера {victim_id}")
                except Exception as e:
                    print(f"Ошибка установки реакции: {e}")

            # 3. КОМАНДА: !стопреакция (в ответ на сообщение)
            elif text.startswith("!стопреакция"):
                try:
                    try: vk.messages.delete(message_ids=message_id, delete_for_all=1)
                    except: pass
                    
                    msg_info = vk.messages.getById(message_ids=message_id)['items'][0]
                    reply_msg = msg_info.get('reply_message')
                    
                    if reply_msg:
                        victim_id = reply_msg['from_id']
                        if victim_id in target_reactions:
                            del target_reactions[victim_id]
                            print(f"Юзер {victim_id} удален из списка.")
                except Exception as e:
                    print(f"Ошибка отмены реакции: {e}")

if __name__ == "__main__":
    try: main()
    except Exception as e: print(f"Ошибка работы: {e}")
