import os
import sys
import subprocess

# Функция автоматической установки библиотеки прямо при запуске
def install_vk_api():
    try:
        import vk_api
    except ImportError:
        print("Библиотека vk_api не найдена. Устанавливаю...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vk-api==11.10.1"])
        print("Библиотека успешно установлена!")

# Запускаем установку перед основным кодом
install_vk_api()

# Теперь спокойно импортируем всё остальное
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
import random
import time

# Твой рабочий токен
USER_TOKEN = "vk1.a.edynZWBJGgef-lj0kOg-OdqtEzdzTm6YwntGyuzMSe8lf53NmWCYCsEW1XCyVTDZnjLnzeamx52N1grIhvo3Ovm7ykq081C7224Qo_uP8ls_tFptamaBjr-1tX6quT3IXUXDkQ9_UL0E1Ye39vGwNwsor7IOzJtx25w82uJXLcLgLmwQuTUtc3nyEclBzFluegboRUL8jb7U4LqFlxo-Pw"

def main():
    vk_session = vk_api.VkApi(token=USER_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    
    print("🚀 Селф-бот успешно запущен! Команда: !спам [текст] [количество]")

    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW:
            peer_id = event.peer_id
            text = event.text
            
            if text.startswith("!спам"):
                try:
                    parts = text.split(" ")
                    count = int(parts[-1])
                    spam_text = " ".join(parts[1:-1])
                    
                    vk.messages.send(
                        peer_id=peer_id,
                        message=f"🔥 Запуск спама ({count} раз)!",
                        random_id=random.randint(1, 1000000)
                    )
                    
                    for i in range(count):
                        vk.messages.send(
                            peer_id=peer_id,
                            message=f"{spam_text}",
                            random_id=random.randint(1, 1000000)
                        )
                        time.sleep(1.5)  # Задержка безопасности
                        
                    vk.messages.send(
                        peer_id=peer_id,
                        message="✅ Готово!",
                        random_id=random.randint(1, 1000000)
                    )
                    
                except Exception as e:
                    print(f"Ошибка: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка работы: {e}")
