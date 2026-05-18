import sys
import subprocess
import importlib.util


def install_if_missing(package, import_name=None):
    import_name = import_name or package
    if importlib.util.find_spec(import_name) is None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])


install_if_missing("vkbottle")
install_if_missing("aiohttp")

import logging
from logging.handlers import RotatingFileHandler
import os
import sqlite3
import time
import re
import random
import asyncio
import json
import aiohttp
import threading
from pathlib import Path
from collections import deque
from datetime import datetime, timedelta, timezone
from vkbottle import API, Bot, Keyboard, Text, OpenLink
from vkbottle.bot import Message
from vkbottle.http import SingleAiohttpClient

# Paths and configuration
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "vk_chat_manager.db"
LOG_PATH = BASE_DIR / "vk_bot.log"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to load %s: %s", path, exc)


_load_env_file(ENV_PATH)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


VK_TOKEN = os.getenv("VK_TOKEN", "vk1.a.jmhGtKNRy-okO7WM6HyGJofKiJMaUnBDyB3kEqxdKypWpcnJaEB7KBJixSmIMLc7YLBJHu6wKY2sElm6VlK59GWdnir2DJQl5D9ohPLQ_8USyg-_gpviWLw31YaUIcx51Y84dSXBPjUpwIULup3JGkiHECtNOGSqlxX4q3IvWgeGEwzaXefqwmTa9aFx2-g9b5dmx07Wx-HH3-Tu_2HDag").strip()
OWNER_ID = _env_int("OWNER_ID", 848213593)
VK_API_URL = os.getenv("VK_API_URL", "https://api.vk.com/method/").strip() or "https://api.vk.com/method/"
VK_API_FALLBACK_URL = os.getenv("VK_API_FALLBACK_URL", "https://api.vk.ru/method/").strip() or "https://api.vk.ru/method/"
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199").strip() or "5.199"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "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").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "~openai/gpt-5-mini").strip() or "~openai/gpt-5-mini"
OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions").strip()
OPENROUTER_REFERER = os.getenv("OPENROUTER_REFERER", "https://vk.com/").strip()
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "VK Chat Manager Bot").strip()
MAX_WARNS = 3
ANTISPAM_FLOOD_WINDOW = 12
ANTISPAM_FLOOD_LIMIT = 6
ANTISPAM_DUPLICATE_WINDOW = 20
ANTISPAM_DUPLICATE_LIMIT = 3
ANTISPAM_LINK_LIMIT = 3
ANTISPAM_ATTACHMENTS_LIMIT = 5
ANTISPAM_AUTO_MUTE_MINUTES = 10
ZOV_MAX_MENTIONS = 50
ZOV_COOLDOWN_SECONDS = 180
EXPIRE_CHECK_INTERVAL_SECONDS = 2
GROUPS_PAGE_SIZE = 6
ANTIMAT_NOTIFY_COOLDOWN_SECONDS = 15
AI_COOLDOWN_SECONDS = 15
AI_MAX_PROMPT_CHARS = 1500
AI_MAX_REPLY_CHARS = 3500

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", handlers=[])
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
file_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
log.addHandler(stream_handler)
log.addHandler(file_handler)

if not VK_TOKEN:
    raise SystemExit(
        f"VK_TOKEN is missing. Create {ENV_PATH.name} next to vk_bot.py and set VK_TOKEN=..."
    )

