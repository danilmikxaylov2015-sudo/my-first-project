#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VK-бот автопиара для бесед — один файл.

Как работает:
- Нужен только токен сообщества, VK_GROUP_ID указывать не требуется.
- ID сообщества бот пытается определить автоматически по токену.
- Настройки хранятся отдельно для каждой беседы.
- Проверки администратора и отдельной активации нет.
- Командами может пользоваться только VK-пользователь 840292888:
    /text <текст> — задать сообщение для текущей беседы
    /on           — включить отправку каждые 2 минуты
    /off          — выключить отправку в текущей беседе
    /status       — показать настройки текущей беседы
    /help         — показать команды
- При перезапуске настройки сохраняются в SQLite.
- vk-api устанавливается автоматически, если библиотеки нет.

Используйте регулярную отправку только в беседах, где на неё согласны
участники и администрация. Частые повторяющиеся сообщения могут быть
расценены платформой или пользователями как спам.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


# ============================================================
# НАСТРОЙКИ
# ============================================================

# Вставьте сюда токен сообщества либо задайте переменную окружения VK_TOKEN.
VK_TOKEN = os.getenv("VK_TOKEN", "vk1.a.9bunEiB9XBiUNHNmgVA4hTjPTGtM5cYpYfxGGWLLTgITxYT78MN3E7DTv8LXOVEUvPhglrZ_ZPIYES8Mx3s-hIk6PfITnNBA9LePu9ZfnXT4DeJBr27691J-wxdC1P9rRjel6P_QgRqfyb5MraIy-N2mXJIsTwQHlUutJ6LGOqbumYnxrQg5YWvluDBi3s9oVRZS_gcirCZIHpac2kYKPA")

# Единственный VK ID, которому разрешено управлять ботом.
OWNER_ID = 840292888

PROMO_INTERVAL_SECONDS = 120       # 2 минуты
WORKER_CHECK_SECONDS = 5           # частота проверки очереди
MAX_PROMO_TEXT_LENGTH = 4000
DATABASE_FILE = Path(__file__).with_name("vk_autopromo.db")
VK_API_PACKAGE = "vk-api==11.10.1"


# ============================================================
# АВТОУСТАНОВКА VK-API
# ============================================================

def ensure_vk_api() -> None:
    try:
        import vk_api  # noqa: F401
    except ImportError:
        print(
            f"[Установка] Пакет vk-api не найден. "
            f"Устанавливаю {VK_API_PACKAGE}..."
        )
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", VK_API_PACKAGE]
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                "Автоустановка vk-api не удалась.\n"
                f"Установите вручную:\n"
                f"{sys.executable} -m pip install {VK_API_PACKAGE}"
            ) from exc


ensure_vk_api()

import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.exceptions import ApiError
from vk_api.utils import get_random_id


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("vk-autopromo")


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

