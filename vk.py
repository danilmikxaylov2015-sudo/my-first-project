import sys
import subprocess
import time
import random
import threading

# Автоустановка vk_api
try:
    import vk_api
    from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
except ImportError:
    print("vk_api не найден. Устанавливаю...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "vk_api"])

    import vk_api
    from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType


# ВСТАВЬ ТОЛЬКО ТОКЕН СООБЩЕСТВА
TOKEN = "vk1.a.jmhGtKNRy-okO7WM6HyGJofKiJMaUnBDyB3kEqxdKypWpcnJaEB7KBJixSmIMLc7YLBJHu6wKY2sElm6VlK59GWdnir2DJQl5D9ohPLQ_8USyg-_gpviWLw31YaUIcx51Y84dSXBPjUpwIULup3JGkiHECtNOGSqlxX4q3IvWgeGEwzaXefqwmTa9aFx2-g9b5dmx07Wx-HH3-Tu_2HDag"

PR_TEXT = "пишите в лс пж. [https://vk.com/danil_mikxaylov|Данил Михайлов]"
DELAY = 5

vk_session = vk_api.VkApi(token=TOKEN)
vk = vk_session.get_api()

active_chats = {}


def get_group_id():
    try:
        info = vk.groups.getById()
        group_id = info[0]["id"]
        print("ID сообщества найден:", group_id)
        return group_id
    except Exception as e:
        print("Не получилось получить ID через groups.getById:", e)

    try:
        info = vk.groups.getTokenPermissions()
        if "group_id" in info:
            group_id = info["group_id"]
            print("ID сообщества найден:", group_id)
            return group_id
    except Exception as e:
        print("Не получилось получить ID через groups.getTokenPermissions:", e)

    print("ВК не дал узнать ID сообщества по токену.")
    print("Проверь, что это именно токен сообщества, а не пользователя.")
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
            print("Пиар отправлен")
        except Exception as e:
            print("Ошибка отправки:", e)

        time.sleep(DELAY)


print("VK пиар-бот запущен.")
print("Напиши /start в беседе, чтобы запустить пиар.")
print("Напиши /stop, чтобы остановить.")


for event in longpoll.listen():
    if event.type == VkBotEventType.MESSAGE_NEW:
        msg = event.object.message

        text = msg.get("text", "").lower().strip()
        peer_id = msg.get("peer_id")

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
