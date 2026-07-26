#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GRAND: Чат менеджер ULTRA — расширенная самостоятельная реализация.

Один Python-файл, SQLite, автоопределение владельца беседы,
модерация, роли, защита чата, правила, приветствия, заметки,
статистика и очистка сообщений.

Это самостоятельная реализация функционального аналога чат-менеджера.
Она не содержит исходного кода сторонних сервисов.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================

BOT_NAME = "GRAND: Чат менеджер ULTRA"
VK_TOKEN = os.getenv("VK_TOKEN", "vk1.a.Q5lY1ONk5mEYbxHqnd16Gw8-owKxJWgB-x5sVNNOqRrL8RxUpwwoKdll6RwhZhhekEGu6rtMOL4A8N0uKJ5ajt1gkdnEuciK6RyFh41vIRZK6tG4cmcDRJnj36DuUyyh6Db6n1kbCdliOBm8IyC_nPjPDKN9-f3ip2f71GFB44MzXy9xN8KEYIRIbIyogBPYFKtC0gg1BzsqJN2YKt0JQg")
API_VERSION = "5.199"
VK_API_PACKAGE = "vk-api>=11.9.9,<12"
DATABASE_FILE = Path(__file__).with_name("grand_manager_pro.db")

DEFAULT_WARN_LIMIT = 3
DEFAULT_SPAM_COUNT = 6
DEFAULT_SPAM_WINDOW = 8
DEFAULT_CAPS_LIMIT = 75
DEFAULT_GUARD_MUTE_SECONDS = 600

MAX_REASON_LENGTH = 500
MAX_NICKNAME_LENGTH = 40
MAX_ROLE_NAME_LENGTH = 40
MAX_ROLE_LEVEL = 999
MAX_NOTE_NAME_LENGTH = 40
MAX_NOTE_TEXT_LENGTH = 3000
MAX_RULES_LENGTH = 5000
MAX_WELCOME_LENGTH = 3000
MAX_CLEAR_MESSAGES = 100
MESSAGE_LOG_LIMIT = 1000

NATIVE_OWNER_LEVEL = 10000
EXTRA_OWNER_LEVEL = 9000
OWNER_SYNC_INTERVAL = 300
CLEANUP_INTERVAL = 30

ALL_PERMISSIONS = {
    "warn", "mute", "ban", "kick", "nick", "roles", "clear",
    "guard", "settings", "rules", "notes", "speak",
}

PERMISSION_NAMES = {
    "warn": "предупреждения",
    "mute": "муты",
    "ban": "баны",
    "kick": "исключение",
    "nick": "никнеймы",
    "roles": "роли",
    "clear": "очистка сообщений",
    "guard": "защита чата",
    "settings": "настройки",
    "rules": "правила и приветствия",
    "notes": "заметки",
    "speak": "сообщения от имени бота",
}


# ============================================================
# АВТОУСТАНОВКА
# ============================================================

