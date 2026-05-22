import sys
import subprocess
import time
import random
import threading

try:
    import vk_api
    from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
except ImportError:
    print("vk_api не найден. Устанавливаю...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "vk_api"])

    import vk_api
    from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType


TOKEN = "vk1.a.jmhGtKNRy-okO7WM6HyGJofKiJMaUnBDyB3kEqxdKypWpcnJaEB7KBJixSmIMLc7YLBJHu6wKY2sElm6VlK59GWdnir2DJQl5D9ohPLQ_8USyg-_gpviWLw31YaUIcx51Y84dSXBPjUpwIULup3JGkiHECtNOGSqlxX4q3IvWgeGEwzaXefqwmTa9aFx2-g9b5dmx07Wx-HH3-Tu_2HDag"

# Владельцы бота
OWNER_IDS = [
    848213593,
    750694024
]

PR_TEXT = "пишите в лс пж. [danil_mikxaylov|Данил Михайлов]"

DELAY = 60

vk_session = vk_api.VkApi(token=TOKEN)
vk = vk_session.get_api()

active_chats = {}


def get_group_id():
    try:
        info = vk.groups.getById()
        return info[0]["id"]
    except Exception as e:
        print("Ошибка получения ID сообщества:", e)
        sys.exit()


GROUP_ID = get_group_id()
longpoll = VkBotLongPoll(vk_session, GROUP_ID)


def send_message(peer_id, text):
    vk.messages.send(
        peer_id=peer_id,
        message=text,
        random_id=random.randint(1, 999999999)
    )


def pr_loop(peer_id):
    while active_chats.get(peer_id, False):
        try:
            send_message(peer_id, PR_TEXT)
            print(f"Пиар отправлен в чат {peer_id}")
        except Exception as e:
            print("Ошибка отправки:", e)

        time.sleep(DELAY)


print("VK пиар-бот запущен.")
print("Команды владельцев: /start и /stop")


for event in longpoll.listen():
    if event.type == VkBotEventType.MESSAGE_NEW:
        msg = event.object.message

        text = msg.get("text", "").lower().strip()
        peer_id = msg.get("peer_id")
        from_id = msg.get("from_id")

        if text in ["/start", "/stop"] and from_id not in OWNER_IDS:
            send_message(peer_id, "У тебя нет доступа к этой команде.")
            continue

        if text == "/start":
            if active_chats.get(peer_id):
                send_message(peer_id, "Пиар уже запущен.")
            else:
                active_chats[peer_id] = True
                threading.Thread(target=pr_loop, args=(peer_id,), daemon=True).start()
                send_message(peer_id, "Пиар запущен. Буду писать каждую минуту.")

        elif text == "/stop":
            if active_chats.get(peer_id):
                active_chats[peer_id] = False
                send_message(peer_id, "Пиар остановлен.")
            else:
                send_message(peer_id, "Пиар и так не запущен.")