class ChatStore:
    """Настройки автопиара отдельно для каждого peer_id."""

    def __init__(self, db_path: Path) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        self._prepare_database()

    def _prepare_database(self) -> None:
        with self._lock, self._conn:
            # Совместимо и с предыдущей версией файла:
            # лишний столбец activated, если он уже существует, не мешает.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    peer_id      INTEGER PRIMARY KEY,
                    enabled      INTEGER NOT NULL DEFAULT 0,
                    promo_text   TEXT NOT NULL DEFAULT '',
                    last_sent_at REAL NOT NULL DEFAULT 0,
                    updated_at   REAL NOT NULL DEFAULT 0
                )
                """
            )

            columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(chats)"
                ).fetchall()
            }

            required_columns = {
                "enabled": "INTEGER NOT NULL DEFAULT 0",
                "promo_text": "TEXT NOT NULL DEFAULT ''",
                "last_sent_at": "REAL NOT NULL DEFAULT 0",
                "updated_at": "REAL NOT NULL DEFAULT 0",
            }

            for name, definition in required_columns.items():
                if name not in columns:
                    self._conn.execute(
                        f"ALTER TABLE chats ADD COLUMN {name} {definition}"
                    )

    def ensure_chat(self, peer_id: int) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO chats (peer_id, updated_at)
                VALUES (?, ?)
                ON CONFLICT(peer_id) DO NOTHING
                """,
                (peer_id, now),
            )

    def get(self, peer_id: int) -> dict[str, Any]:
        self.ensure_chat(peer_id)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT peer_id, enabled, promo_text, last_sent_at, updated_at
                FROM chats
                WHERE peer_id = ?
                """,
                (peer_id,),
            ).fetchone()

        if row is None:
            return {}

        return dict(row)

    def set_text(self, peer_id: int, promo_text: str) -> None:
        self.ensure_chat(peer_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE chats
                SET promo_text = ?,
                    updated_at = ?
                WHERE peer_id = ?
                """,
                (promo_text, time.time(), peer_id),
            )

    def set_enabled(self, peer_id: int, enabled: bool) -> None:
        self.ensure_chat(peer_id)
        now = time.time()

        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE chats
                SET enabled = ?,
                    last_sent_at =
                        CASE
                            WHEN ? = 1 THEN ?
                            ELSE last_sent_at
                        END,
                    updated_at = ?
                WHERE peer_id = ?
                """,
                (
                    int(enabled),
                    int(enabled),
                    now,
                    now,
                    peer_id,
                ),
            )

    def due_chats(self, now: float) -> list[dict[str, Any]]:
        threshold = now - PROMO_INTERVAL_SECONDS

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT peer_id, promo_text, last_sent_at
                FROM chats
                WHERE enabled = 1
                  AND promo_text <> ''
                  AND last_sent_at <= ?
                ORDER BY last_sent_at ASC
                """,
                (threshold,),
            ).fetchall()

        return [dict(row) for row in rows]

    def mark_attempt(self, peer_id: int, timestamp: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE chats
                SET last_sent_at = ?,
                    updated_at = ?
                WHERE peer_id = ?
                """,
                (timestamp, timestamp, peer_id),
            )


store = ChatStore(DATABASE_FILE)


# ============================================================
# VK
# ============================================================

vk_session: vk_api.VkApi | None = None
vk: Any = None


def validate_token() -> None:
    if not VK_TOKEN or VK_TOKEN == "ВСТАВЬТЕ_ТОКЕН_СООБЩЕСТВА":
        raise SystemExit(
            "Вставьте токен сообщества в VK_TOKEN в начале файла "
            "или задайте переменную окружения VK_TOKEN."
        )


def extract_group_id(response: Any) -> int | None:
    """
    Извлекает ID сообщества из разных форматов ответа groups.getById.

    VK API в разных версиях может возвращать:
    - список сообществ;
    - словарь с ключом groups;
    - словарь с ключом items;
    - один объект сообщества.
    """
    if isinstance(response, list):
        for item in response:
            result = extract_group_id(item)
            if result:
                return result
        return None

    if not isinstance(response, dict):
        return None

    # Объект сообщества обычно содержит id и хотя бы одно из этих полей.
    if "id" in response and any(
        key in response
        for key in ("name", "screen_name", "type", "is_closed")
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
    """
    Автоматически получает ID сообщества по его токену.

    Для Bots Long Poll библиотеке vk-api технически нужен group_id,
    поэтому бот получает его сам через API — вручную вводить ID не нужно.
    """
    attempts = (
        {},
        {"fields": "screen_name"},
    )

    last_error: Exception | None = None

    for params in attempts:
        try:
            response = vk.groups.getById(**params)
            group_id = extract_group_id(response)
            if group_id:
                return group_id
        except Exception as exc:
            last_error = exc
            log.warning(
                "Не удалось определить ID сообщества с параметрами %s: %s",
                params,
                exc,
            )

    details = f"\nПоследняя ошибка: {last_error}" if last_error else ""

    raise SystemExit(
        "Не удалось автоматически определить ID сообщества.\n"
        "Проверьте, что указан именно действующий токен сообщества, "
        "а не пользовательский токен, и что у ключа есть доступ "
        "к сообщениям сообщества."
        + details
    )


def send_message(peer_id: int, message: str) -> None:
    vk.messages.send(
        peer_id=peer_id,
        random_id=get_random_id(),
        message=message,
    )


def is_group_chat(peer_id: int) -> bool:
    return peer_id >= 2_000_000_000


def normalize_text(text: str) -> str:
    text = text.strip()

    # Убирает упоминание сообщества в начале:
    # [club123456|Название] /on
    text = re.sub(
        r"^\[(?:club|public)\d+\|[^\]]+\]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def command_name(text: str) -> str:
    if not text:
        return ""

    first = text.split(maxsplit=1)[0].lower()

    # На случай формата /on@botname.
    return first.split("@", maxsplit=1)[0]


def extract_text_argument(text: str) -> str:
    """
    Возвращает всё после команды /text, сохраняя переносы строк.
    """
    match = re.match(
        r"^/text(?:@[^\s]+)?(?:\s+([\s\S]*))?$",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    return (match.group(1) or "").strip()


def send_help(peer_id: int, greeting: bool = False) -> None:
    prefix = (
        "Бот добавлен и сразу готов к работе.\n\n"
        if greeting
        else ""
    )

    send_message(
        peer_id,
        prefix
        + (
            "Команды для этой беседы:\n"
            "/text <сообщение> — задать или изменить текст\n"
            "/on — включить отправку каждые 2 минуты\n"
            "/off — выключить отправку\n"
            "/status — показать состояние\n"
            "/help — показать команды\n\n"
            "Настройки действуют только в той беседе, "
            "где была отправлена команда."
        ),
    )


def handle_command(peer_id: int, text: str) -> None:
    """Обрабатывает команды разрешённого владельца бота."""
    normalized = normalize_text(text)
    cmd = command_name(normalized)

    if cmd == "/text":
        promo_text = extract_text_argument(normalized)

        if not promo_text:
            send_message(
                peer_id,
                "Использование:\n/text Ваш рекламный текст",
            )
            return

        if len(promo_text) > MAX_PROMO_TEXT_LENGTH:
            send_message(
                peer_id,
                (
                    f"Текст слишком длинный: {len(promo_text)} символов.\n"
                    f"Максимум: {MAX_PROMO_TEXT_LENGTH}."
                ),
            )
            return

        store.set_text(peer_id, promo_text)

        send_message(
            peer_id,
            (
                "✅ Текст для этой беседы сохранён.\n"
                "Для включения отправьте /on.\n\n"
                f"Текущий текст:\n{promo_text}"
            ),
        )
        return

    if cmd == "/on":
        state = store.get(peer_id)
        promo_text = str(state.get("promo_text", "")).strip()

        if not promo_text:
            send_message(
                peer_id,
                "Сначала задайте сообщение:\n/text Ваш рекламный текст",
            )
            return

        if state.get("enabled"):
            send_message(
                peer_id,
                "Автопиар в этой беседе уже включён.",
            )
            return

        store.set_enabled(peer_id, True)

        send_message(
            peer_id,
            (
                "▶ Автопиар включён только для этой беседы.\n"
                "Сообщение будет отправляться каждые 2 минуты.\n"
                "Первое сообщение — примерно через 2 минуты."
            ),
        )
        return

    if cmd == "/off":
        state = store.get(peer_id)

        if not state.get("enabled"):
            send_message(
                peer_id,
                "Автопиар в этой беседе уже выключен.",
            )
            return

        store.set_enabled(peer_id, False)
        send_message(
            peer_id,
            "⏹ Автопиар выключен только в этой беседе.",
        )
        return

    if cmd == "/status":
        state = store.get(peer_id)
        enabled = "включён" if state.get("enabled") else "выключен"

        promo_text = str(state.get("promo_text", "")).strip()
        preview = promo_text if promo_text else "не задан"

        if len(preview) > 700:
            preview = preview[:700] + "…"

        send_message(
            peer_id,
            (
                "Статус этой беседы:\n"
                f"• Автопиар: {enabled}\n"
                f"• Интервал: {PROMO_INTERVAL_SECONDS // 60} минуты\n"
                f"• Текст:\n{preview}"
            ),
        )
        return

    if cmd == "/help":
        send_help(peer_id)


# ============================================================
# ФОНОВЫЙ ЦИКЛ
# ============================================================

def promo_worker() -> None:
    while True:
        now = time.time()

        try:
            for chat in store.due_chats(now):
                peer_id = int(chat["peer_id"])

                # Повторная проверка перед отправкой, чтобы /off
                # максимально быстро останавливал рассылку.
                current = store.get(peer_id)
                promo_text = str(current.get("promo_text", "")).strip()

                if not current.get("enabled") or not promo_text:
                    continue

                try:
                    send_message(peer_id, promo_text)
                    log.info(
                        "Промосообщение отправлено в peer_id=%s",
                        peer_id,
                    )
                except ApiError as exc:
                    log.error(
                        "Ошибка VK при отправке в peer_id=%s: %s",
                        peer_id,
                        exc,
                    )
                except Exception:
                    log.exception(
                        "Неожиданная ошибка отправки в peer_id=%s",
                        peer_id,
                    )
                finally:
                    # При ошибке также обновляем время, чтобы бот
                    # не создавал частые повторные запросы.
                    store.mark_attempt(peer_id, time.time())

        except Exception:
            log.exception("Ошибка фонового цикла")

        time.sleep(WORKER_CHECK_SECONDS)


# ============================================================
# ЗАПУСК
# ============================================================

def main() -> None:
    global vk_session, vk

    validate_token()

    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()

    group_id = detect_group_id()
    longpoll = VkBotLongPoll(vk_session, group_id)

    threading.Thread(
        target=promo_worker,
        name="promo-worker",
        daemon=True,
    ).start()

    log.info("Бот запущен.")
    log.info("ID сообщества определён автоматически: %s", group_id)
    log.info("Интервал отправки: %s секунд", PROMO_INTERVAL_SECONDS)
    log.info("Управлять ботом может только VK ID: %s", OWNER_ID)

    while True:
        try:
            for event in longpoll.listen():
                if event.type != VkBotEventType.MESSAGE_NEW:
                    continue

                message = event.object.message

                peer_id = int(message.get("peer_id", 0))
                from_id = int(message.get("from_id", 0))
                text = str(message.get("text", "") or "")
                action = message.get("action") or {}

                if not is_group_chat(peer_id):
                    continue

                # Когда именно это сообщество пригласили в беседу,
                # бот сразу показывает команды. Активация не требуется.
                if action.get("type") == "chat_invite_user":
                    try:
                        member_id = int(action.get("member_id", 0))
                    except (TypeError, ValueError):
                        member_id = 0

                    if member_id == -group_id:
                        store.ensure_chat(peer_id)
                        try:
                            send_help(peer_id, greeting=True)
                        except ApiError:
                            log.exception(
                                "Не удалось отправить приветствие "
                                "в peer_id=%s",
                                peer_id,
                            )
                    continue

                # Управлять ботом может только один заданный VK ID.
                # Сообщения остальных пользователей, сообществ и ботов
                # полностью игнорируются.
                if from_id != OWNER_ID:
                    continue

                handle_command(peer_id, text)

        except KeyboardInterrupt:
            log.info("Бот остановлен пользователем.")
            return
        except Exception:
            log.exception(
                "Bots Long Poll отключился. Повтор через 5 секунд."
            )
            time.sleep(5)


if __name__ == "__main__":
    main()
