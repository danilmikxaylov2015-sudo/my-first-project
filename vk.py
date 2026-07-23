#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Blozz online — VK чат-менеджер для конференций.

Основные возможности:
- автоматическое определение создателя каждой беседы;
- дополнительные владельцы через /addowner;
- предупреждения и автоматический бан по лимиту;
- мут с удалением сообщений замьюченного пользователя;
- бан с исключением и повторным исключением при возвращении;
- локальные никнеймы внутри бота;
- настраиваемые роли, уровни и разрешения;
- отдельные настройки и персонал для каждой беседы;
- SQLite-база и автоматическая установка vk-api;
- для запуска нужен только токен сообщества.

Техническое ограничение VK:
- /unban снимает запрет в базе Blozz online;
- вернуть исключённого пользователя должен участник беседы вручную;
- для модерации сообщество должно быть администратором беседы.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================

BOT_NAME = "Blozz online"

# Вставьте токен сообщества сюда либо задайте переменную окружения VK_TOKEN.
VK_TOKEN = os.getenv("VK_TOKEN", "vk1.a.TNxx6sDk4wLdoIHVR2nhHDceAt1qw02C-1WroKPAR7Pj1F3bpgTZ5w8MX-oJ8rCorirS01X0f8zuBhL6Sgc9cRtbL-PSYBQf9Umri6vOJIiTE-j6OAAH19wpZSrQlD-kmlPmuNT6gnH4KNMFyndHIOfGvsJWlqobomGiGib33VHI-JxFVZdVfCTb6vezqvz_x-5_sApCvNJwxb-y0qXYBw")

API_VERSION = "5.199"
VK_API_PACKAGE = "vk-api==11.10.1"

DATABASE_FILE = Path(__file__).with_name("blozz_online.db")
DEFAULT_WARN_LIMIT = 3
MAX_REASON_LENGTH = 500
MAX_NICKNAME_LENGTH = 40
MAX_ROLE_NAME_LENGTH = 40
MAX_ROLE_LEVEL = 999

NATIVE_OWNER_LEVEL = 10_000
EXTRA_OWNER_LEVEL = 9_000

ALL_PERMISSIONS = {
    "warn",
    "mute",
    "ban",
    "kick",
    "nick",
    "roles",
}

PERMISSION_NAMES = {
    "warn": "предупреждения",
    "mute": "муты",
    "ban": "баны",
    "kick": "исключение",
    "nick": "никнеймы",
    "roles": "выдача ролей",
}

OWNER_SYNC_INTERVAL = 300
MUTE_CLEANUP_INTERVAL = 30
ACTION_DELAY_SECONDS = 0.35


# ============================================================
# АВТОУСТАНОВКА VK-API
# ============================================================

def ensure_vk_api() -> None:
    try:
        import vk_api  # noqa: F401
    except ImportError:
        print(
            f"[{BOT_NAME}] Библиотека vk-api не найдена. "
            f"Устанавливаю {VK_API_PACKAGE}..."
        )
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", VK_API_PACKAGE]
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                "Не удалось автоматически установить vk-api.\n"
                f"Установите вручную:\n"
                f"{sys.executable} -m pip install {VK_API_PACKAGE}"
            ) from exc


ensure_vk_api()

import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.exceptions import ApiError
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("blozz-online")


# ============================================================
# МОДЕЛИ
# ============================================================

@dataclass(frozen=True)
class ActorContext:
    user_id: int
    level: int
    permissions: frozenset[str]
    kind: str
    roles: tuple[str, ...]

    @property
    def is_native_owner(self) -> bool:
        return self.kind == "native_owner"

    @property
    def is_owner(self) -> bool:
        return self.kind in {"native_owner", "extra_owner"}


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

