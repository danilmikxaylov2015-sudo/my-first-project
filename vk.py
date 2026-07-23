#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Отдельный VK-бот для расформирования беседы.

Только пользователь VK ID 840292888 может выполнить /расформ.
После команды бот показывает две кнопки:
- 🧨 Удалить всех
- ❌ Не делать расформ

Бот пытается исключить всех обычных пользователей, кроме:
- пользователя 840292888;
- создателя беседы;
- сообществ.

Сообщество-бот должно быть администратором беседы.
ID сообщества определяется автоматически по токену.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any

VK_TOKEN = os.getenv("VK_TOKEN", "vk1.a.1SlnpCE460vdARgg09mweisHyu0um4kSD1cmKBNs2u4OcrWUHJtZAj678YVIX8OZlsRu9jLblRvBsYFTRuWwJNwufVovu9b-NVW9HzQT9Bws4pG3PG-lRq5Vm9iLFs5zXuoRO_8i86V5eOzSluWHF92ZVqyIXocRcPT5Cxj3hT8Q_LCx-mwywz5lzTvzQbsAGjA1sGTNp88It33b1-wAxA")
OWNER_ID = 840292888
CONFIRMATION_TIMEOUT_SECONDS = 60
REMOVE_DELAY_SECONDS = 0.40
VK_API_PACKAGE = "vk-api==11.10.1"


def ensure_vk_api() -> None:
    try:
        import vk_api  # noqa: F401
    except ImportError:
        print(f"[Установка] Устанавливаю {VK_API_PACKAGE}...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", VK_API_PACKAGE]
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                "Не удалось установить vk-api. Выполните вручную:\n"
                f"{sys.executable} -m pip install {VK_API_PACKAGE}"
            ) from exc


ensure_vk_api()

import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.exceptions import ApiError
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("vk-disband-bot")

vk_session: vk_api.VkApi | None = None
vk: Any = None
BOT_GROUP_ID = 0

pending_confirmations: dict[int, float] = {}
confirmation_lock = threading.RLock()
running_chats: set[int] = set()
running_lock = threading.RLock()


def validate_token() -> None:
    if not VK_TOKEN or VK_TOKEN == "ВСТАВЬТЕ_ТОКЕН_СООБЩЕСТВА":
        raise SystemExit(
            "Вставьте токен сообщества в VK_TOKEN в начале файла "
            "или задайте переменную окружения VK_TOKEN."
        )


def extract_group_id(response: Any) -> int | None:
    if isinstance(response, list):
        for item in response:
            result = extract_group_id(item)
            if result:
                return result
        return None

    if not isinstance(response, dict):
        return None

    if "id" in response and any(
        key in response for key in ("name", "screen_name", "type", "is_closed")
    ):
        try:
            group_id = int(response["id"])
            if group_id > 0:
                return group_id
        except (TypeError, ValueError):
            pass

    for key in ("groups", "items", "response"):
        if key in response:
            result = extract_group_id(response[key])
            if result:
                return result

    return None


def detect_group_id() -> int:
    last_error: Exception | None = None

    for params in ({}, {"fields": "screen_name"}):
        try:
            response = vk.groups.getById(**params)
            group_id = extract_group_id(response)
            if group_id:
                return group_id
        except Exception as exc:
            last_error = exc
            log.warning("Не удалось определить ID сообщества: %s", exc)

    details = f"\nПоследняя ошибка: {last_error}" if last_error else ""
    raise SystemExit(
        "Не удалось определить ID сообщества. Проверьте токен сообщества."
        + details
    )


def send_message(peer_id: int, message: str, keyboard: str | None = None) -> None:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "random_id": get_random_id(),
        "message": message,
    }
    if keyboard is not None:
        params["keyboard"] = keyboard
    vk.messages.send(**params)


def is_group_chat(peer_id: int) -> bool:
    return peer_id >= 2_000_000_000


def peer_id_to_chat_id(peer_id: int) -> int:
    return peer_id - 2_000_000_000


