import os
import sys
import time

# --- САМОУСТАНОВКА БИБЛИОТЕК ---
try:
    import vk_api
    import requests
except ImportError:
    print("⏳ Устанавливаю необходимые библиотеки...")
    os.system(f"{sys.executable} -m pip install vk_api requests")
    import vk_api
    import requests

from vk_api.longpoll import VkLongPoll, VkEventType

# --- ТВОИ ДАННЫЕ ---
TOKEN = 'vk1.a.jmhGtKNRy-okO7WM6HyGJofKiJMaUnBDyB3kEqxdKypWpcnJaEB7KBJixSmIMLc7YLBJHu6wKY2sElm6VlK59GWdnir2DJQl5D9ohPLQ_8USyg-_gpviWLw31YaUIcx51Y84dSXBPjUpwIULup3JGkiHECtNOGSqlxX4q3IvWgeGEwzaXefqwmTa9aFx2-g9b5dmx07Wx-HH3-Tu_2HDag'
ADMIN_ID = 848213593
URL = 'https://hostgta.ru/support/ajax//servers/base/883617'
COOKIE = '_COCREAL=realuser; _COLSCL=dfdcf364dc2a52cbc9b429940d1195b9; _ym_uid=177572928444511068; _ym_d=1775729284; key=0240d4944976ae53dcadeffb275b5ed0377ac1f791f5388ca2e4883b5a11cd66; key2=bd52c76ff68b3d664016d779e3b6894fcf1c9e59301258e0a02d369b5f37b87c; refers=xlwNjALZS3Aw5vE9pbFnjJEaGD2jbcO1as9xPIV1; PHPSESSID=941e3c3ff6af453f5c6c6e9a68faed38; _ym_isad=2; refer=https%3A%2F%2Fhostgta.ru%2F'

headers = {
    'Cookie': COOKIE,
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Content-Type': 'application/x-www-form-urlencoded'
}
payload = 'timest=0'

# --- АВТОРИЗАЦИЯ И НАСТРОЙКИ ---
vk_session = vk_api.VkApi(token=TOKEN)
longpoll = VkLongPoll(vk_session)
vk = vk_session.get_api()

last_command_time = 0.0  # Переменная для тайм-аута

def send_msg(user_id, text):
    vk.messages.send(user_id=user_id, message=text, random_id=0)

print("✅ Бот успешно запущен и готов к работе!")

# --- ОСНОВНОЙ ЦИКЛ БОТА ---
for event in longpoll.listen():
    if event.type == VkEventType.MESSAGE_NEW and event.to_me:
        # Проверка на админа
        if event.user_id != ADMIN_ID:
            continue

        # Защита от спама (тайм-аут 0.9 сек)
        current_time = time.time()
        if current_time - last_command_time < 0.9:
            continue
        
        text = event.text.lower()

        if text == '/старт':
            last_command_time = current_time
            send_msg(event.user_id, '⏳ Запускаю сервер...')
            try:
                res = requests.post(URL, data=payload, headers=headers)
                if res.status_code == 200:
                    send_msg(event.user_id, '✅ Сервер запущен!')
                else:
                    send_msg(event.user_id, f'❌ Сервер выдал ошибку: {res.status_code}')
            except Exception:
                send_msg(event.user_id, '❌ Ошибка соединения. Проверь куки.')

        elif text == '/стоп':
            last_command_time = current_time
            send_msg(event.user_id, '⏳ Останавливаю сервер...')
            try:
                res = requests.post(URL, data=payload, headers=headers)
                if res.status_code == 200:
                    send_msg(event.user_id, '🛑 Сервер остановлен!')
                else:
                    send_msg(event.user_id, f'❌ Сервер выдал ошибку: {res.status_code}')
            except Exception:
                send_msg(event.user_id, '❌ Ошибка соединения. Проверь куки.')