def ensure_vk_api() -> None:
    try:
        import vk_api  # noqa: F401
    except ImportError:
        print(f"[{BOT_NAME}] Устанавливаю {VK_API_PACKAGE}...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", VK_API_PACKAGE]
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                "Не удалось установить vk-api автоматически.\n"
                f"Выполните: {sys.executable} -m pip install {VK_API_PACKAGE}"
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
log = logging.getLogger("grand-manager-pro")


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
    CHAT_FIELDS = {
        "native_owner_id", "warn_limit", "rules_text",
        "welcome_text", "welcome_enabled", "goodbye_text",
        "goodbye_enabled", "locked", "slowmode_seconds",
        "antispam_enabled", "spam_count", "spam_window",
        "antilink_enabled", "link_action", "anticaps_enabled",
        "caps_limit", "antimat_enabled", "mat_action",
    }

    def __init__(self, path: Path) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, timeout=30
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._prepare()

    def _prepare(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS chats (
                    peer_id INTEGER PRIMARY KEY,
                    native_owner_id INTEGER NOT NULL DEFAULT 0,
                    warn_limit INTEGER NOT NULL DEFAULT {DEFAULT_WARN_LIMIT},
                    rules_text TEXT NOT NULL DEFAULT '',
                    welcome_text TEXT NOT NULL DEFAULT 'Добро пожаловать, {{user}}!',
                    welcome_enabled INTEGER NOT NULL DEFAULT 0,
                    goodbye_text TEXT NOT NULL DEFAULT '{{user}} покинул беседу.',
                    goodbye_enabled INTEGER NOT NULL DEFAULT 0,
                    locked INTEGER NOT NULL DEFAULT 0,
                    slowmode_seconds INTEGER NOT NULL DEFAULT 0,
                    antispam_enabled INTEGER NOT NULL DEFAULT 0,
                    spam_count INTEGER NOT NULL DEFAULT {DEFAULT_SPAM_COUNT},
                    spam_window INTEGER NOT NULL DEFAULT {DEFAULT_SPAM_WINDOW},
                    antilink_enabled INTEGER NOT NULL DEFAULT 0,
                    link_action TEXT NOT NULL DEFAULT 'delete',
                    anticaps_enabled INTEGER NOT NULL DEFAULT 0,
                    caps_limit INTEGER NOT NULL DEFAULT {DEFAULT_CAPS_LIMIT},
                    antimat_enabled INTEGER NOT NULL DEFAULT 0,
                    mat_action TEXT NOT NULL DEFAULT 'delete',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS extra_owners (
                    peer_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    added_by INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (peer_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS members (
                    peer_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    warnings INTEGER NOT NULL DEFAULT 0,
                    muted_until REAL NOT NULL DEFAULT 0,
                    mute_reason TEXT NOT NULL DEFAULT '',
                    banned INTEGER NOT NULL DEFAULT 0,
                    ban_reason TEXT NOT NULL DEFAULT '',
                    messages_count INTEGER NOT NULL DEFAULT 0,
                    commands_count INTEGER NOT NULL DEFAULT 0,
                    deleted_count INTEGER NOT NULL DEFAULT 0,
                    last_seen REAL NOT NULL DEFAULT 0,
                    joined_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (peer_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS roles (
                    peer_id INTEGER NOT NULL,
                    role_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    permissions TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (peer_id, role_key)
                );

                CREATE TABLE IF NOT EXISTS member_roles (
                    peer_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role_key TEXT NOT NULL,
                    assigned_by INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (peer_id, user_id, role_key)
                );

                CREATE TABLE IF NOT EXISTS notes (
                    peer_id INTEGER NOT NULL,
                    note_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (peer_id, note_key)
                );

                CREATE TABLE IF NOT EXISTS allowed_domains (
                    peer_id INTEGER NOT NULL,
                    domain TEXT NOT NULL,
                    added_by INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (peer_id, domain)
                );

                CREATE TABLE IF NOT EXISTS bad_words (
                    peer_id INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    added_by INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (peer_id, word)
                );

                CREATE TABLE IF NOT EXISTS action_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id INTEGER NOT NULL,
                    actor_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL DEFAULT 0,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS message_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    cmid INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_member_mute
                    ON members(peer_id, muted_until);
                CREATE INDEX IF NOT EXISTS idx_member_ban
                    ON members(peer_id, banned);
                CREATE INDEX IF NOT EXISTS idx_action_log
                    ON action_log(peer_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_message_log
                    ON message_log(peer_id, id DESC);
                """
            )

    def ensure_chat(self, peer_id: int) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO chats (peer_id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(peer_id) DO NOTHING
                """,
                (peer_id, now, now),
            )

    def get_chat(self, peer_id: int) -> dict[str, Any]:
        self.ensure_chat(peer_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chats WHERE peer_id = ?", (peer_id,)
            ).fetchone()
        return dict(row) if row else {}

    def update_chat(self, peer_id: int, **fields: Any) -> None:
        self.ensure_chat(peer_id)
        clean = {k: v for k, v in fields.items() if k in self.CHAT_FIELDS}
        if not clean:
            return
        clean["updated_at"] = time.time()
        set_sql = ", ".join(f"{key} = ?" for key in clean)
        values = list(clean.values()) + [peer_id]
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE chats SET {set_sql} WHERE peer_id = ?", values
            )

    def ensure_member(self, peer_id: int, user_id: int) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO members (peer_id, user_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(peer_id, user_id) DO NOTHING
                """,
                (peer_id, user_id, now),
            )

    def get_member(self, peer_id: int, user_id: int) -> dict[str, Any]:
        self.ensure_member(peer_id, user_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM members WHERE peer_id = ? AND user_id = ?",
                (peer_id, user_id),
            ).fetchone()
        return dict(row) if row else {}

    def update_member(self, peer_id: int, user_id: int, **fields: Any) -> None:
        allowed = {
            "nickname", "warnings", "muted_until", "mute_reason",
            "banned", "ban_reason", "messages_count", "commands_count",
            "deleted_count", "last_seen", "joined_at",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return
        self.ensure_member(peer_id, user_id)
        clean["updated_at"] = time.time()
        set_sql = ", ".join(f"{key} = ?" for key in clean)
        values = list(clean.values()) + [peer_id, user_id]
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE members SET {set_sql} WHERE peer_id = ? AND user_id = ?",
                values,
            )

    def increment_member(
        self, peer_id: int, user_id: int, field: str, amount: int = 1
    ) -> None:
        if field not in {"messages_count", "commands_count", "deleted_count"}:
            return
        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"""
                UPDATE members
                SET {field} = {field} + ?, updated_at = ?
                WHERE peer_id = ? AND user_id = ?
                """,
                (amount, time.time(), peer_id, user_id),
            )

    def change_warnings(self, peer_id: int, user_id: int, delta: int) -> int:
        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE members
                SET warnings = MAX(0, warnings + ?), updated_at = ?
                WHERE peer_id = ? AND user_id = ?
                """,
                (delta, time.time(), peer_id, user_id),
            )
        return int(self.get_member(peer_id, user_id).get("warnings", 0))

    def add_owner(self, peer_id: int, user_id: int, actor_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO extra_owners (peer_id, user_id, added_by, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(peer_id, user_id) DO UPDATE SET
                    added_by = excluded.added_by,
                    created_at = excluded.created_at
                """,
                (peer_id, user_id, actor_id, time.time()),
            )

    def del_owner(self, peer_id: int, user_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM extra_owners WHERE peer_id = ? AND user_id = ?",
                (peer_id, user_id),
            )
        return cur.rowcount > 0

    def is_extra_owner(self, peer_id: int, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM extra_owners WHERE peer_id = ? AND user_id = ?",
                (peer_id, user_id),
            ).fetchone()
        return row is not None

    def list_owners(self, peer_id: int) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id FROM extra_owners WHERE peer_id = ? ORDER BY created_at",
                (peer_id,),
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    @staticmethod
    def key(value: str) -> str:
        return " ".join(value.casefold().split())

    def create_role(
        self, peer_id: int, name: str, level: int,
        permissions: Iterable[str], actor_id: int,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO roles (
                    peer_id, role_key, display_name, level, permissions,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    peer_id, self.key(name), name, level,
                    json.dumps(sorted(set(permissions)), ensure_ascii=False),
                    actor_id, now, now,
                ),
            )

    def get_role(self, peer_id: int, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM roles WHERE peer_id = ? AND role_key = ?",
                (peer_id, self.key(name)),
            ).fetchone()
        return dict(row) if row else None

    def list_roles(self, peer_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM roles WHERE peer_id = ? ORDER BY level DESC, display_name",
                (peer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_role(self, peer_id: int, name: str, **fields: Any) -> bool:
        clean: dict[str, Any] = {}
        if "level" in fields:
            clean["level"] = int(fields["level"])
        if "permissions" in fields:
            clean["permissions"] = json.dumps(
                sorted(set(fields["permissions"])), ensure_ascii=False
            )
        if not clean:
            return False
        clean["updated_at"] = time.time()
        set_sql = ", ".join(f"{key} = ?" for key in clean)
        values = list(clean.values()) + [peer_id, self.key(name)]
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE roles SET {set_sql} WHERE peer_id = ? AND role_key = ?",
                values,
            )
        return cur.rowcount > 0

    def delete_role(self, peer_id: int, name: str) -> bool:
        role_key = self.key(name)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM member_roles WHERE peer_id = ? AND role_key = ?",
                (peer_id, role_key),
            )
            cur = self._conn.execute(
                "DELETE FROM roles WHERE peer_id = ? AND role_key = ?",
                (peer_id, role_key),
            )
        return cur.rowcount > 0

    def give_role(self, peer_id: int, user_id: int, name: str, actor_id: int) -> bool:
        role = self.get_role(peer_id, name)
        if not role:
            return False
        self.ensure_member(peer_id, user_id)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO member_roles (peer_id, user_id, role_key, assigned_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(peer_id, user_id, role_key) DO UPDATE SET
                    assigned_by = excluded.assigned_by,
                    created_at = excluded.created_at
                """,
                (peer_id, user_id, role["role_key"], actor_id, time.time()),
            )
        return True

    def take_role(self, peer_id: int, user_id: int, name: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                DELETE FROM member_roles
                WHERE peer_id = ? AND user_id = ? AND role_key = ?
                """,
                (peer_id, user_id, self.key(name)),
            )
        return cur.rowcount > 0

    def clear_roles(self, peer_id: int, user_id: int) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM member_roles WHERE peer_id = ? AND user_id = ?",
                (peer_id, user_id),
            )
        return cur.rowcount

    def member_roles(self, peer_id: int, user_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT r.* FROM member_roles mr
                JOIN roles r ON r.peer_id = mr.peer_id AND r.role_key = mr.role_key
                WHERE mr.peer_id = ? AND mr.user_id = ?
                ORDER BY r.level DESC, r.display_name
                """,
                (peer_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def staff_rows(self, peer_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT mr.user_id, r.display_name, r.level
                FROM member_roles mr
                JOIN roles r ON r.peer_id = mr.peer_id AND r.role_key = mr.role_key
                WHERE mr.peer_id = ?
                ORDER BY r.level DESC, mr.user_id
                """,
                (peer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_note(self, peer_id: int, name: str, text: str, actor_id: int) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO notes (
                    peer_id, note_key, display_name, text,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(peer_id, note_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    text = excluded.text,
                    created_by = excluded.created_by,
                    updated_at = excluded.updated_at
                """,
                (peer_id, self.key(name), name, text, actor_id, now, now),
            )

    def get_note(self, peer_id: int, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM notes WHERE peer_id = ? AND note_key = ?",
                (peer_id, self.key(name)),
            ).fetchone()
        return dict(row) if row else None

    def list_notes(self, peer_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notes WHERE peer_id = ? ORDER BY display_name",
                (peer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_note(self, peer_id: int, name: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM notes WHERE peer_id = ? AND note_key = ?",
                (peer_id, self.key(name)),
            )
        return cur.rowcount > 0

    def add_domain(self, peer_id: int, domain: str, actor_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO allowed_domains
                (peer_id, domain, added_by, created_at) VALUES (?, ?, ?, ?)
                """,
                (peer_id, domain, actor_id, time.time()),
            )

    def del_domain(self, peer_id: int, domain: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM allowed_domains WHERE peer_id = ? AND domain = ?",
                (peer_id, domain),
            )
        return cur.rowcount > 0

    def list_domains(self, peer_id: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT domain FROM allowed_domains WHERE peer_id = ? ORDER BY domain",
                (peer_id,),
            ).fetchall()
        return [str(row["domain"]) for row in rows]

    def add_bad_word(self, peer_id: int, word: str, actor_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO bad_words
                (peer_id, word, added_by, created_at) VALUES (?, ?, ?, ?)
                """,
                (peer_id, word, actor_id, time.time()),
            )

    def del_bad_word(self, peer_id: int, word: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM bad_words WHERE peer_id = ? AND word = ?",
                (peer_id, word),
            )
        return cur.rowcount > 0

    def list_bad_words(self, peer_id: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT word FROM bad_words WHERE peer_id = ? ORDER BY word",
                (peer_id,),
            ).fetchall()
        return [str(row["word"]) for row in rows]

    def log_action(
        self, peer_id: int, actor_id: int, target_id: int,
        action: str, details: str = "",
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO action_log
                (peer_id, actor_id, target_id, action, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (peer_id, actor_id, target_id, action, details, time.time()),
            )

    def action_history(
        self, peer_id: int, target_id: int | None = None, limit: int = 15
    ) -> list[dict[str, Any]]:
        with self._lock:
            if target_id is None:
                rows = self._conn.execute(
                    """
                    SELECT * FROM action_log WHERE peer_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (peer_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM action_log
                    WHERE peer_id = ? AND target_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (peer_id, target_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def log_message(self, peer_id: int, user_id: int, cmid: int) -> None:
        if cmid <= 0:
            return
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO message_log (peer_id, user_id, cmid, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (peer_id, user_id, cmid, time.time()),
            )
            self._conn.execute(
                """
                DELETE FROM message_log
                WHERE peer_id = ? AND id NOT IN (
                    SELECT id FROM message_log WHERE peer_id = ?
                    ORDER BY id DESC LIMIT ?
                )
                """,
                (peer_id, peer_id, MESSAGE_LOG_LIMIT),
            )

    def recent_cmids(
        self, peer_id: int, limit: int, user_id: int | None = None
    ) -> list[int]:
        with self._lock:
            if user_id is None:
                rows = self._conn.execute(
                    """
                    SELECT cmid FROM message_log WHERE peer_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (peer_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT cmid FROM message_log
                    WHERE peer_id = ? AND user_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (peer_id, user_id, limit),
                ).fetchall()
        return [int(row["cmid"]) for row in rows]

    def top_members(self, peer_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM members WHERE peer_id = ?
                ORDER BY messages_count DESC, user_id ASC LIMIT ?
                """,
                (peer_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def warning_members(self, peer_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM members
                WHERE peer_id = ? AND warnings > 0
                ORDER BY warnings DESC, updated_at DESC LIMIT ?
                """,
                (peer_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def muted_members(self, peer_id: int, limit: int = 100) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM members
                WHERE peer_id = ? AND (muted_until = -1 OR muted_until > ?)
                ORDER BY muted_until ASC LIMIT ?
                """,
                (peer_id, now, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def banned_members(self, peer_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM members WHERE peer_id = ? AND banned = 1
                ORDER BY updated_at DESC LIMIT ?
                """,
                (peer_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def nick_members(self, peer_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM members WHERE peer_id = ? AND nickname <> ''
                ORDER BY nickname LIMIT ?
                """,
                (peer_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def cleanup(self) -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                UPDATE members SET muted_until = 0, mute_reason = '', updated_at = ?
                WHERE muted_until > 0 AND muted_until <= ?
                """,
                (now, now),
            )
        return cur.rowcount


db = Database(DATABASE_FILE)


# ============================================================
# VK И КЭШИ
# ============================================================

vk_session: vk_api.VkApi | None = None
vk: Any = None
BOT_GROUP_ID = 0

cache_lock = threading.RLock()
name_cache: dict[int, tuple[float, str]] = {}
owner_cache: dict[int, float] = {}
chat_title_cache: dict[int, tuple[float, str]] = {}

spam_history: dict[tuple[int, int], deque[float]] = defaultdict(deque)
slowmode_last: dict[tuple[int, int], float] = {}
report_cooldown: dict[tuple[int, int], float] = {}
runtime_lock = threading.RLock()


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
        key in response for key in ("name", "screen_name", "type", "is_closed")
    ):
        try:
            value = int(response["id"])
            return value if value > 0 else None
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
            group_id = extract_group_id(vk.groups.getById(**params))
            if group_id:
                return group_id
        except Exception as exc:
            last_error = exc
    raise SystemExit(
        "Не удалось определить ID сообщества по токену. "
        f"Последняя ошибка: {last_error}"
    )


def send_message(
    peer_id: int, text: str, *, reply_to: int | None = None
) -> None:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "random_id": get_random_id(),
        "message": text,
        "disable_mentions": 1,
    }
    if reply_to:
        params["reply_to"] = reply_to
    vk.messages.send(**params)


def send_long(peer_id: int, text: str, limit: int = 3500) -> None:
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            send_message(peer_id, remaining)
            return
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        send_message(peer_id, remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
        time.sleep(0.25)


def is_group_chat(peer_id: int) -> bool:
    return peer_id >= 2_000_000_000


def chat_id(peer_id: int) -> int:
    return peer_id - 2_000_000_000


def get_vk_name(user_id: int) -> str:
    now = time.time()
    with cache_lock:
        cached = name_cache.get(user_id)
        if cached and cached[0] > now:
            return cached[1]
    name = f"id{user_id}"
    try:
        users = vk.users.get(user_ids=[user_id])
        if users:
            full = f"{users[0].get('first_name', '')} {users[0].get('last_name', '')}".strip()
            if full:
                name = full
    except Exception:
        pass
    with cache_lock:
        name_cache[user_id] = (now + 3600, name)
    return name


def display_name(peer_id: int, user_id: int) -> str:
    nickname = str(db.get_member(peer_id, user_id).get("nickname", "")).strip()
    return nickname or get_vk_name(user_id)


def mention(peer_id: int, user_id: int) -> str:
    label = display_name(peer_id, user_id)
    label = label.replace("[", "").replace("]", "").replace("|", "")
    return f"[id{user_id}|{label}]"


def get_chat_title(peer_id: int) -> str:
    now = time.time()
    with cache_lock:
        cached = chat_title_cache.get(peer_id)
        if cached and cached[0] > now:
            return cached[1]
    title = "беседа"
    try:
        response = vk.messages.getConversationsById(peer_ids=[peer_id])
        items = response.get("items", [])
        if items:
            title = str(items[0].get("chat_settings", {}).get("title", title))
    except Exception:
        pass
    with cache_lock:
        chat_title_cache[peer_id] = (now + 600, title)
    return title


def conversation_members(peer_id: int) -> list[dict[str, Any]]:
    response = vk.messages.getConversationMembers(
        peer_id=peer_id, count=1000, extended=0
    )
    items = response.get("items", [])
    return [item for item in items if isinstance(item, dict)]


def sync_owner(peer_id: int, force: bool = False) -> int:
    now = time.time()
    chat = db.get_chat(peer_id)
    current = int(chat.get("native_owner_id", 0))
    with cache_lock:
        last = owner_cache.get(peer_id, 0)
    if current and not force and now - last < OWNER_SYNC_INTERVAL:
        return current
    owner_id = 0
    for item in conversation_members(peer_id):
        if item.get("is_owner"):
            try:
                candidate = int(item.get("member_id", 0))
            except (TypeError, ValueError):
                continue
            if candidate > 0:
                owner_id = candidate
                break
    if owner_id:
        db.update_chat(peer_id, native_owner_id=owner_id)
        db.del_owner(peer_id, owner_id)
    with cache_lock:
        owner_cache[peer_id] = now
    return owner_id or current


def delete_cmids(peer_id: int, cmids: list[int]) -> tuple[int, str]:
    unique = list(dict.fromkeys(cmid for cmid in cmids if cmid > 0))
    if not unique:
        return 0, "Сообщения не найдены."
    deleted = 0
    error = ""
    for start in range(0, len(unique), 100):
        batch = unique[start:start + 100]
        try:
            vk.messages.delete(
                peer_id=peer_id, cmids=batch, delete_for_all=1
            )
            deleted += len(batch)
        except ApiError as exc:
            error = str(exc)
            break
        except Exception as exc:
            error = str(exc)
            break
    return deleted, error


def delete_event_message(peer_id: int, message: dict[str, Any]) -> bool:
    try:
        cmid = int(message.get("conversation_message_id", 0))
    except (TypeError, ValueError):
        cmid = 0
    deleted, _ = delete_cmids(peer_id, [cmid])
    return deleted > 0


def remove_chat_user(peer_id: int, user_id: int) -> tuple[bool, str]:
    try:
        vk.messages.removeChatUser(chat_id=chat_id(peer_id), member_id=user_id)
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ============================================================
# ПРАВА
# ============================================================

def decode_permissions(value: str) -> set[str]:
    try:
        data = json.loads(value)
    except Exception:
        return set()
    return {str(item) for item in data if str(item) in ALL_PERMISSIONS}


def actor_context(peer_id: int, user_id: int) -> ActorContext:
    owner_id = int(db.get_chat(peer_id).get("native_owner_id", 0))
    if owner_id and user_id == owner_id:
        return ActorContext(
            user_id, NATIVE_OWNER_LEVEL, frozenset(ALL_PERMISSIONS),
            "native_owner", (),
        )
    if db.is_extra_owner(peer_id, user_id):
        return ActorContext(
            user_id, EXTRA_OWNER_LEVEL, frozenset(ALL_PERMISSIONS),
            "extra_owner", (),
        )
    roles = db.member_roles(peer_id, user_id)
    permissions: set[str] = set()
    names: list[str] = []
    level = 0
    for role in roles:
        level = max(level, int(role["level"]))
        permissions.update(decode_permissions(str(role["permissions"])))
        names.append(str(role["display_name"]))
    return ActorContext(
        user_id, level, frozenset(permissions),
        "role" if roles else "member", tuple(names),
    )


def require_permission(peer_id: int, user_id: int, permission: str) -> ActorContext | None:
    actor = actor_context(peer_id, user_id)
    if permission in actor.permissions:
        return actor
    send_message(
        peer_id,
        f"⛔ Недостаточно прав. Нужно право: {PERMISSION_NAMES[permission]}.",
    )
    return None


def require_owner(peer_id: int, user_id: int) -> ActorContext | None:
    actor = actor_context(peer_id, user_id)
    if actor.is_owner:
        return actor
    send_message(peer_id, "⛔ Команда доступна только владельцам беседы.")
    return None


def require_native_owner(peer_id: int, user_id: int) -> ActorContext | None:
    actor = actor_context(peer_id, user_id)
    if actor.is_native_owner:
        return actor
    send_message(peer_id, "⛔ Команда доступна только создателю беседы.")
    return None


def can_target(peer_id: int, actor: ActorContext, target_id: int) -> tuple[bool, str]:
    if target_id <= 0:
        return False, "Некорректный пользователь."
    if target_id == actor.user_id:
        return False, "Нельзя применить команду к себе."
    owner_id = int(db.get_chat(peer_id).get("native_owner_id", 0))
    if target_id == owner_id:
        return False, "Создателя беседы нельзя модерировать."
    if actor.is_native_owner:
        return True, ""
    if db.is_extra_owner(peer_id, target_id):
        return False, "Дополнительного владельца может модерировать только создатель."
    target = actor_context(peer_id, target_id)
    if actor.level <= target.level:
        return False, "Нельзя модерировать равный или более высокий уровень."
    return True, ""


def ensure_owner(peer_id: int) -> bool:
    try:
        owner_id = sync_owner(peer_id)
    except Exception as exc:
        send_message(
            peer_id,
            "⚠ Не удалось определить создателя. Назначьте сообщество "
            f"администратором беседы и отправьте /setup. Ошибка: {exc}",
        )
        return False
    if owner_id <= 0:
        send_message(peer_id, "⚠ Создатель беседы не определён. Выполните /setup.")
        return False
    return True


# ============================================================
# РАЗБОР КОМАНД
# ============================================================

ALIASES = {
    "/помощь": "/help", "/команды": "/help", "/старт": "/help",
    "/настройка": "/setup", "/профиль": "/profile",
    "/пред": "/warn", "/снятьпред": "/unwarn",
    "/преды": "/warns", "/предлист": "/warnlist",
    "/мут": "/mute", "/размут": "/unmute", "/мутлист": "/mutelist",
    "/бан": "/ban", "/разбан": "/unban", "/банлист": "/banlist",
    "/кик": "/kick", "/ник": "/nick", "/снятьник": "/unnick",
    "/роли": "/roles", "/персонал": "/staff",
    "/владельцы": "/owners", "/правила": "/rules",
    "/статистика": "/stats", "/топ": "/top",
}

TARGET_PATTERNS = [
    re.compile(r"^\[id(\d+)\|[^\]]+\]\s*", re.I),
    re.compile(r"^@?id(\d+)\s*", re.I),
    re.compile(r"^(?:https?://)?(?:m\.)?vk\.(?:com|ru)/id(\d+)\s*", re.I),
    re.compile(r"^(\d+)\s*"),
]

DURATION_UNITS = {
    "s": 1, "с": 1, "sec": 1,
    "m": 60, "м": 60, "min": 60,
    "h": 3600, "ч": 3600,
    "d": 86400, "д": 86400,
    "w": 604800, "н": 604800,
}

LINK_RE = re.compile(
    r"(?i)(?:https?://|www\.|vk\.cc/|t\.me/|discord\.gg/|"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-zа-я]{2,})\S*"
)


def normalize_text(text: str) -> str:
    text = text.strip()
    return re.sub(
        r"^\[(?:club|public)\d+\|[^\]]+\]\s*", "", text, flags=re.I
    ).strip()


def split_command(text: str) -> tuple[str, str]:
    text = normalize_text(text)
    if not text:
        return "", ""
    parts = text.split(maxsplit=1)
    command = parts[0].casefold().split("@", 1)[0]
    if command in {"help", "команды", "помощь", "start"}:
        command = "/help"
    command = ALIASES.get(command, command)
    args = parts[1].strip() if len(parts) > 1 else ""
    return command, args


def replied_user(message: dict[str, Any]) -> int:
    reply = message.get("reply_message")
    if isinstance(reply, dict):
        try:
            value = int(reply.get("from_id", 0))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    forwards = message.get("fwd_messages")
    if isinstance(forwards, list):
        for item in forwards:
            if isinstance(item, dict):
                try:
                    value = int(item.get("from_id", 0))
                    if value > 0:
                        return value
                except (TypeError, ValueError):
                    pass
    return 0


def extract_target(message: dict[str, Any], args: str) -> tuple[int, str]:
    reply_id = replied_user(message)
    if reply_id:
        return reply_id, args.strip()
    for pattern in TARGET_PATTERNS:
        match = pattern.match(args)
        if match:
            return int(match.group(1)), args[match.end():].strip()
    return 0, args.strip()


def tokens(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:
        return []


def parse_permissions(value: str) -> tuple[set[str], set[str]]:
    values = {
        item.strip().casefold()
        for item in re.split(r"[,;\s]+", value)
        if item.strip()
    }
    if values & {"all", "все"}:
        return set(ALL_PERMISSIONS), set()
    return values & ALL_PERMISSIONS, values - ALL_PERMISSIONS


def parse_duration(value: str) -> int | None:
    value = value.casefold().strip()
    if value in {"навсегда", "вечный", "forever", "perm", "0"}:
        return -1
    match = re.fullmatch(r"(\d+)([a-zа-я]+)?", value)
    if not match:
        return None
    amount = int(match.group(1))
    multiplier = DURATION_UNITS.get(match.group(2) or "m")
    if amount <= 0 or multiplier is None:
        return None
    seconds = amount * multiplier
    return seconds if seconds <= 365 * 86400 else None


def format_duration(seconds: int | float) -> str:
    if seconds == -1:
        return "навсегда"
    seconds = max(0, int(seconds))
    result: list[str] = []
    for unit, name in ((86400, "д."), (3600, "ч."), (60, "мин."), (1, "сек.")):
        if seconds >= unit:
            value, seconds = divmod(seconds, unit)
            result.append(f"{value} {name}")
            if len(result) == 2:
                break
    return " ".join(result) or "0 сек."


def reason(value: str) -> str:
    value = " ".join(value.split())
    return (value or "Причина не указана")[:MAX_REASON_LENGTH]


def on_off(value: str) -> bool | None:
    value = value.casefold().strip()
    if value in {"on", "вкл", "включить", "1", "+"}:
        return True
    if value in {"off", "выкл", "выключить", "0", "-"}:
        return False
    return None


def normalize_domain(value: str) -> str:
    value = value.strip().casefold()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    try:
        host = urlparse(value).hostname or ""
    except Exception:
        host = ""
    return host.removeprefix("www.").strip(".")


def extract_domains(text: str) -> list[str]:
    result: list[str] = []
    for raw in LINK_RE.findall(text):
        candidate = raw
        if candidate.startswith("vk.cc/"):
            result.append("vk.cc")
            continue
        if candidate.startswith("t.me/"):
            result.append("t.me")
            continue
        if candidate.startswith("discord.gg/"):
            result.append("discord.gg")
            continue
        domain = normalize_domain(candidate)
        if domain:
            result.append(domain)
    return list(dict.fromkeys(result))


# ============================================================
# СПРАВКА
# ============================================================

HELP_PAGES = [
    """📚 GRAND Manager PRO — команды 1/4

ОБЩИЕ
/help — вся справка
/about — информация о боте
/ping — проверка работы
/setup — определить создателя
/status — состояние бота
/settings — настройки беседы
/profile [пользователь] — профиль
/id [пользователь] — VK ID
/chatid — ID беседы
/members — число участников
/owner — создатель
/owners — все владельцы
/staff — персонал
/seen [пользователь] — последняя активность
/stats [пользователь] — статистика
/top — топ активности""",
    """🛡 GRAND Manager PRO — команды 2/4

МОДЕРАЦИЯ
/warn [пользователь] [причина]
/unwarn [пользователь]
/clearwarns [пользователь]
/warns [пользователь]
/warnlist
/mute [пользователь] 10m [причина]
/unmute [пользователь]
/mutelist
/ban [пользователь] [причина]
/unban [пользователь]
/banlist
/kick [пользователь] [причина]
/nick [пользователь] <ник>
/unnick [пользователь]
/nicks
/history [пользователь]
/reason [пользователь]
/report [пользователь] [причина]
/clear <1-100>
/purge [пользователь] <1-100>""",
    """🎖 GRAND Manager PRO — команды 3/4

ВЛАДЕЛЬЦЫ И РОЛИ
/addowner [пользователь]
/delowner [пользователь]
/rolecreate \"Название\" <уровень> <права>
/roledelete \"Название\"
/roleperm \"Название\" <права>
/rolelevel \"Название\" <уровень>
/giverole [пользователь] \"Название\"
/takerole [пользователь] \"Название\"
/clearroles [пользователь]
/roles
/myrole
/permissions [роль]

Права: warn, mute, ban, kick, nick, roles,
clear, guard, settings, rules, notes, speak

ЧАТ
/lock и /unlock
/slowmode <секунды>
/slowmodeoff
/setwarnlimit <1-20>
/title <новое название>""",
    """⚙ GRAND Manager PRO — команды 4/4

ЗАЩИТА
/guard
/antispam on|off
/spamlimit <сообщений> <секунд>
/antilink on|off
/linkaction delete|warn|mute
/linkallow <домен>
/linkdel <домен>
/linklist
/anticaps on|off
/capslimit <процент>
/antimat on|off
/mataction delete|warn|mute
/badwordadd <слово>
/badworddel <слово>
/badwords

КОНТЕНТ
/setrules <текст>, /rules, /delrules
/setwelcome <текст>, /welcomeon, /welcomeoff, /welcome
/setgoodbye <текст>, /goodbyeon, /goodbyeoff, /goodbye
/noteadd \"название\" <текст>
/note \"название\", /notelist, /notedel \"название\"
/say <текст>, /announce <текст>
/coin, /dice, /random <от> <до>""",
]


def send_help(peer_id: int) -> None:
    for page in HELP_PAGES:
        send_message(peer_id, page)
        time.sleep(0.25)


def permission_text(values: Iterable[str]) -> str:
    values = sorted(set(values))
    return ", ".join(PERMISSION_NAMES.get(v, v) for v in values) or "нет"


# ============================================================
# МОДЕРАЦИЯ И ЗАЩИТА
# ============================================================

def ban_member(
    peer_id: int, actor_id: int, target_id: int, why: str, automatic: bool = False
) -> tuple[bool, str]:
    db.update_member(
        peer_id, target_id, banned=1, ban_reason=why,
        muted_until=0, mute_reason="",
    )
    ok, error = remove_chat_user(peer_id, target_id)
    db.log_action(
        peer_id, actor_id, target_id,
        "auto_ban" if automatic else "ban", why,
    )
    return ok, error


def guard_penalty(
    peer_id: int, user_id: int, message: dict[str, Any],
    action: str, why: str,
) -> None:
    delete_event_message(peer_id, message)
    db.increment_member(peer_id, user_id, "deleted_count")
    if action == "warn":
        count = db.change_warnings(peer_id, user_id, 1)
        limit = int(db.get_chat(peer_id).get("warn_limit", DEFAULT_WARN_LIMIT))
        db.log_action(peer_id, 0, user_id, "guard_warn", why)
        if count >= limit:
            ban_member(peer_id, 0, user_id, f"Автобан защиты: {why}", True)
    elif action == "mute":
        db.update_member(
            peer_id, user_id,
            muted_until=time.time() + DEFAULT_GUARD_MUTE_SECONDS,
            mute_reason=f"Защита: {why}",
        )
        db.log_action(peer_id, 0, user_id, "guard_mute", why)


def enforce_guards(
    peer_id: int, user_id: int, message: dict[str, Any], text: str
) -> bool:
    member = db.get_member(peer_id, user_id)
    now = time.time()

    if member.get("banned"):
        remove_chat_user(peer_id, user_id)
        return True

    muted_until = float(member.get("muted_until", 0))
    if muted_until > 0 and muted_until <= now:
        db.update_member(peer_id, user_id, muted_until=0, mute_reason="")
    elif muted_until == -1 or muted_until > now:
        delete_event_message(peer_id, message)
        db.increment_member(peer_id, user_id, "deleted_count")
        return True

    actor = actor_context(peer_id, user_id)
    if actor.level > 0:
        return False

    chat = db.get_chat(peer_id)

    if chat.get("locked"):
        delete_event_message(peer_id, message)
        db.increment_member(peer_id, user_id, "deleted_count")
        return True

    slowmode = int(chat.get("slowmode_seconds", 0))
    if slowmode > 0:
        key = (peer_id, user_id)
        with runtime_lock:
            last = slowmode_last.get(key, 0)
            slowmode_last[key] = now
        if now - last < slowmode:
            delete_event_message(peer_id, message)
            db.increment_member(peer_id, user_id, "deleted_count")
            return True

    if chat.get("antispam_enabled"):
        key = (peer_id, user_id)
        window = int(chat.get("spam_window", DEFAULT_SPAM_WINDOW))
        count = int(chat.get("spam_count", DEFAULT_SPAM_COUNT))
        with runtime_lock:
            history = spam_history[key]
            history.append(now)
            while history and history[0] < now - window:
                history.popleft()
            exceeded = len(history) > count
        if exceeded:
            guard_penalty(peer_id, user_id, message, "mute", "флуд")
            return True

    if chat.get("antilink_enabled"):
        domains = extract_domains(text)
        if domains:
            allowed = set(db.list_domains(peer_id))
            blocked = [
                domain for domain in domains
                if not any(domain == item or domain.endswith("." + item) for item in allowed)
            ]
            if blocked:
                guard_penalty(
                    peer_id, user_id, message,
                    str(chat.get("link_action", "delete")),
                    "запрещённая ссылка: " + ", ".join(blocked),
                )
                return True

    if chat.get("anticaps_enabled"):
        letters = [char for char in text if char.isalpha()]
        if len(letters) >= 8:
            upper = sum(1 for char in letters if char.isupper())
            ratio = upper * 100 / len(letters)
            if ratio >= int(chat.get("caps_limit", DEFAULT_CAPS_LIMIT)):
                guard_penalty(peer_id, user_id, message, "delete", "капс")
                return True

    if chat.get("antimat_enabled"):
        lowered = text.casefold()
        found = [word for word in db.list_bad_words(peer_id) if word in lowered]
        if found:
            guard_penalty(
                peer_id, user_id, message,
                str(chat.get("mat_action", "delete")),
                "стоп-слово: " + found[0],
            )
            return True

    return False


# ============================================================
# ОБРАБОТЧИК КОМАНД
# ============================================================

def handle_command(peer_id: int, user_id: int, message: dict[str, Any]) -> None:
    command, args = split_command(str(message.get("text", "")))
    if not command:
        return
    if command == "/help":
        send_help(peer_id, args)
        return
    if command == "/about":
        send_message(
            peer_id,
            f"🤖 {BOT_NAME} \n"
            "Многофункциональный менеджер VK-бесед. "
            "Модерация, роли, защита, статистика и контент.",
        )
        return
    if command == "/ping":
        send_message(peer_id, f"🏓 Pong! {BOT_NAME} работает.")
        return
    if command == "/setup":
        try:
            owner_id = sync_owner(peer_id, force=True)
        except Exception as exc:
            send_message(
                peer_id,
                "⚠ Не удалось определить создателя. Сделайте сообщество "
                f"администратором беседы. Ошибка: {exc}",
            )
            return
        send_message(
            peer_id,
            f"✅ Настройка завершена. Создатель: {mention(peer_id, owner_id)}",
        )
        return

    if not ensure_owner(peer_id):
        return

    if handle_grand_ultra(peer_id, user_id, message, command, args):
        return

    # ---------- Общие ----------
    if command in {"/status", "/settings"}:
        chat = db.get_chat(peer_id)
        send_message(
            peer_id,
            f"⚙ {BOT_NAME}\n"
            f"Владелец: {mention(peer_id, int(chat['native_owner_id']))}\n"
            f"Лимит предупреждений: {chat['warn_limit']}\n"
            f"Закрытый режим: {'вкл' if chat['locked'] else 'выкл'}\n"
            f"Медленный режим: {chat['slowmode_seconds']} сек.\n"
            f"Антиспам: {'вкл' if chat['antispam_enabled'] else 'выкл'}\n"
            f"Антиссылки: {'вкл' if chat['antilink_enabled'] else 'выкл'}\n"
            f"Антикапс: {'вкл' if chat['anticaps_enabled'] else 'выкл'}\n"
            f"Стоп-слова: {'вкл' if chat['antimat_enabled'] else 'выкл'}\n"
            f"Ролей: {len(db.list_roles(peer_id))}",
        )
        return

    if command in {"/profile", "/stats"}:
        target_id, _ = extract_target(message, args)
        target_id = target_id or user_id
        member = db.get_member(peer_id, target_id)
        context = actor_context(peer_id, target_id)
        muted_until = float(member.get("muted_until", 0))
        mute = (
            "навсегда" if muted_until == -1 else
            format_duration(muted_until - time.time()) if muted_until > time.time() else "нет"
        )
        role = (
            "Создатель" if context.is_native_owner else
            "Доп. владелец" if context.kind == "extra_owner" else
            ", ".join(context.roles) if context.roles else "Участник"
        )
        send_message(
            peer_id,
            f"👤 {mention(peer_id, target_id)}\n"
            f"ID: {target_id}\nСтатус: {role}\nУровень: {context.level}\n"
            f"Предупреждения: {member['warnings']}\nМут: {mute}\n"
            f"Бан: {'да' if member['banned'] else 'нет'}\n"
            f"Сообщений: {member['messages_count']}\n"
            f"Команд: {member['commands_count']}\n"
            f"Удалено защитой: {member['deleted_count']}",
        )
        return

    if command == "/id":
        target_id, _ = extract_target(message, args)
        target_id = target_id or user_id
        send_message(peer_id, f"🆔 {mention(peer_id, target_id)}: {target_id}")
        return
    if command == "/chatid":
        send_message(peer_id, f"🆔 peer_id беседы: {peer_id}\nchat_id: {chat_id(peer_id)}")
        return
    if command == "/members":
        try:
            items = conversation_members(peer_id)
            users = sum(1 for item in items if int(item.get("member_id", 0)) > 0)
            send_message(peer_id, f"👥 Участников в беседе: {users}")
        except Exception as exc:
            send_message(peer_id, f"⚠ Не удалось получить участников: {exc}")
        return
    if command == "/owner":
        owner_id = int(db.get_chat(peer_id)["native_owner_id"])
        send_message(peer_id, f"👑 Создатель: {mention(peer_id, owner_id)}")
        return
    if command == "/owners":
        owner_id = int(db.get_chat(peer_id)["native_owner_id"])
        lines = [f"👑 Создатель: {mention(peer_id, owner_id)}"]
        lines += [f"• Доп. владелец: {mention(peer_id, uid)}" for uid in db.list_owners(peer_id)]
        send_message(peer_id, "\n".join(lines))
        return
    if command == "/staff":
        lines = ["👥 Персонал:"]
        owner_id = int(db.get_chat(peer_id)["native_owner_id"])
        lines.append(f"• Создатель: {mention(peer_id, owner_id)}")
        lines += [f"• Владелец: {mention(peer_id, uid)}" for uid in db.list_owners(peer_id)]
        grouped: dict[int, list[str]] = defaultdict(list)
        for row in db.staff_rows(peer_id):
            grouped[int(row["user_id"])].append(str(row["display_name"]))
        for uid, role_names in grouped.items():
            lines.append(f"• {mention(peer_id, uid)} — {', '.join(role_names)}")
        send_long(peer_id, "\n".join(lines))
        return
    if command == "/seen":
        target_id, _ = extract_target(message, args)
        target_id = target_id or user_id
        last_seen = float(db.get_member(peer_id, target_id).get("last_seen", 0))
        value = "нет данных" if last_seen <= 0 else time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(last_seen))
        send_message(peer_id, f"👁 {mention(peer_id, target_id)} был активен: {value}")
        return
    if command == "/top":
        rows = db.top_members(peer_id, 10)
        lines = ["🏆 Топ активности:"]
        for index, row in enumerate(rows, 1):
            uid = int(row["user_id"])
            lines.append(f"{index}. {mention(peer_id, uid)} — {row['messages_count']}")
        send_message(peer_id, "\n".join(lines))
        return

    # ---------- Предупреждения ----------
    if command == "/warn":
        actor = require_permission(peer_id, user_id, "warn")
        if not actor:
            return
        target_id, rest = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Пример: /warn @id123 причина или ответом на сообщение.")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        why = reason(rest)
        count = db.change_warnings(peer_id, target_id, 1)
        limit = int(db.get_chat(peer_id)["warn_limit"])
        db.log_action(peer_id, user_id, target_id, "warn", why)
        if count >= limit:
            ok, api_error = ban_member(
                peer_id, user_id, target_id,
                f"Лимит предупреждений {count}/{limit}. {why}", True,
            )
            send_message(
                peer_id,
                f"🚫 {mention(peer_id, target_id)} получил автобан ({count}/{limit})."
                + ("" if ok else f"\nVK не исключил: {api_error}"),
            )
        else:
            send_message(
                peer_id,
                f"⚠ {mention(peer_id, target_id)} получил предупреждение "
                f"{count}/{limit}.\nПричина: {why}",
            )
        return

    if command in {"/unwarn", "/clearwarns"}:
        actor = require_permission(peer_id, user_id, "warn")
        if not actor:
            return
        target_id, _ = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Укажите пользователя.")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        if command == "/clearwarns":
            db.update_member(peer_id, target_id, warnings=0)
            count = 0
        else:
            count = db.change_warnings(peer_id, target_id, -1)
        db.log_action(peer_id, user_id, target_id, command.removeprefix("/"))
        send_message(peer_id, f"✅ Предупреждения {mention(peer_id, target_id)}: {count}")
        return

    if command == "/warns":
        target_id, _ = extract_target(message, args)
        target_id = target_id or user_id
        member = db.get_member(peer_id, target_id)
        limit = int(db.get_chat(peer_id)["warn_limit"])
        send_message(peer_id, f"⚠ {mention(peer_id, target_id)}: {member['warnings']}/{limit}")
        return
    if command == "/warnlist":
        rows = db.warning_members(peer_id)
        lines = ["⚠ Список предупреждений:"]
        lines += [f"• {mention(peer_id, int(row['user_id']))} — {row['warnings']}" for row in rows]
        send_long(peer_id, "\n".join(lines) if rows else "⚠ Список предупреждений пуст.")
        return

    # ---------- Мут, бан, кик ----------
    if command == "/mute":
        actor = require_permission(peer_id, user_id, "mute")
        if not actor:
            return
        target_id, rest = extract_target(message, args)
        parts = rest.split(maxsplit=1)
        if not target_id or not parts:
            send_message(peer_id, "Пример: /mute @id123 30m причина")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        duration = parse_duration(parts[0])
        if duration is None:
            send_message(peer_id, "Срок: 10m, 2h, 1d или навсегда.")
            return
        why = reason(parts[1] if len(parts) > 1 else "")
        until = -1 if duration == -1 else time.time() + duration
        db.update_member(peer_id, target_id, muted_until=until, mute_reason=why)
        db.log_action(peer_id, user_id, target_id, "mute", f"{format_duration(duration)} | {why}")
        send_message(peer_id, f"🔇 {mention(peer_id, target_id)} получил мут {format_duration(duration)}.\nПричина: {why}")
        return

    if command == "/unmute":
        actor = require_permission(peer_id, user_id, "mute")
        if not actor:
            return
        target_id, _ = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Укажите пользователя.")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        db.update_member(peer_id, target_id, muted_until=0, mute_reason="")
        db.log_action(peer_id, user_id, target_id, "unmute")
        send_message(peer_id, f"🔊 Мут снят с {mention(peer_id, target_id)}.")
        return

    if command == "/mutelist":
        rows = db.muted_members(peer_id)
        now = time.time()
        lines = ["🔇 Активные муты:"]
        for row in rows:
            until = float(row["muted_until"])
            duration = "навсегда" if until == -1 else format_duration(until - now)
            lines.append(f"• {mention(peer_id, int(row['user_id']))} — {duration}")
        send_long(peer_id, "\n".join(lines) if rows else "🔇 Активных мутов нет.")
        return

    if command == "/ban":
        actor = require_permission(peer_id, user_id, "ban")
        if not actor:
            return
        target_id, rest = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Пример: /ban @id123 причина")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        why = reason(rest)
        ok, api_error = ban_member(peer_id, user_id, target_id, why)
        send_message(
            peer_id,
            f"🚫 {mention(peer_id, target_id)} заблокирован.\nПричина: {why}"
            + ("" if ok else f"\nVK не исключил: {api_error}"),
        )
        return

    if command == "/unban":
        actor = require_permission(peer_id, user_id, "ban")
        if not actor:
            return
        target_id, _ = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Пример: /unban 123456")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        db.update_member(peer_id, target_id, banned=0, ban_reason="")
        db.log_action(peer_id, user_id, target_id, "unban")
        send_message(peer_id, f"✅ Бан снят с {mention(peer_id, target_id)}. Вернуть в беседу нужно вручную.")
        return

    if command == "/banlist":
        rows = db.banned_members(peer_id)
        lines = ["🚫 Бан-лист:"]
        lines += [f"• {mention(peer_id, int(row['user_id']))} — {row['ban_reason'] or 'без причины'}" for row in rows]
        send_long(peer_id, "\n".join(lines) if rows else "🚫 Бан-лист пуст.")
        return

    if command == "/kick":
        actor = require_permission(peer_id, user_id, "kick")
        if not actor:
            return
        target_id, rest = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Пример: /kick @id123 причина")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        ok, api_error = remove_chat_user(peer_id, target_id)
        if not ok:
            send_message(peer_id, f"⚠ VK не исключил пользователя: {api_error}")
            return
        db.log_action(peer_id, user_id, target_id, "kick", reason(rest))
        send_message(peer_id, f"👢 {mention(peer_id, target_id)} исключён.")
        return

    # ---------- Ники и история ----------
    if command == "/nick":
        actor = require_permission(peer_id, user_id, "nick")
        if not actor:
            return
        target_id, rest = extract_target(message, args)
        nickname = " ".join(rest.split()).replace("[", "").replace("]", "").replace("|", "")
        if not target_id or not 1 <= len(nickname) <= MAX_NICKNAME_LENGTH:
            send_message(peer_id, f"Пример: /nick @id123 Новый ник (до {MAX_NICKNAME_LENGTH} символов)")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        db.update_member(peer_id, target_id, nickname=nickname)
        db.log_action(peer_id, user_id, target_id, "nick", nickname)
        send_message(peer_id, f"🏷 Установлен ник «{nickname}» для [id{target_id}|{nickname}].")
        return

    if command == "/unnick":
        actor = require_permission(peer_id, user_id, "nick")
        if not actor:
            return
        target_id, _ = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Укажите пользователя.")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        db.update_member(peer_id, target_id, nickname="")
        db.log_action(peer_id, user_id, target_id, "unnick")
        send_message(peer_id, f"🏷 Ник снят с {mention(peer_id, target_id)}.")
        return

    if command == "/nicks":
        rows = db.nick_members(peer_id)
        lines = ["🏷 Никнеймы:"]
        lines += [f"• [id{int(row['user_id'])}|{row['nickname']}] — {row['nickname']}" for row in rows]
        send_long(peer_id, "\n".join(lines) if rows else "🏷 Никнеймов нет.")
        return

    if command in {"/history", "/reason"}:
        target_id, _ = extract_target(message, args)
        target_id = target_id or user_id
        if command == "/reason":
            member = db.get_member(peer_id, target_id)
            send_message(
                peer_id,
                f"📌 {mention(peer_id, target_id)}\n"
                f"Мут: {member['mute_reason'] or 'нет'}\n"
                f"Бан: {member['ban_reason'] or 'нет'}",
            )
            return
        rows = db.action_history(peer_id, target_id, 15)
        lines = [f"📜 История {mention(peer_id, target_id)}:"]
        for row in rows:
            date = time.strftime("%d.%m %H:%M", time.localtime(float(row["created_at"])))
            lines.append(f"• {date} — {row['action']}: {row['details'] or 'без деталей'}")
        send_long(peer_id, "\n".join(lines) if rows else "📜 История пуста.")
        return

    if command == "/report":
        target_id, rest = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Ответьте на сообщение: /report причина")
            return
        key = (peer_id, user_id)
        now = time.time()
        with runtime_lock:
            allowed_at = report_cooldown.get(key, 0)
            if now < allowed_at:
                send_message(peer_id, "⏳ Жалобу можно отправлять раз в 60 секунд.")
                return
            report_cooldown[key] = now + 60
        why = reason(rest)
        db.log_action(peer_id, user_id, target_id, "report", why)
        send_message(
            peer_id,
            f"🚨 Жалоба от {mention(peer_id, user_id)} на {mention(peer_id, target_id)}.\nПричина: {why}",
        )
        return

    if command in {"/clear", "/purge"}:
        actor = require_permission(peer_id, user_id, "clear")
        if not actor:
            return
        target_id = 0
        rest = args
        if command == "/purge":
            target_id, rest = extract_target(message, args)
            if not target_id:
                send_message(peer_id, "Пример: /purge @id123 20")
                return
            allowed, error = can_target(peer_id, actor, target_id)
            if not allowed:
                send_message(peer_id, f"⛔ {error}")
                return
        try:
            count = int(rest.strip() or "10")
        except ValueError:
            send_message(peer_id, "Количество должно быть числом от 1 до 100.")
            return
        count = max(1, min(MAX_CLEAR_MESSAGES, count))
        cmids = db.recent_cmids(peer_id, count, target_id or None)
        deleted, api_error = delete_cmids(peer_id, cmids)
        db.log_action(peer_id, user_id, target_id, command.removeprefix("/"), str(deleted))
        if api_error:
            send_message(peer_id, f"🧹 Удалено: {deleted}. Ошибка VK: {api_error}")
        return

    # ---------- Владельцы и роли ----------
    if command in {"/addowner", "/delowner"}:
        if not require_native_owner(peer_id, user_id):
            return
        target_id, _ = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Укажите пользователя.")
            return
        if command == "/addowner":
            if target_id == user_id:
                send_message(peer_id, "Вы уже создатель беседы.")
                return
            db.add_owner(peer_id, target_id, user_id)
            db.log_action(peer_id, user_id, target_id, "addowner")
            send_message(peer_id, f"👑 {mention(peer_id, target_id)} назначен владельцем.")
        else:
            if db.del_owner(peer_id, target_id):
                db.log_action(peer_id, user_id, target_id, "delowner")
                send_message(peer_id, f"👑 Права владельца сняты с {mention(peer_id, target_id)}.")
            else:
                send_message(peer_id, "Этот пользователь не является дополнительным владельцем.")
        return

    if command == "/rolecreate":
        if not require_owner(peer_id, user_id):
            return
        parts = tokens(args)
        if len(parts) < 2:
            send_message(peer_id, 'Пример: /rolecreate "Модератор" 50 warn,mute,nick')
            return
        name = parts[0]
        try:
            level = int(parts[1])
        except ValueError:
            send_message(peer_id, "Уровень роли должен быть числом.")
            return
        perms, invalid = parse_permissions(parts[2] if len(parts) > 2 else "warn,mute,nick")
        if invalid or not 1 <= level <= MAX_ROLE_LEVEL or not 1 <= len(name) <= MAX_ROLE_NAME_LENGTH:
            send_message(peer_id, f"Некорректная роль. Неизвестные права: {', '.join(sorted(invalid)) or 'нет'}")
            return
        if db.get_role(peer_id, name):
            send_message(peer_id, "Роль уже существует.")
            return
        db.create_role(peer_id, name, level, perms, user_id)
        db.log_action(peer_id, user_id, 0, "rolecreate", name)
        send_message(peer_id, f"✅ Роль «{name}» создана. Уровень {level}. Права: {permission_text(perms)}")
        return

    if command in {"/roledelete", "/roleperm", "/rolelevel"}:
        if not require_owner(peer_id, user_id):
            return
        parts = tokens(args)
        if not parts:
            send_message(peer_id, "Укажите название роли в кавычках.")
            return
        name = parts[0]
        if command == "/roledelete":
            ok = db.delete_role(peer_id, name)
            send_message(peer_id, f"{'✅ Роль удалена.' if ok else 'Роль не найдена.'}")
            return
        if len(parts) < 2:
            send_message(peer_id, "Не хватает нового значения.")
            return
        if command == "/roleperm":
            perms, invalid = parse_permissions(parts[1])
            if invalid:
                send_message(peer_id, "Неизвестные права: " + ", ".join(sorted(invalid)))
                return
            ok = db.update_role(peer_id, name, permissions=perms)
        else:
            try:
                level = int(parts[1])
            except ValueError:
                send_message(peer_id, "Уровень должен быть числом.")
                return
            if not 1 <= level <= MAX_ROLE_LEVEL:
                send_message(peer_id, f"Уровень: 1–{MAX_ROLE_LEVEL}.")
                return
            ok = db.update_role(peer_id, name, level=level)
        send_message(peer_id, "✅ Роль обновлена." if ok else "Роль не найдена.")
        return

    if command in {"/giverole", "/takerole", "/clearroles"}:
        actor = require_permission(peer_id, user_id, "roles")
        if not actor:
            return
        target_id, rest = extract_target(message, args)
        if not target_id:
            send_message(peer_id, "Укажите пользователя.")
            return
        allowed, error = can_target(peer_id, actor, target_id)
        if not allowed:
            send_message(peer_id, f"⛔ {error}")
            return
        if command == "/clearroles":
            count = db.clear_roles(peer_id, target_id)
            send_message(peer_id, f"🎖 Снято ролей: {count}.")
            return
        role_parts = tokens(rest)
        if not role_parts:
            send_message(peer_id, "Укажите название роли в кавычках.")
            return
        role_name = role_parts[0]
        role = db.get_role(peer_id, role_name)
        if not role:
            send_message(peer_id, "Роль не найдена.")
            return
        if not actor.is_owner and int(role["level"]) >= actor.level:
            send_message(peer_id, "Нельзя управлять ролью своего или более высокого уровня.")
            return
        ok = (
            db.give_role(peer_id, target_id, role_name, user_id)
            if command == "/giverole"
            else db.take_role(peer_id, target_id, role_name)
        )
        send_message(peer_id, "✅ Готово." if ok else "Изменение не выполнено.")
        return

    if command in {"/roles", "/myrole", "/permissions"}:
        if command == "/roles":
            roles = db.list_roles(peer_id)
            lines = ["🎖 Роли:"]
            for role in roles:
                lines.append(
                    f"• {role['display_name']} — уровень {role['level']}; "
                    f"{permission_text(decode_permissions(str(role['permissions'])))}"
                )
            send_long(peer_id, "\n".join(lines) if roles else "Ролей ещё нет.")
            return
        if command == "/myrole":
            context = actor_context(peer_id, user_id)
            send_message(peer_id, f"🎖 Ваш статус: {context.kind}. Роли: {', '.join(context.roles) or 'нет'}. Права: {permission_text(context.permissions)}")
            return
        role_name = " ".join(tokens(args))
        role = db.get_role(peer_id, role_name) if role_name else None
        if not role:
            send_message(peer_id, "Пример: /permissions \"Модератор\"")
            return
        send_message(peer_id, f"🔑 Права «{role['display_name']}»: {permission_text(decode_permissions(str(role['permissions'])))}")
        return

    # ---------- Настройки чата ----------
    if command in {"/lock", "/unlock"}:
        if not require_permission(peer_id, user_id, "settings"):
            return
        enabled = command == "/lock"
        db.update_chat(peer_id, locked=int(enabled))
        send_message(peer_id, "🔒 Беседа закрыта для обычных участников." if enabled else "🔓 Беседа открыта.")
        return
    if command in {"/slowmode", "/slowmodeoff"}:
        if not require_permission(peer_id, user_id, "settings"):
            return
        if command == "/slowmodeoff":
            seconds = 0
        else:
            try:
                seconds = int(args.strip())
            except ValueError:
                send_message(peer_id, "Пример: /slowmode 10")
                return
            if not 1 <= seconds <= 3600:
                send_message(peer_id, "Интервал: 1–3600 секунд.")
                return
        db.update_chat(peer_id, slowmode_seconds=seconds)
        send_message(peer_id, f"🐢 Медленный режим: {seconds} сек." if seconds else "🐢 Медленный режим выключен.")
        return
    if command == "/setwarnlimit":
        if not require_owner(peer_id, user_id):
            return
        try:
            value = int(args.strip())
        except ValueError:
            send_message(peer_id, "Пример: /setwarnlimit 3")
            return
        if not 1 <= value <= 20:
            send_message(peer_id, "Лимит: 1–20.")
            return
        db.update_chat(peer_id, warn_limit=value)
        send_message(peer_id, f"✅ Лимит предупреждений: {value}.")
        return
    if command == "/title":
        if not require_permission(peer_id, user_id, "settings"):
            return
        title = " ".join(args.split())[:250]
        if not title:
            send_message(peer_id, "Пример: /title Новое название")
            return
        try:
            vk.messages.editChat(chat_id=chat_id(peer_id), title=title)
            with cache_lock:
                chat_title_cache.pop(peer_id, None)
            send_message(peer_id, f"✅ Название изменено на «{title}».")
        except Exception as exc:
            send_message(peer_id, f"⚠ VK не изменил название: {exc}")
        return

    # ---------- Защита ----------
    if command == "/guard":
        chat = db.get_chat(peer_id)
        send_message(
            peer_id,
            "🛡 Защита беседы\n"
            f"Антиспам: {'вкл' if chat['antispam_enabled'] else 'выкл'} "
            f"({chat['spam_count']} сообщений/{chat['spam_window']} сек.)\n"
            f"Антиссылки: {'вкл' if chat['antilink_enabled'] else 'выкл'}; действие {chat['link_action']}\n"
            f"Антикапс: {'вкл' if chat['anticaps_enabled'] else 'выкл'}; лимит {chat['caps_limit']}%\n"
            f"Стоп-слова: {'вкл' if chat['antimat_enabled'] else 'выкл'}; действие {chat['mat_action']}\n"
            f"Разрешённых доменов: {len(db.list_domains(peer_id))}\n"
            f"Стоп-слов: {len(db.list_bad_words(peer_id))}",
        )
        return

    if command in {"/antispam", "/antilink", "/anticaps", "/antimat"}:
        if not require_permission(peer_id, user_id, "guard"):
            return
        enabled = on_off(args)
        if enabled is None:
            send_message(peer_id, f"Пример: {command} on")
            return
        field = {
            "/antispam": "antispam_enabled",
            "/antilink": "antilink_enabled",
            "/anticaps": "anticaps_enabled",
            "/antimat": "antimat_enabled",
        }[command]
        db.update_chat(peer_id, **{field: int(enabled)})
        send_message(peer_id, f"🛡 {command}: {'включено' if enabled else 'выключено'}.")
        return

    if command == "/spamlimit":
        if not require_permission(peer_id, user_id, "guard"):
            return
        parts = args.split()
        try:
            count, window = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            send_message(peer_id, "Пример: /spamlimit 6 8")
            return
        if not 3 <= count <= 30 or not 2 <= window <= 60:
            send_message(peer_id, "Сообщения: 3–30, окно: 2–60 секунд.")
            return
        db.update_chat(peer_id, spam_count=count, spam_window=window)
        send_message(peer_id, f"✅ Антиспам: {count} сообщений за {window} секунд.")
        return

    if command in {"/linkaction", "/mataction"}:
        if not require_permission(peer_id, user_id, "guard"):
            return
        action = args.casefold().strip()
        if action not in {"delete", "warn", "mute"}:
            send_message(peer_id, f"Пример: {command} delete|warn|mute")
            return
        field = "link_action" if command == "/linkaction" else "mat_action"
        db.update_chat(peer_id, **{field: action})
        send_message(peer_id, f"✅ Действие защиты: {action}.")
        return

    if command in {"/linkallow", "/linkdel"}:
        if not require_permission(peer_id, user_id, "guard"):
            return
        domain = normalize_domain(args)
        if not domain:
            send_message(peer_id, f"Пример: {command} example.com")
            return
        if command == "/linkallow":
            db.add_domain(peer_id, domain, user_id)
            send_message(peer_id, f"✅ Домен {domain} разрешён.")
        else:
            send_message(peer_id, "✅ Домен удалён." if db.del_domain(peer_id, domain) else "Домен не найден.")
        return
    if command == "/linklist":
        values = db.list_domains(peer_id)
        send_message(peer_id, "🔗 Разрешённые домены:\n" + ("\n".join(f"• {x}" for x in values) if values else "список пуст"))
        return
    if command == "/capslimit":
        if not require_permission(peer_id, user_id, "guard"):
            return
        try:
            value = int(args.strip())
        except ValueError:
            send_message(peer_id, "Пример: /capslimit 75")
            return
        if not 30 <= value <= 100:
            send_message(peer_id, "Процент: 30–100.")
            return
        db.update_chat(peer_id, caps_limit=value)
        send_message(peer_id, f"✅ Лимит капса: {value}%.")
        return
    if command in {"/badwordadd", "/badworddel"}:
        if not require_permission(peer_id, user_id, "guard"):
            return
        word = args.casefold().strip()[:100]
        if len(word) < 2:
            send_message(peer_id, "Укажите слово длиной от 2 символов.")
            return
        if command == "/badwordadd":
            db.add_bad_word(peer_id, word, user_id)
            send_message(peer_id, "✅ Стоп-слово добавлено.")
        else:
            send_message(peer_id, "✅ Стоп-слово удалено." if db.del_bad_word(peer_id, word) else "Слово не найдено.")
        return
    if command == "/badwords":
        values = db.list_bad_words(peer_id)
        send_long(peer_id, "🚫 Стоп-слова:\n" + (", ".join(values) if values else "список пуст"))
        return

    # ---------- Правила и приветствия ----------
    if command in {"/setrules", "/delrules"}:
        if not require_permission(peer_id, user_id, "rules"):
            return
        text = args.strip()
        if command == "/delrules":
            text = ""
        if len(text) > MAX_RULES_LENGTH:
            send_message(peer_id, f"Максимум {MAX_RULES_LENGTH} символов.")
            return
        db.update_chat(peer_id, rules_text=text)
        send_message(peer_id, "✅ Правила обновлены." if text else "✅ Правила удалены.")
        return
    if command == "/rules":
        text = str(db.get_chat(peer_id).get("rules_text", "")).strip()
        send_long(peer_id, "📜 Правила беседы:\n" + (text or "Правила не установлены."))
        return

    if command in {"/setwelcome", "/setgoodbye"}:
        if not require_permission(peer_id, user_id, "rules"):
            return
        text = args.strip()
        if not text or len(text) > MAX_WELCOME_LENGTH:
            send_message(peer_id, f"Текст должен содержать 1–{MAX_WELCOME_LENGTH} символов.")
            return
        field = "welcome_text" if command == "/setwelcome" else "goodbye_text"
        db.update_chat(peer_id, **{field: text})
        send_message(peer_id, "✅ Текст сохранён. Доступны {user}, {id}, {chat}.")
        return
    if command in {"/welcomeon", "/welcomeoff", "/goodbyeon", "/goodbyeoff"}:
        if not require_permission(peer_id, user_id, "rules"):
            return
        is_welcome = command.startswith("/welcome")
        enabled = command.endswith("on")
        field = "welcome_enabled" if is_welcome else "goodbye_enabled"
        db.update_chat(peer_id, **{field: int(enabled)})
        send_message(peer_id, f"✅ {'Приветствие' if is_welcome else 'Прощание'} {'включено' if enabled else 'выключено'}.")
        return
    if command in {"/welcome", "/goodbye"}:
        chat = db.get_chat(peer_id)
        field = "welcome_text" if command == "/welcome" else "goodbye_text"
        enabled_field = "welcome_enabled" if command == "/welcome" else "goodbye_enabled"
        send_message(peer_id, f"Статус: {'вкл' if chat[enabled_field] else 'выкл'}\nТекст: {chat[field]}")
        return

    # ---------- Заметки ----------
    if command == "/noteadd":
        if not require_permission(peer_id, user_id, "notes"):
            return
        parts = tokens(args)
        if len(parts) < 2:
            send_message(peer_id, 'Пример: /noteadd "ссылки" Полезный текст')
            return
        name = parts[0]
        prefix_end = args.find(parts[1])
        text = args[prefix_end:].strip() if prefix_end >= 0 else " ".join(parts[1:])
        if not 1 <= len(name) <= MAX_NOTE_NAME_LENGTH or not 1 <= len(text) <= MAX_NOTE_TEXT_LENGTH:
            send_message(peer_id, "Некорректная длина названия или текста.")
            return
        db.set_note(peer_id, name, text, user_id)
        send_message(peer_id, f"📝 Заметка «{name}» сохранена.")
        return
    if command == "/note":
        name = " ".join(tokens(args))
        note = db.get_note(peer_id, name) if name else None
        send_long(peer_id, f"📝 {note['display_name']}\n{note['text']}" if note else "Заметка не найдена.")
        return
    if command == "/notelist":
        notes = db.list_notes(peer_id)
        send_message(peer_id, "📝 Заметки:\n" + ("\n".join(f"• {n['display_name']}" for n in notes) if notes else "список пуст"))
        return
    if command == "/notedel":
        if not require_permission(peer_id, user_id, "notes"):
            return
        name = " ".join(tokens(args))
        send_message(peer_id, "✅ Заметка удалена." if db.delete_note(peer_id, name) else "Заметка не найдена.")
        return

    # ---------- Утилиты ----------
    if command in {"/say", "/announce"}:
        if not require_permission(peer_id, user_id, "speak"):
            return
        text = args.strip()
        if not text:
            send_message(peer_id, f"Пример: {command} текст")
            return
        send_long(peer_id, ("📢 ОБЪЯВЛЕНИЕ\n\n" if command == "/announce" else "") + text)
        return
    if command == "/coin":
        send_message(peer_id, "🪙 " + random.choice(["Орёл", "Решка"]))
        return
    if command == "/dice":
        send_message(peer_id, f"🎲 Выпало: {random.randint(1, 6)}")
        return
    if command == "/random":
        parts = args.split()
        try:
            start, end = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            send_message(peer_id, "Пример: /random 1 100")
            return
        if start > end:
            start, end = end, start
        if end - start > 10_000_000:
            send_message(peer_id, "Слишком большой диапазон.")
            return
        send_message(peer_id, f"🎯 Случайное число: {random.randint(start, end)}")
        return



# ============================================================
# GRAND ULTRA EXTENSION — КАТАЛОГ, ЭКОНОМИКА И УТИЛИТЫ
# ============================================================
import ast as _grand_ast
import math as _grand_math

GRAND_STARTED_AT = time.time()
GRAND_COMMAND_ENTRIES = [{'command': '/8ball',
  'canonical': '/8ball',
  'section': 'Игры',
  'description': 'Отвечает на вопрос в стиле магического шара.',
  'syntax': '/8ball <вопрос>',
  'alias': False},
 {'command': '/about',
  'canonical': '/about',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/about',
  'alias': False},
 {'command': '/addowner',
  'canonical': '/addowner',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/addowner',
  'alias': False},
 {'command': '/announce',
  'canonical': '/announce',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/announce',
  'alias': False},
 {'command': '/anticaps',
  'canonical': '/anticaps',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/anticaps',
  'alias': False},
 {'command': '/antilink',
  'canonical': '/antilink',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/antilink',
  'alias': False},
 {'command': '/antimat',
  'canonical': '/antimat',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/antimat',
  'alias': False},
 {'command': '/antispam',
  'canonical': '/antispam',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/antispam',
  'alias': False},
 {'command': '/badwordadd',
  'canonical': '/badwordadd',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/badwordadd',
  'alias': False},
 {'command': '/badworddel',
  'canonical': '/badworddel',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/badworddel',
  'alias': False},
 {'command': '/badwords',
  'canonical': '/badwords',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/badwords',
  'alias': False},
 {'command': '/balance',
  'canonical': '/balance',
  'section': 'Экономика',
  'description': 'Показывает баланс баллов пользователя.',
  'syntax': '/balance [пользователь]',
  'alias': False},
 {'command': '/ban',
  'canonical': '/ban',
  'section': 'Модерация',
  'description': 'Работа с бан-листом и исключением участников.',
  'syntax': '/ban',
  'alias': False},
 {'command': '/banlist',
  'canonical': '/banlist',
  'section': 'Модерация',
  'description': 'Работа с бан-листом и исключением участников.',
  'syntax': '/banlist',
  'alias': False},
 {'command': '/bio',
  'canonical': '/bio',
  'section': 'Профиль',
  'description': 'Показывает описание профиля.',
  'syntax': '/bio [пользователь]',
  'alias': False},
 {'command': '/calc',
  'canonical': '/calc',
  'section': 'Утилиты',
  'description': 'Безопасно вычисляет арифметическое выражение.',
  'syntax': '/calc <выражение>',
  'alias': False},
 {'command': '/capslimit',
  'canonical': '/capslimit',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/capslimit',
  'alias': False},
 {'command': '/chatid',
  'canonical': '/chatid',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/chatid',
  'alias': False},
 {'command': '/choose',
  'canonical': '/choose',
  'section': 'Игры',
  'description': 'Выбирает один из вариантов, разделённых символом |.',
  'syntax': '/choose вариант 1 | вариант 2',
  'alias': False},
 {'command': '/clear',
  'canonical': '/clear',
  'section': 'Модерация',
  'description': 'Очистка сообщений или исключение участника.',
  'syntax': '/clear',
  'alias': False},
 {'command': '/clearroles',
  'canonical': '/clearroles',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/clearroles',
  'alias': False},
 {'command': '/clearwarns',
  'canonical': '/clearwarns',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/clearwarns',
  'alias': False},
 {'command': '/coin',
  'canonical': '/coin',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/coin',
  'alias': False},
 {'command': '/commands',
  'canonical': '/commands',
  'section': 'Справка',
  'description': 'Постраничный каталог всех команд и алиасов.',
  'syntax': '/commands [страница]',
  'alias': False},
 {'command': '/customadd',
  'canonical': '/customadd',
  'section': 'Пользовательские команды',
  'description': 'Создаёт локальную команду с текстовым ответом.',
  'syntax': '/customadd /команда <ответ>',
  'alias': False},
 {'command': '/customdel',
  'canonical': '/customdel',
  'section': 'Пользовательские команды',
  'description': 'Удаляет локальную команду.',
  'syntax': '/customdel /команда',
  'alias': False},
 {'command': '/customlist',
  'canonical': '/customlist',
  'section': 'Пользовательские команды',
  'description': 'Показывает локальные команды.',
  'syntax': '/customlist',
  'alias': False},
 {'command': '/daily',
  'canonical': '/daily',
  'section': 'Экономика',
  'description': 'Выдаёт ежедневную награду и увеличивает серию.',
  'syntax': '/daily',
  'alias': False},
 {'command': '/delbio',
  'canonical': '/delbio',
  'section': 'Профиль',
  'description': 'Удаляет описание профиля.',
  'syntax': '/delbio',
  'alias': False},
 {'command': '/delowner',
  'canonical': '/delowner',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/delowner',
  'alias': False},
 {'command': '/delrules',
  'canonical': '/delrules',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/delrules',
  'alias': False},
 {'command': '/dice',
  'canonical': '/dice',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/dice',
  'alias': False},
 {'command': '/fact',
  'canonical': '/fact',
  'section': 'Игры',
  'description': 'Показывает случайный познавательный факт.',
  'syntax': '/fact',
  'alias': False},
 {'command': '/findcmd',
  'canonical': '/findcmd',
  'section': 'Справка',
  'description': 'Поиск команды по названию, синтаксису и описанию.',
  'syntax': '/findcmd текст',
  'alias': False},
 {'command': '/givepoints',
  'canonical': '/givepoints',
  'section': 'Экономика',
  'description': 'Передаёт баллы другому участнику.',
  'syntax': '/givepoints [пользователь] <сумма>',
  'alias': False},
 {'command': '/giverole',
  'canonical': '/giverole',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/giverole',
  'alias': False},
 {'command': '/goodbye',
  'canonical': '/goodbye',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/goodbye',
  'alias': False},
 {'command': '/goodbyeoff',
  'canonical': '/goodbyeoff',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/goodbyeoff',
  'alias': False},
 {'command': '/goodbyeon',
  'canonical': '/goodbyeon',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/goodbyeon',
  'alias': False},
 {'command': '/guard',
  'canonical': '/guard',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/guard',
  'alias': False},
 {'command': '/guide',
  'canonical': '/guide',
  'section': 'Справка',
  'description': 'Подробные главы по установке и настройке.',
  'syntax': '/guide [номер]',
  'alias': False},
 {'command': '/help',
  'canonical': '/help',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/help',
  'alias': False},
 {'command': '/history',
  'canonical': '/history',
  'section': 'Статистика',
  'description': 'Просмотр активности, истории и статистики.',
  'syntax': '/history',
  'alias': False},
 {'command': '/hug',
  'canonical': '/hug',
  'section': 'Социальные',
  'description': 'Обнимает выбранного участника.',
  'syntax': '/hug [пользователь]',
  'alias': False},
 {'command': '/id',
  'canonical': '/id',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/id',
  'alias': False},
 {'command': '/joke',
  'canonical': '/joke',
  'section': 'Игры',
  'description': 'Показывает короткую шутку.',
  'syntax': '/joke',
  'alias': False},
 {'command': '/kick',
  'canonical': '/kick',
  'section': 'Модерация',
  'description': 'Очистка сообщений или исключение участника.',
  'syntax': '/kick',
  'alias': False},
 {'command': '/kiss',
  'canonical': '/kiss',
  'section': 'Социальные',
  'description': 'Целует выбранного участника.',
  'syntax': '/kiss [пользователь]',
  'alias': False},
 {'command': '/length',
  'canonical': '/length',
  'section': 'Утилиты',
  'description': 'Считает символы, слова и строки.',
  'syntax': '/length <текст>',
  'alias': False},
 {'command': '/linkaction',
  'canonical': '/linkaction',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/linkaction',
  'alias': False},
 {'command': '/linkallow',
  'canonical': '/linkallow',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/linkallow',
  'alias': False},
 {'command': '/linkdel',
  'canonical': '/linkdel',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/linkdel',
  'alias': False},
 {'command': '/linklist',
  'canonical': '/linklist',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/linklist',
  'alias': False},
 {'command': '/lock',
  'canonical': '/lock',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/lock',
  'alias': False},
 {'command': '/lower',
  'canonical': '/lower',
  'section': 'Утилиты',
  'description': 'Переводит текст в нижний регистр.',
  'syntax': '/lower <текст>',
  'alias': False},
 {'command': '/manual',
  'canonical': '/manual',
  'section': 'Справка',
  'description': 'Подробная карточка выбранной команды.',
  'syntax': '/manual /команда',
  'alias': False},
 {'command': '/mataction',
  'canonical': '/mataction',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/mataction',
  'alias': False},
 {'command': '/members',
  'canonical': '/members',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/members',
  'alias': False},
 {'command': '/mute',
  'canonical': '/mute',
  'section': 'Модерация',
  'description': 'Работа с временными и постоянными мутами.',
  'syntax': '/mute',
  'alias': False},
 {'command': '/mutelist',
  'canonical': '/mutelist',
  'section': 'Модерация',
  'description': 'Работа с временными и постоянными мутами.',
  'syntax': '/mutelist',
  'alias': False},
 {'command': '/myreports',
  'canonical': '/myreports',
  'section': 'Жалобы',
  'description': 'Показывает жалобы, созданные пользователем.',
  'syntax': '/myreports',
  'alias': False},
 {'command': '/myrole',
  'canonical': '/myrole',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/myrole',
  'alias': False},
 {'command': '/nick',
  'canonical': '/nick',
  'section': 'Профиль',
  'description': 'Управление локальными никнеймами.',
  'syntax': '/nick',
  'alias': False},
 {'command': '/nicks',
  'canonical': '/nicks',
  'section': 'Профиль',
  'description': 'Управление локальными никнеймами.',
  'syntax': '/nicks',
  'alias': False},
 {'command': '/note',
  'canonical': '/note',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/note',
  'alias': False},
 {'command': '/noteadd',
  'canonical': '/noteadd',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/noteadd',
  'alias': False},
 {'command': '/notedel',
  'canonical': '/notedel',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/notedel',
  'alias': False},
 {'command': '/notelist',
  'canonical': '/notelist',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/notelist',
  'alias': False},
 {'command': '/owner',
  'canonical': '/owner',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/owner',
  'alias': False},
 {'command': '/owners',
  'canonical': '/owners',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/owners',
  'alias': False},
 {'command': '/pat',
  'canonical': '/pat',
  'section': 'Социальные',
  'description': 'Гладит выбранного участника.',
  'syntax': '/pat [пользователь]',
  'alias': False},
 {'command': '/permissions',
  'canonical': '/permissions',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/permissions',
  'alias': False},
 {'command': '/ping',
  'canonical': '/ping',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/ping',
  'alias': False},
 {'command': '/profile',
  'canonical': '/profile',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/profile',
  'alias': False},
 {'command': '/purge',
  'canonical': '/purge',
  'section': 'Модерация',
  'description': 'Очистка сообщений или исключение участника.',
  'syntax': '/purge',
  'alias': False},
 {'command': '/random',
  'canonical': '/random',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/random',
  'alias': False},
 {'command': '/rate',
  'canonical': '/rate',
  'section': 'Игры',
  'description': 'Стабильно оценивает указанную фразу от 0 до 100.',
  'syntax': '/rate <текст>',
  'alias': False},
 {'command': '/reason',
  'canonical': '/reason',
  'section': 'Статистика',
  'description': 'Просмотр активности, истории и статистики.',
  'syntax': '/reason',
  'alias': False},
 {'command': '/remind',
  'canonical': '/remind',
  'section': 'Напоминания',
  'description': 'Создаёт личное напоминание.',
  'syntax': '/remind <10m|2h|1d> <текст>',
  'alias': False},
 {'command': '/reminddel',
  'canonical': '/reminddel',
  'section': 'Напоминания',
  'description': 'Удаляет напоминание.',
  'syntax': '/reminddel <номер>',
  'alias': False},
 {'command': '/reminders',
  'canonical': '/reminders',
  'section': 'Напоминания',
  'description': 'Показывает активные напоминания.',
  'syntax': '/reminders',
  'alias': False},
 {'command': '/report',
  'canonical': '/report',
  'section': 'Жалобы',
  'description': 'Создаёт жалобу на участника.',
  'syntax': '/report [пользователь] <причина>',
  'alias': False},
 {'command': '/reportclose',
  'canonical': '/reportclose',
  'section': 'Жалобы',
  'description': 'Закрывает жалобу с комментарием.',
  'syntax': '/reportclose <номер> [комментарий]',
  'alias': False},
 {'command': '/reports',
  'canonical': '/reports',
  'section': 'Жалобы',
  'description': 'Показывает очередь открытых или закрытых жалоб.',
  'syntax': '/reports [open|closed]',
  'alias': False},
 {'command': '/reverse',
  'canonical': '/reverse',
  'section': 'Утилиты',
  'description': 'Разворачивает текст.',
  'syntax': '/reverse <текст>',
  'alias': False},
 {'command': '/rolecreate',
  'canonical': '/rolecreate',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/rolecreate',
  'alias': False},
 {'command': '/roledelete',
  'canonical': '/roledelete',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/roledelete',
  'alias': False},
 {'command': '/rolelevel',
  'canonical': '/rolelevel',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/rolelevel',
  'alias': False},
 {'command': '/roleperm',
  'canonical': '/roleperm',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/roleperm',
  'alias': False},
 {'command': '/roles',
  'canonical': '/roles',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/roles',
  'alias': False},
 {'command': '/rules',
  'canonical': '/rules',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/rules',
  'alias': False},
 {'command': '/say',
  'canonical': '/say',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/say',
  'alias': False},
 {'command': '/seen',
  'canonical': '/seen',
  'section': 'Статистика',
  'description': 'Просмотр активности, истории и статистики.',
  'syntax': '/seen',
  'alias': False},
 {'command': '/setbio',
  'canonical': '/setbio',
  'section': 'Профиль',
  'description': 'Устанавливает описание профиля.',
  'syntax': '/setbio <текст>',
  'alias': False},
 {'command': '/setgoodbye',
  'canonical': '/setgoodbye',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/setgoodbye',
  'alias': False},
 {'command': '/setrules',
  'canonical': '/setrules',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/setrules',
  'alias': False},
 {'command': '/settimezone',
  'canonical': '/settimezone',
  'section': 'Профиль',
  'description': 'Устанавливает часовой пояс UTC.',
  'syntax': '/settimezone <-12..14>',
  'alias': False},
 {'command': '/settings',
  'canonical': '/settings',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/settings',
  'alias': False},
 {'command': '/setup',
  'canonical': '/setup',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/setup',
  'alias': False},
 {'command': '/setwarnlimit',
  'canonical': '/setwarnlimit',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/setwarnlimit',
  'alias': False},
 {'command': '/setwelcome',
  'canonical': '/setwelcome',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/setwelcome',
  'alias': False},
 {'command': '/slap',
  'canonical': '/slap',
  'section': 'Социальные',
  'description': 'Шутливо даёт пощёчину выбранному участнику.',
  'syntax': '/slap [пользователь]',
  'alias': False},
 {'command': '/slowmode',
  'canonical': '/slowmode',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/slowmode',
  'alias': False},
 {'command': '/slowmodeoff',
  'canonical': '/slowmodeoff',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/slowmodeoff',
  'alias': False},
 {'command': '/spamlimit',
  'canonical': '/spamlimit',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/spamlimit',
  'alias': False},
 {'command': '/staff',
  'canonical': '/staff',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/staff',
  'alias': False},
 {'command': '/stats',
  'canonical': '/stats',
  'section': 'Статистика',
  'description': 'Просмотр активности, истории и статистики.',
  'syntax': '/stats',
  'alias': False},
 {'command': '/status',
  'canonical': '/status',
  'section': 'Статистика',
  'description': 'Просмотр активности, истории и статистики.',
  'syntax': '/status',
  'alias': False},
 {'command': '/takerole',
  'canonical': '/takerole',
  'section': 'Роли и владельцы',
  'description': 'Управление иерархией, персоналом, ролями и разрешениями.',
  'syntax': '/takerole',
  'alias': False},
 {'command': '/time',
  'canonical': '/time',
  'section': 'Профиль',
  'description': 'Показывает локальное время пользователя.',
  'syntax': '/time [пользователь]',
  'alias': False},
 {'command': '/title',
  'canonical': '/title',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/title',
  'alias': False},
 {'command': '/top',
  'canonical': '/top',
  'section': 'Статистика',
  'description': 'Просмотр активности, истории и статистики.',
  'syntax': '/top',
  'alias': False},
 {'command': '/topbalance',
  'canonical': '/topbalance',
  'section': 'Экономика',
  'description': 'Показывает лидеров беседы по баллам.',
  'syntax': '/topbalance',
  'alias': False},
 {'command': '/unban',
  'canonical': '/unban',
  'section': 'Модерация',
  'description': 'Работа с бан-листом и исключением участников.',
  'syntax': '/unban',
  'alias': False},
 {'command': '/unlock',
  'canonical': '/unlock',
  'section': 'Защита',
  'description': 'Настройка автоматической защиты конференции.',
  'syntax': '/unlock',
  'alias': False},
 {'command': '/unmute',
  'canonical': '/unmute',
  'section': 'Модерация',
  'description': 'Работа с временными и постоянными мутами.',
  'syntax': '/unmute',
  'alias': False},
 {'command': '/unnick',
  'canonical': '/unnick',
  'section': 'Профиль',
  'description': 'Управление локальными никнеймами.',
  'syntax': '/unnick',
  'alias': False},
 {'command': '/unwarn',
  'canonical': '/unwarn',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/unwarn',
  'alias': False},
 {'command': '/upper',
  'canonical': '/upper',
  'section': 'Утилиты',
  'description': 'Переводит текст в верхний регистр.',
  'syntax': '/upper <текст>',
  'alias': False},
 {'command': '/uptime',
  'canonical': '/uptime',
  'section': 'Утилиты',
  'description': 'Показывает время работы процесса.',
  'syntax': '/uptime',
  'alias': False},
 {'command': '/warn',
  'canonical': '/warn',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/warn',
  'alias': False},
 {'command': '/warnlist',
  'canonical': '/warnlist',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/warnlist',
  'alias': False},
 {'command': '/warns',
  'canonical': '/warns',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/warns',
  'alias': False},
 {'command': '/welcome',
  'canonical': '/welcome',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/welcome',
  'alias': False},
 {'command': '/welcomeoff',
  'canonical': '/welcomeoff',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/welcomeoff',
  'alias': False},
 {'command': '/welcomeon',
  'canonical': '/welcomeon',
  'section': 'Контент',
  'description': 'Настройка правил, шаблонов и информационных материалов.',
  'syntax': '/welcomeon',
  'alias': False},
 {'command': '/бан',
  'canonical': '/бан',
  'section': 'Модерация',
  'description': 'Работа с бан-листом и исключением участников.',
  'syntax': '/бан',
  'alias': False},
 {'command': '/банлист',
  'canonical': '/банлист',
  'section': 'Модерация',
  'description': 'Работа с бан-листом и исключением участников.',
  'syntax': '/банлист',
  'alias': False},
 {'command': '/владельцы',
  'canonical': '/владельцы',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/владельцы',
  'alias': False},
 {'command': '/кик',
  'canonical': '/кик',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/кик',
  'alias': False},
 {'command': '/команды',
  'canonical': '/команды',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/команды',
  'alias': False},
 {'command': '/мут',
  'canonical': '/мут',
  'section': 'Модерация',
  'description': 'Работа с временными и постоянными мутами.',
  'syntax': '/мут',
  'alias': False},
 {'command': '/мутлист',
  'canonical': '/мутлист',
  'section': 'Модерация',
  'description': 'Работа с временными и постоянными мутами.',
  'syntax': '/мутлист',
  'alias': False},
 {'command': '/настройка',
  'canonical': '/настройка',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/настройка',
  'alias': False},
 {'command': '/ник',
  'canonical': '/ник',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/ник',
  'alias': False},
 {'command': '/персонал',
  'canonical': '/персонал',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/персонал',
  'alias': False},
 {'command': '/помощь',
  'canonical': '/помощь',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/помощь',
  'alias': False},
 {'command': '/правила',
  'canonical': '/правила',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/правила',
  'alias': False},
 {'command': '/пред',
  'canonical': '/пред',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/пред',
  'alias': False},
 {'command': '/предлист',
  'canonical': '/предлист',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/предлист',
  'alias': False},
 {'command': '/преды',
  'canonical': '/преды',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/преды',
  'alias': False},
 {'command': '/профиль',
  'canonical': '/профиль',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/профиль',
  'alias': False},
 {'command': '/разбан',
  'canonical': '/разбан',
  'section': 'Модерация',
  'description': 'Работа с бан-листом и исключением участников.',
  'syntax': '/разбан',
  'alias': False},
 {'command': '/размут',
  'canonical': '/размут',
  'section': 'Модерация',
  'description': 'Работа с временными и постоянными мутами.',
  'syntax': '/размут',
  'alias': False},
 {'command': '/роли',
  'canonical': '/роли',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/роли',
  'alias': False},
 {'command': '/снятьник',
  'canonical': '/снятьник',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/снятьник',
  'alias': False},
 {'command': '/снятьпред',
  'canonical': '/снятьпред',
  'section': 'Модерация',
  'description': 'Работа с предупреждениями и причинами нарушений.',
  'syntax': '/снятьпред',
  'alias': False},
 {'command': '/старт',
  'canonical': '/старт',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/старт',
  'alias': False},
 {'command': '/статистика',
  'canonical': '/статистика',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/статистика',
  'alias': False},
 {'command': '/топ',
  'canonical': '/топ',
  'section': 'Основные',
  'description': 'Рабочая команда управления или получения информации в конференции.',
  'syntax': '/топ',
  'alias': False}]
GRAND_COMMAND_MANUAL = {'/8ball': 'КАРТОЧКА КОМАНДЫ №1\n'
           'Команда: /8ball\n'
           'Раздел: Игры\n'
           'Синтаксис: /8ball <вопрос>\n'
           '\n'
           'Описание: Отвечает на вопрос в стиле магического шара.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/about': 'КАРТОЧКА КОМАНДЫ №2\n'
           'Команда: /about\n'
           'Раздел: Основные\n'
           'Синтаксис: /about\n'
           '\n'
           'Описание: Рабочая команда управления или получения информации в конференции.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/addowner': 'КАРТОЧКА КОМАНДЫ №3\n'
              'Команда: /addowner\n'
              'Раздел: Роли и владельцы\n'
              'Синтаксис: /addowner\n'
              '\n'
              'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/announce': 'КАРТОЧКА КОМАНДЫ №4\n'
              'Команда: /announce\n'
              'Раздел: Контент\n'
              'Синтаксис: /announce\n'
              '\n'
              'Описание: Настройка правил, шаблонов и информационных материалов.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/anticaps': 'КАРТОЧКА КОМАНДЫ №5\n'
              'Команда: /anticaps\n'
              'Раздел: Защита\n'
              'Синтаксис: /anticaps\n'
              '\n'
              'Описание: Настройка автоматической защиты конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/antilink': 'КАРТОЧКА КОМАНДЫ №6\n'
              'Команда: /antilink\n'
              'Раздел: Защита\n'
              'Синтаксис: /antilink\n'
              '\n'
              'Описание: Настройка автоматической защиты конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/antimat': 'КАРТОЧКА КОМАНДЫ №7\n'
             'Команда: /antimat\n'
             'Раздел: Основные\n'
             'Синтаксис: /antimat\n'
             '\n'
             'Описание: Рабочая команда управления или получения информации в конференции.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/antispam': 'КАРТОЧКА КОМАНДЫ №8\n'
              'Команда: /antispam\n'
              'Раздел: Защита\n'
              'Синтаксис: /antispam\n'
              '\n'
              'Описание: Настройка автоматической защиты конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/badwordadd': 'КАРТОЧКА КОМАНДЫ №9\n'
                'Команда: /badwordadd\n'
                'Раздел: Защита\n'
                'Синтаксис: /badwordadd\n'
                '\n'
                'Описание: Настройка автоматической защиты конференции.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/badworddel': 'КАРТОЧКА КОМАНДЫ №10\n'
                'Команда: /badworddel\n'
                'Раздел: Защита\n'
                'Синтаксис: /badworddel\n'
                '\n'
                'Описание: Настройка автоматической защиты конференции.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/badwords': 'КАРТОЧКА КОМАНДЫ №11\n'
              'Команда: /badwords\n'
              'Раздел: Защита\n'
              'Синтаксис: /badwords\n'
              '\n'
              'Описание: Настройка автоматической защиты конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/balance': 'КАРТОЧКА КОМАНДЫ №12\n'
             'Команда: /balance\n'
             'Раздел: Экономика\n'
             'Синтаксис: /balance [пользователь]\n'
             '\n'
             'Описание: Показывает баланс баллов пользователя.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/ban': 'КАРТОЧКА КОМАНДЫ №13\n'
         'Команда: /ban\n'
         'Раздел: Модерация\n'
         'Синтаксис: /ban\n'
         '\n'
         'Описание: Работа с бан-листом и исключением участников.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/banlist': 'КАРТОЧКА КОМАНДЫ №14\n'
             'Команда: /banlist\n'
             'Раздел: Модерация\n'
             'Синтаксис: /banlist\n'
             '\n'
             'Описание: Работа с бан-листом и исключением участников.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/bio': 'КАРТОЧКА КОМАНДЫ №15\n'
         'Команда: /bio\n'
         'Раздел: Профиль\n'
         'Синтаксис: /bio [пользователь]\n'
         '\n'
         'Описание: Показывает описание профиля.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/calc': 'КАРТОЧКА КОМАНДЫ №16\n'
          'Команда: /calc\n'
          'Раздел: Утилиты\n'
          'Синтаксис: /calc <выражение>\n'
          '\n'
          'Описание: Безопасно вычисляет арифметическое выражение.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/capslimit': 'КАРТОЧКА КОМАНДЫ №17\n'
               'Команда: /capslimit\n'
               'Раздел: Защита\n'
               'Синтаксис: /capslimit\n'
               '\n'
               'Описание: Настройка автоматической защиты конференции.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/chatid': 'КАРТОЧКА КОМАНДЫ №18\n'
            'Команда: /chatid\n'
            'Раздел: Основные\n'
            'Синтаксис: /chatid\n'
            '\n'
            'Описание: Рабочая команда управления или получения информации в конференции.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/choose': 'КАРТОЧКА КОМАНДЫ №19\n'
            'Команда: /choose\n'
            'Раздел: Игры\n'
            'Синтаксис: /choose вариант 1 | вариант 2\n'
            '\n'
            'Описание: Выбирает один из вариантов, разделённых символом |.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/clear': 'КАРТОЧКА КОМАНДЫ №20\n'
           'Команда: /clear\n'
           'Раздел: Модерация\n'
           'Синтаксис: /clear\n'
           '\n'
           'Описание: Очистка сообщений или исключение участника.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/clearroles': 'КАРТОЧКА КОМАНДЫ №21\n'
                'Команда: /clearroles\n'
                'Раздел: Роли и владельцы\n'
                'Синтаксис: /clearroles\n'
                '\n'
                'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/clearwarns': 'КАРТОЧКА КОМАНДЫ №22\n'
                'Команда: /clearwarns\n'
                'Раздел: Модерация\n'
                'Синтаксис: /clearwarns\n'
                '\n'
                'Описание: Работа с предупреждениями и причинами нарушений.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/coin': 'КАРТОЧКА КОМАНДЫ №23\n'
          'Команда: /coin\n'
          'Раздел: Основные\n'
          'Синтаксис: /coin\n'
          '\n'
          'Описание: Рабочая команда управления или получения информации в конференции.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/commands': 'КАРТОЧКА КОМАНДЫ №24\n'
              'Команда: /commands\n'
              'Раздел: Справка\n'
              'Синтаксис: /commands [страница]\n'
              '\n'
              'Описание: Постраничный каталог всех команд и алиасов.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/customadd': 'КАРТОЧКА КОМАНДЫ №25\n'
               'Команда: /customadd\n'
               'Раздел: Пользовательские команды\n'
               'Синтаксис: /customadd /команда <ответ>\n'
               '\n'
               'Описание: Создаёт локальную команду с текстовым ответом.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/customdel': 'КАРТОЧКА КОМАНДЫ №26\n'
               'Команда: /customdel\n'
               'Раздел: Пользовательские команды\n'
               'Синтаксис: /customdel /команда\n'
               '\n'
               'Описание: Удаляет локальную команду.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/customlist': 'КАРТОЧКА КОМАНДЫ №27\n'
                'Команда: /customlist\n'
                'Раздел: Пользовательские команды\n'
                'Синтаксис: /customlist\n'
                '\n'
                'Описание: Показывает локальные команды.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/daily': 'КАРТОЧКА КОМАНДЫ №28\n'
           'Команда: /daily\n'
           'Раздел: Экономика\n'
           'Синтаксис: /daily\n'
           '\n'
           'Описание: Выдаёт ежедневную награду и увеличивает серию.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/delbio': 'КАРТОЧКА КОМАНДЫ №29\n'
            'Команда: /delbio\n'
            'Раздел: Профиль\n'
            'Синтаксис: /delbio\n'
            '\n'
            'Описание: Удаляет описание профиля.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/delowner': 'КАРТОЧКА КОМАНДЫ №30\n'
              'Команда: /delowner\n'
              'Раздел: Роли и владельцы\n'
              'Синтаксис: /delowner\n'
              '\n'
              'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/delrules': 'КАРТОЧКА КОМАНДЫ №31\n'
              'Команда: /delrules\n'
              'Раздел: Контент\n'
              'Синтаксис: /delrules\n'
              '\n'
              'Описание: Настройка правил, шаблонов и информационных материалов.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/dice': 'КАРТОЧКА КОМАНДЫ №32\n'
          'Команда: /dice\n'
          'Раздел: Основные\n'
          'Синтаксис: /dice\n'
          '\n'
          'Описание: Рабочая команда управления или получения информации в конференции.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/fact': 'КАРТОЧКА КОМАНДЫ №33\n'
          'Команда: /fact\n'
          'Раздел: Игры\n'
          'Синтаксис: /fact\n'
          '\n'
          'Описание: Показывает случайный познавательный факт.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/findcmd': 'КАРТОЧКА КОМАНДЫ №34\n'
             'Команда: /findcmd\n'
             'Раздел: Справка\n'
             'Синтаксис: /findcmd текст\n'
             '\n'
             'Описание: Поиск команды по названию, синтаксису и описанию.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/givepoints': 'КАРТОЧКА КОМАНДЫ №35\n'
                'Команда: /givepoints\n'
                'Раздел: Экономика\n'
                'Синтаксис: /givepoints [пользователь] <сумма>\n'
                '\n'
                'Описание: Передаёт баллы другому участнику.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/giverole': 'КАРТОЧКА КОМАНДЫ №36\n'
              'Команда: /giverole\n'
              'Раздел: Роли и владельцы\n'
              'Синтаксис: /giverole\n'
              '\n'
              'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/goodbye': 'КАРТОЧКА КОМАНДЫ №37\n'
             'Команда: /goodbye\n'
             'Раздел: Контент\n'
             'Синтаксис: /goodbye\n'
             '\n'
             'Описание: Настройка правил, шаблонов и информационных материалов.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/goodbyeoff': 'КАРТОЧКА КОМАНДЫ №38\n'
                'Команда: /goodbyeoff\n'
                'Раздел: Контент\n'
                'Синтаксис: /goodbyeoff\n'
                '\n'
                'Описание: Настройка правил, шаблонов и информационных материалов.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/goodbyeon': 'КАРТОЧКА КОМАНДЫ №39\n'
               'Команда: /goodbyeon\n'
               'Раздел: Контент\n'
               'Синтаксис: /goodbyeon\n'
               '\n'
               'Описание: Настройка правил, шаблонов и информационных материалов.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/guard': 'КАРТОЧКА КОМАНДЫ №40\n'
           'Команда: /guard\n'
           'Раздел: Защита\n'
           'Синтаксис: /guard\n'
           '\n'
           'Описание: Настройка автоматической защиты конференции.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/guide': 'КАРТОЧКА КОМАНДЫ №41\n'
           'Команда: /guide\n'
           'Раздел: Справка\n'
           'Синтаксис: /guide [номер]\n'
           '\n'
           'Описание: Подробные главы по установке и настройке.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/help': 'КАРТОЧКА КОМАНДЫ №42\n'
          'Команда: /help\n'
          'Раздел: Основные\n'
          'Синтаксис: /help\n'
          '\n'
          'Описание: Рабочая команда управления или получения информации в конференции.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/history': 'КАРТОЧКА КОМАНДЫ №43\n'
             'Команда: /history\n'
             'Раздел: Статистика\n'
             'Синтаксис: /history\n'
             '\n'
             'Описание: Просмотр активности, истории и статистики.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/hug': 'КАРТОЧКА КОМАНДЫ №44\n'
         'Команда: /hug\n'
         'Раздел: Социальные\n'
         'Синтаксис: /hug [пользователь]\n'
         '\n'
         'Описание: Обнимает выбранного участника.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/id': 'КАРТОЧКА КОМАНДЫ №45\n'
        'Команда: /id\n'
        'Раздел: Основные\n'
        'Синтаксис: /id\n'
        '\n'
        'Описание: Рабочая команда управления или получения информации в конференции.\n'
        '\n'
        'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
        'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
        'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
        'права сообщества и консоль процесса.',
 '/joke': 'КАРТОЧКА КОМАНДЫ №46\n'
          'Команда: /joke\n'
          'Раздел: Игры\n'
          'Синтаксис: /joke\n'
          '\n'
          'Описание: Показывает короткую шутку.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/kick': 'КАРТОЧКА КОМАНДЫ №47\n'
          'Команда: /kick\n'
          'Раздел: Модерация\n'
          'Синтаксис: /kick\n'
          '\n'
          'Описание: Очистка сообщений или исключение участника.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/kiss': 'КАРТОЧКА КОМАНДЫ №48\n'
          'Команда: /kiss\n'
          'Раздел: Социальные\n'
          'Синтаксис: /kiss [пользователь]\n'
          '\n'
          'Описание: Целует выбранного участника.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/length': 'КАРТОЧКА КОМАНДЫ №49\n'
            'Команда: /length\n'
            'Раздел: Утилиты\n'
            'Синтаксис: /length <текст>\n'
            '\n'
            'Описание: Считает символы, слова и строки.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/linkaction': 'КАРТОЧКА КОМАНДЫ №50\n'
                'Команда: /linkaction\n'
                'Раздел: Защита\n'
                'Синтаксис: /linkaction\n'
                '\n'
                'Описание: Настройка автоматической защиты конференции.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/linkallow': 'КАРТОЧКА КОМАНДЫ №51\n'
               'Команда: /linkallow\n'
               'Раздел: Защита\n'
               'Синтаксис: /linkallow\n'
               '\n'
               'Описание: Настройка автоматической защиты конференции.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/linkdel': 'КАРТОЧКА КОМАНДЫ №52\n'
             'Команда: /linkdel\n'
             'Раздел: Защита\n'
             'Синтаксис: /linkdel\n'
             '\n'
             'Описание: Настройка автоматической защиты конференции.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/linklist': 'КАРТОЧКА КОМАНДЫ №53\n'
              'Команда: /linklist\n'
              'Раздел: Защита\n'
              'Синтаксис: /linklist\n'
              '\n'
              'Описание: Настройка автоматической защиты конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/lock': 'КАРТОЧКА КОМАНДЫ №54\n'
          'Команда: /lock\n'
          'Раздел: Защита\n'
          'Синтаксис: /lock\n'
          '\n'
          'Описание: Настройка автоматической защиты конференции.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/lower': 'КАРТОЧКА КОМАНДЫ №55\n'
           'Команда: /lower\n'
           'Раздел: Утилиты\n'
           'Синтаксис: /lower <текст>\n'
           '\n'
           'Описание: Переводит текст в нижний регистр.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/manual': 'КАРТОЧКА КОМАНДЫ №56\n'
            'Команда: /manual\n'
            'Раздел: Справка\n'
            'Синтаксис: /manual /команда\n'
            '\n'
            'Описание: Подробная карточка выбранной команды.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/mataction': 'КАРТОЧКА КОМАНДЫ №57\n'
               'Команда: /mataction\n'
               'Раздел: Основные\n'
               'Синтаксис: /mataction\n'
               '\n'
               'Описание: Рабочая команда управления или получения информации в конференции.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/members': 'КАРТОЧКА КОМАНДЫ №58\n'
             'Команда: /members\n'
             'Раздел: Основные\n'
             'Синтаксис: /members\n'
             '\n'
             'Описание: Рабочая команда управления или получения информации в конференции.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/mute': 'КАРТОЧКА КОМАНДЫ №59\n'
          'Команда: /mute\n'
          'Раздел: Модерация\n'
          'Синтаксис: /mute\n'
          '\n'
          'Описание: Работа с временными и постоянными мутами.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/mutelist': 'КАРТОЧКА КОМАНДЫ №60\n'
              'Команда: /mutelist\n'
              'Раздел: Модерация\n'
              'Синтаксис: /mutelist\n'
              '\n'
              'Описание: Работа с временными и постоянными мутами.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/myreports': 'КАРТОЧКА КОМАНДЫ №61\n'
               'Команда: /myreports\n'
               'Раздел: Жалобы\n'
               'Синтаксис: /myreports\n'
               '\n'
               'Описание: Показывает жалобы, созданные пользователем.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/myrole': 'КАРТОЧКА КОМАНДЫ №62\n'
            'Команда: /myrole\n'
            'Раздел: Роли и владельцы\n'
            'Синтаксис: /myrole\n'
            '\n'
            'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/nick': 'КАРТОЧКА КОМАНДЫ №63\n'
          'Команда: /nick\n'
          'Раздел: Профиль\n'
          'Синтаксис: /nick\n'
          '\n'
          'Описание: Управление локальными никнеймами.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/nicks': 'КАРТОЧКА КОМАНДЫ №64\n'
           'Команда: /nicks\n'
           'Раздел: Профиль\n'
           'Синтаксис: /nicks\n'
           '\n'
           'Описание: Управление локальными никнеймами.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/note': 'КАРТОЧКА КОМАНДЫ №65\n'
          'Команда: /note\n'
          'Раздел: Контент\n'
          'Синтаксис: /note\n'
          '\n'
          'Описание: Настройка правил, шаблонов и информационных материалов.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/noteadd': 'КАРТОЧКА КОМАНДЫ №66\n'
             'Команда: /noteadd\n'
             'Раздел: Контент\n'
             'Синтаксис: /noteadd\n'
             '\n'
             'Описание: Настройка правил, шаблонов и информационных материалов.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/notedel': 'КАРТОЧКА КОМАНДЫ №67\n'
             'Команда: /notedel\n'
             'Раздел: Контент\n'
             'Синтаксис: /notedel\n'
             '\n'
             'Описание: Настройка правил, шаблонов и информационных материалов.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/notelist': 'КАРТОЧКА КОМАНДЫ №68\n'
              'Команда: /notelist\n'
              'Раздел: Контент\n'
              'Синтаксис: /notelist\n'
              '\n'
              'Описание: Настройка правил, шаблонов и информационных материалов.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/owner': 'КАРТОЧКА КОМАНДЫ №69\n'
           'Команда: /owner\n'
           'Раздел: Роли и владельцы\n'
           'Синтаксис: /owner\n'
           '\n'
           'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/owners': 'КАРТОЧКА КОМАНДЫ №70\n'
            'Команда: /owners\n'
            'Раздел: Роли и владельцы\n'
            'Синтаксис: /owners\n'
            '\n'
            'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/pat': 'КАРТОЧКА КОМАНДЫ №71\n'
         'Команда: /pat\n'
         'Раздел: Социальные\n'
         'Синтаксис: /pat [пользователь]\n'
         '\n'
         'Описание: Гладит выбранного участника.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/permissions': 'КАРТОЧКА КОМАНДЫ №72\n'
                 'Команда: /permissions\n'
                 'Раздел: Основные\n'
                 'Синтаксис: /permissions\n'
                 '\n'
                 'Описание: Рабочая команда управления или получения информации в конференции.\n'
                 '\n'
                 'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                 'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                 'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                 'событие входящего сообщения, права сообщества и консоль процесса.',
 '/ping': 'КАРТОЧКА КОМАНДЫ №73\n'
          'Команда: /ping\n'
          'Раздел: Основные\n'
          'Синтаксис: /ping\n'
          '\n'
          'Описание: Рабочая команда управления или получения информации в конференции.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/profile': 'КАРТОЧКА КОМАНДЫ №74\n'
             'Команда: /profile\n'
             'Раздел: Основные\n'
             'Синтаксис: /profile\n'
             '\n'
             'Описание: Рабочая команда управления или получения информации в конференции.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/purge': 'КАРТОЧКА КОМАНДЫ №75\n'
           'Команда: /purge\n'
           'Раздел: Модерация\n'
           'Синтаксис: /purge\n'
           '\n'
           'Описание: Очистка сообщений или исключение участника.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/random': 'КАРТОЧКА КОМАНДЫ №76\n'
            'Команда: /random\n'
            'Раздел: Основные\n'
            'Синтаксис: /random\n'
            '\n'
            'Описание: Рабочая команда управления или получения информации в конференции.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/rate': 'КАРТОЧКА КОМАНДЫ №77\n'
          'Команда: /rate\n'
          'Раздел: Игры\n'
          'Синтаксис: /rate <текст>\n'
          '\n'
          'Описание: Стабильно оценивает указанную фразу от 0 до 100.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/reason': 'КАРТОЧКА КОМАНДЫ №78\n'
            'Команда: /reason\n'
            'Раздел: Статистика\n'
            'Синтаксис: /reason\n'
            '\n'
            'Описание: Просмотр активности, истории и статистики.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/remind': 'КАРТОЧКА КОМАНДЫ №79\n'
            'Команда: /remind\n'
            'Раздел: Напоминания\n'
            'Синтаксис: /remind <10m|2h|1d> <текст>\n'
            '\n'
            'Описание: Создаёт личное напоминание.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/reminddel': 'КАРТОЧКА КОМАНДЫ №80\n'
               'Команда: /reminddel\n'
               'Раздел: Напоминания\n'
               'Синтаксис: /reminddel <номер>\n'
               '\n'
               'Описание: Удаляет напоминание.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/reminders': 'КАРТОЧКА КОМАНДЫ №81\n'
               'Команда: /reminders\n'
               'Раздел: Напоминания\n'
               'Синтаксис: /reminders\n'
               '\n'
               'Описание: Показывает активные напоминания.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/report': 'КАРТОЧКА КОМАНДЫ №82\n'
            'Команда: /report\n'
            'Раздел: Жалобы\n'
            'Синтаксис: /report [пользователь] <причина>\n'
            '\n'
            'Описание: Создаёт жалобу на участника.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/reportclose': 'КАРТОЧКА КОМАНДЫ №83\n'
                 'Команда: /reportclose\n'
                 'Раздел: Жалобы\n'
                 'Синтаксис: /reportclose <номер> [комментарий]\n'
                 '\n'
                 'Описание: Закрывает жалобу с комментарием.\n'
                 '\n'
                 'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                 'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                 'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                 'событие входящего сообщения, права сообщества и консоль процесса.',
 '/reports': 'КАРТОЧКА КОМАНДЫ №84\n'
             'Команда: /reports\n'
             'Раздел: Жалобы\n'
             'Синтаксис: /reports [open|closed]\n'
             '\n'
             'Описание: Показывает очередь открытых или закрытых жалоб.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/reverse': 'КАРТОЧКА КОМАНДЫ №85\n'
             'Команда: /reverse\n'
             'Раздел: Утилиты\n'
             'Синтаксис: /reverse <текст>\n'
             '\n'
             'Описание: Разворачивает текст.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/rolecreate': 'КАРТОЧКА КОМАНДЫ №86\n'
                'Команда: /rolecreate\n'
                'Раздел: Роли и владельцы\n'
                'Синтаксис: /rolecreate\n'
                '\n'
                'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/roledelete': 'КАРТОЧКА КОМАНДЫ №87\n'
                'Команда: /roledelete\n'
                'Раздел: Роли и владельцы\n'
                'Синтаксис: /roledelete\n'
                '\n'
                'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/rolelevel': 'КАРТОЧКА КОМАНДЫ №88\n'
               'Команда: /rolelevel\n'
               'Раздел: Роли и владельцы\n'
               'Синтаксис: /rolelevel\n'
               '\n'
               'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/roleperm': 'КАРТОЧКА КОМАНДЫ №89\n'
              'Команда: /roleperm\n'
              'Раздел: Роли и владельцы\n'
              'Синтаксис: /roleperm\n'
              '\n'
              'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/roles': 'КАРТОЧКА КОМАНДЫ №90\n'
           'Команда: /roles\n'
           'Раздел: Роли и владельцы\n'
           'Синтаксис: /roles\n'
           '\n'
           'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/rules': 'КАРТОЧКА КОМАНДЫ №91\n'
           'Команда: /rules\n'
           'Раздел: Контент\n'
           'Синтаксис: /rules\n'
           '\n'
           'Описание: Настройка правил, шаблонов и информационных материалов.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/say': 'КАРТОЧКА КОМАНДЫ №92\n'
         'Команда: /say\n'
         'Раздел: Контент\n'
         'Синтаксис: /say\n'
         '\n'
         'Описание: Настройка правил, шаблонов и информационных материалов.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/seen': 'КАРТОЧКА КОМАНДЫ №93\n'
          'Команда: /seen\n'
          'Раздел: Статистика\n'
          'Синтаксис: /seen\n'
          '\n'
          'Описание: Просмотр активности, истории и статистики.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/setbio': 'КАРТОЧКА КОМАНДЫ №94\n'
            'Команда: /setbio\n'
            'Раздел: Профиль\n'
            'Синтаксис: /setbio <текст>\n'
            '\n'
            'Описание: Устанавливает описание профиля.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/setgoodbye': 'КАРТОЧКА КОМАНДЫ №95\n'
                'Команда: /setgoodbye\n'
                'Раздел: Контент\n'
                'Синтаксис: /setgoodbye\n'
                '\n'
                'Описание: Настройка правил, шаблонов и информационных материалов.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/setrules': 'КАРТОЧКА КОМАНДЫ №96\n'
              'Команда: /setrules\n'
              'Раздел: Контент\n'
              'Синтаксис: /setrules\n'
              '\n'
              'Описание: Настройка правил, шаблонов и информационных материалов.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/settimezone': 'КАРТОЧКА КОМАНДЫ №97\n'
                 'Команда: /settimezone\n'
                 'Раздел: Профиль\n'
                 'Синтаксис: /settimezone <-12..14>\n'
                 '\n'
                 'Описание: Устанавливает часовой пояс UTC.\n'
                 '\n'
                 'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                 'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                 'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                 'событие входящего сообщения, права сообщества и консоль процесса.',
 '/settings': 'КАРТОЧКА КОМАНДЫ №98\n'
              'Команда: /settings\n'
              'Раздел: Основные\n'
              'Синтаксис: /settings\n'
              '\n'
              'Описание: Рабочая команда управления или получения информации в конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/setup': 'КАРТОЧКА КОМАНДЫ №99\n'
           'Команда: /setup\n'
           'Раздел: Основные\n'
           'Синтаксис: /setup\n'
           '\n'
           'Описание: Рабочая команда управления или получения информации в конференции.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/setwarnlimit': 'КАРТОЧКА КОМАНДЫ №100\n'
                  'Команда: /setwarnlimit\n'
                  'Раздел: Модерация\n'
                  'Синтаксис: /setwarnlimit\n'
                  '\n'
                  'Описание: Работа с предупреждениями и причинами нарушений.\n'
                  '\n'
                  'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                  'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                  'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                  'событие входящего сообщения, права сообщества и консоль процесса.',
 '/setwelcome': 'КАРТОЧКА КОМАНДЫ №101\n'
                'Команда: /setwelcome\n'
                'Раздел: Контент\n'
                'Синтаксис: /setwelcome\n'
                '\n'
                'Описание: Настройка правил, шаблонов и информационных материалов.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/slap': 'КАРТОЧКА КОМАНДЫ №102\n'
          'Команда: /slap\n'
          'Раздел: Социальные\n'
          'Синтаксис: /slap [пользователь]\n'
          '\n'
          'Описание: Шутливо даёт пощёчину выбранному участнику.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/slowmode': 'КАРТОЧКА КОМАНДЫ №103\n'
              'Команда: /slowmode\n'
              'Раздел: Защита\n'
              'Синтаксис: /slowmode\n'
              '\n'
              'Описание: Настройка автоматической защиты конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/slowmodeoff': 'КАРТОЧКА КОМАНДЫ №104\n'
                 'Команда: /slowmodeoff\n'
                 'Раздел: Защита\n'
                 'Синтаксис: /slowmodeoff\n'
                 '\n'
                 'Описание: Настройка автоматической защиты конференции.\n'
                 '\n'
                 'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                 'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                 'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                 'событие входящего сообщения, права сообщества и консоль процесса.',
 '/spamlimit': 'КАРТОЧКА КОМАНДЫ №105\n'
               'Команда: /spamlimit\n'
               'Раздел: Защита\n'
               'Синтаксис: /spamlimit\n'
               '\n'
               'Описание: Настройка автоматической защиты конференции.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/staff': 'КАРТОЧКА КОМАНДЫ №106\n'
           'Команда: /staff\n'
           'Раздел: Роли и владельцы\n'
           'Синтаксис: /staff\n'
           '\n'
           'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/stats': 'КАРТОЧКА КОМАНДЫ №107\n'
           'Команда: /stats\n'
           'Раздел: Статистика\n'
           'Синтаксис: /stats\n'
           '\n'
           'Описание: Просмотр активности, истории и статистики.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/status': 'КАРТОЧКА КОМАНДЫ №108\n'
            'Команда: /status\n'
            'Раздел: Статистика\n'
            'Синтаксис: /status\n'
            '\n'
            'Описание: Просмотр активности, истории и статистики.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/takerole': 'КАРТОЧКА КОМАНДЫ №109\n'
              'Команда: /takerole\n'
              'Раздел: Роли и владельцы\n'
              'Синтаксис: /takerole\n'
              '\n'
              'Описание: Управление иерархией, персоналом, ролями и разрешениями.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/time': 'КАРТОЧКА КОМАНДЫ №110\n'
          'Команда: /time\n'
          'Раздел: Профиль\n'
          'Синтаксис: /time [пользователь]\n'
          '\n'
          'Описание: Показывает локальное время пользователя.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/title': 'КАРТОЧКА КОМАНДЫ №111\n'
           'Команда: /title\n'
           'Раздел: Основные\n'
           'Синтаксис: /title\n'
           '\n'
           'Описание: Рабочая команда управления или получения информации в конференции.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/top': 'КАРТОЧКА КОМАНДЫ №112\n'
         'Команда: /top\n'
         'Раздел: Статистика\n'
         'Синтаксис: /top\n'
         '\n'
         'Описание: Просмотр активности, истории и статистики.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/topbalance': 'КАРТОЧКА КОМАНДЫ №113\n'
                'Команда: /topbalance\n'
                'Раздел: Экономика\n'
                'Синтаксис: /topbalance\n'
                '\n'
                'Описание: Показывает лидеров беседы по баллам.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/unban': 'КАРТОЧКА КОМАНДЫ №114\n'
           'Команда: /unban\n'
           'Раздел: Модерация\n'
           'Синтаксис: /unban\n'
           '\n'
           'Описание: Работа с бан-листом и исключением участников.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/unlock': 'КАРТОЧКА КОМАНДЫ №115\n'
            'Команда: /unlock\n'
            'Раздел: Защита\n'
            'Синтаксис: /unlock\n'
            '\n'
            'Описание: Настройка автоматической защиты конференции.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/unmute': 'КАРТОЧКА КОМАНДЫ №116\n'
            'Команда: /unmute\n'
            'Раздел: Модерация\n'
            'Синтаксис: /unmute\n'
            '\n'
            'Описание: Работа с временными и постоянными мутами.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/unnick': 'КАРТОЧКА КОМАНДЫ №117\n'
            'Команда: /unnick\n'
            'Раздел: Профиль\n'
            'Синтаксис: /unnick\n'
            '\n'
            'Описание: Управление локальными никнеймами.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/unwarn': 'КАРТОЧКА КОМАНДЫ №118\n'
            'Команда: /unwarn\n'
            'Раздел: Модерация\n'
            'Синтаксис: /unwarn\n'
            '\n'
            'Описание: Работа с предупреждениями и причинами нарушений.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/upper': 'КАРТОЧКА КОМАНДЫ №119\n'
           'Команда: /upper\n'
           'Раздел: Утилиты\n'
           'Синтаксис: /upper <текст>\n'
           '\n'
           'Описание: Переводит текст в верхний регистр.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/uptime': 'КАРТОЧКА КОМАНДЫ №120\n'
            'Команда: /uptime\n'
            'Раздел: Утилиты\n'
            'Синтаксис: /uptime\n'
            '\n'
            'Описание: Показывает время работы процесса.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/warn': 'КАРТОЧКА КОМАНДЫ №121\n'
          'Команда: /warn\n'
          'Раздел: Модерация\n'
          'Синтаксис: /warn\n'
          '\n'
          'Описание: Работа с предупреждениями и причинами нарушений.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/warnlist': 'КАРТОЧКА КОМАНДЫ №122\n'
              'Команда: /warnlist\n'
              'Раздел: Модерация\n'
              'Синтаксис: /warnlist\n'
              '\n'
              'Описание: Работа с предупреждениями и причинами нарушений.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/warns': 'КАРТОЧКА КОМАНДЫ №123\n'
           'Команда: /warns\n'
           'Раздел: Модерация\n'
           'Синтаксис: /warns\n'
           '\n'
           'Описание: Работа с предупреждениями и причинами нарушений.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/welcome': 'КАРТОЧКА КОМАНДЫ №124\n'
             'Команда: /welcome\n'
             'Раздел: Контент\n'
             'Синтаксис: /welcome\n'
             '\n'
             'Описание: Настройка правил, шаблонов и информационных материалов.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/welcomeoff': 'КАРТОЧКА КОМАНДЫ №125\n'
                'Команда: /welcomeoff\n'
                'Раздел: Контент\n'
                'Синтаксис: /welcomeoff\n'
                '\n'
                'Описание: Настройка правил, шаблонов и информационных материалов.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/welcomeon': 'КАРТОЧКА КОМАНДЫ №126\n'
               'Команда: /welcomeon\n'
               'Раздел: Контент\n'
               'Синтаксис: /welcomeon\n'
               '\n'
               'Описание: Настройка правил, шаблонов и информационных материалов.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/бан': 'КАРТОЧКА КОМАНДЫ №127\n'
         'Команда: /бан\n'
         'Раздел: Модерация\n'
         'Синтаксис: /бан\n'
         '\n'
         'Описание: Работа с бан-листом и исключением участников.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/банлист': 'КАРТОЧКА КОМАНДЫ №128\n'
             'Команда: /банлист\n'
             'Раздел: Модерация\n'
             'Синтаксис: /банлист\n'
             '\n'
             'Описание: Работа с бан-листом и исключением участников.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/владельцы': 'КАРТОЧКА КОМАНДЫ №129\n'
               'Команда: /владельцы\n'
               'Раздел: Основные\n'
               'Синтаксис: /владельцы\n'
               '\n'
               'Описание: Рабочая команда управления или получения информации в конференции.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/кик': 'КАРТОЧКА КОМАНДЫ №130\n'
         'Команда: /кик\n'
         'Раздел: Основные\n'
         'Синтаксис: /кик\n'
         '\n'
         'Описание: Рабочая команда управления или получения информации в конференции.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/команды': 'КАРТОЧКА КОМАНДЫ №131\n'
             'Команда: /команды\n'
             'Раздел: Основные\n'
             'Синтаксис: /команды\n'
             '\n'
             'Описание: Рабочая команда управления или получения информации в конференции.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/мут': 'КАРТОЧКА КОМАНДЫ №132\n'
         'Команда: /мут\n'
         'Раздел: Модерация\n'
         'Синтаксис: /мут\n'
         '\n'
         'Описание: Работа с временными и постоянными мутами.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/мутлист': 'КАРТОЧКА КОМАНДЫ №133\n'
             'Команда: /мутлист\n'
             'Раздел: Модерация\n'
             'Синтаксис: /мутлист\n'
             '\n'
             'Описание: Работа с временными и постоянными мутами.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/настройка': 'КАРТОЧКА КОМАНДЫ №134\n'
               'Команда: /настройка\n'
               'Раздел: Основные\n'
               'Синтаксис: /настройка\n'
               '\n'
               'Описание: Рабочая команда управления или получения информации в конференции.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/ник': 'КАРТОЧКА КОМАНДЫ №135\n'
         'Команда: /ник\n'
         'Раздел: Основные\n'
         'Синтаксис: /ник\n'
         '\n'
         'Описание: Рабочая команда управления или получения информации в конференции.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.',
 '/персонал': 'КАРТОЧКА КОМАНДЫ №136\n'
              'Команда: /персонал\n'
              'Раздел: Основные\n'
              'Синтаксис: /персонал\n'
              '\n'
              'Описание: Рабочая команда управления или получения информации в конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/помощь': 'КАРТОЧКА КОМАНДЫ №137\n'
            'Команда: /помощь\n'
            'Раздел: Основные\n'
            'Синтаксис: /помощь\n'
            '\n'
            'Описание: Рабочая команда управления или получения информации в конференции.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/правила': 'КАРТОЧКА КОМАНДЫ №138\n'
             'Команда: /правила\n'
             'Раздел: Основные\n'
             'Синтаксис: /правила\n'
             '\n'
             'Описание: Рабочая команда управления или получения информации в конференции.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/пред': 'КАРТОЧКА КОМАНДЫ №139\n'
          'Команда: /пред\n'
          'Раздел: Модерация\n'
          'Синтаксис: /пред\n'
          '\n'
          'Описание: Работа с предупреждениями и причинами нарушений.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/предлист': 'КАРТОЧКА КОМАНДЫ №140\n'
              'Команда: /предлист\n'
              'Раздел: Модерация\n'
              'Синтаксис: /предлист\n'
              '\n'
              'Описание: Работа с предупреждениями и причинами нарушений.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/преды': 'КАРТОЧКА КОМАНДЫ №141\n'
           'Команда: /преды\n'
           'Раздел: Модерация\n'
           'Синтаксис: /преды\n'
           '\n'
           'Описание: Работа с предупреждениями и причинами нарушений.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/профиль': 'КАРТОЧКА КОМАНДЫ №142\n'
             'Команда: /профиль\n'
             'Раздел: Основные\n'
             'Синтаксис: /профиль\n'
             '\n'
             'Описание: Рабочая команда управления или получения информации в конференции.\n'
             '\n'
             'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
             'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников '
             'сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего '
             'сообщения, права сообщества и консоль процесса.',
 '/разбан': 'КАРТОЧКА КОМАНДЫ №143\n'
            'Команда: /разбан\n'
            'Раздел: Модерация\n'
            'Синтаксис: /разбан\n'
            '\n'
            'Описание: Работа с бан-листом и исключением участников.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/размут': 'КАРТОЧКА КОМАНДЫ №144\n'
            'Команда: /размут\n'
            'Раздел: Модерация\n'
            'Синтаксис: /размут\n'
            '\n'
            'Описание: Работа с временными и постоянными мутами.\n'
            '\n'
            'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки '
            'и права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
            'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
            'права сообщества и консоль процесса.',
 '/роли': 'КАРТОЧКА КОМАНДЫ №145\n'
          'Команда: /роли\n'
          'Раздел: Основные\n'
          'Синтаксис: /роли\n'
          '\n'
          'Описание: Рабочая команда управления или получения информации в конференции.\n'
          '\n'
          'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
          'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
          'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
          'права сообщества и консоль процесса.',
 '/снятьник': 'КАРТОЧКА КОМАНДЫ №146\n'
              'Команда: /снятьник\n'
              'Раздел: Основные\n'
              'Синтаксис: /снятьник\n'
              '\n'
              'Описание: Рабочая команда управления или получения информации в конференции.\n'
              '\n'
              'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
              'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
              'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
              'событие входящего сообщения, права сообщества и консоль процесса.',
 '/снятьпред': 'КАРТОЧКА КОМАНДЫ №147\n'
               'Команда: /снятьпред\n'
               'Раздел: Модерация\n'
               'Синтаксис: /снятьпред\n'
               '\n'
               'Описание: Работа с предупреждениями и причинами нарушений.\n'
               '\n'
               'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
               'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
               'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
               'событие входящего сообщения, права сообщества и консоль процесса.',
 '/старт': 'КАРТОЧКА КОМАНДЫ №148\n'
           'Команда: /старт\n'
           'Раздел: Основные\n'
           'Синтаксис: /старт\n'
           '\n'
           'Описание: Рабочая команда управления или получения информации в конференции.\n'
           '\n'
           'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
           'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
           'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
           'права сообщества и консоль процесса.',
 '/статистика': 'КАРТОЧКА КОМАНДЫ №149\n'
                'Команда: /статистика\n'
                'Раздел: Основные\n'
                'Синтаксис: /статистика\n'
                '\n'
                'Описание: Рабочая команда управления или получения информации в конференции.\n'
                '\n'
                'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. '
                'Настройки и права действуют только в текущей конференции. Для удаления сообщений и исключения '
                'участников сообщество должно быть администратором. При проблемах проверьте токен, Bots Long Poll, '
                'событие входящего сообщения, права сообщества и консоль процесса.',
 '/топ': 'КАРТОЧКА КОМАНДЫ №150\n'
         'Команда: /топ\n'
         'Раздел: Основные\n'
         'Синтаксис: /топ\n'
         '\n'
         'Описание: Рабочая команда управления или получения информации в конференции.\n'
         '\n'
         'Пользователя можно указать ответом на сообщение, упоминанием, @id, ссылкой или числовым VK ID. Настройки и '
         'права действуют только в текущей конференции. Для удаления сообщений и исключения участников сообщество '
         'должно быть администратором. При проблемах проверьте токен, Bots Long Poll, событие входящего сообщения, '
         'права сообщества и консоль процесса.'}
GRAND_HELP_SECTIONS = {'Игры': ['/8ball', '/choose', '/fact', '/joke', '/rate'],
 'Основные': ['/about',
              '/antimat',
              '/chatid',
              '/coin',
              '/dice',
              '/help',
              '/id',
              '/mataction',
              '/members',
              '/permissions',
              '/ping',
              '/profile',
              '/random',
              '/settings',
              '/setup',
              '/title',
              '/владельцы',
              '/кик',
              '/команды',
              '/настройка',
              '/ник',
              '/персонал',
              '/помощь',
              '/правила',
              '/профиль',
              '/роли',
              '/снятьник',
              '/старт',
              '/статистика',
              '/топ'],
 'Роли и владельцы': ['/addowner',
                      '/clearroles',
                      '/delowner',
                      '/giverole',
                      '/myrole',
                      '/owner',
                      '/owners',
                      '/rolecreate',
                      '/roledelete',
                      '/rolelevel',
                      '/roleperm',
                      '/roles',
                      '/staff',
                      '/takerole'],
 'Контент': ['/announce',
             '/delrules',
             '/goodbye',
             '/goodbyeoff',
             '/goodbyeon',
             '/note',
             '/noteadd',
             '/notedel',
             '/notelist',
             '/rules',
             '/say',
             '/setgoodbye',
             '/setrules',
             '/setwelcome',
             '/welcome',
             '/welcomeoff',
             '/welcomeon'],
 'Защита': ['/anticaps',
            '/antilink',
            '/antispam',
            '/badwordadd',
            '/badworddel',
            '/badwords',
            '/capslimit',
            '/guard',
            '/linkaction',
            '/linkallow',
            '/linkdel',
            '/linklist',
            '/lock',
            '/slowmode',
            '/slowmodeoff',
            '/spamlimit',
            '/unlock'],
 'Экономика': ['/balance', '/daily', '/givepoints', '/topbalance'],
 'Модерация': ['/ban',
               '/banlist',
               '/clear',
               '/clearwarns',
               '/kick',
               '/mute',
               '/mutelist',
               '/purge',
               '/setwarnlimit',
               '/unban',
               '/unmute',
               '/unwarn',
               '/warn',
               '/warnlist',
               '/warns',
               '/бан',
               '/банлист',
               '/мут',
               '/мутлист',
               '/пред',
               '/предлист',
               '/преды',
               '/разбан',
               '/размут',
               '/снятьпред'],
 'Профиль': ['/bio', '/delbio', '/nick', '/nicks', '/setbio', '/settimezone', '/time', '/unnick'],
 'Утилиты': ['/calc', '/length', '/lower', '/reverse', '/upper', '/uptime'],
 'Справка': ['/commands', '/findcmd', '/guide', '/manual'],
 'Пользовательские команды': ['/customadd', '/customdel', '/customlist'],
 'Статистика': ['/history', '/reason', '/seen', '/stats', '/status', '/top'],
 'Социальные': ['/hug', '/kiss', '/pat', '/slap'],
 'Жалобы': ['/myreports', '/report', '/reportclose', '/reports'],
 'Напоминания': ['/remind', '/reminddel', '/reminders']}
GRAND_GUIDES = {1: 'РУКОВОДСТВО 1/12 — Первый запуск\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Первый запуск. Все настройки применяются отдельно к каждой конференции и не влияют на остальные чаты.',
 2: 'РУКОВОДСТВО 2/12 — Получение токена\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Получение токена. Все настройки применяются отдельно к каждой конференции и не влияют на остальные '
    'чаты.',
 3: 'РУКОВОДСТВО 3/12 — Bots Long Poll\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Bots Long Poll. Все настройки применяются отдельно к каждой конференции и не влияют на остальные '
    'чаты.',
 4: 'РУКОВОДСТВО 4/12 — Права администратора\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Права администратора. Все настройки применяются отдельно к каждой конференции и не влияют на '
    'остальные чаты.',
 5: 'РУКОВОДСТВО 5/12 — Определение владельца\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Определение владельца. Все настройки применяются отдельно к каждой конференции и не влияют на '
    'остальные чаты.',
 6: 'РУКОВОДСТВО 6/12 — Роли и уровни\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Роли и уровни. Все настройки применяются отдельно к каждой конференции и не влияют на остальные чаты.',
 7: 'РУКОВОДСТВО 7/12 — Предупреждения\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Предупреждения. Все настройки применяются отдельно к каждой конференции и не влияют на остальные '
    'чаты.',
 8: 'РУКОВОДСТВО 8/12 — Муты\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Муты. Все настройки применяются отдельно к каждой конференции и не влияют на остальные чаты.',
 9: 'РУКОВОДСТВО 9/12 — Баны\n'
    '\n'
    '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
    'собственный токен.\n'
    '\n'
    '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
    'в беседу назначьте сообщество администратором.\n'
    '\n'
    '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
    'станет создателем только для текущего peer_id.\n'
    '\n'
    '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
    'модерировать более высокий уровень.\n'
    '\n'
    '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу при '
    'обычной замене Python-файла.\n'
    '\n'
    '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
    'причину отказа.\n'
    '\n'
    '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
    'нормальные сообщения.\n'
    '\n'
    'Тема главы: Баны. Все настройки применяются отдельно к каждой конференции и не влияют на остальные чаты.',
 10: 'РУКОВОДСТВО 10/12 — Антиспам\n'
     '\n'
     '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
     'собственный токен.\n'
     '\n'
     '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
     'в беседу назначьте сообщество администратором.\n'
     '\n'
     '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
     'станет создателем только для текущего peer_id.\n'
     '\n'
     '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
     'модерировать более высокий уровень.\n'
     '\n'
     '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу '
     'при обычной замене Python-файла.\n'
     '\n'
     '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
     'причину отказа.\n'
     '\n'
     '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
     'нормальные сообщения.\n'
     '\n'
     'Тема главы: Антиспам. Все настройки применяются отдельно к каждой конференции и не влияют на остальные чаты.',
 11: 'РУКОВОДСТВО 11/12 — Экономика\n'
     '\n'
     '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
     'собственный токен.\n'
     '\n'
     '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
     'в беседу назначьте сообщество администратором.\n'
     '\n'
     '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
     'станет создателем только для текущего peer_id.\n'
     '\n'
     '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
     'модерировать более высокий уровень.\n'
     '\n'
     '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу '
     'при обычной замене Python-файла.\n'
     '\n'
     '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
     'причину отказа.\n'
     '\n'
     '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
     'нормальные сообщения.\n'
     '\n'
     'Тема главы: Экономика. Все настройки применяются отдельно к каждой конференции и не влияют на остальные чаты.',
 12: 'РУКОВОДСТВО 12/12 — Резервное копирование\n'
     '\n'
     '1. Используйте токен сообщества и никогда не публикуйте его в продаваемом архиве. Покупатель должен вставить '
     'собственный токен.\n'
     '\n'
     '2. В настройках сообщества включите сообщения, Bots Long Poll API и событие входящих сообщений. После добавления '
     'в беседу назначьте сообщество администратором.\n'
     '\n'
     '3. Выполните /setup. Бот получит список участников и найдёт элемент с признаком is_owner. Этот пользователь '
     'станет создателем только для текущего peer_id.\n'
     '\n'
     '4. Сначала создайте роли с уровнями и разрешениями. Пользователь более низкого или равного уровня не должен '
     'модерировать более высокий уровень.\n'
     '\n'
     '5. Все данные хранятся в SQLite. Перед обновлением остановите процесс и сделайте копию базы. Не удаляйте базу '
     'при обычной замене Python-файла.\n'
     '\n'
     '6. Для диагностики используйте /ping, /status, /settings и журнал консоли. Ошибки VK API обычно содержат точную '
     'причину отказа.\n'
     '\n'
     '7. Автоматические системы защиты сначала тестируют в закрытой беседе. Слишком строгие лимиты могут удалять '
     'нормальные сообщения.\n'
     '\n'
     'Тема главы: Резервное копирование. Все настройки применяются отдельно к каждой конференции и не влияют на '
     'остальные чаты.'}
GRAND_ALIASES = {'/команды2': '/commands',
 '/списоккоманд': '/commands',
 '/каталог': '/commands',
 '/cmds': '/commands',
 '/мануал': '/manual',
 '/описаниекоманды': '/manual',
 '/инфокоманда': '/manual',
 '/поисккоманд': '/findcmd',
 '/найтикоманду': '/findcmd',
 '/гайд': '/guide',
 '/руководство': '/guide',
 '/баланс': '/balance',
 '/счет': '/balance',
 '/счёт': '/balance',
 '/б': '/balance',
 '/ежедневка': '/daily',
 '/ежедневная': '/daily',
 '/награда': '/daily',
 '/бонусдня': '/daily',
 '/передатьбаллы': '/givepoints',
 '/перевод': '/givepoints',
 '/датьбаллы': '/givepoints',
 '/топбаланс': '/topbalance',
 '/богачи': '/topbalance',
 '/топбаллов': '/topbalance',
 '/био': '/bio',
 '/описание': '/bio',
 '/профильбио': '/bio',
 '/установитьбио': '/setbio',
 '/задатьбио': '/setbio',
 '/удалитьбио': '/delbio',
 '/очиститьбио': '/delbio',
 '/часовойпояс': '/settimezone',
 '/пояс': '/settimezone',
 '/время': '/time',
 '/местноевремя': '/time',
 '/напомнить': '/remind',
 '/напоминалка': '/remind',
 '/напоминания': '/reminders',
 '/удалитьнапоминание': '/reminddel',
 '/кастомдобавить': '/customadd',
 '/создатькоманду': '/customadd',
 '/добавитькоманду': '/customadd',
 '/кастомудалить': '/customdel',
 '/удалитькоманду': '/customdel',
 '/кастомы': '/customlist',
 '/своикоманды': '/customlist',
 '/жалоба': '/report',
 '/репорт': '/report',
 '/пожаловаться': '/report',
 '/моижалобы': '/myreports',
 '/жалобы': '/reports',
 '/закрытьжалобу': '/reportclose',
 '/шар': '/8ball',
 '/магическийшар': '/8ball',
 '/выбери': '/choose',
 '/выбор': '/choose',
 '/оцени': '/rate',
 '/оценка': '/rate',
 '/факт': '/fact',
 '/интересныйфакт': '/fact',
 '/шутка': '/joke',
 '/анекдот': '/joke',
 '/обнять': '/hug',
 '/обнимашки': '/hug',
 '/поцеловать': '/kiss',
 '/поцелуй': '/kiss',
 '/погладить': '/pat',
 '/гладить': '/pat',
 '/шлепнуть': '/slap',
 '/пощечина': '/slap',
 '/калькулятор': '/calc',
 '/посчитать': '/calc',
 '/верхний': '/upper',
 '/капс': '/upper',
 '/нижний': '/lower',
 '/маленькими': '/lower',
 '/наоборот': '/reverse',
 '/перевернуть': '/reverse',
 '/длина': '/length',
 '/счетчиктекста': '/length',
 '/аптайм': '/uptime',
 '/времяработы': '/uptime',
 '/пред': '/warn',
 '/предупреждение': '/warn',
 '/выдатьпред': '/warn',
 '/снятьпред': '/unwarn',
 '/убратьпред': '/unwarn',
 '/очиститьпреды': '/clearwarns',
 '/преды': '/warns',
 '/предлист': '/warnlist',
 '/мут': '/mute',
 '/замутить': '/mute',
 '/выдатьмут': '/mute',
 '/размут': '/unmute',
 '/снятьмут': '/unmute',
 '/муты': '/mutelist',
 '/мутлист': '/mutelist',
 '/бан': '/ban',
 '/забанить': '/ban',
 '/выдатьбан': '/ban',
 '/разбан': '/unban',
 '/снятьбан': '/unban',
 '/баны': '/banlist',
 '/банлист': '/banlist',
 '/кик': '/kick',
 '/выгнать': '/kick',
 '/исключить': '/kick',
 '/ник': '/nick',
 '/выдатьник': '/nick',
 '/снятьник': '/unnick',
 '/ники': '/nicks',
 '/история': '/history',
 '/причина': '/reason',
 '/очистить': '/clear',
 '/чистка': '/clear',
 '/пурж': '/purge',
 '/роли': '/roles',
 '/мояроль': '/myrole',
 '/создатьроль': '/rolecreate',
 '/удалитьроль': '/roledelete',
 '/выдатьроль': '/giverole',
 '/снятьроль': '/takerole',
 '/права': '/permissions',
 '/персонал': '/staff',
 '/владелец': '/owner',
 '/владельцы': '/owners',
 '/добавитьвладельца': '/addowner',
 '/снятьвладельца': '/delowner',
 '/защита': '/guard',
 '/закрытьчат': '/lock',
 '/открытьчат': '/unlock',
 '/медленныйрежим': '/slowmode',
 '/выключитьслоумод': '/slowmodeoff',
 '/антиспам': '/antispam',
 '/лимитспама': '/spamlimit',
 '/антиссылки': '/antilink',
 '/действиессылки': '/linkaction',
 '/разрешитьдомен': '/linkallow',
 '/удалитьдомен': '/linkdel',
 '/домены': '/linklist',
 '/антикапс': '/anticaps',
 '/лимиткапса': '/capslimit',
 '/стопслова': '/antimat',
 '/действиемата': '/mataction',
 '/добавитьслово': '/badwordadd',
 '/удалитьслово': '/badworddel',
 '/словасписок': '/badwords',
 '/правила': '/rules',
 '/установитьправила': '/setrules',
 '/удалитьправила': '/delrules',
 '/приветствие': '/welcome',
 '/включитьприветствие': '/welcomeon',
 '/выключитьприветствие': '/welcomeoff',
 '/прощание': '/goodbye',
 '/включитьпрощание': '/goodbyeon',
 '/выключитьпрощание': '/goodbyeoff',
 '/заметка': '/note',
 '/заметки': '/notelist',
 '/добавитьзаметку': '/noteadd',
 '/удалитьзаметку': '/notedel',
 '/сказать': '/say',
 '/объявление': '/announce',
 '/статус': '/status',
 '/настройки': '/settings',
 '/профиль': '/profile',
 '/айди': '/id',
 '/айдибеседы': '/chatid',
 '/участники': '/members',
 '/был': '/seen',
 '/статистика': '/stats',
 '/топ': '/top',
 '/монета': '/coin',
 '/кубик': '/dice',
 '/случайноечисло': '/random',
 '/название': '/title',
 '/лимитпредов': '/setwarnlimit'}
GRAND_FACTS = ['SQLite не требует отдельного сервера базы данных.',
 'VK peer_id беседы начинается с 2000000000.',
 'Python использует отступы как часть синтаксиса.',
 'Сообществу нужны права для удаления сообщений.',
 'Резервную копию базы лучше делать после остановки процесса.']
GRAND_JOKES = ['Работает — не трогай. Не работает — проверь токен.',
 'Админ обещал пять минут, сервер понял это как вечность.',
 'SQLite пришёл один, а таблиц принёс много.',
 'Бот не спорит: у него уже есть аргументы.']
GRAND_BALL = ['Да.',
 'Нет.',
 'Скорее всего.',
 'Сомнительно.',
 'Определённо.',
 'Спроси позже.',
 'Шансы высокие.',
 'Всё зависит от тебя.']
ALIASES.update(GRAND_ALIASES)


def grand_install_schema() -> None:
    with db._lock, db._conn:
        db._conn.executescript("""
        CREATE TABLE IF NOT EXISTS grand_profiles (
            peer_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            bio TEXT NOT NULL DEFAULT '', timezone INTEGER NOT NULL DEFAULT 3,
            points INTEGER NOT NULL DEFAULT 0, last_daily REAL NOT NULL DEFAULT 0,
            daily_streak INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
            PRIMARY KEY(peer_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS grand_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, peer_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, text TEXT NOT NULL, due_at REAL NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS grand_custom_commands (
            peer_id INTEGER NOT NULL, command_key TEXT NOT NULL,
            response_text TEXT NOT NULL, created_by INTEGER NOT NULL,
            uses_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, PRIMARY KEY(peer_id,command_key)
        );
        CREATE TABLE IF NOT EXISTS grand_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, peer_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
            reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            moderator_id INTEGER NOT NULL DEFAULT 0,
            moderator_comment TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
            closed_at REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_grand_reminders ON grand_reminders(delivered,due_at);
        CREATE INDEX IF NOT EXISTS idx_grand_reports ON grand_reports(peer_id,status,id DESC);
        """)

grand_install_schema()


def grand_sql(sql: str, params: tuple[Any, ...] = (), one: bool = False, rows: bool = False) -> Any:
    with db._lock, db._conn:
        cur = db._conn.execute(sql, params)
        if one:
            item = cur.fetchone()
            return dict(item) if item else None
        if rows:
            return [dict(item) for item in cur.fetchall()]
        return cur.lastrowid


def grand_profile(peer_id: int, user_id: int) -> dict[str, Any]:
    grand_sql('INSERT INTO grand_profiles(peer_id,user_id,updated_at) VALUES(?,?,?) ON CONFLICT(peer_id,user_id) DO NOTHING',(peer_id,user_id,time.time()))
    return grand_sql('SELECT * FROM grand_profiles WHERE peer_id=? AND user_id=?',(peer_id,user_id),one=True) or {}


def grand_profile_update(peer_id: int, user_id: int, **fields: Any) -> None:
    allowed={'bio','timezone','points','last_daily','daily_streak'}
    clean={k:v for k,v in fields.items() if k in allowed}
    if not clean: return
    grand_profile(peer_id,user_id)
    clean['updated_at']=time.time()
    grand_sql('UPDATE grand_profiles SET '+', '.join(f'{k}=?' for k in clean)+' WHERE peer_id=? AND user_id=?',tuple(clean.values())+(peer_id,user_id))


def grand_points(peer_id: int, user_id: int, delta: int) -> int:
    grand_profile(peer_id,user_id)
    grand_sql('UPDATE grand_profiles SET points=MAX(0,points+?),updated_at=? WHERE peer_id=? AND user_id=?',(delta,time.time(),peer_id,user_id))
    return int(grand_profile(peer_id,user_id).get('points',0))


def send_help(peer_id: int, query: str = '') -> None:
    query=query.strip().casefold()
    if not query:
        lines=[f'📚 {BOT_NAME} ',f'Каталог: {len(GRAND_COMMAND_ENTRIES)} основных команд и {len(GRAND_ALIASES)} алиасов','']
        for section, values in GRAND_HELP_SECTIONS.items():
            lines.append(f'• /help {section.casefold().replace(" ","_")} — {section} ({len(values)})')
        lines += ['','/commands [страница] — общий каталог','/manual /команда — подробности','/findcmd текст — поиск','/guide 1 — руководство']
        send_long(peer_id,'\n'.join(lines)); return
    normalized=query.replace('_',' ')
    for section, values in GRAND_HELP_SECTIONS.items():
        if normalized==section.casefold():
            lines=[f'📖 {section}:']+[f'• {cmd}' for cmd in values]
            send_long(peer_id,'\n'.join(lines)); return
    command=query if query.startswith('/') else '/'+query
    canonical=GRAND_ALIASES.get(command,command)
    text=GRAND_COMMAND_MANUAL.get(canonical)
    send_long(peer_id,text or 'Раздел или команда не найдены. Используйте /findcmd текст.')


def grand_catalog(peer_id: int, args: str) -> None:
    try: page=max(1,int(args.strip() or '1'))
    except ValueError: page=1
    size=24; total=max(1,(len(GRAND_COMMAND_ENTRIES)+size-1)//size); page=min(page,total)
    part=GRAND_COMMAND_ENTRIES[(page-1)*size:page*size]
    lines=[f'📋 Команды {page}/{total}']+[f"• {x['command']} — {x['description']}" for x in part]
    send_long(peer_id,'\n'.join(lines))


def grand_find(peer_id: int, query: str) -> None:
    q=query.strip().casefold()
    if not q: send_message(peer_id,'Пример: /findcmd мут'); return
    found=[x for x in GRAND_COMMAND_ENTRIES if q in (x['command']+' '+x['canonical']+' '+x['section']+' '+x['description']).casefold()]
    send_long(peer_id,'🔎 Найдено: '+str(len(found))+'\n'+'\n'.join(f"• {x['command']} → {x['canonical']}" for x in found[:80]) if found else 'Ничего не найдено.')


_GRAND_AST={_grand_ast.Expression,_grand_ast.BinOp,_grand_ast.UnaryOp,_grand_ast.Constant,_grand_ast.Add,_grand_ast.Sub,_grand_ast.Mult,_grand_ast.Div,_grand_ast.FloorDiv,_grand_ast.Mod,_grand_ast.Pow,_grand_ast.USub,_grand_ast.UAdd}
def grand_calc(text: str) -> float|int:
    if len(text)>120: raise ValueError('слишком длинно')
    tree=_grand_ast.parse(text,mode='eval')
    for node in _grand_ast.walk(tree):
        if type(node) not in _GRAND_AST: raise ValueError('недопустимая операция')
        if isinstance(node,_grand_ast.Constant) and not isinstance(node.value,(int,float)): raise ValueError('разрешены только числа')
    value=eval(compile(tree,'<calc>','eval'),{'__builtins__':{}},{})
    if not isinstance(value,(int,float)) or not _grand_math.isfinite(float(value)): raise ValueError('некорректный результат')
    return value


def grand_custom(peer_id: int, command: str) -> bool:
    row=grand_sql('SELECT response_text FROM grand_custom_commands WHERE peer_id=? AND command_key=?',(peer_id,command.casefold()),one=True)
    if not row: return False
    grand_sql('UPDATE grand_custom_commands SET uses_count=uses_count+1 WHERE peer_id=? AND command_key=?',(peer_id,command.casefold()))
    send_long(peer_id,str(row['response_text'])); return True


def handle_grand_ultra(peer_id: int, user_id: int, message: dict[str,Any], command: str, args: str) -> bool:
    if command=='/commands': grand_catalog(peer_id,args); return True
    if command=='/manual': send_help(peer_id,args); return True
    if command=='/findcmd': grand_find(peer_id,args); return True
    if command=='/guide':
        try: n=int(args.strip() or '1')
        except ValueError: n=1
        send_long(peer_id,GRAND_GUIDES.get(n,'Доступны главы /guide 1 — /guide 12')); return True
    if command=='/setbio':
        text=args.strip()
        if not 1<=len(text)<=800: send_message(peer_id,'Описание: 1–800 символов.'); return True
        grand_profile_update(peer_id,user_id,bio=text); send_message(peer_id,'✅ Описание сохранено.'); return True
    if command=='/bio':
        target,_=extract_target(message,args); target=target or user_id
        text=str(grand_profile(peer_id,target).get('bio','')).strip()
        send_long(peer_id,f'📝 {mention(peer_id,target)}\n{text or "Описание не заполнено."}'); return True
    if command=='/delbio': grand_profile_update(peer_id,user_id,bio=''); send_message(peer_id,'✅ Описание удалено.'); return True
    if command=='/settimezone':
        try: zone=int(args.strip())
        except ValueError: zone=99
        if not -12<=zone<=14: send_message(peer_id,'Пример: /settimezone 3'); return True
        grand_profile_update(peer_id,user_id,timezone=zone); send_message(peer_id,f'🕒 UTC{zone:+d} сохранён.'); return True
    if command=='/time':
        target,_=extract_target(message,args); target=target or user_id; zone=int(grand_profile(peer_id,target).get('timezone',3))
        local=time.gmtime(time.time()+zone*3600); send_message(peer_id,f"🕒 {mention(peer_id,target)}: {time.strftime('%d.%m.%Y %H:%M:%S',local)} UTC{zone:+d}"); return True
    if command=='/balance':
        target,_=extract_target(message,args); target=target or user_id
        send_message(peer_id,f"💰 {mention(peer_id,target)}: {grand_profile(peer_id,target).get('points',0)} баллов."); return True
    if command=='/daily':
        p=grand_profile(peer_id,user_id); now=time.time(); last=float(p.get('last_daily',0))
        if now-last<86400: send_message(peer_id,'⏳ Доступно через '+format_duration(86400-(now-last))+'.'); return True
        streak=int(p.get('daily_streak',0))+1 if now-last<172800 else 1; reward=min(100+(streak-1)*15,400)
        grand_points(peer_id,user_id,reward); grand_profile_update(peer_id,user_id,last_daily=now,daily_streak=streak)
        send_message(peer_id,f'🎁 +{reward} баллов. Серия: {streak}.'); return True
    if command=='/givepoints':
        target,rest=extract_target(message,args); parts=rest.split(maxsplit=1)
        try: amount=int(parts[0])
        except (ValueError,IndexError): amount=0
        balance=int(grand_profile(peer_id,user_id).get('points',0))
        if not target or target==user_id or amount<=0 or amount>balance: send_message(peer_id,'Пример: /givepoints @id123 50'); return True
        grand_points(peer_id,user_id,-amount); grand_points(peer_id,target,amount); send_message(peer_id,f'💸 Передано {amount} баллов.'); return True
    if command=='/topbalance':
        rows=grand_sql('SELECT user_id,points FROM grand_profiles WHERE peer_id=? ORDER BY points DESC LIMIT 15',(peer_id,),rows=True)
        send_long(peer_id,'💰 Топ:\n'+'\n'.join(f"{i}. {mention(peer_id,int(r['user_id']))} — {r['points']}" for i,r in enumerate(rows,1)) if rows else 'Нет данных.'); return True
    if command=='/remind':
        parts=args.split(maxsplit=1); duration=parse_duration(parts[0]) if parts else None
        if duration in {None,-1} or len(parts)<2: send_message(peer_id,'Пример: /remind 30m проверить чат'); return True
        rid=grand_sql('INSERT INTO grand_reminders(peer_id,user_id,text,due_at,created_at) VALUES(?,?,?,?,?)',(peer_id,user_id,parts[1][:1000],time.time()+duration,time.time()))
        send_message(peer_id,f'⏰ Напоминание №{rid} создано.'); return True
    if command=='/reminders':
        rows=grand_sql('SELECT * FROM grand_reminders WHERE peer_id=? AND user_id=? AND delivered=0 ORDER BY due_at LIMIT 30',(peer_id,user_id),rows=True)
        send_long(peer_id,'⏰ Напоминания:\n'+'\n'.join(f"• №{r['id']} через {format_duration(float(r['due_at'])-time.time())}: {r['text']}" for r in rows) if rows else 'Напоминаний нет.'); return True
    if command=='/reminddel':
        try: rid=int(args.strip())
        except ValueError: rid=0
        grand_sql('DELETE FROM grand_reminders WHERE peer_id=? AND user_id=? AND id=?',(peer_id,user_id,rid)); send_message(peer_id,'✅ Удалено, если существовало.'); return True
    if command=='/customadd':
        if not require_permission(peer_id,user_id,'settings'): return True
        parts=args.split(maxsplit=1)
        if len(parts)<2 or not parts[0].startswith('/'): send_message(peer_id,'Пример: /customadd /сайт ссылка'); return True
        key=parts[0].casefold(); now=time.time()
        grand_sql('INSERT INTO grand_custom_commands(peer_id,command_key,response_text,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(peer_id,command_key) DO UPDATE SET response_text=excluded.response_text,created_by=excluded.created_by,updated_at=excluded.updated_at',(peer_id,key,parts[1][:3500],user_id,now,now)); send_message(peer_id,f'✅ {key} сохранена.'); return True
    if command=='/customdel':
        if not require_permission(peer_id,user_id,'settings'): return True
        key=args.strip().casefold(); key=key if key.startswith('/') else '/'+key
        grand_sql('DELETE FROM grand_custom_commands WHERE peer_id=? AND command_key=?',(peer_id,key)); send_message(peer_id,'✅ Удалено.'); return True
    if command=='/customlist':
        rows=grand_sql('SELECT command_key,uses_count FROM grand_custom_commands WHERE peer_id=? ORDER BY command_key',(peer_id,),rows=True)
        send_long(peer_id,'🧩 Команды:\n'+'\n'.join(f"• {r['command_key']} — {r['uses_count']}" for r in rows) if rows else 'Команд нет.'); return True
    if command=='/report':
        target,rest=extract_target(message,args)
        if not target or target==user_id or not rest: send_message(peer_id,'Пример: /report @id123 причина'); return True
        rid=grand_sql('INSERT INTO grand_reports(peer_id,reporter_id,target_id,reason,created_at) VALUES(?,?,?,?,?)',(peer_id,user_id,target,reason(rest),time.time())); send_message(peer_id,f'📨 Жалоба №{rid} создана.'); return True
    if command=='/myreports':
        rows=grand_sql('SELECT * FROM grand_reports WHERE peer_id=? AND reporter_id=? ORDER BY id DESC LIMIT 20',(peer_id,user_id),rows=True)
        send_long(peer_id,'📨 Жалобы:\n'+'\n'.join(f"• №{r['id']} {r['status']}: {r['reason']}" for r in rows) if rows else 'Жалоб нет.'); return True
    if command=='/reports':
        if not require_permission(peer_id,user_id,'warn'): return True
        status=args.strip() if args.strip() in {'open','closed'} else 'open'; rows=grand_sql('SELECT * FROM grand_reports WHERE peer_id=? AND status=? ORDER BY id DESC LIMIT 30',(peer_id,status),rows=True)
        send_long(peer_id,'📨 Очередь:\n'+'\n'.join(f"• №{r['id']} {mention(peer_id,int(r['reporter_id']))} → {mention(peer_id,int(r['target_id']))}: {r['reason']}" for r in rows) if rows else 'Список пуст.'); return True
    if command=='/reportclose':
        if not require_permission(peer_id,user_id,'warn'): return True
        parts=args.split(maxsplit=1)
        try: rid=int(parts[0])
        except (ValueError,IndexError): rid=0
        comment=parts[1][:500] if len(parts)>1 else ''
        grand_sql('UPDATE grand_reports SET status="closed",moderator_id=?,moderator_comment=?,closed_at=? WHERE peer_id=? AND id=?',(user_id,comment,time.time(),peer_id,rid)); send_message(peer_id,f'✅ Жалоба №{rid} закрыта.'); return True
    if command=='/8ball':
        if not args.strip(): send_message(peer_id,'Задайте вопрос.'); return True
        send_message(peer_id,'🎱 '+random.choice(GRAND_BALL)); return True
    if command=='/choose':
        variants=[x.strip() for x in args.split('|') if x.strip()]
        send_message(peer_id,'🎯 '+random.choice(variants) if len(variants)>=2 else 'Пример: /choose чай | кофе'); return True
    if command=='/rate':
        if not args.strip(): send_message(peer_id,'Укажите текст.'); return True
        value=random.Random(sum(map(ord,args.casefold()))+peer_id+user_id).randint(0,100); send_message(peer_id,f'📊 {value}/100.'); return True
    if command=='/fact': send_message(peer_id,'🧠 '+random.choice(GRAND_FACTS)); return True
    if command=='/joke': send_message(peer_id,'😄 '+random.choice(GRAND_JOKES)); return True
    if command in {'/hug','/kiss','/pat','/slap'}:
        target,_=extract_target(message,args)
        if not target: send_message(peer_id,'Укажите пользователя.'); return True
        verbs={'/hug':'обнимает','/kiss':'целует','/pat':'гладит','/slap':'шутливо даёт пощёчину'}
        send_message(peer_id,f'{mention(peer_id,user_id)} {verbs[command]} {mention(peer_id,target)}.'); return True
    if command=='/calc':
        try: value=grand_calc(args.strip())
        except Exception as exc: send_message(peer_id,f'Ошибка: {exc}'); return True
        send_message(peer_id,f'🧮 {value}'); return True
    if command in {'/upper','/lower','/reverse'}:
        if not args: send_message(peer_id,'Укажите текст.'); return True
        result=args.upper() if command=='/upper' else args.lower() if command=='/lower' else args[::-1]; send_long(peer_id,result); return True
    if command=='/length': send_message(peer_id,f'📏 Символов: {len(args)}\nСлов: {len(args.split())}\nСтрок: {args.count(chr(10))+1 if args else 0}'); return True
    if command=='/uptime': send_message(peer_id,'⏱ '+format_duration(time.time()-GRAND_STARTED_AT)); return True
    return grand_custom(peer_id,command)


def grand_background() -> None:
    rows=grand_sql('SELECT * FROM grand_reminders WHERE delivered=0 AND due_at<=? ORDER BY due_at LIMIT 100',(time.time(),),rows=True)
    for row in rows:
        try: send_long(int(row['peer_id']),f"⏰ Напоминание для {mention(int(row['peer_id']),int(row['user_id']))}:\n{row['text']}")
        except Exception: log.exception('Ошибка напоминания %s',row['id'])
        finally: grand_sql('UPDATE grand_reminders SET delivered=1 WHERE id=?',(int(row['id']),))

# ============================================================
# СОБЫТИЯ
# ============================================================

def render_template(peer_id: int, user_id: int, template: str) -> str:
    return (
        template.replace("{user}", mention(peer_id, user_id))
        .replace("{id}", str(user_id))
        .replace("{chat}", get_chat_title(peer_id))
    )


def handle_action(peer_id: int, action: dict[str, Any]) -> None:
    action_type = str(action.get("type", ""))
    try:
        member_id = int(action.get("member_id", 0))
    except (TypeError, ValueError):
        member_id = 0

    if action_type in {"chat_invite_user", "chat_invite_user_by_link"}:
        if member_id == -BOT_GROUP_ID:
            db.ensure_chat(peer_id)
            try:
                owner_id = sync_owner(peer_id, force=True)
                owner_line = f"\nСоздатель: {mention(peer_id, owner_id)}" if owner_id else ""
            except Exception:
                owner_line = "\nВыполните /setup после назначения бота администратором."
            send_message(
                peer_id,
                f"👋 {BOT_NAME} подключён. Доступно более 250 команд и алиасов."
                f"{owner_line}\nОтправьте /help.",
            )
            return
        if member_id > 0:
            db.update_member(peer_id, member_id, joined_at=time.time())
            if db.get_member(peer_id, member_id).get("banned"):
                remove_chat_user(peer_id, member_id)
                return
            chat = db.get_chat(peer_id)
            if chat.get("welcome_enabled"):
                send_long(peer_id, render_template(peer_id, member_id, str(chat["welcome_text"])))
            return

    if action_type == "chat_kick_user" and member_id > 0:
        chat = db.get_chat(peer_id)
        if chat.get("goodbye_enabled"):
            send_long(peer_id, render_template(peer_id, member_id, str(chat["goodbye_text"])))


def cleanup_worker() -> None:
    while True:
        try:
            db.cleanup()
            grand_background()
        except Exception:
            log.exception("Ошибка фоновой очистки")
        time.sleep(CLEANUP_INTERVAL)


# ============================================================
# ЗАПУСК
# ============================================================

def main() -> None:
    global vk_session, vk, BOT_GROUP_ID
    validate_config()
    vk_session = vk_api.VkApi(token=VK_TOKEN, api_version=API_VERSION)
    vk = vk_session.get_api()
    BOT_GROUP_ID = detect_group_id()
    longpoll = VkBotLongPoll(vk_session, BOT_GROUP_ID)

    threading.Thread(target=cleanup_worker, daemon=True, name="cleanup").start()

    log.info("%s запущен", BOT_NAME)
    log.info("Сообщество: %s", BOT_GROUP_ID)
    log.info("База: %s", DATABASE_FILE)

    while True:
        try:
            for event in longpoll.listen():
                if event.type != VkBotEventType.MESSAGE_NEW:
                    continue
                message = event.object.message
                try:
                    peer_id = int(message.get("peer_id", 0))
                    user_id = int(message.get("from_id", 0))
                    cmid = int(message.get("conversation_message_id", 0))
                except (TypeError, ValueError):
                    continue
                if not is_group_chat(peer_id):
                    continue
                db.ensure_chat(peer_id)

                action = message.get("action")
                if isinstance(action, dict) and action:
                    handle_action(peer_id, action)
                    continue
                if user_id <= 0:
                    continue

                text = str(message.get("text", "") or "")
                db.log_message(peer_id, user_id, cmid)
                db.increment_member(peer_id, user_id, "messages_count")
                db.update_member(peer_id, user_id, last_seen=time.time())

                command, _ = split_command(text)
                if command:
                    db.increment_member(peer_id, user_id, "commands_count")

                try:
                    sync_owner(peer_id)
                except Exception:
                    pass

                if enforce_guards(peer_id, user_id, message, text):
                    continue

                handle_command(peer_id, user_id, message)

        except KeyboardInterrupt:
            log.info("Бот остановлен")
            return
        except Exception:
            log.exception("Long Poll отключился. Повтор через 5 секунд")
            time.sleep(5)


if __name__ == "__main__":
    main()