# Database
class LockedCursor:
    def __init__(self, database, cursor):
        self.database = database
        self.cursor = cursor

    def execute(self, *args, **kwargs):
        last_error = None
        for attempt in range(8):
            try:
                with self.database._lock:
                    return self.cursor.execute(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                last_error = exc
                time.sleep(0.15 * (attempt + 1))
        raise last_error


class LockedConnection:
    def __init__(self, database, connection):
        self.database = database
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, *args, **kwargs):
        last_error = None
        for attempt in range(8):
            try:
                with self.database._lock:
                    return self.connection.execute(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                last_error = exc
                time.sleep(0.15 * (attempt + 1))
        raise last_error

    def commit(self):
        last_error = None
        for attempt in range(8):
            try:
                with self.database._lock:
                    return self.connection.commit()
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                last_error = exc
                time.sleep(0.15 * (attempt + 1))
        raise last_error


class Database:
    def __init__(self):
        self._lock = threading.RLock()
        raw_conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        raw_conn.row_factory = sqlite3.Row
        self.conn = LockedConnection(self, raw_conn)
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.cursor = LockedCursor(self, self.conn.cursor())
        self._create_tables()
        self._bootstrap_known_chats()
    
    def _create_tables(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS banned (
                user_id INTEGER,
                reason TEXT,
                banned_by INTEGER,
                banned_until INTEGER,
                chat_id INTEGER,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS ban_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                reason TEXT,
                banned_by INTEGER,
                banned_until INTEGER,
                created_at INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS warns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                reason TEXT,
                warned_by INTEGER,
                timestamp INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                welcome TEXT,
                rules TEXT,
                antispam_enabled INTEGER DEFAULT 1,
                antimat_enabled INTEGER DEFAULT 1
            )
        """)
        columns = [col[1] for col in self.cursor.execute("PRAGMA table_info(chat_settings)").fetchall()]
        if "antispam_enabled" not in columns:
            self.cursor.execute("ALTER TABLE chat_settings ADD COLUMN antispam_enabled INTEGER DEFAULT 1")
        if "antimat_enabled" not in columns:
            self.cursor.execute("ALTER TABLE chat_settings ADD COLUMN antimat_enabled INTEGER DEFAULT 1")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                name TEXT,
                content TEXT,
                created_by INTEGER,
                UNIQUE(chat_id, name)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS mutes (
                user_id INTEGER,
                chat_id INTEGER,
                muted_until INTEGER,
                muted_by INTEGER,
                reason TEXT,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER,
                chat_id INTEGER,
                role TEXT,
                assigned_by INTEGER,
                assigned_at INTEGER,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS nicknames (
                user_id INTEGER,
                chat_id INTEGER,
                nickname TEXT,
                set_by INTEGER,
                set_at INTEGER,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_roles (
                chat_id INTEGER,
                role_name TEXT,
                display_name TEXT,
                level INTEGER,
                can_ban INTEGER,
                can_kick INTEGER,
                can_warn INTEGER,
                can_mute INTEGER,
                can_set_role INTEGER,
                can_gban INTEGER,
                can_gkick INTEGER,
                can_gmute INTEGER,
                can_grole INTEGER,
                created_by INTEGER,
                PRIMARY KEY (chat_id, role_name)
            )
        """)
        custom_role_columns = [col[1] for col in self.cursor.execute("PRAGMA table_info(custom_roles)").fetchall()]
        if "display_name" not in custom_role_columns:
            self.cursor.execute("ALTER TABLE custom_roles ADD COLUMN display_name TEXT")
        for column_name in ("can_gban", "can_gkick", "can_gmute", "can_grole"):
            if column_name not in custom_role_columns:
                self.cursor.execute(f"ALTER TABLE custom_roles ADD COLUMN {column_name} INTEGER DEFAULT 0")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS known_chats (
                chat_id INTEGER PRIMARY KEY,
                last_seen INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS linked_chats (
                chat_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_user_stats (
                chat_id INTEGER,
                user_id INTEGER,
                messages INTEGER DEFAULT 0,
                first_seen_at INTEGER,
                last_message_at INTEGER,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        stats_columns = [col[1] for col in self.cursor.execute("PRAGMA table_info(chat_user_stats)").fetchall()]
        if "first_seen_at" not in stats_columns:
            self.cursor.execute("ALTER TABLE chat_user_stats ADD COLUMN first_seen_at INTEGER")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS forbidden_words (
                chat_id INTEGER,
                word TEXT,
                added_by INTEGER,
                added_at INTEGER,
                PRIMARY KEY (chat_id, word)
            )
        """)
        # Currency system
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS currency (
                user_id INTEGER,
                chat_id INTEGER,
                balance INTEGER DEFAULT 0,
                last_daily INTEGER,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS premium (
                user_id INTEGER PRIMARY KEY,
                premium_until INTEGER NOT NULL,
                granted_by INTEGER,
                reason TEXT
            )
        """)
        self.conn.commit()
    
    def is_banned(self, user_id, chat_id):
        now = int(time.time())
        result = self.cursor.execute(
            "SELECT 1 FROM banned WHERE user_id=? AND chat_id=? AND (banned_until > ? OR banned_until IS NULL)",
            (user_id, chat_id, now)
        ).fetchone()
        return result is not None
    
    def ban_user(self, user_id, chat_id, reason, banned_by, days=None, duration_seconds=None):
        if duration_seconds is not None:
            banned_until = int(time.time()) + max(1, int(duration_seconds))
        elif days:
            banned_until = int((datetime.now() + timedelta(days=days)).timestamp())
        else:
            banned_until = None
        self.cursor.execute(
            "INSERT OR REPLACE INTO banned (user_id, chat_id, reason, banned_by, banned_until) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, reason, banned_by, banned_until)
        )
        self.cursor.execute(
            "INSERT INTO ban_history (user_id, chat_id, reason, banned_by, banned_until, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, reason, banned_by, banned_until, int(time.time()))
        )
        self.conn.commit()
    
    def unban_user(self, user_id, chat_id):
        self.cursor.execute("DELETE FROM banned WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        self.conn.commit()
    
    def get_ban_info(self, user_id, chat_id):
        now = int(time.time())
        return self.cursor.execute(
            "SELECT reason, banned_until, banned_by FROM banned WHERE user_id=? AND chat_id=? AND (banned_until > ? OR banned_until IS NULL)",
            (user_id, chat_id, now)
        ).fetchone()

    def get_ban_history_count(self, user_id, chat_id):
        result = self.cursor.execute(
            "SELECT COUNT(*) FROM ban_history WHERE user_id=? AND chat_id=?",
            (user_id, chat_id)
        ).fetchone()
        return result[0] if result else 0

    def get_last_ban_history(self, user_id, chat_id):
        return self.cursor.execute(
            "SELECT reason, banned_by, banned_until, created_at FROM ban_history WHERE user_id=? AND chat_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id, chat_id)
        ).fetchone()
    
    def get_warns(self, user_id, chat_id):
        result = self.cursor.execute(
            "SELECT COUNT(*) FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        ).fetchone()
        return result[0] if result else 0
    
    def add_warn(self, user_id, chat_id, reason, warned_by):
        self.cursor.execute(
            "INSERT INTO warns (user_id, chat_id, reason, warned_by, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, reason, warned_by, int(time.time()))
        )
        self.conn.commit()
        return self.get_warns(user_id, chat_id)
    
    def clear_warns(self, user_id, chat_id):
        self.cursor.execute("DELETE FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        self.conn.commit()
    
    def remove_one_warn(self, user_id, chat_id):
        """Remove one warn (the oldest) for a user in a chat"""
        # Get the oldest warn
        result = self.cursor.execute(
            "SELECT id FROM warns WHERE user_id=? AND chat_id=? ORDER BY timestamp ASC LIMIT 1",
            (user_id, chat_id)
        ).fetchone()
        if result:
            self.cursor.execute("DELETE FROM warns WHERE id=?", (result[0],))
            self.conn.commit()
            return True
        return False
    
    def get_chat_settings(self, chat_id):
        result = self.cursor.execute(
            "SELECT welcome, rules, COALESCE(antispam_enabled, 1), COALESCE(antimat_enabled, 1) FROM chat_settings WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if result:
            return {
                'welcome': result[0] or '',
                'rules': result[1] or '',
                'antispam_enabled': bool(result[2]),
                'antimat_enabled': bool(result[3]),
            }
        return {'welcome': '', 'rules': '', 'antispam_enabled': True, 'antimat_enabled': True}
    
    def set_welcome(self, chat_id, welcome):
        self.cursor.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, welcome, rules, antispam_enabled, antimat_enabled) VALUES (?, ?, COALESCE((SELECT rules FROM chat_settings WHERE chat_id=?), ''), COALESCE((SELECT antispam_enabled FROM chat_settings WHERE chat_id=?), 1), COALESCE((SELECT antimat_enabled FROM chat_settings WHERE chat_id=?), 1))",
            (chat_id, welcome, chat_id, chat_id, chat_id)
        )
        self.conn.commit()
    
    def set_rules(self, chat_id, rules):
        self.cursor.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, welcome, rules, antispam_enabled, antimat_enabled) VALUES (?, COALESCE((SELECT welcome FROM chat_settings WHERE chat_id=?), ''), ?, COALESCE((SELECT antispam_enabled FROM chat_settings WHERE chat_id=?), 1), COALESCE((SELECT antimat_enabled FROM chat_settings WHERE chat_id=?), 1))",
            (chat_id, chat_id, rules, chat_id, chat_id)
        )
        self.conn.commit()

    def is_antispam_enabled(self, chat_id):
        result = self.cursor.execute(
            "SELECT COALESCE(antispam_enabled, 1) FROM chat_settings WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return bool(result[0]) if result else True

    def set_antispam_enabled(self, chat_id, enabled):
        value = 1 if enabled else 0
        self.cursor.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, welcome, rules, antispam_enabled, antimat_enabled) VALUES (?, COALESCE((SELECT welcome FROM chat_settings WHERE chat_id=?), ''), COALESCE((SELECT rules FROM chat_settings WHERE chat_id=?), ''), ?, COALESCE((SELECT antimat_enabled FROM chat_settings WHERE chat_id=?), 1))",
            (chat_id, chat_id, chat_id, value, chat_id)
        )
        self.conn.commit()

    def is_antimat_enabled(self, chat_id):
        result = self.cursor.execute(
            "SELECT COALESCE(antimat_enabled, 1) FROM chat_settings WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return bool(result[0]) if result else True

    def set_antimat_enabled(self, chat_id, enabled):
        value = 1 if enabled else 0
        self.cursor.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, welcome, rules, antispam_enabled, antimat_enabled) VALUES (?, COALESCE((SELECT welcome FROM chat_settings WHERE chat_id=?), ''), COALESCE((SELECT rules FROM chat_settings WHERE chat_id=?), ''), COALESCE((SELECT antispam_enabled FROM chat_settings WHERE chat_id=?), 1), ?)",
            (chat_id, chat_id, chat_id, chat_id, value)
        )
        self.conn.commit()
    
    def save_note(self, chat_id, name, content, created_by):
        self.cursor.execute(
            "INSERT OR REPLACE INTO notes (chat_id, name, content, created_by) VALUES (?, ?, ?, ?)",
            (chat_id, name, content, created_by)
        )
        self.conn.commit()
    
    def get_note(self, chat_id, name):
        result = self.cursor.execute(
            "SELECT content FROM notes WHERE chat_id=? AND name=?", (chat_id, name)
        ).fetchone()
        return result[0] if result else None
    
    def get_notes(self, chat_id):
        return [r[0] for r in self.cursor.execute(
            "SELECT name FROM notes WHERE chat_id=?", (chat_id,)
        ).fetchall()]
    
    def delete_note(self, chat_id, name):
        self.cursor.execute("DELETE FROM notes WHERE chat_id=? AND name=?", (chat_id, name))
        self.conn.commit()
    
    def is_muted(self, user_id, chat_id):
        now = int(time.time())
        result = self.cursor.execute(
            "SELECT muted_until FROM mutes WHERE user_id=? AND chat_id=? AND muted_until > ?",
            (user_id, chat_id, now)
        ).fetchone()
        return result is not None
    
    def get_mute_info(self, user_id, chat_id):
        now = int(time.time())
        return self.cursor.execute(
            "SELECT muted_until, muted_by, reason FROM mutes WHERE user_id=? AND chat_id=? AND muted_until > ?",
            (user_id, chat_id, now)
        ).fetchone()
    
    def mute_user(self, user_id, chat_id, minutes, muted_by, reason=""):
        duration_seconds = max(1, int(float(minutes) * 60))
        muted_until = int(time.time()) + duration_seconds
        self.cursor.execute(
            "INSERT OR REPLACE INTO mutes (user_id, chat_id, muted_until, muted_by, reason) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, muted_until, muted_by, reason)
        )
        self.conn.commit()
    
    def unmute_user(self, user_id, chat_id):
        self.cursor.execute("DELETE FROM mutes WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        self.conn.commit()
    
    def get_role(self, user_id, chat_id):
        result = self.cursor.execute(
            "SELECT role FROM user_roles WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        ).fetchone()
        return result[0] if result else None
    
    def set_role(self, user_id, chat_id, role, assigned_by):
        self.cursor.execute(
            "INSERT OR REPLACE INTO user_roles (user_id, chat_id, role, assigned_by, assigned_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, role, assigned_by, int(time.time()))
        )
        self.conn.commit()
    
    def remove_role(self, user_id, chat_id):
        self.cursor.execute("DELETE FROM user_roles WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        self.conn.commit()
    
    def get_users_by_role(self, chat_id, role):
        return self.cursor.execute(
            "SELECT user_id FROM user_roles WHERE chat_id=? AND role=?", (chat_id, role)
        ).fetchall()
    
    def get_expired_mutes(self):
        now = int(time.time())
        return self.cursor.execute(
            "SELECT user_id, chat_id, muted_by, reason FROM mutes WHERE muted_until <= ? AND muted_until > 0",
            (now,)
        ).fetchall()
    
    def cleanup_expired_mutes(self):
        now = int(time.time())
        self.cursor.execute("DELETE FROM mutes WHERE muted_until <= ? AND muted_until > 0", (now,))
        self.conn.commit()

    def get_expired_bans(self):
        now = int(time.time())
        return self.cursor.execute(
            "SELECT user_id, chat_id, reason, banned_by FROM banned WHERE banned_until IS NOT NULL AND banned_until <= ?",
            (now,)
        ).fetchall()

    def cleanup_expired_bans(self):
        now = int(time.time())
        self.cursor.execute(
            "DELETE FROM banned WHERE banned_until IS NOT NULL AND banned_until <= ?",
            (now,)
        )
        self.conn.commit()
    
    def set_nickname(self, user_id, chat_id, nickname, set_by):
        self.cursor.execute(
            "INSERT OR REPLACE INTO nicknames (user_id, chat_id, nickname, set_by, set_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, nickname, set_by, int(time.time()))
        )
        self.conn.commit()
    
    def get_nickname(self, user_id, chat_id):
        result = self.cursor.execute(
            "SELECT nickname FROM nicknames WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        ).fetchone()
        return result[0] if result else None
    
    def remove_nickname(self, user_id, chat_id):
        self.cursor.execute("DELETE FROM nicknames WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        self.conn.commit()
    
    def create_custom_role(self, chat_id, role_name, level, permissions, created_by):
        self.cursor.execute(
            """
            INSERT OR REPLACE INTO custom_roles
            (chat_id, role_name, display_name, level, can_ban, can_kick, can_warn, can_mute, can_set_role, can_gban, can_gkick, can_gmute, can_grole, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                normalize_role_key(role_name),
                role_name.strip(),
                level,
                permissions.get('can_ban', 0),
                permissions.get('can_kick', 0),
                permissions.get('can_warn', 0),
                permissions.get('can_mute', 0),
                permissions.get('can_set_role', 0),
                permissions.get('can_gban', 0),
                permissions.get('can_gkick', 0),
                permissions.get('can_gmute', 0),
                permissions.get('can_grole', 0),
                created_by,
            )
        )
        self.conn.commit()

    def create_priority_role(self, chat_id, role_name, level, created_by):
        permissions = role_permissions_from_level(level)
        role_key = normalize_role_key(role_name)
        existing_by_level = self.get_custom_role_by_level(chat_id, level)
        if existing_by_level and existing_by_level[0] != role_key:
            old_key = existing_by_level[0]
            self.cursor.execute(
                """
                UPDATE custom_roles
                SET role_name=?, display_name=?, level=?, can_ban=?, can_kick=?, can_warn=?, can_mute=?, can_set_role=?, can_gban=?, can_gkick=?, can_gmute=?, can_grole=?, created_by=?
                WHERE chat_id=? AND role_name=?
                """,
                (
                    role_key,
                    role_name.strip(),
                    int(level),
                    permissions.get('can_ban', 0),
                    permissions.get('can_kick', 0),
                    permissions.get('can_warn', 0),
                    permissions.get('can_mute', 0),
                    permissions.get('can_set_role', 0),
                    permissions.get('can_gban', 0),
                    permissions.get('can_gkick', 0),
                    permissions.get('can_gmute', 0),
                    permissions.get('can_grole', 0),
                    created_by,
                    chat_id,
                    old_key,
                )
            )
            self.cursor.execute(
                "UPDATE user_roles SET role=? WHERE chat_id=? AND role=?",
                (role_key, chat_id, old_key)
            )
            self.conn.commit()
            return
        self.create_custom_role(chat_id, role_name, level, permissions, created_by)

    def get_custom_role(self, chat_id, role_name):
        result = self.cursor.execute(
            "SELECT display_name, level, can_ban, can_kick, can_warn, can_mute, can_set_role, COALESCE(can_gban, 0), COALESCE(can_gkick, 0), COALESCE(can_gmute, 0), COALESCE(can_grole, 0) FROM custom_roles WHERE chat_id=? AND role_name=?",
            (chat_id, normalize_role_key(role_name))
        ).fetchone()
        if result:
            return {
                "name": result[0] or role_name,
                "level": result[1],
                "can_ban": bool(result[2]),
                "can_kick": bool(result[3]),
                "can_warn": bool(result[4]),
                "can_mute": bool(result[5]),
                "can_set_role": bool(result[6]),
                "can_gban": bool(result[7]),
                "can_gkick": bool(result[8]),
                "can_gmute": bool(result[9]),
                "can_grole": bool(result[10])
            }
        return None

    def get_custom_role_by_level(self, chat_id, level):
        result = self.cursor.execute(
            "SELECT role_name FROM custom_roles WHERE chat_id=? AND level=? ORDER BY role_name LIMIT 1",
            (chat_id, int(level))
        ).fetchone()
        if not result:
            return None
        return result[0], self.get_custom_role(chat_id, result[0])
    
    def get_custom_roles(self, chat_id):
        return self.cursor.execute(
            "SELECT role_name, COALESCE(display_name, role_name), level FROM custom_roles WHERE chat_id=? ORDER BY level DESC",
            (chat_id,)
        ).fetchall()

    def get_all_user_roles(self, chat_id):
        return self.cursor.execute(
            "SELECT user_id, role FROM user_roles WHERE chat_id=?",
            (chat_id,)
        ).fetchall()
    
    def delete_custom_role(self, chat_id, role_name):
        role_key = normalize_role_key(role_name)
        self.cursor.execute("DELETE FROM custom_roles WHERE chat_id=? AND role_name=?", (chat_id, role_key))
        self.cursor.execute("DELETE FROM user_roles WHERE chat_id=? AND role=?", (chat_id, role_key))
        self.conn.commit()

    def update_custom_role_permission(self, chat_id, role_ref, permission, enabled):
        role_key = None
        role_data = None
        if str(role_ref).isdigit():
            found = self.get_custom_role_by_level(chat_id, int(role_ref))
            if found:
                role_key, role_data = found
        else:
            role_key = normalize_role_key(role_ref)
            role_data = self.get_custom_role(chat_id, role_key)
        if not role_key or not role_data:
            return None
        column_map = {
            "ban": "can_ban",
            "kick": "can_kick",
            "warn": "can_warn",
            "mute": "can_mute",
            "setrole": "can_set_role",
            "role": "can_set_role",
            "gban": "can_gban",
            "gkick": "can_gkick",
            "gmute": "can_gmute",
            "grole": "can_grole",
        }
        column = column_map.get(permission)
        if not column:
            return None
        self.cursor.execute(
            f"UPDATE custom_roles SET {column}=? WHERE chat_id=? AND role_name=?",
            (1 if enabled else 0, chat_id, role_key)
        )
        self.conn.commit()
        return role_key, self.get_custom_role(chat_id, role_key), column

    def touch_chat(self, chat_id):
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO known_chats (chat_id, last_seen) VALUES (?, ?)",
                (chat_id, int(time.time()))
            )
            self.conn.commit()
        except sqlite3.OperationalError as exc:
            log.warning(f"Failed to touch chat {chat_id}: {exc}")

    def get_known_chats(self):
        return [row[0] for row in self.cursor.execute(
            "SELECT chat_id FROM known_chats ORDER BY last_seen DESC"
        ).fetchall()]

    def link_chat(self, chat_id, added_by):
        self.cursor.execute(
            "INSERT OR REPLACE INTO linked_chats (chat_id, added_by, added_at) VALUES (?, ?, ?)",
            (chat_id, added_by, int(time.time()))
        )
        self.conn.commit()

    def unlink_chat(self, chat_id):
        self.cursor.execute("DELETE FROM linked_chats WHERE chat_id=?", (chat_id,))
        self.conn.commit()

    def clear_linked_chats(self):
        self.cursor.execute("DELETE FROM linked_chats")
        self.conn.commit()

    def get_linked_chats(self):
        return [row[0] for row in self.cursor.execute(
            "SELECT chat_id FROM linked_chats ORDER BY added_at DESC"
        ).fetchall()]

    def is_chat_linked(self, chat_id):
        result = self.cursor.execute("SELECT 1 FROM linked_chats WHERE chat_id=?", (chat_id,)).fetchone()
        return result is not None

    def set_setting(self, key, value):
        self.cursor.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        self.conn.commit()

    def get_setting(self, key, default=None):
        result = self.cursor.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
        return result[0] if result else default

    def increment_user_stat(self, chat_id, user_id):
        now = int(time.time())
        self.cursor.execute(
            """
            INSERT INTO chat_user_stats (chat_id, user_id, messages, first_seen_at, last_message_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET
                messages = messages + 1,
                first_seen_at = COALESCE(chat_user_stats.first_seen_at, excluded.first_seen_at),
                last_message_at = excluded.last_message_at
            """,
            (int(chat_id), int(user_id), now, now)
        )
        self.conn.commit()

    def get_user_stat(self, chat_id, user_id):
        return self.cursor.execute(
            "SELECT messages, COALESCE(first_seen_at, last_message_at), last_message_at FROM chat_user_stats WHERE chat_id=? AND user_id=?",
            (int(chat_id), int(user_id))
        ).fetchone()

    def get_top_chat_stats(self, chat_id, limit=10):
        return self.cursor.execute(
            "SELECT user_id, messages, last_message_at FROM chat_user_stats WHERE chat_id=? ORDER BY messages DESC, last_message_at DESC LIMIT ?",
            (int(chat_id), int(limit))
        ).fetchall()

    def add_forbidden_word(self, chat_id, word, added_by):
        self.cursor.execute(
            "INSERT OR REPLACE INTO forbidden_words (chat_id, word, added_by, added_at) VALUES (?, ?, ?, ?)",
            (int(chat_id), word.lower().strip(), int(added_by), int(time.time()))
        )
        self.conn.commit()

    def remove_forbidden_word(self, chat_id, word):
        self.cursor.execute(
            "DELETE FROM forbidden_words WHERE chat_id=? AND word=?",
            (int(chat_id), word.lower().strip())
        )
        self.conn.commit()

    def get_forbidden_words(self, chat_id):
        return [row[0] for row in self.cursor.execute(
            "SELECT word FROM forbidden_words WHERE chat_id=? ORDER BY word",
            (int(chat_id),)
        ).fetchall()]

    # ---------------------------
    # Economy / monetization
    # ---------------------------
    def _economy_row(self, user_id, chat_id=0):
        chat_id = int(chat_id or 0)
        self.cursor.execute(
            "INSERT OR IGNORE INTO currency (user_id, chat_id, balance, last_daily) VALUES (?, ?, 0, 0)",
            (int(user_id), chat_id)
        )
        return self.cursor.execute(
            "SELECT balance, COALESCE(last_daily, 0) FROM currency WHERE user_id=? AND chat_id=?",
            (int(user_id), chat_id)
        ).fetchone()

    def get_balance(self, user_id, chat_id=0):
        row = self._economy_row(user_id, chat_id)
        return int(row[0]) if row else 0

    def add_balance(self, user_id, amount, chat_id=0):
        amount = int(amount)
        if amount == 0:
            return self.get_balance(user_id, chat_id)
        self._economy_row(user_id, chat_id)
        self.cursor.execute(
            """
            UPDATE currency
            SET balance = CASE WHEN balance + ? < 0 THEN 0 ELSE balance + ? END
            WHERE user_id=? AND chat_id=?
            """,
            (amount, amount, int(user_id), int(chat_id or 0))
        )
        self.conn.commit()
        return self.get_balance(user_id, chat_id)

    def spend_balance(self, user_id, amount, chat_id=0):
        amount = int(amount)
        if amount <= 0:
            return False
        current = self.get_balance(user_id, chat_id)
        if current < amount:
            return False
        self.add_balance(user_id, -amount, chat_id)
        return True

    def can_claim_daily(self, user_id, chat_id=0, cooldown_seconds=86400):
        row = self._economy_row(user_id, chat_id)
        last_daily = int(row[1]) if row else 0
        return int(time.time()) - last_daily >= cooldown_seconds

    def claim_daily(self, user_id, amount, chat_id=0):
        self._economy_row(user_id, chat_id)
        now = int(time.time())
        self.cursor.execute(
            "UPDATE currency SET balance = balance + ?, last_daily=? WHERE user_id=? AND chat_id=?",
            (int(amount), now, int(user_id), int(chat_id or 0))
        )
        self.conn.commit()
        return self.get_balance(user_id, chat_id)

    def get_premium_info(self, user_id):
        return self.cursor.execute(
            "SELECT premium_until, granted_by, reason FROM premium WHERE user_id=?",
            (int(user_id),)
        ).fetchone()

    def is_premium(self, user_id):
        row = self.get_premium_info(user_id)
        return bool(row and int(row[0]) > int(time.time()))

    def grant_premium(self, user_id, days, granted_by, reason=""):
        premium_until = int(time.time()) + max(1, int(days)) * 86400
        current = self.get_premium_info(user_id)
        if current and int(current[0]) > int(time.time()):
            premium_until = max(premium_until, int(current[0]) + max(1, int(days)) * 86400)
        self.cursor.execute(
            "INSERT OR REPLACE INTO premium (user_id, premium_until, granted_by, reason) VALUES (?, ?, ?, ?)",
            (int(user_id), premium_until, int(granted_by), reason)
        )
        self.conn.commit()
        return premium_until

    def extend_premium(self, user_id, days, granted_by, reason=""):
        return self.grant_premium(user_id, days, granted_by, reason)

    def get_top_balances(self, limit=10, chat_id=0):
        return self.cursor.execute(
            "SELECT user_id, balance FROM currency WHERE chat_id=? ORDER BY balance DESC, user_id ASC LIMIT ?",
            (int(chat_id or 0), int(limit))
        ).fetchall()

    def get_daily_streak_reset(self, user_id, chat_id=0):
        row = self._economy_row(user_id, chat_id)
        return int(row[1]) if row else 0

    def _bootstrap_known_chats(self):
        # Seed known chats from existing tables so broadcast works right after restart.
        existing_count = self.cursor.execute("SELECT COUNT(*) FROM known_chats").fetchone()[0]
        if existing_count > 0:
            return

        sources = [
            "chat_settings",
            "user_roles",
            "notes",
            "mutes",
            "warns",
            "banned",
            "nicknames",
            "custom_roles",
        ]
        now = int(time.time())
        chat_ids = set()

        for table_name in sources:
            try:
                rows = self.cursor.execute(f"SELECT DISTINCT chat_id FROM {table_name}").fetchall()
                for row in rows:
                    chat_id = row[0]
                    if isinstance(chat_id, int) and chat_id >= 2000000000:
                        chat_ids.add(chat_id)
            except Exception:
                continue

        for chat_id in chat_ids:
            self.cursor.execute(
                "INSERT OR IGNORE INTO known_chats (chat_id, last_seen) VALUES (?, ?)",
                (chat_id, now)
            )
        self.conn.commit()

db = Database()
vk_http_client = SingleAiohttpClient(trust_env=True)
api = API(VK_TOKEN, http_client=vk_http_client)
api.API_URL = VK_API_URL
api.API_VERSION = VK_API_VERSION
bot = Bot(api=api)

# Get bot user ID once at startup to check for own messages
BOT_USER_ID = None
async def get_bot_user_id():
    global BOT_USER_ID
    try:
        me = await api.users.get()
        if me:
            BOT_USER_ID = me[0].id
            log.info(f"Bot user ID: {BOT_USER_ID}")
    except Exception as e:
        log.error(f"Failed to get bot user ID: {e}")


async def ensure_vk_api_endpoint():
    candidates = [VK_API_URL, VK_API_FALLBACK_URL]
    seen = set()
    last_error = None

    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        api.API_URL = candidate if candidate.endswith("/") else candidate + "/"
        try:
            await api.request("utils.getServerTime", {})
            log.info("VK API endpoint selected: %s", api.API_URL)
            return
        except Exception as exc:
            last_error = exc
            log.warning("VK API endpoint failed: %s (%s)", api.API_URL, exc)

    raise SystemExit(
        "VK API is unreachable from this server. "
        f"Tried: {', '.join(seen) if seen else 'no endpoints'}. "
        f"Last error: {last_error}"
    )

ROLES = {
    "owner": {"name": "Владелец", "level": 100, "can_ban": True, "can_kick": True, "can_warn": True, "can_mute": True, "can_set_role": True, "can_gban": True, "can_gkick": True, "can_gmute": True, "can_grole": True},
    "admin": {"name": "Admin", "level": 80, "can_ban": True, "can_kick": True, "can_warn": True, "can_mute": True, "can_set_role": False, "can_gban": False, "can_gkick": False, "can_gmute": False, "can_grole": False},
    "moderator": {"name": "Moderator", "level": 50, "can_ban": False, "can_kick": True, "can_warn": True, "can_mute": True, "can_set_role": False, "can_gban": False, "can_gkick": False, "can_gmute": False, "can_grole": False},
    "helper": {"name": "Helper", "level": 20, "can_ban": False, "can_kick": False, "can_warn": True, "can_mute": True, "can_set_role": False, "can_gban": False, "can_gkick": False, "can_gmute": False, "can_grole": False},
    "user": {"name": "User", "level": 0, "can_ban": False, "can_kick": False, "can_warn": False, "can_mute": False, "can_set_role": False, "can_gban": False, "can_gkick": False, "can_gmute": False, "can_grole": False}
}

def get_owner_role_name():
    return db.get_setting("owner_role_name", ROLES["owner"]["name"])

def role_permissions_from_level(level):
    level = int(level)
    return {
        "can_ban": level >= 80,
        "can_kick": level >= 50,
        "can_warn": level >= 20,
        "can_mute": level >= 20,
        "can_set_role": level >= 100,
        "can_gban": level >= 100,
        "can_gkick": level >= 100,
        "can_gmute": level >= 100,
        "can_grole": level >= 100,
    }

def build_priority_role(role_name, level):
    permissions = role_permissions_from_level(level)
    return {
        "name": role_name,
        "level": int(level),
        **permissions,
    }

def normalize_role_key(role):
    return (role or "").strip().lower()

def parse_bool_word(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes", "on", "да", "вкл", "включить"}:
        return True
    if normalized in {"false", "0", "no", "off", "нет", "выкл", "выключить"}:
        return False
    return None

def extract_role_argument(parts, has_reply):
    if has_reply:
        return " ".join(parts[1:]).strip() if len(parts) > 1 else ""
    return " ".join(parts[2:]).strip() if len(parts) > 2 else ""

def resolve_role_for_chat(chat_id, role_input):
    key = normalize_role_key(role_input)
    if not key:
        return None, None
    if key == normalize_role_key(get_owner_role_name()) or key == "владелец":
        return "level:100", build_priority_role(get_owner_role_name(), 100)
    if key in ROLES and key != "owner":
        return key, ROLES[key]
    if key.startswith("level:") and key[6:].isdigit():
        level = int(key[6:])
        if level == 0:
            return "user", ROLES["user"]
        if level == 100:
            return "level:100", build_priority_role(get_owner_role_name(), 100)
        if 0 <= level <= 100:
            custom_by_level = db.get_custom_role_by_level(chat_id, level)
            if custom_by_level:
                return custom_by_level
            return f"level:{level}", build_priority_role(f"Priority {level}", level)
    if key.isdigit():
        level = int(key)
        if level == 0:
            return "user", ROLES["user"]
        if level == 100:
            return "level:100", build_priority_role(get_owner_role_name(), 100)
        if 0 <= level <= 100:
            custom_by_level = db.get_custom_role_by_level(chat_id, level)
            if custom_by_level:
                return custom_by_level
            return f"level:{level}", build_priority_role(f"Priority {level}", level)
    for custom_row in db.get_custom_roles(chat_id):
        custom_key = custom_row[0]
        custom_display_name = custom_row[1]
        if key == normalize_role_key(custom_display_name):
            return custom_key, db.get_custom_role(chat_id, custom_key)
    custom_role = db.get_custom_role(chat_id, key)
    if custom_role:
        return key, custom_role
    return None, None

def format_role_list(chat_id):
    def perms_text(role):
        enabled = []
        if role.get("can_ban"):
            enabled.append("ban")
        if role.get("can_kick"):
            enabled.append("kick")
        if role.get("can_warn"):
            enabled.append("warn")
        if role.get("can_mute"):
            enabled.append("mute")
        if role.get("can_set_role"):
            enabled.append("role")
        if role.get("can_gban"):
            enabled.append("gban")
        if role.get("can_gkick"):
            enabled.append("gkick")
        if role.get("can_gmute"):
            enabled.append("gmute")
        if role.get("can_grole"):
            enabled.append("grole")
        return ", ".join(enabled) if enabled else "нет"

    lines = ["Список ролей:", f"{get_owner_role_name()} — 100"]
    for role_key in ("admin", "moderator", "helper", "user"):
        role = ROLES[role_key]
        lines.append(f"{role['name']} — {role['level']} ({perms_text(role)})")
    custom_roles = db.get_custom_roles(chat_id)
    if custom_roles:
        lines.append("")
        lines.append("Созданные роли:")
        for role_key, display_name, level in custom_roles:
            role = db.get_custom_role(chat_id, role_key)
            lines.append(f"{display_name or role_key} — {level} ({perms_text(role or {})})")
    return "\n".join(lines)

def is_owner(user_id):
    return user_id == OWNER_ID

async def get_user_role(chat_id, user_id):
    if is_owner(user_id):
        return {**ROLES["owner"], "name": get_owner_role_name()}
    role_name = db.get_role(user_id, chat_id)
    if role_name:
        resolved_key, resolved_role = resolve_role_for_chat(chat_id, role_name)
        if resolved_role:
            return resolved_role
    # Check if user is VK chat admin (has star)
    try:
        members = await api.messages.get_conversation_members(peer_id=chat_id)
        for m in members.items:
            if m.member_id == user_id:
                if m.is_admin:
                    return ROLES["admin"]
                break
    except:
        pass
    return ROLES["user"]

async def has_permission(chat_id, user_id, permission):
    role = await get_user_role(chat_id, user_id)
    return role.get(permission, False) or is_owner(user_id)

async def can_manage_target(chat_id, actor_id, target_id, action_name="действие"):
    if is_owner(actor_id):
        return True, ""
    if is_owner(target_id):
        return False, "❌ Нельзя применять это к владельцу."
    actor_role = await get_user_role(chat_id, actor_id)
    target_role = await get_user_role(chat_id, target_id)
    if int(target_role.get("level", 0)) >= int(actor_role.get("level", 0)):
        return False, (
            f"❌ Нельзя выполнить {action_name}: роль цели "
            f"{target_role.get('name')} ({target_role.get('level')}) не ниже вашей "
            f"{actor_role.get('name')} ({actor_role.get('level')})."
        )
    return True, ""

async def can_assign_role(chat_id, actor_id, target_id, role_data):
    if is_owner(actor_id):
        return True, ""
    actor_role = await get_user_role(chat_id, actor_id)
    if int(role_data.get("level", 0)) >= int(actor_role.get("level", 0)):
        return False, (
            f"❌ Нельзя выдать роль {role_data.get('name')} ({role_data.get('level')}): "
            f"она не ниже вашей роли {actor_role.get('name')} ({actor_role.get('level')})."
        )
    return await can_manage_target(chat_id, actor_id, target_id, "выдачу роли")


# In-memory anti-spam tracker: key=(chat_id, user_id)
antispam_tracker = {}
zov_last_used = {}
owner_selected_group = {}
antimat_notify_cooldown = {}
ai_last_used = {}
processed_messages = deque()
processed_message_ids = set()
recent_chat_messages = {}


def _cleanup_processed_messages(now: float, ttl_seconds: int = 300) -> None:
    while processed_messages and now - processed_messages[0][0] > ttl_seconds:
        _, message_id = processed_messages.popleft()
        processed_message_ids.discard(message_id)


def _mark_processed_message(message_id) -> bool:
    if message_id in processed_message_ids:
        return False
    processed_message_ids.add(message_id)
    processed_messages.append((time.time(), message_id))
    return True


def remember_chat_message(chat_id, conversation_message_id):
    if not conversation_message_id:
        return
    messages = recent_chat_messages.setdefault(chat_id, deque(maxlen=250))
    messages.append(int(conversation_message_id))


def get_recent_chat_message_ids(chat_id, limit):
    messages = recent_chat_messages.get(chat_id)
    if not messages:
        return []
    limit = max(1, min(int(limit), 100))
    return list(messages)[-limit:]

ANTIMAT_STEMS = (
    "бля", "бляд", "блять", "сук", "хуй", "хуе", "хуя", "хер",
    "пизд", "еба", "ебл", "ебн", "ебуч", "ебат", "долбоеб",
    "мудак", "гандон", "уеб", "мраз", "чмо", "нахуй", "похуй", "оху"
)


def is_antispam_exempt(chat_id, user_id):
    # Anti-spam bypass only for bot owner
    return is_owner(user_id)


def check_antispam(chat_id, user_id, text, attachments_count=0):
    now = time.time()
    key = (chat_id, user_id)
    state = antispam_tracker.setdefault(key, {"times": deque(), "texts": deque()})

    times = state["times"]
    texts = state["texts"]

    while times and now - times[0] > ANTISPAM_FLOOD_WINDOW:
        times.popleft()
    times.append(now)

    while texts and now - texts[0][0] > ANTISPAM_DUPLICATE_WINDOW:
        texts.popleft()

    normalized_text = re.sub(r"\s+", " ", (text or "").strip().lower())
    duplicate_count = 0
    if normalized_text:
        texts.append((now, normalized_text))
        if len(normalized_text) >= 4:
            duplicate_count = sum(1 for _, msg in texts if msg == normalized_text)

    link_count = len(re.findall(r"(https?://\S+|vk\.(?:ru|com)/\S+)", text or "", flags=re.IGNORECASE))

    if len(times) >= ANTISPAM_FLOOD_LIMIT:
        return True, f"флуд ({len(times)} сообщений за {ANTISPAM_FLOOD_WINDOW}с)"
    if duplicate_count >= ANTISPAM_DUPLICATE_LIMIT:
        return True, f"повтор одинаковых сообщений ({duplicate_count})"
    if link_count >= ANTISPAM_LINK_LIMIT:
        return True, f"слишком много ссылок ({link_count})"
    if attachments_count >= ANTISPAM_ATTACHMENTS_LIMIT:
        return True, f"слишком много вложений ({attachments_count})"
    return False, ""


def clear_antispam_state(chat_id, user_id):
    antispam_tracker.pop((chat_id, user_id), None)


def contains_profanity(text):
    if not text:
        return False
    normalized = text.lower().replace("ё", "е")
    tokens = re.findall(r"[a-zа-я0-9_]+", normalized)
    for token in tokens:
        if len(token) < 3:
            continue
        for stem in ANTIMAT_STEMS:
            if token.startswith(stem):
                return True
    return False


def parse_mute_duration(raw_value):
    value = (raw_value or "").strip().lower()
    if not value:
        return None

    # Backward compatibility: plain number means minutes.
    if value.isdigit():
        minutes = int(value)
        if minutes <= 0:
            return None
        return minutes * 60, f"{minutes} мин."

    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        return None

    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    labels = {"s": "сек.", "m": "мин.", "h": "ч.", "d": "д."}
    return amount * multiplier[unit], f"{amount} {labels[unit]}"


def parse_ban_duration(raw_value):
    value = (raw_value or "").strip().lower()
    if not value:
        return None

    # Backward compatibility: plain number means days.
    if value.isdigit():
        days = int(value)
        if days <= 0:
            return None
        return {"seconds": days * 86400, "text": f"{days} дн.", "is_days": True}

    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        return None

    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    labels = {"s": "сек.", "m": "мин.", "h": "ч.", "d": "д."}
    return {"seconds": amount * multiplier[unit], "text": f"{amount} {labels[unit]}", "is_days": unit == "d"}

def extract_user_id(text, reply_message=None):
    if reply_message:
        return reply_message.from_id
    # @id123456 format
    match = re.search(r'@id(\d+)', text)
    if match:
        return int(match.group(1))
    # [id123456|name] format
    match = re.search(r'\[id(\d+)\|', text)
    if match:
        return int(match.group(1))
    # [club123456|name] or [public123456|name] format (VK communities/bots)
    match = re.search(r'\[(?:club|public)(\d+)\|', text, flags=re.IGNORECASE)
    if match:
        return -int(match.group(1))
    # @username format
    match = re.search(r'@([a-zA-Z][a-zA-Z0-9_.-]{2,})', text)
    if match:
        return f"username:{match.group(1)}"
    # @club123456 or @public123456 format
    match = re.search(r'@(?:club|public)(\d+)', text, flags=re.IGNORECASE)
    if match:
        return -int(match.group(1))
    # https://vk.ru/id123456 or https://vk.com/id123456 (numeric ID)
    match = re.search(r'vk\.(?:ru|com)/id(\d+)', text)
    if match:
        return int(match.group(1))
    # https://vk.ru/club123456 or https://vk.com/public123456 (community numeric ID)
    match = re.search(r'vk\.(?:ru|com)/(?:club|public)(\d+)', text, flags=re.IGNORECASE)
    if match:
        return -int(match.group(1))
    # https://vk.ru/id_username or https://vk.com/id_username (screen name with id prefix)
    match = re.search(r'vk\.(?:ru|com)/(id_[a-zA-Z][a-zA-Z0-9_.-]+)', text)
    if match:
        return f"username:{match.group(1)}"
    # https://vk.ru/username or https://vk.com/username (short link)
    match = re.search(r'vk\.(?:ru|com)/([a-zA-Z][a-zA-Z0-9_.-]{2,})', text)
    if match:
        return f"username:{match.group(1)}"
    # Plain ID (6+ digits)
    match = re.search(r'(\d{6,})', text)
    if match:
        return int(match.group(1))
    return None

async def resolve_username(username):
    """Resolve VK username to user ID"""
    try:
        log.info(f"Resolving username: {username}")
        result = await api.utils.resolve_screen_name(screen_name=username)
        log.info(f"Resolve result: {result}")
        if result and hasattr(result, 'type'):
            if result.type == "user":
                return result.object_id
            if result.type in ("group", "page", "event"):
                return -result.object_id
    except Exception as e:
        log.error(f"Error resolving username {username}: {e}")
    return None

async def get_target_id(text, reply_message=None):
    """Extract user ID, resolving username if needed"""
    target = extract_user_id(text, reply_message)
    log.info(f"Extracted target: {target}")
    if isinstance(target, str) and target.startswith("username:"):
        username = target[9:]
        resolved = await resolve_username(username)
        if resolved:
            return resolved
        return None
    return target


ECONOMY_SCOPE_ID = 0
DAILY_REWARD = 100
VIP_DAILY_REWARD = 150
VIP_PRICE_COINS = 500

SHOP_ITEMS = {
    "vip_30": {"price": VIP_PRICE_COINS, "days": 30, "title": "VIP на 30 дней"},
    "vip_90": {"price": 1200, "days": 90, "title": "VIP на 90 дней"},
    "vip_365": {"price": 3500, "days": 365, "title": "VIP на 365 дней"},
}

GAME_EMOJIS = ["🍒", "🍋", "🍇", "⭐", "💎", "7️⃣"]


def format_coins(amount):
    amount = int(amount)
    return f"🪙 {amount:,}".replace(",", " ")


async def ask_openrouter(prompt, user_context=""):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("empty prompt")
    if len(prompt) > AI_MAX_PROMPT_CHARS:
        prompt = prompt[:AI_MAX_PROMPT_CHARS]

    moscow_now = datetime.now(timezone(timedelta(hours=3)))

    system_prompt = (
        "Ты полезный ассистент внутри VK чат-бота. "
        "Отвечай по-русски, кратко и понятно. "
        f"Текущая дата и время по Москве (GMT+03:00): {moscow_now.strftime('%d.%m.%Y %H:%M')}. "
        f"Текущий год: {moscow_now.year}. "
        "Если спрашивают дату, время или год, используй именно эти данные. "
        "Не используй markdown-таблицы. Если вопрос про команды бота, отвечай практично."
    )
    if user_context:
        system_prompt += f" Контекст пользователя: {user_context}"

    time_context = (
        f"Контекст времени: сейчас в Москве (GMT+03:00) {moscow_now.strftime('%d.%m.%Y %H:%M')} "
        f"(год {moscow_now.year}). Если вопрос про текущее время или дату, отвечай строго по этому контексту.\n\n"
        f"Вопрос пользователя: {prompt}"
    )

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": time_context},
        ],
        "temperature": 0.7,
        "max_tokens": 700,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }
    if OPENROUTER_REFERER:
        headers["HTTP-Referer"] = OPENROUTER_REFERER
    if OPENROUTER_APP_TITLE:
        headers["X-OpenRouter-Title"] = OPENROUTER_APP_TITLE

    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.post(OPENROUTER_API_URL, headers=headers, json=payload) as response:
            raw_text = await response.text()
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                raise RuntimeError(f"OpenRouter returned invalid JSON: {raw_text[:300]}")

            if response.status >= 400:
                error_data = data.get("error") if isinstance(data, dict) else None
                if isinstance(error_data, dict):
                    error_message = error_data.get("message") or str(error_data)
                else:
                    error_message = raw_text[:300]
                raise RuntimeError(f"OpenRouter API error {response.status}: {error_message}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned empty choices")
    content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("OpenRouter returned empty response")
    if len(content) > AI_MAX_REPLY_CHARS:
        content = content[:AI_MAX_REPLY_CHARS].rstrip() + "\n\n…ответ сокращён"
    return content

def get_premium_status_text(user_id):
    info = db.get_premium_info(user_id)
    if not info:
        return "обычный"
    premium_until = int(info[0])
    if premium_until <= int(time.time()):
        return "обычный"
    remaining_days = max(1, (premium_until - int(time.time()) + 86399) // 86400)
    return f"VIP, осталось {remaining_days} дн."


def parse_positive_int(value):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def pick_slots():
    return random.choice(GAME_EMOJIS), random.choice(GAME_EMOJIS), random.choice(GAME_EMOJIS)


def calc_slots_payout(bet, a, b, c):
    if a == b == c:
        return bet * 5
    if a == b or a == c or b == c:
        return bet * 2
    return 0


async def handle_economy_command(message: Message, text: str, user_id: int) -> bool:
    raw = (text or "").strip()
    if not raw or not raw.startswith("/"):
        return False

    display_chat_id = message.peer_id if getattr(message, "peer_id", 0) >= 2000000000 else None
    parts = raw.split()
    command = parts[0].lower()
    args = parts[1:]
    reply = getattr(message, "reply_message", None)

    if command in {"/coins", "/balance", "/монеты", "/баланс"}:
        balance = db.get_balance(user_id, ECONOMY_SCOPE_ID)
        premium_text = get_premium_status_text(user_id)
        daily_ready = "готов" if db.can_claim_daily(user_id, ECONOMY_SCOPE_ID) else "уже получен сегодня"
        await message.answer(
            f"🪙 Баланс: {format_coins(balance)}\n"
            f"⭐ Статус: {premium_text}\n"
            f"🎁 Дэйлик: {daily_ready}\n\n"
            "Команды: /daily, /shop, /leaderboard, /coinflip, /slots, /dice"
        )
        return True

    if command in {"/daily", "/дэйлик", "/ежедневно"}:
        if not db.can_claim_daily(user_id, ECONOMY_SCOPE_ID):
            last_daily = db.get_daily_streak_reset(user_id, ECONOMY_SCOPE_ID)
            next_time = datetime.fromtimestamp(last_daily + 86400)
            await message.answer(f"⏳ Дэйлик уже получен. Следующая награда будет доступна после {next_time:%d.%m %H:%M}.")
            return True

        reward = VIP_DAILY_REWARD if db.is_premium(user_id) else DAILY_REWARD
        balance = db.claim_daily(user_id, reward, ECONOMY_SCOPE_ID)
        await message.answer(
            f"🎁 Ты получил {format_coins(reward)}.\n"
            f"Текущий баланс: {format_coins(balance)}"
        )
        return True

    if command in {"/shop", "/store", "/магазин"}:
        lines = [
            "🛒 Магазин монет:",
            f"1) VIP на 30 дней - {format_coins(SHOP_ITEMS['vip_30']['price'])}",
            f"2) VIP на 90 дней - {format_coins(SHOP_ITEMS['vip_90']['price'])}",
            f"3) VIP на 365 дней - {format_coins(SHOP_ITEMS['vip_365']['price'])}",
            "",
            "Покупка: /buy vip 30",
            "Игры: /coinflip heads 100, /slots 100, /dice 50",
        ]
        await message.answer("\n".join(lines))
        return True

    if command in {"/leaderboard", "/top", "/топ"}:
        rows = db.get_top_balances(limit=10, chat_id=ECONOMY_SCOPE_ID)
        if not rows:
            await message.answer("Пока нет игроков с монетами.")
            return True

        lines = ["🏆 Топ монет:"]
        for index, row in enumerate(rows, 1):
            uid = int(row[0])
            balance = int(row[1])
            name = await mention_user(uid, display_chat_id)
            lines.append(f"{index}. {name} — {format_coins(balance)}")
        await message.answer("\n".join(lines))
        return True

    if command == "/buy":
        if not args:
            await message.answer("Использование: /buy vip 30")
            return True
        item = args[0].lower()
        if item in {"vip", "premium"}:
            days = 30
            if len(args) > 1:
                parsed_days = parse_positive_int(args[1])
                if parsed_days:
                    days = parsed_days
            if days not in {30, 90, 365}:
                await message.answer("Доступны только VIP на 30, 90 или 365 дней.")
                return True
            sku = f"vip_{days}"
            price = SHOP_ITEMS[sku]["price"]
            if not db.spend_balance(user_id, price, ECONOMY_SCOPE_ID):
                await message.answer(f"Не хватает монет. Нужно {format_coins(price)}.")
                return True
            premium_until = db.extend_premium(user_id, days, OWNER_ID, reason=f"purchase:{sku}")
            expiry = datetime.fromtimestamp(premium_until).strftime("%d.%m.%Y %H:%M")
            await message.answer(
                f"✅ Покупка успешна: {SHOP_ITEMS[sku]['title']}\n"
                f"Остаток: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}\n"
                f"VIP активен до {expiry}."
            )
            return True
        await message.answer("Неизвестный товар. Открой /shop.")
        return True

    if command in {"/coinflip", "/flip"}:
        if len(args) < 2:
            await message.answer("Использование: /coinflip heads 100")
            return True
        side = args[0].lower()
        bet = parse_positive_int(args[1])
        if bet is None:
            await message.answer("Ставка должна быть положительным числом.")
            return True
        if db.get_balance(user_id, ECONOMY_SCOPE_ID) < bet:
            await message.answer("Недостаточно монет для ставки.")
            return True
        if side in {"орел", "орёл", "heads"}:
            chosen = "heads"
        elif side in {"решка", "tails"}:
            chosen = "tails"
        else:
            await message.answer("Выбери сторону: heads/tails или орёл/решка.")
            return True
        db.spend_balance(user_id, bet, ECONOMY_SCOPE_ID)
        result = random.choice(["heads", "tails"])
        if result == chosen:
            payout = bet * 2
            db.add_balance(user_id, payout, ECONOMY_SCOPE_ID)
            await message.answer(
                f"🪙 Выпало: {result}\n"
                f"🎉 Ты выиграл {format_coins(bet)}.\n"
                f"Баланс: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}"
            )
        else:
            await message.answer(
                f"🪙 Выпало: {result}\n"
                f"❌ Ты проиграл {format_coins(bet)}.\n"
                f"Баланс: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}"
            )
        return True

    if command in {"/dice", "/roll"}:
        bet = 0
        if args:
            bet = parse_positive_int(args[0]) or 0
        if bet and db.get_balance(user_id, ECONOMY_SCOPE_ID) < bet:
            await message.answer("Недостаточно монет для ставки.")
            return True
        if bet:
            db.spend_balance(user_id, bet, ECONOMY_SCOPE_ID)
        roll = random.randint(1, 6)
        payout = 0
        if roll == 6 and bet:
            payout = bet * 6
        elif roll in {4, 5} and bet:
            payout = bet * 2
        if payout:
            db.add_balance(user_id, payout, ECONOMY_SCOPE_ID)
        text_result = f"🎲 Выпало: {roll}"
        if bet:
            if payout:
                text_result += f"\n✅ Ты выиграл {format_coins(payout - bet)}"
            else:
                text_result += f"\n❌ Ставка {format_coins(bet)} проиграна"
        text_result += f"\nБаланс: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}"
        await message.answer(text_result)
        return True

    if command in {"/slots", "/slot"}:
        bet = parse_positive_int(args[0]) if args else None
        if bet is None:
            await message.answer("Использование: /slots 100")
            return True
        if db.get_balance(user_id, ECONOMY_SCOPE_ID) < bet:
            await message.answer("Недостаточно монет для ставки.")
            return True
        db.spend_balance(user_id, bet, ECONOMY_SCOPE_ID)
        a, b, c = pick_slots()
        payout = calc_slots_payout(bet, a, b, c)
        if payout:
            db.add_balance(user_id, payout, ECONOMY_SCOPE_ID)
        await message.answer(
            f"🎰 {a} | {b} | {c}\n"
            f"{'🎉 Выигрыш ' + format_coins(payout - bet) if payout else '❌ Ставка проиграна'}\n"
            f"Баланс: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}"
        )
        return True

    if command == "/givecoins":
        if not is_owner(user_id):
            await message.answer("Команда доступна только владельцу.")
            return True
        if len(args) < 2:
            await message.answer("Использование: /givecoins @user 1000")
            return True
        target = await get_target_id(args[0], reply)
        amount = parse_positive_int(args[1])
        if not target or amount is None:
            await message.answer("Не удалось определить пользователя или сумму.")
            return True
        db.add_balance(target, amount, ECONOMY_SCOPE_ID)
        target_name = await mention_user(target, display_chat_id)
        await message.answer(f"✅ Начислено {format_coins(amount)} пользователю {target_name}.")
        return True

    if command == "/setvip":
        if not is_owner(user_id):
            await message.answer("Команда доступна только владельцу.")
            return True
        if len(args) < 2:
            await message.answer("Использование: /setvip @user 30")
            return True
        target = await get_target_id(args[0], reply)
        days = parse_positive_int(args[1])
        if not target or days is None:
            await message.answer("Не удалось определить пользователя или срок VIP.")
            return True
        premium_until = db.extend_premium(target, days, user_id, reason="owner_grant")
        expiry = datetime.fromtimestamp(premium_until).strftime("%d.%m.%Y %H:%M")
        target_name = await mention_user(target, display_chat_id)
        await message.answer(f"✅ VIP выдан {target_name} до {expiry}.")
        return True

    return False

async def get_user_name(user_id):
    try:
        users = await api.users.get(user_ids=[str(user_id)])
        if users:
            return f"{users[0].first_name} {users[0].last_name}"
    except:
        pass
    return f"id{user_id}"

async def get_display_name(user_id, chat_id):
    """Get user display name with nickname if set"""
    nickname = db.get_nickname(user_id, chat_id)
    if nickname:
        return f"«{nickname}»"
    return await get_user_name(user_id)

def sanitize_vk_link_label(value):
    label = str(value or "Пользователь").strip()
    label = label.replace("[", "(").replace("]", ")").replace("|", " ")
    return label or "Пользователь"

async def mention_user(user_id, chat_id=None):
    if user_id is None:
        return "Неизвестно"
    display_name = None
    if chat_id is not None:
        display_name = db.get_nickname(user_id, chat_id)
    if not display_name:
        display_name = await get_user_name(user_id)
    display_name = sanitize_vk_link_label(display_name)
    if int(user_id) > 0:
        return f"[id{int(user_id)}|{display_name}]"
    return f"[club{abs(int(user_id))}|{display_name}]"

async def staff_display_user(user_id, chat_id, mode):
    if mode == "nick":
        nickname = db.get_nickname(user_id, chat_id)
        if nickname:
            return f"[id{int(user_id)}|{sanitize_vk_link_label(nickname)}]"
    return await mention_user(user_id, None)

async def build_staff_text(chat_id, mode="nick"):
    grouped = {}

    owner_title = get_owner_role_name()
    grouped.setdefault((100, owner_title), [])
    grouped[(100, owner_title)].append(OWNER_ID)

    for role_key in ("admin", "moderator", "helper"):
        role = ROLES[role_key]
        if 20 <= role["level"] <= 100:
            grouped.setdefault((role["level"], role["name"]), [])

    for _role_key, display_name, level in db.get_custom_roles(chat_id):
        level = int(level)
        if 20 <= level <= 100:
            grouped.setdefault((level, display_name), [])

    staff_chat_ids = [chat_id]
    if db.get_linked_chats():
        staff_chat_ids = get_global_chat_peer_ids_sync(chat_id)

    best_by_user = {}
    for staff_chat_id in staff_chat_ids:
        for uid, role_key in db.get_all_user_roles(staff_chat_id):
            _, role_data = resolve_role_for_chat(staff_chat_id, role_key)
            if role_data and role_data.get("level", 0) >= 20:
                current = best_by_user.get(uid)
                if current is None or role_data["level"] > current[0]:
                    best_by_user[uid] = (role_data["level"], role_data["name"])

    for uid, (level, role_name) in best_by_user.items():
        if uid == OWNER_ID:
            continue
        grouped.setdefault((level, role_name), [])
        if uid not in grouped[(level, role_name)]:
            grouped[(level, role_name)].append(uid)

    lines = ["👥 Администрация:"]
    for (level, role_name), user_ids in sorted(grouped.items(), key=lambda item: item[0][0], reverse=True):
        lines.append("")
        title = f"🔥 {role_name}:" if level >= 100 else f"{role_name}:"
        lines.append(title)
        for uid in sorted(user_ids):
            display = await staff_display_user(uid, chat_id, mode)
            lines.append(f"— {display}")

    if len(lines) == 1:
        lines.append("\nНет назначенных ролей.")
    return "\n".join(lines)

def staff_keyboard(mode):
    next_mode = "name" if mode == "nick" else "nick"
    label = "имена" if mode == "nick" else "ники"
    keyboard = Keyboard(inline=True)
    keyboard.add(Text(label, payload={"command": "staff_toggle", "mode": next_mode}))
    return keyboard

def format_ban_until_timestamp(banned_until):
    if not banned_until:
        return "Навсегда"
    try:
        return datetime.fromtimestamp(int(banned_until)).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "Неизвестно"

async def get_global_chat_peer_ids():
    linked = db.get_linked_chats()
    peer_ids = linked if linked else await get_all_chat_peer_ids()
    return [peer_id for peer_id in peer_ids if peer_id >= 2000000000]

def get_global_chat_peer_ids_sync(current_chat_id=None):
    linked = db.get_linked_chats()
    if linked:
        return [peer_id for peer_id in linked if peer_id >= 2000000000]
    known = db.get_known_chats()
    if known:
        return [peer_id for peer_id in known if peer_id >= 2000000000]
    return [current_chat_id] if current_chat_id else []

async def apply_global_ban(target_id, actor_id, reason, duration_seconds=None):
    peer_ids = await get_global_chat_peer_ids()
    results = []
    for peer_id in peer_ids:
        try:
            allowed, _ = await can_manage_target(peer_id, actor_id, target_id, "глобальный бан")
            if not allowed:
                continue
            db.ban_user(target_id, peer_id, reason, actor_id, duration_seconds=duration_seconds)
            await kick_user(peer_id, target_id)
            results.append(peer_id)
        except Exception as exc:
            log.error(f"Error applying global ban in {peer_id}: {exc}")
    return results

async def apply_global_mute(target_id, actor_id, reason, duration_seconds):
    peer_ids = await get_global_chat_peer_ids()
    results = []
    for peer_id in peer_ids:
        try:
            allowed, _ = await can_manage_target(peer_id, actor_id, target_id, "глобальный мут")
            if not allowed:
                continue
            db.mute_user(target_id, peer_id, duration_seconds / 60, actor_id, reason)
            results.append(peer_id)
        except Exception as exc:
            log.error(f"Error applying global mute in {peer_id}: {exc}")
    return results

async def apply_global_kick(target_id, actor_id=None):
    peer_ids = await get_global_chat_peer_ids()
    results = []
    for peer_id in peer_ids:
        try:
            if actor_id is not None:
                allowed, _ = await can_manage_target(peer_id, actor_id, target_id, "глобальный кик")
                if not allowed:
                    continue
            if await kick_user(peer_id, target_id):
                results.append(peer_id)
        except Exception as exc:
            log.error(f"Error applying global kick in {peer_id}: {exc}")
    return results

async def apply_global_role(target_id, role, actor_id):
    peer_ids = await get_global_chat_peer_ids()
    results = []
    for peer_id in peer_ids:
        try:
            db.set_role(target_id, peer_id, role, actor_id)
            results.append(peer_id)
        except Exception as exc:
            log.error(f"Error applying global role in {peer_id}: {exc}")
    return results

async def apply_global_remove_role(target_id):
    peer_ids = await get_global_chat_peer_ids()
    results = []
    for peer_id in peer_ids:
        try:
            db.remove_role(target_id, peer_id)
            results.append(peer_id)
        except Exception as exc:
            log.error(f"Error removing global role in {peer_id}: {exc}")
    return results

async def kick_user(chat_id, user_id):
    try:
        local_chat_id = chat_id - 2000000000
        # Primary way: member_id supports both users and communities (negative IDs).
        try:
            await api.messages.remove_chat_user(chat_id=local_chat_id, member_id=user_id)
            if not is_owner(user_id):
                db.remove_role(user_id, chat_id)
            return True
        except Exception as e_member:
            # Fallback for API variations.
            try:
                fallback_user_id = abs(user_id) if user_id < 0 else user_id
                await api.messages.remove_chat_user(chat_id=local_chat_id, user_id=fallback_user_id)
                if not is_owner(user_id):
                    db.remove_role(user_id, chat_id)
                return True
            except Exception:
                log.error(f"Error kicking: {e_member}")
                return False
    except Exception as e:
        log.error(f"Error kicking: {e}")
        return False


async def get_all_chat_peer_ids():
    # Primary source: chats where bot already received messages.
    chat_peer_ids = db.get_known_chats()
    if chat_peer_ids:
        return sorted(set(chat_peer_ids))

    # Fallback: try to discover chats from conversations API.
    discovered = []
    offset = 0
    batch_size = 200

    while True:
        result = await api.messages.get_conversations(offset=offset, count=batch_size)
        items = getattr(result, "items", None) or []
        if not items:
            break

        for item in items:
            conversation = getattr(item, "conversation", None)
            peer = getattr(conversation, "peer", None) if conversation else None
            peer_id = getattr(peer, "id", None) if peer else None
            peer_type = getattr(getattr(peer, "type", None), "value", getattr(peer, "type", None)) if peer else None
            if peer_id and (peer_type == "chat" or peer_id >= 2000000000):
                discovered.append(peer_id)

        if len(items) < batch_size:
            break
        offset += batch_size

    return sorted(set(discovered))


async def get_chat_titles(peer_ids):
    titles = {}
    if not peer_ids:
        return titles

    for i in range(0, len(peer_ids), 100):
        chunk = peer_ids[i:i + 100]
        try:
            result = await api.messages.get_conversations_by_id(peer_ids=chunk)
            for item in getattr(result, "items", None) or []:
                peer = getattr(item, "peer", None)
                peer_id = getattr(peer, "id", None) if peer else None
                chat_settings = getattr(item, "chat_settings", None)
                title = getattr(chat_settings, "title", None) if chat_settings else None
                if peer_id:
                    titles[peer_id] = title or f"Чат {peer_id}"
        except Exception as e:
            log.error(f"Error loading chat titles for chunk: {e}")

    for peer_id in peer_ids:
        if peer_id not in titles:
            titles[peer_id] = f"Чат {peer_id}"
    return titles


def build_groups_keyboard(peer_ids, titles, page=1):
    total = len(peer_ids)
    max_page = max(1, (total + GROUPS_PAGE_SIZE - 1) // GROUPS_PAGE_SIZE)
    page = max(1, min(page, max_page))
    start = (page - 1) * GROUPS_PAGE_SIZE
    current_ids = peer_ids[start:start + GROUPS_PAGE_SIZE]

    keyboard = Keyboard(inline=True)
    for idx, peer_id in enumerate(current_ids):
        title = titles.get(peer_id, f"Чат {peer_id}")
        short_title = title if len(title) <= 36 else title[:33] + "..."
        keyboard.add(Text(short_title, payload={"command": "owner_select_group", "chat_id": peer_id}))
        # Put 2 buttons per row to keep VK keyboard row count low.
        if idx % 2 == 1 and idx != len(current_ids) - 1:
            keyboard.row()

    if max_page > 1:
        keyboard.row()
        if page > 1:
            keyboard.add(Text("◀️ Назад", payload={"command": "owner_groups_page", "page": page - 1}))
        if page < max_page:
            keyboard.add(Text("▶️ Вперед", payload={"command": "owner_groups_page", "page": page + 1}))

    keyboard.row()
    keyboard.add(Text("❌ Сбросить выбор", payload={"command": "owner_clear_group"}))
    return keyboard, page, max_page


async def broadcast_owner_message_to_chats(text):
    peer_ids = await get_all_chat_peer_ids()
    sent = 0
    failed = 0

    for peer_id in peer_ids:
        try:
            await api.messages.send(
                peer_id=peer_id,
                message=text,
                random_id=random.randint(0, 2**31)
            )
            sent += 1
        except Exception as e:
            failed += 1
            log.error(f"Owner broadcast failed for chat {peer_id}: {e}")

    return sent, failed, len(peer_ids)


async def check_expired_mutes():
    while True:
        try:
            await asyncio.sleep(EXPIRE_CHECK_INTERVAL_SECONDS)
            expired_mutes = db.get_expired_mutes()
            for user_id, chat_id, muted_by, reason in expired_mutes:
                user_name = await mention_user(user_id, chat_id)
                try:
                    await api.messages.send(
                        peer_id=chat_id,
                        message=f"🔊 Мут истёк!\n\n{user_name} (id{user_id})\n✅ Теперь может писать в чате!",
                        random_id=random.randint(0, 2**31)
                    )
                    log.info(f"Mute expired for user {user_id} in chat {chat_id}")
                except Exception as e:
                    log.error(f"Error notifying about expired mute: {e}")
            db.cleanup_expired_mutes()
        except Exception as e:
            log.error(f"Error in background task: {e}")


async def check_expired_bans():
    while True:
        try:
            await asyncio.sleep(EXPIRE_CHECK_INTERVAL_SECONDS)
            expired_bans = db.get_expired_bans()
            for user_id, chat_id, reason, banned_by in expired_bans:
                user_name = await mention_user(user_id, chat_id)
                try:
                    await api.messages.send(
                        peer_id=chat_id,
                        message=f"✅ Пользователь разбанен: {user_name} (id{user_id})\n⏱ Срок бана истек.",
                        random_id=random.randint(0, 2**31)
                    )
                    log.info(f"Ban expired for user {user_id} in chat {chat_id}")
                except Exception as e:
                    log.error(f"Error notifying about expired ban: {e}")
            db.cleanup_expired_bans()
        except Exception as e:
            log.error(f"Error in ban background task: {e}")

@bot.labeler.private_message()
async def handle_private(message: Message):
    text = message.text or ""
    user_id = message.from_id
    
    # Deduplicate messages
    now = time.time()
    _cleanup_processed_messages(now)
    message_identifier = (
        message.conversation_message_id
        if getattr(message, "conversation_message_id", None) is not None
        else getattr(message, "id", None)
    )
    if message_identifier is not None and not _mark_processed_message(message_identifier):
        return
    
    log.info(f"Private: '{text}' from {user_id}")

    if await handle_economy_command(message, text, user_id):
        return

    if is_owner(user_id) and message.payload:
        try:
            payload = json.loads(message.payload) if isinstance(message.payload, str) else message.payload
            if isinstance(payload, dict):
                command = payload.get("command")
                if command == "owner_select_group":
                    chat_id = payload.get("chat_id")
                    if isinstance(chat_id, int) and chat_id >= 2000000000:
                        owner_selected_group[user_id] = chat_id
                        titles = await get_chat_titles([chat_id])
                        selected_title = titles.get(chat_id, f"Чат {chat_id}")
                        await message.answer(
                            f"✅ Выбрана группа: {selected_title}\n"
                            "Теперь отправьте текст без команды, и я отправлю его в выбранную группу."
                        )
                        return
                elif command == "owner_groups_page":
                    page = payload.get("page", 1)
                    if not isinstance(page, int):
                        page = 1
                    peer_ids = await get_all_chat_peer_ids()
                    if not peer_ids:
                        await message.answer("❌ Не найдено групп/чатов для выбора.")
                        return
                    titles = await get_chat_titles(peer_ids)
                    keyboard, current_page, max_page = build_groups_keyboard(peer_ids, titles, page)
                    selected_chat_id = owner_selected_group.get(user_id)
                    selected_title = titles.get(selected_chat_id, f"Чат {selected_chat_id}") if selected_chat_id else "не выбрана"
                    await message.answer(
                        f"📋 Группы/чаты бота: {len(peer_ids)}\n"
                        f"📄 Страница: {current_page}/{max_page}\n"
                        f"🎯 Выбрано: {selected_title}\n\n"
                        "👇 Выберите кнопку чата ниже:",
                        keyboard=keyboard.get_json()
                    )
                    return
                elif command == "owner_clear_group":
                    owner_selected_group.pop(user_id, None)
                    await message.answer("✅ Выбор группы сброшен. Используйте /groups.")
                    return
        except Exception as e:
            log.error(f"Error handling owner private payload: {e}")
    
    if text == "/start":
        await message.answer(
            f"🤖 VK Чат Менеджер Бот\n\n"
            f"👤 Владелец: id{OWNER_ID}\n\n"
            f"📝 Команды:\n"
            f"/start - Начать\n"
            f"/help - Помощь\n"
            f"/profile - Профиль\n"
            f"/groups - Выбрать группу для отправки\n"
            f"/coins - баланс монет\n"
            f"/daily - ежедневная награда\n"
            f"/shop - магазин VIP\n"
            f"/coinflip, /slots, /dice - игры на монеты\n\n"
            f"➕ Добавьте бота в чат!"
        )
        return
    elif text == "/help":
        await message.answer(
            "📚 Помощь\n\n"
            "🔹 Основные:\n"
            "/start - Начать\n"
            "/help - Помощь\n"
            "/profile - Профиль\n"
            "/groups - Список групп/чатов бота (кнопки выбора)\n\n"
            "👑 Для владельца:\n"
            "1) Выберите группу через /groups\n"
            "2) Отправьте текст без /команды в ЛС\n"
            "3) Бот отправит его в выбранную группу\n"
            "/linkchat list/add/remove/all/clear - объединение чатов для глобальных команд\n"
            "P.S Данил Михайлов будет добавлено автоматически.\n\n"
            "👑 Роли:\n"
            "Владелец - Полный доступ\n"
            "Администратор - Бан, кик, варн, мут\n"
            "Модератор - Кик, варн, мут\n"
            "Хелпер - Варн, мут\n"
            "Пользователь - Базовые команды\n\n"
            "🎮 Монеты и игры:\n"
            "/coins - баланс\n"
            "/daily - ежедневная награда\n"
            "/shop - магазин VIP\n"
            "/buy vip 30 - покупка VIP за монеты\n"
            "/coinflip heads 100 - игра в орёл/решку\n"
            "/slots 100 - слот-машина\n"
            "/dice 50 - кубик на ставку\n"
            "/leaderboard - топ монет"
        )
        return
    elif text == "/profile":
        name = await mention_user(user_id, chat_id)
        status = "👑 Владелец" if is_owner(user_id) else "👤 Пользователь"
        await message.answer(f"👤 Профиль\n\n📝 Имя: {name}\n🆔 ID: {user_id}\n⭐ Статус: {status}")
        return
    elif text.startswith("/groups"):
        if not is_owner(user_id):
            await message.answer("❌ Команда доступна только владельцу.")
            return
        parts = text.split(maxsplit=1)
        page = 1
        if len(parts) > 1:
            arg = parts[1].strip()
            if arg.isdigit():
                page = max(1, int(arg))
            elif arg.lower().startswith("select "):
                chat_id_raw = arg[7:].strip()
                if chat_id_raw.isdigit():
                    selected_chat_id = int(chat_id_raw)
                    peer_ids = await get_all_chat_peer_ids()
                    if selected_chat_id in peer_ids:
                        owner_selected_group[user_id] = selected_chat_id
                        titles = await get_chat_titles([selected_chat_id])
                        selected_title = titles.get(selected_chat_id, f"Чат {selected_chat_id}")
                        await message.answer(
                            f"✅ Выбрана группа: {selected_title}\n"
                            "Теперь отправьте текст без команды, и я отправлю его в выбранную группу."
                        )
                        return
                await message.answer("❌ Неверный chat_id. Используйте /groups для списка.")
                return
        peer_ids = await get_all_chat_peer_ids()
        if not peer_ids:
            await message.answer("❌ Не найдено групп/чатов для выбора.")
            return
        titles = await get_chat_titles(peer_ids)
        keyboard, current_page, max_page = build_groups_keyboard(peer_ids, titles, page)
        selected_chat_id = owner_selected_group.get(user_id)
        selected_title = titles.get(selected_chat_id, f"Чат {selected_chat_id}") if selected_chat_id else "не выбрана"
        try:
            await message.answer(
                f"📋 Группы/чаты бота: {len(peer_ids)}\n"
                f"📄 Страница: {current_page}/{max_page}\n"
                f"🎯 Выбрано: {selected_title}\n\n"
                "👇 Выберите кнопку чата ниже.\n"
                "После выбора отправьте текст без команды.",
                keyboard=keyboard.get_json()
            )
            return
        except Exception as e:
            log.error(f"Error sending groups keyboard: {e}")
            start = (current_page - 1) * GROUPS_PAGE_SIZE
            chunk = peer_ids[start:start + GROUPS_PAGE_SIZE]
            lines = []
            for cid in chunk:
                lines.append(f"{cid} — {titles.get(cid, f'Чат {cid}')}")
            await message.answer(
                f"📋 Группы/чаты бота: {len(peer_ids)}\n"
                f"📄 Страница: {current_page}/{max_page}\n"
                f"🎯 Выбрано: {selected_title}\n\n"
                + "\n".join(lines) +
                "\n\nВыбор без кнопок: /groups select <chat_id>"
            )
            return
    elif text.startswith("/linkchat"):
        if not is_owner(user_id):
            await message.answer("❌ Команда доступна только владельцу.")
            return
        parts = text.split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "list"
        known_peer_ids = await get_all_chat_peer_ids()
        known_set = set(known_peer_ids)

        if action == "list":
            linked = db.get_linked_chats()
            if not linked:
                await message.answer(
                    "🔗 Объединение чатов пустое.\n"
                    "Добавить выбранный чат: /linkchat add\n"
                    "Добавить все известные: /linkchat all"
                )
                return
            titles = await get_chat_titles(linked)
            lines = [f"{chat_id} — {titles.get(chat_id, f'Чат {chat_id}')}" for chat_id in linked]
            await message.answer("🔗 Объединенные чаты:\n\n" + "\n".join(lines))
            return

        if action == "add":
            chat_id = None
            if len(parts) > 2 and parts[2].strip().isdigit():
                chat_id = int(parts[2].strip())
            else:
                chat_id = owner_selected_group.get(user_id)
            if not chat_id:
                await message.answer("❌ Укажите chat_id или выберите чат через /groups.")
                return
            if chat_id not in known_set:
                await message.answer("❌ Этот чат не найден среди известных. Сначала бот должен увидеть там сообщение.")
                return
            db.link_chat(chat_id, user_id)
            titles = await get_chat_titles([chat_id])
            await message.answer(f"✅ Чат добавлен в объединение: {titles.get(chat_id, f'Чат {chat_id}')} ({chat_id})")
            return

        if action == "remove":
            chat_id = None
            if len(parts) > 2 and parts[2].strip().isdigit():
                chat_id = int(parts[2].strip())
            else:
                chat_id = owner_selected_group.get(user_id)
            if not chat_id:
                await message.answer("❌ Укажите chat_id или выберите чат через /groups.")
                return
            db.unlink_chat(chat_id)
            await message.answer(f"✅ Чат убран из объединения: {chat_id}")
            return

        if action == "all":
            count = 0
            for chat_id in known_peer_ids:
                if chat_id >= 2000000000:
                    db.link_chat(chat_id, user_id)
                    count += 1
            await message.answer(f"✅ В объединение добавлено чатов: {count}")
            return

        if action == "clear":
            db.clear_linked_chats()
            await message.answer("✅ Объединение чатов очищено.")
            return

        await message.answer(
            "❌ Использование:\n"
            "/linkchat list\n"
            "/linkchat add [chat_id]\n"
            "/linkchat remove [chat_id]\n"
            "/linkchat all\n"
            "/linkchat clear"
        )
        return
    elif is_owner(user_id) and text and not text.startswith("/"):
        target_chat_id = owner_selected_group.get(user_id)
        if not target_chat_id:
            await message.answer("❌ Сначала выберите группу: /groups")
            return
        out_text = f"{text}\n\nP.S Данил Михайлов"
        try:
            await api.messages.send(
                peer_id=target_chat_id,
                message=out_text,
                random_id=random.randint(0, 2**31)
            )
            titles = await get_chat_titles([target_chat_id])
            target_title = titles.get(target_chat_id, f"Чат {target_chat_id}")
            await message.answer(f"✅ Отправлено в группу: {target_title}")
            return
        except Exception as e:
            log.error(f"Error sending owner text to selected group {target_chat_id}: {e}")
            await message.answer(f"❌ Не удалось отправить в выбранную группу: {str(e)[:120]}")
            return

@bot.labeler.chat_message()
async def handle_chat(message: Message):
    text = message.text or ""
    chat_id = message.peer_id
    user_id = message.from_id
    
    # Ignore messages from the bot itself to prevent duplicate processing
    # VK bots can receive their own messages if configured, which causes duplicates
    if BOT_USER_ID and user_id == BOT_USER_ID:
        return
    reply = message.reply_message
    db.touch_chat(chat_id)

    if await handle_economy_command(message, text, user_id):
        return

    # Improved deduplication - use both conversation_message_id and message ID
    now = time.time()
    _cleanup_processed_messages(now)
    
    # Check for duplicates using both conversation_message_id and message id
    msg_identifier = (
        message.conversation_message_id 
        if hasattr(message, 'conversation_message_id') and message.conversation_message_id 
        else (message.id if hasattr(message, 'id') else None)
    )
    
    if msg_identifier:
        if not _mark_processed_message(msg_identifier):
            log.info(f"Duplicate message detected: {msg_identifier}")
            return
    remember_chat_message(chat_id, getattr(message, "conversation_message_id", None))
    if user_id > 0 and not message.action:
        db.increment_user_stat(chat_id, user_id)

    # Convert Russian commands to English
    russian_to_english = {
        "/бан": "/ban", "/разбан": "/unban", "/разбань": "/unban",
        "/кик": "/kick", "/кинь": "/kick",
        "/мут": "/mute", "/размут": "/unmute", "/размуть": "/unmute",
        "/варн": "/warn", "/пред": "/warn",
        "/разварн": "/unwarn", "/снятьварн": "/unwarn",
        "/очиститьварны": "/clearwarns", "/сброситьварны": "/clearwarns",
        "/роль": "/setrole", "/сетроль": "/setrole", "/датьроль": "/setrole",
        "/гроль": "/grole",
        "/нроль": "/newrole", "/новаяроль": "/newrole",
        "/удалитьроль": "/delrole", "/делроль": "/delrole",
        "/права": "/recrate", "/праваяроли": "/recrate",
        "/рольвладельца": "/ownername", "/овнерроль": "/ownername",
        "/снятьроль": "/removerole", "/убратьроль": "/removerole",
        "/гетбан": "/getban",
        "/гбан": "/gban", "/гмут": "/gmute", "/гкик": "/gkick",
        "/ник": "/nick", "/сетник": "/nick", "/датьник": "/nick",
        "/удалитьник": "/removenick", "/снятьник": "/removenick",
        "/правила": "/rules", "/приветствие": "/welcome",
        "/заметки": "/notes", "/заметка": "/note",
        "/сохранить": "/save", "/удалить": "/delete", "/дел": "/del",
        "/профиль": "/profile", "/помощь": "/help",
        "/старт": "/start", "/персонал": "/staff", "/стафф": "/staff",
        "/роли": "/roles",
        "/варны": "/warns", "/закрепить": "/pin", "/открепить": "/unpin",
        "/пригласить": "/invite", "/репорт": "/report", "/жалоба": "/report",
        "/зов": "/zov", "/масскик": "/masskick", "/антиспам": "/antispam", "/антимат": "/antimat",
        "/объединение": "/linkchat", "/линкчат": "/linkchat",
        "/стата": "/stat", "/топчат": "/topchat", "/чатинфо": "/chatinfo",
        "/фильтр": "/filter", "/чистка": "/clean", "/очистка": "/clean",
        "/ии": "/ai", "/аи": "/ai", "/нейро": "/ai"
    }
    
    # Check if text starts with / and convert
    original_text = text
    for rus, eng in russian_to_english.items():
        if original_text.lower().startswith(rus):
            # Check if the command is complete (followed by space, end of string, or special char)
            rest = original_text[len(rus):]
            if len(rest) == 0 or rest[0] in " \n\t":
                # Add space if rest doesn't start with space and is not empty
                if rest and not rest.startswith(" "):
                    text = eng + " " + rest
                else:
                    text = eng + rest
                log.info(f"Converted command: {rus} -> {eng}, rest: '{rest}'")
            break
    
    log.info(f"Chat {chat_id}: '{text}' from {user_id}")

    settings = db.get_chat_settings(chat_id)

    # Forward photos and links to owner (except from owner)
    if user_id != OWNER_ID:
        has_photo = False
        has_link = False
        photo_urls = []
        link_info = ""
        
        # Check for attachments
        if message.attachments:
            log.info(f"Attachments found: {len(message.attachments)}")
            for att in message.attachments:
                log.info(f"Attachment type: {att.type.value if hasattr(att, 'type') else 'unknown'}")
                if att.type.value == "photo":
                    has_photo = True
                    photo = att.photo
                    # Get the largest photo size
                    if photo.sizes:
                        largest = max(photo.sizes, key=lambda s: s.width * s.height)
                        photo_urls.append(largest.url)
                        log.info(f"Photo URL: {largest.url}")
                elif att.type.value == "link":
                    has_link = True
                    link = att.link
                    link_info = f"🔗 Ссылка: {link.url}"
        
        # Check for links in text
        link_pattern = r'https?://[^\s]+'
        text_links = re.findall(link_pattern, text)
        if text_links:
            has_link = True
            link_info = f"🔗 Ссылки: {', '.join(text_links)}"
        
        # Send to owner
        if has_photo or has_link:
            user_name = await mention_user(user_id, chat_id)
            chat_name = ""
            try:
                chat_info = await api.messages.get_conversations_by_id(peer_ids=[chat_id])
                if chat_info.items and chat_info.items[0].chat_settings:
                    chat_name = chat_info.items[0].chat_settings.title or f"Чат {chat_id}"
            except:
                chat_name = f"Чат {chat_id}"
            
            notify_text = f"📢 Новое сообщение в чате \"{chat_name}\"\n\n👤 От: {user_name} (id{user_id})\n"
            if has_link:
                notify_text += f"{link_info}\n"
            notify_text += f"\n💬 Текст: {text[:500] if text else '(без текста)'}"
            
            # Download and upload photos
            photo_attachments = []
            if has_photo and photo_urls:
                for photo_url in photo_urls:
                    try:
                        # Download photo
                        async with aiohttp.ClientSession() as session:
                            async with session.get(photo_url) as resp:
                                if resp.status == 200:
                                    photo_data = await resp.read()
                        
                        # Get upload server
                        upload_server = await api.photos.get_messages_upload_server(peer_id=OWNER_ID)
                        
                        # Upload photo to VK server
                        async with aiohttp.ClientSession() as session:
                            form = aiohttp.FormData()
                            form.add_field('photo', photo_data, filename='photo.jpg', content_type='image/jpeg')
                            async with session.post(upload_server.upload_url, data=form) as resp:
                                if resp.status == 200:
                                    upload_result = await resp.json()
                        
                        # Save photo
                        if upload_result:
                            saved = await api.photos.save_messages_photo(
                                photo=upload_result['photo'],
                                server=upload_result['server'],
                                hash=upload_result['hash']
                            )
                            if saved:
                                photo_attachments.append(f"photo{saved[0].owner_id}_{saved[0].id}")
                                log.info(f"Uploaded photo for forwarding: photo{saved[0].owner_id}_{saved[0].id}")
                    except Exception as e:
                        log.error(f"Error downloading/uploading photo: {e}")
            
            try:
                await api.messages.send(
                    peer_id=OWNER_ID,
                    message=notify_text,
                    attachment=",".join(photo_attachments) if photo_attachments else None,
                    random_id=random.randint(0, 2**31)
                )
                log.info(f"Forwarded photo/link from {user_id} in chat {chat_id} to owner")
            except Exception as e:
                log.error(f"Error forwarding to owner: {e}")
    
    # Check for invite
    if message.action:
        action = message.action
        action_type = action.type.value.lower() if action.type else ""
        left_id = action.member_id if hasattr(action, 'member_id') else None
        if left_id and left_id > 0 and not is_owner(left_id) and any(marker in action_type for marker in ("kick", "leave", "remove")):
            db.remove_role(left_id, chat_id)
        if action.type and "invite" in action.type.value.lower():
            invited_id = action.member_id if hasattr(action, 'member_id') else None
            if invited_id and invited_id > 0:
                if db.is_banned(invited_id, chat_id):
                    ban_info = db.get_ban_info(invited_id, chat_id)
                    invited_name = await mention_user(invited_id, chat_id)
                    await kick_user(chat_id, invited_id)
                    ban_reason = ban_info[0] if ban_info else "Не указана"
                    await message.answer(f"🚫 Забаненный пользователь!\n\n{invited_name} (id{invited_id})\n📝 Причина: {ban_reason}\n👤 Авто-кик из чата.")
                    return
    
    # Check ban - only bot owner is immune
    if db.is_banned(user_id, chat_id):
        if not is_owner(user_id):
            ban_info = db.get_ban_info(user_id, chat_id)
            user_name = await mention_user(user_id, chat_id)
            ban_reason = ban_info[0] if ban_info else "Не указана"
            kick_result = await kick_user(chat_id, user_id)
            if kick_result:
                await message.answer(f"🚫 Забаненный пользователь!\n\n{user_name} (id{user_id})\n📝 Причина: {ban_reason}\n👢 Кикнут из чата!")
            else:
                await message.answer(f"🚫 Забаненный пользователь!\n\n{user_name} (id{user_id})\n📝 Причина: {ban_reason}\n⚠️ Не удалось кикнуть - проверьте права бота!")
            return
    
    # Check mute - only bot owner is immune
    if db.is_muted(user_id, chat_id):
        if not is_owner(user_id):
            mute_info = db.get_mute_info(user_id, chat_id)
            if mute_info:
                muted_until, muted_by, reason = mute_info
                remaining = muted_until - int(time.time())
                minutes_left = max(0, remaining // 60)
                seconds_left = max(0, remaining % 60)
                # Delete message using conversation_message_id
                try:
                    await api.messages.delete(
                        peer_id=chat_id,
                        conversation_message_ids=[message.conversation_message_id],
                        delete_for_all=True
                    )
                    log.info(f"Deleted message from muted user {user_id}")
                except Exception as e:
                    log.error(f"Error deleting message: {e}")
                await message.answer(f"🔇 Вы в муте!\n⏱ Осталось: {minutes_left}м {seconds_left}с\n📝 Причина: {reason or 'Не указана'}")
        return

    # Custom forbidden word filter.
    if not message.action and text and not text.startswith("/") and not is_antispam_exempt(chat_id, user_id):
        normalized_for_filter = re.sub(r"\s+", " ", text.lower())
        forbidden_hit = None
        for forbidden_word in db.get_forbidden_words(chat_id):
            if forbidden_word and forbidden_word in normalized_for_filter:
                forbidden_hit = forbidden_word
                break
        if forbidden_hit:
            try:
                await api.messages.delete(
                    peer_id=chat_id,
                    conversation_message_ids=[message.conversation_message_id],
                    delete_for_all=True
                )
            except Exception as e:
                log.error(f"Error deleting forbidden word message: {e}")
            user_name = await mention_user(user_id, chat_id)
            await message.answer(
                "🚫 Фильтр чата\n\n"
                f"👤 Пользователь: {user_name}\n"
                f"🧩 Совпадение: {forbidden_hit}\n"
                "Сообщение удалено."
            )
            return

    # Auto anti-spam (for regular users)
    if not message.action and db.is_antispam_enabled(chat_id) and not is_antispam_exempt(chat_id, user_id):
        # Check if user is actually in the chat
        try:
            members = await api.messages.get_conversation_members(peer_id=chat_id)
            user_in_chat = any(m.member_id == user_id for m in members.items)
            if not user_in_chat:
                # User is not in chat, skip mute
                return
        except Exception as e:
            log.error(f"Error checking chat membership for anti-spam: {e}")
            # If we can't check, proceed with mute as safety measure
            
        attachments_count = len(message.attachments) if message.attachments else 0
        spam_detected, spam_reason = check_antispam(chat_id, user_id, text, attachments_count)
        if spam_detected:
            mute_reason = f"Авто-мут: антиспам ({spam_reason})"
            db.mute_user(user_id, chat_id, ANTISPAM_AUTO_MUTE_MINUTES, OWNER_ID, mute_reason)
            clear_antispam_state(chat_id, user_id)
            try:
                await api.messages.delete(
                    peer_id=chat_id,
                    conversation_message_ids=[message.conversation_message_id],
                    delete_for_all=True
                )
            except Exception as e:
                log.error(f"Error deleting spam message: {e}")
            user_name = await mention_user(user_id, chat_id)
            await message.answer(
                f"🛡 Антиспам: {user_name} (id{user_id}) получил мут на {ANTISPAM_AUTO_MUTE_MINUTES} минут.\n"
                f"📝 Причина: {spam_reason}"
            )
            log.info(f"Anti-spam mute in chat {chat_id}: user={user_id}, reason={spam_reason}")
            return
    
    # Auto anti-mat (for regular users)
    if not message.action and db.is_antimat_enabled(chat_id) and not is_antispam_exempt(chat_id, user_id):
        # Check if user is actually in the chat
        try:
            members = await api.messages.get_conversation_members(peer_id=chat_id)
            user_in_chat = any(m.member_id == user_id for m in members.items)
            if not user_in_chat:
                # User is not in chat, skip processing
                return
        except Exception as e:
            log.error(f"Error checking chat membership for anti-mat: {e}")
            # If we can't check, proceed as safety measure
            
        if contains_profanity(text):
            # Check if we should notify about antimat violation
            now = time.time()
            last_notify = antimat_notify_cooldown.get(chat_id, 0)
            
            if now - last_notify > ANTIMAT_NOTIFY_COOLDOWN_SECONDS:
                antimat_notify_cooldown[chat_id] = now
                user_name = await mention_user(user_id, chat_id)
                await message.answer(
                    f"⚠️ Матерные выражения запрещены!\n\n{user_name} (id{user_id}), пожалуйста, соблюдайте правила чата."
                )
                
            # Delete the offensive message
            try:
                await api.messages.delete(
                    peer_id=chat_id,
                    conversation_message_ids=[message.conversation_message_id],
                    delete_for_all=True
                )
                log.info(f"Deleted offensive message from user {user_id} in chat {chat_id}")
            except Exception as e:
                log.error(f"Error deleting offensive message: {e}")
            
            return
    
    # Commands
    if text == "/start":
        await message.answer("🤖 VK Чат Менеджер Бот\n\n/help - Помощь\n/profile - Профиль\n/rules - Правила")
        return

    elif text.startswith("/ai"):
        prompt = text[3:].strip()
        if not prompt and reply and getattr(reply, "text", None):
            prompt = reply.text.strip()
        if not prompt:
            await message.answer("❌ Использование: /ai [вопрос]\nМожно также ответить /ai на сообщение.")
            return
        if len(prompt) > AI_MAX_PROMPT_CHARS:
            await message.answer(f"❌ Слишком длинный запрос. Максимум {AI_MAX_PROMPT_CHARS} символов.")
            return
        now_ts = int(time.time())
        cooldown_key = (chat_id, user_id)
        last_used = ai_last_used.get(cooldown_key, 0)
        if not is_owner(user_id) and now_ts - last_used < AI_COOLDOWN_SECONDS:
            wait_seconds = AI_COOLDOWN_SECONDS - (now_ts - last_used)
            await message.answer(f"⏳ /ai можно использовать через {wait_seconds} сек.")
            return
        ai_last_used[cooldown_key] = now_ts
        user_name = await get_display_name(user_id, chat_id)
        try:
            answer = await ask_openrouter(prompt, user_context=f"user_id={user_id}, display_name={user_name}")
            await message.answer(
                "🤖 | AI\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{answer}"
            )
        except Exception as e:
            log.error(f"OpenRouter AI error in chat {chat_id} from {user_id}: {e}")
            error_text = str(e)
            await message.answer(f"❌ AI временно недоступен: {error_text[:180]}")
        return

    elif text.startswith("/stat"):
        target_id = await get_target_id(text, reply) or user_id
        target_name = await mention_user(target_id, chat_id)
        stat = db.get_user_stat(chat_id, target_id)
        messages_count = int(stat[0]) if stat else 0
        last_seen_at = int(stat[2]) if stat and stat[2] else 0
        last_seen_text = datetime.fromtimestamp(last_seen_at).strftime("%d.%m.%Y %H:%M") if last_seen_at else "нет данных"
        await message.answer(
            "📊 | STATS — активность\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Пользователь: {target_name}\n"
            f"💬 Сообщений в чате: {messages_count}\n"
            f"🕒 Последняя активность: {last_seen_text}"
        )
        return

    elif text.startswith("/topchat"):
        rows = db.get_top_chat_stats(chat_id, limit=10)
        if not rows:
            await message.answer("📊 Пока нет статистики по этому чату.")
            return
        lines = ["🏆 | TOP CHAT — активность", "━━━━━━━━━━━━━━━━━━━━", ""]
        for index, row in enumerate(rows, 1):
            uid, count, _last = int(row[0]), int(row[1]), row[2]
            name = await mention_user(uid, chat_id)
            lines.append(f"{index}. {name} — {count} сообщений")
        await message.answer("\n".join(lines))
        return

    elif text.startswith("/chatinfo"):
        try:
            conv = await api.messages.get_conversations_by_id(peer_ids=[chat_id])
            item = conv.items[0] if conv.items else None
            title = item.chat_settings.title if item and item.chat_settings else f"Чат {chat_id}"
        except Exception:
            title = f"Чат {chat_id}"
        try:
            members = await api.messages.get_conversation_members(peer_id=chat_id)
            member_count = len(members.items)
            admin_count = sum(1 for m in members.items if getattr(m, "is_admin", False))
        except Exception:
            member_count = 0
            admin_count = 0
        settings_info = db.get_chat_settings(chat_id)
        filters_count = len(db.get_forbidden_words(chat_id))
        linked_text = "да" if db.is_chat_linked(chat_id) else "нет"
        await message.answer(
            "ℹ️ | CHAT INFO\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💬 Название: {title}\n"
            f"🆔 ID: {chat_id}\n"
            f"👥 Участников: {member_count}\n"
            f"🛡 Админов VK: {admin_count}\n"
            f"🔗 В объединении: {linked_text}\n"
            f"🚫 Фильтр-слов: {filters_count}\n"
            f"🛡 Антиспам: {'вкл' if settings_info['antispam_enabled'] else 'выкл'}\n"
            f"🤬 Антимат: {'вкл' if settings_info['antimat_enabled'] else 'выкл'}"
        )
        return

    elif text.startswith("/filter"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            await message.answer(
                "🚫 | FILTER\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "/filter add [слово]\n"
                "/filter del [слово]\n"
                "/filter list"
            )
            return
        action = parts[1].lower()
        if action in {"list", "список"}:
            words = db.get_forbidden_words(chat_id)
            if not words:
                await message.answer("🚫 Фильтр пуст.")
                return
            await message.answer("🚫 Запрещённые слова:\n\n" + "\n".join(f"• {word}" for word in words))
            return
        if len(parts) < 3 or not parts[2].strip():
            await message.answer("❌ Укажите слово: /filter add [слово] или /filter del [слово]")
            return
        word = parts[2].strip().lower()
        if len(word) < 2 or len(word) > 64:
            await message.answer("❌ Слово должно быть от 2 до 64 символов.")
            return
        if action in {"add", "добавить", "+"}:
            db.add_forbidden_word(chat_id, word, user_id)
            await message.answer(f"✅ Фильтр добавлен: {word}")
            return
        if action in {"del", "delete", "remove", "удалить", "-"}:
            db.remove_forbidden_word(chat_id, word)
            await message.answer(f"✅ Фильтр удалён: {word}")
            return
        await message.answer("❌ Использование: /filter add|del|list")
        return

    elif text.startswith("/clean"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        parts = text.split(maxsplit=1)
        amount = parse_positive_int(parts[1]) if len(parts) > 1 else 10
        if not amount:
            await message.answer("❌ Использование: /clean [1-100]")
            return
        amount = min(amount, 100)
        ids = get_recent_chat_message_ids(chat_id, amount)
        if not ids:
            await message.answer("❌ Нет сохранённых сообщений для очистки после перезапуска.")
            return
        try:
            await api.messages.delete(
                peer_id=chat_id,
                conversation_message_ids=ids,
                delete_for_all=True
            )
            await message.answer(f"🧹 Удалено последних сообщений: {len(ids)}")
        except Exception as e:
            log.error(f"Error cleaning chat messages: {e}")
            await message.answer(f"❌ Не удалось очистить сообщения: {str(e)[:100]}")
        return

    elif text.startswith("/q"):
        # Self-kick command - anyone can use to kick themselves
        target_name = await mention_user(user_id, chat_id)
        if await kick_user(chat_id, user_id):
            await message.answer(f"👋 Пока, {target_name} (id{user_id})!")
        else:
            await message.answer("❌ Не удалось выйти из чата.")
    
    elif text == "/help":
        role = await get_user_role(chat_id, user_id)
        owner_mode = is_owner(user_id)
        help_text = (
            "📚 Команды бота\n\n"
            "👤 Пользователь:\n"
            "/start - Начать\n"
            "/help - Помощь\n"
            "/profile - Профиль\n"
            "/ai [текст] - Ответ Mistral AI\n"
            "/stat [@id|reply] - Статистика пользователя\n"
            "/topchat - Топ активности чата\n"
            "/chatinfo - Информация о беседе\n"
            "/rules - Правила\n"
            "/notes - Заметки\n"
            "/note [имя] - Показать заметку\n"
            "/staff - Список администрации\n"
            "/report [причина] (ответом) - Жалоба владельцу\n\n"
        )

        helper_text = (
            "🟡 Helper:\n"
            "/warn [@id|ID|reply] [причина] - Варн\n"
            "/unwarn [@id|ID|reply] - Снять 1 варн\n"
            "/clearwarns [@id|ID|reply] - Очистить все варны\n"
            "/warns [@id|ID|reply] - Список варнов\n"
            "/mute [@id|ID|reply] [время: 3s|3m|3h|3d] [причина] - Мут\n"
            "/unmute [@id|ID|reply] - Снять мут\n"
            "/setwelcome [текст]\n"
            "/setrules [текст]\n"
            "/antispam [on|off] - Вкл/выкл антиспам\n"
            "/antimat [on|off] - Вкл/выкл антимат\n"
            "/filter add|del|list [слово] - Фильтр запрещенных слов\n"
            "/clean [1-100] - Очистить последние сообщения после запуска бота\n"
            "/save [имя] [текст]\n"
            "/delete [имя]\n"
            "/del (ответом) - Удалить сообщение\n"
            "/pin (ответом) - Закрепить сообщение\n"
            "/unpin - Открепить сообщение\n"
            "/invite [ссылка/username] - Добавить пользователя в чат\n\n"
        )

        moderator_text = (
            "🟠 Moderator:\n"
            "/kick [@id|ID|reply] [причина] - Кик\n"
            "/zov [текст] - Вызвать всех участников\n\n"
        )

        admin_text = (
            "🔴 Admin:\n"
            "/ban [@id|ID|reply] [время: 3s|3m|3h|3d или дни] [причина] - Бан\n"
            "/unban [@id|ID|reply] - Разбан\n"
            "/getban [@id|ID|username|reply] - Информация о бане\n"
            "/gban [@id|ID|username|reply] [время] [причина] - Глобальный бан\n"
            "/gmute [@id|ID|username|reply] [время] [причина] - Глобальный мут\n"
            "/gkick [@id|ID|username|reply] [причина] - Глобальный кик\n"
            "/nick [@id|ID|reply] [ник] - Выдать ник\n"
            "/removenick [@id|ID|reply] - Удалить ник\n"
            "/masskick - Кикнуть всех пользователей без роли\n\n"
        )

        role_manage_text = (
            "👑 Роли:\n"
            "/roles - Список ролей\n"
            "/newrole [приоритет] [название] - Создать/обновить роль\n"
            "/delrole [приоритет|название] - Удалить роль\n"
            "/recrate [роль] [/ban|/kick|/warn|/mute|/role|/gban|/gkick|/gmute|/grole] true|false - Изменить права\n"
            "/ownername [название] - Переименовать роль владельца\n"
            "/setrole [@id|ID|reply] [роль] - Выдать роль\n"
            "/grole [@id|ID|reply] [роль] - Выдать роль во всех объединенных чатах\n"
            "/removerole [@id|ID|reply] - Снять роль\n"
            "Владелец всегда имеет приоритет 100\n\n"
        )

        if owner_mode:
            help_text += helper_text + moderator_text + admin_text
            help_text += role_manage_text
        elif role["level"] >= 4:
            help_text += admin_text
        elif role["level"] >= 3:
            help_text += moderator_text
        elif role["level"] >= 2:
            help_text += helper_text

        await message.answer(help_text.strip())
    
    elif text.startswith("/profile"):
        target_id = await get_target_id(text, reply) or user_id
        name = await mention_user(target_id, chat_id)
        nickname = db.get_nickname(target_id, chat_id)
        role = await get_user_role(chat_id, target_id)
        warns = db.get_warns(target_id, chat_id)
        ban_status = "да" if db.is_banned(target_id, chat_id) else "нет"
        mute_status = "да" if db.is_muted(target_id, chat_id) else "нет"
        role_display = role["name"]
        nickname_display = nickname or "не установлен"
        stat = db.get_user_stat(chat_id, target_id)
        messages_count = int(stat[0]) if stat else 0
        first_seen_at = int(stat[1]) if stat and stat[1] else 0
        first_seen_text = datetime.fromtimestamp(first_seen_at).strftime("%d.%m.%Y %H:%M") if first_seen_at else "нет данных"
        premium_info = db.get_premium_info(target_id)
        if db.is_premium(target_id) and premium_info:
            vip_status = "VIP"
            vip_until = datetime.fromtimestamp(int(premium_info[0])).strftime("%d.%m.%Y %H:%M")
        else:
            vip_status = "нет"
            vip_until = "-"
        await message.answer(
            "🔍 Информация о пользователе:\n\n"
            f"👤 Пользователь: {name}\n"
            f"🗣 Статус: {role_display}\n"
            f"⚠ Предупреждений: {warns}/{MAX_WARNS}\n"
            f"📄 Никнейм: {nickname_display}\n"
            f"🚧 Блокировка чата: {ban_status}\n"
            f"🔇 Мут: {mute_status}\n"
            f"📅 Дата появления в чате: {first_seen_text}\n\n"
            "📋 Глобальная информация:\n"
            f"💎 VIP статус: {vip_status}\n"
            f"💎 Действует до: {vip_until}\n"
            f"✍ Сообщений отправлено: {messages_count}\n"
            "👫 Пригласил(а): нет данных\n"
            f"⚙ ID: {target_id}"
        )

    elif text == "/roles":
        await message.answer(format_role_list(chat_id))
    
    elif text == "/rules":
        settings = db.get_chat_settings(chat_id)
        if settings['rules']:
            await message.answer(f"📜 Правила\n\n{settings['rules']}")
        else:
            await message.answer("📜 Правила не установлены. Используйте /setrules [текст]")
    
    elif text.startswith("/setrules"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        rules = text[10:]
        db.set_rules(chat_id, rules)
        await message.answer("✅ Правила установлены!")

    elif text.startswith("/antispam"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            current_status = "включен" if db.is_antispam_enabled(chat_id) else "выключен"
            await message.answer(
                f"🛡 Антиспам сейчас {current_status}.\n"
                "Использование: /antispam on|off"
            )
            return
        mode = parts[1].strip().lower()
        on_values = {"on", "1", "enable", "enabled", "вкл", "включить", "включен"}
        off_values = {"off", "0", "disable", "disabled", "выкл", "выключить", "выключен"}
        if mode in on_values:
            db.set_antispam_enabled(chat_id, True)
            await message.answer("✅ Антиспам включен.")
            return
        elif mode in off_values:
            db.set_antispam_enabled(chat_id, False)
            await message.answer("✅ Антиспам выключен.")
            return
        else:
            await message.answer("❌ Использование: /antispam on|off")
            return

    elif text.startswith("/antimat"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            current_status = "включен" if db.is_antimat_enabled(chat_id) else "выключен"
            await message.answer(
                f"🛡 Антимат сейчас {current_status}.\n"
                "Использование: /antimat on|off"
            )
            return
        mode = parts[1].strip().lower()
        on_values = {"on", "1", "enable", "enabled", "вкл", "включить", "включен"}
        off_values = {"off", "0", "disable", "disabled", "выкл", "выключить", "выключен"}
        if mode in on_values:
            db.set_antimat_enabled(chat_id, True)
            await message.answer("✅ Антимат включен.")
            return
        elif mode in off_values:
            db.set_antimat_enabled(chat_id, False)
            await message.answer("✅ Антимат выключен.")
            return
        else:
            await message.answer("❌ Использование: /antimat on|off")
            return

    elif text == "/staff":
        mode = "nick"
        await message.answer(
            await build_staff_text(chat_id, mode),
            keyboard=staff_keyboard(mode).get_json()
        )
    
    elif text.startswith("/warns"):
        target_id = await get_target_id(text, reply) or user_id
        warns = db.get_warns(target_id, chat_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"⚠️ Варны для {target_name} (id{target_id}): {warns}/{MAX_WARNS}")
    
    elif text.startswith("/warn"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /warn [@id|ID|reply] [причина]")
            return
        if target_id == user_id:
            await message.answer("❌ Нельзя выдать варн самому себе!")
            return
        # Only bot owner is protected
        if is_owner(target_id):
            await message.answer("❌ Нельзя выдать варн владельцу!")
            return
        allowed, reason_text = await can_manage_target(chat_id, user_id, target_id, "варн")
        if not allowed:
            await message.answer(reason_text)
            return
        parts = text.split(maxsplit=2)
        reason = parts[2] if len(parts) > 2 else "Не указана"
        warns = db.add_warn(target_id, chat_id, reason, user_id)
        target_name = await mention_user(target_id, chat_id)
        if warns >= MAX_WARNS:
            db.clear_warns(target_id, chat_id)
            db.ban_user(target_id, chat_id, f"Максимум варнов ({MAX_WARNS})", user_id)
            await kick_user(chat_id, target_id)
            await message.answer(f"⚠️ Максимум варнов!\n\n{target_name} (id{target_id})\n⚠️ Варны: {warns}/{MAX_WARNS}\n🚫 Пользователь забанен и кикнут!")
        else:
            await message.answer(f"⚠️ Варн!\n\n{target_name} (id{target_id})\n📝 Причина: {reason}\n⚠️ Варны: {warns}/{MAX_WARNS}")
    
    elif text.startswith("/unwarn"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /unwarn [@id|ID|reply]")
            return
        target_name = await mention_user(target_id, chat_id)
        current_warns = db.get_warns(target_id, chat_id)
        if current_warns <= 0:
            await message.answer(f"⚠️ У {target_name} (id{target_id}) нет варнов!")
            return
        db.remove_one_warn(target_id, chat_id)
        new_warns = db.get_warns(target_id, chat_id)
        await message.answer(f"✅ Снят 1 варн с {target_name} (id{target_id})\n⚠️ Осталось варнов: {new_warns}/{MAX_WARNS}")
    
    elif text.startswith("/clearwarns"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /clearwarns [@id|ID|reply]")
            return
        db.clear_warns(target_id, chat_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"✅ Все варны очищены для {target_name} (id{target_id})")
    
    elif text.startswith("/kick"):
        if not await has_permission(chat_id, user_id, "can_kick"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /kick [@id|ID|reply] [причина]")
            return
        if target_id == user_id:
            await message.answer("❌ Нельзя кикнуть самого себя!")
            return
        # Only bot owner is protected
        if is_owner(target_id):
            await message.answer("❌ Нельзя кикнуть владельца!")
            return
        allowed, reason_text = await can_manage_target(chat_id, user_id, target_id, "кик")
        if not allowed:
            await message.answer(reason_text)
            return
        parts = text.split(maxsplit=1)
        reason = parts[1] if len(parts) > 1 else "Не указана"
        reason = re.sub(r'@id\d+|\[id\d+\|[^\]]+\]|\d{6,}', '', reason).strip() or "Не указана"
        target_name = await mention_user(target_id, chat_id)
        if await kick_user(chat_id, target_id):
            await message.answer(f"👢 Кикнут!\n\n{target_name} (id{target_id})\n📝 Причина: {reason}")
        else:
            await message.answer("❌ Не удалось кикнуть. Проверьте права бота.")

    elif text.startswith("/gkick"):
        if not await has_permission(chat_id, user_id, "can_gkick"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /gkick [@id|ID|username|reply] [причина]")
            return
        if target_id == user_id:
            await message.answer("❌ Нельзя кикнуть самого себя!")
            return
        if is_owner(target_id):
            await message.answer("❌ Нельзя кикнуть владельца!")
            return
        allowed, reason_text = await can_manage_target(chat_id, user_id, target_id, "кик")
        if not allowed:
            await message.answer(reason_text)
            return
        parts = text.split(maxsplit=1)
        reason = parts[1] if len(parts) > 1 else "Не указана"
        reason = re.sub(r'@id\d+|\[id\d+\|[^\]]+\]|\d{6,}|@([a-zA-Z][a-zA-Z0-9_.-]{2,})', '', reason).strip() or "Не указана"
        kicked_chats = await apply_global_kick(target_id, user_id)
        target_name = await mention_user(target_id, chat_id)
        if kicked_chats:
            await message.answer(
                f"👢 Глобальный кик!\n\n"
                f"👤 {target_name} (id{target_id})\n"
                f"💬 Чатов: {len(kicked_chats)}\n"
                f"📝 Причина: {reason}"
            )
        else:
            await message.answer(f"❌ Не удалось кикнуть {target_name} ни в одном чате.")

    elif text.startswith("/ban"):
        if not await has_permission(chat_id, user_id, "can_ban"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /ban [@id|ID|reply] [время] [причина]\nПример: /ban @id123 3d спам")
            return
        if target_id == user_id:
            await message.answer("❌ Нельзя забанить самого себя!")
            return
        # Only bot owner is protected
        if is_owner(target_id):
            await message.answer("❌ Нельзя забанить владельца!")
            return
        # Check if already banned
        if db.is_banned(target_id, chat_id):
            target_name = await mention_user(target_id, chat_id)
            await message.answer(f"⚠️ {target_name} (id{target_id}) уже забанен!")
            return
        allowed, reason_text = await can_manage_target(chat_id, user_id, target_id, "бан")
        if not allowed:
            await message.answer(reason_text)
            return
        parts = text.split()
        ban_duration_seconds = None
        ban_duration_text = None
        reason = "Не указана"
        reason_parts = []
        duration_parsed = False
        for part in parts[1:]:
            # Skip user mentions and IDs
            if part.startswith("@id") or part.startswith("[id") or (part.isdigit() and len(part) >= 6):
                continue
            # Skip VK links
            if part.startswith("https://vk.") or part.startswith("http://vk.") or "vk.com" in part or "vk.ru" in part:
                continue
            if not duration_parsed:
                parsed_duration = parse_ban_duration(part)
                if parsed_duration:
                    ban_duration_seconds = parsed_duration["seconds"]
                    ban_duration_text = parsed_duration["text"]
                    duration_parsed = True
                    continue
            reason_parts.append(part)
        if reason_parts:
            reason = " ".join(reason_parts)
        db.ban_user(target_id, chat_id, reason, user_id, duration_seconds=ban_duration_seconds)
        target_name = await mention_user(target_id, chat_id)
        await kick_user(chat_id, target_id)
        if ban_duration_seconds:
            await message.answer(f"🚫 Забанен!\n\n{target_name} (id{target_id})\n⏱ Срок: {ban_duration_text}\n📝 Причина: {reason}")
        else:
            await message.answer(f"🚫 Забанен навсегда!\n\n{target_name} (id{target_id})\n📝 Причина: {reason}")

    elif text.startswith("/gban"):
        if not await has_permission(chat_id, user_id, "can_gban"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /gban [@id|ID|username|reply] [время] [причина]")
            return
        if target_id == user_id:
            await message.answer("❌ Нельзя забанить самого себя!")
            return
        if is_owner(target_id):
            await message.answer("❌ Нельзя забанить владельца!")
            return
        if db.is_banned(target_id, chat_id):
            target_name = await mention_user(target_id, chat_id)
            await message.answer(f"⚠️ {target_name} (id{target_id}) уже забанен в этом чате.")
            return
        allowed, reason_text = await can_manage_target(chat_id, user_id, target_id, "бан")
        if not allowed:
            await message.answer(reason_text)
            return
        parts = text.split()
        ban_duration_seconds = None
        ban_duration_text = None
        reason = "Не указана"
        reason_parts = []
        duration_parsed = False
        for part in parts[1:]:
            if part.startswith("@id") or part.startswith("[id") or (part.isdigit() and len(part) >= 6):
                continue
            if part.startswith("https://vk.") or part.startswith("http://vk.") or "vk.com" in part or "vk.ru" in part:
                continue
            if not duration_parsed:
                parsed_duration = parse_ban_duration(part)
                if parsed_duration:
                    ban_duration_seconds = parsed_duration["seconds"]
                    ban_duration_text = parsed_duration["text"]
                    duration_parsed = True
                    continue
            reason_parts.append(part)
        if reason_parts:
            reason = " ".join(reason_parts)
        kicked_chats = await apply_global_ban(target_id, user_id, reason, duration_seconds=ban_duration_seconds)
        target_name = await mention_user(target_id, chat_id)
        if ban_duration_seconds:
            await message.answer(
                f"🚫 Глобальный бан!\n\n"
                f"👤 {target_name} (id{target_id})\n"
                f"⏱ Срок: {ban_duration_text}\n"
                f"💬 Чатов: {len(kicked_chats)}\n"
                f"📝 Причина: {reason}"
            )
        else:
            await message.answer(
                f"🚫 Глобальный бан навсегда!\n\n"
                f"👤 {target_name} (id{target_id})\n"
                f"💬 Чатов: {len(kicked_chats)}\n"
                f"📝 Причина: {reason}"
            )
    
    elif text.startswith("/unban"):
        if not await has_permission(chat_id, user_id, "can_ban"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /unban [@id|ID|reply]")
            return
        db.unban_user(target_id, chat_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"✅ Разбанен: {target_name} (id{target_id})")

    elif text.startswith("/gmute"):
        if not await has_permission(chat_id, user_id, "can_gmute"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /gmute [@id|ID|username|reply] [время] [причина]")
            return
        if target_id == user_id:
            await message.answer("❌ Нельзя замутить самого себя!")
            return
        if is_owner(target_id):
            await message.answer("❌ Нельзя замутить владельца!")
            return
        allowed, reason_text = await can_manage_target(chat_id, user_id, target_id, "мут")
        if not allowed:
            await message.answer(reason_text)
            return
        parts = text.split()
        duration_seconds = None
        duration_text = None
        reason = "Не указана"
        reason_parts = []
        duration_parsed = False
        for part in parts[1:]:
            if part.startswith("@id") or part.startswith("[id") or (part.isdigit() and len(part) >= 6):
                continue
            if part.startswith("https://vk.") or part.startswith("http://vk.") or "vk.com" in part or "vk.ru" in part:
                continue
            if not duration_parsed:
                parsed_duration = parse_mute_duration(part)
                if parsed_duration:
                    duration_seconds, duration_text = parsed_duration
                    duration_parsed = True
                    continue
            reason_parts.append(part)
        if reason_parts:
            reason = " ".join(reason_parts)
        muted_chats = await apply_global_mute(target_id, user_id, reason, duration_seconds or 60)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(
            f"🔇 Глобальный мут!\n\n"
            f"👤 {target_name} (id{target_id})\n"
            f"⏱ Срок: {duration_text or '1 минута'}\n"
            f"💬 Чатов: {len(muted_chats)}\n"
            f"📝 Причина: {reason}"
        )
    
    elif text.startswith("/getban"):
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /getban [@id|ID|username|reply]")
            return
        ban_info = db.get_ban_info(target_id, chat_id)
        ban_count = db.get_ban_history_count(target_id, chat_id)
        target_name = await mention_user(target_id, chat_id)
        if ban_info:
            reason, banned_until, banned_by = ban_info
            banned_by_name = await mention_user(banned_by, chat_id) if banned_by else "Неизвестно"
            if banned_until:
                remaining = max(0, int(banned_until) - int(time.time()))
                days = remaining // 86400
                hours = (remaining % 86400) // 3600
                minutes = (remaining % 3600) // 60
                seconds = remaining % 60
                if remaining > 0:
                    if days > 0:
                        until_text = f"ещё {days}д {hours}ч {minutes}м"
                    elif hours > 0:
                        until_text = f"ещё {hours}ч {minutes}м {seconds}с"
                    else:
                        until_text = f"ещё {minutes}м {seconds}с"
                else:
                    until_text = "срок уже истёк"
            else:
                until_text = "навсегда"
            await message.answer(
                f"🚫 Бан найден\n\n"
                f"👤 Пользователь: {target_name} (id{target_id})\n"
                f"🔢 Банов в этом чате: {ban_count}\n"
                f"📝 Причина: {reason or 'Не указана'}\n"
                f"👮 Кто забанил: {banned_by_name} (id{banned_by})\n"
                f"⏱ Срок: {format_ban_until_timestamp(banned_until)}\n"
                f"📌 Статус: {until_text}"
            )
            return

        last_ban = db.get_last_ban_history(target_id, chat_id)
        if last_ban:
            reason, banned_by, banned_until, created_at = last_ban
            banned_by_name = await mention_user(banned_by, chat_id) if banned_by else "Неизвестно"
            created_text = datetime.fromtimestamp(int(created_at)).strftime("%d.%m.%Y %H:%M")
            await message.answer(
                f"ℹ️ Активного бана нет\n\n"
                f"👤 Пользователь: {target_name} (id{target_id})\n"
                f"🔢 Всего банов в этом чате: {ban_count}\n"
                f"📝 Последняя причина: {reason or 'Не указана'}\n"
                f"👮 Последний бан выдал: {banned_by_name} (id{banned_by})\n"
                f"📅 Последний бан: {created_text}\n"
                f"⏱ Последний срок: {format_ban_until_timestamp(banned_until)}"
            )
        else:
            await message.answer(
                f"ℹ️ Банов не найдено\n\n"
                f"👤 Пользователь: {target_name} (id{target_id})\n"
                f"🔢 Банов в этом чате: 0"
            )
    
    elif text.startswith("/mute"):
        if not await has_permission(chat_id, user_id, "can_mute"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /mute [@id|ID|reply] [время] [причина]\nПример: /mute @id123 3h флуд")
            return
        if target_id == user_id:
            await message.answer("❌ Нельзя дать мут самому себе!")
            return
        # Only bot owner is protected
        if is_owner(target_id):
            await message.answer("❌ Нельзя дать мут владельцу!")
            return
        allowed, reason_text = await can_manage_target(chat_id, user_id, target_id, "мут")
        if not allowed:
            await message.answer(reason_text)
            return
        parts = text.split()
        duration_seconds = 10 * 60
        duration_text = "10 мин."
        reason = "Не указана"
        reason_parts = []
        duration_parsed = False
        for part in parts[1:]:
            if part.startswith("@id") or part.startswith("[id") or (part.isdigit() and len(part) >= 6):
                continue
            if part.startswith("https://vk.") or part.startswith("http://vk.") or "vk.com" in part or "vk.ru" in part:
                continue
            if not duration_parsed:
                parsed_duration = parse_mute_duration(part)
                if parsed_duration:
                    duration_seconds, duration_text = parsed_duration
                    duration_parsed = True
                    continue
            reason_parts.append(part)
        if reason_parts:
            reason = " ".join(reason_parts)
        db.mute_user(target_id, chat_id, duration_seconds / 60, user_id, reason)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"🔇 Мут!\n\n{target_name} (id{target_id})\n⏱ Срок: {duration_text}\n📝 Причина: {reason}")
    
    elif text.startswith("/unmute"):
        if not await has_permission(chat_id, user_id, "can_mute"):
            await message.answer("❌ Недостаточно прав.")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /unmute [@id|ID|reply]")
            return
        db.unmute_user(target_id, chat_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"🔊 Мут снят: {target_name} (id{target_id})")
    
    elif text.startswith("/setrole"):
        if not is_owner(user_id):
            await message.answer("❌ Только владелец может выдавать роли!")
            return
        parts = text.split()
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /setrole [@id|ID|reply] [роль]")
            return
        role_arg = extract_role_argument(parts, bool(reply))
        if not role_arg:
            await message.answer("❌ Укажите роль: /setrole [@id|ID|reply] [роль]")
            return
        role_key, role_data = resolve_role_for_chat(chat_id, role_arg)
        if not role_data:
            await message.answer("❌ Роль не найдена. Создайте её: /newrole [приоритет] [название]")
            return
        if target_id == OWNER_ID:
            await message.answer("❌ Нельзя изменить роль владельца!")
            return
        if int(role_data.get("level", 0)) == 0:
            db.remove_role(target_id, chat_id)
            target_name = await mention_user(target_id, chat_id)
            await message.answer(f"✅ Роль снята: {target_name} (id{target_id}) теперь User (0)")
            return
        allowed, reason_text = await can_assign_role(chat_id, user_id, target_id, role_data)
        if not allowed:
            await message.answer(reason_text)
            return
        db.set_role(target_id, chat_id, role_key, user_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"✅ Роль выдана: {target_name} (id{target_id}) теперь {role_data['name']} ({role_data['level']})")

    elif text.startswith("/newrole"):
        if not is_owner(user_id):
            await message.answer("❌ Только владелец может создавать роли!")
            return
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("❌ Использование: /newrole [приоритет 1-100] [название]")
            return
        try:
            level = int(parts[1])
        except ValueError:
            await message.answer("❌ Приоритет должен быть числом от 1 до 100.")
            return
        if level <= 0 or level > 100:
            await message.answer("❌ Приоритет роли должен быть от 1 до 100.")
            return
        actor_role = await get_user_role(chat_id, user_id)
        if not is_owner(user_id) and level >= int(actor_role.get("level", 0)):
            await message.answer(f"❌ Нельзя создать роль {level}: она не ниже вашей роли {actor_role.get('name')} ({actor_role.get('level')}).")
            return
        if level == 100 and not is_owner(user_id):
            await message.answer("❌ Роль с приоритетом 100 может создавать только владелец.")
            return
        role_name = parts[2].strip()
        if len(role_name) < 2 or len(role_name) > 64:
            await message.answer("❌ Название роли: 2-64 символа.")
            return
        role_key = normalize_role_key(role_name)
        if role_key == "owner":
            await message.answer("❌ Название owner зарезервировано.")
            return
        if role_key in ROLES:
            await message.answer("❌ Такое имя занято встроенной ролью.")
            return
        existed = db.get_custom_role(chat_id, role_key) is not None or db.get_custom_role_by_level(chat_id, level) is not None
        db.create_priority_role(chat_id, role_name, level, user_id)
        role_data = build_priority_role(role_name, level)
        await message.answer(
            f"✅ Роль {'обновлена' if existed else 'создана'}\n\n"
            f"⭐ Название: {role_name}\n"
            f"🔢 Приоритет: {level}\n"
            f"Права: ban={role_data['can_ban']}, kick={role_data['can_kick']}, warn={role_data['can_warn']}, mute={role_data['can_mute']}, "
            f"gban={role_data['can_gban']}, gkick={role_data['can_gkick']}, gmute={role_data['can_gmute']}, grole={role_data['can_grole']}"
        )

    elif text.startswith("/delrole"):
        if not is_owner(user_id):
            await message.answer("❌ Только владелец может удалять роли!")
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("❌ Использование: /delrole [приоритет|название]")
            return
        role_ref = parts[1].strip()
        role_key, role_data = resolve_role_for_chat(chat_id, role_ref)
        if not role_data or not role_key or role_key in ROLES or role_key.startswith("level:"):
            await message.answer("❌ Можно удалить только созданную кастомную роль.")
            return
        actor_role = await get_user_role(chat_id, user_id)
        if not is_owner(user_id) and int(role_data.get("level", 0)) >= int(actor_role.get("level", 0)):
            await message.answer("❌ Нельзя удалить роль не ниже вашей.")
            return
        db.delete_custom_role(chat_id, role_key)
        await message.answer(f"✅ Роль удалена: {role_data['name']} ({role_data['level']})")

    elif text.startswith("/recrate"):
        if not is_owner(user_id):
            await message.answer("❌ Только владелец может менять права ролей!")
            return
        parts = text.split()
        if len(parts) < 4:
            await message.answer("❌ Использование: /recrate [приоритет|название] [/ban|/kick|/warn|/mute|/role|/gban|/gkick|/gmute|/grole] true|false")
            return
        role_ref = parts[1]
        permission = parts[2].lstrip("/").lower()
        enabled = parse_bool_word(parts[3])
        if enabled is None:
            await message.answer("❌ Последний параметр должен быть true/false или да/нет.")
            return
        pre_role_key, pre_role_data = resolve_role_for_chat(chat_id, role_ref)
        if pre_role_data:
            actor_role = await get_user_role(chat_id, user_id)
            if not is_owner(user_id) and int(pre_role_data.get("level", 0)) >= int(actor_role.get("level", 0)):
                await message.answer("❌ Нельзя менять права роли не ниже вашей.")
                return
        updated = db.update_custom_role_permission(chat_id, role_ref, permission, enabled)
        if not updated:
            await message.answer("❌ Роль или право не найдены. Права: /ban /kick /warn /mute /role /gban /gkick /gmute /grole")
            return
        _role_key, role_data, column = updated
        await message.answer(
            "✅ Права роли изменены\n\n"
            f"⭐ Роль: {role_data['name']} ({role_data['level']})\n"
            f"⚙ Право: {column}\n"
            f"📌 Значение: {'true' if enabled else 'false'}"
        )

    elif text.startswith("/ownername"):
        if not is_owner(user_id):
            await message.answer("❌ Команда доступна только владельцу.")
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("❌ Использование: /ownername [новое название роли владельца]")
            return
        new_name = parts[1].strip()
        if len(new_name) > 64:
            await message.answer("❌ Название слишком длинное. Максимум 64 символа.")
            return
        db.set_setting("owner_role_name", new_name)
        await message.answer(f"✅ Название роли владельца изменено: {new_name}")

    elif text.startswith("/grole"):
        if not is_owner(user_id):
            await message.answer("❌ Только владелец может выдавать роли!")
            return
        parts = text.split()
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /grole [@id|ID|reply] [роль]")
            return
        role_arg = extract_role_argument(parts, bool(reply))
        if not role_arg:
            await message.answer("❌ Укажите роль: /grole [@id|ID|reply] [роль]")
            return
        role_key, role_data = resolve_role_for_chat(chat_id, role_arg)
        if not role_data:
            await message.answer("❌ Роль не найдена. Создайте её: /newrole [приоритет] [название]")
            return
        if target_id == OWNER_ID:
            await message.answer("❌ Нельзя изменить роль владельца!")
            return
        if int(role_data.get("level", 0)) == 0:
            changed_chats = await apply_global_remove_role(target_id)
            target_name = await mention_user(target_id, chat_id)
            await message.answer(f"✅ Глобальная роль снята: {target_name} (id{target_id}) теперь User (0)\n💬 Чатов: {len(changed_chats)}")
            return
        allowed, reason_text = await can_assign_role(chat_id, user_id, target_id, role_data)
        if not allowed:
            await message.answer(reason_text)
            return
        changed_chats = await apply_global_role(target_id, role_key, user_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(
            f"✅ Глобальная роль выдана\n\n"
            f"👤 {target_name} (id{target_id})\n"
            f"⭐ Роль: {role_data['name']} ({role_data['level']})\n"
            f"💬 Чатов: {len(changed_chats)}"
        )
    
    elif text.startswith("/removerole"):
        if not is_owner(user_id):
            await message.answer("❌ Только владелец может снимать роли!")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /removerole [@id|ID|reply]")
            return
        db.remove_role(target_id, chat_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"✅ Роль снята: {target_name} (id{target_id})")
    
    elif text.startswith("/zov"):
        role = await get_user_role(chat_id, user_id)
        if role["level"] < 3:
            await message.answer("❌ Только модераторы+ могут использовать эту команду!")
            return
        now = int(time.time())
        last_used = zov_last_used.get(chat_id, 0)
        if not is_owner(user_id) and now - last_used < ZOV_COOLDOWN_SECONDS:
            wait_seconds = ZOV_COOLDOWN_SECONDS - (now - last_used)
            await message.answer(f"⏳ /zov можно использовать снова через {wait_seconds} сек.")
            return
        # Get custom text after command (required)
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("❌ Укажите текст: /zov [текст]\nПример: /zov Привет участники!")
            return
        custom_text = parts[1]
        try:
            members = await api.messages.get_conversation_members(peer_id=chat_id)
            mentions = []
            for m in members.items:
                if m.member_id > 0:
                    mentions.append(f"[id{m.member_id}|&#8288;]")
            if not mentions:
                await message.answer("❌ Не удалось найти участников для вызова.")
                return

            total_mentions = len(mentions)
            limited_mentions = mentions[:ZOV_MAX_MENTIONS]
            overflow = total_mentions - len(limited_mentions)

            zov_last_used[chat_id] = now
            call_text = f"📢 {custom_text}\n\n{''.join(limited_mentions)}"
            if overflow > 0:
                call_text += (
                    f"\n\n⚠️ Вызов ограничен: упомянуто {len(limited_mentions)} из {total_mentions} "
                    f"(лимит {ZOV_MAX_MENTIONS})."
                )
            await message.answer(call_text)
            log.info(
                f"Zov sent in chat {chat_id}: caller={user_id}, mentioned={len(limited_mentions)}, total={total_mentions}"
            )
        except Exception as e:
            log.error(f"Error in /zov: {e}")
            await message.answer(f"❌ Ошибка: {str(e)[:100]}")
    
    elif text.startswith("/report"):
        # Report a user to bot owner
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("❌ Использование: /report [причина]\nОтветьте на сообщение пользователя, чтобы пожаловаться.")
            return
        if not reply:
            await message.answer("❌ Ответьте на сообщение пользователя, чтобы пожаловаться!")
            return
        reason = parts[1].lower()
        target_id = reply.from_id
        target_name = await mention_user(target_id, chat_id)
        reporter_name = await mention_user(user_id, chat_id)
        
        # Check for spam in reason - check the reported message
        spam_detected = False
        spam_info = ""
        if "спам" in reason:
            # Check if the reported message contains links or suspicious patterns
            reported_text = reply.text or ""
            link_pattern = r'https?://[^\s]+'
            links = re.findall(link_pattern, reported_text.lower())
            
            # Check for common spam patterns
            spam_patterns = ['купить', 'бесплатно', 'подписывайся', 'подпишись', 'лайк', 'репост', 
                           'казино', 'заработок', 'деньги', 'халява', 'промокод', 'скидка']
            found_patterns = [p for p in spam_patterns if p in reported_text.lower()]
            
            if links or found_patterns:
                spam_detected = True
                spam_info = f"\n⚠️ ОБНАРУЖЕНЫ ПРИЗНАКИ СПАМА!\n"
                if links:
                    spam_info += f"🔗 Ссылки: {len(links)}\n"
                if found_patterns:
                    spam_info += f"📝 Спам-слова: {', '.join(found_patterns)}\n"
                spam_info += f"💬 Сообщение: \"{reported_text[:100]}...\""
        
        # Notify bot owner with button to open chat with violator
        try:
            keyboard = Keyboard(inline=True)
            keyboard = keyboard.add(OpenLink("https://vk.com/write" + str(target_id), "💬 Написать нарушителю"))
            
            report_text = f"📢 Жалоба в чате {chat_id}\n\n👤 Нарушитель: {target_name} (id{target_id})\n📝 От: {reporter_name} (id{user_id})\n❗ Причина: {parts[1]}"
            if spam_detected:
                report_text += spam_info
            
            await api.messages.send(
                peer_id=OWNER_ID,
                message=report_text,
                random_id=random.randint(0, 2**31),
                keyboard=keyboard.get_json()
            )
            
            if spam_detected:
                await message.answer(f"✅ Жалоба отправлена владельцу бота!\n{spam_info}")
            else:
                await message.answer("✅ Жалоба отправлена владельцу бота!")
        except Exception as e:
            log.error(f"Error sending report to owner: {e}")
            await message.answer("❌ Не удалось отправить жалобу.")
    
    elif text.startswith("/nick"):
        role = await get_user_role(chat_id, user_id)
        if role["level"] < 4:
            await message.answer("❌ Только админы могут выдавать ники!")
            return
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            await message.answer("❌ Укажите пользователя: /nick [@id|ID|reply] [ник]")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /nick [@id|ID|reply] [ник]")
            return
        # Extract nickname - get everything after the user mention
        nickname = None
        for i, part in enumerate(parts[1:], 1):
            if not (part.startswith("@id") or part.startswith("[id") or (part.isdigit() and len(part) >= 6)):
                nickname = " ".join(parts[i:])
                break
        if not nickname:
            await message.answer("❌ Укажите ник: /nick [@id|ID|reply] [ник]")
            return
        if len(nickname) > 30:
            await message.answer("❌ Ник слишком длинный (макс. 30 символов)")
            return
        db.set_nickname(target_id, chat_id, nickname, user_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"✅ Ник установлен: {target_name} (id{target_id}) → «{nickname}»")
    
    elif text.startswith("/removenick"):
        role = await get_user_role(chat_id, user_id)
        if role["level"] < 4:
            await message.answer("❌ Только админы могут удалять ники!")
            return
        target_id = await get_target_id(text, reply)
        if not target_id:
            await message.answer("❌ Укажите пользователя: /removenick [@id|ID|reply]")
            return
        db.remove_nickname(target_id, chat_id)
        target_name = await mention_user(target_id, chat_id)
        await message.answer(f"✅ Ник удалён: {target_name} (id{target_id})")
    
    elif text.startswith("/setwelcome"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        welcome = text[12:]
        db.set_welcome(chat_id, welcome)
        await message.answer("✅ Приветствие установлено!")
    
    elif text == "/welcome":
        settings = db.get_chat_settings(chat_id)
        if settings['welcome']:
            await message.answer(f"👋 Приветствие:\n\n{settings['welcome']}")
        else:
            await message.answer("👋 Приветствие не установлено. Используйте /setwelcome [текст]")
    
    elif text == "/notes":
        notes = db.get_notes(chat_id)
        if notes:
            await message.answer("📝 Заметки:\n\n" + "\n".join([f"#{n}" for n in notes]))
        else:
            await message.answer("📝 Нет заметок. Используйте /save [имя] [текст]")
    
    elif text.startswith("/notes"):
        # This handles /notes with extra characters
        pass
    
    elif text.startswith("/note"):
        name = text[6:].strip()
        if not name:
            await message.answer("❌ Использование: /note [имя]")
            return
        content = db.get_note(chat_id, name)
        if content:
            await message.answer(content)
        else:
            await message.answer(f"❌ Заметка #{name} не найдена")
    
    elif text.startswith("/save"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("❌ Использование: /save [имя] [текст]")
            return
        name, content = parts[1], parts[2]
        db.save_note(chat_id, name, content, user_id)
        await message.answer(f"✅ Заметка #{name} сохранена!")

    elif text.startswith("/del"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        if not reply:
            await message.answer("❌ Ответьте на сообщение, которое нужно удалить: /del")
            return
        try:
            await api.messages.delete(
                peer_id=chat_id,
                conversation_message_ids=[reply.conversation_message_id],
                delete_for_all=True
            )
            await message.answer("✅ Сообщение удалено.")
        except Exception as e:
            log.error(f"Error deleting message by /del: {e}")
            await message.answer(f"❌ Не удалось удалить сообщение: {str(e)[:100]}")
    
    elif text.startswith("/delete"):
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        name = text[8:]
        db.delete_note(chat_id, name)
        await message.answer(f"✅ Заметка #{name} удалена.")
    
    elif text.startswith("/pin"):
        # Pin a message (reply to message)
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        if not reply:
            await message.answer("❌ Ответьте на сообщение, которое нужно закрепить: /pin")
            return
        try:
            await api.messages.pin(
                peer_id=chat_id,
                conversation_message_id=reply.conversation_message_id
            )
            await message.answer("📌 Сообщение закреплено!")
        except Exception as e:
            log.error(f"Error pinning message: {e}")
            await message.answer(f"❌ Не удалось закрепить сообщение: {str(e)[:100]}")
    
    elif text == "/unpin":
        # Unpin message
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        try:
            await api.messages.unpin(peer_id=chat_id)
            await message.answer("📌 Сообщение откреплено!")
        except Exception as e:
            log.error(f"Error unpinning message: {e}")
            await message.answer(f"❌ Не удалось открепить сообщение: {str(e)[:100]}")
    
    elif text == "/masskick":
        # Kick users without assigned role (except bot owner and VK starred admins)
        role = await get_user_role(chat_id, user_id)
        if role["level"] < 4 and not is_owner(user_id):
            await message.answer("❌ Только владелец или админ может использовать эту команду!")
            return
        try:
            members = await api.messages.get_conversation_members(peer_id=chat_id)
            kicked_count = 0
            protected_count = 0
            with_role_count = 0

            # VK admins with star in this chat
            starred_admins = {
                m.member_id for m in members.items
                if m.member_id > 0 and getattr(m, "is_admin", False)
            }

            for m in members.items:
                member_id = m.member_id
                # Skip groups (negative IDs) and bot
                if member_id < 0:
                    continue

                # Protect owner and VK starred admins
                if is_owner(member_id) or member_id in starred_admins:
                    protected_count += 1
                    continue

                # Kick only users without assigned role in DB
                assigned_role = db.get_role(member_id, chat_id)
                if assigned_role:
                    with_role_count += 1
                    continue

                success = await kick_user(chat_id, member_id)
                if success:
                    kicked_count += 1
                    log.info(f"Masskick: kicked user {member_id} from chat {chat_id}")
            await message.answer(
                f"✅ Кикнуто без роли: {kicked_count}\n"
                f"🛡 Пропущено (владелец + админы со звездой): {protected_count}\n"
                f"👑 Пропущено (есть роль): {with_role_count}"
            )
        except Exception as e:
            log.error(f"Error in /masskick: {e}")
            await message.answer(f"❌ Ошибка: {str(e)[:100]}")
    
    elif text.startswith("/invite"):
        # Generate invite link for user
        if not await has_permission(chat_id, user_id, "can_warn"):
            await message.answer("❌ Недостаточно прав.")
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("❌ Использование: /invite [ссылка/username/id]\nПримеры:\n/invite https://vk.com/username\n/invite @username\n/invite id123456")
            return
        target = parts[1].strip()
        target_id = None
        
        # Parse different formats
        # https://vk.ru/id123456 or https://vk.com/id123456 (numeric ID)
        match = re.search(r'vk\.(?:ru|com)/id(\d+)', target)
        if match:
            target_id = int(match.group(1))
        # https://vk.ru/username or https://vk.com/username (short link)
        else:
            match = re.search(r'vk\.(?:ru|com)/([a-zA-Z][a-zA-Z0-9_.-]{2,})', target)
            if match:
                username = match.group(1)
                resolved = await resolve_username(username)
                if resolved:
                    target_id = resolved
            # @username format
            elif target.startswith("@"):
                username = target[1:]
                resolved = await resolve_username(username)
                if resolved:
                    target_id = resolved
            # [id123456|name] format
            elif target.startswith("[id"):
                match = re.search(r'\[id(\d+)\|', target)
                if match:
                    target_id = int(match.group(1))
            # Plain numeric ID
            elif target.isdigit() and len(target) >= 6:
                target_id = int(target)
            # Just username without @
            else:
                resolved = await resolve_username(target)
                if resolved:
                    target_id = resolved
        
        if not target_id:
            await message.answer("❌ Не удалось найти пользователя. Проверьте ссылку или username.")
            return
        
        try:
            # Get invite link
            invite_link = await api.messages.get_invite_link(peer_id=chat_id)
            target_name = await mention_user(target_id, chat_id)
            # Send invite link to the user
            keyboard = Keyboard(inline=True)
            keyboard = keyboard.add(OpenLink(invite_link.link, "🔗 Войти в чат"))
            await api.messages.send(
                peer_id=target_id,
                message=f"📨 Вас приглашают в чат!\n\nНажмите кнопку ниже, чтобы присоединиться.",
                random_id=random.randint(0, 2**31),
                keyboard=keyboard.get_json()
            )
            await message.answer(f"✅ Приглашение отправлено {target_name} (id{target_id})!")
            log.info(f"Invite sent to user {target_id} for chat {chat_id} by {user_id}")
        except Exception as e:
            log.error(f"Error sending invite: {e}")
            error_msg = str(e)[:100]
            await message.answer(f"❌ Ошибка: {error_msg}")
    
    # Handle button clicks
    elif message.payload:
        try:
            payload = json.loads(message.payload)
            if isinstance(payload, dict) and payload.get("command") == "unmute":
                target_id = payload.get("user_id")
                target_chat_id = payload.get("chat_id")
                # Check permissions
                if not await has_permission(chat_id, user_id, "can_mute"):
                    await message.answer("❌ Недостаточно прав для снятия мута.")
                    return
                if target_id and target_chat_id:
                    db.unmute_user(target_id, target_chat_id)
                    target_name = await mention_user(target_id, chat_id)
                    await message.answer(f"🔊 Мут снят: {target_name} (id{target_id})")
            elif isinstance(payload, dict) and payload.get("command") == "staff_toggle":
                mode = payload.get("mode") if payload.get("mode") in {"nick", "name"} else "nick"
                await message.answer(
                    await build_staff_text(chat_id, mode),
                    keyboard=staff_keyboard(mode).get_json()
                )
        except Exception as e:
            log.error(f"Error handling button: {e}")
    
    # Unknown command suggestion
    elif text.startswith("/"):
        # List of known commands (English + Russian)
        known_commands = ["/start", "/help", "/profile", "/rules", "/roles", "/staff", "/warns", "/warn", "/unwarn",
                         "/clearwarns", "/kick", "/gkick", "/ban", "/gban", "/unban", "/getban", "/mute", "/gmute", "/unmute", "/setrole", "/grole",
                         "/newrole", "/delrole", "/recrate", "/ownername", "/removerole", "/zov", "/report", "/nick", "/removenick", "/setwelcome", "/linkchat",
                         "/welcome", "/notes", "/note", "/save", "/delete", "/del", "/masskick", "/pin", "/unpin", "/invite", "/q",
                         "/antispam", "/antimat", "/stat", "/topchat", "/chatinfo", "/filter", "/clean", "/ai",
                         # Russian commands
                         "/бан", "/разбан", "/разбань", "/кик", "/кинь", "/мут", "/размут", "/размуть",
                         "/варн", "/пред", "/разварн", "/снятьварн", "/очиститьварны", "/сброситьварны",
                         "/роль", "/сетроль", "/датьроль", "/гроль", "/нроль", "/новаяроль", "/удалитьроль", "/делроль", "/права", "/праваяроли", "/рольвладельца", "/овнерроль", "/снятьроль", "/убратьроль", "/гбан", "/гмут", "/гкик", "/гетбан", "/ник", "/сетник",
                         "/датьник", "/удалитьник", "/снятьник", "/правила", "/приветствие", "/заметки",
                         "/заметка", "/сохранить", "/удалить", "/дел", "/профиль", "/помощь", "/старт", "/админы", "/роли",
                         "/варны", "/закрепить", "/открепить", "/пригласить", "/репорт", "/жалоба", "/зов", "/масскик", "/антиспам", "/антимат",
                         "/объединение", "/линкчат", "/стата", "/топчат", "/чатинфо", "/фильтр", "/чистка", "/очистка",
                         "/ии", "/аи", "/нейро"]
        cmd = text.split()[0].lower()
        if cmd not in known_commands:
            # Find similar command
            def levenshtein(s1, s2):
                if len(s1) < len(s2):
                    return levenshtein(s2, s1)
                if len(s2) == 0:
                    return len(s1)
                prev_row = range(len(s2) + 1)
                for i, c1 in enumerate(s1):
                    curr_row = [i + 1]
                    for j, c2 in enumerate(s2):
                        insertions = prev_row[j + 1] + 1
                        deletions = curr_row[j] + 1
                        substitutions = prev_row[j] + (c1 != c2)
                        curr_row.append(min(insertions, deletions, substitutions))
                    prev_row = curr_row
                return prev_row[-1]
            
            best_match = None
            best_distance = float('inf')
            for known_cmd in known_commands:
                dist = levenshtein(cmd, known_cmd)
                if dist < best_distance and dist <= 3:  # Max 3 characters difference
                    best_distance = dist
                    best_match = known_cmd
            
            if best_match:
                await message.answer(f"❓ Неизвестная команда: {cmd}\n💡 Возможно вы имели в виду: {best_match}?\n📖 /help - список команд")
            else:
                await message.answer(f"❓ Неизвестная команда: {cmd}\n📖 /help - список команд")

if __name__ == "__main__":
    log.info("🚀 Запуск VK Чат Менеджер Бота...")
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(ensure_vk_api_endpoint())
        # Get bot user ID first
        loop.run_until_complete(get_bot_user_id())
        loop.create_task(check_expired_mutes())
        loop.create_task(check_expired_bans())
        bot.run_forever()
    except KeyboardInterrupt:
        log.info("🛑 Бот остановлен")