class Database:
    def __init__(self, path: Path) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._prepare()

    def _prepare(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    peer_id          INTEGER PRIMARY KEY,
                    native_owner_id  INTEGER NOT NULL DEFAULT 0,
                    warn_limit       INTEGER NOT NULL DEFAULT 3,
                    created_at       REAL NOT NULL,
                    updated_at       REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS extra_owners (
                    peer_id     INTEGER NOT NULL,
                    user_id     INTEGER NOT NULL,
                    added_by    INTEGER NOT NULL,
                    created_at  REAL NOT NULL,
                    PRIMARY KEY (peer_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS members (
                    peer_id       INTEGER NOT NULL,
                    user_id       INTEGER NOT NULL,
                    nickname      TEXT NOT NULL DEFAULT '',
                    warnings      INTEGER NOT NULL DEFAULT 0,
                    muted_until   REAL NOT NULL DEFAULT 0,
                    mute_reason   TEXT NOT NULL DEFAULT '',
                    banned        INTEGER NOT NULL DEFAULT 0,
                    ban_reason    TEXT NOT NULL DEFAULT '',
                    updated_at    REAL NOT NULL,
                    PRIMARY KEY (peer_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS roles (
                    peer_id       INTEGER NOT NULL,
                    role_key      TEXT NOT NULL,
                    display_name  TEXT NOT NULL,
                    level         INTEGER NOT NULL,
                    permissions   TEXT NOT NULL,
                    created_by    INTEGER NOT NULL,
                    created_at    REAL NOT NULL,
                    updated_at    REAL NOT NULL,
                    PRIMARY KEY (peer_id, role_key)
                );

                CREATE TABLE IF NOT EXISTS member_roles (
                    peer_id     INTEGER NOT NULL,
                    user_id     INTEGER NOT NULL,
                    role_key    TEXT NOT NULL,
                    assigned_by INTEGER NOT NULL,
                    created_at  REAL NOT NULL,
                    PRIMARY KEY (peer_id, user_id, role_key)
                );

                CREATE TABLE IF NOT EXISTS action_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id     INTEGER NOT NULL,
                    actor_id    INTEGER NOT NULL,
                    target_id   INTEGER NOT NULL DEFAULT 0,
                    action      TEXT NOT NULL,
                    details     TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_members_banned
                    ON members(peer_id, banned);

                CREATE INDEX IF NOT EXISTS idx_members_muted
                    ON members(peer_id, muted_until);

                CREATE INDEX IF NOT EXISTS idx_action_log_peer
                    ON action_log(peer_id, id DESC);
                """
            )

    def ensure_chat(self, peer_id: int) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO chats (
                    peer_id, native_owner_id, warn_limit,
                    created_at, updated_at
                )
                VALUES (?, 0, ?, ?, ?)
                ON CONFLICT(peer_id) DO NOTHING
                """,
                (peer_id, DEFAULT_WARN_LIMIT, now, now),
            )

    def get_chat(self, peer_id: int) -> dict[str, Any]:
        self.ensure_chat(peer_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chats WHERE peer_id = ?",
                (peer_id,),
            ).fetchone()
        return dict(row) if row else {}

    def set_native_owner(self, peer_id: int, user_id: int) -> None:
        self.ensure_chat(peer_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE chats
                SET native_owner_id = ?, updated_at = ?
                WHERE peer_id = ?
                """,
                (user_id, time.time(), peer_id),
            )
            # Создатель беседы не должен дублироваться среди доп. владельцев.
            self._conn.execute(
                """
                DELETE FROM extra_owners
                WHERE peer_id = ? AND user_id = ?
                """,
                (peer_id, user_id),
            )

    def get_native_owner(self, peer_id: int) -> int:
        return int(self.get_chat(peer_id).get("native_owner_id", 0))

    def set_warn_limit(self, peer_id: int, limit: int) -> None:
        self.ensure_chat(peer_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE chats
                SET warn_limit = ?, updated_at = ?
                WHERE peer_id = ?
                """,
                (limit, time.time(), peer_id),
            )

    def add_extra_owner(
        self,
        peer_id: int,
        user_id: int,
        added_by: int,
    ) -> None:
        self.ensure_chat(peer_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO extra_owners (
                    peer_id, user_id, added_by, created_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(peer_id, user_id)
                DO UPDATE SET
                    added_by = excluded.added_by,
                    created_at = excluded.created_at
                """,
                (peer_id, user_id, added_by, time.time()),
            )

    def remove_extra_owner(self, peer_id: int, user_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                DELETE FROM extra_owners
                WHERE peer_id = ? AND user_id = ?
                """,
                (peer_id, user_id),
            )
        return cursor.rowcount > 0

    def is_extra_owner(self, peer_id: int, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM extra_owners
                WHERE peer_id = ? AND user_id = ?
                """,
                (peer_id, user_id),
            ).fetchone()
        return row is not None

    def list_extra_owners(self, peer_id: int) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT user_id FROM extra_owners
                WHERE peer_id = ?
                ORDER BY created_at ASC
                """,
                (peer_id,),
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def ensure_member(self, peer_id: int, user_id: int) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO members (
                    peer_id, user_id, updated_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(peer_id, user_id) DO NOTHING
                """,
                (peer_id, user_id, now),
            )

    def get_member(self, peer_id: int, user_id: int) -> dict[str, Any]:
        self.ensure_member(peer_id, user_id)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM members
                WHERE peer_id = ? AND user_id = ?
                """,
                (peer_id, user_id),
            ).fetchone()
        return dict(row) if row else {}

    def set_nickname(
        self,
        peer_id: int,
        user_id: int,
        nickname: str,
    ) -> None:
        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE members
                SET nickname = ?, updated_at = ?
                WHERE peer_id = ? AND user_id = ?
                """,
                (nickname, time.time(), peer_id, user_id),
            )

    def change_warnings(
        self,
        peer_id: int,
        user_id: int,
        delta: int,
    ) -> int:
        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE members
                SET warnings = MAX(0, warnings + ?),
                    updated_at = ?
                WHERE peer_id = ? AND user_id = ?
                """,
                (delta, time.time(), peer_id, user_id),
            )
        return int(self.get_member(peer_id, user_id).get("warnings", 0))

    def clear_warnings(self, peer_id: int, user_id: int) -> None:
        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE members
                SET warnings = 0, updated_at = ?
                WHERE peer_id = ? AND user_id = ?
                """,
                (time.time(), peer_id, user_id),
            )

    def set_mute(
        self,
        peer_id: int,
        user_id: int,
        muted_until: float,
        reason: str,
    ) -> None:
        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE members
                SET muted_until = ?,
                    mute_reason = ?,
                    updated_at = ?
                WHERE peer_id = ? AND user_id = ?
                """,
                (
                    muted_until,
                    reason,
                    time.time(),
                    peer_id,
                    user_id,
                ),
            )

    def clear_mute(self, peer_id: int, user_id: int) -> None:
        self.set_mute(peer_id, user_id, 0, "")

    def clear_expired_mutes(self) -> int:
        now = time.time()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE members
                SET muted_until = 0,
                    mute_reason = '',
                    updated_at = ?
                WHERE muted_until > 0 AND muted_until <= ?
                """,
                (now, now),
            )
        return cursor.rowcount

    def list_muted(self, peer_id: int) -> list[dict[str, Any]]:
        now = time.time()
        self.clear_expired_mutes()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM members
                WHERE peer_id = ?
                  AND (muted_until = -1 OR muted_until > ?)
                ORDER BY
                    CASE WHEN muted_until = -1 THEN 1 ELSE 0 END DESC,
                    muted_until ASC
                LIMIT 100
                """,
                (peer_id, now),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_ban(
        self,
        peer_id: int,
        user_id: int,
        banned: bool,
        reason: str = "",
    ) -> None:
        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE members
                SET banned = ?,
                    ban_reason = ?,
                    muted_until = CASE WHEN ? = 0 THEN 0 ELSE muted_until END,
                    mute_reason = CASE WHEN ? = 0 THEN '' ELSE mute_reason END,
                    updated_at = ?
                WHERE peer_id = ? AND user_id = ?
                """,
                (
                    int(banned),
                    reason if banned else "",
                    int(banned),
                    int(banned),
                    time.time(),
                    peer_id,
                    user_id,
                ),
            )

    def is_banned(self, peer_id: int, user_id: int) -> bool:
        return bool(self.get_member(peer_id, user_id).get("banned"))

    def list_banned(self, peer_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM members
                WHERE peer_id = ? AND banned = 1
                ORDER BY updated_at DESC
                LIMIT 100
                """,
                (peer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def role_key(name: str) -> str:
        return " ".join(name.casefold().split())

    def create_role(
        self,
        peer_id: int,
        name: str,
        level: int,
        permissions: Iterable[str],
        created_by: int,
    ) -> None:
        key = self.role_key(name)
        now = time.time()
        permissions_json = json.dumps(
            sorted(set(permissions)),
            ensure_ascii=False,
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO roles (
                    peer_id, role_key, display_name, level,
                    permissions, created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    peer_id,
                    key,
                    name,
                    level,
                    permissions_json,
                    created_by,
                    now,
                    now,
                ),
            )

    def update_role_permissions(
        self,
        peer_id: int,
        name: str,
        permissions: Iterable[str],
    ) -> bool:
        key = self.role_key(name)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE roles
                SET permissions = ?, updated_at = ?
                WHERE peer_id = ? AND role_key = ?
                """,
                (
                    json.dumps(
                        sorted(set(permissions)),
                        ensure_ascii=False,
                    ),
                    time.time(),
                    peer_id,
                    key,
                ),
            )
        return cursor.rowcount > 0

    def delete_role(self, peer_id: int, name: str) -> bool:
        key = self.role_key(name)
        with self._lock, self._conn:
            self._conn.execute(
                """
                DELETE FROM member_roles
                WHERE peer_id = ? AND role_key = ?
                """,
                (peer_id, key),
            )
            cursor = self._conn.execute(
                """
                DELETE FROM roles
                WHERE peer_id = ? AND role_key = ?
                """,
                (peer_id, key),
            )
        return cursor.rowcount > 0

    def get_role(self, peer_id: int, name: str) -> dict[str, Any] | None:
        key = self.role_key(name)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM roles
                WHERE peer_id = ? AND role_key = ?
                """,
                (peer_id, key),
            ).fetchone()
        return dict(row) if row else None

    def list_roles(self, peer_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM roles
                WHERE peer_id = ?
                ORDER BY level DESC, display_name ASC
                """,
                (peer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def assign_role(
        self,
        peer_id: int,
        user_id: int,
        role_name: str,
        assigned_by: int,
    ) -> bool:
        role = self.get_role(peer_id, role_name)
        if role is None:
            return False

        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO member_roles (
                    peer_id, user_id, role_key, assigned_by, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(peer_id, user_id, role_key)
                DO UPDATE SET
                    assigned_by = excluded.assigned_by,
                    created_at = excluded.created_at
                """,
                (
                    peer_id,
                    user_id,
                    role["role_key"],
                    assigned_by,
                    time.time(),
                ),
            )
        return True

    def remove_role(
        self,
        peer_id: int,
        user_id: int,
        role_name: str,
    ) -> bool:
        key = self.role_key(role_name)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                DELETE FROM member_roles
                WHERE peer_id = ? AND user_id = ? AND role_key = ?
                """,
                (peer_id, user_id, key),
            )
        return cursor.rowcount > 0

    def get_member_roles(
        self,
        peer_id: int,
        user_id: int,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT r.*
                FROM member_roles mr
                JOIN roles r
                  ON r.peer_id = mr.peer_id
                 AND r.role_key = mr.role_key
                WHERE mr.peer_id = ? AND mr.user_id = ?
                ORDER BY r.level DESC, r.display_name ASC
                """,
                (peer_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_staff(self, peer_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    mr.user_id,
                    r.display_name,
                    r.level
                FROM member_roles mr
                JOIN roles r
                  ON r.peer_id = mr.peer_id
                 AND r.role_key = mr.role_key
                WHERE mr.peer_id = ?
                ORDER BY r.level DESC, mr.user_id ASC
                """,
                (peer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def log_action(
        self,
        peer_id: int,
        actor_id: int,
        target_id: int,
        action: str,
        details: str = "",
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO action_log (
                    peer_id, actor_id, target_id,
                    action, details, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    peer_id,
                    actor_id,
                    target_id,
                    action,
                    details,
                    time.time(),
                ),
            )

    def get_log(self, peer_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM action_log
                WHERE peer_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (peer_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]


db = Database(DATABASE_FILE)


# ============================================================
# VK И КЭШИ
# ============================================================

vk_session: vk_api.VkApi | None = None
vk: Any = None
BOT_GROUP_ID = 0

name_cache: dict[int, tuple[float, str]] = {}
owner_sync_cache: dict[int, float] = {}
cache_lock = threading.RLock()


def validate_config() -> None:
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

    suffix = f"\nПоследняя ошибка: {last_error}" if last_error else ""
    raise SystemExit(
        "Не удалось определить ID сообщества по токену. "
        "Используйте действующий токен сообщества."
        + suffix
    )


def send_message(
    peer_id: int,
    text: str,
    *,
    keyboard: str | None = None,
    reply_to: int | None = None,
) -> None:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "random_id": get_random_id(),
        "message": text,
        "disable_mentions": 1,
    }
    if keyboard is not None:
        params["keyboard"] = keyboard
    if reply_to:
        params["reply_to"] = reply_to
    vk.messages.send(**params)


def is_group_chat(peer_id: int) -> bool:
    return peer_id >= 2_000_000_000


def chat_id_from_peer(peer_id: int) -> int:
    return peer_id - 2_000_000_000


def get_vk_name(user_id: int) -> str:
    now = time.time()
    with cache_lock:
        cached = name_cache.get(user_id)
        if cached and cached[0] > now:
            return cached[1]

    name = f"id{user_id}"
    try:
        response = vk.users.get(user_ids=[user_id])
        if response:
            first_name = str(response[0].get("first_name", "")).strip()
            last_name = str(response[0].get("last_name", "")).strip()
            full = f"{first_name} {last_name}".strip()
            if full:
                name = full
    except Exception:
        log.debug("Не удалось получить имя user_id=%s", user_id)

    with cache_lock:
        name_cache[user_id] = (now + 3600, name)
    return name


def display_name(peer_id: int, user_id: int) -> str:
    member = db.get_member(peer_id, user_id)
    nickname = str(member.get("nickname", "")).strip()
    return nickname or get_vk_name(user_id)


def mention(peer_id: int, user_id: int) -> str:
    label = display_name(peer_id, user_id)
    label = label.replace("[", "").replace("]", "").replace("|", "")
    return f"[id{user_id}|{label}]"


def get_conversation_members(peer_id: int) -> list[dict[str, Any]]:
    response = vk.messages.getConversationMembers(
        peer_id=peer_id,
        count=1000,
        extended=0,
    )
    items = response.get("items", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def sync_native_owner(peer_id: int, *, force: bool = False) -> int:
    now = time.time()

    with cache_lock:
        last_sync = owner_sync_cache.get(peer_id, 0)

    existing = db.get_native_owner(peer_id)
    if not force and existing and now - last_sync < OWNER_SYNC_INTERVAL:
        return existing

    members = get_conversation_members(peer_id)
    owner_id = 0

    for item in members:
        if not item.get("is_owner"):
            continue
        try:
            candidate = int(item.get("member_id", 0))
        except (TypeError, ValueError):
            continue
        if candidate > 0:
            owner_id = candidate
            break

    if owner_id:
        db.set_native_owner(peer_id, owner_id)

    with cache_lock:
        owner_sync_cache[peer_id] = now

    return owner_id or existing


# ============================================================
# ПРАВА И ИЕРАРХИЯ
# ============================================================

def decode_permissions(value: str) -> set[str]:
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return set()

    if not isinstance(result, list):
        return set()

    return {
        str(item)
        for item in result
        if str(item) in ALL_PERMISSIONS
    }


def get_actor_context(peer_id: int, user_id: int) -> ActorContext:
    native_owner = db.get_native_owner(peer_id)

    if user_id == native_owner and native_owner > 0:
        return ActorContext(
            user_id=user_id,
            level=NATIVE_OWNER_LEVEL,
            permissions=frozenset(ALL_PERMISSIONS),
            kind="native_owner",
            roles=(),
        )

    if db.is_extra_owner(peer_id, user_id):
        return ActorContext(
            user_id=user_id,
            level=EXTRA_OWNER_LEVEL,
            permissions=frozenset(ALL_PERMISSIONS),
            kind="extra_owner",
            roles=(),
        )

    roles = db.get_member_roles(peer_id, user_id)
    level = max((int(role["level"]) for role in roles), default=0)

    permissions: set[str] = set()
    role_names: list[str] = []

    for role in roles:
        permissions.update(
            decode_permissions(str(role.get("permissions", "[]")))
        )
        role_names.append(str(role["display_name"]))

    return ActorContext(
        user_id=user_id,
        level=level,
        permissions=frozenset(permissions),
        kind="role" if roles else "member",
        roles=tuple(role_names),
    )


def require_permission(
    peer_id: int,
    actor_id: int,
    permission: str,
) -> ActorContext | None:
    actor = get_actor_context(peer_id, actor_id)

    if permission in actor.permissions:
        return actor

    send_message(
        peer_id,
        (
            f"⛔ Недостаточно прав.\n"
            f"Нужно разрешение: {PERMISSION_NAMES[permission]}."
        ),
    )
    return None


def require_owner(peer_id: int, actor_id: int) -> ActorContext | None:
    actor = get_actor_context(peer_id, actor_id)

    if actor.is_owner:
        return actor

    send_message(
        peer_id,
        "⛔ Эта команда доступна только владельцам беседы.",
    )
    return None


def require_native_owner(
    peer_id: int,
    actor_id: int,
) -> ActorContext | None:
    actor = get_actor_context(peer_id, actor_id)

    if actor.is_native_owner:
        return actor

    send_message(
        peer_id,
        "⛔ Эта команда доступна только создателю беседы.",
    )
    return None


def can_moderate(
    peer_id: int,
    actor: ActorContext,
    target_id: int,
) -> tuple[bool, str]:
    if target_id <= 0:
        return False, "Некорректный пользователь."

    if target_id == actor.user_id:
        return False, "Нельзя применить эту команду к себе."

    native_owner = db.get_native_owner(peer_id)

    if target_id == native_owner:
        return False, "Создателя беседы нельзя модерировать через бота."

    if actor.is_native_owner:
        return True, ""

    if db.is_extra_owner(peer_id, target_id):
        return False, "Дополнительного владельца может снять только создатель."

    target = get_actor_context(peer_id, target_id)

    if actor.level <= target.level:
        return (
            False,
            "Нельзя модерировать пользователя с равным или более высоким уровнем.",
        )

    return True, ""


def ensure_owner_available(peer_id: int) -> bool:
    try:
        owner_id = sync_native_owner(peer_id)
    except ApiError as exc:
        send_message(
            peer_id,
            (
                f"⚠ {BOT_NAME} не смог определить создателя беседы.\n"
                "Назначьте сообщество администратором конференции "
                "и повторите /setup.\n\n"
                f"Ошибка VK: {exc}"
            ),
        )
        return False
    except Exception as exc:
        log.exception("Ошибка определения владельца peer_id=%s", peer_id)
        send_message(
            peer_id,
            f"⚠ Не удалось определить создателя беседы: {exc}",
        )
        return False

    if owner_id <= 0:
        send_message(
            peer_id,
            (
                "⚠ Создатель беседы не найден. "
                "Назначьте сообщество администратором и выполните /setup."
            ),
        )
        return False

    return True


# ============================================================
# РАЗБОР КОМАНД И ПОЛЬЗОВАТЕЛЕЙ
# ============================================================

COMMAND_ALIASES = {
    "/помощь": "/help",
    "/команды": "/help",
    "/настройка": "/setup",
    "/профиль": "/profile",
    "/пред": "/warn",
    "/преды": "/warns",
    "/снятьпред": "/unwarn",
    "/очиститьпреды": "/clearwarns",
    "/мут": "/mute",
    "/размут": "/unmute",
    "/бан": "/ban",
    "/разбан": "/unban",
    "/кик": "/kick",
    "/ник": "/nick",
    "/снятьник": "/unnick",
    "/роли": "/roles",
    "/персонал": "/staff",
    "/владельцы": "/owners",
    "/настройки": "/settings",
    "/банлист": "/banlist",
    "/мутлист": "/mutelist",
    "/лог": "/audit",
}


def normalize_text(text: str) -> str:
    text = text.strip()

    # Удаляет обращение к сообществу перед командой.
    text = re.sub(
        r"^\[(?:club|public)\d+\|[^\]]+\]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def split_command(text: str) -> tuple[str, str]:
    normalized = normalize_text(text)

    if not normalized:
        return "", ""

    parts = normalized.split(maxsplit=1)
    command = parts[0].lower().split("@", maxsplit=1)[0]
    command = COMMAND_ALIASES.get(command, command)
    args = parts[1].strip() if len(parts) > 1 else ""

    return command, args


TARGET_PATTERNS = [
    re.compile(r"^\[id(\d+)\|[^\]]+\]\s*", re.IGNORECASE),
    re.compile(r"^@?id(\d+)\s*", re.IGNORECASE),
    re.compile(
        r"^(?:https?://)?(?:m\.)?vk\.(?:com|ru)/id(\d+)\s*",
        re.IGNORECASE,
    ),
    re.compile(r"^(\d+)\s*"),
]


def replied_user_id(message: dict[str, Any]) -> int:
    reply = message.get("reply_message")
    if isinstance(reply, dict):
        try:
            user_id = int(reply.get("from_id", 0))
            if user_id > 0:
                return user_id
        except (TypeError, ValueError):
            pass

    forwards = message.get("fwd_messages")
    if isinstance(forwards, list):
        for forwarded in forwards:
            if not isinstance(forwarded, dict):
                continue
            try:
                user_id = int(forwarded.get("from_id", 0))
                if user_id > 0:
                    return user_id
            except (TypeError, ValueError):
                continue

    return 0


def extract_target(
    message: dict[str, Any],
    args: str,
) -> tuple[int, str]:
    reply_target = replied_user_id(message)
    if reply_target:
        return reply_target, args.strip()

    for pattern in TARGET_PATTERNS:
        match = pattern.match(args)
        if not match:
            continue

        return int(match.group(1)), args[match.end():].strip()

    return 0, args.strip()


def parse_quoted_tokens(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:
        return []


def parse_permissions(value: str) -> tuple[set[str], set[str]]:
    items = {
        item.strip().casefold()
        for item in re.split(r"[,;\s]+", value)
        if item.strip()
    }

    if "all" in items or "все" in items:
        return set(ALL_PERMISSIONS), set()

    valid = items & ALL_PERMISSIONS
    invalid = items - ALL_PERMISSIONS

    return valid, invalid


DURATION_UNITS = {
    "s": 1,
    "с": 1,
    "sec": 1,
    "m": 60,
    "м": 60,
    "min": 60,
    "h": 3600,
    "ч": 3600,
    "d": 86400,
    "д": 86400,
    "w": 604800,
    "н": 604800,
}


def parse_duration(value: str) -> int | None:
    lowered = value.strip().casefold()

    if lowered in {
        "навсегда",
        "вечный",
        "permanent",
        "perm",
        "forever",
        "0",
    }:
        return -1

    match = re.fullmatch(r"(\d+)([a-zа-я]+)?", lowered)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2) or "m"

    multiplier = DURATION_UNITS.get(unit)
    if multiplier is None or amount <= 0:
        return None

    seconds = amount * multiplier

    # Максимум один год.
    if seconds > 365 * 86400:
        return None

    return seconds


def format_duration(seconds: int | float) -> str:
    if seconds == -1:
        return "навсегда"

    seconds = max(0, int(seconds))

    units = [
        (86400, "д."),
        (3600, "ч."),
        (60, "мин."),
        (1, "сек."),
    ]

    parts: list[str] = []

    for unit_seconds, label in units:
        if seconds < unit_seconds:
            continue
        value, seconds = divmod(seconds, unit_seconds)
        parts.append(f"{value} {label}")
        if len(parts) == 2:
            break

    return " ".join(parts) if parts else "0 сек."


def sanitize_reason(value: str) -> str:
    value = " ".join(value.strip().split())
    if not value:
        return "Причина не указана"
    return value[:MAX_REASON_LENGTH]


def parse_role_name_argument(args: str) -> str:
    tokens = parse_quoted_tokens(args)
    if not tokens:
        return ""
    return " ".join(tokens).strip()


# ============================================================
# МОДЕРАЦИОННЫЕ ДЕЙСТВИЯ
# ============================================================

def remove_user_from_chat(peer_id: int, user_id: int) -> tuple[bool, str]:
    try:
        vk.messages.removeChatUser(
            chat_id=chat_id_from_peer(peer_id),
            member_id=user_id,
        )
        return True, ""
    except ApiError as exc:
        return False, str(exc)
    except Exception as exc:
        log.exception(
            "Ошибка исключения peer_id=%s user_id=%s",
            peer_id,
            user_id,
        )
        return False, str(exc)


def delete_member_message(
    peer_id: int,
    message: dict[str, Any],
) -> bool:
    try:
        cmid = int(message.get("conversation_message_id", 0))
    except (TypeError, ValueError):
        cmid = 0

    try:
        message_id = int(message.get("id", 0))
    except (TypeError, ValueError):
        message_id = 0

    try:
        if cmid:
            vk.messages.delete(
                peer_id=peer_id,
                cmids=[cmid],
                delete_for_all=1,
            )
            return True

        if message_id:
            vk.messages.delete(
                message_ids=[message_id],
                delete_for_all=1,
            )
            return True
    except ApiError as exc:
        log.warning(
            "Не удалось удалить сообщение muted user: %s",
            exc,
        )
    except Exception:
        log.exception("Ошибка удаления сообщения muted user")

    return False


def ban_user(
    peer_id: int,
    actor_id: int,
    target_id: int,
    reason: str,
    *,
    automatic: bool = False,
) -> tuple[bool, str]:
    db.set_ban(peer_id, target_id, True, reason)
    removed, error = remove_user_from_chat(peer_id, target_id)

    action = "auto_ban" if automatic else "ban"
    db.log_action(peer_id, actor_id, target_id, action, reason)

    return removed, error


# ============================================================
# ТЕКСТЫ И КЛАВИАТУРА
# ============================================================

def welcome_keyboard() -> str:
    keyboard = VkKeyboard(one_time=False, inline=False)
    keyboard.add_button(
        "📋 Команды",
        color=VkKeyboardColor.PRIMARY,
        payload={"command": "help"},
    )
    keyboard.add_button(
        "⚙ Настройки",
        color=VkKeyboardColor.SECONDARY,
        payload={"command": "settings"},
    )
    return keyboard.get_keyboard()


def help_text() -> str:
    return f"""🤖 {BOT_NAME}
Чат-менеджер для VK бесед менеджер.

ОБЩИЕ КОМАНДЫ
/help — список команд
/setup — обновить создателя беседы
/profile [пользователь] — профиль
/roles — список ролей
/staff — владельцы и персонал
/settings — настройки беседы
/owners — владельцы
/audit — последние действия

ПРЕДУПРЕЖДЕНИЯ
/warn [пользователь] [причина]
/unwarn [пользователь]
/clearwarns [пользователь]
/warns [пользователь]

МУТЫ
/mute [пользователь] <10m|2h|1d|навсегда> [причина]
/unmute [пользователь]
/mutelist

БАНЫ И ИСКЛЮЧЕНИЕ
/ban [пользователь] [причина]
/unban [пользователь]
/banlist
/kick [пользователь] [причина]

НИКНЕЙМЫ
/nick [пользователь] <никнейм>
/unnick [пользователь]

РОЛИ
/rolecreate "Название" <уровень> [права]
/roleperm "Название" <права>
/roledelete "Название"
/giverole [пользователь] "Название"
/takerole [пользователь] "Название"

Права ролей:
warn, mute, ban, kick, nick, roles

ВЛАДЕЛЬЦЫ И НАСТРОЙКИ
/addowner [пользователь] — только создатель
/delowner [пользователь] — только создатель
/setwarnlimit <число>

Пользователя можно указать ссылкой, ID или упоминанием.
Удобнее ответить на его сообщение командой:
Ответ → /mute 10m флуд"""


def permission_list_text(permissions: Iterable[str]) -> str:
    values = list(permissions)
    if not values:
        return "нет"
    return ", ".join(
        PERMISSION_NAMES.get(permission, permission)
        for permission in sorted(values)
    )


def action_name(value: str) -> str:
    names = {
        "warn": "предупреждение",
        "unwarn": "снятие предупреждения",
        "clearwarns": "очистка предупреждений",
        "mute": "мут",
        "unmute": "снятие мута",
        "ban": "бан",
        "auto_ban": "автоматический бан",
        "unban": "снятие бана",
        "kick": "исключение",
        "nick": "изменение никнейма",
        "unnick": "снятие никнейма",
        "addowner": "добавление владельца",
        "delowner": "снятие владельца",
        "giverole": "выдача роли",
        "takerole": "снятие роли",
        "rolecreate": "создание роли",
        "roledelete": "удаление роли",
        "roleperm": "изменение прав роли",
        "setwarnlimit": "изменение лимита предупреждений",
    }
    return names.get(value, value)


# ============================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================

def command_setup(peer_id: int, actor_id: int) -> None:
    try:
        owner_id = sync_native_owner(peer_id, force=True)
    except Exception as exc:
        send_message(
            peer_id,
            (
                "⚠ Не удалось получить участников беседы.\n"
                "Сделайте сообщество администратором конференции.\n\n"
                f"Ошибка: {exc}"
            ),
        )
        return

    if owner_id <= 0:
        send_message(
            peer_id,
            "⚠ Создатель беседы не найден.",
        )
        return

    send_message(
        peer_id,
        (
            f"✅ {BOT_NAME} настроен.\n"
            f"Создатель беседы: {mention(peer_id, owner_id)}\n"
            f"Лимит предупреждений: "
            f"{db.get_chat(peer_id)['warn_limit']}\n\n"
            "Для полного функционала сообщество должно оставаться "
            "администратором конференции."
        ),
        keyboard=welcome_keyboard(),
    )


def command_profile(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    target_id, _ = extract_target(message, args)
    if not target_id:
        target_id = actor_id

    member = db.get_member(peer_id, target_id)
    context = get_actor_context(peer_id, target_id)

    muted_until = float(member.get("muted_until", 0))
    if muted_until == -1:
        mute_status = "навсегда"
    elif muted_until > time.time():
        mute_status = format_duration(muted_until - time.time())
    else:
        mute_status = "нет"

    if context.kind == "native_owner":
        status = "Создатель беседы"
    elif context.kind == "extra_owner":
        status = "Дополнительный владелец"
    elif context.roles:
        status = ", ".join(context.roles)
    else:
        status = "Участник"

    send_message(
        peer_id,
        (
            f"👤 Профиль {mention(peer_id, target_id)}\n"
            f"ID: {target_id}\n"
            f"Статус: {status}\n"
            f"Уровень: {context.level}\n"
            f"Предупреждения: {int(member.get('warnings', 0))}\n"
            f"Мут: {mute_status}\n"
            f"Бан: {'да' if member.get('banned') else 'нет'}\n"
            f"Права: {permission_list_text(context.permissions)}"
        ),
    )


def command_warn(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "warn")
    if actor is None:
        return

    target_id, remaining = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Использование: /warn @пользователь причина\n"
            "Или ответьте на сообщение: /warn причина",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    reason = sanitize_reason(remaining)
    warnings = db.change_warnings(peer_id, target_id, 1)
    limit = int(db.get_chat(peer_id)["warn_limit"])

    db.log_action(peer_id, actor_id, target_id, "warn", reason)

    if warnings >= limit:
        ban_reason = (
            f"Достигнут лимит предупреждений: {warnings}/{limit}. "
            f"Последняя причина: {reason}"
        )
        removed, api_error = ban_user(
            peer_id,
            actor_id,
            target_id,
            ban_reason,
            automatic=True,
        )

        suffix = (
            ""
            if removed
            else f"\nVK не исключил участника: {api_error}"
        )

        send_message(
            peer_id,
            (
                f"🚫 {mention(peer_id, target_id)} автоматически заблокирован.\n"
                f"Предупреждения: {warnings}/{limit}\n"
                f"Причина: {reason}"
                f"{suffix}"
            ),
        )
        return

    send_message(
        peer_id,
        (
            f"⚠ {mention(peer_id, target_id)} получил предупреждение.\n"
            f"Предупреждения: {warnings}/{limit}\n"
            f"Причина: {reason}\n"
            f"Модератор: {mention(peer_id, actor_id)}"
        ),
    )


def command_unwarn(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
    *,
    clear_all: bool = False,
) -> None:
    actor = require_permission(peer_id, actor_id, "warn")
    if actor is None:
        return

    target_id, _ = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Укажите пользователя или ответьте на его сообщение.",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    if clear_all:
        db.clear_warnings(peer_id, target_id)
        warnings = 0
        action = "clearwarns"
        text = "Все предупреждения сняты"
    else:
        warnings = db.change_warnings(peer_id, target_id, -1)
        action = "unwarn"
        text = "Одно предупреждение снято"

    db.log_action(peer_id, actor_id, target_id, action)

    send_message(
        peer_id,
        (
            f"✅ {text} у {mention(peer_id, target_id)}.\n"
            f"Осталось предупреждений: {warnings}."
        ),
    )


def command_warns(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    target_id, _ = extract_target(message, args)
    if not target_id:
        target_id = actor_id

    warnings = int(
        db.get_member(peer_id, target_id).get("warnings", 0)
    )
    limit = int(db.get_chat(peer_id)["warn_limit"])

    send_message(
        peer_id,
        (
            f"⚠ Предупреждения {mention(peer_id, target_id)}: "
            f"{warnings}/{limit}."
        ),
    )


def command_mute(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "mute")
    if actor is None:
        return

    target_id, remaining = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Использование: /mute @пользователь 10m причина\n"
            "Или ответьте: /mute 10m причина",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    parts = remaining.split(maxsplit=1)
    if not parts:
        send_message(
            peer_id,
            "Укажите срок: 10m, 2h, 1d или навсегда.",
        )
        return

    duration_seconds = parse_duration(parts[0])
    if duration_seconds is None:
        send_message(
            peer_id,
            "Некорректный срок. Примеры: 30m, 2h, 1d, навсегда.",
        )
        return

    reason = sanitize_reason(parts[1] if len(parts) > 1 else "")
    muted_until = (
        -1
        if duration_seconds == -1
        else time.time() + duration_seconds
    )

    db.set_mute(
        peer_id,
        target_id,
        muted_until,
        reason,
    )
    db.log_action(
        peer_id,
        actor_id,
        target_id,
        "mute",
        f"{format_duration(duration_seconds)} | {reason}",
    )

    send_message(
        peer_id,
        (
            f"🔇 {mention(peer_id, target_id)} получил мут "
            f"{format_duration(duration_seconds)}.\n"
            f"Причина: {reason}\n"
            f"Модератор: {mention(peer_id, actor_id)}"
        ),
    )


def command_unmute(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "mute")
    if actor is None:
        return

    target_id, _ = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Укажите пользователя или ответьте на его сообщение.",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    db.clear_mute(peer_id, target_id)
    db.log_action(peer_id, actor_id, target_id, "unmute")

    send_message(
        peer_id,
        f"🔊 Мут снят с {mention(peer_id, target_id)}.",
    )


def command_ban(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "ban")
    if actor is None:
        return

    target_id, remaining = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Использование: /ban @пользователь причина\n"
            "Или ответьте: /ban причина",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    reason = sanitize_reason(remaining)
    removed, api_error = ban_user(
        peer_id,
        actor_id,
        target_id,
        reason,
    )

    suffix = (
        "\nПользователь исключён из беседы."
        if removed
        else (
            "\nБан сохранён, но VK не исключил пользователя: "
            f"{api_error}"
        )
    )

    send_message(
        peer_id,
        (
            f"🚫 {mention(peer_id, target_id)} заблокирован.\n"
            f"Причина: {reason}\n"
            f"Модератор: {mention(peer_id, actor_id)}"
            f"{suffix}"
        ),
    )


def command_unban(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "ban")
    if actor is None:
        return

    target_id, _ = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Использование: /unban ID_пользователя",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    db.set_ban(peer_id, target_id, False)
    db.log_action(peer_id, actor_id, target_id, "unban")

    send_message(
        peer_id,
        (
            f"✅ Бан снят с {mention(peer_id, target_id)}.\n"
            "Вернуть исключённого участника в беседу нужно вручную."
        ),
    )


def command_kick(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "kick")
    if actor is None:
        return

    target_id, remaining = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Использование: /kick @пользователь причина",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    reason = sanitize_reason(remaining)
    removed, api_error = remove_user_from_chat(peer_id, target_id)

    if not removed:
        send_message(
            peer_id,
            f"⚠ VK не исключил пользователя: {api_error}",
        )
        return

    db.log_action(peer_id, actor_id, target_id, "kick", reason)

    send_message(
        peer_id,
        (
            f"👢 {mention(peer_id, target_id)} исключён.\n"
            f"Причина: {reason}\n"
            f"Модератор: {mention(peer_id, actor_id)}"
        ),
    )


def command_nick(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "nick")
    if actor is None:
        return

    target_id, remaining = extract_target(message, args)
    if not target_id or not remaining:
        send_message(
            peer_id,
            "Использование: /nick @пользователь Новый ник\n"
            "Или ответьте: /nick Новый ник",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    nickname = " ".join(remaining.split())
    nickname = (
        nickname.replace("[", "")
        .replace("]", "")
        .replace("|", "")
        .strip()
    )

    if not nickname or len(nickname) > MAX_NICKNAME_LENGTH:
        send_message(
            peer_id,
            f"Никнейм должен содержать 1–{MAX_NICKNAME_LENGTH} символов.",
        )
        return

    db.set_nickname(peer_id, target_id, nickname)
    db.log_action(peer_id, actor_id, target_id, "nick", nickname)

    send_message(
        peer_id,
        (
            f"🏷 Пользователю [id{target_id}|{nickname}] "
            f"установлен никнейм «{nickname}»."
        ),
    )


def command_unnick(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "nick")
    if actor is None:
        return

    target_id, _ = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Укажите пользователя или ответьте на его сообщение.",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    old_name = display_name(peer_id, target_id)
    db.set_nickname(peer_id, target_id, "")
    db.log_action(peer_id, actor_id, target_id, "unnick")

    send_message(
        peer_id,
        (
            f"🏷 Никнейм «{old_name}» снят с "
            f"{mention(peer_id, target_id)}."
        ),
    )


def command_addowner(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    if require_native_owner(peer_id, actor_id) is None:
        return

    target_id, _ = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Использование: /addowner @пользователь",
        )
        return

    if target_id == actor_id:
        send_message(peer_id, "Вы уже являетесь создателем беседы.")
        return

    db.add_extra_owner(peer_id, target_id, actor_id)
    db.log_action(peer_id, actor_id, target_id, "addowner")

    send_message(
        peer_id,
        (
            f"👑 {mention(peer_id, target_id)} назначен "
            "дополнительным владельцем."
        ),
    )


def command_delowner(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    if require_native_owner(peer_id, actor_id) is None:
        return

    target_id, _ = extract_target(message, args)
    if not target_id:
        send_message(
            peer_id,
            "Использование: /delowner @пользователь",
        )
        return

    removed = db.remove_extra_owner(peer_id, target_id)
    if not removed:
        send_message(
            peer_id,
            "Этот пользователь не является дополнительным владельцем.",
        )
        return

    db.log_action(peer_id, actor_id, target_id, "delowner")

    send_message(
        peer_id,
        (
            f"👑 Права дополнительного владельца сняты с "
            f"{mention(peer_id, target_id)}."
        ),
    )


def command_rolecreate(
    peer_id: int,
    actor_id: int,
    args: str,
) -> None:
    if require_owner(peer_id, actor_id) is None:
        return

    tokens = parse_quoted_tokens(args)

    if len(tokens) < 2:
        send_message(
            peer_id,
            (
                'Использование:\n'
                '/rolecreate "Модератор" 50 warn,mute,nick'
            ),
        )
        return

    name = tokens[0].strip()

    try:
        level = int(tokens[1])
    except ValueError:
        send_message(peer_id, "Уровень роли должен быть числом.")
        return

    if not name or len(name) > MAX_ROLE_NAME_LENGTH:
        send_message(
            peer_id,
            f"Название роли: 1–{MAX_ROLE_NAME_LENGTH} символов.",
        )
        return

    if not 1 <= level <= MAX_ROLE_LEVEL:
        send_message(
            peer_id,
            f"Уровень роли должен быть от 1 до {MAX_ROLE_LEVEL}.",
        )
        return

    permissions_value = (
        tokens[2]
        if len(tokens) >= 3
        else "warn,mute,nick"
    )
    permissions, invalid = parse_permissions(permissions_value)

    if invalid:
        send_message(
            peer_id,
            "Неизвестные права: " + ", ".join(sorted(invalid)),
        )
        return

    if db.get_role(peer_id, name):
        send_message(peer_id, "Роль с таким названием уже существует.")
        return

    db.create_role(
        peer_id,
        name,
        level,
        permissions,
        actor_id,
    )
    db.log_action(
        peer_id,
        actor_id,
        0,
        "rolecreate",
        f"{name} | {level} | {','.join(sorted(permissions))}",
    )

    send_message(
        peer_id,
        (
            f"✅ Роль «{name}» создана.\n"
            f"Уровень: {level}\n"
            f"Права: {permission_list_text(permissions)}"
        ),
    )


def command_roleperm(
    peer_id: int,
    actor_id: int,
    args: str,
) -> None:
    if require_owner(peer_id, actor_id) is None:
        return

    tokens = parse_quoted_tokens(args)
    if len(tokens) < 2:
        send_message(
            peer_id,
            (
                'Использование:\n'
                '/roleperm "Модератор" warn,mute,ban,nick'
            ),
        )
        return

    name = tokens[0]
    permissions, invalid = parse_permissions(tokens[1])

    if invalid:
        send_message(
            peer_id,
            "Неизвестные права: " + ", ".join(sorted(invalid)),
        )
        return

    if not db.update_role_permissions(peer_id, name, permissions):
        send_message(peer_id, "Роль не найдена.")
        return

    db.log_action(
        peer_id,
        actor_id,
        0,
        "roleperm",
        f"{name} | {','.join(sorted(permissions))}",
    )

    send_message(
        peer_id,
        (
            f"✅ Права роли «{name}» обновлены.\n"
            f"Права: {permission_list_text(permissions)}"
        ),
    )


def command_roledelete(
    peer_id: int,
    actor_id: int,
    args: str,
) -> None:
    if require_owner(peer_id, actor_id) is None:
        return

    name = parse_role_name_argument(args)
    if not name:
        send_message(
            peer_id,
            'Использование: /roledelete "Название роли"',
        )
        return

    if not db.delete_role(peer_id, name):
        send_message(peer_id, "Роль не найдена.")
        return

    db.log_action(peer_id, actor_id, 0, "roledelete", name)

    send_message(
        peer_id,
        f"🗑 Роль «{name}» удалена у всех пользователей.",
    )


def command_giverole(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "roles")
    if actor is None:
        return

    target_id, remaining = extract_target(message, args)
    role_name = parse_role_name_argument(remaining)

    if not target_id or not role_name:
        send_message(
            peer_id,
            (
                'Использование: /giverole @пользователь "Модератор"\n'
                'Или ответьте: /giverole "Модератор"'
            ),
        )
        return

    role = db.get_role(peer_id, role_name)
    if role is None:
        send_message(peer_id, "Роль не найдена.")
        return

    role_level = int(role["level"])

    if not actor.is_owner and role_level >= actor.level:
        send_message(
            peer_id,
            "Нельзя выдавать роль своего или более высокого уровня.",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    db.assign_role(peer_id, target_id, role_name, actor_id)
    db.log_action(
        peer_id,
        actor_id,
        target_id,
        "giverole",
        str(role["display_name"]),
    )

    send_message(
        peer_id,
        (
            f"🎖 {mention(peer_id, target_id)} получил роль "
            f"«{role['display_name']}»."
        ),
    )


def command_takerole(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
    args: str,
) -> None:
    actor = require_permission(peer_id, actor_id, "roles")
    if actor is None:
        return

    target_id, remaining = extract_target(message, args)
    role_name = parse_role_name_argument(remaining)

    if not target_id or not role_name:
        send_message(
            peer_id,
            (
                'Использование: /takerole @пользователь "Модератор"\n'
                'Или ответьте: /takerole "Модератор"'
            ),
        )
        return

    role = db.get_role(peer_id, role_name)
    if role is None:
        send_message(peer_id, "Роль не найдена.")
        return

    if not actor.is_owner and int(role["level"]) >= actor.level:
        send_message(
            peer_id,
            "Нельзя снимать роль своего или более высокого уровня.",
        )
        return

    allowed, error = can_moderate(peer_id, actor, target_id)
    if not allowed:
        send_message(peer_id, f"⛔ {error}")
        return

    if not db.remove_role(peer_id, target_id, role_name):
        send_message(
            peer_id,
            "У пользователя нет этой роли.",
        )
        return

    db.log_action(
        peer_id,
        actor_id,
        target_id,
        "takerole",
        str(role["display_name"]),
    )

    send_message(
        peer_id,
        (
            f"🎖 Роль «{role['display_name']}» снята с "
            f"{mention(peer_id, target_id)}."
        ),
    )


def command_roles(peer_id: int) -> None:
    roles = db.list_roles(peer_id)

    if not roles:
        send_message(
            peer_id,
            (
                "В этой беседе роли ещё не созданы.\n"
                'Пример: /rolecreate "Модератор" 50 warn,mute,nick'
            ),
        )
        return

    lines = ["🎖 Роли беседы:"]

    for role in roles:
        permissions = decode_permissions(str(role["permissions"]))
        lines.append(
            f"• {role['display_name']} — уровень {role['level']}; "
            f"права: {permission_list_text(permissions)}"
        )

    send_message(peer_id, "\n".join(lines))


def command_staff(peer_id: int) -> None:
    native_owner = db.get_native_owner(peer_id)
    extra_owners = db.list_extra_owners(peer_id)
    staff_rows = db.list_staff(peer_id)

    lines = ["👥 Персонал беседы:"]

    if native_owner:
        lines.append(
            f"• Создатель: {mention(peer_id, native_owner)}"
        )

    for user_id in extra_owners:
        lines.append(
            f"• Доп. владелец: {mention(peer_id, user_id)}"
        )

    grouped: dict[int, list[str]] = {}
    levels: dict[int, int] = {}

    for row in staff_rows:
        user_id = int(row["user_id"])
        grouped.setdefault(user_id, []).append(
            str(row["display_name"])
        )
        levels[user_id] = max(
            levels.get(user_id, 0),
            int(row["level"]),
        )

    for user_id in sorted(
        grouped,
        key=lambda value: (-levels[value], value),
    ):
        lines.append(
            f"• {mention(peer_id, user_id)} — "
            f"{', '.join(grouped[user_id])}"
        )

    if len(lines) == 1:
        lines.append("Персонал не назначен.")

    send_message(peer_id, "\n".join(lines))


def command_owners(peer_id: int) -> None:
    native_owner = db.get_native_owner(peer_id)
    extra_owners = db.list_extra_owners(peer_id)

    lines = ["👑 Владельцы беседы:"]

    if native_owner:
        lines.append(
            f"• Создатель: {mention(peer_id, native_owner)}"
        )
    else:
        lines.append("• Создатель: не определён")

    for user_id in extra_owners:
        lines.append(
            f"• Дополнительный: {mention(peer_id, user_id)}"
        )

    send_message(peer_id, "\n".join(lines))


def command_settings(peer_id: int) -> None:
    chat = db.get_chat(peer_id)
    native_owner = int(chat.get("native_owner_id", 0))

    send_message(
        peer_id,
        (
            f"⚙ Настройки {BOT_NAME}\n"
            f"Версия: {BOT_VERSION}\n"
            f"Создатель: "
            f"{mention(peer_id, native_owner) if native_owner else 'не определён'}\n"
            f"Доп. владельцев: {len(db.list_extra_owners(peer_id))}\n"
            f"Ролей: {len(db.list_roles(peer_id))}\n"
            f"Лимит предупреждений: {chat.get('warn_limit', DEFAULT_WARN_LIMIT)}\n"
            f"Активных мутов: {len(db.list_muted(peer_id))}\n"
            f"Активных банов: {len(db.list_banned(peer_id))}"
        ),
    )


def command_setwarnlimit(
    peer_id: int,
    actor_id: int,
    args: str,
) -> None:
    if require_owner(peer_id, actor_id) is None:
        return

    try:
        limit = int(args.strip())
    except ValueError:
        send_message(
            peer_id,
            "Использование: /setwarnlimit 3",
        )
        return

    if not 1 <= limit <= 20:
        send_message(
            peer_id,
            "Лимит должен быть от 1 до 20.",
        )
        return

    db.set_warn_limit(peer_id, limit)
    db.log_action(
        peer_id,
        actor_id,
        0,
        "setwarnlimit",
        str(limit),
    )

    send_message(
        peer_id,
        f"✅ Лимит предупреждений установлен: {limit}.",
    )


def command_banlist(peer_id: int) -> None:
    rows = db.list_banned(peer_id)

    if not rows:
        send_message(peer_id, "🚫 Список банов пуст.")
        return

    lines = ["🚫 Заблокированные пользователи:"]

    for row in rows[:50]:
        user_id = int(row["user_id"])
        reason = str(row.get("ban_reason", "")).strip()
        lines.append(
            f"• {mention(peer_id, user_id)} — "
            f"{reason or 'без причины'}"
        )

    if len(rows) > 50:
        lines.append(f"И ещё: {len(rows) - 50}")

    send_message(peer_id, "\n".join(lines))


def command_mutelist(peer_id: int) -> None:
    rows = db.list_muted(peer_id)

    if not rows:
        send_message(peer_id, "🔇 Активных мутов нет.")
        return

    now = time.time()
    lines = ["🔇 Замьюченные пользователи:"]

    for row in rows[:50]:
        user_id = int(row["user_id"])
        muted_until = float(row["muted_until"])
        duration = (
            "навсегда"
            if muted_until == -1
            else format_duration(muted_until - now)
        )
        reason = str(row.get("mute_reason", "")).strip()
        lines.append(
            f"• {mention(peer_id, user_id)} — {duration}; "
            f"{reason or 'без причины'}"
        )

    if len(rows) > 50:
        lines.append(f"И ещё: {len(rows) - 50}")

    send_message(peer_id, "\n".join(lines))


def command_audit(peer_id: int, actor_id: int) -> None:
    actor = get_actor_context(peer_id, actor_id)

    if actor.level <= 0:
        send_message(
            peer_id,
            "⛔ Журнал доступен только персоналу.",
        )
        return

    rows = db.get_log(peer_id, 15)
    if not rows:
        send_message(peer_id, "📜 Журнал действий пуст.")
        return

    lines = ["📜 Последние действия:"]

    for row in rows:
        timestamp = time.strftime(
            "%d.%m %H:%M",
            time.localtime(float(row["created_at"])),
        )
        actor_mention = mention(peer_id, int(row["actor_id"]))
        target_id = int(row["target_id"])
        target = (
            mention(peer_id, target_id)
            if target_id > 0
            else "система"
        )
        details = str(row.get("details", "")).strip()
        details_text = f" — {details[:120]}" if details else ""

        lines.append(
            f"• {timestamp}: {actor_mention} → {target}: "
            f"{action_name(str(row['action']))}{details_text}"
        )

    send_message(peer_id, "\n".join(lines))


def handle_command(
    peer_id: int,
    actor_id: int,
    message: dict[str, Any],
) -> None:
    command, args = split_command(str(message.get("text", "")))

    # Кнопки отправляют обычный текст, но payload также поддерживается.
    payload = message.get("payload")
    if payload:
        try:
            parsed_payload = json.loads(payload)
            payload_command = parsed_payload.get("command")
            if payload_command == "help":
                command = "/help"
            elif payload_command == "settings":
                command = "/settings"
        except (TypeError, json.JSONDecodeError):
            pass

    if not command.startswith("/"):
        return

    if command == "/help":
        send_message(
            peer_id,
            help_text(),
            keyboard=welcome_keyboard(),
        )
        return

    if command == "/about":
        send_message(
            peer_id,
            (
                f"🤖 {BOT_NAME} v{BOT_VERSION}\n"
                "VK чат-менеджер: предупреждения, муты, баны, "
                "никнеймы, роли и иерархия владельцев."
            ),
        )
        return

    if command == "/setup":
        command_setup(peer_id, actor_id)
        return

    # Для всех команд ниже нужен автоматически определённый создатель.
    if not ensure_owner_available(peer_id):
        return

    if command == "/profile":
        command_profile(peer_id, actor_id, message, args)
    elif command == "/warn":
        command_warn(peer_id, actor_id, message, args)
    elif command == "/unwarn":
        command_unwarn(
            peer_id,
            actor_id,
            message,
            args,
            clear_all=False,
        )
    elif command == "/clearwarns":
        command_unwarn(
            peer_id,
            actor_id,
            message,
            args,
            clear_all=True,
        )
    elif command == "/warns":
        command_warns(peer_id, actor_id, message, args)
    elif command == "/mute":
        command_mute(peer_id, actor_id, message, args)
    elif command == "/unmute":
        command_unmute(peer_id, actor_id, message, args)
    elif command == "/ban":
        command_ban(peer_id, actor_id, message, args)
    elif command == "/unban":
        command_unban(peer_id, actor_id, message, args)
    elif command == "/kick":
        command_kick(peer_id, actor_id, message, args)
    elif command == "/nick":
        command_nick(peer_id, actor_id, message, args)
    elif command == "/unnick":
        command_unnick(peer_id, actor_id, message, args)
    elif command == "/addowner":
        command_addowner(peer_id, actor_id, message, args)
    elif command == "/delowner":
        command_delowner(peer_id, actor_id, message, args)
    elif command == "/rolecreate":
        command_rolecreate(peer_id, actor_id, args)
    elif command == "/roleperm":
        command_roleperm(peer_id, actor_id, args)
    elif command == "/roledelete":
        command_roledelete(peer_id, actor_id, args)
    elif command == "/giverole":
        command_giverole(peer_id, actor_id, message, args)
    elif command == "/takerole":
        command_takerole(peer_id, actor_id, message, args)
    elif command == "/roles":
        command_roles(peer_id)
    elif command == "/staff":
        command_staff(peer_id)
    elif command == "/owners":
        command_owners(peer_id)
    elif command == "/settings":
        command_settings(peer_id)
    elif command == "/setwarnlimit":
        command_setwarnlimit(peer_id, actor_id, args)
    elif command == "/banlist":
        command_banlist(peer_id)
    elif command == "/mutelist":
        command_mutelist(peer_id)
    elif command == "/audit":
        command_audit(peer_id, actor_id)


# ============================================================
# СОБЫТИЯ БЕСЕДЫ
# ============================================================

def invited_member_id(action: dict[str, Any]) -> int:
    try:
        return int(action.get("member_id", 0))
    except (TypeError, ValueError):
        return 0


def handle_chat_action(
    peer_id: int,
    actor_id: int,
    action: dict[str, Any],
) -> None:
    action_type = str(action.get("type", ""))
    member_id = invited_member_id(action)

    if action_type not in {
        "chat_invite_user",
        "chat_invite_user_by_link",
    }:
        return

    # Бота пригласили в конференцию.
    if member_id == -BOT_GROUP_ID:
        db.ensure_chat(peer_id)

        try:
            owner_id = sync_native_owner(peer_id, force=True)
            owner_line = (
                f"\nСоздатель определён: {mention(peer_id, owner_id)}"
                if owner_id
                else ""
            )
        except Exception:
            owner_line = (
                "\nСоздатель пока не определён. "
                "Назначьте сообщество администратором и выполните /setup."
            )

        send_message(
            peer_id,
            (
                f"👋 {BOT_NAME} подключён к беседе.\n"
                "Я управляю предупреждениями, мутами, банами, "
                "никнеймами и ролями."
                f"{owner_line}\n\n"
                "Откройте /help для списка команд."
            ),
            keyboard=welcome_keyboard(),
        )
        return

    # Заблокированного пользователя вернули в беседу.
    if member_id > 0 and db.is_banned(peer_id, member_id):
        reason = str(
            db.get_member(peer_id, member_id).get("ban_reason", "")
        ).strip()

        removed, error = remove_user_from_chat(peer_id, member_id)

        if removed:
            send_message(
                peer_id,
                (
                    f"🚫 {mention(peer_id, member_id)} находится в бан-листе "
                    "и был повторно исключён.\n"
                    f"Причина: {reason or 'не указана'}"
                ),
            )
        else:
            log.warning(
                "Не удалось повторно исключить banned user %s: %s",
                member_id,
                error,
            )


def enforce_member_state(
    peer_id: int,
    user_id: int,
    message: dict[str, Any],
) -> bool:
    """
    Возвращает True, если дальнейшая обработка сообщения запрещена.
    """
    member = db.get_member(peer_id, user_id)

    if member.get("banned"):
        remove_user_from_chat(peer_id, user_id)
        return True

    muted_until = float(member.get("muted_until", 0))
    now = time.time()

    if muted_until > 0 and muted_until <= now:
        db.clear_mute(peer_id, user_id)
        return False

    if muted_until == -1 or muted_until > now:
        delete_member_message(peer_id, message)
        return True

    return False


def cleanup_worker() -> None:
    while True:
        try:
            cleared = db.clear_expired_mutes()
            if cleared:
                log.info("Снято истёкших мутов: %s", cleared)
        except Exception:
            log.exception("Ошибка фоновой очистки мутов")

        time.sleep(MUTE_CLEANUP_INTERVAL)


# ============================================================
# ЗАПУСК
# ============================================================

def main() -> None:
    global vk_session, vk, BOT_GROUP_ID

    validate_config()

    vk_session = vk_api.VkApi(
        token=VK_TOKEN,
        api_version=API_VERSION,
    )
    vk = vk_session.get_api()

    BOT_GROUP_ID = detect_group_id()
    longpoll = VkBotLongPoll(vk_session, BOT_GROUP_ID)

    threading.Thread(
        target=cleanup_worker,
        name="mute-cleanup",
        daemon=True,
    ).start()

    log.info("%s v%s запущен", BOT_NAME, BOT_VERSION)
    log.info("ID сообщества: %s", BOT_GROUP_ID)
    log.info("База: %s", DATABASE_FILE)

    while True:
        try:
            for event in longpoll.listen():
                if event.type != VkBotEventType.MESSAGE_NEW:
                    continue

                message = event.object.message

                try:
                    peer_id = int(message.get("peer_id", 0))
                    actor_id = int(message.get("from_id", 0))
                except (TypeError, ValueError):
                    continue

                if not is_group_chat(peer_id):
                    continue

                db.ensure_chat(peer_id)

                action = message.get("action")
                if isinstance(action, dict) and action:
                    handle_chat_action(peer_id, actor_id, action)
                    continue

                # Сообщения сообществ и самого бота не обрабатываются.
                if actor_id <= 0:
                    continue

                # Ленивая синхронизация создателя.
                try:
                    sync_native_owner(peer_id)
                except Exception:
                    pass

                if enforce_member_state(peer_id, actor_id, message):
                    continue

                handle_command(peer_id, actor_id, message)

        except KeyboardInterrupt:
            log.info("%s остановлен", BOT_NAME)
            return
        except Exception:
            log.exception(
                "Bots Long Poll отключился. Повтор через 5 секунд."
            )
            time.sleep(5)


if __name__ == "__main__":
    main()
