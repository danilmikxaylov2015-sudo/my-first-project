import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
import random
import time

# Твой успешно полученный токен пользователя
USER_TOKEN = "vk1.a.edynZWBJGgef-lj0kOg-OdqtEzdzTm6YwntGyuzMSe8lf53NmWCYCsEW1XCyVTDZnjLnzeamx52N1grIhvo3Ovm7ykq081C7224Qo_uP8ls_tFptamaBjr-1tX6quT3IXUXDkQ9_UL0E1Ye39vGwNwsor7IOzJtx25w82uJXLcLgLmwQuTUtc3nyEclBzFluegboRUL8jb7U4LqFlxo-Pw"

def main():
    # Авторизация в ВК от имени твоего аккаунта
    vk_session = vk_api.VkApi(token=USER_TOKEN)
    vk = vk_session.get_api()
    
    # Подключение к серверу обновлений (LongPoll)
    longpoll = VkLongPoll(vk_session)
    
    print("🚀 Селф-бот успешно запущен и работает прямо с твоего аккаунта!")
    print("Доступная команда в любом чате/ЛС: !спам [текст] [количество]")

    for event in longpoll.listen():
        # Ловим только новые входящие или исходящие текстовые сообщения
        if event.type == VkEventType.MESSAGE_NEW:
            
            peer_id = event.peer_id
            text = event.text
            
            # Проверяем команду активации спама
            if text.startswith("!спам"):
                try:
                    # Разбираем сообщение по пробелам
                    parts = text.split(" ")
                    
                    # Извлекаем количество (последний элемент) и текст спама
                    count = int(parts[-1])
                    spam_text = " ".join(parts[1:-1])
                    
                    # Небольшое уведомление о старте атаки
                    vk.messages.send(
                        peer_id=peer_id,
                        message=f"🔥 Запускаю отправку сообщений ({count} раз)!",
                        random_id=random.randint(1, 1000000)
                    )
                    
                    # Цикл отправки
                    for i in range(count):
                        vk.messages.send(
                            peer_id=peer_id,
                            message=f"{spam_text}",
                            random_id=random.randint(1, 1000000)
                        )
                        # Задержка 1.5 секунды, чтобы ВК не заблокировал аккаунт за флуд
                        time.sleep(1.5)
                        
                    # Уведомление об успешном окончании
                    vk.messages.send(
                        peer_id=peer_id,
                        message="✅ Всё отправлено!",
                        random_id=random.randint(1, 1000000)
                    )
                    
                except Exception as e:
                    print(f"Ошибка при выполнении команды: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка работы бота: {e}")
