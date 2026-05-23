import os
import sys
import subprocess
import re

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

# ТВОИ НАСТРОЙКИ
USER_TOKEN = "vk1.a.edynZWBJGgef-lj0kOg-OdqtEzdzTm6YwntGyuzMSe8lf53NmWCYCsEW1XCyVTDZnjLnzeamx52N1grIhvo3Ovm7ykq081C7224Qo_uP8ls_tFptamaBjr-1tX6quT3IXUXDkQ9_UL0E1Ye39vGwNwsor7IOzJtx25w82uJXLcLgLmwQuTUtc3nyEclBzFluegboRUL8jb7U4LqFlxo-Pw"
MY_USER_ID = 848213593

# Списки целей в памяти
target_reactions = {}  
target_negatives = []  
target_clones = []     

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
    """Вспомогательная функция для определения ID цели по реплаю или тегу"""
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
    
    print("🚀 Бот успешно запущен и готов к работе!")

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

            # Если пишет кто-то другой (авто-троллинг)
            if from_id and from_id != MY_USER_ID:
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

            # Если команду вводишь ты (Владелец)
            if text.startswith("/") and (from_id == MY_USER_ID or peer_id == MY_USER_ID):
                
                # --- УМНОЕ УДАЛЕНИЕ СООБЩЕНИЙ ---
                if text.strip() in ["/удалить", "/дел"]:
                    try:
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            target_msg_id = reply_msg['id']
                            try: vk.messages.delete(message_ids=message_id, delete_for_all=1)
                            except: pass
                            vk.messages.delete(message_ids=target_msg_id, delete_for_all=1)
                    except Exception as e:
                        print(f"Ошибка удаления: {e}")
                    continue

                # Сразу удаляем саму команду для скрытности в беседе
                try: vk.messages.delete(message_ids=message_id, delete_for_all=1)
                except: pass

                # --- КОМАНДА: ОПУБЛИКОВАТЬ ПОСТ НА СТЕНУ ---
                if text.startswith("/опубликовать"):
                    try:
                        post_text = text[13:].strip()
                        if post_text:
                            wall_post = vk.wall.post(message=post_text)
                            post_id = wall_post.get('post_id')
                            vk.messages.send(
                                peer_id=peer_id, 
                                message=f"✅ Пост успешно опубликован на твоей стене! ID поста: {post_id}", 
                                random_id=random.randint(1, 1000000)
                            )
                        else:
                            vk.messages.send(
                                peer_id=peer_id, 
                                message="⚠️ Напиши текст поста после команды. Пример: /опубликовать Всем привет!", 
                                random_id=random.randint(1, 1000000)
                            )
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка публикации поста: {e}", random_id=random.randint(1, 1000000))
                    continue

                # --- КОМАНДА: СПИСОК ГРУПП ПОЛЬЗОВАТЕЛЯ ---
                elif text.startswith("/группы"):
                    try:
                        t_id = get_target_id(text, msg_info, vk)
                        if t_id:
                            groups = vk.groups.get(user_id=t_id, extended=1, count=15)
                            if groups['items']:
                                lines = [f"➡️ {g['name']} (vk.com/{g['screen_name']})" for g in groups['items']]
                                res_text = f"📋 Список открытых групп id{t_id}:\n" + "\n".join(lines)
                            else:
                                res_text = f"❌ Группы пользователя id{t_id} скрыты приватностью или отсутствуют."
                            
                            vk.messages.send(peer_id=peer_id, message=res_text, random_id=random.randint(1, 1000000))
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка получения групп: {e}", random_id=random.randint(1, 1000000))
                    continue

                # --- КОМАНДА: ДОБАВИТЬ В ДРУЗЬЯ (ПРИНЯТЬ ЗАЯВКУ) ---
                elif text.startswith("/друзья"):
                    try:
                        t_id = get_target_id(text, msg_info, vk)
                        if t_id:
                            # Используем approve, он стабильнее на пользовательских токенах
                            vk.friends.approve(user_id=t_id)
                            vk.messages.send(peer_id=peer_id, message=f"➕ Взаимодействие с друзьями для id{t_id} выполнено!", random_id=random.randint(1, 1000000))
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Не удалось обработать друзей: {e}", random_id=random.randint(1, 1000000))
                    continue

                # --- КОМАНДА /ОТПРАВИТЬ (В ТОТ ЖЕ ЧАТ) ---
                elif text.strip() == "/отправить":
                    try:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message="ХОТИТЕ ПОЛУЧИТЬ МЕНЯ ПИШИ В ЛС", 
                            random_id=random.randint(1, 1000000)
                        )
                    except: pass
                    continue

                # --- КОМАНДА /ОТПРЧЕЛУ (В ЛС ЖЕРТВЕ) ---
                elif text.startswith("/отпрчелу"):
                    try:
                        target_id = get_target_id(text, msg_info, vk)
                        message_to_send = ""
                        
                        reply_msg = msg_info.get('reply_message')
                        if reply_msg:
                            message_to_send = text[10:].strip()
                        else:
                            clean_text = re.sub(r'\[.*?\]', '', text).strip()
                            message_to_send = clean_text[10:].strip()

                        if target_id and message_to_send:
                            vk.messages.send(peer_id=target_id, message=message_to_send, random_id=random.randint(1, 1000000))
                            vk.messages.send(peer_id=peer_id, message=f"✅ Отправлено в ЛС для id{target_id}", random_id=random.randint(1, 1000000))
                    except: pass
                    continue

                # --- КОМАНДЫ ТРОЛЛИНГА ---
                elif text.startswith("/негатив"):
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

if __name__ == "__main__":
    try: main()
    except Exception as e: print(f"Ошибка в главном потоке: {e}")