def normalize_text(text: str) -> str:
    text = text.strip()
    return re.sub(
        r"^\[(?:club|public)\d+\|[^\]]+\]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def confirmation_keyboard() -> str:
    keyboard = VkKeyboard(one_time=True, inline=False)
    keyboard.add_button(
        "🧨 Удалить всех",
        color=VkKeyboardColor.NEGATIVE,
        payload={"action": "confirm_disband"},
    )
    keyboard.add_line()
    keyboard.add_button(
        "❌ Не делать расформ",
        color=VkKeyboardColor.SECONDARY,
        payload={"action": "cancel_disband"},
    )
    return keyboard.get_keyboard()


def empty_keyboard() -> str:
    return VkKeyboard.get_empty_keyboard()


def create_confirmation(peer_id: int) -> None:
    with confirmation_lock:
        pending_confirmations[peer_id] = (
            time.time() + CONFIRMATION_TIMEOUT_SECONDS
        )


def consume_confirmation(peer_id: int) -> bool:
    with confirmation_lock:
        expires_at = pending_confirmations.pop(peer_id, None)
    return expires_at is not None and time.time() <= expires_at


def cancel_confirmation(peer_id: int) -> bool:
    with confirmation_lock:
        existed = peer_id in pending_confirmations
        pending_confirmations.pop(peer_id, None)
    return existed


def collect_removable_users(
    members: list[dict[str, Any]],
) -> tuple[list[int], list[int]]:
    removable: list[int] = []
    skipped: list[int] = []

    for item in members:
        try:
            member_id = int(item.get("member_id", 0))
        except (TypeError, ValueError):
            continue

        if member_id <= 0:
            continue

        if member_id == OWNER_ID or bool(item.get("is_owner")):
            skipped.append(member_id)
            continue

        removable.append(member_id)

    return list(dict.fromkeys(removable)), list(dict.fromkeys(skipped))


def perform_disband(peer_id: int) -> None:
    with running_lock:
        if peer_id in running_chats:
            send_message(
                peer_id,
                "Расформ этой беседы уже выполняется.",
                keyboard=empty_keyboard(),
            )
            return
        running_chats.add(peer_id)

    try:
        chat_id = peer_id_to_chat_id(peer_id)

        try:
            response = vk.messages.getConversationMembers(peer_id=peer_id)
            members = response.get("items", [])
            if not isinstance(members, list):
                members = []
        except ApiError as exc:
            send_message(
                peer_id,
                "Не удалось получить участников. Назначьте сообщество "
                "администратором беседы.\n\n"
                f"Ошибка VK: {exc}",
                keyboard=empty_keyboard(),
            )
            return

        removable, skipped = collect_removable_users(members)

        if not removable:
            send_message(
                peer_id,
                "Нет доступных пользователей для удаления. "
                "Владелец команды и создатель беседы остаются.",
                keyboard=empty_keyboard(),
            )
            return

        send_message(
            peer_id,
            "⚠ Расформ начат.\n"
            f"Попытка удалить участников: {len(removable)}.",
            keyboard=empty_keyboard(),
        )

        removed: list[int] = []
        failed: list[int] = []

        for member_id in removable:
            try:
                vk.messages.removeChatUser(
                    chat_id=chat_id,
                    member_id=member_id,
                )
                removed.append(member_id)
                log.info(
                    "Удалён member_id=%s из peer_id=%s",
                    member_id,
                    peer_id,
                )
            except Exception as exc:
                failed.append(member_id)
                log.warning(
                    "Не удалось удалить member_id=%s из peer_id=%s: %s",
                    member_id,
                    peer_id,
                    exc,
                )

            time.sleep(REMOVE_DELAY_SECONDS)

        lines = [
            "✅ Расформ завершён.",
            f"Удалено: {len(removed)}.",
            f"Не удалось удалить: {len(failed)}.",
        ]

        if skipped:
            lines.append("Владелец команды и создатель беседы оставлены.")

        if failed:
            ids = ", ".join(str(user_id) for user_id in failed[:20])
            lines.append("ID с ошибкой: " + ids + ("…" if len(failed) > 20 else ""))

        send_message(peer_id, "\n".join(lines), keyboard=empty_keyboard())

    finally:
        with running_lock:
            running_chats.discard(peer_id)


def start_disband(peer_id: int) -> None:
    threading.Thread(
        target=perform_disband,
        args=(peer_id,),
        name=f"disband-{peer_id}",
        daemon=True,
    ).start()


def handle_owner_message(peer_id: int, text: str) -> None:
    lowered = normalize_text(text).lower()

    if lowered in {"/расформ", "/disband"}:
        create_confirmation(peer_id)
        send_message(
            peer_id,
            "⚠ Подтвердите расформирование этой беседы.\n\n"
            "Бот удалит всех доступных пользователей, кроме вас, "
            "создателя беседы и сообществ.\n"
            f"Подтверждение действует {CONFIRMATION_TIMEOUT_SECONDS} секунд.",
            keyboard=confirmation_keyboard(),
        )
        return

    if lowered == "❌ не делать расформ":
        existed = cancel_confirmation(peer_id)
        send_message(
            peer_id,
            "Расформ отменён." if existed else "Активного расформа нет.",
            keyboard=empty_keyboard(),
        )
        return

    if lowered == "🧨 удалить всех":
        if not consume_confirmation(peer_id):
            send_message(
                peer_id,
                "Подтверждение отсутствует или устарело. "
                "Снова отправьте /расформ.",
                keyboard=empty_keyboard(),
            )
            return
        start_disband(peer_id)


def main() -> None:
    global vk_session, vk, BOT_GROUP_ID

    validate_token()
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()
    BOT_GROUP_ID = detect_group_id()
    longpoll = VkBotLongPoll(vk_session, BOT_GROUP_ID)

    log.info("Бот запущен. ID сообщества: %s", BOT_GROUP_ID)
    log.info("Управляющий VK ID: %s", OWNER_ID)

    while True:
        try:
            for event in longpoll.listen():
                if event.type != VkBotEventType.MESSAGE_NEW:
                    continue

                message = event.object.message
                peer_id = int(message.get("peer_id", 0))
                from_id = int(message.get("from_id", 0))
                text = str(message.get("text", "") or "")

                if not is_group_chat(peer_id):
                    continue

                if from_id != OWNER_ID:
                    continue

                handle_owner_message(peer_id, text)

        except KeyboardInterrupt:
            log.info("Бот остановлен.")
            return
        except Exception:
            log.exception("Bots Long Poll отключился. Повтор через 5 секунд.")
            time.sleep(5)


if __name__ == "__main__":
    main()
