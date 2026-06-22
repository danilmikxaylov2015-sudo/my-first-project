import logging
from logging.handlers import RotatingFileHandler
import sqlite3
import time
import re
import random
import asyncio
import json
import os
import ast
import operator
from pathlib import Path
from collections import deque
from datetime import datetime, timedelta, timezone

import aiohttp
from vkbottle import API, Bot, Keyboard, Text, OpenLink
from vkbottle.bot import Message
from vkbottle.http import SingleAiohttpClient

# Paths and configuration
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "vk_chat_manager.db"
LOG_PATH = BASE_DIR / "vk_bot.log"
CONFIG_PATH = BASE_DIR / "config.json"

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Ошибка чтения config.json: {e}")
    return {}

def save_config(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

config = load_config()

# Загружаем токен. Если его нет в конфиге, берем токен из твоего старого файла bot2.py, чтобы бот 100% запустился
DEFAULT_TOKEN = "vk1.a.jmhGtKNRy-okO7WM6HyGJofKiJMaUnBDyB3kEqxdKypWpcnJaEB7KBJixSmIMLc7YLBJHu6wKY2sElm6VlK59GWdnir2DJQl5D9ohPLQ_8USyg-_gpviWLw31YaUIcx51Y84dSXBPjUpwIULup3JGkiHECtNOGSqlxX4q3IvWgeGEwzaXefqwmTa9aFx2-g9b5dmx07Wx-HH3-Tu_2HDag"
VK_TOKEN = config.get("VK_TOKEN", "").strip() or os.getenv("VK_TOKEN", "").strip() or DEFAULT_TOKEN

try:
    OWNER_ID = int(config.get("OWNER_ID", 750694024))
except ValueError:
    OWNER_ID = 750694024

VK_API_URL = config.get("VK_API_URL", "https://api.vk.com/method/").strip() or "https://api.vk.com/method/"
VK_API_FALLBACK_URL = config.get("VK_API_FALLBACK_URL", "https://api.vk.ru/method/").strip() or "https://api.vk.ru/method/"
VK_API_VERSION = config.get("VK_API_VERSION", "5.199").strip() or "5.199"
CEREBRAS_API_KEY = config.get("CEREBRAS_API_KEY", "csk-ph2w5j3tthvhrfhd4n6vw4eypkecj58hppf2eef6y5cte3vy").strip()
CEREBRAS_MODEL = config.get("CEREBRAS_MODEL", "llama3.1-8b").strip() or "llama3.1-8b"
CEREBRAS_API_URL = config.get("CEREBRAS_API_URL", "https://api.cerebras.ai/v1/chat/completions").strip() or "https://api.cerebras.ai/v1/chat/completions"

# Если файла нет, сохраняем дефолтные настройки
if not CONFIG_PATH.exists():
    config["VK_TOKEN"] = VK_TOKEN
    config["OWNER_ID"] = OWNER_ID
    config["CEREBRAS_API_KEY"] = CEREBRAS_API_KEY
    save_config(config)

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
EXPIRE_CHECK_INTERVAL_SECONDS = 5
GROUPS_PAGE_SIZE = 6
ANTIMAT_NOTIFY_COOLDOWN_SECONDS = 15
AI_COOLDOWN_SECONDS = 15
AI_MAX_PROMPT_CHARS = 1500
AI_MAX_REPLY_CHARS = 3500

QUOTES = [
    "Лучше сделать и исправить, чем бесконечно ждать идеала.",
    "Порядок в чате начинается с правил.",
    "Сильная беседа — это активные люди и нормальная модерация.",
    "Уважение — лучший антиспам.",
    "Работает — не трогай."
]

JOKES = [
    "Почему бот не спорит? Потому что у него try/except на эмоции.",
    "Админ сказал: сейчас всё починю. Беседа затаила дыхание.",
    "Модератор — это человек, который видит капс даже во сне.",
    "Почему программисты путают Хэллоуин и Рождество? Потому что 31 OCT == 25 DEC."
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", handlers=[])
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
file_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
log.addHandler(stream_handler)
log.addHandler(file_handler)

# Database Setup
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
                user_id INTEGER, reason TEXT, banned_by INTEGER,
                banned_until INTEGER, chat_id INTEGER,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS ban_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                chat_id INTEGER, reason TEXT, banned_by INTEGER,
                banned_until INTEGER, created_at INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS warns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                chat_id INTEGER, reason TEXT, warned_by INTEGER, timestamp INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY, welcome TEXT, rules TEXT,
                antispam_enabled INTEGER DEFAULT 1, antimat_enabled INTEGER DEFAULT 1,
                antilink_enabled INTEGER DEFAULT 0, anticaps_enabled INTEGER DEFAULT 0
            )
        """)
        columns = [col[1] for col in self.cursor.execute("PRAGMA table_info(chat_settings)").fetchall()]
        if "antispam_enabled" not in columns:
            self.cursor.execute("ALTER TABLE chat_settings ADD COLUMN antispam_enabled INTEGER DEFAULT 1")
        if "antimat_enabled" not in columns:
            self.cursor.execute("ALTER TABLE chat_settings ADD COLUMN antimat_enabled INTEGER DEFAULT 1")
        if "antilink_enabled" not in columns:
            self.cursor.execute("ALTER TABLE chat_settings ADD COLUMN antilink_enabled INTEGER DEFAULT 0")
        if "anticaps_enabled" not in columns:
            self.cursor.execute("ALTER TABLE chat_settings ADD COLUMN anticaps_enabled INTEGER DEFAULT 0")

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER,
                name TEXT, content TEXT, created_by INTEGER, UNIQUE(chat_id, name)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS mutes (
                user_id INTEGER, chat_id INTEGER, muted_until INTEGER,
                muted_by INTEGER, reason TEXT, PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER, chat_id INTEGER, role TEXT,
                assigned_by INTEGER, assigned_at INTEGER, PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS nicknames (
                user_id INTEGER, chat_id INTEGER, nickname TEXT,
                set_by INTEGER, set_at INTEGER, PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_roles (
                chat_id INTEGER, role_name TEXT, display_name TEXT, level INTEGER,
                can_ban INTEGER, can_kick INTEGER, can_warn INTEGER, can_mute INTEGER,
                can_set_role INTEGER, can_gban INTEGER, can_gkick INTEGER, can_gmute INTEGER,
                can_grole INTEGER, created_by INTEGER, PRIMARY KEY (chat_id, role_name)
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
                chat_id INTEGER PRIMARY KEY, last_seen INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS linked_chats (
                chat_id INTEGER PRIMARY KEY, added_by INTEGER, added_at INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY, value TEXT
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_user_stats (
                chat_id INTEGER, user_id INTEGER, messages INTEGER DEFAULT 0,
                first_seen_at INTEGER, last_message_at INTEGER, PRIMARY KEY (chat_id, user_id)
            )
        """)
        stats_columns = [col[1] for col in self.cursor.execute("PRAGMA table_info(chat_user_stats)").fetchall()]
        if "first_seen_at" not in stats_columns:
            self.cursor.execute("ALTER TABLE chat_user_stats ADD COLUMN first_seen_at INTEGER")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS forbidden_words (
                chat_id INTEGER, word TEXT, added_by INTEGER, added_at INTEGER,
                PRIMARY KEY (chat_id, word)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS currency (
                user_id INTEGER, chat_id INTEGER, balance INTEGER DEFAULT 0,
                last_daily INTEGER, PRIMARY KEY (user_id, chat_id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS premium (
                user_id INTEGER PRIMARY KEY, premium_until INTEGER NOT NULL,
                granted_by INTEGER, reason TEXT
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                text TEXT,
                trigger_at INTEGER
            )
        """)
        self.conn.commit()

    def add_reminder(self, user_id, chat_id, text, trigger_at):
        self.cursor.execute("INSERT INTO reminders (user_id, chat_id, text, trigger_at) VALUES (?, ?, ?, ?)", (user_id, chat_id, text, trigger_at))
        self.conn.commit()

    def get_due_reminders(self):
        now = int(time.time())
        return self.cursor.execute("SELECT id, user_id, chat_id, text FROM reminders WHERE trigger_at <= ?", (now,)).fetchall()

    def remove_reminder(self, rem_id):
        self.cursor.execute("DELETE FROM reminders WHERE id=?", (rem_id,))
        self.conn.commit()

    def is_antilink_enabled(self, chat_id):
        result = self.cursor.execute("SELECT COALESCE(antilink_enabled, 0) FROM chat_settings WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(result[0]) if result else False

    def set_antilink_enabled(self, chat_id, enabled):
        value = 1 if enabled else 0
        self.cursor.execute("UPDATE chat_settings SET antilink_enabled=? WHERE chat_id=?", (value, chat_id))
        self.conn.commit()

    def is_anticaps_enabled(self, chat_id):
        result = self.cursor.execute("SELECT COALESCE(anticaps_enabled, 0) FROM chat_settings WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(result[0]) if result else False

    def set_anticaps_enabled(self, chat_id, enabled):
        value = 1 if enabled else 0
        self.cursor.execute("UPDATE chat_settings SET anticaps_enabled=? WHERE chat_id=?", (value, chat_id))
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
        result = self.cursor.execute("SELECT COUNT(*) FROM ban_history WHERE user_id=? AND chat_id=?", (user_id, chat_id)).fetchone()
        return result[0] if result else 0

    def get_last_ban_history(self, user_id, chat_id):
        return self.cursor.execute(
            "SELECT reason, banned_by, banned_until, created_at FROM ban_history WHERE user_id=? AND chat_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id, chat_id)
        ).fetchone()
    
    def get_warns(self, user_id, chat_id):
        result = self.cursor.execute("SELECT COUNT(*) FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)).fetchone()
        return result[0] if result else 0
    
    def add_warn(self, user_id, chat_id, reason, warned_by):
        self.cursor.execute("INSERT INTO warns (user_id, chat_id, reason, warned_by, timestamp) VALUES (?, ?, ?, ?, ?)", (user_id, chat_id, reason, warned_by, int(time.time())))
        self.conn.commit()
        return self.get_warns(user_id, chat_id)
    
    def clear_warns(self, user_id, chat_id):
        self.cursor.execute("DELETE FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        self.conn.commit()
    
    def remove_one_warn(self, user_id, chat_id):
        result = self.cursor.execute("SELECT id FROM warns WHERE user_id=? AND chat_id=? ORDER BY timestamp ASC LIMIT 1", (user_id, chat_id)).fetchone()
        if result:
            self.cursor.execute("DELETE FROM warns WHERE id=?", (result[0],))
            self.conn.commit()
            return True
        return False
    
    def get_chat_settings(self, chat_id):
        result = self.cursor.execute(
            "SELECT welcome, rules, COALESCE(antispam_enabled, 1), COALESCE(antimat_enabled, 1), COALESCE(antilink_enabled, 0), COALESCE(anticaps_enabled, 0) FROM chat_settings WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if result:
            return {
                'welcome': result[0] or '',
                'rules': result[1] or '',
                'antispam_enabled': bool(result[2]),
                'antimat_enabled': bool(result[3]),
                'antilink_enabled': bool(result[4]),
                'anticaps_enabled': bool(result[5]),
            }
        return {'welcome': '', 'rules': '', 'antispam_enabled': True, 'antimat_enabled': True, 'antilink_enabled': False, 'anticaps_enabled': False}
    
    def set_welcome(self, chat_id, welcome):
        self.cursor.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, welcome, rules, antispam_enabled, antimat_enabled, antilink_enabled, anticaps_enabled) VALUES (?, ?, COALESCE((SELECT rules FROM chat_settings WHERE chat_id=?), ''), COALESCE((SELECT antispam_enabled FROM chat_settings WHERE chat_id=?), 1), COALESCE((SELECT antimat_enabled FROM chat_settings WHERE chat_id=?), 1), COALESCE((SELECT antilink_enabled FROM chat_settings WHERE chat_id=?), 0), COALESCE((SELECT anticaps_enabled FROM chat_settings WHERE chat_id=?), 0))",
            (chat_id, welcome, chat_id, chat_id, chat_id, chat_id, chat_id)
        )
        self.conn.commit()
    
    def set_rules(self, chat_id, rules):
        self.cursor.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, welcome, rules, antispam_enabled, antimat_enabled, antilink_enabled, anticaps_enabled) VALUES (?, COALESCE((SELECT welcome FROM chat_settings WHERE chat_id=?), ''), ?, COALESCE((SELECT antispam_enabled FROM chat_settings WHERE chat_id=?), 1), COALESCE((SELECT antimat_enabled FROM chat_settings WHERE chat_id=?), 1), COALESCE((SELECT antilink_enabled FROM chat_settings WHERE chat_id=?), 0), COALESCE((SELECT anticaps_enabled FROM chat_settings WHERE chat_id=?), 0))",
            (chat_id, chat_id, rules, chat_id, chat_id, chat_id, chat_id)
        )
        self.conn.commit()

    def is_antispam_enabled(self, chat_id):
        result = self.cursor.execute("SELECT COALESCE(antispam_enabled, 1) FROM chat_settings WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(result[0]) if result else True

    def set_antispam_enabled(self, chat_id, enabled):
        value = 1 if enabled else 0
        self.cursor.execute("UPDATE chat_settings SET antispam_enabled=? WHERE chat_id=?", (value, chat_id))
        self.conn.commit()

    def is_antimat_enabled(self, chat_id):
        result = self.cursor.execute("SELECT COALESCE(antimat_enabled, 1) FROM chat_settings WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(result[0]) if result else True

    def set_antimat_enabled(self, chat_id, enabled):
        value = 1 if enabled else 0
        self.cursor.execute("UPDATE chat_settings SET antimat_enabled=? WHERE chat_id=?", (value, chat_id))
        self.conn.commit()
    
    def save_note(self, chat_id, name, content, created_by):
        self.cursor.execute("INSERT OR REPLACE INTO notes (chat_id, name, content, created_by) VALUES (?, ?, ?, ?)", (chat_id, name, content, created_by))
        self.conn.commit()
    
    def get_note(self, chat_id, name):
        result = self.cursor.execute("SELECT content FROM notes WHERE chat_id=? AND name=?", (chat_id, name)).fetchone()
        return result[0] if result else None
    
    def get_notes(self, chat_id):
        return [r[0] for r in self.cursor.execute("SELECT name FROM notes WHERE chat_id=?", (chat_id,)).fetchall()]
    
    def delete_note(self, chat_id, name):
        self.cursor.execute("DELETE FROM notes WHERE chat_id=? AND name=?", (chat_id, name))
        self.conn.commit()
    
    def is_muted(self, user_id, chat_id):
        now = int(time.time())
        result = self.cursor.execute("SELECT muted_until FROM mutes WHERE user_id=? AND chat_id=? AND muted_until > ?", (user_id, chat_id, now)).fetchone()
        return result is not None
    
    def get_mute_info(self, user_id, chat_id):
        now = int(time.time())
        return self.cursor.execute("SELECT muted_until, muted_by, reason FROM mutes WHERE user_id=? AND chat_id=? AND muted_until > ?", (user_id, chat_id, now)).fetchone()
    
    def mute_user(self, user_id, chat_id, minutes, muted_by, reason=""):
        duration_seconds = max(1, int(float(minutes) * 60))
        muted_until = int(time.time()) + duration_seconds
        self.cursor.execute("INSERT OR REPLACE INTO mutes (user_id, chat_id, muted_until, muted_by, reason) VALUES (?, ?, ?, ?, ?)", (user_id, chat_id, muted_until, muted_by, reason))
        self.conn.commit()
    
    def unmute_user(self, user_id, chat_id):
        self.cursor.execute("DELETE FROM mutes WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        self.conn.commit()
    
    def get_role(self, user_id, chat_id):
        result = self.cursor.execute("SELECT role FROM user_roles WHERE user_id=? AND chat_id=?", (user_id, chat_id)).fetchone()
        return result[0] if result else None
    
    def set_role(self, user_id, chat_id, role, assigned_by):
        self.cursor.execute("INSERT OR REPLACE INTO user_roles (user_id, chat_id, role, assigned_by, assigned_at) VALUES (?, ?, ?, ?, ?)", (user_id, chat_id, role, assigned_by, int(time.time())))
        self.conn.commit()
    
    def remove_role(self, user_id, chat_id):
        self.cursor.execute("DELETE FROM user_roles WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        self.conn.commit()
    
    def get_users_by_role(self, chat_id, role):
        return self.cursor.execute("SELECT user_id FROM user_roles WHERE chat_id=? AND role=?", (chat_id, role)).fetchall()
    
    def get_expired_mutes(self):
        now = int(time.time())
        return self.cursor.execute("SELECT user_id, chat_id, muted_by, reason FROM mutes WHERE muted_until <= ? AND muted_until > 0", (now,)).fetchall()
    
    def cleanup_expired_mutes(self):
        now = int(time.time())
        self.cursor.execute("DELETE FROM mutes WHERE muted_until <= ? AND muted_until > 0", (now,))
        self.conn.commit()

    def get_expired_bans(self):
        now = int(time.time())
        return self.cursor.execute("SELECT user_id, chat_id, reason, banned_by FROM banned WHERE banned_until IS NOT NULL AND banned_until <= ?", (now,)).fetchall()

    def cleanup_expired_bans(self):
        now = int(time.time())
        self.cursor.execute("DELETE FROM banned WHERE banned_until IS NOT NULL AND banned_until <= ?", (now,))
        self.conn.commit()
    
    def set_nickname(self, user_id, chat_id, nickname, set_by):
        self.cursor.execute("INSERT OR REPLACE INTO nicknames (user_id, chat_id, nickname, set_by, set_at) VALUES (?, ?, ?, ?, ?)", (user_id, chat_id, nickname, set_by, int(time.time())))
        self.conn.commit()
    
    def get_nickname(self, user_id, chat_id):
        result = self.cursor.execute("SELECT nickname FROM nicknames WHERE user_id=? AND chat_id=?", (user_id, chat_id)).fetchone()
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
                chat_id, normalize_role_key(role_name), role_name.strip(), level,
                permissions.get('can_ban', 0), permissions.get('can_kick', 0), permissions.get('can_warn', 0),
                permissions.get('can_mute', 0), permissions.get('can_set_role', 0), permissions.get('can_gban', 0),
                permissions.get('can_gkick', 0), permissions.get('can_gmute', 0), permissions.get('can_grole', 0), created_by,
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
                    role_key, role_name.strip(), int(level),
                    permissions.get('can_ban', 0), permissions.get('can_kick', 0), permissions.get('can_warn', 0),
                    permissions.get('can_mute', 0), permissions.get('can_set_role', 0), permissions.get('can_gban', 0),
                    permissions.get('can_gkick', 0), permissions.get('can_gmute', 0), permissions.get('can_grole', 0),
                    created_by, chat_id, old_key,
                )
            )
            self.cursor.execute("UPDATE user_roles SET role=? WHERE chat_id=? AND role=?", (role_key, chat_id, old_key))
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
                "name": result[0] or role_name, "level": result[1], "can_ban": bool(result[2]), "can_kick": bool(result[3]),
                "can_warn": bool(result[4]), "can_mute": bool(result[5]), "can_set_role": bool(result[6]),
                "can_gban": bool(result[7]), "can_gkick": bool(result[8]), "can_gmute": bool(result[9]), "can_grole": bool(result[10])
            }
        return None

    def get_custom_role_by_level(self, chat_id, level):
        result = self.cursor.execute("SELECT role_name FROM custom_roles WHERE chat_id=? AND level=? ORDER BY role_name LIMIT 1", (chat_id, int(level))).fetchone()
        if not result: return None
        return result[0], self.get_custom_role(chat_id, result[0])
    
    def get_custom_roles(self, chat_id):
        return self.cursor.execute("SELECT role_name, COALESCE(display_name, role_name), level FROM custom_roles WHERE chat_id=? ORDER BY level DESC", (chat_id,)).fetchall()

    def get_all_user_roles(self, chat_id):
        return self.cursor.execute("SELECT user_id, role FROM user_roles WHERE chat_id=?", (chat_id,)).fetchall()
    
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
        if not role_key or not role_data: return None
        column_map = {"ban": "can_ban", "kick": "can_kick", "warn": "can_warn", "mute": "can_mute", "setrole": "can_set_role", "role": "can_set_role", "gban": "can_gban", "gkick": "can_gkick", "gmute": "can_gmute", "grole": "can_grole"}
        column = column_map.get(permission)
        if not column: return None
        self.cursor.execute(f"UPDATE custom_roles SET {column}=? WHERE chat_id=? AND role_name=?", (1 if enabled else 0, chat_id, role_key))
        self.conn.commit()
        return role_key, self.get_custom_role(chat_id, role_key), column

    def touch_chat(self, chat_id):
        try:
            self.cursor.execute("INSERT OR REPLACE INTO known_chats (chat_id, last_seen) VALUES (?, ?)", (chat_id, int(time.time())))
            self.conn.commit()
        except sqlite3.OperationalError as exc:
            log.warning(f"Failed to touch chat {chat_id}: {exc}")

    def get_known_chats(self):
        return [row[0] for row in self.cursor.execute("SELECT chat_id FROM known_chats ORDER BY last_seen DESC").fetchall()]

    def link_chat(self, chat_id, added_by):
        self.cursor.execute("INSERT OR REPLACE INTO linked_chats (chat_id, added_by, added_at) VALUES (?, ?, ?)", (chat_id, added_by, int(time.time())))
        self.conn.commit()

    def unlink_chat(self, chat_id):
        self.cursor.execute("DELETE FROM linked_chats WHERE chat_id=?", (chat_id,))
        self.conn.commit()

    def clear_linked_chats(self):
        self.cursor.execute("DELETE FROM linked_chats")
        self.conn.commit()

    def get_linked_chats(self):
        return [row[0] for row in self.cursor.execute("SELECT chat_id FROM linked_chats ORDER BY added_at DESC").fetchall()]

    def is_chat_linked(self, chat_id):
        result = self.cursor.execute("SELECT 1 FROM linked_chats WHERE chat_id=?", (chat_id,)).fetchone()
        return result is not None

    def set_setting(self, key, value):
        self.cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, value))
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
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                messages = messages + 1,
                first_seen_at = COALESCE(chat_user_stats.first_seen_at, excluded.first_seen_at),
                last_message_at = excluded.last_message_at
            """, (int(chat_id), int(user_id), now, now)
        )
        self.conn.commit()

    def get_user_stat(self, chat_id, user_id):
        return self.cursor.execute("SELECT messages, COALESCE(first_seen_at, last_message_at), last_message_at FROM chat_user_stats WHERE chat_id=? AND user_id=?", (int(chat_id), int(user_id))).fetchone()

    def get_top_chat_stats(self, chat_id, limit=10):
        return self.cursor.execute("SELECT user_id, messages, last_message_at FROM chat_user_stats WHERE chat_id=? ORDER BY messages DESC, last_message_at DESC LIMIT ?", (int(chat_id), int(limit))).fetchall()

    def add_forbidden_word(self, chat_id, word, added_by):
        self.cursor.execute("INSERT OR REPLACE INTO forbidden_words (chat_id, word, added_by, added_at) VALUES (?, ?, ?, ?)", (int(chat_id), word.lower().strip(), int(added_by), int(time.time())))
        self.conn.commit()

    def remove_forbidden_word(self, chat_id, word):
        self.cursor.execute("DELETE FROM forbidden_words WHERE chat_id=? AND word=?", (int(chat_id), word.lower().strip()))
        self.conn.commit()

    def get_forbidden_words(self, chat_id):
        return [row[0] for row in self.cursor.execute("SELECT word FROM forbidden_words WHERE chat_id=? ORDER BY word", (int(chat_id),)).fetchall()]

    # Economy
    def _economy_row(self, user_id, chat_id=0):
        chat_id = int(chat_id or 0)
        self.cursor.execute("INSERT OR IGNORE INTO currency (user_id, chat_id, balance, last_daily) VALUES (?, ?, 0, 0)", (int(user_id), chat_id))
        return self.cursor.execute("SELECT balance, COALESCE(last_daily, 0) FROM currency WHERE user_id=? AND chat_id=?", (int(user_id), chat_id)).fetchone()

    def get_balance(self, user_id, chat_id=0):
        row = self._economy_row(user_id, chat_id)
        return int(row[0]) if row else 0

    def add_balance(self, user_id, amount, chat_id=0):
        amount = int(amount)
        if amount == 0: return self.get_balance(user_id, chat_id)
        self._economy_row(user_id, chat_id)
        self.cursor.execute("UPDATE currency SET balance = CASE WHEN balance + ? < 0 THEN 0 ELSE balance + ? END WHERE user_id=? AND chat_id=?", (amount, amount, int(user_id), int(chat_id or 0)))
        self.conn.commit()
        return self.get_balance(user_id, chat_id)

    def spend_balance(self, user_id, amount, chat_id=0):
        amount = int(amount)
        if amount <= 0: return False
        if self.get_balance(user_id, chat_id) < amount: return False
        self.add_balance(user_id, -amount, chat_id)
        return True

    def can_claim_daily(self, user_id, chat_id=0, cooldown_seconds=86400):
        row = self._economy_row(user_id, chat_id)
        last_daily = int(row[1]) if row else 0
        return int(time.time()) - last_daily >= cooldown_seconds

    def claim_daily(self, user_id, amount, chat_id=0):
        self._economy_row(user_id, chat_id)
        self.cursor.execute("UPDATE currency SET balance = balance + ?, last_daily=? WHERE user_id=? AND chat_id=?", (int(amount), int(time.time()), int(user_id), int(chat_id or 0)))
        self.conn.commit()
        return self.get_balance(user_id, chat_id)

    def get_premium_info(self, user_id):
        return self.cursor.execute("SELECT premium_until, granted_by, reason FROM premium WHERE user_id=?", (int(user_id),)).fetchone()

    def is_premium(self, user_id):
        row = self.get_premium_info(user_id)
        return bool(row and int(row[0]) > int(time.time()))

    def grant_premium(self, user_id, days, granted_by, reason=""):
        premium_until = int(time.time()) + max(1, int(days)) * 86400
        current = self.get_premium_info(user_id)
        if current and int(current[0]) > int(time.time()):
            premium_until = max(premium_until, int(current[0]) + max(1, int(days)) * 86400)
        self.cursor.execute("INSERT OR REPLACE INTO premium (user_id, premium_until, granted_by, reason) VALUES (?, ?, ?, ?)", (int(user_id), premium_until, int(granted_by), reason))
        self.conn.commit()
        return premium_until

    def extend_premium(self, user_id, days, granted_by, reason=""):
        return self.grant_premium(user_id, days, granted_by, reason)

    def get_top_balances(self, limit=10, chat_id=0):
        return self.cursor.execute("SELECT user_id, balance FROM currency WHERE chat_id=? ORDER BY balance DESC, user_id ASC LIMIT ?", (int(chat_id or 0), int(limit))).fetchall()

    def get_daily_streak_reset(self, user_id, chat_id=0):
        row = self._economy_row(user_id, chat_id)
        return int(row[1]) if row else 0

    def _bootstrap_known_chats(self):
        if self.cursor.execute("SELECT COUNT(*) FROM known_chats").fetchone()[0] > 0: return
        sources = ["chat_settings", "user_roles", "notes", "mutes", "warns", "banned", "nicknames", "custom_roles"]
        now = int(time.time())
        chat_ids = set()
        for table_name in sources:
            try:
                for row in self.cursor.execute(f"SELECT DISTINCT chat_id FROM {table_name}").fetchall():
                    if isinstance(row[0], int) and row[0] >= 2000000000: chat_ids.add(row[0])
            except Exception: continue
        for chat_id in chat_ids:
            self.cursor.execute("INSERT OR IGNORE INTO known_chats (chat_id, last_seen) VALUES (?, ?)", (chat_id, now))
        self.conn.commit()

db = Database()
vk_http_client = SingleAiohttpClient(trust_env=True)
api = API(VK_TOKEN, http_client=vk_http_client)
api.API_URL = VK_API_URL
api.API_VERSION = VK_API_VERSION
bot = Bot(api=api)

BOT_USER_ID = None
async def get_bot_user_id():
    global BOT_USER_ID
    try:
        me = await api.users.get()
        if me: BOT_USER_ID = me[0].id
    except: pass

async def ensure_vk_api_endpoint():
    candidates = [VK_API_URL, VK_API_FALLBACK_URL]
    seen = set()
    last_error = None
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate or candidate in seen: continue
        seen.add(candidate)
        api.API_URL = candidate if candidate.endswith("/") else candidate + "/"
        try:
            await api.request("utils.getServerTime", {})
            return
        except Exception as exc: last_error = exc
    raise SystemExit(f"VK API is unreachable. Last error: {last_error}")

ROLES = {
    "owner": {"name": "Владелец", "level": 100, "can_ban": True, "can_kick": True, "can_warn": True, "can_mute": True, "can_set_role": True, "can_gban": True, "can_gkick": True, "can_gmute": True, "can_grole": True},
    "admin": {"name": "Admin", "level": 80, "can_ban": True, "can_kick": True, "can_warn": True, "can_mute": True, "can_set_role": False, "can_gban": False, "can_gkick": False, "can_gmute": False, "can_grole": False},
    "moderator": {"name": "Moderator", "level": 50, "can_ban": False, "can_kick": True, "can_warn": True, "can_mute": True, "can_set_role": False, "can_gban": False, "can_gkick": False, "can_gmute": False, "can_grole": False},
    "helper": {"name": "Helper", "level": 20, "can_ban": False, "can_kick": False, "can_warn": True, "can_mute": True, "can_set_role": False, "can_gban": False, "can_gkick": False, "can_gmute": False, "can_grole": False},
    "user": {"name": "User", "level": 0, "can_ban": False, "can_kick": False, "can_warn": False, "can_mute": False, "can_set_role": False, "can_gban": False, "can_gkick": False, "can_gmute": False, "can_grole": False}
}

def get_owner_role_name(): return db.get_setting("owner_role_name", ROLES["owner"]["name"])
def role_permissions_from_level(level):
    level = int(level)
    return {"can_ban": level>=80, "can_kick": level>=50, "can_warn": level>=20, "can_mute": level>=20, "can_set_role": level>=100, "can_gban": level>=100, "can_gkick": level>=100, "can_gmute": level>=100, "can_grole": level>=100}
def build_priority_role(role_name, level): return {"name": role_name, "level": int(level), **role_permissions_from_level(level)}
def normalize_role_key(role): return (role or "").strip().lower()

def safe_calc(expr):
    expr = expr.strip().replace(",", ".")
    if len(expr) > 80: return "❌ Слишком длинный пример"
    if not re.fullmatch(r"[0-9+\-*/(). %]+", expr): return "❌ Разрешены только цифры и + - * / % ( )"
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
    def ev(node):
        if isinstance(node, ast.Expression): return ev(node.body)
        if getattr(ast, 'Constant', None) and isinstance(node, getattr(ast, 'Constant')) and isinstance(node.value, (int, float)): return node.value
        if getattr(ast, 'Num', None) and isinstance(node, getattr(ast, 'Num')): return node.n
        if isinstance(node, ast.BinOp) and type(node.op) in ops: return ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops: return ops[type(node.op)](ev(node.operand))
        raise ValueError("Запрещённая операция")
    try: return f"🧮 Ответ: {ev(ast.parse(expr, mode='eval'))}"
    except Exception: return "❌ Ошибка вычисления"

def parse_bool_word(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes", "on", "да", "вкл", "включить"}: return True
    if normalized in {"false", "0", "no", "off", "нет", "выкл", "выключить"}: return False
    return None

def extract_role_argument(parts, has_reply): return " ".join(parts[1:]).strip() if has_reply and len(parts) > 1 else " ".join(parts[2:]).strip() if len(parts) > 2 else ""

def resolve_role_for_chat(chat_id, role_input):
    key = normalize_role_key(role_input)
    if not key: return None, None
    if key == normalize_role_key(get_owner_role_name()) or key == "владелец": return "level:100", build_priority_role(get_owner_role_name(), 100)
    if key in ROLES and key != "owner": return key, ROLES[key]
    if key.startswith("level:") and key[6:].isdigit():
        level = int(key[6:])
        if level == 0: return "user", ROLES["user"]
        if level == 100: return "level:100", build_priority_role(get_owner_role_name(), 100)
        if 0 <= level <= 100:
            custom_by_level = db.get_custom_role_by_level(chat_id, level)
            return custom_by_level if custom_by_level else (f"level:{level}", build_priority_role(f"Priority {level}", level))
    if key.isdigit():
        level = int(key)
        if level == 0: return "user", ROLES["user"]
        if level == 100: return "level:100", build_priority_role(get_owner_role_name(), 100)
        if 0 <= level <= 100:
            custom_by_level = db.get_custom_role_by_level(chat_id, level)
            return custom_by_level if custom_by_level else (f"level:{level}", build_priority_role(f"Priority {level}", level))
    for custom_key, custom_display_name, _ in db.get_custom_roles(chat_id):
        if key == normalize_role_key(custom_display_name): return custom_key, db.get_custom_role(chat_id, custom_key)
    custom_role = db.get_custom_role(chat_id, key)
    if custom_role: return key, custom_role
    return None, None

def format_role_list(chat_id):
    def perms_text(role):
        enabled = [p for p, k in [("ban", "can_ban"), ("kick", "can_kick"), ("warn", "can_warn"), ("mute", "can_mute"), ("role", "can_set_role"), ("gban", "can_gban"), ("gkick", "can_gkick"), ("gmute", "can_gmute"), ("grole", "can_grole")] if role.get(k)]
        return ", ".join(enabled) if enabled else "нет"
    lines = ["Список ролей:", f"{get_owner_role_name()} — 100"]
    for role_key in ("admin", "moderator", "helper", "user"):
        role = ROLES[role_key]; lines.append(f"{role['name']} — {role['level']} ({perms_text(role)})")
    custom_roles = db.get_custom_roles(chat_id)
    if custom_roles:
        lines.extend(["", "Созданные роли:"])
        for role_key, display_name, level in custom_roles:
            role = db.get_custom_role(chat_id, role_key)
            lines.append(f"{display_name or role_key} — {level} ({perms_text(role or {})})")
    return "\n".join(lines)

def is_owner(user_id): return user_id == OWNER_ID

async def get_user_role(chat_id, user_id):
    if is_owner(user_id): return {**ROLES["owner"], "name": get_owner_role_name()}
    role_name = db.get_role(user_id, chat_id)
    if role_name:
        _, resolved_role = resolve_role_for_chat(chat_id, role_name)
        if resolved_role: return resolved_role
    try:
        members = await api.messages.get_conversation_members(peer_id=chat_id)
        for m in members.items:
            if m.member_id == user_id and getattr(m, "is_admin", False): return ROLES["admin"]
    except: pass
    return ROLES["user"]

async def has_permission(chat_id, user_id, permission):
    role = await get_user_role(chat_id, user_id)
    return role.get(permission, False) or is_owner(user_id)

async def can_manage_target(chat_id, actor_id, target_id, action_name="действие"):
    if is_owner(actor_id): return True, ""
    if is_owner(target_id): return False, "❌ Нельзя применять это к владельцу."
    actor_role = await get_user_role(chat_id, actor_id)
    target_role = await get_user_role(chat_id, target_id)
    if int(target_role.get("level", 0)) >= int(actor_role.get("level", 0)):
        return False, f"❌ Нельзя выполнить {action_name}: роль цели {target_role.get('name')} ({target_role.get('level')}) не ниже вашей {actor_role.get('name')} ({actor_role.get('level')})."
    return True, ""

async def can_assign_role(chat_id, actor_id, target_id, role_data):
    if is_owner(actor_id): return True, ""
    actor_role = await get_user_role(chat_id, actor_id)
    if int(role_data.get("level", 0)) >= int(actor_role.get("level", 0)): return False, f"❌ Нельзя выдать роль {role_data.get('name')} ({role_data.get('level')}): она не ниже вашей роли {actor_role.get('name')} ({actor_role.get('level')})."
    return await can_manage_target(chat_id, actor_id, target_id, "выдачу роли")

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
    if message_id in processed_message_ids: return False
    processed_message_ids.add(message_id)
    processed_messages.append((time.time(), message_id))
    return True

def remember_chat_message(chat_id, conversation_message_id):
    if not conversation_message_id: return
    messages = recent_chat_messages.setdefault(chat_id, deque(maxlen=250))
    messages.append(int(conversation_message_id))

def get_recent_chat_message_ids(chat_id, limit):
    messages = recent_chat_messages.get(chat_id)
    return list(messages)[-max(1, min(int(limit), 100)):] if messages else []

ANTIMAT_STEMS = ("бля", "бляд", "блять", "сук", "хуй", "хуе", "хуя", "хер", "пизд", "еба", "ебл", "ебн", "ебуч", "ебат", "долбоеб", "мудак", "гандон", "уеб", "мраз", "чмо", "нахуй", "похуй", "оху")

def is_antispam_exempt(chat_id, user_id): return is_owner(user_id)

def check_antispam(chat_id, user_id, text, attachments_count=0):
    now = time.time()
    state = antispam_tracker.setdefault((chat_id, user_id), {"times": deque(), "texts": deque()})
    times, texts = state["times"], state["texts"]
    while times and now - times[0] > ANTISPAM_FLOOD_WINDOW: times.popleft()
    times.append(now)
    while texts and now - texts[0][0] > ANTISPAM_DUPLICATE_WINDOW: texts.popleft()
    normalized_text = re.sub(r"\s+", " ", (text or "").strip().lower())
    duplicate_count = 0
    if normalized_text:
        texts.append((now, normalized_text))
        if len(normalized_text) >= 4: duplicate_count = sum(1 for _, msg in texts if msg == normalized_text)
    link_count = len(re.findall(r"(https?://\S+|vk\.(?:ru|com)/\S+)", text or "", flags=re.IGNORECASE))
    if len(times) >= ANTISPAM_FLOOD_LIMIT: return True, f"флуд ({len(times)} сообщений за {ANTISPAM_FLOOD_WINDOW}с)"
    if duplicate_count >= ANTISPAM_DUPLICATE_LIMIT: return True, f"повтор одинаковых сообщений ({duplicate_count})"
    if link_count >= ANTISPAM_LINK_LIMIT: return True, f"слишком много ссылок ({link_count})"
    if attachments_count >= ANTISPAM_ATTACHMENTS_LIMIT: return True, f"слишком много вложений ({attachments_count})"
    return False, ""

def clear_antispam_state(chat_id, user_id): antispam_tracker.pop((chat_id, user_id), None)

def contains_profanity(text):
    if not text: return False
    for token in re.findall(r"[a-zа-я0-9_]+", text.lower().replace("ё", "е")):
        if len(token) >= 3 and any(token.startswith(s) for s in ANTIMAT_STEMS): return True
    return False

def parse_mute_duration(raw_value):
    value = (raw_value or "").strip().lower()
    if not value: return None
    if value.isdigit(): return int(value) * 60 if int(value) > 0 else None, f"{value} мин."
    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match: return None
    amount, unit = int(match.group(1)), match.group(2)
    if amount <= 0: return None
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    labels = {"s": "сек.", "m": "мин.", "h": "ч.", "d": "д."}
    return amount * multiplier[unit], f"{amount} {labels[unit]}"

def parse_ban_duration(raw_value):
    value = (raw_value or "").strip().lower()
    if not value: return None
    if value.isdigit(): return {"seconds": int(value) * 86400, "text": f"{value} дн.", "is_days": True} if int(value) > 0 else None
    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match: return None
    amount, unit = int(match.group(1)), match.group(2)
    if amount <= 0: return None
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    labels = {"s": "сек.", "m": "мин.", "h": "ч.", "d": "д."}
    return {"seconds": amount * multiplier[unit], "text": f"{amount} {labels[unit]}", "is_days": unit == "d"}

def extract_user_id(text, reply_message=None):
    if reply_message: return reply_message.from_id
    for pat, is_neg in [(r'@id(\d+)', False), (r'\[id(\d+)\|', False), (r'\[(?:club|public)(\d+)\|', True), (r'@(?:club|public)(\d+)', True), (r'vk\.(?:ru|com)/id(\d+)', False), (r'vk\.(?:ru|com)/(?:club|public)(\d+)', True), (r'(\d{6,})', False)]:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m: return -int(m.group(1)) if is_neg else int(m.group(1))
    for pat in [r'@([a-zA-Z][a-zA-Z0-9_.-]{2,})', r'vk\.(?:ru|com)/(id_[a-zA-Z][a-zA-Z0-9_.-]+)', r'vk\.(?:ru|com)/([a-zA-Z][a-zA-Z0-9_.-]{2,})']:
        m = re.search(pat, text)
        if m: return f"username:{m.group(1)}"
    return None

async def resolve_username(username):
    try:
        result = await api.utils.resolve_screen_name(screen_name=username)
        if result and hasattr(result, 'type'): return result.object_id if result.type == "user" else -result.object_id if result.type in ("group", "page", "event") else None
    except: pass
    return None

async def get_target_id(text, reply_message=None):
    target = extract_user_id(text, reply_message)
    if isinstance(target, str) and target.startswith("username:"): return await resolve_username(target[9:])
    return target

ECONOMY_SCOPE_ID = 0
DAILY_REWARD = 100
VIP_DAILY_REWARD = 150
VIP_PRICE_COINS = 500
SHOP_ITEMS = {"vip_30": {"price": VIP_PRICE_COINS, "days": 30, "title": "VIP на 30 дней"}, "vip_90": {"price": 1200, "days": 90, "title": "VIP на 90 дней"}, "vip_365": {"price": 3500, "days": 365, "title": "VIP на 365 дней"}}
GAME_EMOJIS = ["🍒", "🍋", "🍇", "⭐", "💎", "7️⃣"]

def format_coins(amount): return f"🪙 {int(amount):,}".replace(",", " ")

async def ask_cerebras(prompt, user_context=""):
    if not CEREBRAS_API_KEY: raise RuntimeError("CEREBRAS_API_KEY is not configured")
    prompt = (prompt or "").strip()[:AI_MAX_PROMPT_CHARS]
    if not prompt: raise ValueError("empty prompt")
    moscow_now = datetime.now(timezone.utc)
    system_prompt = f"Ты полезный ассистент внутри VK чат-бота. Отвечай по-русски, кратко и понятно. Текущая дата и время по Москве (GMT+03:00): {moscow_now.strftime('%d.%m.%Y %H:%M')}. Не используй markdown-таблицы." + (f" Контекст пользователя: {user_context}" if user_context else "")
    time_context = f"Сейчас в Москве {moscow_now.strftime('%d.%m.%Y %H:%M')}.\n\nВопрос пользователя: {prompt}"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45), trust_env=True) as session:
        async with session.post(CEREBRAS_API_URL, headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}, json={"model": CEREBRAS_MODEL, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": time_context}], "temperature": 0.7, "max_completion_tokens": 700}) as response:
            raw_text = await response.text()
            if response.status >= 400: raise RuntimeError(f"API error: {raw_text[:300]}")
    content = (((json.loads(raw_text).get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    return content[:AI_MAX_REPLY_CHARS].rstrip() + "\n\n…ответ сокращён" if len(content) > AI_MAX_REPLY_CHARS else content

def get_premium_status_text(user_id):
    info = db.get_premium_info(user_id)
    return f"VIP, осталось {max(1, (int(info[0]) - int(time.time()) + 86399) // 86400)} дн." if info and int(info[0]) > int(time.time()) else "обычный"

def parse_positive_int(value):
    try: parsed = int(str(value).strip())
    except: return None
    return parsed if parsed > 0 else None

def calc_slots_payout(bet, a, b, c): return bet * 5 if a == b == c else bet * 2 if a == b or a == c or b == c else 0

async def handle_economy_command(message: Message, text: str, user_id: int) -> bool:
    raw = (text or "").strip()
    if not raw or not raw.startswith("/"): return False
    command, args, reply = raw.split()[0].lower(), raw.split()[1:], getattr(message, "reply_message", None)

    if command in {"/coins", "/balance", "/монеты", "/баланс"}:
        await message.answer(f"🪙 Баланс: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}\n⭐ Статус: {get_premium_status_text(user_id)}\n🎁 Дэйлик: {'готов' if db.can_claim_daily(user_id, ECONOMY_SCOPE_ID) else 'получен'}\n\nКоманды: /daily, /shop, /leaderboard, /coinflip, /slots, /dice")
        return True
    if command in {"/daily", "/дэйлик", "/ежедневно"}:
        if not db.can_claim_daily(user_id, ECONOMY_SCOPE_ID):
            await message.answer(f"⏳ Дэйлик уже получен. Следующая награда после {datetime.fromtimestamp(db.get_daily_streak_reset(user_id, ECONOMY_SCOPE_ID) + 86400):%d.%m %H:%M}.")
            return True
        reward = VIP_DAILY_REWARD if db.is_premium(user_id) else DAILY_REWARD
        await message.answer(f"🎁 Ты получил {format_coins(reward)}.\nТекущий баланс: {format_coins(db.claim_daily(user_id, reward, ECONOMY_SCOPE_ID))}")
        return True
    if command in {"/shop", "/store", "/магазин"}:
        await message.answer("🛒 Магазин монет:\n" + "\n".join([f"{i}) {v['title']} - {format_coins(v['price'])}" for i, v in enumerate(SHOP_ITEMS.values(), 1)]) + "\n\nПокупка: /buy vip 30\nИгры: /coinflip heads 100, /slots 100, /dice 50")
        return True
    if command in {"/leaderboard", "/top", "/топ"}:
        rows = db.get_top_balances(10, ECONOMY_SCOPE_ID)
        await message.answer("🏆 Топ монет:\n" + "\n".join([f"{i}. {await mention_user(int(r[0]), message.peer_id if message.peer_id >= 2000000000 else None)} — {format_coins(int(r[1]))}" for i, r in enumerate(rows, 1)]) if rows else "Пока нет игроков.")
        return True
    if command == "/buy" and args:
        if args[0].lower() in {"vip", "premium"}:
            days = parse_positive_int(args[1]) if len(args) > 1 else 30
            if days not in {30, 90, 365}: return (await message.answer("Только VIP на 30, 90 или 365 дней."), True)[1]
            sku = f"vip_{days}"
            if not db.spend_balance(user_id, SHOP_ITEMS[sku]["price"], ECONOMY_SCOPE_ID): return (await message.answer(f"Не хватает монет. Нужно {format_coins(SHOP_ITEMS[sku]['price'])}."), True)[1]
            await message.answer(f"✅ Успешно: {SHOP_ITEMS[sku]['title']}\nОстаток: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}\nVIP до {datetime.fromtimestamp(db.extend_premium(user_id, days, OWNER_ID, f'purchase:{sku}')):%d.%m.%Y %H:%M}.")
            return True
        return (await message.answer("Неизвестный товар."), True)[1]
    if command in {"/coinflip", "/flip"} and len(args) >= 2:
        bet = parse_positive_int(args[1])
        if not bet or db.get_balance(user_id, ECONOMY_SCOPE_ID) < bet: return (await message.answer("Ставка должна быть положительной и не больше баланса."), True)[1]
        chosen = "heads" if args[0].lower() in {"орел", "орёл", "heads"} else "tails" if args[0].lower() in {"решка", "tails"} else None
        if not chosen: return (await message.answer("Выбери: heads/tails или орёл/решка."), True)[1]
        db.spend_balance(user_id, bet, ECONOMY_SCOPE_ID)
        result = random.choice(["heads", "tails"])
        db.add_balance(user_id, bet * 2 if result == chosen else 0, ECONOMY_SCOPE_ID)
        await message.answer(f"🪙 Выпало: {result}\n{'🎉 Выиграл ' + format_coins(bet) if result == chosen else '❌ Проиграл ' + format_coins(bet)}.\nБаланс: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}")
        return True
    if command in {"/dice", "/roll"}:
        bet = parse_positive_int(args[0]) if args else 0
        if bet and db.get_balance(user_id, ECONOMY_SCOPE_ID) < bet: return (await message.answer("Недостаточно монет."), True)[1]
        if bet: db.spend_balance(user_id, bet, ECONOMY_SCOPE_ID)
        roll = random.randint(1, 6)
        payout = bet * 6 if roll == 6 and bet else bet * 2 if roll in {4, 5} and bet else 0
        if payout: db.add_balance(user_id, payout, ECONOMY_SCOPE_ID)
        await message.answer(f"🎲 Выпало: {roll}" + (f"\n{'✅ Выиграл ' + format_coins(payout - bet) if payout else '❌ Проиграна ставка ' + format_coins(bet)}\nБаланс: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}" if bet else ""))
        return True
    if command in {"/slots", "/slot"} and args:
        bet = parse_positive_int(args[0])
        if not bet or db.get_balance(user_id, ECONOMY_SCOPE_ID) < bet: return (await message.answer("Недостаточно монет или неверная ставка."), True)[1]
        db.spend_balance(user_id, bet, ECONOMY_SCOPE_ID)
        a, b, c = random.choice(GAME_EMOJIS), random.choice(GAME_EMOJIS), random.choice(GAME_EMOJIS)
        payout = calc_slots_payout(bet, a, b, c)
        if payout: db.add_balance(user_id, payout, ECONOMY_SCOPE_ID)
        await message.answer(f"🎰 {a} | {b} | {c}\n{'🎉 Выигрыш ' + format_coins(payout - bet) if payout else '❌ Ставка проиграна'}\nБаланс: {format_coins(db.get_balance(user_id, ECONOMY_SCOPE_ID))}")
        return True
    if command == "/givecoins" and len(args) >= 2 and is_owner(user_id):
        target, amount = await get_target_id(args[0], reply), parse_positive_int(args[1])
        if target and amount: await message.answer(f"✅ Начислено {format_coins(amount)} пользователю {await mention_user(target, message.peer_id)}." if db.add_balance(target, amount, ECONOMY_SCOPE_ID) else "")
        return True
    if command == "/setvip" and len(args) >= 2 and is_owner(user_id):
        target, days = await get_target_id(args[0], reply), parse_positive_int(args[1])
        if target and days: await message.answer(f"✅ VIP выдан {await mention_user(target, message.peer_id)} до {datetime.fromtimestamp(db.extend_premium(target, days, user_id, 'owner_grant')):%d.%m.%Y %H:%M}.")
        return True
    return False

async def get_user_name(user_id):
    try:
        users = await api.users.get(user_ids=[str(user_id)])
        if users: return f"{users[0].first_name} {users[0].last_name}"
    except: pass
    return f"id{user_id}"

async def get_display_name(user_id, chat_id):
    nickname = db.get_nickname(user_id, chat_id)
    return f"«{nickname}»" if nickname else await get_user_name(user_id)

def sanitize_vk_link_label(value): return str(value or "Пользователь").strip().replace("[", "(").replace("]", ")").replace("|", " ") or "Пользователь"

async def mention_user(user_id, chat_id=None):
    if user_id is None: return "Неизвестно"
    display_name = db.get_nickname(user_id, chat_id) if chat_id else None
    if not display_name: display_name = await get_user_name(user_id)
    display_name = sanitize_vk_link_label(display_name)
    return f"[id{int(user_id)}|{display_name}]" if int(user_id) > 0 else f"[club{abs(int(user_id))}|{display_name}]"

async def staff_display_user(user_id, chat_id, mode):
    return f"[id{int(user_id)}|{sanitize_vk_link_label(db.get_nickname(user_id, chat_id))}]" if mode == "nick" and db.get_nickname(user_id, chat_id) else await mention_user(user_id, None)

async def build_staff_text(chat_id, mode="nick"):
    grouped = {(100, get_owner_role_name()): [OWNER_ID]}
    best_by_user = {}
    for staff_chat_id in (db.get_linked_chats() or [chat_id]):
        for uid, role_key in db.get_all_user_roles(staff_chat_id):
            _, role_data = resolve_role_for_chat(staff_chat_id, role_key)
            if role_data and role_data.get("level", 0) >= 20:
                if uid not in best_by_user or role_data["level"] > best_by_user[uid][0]: best_by_user[uid] = (role_data["level"], role_data["name"])
    for uid, (level, role_name) in best_by_user.items():
        if uid != OWNER_ID: grouped.setdefault((level, role_name), []).append(uid)
    lines = ["👥 Администрация:"]
    for (level, role_name), user_ids in sorted(grouped.items(), key=lambda item: item[0][0], reverse=True):
        lines.extend(["", f"🔥 {role_name}:" if level >= 100 else f"{role_name}:"])
        for uid in sorted(set(user_ids)): lines.append(f"— {await staff_display_user(uid, chat_id, mode)}")
    return "\n".join(lines) if len(lines) > 1 else "👥 Администрация:\nНет назначенных ролей."

def staff_keyboard(mode): return Keyboard(inline=True).add(Text("имена" if mode == "nick" else "ники", payload={"command": "staff_toggle", "mode": "name" if mode == "nick" else "nick"}))
def format_ban_until_timestamp(banned_until): return datetime.fromtimestamp(int(banned_until)).strftime("%d.%m.%Y %H:%M") if banned_until else "Навсегда"

async def get_global_chat_peer_ids(): return [p for p in (db.get_linked_chats() or await get_all_chat_peer_ids()) if p >= 2000000000]

async def apply_global_action(action, target_id, actor_id, *args):
    results = []
    for peer_id in await get_global_chat_peer_ids():
        try:
            if action in ("gban", "gmute", "gkick") and not (await can_manage_target(peer_id, actor_id, target_id, f"глобальный {action[1:]}"))[0]: continue
            if action == "gban": db.ban_user(target_id, peer_id, args[0], actor_id, duration_seconds=args[1]); await kick_user(peer_id, target_id)
            elif action == "gmute": db.mute_user(target_id, peer_id, args[1] / 60, actor_id, args[0])
            elif action == "gkick": await kick_user(peer_id, target_id)
            elif action == "grole": db.set_role(target_id, peer_id, args[0], actor_id)
            elif action == "grmrole": db.remove_role(target_id, peer_id)
            results.append(peer_id)
        except Exception as exc: log.error(f"Error global {action} in {peer_id}: {exc}")
    return results

async def kick_user(chat_id, user_id):
    try:
        await api.messages.remove_chat_user(chat_id=chat_id - 2000000000, member_id=user_id)
        if not is_owner(user_id): db.remove_role(user_id, chat_id)
        return True
    except Exception:
        try:
            await api.messages.remove_chat_user(chat_id=chat_id - 2000000000, user_id=abs(user_id) if user_id < 0 else user_id)
            if not is_owner(user_id): db.remove_role(user_id, chat_id)
            return True
        except Exception: return False

async def get_all_chat_peer_ids():
    if known := db.get_known_chats(): return sorted(set(known))
    discovered, offset = [], 0
    while True:
        res = await api.messages.get_conversations(offset=offset, count=200)
        items = getattr(res, "items", None) or []
        for i in items:
            p = getattr(getattr(i, "conversation", None), "peer", None)
            if p and (getattr(getattr(p, "type", None), "value", getattr(p, "type", None)) == "chat" or getattr(p, "id", 0) >= 2000000000): discovered.append(getattr(p, "id"))
        if len(items) < 200: break
        offset += 200
    return sorted(set(discovered))

async def get_chat_titles(peer_ids):
    titles = {}
    for i in range(0, len(peer_ids), 100):
        try:
            for item in getattr(await api.messages.get_conversations_by_id(peer_ids=peer_ids[i:i+100]), "items", None) or []:
                if p := getattr(item, "peer", None): titles[getattr(p, "id")] = getattr(getattr(item, "chat_settings", None), "title", None) or f"Чат {getattr(p, 'id')}"
        except: pass
    return {pid: titles.get(pid, f"Чат {pid}") for pid in peer_ids}

def build_groups_keyboard(peer_ids, titles, page=1):
    max_page = max(1, (len(peer_ids) + GROUPS_PAGE_SIZE - 1) // GROUPS_PAGE_SIZE)
    page = max(1, min(page, max_page))
    keyboard, current_ids = Keyboard(inline=True), peer_ids[(page - 1) * GROUPS_PAGE_SIZE:page * GROUPS_PAGE_SIZE]
    for idx, peer_id in enumerate(current_ids):
        t = titles.get(peer_id, f"Чат {peer_id}")
        keyboard.add(Text(t if len(t) <= 36 else t[:33] + "...", payload={"command": "owner_select_group", "chat_id": peer_id}))
        if idx % 2 == 1 and idx != len(current_ids) - 1: keyboard.row()
    if max_page > 1:
        keyboard.row()
        if page > 1: keyboard.add(Text("◀️ Назад", payload={"command": "owner_groups_page", "page": page - 1}))
        if page < max_page: keyboard.add(Text("▶️ Вперед", payload={"command": "owner_groups_page", "page": page + 1}))
    keyboard.row().add(Text("❌ Сбросить выбор", payload={"command": "owner_clear_group"}))
    return keyboard, page, max_page

@bot.labeler.private_message()
async def handle_private(message: Message):
    text, user_id = message.text or "", message.from_id
    now = time.time()
    _cleanup_processed_messages(now)
    msg_id = getattr(message, "conversation_message_id", getattr(message, "id", None))
    if msg_id is not None and not _mark_processed_message(msg_id): return
    if await handle_economy_command(message, text, user_id): return

    if is_owner(user_id) and message.payload:
        try:
            payload = json.loads(message.payload) if isinstance(message.payload, str) else message.payload
            if isinstance(payload, dict):
                cmd = payload.get("command")
                if cmd == "owner_select_group" and isinstance(payload.get("chat_id"), int):
                    owner_selected_group[user_id] = payload["chat_id"]
                    await message.answer(f"✅ Выбрана группа: {(await get_chat_titles([payload['chat_id']])).get(payload['chat_id'], 'Чат')}\nТеперь отправьте текст без команды.")
                elif cmd == "owner_groups_page":
                    peer_ids = await get_all_chat_peer_ids()
                    if peer_ids:
                        keyboard, p, m = build_groups_keyboard(peer_ids, await get_chat_titles(peer_ids), payload.get("page", 1))
                        await message.answer(f"📋 Группы: {len(peer_ids)}\n📄 Стр: {p}/{m}\n🎯 Выбрано: {owner_selected_group.get(user_id, 'нет')}", keyboard=keyboard.get_json())
                elif cmd == "owner_clear_group":
                    owner_selected_group.pop(user_id, None); await message.answer("✅ Сброшено.")
        except: pass

    if text == "/start": await message.answer(f"🤖 VK Чат Менеджер Бот\n\n👤 Владелец: id{OWNER_ID}\n\n📝 Команды:\n/start - Начать\n/help - Помощь\n/profile - Профиль\n/groups - Выбрать группу для отправки\n/coins - баланс монет\n/daily - ежедневная награда\n/shop - магазин VIP")
    elif text == "/help": await message.answer("📚 Помощь\n\n🔹 Основные:\n/start - Начать\n/help - Помощь\n/profile - Профиль\n/groups - Список групп/чатов бота\n\n👑 Для владельца:\n1) Выберите группу через /groups\n2) Отправьте текст без /команды\n/linkchat list/add/remove/all/clear - объединение чатов\n\n🎮 Игры:\n/coins, /daily, /shop, /coinflip, /slots, /dice, /leaderboard\n/calc [пример] - калькулятор\n/quote, /joke - фан")
    elif text == "/profile": await message.answer(f"👤 Профиль\n\n📝 Имя: {await mention_user(user_id, None)}\n🆔 ID: {user_id}\n⭐ Статус: {'👑 Владелец' if is_owner(user_id) else '👤 Пользователь'}")
    elif text.startswith("/groups") and is_owner(user_id):
        peer_ids = await get_all_chat_peer_ids()
        if not peer_ids: return await message.answer("❌ Не найдено групп.")
        keyboard, p, m = build_groups_keyboard(peer_ids, await get_chat_titles(peer_ids), int(text.split()[1]) if len(text.split())>1 and text.split()[1].isdigit() else 1)
        await message.answer(f"📋 Группы: {len(peer_ids)}\n📄 Страница: {p}/{m}\n👇 Выберите кнопку:", keyboard=keyboard.get_json())
    elif text.startswith("/linkchat") and is_owner(user_id):
        action = text.split()[1].lower() if len(text.split()) > 1 else "list"
        if action == "list": await message.answer("🔗 Объединенные чаты:\n" + "\n".join([f"{c} — {(await get_chat_titles([c])).get(c)}" for c in db.get_linked_chats()]))
        elif action == "add": db.link_chat(owner_selected_group.get(user_id), user_id) if owner_selected_group.get(user_id) else None; await message.answer("✅ Добавлено.")
        elif action == "remove": db.unlink_chat(owner_selected_group.get(user_id)) if owner_selected_group.get(user_id) else None; await message.answer("✅ Удалено.")
        elif action == "all": [db.link_chat(c, user_id) for c in await get_all_chat_peer_ids() if c >= 2000000000]; await message.answer("✅ Добавлены все.")
        elif action == "clear": db.clear_linked_chats(); await message.answer("✅ Очищено.")
    elif is_owner(user_id) and text and not text.startswith("/"):
        if tgt := owner_selected_group.get(user_id):
            try: await api.messages.send(peer_id=tgt, message=f"{text}\n\nP.S Данил Михайлов", random_id=random.randint(0, 2**31)); await message.answer("✅ Отправлено.")
            except Exception as e: await message.answer(f"❌ Ошибка: {e}")
        else: await message.answer("❌ Сначала выберите группу: /groups")

@bot.labeler.chat_message()
async def handle_chat(message: Message):
    text, chat_id, user_id = message.text or "", message.peer_id, message.from_id
    if BOT_USER_ID and user_id == BOT_USER_ID: return
    reply = message.reply_message
    db.touch_chat(chat_id)

    if await handle_economy_command(message, text, user_id): return

    now = time.time()
    _cleanup_processed_messages(now)
    msg_id = getattr(message, "conversation_message_id", getattr(message, "id", None))
    if msg_id and not _mark_processed_message(msg_id): return
    remember_chat_message(chat_id, getattr(message, "conversation_message_id", None))
    if user_id > 0 and not message.action: db.increment_user_stat(chat_id, user_id)

    rus_to_eng = {
        "/бан": "/ban", "/разбан": "/unban", "/кик": "/kick", "/мут": "/mute", "/размут": "/unmute", "/варн": "/warn", "/снятьварн": "/unwarn", "/очиститьварны": "/clearwarns",
        "/сетроль": "/setrole", "/гроль": "/grole", "/нроль": "/newrole", "/удалитьроль": "/delrole", "/права": "/recrate", "/овнерроль": "/ownername", "/снятьроль": "/removerole",
        "/гетбан": "/getban", "/гбан": "/gban", "/гмут": "/gmute", "/гкик": "/gkick", "/ник": "/nick", "/снятьник": "/removenick", "/правила": "/rules", "/приветствие": "/welcome",
        "/заметки": "/notes", "/заметка": "/note", "/сохранить": "/save", "/удалить": "/delete", "/дел": "/del", "/профиль": "/profile", "/помощь": "/help", "/старт": "/start",
        "/персонал": "/staff", "/роли": "/roles", "/варны": "/warns", "/закрепить": "/pin", "/открепить": "/unpin", "/пригласить": "/invite", "/репорт": "/report", "/зов": "/zov",
        "/масскик": "/masskick", "/антиспам": "/antispam", "/антимат": "/antimat", "/линкчат": "/linkchat", "/стата": "/stat", "/топчат": "/topchat", "/чатинфо": "/chatinfo",
        "/фильтр": "/filter", "/чистка": "/clean", "/ии": "/ai", "/цитата": "/quote", "/шутка": "/joke", "/монетка": "/coin", "/кубик": "/dice", "/выбери": "/choose",
        "/напомни": "/remind", "/антикапс": "/anticaps", "/антиссылка": "/antilink", "/кальк": "/calc"
    }
    
    for rus, eng in rus_to_eng.items():
        if text.lower().startswith(rus):
            rest = text[len(rus):]
            if not rest or rest[0] in " \n\t": text = eng + (" " + rest.lstrip() if rest else ""); break

    # Filters and auto-moderation
    if not message.action and not is_antispam_exempt(chat_id, user_id):
        # 1. Custom forbidden words
        if forbidden_hit := next((fw for fw in db.get_forbidden_words(chat_id) if fw in re.sub(r"\s+", " ", text.lower())), None):
            try: await api.messages.delete(peer_id=chat_id, conversation_message_ids=[message.conversation_message_id], delete_for_all=True)
            except: pass
            return await message.answer(f"🚫 Фильтр чата\nПользователь: {await mention_user(user_id, chat_id)}\nСовпадение: {forbidden_hit}\nСообщение удалено.")
        
        # 2. Antilink
        if db.is_antilink_enabled(chat_id) and re.search(r"(https?://|www\.|[a-zA-Z0-9-]+\.(ru|com|net|org|me|io|gg|su|рф))", text, re.I):
            try: await api.messages.delete(peer_id=chat_id, conversation_message_ids=[message.conversation_message_id], delete_for_all=True)
            except: pass
            return await message.answer(f"🚫 {await mention_user(user_id, chat_id)}, отправка ссылок в этом чате запрещена!")
        
        # 3. Anticaps
        if db.is_anticaps_enabled(chat_id):
            letters = [c for c in text if c.isalpha()]
            if len(letters) > 10 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
                try: await api.messages.delete(peer_id=chat_id, conversation_message_ids=[message.conversation_message_id], delete_for_all=True)
                except: pass
                return await message.answer(f"🚫 {await mention_user(user_id, chat_id)}, выключи CAPS LOCK!")

        # 4. Antimat
        if db.is_antimat_enabled(chat_id) and contains_profanity(text):
            if now - antimat_notify_cooldown.get(chat_id, 0) > ANTIMAT_NOTIFY_COOLDOWN_SECONDS:
                antimat_notify_cooldown[chat_id] = now
                await message.answer(f"⚠️ {await mention_user(user_id, chat_id)}, матерные выражения запрещены!")
            try: await api.messages.delete(peer_id=chat_id, conversation_message_ids=[message.conversation_message_id], delete_for_all=True)
            except: pass
            return

        # 5. Antispam
        if db.is_antispam_enabled(chat_id):
            spam_detected, spam_reason = check_antispam(chat_id, user_id, text, len(message.attachments) if message.attachments else 0)
            if spam_detected:
                db.mute_user(user_id, chat_id, ANTISPAM_AUTO_MUTE_MINUTES, OWNER_ID, f"Авто-мут: {spam_reason}")
                clear_antispam_state(chat_id, user_id)
                try: await api.messages.delete(peer_id=chat_id, conversation_message_ids=[message.conversation_message_id], delete_for_all=True)
                except: pass
                return await message.answer(f"🛡 Антиспам: {await mention_user(user_id, chat_id)} получил мут на {ANTISPAM_AUTO_MUTE_MINUTES} минут.\nПричина: {spam_reason}")

    # Process action (invites/kicks)
    if message.action:
        act = message.action
        left_id = getattr(act, 'member_id', None)
        if left_id and left_id > 0 and not is_owner(left_id) and any(m in (act.type.value.lower() if act.type else "") for m in ("kick", "leave", "remove")): db.remove_role(left_id, chat_id)
        if act.type and "invite" in act.type.value.lower() and getattr(act, 'member_id', None) and getattr(act, 'member_id') > 0:
            if db.is_banned(act.member_id, chat_id):
                await kick_user(chat_id, act.member_id)
                return await message.answer(f"🚫 Забаненный пользователь!\n{await mention_user(act.member_id, chat_id)}\nПричина: {(db.get_ban_info(act.member_id, chat_id) or ['Не указана'])[0]}\nАвто-кик.")

    # Punishments check
    if db.is_banned(user_id, chat_id) and not is_owner(user_id):
        if await kick_user(chat_id, user_id): await message.answer(f"🚫 {await mention_user(user_id, chat_id)} в бане! Кикнут.")
        return
    if db.is_muted(user_id, chat_id) and not is_owner(user_id):
        mute_info = db.get_mute_info(user_id, chat_id)
        if mute_info:
            try: await api.messages.delete(peer_id=chat_id, conversation_message_ids=[message.conversation_message_id], delete_for_all=True)
            except: pass
            rem = max(0, mute_info[0] - int(time.time()))
            await message.answer(f"🔇 Вы в муте!\nОсталось: {rem//60}м {rem%60}с\nПричина: {mute_info[2] or 'Не указана'}")
        return

    # Forward media to owner
    if user_id != OWNER_ID and (message.attachments or re.findall(r'https?://[^\s]+', text)):
        try: await api.messages.send(peer_id=OWNER_ID, message=f"📢 Новое медиа/ссылка в чате {chat_id}\n👤 От: {await mention_user(user_id, chat_id)}\n💬 Текст: {text[:500]}", random_id=random.randint(0, 2**31))
        except: pass

    # COMMANDS
    if text == "/start": return await message.answer("🤖 VK Чат Менеджер Бот\n\n/help - Помощь\n/profile - Профиль\n/rules - Правила")
    
    # Games & Fun (from bot2.py)
    elif text.startswith("/calc"): return await message.answer(safe_calc(text[5:]))
    elif text.startswith("/quote"): return await message.answer(f"💬 {random.choice(QUOTES)}")
    elif text.startswith("/joke"): return await message.answer(f"😄 {random.choice(JOKES)}")
    elif text.startswith("/coin"): return await message.answer(f"🪙 Выпало: {random.choice(['Орёл', 'Решка'])}")
    elif text.startswith("/dice"): return await message.answer(f"🎲 Выпало: {random.randint(1, 6)}")
    elif text.startswith("/random"):
        p = text.split()
        return await message.answer(f"🎲 Число: {random.randint(min(int(p[1]), int(p[2])), max(int(p[1]), int(p[2])))}" if len(p)>=3 and p[1].isdigit() and p[2].isdigit() else "❌ Пример: /random 1 100")
    elif text.startswith("/choose"):
        opts = [x.strip() for x in text[7:].split("|") if x.strip()]
        return await message.answer(f"🤔 Я выбираю: {random.choice(opts)}" if opts else "❌ Используй: /choose вариант 1 | вариант 2")
    elif text.startswith("/remind"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3: return await message.answer("❌ Пример: /remind 10m текст")
        sec = parse_mute_duration(parts[1])
        if not sec:
            if parts[1].isdigit(): sec = (int(parts[1]) * 60, f"{parts[1]} мин.")
            else: return await message.answer("❌ Неверный формат (10m, 1h).")
        db.add_reminder(user_id, chat_id, parts[2], int(time.time()) + sec[0])
        return await message.answer(f"⏰ Напомню через {sec[1]}.")

    elif text.startswith("/ai"):
        prompt = text[3:].strip() or (reply.text.strip() if reply and getattr(reply, "text", None) else "")
        if not prompt: return await message.answer("❌ Использование: /ai [вопрос]")
        if time.time() - ai_last_used.get((chat_id, user_id), 0) < AI_COOLDOWN_SECONDS and not is_owner(user_id): return await message.answer(f"⏳ Жди {AI_COOLDOWN_SECONDS - int(time.time() - ai_last_used.get((chat_id, user_id), 0))} сек.")
        ai_last_used[(chat_id, user_id)] = time.time()
        try: await message.answer(f"🤖 | AI\n━━━━━━━━━━━━━━━━━━━━\n\n{await ask_cerebras(prompt, f'user_id={user_id}')}")
        except Exception as e: await message.answer(f"❌ AI ошибка: {str(e)[:180]}")

    elif text.startswith("/stat"):
        tgt = await get_target_id(text, reply) or user_id
        stat = db.get_user_stat(chat_id, tgt)
        return await message.answer(f"📊 STATS\n👤 {await mention_user(tgt, chat_id)}\n💬 Сообщений: {stat[0] if stat else 0}\n🕒 Активен: {datetime.fromtimestamp(stat[2]).strftime('%d.%m.%Y %H:%M') if stat and stat[2] else 'нет данных'}")
    
    elif text.startswith("/topchat"):
        rows = db.get_top_chat_stats(chat_id, 10)
        return await message.answer("🏆 TOP CHAT\n" + "\n".join([f"{i}. {await mention_user(int(r[0]), chat_id)} — {r[1]}" for i, r in enumerate(rows, 1)]) if rows else "Нет данных.")
    
    elif text.startswith("/chatinfo"):
        try: item = (await api.messages.get_conversations_by_id(peer_ids=[chat_id])).items[0]; title = item.chat_settings.title
        except: title = f"Чат {chat_id}"
        try: mems = (await api.messages.get_conversation_members(peer_id=chat_id)).items; cnt = len(mems); adm = sum(1 for m in mems if getattr(m, "is_admin", False))
        except: cnt = adm = 0
        s = db.get_chat_settings(chat_id)
        return await message.answer(f"ℹ️ CHAT INFO\n💬 Название: {title}\n🆔 ID: {chat_id}\n👥 Участников: {cnt}\n🛡 Админов VK: {adm}\n🔗 В объединении: {'да' if db.is_chat_linked(chat_id) else 'нет'}\n🛡 Антиспам: {'вкл' if s['antispam_enabled'] else 'выкл'}\n🤬 Антимат: {'вкл' if s['antimat_enabled'] else 'выкл'}\n🔗 Антиссылка: {'вкл' if s['antilink_enabled'] else 'выкл'}\n🔠 Антикапс: {'вкл' if s['anticaps_enabled'] else 'выкл'}")

    elif text.startswith("/filter"):
        if not await has_permission(chat_id, user_id, "can_warn"): return await message.answer("❌ Недостаточно прав.")
        p = text.split(maxsplit=2)
        if len(p) < 2: return await message.answer("/filter add|del|list [слово]")
        if p[1].lower() in {"list", "список"}: return await message.answer("🚫 Фильтры:\n" + "\n".join(db.get_forbidden_words(chat_id)) if db.get_forbidden_words(chat_id) else "🚫 Пусто.")
        if len(p) < 3: return await message.answer("❌ Укажите слово.")
        if p[1].lower() in {"add", "+"}: db.add_forbidden_word(chat_id, p[2].lower(), user_id); return await message.answer(f"✅ Добавлено: {p[2]}")
        if p[1].lower() in {"del", "-"}: db.remove_forbidden_word(chat_id, p[2].lower()); return await message.answer(f"✅ Удалено: {p[2]}")

    elif text.startswith("/clean"):
        if not await has_permission(chat_id, user_id, "can_warn"): return await message.answer("❌ Недостаточно прав.")
        ids = get_recent_chat_message_ids(chat_id, parse_positive_int(text.split()[1]) if len(text.split())>1 else 10)
        if ids:
            try: await api.messages.delete(peer_id=chat_id, conversation_message_ids=ids, delete_for_all=True); return await message.answer(f"🧹 Удалено {len(ids)}.")
            except Exception as e: return await message.answer(f"❌ Ошибка: {e}")
        return await message.answer("❌ Нет сообщений для очистки.")

    elif text.startswith("/q"):
        if await kick_user(chat_id, user_id): return await message.answer(f"👋 Пока, {await mention_user(user_id, chat_id)}!")

    elif text == "/help":
        role = await get_user_role(chat_id, user_id)
        ht = "📚 Команды\n\n👤 Пользователь:\n/start, /help, /profile, /ai, /stat, /topchat, /chatinfo, /rules, /notes, /note, /staff, /report\n🎮 Фан: /calc, /quote, /joke, /coin, /dice, /random, /choose, /remind\n\n"
        if role["level"] >= 20 or is_owner(user_id): ht += "🟡 Helper:\n/warn, /unwarn, /clearwarns, /warns, /mute, /unmute, /setwelcome, /setrules, /save, /delete, /del, /pin, /unpin, /invite, /clean\n/antispam, /antimat, /antilink, /anticaps on|off\n\n"
        if role["level"] >= 50 or is_owner(user_id): ht += "🟠 Moderator:\n/kick, /zov\n\n"
        if role["level"] >= 80 or is_owner(user_id): ht += "🔴 Admin:\n/ban, /unban, /getban, /gban, /gmute, /gkick, /nick, /removenick, /masskick\n\n"
        if is_owner(user_id): ht += "👑 Владелец:\n/roles, /newrole, /delrole, /recrate, /ownername, /setrole, /grole, /removerole\n"
        return await message.answer(ht.strip())

    elif text.startswith("/profile"):
        tgt = await get_target_id(text, reply) or user_id
        pi = db.get_premium_info(tgt)
        return await message.answer(f"🔍 Профиль {await mention_user(tgt, chat_id)}\n\n🗣 Роль: {(await get_user_role(chat_id, tgt))['name']}\n⚠ Варнов: {db.get_warns(tgt, chat_id)}/{MAX_WARNS}\n📄 Ник: {db.get_nickname(tgt, chat_id) or 'нет'}\n💎 VIP: {'Да' if db.is_premium(tgt) else 'Нет'}")

    elif text == "/roles": return await message.answer(format_role_list(chat_id))
    elif text == "/rules": return await message.answer(f"📜 Правила\n\n{db.get_chat_settings(chat_id)['rules'] or 'Не установлены. /setrules [текст]'}")
    elif text.startswith("/setrules"):
        if await has_permission(chat_id, user_id, "can_warn"): db.set_rules(chat_id, text[10:]); return await message.answer("✅ Установлены!")

    elif text.startswith("/antispam") or text.startswith("/antimat") or text.startswith("/antilink") or text.startswith("/anticaps"):
        if not await has_permission(chat_id, user_id, "can_warn"): return await message.answer("❌ Нет прав.")
        cmd = text.split()[0][1:]
        p = text.split(maxsplit=1)
        if len(p) < 2: return await message.answer(f"🛡 {cmd} статус. Используйте: /{cmd} on|off")
        val = p[1].lower() in {"on", "1", "вкл", "yes"}
        if cmd == "antispam": db.set_antispam_enabled(chat_id, val)
        elif cmd == "antimat": db.set_antimat_enabled(chat_id, val)
        elif cmd == "antilink": db.set_antilink_enabled(chat_id, val)
        elif cmd == "anticaps": db.set_anticaps_enabled(chat_id, val)
        return await message.answer(f"✅ {cmd} {'включен' if val else 'выключен'}.")

    elif text == "/staff": return await message.answer(await build_staff_text(chat_id, "nick"), keyboard=staff_keyboard("nick").get_json())
    
    elif text.startswith("/warns"): return await message.answer(f"⚠️ Варны: {db.get_warns(await get_target_id(text, reply) or user_id, chat_id)}/{MAX_WARNS}")
    elif text.startswith("/warn "):
        if not await has_permission(chat_id, user_id, "can_warn"): return
        tgt = await get_target_id(text, reply)
        if not tgt or tgt == user_id or is_owner(tgt) or not (await can_manage_target(chat_id, user_id, tgt, "варн"))[0]: return await message.answer("❌ Ошибка прав/цели.")
        w = db.add_warn(tgt, chat_id, text.split(maxsplit=2)[2] if len(text.split())>2 else "Не указано", user_id)
        if w >= MAX_WARNS:
            db.clear_warns(tgt, chat_id); db.ban_user(tgt, chat_id, f"Макс варнов", user_id); await kick_user(chat_id, tgt)
            return await message.answer(f"⚠️ Максимум варнов! {await mention_user(tgt, chat_id)} забанен.")
        return await message.answer(f"⚠️ Варн выдан. Всего: {w}/{MAX_WARNS}")
    elif text.startswith("/unwarn"):
        if await has_permission(chat_id, user_id, "can_warn") and (tgt := await get_target_id(text, reply)):
            if db.remove_one_warn(tgt, chat_id): return await message.answer(f"✅ Варн снят. Осталось: {db.get_warns(tgt, chat_id)}/{MAX_WARNS}")
    elif text.startswith("/clearwarns"):
        if await has_permission(chat_id, user_id, "can_warn") and (tgt := await get_target_id(text, reply)):
            db.clear_warns(tgt, chat_id); return await message.answer("✅ Варны очищены.")

    elif text.startswith("/kick"):
        if not await has_permission(chat_id, user_id, "can_kick"): return
        tgt = await get_target_id(text, reply)
        if tgt and tgt != user_id and not is_owner(tgt) and (await can_manage_target(chat_id, user_id, tgt, "кик"))[0]:
            if await kick_user(chat_id, tgt): return await message.answer(f"👢 {await mention_user(tgt, chat_id)} кикнут.")

    elif text.startswith("/gkick"):
        if not await has_permission(chat_id, user_id, "can_gkick"): return
        tgt = await get_target_id(text, reply)
        if tgt and tgt != user_id and not is_owner(tgt):
            res = await apply_global_action("gkick", tgt, user_id)
            return await message.answer(f"👢 Глобальный кик в {len(res)} чатах.")

    elif text.startswith("/ban"):
        if not await has_permission(chat_id, user_id, "can_ban"): return
        tgt = await get_target_id(text, reply)
        if tgt and tgt != user_id and not is_owner(tgt) and not db.is_banned(tgt, chat_id) and (await can_manage_target(chat_id, user_id, tgt, "бан"))[0]:
            p = text.split(); ds = next((parse_ban_duration(x) for x in p[1:] if parse_ban_duration(x)), None)
            rs = " ".join([x for x in p[1:] if not parse_ban_duration(x) and not x.startswith("@")]) or "Нет"
            db.ban_user(tgt, chat_id, rs, user_id, duration_seconds=ds["seconds"] if ds else None); await kick_user(chat_id, tgt)
            return await message.answer(f"🚫 {await mention_user(tgt, chat_id)} забанен. Срок: {ds['text'] if ds else 'Навсегда'}. Причина: {rs}")

    elif text.startswith("/gban"):
        if not await has_permission(chat_id, user_id, "can_gban"): return
        tgt = await get_target_id(text, reply)
        if tgt and tgt != user_id and not is_owner(tgt):
            p = text.split(); ds = next((parse_ban_duration(x) for x in p[1:] if parse_ban_duration(x)), None)
            res = await apply_global_action("gban", tgt, user_id, "Глобальный бан", ds["seconds"] if ds else None)
            return await message.answer(f"🚫 Глобальный бан в {len(res)} чатах.")

    elif text.startswith("/unban"):
        if await has_permission(chat_id, user_id, "can_ban") and (tgt := await get_target_id(text, reply)):
            db.unban_user(tgt, chat_id); return await message.answer("✅ Разбанен.")

    elif text.startswith("/mute"):
        if not await has_permission(chat_id, user_id, "can_mute"): return
        tgt = await get_target_id(text, reply)
        if tgt and tgt != user_id and not is_owner(tgt) and (await can_manage_target(chat_id, user_id, tgt, "мут"))[0]:
            p = text.split(); ds = next((parse_mute_duration(x) for x in p[1:] if parse_mute_duration(x)), (600, "10 мин."))
            db.mute_user(tgt, chat_id, ds[0]/60, user_id, "Мут"); return await message.answer(f"🔇 Мут на {ds[1]}")

    elif text.startswith("/gmute"):
        if not await has_permission(chat_id, user_id, "can_gmute"): return
        tgt = await get_target_id(text, reply)
        if tgt and tgt != user_id and not is_owner(tgt):
            p = text.split(); ds = next((parse_mute_duration(x) for x in p[1:] if parse_mute_duration(x)), (600, "10 мин."))
            res = await apply_global_action("gmute", tgt, user_id, "Глобальный мут", ds[0])
            return await message.answer(f"🔇 Глобальный мут в {len(res)} чатах.")

    elif text.startswith("/unmute"):
        if await has_permission(chat_id, user_id, "can_mute") and (tgt := await get_target_id(text, reply)):
            db.unmute_user(tgt, chat_id); return await message.answer("🔊 Размучен.")

    elif text.startswith("/setrole") and is_owner(user_id):
        tgt = await get_target_id(text, reply); rarg = extract_role_argument(text.split(), bool(reply))
        if tgt and rarg and tgt != OWNER_ID:
            rk, rd = resolve_role_for_chat(chat_id, rarg)
            if rd: db.set_role(tgt, chat_id, rk, user_id); return await message.answer(f"✅ Роль {rd['name']} выдана.")

    elif text.startswith("/newrole") and is_owner(user_id):
        p = text.split(maxsplit=2)
        if len(p) >= 3 and p[1].isdigit():
            db.create_priority_role(chat_id, p[2].strip(), int(p[1]), user_id)
            return await message.answer(f"✅ Роль {p[2]} (lvl {p[1]}) создана.")

    elif text.startswith("/delrole") and is_owner(user_id):
        rk, rd = resolve_role_for_chat(chat_id, text.split(maxsplit=1)[1].strip() if len(text.split())>1 else "")
        if rd and rk not in ROLES: db.delete_custom_role(chat_id, rk); return await message.answer("✅ Удалена.")

    elif text.startswith("/recrate") and is_owner(user_id):
        p = text.split()
        if len(p) >= 4 and (v := parse_bool_word(p[3])) is not None:
            if db.update_custom_role_permission(chat_id, p[1], p[2].lstrip("/").lower(), v): return await message.answer("✅ Права изменены.")

    elif text.startswith("/ownername") and is_owner(user_id):
        if len(text.split()) > 1: db.set_setting("owner_role_name", text.split(maxsplit=1)[1][:64]); return await message.answer("✅ Изменено.")

    elif text.startswith("/grole") and is_owner(user_id):
        tgt = await get_target_id(text, reply); rarg = extract_role_argument(text.split(), bool(reply))
        rk, rd = resolve_role_for_chat(chat_id, rarg)
        if tgt and rd and tgt != OWNER_ID:
            res = await apply_global_action("grole", tgt, user_id, rk)
            return await message.answer(f"✅ Глобальная роль выдана в {len(res)} чатах.")

    elif text.startswith("/removerole") and is_owner(user_id):
        if tgt := await get_target_id(text, reply): db.remove_role(tgt, chat_id); return await message.answer("✅ Снята.")

    elif text.startswith("/zov"):
        if (await get_user_role(chat_id, user_id))["level"] < 50: return
        if time.time() - zov_last_used.get(chat_id, 0) < ZOV_COOLDOWN_SECONDS and not is_owner(user_id): return
        zov_last_used[chat_id] = time.time()
        mems = "".join([f"[id{m.member_id}|&#8288;]" for m in (await api.messages.get_conversation_members(peer_id=chat_id)).items if m.member_id > 0][:ZOV_MAX_MENTIONS])
        return await message.answer(f"📢 {text.split(maxsplit=1)[1] if len(text.split())>1 else 'Сбор!'}\n\n{mems}")

    elif text.startswith("/report") and reply:
        rtext = f"📢 Жалоба из {chat_id}\nНарушитель: id{reply.from_id}\nОт: id{user_id}\nПричина: {text.split(maxsplit=1)[1] if len(text.split())>1 else 'нет'}"
        await api.messages.send(peer_id=OWNER_ID, message=rtext, random_id=random.randint(0, 2**31)); return await message.answer("✅ Отправлено.")

    elif text.startswith("/nick") and (await get_user_role(chat_id, user_id))["level"] >= 80:
        tgt = await get_target_id(text, reply); n = text.split(maxsplit=2)[2] if len(text.split())>2 else ""
        if tgt and n: db.set_nickname(tgt, chat_id, n[:30], user_id); return await message.answer("✅ Ник установлен.")

    elif text.startswith("/removenick") and (await get_user_role(chat_id, user_id))["level"] >= 80:
        if tgt := await get_target_id(text, reply): db.remove_nickname(tgt, chat_id); return await message.answer("✅ Удалён.")

    elif text.startswith("/setwelcome") and await has_permission(chat_id, user_id, "can_warn"):
        db.set_welcome(chat_id, text[12:]); return await message.answer("✅ Установлено.")
    elif text == "/welcome": return await message.answer(db.get_chat_settings(chat_id)['welcome'] or "Не установлено.")

    elif text == "/notes": return await message.answer("📝 Заметки:\n" + "\n".join(db.get_notes(chat_id)))
    elif text.startswith("/note "): return await message.answer(db.get_note(chat_id, text[6:].strip()) or "❌ Не найдена")
    elif text.startswith("/save ") and await has_permission(chat_id, user_id, "can_warn"):
        p = text.split(maxsplit=2)
        if len(p)>=3: db.save_note(chat_id, p[1], p[2], user_id); return await message.answer("✅ Сохранена.")
    elif text.startswith("/delete ") and await has_permission(chat_id, user_id, "can_warn"):
        db.delete_note(chat_id, text[8:]); return await message.answer("✅ Удалена.")

    elif text.startswith("/del") and reply and await has_permission(chat_id, user_id, "can_warn"):
        try: await api.messages.delete(peer_id=chat_id, conversation_message_ids=[reply.conversation_message_id], delete_for_all=True); return await message.answer("✅ Удалено.")
        except: return

    elif text.startswith("/pin") and reply and await has_permission(chat_id, user_id, "can_warn"):
        try: await api.messages.pin(peer_id=chat_id, conversation_message_id=reply.conversation_message_id); return await message.answer("📌 Закреплено.")
        except: return
    elif text == "/unpin" and await has_permission(chat_id, user_id, "can_warn"):
        try: await api.messages.unpin(peer_id=chat_id); return await message.answer("📌 Откреплено.")
        except: return

    elif text == "/masskick" and ((await get_user_role(chat_id, user_id))["level"] >= 80 or is_owner(user_id)):
        cnt = 0
        for m in (await api.messages.get_conversation_members(peer_id=chat_id)).items:
            if m.member_id > 0 and not getattr(m, "is_admin", False) and not is_owner(m.member_id) and not db.get_role(m.member_id, chat_id):
                if await kick_user(chat_id, m.member_id): cnt += 1
        return await message.answer(f"✅ Кикнуто без роли: {cnt}")

    elif text.startswith("/invite") and await has_permission(chat_id, user_id, "can_warn"):
        tgt = await get_target_id(text, reply)
        if tgt:
            try:
                lnk = await api.messages.get_invite_link(peer_id=chat_id)
                await api.messages.send(peer_id=tgt, message="📨 Приглашение!", random_id=random.randint(0, 2**31), keyboard=Keyboard(inline=True).add(OpenLink(lnk.link, "🔗 Войти")).get_json())
                return await message.answer("✅ Отправлено.")
            except: return await message.answer("❌ Ошибка отправки.")

    elif message.payload:
        try:
            p = json.loads(message.payload) if isinstance(message.payload, str) else message.payload
            if p.get("command") == "unmute" and await has_permission(chat_id, user_id, "can_mute"):
                db.unmute_user(p.get("user_id"), p.get("chat_id")); return await message.answer("🔊 Размучен.")
            elif p.get("command") == "staff_toggle": return await message.answer(await build_staff_text(chat_id, p.get("mode", "nick")), keyboard=staff_keyboard(p.get("mode", "nick")).get_json())
        except: pass

    elif text.startswith("/"):
        kc = ["/start", "/help", "/profile", "/rules", "/roles", "/staff", "/warns", "/warn", "/unwarn", "/clearwarns", "/kick", "/gkick", "/ban", "/gban", "/unban", "/getban", "/mute", "/gmute", "/unmute", "/setrole", "/grole", "/newrole", "/delrole", "/recrate", "/ownername", "/removerole", "/zov", "/report", "/nick", "/removenick", "/setwelcome", "/linkchat", "/welcome", "/notes", "/note", "/save", "/delete", "/del", "/masskick", "/pin", "/unpin", "/invite", "/q", "/antispam", "/antimat", "/antilink", "/anticaps", "/stat", "/topchat", "/chatinfo", "/filter", "/clean", "/ai", "/calc", "/quote", "/joke", "/coin", "/dice", "/random", "/choose", "/remind"]
        cmd = text.split()[0].lower()
        if cmd not in kc:
            bm, bd = None, float('inf')
            for k in kc:
                d = sum(1 for c1, c2 in zip(cmd, k) if c1 != c2) + abs(len(cmd) - len(k))
                if d < bd and d <= 3: bd, bm = d, k
            return await message.answer(f"❓ Неизвестно: {cmd}\n💡 Может: {bm}?" if bm else f"❓ Неизвестная команда: {cmd}")

# Запуск фоновых задач Vkbottle (официальный и единственный безопасный способ)
@bot.loop_wrapper.interval(seconds=EXPIRE_CHECK_INTERVAL_SECONDS)
async def background_tasks_loop():
    try:
        now = int(time.time())
        for uid, cid, _mby, reason in db.get_expired_mutes():
            try: await api.messages.send(peer_id=cid, message=f"🔊 Мут истёк!\n\n{await mention_user(uid, cid)} (id{uid})\n✅ Теперь может писать в чате!", random_id=random.randint(0, 2**31))
            except: pass
        db.cleanup_expired_mutes()

        for uid, cid, reason, _bby in db.get_expired_bans():
            try: await api.messages.send(peer_id=cid, message=f"✅ Пользователь разбанен: {await mention_user(uid, cid)} (id{uid})\n⏱ Срок бана истек.", random_id=random.randint(0, 2**31))
            except: pass
        db.cleanup_expired_bans()

        for rem_id, uid, cid, text in db.get_due_reminders():
            try: await api.messages.send(peer_id=cid, message=f"⏰ Напоминание для {await mention_user(uid, cid)}:\n{text}", random_id=random.randint(0, 2**31))
            except: pass
            db.remove_reminder(rem_id)

    except Exception as e:
        log.error(f"Background task error: {e}")

@bot.loop_wrapper.on_startup.append
async def startup_tasks():
    log.info("Настройка API...")
    await ensure_vk_api_endpoint()
    await get_bot_user_id()
    log.info("API настроено, бот готов к работе.")

if __name__ == "__main__":
    log.info("🚀 Запуск MEGA VK Чат Менеджер Бота...")
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        log.info("🛑 Бот остановлен")
