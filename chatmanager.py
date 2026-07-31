# =============================================================================
#  FLAME: чат-менеджер — VK Community Chat Manager Bot
#  Библиотека: vk_api (токен сообщества)
#  Запуск: python3 bot.py
# =============================================================================

import sys
import subprocess
import importlib
import importlib.util
import os
import re
import time
import random
import secrets
import datetime
import logging
import threading
import sqlite3
import json
import hashlib
import math
import signal
import asyncio
import socket
from pathlib import Path
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any

# ══════════════════════════════════════════════════════════════════════════════
#  ★  НАСТРОЙКИ
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN  = "vk1.a.6swHdGE_edouQ2HunrS5zZ5P18mARaVRPdqygO4ftu7Tew8VCs4Li2X1NkduZUKBpmVsGFUQm7iuGHxeppuwJkp2rKpfS8bEOvsDVd_LHpK7rkyXOM-64XpoGLyOhr8VGDk2KZM1GDz38-NX5Cb1ystI4t0XP3xaoVgq0P8kgLMbBPSakEbmgGsZfvLcMGOwVgcq9rdDHjV12EP-EIJj9w"
CREATOR_ID = 848213593
PREFIX     = "/"

BOT_NAME             = "FLAME: чат-менеджер"

# Встроенная сетка ролей беседы. Эти роли всегда показываются в /roles.
BUILTIN_CHAT_ROLES = {
    100: ("Владелец", "🔱"),
    80:  ("Главный администратор", "⭐"),
    60:  ("Администратор", "🛡"),
    40:  ("Модератор", "⚔️"),
    20:  ("Помощник", "🔰"),
}
BUILTIN_ROLE_PRIORITIES = frozenset({0, 20, 40, 60, 80, 100})

DEFAULT_BAN_REASON   = "Нарушение правил"
DEFAULT_MUTE_REASON  = "Нарушение правил"
DEFAULT_KICK_REASON  = "Нарушение правил"
DEFAULT_WARN_REASON  = "Нарушение правил"
DEFAULT_MUTE_MINUTES = 10
MAX_WARNS            = 3
AUTO_BAN_DAYS        = 7
TZ_OFFSET_HOURS      = 3

DB_PATH       = Path("database.db")
LOG_PATH      = Path("bot.log")
START_TIME    = int(time.time())

# ══════════════════════════════════════════════════════════════════════════════
#  ЛОГГЕР
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("norot")

# ══════════════════════════════════════════════════════════════════════════════
#  АВТО-УСТАНОВКА ЗАВИСИМОСТЕЙ
# ══════════════════════════════════════════════════════════════════════════════

def _ensure(packages: dict) -> None:
    missing = [pip for mod, pip in packages.items() if not importlib.util.find_spec(mod)]
    if missing:
        log.info(f"[SETUP] Устанавливаю: {', '.join(missing)}")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", *missing], check=True)
        log.info("[SETUP] Готово. Перезапуск...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

_ensure({"vk_api": "vk_api", "requests": "requests", "aiohttp": "aiohttp"})

import aiohttp
from aiohttp import web as _aiohttp_web
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

# ══════════════════════════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ
# ══════════════════════════════════════════════════════════════════════════════

class Database:
    _inst: Optional["Database"] = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
            cls._inst._conn = None
        return cls._inst

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id        INTEGER PRIMARY KEY,
            unity_id       INTEGER,
            owner_id       INTEGER,
            title          TEXT    DEFAULT '',
            welcome_text   TEXT    DEFAULT '',
            goodbye_text   TEXT    DEFAULT '',
            rules_text     TEXT    DEFAULT '',
            silence_mode   INTEGER DEFAULT 0,
            antiraid       INTEGER DEFAULT 0,
            filter_caps    INTEGER DEFAULT 0,
            filter_links   INTEGER DEFAULT 0,
            filter_mat     INTEGER DEFAULT 0,
            filter_voice   INTEGER DEFAULT 0,
            filter_forward INTEGER DEFAULT 0,
            filter_sticker INTEGER DEFAULT 0,
            slowmode_sec   INTEGER DEFAULT 0,
            flood_limit    INTEGER DEFAULT 0,
            flood_interval INTEGER DEFAULT 0,
            flood_mute_min INTEGER DEFAULT 5,
            log_peer_id    INTEGER DEFAULT 0,
            autorole_id    INTEGER DEFAULT NULL,
            kickgroups     INTEGER DEFAULT 0,
            msg_count      INTEGER DEFAULT 0,
            created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS unities (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            owner_id   INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS roles (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            priority   INTEGER NOT NULL,
            badge      TEXT    DEFAULT NULL,
            chat_id    INTEGER DEFAULT NULL,
            unity_id   INTEGER DEFAULT NULL,
            scope      TEXT    NOT NULL DEFAULT 'local',
            created_by INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS members (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            chat_id      INTEGER NOT NULL,
            role_id      INTEGER DEFAULT NULL,
            priority     INTEGER NOT NULL DEFAULT 0,
            nickname     TEXT    DEFAULT NULL,
            badge        TEXT    DEFAULT NULL,
            is_muted     INTEGER DEFAULT 0,
            mute_until   INTEGER DEFAULT 0,
            msg_count    INTEGER DEFAULT 0,
            last_msg_ts  INTEGER DEFAULT 0,
            joined_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            last_seen    INTEGER DEFAULT 0,
            reg_date     INTEGER DEFAULT NULL,
            temprole_id  INTEGER DEFAULT NULL,
            temprole_until INTEGER DEFAULT 0,
            temprole_prev_priority INTEGER DEFAULT 0,
            temprole_prev_role_id INTEGER DEFAULT NULL,
            UNIQUE(user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS warns (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            chat_id    INTEGER NOT NULL,
            issued_by  INTEGER NOT NULL,
            reason     TEXT    NOT NULL DEFAULT 'Нарушение правил',
            active     INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS warn_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            chat_id    INTEGER NOT NULL,
            issued_by  INTEGER NOT NULL,
            reason     TEXT    NOT NULL,
            action     TEXT    NOT NULL DEFAULT 'issued',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS bans (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            chat_id    INTEGER DEFAULT NULL,
            unity_id   INTEGER DEFAULT NULL,
            scope      TEXT    NOT NULL DEFAULT 'local',
            issued_by  INTEGER NOT NULL,
            reason     TEXT    NOT NULL DEFAULT 'Нарушение правил',
            ban_until  INTEGER NOT NULL DEFAULT 0,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS action_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            actor_id   INTEGER NOT NULL,
            target_id  INTEGER DEFAULT NULL,
            action     TEXT    NOT NULL,
            details    TEXT    DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS triggers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      INTEGER NOT NULL,
            keyword      TEXT    NOT NULL,
            response     TEXT    NOT NULL,
            match_type   TEXT    NOT NULL DEFAULT 'contains',
            created_by   INTEGER NOT NULL,
            use_count    INTEGER DEFAULT 0,
            created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, keyword)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            text       TEXT    NOT NULL,
            title      TEXT    DEFAULT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS mention_opt (
            user_id  INTEGER NOT NULL,
            chat_id  INTEGER NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS reports (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            target_id  INTEGER DEFAULT NULL,
            text       TEXT    NOT NULL,
            reviewed   INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS cmd_blocked (
            user_id    INTEGER PRIMARY KEY,
            blocked_by INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS cmd_overrides (
            chat_id    INTEGER NOT NULL,
            command    TEXT    NOT NULL,
            priority   INTEGER NOT NULL,
            allowed    INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (chat_id, command, priority)
        );
        CREATE TABLE IF NOT EXISTS blacklist_words (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            word       TEXT    NOT NULL,
            action     TEXT    NOT NULL DEFAULT 'delete',
            added_by   INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, word)
        );
        CREATE TABLE IF NOT EXISTS whitelist_links (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            domain     TEXT    NOT NULL,
            added_by   INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(chat_id, domain)
        );
        CREATE TABLE IF NOT EXISTS schedules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            text        TEXT    NOT NULL,
            send_at     INTEGER NOT NULL,
            repeat_sec  INTEGER DEFAULT 0,
            created_by  INTEGER NOT NULL,
            sent        INTEGER DEFAULT 0,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS slowmode_tracker (
            user_id    INTEGER NOT NULL,
            chat_id    INTEGER NOT NULL,
            last_msg   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS flood_tracker (
            user_id    INTEGER NOT NULL,
            chat_id    INTEGER NOT NULL,
            timestamps TEXT    NOT NULL DEFAULT '[]',
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS vip_users (
            user_id    INTEGER NOT NULL,
            chat_id    INTEGER NOT NULL,
            badge      TEXT    NOT NULL DEFAULT '⭐',
            added_by   INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS cooldowns (
            user_id    INTEGER NOT NULL,
            chat_id    INTEGER NOT NULL,
            command    TEXT    NOT NULL,
            last_use   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, chat_id, command)
        );
        CREATE TABLE IF NOT EXISTS processed_events (
            event_key  TEXT    PRIMARY KEY,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS chat_media_stats (
            chat_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            photos     INTEGER DEFAULT 0,
            videos     INTEGER DEFAULT 0,
            docs       INTEGER DEFAULT 0,
            stickers   INTEGER DEFAULT 0,
            voices     INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );

        -- Игровая экономика
        CREATE TABLE IF NOT EXISTS game_wallets (
            user_id    INTEGER PRIMARY KEY,
            balance    INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS game_agents (
            user_id    INTEGER PRIMARY KEY,
            added_by   INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS game_countries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
            owner_id   INTEGER NOT NULL UNIQUE,
            infantry   INTEGER NOT NULL DEFAULT 0,
            tanks      INTEGER NOT NULL DEFAULT 0,
            planes     INTEGER NOT NULL DEFAULT 0,
            wins       INTEGER NOT NULL DEFAULT 0,
            losses     INTEGER NOT NULL DEFAULT 0,
            treasury   INTEGER NOT NULL DEFAULT 0,
            reputation INTEGER NOT NULL DEFAULT 0,
            is_open    INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS game_factions (
            user_id      INTEGER PRIMARY KEY,
            faction      TEXT NOT NULL,
            joined_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            action_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS game_inventory (
            user_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            qty     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS game_market_state (
            item_id    INTEGER PRIMARY KEY,
            buy_price  INTEGER NOT NULL,
            stock      INTEGER NOT NULL DEFAULT 0,
            bucket     INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS game_cooldowns (
            user_id INTEGER NOT NULL,
            action  TEXT NOT NULL,
            last_use INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, action)
        );
        CREATE TABLE IF NOT EXISTS game_battles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            attacker_id INTEGER NOT NULL,
            defender_id INTEGER NOT NULL,
            winner_id   INTEGER NOT NULL,
            loot        INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS game_country_members (
            user_id    INTEGER PRIMARY KEY,
            country_id INTEGER NOT NULL,
            rank       TEXT NOT NULL DEFAULT 'citizen',
            joined_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_game_country_members_country
            ON game_country_members(country_id);
        CREATE TABLE IF NOT EXISTS game_country_bans (
            country_id INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            banned_by  INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (country_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS game_businesses (
            user_id     INTEGER PRIMARY KEY,
            business_id INTEGER NOT NULL,
            products    INTEGER NOT NULL DEFAULT 0,
            last_income INTEGER NOT NULL DEFAULT 0,
            purchased_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS game_weapons (
            user_id   INTEGER NOT NULL,
            weapon_id INTEGER NOT NULL,
            qty       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, weapon_id)
        );
        CREATE TABLE IF NOT EXISTS site_login_codes (
            code_hash  TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used_at    INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS game_player_stats (
            user_id       INTEGER PRIMARY KEY,
            shooting_kills INTEGER NOT NULL DEFAULT 0
        );
        """)
        self._conn.commit()
        self._run_column_migrations()
        # Владельцы уже существующих стран автоматически становятся гражданами.
        self._conn.execute(
            "INSERT OR IGNORE INTO game_country_members(user_id,country_id,rank) "
            "SELECT owner_id,id,'owner' FROM game_countries"
        )
        self._conn.commit()
        self._cleanup_priority_invariant()

    def _cleanup_priority_invariant(self) -> None:
        """Не допускает произвольный priority>=100.

        Приоритет 100 разрешён реальному владельцу беседы либо участнику,
        которому создатель назначил встроенную роль «Владелец».
        """
        try:
            self._conn.execute("""
                UPDATE members SET role_id=NULL, priority=99
                WHERE priority >= 100
                AND NOT EXISTS (
                    SELECT 1 FROM chats
                    WHERE chats.chat_id = members.chat_id
                    AND chats.owner_id = members.user_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM roles
                    WHERE roles.id = members.role_id
                    AND roles.priority = 100
                )
            """)
            changed = self._conn.execute(
                "SELECT changes()"
            ).fetchone()[0]
            self._conn.commit()
            if changed:
                log.info(f"[DB] Инвариант владельца: понижен priority у {changed} участников.")
        except Exception as e:
            log.warning(f"[DB] _cleanup_priority_invariant: {e}")

    def _run_column_migrations(self) -> None:
        """Добавляет недостающие колонки в существующие таблицы (безопасно)."""
        migrations = [
            # members
            ("members", "msg_count",               "INTEGER DEFAULT 0"),
            ("members", "last_msg_ts",             "INTEGER DEFAULT 0"),
            ("members", "last_seen",               "INTEGER DEFAULT 0"),
            ("members", "reg_date",                "INTEGER DEFAULT NULL"),
            ("members", "nickname",                "TEXT DEFAULT NULL"),
            ("members", "badge",                   "TEXT DEFAULT NULL"),
            ("members", "is_muted",                "INTEGER DEFAULT 0"),
            ("members", "mute_until",              "INTEGER DEFAULT 0"),
            ("members", "role_id",                 "INTEGER DEFAULT NULL"),
            ("members", "priority",                "INTEGER DEFAULT 0"),
            ("members", "temprole_id",             "INTEGER DEFAULT NULL"),
            ("members", "temprole_until",          "INTEGER DEFAULT 0"),
            ("members", "temprole_prev_priority",  "INTEGER DEFAULT 0"),
            ("members", "temprole_prev_role_id",   "INTEGER DEFAULT NULL"),
            ("members", "joined_at",               "INTEGER NOT NULL DEFAULT 0"),
            # chats
            ("chats",   "msg_count",               "INTEGER DEFAULT 0"),
            ("chats",   "title",                   "TEXT DEFAULT ''"),
            ("chats",   "welcome_text",            "TEXT DEFAULT ''"),
            ("chats",   "goodbye_text",            "TEXT DEFAULT ''"),
            ("chats",   "rules_text",              "TEXT DEFAULT ''"),
            ("chats",   "silence_mode",            "INTEGER DEFAULT 0"),
            ("chats",   "antiraid",                "INTEGER DEFAULT 0"),
            ("chats",   "filter_caps",             "INTEGER DEFAULT 0"),
            ("chats",   "filter_links",            "INTEGER DEFAULT 0"),
            ("chats",   "filter_mat",              "INTEGER DEFAULT 0"),
            ("chats",   "filter_voice",            "INTEGER DEFAULT 0"),
            ("chats",   "filter_forward",          "INTEGER DEFAULT 0"),
            ("chats",   "filter_sticker",          "INTEGER DEFAULT 0"),
            ("chats",   "slowmode_sec",            "INTEGER DEFAULT 0"),
            ("chats",   "flood_limit",             "INTEGER DEFAULT 0"),
            ("chats",   "flood_interval",          "INTEGER DEFAULT 0"),
            ("chats",   "flood_mute_min",           "INTEGER DEFAULT 5"),
            ("chats",   "log_peer_id",             "INTEGER DEFAULT 0"),
            ("chats",   "autorole_id",             "INTEGER DEFAULT NULL"),
            ("chats",   "kickgroups",              "INTEGER DEFAULT 0"),
            ("chats",   "unity_id",                "INTEGER DEFAULT NULL"),
            ("chats",   "owner_id",                "INTEGER DEFAULT NULL"),
            ("chats",   "created_at",              "INTEGER NOT NULL DEFAULT 0"),
            ("chats",   "public_link",             "TEXT DEFAULT NULL"),
            # roles — badge добавлялась позже, нужна явная миграция
            ("roles",   "badge",                   "TEXT DEFAULT NULL"),
            ("roles",   "unity_id",                "INTEGER DEFAULT NULL"),
            ("roles",   "scope",                   "TEXT NOT NULL DEFAULT 'local'"),
            ("roles",   "chat_id",                 "INTEGER DEFAULT NULL"),
            # game_countries — новые поля игрового модуля
            ("game_countries", "treasury",          "INTEGER NOT NULL DEFAULT 0"),
            ("game_countries", "reputation",        "INTEGER NOT NULL DEFAULT 0"),
            ("game_countries", "is_open",           "INTEGER NOT NULL DEFAULT 0"),
        ]
        for table, column, definition in migrations:
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                self._conn.commit()
                log.info(f"[DB] Миграция применена: {table}.{column}")
            except sqlite3.OperationalError as e:
                err = str(e).lower()
                if "duplicate column" in err or "already exists" in err:
                    pass  # Колонка уже есть — норма
                else:
                    log.warning(f"[DB] Миграция {table}.{column}: {e}")
            except Exception as e:
                log.warning(f"[DB] Миграция {table}.{column}: {e}")

    def _row(self, r):
        return dict(r) if r else None

    def _rows(self, rs):
        return [dict(r) for r in rs]

    def ex(self, sql: str, p: tuple = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, p)
        self._conn.commit()
        return cur

    def one(self, sql: str, p: tuple = ()) -> Optional[dict]:
        return self._row(self._conn.execute(sql, p).fetchone())

    def all(self, sql: str, p: tuple = ()) -> List[dict]:
        return self._rows(self._conn.execute(sql, p).fetchall())

    # ── chats ──────────────────────────────────────────────────────────────────
    def get_chat(self, chat_id: int) -> Optional[dict]:
        return self.one("SELECT * FROM chats WHERE chat_id=?", (chat_id,))

    def upsert_chat(self, chat_id: int, owner_id: Optional[int], title: str = "") -> None:
        self.ex(
            "INSERT INTO chats(chat_id,owner_id,title) VALUES(?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "title=CASE WHEN excluded.title!='' THEN excluded.title ELSE chats.title END, "
            "owner_id=COALESCE(chats.owner_id, excluded.owner_id)",
            (chat_id, owner_id, title),
        )

    def set_chat_field(self, chat_id: int, field: str, value) -> None:
        allowed = {
            "welcome_text", "goodbye_text", "rules_text", "silence_mode", "antiraid",
            "filter_caps", "filter_links", "filter_mat", "filter_voice",
            "filter_forward", "filter_sticker", "slowmode_sec", "flood_limit",
            "flood_interval", "flood_mute_min", "log_peer_id", "autorole_id",
            "unity_id", "owner_id", "kickgroups", "public_link",
        }
        if field not in allowed:
            raise ValueError(f"Unknown field: {field}")
        self.ex(f"UPDATE chats SET {field}=? WHERE chat_id=?", (value, chat_id))

    def increment_chat_msg(self, chat_id: int) -> None:
        self.ex("UPDATE chats SET msg_count=msg_count+1 WHERE chat_id=?", (chat_id,))

    def get_chats_by_unity(self, unity_id: int) -> List[dict]:
        return self.all("SELECT * FROM chats WHERE unity_id=?", (unity_id,))

    def get_all_chats(self) -> List[dict]:
        return self.all("SELECT * FROM chats")

    # ── unities ────────────────────────────────────────────────────────────────
    def get_unity(self, uid: int) -> Optional[dict]:
        return self.one("SELECT * FROM unities WHERE id=?", (uid,))

    def get_unity_by_name(self, name: str) -> Optional[dict]:
        return self.one("SELECT * FROM unities WHERE name=?", (name,))

    def create_unity(self, name: str, owner_id: int) -> dict:
        cur = self.ex("INSERT INTO unities(name,owner_id) VALUES(?,?)", (name, owner_id))
        return self.get_unity(cur.lastrowid)

    def delete_unity(self, uid: int) -> None:
        self.ex("DELETE FROM unities WHERE id=?", (uid,))

    def list_unities(self) -> List[dict]:
        return self.all("SELECT * FROM unities ORDER BY id")

    # ── members ────────────────────────────────────────────────────────────────
    def try_claim_event(self, event_key: str) -> bool:
        """Атомарно занимает event_key через отдельное соединение с BEGIN IMMEDIATE.
        BEGIN IMMEDIATE блокирует запись сразу — второй процесс получит 'database is locked'
        и вернёт False, даже если первый ещё не сделал commit."""
        conn = None
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=0.5)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT 1 FROM processed_events WHERE event_key=?", (event_key,)
            ).fetchone()
            if row:
                conn.rollback()
                return False
            conn.execute(
                "INSERT INTO processed_events(event_key,created_at) VALUES(?,?)",
                (event_key, int(time.time())),
            )
            conn.commit()
            return True
        except Exception:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            return False
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    def cleanup_old_events(self, max_age_sec: int = 120) -> None:
        cutoff = int(time.time()) - max_age_sec
        try:
            self._conn.execute("DELETE FROM processed_events WHERE created_at<?", (cutoff,))
            self._conn.commit()
        except Exception:
            pass

    def get_member(self, user_id: int, chat_id: int) -> Optional[dict]:
        return self.one("SELECT * FROM members WHERE user_id=? AND chat_id=?", (user_id, chat_id))

    def upsert_member(self, user_id: int, chat_id: int) -> None:
        self.ex(
            "INSERT INTO members(user_id,chat_id) VALUES(?,?) ON CONFLICT(user_id,chat_id) DO NOTHING",
            (user_id, chat_id),
        )

    def set_mute(self, user_id: int, chat_id: int, until_ts: int) -> None:
        self.ex(
            "UPDATE members SET is_muted=1,mute_until=? WHERE user_id=? AND chat_id=?",
            (until_ts, user_id, chat_id),
        )

    def remove_mute(self, user_id: int, chat_id: int) -> None:
        self.ex(
            "UPDATE members SET is_muted=0,mute_until=0 WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        )

    def set_nickname(self, user_id: int, chat_id: int, nick: Optional[str]) -> None:
        self.ex(
            "UPDATE members SET nickname=? WHERE user_id=? AND chat_id=?",
            (nick, user_id, chat_id),
        )

    def set_badge(self, user_id: int, chat_id: int, badge: Optional[str]) -> None:
        self.ex(
            "UPDATE members SET badge=? WHERE user_id=? AND chat_id=?",
            (badge, user_id, chat_id),
        )

    def get_nicklist(self, chat_id: int) -> List[dict]:
        return self.all(
            "SELECT * FROM members WHERE chat_id=? AND nickname IS NOT NULL AND nickname!=''",
            (chat_id,),
        )

    def get_by_nick_part(self, chat_id: int, part: str) -> List[dict]:
        return self.all(
            "SELECT * FROM members WHERE chat_id=? AND nickname LIKE ?",
            (chat_id, f"%{part}%"),
        )

    def update_member_role(self, user_id: int, chat_id: int, role_id: Optional[int], priority: int) -> None:
        self.ex(
            "UPDATE members SET role_id=?,priority=? WHERE user_id=? AND chat_id=?",
            (role_id, priority, user_id, chat_id),
        )

    def update_last_seen(self, user_id: int, chat_id: int) -> None:
        now = int(time.time())
        self.ex(
            "UPDATE members SET last_seen=?,last_msg_ts=?,msg_count=msg_count+1 WHERE user_id=? AND chat_id=?",
            (now, now, user_id, chat_id),
        )

    def set_reg(self, user_id: int, chat_id: int) -> None:
        self.ex(
            "UPDATE members SET reg_date=? WHERE user_id=? AND chat_id=? AND reg_date IS NULL",
            (int(time.time()), user_id, chat_id),
        )

    def get_inactive(self, chat_id: int, days: int) -> List[dict]:
        threshold = int(time.time()) - days * 86400
        return self.all(
            "SELECT * FROM members WHERE chat_id=? AND last_seen>0 AND last_seen<?",
            (chat_id, threshold),
        )

    def get_all_members(self, chat_id: int) -> List[dict]:
        return self.all("SELECT * FROM members WHERE chat_id=?", (chat_id,))

    def get_top_members(self, chat_id: int, limit: int = 10) -> List[dict]:
        return self.all(
            "SELECT * FROM members WHERE chat_id=? AND msg_count>0 ORDER BY msg_count DESC LIMIT ?",
            (chat_id, limit),
        )

    def set_temprole(self, user_id: int, chat_id: int, role_id: Optional[int], until_ts: int,
                     prev_priority: int, prev_role_id: Optional[int]) -> None:
        self.ex(
            "UPDATE members SET temprole_id=?,temprole_until=?,temprole_prev_priority=?,temprole_prev_role_id=? "
            "WHERE user_id=? AND chat_id=?",
            (role_id, until_ts, prev_priority, prev_role_id, user_id, chat_id),
        )

    def clear_temprole(self, user_id: int, chat_id: int) -> None:
        self.ex(
            "UPDATE members SET temprole_id=NULL,temprole_until=0,temprole_prev_priority=0,temprole_prev_role_id=NULL "
            "WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        )

    def get_expired_temproles(self) -> List[dict]:
        now = int(time.time())
        return self.all(
            "SELECT * FROM members WHERE temprole_until>0 AND temprole_until<=?",
            (now,),
        )

    # ── roles ──────────────────────────────────────────────────────────────────
    def ensure_builtin_roles(self, chat_id: int, created_by: int) -> None:
        """Создаёт/восстанавливает стандартные роли 100/80/60/40/20."""
        for priority, (name, badge) in BUILTIN_CHAT_ROLES.items():
            existing = self.get_role_by_priority(priority, chat_id=chat_id)
            if existing:
                # Встроенные приоритеты всегда имеют стандартные названия.
                self.ex(
                    "UPDATE roles SET name=?,badge=?,scope='local',chat_id=? WHERE id=?",
                    (name, badge, chat_id, existing["id"]),
                )
            else:
                self.create_role(
                    name=name,
                    priority=priority,
                    created_by=created_by,
                    chat_id=chat_id,
                    scope="local",
                    badge=badge,
                )

    def create_role(self, name: str, priority: int, created_by: int,
                    chat_id: Optional[int] = None, unity_id: Optional[int] = None,
                    scope: str = "local", badge: Optional[str] = None) -> dict:
        cur = self.ex(
            "INSERT INTO roles(name,priority,chat_id,unity_id,scope,created_by,badge) VALUES(?,?,?,?,?,?,?)",
            (name, priority, chat_id, unity_id, scope, created_by, badge),
        )
        return self.one("SELECT * FROM roles WHERE id=?", (cur.lastrowid,))

    def get_role_by_name(self, name: str, chat_id: Optional[int] = None, unity_id: Optional[int] = None) -> Optional[dict]:
        if chat_id:
            return self.one("SELECT * FROM roles WHERE LOWER(name)=LOWER(?) AND chat_id=?", (name, chat_id))
        if unity_id:
            return self.one("SELECT * FROM roles WHERE LOWER(name)=LOWER(?) AND unity_id=?", (name, unity_id))
        return None

    def get_role_by_priority(self, priority: int, chat_id: Optional[int] = None, unity_id: Optional[int] = None) -> Optional[dict]:
        if chat_id:
            return self.one("SELECT * FROM roles WHERE priority=? AND chat_id=?", (priority, chat_id))
        if unity_id:
            return self.one("SELECT * FROM roles WHERE priority=? AND unity_id=?", (priority, unity_id))
        return None

    def get_role_by_id(self, role_id: int) -> Optional[dict]:
        return self.one("SELECT * FROM roles WHERE id=?", (role_id,))

    def list_roles(self, chat_id: Optional[int] = None, unity_id: Optional[int] = None) -> List[dict]:
        if chat_id:
            return self.all("SELECT * FROM roles WHERE chat_id=? ORDER BY priority DESC", (chat_id,))
        if unity_id:
            return self.all("SELECT * FROM roles WHERE unity_id=? ORDER BY priority DESC", (unity_id,))
        return []

    def delete_role(self, priority: int, chat_id: Optional[int] = None, unity_id: Optional[int] = None) -> bool:
        r = self.get_role_by_priority(priority, chat_id, unity_id)
        if not r:
            return False
        role_id = r["id"]
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(
                "UPDATE members SET role_id=NULL,priority=0 WHERE role_id=?",
                (role_id,),
            )
            self._conn.execute(
                "UPDATE members SET temprole_id=NULL,temprole_until=0,temprole_prev_priority=0,temprole_prev_role_id=NULL "
                "WHERE temprole_id=?",
                (role_id,),
            )
            self._conn.execute("UPDATE chats SET autorole_id=NULL WHERE autorole_id=?", (role_id,))
            self._conn.execute("DELETE FROM roles WHERE id=?", (role_id,))
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    def update_role_badge(self, role_id: int, badge: Optional[str]) -> None:
        self.ex("UPDATE roles SET badge=? WHERE id=?", (badge, role_id))

    # ── warns ──────────────────────────────────────────────────────────────────
    def count_warns(self, user_id: int, chat_id: int) -> int:
        r = self.one("SELECT COUNT(*) AS c FROM warns WHERE user_id=? AND chat_id=? AND active=1", (user_id, chat_id))
        return r["c"] if r else 0

    def add_warn(self, user_id: int, chat_id: int, issued_by: int, reason: str) -> int:
        self.ex("INSERT INTO warns(user_id,chat_id,issued_by,reason) VALUES(?,?,?,?)", (user_id, chat_id, issued_by, reason))
        self.ex(
            "INSERT INTO warn_history(user_id,chat_id,issued_by,reason,action) VALUES(?,?,?,?,'issued')",
            (user_id, chat_id, issued_by, reason),
        )
        return self.count_warns(user_id, chat_id)

    def remove_last_warn(self, user_id: int, chat_id: int, issued_by: int) -> bool:
        r = self.one(
            "SELECT id FROM warns WHERE user_id=? AND chat_id=? AND active=1 ORDER BY created_at DESC LIMIT 1",
            (user_id, chat_id),
        )
        if not r:
            return False
        self.ex("UPDATE warns SET active=0 WHERE id=?", (r["id"],))
        self.ex(
            "INSERT INTO warn_history(user_id,chat_id,issued_by,reason,action) VALUES(?,?,?,'снят','removed')",
            (user_id, chat_id, issued_by),
        )
        return True

    def get_warns(self, user_id: int, chat_id: int) -> List[dict]:
        return self.all(
            "SELECT * FROM warns WHERE user_id=? AND chat_id=? AND active=1 ORDER BY created_at",
            (user_id, chat_id),
        )

    def get_warn_history(self, user_id: int, chat_id: int) -> List[dict]:
        return self.all(
            "SELECT * FROM warn_history WHERE user_id=? AND chat_id=? ORDER BY created_at",
            (user_id, chat_id),
        )

    def get_all_warned(self, chat_id: int) -> List[dict]:
        return self.all(
            "SELECT user_id, COUNT(*) AS cnt FROM warns WHERE chat_id=? AND active=1 "
            "GROUP BY user_id HAVING cnt>0 ORDER BY cnt DESC",
            (chat_id,),
        )

    def clear_warns(self, user_id: int, chat_id: int) -> None:
        self.ex("UPDATE warns SET active=0 WHERE user_id=? AND chat_id=? AND active=1", (user_id, chat_id))

    def clear_all_warns(self, chat_id: int) -> int:
        cur = self.ex("UPDATE warns SET active=0 WHERE chat_id=? AND active=1", (chat_id,))
        return cur.rowcount

    # ── bans ───────────────────────────────────────────────────────────────────
    def add_ban(self, user_id: int, issued_by: int, reason: str, ban_until: int,
                chat_id: Optional[int] = None, unity_id: Optional[int] = None, scope: str = "local") -> dict:
        cur = self.ex(
            "INSERT INTO bans(user_id,chat_id,unity_id,scope,issued_by,reason,ban_until) VALUES(?,?,?,?,?,?,?)",
            (user_id, chat_id, unity_id, scope, issued_by, reason, ban_until),
        )
        return self.one("SELECT * FROM bans WHERE id=?", (cur.lastrowid,))

    def get_active_syncban(self, user_id: int) -> Optional[dict]:
        """Проверяет наличие глобального бана (syncban) по всем беседам."""
        now = int(time.time())
        return self.one(
            "SELECT * FROM bans WHERE user_id=? AND scope='global' AND active=1 AND (ban_until=0 OR ban_until>?)",
            (user_id, now),
        )

    def get_active_ban(self, user_id: int, chat_id: Optional[int] = None, unity_id: Optional[int] = None) -> Optional[dict]:
        now = int(time.time())
        if chat_id:
            return self.one(
                "SELECT * FROM bans WHERE user_id=? AND chat_id=? AND active=1 AND (ban_until=0 OR ban_until>?)",
                (user_id, chat_id, now),
            )
        if unity_id:
            return self.one(
                "SELECT * FROM bans WHERE user_id=? AND unity_id=? AND scope='global' AND active=1 AND (ban_until=0 OR ban_until>?)",
                (user_id, unity_id, now),
            )
        return None

    def remove_ban(self, user_id: int, chat_id: Optional[int] = None, unity_id: Optional[int] = None) -> bool:
        if chat_id:
            cur = self.ex("UPDATE bans SET active=0 WHERE user_id=? AND chat_id=? AND active=1", (user_id, chat_id))
        elif unity_id:
            cur = self.ex("UPDATE bans SET active=0 WHERE user_id=? AND unity_id=? AND active=1", (user_id, unity_id))
        else:
            return False
        return cur.rowcount > 0

    def get_banlist(self, chat_id: int) -> List[dict]:
        now = int(time.time())
        return self.all(
            "SELECT * FROM bans WHERE chat_id=? AND active=1 AND (ban_until=0 OR ban_until>?) ORDER BY created_at DESC",
            (chat_id, now),
        )

    def get_expired_bans(self) -> List[dict]:
        now = int(time.time())
        return self.all(
            "SELECT * FROM bans WHERE active=1 AND ban_until>0 AND ban_until<=?",
            (now,),
        )

    # ── action logs ────────────────────────────────────────────────────────────
    def log_action(self, chat_id: int, actor_id: int, action: str,
                   target_id: Optional[int] = None, details: str = "") -> None:
        self.ex(
            "INSERT INTO action_logs(chat_id,actor_id,target_id,action,details) VALUES(?,?,?,?,?)",
            (chat_id, actor_id, target_id, action, details),
        )

    def get_logs(self, chat_id: int, limit: int = 20) -> List[dict]:
        return self.all(
            "SELECT * FROM action_logs WHERE chat_id=? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        )

    def get_logs_by_user(self, chat_id: int, user_id: int, limit: int = 10) -> List[dict]:
        return self.all(
            "SELECT * FROM action_logs WHERE chat_id=? AND (actor_id=? OR target_id=?) "
            "ORDER BY created_at DESC LIMIT ?",
            (chat_id, user_id, user_id, limit),
        )

    # ── triggers ───────────────────────────────────────────────────────────────
    def add_trigger(self, chat_id: int, keyword: str, response: str,
                    created_by: int, match_type: str = "contains") -> None:
        self.ex(
            "INSERT INTO triggers(chat_id,keyword,response,created_by,match_type) VALUES(?,?,?,?,?) "
            "ON CONFLICT(chat_id,keyword) DO UPDATE SET response=excluded.response,match_type=excluded.match_type",
            (chat_id, keyword.lower(), response, created_by, match_type),
        )

    def remove_trigger(self, chat_id: int, keyword: str) -> bool:
        cur = self.ex("DELETE FROM triggers WHERE chat_id=? AND keyword=?", (chat_id, keyword.lower()))
        return cur.rowcount > 0

    def get_triggers(self, chat_id: int) -> List[dict]:
        return self.all("SELECT * FROM triggers WHERE chat_id=? ORDER BY keyword", (chat_id,))

    def increment_trigger_use(self, trigger_id: int) -> None:
        self.ex("UPDATE triggers SET use_count=use_count+1 WHERE id=?", (trigger_id,))

    # ── notes ──────────────────────────────────────────────────────────────────
    def add_note(self, chat_id: int, user_id: int, text: str, title: Optional[str] = None) -> int:
        cur = self.ex("INSERT INTO notes(chat_id,user_id,text,title) VALUES(?,?,?,?)", (chat_id, user_id, text, title))
        return cur.lastrowid

    def get_notes(self, chat_id: int, limit: int = 10) -> List[dict]:
        return self.all(
            "SELECT * FROM notes WHERE chat_id=? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        )

    def delete_note(self, note_id: int, chat_id: int) -> bool:
        cur = self.ex("DELETE FROM notes WHERE id=? AND chat_id=?", (note_id, chat_id))
        return cur.rowcount > 0

    # ── reports ────────────────────────────────────────────────────────────────
    def add_report(self, chat_id: int, user_id: int, text: str, target_id: Optional[int] = None) -> int:
        cur = self.ex(
            "INSERT INTO reports(chat_id,user_id,target_id,text) VALUES(?,?,?,?)",
            (chat_id, user_id, target_id, text),
        )
        return cur.lastrowid

    def get_reports(self, chat_id: int, only_new: bool = True) -> List[dict]:
        if only_new:
            return self.all("SELECT * FROM reports WHERE chat_id=? AND reviewed=0 ORDER BY created_at", (chat_id,))
        return self.all("SELECT * FROM reports WHERE chat_id=? ORDER BY created_at DESC LIMIT 20", (chat_id,))

    def mark_report_reviewed(self, report_id: int, chat_id: int) -> bool:
        cur = self.ex(
            "UPDATE reports SET reviewed=1 WHERE id=? AND chat_id=? AND reviewed=0",
            (report_id, chat_id),
        )
        return cur.rowcount > 0

    def mark_all_reports_reviewed(self, chat_id: int) -> int:
        cur = self.ex("UPDATE reports SET reviewed=1 WHERE chat_id=? AND reviewed=0", (chat_id,))
        return cur.rowcount

    # ── wipe ───────────────────────────────────────────────────────────────────
    def wipe(self, chat_id: int, target: str) -> int:
        ops = {
            "warns":    ("UPDATE warns SET active=0 WHERE chat_id=?", (chat_id,)),
            "bans":     ("UPDATE bans SET active=0 WHERE chat_id=?", (chat_id,)),
            "roles":    ("UPDATE members SET role_id=NULL,priority=0 WHERE chat_id=?", (chat_id,)),
            "nicks":    ("UPDATE members SET nickname=NULL WHERE chat_id=?", (chat_id,)),
            "notes":    ("DELETE FROM notes WHERE chat_id=?", (chat_id,)),
            "triggers": ("DELETE FROM triggers WHERE chat_id=?", (chat_id,)),
            "logs":     ("DELETE FROM action_logs WHERE chat_id=?", (chat_id,)),
            "stats":    ("UPDATE members SET msg_count=0 WHERE chat_id=?", (chat_id,)),
        }
        if target in ops:
            sql, params = ops[target]
            cur = self.ex(sql, params)
            return cur.rowcount
        return 0

    # ── mention opt ────────────────────────────────────────────────────────────
    def set_mention_disabled(self, user_id: int, chat_id: int, disabled: bool) -> None:
        self.ex(
            "INSERT INTO mention_opt(user_id,chat_id,disabled) VALUES(?,?,?) "
            "ON CONFLICT(user_id,chat_id) DO UPDATE SET disabled=excluded.disabled",
            (user_id, chat_id, int(disabled)),
        )

    def get_mention_disabled(self, chat_id: int) -> List[dict]:
        return self.all("SELECT user_id FROM mention_opt WHERE chat_id=? AND disabled=1", (chat_id,))

    # ── cmd_overrides ──────────────────────────────────────────────────────────
    def set_cmd_override(self, chat_id: int, command: str, priority: int, allowed: bool) -> None:
        self.ex(
            "INSERT INTO cmd_overrides(chat_id,command,priority,allowed) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id,command,priority) DO UPDATE SET allowed=excluded.allowed",
            (chat_id, command.lower(), priority, int(allowed)),
        )

    def remove_cmd_override(self, chat_id: int, command: str, priority: int) -> bool:
        cur = self.ex(
            "DELETE FROM cmd_overrides WHERE chat_id=? AND command=? AND priority=?",
            (chat_id, command.lower(), priority),
        )
        return cur.rowcount > 0

    def get_cmd_override(self, chat_id: int, command: str, priority: int) -> Optional[bool]:
        # Ищем ближайший подходящий override (priority <= user_priority),
        # берём с наибольшим priority (наиболее специфичное правило).
        r = self.one(
            "SELECT allowed FROM cmd_overrides "
            "WHERE chat_id=? AND command=? AND priority<=? "
            "ORDER BY priority DESC LIMIT 1",
            (chat_id, command.lower(), priority),
        )
        return bool(r["allowed"]) if r is not None else None

    def get_cmd_overrides(self, chat_id: int) -> List[dict]:
        return self.all("SELECT * FROM cmd_overrides WHERE chat_id=? ORDER BY command, priority", (chat_id,))

    # ── cmd_blocked ────────────────────────────────────────────────────────────
    def set_cmd_blocked(self, user_id: int, blocked_by: int) -> None:
        self.ex(
            "INSERT INTO cmd_blocked(user_id,blocked_by) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET blocked_by=excluded.blocked_by, created_at=strftime('%s','now')",
            (user_id, blocked_by),
        )

    def remove_cmd_blocked(self, user_id: int) -> bool:
        cur = self.ex("DELETE FROM cmd_blocked WHERE user_id=?", (user_id,))
        return cur.rowcount > 0

    def is_cmd_blocked(self, user_id: int) -> bool:
        return self.one("SELECT 1 FROM cmd_blocked WHERE user_id=?", (user_id,)) is not None

    # ── blacklist_words ────────────────────────────────────────────────────────
    def add_blacklist_word(self, chat_id: int, word: str, added_by: int, action: str = "delete") -> bool:
        try:
            self.ex(
                "INSERT INTO blacklist_words(chat_id,word,added_by,action) VALUES(?,?,?,?) "
                "ON CONFLICT(chat_id,word) DO UPDATE SET action=excluded.action",
                (chat_id, word.lower().strip(), added_by, action),
            )
            return True
        except Exception:
            return False

    def remove_blacklist_word(self, chat_id: int, word: str) -> bool:
        cur = self.ex("DELETE FROM blacklist_words WHERE chat_id=? AND word=?", (chat_id, word.lower().strip()))
        return cur.rowcount > 0

    def get_blacklist_words(self, chat_id: int) -> List[dict]:
        return self.all("SELECT * FROM blacklist_words WHERE chat_id=? ORDER BY word", (chat_id,))

    def clear_blacklist(self, chat_id: int) -> int:
        cur = self.ex("DELETE FROM blacklist_words WHERE chat_id=?", (chat_id,))
        return cur.rowcount

    # ── whitelist_links ────────────────────────────────────────────────────────
    def add_whitelist_link(self, chat_id: int, domain: str, added_by: int) -> None:
        self.ex(
            "INSERT INTO whitelist_links(chat_id,domain,added_by) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,domain) DO NOTHING",
            (chat_id, domain.lower().strip(), added_by),
        )

    def remove_whitelist_link(self, chat_id: int, domain: str) -> bool:
        cur = self.ex("DELETE FROM whitelist_links WHERE chat_id=? AND domain=?", (chat_id, domain.lower().strip()))
        return cur.rowcount > 0

    def get_whitelist_links(self, chat_id: int) -> List[dict]:
        return self.all("SELECT * FROM whitelist_links WHERE chat_id=? ORDER BY domain", (chat_id,))

    def is_link_allowed(self, chat_id: int, domain: str) -> bool:
        return self.one("SELECT 1 FROM whitelist_links WHERE chat_id=? AND domain=?", (chat_id, domain.lower())) is not None

    # ── schedules ──────────────────────────────────────────────────────────────
    def add_schedule(self, chat_id: int, text: str, send_at: int, repeat_sec: int, created_by: int) -> dict:
        cur = self.ex(
            "INSERT INTO schedules(chat_id,text,send_at,repeat_sec,created_by) VALUES(?,?,?,?,?)",
            (chat_id, text, send_at, repeat_sec, created_by),
        )
        return self.one("SELECT * FROM schedules WHERE id=?", (cur.lastrowid,))

    def get_schedules(self, chat_id: int) -> List[dict]:
        return self.all("SELECT * FROM schedules WHERE chat_id=? AND sent=0 ORDER BY send_at", (chat_id,))

    def get_pending_schedules(self) -> List[dict]:
        now = int(time.time())
        return self.all("SELECT * FROM schedules WHERE sent=0 AND send_at<=?", (now,))

    def mark_schedule_sent(self, schedule_id: int, repeat_sec: int, previous_send_at: int = 0) -> None:
        if repeat_sec > 0:
            now = int(time.time())
            next_ts = previous_send_at or now
            while next_ts <= now:
                next_ts += repeat_sec
            self.ex("UPDATE schedules SET send_at=? WHERE id=?", (next_ts, schedule_id))
        else:
            self.ex("UPDATE schedules SET sent=1 WHERE id=?", (schedule_id,))

    def delete_schedule(self, schedule_id: int, chat_id: int) -> bool:
        cur = self.ex("DELETE FROM schedules WHERE id=? AND chat_id=?", (schedule_id, chat_id))
        return cur.rowcount > 0

    # ── slowmode ───────────────────────────────────────────────────────────────
    def get_slowmode(self, user_id: int, chat_id: int) -> Optional[dict]:
        return self.one("SELECT * FROM slowmode_tracker WHERE user_id=? AND chat_id=?", (user_id, chat_id))

    def update_slowmode(self, user_id: int, chat_id: int) -> None:
        self.ex(
            "INSERT INTO slowmode_tracker(user_id,chat_id,last_msg) VALUES(?,?,?) "
            "ON CONFLICT(user_id,chat_id) DO UPDATE SET last_msg=excluded.last_msg",
            (user_id, chat_id, int(time.time())),
        )

    # ── flood ──────────────────────────────────────────────────────────────────
    def get_flood_data(self, user_id: int, chat_id: int) -> List[int]:
        r = self.one("SELECT timestamps FROM flood_tracker WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        if not r:
            return []
        try:
            return json.loads(r["timestamps"])
        except Exception:
            return []

    def set_flood_data(self, user_id: int, chat_id: int, timestamps: List[int]) -> None:
        self.ex(
            "INSERT INTO flood_tracker(user_id,chat_id,timestamps) VALUES(?,?,?) "
            "ON CONFLICT(user_id,chat_id) DO UPDATE SET timestamps=excluded.timestamps",
            (user_id, chat_id, json.dumps(timestamps)),
        )

    # ── vip ────────────────────────────────────────────────────────────────────
    def add_vip(self, user_id: int, chat_id: int, badge: str, added_by: int) -> None:
        self.ex(
            "INSERT INTO vip_users(user_id,chat_id,badge,added_by) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,chat_id) DO UPDATE SET badge=excluded.badge,added_by=excluded.added_by",
            (user_id, chat_id, badge, added_by),
        )

    def remove_vip(self, user_id: int, chat_id: int) -> bool:
        cur = self.ex("DELETE FROM vip_users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        return cur.rowcount > 0

    def get_vip(self, user_id: int, chat_id: int) -> Optional[dict]:
        return self.one("SELECT * FROM vip_users WHERE user_id=? AND chat_id=?", (user_id, chat_id))

    def get_vip_list(self, chat_id: int) -> List[dict]:
        return self.all("SELECT * FROM vip_users WHERE chat_id=? ORDER BY created_at", (chat_id,))

    # ── cooldowns ──────────────────────────────────────────────────────────────
    def get_cooldown(self, user_id: int, chat_id: int, command: str) -> int:
        r = self.one(
            "SELECT last_use FROM cooldowns WHERE user_id=? AND chat_id=? AND command=?",
            (user_id, chat_id, command),
        )
        return r["last_use"] if r else 0

    def set_cooldown(self, user_id: int, chat_id: int, command: str) -> None:
        self.ex(
            "INSERT INTO cooldowns(user_id,chat_id,command,last_use) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,chat_id,command) DO UPDATE SET last_use=excluded.last_use",
            (user_id, chat_id, command, int(time.time())),
        )

    # ── media stats ────────────────────────────────────────────────────────────
    def update_media_stats(self, user_id: int, chat_id: int, media_type: str) -> None:
        allowed_types = {"photos", "videos", "docs", "stickers", "voices"}
        if media_type not in allowed_types:
            return
        self.ex(
            f"INSERT INTO chat_media_stats(chat_id,user_id,{media_type}) VALUES(?,?,1) "
            f"ON CONFLICT(chat_id,user_id) DO UPDATE SET {media_type}={media_type}+1",
            (chat_id, user_id),
        )

    def get_media_stats(self, chat_id: int, user_id: Optional[int] = None) -> List[dict]:
        if user_id:
            return self.all("SELECT * FROM chat_media_stats WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        return self.all(
            "SELECT *, (photos+videos+docs+stickers+voices) AS total "
            "FROM chat_media_stats WHERE chat_id=? ORDER BY total DESC LIMIT 10",
            (chat_id,),
        )

# ══════════════════════════════════════════════════════════════════════════════
#  ИНИЦИАЛИЗАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

db = Database()
db.connect()
for _known_chat in db.get_all_chats():
    db.ensure_builtin_roles(_known_chat["chat_id"], CREATOR_ID)
log.info("[DB] База данных подключена. Стандартные роли готовы.")

vk_session = vk_api.VkApi(token=BOT_TOKEN, api_version="5.131")
vk = vk_session.get_api()

try:
    _gi        = vk.groups.getById()
    GROUP_ID   = _gi[0]["id"]
    GROUP_NAME = _gi[0]["name"]
    log.info(f"✅ {BOT_NAME} | Сообщество: {GROUP_NAME} (id{GROUP_ID})")
except Exception as e:
    log.critical(f"❌ Ошибка токена: {e}")
    log.critical("Убедись что токен — токен сообщества (Управление → Работа с API → Создать ключ).")
    sys.exit(1)

longpoll = VkBotLongPoll(vk_session, GROUP_ID)

# Автоматически включаем нужные типы событий в Long Poll настройках сообщества
# (message_event нужен для callback-кнопок)
try:
    vk.groups.setLongPollSettings(
        group_id=GROUP_ID,
        enabled=1,
        api_version="5.131",
        message_new=1,
        message_event=1,
        message_reply=0,
        message_allow=0,
        message_deny=0,
        message_edit=0,
    )
    log.info("[LONGPOLL] Настройки событий обновлены (message_event включён).")
except Exception as _lp_err:
    log.warning(f"[LONGPOLL] Не удалось обновить настройки событий: {_lp_err}")

_db_lock      = threading.RLock()
# Дедупликация событий теперь ведётся через таблицу processed_events в SQLite
# (работает между несколькими процессами, в отличие от in-memory словарей)

# ══════════════════════════════════════════════════════════════════════════════
#  СИСТЕМА ПРИОРИТЕТОВ
# ══════════════════════════════════════════════════════════════════════════════

class PE:
    CREATOR = 105
    OWNER   = 100
    CHIEF   = 80
    ADMIN   = 60
    MODER   = 40
    HELPER  = 20
    MEMBER  = 0

    @staticmethod
    def get(user_id: int, chat_id: int) -> int:
        if user_id == CREATOR_ID:
            return PE.CREATOR
        with _db_lock:
            chat = db.get_chat(chat_id)
            if chat and chat.get("owner_id") == user_id:
                return PE.OWNER
            m = db.get_member(user_id, chat_id)
            return m.get("priority", 0) if m else 0

    @staticmethod
    def can_punish(actor: int, target: int) -> bool:
        return actor > target

    @staticmethod
    def can_create_role(creator_prio: int, role_prio: int) -> bool:
        # priority 100 (PE.OWNER) зарезервирован для owner_id — нельзя назначить через роль
        if creator_prio == PE.CREATOR:
            return 1 <= role_prio <= 99
        if creator_prio >= PE.OWNER:
            return 1 <= role_prio <= 99
        return False

    @staticmethod
    def role_name(p: int) -> str:
        if p >= PE.CREATOR: return "👑 Создатель"
        if p >= PE.OWNER:   return "🔱 Владелец"
        if p >= PE.CHIEF:   return "⭐ Главный администратор"
        if p >= PE.ADMIN:   return "🛡 Администратор"
        if p >= PE.MODER:   return "⚔️ Модератор"
        if p >= PE.HELPER:  return "🔰 Помощник"
        return "Пользователь"

# ══════════════════════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════════════════

def send(peer_id: int, text: str, keyboard: Optional[str] = None, reply_to: Optional[int] = None) -> bool:
    try:
        kwargs: Dict[str, Any] = {
            "peer_id": peer_id,
            "message": text,
            "random_id": random.randint(1, 2_147_483_647),
        }
        if keyboard:
            kwargs["keyboard"] = keyboard
        if reply_to:
            kwargs["reply_to"] = reply_to
        vk.messages.send(**kwargs)
        return True
    except Exception as e:
        log.warning(f"send error peer={peer_id}: {e}")
        return False



# ══════════════════════════════════════════════════════════════════════════════
#  ИНЛАЙН-КЛАВИАТУРЫ (CALLBACK-КНОПКИ)
# ══════════════════════════════════════════════════════════════════════════════

def make_inline_keyboard(rows: list) -> str:
    """Создаёт JSON строку инлайн-клавиатуры для VK Bot API.
    rows — список строк, каждая строка — список кнопок:
    [{"label": "текст", "color": "positive", "payload": {...}}]
    """
    buttons = []
    for row in rows:
        btn_row = []
        for b in row:
            btn_row.append({
                "action": {
                    "type": "callback",
                    "label": b["label"],
                    "payload": json.dumps(b.get("payload", {}), ensure_ascii=False),
                },
                "color": b.get("color", "secondary"),
            })
        buttons.append(btn_row)
    return json.dumps({"inline": True, "buttons": buttons}, ensure_ascii=False)


def make_unmute_keyboard(tid: int, chat_id: int) -> str:
    return make_inline_keyboard([[{
        "label": "🔊 СНЯТЬ МУТ",
        "color": "positive",
        "payload": {"cmd": "unmute", "tid": tid, "chat_id": chat_id},
    }]])


def make_unban_keyboard(tid: int, chat_id: int, scope: str = "local") -> str:
    return make_inline_keyboard([[{
        "label": "✅ СНЯТЬ БАН",
        "color": "positive",
        "payload": {"cmd": "unban", "tid": tid, "chat_id": chat_id, "scope": scope},
    }]])


def make_kickall_confirm_keyboard(chat_id: int, executor_uid: int) -> str:
    return make_inline_keyboard([[{
        "label": "✅ ПОДТВЕРЖДАЮ",
        "color": "negative",
        "payload": {"cmd": "kickall_confirm", "chat_id": chat_id, "executor_uid": executor_uid},
    }]])


def callback_answer(event_id: str, peer_id: int, user_id: int, text: str = "") -> None:
    """Отвечает на callback-событие (подтверждает нажатие кнопки)."""
    try:
        vk.messages.sendMessageEventAnswer(
            event_id=event_id,
            user_id=user_id,
            peer_id=peer_id,
            event_data=json.dumps({"type": "show_snackbar", "text": text or "✅"}, ensure_ascii=False),
        )
    except Exception as e:
        log.warning(f"callback_answer error: {e}")


def handle_callback_event(event) -> None:
    """Обрабатывает нажатия инлайн-кнопок (MESSAGE_EVENT)."""
    try:
        # vk_api может вернуть объект с атрибутами или словарь
        raw = event.object
        if isinstance(raw, dict):
            peer_id     = raw.get("peer_id")
            user_id     = raw.get("user_id")
            event_id    = raw.get("event_id")
            raw_payload = raw.get("payload", {})
        else:
            peer_id     = getattr(raw, "peer_id", None)
            user_id     = getattr(raw, "user_id", None)
            event_id    = getattr(raw, "event_id", None)
            raw_payload = getattr(raw, "payload", {})

        if not peer_id or not user_id or not event_id:
            log.warning(f"[CALLBACK] Неполные данные события: {event.raw}")
            return

        if isinstance(raw_payload, str):
            try:
                payload = json.loads(raw_payload)
            except Exception:
                payload = {}
        elif isinstance(raw_payload, dict):
            payload = raw_payload
        else:
            payload = {}

        cmd = payload.get("cmd")

        if cmd == "unmute":
            tid     = payload.get("tid")
            chat_id = payload.get("chat_id")
            if not tid or not chat_id:
                callback_answer(event_id, peer_id, user_id, "❌ Ошибка данных")
                return
            clicker_prio = PE.get(user_id, chat_id)
            t_prio       = PE.get(tid, chat_id)
            if clicker_prio <= PE.MEMBER or not PE.can_punish(clicker_prio, t_prio):
                callback_answer(event_id, peer_id, user_id, "❌ Недостаточно прав")
                return
            with _db_lock:
                db.remove_mute(tid, chat_id)
                db.log_action(chat_id, user_id, "unmute", tid, "снят через кнопку")
            t_name = get_name(tid)
            send(chat_id, f"🔊 [id{tid}|{t_name}] размучен.")
            callback_answer(event_id, peer_id, user_id, "🔊 Мут снят")

        elif cmd == "unban":
            tid     = payload.get("tid")
            chat_id = payload.get("chat_id")
            scope   = payload.get("scope", "local")
            if not tid or not chat_id:
                callback_answer(event_id, peer_id, user_id, "❌ Ошибка данных")
                return
            clicker_prio = PE.get(user_id, chat_id)
            t_prio       = PE.get(tid, chat_id)
            if clicker_prio <= PE.MEMBER or not PE.can_punish(clicker_prio, t_prio):
                callback_answer(event_id, peer_id, user_id, "❌ Недостаточно прав")
                return
            if scope == "global":
                clicker_prio = PE.get(user_id, chat_id)
                if not can_use_cmd(chat_id, "syncunban", clicker_prio, PE.CREATOR):
                    callback_answer(event_id, peer_id, user_id, "❌ Недостаточно прав")
                    return
                with _db_lock:
                    all_chats = db.get_all_chats()
                unbanned = 0
                for c in all_chats:
                    with _db_lock:
                        if db.remove_ban(tid, c["chat_id"]):
                            unbanned += 1
                t_name = get_name(tid)
                send(chat_id, f"✅ [id{tid}|{t_name}] глобально разбанен ({unbanned} бесед).")
            else:
                with _db_lock:
                    ok = db.remove_ban(tid, chat_id)
                t_name = get_name(tid)
                if ok:
                    with _db_lock:
                        db.log_action(chat_id, user_id, "unban", tid, "разбанен через кнопку")
                    send(chat_id, f"✅ [id{tid}|{t_name}] разбанен.")
                else:
                    send(chat_id, f"ℹ️ [id{tid}|{t_name}] не забанен.")
            callback_answer(event_id, peer_id, user_id, "✅ Бан снят")

        elif cmd == "kickall_confirm":
            chat_id      = payload.get("chat_id")
            executor_uid = payload.get("executor_uid")
            if not chat_id or not executor_uid:
                callback_answer(event_id, peer_id, user_id, "❌ Ошибка данных")
                return
            # Только тот, кто выдал команду, может подтвердить
            if user_id != executor_uid:
                callback_answer(event_id, peer_id, user_id, "❌ Только выдавший команду может подтвердить")
                return
            clicker_prio = PE.get(user_id, chat_id)
            if not can_use_cmd(chat_id, "kickall", clicker_prio, PE.OWNER):
                callback_answer(event_id, peer_id, user_id, "❌ Недостаточно прав")
                return
            callback_answer(event_id, peer_id, user_id, "⏳ Выполняю...")
            try:
                resp       = vk.messages.getConversationMembers(peer_id=chat_id)
                member_ids = [item["member_id"] for item in resp.get("items", []) if item.get("member_id", 0) > 0]
            except Exception as e:
                send(chat_id, f"❌ Не удалось получить список участников: {e}")
                return
            kicked  = 0
            skipped = 0
            for mid in member_ids:
                # Создатель бота никогда не кикается
                if mid == CREATOR_ID:
                    skipped += 1
                    continue
                if clicker_prio == PE.CREATOR:
                    # Создатель кикает всех кроме себя
                    if mid == user_id:
                        skipped += 1
                        continue
                else:
                    # Владелец кикает всех кроме себя и создателя
                    if mid == user_id:
                        skipped += 1
                        continue
                ok_k, _ = kick(chat_id, mid)
                if ok_k:
                    kicked += 1
                    time.sleep(0.1)
                else:
                    skipped += 1
            send(chat_id,
                f"👢 KickAll завершён!\n"
                f"✅ Кикнуто: {kicked}\n"
                f"⏭ Пропущено: {skipped}"
            )
            send_log(chat_id, f"👢 KickAll | кикнуто {kicked} | пропущено {skipped} | кто: id{user_id}")

        else:
            callback_answer(event_id, peer_id, user_id, "❓ Неизвестная команда")

    except Exception as e:
        log.error(f"[CALLBACK] {e}")


def send_log(chat_id: int, text: str) -> None:
    with _db_lock:
        chat = db.get_chat(chat_id)
    if chat and chat.get("log_peer_id"):
        send(chat["log_peer_id"], text)


def get_vk_chat_owner(chat_id: int) -> Optional[int]:
    """Возвращает реального владельца беседы по данным VK API.

    Важно: при ошибке API возвращает None и никогда не подставляет ID
    пользователя, который добавил бота. Это исключает автоматическую
    выдачу прав владельца пригласившему участнику.
    """
    try:
        response = vk.messages.getConversationMembers(peer_id=chat_id)
        for item in response.get("items", []):
            member_id = item.get("member_id")
            if item.get("is_owner") and isinstance(member_id, int) and member_id > 0:
                return member_id
        log.warning(f"[OWNER] VK API не вернул владельца беседы peer_id={chat_id}")
    except Exception as e:
        log.warning(f"[OWNER] Не удалось определить владельца peer_id={chat_id}: {e}")
    return None


_name_cache: Dict[int, tuple] = {}
_name_cache_lock = threading.Lock()


def get_name(user_id: int) -> str:
    with _name_cache_lock:
        cached = _name_cache.get(user_id)
    if cached and int(time.time()) - cached[1] < 3600:
        return cached[0]
    try:
        if user_id < 0:
            r = vk.groups.getById(group_id=abs(user_id))
            if r:
                name = r[0].get("name", f"club{abs(user_id)}")
                with _name_cache_lock:
                    _name_cache[user_id] = (name, int(time.time()))
                return name
        else:
            r = vk.users.get(user_ids=user_id, fields="first_name,last_name")
            if r:
                name = f"{r[0]['first_name']} {r[0]['last_name']}"
                with _name_cache_lock:
                    _name_cache[user_id] = (name, int(time.time()))
                return name
    except Exception:
        pass
    return f"club{abs(user_id)}" if user_id < 0 else f"id{user_id}"


def resolve_id(raw: str) -> Optional[int]:
    raw = raw.strip()
    # Прямой числовой ID (положительный — пользователь, отрицательный — группа)
    if raw.lstrip("-").isdigit():
        return int(raw)
    # Упоминание пользователя [id123|имя]
    m = re.match(r"\[id(\d+)\|.+?\]", raw)
    if m:
        return int(m.group(1))
    # Упоминание группы/бота [club123|имя]
    m = re.match(r"\[club(\d+)\|.+?\]", raw)
    if m:
        return -int(m.group(1))
    # vk.com/id123
    m = re.match(r"(?:https?://)?vk\.com/id(\d+)", raw)
    if m:
        return int(m.group(1))
    # vk.com/club123 или vk.com/public123
    m = re.match(r"(?:https?://)?vk\.com/(?:club|public)(\d+)", raw)
    if m:
        return -int(m.group(1))
    # vk.com/screenname или @screenname — определяем через API
    m = re.match(r"(?:https?://)?vk\.com/([a-zA-Z0-9_.]+)", raw) or re.match(r"@([a-zA-Z0-9_]+)", raw)
    if m:
        try:
            r = vk.utils.resolveScreenName(screen_name=m.group(1))
            if r and r.get("object_id"):
                obj_type = r.get("type", "user")
                oid = r["object_id"]
                # Для групп/пабликов/ботов возвращаем отрицательный ID
                if obj_type in ("group", "public", "application"):
                    return -oid
                return oid
        except Exception:
            pass
    return None


def fmt_mention(user_id: int, name: str) -> str:
    """Формирует правильное VK-упоминание: для юзеров [id...|имя], для групп/ботов [club...|имя]."""
    if user_id < 0:
        return f"[club{abs(user_id)}|{name}]"
    return f"[id{user_id}|{name}]"


def _int(s: str) -> Optional[int]:
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return None


def ts_ban(days: Optional[int]) -> int:
    return int(time.time()) + days * 86400 if days and days > 0 else 0


def ts_mute(minutes: Optional[int]) -> int:
    m = minutes if minutes and minutes > 0 else DEFAULT_MUTE_MINUTES
    return int(time.time()) + m * 60


def fmt_ts(ts) -> str:
    if not ts or ts == 0:
        return "навсегда"
    try:
        tz = datetime.timezone(datetime.timedelta(hours=TZ_OFFSET_HOURS))
        return datetime.datetime.fromtimestamp(int(ts), tz=tz).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "?"


def fmt_dur(secs: int) -> str:
    if secs <= 0:
        return "меньше минуты"
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}д")
    if h: parts.append(f"{h}ч")
    if m: parts.append(f"{m}м")
    if s and not d and not h: parts.append(f"{s}с")
    return " ".join(parts) or "меньше минуты"


def fmt_remaining(until_ts: int) -> str:
    if until_ts == 0:
        return "навсегда"
    left = until_ts - int(time.time())
    return "истёк" if left <= 0 else f"ещё {fmt_dur(left)}"


def fmt_uptime(secs: int) -> str:
    return fmt_dur(secs)


def warn_bar(count: int, max_w: int) -> str:
    count = max(0, min(count, max_w))
    return f"[{'■' * count}{'□' * (max_w - count)}]"


def progress_bar(current: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "[" + "░" * width + "]"
    filled = int(width * current / total)
    filled = min(filled, width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def is_cmd(txt: str, *cmds) -> bool:
    t = txt.strip().lower()
    for c in cmds:
        p = (PREFIX + c).lower()
        if t == p or t.startswith(p + " "):
            return True
    return False


def get_args(txt: str, *cmds) -> str:
    t = txt.strip()
    for c in cmds:
        p = PREFIX + c
        if t.lower().startswith(p.lower()):
            return t[len(p):].strip()
    return ""


# Команды, которые нельзя делегировать никому. /setcmd оставляем защищённой,
# иначе пользователь с выданным доступом сможет сам менять права всех команд.
_NEVER_OVERRIDABLE_COMMANDS = {"setcmd", "setagent", "delagent"}

# Опасные системные команды разрешено переопределять ТОЛЬКО создателю бота.
# Без явного /setcmd они по-прежнему требуют свой стандартный приоритет.
_CREATOR_MANAGED_COMMANDS = {
    "kickall", "syncban", "syncunban",
    "blockcmd", "unblockcmd",
    "createunity", "addunity", "removeunity", "deleteunity",
    "syskick", "sysrole", "giverub", "sellbiz", "agenthelp",
}

# Алиасы хранят одно общее правило доступа с основной командой.
_COMMAND_ALIASES = {
    "synccmd": "blockcmd",
    "syncuncmd": "unblockcmd",
    "runity": "removeunity",
    "unites": "unity",
    "whois": "find",
}


def canonical_cmd_name(cmd_name: str) -> str:
    name = cmd_name.lower().strip().lstrip("/")
    return _COMMAND_ALIASES.get(name, name)


def can_use_cmd(chat_id: int, cmd_name: str, user_prio: int, default_min: int) -> bool:
    cmd_name = canonical_cmd_name(cmd_name)
    # Создатель всегда сохраняет полный доступ, даже если случайно запретил
    # команду для приоритета 105 через /setcmd.
    if user_prio >= PE.CREATOR:
        return True
    # /setcmd нельзя открыть через другое правило /setcmd.
    if cmd_name in _NEVER_OVERRIDABLE_COMMANDS:
        return user_prio >= default_min
    # В том числе для syncban/kickall/blockcmd сначала учитываем правило,
    # которое создатель записал в cmd_overrides.
    with _db_lock:
        override = db.get_cmd_override(chat_id, cmd_name, user_prio)
    if override is not None:
        return override
    return user_prio >= default_min



# ══════════════════════════════════════════════════════════════════════════════
#  ИГРОВОЙ МОДУЛЬ: СТРАНЫ, ФРАКЦИИ, АРМИЯ И ЧЁРНЫЙ РЫНОК
# ══════════════════════════════════════════════════════════════════════════════

GAME_COUNTRY_CREATE_COST = 5_000_000
GAME_COUNTRY_DELETE_COST = 1_000_000
GAME_ATTACK_COOLDOWN = 6 * 3600
GAME_FACTION_SWITCH_COOLDOWN = 12 * 3600

GAME_ARMY = {
    "пехота": ("infantry", 500, "🪖 Пехота"),
    "пехоты": ("infantry", 500, "🪖 Пехота"),
    "пешеход": ("infantry", 500, "🪖 Пехота"),
    "пешеходы": ("infantry", 500, "🪖 Пехота"),
    "танк": ("tanks", 50_000, "🛡 Танк"),
    "танки": ("tanks", 50_000, "🛡 Танк"),
    "самолет": ("planes", 250_000, "✈️ Самолёт"),
    "самолеты": ("planes", 250_000, "✈️ Самолёт"),
    "самолёт": ("planes", 250_000, "✈️ Самолёт"),
    "самолёты": ("planes", 250_000, "✈️ Самолёт"),
}

GAME_FACTIONS = {
    "military": ("🪖 ВЧ", "Прибыль: /тренировка"),
    "police": ("👮 Полиция", "Прибыль: /арест"),
    "bandits": ("🕶 Бандиты", "Прибыль: /контрабанда и /украсть"),
    "hospital": ("🏥 Больница", "Спокойная фракция без денежной прибыли"),
}

GAME_FACTION_ALIASES = {
    "вч": "military", "военная часть": "military", "воинская часть": "military",
    "полиция": "police", "мвд": "police",
    "бандиты": "bandits", "банда": "bandits",
    "больница": "hospital", "медики": "hospital", "врачи": "hospital",
}

GAME_MARKET_ITEMS = {
    1: ("🚬 Контрабандные сигареты", 758, 40),
    2: ("🍺 Нелегальный алкоголь", 2_020, 24),
    3: ("📱 Серая электроника", 11_566, 12),
    4: ("🔫 Огнестрельное оружие", 45_175, 7),
    5: ("📄 Поддельные паспорта", 57_972, 6),
    6: ("💎 Краденые бриллианты", 119_067, 4),
    7: ("🏺 Антиквариат", 258_926, 3),
    8: ("💊 Рецептурные препараты", 7_881, 14),
}

GAME_BUSINESS_INCOME_COOLDOWN = 24 * 3600
GAME_BUSINESS_PRODUCT_COST = 100
GAME_BUSINESS_PRODUCT_USE = 100
GAME_BUSINESS_SELL_PERCENT = 70
GAME_BUSINESSES = {
    1: {"name": "🎰 Казино", "cost": 10_000_000, "income": 7_000_000},
    2: {"name": "⛽ АЗС", "cost": 3_000_000, "income": 1_000_999},
    3: {"name": "🏪 Магазин", "cost": 2_000_000, "income": 999_999},
}

GAME_WEAPON_CASE_COST = 500_000
GAME_WEAPONS = {
    1: {"name": "🔫 Пистолет", "rep": 1, "weight": 42},
    2: {"name": "🔫 Пистолет-пулемёт", "rep": 2, "weight": 27},
    3: {"name": "🪖 Штурмовая винтовка", "rep": 3, "weight": 17},
    4: {"name": "🎯 Снайперская винтовка", "rep": 5, "weight": 9},
    5: {"name": "💥 Пулемёт", "rep": 7, "weight": 4},
    6: {"name": "✨ Золотой автомат", "rep": 10, "weight": 1},
}

# Правительство страны. Должность хранится в game_country_members.rank.
GAME_GOVERNMENT_TITLES = {
    "prime_minister": "🤝 Премьер-министр",
    "finance_minister": "💰 Министр финансов",
    "defense_minister": "🪖 Министр обороны",
    "interior_minister": "👮 Министр внутренних дел",
    "health_minister": "🏥 Министр здравоохранения",
}

GAME_GOVERNMENT_ALIASES = {
    "премьер": "prime_minister",
    "премьер-министр": "prime_minister",
    "премьер министр": "prime_minister",
    "заместитель": "prime_minister",
    "зам": "prime_minister",
    "финансы": "finance_minister",
    "министр финансов": "finance_minister",
    "министрфинансов": "finance_minister",
    "минфин": "finance_minister",
    "оборона": "defense_minister",
    "министр обороны": "defense_minister",
    "министрообороны": "defense_minister",
    "министробороны": "defense_minister",
    "минобороны": "defense_minister",
    "мвд": "interior_minister",
    "министр мвд": "interior_minister",
    "министрмвд": "interior_minister",
    "министр внутренних дел": "interior_minister",
    "здрав": "health_minister",
    "здравоохранение": "health_minister",
    "минздрав": "health_minister",
    "министр здравоохранения": "health_minister",
    "министрздравоохранения": "health_minister",
}

# Полномочия правительства. Назначение/снятие правительства и удаление страны
# всегда остаются только у правителя страны.
GAME_GOVERNMENT_PERMISSIONS = {
    "prime_minister": {"recruitment", "citizens", "treasury", "army", "attack"},
    "finance_minister": {"treasury"},
    "defense_minister": {"army", "attack"},
    "interior_minister": {"recruitment", "citizens"},
    "health_minister": set(),
}


def site_create_login_code(user_id: int, lifetime_minutes: int = 10) -> str:
    code = f"{secrets.randbelow(100_000_000):08d}"
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    now = int(time.time())
    with _db_lock:
        db.ex("DELETE FROM site_login_codes WHERE user_id=? OR expires_at<?", (user_id, now))
        db.ex(
            "INSERT INTO site_login_codes(code_hash,user_id,expires_at) VALUES(?,?,?)",
            (code_hash, user_id, now + lifetime_minutes * 60),
        )
    return code


def game_money(value: int) -> str:
    return f"{int(value):,}".replace(",", " ") + " ₽"


def parse_game_amount(raw: str) -> Optional[int]:
    s = str(raw or "").strip().lower().replace("_", "").replace(" ", "")
    if not s:
        return None
    mult = 1
    if s[-1:] in {"к", "k"}:
        mult, s = 1_000, s[:-1]
    elif s[-1:] in {"м", "m"}:
        mult, s = 1_000_000, s[:-1]
    try:
        if mult != 1:
            value = int(float(s.replace(",", ".")) * mult)
        else:
            value = int(s.replace(".", "").replace(",", ""))
        return value if value > 0 else None
    except (ValueError, TypeError):
        return None


def game_is_agent(user_id: int) -> bool:
    if user_id == CREATOR_ID:
        return True
    with _db_lock:
        return db.one("SELECT 1 FROM game_agents WHERE user_id=?", (user_id,)) is not None


def game_ensure_wallet(user_id: int) -> None:
    db.ex("INSERT INTO game_wallets(user_id) VALUES(?) ON CONFLICT(user_id) DO NOTHING", (user_id,))


def game_balance(user_id: int) -> int:
    with _db_lock:
        game_ensure_wallet(user_id)
        row = db.one("SELECT balance FROM game_wallets WHERE user_id=?", (user_id,))
        return int(row["balance"]) if row else 0


def game_add_money(user_id: int, amount: int) -> int:
    with _db_lock:
        game_ensure_wallet(user_id)
        db.ex(
            "UPDATE game_wallets SET balance=MAX(0,balance+?),updated_at=? WHERE user_id=?",
            (int(amount), int(time.time()), user_id),
        )
        row = db.one("SELECT balance FROM game_wallets WHERE user_id=?", (user_id,))
        return int(row["balance"]) if row else 0


def game_take_money(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return False
    with _db_lock:
        game_ensure_wallet(user_id)
        cur = db.ex(
            "UPDATE game_wallets SET balance=balance-?,updated_at=? WHERE user_id=? AND balance>=?",
            (amount, int(time.time()), user_id, amount),
        )
        return cur.rowcount > 0


def game_country_by_owner(user_id: int) -> Optional[dict]:
    with _db_lock:
        return db.one("SELECT * FROM game_countries WHERE owner_id=?", (user_id,))


def game_country_by_id(country_id: int) -> Optional[dict]:
    with _db_lock:
        return db.one("SELECT * FROM game_countries WHERE id=?", (country_id,))


def game_country_membership(user_id: int) -> Optional[dict]:
    with _db_lock:
        return db.one(
            "SELECT c.*,m.rank AS member_rank,m.joined_at AS citizenship_since "
            "FROM game_country_members m JOIN game_countries c ON c.id=m.country_id "
            "WHERE m.user_id=?",
            (user_id,),
        )


def game_country_member_count(country_id: int) -> int:
    with _db_lock:
        row = db.one("SELECT COUNT(*) AS c FROM game_country_members WHERE country_id=?", (country_id,))
        return int(row["c"]) if row else 0


def game_country_member(user_id: int) -> Optional[dict]:
    with _db_lock:
        return db.one("SELECT * FROM game_country_members WHERE user_id=?", (user_id,))


def game_government_rank(raw: str) -> Optional[str]:
    value = re.sub(r"\s+", " ", str(raw or "").strip().lower().replace("ё", "е"))
    # Сначала обычные алиасы, затем вариант с восстановленной буквой ё не нужен:
    # все ключи ниже нормализуются тем же способом.
    normalized = {
        re.sub(r"\s+", " ", key.strip().lower().replace("ё", "е")): rank
        for key, rank in GAME_GOVERNMENT_ALIASES.items()
    }
    return normalized.get(value)


def game_government_title(rank: str) -> str:
    if rank == "owner":
        return "👑 Правитель"
    if rank == "citizen":
        return "👤 Гражданин"
    return GAME_GOVERNMENT_TITLES.get(rank, rank)


def game_can_govern(user_id: int, country: Optional[dict], permission: str) -> bool:
    if not country:
        return False
    if int(country.get("owner_id", 0)) == int(user_id):
        return True
    country_id = int(country.get("id") or country.get("country_id") or 0)
    if not country_id:
        return False
    with _db_lock:
        member = db.one(
            "SELECT rank FROM game_country_members WHERE user_id=? AND country_id=?",
            (user_id, country_id),
        )
    if not member:
        return False
    return permission in GAME_GOVERNMENT_PERMISSIONS.get(member.get("rank", "citizen"), set())


def game_business_by_user(user_id: int) -> Optional[dict]:
    with _db_lock:
        return db.one("SELECT * FROM game_businesses WHERE user_id=?", (user_id,))


def game_weapon_total(user_id: int) -> int:
    with _db_lock:
        row = db.one("SELECT COALESCE(SUM(qty),0) AS c FROM game_weapons WHERE user_id=?", (user_id,))
        return int(row["c"]) if row else 0


def game_army_power(country: dict) -> int:
    return int(country.get("infantry", 0)) + int(country.get("tanks", 0)) * 100 + int(country.get("planes", 0)) * 500


def game_country_card(country: dict) -> str:
    owner_id = int(country["owner_id"])
    owner = fmt_mention(owner_id, get_name(owner_id))
    citizens = game_country_member_count(int(country["id"]))
    recruitment = "открыт" if int(country.get("is_open", 0)) else "закрыт"
    return (
        f"🏳 СТРАНА #{country['id']}: {country['name']}\n"
        f"👑 Правитель: {owner}\n"
        f"👥 Граждане: {citizens} | Набор: {recruitment}\n"
        f"💰 Казна: {game_money(int(country.get('treasury', 0)))}\n"
        f"⭐ Репутация: {int(country.get('reputation', 0)):,}\n"
        f"──────────────────\n"
        f"🪖 Пехота: {country['infantry']:,}\n"
        f"🛡 Танки: {country['tanks']:,}\n"
        f"✈️ Самолёты: {country['planes']:,}\n"
        f"⚔️ Военная сила: {game_army_power(country):,}\n"
        f"🏆 Победы: {country['wins']} | 💀 Поражения: {country['losses']}"
    ).replace(",", " ")


def game_get_cooldown(user_id: int, action: str, seconds: int) -> int:
    with _db_lock:
        row = db.one("SELECT last_use FROM game_cooldowns WHERE user_id=? AND action=?", (user_id, action))
    if not row:
        return 0
    return max(0, int(row["last_use"]) + seconds - int(time.time()))


def game_use_cooldown(user_id: int, action: str) -> None:
    with _db_lock:
        db.ex(
            "INSERT INTO game_cooldowns(user_id,action,last_use) VALUES(?,?,?) "
            "ON CONFLICT(user_id,action) DO UPDATE SET last_use=excluded.last_use",
            (user_id, action, int(time.time())),
        )


def game_market_rows() -> List[dict]:
    bucket = int(time.time()) // (3 * 3600)
    rows = []
    with _db_lock:
        for item_id, (name, base_price, max_stock) in GAME_MARKET_ITEMS.items():
            row = db.one("SELECT * FROM game_market_state WHERE item_id=?", (item_id,))
            if not row or int(row.get("bucket", -1)) != bucket:
                rng = random.Random(f"{GROUP_ID}:{bucket}:{item_id}")
                buy_price = max(1, int(base_price * rng.uniform(0.78, 1.28)))
                stock = rng.randint(0, max_stock)
                db.ex(
                    "INSERT INTO game_market_state(item_id,buy_price,stock,bucket) VALUES(?,?,?,?) "
                    "ON CONFLICT(item_id) DO UPDATE SET buy_price=excluded.buy_price,stock=excluded.stock,bucket=excluded.bucket",
                    (item_id, buy_price, stock, bucket),
                )
                row = {"item_id": item_id, "buy_price": buy_price, "stock": stock, "bucket": bucket}
            row = dict(row)
            row["name"] = name
            row["sell_price"] = max(1, int(int(row["buy_price"]) * 0.8))
            rows.append(row)
    return rows


def game_inventory_qty(user_id: int, item_id: int) -> int:
    with _db_lock:
        row = db.one("SELECT qty FROM game_inventory WHERE user_id=? AND item_id=?", (user_id, item_id))
        return int(row["qty"]) if row else 0


def game_change_inventory(user_id: int, item_id: int, delta: int) -> bool:
    with _db_lock:
        row = db.one("SELECT qty FROM game_inventory WHERE user_id=? AND item_id=?", (user_id, item_id))
        current = int(row["qty"]) if row else 0
        new_qty = current + int(delta)
        if new_qty < 0:
            return False
        db.ex(
            "INSERT INTO game_inventory(user_id,item_id,qty) VALUES(?,?,?) "
            "ON CONFLICT(user_id,item_id) DO UPDATE SET qty=excluded.qty",
            (user_id, item_id, new_qty),
        )
        return True


def normalize_game_peer(raw: int) -> int:
    return raw if raw >= 2_000_000_000 else 2_000_000_000 + raw


def check_cooldown(user_id: int, chat_id: int, cmd: str, seconds: int) -> int:
    if seconds <= 0:
        return 0
    with _db_lock:
        last = db.get_cooldown(user_id, chat_id, cmd)
    remaining = int(last + seconds - time.time())
    return max(0, remaining)


def use_cooldown(user_id: int, chat_id: int, cmd: str) -> None:
    with _db_lock:
        db.set_cooldown(user_id, chat_id, cmd)


# ── Парсинг аргументов ────────────────────────────────────────────────────────

def get_target(raw: str, reply_from_id: Optional[int] = None) -> tuple:
    # Принимаем реплай как на пользователей (>0), так и на ботов/сообщества (<0)
    if reply_from_id and reply_from_id != 0:
        return reply_from_id, raw.strip()
    if raw:
        parts = raw.split(maxsplit=1)
        tid = resolve_id(parts[0])
        if tid:
            return tid, (parts[1].strip() if len(parts) > 1 else "")
    return None, raw.strip()


def parse_ban_args(raw: str, reply_from_id: Optional[int] = None):
    tid, rest = get_target(raw, reply_from_id)
    if not tid:
        return None, None, DEFAULT_BAN_REASON
    tokens = rest.split(maxsplit=1)
    days, reason = None, DEFAULT_BAN_REASON
    if tokens:
        d = _int(tokens[0])
        if d and d > 0:
            days = d
            reason = tokens[1].strip() if len(tokens) > 1 else DEFAULT_BAN_REASON
        else:
            reason = rest.strip() or DEFAULT_BAN_REASON
    return tid, days, reason


_UNIT_WORDS = {"д","дн","дня","дней","день","ч","ч.","час","часа","часов","м","мин","мин.","минут","минуту","с","сек","секунд","d","h","m","s"}
_TIME_UNITS = [
    (("д","дн","день","дней","дня","d"),   1440),
    (("ч","ч.","час","часа","часов","h"),    60),
    (("м","мин","минут","минуту","мин.","m"),  1),
    (("с","сек","секунд","s"),             1/60),
]


def parse_duration_minutes(token: str) -> Optional[int]:
    token = token.strip().lower()
    for suffixes, mult in _TIME_UNITS:
        for suf in sorted(suffixes, key=len, reverse=True):
            if token.endswith(suf) and len(token) > len(suf):
                try:
                    return max(1, int(float(token[:-len(suf)].replace(",", ".")) * mult))
                except ValueError:
                    pass
    try:
        v = int(token)
        return v if v > 0 else None
    except ValueError:
        return None


def parse_mute_args(raw: str, reply_from_id: Optional[int] = None):
    tid, rest = get_target(raw, reply_from_id)
    if not tid:
        return None, None, DEFAULT_MUTE_REASON
    tokens = rest.split(maxsplit=2)
    minutes, reason = None, DEFAULT_MUTE_REASON
    if not tokens:
        return tid, minutes, reason
    m2 = parse_duration_minutes(tokens[0])
    if m2 is not None:
        consumed = 1
        if tokens[0].strip().lstrip("-").isdigit() and len(tokens) > 1:
            t2 = tokens[1].strip().lower()
            if t2 in _UNIT_WORDS:
                m3 = parse_duration_minutes(tokens[0] + t2)
                if m3 is not None:
                    m2 = m3
                    consumed = 2
        minutes = m2
        reason = " ".join(tokens[consumed:]).strip() or DEFAULT_MUTE_REASON
    else:
        reason = rest.strip() or DEFAULT_MUTE_REASON
    return tid, minutes, reason


def parse_target_reason(raw: str, default: str = DEFAULT_KICK_REASON, reply_from_id: Optional[int] = None):
    tid, rest = get_target(raw, reply_from_id)
    return tid, (rest.strip() if rest else default)


def parse_target_text(raw: str, reply_from_id: Optional[int] = None):
    tid, rest = get_target(raw, reply_from_id)
    return tid, (rest.strip() if rest else None)


def parse_target_role(raw: str, reply_from_id: Optional[int] = None):
    tid, rest = get_target(raw, reply_from_id)
    if not tid:
        return None, None, None
    role_str = rest.strip()
    is_number = bool(re.fullmatch(r"-?\d+", role_str))
    rp = int(role_str) if is_number else None
    return tid, rp, (None if is_number else role_str)


def parse_role_args(raw: str):
    m = re.match(r"^(\d+)\s+(.+)$", raw.strip())
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, None


def parse_schedule_args(raw: str):
    """
    Формат: [время/дата] [повтор?] | [текст]
    Время: +30м, +1ч, +1д, или HH:MM, или DD.MM HH:MM
    Повтор: каждые Nм/ч/д
    """
    if "|" not in raw:
        return None, None, None
    time_part, text = raw.split("|", 1)
    text = text.strip()
    time_part = time_part.strip()
    repeat_sec = 0
    repeat_match = re.search(r"каждые?\s+(\S+)", time_part, re.I)
    if repeat_match:
        r_mins = parse_duration_minutes(repeat_match.group(1))
        if r_mins:
            repeat_sec = r_mins * 60
        time_part = time_part[:repeat_match.start()].strip()
    now = int(time.time())
    tz  = datetime.timezone(datetime.timedelta(hours=TZ_OFFSET_HOURS))
    rel_m = parse_duration_minutes(time_part) if time_part.startswith("+") else None
    if rel_m:
        send_at = now + rel_m * 60
    else:
        try:
            if re.match(r"^\d{2}:\d{2}$", time_part):
                dt = datetime.datetime.now(tz).replace(
                    hour=int(time_part[:2]), minute=int(time_part[3:5]), second=0
                )
                if dt.timestamp() <= now:
                    dt += datetime.timedelta(days=1)
                send_at = int(dt.timestamp())
            elif re.match(r"^\d{2}\.\d{2}\s+\d{2}:\d{2}$", time_part):
                dm, tm = time_part.split()
                day, mon = int(dm[:2]), int(dm[3:5])
                hour, minute = int(tm[:2]), int(tm[3:5])
                yr = datetime.datetime.now(tz).year
                dt = datetime.datetime(yr, mon, day, hour, minute, tzinfo=tz)
                if dt.timestamp() <= now:
                    dt = dt.replace(year=yr + 1)
                send_at = int(dt.timestamp())
            else:
                return None, None, None
        except Exception:
            return None, None, None
    return send_at, repeat_sec, text


# ── VK операции ───────────────────────────────────────────────────────────────

def kick(chat_id: int, user_id: int) -> tuple:
    """Возвращает (success: bool, reason: str|None)."""
    try:
        vk.messages.removeChatUser(chat_id=chat_id - 2_000_000_000, member_id=user_id)
        return True, None
    except Exception as e:
        err = str(e).lower()
        log.warning(f"kick error chat={chat_id} user={user_id}: {e}")
        if "not in chat" in err or "не состоит" in err or "user not found" in err or "member not found" in err:
            return False, "not_in_chat"
        return False, "error"


def delete_msg(message_id: int, peer_id: Optional[int] = None, cmid: Optional[int] = None) -> bool:
    if not message_id and not cmid:
        return False
    # Способ 1: conversation_message_id + peer_id (правильный для бесед)
    if peer_id and cmid:
        try:
            vk.messages.delete(peer_id=peer_id, cmids=cmid, delete_for_all=1)
            return True
        except Exception as e1:
            log.debug(f"delete_msg cmids fail: {e1}")
    # Способ 2: глобальный message_id
    if message_id:
        try:
            vk.messages.delete(message_ids=message_id, delete_for_all=1)
            return True
        except Exception as e2:
            log.debug(f"delete_msg msg_ids fail: {e2}")
    # Способ 3: только peer_id + cmid без fallback
    if peer_id and message_id:
        try:
            vk.messages.delete(peer_id=peer_id, cmids=message_id, delete_for_all=1)
            return True
        except Exception as e3:
            log.debug(f"delete_msg cmids(global) fail: {e3}")
    return False


def unity_broadcast(unity_id: int, action_fn) -> tuple:
    with _db_lock:
        chats = db.get_chats_by_unity(unity_id)
    ok = fail = 0
    for c in chats:
        try:
            action_fn(c["chat_id"])
            ok += 1
        except Exception as e:
            log.warning(f"[UNITY] chat {c['chat_id']}: {e}")
            fail += 1
    return ok, fail


def _role_label(m_row: Optional[dict], prio: int, chat_id: Optional[int] = None) -> str:
    if m_row and m_row.get("role_id"):
        with _db_lock:
            r = db.get_role_by_id(m_row["role_id"])
        if r:
            badge = r.get("badge") or ""
            return f"{badge} {r['name']} [{r['priority']}]".strip()
    return PE.role_name(prio)


def extract_domain(url: str) -> str:
    m = re.search(r"(?:https?://)?(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", url)
    return m.group(1).lower() if m else url.lower()


# ── Антиспам ──────────────────────────────────────────────────────────────────

def check_flood(user_id: int, chat_id: int, chat: dict) -> int:
    """Возвращает кол-во минут мута если флуд обнаружен, иначе 0."""
    limit    = chat.get("flood_limit", 0)
    interval = chat.get("flood_interval", 0)
    if not limit or not interval:
        return 0
    now = int(time.time())
    with _db_lock:
        ts_list = db.get_flood_data(user_id, chat_id)
    ts_list = [t for t in ts_list if now - t <= interval]
    ts_list.append(now)
    with _db_lock:
        db.set_flood_data(user_id, chat_id, ts_list[-50:])
    if len(ts_list) > limit:
        return int(chat.get("flood_mute_min", 5) or 5)
    return 0


def check_slowmode(user_id: int, chat_id: int, chat: dict) -> int:
    """Возвращает секунды ожидания, или 0 если разрешено."""
    slowmode = chat.get("slowmode_sec", 0)
    if not slowmode:
        return 0
    with _db_lock:
        sm = db.get_slowmode(user_id, chat_id)
    if not sm:
        return 0
    wait = int(sm["last_msg"] + slowmode - time.time())
    return max(0, wait)


# ── Фильтр нецензурной лексики ────────────────────────────────────────────────

_MAT_PATTERN = re.compile(
    r"\b(х[уy][йиея]|пизд|ёб|еб[ауо]|блядь|сука|мудак|пидор|залуп|ёп|хуй|пиздец|"
    r"хуёв|манда|ёбан|уёб|пиздан|залупа|мудила|педик|шлюх|проститут|ёб твою|"
    r"ёбан[ыао]|хуёв[ыао]|пиздёж|ёбнут|хуясе|ёбат)\w*",
    re.IGNORECASE | re.UNICODE,
)


def has_mat(text: str) -> bool:
    return bool(_MAT_PATTERN.search(text))


def check_blacklist(text: str, blacklist: List[dict]) -> Optional[dict]:
    tl = text.lower()
    for bw in blacklist:
        if bw["word"] in tl:
            return bw
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИК СОБЫТИЙ
# ══════════════════════════════════════════════════════════════════════════════

def on_message(event) -> None:
    try:
        _on_message_inner(event)
    except Exception as e:
        log.error(f"[ON_MESSAGE] Необработанная ошибка: {e}", exc_info=False)


def _on_message_inner(event) -> None:
    obj     = event.object
    msg     = obj.get("message", obj)
    peer_id = msg.get("peer_id", 0)
    from_id = msg.get("from_id", 0)
    text    = (msg.get("text") or "").strip()
    msg_id  = msg.get("id", 0)
    cmid    = msg.get("conversation_message_id", 0)
    action  = msg.get("action")
    attachments = msg.get("attachments", [])

    if peer_id <= 2_000_000_000:
        return

    chat_id = peer_id

    # ── Дедупликация через SQLite — работает между несколькими процессами ─────
    now_ts = int(time.time())
    if msg_id:
        event_key = f"msg:{msg_id}"
    elif action:
        # Для служебных событий без уникального ID — ключ по содержанию + округлённое время
        atype_raw = action.get("type", "")
        actor     = action.get("member_id", from_id)
        bucket    = now_ts // 5  # окно 5 секунд
        event_key = f"act:{chat_id}:{from_id}:{actor}:{atype_raw}:{bucket}"
    else:
        event_key = None

    if event_key:
        if not db.try_claim_event(event_key):
            log.debug(f"[DEDUP] Пропущено дублирующее событие: {event_key}")
            return  # другой процесс уже обрабатывает это событие

    # ── Служебные события ──────────────────────────────────────────────────────
    if action:
        atype = action.get("type", "")

        if atype in ("chat_invite_user", "chat_invite_user_by_link"):
            new_uid = action.get("member_id", from_id)

            # Бот сам добавлен в беседу
            if new_uid == -GROUP_ID:
                # Владельцем назначается только реальный владелец беседы из VK API.
                # Пригласивший бота пользователь больше не получает owner_id автоматически.
                owner_id = get_vk_chat_owner(chat_id)
                try:
                    conv_info = vk.messages.getConversationsById(peer_ids=chat_id)
                    chat_title = ""
                    items = conv_info.get("items", [])
                    if items:
                        chat_settings = items[0].get("chat_settings", {})
                        chat_title = chat_settings.get("title", "")
                except Exception:
                    chat_title = ""

                with _db_lock:
                    db.upsert_chat(chat_id, owner_id, chat_title)
                    # Если запись существовала до исправления, корректируем ошибочно
                    # сохранённого пригласившего только при успешной проверке VK API.
                    if owner_id is not None:
                        db.set_chat_field(chat_id, "owner_id", owner_id)

                inviter_name = get_name(from_id)
                greeting = (
                    f"👋 Привет, беседа «{chat_title}»!\n\n"
                    f"Меня добавил [id{from_id}|{inviter_name}] — спасибо! 🎉\n\n"
                    f"🤖 Я — {BOT_NAME}, ваш умный помощник для управления беседой.\n\n"
                    f"📌 Что я умею:\n"
                    f"  • 🔇 Мут / бан / кик нарушителей\n"
                    f"  • ⚠️ Система варнов\n"
                    f"  • 🚫 Антиспам, антирейд, фильтры слов\n"
                    f"  • 📝 Заметки, триггеры, жалобы\n"
                    f"  • 📊 Топ активности, медиастатистика\n"
                    f"  • ⭐ VIP, значки, временные роли\n"
                    f"  • ⏰ Расписание сообщений\n"
                    f"  • 🔗 Объединения бесед\n\n"
                    f"ℹ️ Напишите /help чтобы увидеть все команды.\n"
                    f"⚙️ Для настройки выдайте мне права администратора!"
                )
                send(chat_id, greeting)
                return

            # Если добавили сообщество (отрицательный ID) — проверяем kickgroups
            if new_uid and new_uid < 0:
                with _db_lock:
                    chat = db.get_chat(chat_id)
                if chat and chat.get("kickgroups"):
                    inviter_prio = PE.get(from_id, chat_id)
                    if inviter_prio >= PE.OWNER:
                        # Владелец/создатель — разрешаем, сообщество остаётся
                        pass
                    else:
                        # Обычный участник — кикаем сообщество, пользователя не трогаем
                        kick(chat_id, new_uid)
                        send(chat_id, f"🚫 Добавление сообществ запрещено — сообщество кикнуто.")
                return

            if new_uid and new_uid > 0:
                with _db_lock:
                    chat = db.get_chat(chat_id)
                    db.upsert_member(new_uid, chat_id)

                if chat and chat.get("antiraid"):
                    kick(chat_id, new_uid)
                    send(chat_id, f"🛡 Антирейд: [id{new_uid}|Пользователь] заблокирован при входе.")
                    return

                if chat and chat.get("unity_id"):
                    with _db_lock:
                        ban = db.get_active_ban(new_uid, None, chat["unity_id"])
                    if ban:
                        kick(chat_id, new_uid)
                        return

                with _db_lock:
                    local_ban = db.get_active_ban(new_uid, chat_id)
                if local_ban:
                    kick(chat_id, new_uid)
                    return

                with _db_lock:
                    syncban = db.get_active_syncban(new_uid)
                if syncban:
                    kick(chat_id, new_uid)
                    return

                if chat and chat.get("autorole_id"):
                    with _db_lock:
                        role = db.get_role_by_id(chat["autorole_id"])
                    if role:
                        with _db_lock:
                            db.update_member_role(new_uid, chat_id, role["id"], role["priority"])

                if chat and chat.get("welcome_text"):
                    wt = chat["welcome_text"].replace("{name}", f"[id{new_uid}|id{new_uid}]")
                    send(chat_id, wt)
            return

        if atype in ("chat_kick_user", "chat_leave_user"):
            left_uid = action.get("member_id", from_id)
            if left_uid and left_uid > 0:
                with _db_lock:
                    chat = db.get_chat(chat_id)
                if chat and chat.get("goodbye_text"):
                    name = get_name(left_uid)
                    gt = chat["goodbye_text"].replace("{name}", f"[id{left_uid}|{name}]")
                    send(chat_id, gt)
            return

    # ── Авторегистрация беседы ─────────────────────────────────────────────────
    with _db_lock:
        chat = db.get_chat(chat_id)
    if not chat:
        try:
            info  = vk.messages.getConversationsById(peer_ids=chat_id)
            title = ""
            if info.get("items"):
                title = info["items"][0].get("chat_settings", {}).get("title", "")
            owner_id = get_vk_chat_owner(chat_id)
            with _db_lock:
                db.upsert_chat(chat_id, owner_id, title)
                db.ensure_builtin_roles(chat_id, CREATOR_ID)
                chat = db.get_chat(chat_id)
        except Exception as e:
            log.debug(f"[INIT] {e}")
            # Без подтверждения VK API owner_id остаётся NULL.
            # Автор первого сообщения не получает права владельца.
            with _db_lock:
                db.upsert_chat(chat_id, None, "")
                db.ensure_builtin_roles(chat_id, CREATOR_ID)
                chat = db.get_chat(chat_id)

    with _db_lock:
        db.upsert_member(from_id, chat_id)
        db.update_last_seen(from_id, chat_id)
        db.increment_chat_msg(chat_id)

    # ── Трекинг медиа ──────────────────────────────────────────────────────────
    for att in attachments:
        atype = att.get("type", "")
        media_map = {"photo": "photos", "video": "videos", "doc": "docs",
                     "sticker": "stickers", "audio_message": "voices"}
        if atype in media_map:
            with _db_lock:
                db.update_media_stats(from_id, chat_id, media_map[atype])

    # ── Проверка бана ──────────────────────────────────────────────────────────
    with _db_lock:
        ban = db.get_active_ban(from_id, chat_id)
    if ban:
        kick(chat_id, from_id)
        return

    if chat and chat.get("unity_id"):
        with _db_lock:
            unity_ban = db.get_active_ban(from_id, None, chat["unity_id"])
        if unity_ban:
            kick(chat_id, from_id)
            return

    # ── Проверка мута ──────────────────────────────────────────────────────────
    with _db_lock:
        member = db.get_member(from_id, chat_id)
    if member and member.get("is_muted"):
        until = member.get("mute_until", 0)
        now   = int(time.time())
        if until == 0 or until > now:
            delete_msg(msg_id, chat_id, cmid=cmid)
            return
        else:
            with _db_lock:
                db.remove_mute(from_id, chat_id)

    prio = PE.get(from_id, chat_id)

    # ── Проверка подписки на публичную страницу ────────────────────────────────
    if chat and chat.get("public_link") and prio < PE.OWNER:
        _pub_grp = chat["public_link"]
        try:
            _is_member = vk.groups.isMember(group_id=_pub_grp, user_id=from_id)
        except Exception:
            _is_member = 1  # при ошибке API не наказываем
        if not _is_member:
            delete_msg(msg_id, chat_id, cmid=cmid)
            send(
                chat_id,
                f"⚠️ [id{from_id}|Участник], чтобы писать в этой беседе, "
                f"необходимо подписаться на сообщество: vk.com/{_pub_grp}\n"
                f"После подписки ты сможешь писать сообщения."
            )
            return

    # ── Фильтры и защита (только для рядовых участников) ──────────────────────
    if not text.startswith(PREFIX) and prio < PE.HELPER:

        # Slowmode
        wait_sec = check_slowmode(from_id, chat_id, chat)
        if wait_sec > 0:
            delete_msg(msg_id, chat_id, cmid=cmid)
            send(chat_id, f"⏳ [id{from_id}|Участник], подожди ещё {fmt_dur(wait_sec)} (медленный режим).")
            return

        # Flood detection
        flood_mute = check_flood(from_id, chat_id, chat)
        if flood_mute:
            delete_msg(msg_id, chat_id, cmid=cmid)
            mute_ts = ts_mute(flood_mute)
            with _db_lock:
                db.set_mute(from_id, chat_id, mute_ts)
            send(chat_id, f"⚠️ [id{from_id}|Участник], обнаружен флуд. Мут на {fmt_dur(flood_mute * 60)}.")
            return

        # Режим тишины
        if chat and chat.get("silence_mode"):
            delete_msg(msg_id, chat_id, cmid=cmid)
            return

        if text:
            # Блеклист слов
            with _db_lock:
                blacklist = db.get_blacklist_words(chat_id)
            bl_hit = check_blacklist(text, blacklist)
            if bl_hit:
                delete_msg(msg_id, chat_id, cmid=cmid)
                if bl_hit["action"] == "warn":
                    with _db_lock:
                        total = db.add_warn(from_id, chat_id, 0, f"Запрещённое слово: {bl_hit['word']}")
                    send(chat_id, f"⚠️ [id{from_id}|Участник], слово запрещено. Варн {warn_bar(total, MAX_WARNS)} {total}/{MAX_WARNS}")
                elif bl_hit["action"] == "mute":
                    mute_ts = ts_mute(10)
                    with _db_lock:
                        db.set_mute(from_id, chat_id, mute_ts)
                    send(chat_id, f"🔇 [id{from_id}|Участник], мут за запрещённое слово. До: {fmt_ts(mute_ts)}")
                elif bl_hit["action"] == "kick":
                    kick(chat_id, from_id)
                    send(chat_id, f"👢 [id{from_id}|Участник] кикнут за запрещённое слово.")
                else:
                    send(chat_id, f"⚠️ [id{from_id}|Участник], сообщение удалено (запрещённое слово).")
                return

            # Фильтр ссылок с вайтлистом
            if chat and chat.get("filter_links"):
                links = re.findall(
                    r"(?:https?://[^\s<>]+|(?:www\.)?(?:vk\.com|t\.me|discord\.gg)/[^\s<>]+)",
                    text,
                    re.I,
                )
                for link in links:
                    domain = extract_domain(link.rstrip(".,!?;:)]}"))
                    with _db_lock:
                        allowed = db.is_link_allowed(chat_id, domain)
                    if not allowed:
                        delete_msg(msg_id, chat_id, cmid=cmid)
                        send(chat_id, f"⚠️ [id{from_id}|Участник], ссылки запрещены!")
                        return

            # Фильтр капса
            if chat and chat.get("filter_caps") and len(text) > 8:
                alpha = [c for c in text if c.isalpha()]
                if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.65:
                    delete_msg(msg_id, chat_id, cmid=cmid)
                    send(chat_id, f"⚠️ [id{from_id}|Участник], не злоупотребляй капсом!")
                    return

            # Фильтр мата
            if chat and chat.get("filter_mat") and has_mat(text):
                delete_msg(msg_id, chat_id, cmid=cmid)
                send(chat_id, f"⚠️ [id{from_id}|Участник], нецензурные выражения запрещены!")
                return

        # Фильтр голосовых
        if chat and chat.get("filter_voice"):
            for att in attachments:
                if att.get("type") == "audio_message":
                    delete_msg(msg_id, chat_id, cmid=cmid)
                    send(chat_id, f"⚠️ [id{from_id}|Участник], голосовые сообщения запрещены!")
                    return

        # Фильтр стикеров
        if chat and chat.get("filter_sticker"):
            for att in attachments:
                if att.get("type") == "sticker":
                    delete_msg(msg_id, chat_id, cmid=cmid)
                    return

        # Фильтр пересылок
        if chat and chat.get("filter_forward") and msg.get("fwd_messages"):
            delete_msg(msg_id, chat_id, cmid=cmid)
            send(chat_id, f"⚠️ [id{from_id}|Участник], пересылки сообщений запрещены!")
            return

        # Обновляем slowmode
        if chat and chat.get("slowmode_sec"):
            with _db_lock:
                db.update_slowmode(from_id, chat_id)

    elif not text.startswith(PREFIX) and prio >= PE.HELPER and chat and chat.get("silence_mode"):
        pass  # модераторы пишут в режиме тишины

    # ── Триггеры ───────────────────────────────────────────────────────────────
    market_bang = bool(text) and (text.strip().lower() == "!рынок" or text.strip().lower().startswith("!рынок "))
    if text and not text.startswith(PREFIX) and not market_bang:
        with _db_lock:
            triggers = db.get_triggers(chat_id)
        tl = text.lower()
        for tr in triggers:
            matched = False
            mtype = tr.get("match_type", "contains")
            if mtype == "exact" and tl == tr["keyword"]:
                matched = True
            elif mtype == "startswith" and tl.startswith(tr["keyword"]):
                matched = True
            elif mtype == "contains" and tr["keyword"] in tl:
                matched = True
            elif mtype == "regex":
                try:
                    if re.search(tr["keyword"], text, re.I):
                        matched = True
                except Exception:
                    pass
            if matched:
                send(chat_id, tr["response"])
                with _db_lock:
                    db.increment_trigger_use(tr["id"])
                return

    # ── Роутер команд ──────────────────────────────────────────────────────────
    if text.startswith(PREFIX) or market_bang:
        routed_text = (PREFIX + text.strip()[1:]) if market_bang else text
        reply_msg     = msg.get("reply_message")
        reply_id      = reply_msg.get("id")                        if reply_msg else None
        reply_cmid    = reply_msg.get("conversation_message_id")   if reply_msg else None
        reply_from_id = reply_msg.get("from_id")                   if reply_msg else None
        if from_id != CREATOR_ID:
            # Проверка: заблокирован ли пользователь в командах
            with _db_lock:
                blocked = db.is_cmd_blocked(from_id)
            if blocked:
                delete_msg(msg_id, chat_id, cmid=cmid)
                return
            # Проверка: глобальный syncban — блокирует команды даже у создателя беседы
            with _db_lock:
                cmd_ban = db.get_active_syncban(from_id)
            if cmd_ban:
                delete_msg(msg_id, chat_id, cmid=cmid)
                return
        handle_command(routed_text, chat_id, from_id, prio, msg_id, reply_id, reply_from_id, chat, cmid=cmid, reply_cmid=reply_cmid)


# ══════════════════════════════════════════════════════════════════════════════
#  РОУТЕР КОМАНД
# ══════════════════════════════════════════════════════════════════════════════

def handle_command(txt, chat_id, uid, prio, msg_id, reply_id, reply_from_id, chat, cmid: int = 0, reply_cmid: int = 0):

    def reply(text: str) -> None:
        send(chat_id, text, reply_to=msg_id)

    def no_access(cmd: str) -> None:
        reply(f"❌ Недостаточно прав для команды /{cmd}.")

    def need_id() -> None:
        reply("❌ Укажи пользователя (ID, @упоминание или реплай).")

    def anti_self(target_id: int) -> bool:
        if target_id == uid:
            reply("❌ Нельзя применить команду к себе.")
            return True
        return False

    # ==========================================================================
    #  ИГРОВОЙ МОДУЛЬ
    # ==========================================================================

    if is_cmd(txt, "сайткод", "sitecode"):
        code = site_create_login_code(uid)
        reply(
            f"🌐 Код входа на сайт: {code}\n"
            f"⏳ Действует 10 минут и используется один раз.\n"
            f"⚠️ Никому не отправляй этот код."
        )
        return

    if is_cmd(txt, "gamecommands", "игровыекоманды"):
        reply(
            "🎮 КОМАНДЫ ИГРЫ\n"
            "──────────────────\n"
            "💰 /баланс — игровой баланс\n"
            "🏳 /страна [ID] — информация о стране\n"
            "🌍 /страны — список стран\n"
            "🏗 /создатьстрану [название] — 5 000 000 ₽\n"
            "🗑 /удалитьстрану — удалить свою страну за 1 000 000 ₽\n"
            "🚪 /открытьстрану [вкл|выкл] — управление набором\n"
            "🪪 /гражданство [ID страны|выйти] — вступить или выйти\n"
            "💝 /пожертвовать [сумма] — внести деньги в казну\n"
            "🏦 /взятьказну [сумма] — правителю взять из казны\n"
            "👢 /странакик [ID] — правителю исключить гражданина\n"
            "⛔ /запретвступить [ID] — запретить вступление\n"
            "✅ /разрешитьвступить [ID] — снять запрет\n"
            "🏛 /правительство [ID страны] — состав правительства\n"
            "➕ /назначитьправительство [ID] [должность] — только правителю\n"
            "➖ /снятьправительство [ID] — только правителю\n"
            "🏢 /фракция [вч|полиция|бандиты|больница]\n"
            "🪖 /тренировка | 👮 /арест | 📦 /контрабанда | 🥷 /украсть\n"
            "🌑 /рынок — чёрный рынок\n"
            "🪖 /армия — состав и покупка армии\n"
            "⚔️ /атака [ID страны] — атаковать страну\n"
            "⭐ /репстраны — репутация своей страны\n"
            "🔫 /оружия — открыть оружейный кейс за 500 000 ₽\n"
            "💥 /бах — тренировка стрельбы и +1 репутации за убийство\n"
            "──────────────────\n"
            "🏬 /бизнес — список и свой бизнес\n"
            "🛒 /купитьбизнес [ID]\n"
            "💵 /бизнес доход — забрать прибыль\n"
            "📦 /купитьпродукт [кол-во] — 100 ₽ за единицу\n"
            "🏷 /продатьбиз — продать свой бизнес\n"
            "📊 /мтоп — топ по сообщениям\n"
            "──────────────────\n"
            "Команды Агентов: /agenthelp"
        )
        return

    if is_cmd(txt, "баланс", "balance"):
        reply(f"💰 Ваш игровой баланс: {game_money(game_balance(uid))}")
        return

    if is_cmd(txt, "agenthelp"):
        if not game_is_agent(uid):
            return no_access("agenthelp")
        creator_only = "\n👑 /setagent [ID], /delagent [ID] — только создатель" if uid == CREATOR_ID else ""
        reply(
            "🕵 КОМАНДЫ АГЕНТОВ\n"
            "──────────────────\n"
            "💸 /giverub [ID] [сумма цифрами]\n"
            "🚪 /syskick [ID] — удалить из всех бесед бота\n"
            "🏷 /sysrole [ID] [приоритет 0-99] — во всех беседах\n"
            "🏳 /удалитьстрану [ID страны] — удалить любую страну\n"
            "🏬 /sellbiz [ID игрока] — принудительно продать бизнес в казну страны"
            + creator_only
        )
        return

    if is_cmd(txt, "setagent"):
        if uid != CREATOR_ID:
            return no_access("setagent")
        tid, _ = get_target(get_args(txt, "setagent"), reply_from_id)
        if not tid:
            return need_id()
        if tid == CREATOR_ID:
            reply("ℹ️ Создатель уже имеет все права агента.")
            return
        with _db_lock:
            db.ex(
                "INSERT INTO game_agents(user_id,added_by) VALUES(?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET added_by=excluded.added_by,created_at=strftime('%s','now')",
                (tid, uid),
            )
        reply(f"✅ {fmt_mention(tid, get_name(tid))} назначен агентом чат-менеджера.")
        return

    if is_cmd(txt, "delagent"):
        if uid != CREATOR_ID:
            return no_access("delagent")
        tid, _ = get_target(get_args(txt, "delagent"), reply_from_id)
        if not tid:
            return need_id()
        with _db_lock:
            removed = db.ex("DELETE FROM game_agents WHERE user_id=?", (tid,)).rowcount > 0
        reply(
            f"✅ Агент {fmt_mention(tid, get_name(tid))} удалён."
            if removed else "ℹ️ Этот пользователь не является агентом."
        )
        return

    if is_cmd(txt, "giverub"):
        if not game_is_agent(uid):
            return no_access("giverub")
        parts = get_args(txt, "giverub").split()
        tid = reply_from_id if reply_from_id else (resolve_id(parts[0]) if parts else None)
        amount_token = (parts[0] if reply_from_id and parts else (parts[1] if len(parts) >= 2 else ""))
        if not tid or not re.fullmatch(r"\d+", amount_token or "") or int(amount_token) <= 0:
            reply("❌ Формат: /giverub [ID] [сумма только цифрами]\nПример: /giverub 123456 5000000")
            return
        amount = int(amount_token)
        try:
            new_balance = game_add_money(tid, amount)
        except (OverflowError, sqlite3.DataError):
            reply("❌ Число слишком большое для хранения в базе данных.")
            return
        reply(
            f"💸 {fmt_mention(tid, get_name(tid))} выдано {game_money(amount)}.\n"
            f"💰 Новый баланс: {game_money(new_balance)}"
        )
        return

    if is_cmd(txt, "syskick"):
        if not game_is_agent(uid):
            return no_access("syskick")
        tid, _ = get_target(get_args(txt, "syskick"), reply_from_id)
        if not tid:
            return need_id()
        if tid == CREATOR_ID:
            reply("❌ Создателя нельзя удалить через /syskick.")
            return
        with _db_lock:
            all_chats = db.get_all_chats()
        kicked, skipped, errors = 0, 0, 0
        for row in all_chats:
            ok, reason = kick(int(row["chat_id"]), tid)
            if ok:
                kicked += 1
            elif reason == "not_in_chat":
                skipped += 1
            else:
                errors += 1
        reply(
            f"🚪 Системный кик: {fmt_mention(tid, get_name(tid))}\n"
            f"✅ Удалён из бесед: {kicked}\n"
            f"ℹ️ Не состоял: {skipped}\n"
            f"⚠️ Ошибок: {errors}"
        )
        return

    if is_cmd(txt, "sysrole"):
        if not game_is_agent(uid):
            return no_access("sysrole")
        raw = get_args(txt, "sysrole").split()
        if len(raw) != 2:
            reply("❌ Формат: /sysrole [ID пользователя] [приоритет 0-99]\nКоманда работает сразу во всех беседах бота.")
            return
        tid = resolve_id(raw[0])
        target_prio = _int(raw[1])
        if not tid or target_prio is None or not 0 <= target_prio <= 99:
            reply("❌ Проверь ID пользователя и приоритет 0–99.")
            return
        if tid == CREATOR_ID:
            reply("❌ Приоритет создателя фиксированный и не изменяется через /sysrole.")
            return
        with _db_lock:
            all_chats = db.get_all_chats()
            changed = 0
            for chat_row in all_chats:
                target_chat = int(chat_row["chat_id"])
                db.upsert_member(tid, target_chat)
                role = db.get_role_by_priority(target_prio, chat_id=target_chat) if target_prio > 0 else None
                db.update_member_role(tid, target_chat, role["id"] if role else None, target_prio)
                changed += 1
        reply(
            f"🌐 {fmt_mention(tid, get_name(tid))} получил приоритет [{target_prio}] "
            f"во всех беседах бота. Обновлено бесед: {changed}."
        )
        return

    if is_cmd(txt, "sellbiz"):
        if not game_is_agent(uid):
            return no_access("sellbiz")
        tid, _ = get_target(get_args(txt, "sellbiz"), reply_from_id)
        if not tid:
            return need_id()
        with _db_lock:
            business = db.one("SELECT * FROM game_businesses WHERE user_id=?", (tid,))
            if not business:
                reply("ℹ️ У этого игрока нет бизнеса.")
                return
            info = GAME_BUSINESSES.get(int(business["business_id"]))
            if not info:
                db.ex("DELETE FROM game_businesses WHERE user_id=?", (tid,))
                reply("⚠️ Неизвестный бизнес удалён без выплаты.")
                return
            proceeds = int(info["cost"] * GAME_BUSINESS_SELL_PERCENT / 100)
            membership = db.one("SELECT country_id FROM game_country_members WHERE user_id=?", (tid,))
            country = db.one("SELECT * FROM game_countries WHERE id=?", (membership["country_id"],)) if membership else None
            db.ex("DELETE FROM game_businesses WHERE user_id=?", (tid,))
            if country:
                db.ex("UPDATE game_countries SET treasury=treasury+? WHERE id=?", (proceeds, country["id"]))
        destination = f"в казну страны «{country['name']}»" if country else "списаны в государственный бюджет (страны у игрока нет)"
        reply(
            f"🏬 Бизнес {fmt_mention(tid, get_name(tid))} принудительно продан.\n"
            f"💰 {game_money(proceeds)} {destination}."
        )
        return

    if is_cmd(txt, "создатьстрану"):
        name = get_args(txt, "создатьстрану").strip()
        if name.startswith("-"):
            name = name[1:].strip()
        if len(name) < 2 or len(name) > 40:
            reply("❌ Формат: /создатьстрану [название от 2 до 40 символов]")
            return
        if re.search(r"[\n\r|]", name):
            reply("❌ В названии страны есть запрещённые символы.")
            return
        with _db_lock:
            exists_owner = db.one("SELECT 1 FROM game_countries WHERE owner_id=?", (uid,))
            exists_name = db.one("SELECT 1 FROM game_countries WHERE name=? COLLATE NOCASE", (name,))
            if exists_owner:
                reply("❌ У вас уже есть страна. Сначала удалите её командой /удалитьстрану.")
                return
            if exists_name:
                reply("❌ Страна с таким названием уже существует.")
                return
            game_ensure_wallet(uid)
            paid = db.ex(
                "UPDATE game_wallets SET balance=balance-?,updated_at=? WHERE user_id=? AND balance>=?",
                (GAME_COUNTRY_CREATE_COST, int(time.time()), uid, GAME_COUNTRY_CREATE_COST),
            ).rowcount > 0
            if not paid:
                reply(
                    f"❌ Для создания страны нужно {game_money(GAME_COUNTRY_CREATE_COST)}.\n"
                    f"💰 Ваш баланс: {game_money(game_balance(uid))}"
                )
                return
            try:
                cur = db.ex("INSERT INTO game_countries(name,owner_id) VALUES(?,?)", (name, uid))
            except Exception:
                game_add_money(uid, GAME_COUNTRY_CREATE_COST)
                reply("❌ Не удалось создать страну: название уже занято.")
                return
            country_id = cur.lastrowid
            db.ex(
                "INSERT OR REPLACE INTO game_country_members(user_id,country_id,rank,joined_at) VALUES(?,?,\'owner\',?)",
                (uid, country_id, int(time.time())),
            )
        reply(f"🏳 Страна «{name}» создана! ID страны: {country_id}.\nС баланса списано {game_money(GAME_COUNTRY_CREATE_COST)}.")
        return

    if is_cmd(txt, "страны"):
        with _db_lock:
            countries = db.all(
                "SELECT *, (infantry+tanks*100+planes*500) AS power FROM game_countries "
                "ORDER BY power DESC,wins DESC,id ASC LIMIT 30"
            )
        if not countries:
            reply("🌍 Стран пока нет. Создать: /создатьстрану [название]")
            return
        lines = [
            f"[{c['id']}] 🏳 {c['name']} — сила {int(c['power']):,} | побед {c['wins']}"
            for c in countries
        ]
        reply(("🌍 СТРАНЫ МИРА\n──────────────────\n" + "\n".join(lines)).replace(",", " "))
        return

    if is_cmd(txt, "страна"):
        raw = get_args(txt, "страна").strip()
        country = None
        if raw:
            cid = _int(raw)
            if cid:
                country = game_country_by_id(cid)
        else:
            country = game_country_membership(uid)
        if not country:
            reply("❌ Страна не найдена. Вступить: /гражданство [ID], список: /страны")
            return
        reply(game_country_card(country) + f"\n💰 Ваш кэш: {game_money(game_balance(uid))}")
        return

    if is_cmd(txt, "удалитьстрану"):
        raw = get_args(txt, "удалитьстрану").strip()
        staff = game_is_agent(uid)
        country = None
        if raw:
            cid = _int(raw)
            country = game_country_by_id(cid) if cid else None
        elif not staff:
            country = game_country_by_owner(uid)
        else:
            country = game_country_by_owner(uid)
        if not country:
            reply("❌ Страна не найдена. Формат для агента: /удалитьстрану [ID страны]")
            return
        if not staff and int(country["owner_id"]) != uid:
            reply("❌ Вы можете удалить только свою страну.")
            return
        if not staff and not game_take_money(uid, GAME_COUNTRY_DELETE_COST):
            reply(f"❌ Для удаления страны требуется {game_money(GAME_COUNTRY_DELETE_COST)}.")
            return
        with _db_lock:
            db.ex("DELETE FROM game_country_members WHERE country_id=?", (country["id"],))
            db.ex("DELETE FROM game_country_bans WHERE country_id=?", (country["id"],))
            db.ex("DELETE FROM game_countries WHERE id=?", (country["id"],))
        fee = "без комиссии агента" if staff else f"комиссия {game_money(GAME_COUNTRY_DELETE_COST)}"
        reply(f"🗑 Страна «{country['name']}» удалена ({fee}).")
        return

    if is_cmd(txt, "открытьстрану"):
        country = game_country_membership(uid)
        if not country or not game_can_govern(uid, country, "recruitment"):
            reply("❌ Управлять набором может правитель, премьер-министр или министр МВД.")
            return
        raw = get_args(txt, "открытьстрану").strip().lower()
        if raw in {"вкл", "on", "1", "да", "открыть"}:
            new_state = 1
        elif raw in {"выкл", "off", "0", "нет", "закрыть"}:
            new_state = 0
        elif not raw:
            new_state = 0 if int(country.get("is_open", 0)) else 1
        else:
            reply("❌ Формат: /открытьстрану [вкл|выкл]")
            return
        with _db_lock:
            db.ex("UPDATE game_countries SET is_open=? WHERE id=?", (new_state, country["id"]))
        reply(f"🚪 Набор в страну «{country['name']}» {'открыт' if new_state else 'закрыт'}.")
        return

    if is_cmd(txt, "гражданство"):
        raw = get_args(txt, "гражданство").strip().lower()
        current = game_country_membership(uid)
        if raw in {"выйти", "выход", "покинуть"}:
            if not current:
                reply("ℹ️ Вы не состоите в стране.")
                return
            if int(current["owner_id"]) == uid:
                reply("❌ Правитель не может выйти из своей страны. Сначала удалите страну.")
                return
            with _db_lock:
                db.ex("DELETE FROM game_country_members WHERE user_id=?", (uid,))
            reply(f"🚪 Вы вышли из страны «{current['name']}».")
            return
        cid = _int(raw)
        country = game_country_by_id(cid) if cid else None
        if not country:
            reply("❌ Формат: /гражданство [ID страны] или /гражданство выйти")
            return
        if current:
            if int(current["id"]) == int(country["id"]):
                reply("ℹ️ Вы уже гражданин этой страны.")
            else:
                reply(f"❌ Сначала выйдите из страны «{current['name']}»: /гражданство выйти")
            return
        if not int(country.get("is_open", 0)):
            reply("❌ Набор в эту страну закрыт.")
            return
        with _db_lock:
            banned = db.one("SELECT 1 FROM game_country_bans WHERE country_id=? AND user_id=?", (country["id"], uid))
            if banned:
                reply("⛔ Вам запрещено вступать в эту страну.")
                return
            db.ex(
                "INSERT INTO game_country_members(user_id,country_id,rank,joined_at) VALUES(?,?,\'citizen\',?)",
                (uid, country["id"], int(time.time())),
            )
        reply(f"🪪 Вы получили гражданство страны «{country['name']}».")
        return

    if is_cmd(txt, "пожертвовать"):
        country = game_country_membership(uid)
        amount = parse_game_amount(get_args(txt, "пожертвовать"))
        if not country:
            reply("❌ Сначала получите гражданство: /гражданство [ID страны]")
            return
        if not amount:
            reply("❌ Формат: /пожертвовать [сумма]")
            return
        with _db_lock:
            if not game_take_money(uid, amount):
                reply(f"❌ Недостаточно денег. Баланс: {game_money(game_balance(uid))}")
                return
            db.ex("UPDATE game_countries SET treasury=treasury+? WHERE id=?", (amount, country["id"]))
            updated = db.one("SELECT treasury FROM game_countries WHERE id=?", (country["id"],))
        reply(f"💝 В казну пожертвовано {game_money(amount)}.\n🏦 Казна: {game_money(updated['treasury'])}")
        return

    if is_cmd(txt, "взятьказну"):
        country = game_country_membership(uid)
        amount = parse_game_amount(get_args(txt, "взятьказну"))
        if not country or not game_can_govern(uid, country, "treasury"):
            reply("❌ Брать деньги из казны может правитель, премьер-министр или министр финансов.")
            return
        if not amount:
            reply("❌ Формат: /взятьказну [сумма]")
            return
        with _db_lock:
            taken = db.ex(
                "UPDATE game_countries SET treasury=treasury-? WHERE id=? AND treasury>=?",
                (amount, country["id"], amount),
            ).rowcount > 0
            if not taken:
                current = db.one("SELECT treasury FROM game_countries WHERE id=?", (country["id"],))
                reply(f"❌ В казне недостаточно средств: {game_money(current['treasury'] if current else 0)}")
                return
            new_balance = game_add_money(uid, amount)
        reply(f"🏦 Из казны взято {game_money(amount)}.\n💰 Ваш баланс: {game_money(new_balance)}")
        return

    if is_cmd(txt, "странакик"):
        country = game_country_membership(uid)
        if not country or not game_can_govern(uid, country, "citizens"):
            reply("❌ Исключать граждан может правитель, премьер-министр или министр МВД.")
            return
        tid, _ = get_target(get_args(txt, "странакик"), reply_from_id)
        if not tid:
            return need_id()
        if tid == uid:
            reply("❌ Нельзя исключить самого себя.")
            return
        with _db_lock:
            member = db.one("SELECT rank FROM game_country_members WHERE user_id=? AND country_id=?", (tid, country["id"]))
            if not member:
                reply("ℹ️ Этот игрок не является гражданином вашей страны.")
                return
            if member["rank"] == "owner":
                reply("❌ Правителя нельзя исключить из его страны.")
                return
            actor_rank = str(country.get("member_rank", "citizen"))
            if member["rank"] != "citizen" and int(country["owner_id"]) != uid and actor_rank != "prime_minister":
                reply("❌ Члена правительства может исключить только правитель или премьер-министр.")
                return
            db.ex("DELETE FROM game_country_members WHERE user_id=? AND country_id=?", (tid, country["id"]))
        reply(f"👢 {fmt_mention(tid, get_name(tid))} исключён из страны «{country['name']}».")
        return

    if is_cmd(txt, "запретвступить"):
        country = game_country_membership(uid)
        if not country or not game_can_govern(uid, country, "citizens"):
            reply("❌ Запрещать вступление может правитель, премьер-министр или министр МВД.")
            return
        tid, _ = get_target(get_args(txt, "запретвступить"), reply_from_id)
        if not tid:
            return need_id()
        if tid == uid:
            reply("❌ Нельзя запретить вступление самому себе.")
            return
        with _db_lock:
            target_member = db.one(
                "SELECT rank FROM game_country_members WHERE user_id=? AND country_id=?",
                (tid, country["id"]),
            )
            if target_member and target_member["rank"] == "owner":
                reply("❌ Правителя нельзя заблокировать в его стране.")
                return
            actor_rank = str(country.get("member_rank", "citizen"))
            if target_member and target_member["rank"] != "citizen" and int(country["owner_id"]) != uid and actor_rank != "prime_minister":
                reply("❌ Члена правительства может заблокировать только правитель или премьер-министр.")
                return
            db.ex(
                "INSERT INTO game_country_bans(country_id,user_id,banned_by) VALUES(?,?,?) "
                "ON CONFLICT(country_id,user_id) DO UPDATE SET banned_by=excluded.banned_by,created_at=strftime('%s','now')",
                (country["id"], tid, uid),
            )
            db.ex("DELETE FROM game_country_members WHERE user_id=? AND country_id=?", (tid, country["id"]))
        reply(f"⛔ {fmt_mention(tid, get_name(tid))} запрещено вступать в страну «{country['name']}».")
        return

    if is_cmd(txt, "разрешитьвступить"):
        country = game_country_membership(uid)
        if not country or not game_can_govern(uid, country, "citizens"):
            reply("❌ Снимать запрет может правитель, премьер-министр или министр МВД.")
            return
        tid, _ = get_target(get_args(txt, "разрешитьвступить"), reply_from_id)
        if not tid:
            return need_id()
        with _db_lock:
            removed = db.ex(
                "DELETE FROM game_country_bans WHERE country_id=? AND user_id=?",
                (country["id"], tid),
            ).rowcount > 0
        if removed:
            reply(f"✅ {fmt_mention(tid, get_name(tid))} снова может вступать в страну «{country['name']}».")
        else:
            reply("ℹ️ У этого игрока не было запрета на вступление.")
        return

    if is_cmd(txt, "правительство"):
        raw = get_args(txt, "правительство").strip()
        if raw.lower() in {"права", "должности", "помощь"}:
            reply(
                "🏛 ДОЛЖНОСТИ ПРАВИТЕЛЬСТВА\n"
                "🤝 Премьер-министр — набор, граждане, казна, армия и атаки\n"
                "💰 Министр финансов — управление казной\n"
                "🪖 Министр обороны — покупка армии и атаки\n"
                "👮 Министр МВД — набор, исключение и запреты граждан\n"
                "🏥 Министр здравоохранения — почётная должность\n"
                "Назначение: /назначитьправительство [ID] [должность]"
            )
            return
        if raw:
            cid = _int(raw)
            country = game_country_by_id(cid) if cid else None
        else:
            country = game_country_membership(uid)
        if not country:
            reply("❌ Страна не найдена. Формат: /правительство [ID страны]")
            return
        with _db_lock:
            officials = db.all(
                "SELECT user_id,rank FROM game_country_members "
                "WHERE country_id=? AND rank NOT IN ('owner','citizen') "
                "ORDER BY rank,user_id",
                (country["id"],),
            )
        lines = [f"👑 Правитель — {fmt_mention(int(country['owner_id']), get_name(int(country['owner_id'])))}"]
        if officials:
            for official in officials:
                oid = int(official["user_id"])
                lines.append(
                    f"{game_government_title(str(official['rank']))} — "
                    f"{fmt_mention(oid, get_name(oid))}"
                )
        else:
            lines.append("ℹ️ Другие должности пока не назначены.")
        reply(
            f"🏛 ПРАВИТЕЛЬСТВО СТРАНЫ «{country['name']}»\n"
            "──────────────────\n" + "\n".join(lines)
        )
        return

    if is_cmd(txt, "назначитьправительство"):
        country = game_country_by_owner(uid)
        if not country:
            reply("❌ Назначать правительство может только правитель своей страны.")
            return
        tid, role_raw = parse_target_text(get_args(txt, "назначитьправительство"), reply_from_id)
        role = game_government_rank(role_raw or "")
        if not tid or not role:
            reply(
                "❌ Формат: /назначитьправительство [ID] [должность]\n"
                "Должности: премьер, финансы, оборона, МВД, здравоохранение."
            )
            return
        if tid == uid:
            reply("❌ Правитель уже является главой государства.")
            return
        with _db_lock:
            member = db.one(
                "SELECT rank FROM game_country_members WHERE user_id=? AND country_id=?",
                (tid, country["id"]),
            )
            if not member:
                reply("❌ Назначить можно только гражданина своей страны.")
                return
            occupied = db.one(
                "SELECT user_id FROM game_country_members WHERE country_id=? AND rank=? AND user_id!=?",
                (country["id"], role, tid),
            )
            if occupied:
                oid = int(occupied["user_id"])
                reply(
                    f"❌ Должность {game_government_title(role)} уже занимает "
                    f"{fmt_mention(oid, get_name(oid))}. Сначала снимите его с должности."
                )
                return
            db.ex(
                "UPDATE game_country_members SET rank=? WHERE user_id=? AND country_id=?",
                (role, tid, country["id"]),
            )
        reply(
            f"🏛 {fmt_mention(tid, get_name(tid))} назначен на должность: "
            f"{game_government_title(role)}."
        )
        return

    if is_cmd(txt, "снятьправительство"):
        country = game_country_by_owner(uid)
        if not country:
            reply("❌ Снимать правительство может только правитель своей страны.")
            return
        tid, _ = get_target(get_args(txt, "снятьправительство"), reply_from_id)
        if not tid:
            return need_id()
        with _db_lock:
            member = db.one(
                "SELECT rank FROM game_country_members WHERE user_id=? AND country_id=?",
                (tid, country["id"]),
            )
            if not member:
                reply("❌ Этот игрок не является гражданином вашей страны.")
                return
            if member["rank"] in {"owner", "citizen"}:
                reply("ℹ️ У этого игрока нет должности в правительстве.")
                return
            old_title = game_government_title(str(member["rank"]))
            db.ex(
                "UPDATE game_country_members SET rank='citizen' WHERE user_id=? AND country_id=?",
                (tid, country["id"]),
            )
        reply(f"➖ {fmt_mention(tid, get_name(tid))} снят с должности: {old_title}.")
        return

    if is_cmd(txt, "фракция"):
        raw = get_args(txt, "фракция").strip().lower()
        with _db_lock:
            current = db.one("SELECT * FROM game_factions WHERE user_id=?", (uid,))
        if not raw:
            if current:
                title, income = GAME_FACTIONS.get(current["faction"], (current["faction"], ""))
                reply(
                    f"🏢 Ваша фракция: {title}\n{income}\n"
                    "──────────────────\n"
                    "Сменить: /фракция вч | полиция | бандиты | больница"
                )
            else:
                reply(
                    "🏢 ВЫБЕРИТЕ ФРАКЦИЮ\n"
                    "🪖 ВЧ — прибыль за /тренировка\n"
                    "👮 Полиция — прибыль за /арест\n"
                    "🕶 Бандиты — /контрабанда и /украсть\n"
                    "🏥 Больница — без прибыли, по кайфу\n"
                    "──────────────────\n"
                    "Вступить: /фракция [название]"
                )
            return
        faction = GAME_FACTION_ALIASES.get(raw)
        if not faction:
            reply("❌ Фракции: вч, полиция, бандиты, больница.")
            return
        if current and current["faction"] == faction:
            reply("ℹ️ Вы уже состоите в этой фракции.")
            return
        if current:
            left = int(current["joined_at"]) + GAME_FACTION_SWITCH_COOLDOWN - int(time.time())
            if left > 0:
                reply(f"⏳ Сменить фракцию можно через {fmt_dur(left)}.")
                return
        with _db_lock:
            db.ex(
                "INSERT INTO game_factions(user_id,faction,joined_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET faction=excluded.faction,joined_at=excluded.joined_at",
                (uid, faction, int(time.time())),
            )
        title, income = GAME_FACTIONS[faction]
        reply(f"✅ Вы вступили во фракцию {title}.\n{income}")
        return

    if is_cmd(txt, "тренировка"):
        with _db_lock:
            faction = db.one("SELECT faction FROM game_factions WHERE user_id=?", (uid,))
        if not faction or faction["faction"] != "military":
            reply("❌ /тренировка доступна только участникам ВЧ.")
            return
        left = game_get_cooldown(uid, "training", 3600)
        if left:
            reply(f"⏳ Следующая тренировка через {fmt_dur(left)}.")
            return
        reward = random.randint(15_000, 40_000)
        game_use_cooldown(uid, "training")
        new_balance = game_add_money(uid, reward)
        with _db_lock:
            db.ex("UPDATE game_factions SET action_count=action_count+1 WHERE user_id=?", (uid,))
        reply(f"🪖 Тренировка завершена. Получено {game_money(reward)}.\n💰 Баланс: {game_money(new_balance)}")
        return

    if is_cmd(txt, "арест"):
        with _db_lock:
            faction = db.one("SELECT faction FROM game_factions WHERE user_id=?", (uid,))
        if not faction or faction["faction"] != "police":
            reply("❌ /арест доступен только полиции.")
            return
        left = game_get_cooldown(uid, "arrest", 90 * 60)
        if left:
            reply(f"⏳ Следующий арест через {fmt_dur(left)}.")
            return
        game_use_cooldown(uid, "arrest")
        if random.random() < 0.72:
            reward = random.randint(20_000, 70_000)
            new_balance = game_add_money(uid, reward)
            reply(f"👮 Преступник задержан! Премия: {game_money(reward)}.\n💰 Баланс: {game_money(new_balance)}")
        else:
            fine = min(game_balance(uid), random.randint(5_000, 15_000))
            if fine:
                game_take_money(uid, fine)
            reply(f"🚗 Подозреваемый ушёл. Расходы операции: {game_money(fine)}.")
        return

    if is_cmd(txt, "контрабанда"):
        with _db_lock:
            faction = db.one("SELECT faction FROM game_factions WHERE user_id=?", (uid,))
        if not faction or faction["faction"] != "bandits":
            reply("❌ /контрабанда доступна только бандитам.")
            return
        left = game_get_cooldown(uid, "contraband", 2 * 3600)
        if left:
            reply(f"⏳ Новый рейс через {fmt_dur(left)}.")
            return
        game_use_cooldown(uid, "contraband")
        if random.random() < 0.70:
            reward = random.randint(30_000, 120_000)
            new_balance = game_add_money(uid, reward)
            reply(f"📦 Контрабанда доставлена. Прибыль: {game_money(reward)}.\n💰 Баланс: {game_money(new_balance)}")
        else:
            fine = min(game_balance(uid), random.randint(10_000, 40_000))
            if fine:
                game_take_money(uid, fine)
            reply(f"🚨 ФСБ накрыла груз. Потеряно: {game_money(fine)}.")
        return

    if is_cmd(txt, "украсть"):
        with _db_lock:
            faction = db.one("SELECT faction FROM game_factions WHERE user_id=?", (uid,))
        if not faction or faction["faction"] != "bandits":
            reply("❌ /украсть доступна только бандитам.")
            return
        tid, _ = get_target(get_args(txt, "украсть"), reply_from_id)
        if not tid:
            return need_id()
        if tid == uid:
            reply("❌ Нельзя ограбить самого себя.")
            return
        left = game_get_cooldown(uid, "steal", 4 * 3600)
        if left:
            reply(f"⏳ Следующая кража через {fmt_dur(left)}.")
            return
        game_use_cooldown(uid, "steal")
        target_balance = game_balance(tid)
        if target_balance <= 0:
            reply("🥷 У цели нет игровых денег.")
            return
        if random.random() < 0.35:
            stolen = max(1, min(100_000, int(target_balance * random.uniform(0.03, 0.10))))
            with _db_lock:
                if not game_take_money(tid, stolen):
                    reply("❌ Деньги цели уже изменились, кража сорвалась.")
                    return
                game_add_money(uid, stolen)
            reply(f"🥷 Успешно украдено {game_money(stolen)} у {fmt_mention(tid, get_name(tid))}.")
        else:
            fine = min(game_balance(uid), random.randint(10_000, 30_000))
            if fine:
                game_take_money(uid, fine)
            reply(f"🚔 Вас поймали. Штраф: {game_money(fine)}.")
        return

    if is_cmd(txt, "рынок"):
        raw = get_args(txt, "рынок").strip().lower()
        rows = game_market_rows()
        by_id = {int(r["item_id"]): r for r in rows}
        if not raw:
            lines = [
                "🌑 ЧЁРНЫЙ РЫНОК",
                f"💰 Ваш кэш: {game_money(game_balance(uid))}",
                "──────────────────",
                "Цены динамические и меняются каждые 3 часа.",
                "⚠️ Осторожно, ФСБ пасет барыг! Шанс облавы: ~10%",
                "──────────────────",
            ]
            for r in rows:
                lines.extend([
                    f"[{r['item_id']}] {r['name']}",
                    f"🛒 Купить: {game_money(r['buy_price'])} | 💵 Продать: {game_money(r['sell_price'])}",
                    f"📦 В наличии: {r['stock']} шт. | У вас: {game_inventory_qty(uid, int(r['item_id']))} шт.",
                    "",
                ])
            lines.extend([
                "──────────────────",
                "📥 Купить: /рынок купить[номер] [кол-во]",
                "📤 Продать: /рынок продать[номер] [кол-во или все]",
                "ℹ️ Также работает префикс !рынок",
            ])
            reply("\n".join(lines))
            return
        match = re.fullmatch(r"(купить|продать)\s*([1-8])(?:\s+(\S+))?", raw)
        if not match:
            reply("❌ Формат: /рынок купить1 2 или /рынок продать1 все")
            return
        action, item_raw, qty_raw = match.groups()
        item_id = int(item_raw)
        row = by_id[item_id]
        if action == "купить":
            qty = _int(qty_raw or "1")
            if not qty or qty <= 0 or qty > 1000:
                reply("❌ Количество должно быть от 1 до 1000.")
                return
            total = int(row["buy_price"]) * qty
            with _db_lock:
                live = db.one("SELECT stock,buy_price,bucket FROM game_market_state WHERE item_id=?", (item_id,))
                if not live or int(live["stock"]) < qty:
                    reply(f"❌ На рынке недостаточно товара. Осталось: {int(live['stock']) if live else 0} шт.")
                    return
                game_ensure_wallet(uid)
                paid = db.ex(
                    "UPDATE game_wallets SET balance=balance-?,updated_at=? WHERE user_id=? AND balance>=?",
                    (total, int(time.time()), uid, total),
                ).rowcount > 0
                if not paid:
                    reply(f"❌ Не хватает денег. Нужно {game_money(total)}.")
                    return
                db.ex("UPDATE game_market_state SET stock=stock-? WHERE item_id=?", (qty, item_id))
                raid = random.random() < 0.10
                if not raid:
                    game_change_inventory(uid, item_id, qty)
            if raid:
                reply(f"🚨 ОБЛАВА! ФСБ конфисковала {qty} шт. товара. Потеряно {game_money(total)}.")
            else:
                reply(f"📥 Куплено: {row['name']} ×{qty} за {game_money(total)}.")
            return
        owned = game_inventory_qty(uid, item_id)
        if qty_raw == "все":
            qty = owned
        else:
            qty = _int(qty_raw or "1")
        if not qty or qty <= 0 or qty > owned:
            reply(f"❌ У вас только {owned} шт. этого товара.")
            return
        total = int(row["sell_price"]) * qty
        with _db_lock:
            if not game_change_inventory(uid, item_id, -qty):
                reply("❌ Недостаточно товара.")
                return
            raid = random.random() < 0.10
            if not raid:
                game_add_money(uid, total)
                db.ex("UPDATE game_market_state SET stock=stock+? WHERE item_id=?", (qty, item_id))
        if raid:
            reply(f"🚨 ОБЛАВА! Товар конфискован, выплату {game_money(total)} вы не получили.")
        else:
            reply(f"📤 Продано: {row['name']} ×{qty}. Получено {game_money(total)}.")
        return

    if is_cmd(txt, "бизнес"):
        raw = get_args(txt, "бизнес").strip().lower()
        business = game_business_by_user(uid)
        if raw in {"доход", "прибыль", "забрать"}:
            if not business:
                reply("❌ У вас нет бизнеса. Купить: /купитьбизнес [ID]")
                return
            info = GAME_BUSINESSES.get(int(business["business_id"]))
            if not info:
                reply("❌ Тип вашего бизнеса не найден.")
                return
            left = max(0, int(business["last_income"]) + GAME_BUSINESS_INCOME_COOLDOWN - int(time.time()))
            if left:
                reply(f"⏳ Следующая прибыль через {fmt_dur(left)}.")
                return
            if int(business["products"]) < GAME_BUSINESS_PRODUCT_USE:
                reply(
                    f"❌ Для работы нужно {GAME_BUSINESS_PRODUCT_USE} продуктов. "
                    f"У вас: {business['products']}. Купить: /купитьпродукт [кол-во]"
                )
                return
            with _db_lock:
                changed = db.ex(
                    "UPDATE game_businesses SET products=products-?,last_income=? "
                    "WHERE user_id=? AND products>=?",
                    (GAME_BUSINESS_PRODUCT_USE, int(time.time()), uid, GAME_BUSINESS_PRODUCT_USE),
                ).rowcount > 0
                if not changed:
                    reply("❌ Состояние бизнеса изменилось, попробуйте снова.")
                    return
                new_balance = game_add_money(uid, int(info["income"]))
            reply(
                f"🏬 {info['name']} принёс {game_money(info['income'])}.\n"
                f"📦 Потрачено продуктов: {GAME_BUSINESS_PRODUCT_USE}\n"
                f"💰 Баланс: {game_money(new_balance)}"
            )
            return
        lines = ["🏬 БИЗНЕСЫ", "──────────────────"]
        for bid, info in GAME_BUSINESSES.items():
            lines.append(
                f"[{bid}] {info['name']}\n"
                f"💳 Стоимость: {game_money(info['cost'])} | 💰 Прибыль: {game_money(info['income'])}"
            )
        lines.append("──────────────────")
        if business:
            info = GAME_BUSINESSES.get(int(business["business_id"]), {"name": "Неизвестный бизнес"})
            left = max(0, int(business["last_income"]) + GAME_BUSINESS_INCOME_COOLDOWN - int(time.time()))
            ready = "готова" if left == 0 else f"через {fmt_dur(left)}"
            lines.append(f"Ваш бизнес: {info['name']} | продукты: {business['products']} | прибыль: {ready}")
        else:
            lines.append("У вас нет бизнеса.")
        lines.extend([
            "/купитьбизнес [ID]",
            "/купитьпродукт [кол-во]",
            "/бизнес доход",
            "/продатьбиз",
        ])
        reply("\n".join(lines))
        return

    if is_cmd(txt, "купитьбизнес"):
        bid = _int(get_args(txt, "купитьбизнес").strip())
        info = GAME_BUSINESSES.get(bid or 0)
        if not info:
            reply("❌ Формат: /купитьбизнес [1-3]. Список: /бизнес")
            return
        if game_business_by_user(uid):
            reply("❌ У вас уже есть бизнес. Сначала продайте его: /продатьбиз")
            return
        with _db_lock:
            if not game_take_money(uid, int(info["cost"])):
                reply(f"❌ Не хватает денег. Нужно {game_money(info['cost'])}.")
                return
            try:
                db.ex(
                    "INSERT INTO game_businesses(user_id,business_id,products,last_income,purchased_at) VALUES(?,?,0,0,?)",
                    (uid, bid, int(time.time())),
                )
            except Exception:
                game_add_money(uid, int(info["cost"]))
                reply("❌ Не удалось купить бизнес. Деньги возвращены.")
                return
        reply(f"✅ Куплен бизнес {info['name']} за {game_money(info['cost'])}.")
        return

    if is_cmd(txt, "продатьбиз"):
        business = game_business_by_user(uid)
        if not business:
            reply("ℹ️ У вас нет бизнеса.")
            return
        info = GAME_BUSINESSES.get(int(business["business_id"]))
        if not info:
            with _db_lock:
                db.ex("DELETE FROM game_businesses WHERE user_id=?", (uid,))
            reply("⚠️ Неизвестный бизнес удалён без выплаты.")
            return
        proceeds = int(info["cost"] * GAME_BUSINESS_SELL_PERCENT / 100)
        with _db_lock:
            db.ex("DELETE FROM game_businesses WHERE user_id=?", (uid,))
            new_balance = game_add_money(uid, proceeds)
        reply(f"🏷 Бизнес продан за {game_money(proceeds)}.\n💰 Баланс: {game_money(new_balance)}")
        return

    if is_cmd(txt, "купитьпродукт"):
        qty = _int(get_args(txt, "купитьпродукт").strip() or "1")
        business = game_business_by_user(uid)
        if not business:
            reply("❌ Сначала купите бизнес: /купитьбизнес [ID]")
            return
        if not qty or qty <= 0 or qty > 10_000_000:
            reply("❌ Количество должно быть от 1 до 10 000 000.")
            return
        total = qty * GAME_BUSINESS_PRODUCT_COST
        with _db_lock:
            if not game_take_money(uid, total):
                reply(f"❌ Не хватает денег. Нужно {game_money(total)}.")
                return
            db.ex("UPDATE game_businesses SET products=products+? WHERE user_id=?", (qty, uid))
            updated = db.one("SELECT products FROM game_businesses WHERE user_id=?", (uid,))
        reply(f"📦 Куплено продуктов: {qty:,} за {game_money(total)}. Всего: {updated['products']:,}.".replace(",", " "))
        return

    if is_cmd(txt, "оружия"):
        raw = get_args(txt, "оружия").strip().lower()
        if raw in {"инвентарь", "мои", "список"}:
            with _db_lock:
                rows = db.all("SELECT * FROM game_weapons WHERE user_id=? AND qty>0 ORDER BY weapon_id", (uid,))
            if not rows:
                reply("🔫 У вас пока нет оружия. Открыть кейс: /оружия")
                return
            lines = [f"{GAME_WEAPONS.get(int(r['weapon_id']), {'name':'Неизвестное оружие'})['name']} ×{r['qty']}" for r in rows]
            reply("🔫 ВАШЕ ОРУЖИЕ\n" + "\n".join(lines))
            return
        country = game_country_membership(uid)
        if not country:
            reply("❌ Для открытия кейса нужно гражданство страны.")
            return
        if not game_take_money(uid, GAME_WEAPON_CASE_COST):
            reply(f"❌ Оружейный кейс стоит {game_money(GAME_WEAPON_CASE_COST)}.")
            return
        ids = list(GAME_WEAPONS)
        weapon_id = random.choices(ids, weights=[GAME_WEAPONS[i]["weight"] for i in ids], k=1)[0]
        weapon = GAME_WEAPONS[weapon_id]
        with _db_lock:
            db.ex(
                "INSERT INTO game_weapons(user_id,weapon_id,qty) VALUES(?,?,1) "
                "ON CONFLICT(user_id,weapon_id) DO UPDATE SET qty=qty+1",
                (uid, weapon_id),
            )
            db.ex("UPDATE game_countries SET reputation=reputation+? WHERE id=?", (weapon["rep"], country["id"]))
        reply(
            f"🎁 Оружейный кейс открыт за {game_money(GAME_WEAPON_CASE_COST)}!\n"
            f"Выпало: {weapon['name']}\n"
            f"⭐ Репутация страны +{weapon['rep']}"
        )
        return

    if is_cmd(txt, "бах"):
        country = game_country_membership(uid)
        if not country:
            reply("❌ Для стрельбы нужно гражданство страны.")
            return
        if game_weapon_total(uid) <= 0:
            reply("❌ У вас нет оружия. Открыть кейс: /оружия")
            return
        left = game_get_cooldown(uid, "shooting", 60)
        if left:
            reply(f"⏳ Следующая перестрелка через {fmt_dur(left)}.")
            return
        game_use_cooldown(uid, "shooting")
        if random.random() < 0.65:
            with _db_lock:
                db.ex("UPDATE game_countries SET reputation=reputation+1 WHERE id=?", (country["id"],))
                db.ex(
                    "INSERT INTO game_player_stats(user_id,shooting_kills) VALUES(?,1) "
                    "ON CONFLICT(user_id) DO UPDATE SET shooting_kills=shooting_kills+1",
                    (uid,),
                )
                stats = db.one("SELECT shooting_kills FROM game_player_stats WHERE user_id=?", (uid,))
            reply(f"💥 БАХ! Убийство засчитано. ⭐ Репутация страны +1. Ваши убийства: {stats['shooting_kills']}.")
        else:
            reply("💨 Выстрел мимо. Репутация не изменилась.")
        return

    if is_cmd(txt, "репстраны"):
        country = game_country_membership(uid)
        if not country:
            reply("❌ Вы не состоите в стране.")
            return
        reply(f"⭐ Репутация страны «{country['name']}»: {int(country.get('reputation', 0)):,}.".replace(",", " "))
        return

    if is_cmd(txt, "армия"):
        country = game_country_membership(uid)
        if not country:
            reply("❌ Сначала получите гражданство страны.")
            return
        raw = get_args(txt, "армия").strip().lower()
        if not raw:
            reply(
                game_country_card(country) +
                "\n──────────────────\n"
                "Цены: пехота — 500 ₽, танк — 50 000 ₽, самолёт — 250 000 ₽.\n"
                "Купить: /армия купить [тип] [кол-во]"
            )
            return
        if not game_can_govern(uid, country, "army"):
            reply("❌ Покупать армию может правитель, премьер-министр или министр обороны.")
            return
        match = re.fullmatch(r"купить\s+(\S+)\s+(\d+)", raw)
        if not match:
            reply("❌ Формат: /армия купить [пехота|танк|самолёт] [кол-во]")
            return
        unit_raw, qty_raw = match.groups()
        unit = GAME_ARMY.get(unit_raw)
        qty = _int(qty_raw)
        if not unit or not qty or qty <= 0 or qty > 1_000_000:
            reply("❌ Проверьте тип войск и количество.")
            return
        column, price, title = unit
        total = price * qty
        with _db_lock:
            if not game_take_money(uid, total):
                reply(f"❌ Не хватает денег. Требуется {game_money(total)}.")
                return
            db.ex(f"UPDATE game_countries SET {column}={column}+? WHERE id=?", (qty, country["id"]))
        reply(f"✅ Куплено: {title} ×{qty:,} за {game_money(total)}.".replace(",", " "))
        return

    if is_cmd(txt, "атака"):
        attacker = game_country_membership(uid)
        if not attacker or not game_can_govern(uid, attacker, "attack"):
            reply("❌ Атаковать может правитель, премьер-министр или министр обороны.")
            return
        target_id = _int(get_args(txt, "атака").strip())
        defender = game_country_by_id(target_id) if target_id else None
        if not defender:
            reply("❌ Формат: /атака [ID страны]. Список: /страны")
            return
        if int(defender["id"]) == int(attacker["id"]):
            reply("❌ Нельзя атаковать свою страну.")
            return
        # Создатель может атаковать без кулдауна; для остальных таймер сохраняется.
        if uid != CREATOR_ID:
            # Кулдаун общий для всей страны, чтобы министры не обходили его по очереди.
            left = game_get_cooldown(-int(attacker["id"]), "country_attack", GAME_ATTACK_COOLDOWN)
            if left:
                reply(f"⏳ Следующая атака будет доступна через {fmt_dur(left)}.")
                return
        atk_power = game_army_power(attacker)
        def_power = game_army_power(defender)
        if atk_power <= 0:
            reply("❌ У вашей страны нет армии.")
            return
        if uid != CREATOR_ID:
            game_use_cooldown(-int(attacker["id"]), "country_attack")
        atk_roll = atk_power * random.uniform(0.85, 1.15)
        def_roll = def_power * random.uniform(0.85, 1.15)
        attacker_won = atk_roll > def_roll
        winner = attacker if attacker_won else defender
        loser = defender if attacker_won else attacker

        def losses(country: dict, low: float, high: float) -> tuple:
            rate = random.uniform(low, high)
            return (
                min(int(country["infantry"]), int(int(country["infantry"]) * rate)),
                min(int(country["tanks"]), int(int(country["tanks"]) * rate)),
                min(int(country["planes"]), int(int(country["planes"]) * rate)),
            )

        atk_losses = losses(attacker, 0.05 if attacker_won else 0.15, 0.18 if attacker_won else 0.35)
        def_losses = losses(defender, 0.15 if attacker_won else 0.05, 0.35 if attacker_won else 0.18)
        loot = 0
        with _db_lock:
            db.ex(
                "UPDATE game_countries SET infantry=MAX(0,infantry-?),tanks=MAX(0,tanks-?),planes=MAX(0,planes-?),"
                "wins=wins+?,losses=losses+? WHERE id=?",
                (*atk_losses, 1 if attacker_won else 0, 0 if attacker_won else 1, attacker["id"]),
            )
            db.ex(
                "UPDATE game_countries SET infantry=MAX(0,infantry-?),tanks=MAX(0,tanks-?),planes=MAX(0,planes-?),"
                "wins=wins+?,losses=losses+? WHERE id=?",
                (*def_losses, 0 if attacker_won else 1, 1 if attacker_won else 0, defender["id"]),
            )
            if attacker_won:
                victim_balance = game_balance(int(defender["owner_id"]))
                loot = min(500_000, int(victim_balance * 0.05))
                if loot > 0 and game_take_money(int(defender["owner_id"]), loot):
                    game_add_money(uid, loot)
                else:
                    loot = 0
            db.ex(
                "INSERT INTO game_battles(attacker_id,defender_id,winner_id,loot) VALUES(?,?,?,?)",
                (attacker["id"], defender["id"], winner["id"], loot),
            )
        result = "🏆 ПОБЕДА" if attacker_won else "💀 ПОРАЖЕНИЕ"
        reply(
            f"⚔️ {attacker['name']} атаковала страну {defender['name']}\n"
            f"{result}! Победитель: {winner['name']}\n"
            f"──────────────────\n"
            f"Потери атакующего: 🪖 {atk_losses[0]} | 🛡 {atk_losses[1]} | ✈️ {atk_losses[2]}\n"
            f"Потери защитника: 🪖 {def_losses[0]} | 🛡 {def_losses[1]} | ✈️ {def_losses[2]}\n"
            f"💰 Захвачено: {game_money(loot)}"
        )
        return

    # ==========================================================================
    #  ПУБЛИЧНЫЕ КОМАНДЫ (все участники)
    # ==========================================================================

    if is_cmd(txt, "help", "команды", "хелп"):
        reply(
            f"╔══ {BOT_NAME} ══╗\n"
            "║  ПУБЛИЧНЫЕ\n"
            "╠════════════════════╣\n"
            "║ /help — список команд\n"
            "║ /gamecommands — команды игры\n"
            "║ /me — моя статистика\n"
            "║ /rules — правила\n"
            "║ /chatinfo — инфо о беседе\n"
            "║ /stats [id] — статистика\n"
            "║ /check [id] — проверка\n"
            "║ /warns [id] — варны\n"
            "║ /warnhistory [id] — история\n"
            "║ /reg [id] — дата регистрации\n"
            "║ /top — топ активных\n"
            "║ /roles — список ролей\n"
            "║ /staff — состав администрации\n"
            "║ /banlist — список банов\n"
            "║ /nicklist — список ников\n"
            "║ /getnick [id] — ник участника\n"
            "║ /getban [id] — инфо о бане\n"
            "║ /nomention — отписка /zov\n"
            "║ /mention — подписка /zov\n"
            "║ /report [текст] — жалоба\n"
            "║ /uptime — время работы бота\n"
            "╠══ ПОМОЩНИК (10+) ════╣\n"
            "║ /mute /unmute /muted\n"
            "║ /kick /warn /unwarn\n"
            "║ /clearwarns /warnlist\n"
            "║ /setnick /removenick\n"
            "║ /zov [текст] — упомянуть всех\n"
            "║ /note [текст] — заметки\n"
            "║ /delnote [id] — удалить заметку\n"
            "║ /logs [id?] — журнал\n"
            "║ /reports — жалобы\n"
            "║ /closerep [id] — закрыть жалобу\n"
            "║ /closeall — закрыть все жалобы\n"
            "║ /viplist — список VIP\n"
            "║ /mediastats [id?] — медиа стат.\n"
            "╠══ МОДЕРАТОР (30+) ════╣\n"
            "║ /ban /unban /banlist\n"
            "║ /setrole /removerole\n"
            "║ /addtrigger /deltrigger\n"
            "║ /triggers /cleartriggers\n"
            "║ /addword /delword /wordlist\n"
            "║ /addwhite /delwhite /whitelist\n"
            "║ /gkick /gmoder /gzov\n"
            "║ /gsetnick /gremovenick\n"
            "║ /gremoverole\n"
            "╠══ АДМИНИСТРАТОР (50+) ╣\n"
            "║ /moder /admin /helper\n"
            "║ /gban /gunban /gmute /gms\n"
            "║ /grole /ghelper\n"
            "║ /del /filter\n"
            "║ /setcmd [команда] [прио.|диапазон] [вкл|выкл]\n"
            "║ /vip /devip — VIP участники\n"
            "║ /setbadge /removebadge\n"
            "║ /muteall /unmuteall\n"
            "║ /kickinactive [дней]\n"
            "╠══ ГЛ. АДМИНИСТРАТОР (70+) ╣\n"
            "║ /gadmin /pin /unpin\n"
            "║ /newrole /delrole\n"
            "║ /gnewrole /gdelrole\n"
            "║ /welcome /goodbye /setrules\n"
            "║ /silence /antiraid\n"
            "║ /slowmode [сек] — медленный режим\n"
            "║ /flood [кол-во] [секунд]\n"
            "║ /schedule — расписание\n"
            "║ /schedlist /delschedule [id]\n"
            "║ /temprole [id] [прио.] [время]\n"
            "╠══ ВЛАДЕЛЕЦ (100+) ════╣\n"
            "║ /kickall — кик всех (с подтверждением)\n"
            "║ /kickgroups — кик сообществ\n"
            "║ /setowner /removeowner\n"
            "║ /wipe [warns/bans/roles/nicks/...]\n"
            "║ /setlog /sync /autorole\n"
            "║ /setpublic [ссылка|off] — подписка обязательна\n"
            "║ /settings — настройки беседы\n"
            "║ /mentions — список отписок\n"
            "║ /createunity /addunity\n"
            "║ /removeunity /unity\n"
            "║ /deleteunity\n"
            "║ /blockcmd /unblockcmd\n"
            "║ /syncban /syncunban\n"
            "║ /dbinfo — статистика базы\n"
            "╚════════════════════╝"
        )
        return

    if is_cmd(txt, "me"):
        with _db_lock:
            m_row       = db.get_member(uid, chat_id)
            warns_count = db.count_warns(uid, chat_id)
            active_ban  = db.get_active_ban(uid, chat_id)
            vip_row     = db.get_vip(uid, chat_id)
        t_prio   = PE.get(uid, chat_id)
        nick     = (m_row.get("nickname") or "нет") if m_row else "нет"
        badge    = (m_row.get("badge") or "") if m_row else ""
        reg      = fmt_ts(m_row["reg_date"])  if m_row and m_row.get("reg_date")  else "нет"
        seen     = fmt_ts(m_row["last_seen"]) if m_row and m_row.get("last_seen") else "нет"
        msg_cnt  = m_row.get("msg_count", 0) if m_row else 0
        mute_str = "нет"
        if m_row and m_row.get("is_muted"):
            until = m_row.get("mute_until", 0)
            if until == 0 or until > int(time.time()):
                mute_str = fmt_remaining(until)
        vip_str  = f" {vip_row['badge']}" if vip_row else ""
        role_str = _role_label(m_row, t_prio, chat_id)
        reply(
            f"👤 Моя статистика{vip_str}\n"
            f"────────────────────\n"
            f"🏷 Роль: {role_str}\n"
            f"🎭 Ник: {nick}{(' | ' + badge) if badge else ''}\n"
            f"💬 Сообщений: {msg_cnt}\n"
            f"⚠️ Варны: {warn_bar(warns_count, MAX_WARNS)} {warns_count}/{MAX_WARNS}\n"
            f"🔇 Мут: {mute_str}\n"
            f"🚫 Бан: {'да' if active_ban else 'нет'}\n"
            f"📅 Регистрация: {reg}\n"
            f"🕐 Активность: {seen}"
        )
        return

    if is_cmd(txt, "roles"):
        # Базовые роли показываются всегда, даже если база ролей ещё пустая.
        lines = [
            "100 — Владелец",
            "80 — Главный администратор",
            "60 — Администратор",
            "40 — Модератор",
            "20 — Помощник",
            "0 — Пользователь",
        ]
        custom_roles = []
        try:
            with _db_lock:
                db.ensure_builtin_roles(chat_id, CREATOR_ID)
                roles = db.list_roles(chat_id)
            custom_roles = [
                r for r in roles if int(r.get("priority", 0)) not in BUILTIN_ROLE_PRIORITIES
            ]
        except Exception as roles_error:
            log.warning(f"[/roles] Не удалось прочитать дополнительные роли: {roles_error}")

        text_out = "📋 Список ролей беседы:\n\n" + "\n".join(lines)
        if custom_roles:
            custom_lines = []
            for r in custom_roles:
                badge = (r.get("badge") or "").strip()
                custom_lines.append(
                    f"{r['priority']} — {badge + ' ' if badge else ''}{r['name']}"
                )
            text_out += "\n\n🏷 Дополнительные роли:\n" + "\n".join(custom_lines)
        text_out += (
            "\n\nВыдать: /setrole [ID] [приоритет]"
            "\nСнять роль: /setrole [ID] 0"
        )
        reply(text_out)
        return

    if is_cmd(txt, "staff"):
        with _db_lock:
            roles_all = db.list_roles(chat_id)
            chat_row  = db.get_chat(chat_id)
            members   = db.get_all_members(chat_id)
        member_by_role: Dict[int, List[int]] = {}
        for m in members:
            rid = m.get("role_id")
            if rid:
                member_by_role.setdefault(rid, []).append(m["user_id"])
        groups: List[tuple] = []
        # системные роли
        groups.append((PE.CREATOR, "👑 Создатель", [CREATOR_ID]))
        owner_id = chat_row.get("owner_id") if chat_row else None
        if owner_id and owner_id != CREATOR_ID:
            groups.append((PE.OWNER, "🔱 Владелец", [owner_id]))
        # созданные роли (от приоритета 20)
        for r in roles_all:
            if r["priority"] < 20:
                continue
            uids = member_by_role.get(r["id"], [])
            if not uids:
                continue
            badge = (r.get("badge") or "").strip()
            label = f"{badge + ' ' if badge else ''}{r['name']}"
            groups.append((r["priority"], label, uids))
        groups.sort(key=lambda g: g[0], reverse=True)
        lines = ["👮 Состав администрации:\n"]
        for _, label, uids in groups:
            lines.append(f"{label}:")
            for uid_ in uids:
                lines.append(f"— {fmt_mention(uid_, get_name(uid_))}")
            lines.append("")
        reply("\n".join(lines).rstrip())
        return

    if is_cmd(txt, "banlist"):
        with _db_lock:
            bans = db.get_banlist(chat_id)
        if not bans:
            reply("✅ Список банов пуст.")
            return
        lines = [
            f"  [id{b['user_id']}|id{b['user_id']}] — {fmt_remaining(b['ban_until'])} | {b['reason']}"
            for b in bans[:20]
        ]
        reply(f"🚫 Заблокированные ({len(bans)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "active"):
        threshold = int(time.time()) - 3600
        with _db_lock:
            members = db.get_all_members(chat_id)
        online = [m for m in members if m.get("last_seen", 0) >= threshold]
        reply(f"🟢 Активны за последний час: {len(online)} чел.")
        return

    if is_cmd(txt, "top", "рейтинг", "мтоп"):
        raw = get_args(txt, "top", "рейтинг", "мтоп").strip()
        limit = _int(raw) if raw else 10
        limit = max(1, min(limit or 10, 30))
        cd = check_cooldown(uid, chat_id, "top", 30)
        if cd > 0:
            reply(f"⏳ Команда доступна через {cd} сек.")
            return
        use_cooldown(uid, chat_id, "top")
        with _db_lock:
            top = db.get_top_members(chat_id, limit)
        if not top:
            reply("📊 Статистика сообщений пуста.")
            return
        with _db_lock:
            chat_row = db.get_chat(chat_id)
        total_msgs = chat_row.get("msg_count", 0) if chat_row else 1
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, m in enumerate(top, 1):
            pct   = min(100, int(m["msg_count"] * 100 / total_msgs)) if total_msgs else 0
            bar   = progress_bar(m["msg_count"], top[0]["msg_count"] if top else 1, 8)
            medal = medals[i - 1] if i <= 3 else f"{i}."
            nick  = m.get("nickname") or f"id{m['user_id']}"
            lines.append(f"  {medal} [id{m['user_id']}|{nick}] {bar} {m['msg_count']} ({pct}%)")
        reply(f"📊 Топ {limit} активных:\n" + "\n".join(lines) + f"\n\n💬 Всего сообщений: {total_msgs}")
        return

    if is_cmd(txt, "rules"):
        rules = chat.get("rules_text") if chat else ""
        reply(f"📜 Правила:\n\n{rules}" if rules else "📜 Правила не установлены.")
        return

    if is_cmd(txt, "chatinfo", "info"):
        if not chat:
            reply("ℹ️ Чат не зарегистрирован.")
            return
        owner_name = get_name(chat["owner_id"]) if chat.get("owner_id") else "не назначен"
        unity_info = "нет"
        if chat.get("unity_id"):
            with _db_lock:
                u = db.get_unity(chat["unity_id"])
            unity_info = f"{u['name']} (ID: {u['id']})" if u else "?"
        with _db_lock:
            mbrs   = db.get_all_members(chat_id)
            bans   = db.get_banlist(chat_id)
            now    = int(time.time())
            muted  = [m for m in mbrs if m.get("is_muted") and (m.get("mute_until", 0) == 0 or m.get("mute_until", 0) > now)]
            warned = db.get_all_warned(chat_id)
        filters = []
        if chat.get("filter_mat"):     filters.append("маты")
        if chat.get("filter_links"):   filters.append("ссылки")
        if chat.get("filter_caps"):    filters.append("капс")
        if chat.get("filter_voice"):   filters.append("голосовые")
        if chat.get("filter_forward"): filters.append("пересылки")
        if chat.get("filter_sticker"): filters.append("стикеры")
        slow  = chat.get("slowmode_sec", 0)
        flood_l = chat.get("flood_limit", 0)
        flood_i = chat.get("flood_interval", 0)
        reply(
            f"💬 Информация о беседе\n"
            f"────────────────────\n"
            f"📌 ID: {chat_id}\n"
            f"👑 Владелец: {owner_name}\n"
            f"🔗 Объединение: {unity_info}\n"
            f"👥 Участников в базе: {len(mbrs)}\n"
            f"🚫 Активных банов: {len(bans)}\n"
            f"🔇 Замучено: {len(muted)}\n"
            f"⚠️ С варнами: {len(warned)}\n"
            f"🔕 Тишина: {'вкл' if chat.get('silence_mode') else 'выкл'}\n"
            f"🛡 Антирейд: {'вкл' if chat.get('antiraid') else 'выкл'}\n"
            f"⏳ Медленный режим: {fmt_dur(slow) if slow else 'выкл'}\n"
            f"🌊 Антифлуд: {f'{flood_l} сообщ./{flood_i}с' if flood_l else 'выкл'}\n"
            f"🔧 Фильтры: {', '.join(filters) if filters else 'нет'}\n"
            f"📋 Лог-беседа: {chat.get('log_peer_id') or 'не задана'}\n"
            f"💬 Всего сообщений: {chat.get('msg_count', 0)}"
        )
        return

    if is_cmd(txt, "stats", "statistic"):
        raw = get_args(txt, "stats", "statistic")
        tid, _ = get_target(raw, reply_from_id)
        if not tid:
            tid = uid
        with _db_lock:
            m_row       = db.get_member(tid, chat_id)
            warns_count = db.count_warns(tid, chat_id)
            active_ban  = db.get_active_ban(tid, chat_id)
            cmd_blk     = db.is_cmd_blocked(tid)
            vip_row     = db.get_vip(tid, chat_id)
        t_prio = PE.get(tid, chat_id)
        t_name = get_name(tid)
        nick   = (m_row.get("nickname") or "нет") if m_row else "нет"
        badge  = (m_row.get("badge") or "") if m_row else ""
        reg    = fmt_ts(m_row["reg_date"])  if m_row and m_row.get("reg_date")  else "нет"
        seen   = fmt_ts(m_row["last_seen"]) if m_row and m_row.get("last_seen") else "нет"
        msg_cnt = m_row.get("msg_count", 0) if m_row else 0
        mute_str = "нет"
        if m_row and m_row.get("is_muted"):
            until = m_row.get("mute_until", 0)
            if until == 0 or until > int(time.time()):
                mute_str = fmt_remaining(until)
        ban_str = "нет"
        if active_ban:
            ban_str = f"до {fmt_ts(active_ban['ban_until'])} ({fmt_remaining(active_ban['ban_until'])})"
        vip_str = f" {vip_row['badge']}" if vip_row else ""
        role_str = _role_label(m_row, t_prio, chat_id)
        with _db_lock:
            chat_row = db.get_chat(chat_id)
        total_msgs = max(1, chat_row.get("msg_count", 1) if chat_row else 1)
        pct = min(100, int(msg_cnt * 100 / total_msgs))
        lines = [
            f"👤 Статистика: {fmt_mention(tid, t_name)}{vip_str}",
            f"────────────────────",
            f"🏷 Роль: {role_str}",
            f"🎭 Ник: {nick}{(' | ' + badge) if badge else ''}",
            f"💬 Сообщений: {msg_cnt} ({pct}% от общих)",
            f"⚠️ Варны: {warn_bar(warns_count, MAX_WARNS)} {warns_count}/{MAX_WARNS}",
            f"🔇 Мут: {mute_str}",
            f"🚫 Бан: {ban_str}",
        ]
        if m_row and m_row.get("temprole_id"):
            lines.append(f"⏱ Временная роль до: {fmt_ts(m_row.get('temprole_until', 0))}")
        if cmd_blk:
            lines.append("🔒 Команды: заблокированы")
        lines += [f"📅 Регистрация: {reg}", f"🕐 Активность: {seen}"]
        reply("\n".join(lines))
        return

    if is_cmd(txt, "check"):
        raw = get_args(txt, "check")
        tid, _ = get_target(raw, reply_from_id)
        if not tid:
            return need_id()
        t_name = get_name(tid)
        t_prio = PE.get(tid, chat_id)
        with _db_lock:
            m_row   = db.get_member(tid, chat_id)
            ban     = db.get_active_ban(tid, chat_id)
            warns   = db.count_warns(tid, chat_id)
            cmd_blk = db.is_cmd_blocked(tid)
            vip_row = db.get_vip(tid, chat_id)
        mute_str = "нет"
        if m_row and m_row.get("is_muted"):
            until = m_row.get("mute_until", 0)
            if until == 0 or until > int(time.time()):
                mute_str = fmt_remaining(until)
        ban_str = f"да — {fmt_remaining(ban['ban_until'])}" if ban else "нет"
        role_str = _role_label(m_row, t_prio, chat_id)
        msg_cnt  = m_row.get("msg_count", 0) if m_row else 0
        reply(
            f"🔍 Проверка: {fmt_mention(tid, t_name)}{' ' + vip_row['badge'] if vip_row else ''}\n"
            f"────────────────────\n"
            f"🏷 Роль: {role_str} [{t_prio}]\n"
            f"💬 Сообщений: {msg_cnt}\n"
            f"⚠️ Варны: {warn_bar(warns, MAX_WARNS)} {warns}/{MAX_WARNS}\n"
            f"🔇 Мут: {mute_str}\n"
            f"🚫 Бан: {ban_str}\n"
            f"🔒 Блок команд: {'да' if cmd_blk else 'нет'}"
        )
        return

    if is_cmd(txt, "getban", "baninfo"):
        raw = get_args(txt, "getban", "baninfo")
        tid, _ = get_target(raw, reply_from_id)
        if not tid:
            return need_id()
        with _db_lock:
            ban = db.get_active_ban(tid, chat_id)
        t_name = get_name(tid)
        if not ban:
            reply(f"✅ {fmt_mention(tid, t_name)} не забанен в этой беседе.")
            return
        iss_name = get_name(ban["issued_by"])
        reply(
            f"🚫 Бан: {fmt_mention(tid, t_name)}\n"
            f"📋 Причина: {ban['reason']}\n"
            f"⏱ До: {fmt_ts(ban['ban_until'])} ({fmt_remaining(ban['ban_until'])})\n"
            f"👮 Кто: {iss_name}"
        )
        return

    if is_cmd(txt, "getnick", "gnick"):
        raw = get_args(txt, "getnick", "gnick")
        tid, _ = get_target(raw, reply_from_id)
        if not tid:
            return need_id()
        with _db_lock:
            m_row = db.get_member(tid, chat_id)
        nick   = m_row.get("nickname") if m_row else None
        t_name = get_name(tid)
        reply(f"🎭 Ник {fmt_mention(tid, t_name)}: {nick}" if nick else f"🎭 У {fmt_mention(tid, t_name)} нет ника.")
        return

    if is_cmd(txt, "nicklist", "nlist"):
        with _db_lock:
            nlist = db.get_nicklist(chat_id)
        if not nlist:
            reply("🎭 Ники не установлены.")
            return
        lines = [f"  [id{m['user_id']}|id{m['user_id']}] → {m['nickname']}" for m in nlist]
        reply(f"🎭 Ники ({len(nlist)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "warns", "getwarns"):
        raw = get_args(txt, "warns", "getwarns")
        tid, _ = get_target(raw, reply_from_id)
        if not tid:
            tid = uid
        with _db_lock:
            wlist = db.get_warns(tid, chat_id)
        t_name = get_name(tid)
        cnt    = len(wlist)
        if not wlist:
            reply(f"✅ У {fmt_mention(tid, t_name)} нет активных варнов.")
            return
        lines = [f"  {i}. {w['reason']} ({fmt_ts(w['created_at'])})" for i, w in enumerate(wlist, 1)]
        reply(f"⚠️ Варны {fmt_mention(tid, t_name)}: {warn_bar(cnt, MAX_WARNS)} {cnt}/{MAX_WARNS}\n" + "\n".join(lines))
        return

    if is_cmd(txt, "warnhistory"):
        raw = get_args(txt, "warnhistory")
        tid, _ = get_target(raw, reply_from_id)
        if not tid:
            tid = uid
        with _db_lock:
            hist = db.get_warn_history(tid, chat_id)
        t_name = get_name(tid)
        if not hist:
            reply(f"📋 История варнов {fmt_mention(tid, t_name)} пуста.")
            return
        lines = [f"  {h['action'].upper()}: {h['reason']} ({fmt_ts(h['created_at'])})" for h in hist]
        reply(f"📋 История {fmt_mention(tid, t_name)} ({len(hist)} зап.):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "warnlist", "warnmans"):
        with _db_lock:
            warned = db.get_all_warned(chat_id)
        if not warned:
            reply("✅ Нарушителей нет.")
            return
        lines = [
            f"  {warn_bar(w['cnt'], MAX_WARNS)} [id{w['user_id']}|id{w['user_id']}] — {w['cnt']}/{MAX_WARNS}"
            for w in warned
        ]
        reply(f"⚠️ Нарушители ({len(warned)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "reg", "registration"):
        raw = get_args(txt, "reg", "registration")
        tid, _ = get_target(raw, reply_from_id)
        if not tid:
            tid = uid
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.set_reg(tid, chat_id)
            m_row = db.get_member(tid, chat_id)
        t_name = get_name(tid)
        reg = fmt_ts(m_row["reg_date"]) if m_row and m_row.get("reg_date") else "нет"
        reply(f"📅 Регистрация {fmt_mention(tid, t_name)}: {reg}")
        return

    if is_cmd(txt, "nomention"):
        with _db_lock:
            db.set_mention_disabled(uid, chat_id, True)
        reply(f"🔕 [id{uid}|Вы] отключили упоминания в этой беседе.")
        return

    if is_cmd(txt, "mention"):
        with _db_lock:
            db.set_mention_disabled(uid, chat_id, False)
        reply(f"🔔 [id{uid}|Вы] включили упоминания.")
        return

    if is_cmd(txt, "report"):
        raw = get_args(txt, "report")
        if not raw:
            reply("❌ Укажи текст жалобы: /report [текст]\nИли: /report [ID] [текст] — жалоба на участника")
            return
        tid, text_part = get_target(raw, reply_from_id)
        target_id = tid if text_part else None
        report_text = text_part if text_part else raw
        cd = check_cooldown(uid, chat_id, "report", 120)
        if cd > 0:
            reply(f"⏳ Следующая жалоба через {cd} сек.")
            return
        use_cooldown(uid, chat_id, "report")
        with _db_lock:
            rep_id = db.add_report(chat_id, uid, report_text, target_id)
        t_info = f" на [id{target_id}|{get_name(target_id)}]" if target_id else ""
        reply(f"✅ Жалоба #{rep_id}{t_info} принята. Персонал уведомлён.")
        send_log(chat_id, f"📢 Жалоба #{rep_id} от [id{uid}|{get_name(uid)}]{t_info}:\n{report_text}")
        return

    if is_cmd(txt, "uptime"):
        elapsed = int(time.time()) - START_TIME
        reply(
            f"⏱ {BOT_NAME}\n"
            f"────────────────────\n"
            f"🟢 Время работы: {fmt_uptime(elapsed)}\n"
            f"🖥 Сообщество: {GROUP_NAME} (id{GROUP_ID})"
        )
        return

    # ==========================================================================
    #  ПОМОЩНИК — 10+
    # ==========================================================================

    if is_cmd(txt, "muted"):
        if not can_use_cmd(chat_id, "muted", prio, PE.HELPER): return no_access("muted")
        with _db_lock:
            members = db.get_all_members(chat_id)
        now   = int(time.time())
        muted = [m for m in members if m.get("is_muted") and (m.get("mute_until", 0) == 0 or m.get("mute_until", 0) > now)]
        if not muted:
            reply("✅ Нет замученных участников.")
            return
        lines = [f"  [id{m['user_id']}|id{m['user_id']}] — {fmt_remaining(m.get('mute_until', 0))}" for m in muted]
        reply(f"🔇 Замучены ({len(muted)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "mute"):
        if not can_use_cmd(chat_id, "mute", prio, PE.HELPER): return no_access("mute")
        raw = get_args(txt, "mute")
        tid, minutes, reason = parse_mute_args(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Нельзя замутить пользователя с равным или высшим приоритетом.")
            return
        until_ts = ts_mute(minutes)
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.set_mute(tid, chat_id, until_ts)
        t_name = get_name(tid)
        dur    = fmt_dur((minutes or DEFAULT_MUTE_MINUTES) * 60)
        send(
            chat_id,
            f"🔇 {fmt_mention(tid, t_name)} замучен на {dur}\n📋 {reason}\n⏱ До: {fmt_ts(until_ts)}",
            keyboard=make_unmute_keyboard(tid, chat_id),
            reply_to=msg_id,
        )
        with _db_lock:
            db.log_action(chat_id, uid, "mute", tid, f"{dur} | {reason}")
        return

    if is_cmd(txt, "unmute"):
        if not can_use_cmd(chat_id, "unmute", prio, PE.HELPER): return no_access("unmute")
        raw = get_args(txt, "unmute")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.remove_mute(tid, chat_id)
            db.log_action(chat_id, uid, "unmute", tid, "снят мут")
        reply(f"🔊 {fmt_mention(tid, get_name(tid))} размучен.")
        return

    if is_cmd(txt, "kick"):
        if not can_use_cmd(chat_id, "kick", prio, PE.HELPER): return no_access("kick")
        raw = get_args(txt, "kick")
        tid, reason = parse_target_reason(raw, DEFAULT_KICK_REASON, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Нельзя кикнуть пользователя с равным или высшим приоритетом.")
            return
        t_name = get_name(tid)
        ok, kick_err = kick(chat_id, tid)
        if ok:
            reply(f"👢 {fmt_mention(tid, t_name)} исключён.\n📋 Причина: {reason}")
            with _db_lock:
                db.log_action(chat_id, uid, "kick", tid, reason)
            send_log(chat_id, f"👢 Кик | {t_name} (id{tid}) | {reason} | кто: id{uid}")
        elif kick_err == "not_in_chat":
            reply(f"❌ {fmt_mention(tid, t_name)} не состоит в этой беседе.")
        else:
            reply(f"❌ Не удалось кикнуть {fmt_mention(tid, t_name)}.")
        return

    if is_cmd(txt, "warn"):
        if not can_use_cmd(chat_id, "warn", prio, PE.HELPER): return no_access("warn")
        raw = get_args(txt, "warn")
        tid, reason = parse_target_reason(raw, DEFAULT_WARN_REASON, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            total = db.add_warn(tid, chat_id, uid, reason)
        t_name = get_name(tid)
        bar    = warn_bar(total, MAX_WARNS)
        reply(f"⚠️ {fmt_mention(tid, t_name)} предупреждён {bar} {total}/{MAX_WARNS}\n📋 {reason}")
        with _db_lock:
            db.log_action(chat_id, uid, "warn", tid, reason)
        send_log(chat_id, f"⚠️ Варн {total}/{MAX_WARNS} | {t_name} (id{tid}) | {reason} | кто: id{uid}")
        if total >= MAX_WARNS:
            ban_ts = ts_ban(AUTO_BAN_DAYS)
            with _db_lock:
                db.add_ban(tid, uid, f"Авто-бан: {MAX_WARNS} предупреждения", ban_ts, chat_id)
            kick(chat_id, tid)
            reply(f"🚫 {fmt_mention(tid, t_name)} авто-забанен на {AUTO_BAN_DAYS} дней за {MAX_WARNS} варна.")
        return

    if is_cmd(txt, "unwarn"):
        if not can_use_cmd(chat_id, "unwarn", prio, PE.HELPER): return no_access("unwarn")
        raw = get_args(txt, "unwarn")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            ok  = db.remove_last_warn(tid, chat_id, uid)
            cnt = db.count_warns(tid, chat_id)
        t_name = get_name(tid)
        if ok:
            reply(f"✅ Последний варн снят. {warn_bar(cnt, MAX_WARNS)} {cnt}/{MAX_WARNS}")
            with _db_lock:
                db.log_action(chat_id, uid, "unwarn", tid, f"снят, осталось {cnt}")
        else:
            reply(f"ℹ️ У {fmt_mention(tid, t_name)} нет варнов.")
        return

    if is_cmd(txt, "clearwarns"):
        if not can_use_cmd(chat_id, "clearwarns", prio, PE.HELPER): return no_access("clearwarns")
        raw = get_args(txt, "clearwarns")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.clear_warns(tid, chat_id)
        reply(f"🗑 Все варны {fmt_mention(tid, get_name(tid))} очищены.")
        return

    if is_cmd(txt, "setnick", "snick"):
        if not can_use_cmd(chat_id, "setnick", prio, PE.HELPER): return no_access("setnick")
        raw = get_args(txt, "setnick", "snick")
        tid, nick = parse_target_text(raw, reply_from_id)
        if not tid or not nick:
            reply("❌ Пример: /setnick 123456 Крутой Ник")
            return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.set_nickname(tid, chat_id, nick)
        reply(f"🎭 Ник {fmt_mention(tid, get_name(tid))} → {nick}")
        return

    if is_cmd(txt, "removenick", "rnick"):
        if not can_use_cmd(chat_id, "removenick", prio, PE.HELPER): return no_access("removenick")
        raw = get_args(txt, "removenick", "rnick")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.set_nickname(tid, chat_id, None)
        reply(f"🗑 Ник {fmt_mention(tid, get_name(tid))} удалён.")
        return

    if is_cmd(txt, "nonames"):
        if not can_use_cmd(chat_id, "nonames", prio, PE.HELPER): return no_access("nonames")
        with _db_lock:
            members = db.get_all_members(chat_id)
        count = 0
        for m in members:
            if m.get("nickname") and m.get("priority", 0) < prio:
                with _db_lock:
                    db.set_nickname(m["user_id"], chat_id, None)
                count += 1
        reply(f"🗑 Удалено ников: {count}")
        return

    if is_cmd(txt, "getbynick", "findnick"):
        if not can_use_cmd(chat_id, "getbynick", prio, PE.HELPER): return no_access("getbynick")
        raw = get_args(txt, "getbynick", "findnick")
        if not raw:
            reply("❌ Укажи часть ника.")
            return
        with _db_lock:
            found = db.get_by_nick_part(chat_id, raw)
        if not found:
            reply(f"❌ Ник «{raw}» не найден.")
            return
        lines = [f"  [id{m['user_id']}|id{m['user_id']}] → {m['nickname']}" for m in found]
        reply(f"🔍 Найдено ({len(found)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "zov"):
        if not can_use_cmd(chat_id, "zov", prio, PE.HELPER): return no_access("zov")
        raw = get_args(txt, "zov")
        if not raw:
            reply("❌ Укажи текст призыва: /zov [текст]")
            return
        # Только создатель использует /zov без таймера.
        if uid != CREATOR_ID:
            cd = check_cooldown(uid, chat_id, "zov", 300)
            if cd > 0:
                reply(f"⏳ Команда доступна через {fmt_dur(cd)}.")
                return
            use_cooldown(uid, chat_id, "zov")
        with _db_lock:
            disabled_ids = {r["user_id"] for r in db.get_mention_disabled(chat_id)}
            members      = db.get_all_members(chat_id)
        mentions = [f"[id{m['user_id']}|·]" for m in members if m["user_id"] > 0 and m["user_id"] not in disabled_ids]
        send(chat_id, f"📢 {raw}")
        for i in range(0, len(mentions), 30):
            send(chat_id, " ".join(mentions[i:i + 30]))
        return

    if is_cmd(txt, "note"):
        if not can_use_cmd(chat_id, "note", prio, PE.HELPER): return no_access("note")
        raw = get_args(txt, "note")
        if not raw:
            with _db_lock:
                notes = db.get_notes(chat_id, 15)
            if not notes:
                reply("📝 Заметок нет. Добавить: /note [текст]")
            else:
                lines = [f"  #{n['id']} [{fmt_ts(n['created_at'])}] [id{n['user_id']}|...]: {n['text'][:60]}" for n in notes]
                reply("📝 Заметки:\n" + "\n".join(lines) + "\n\nУдалить: /delnote [id]")
        else:
            title = None
            if raw.startswith('"') and '"' in raw[1:]:
                end = raw.index('"', 1)
                title = raw[1:end]
                raw = raw[end + 1:].strip()
            with _db_lock:
                nid = db.add_note(chat_id, uid, raw, title)
            reply(f"📝 Заметка #{nid} сохранена{f' «{title}»' if title else ''}.")
        return

    if is_cmd(txt, "delnote"):
        if not can_use_cmd(chat_id, "delnote", prio, PE.HELPER): return no_access("delnote")
        raw = get_args(txt, "delnote").strip()
        nid = _int(raw)
        if not nid:
            reply("❌ Укажи ID заметки: /delnote [id]")
            return
        with _db_lock:
            ok = db.delete_note(nid, chat_id)
        reply(f"🗑 Заметка #{nid} удалена." if ok else f"❌ Заметка #{nid} не найдена.")
        return

    if is_cmd(txt, "logs"):
        if not can_use_cmd(chat_id, "logs", prio, PE.HELPER): return no_access("logs")
        raw = get_args(txt, "logs")
        tid, _ = get_target(raw, None)
        if tid:
            with _db_lock:
                logs = db.get_logs_by_user(chat_id, tid, 15)
            header = f"📋 Логи {fmt_mention(tid, f'id{tid}')}:"
        else:
            with _db_lock:
                logs = db.get_logs(chat_id, 20)
            header = "📋 Последние действия:"
        if not logs:
            reply("📋 Логи пусты.")
            return
        lines = [
            f"[{fmt_ts(l['created_at'])}] {l['action']} | id{l['actor_id']}→id{l['target_id']} | {l['details']}"
            for l in logs
        ]
        reply(header + "\n" + "\n".join(lines))
        return

    if is_cmd(txt, "reports"):
        if not can_use_cmd(chat_id, "reports", prio, PE.HELPER): return no_access("reports")
        with _db_lock:
            reps = db.get_reports(chat_id, only_new=True)
        if not reps:
            reply("✅ Непросмотренных жалоб нет.")
            return
        lines = [
            f"  #{r['id']} от id{r['user_id']}"
            + (f" на id{r['target_id']}" if r.get("target_id") else "")
            + f": {r['text'][:60]}"
            for r in reps[:10]
        ]
        reply(f"📢 Новые жалобы ({len(reps)}):\n" + "\n".join(lines) + "\n\nЗакрыть: /closerep [id]")
        return

    if is_cmd(txt, "closerep"):
        if not can_use_cmd(chat_id, "closerep", prio, PE.HELPER): return no_access("closerep")
        raw = get_args(txt, "closerep").strip()
        rid = _int(raw)
        if not rid:
            reply("❌ Укажи ID жалобы: /closerep [id]")
            return
        with _db_lock:
            ok = db.mark_report_reviewed(rid, chat_id)
        reply(f"✅ Жалоба #{rid} закрыта." if ok else f"❌ Жалоба #{rid} в этой беседе не найдена или уже закрыта.")
        return

    if is_cmd(txt, "closeall"):
        if not can_use_cmd(chat_id, "closeall", prio, PE.HELPER): return no_access("closeall")
        with _db_lock:
            count = db.mark_all_reports_reviewed(chat_id)
        reply(f"✅ Закрыто жалоб: {count}.")
        return

    if is_cmd(txt, "viplist"):
        if not can_use_cmd(chat_id, "viplist", prio, PE.HELPER): return no_access("viplist")
        with _db_lock:
            vips = db.get_vip_list(chat_id)
        if not vips:
            reply("⭐ VIP участников нет.")
            return
        lines = [f"  {v['badge']} [id{v['user_id']}|id{v['user_id']}]" for v in vips]
        reply(f"⭐ VIP участники ({len(vips)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "mediastats", "mstats"):
        if not can_use_cmd(chat_id, "mediastats", prio, PE.HELPER): return no_access("mediastats")
        raw = get_args(txt, "mediastats", "mstats")
        tid, _ = get_target(raw, reply_from_id)
        with _db_lock:
            stats = db.get_media_stats(chat_id, tid)
        if not stats:
            reply("📊 Медиа статистика пуста.")
            return
        if tid:
            s = stats[0] if stats else {}
            reply(
                f"📊 Медиа {fmt_mention(tid, f'id{tid}')}:\n"
                f"  🖼 Фото: {s.get('photos', 0)}\n"
                f"  🎬 Видео: {s.get('videos', 0)}\n"
                f"  📄 Документы: {s.get('docs', 0)}\n"
                f"  🎭 Стикеры: {s.get('stickers', 0)}\n"
                f"  🎤 Голосовые: {s.get('voices', 0)}"
            )
        else:
            lines = [
                f"  {i}. [id{s['user_id']}|id{s['user_id']}] — "
                f"🖼{s.get('photos',0)} 🎬{s.get('videos',0)} 📄{s.get('docs',0)} "
                f"🎭{s.get('stickers',0)} 🎤{s.get('voices',0)}"
                for i, s in enumerate(stats[:10], 1)
            ]
            reply(f"📊 Топ медиа активность:\n" + "\n".join(lines))
        return

    # ==========================================================================
    #  МОДЕРАТОР — 30+
    # ==========================================================================

    if is_cmd(txt, "ban"):
        if not can_use_cmd(chat_id, "ban", prio, PE.MODER): return no_access("ban")
        raw = get_args(txt, "ban")
        tid, days, reason = parse_ban_args(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Нельзя забанить пользователя с равным или высшим приоритетом.")
            return
        with _db_lock:
            existing = db.get_active_ban(tid, chat_id)
        if existing:
            reply(f"ℹ️ Уже забанен. До: {fmt_ts(existing['ban_until'])} ({fmt_remaining(existing['ban_until'])})")
            return
        ban_ts = ts_ban(days)
        with _db_lock:
            db.add_ban(tid, uid, reason, ban_ts, chat_id)
        kick(chat_id, tid)
        t_name   = get_name(tid)
        dur_info = f"{days} дн." if days else "навсегда"
        send(
            chat_id,
            f"🚫 {fmt_mention(tid, t_name)} забанен {dur_info}\n📋 {reason}\n⏱ До: {fmt_ts(ban_ts)}",
            keyboard=make_unban_keyboard(tid, chat_id),
            reply_to=msg_id,
        )
        with _db_lock:
            db.log_action(chat_id, uid, "ban", tid, f"{dur_info} | {reason}")
        return

    if is_cmd(txt, "unban"):
        if not can_use_cmd(chat_id, "unban", prio, PE.MODER): return no_access("unban")
        raw = get_args(txt, "unban")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            ok = db.remove_ban(tid, chat_id)
        t_name = get_name(tid)
        reply(f"✅ {fmt_mention(tid, t_name)} разбанен." if ok else f"ℹ️ {fmt_mention(tid, t_name)} не забанен.")
        if ok:
            with _db_lock:
                db.log_action(chat_id, uid, "unban", tid, "разбанен")
        return

    if is_cmd(txt, "setrole"):
        if not can_use_cmd(chat_id, "setrole", prio, PE.MODER): return no_access("setrole")
        raw = get_args(txt, "setrole")
        tid, role_prio, role_name_str = parse_target_role(raw, reply_from_id)
        if not tid or (role_prio is None and not role_name_str):
            reply("❌ Пример: /setrole [ID] [приоритет/название роли]")
            return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return

        # Приоритет 0 означает обычного пользователя и снимает текущую роль.
        if role_prio == 0:
            with _db_lock:
                db.upsert_member(tid, chat_id)
                db.update_member_role(tid, chat_id, None, PE.MEMBER)
            reply(f"✅ {fmt_mention(tid, get_name(tid))} теперь Пользователь [0].")
            return

        with _db_lock:
            db.ensure_builtin_roles(chat_id, CREATOR_ID)
            role = (
                db.get_role_by_priority(role_prio, chat_id)
                if role_prio is not None
                else db.get_role_by_name(role_name_str, chat_id)
            )
        if not role:
            reply("❌ Роль не найдена. Список: /roles")
            return
        if role["priority"] == PE.OWNER and uid != CREATOR_ID:
            reply("❌ Роль Владелец [100] может выдавать только создатель бота.")
            return
        if role["priority"] >= prio:
            reply(f"❌ Нельзя назначить роль [{role['priority']}] — она не ниже вашего приоритета [{prio}].")
            return
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.update_member_role(tid, chat_id, role["id"], role["priority"])
        reply(f"✅ {fmt_mention(tid, get_name(tid))} роль: {role['name']} [{role['priority']}]")
        return

    if is_cmd(txt, "removerole"):
        if not can_use_cmd(chat_id, "removerole", prio, PE.MODER): return no_access("removerole")
        raw = get_args(txt, "removerole")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.update_member_role(tid, chat_id, None, 0)
        reply(f"🗑 Роль {fmt_mention(tid, get_name(tid))} снята.")
        return

    if is_cmd(txt, "triggers"):
        if not can_use_cmd(chat_id, "triggers", prio, PE.MODER): return no_access("triggers")
        with _db_lock:
            triggers = db.get_triggers(chat_id)
        if not triggers:
            reply("⚡ Триггеры не установлены.")
            return
        lines = [
            f"  «{t['keyword']}» [{t.get('match_type','contains')}] → {t['response'][:40]}{'...' if len(t['response']) > 40 else ''} (исп: {t.get('use_count',0)})"
            for t in triggers
        ]
        reply(f"⚡ Триггеры ({len(triggers)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "cleartriggers"):
        if not can_use_cmd(chat_id, "cleartriggers", prio, PE.MODER): return no_access("cleartriggers")
        with _db_lock:
            db.wipe(chat_id, "triggers")
        reply("🗑 Все триггеры удалены.")
        return

    if is_cmd(txt, "addword"):
        if not can_use_cmd(chat_id, "addword", prio, PE.MODER): return no_access("addword")
        raw = get_args(txt, "addword")
        parts = raw.split(maxsplit=1)
        if not parts:
            reply("❌ Формат: /addword [слово] [delete|warn|mute|kick]\n(действие по умолчанию: delete)")
            return
        word   = parts[0].strip().lower()
        action = parts[1].strip().lower() if len(parts) > 1 else "delete"
        if action not in {"delete", "warn", "mute", "kick"}:
            reply("❌ Доступные действия: delete, warn, mute, kick")
            return
        with _db_lock:
            db.add_blacklist_word(chat_id, word, uid, action)
        reply(f"🚫 Слово «{word}» добавлено в блеклист. Действие: {action}")
        return

    if is_cmd(txt, "delword"):
        if not can_use_cmd(chat_id, "delword", prio, PE.MODER): return no_access("delword")
        raw = get_args(txt, "delword").strip()
        if not raw:
            reply("❌ Укажи слово: /delword [слово]")
            return
        with _db_lock:
            ok = db.remove_blacklist_word(chat_id, raw)
        reply(f"✅ Слово «{raw}» удалено из блеклиста." if ok else f"❌ Слово «{raw}» не найдено.")
        return

    if is_cmd(txt, "wordlist", "blacklist"):
        if not can_use_cmd(chat_id, "wordlist", prio, PE.MODER): return no_access("wordlist")
        with _db_lock:
            words = db.get_blacklist_words(chat_id)
        if not words:
            reply("✅ Блеклист пуст. Добавить: /addword [слово]")
            return
        lines = [f"  «{w['word']}» — {w['action']}" for w in words]
        reply(f"🚫 Блеклист слов ({len(words)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "addwhite"):
        if not can_use_cmd(chat_id, "addwhite", prio, PE.MODER): return no_access("addwhite")
        raw = get_args(txt, "addwhite").strip()
        if not raw:
            reply("❌ Укажи домен: /addwhite vk.com")
            return
        domain = extract_domain(raw) if "." in raw else raw.lower()
        with _db_lock:
            db.add_whitelist_link(chat_id, domain, uid)
        reply(f"✅ Домен «{domain}» добавлен в вайтлист ссылок.")
        return

    if is_cmd(txt, "delwhite"):
        if not can_use_cmd(chat_id, "delwhite", prio, PE.MODER): return no_access("delwhite")
        raw = get_args(txt, "delwhite").strip()
        if not raw:
            reply("❌ Укажи домен: /delwhite vk.com")
            return
        with _db_lock:
            ok = db.remove_whitelist_link(chat_id, raw.lower())
        reply(f"✅ Домен «{raw}» удалён из вайтлиста." if ok else f"❌ Домен «{raw}» не найден.")
        return

    if is_cmd(txt, "whitelist", "wlist"):
        if not can_use_cmd(chat_id, "whitelist", prio, PE.MODER): return no_access("whitelist")
        with _db_lock:
            links = db.get_whitelist_links(chat_id)
        if not links:
            reply("✅ Вайтлист пуст. Добавить: /addwhite [домен]")
            return
        lines = [f"  ✅ {l['domain']}" for l in links]
        reply(f"🔗 Разрешённые домены ({len(links)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "gzov"):
        if not can_use_cmd(chat_id, "gzov", prio, PE.MODER): return no_access("gzov")
        raw = get_args(txt, "gzov")
        if not raw:
            reply("❌ Укажи текст: /gzov [текст]")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        ok, _ = unity_broadcast(chat["unity_id"], lambda cid: send(cid, f"📢 {raw}"))
        reply(f"📢 Отправлено в {ok}/{len(chats)} бесед.")
        return

    if is_cmd(txt, "gkick"):
        if not can_use_cmd(chat_id, "gkick", prio, PE.MODER): return no_access("gkick")
        raw = get_args(txt, "gkick")
        tid, reason = parse_target_reason(raw, DEFAULT_KICK_REASON, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        ok, _ = unity_broadcast(chat["unity_id"], lambda cid: kick(cid, tid))
        reply(f"👢 {fmt_mention(tid, get_name(tid))} исключён из {ok}/{len(chats)} бесед.")
        return

    if is_cmd(txt, "gsetnick", "gsnick"):
        if not can_use_cmd(chat_id, "gsetnick", prio, PE.MODER): return no_access("gsetnick")
        raw = get_args(txt, "gsetnick", "gsnick")
        tid, nick = parse_target_text(raw, reply_from_id)
        if not tid or not nick:
            reply("❌ Укажи ID и ник: /gsetnick [ID] [ник]")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        for c in chats:
            with _db_lock:
                db.upsert_member(tid, c["chat_id"])
                db.set_nickname(tid, c["chat_id"], nick)
        reply(f"🎭 Глобальный ник {fmt_mention(tid, get_name(tid))} → {nick} ({len(chats)} бесед)")
        return

    if is_cmd(txt, "gremovenick", "grnick"):
        if not can_use_cmd(chat_id, "gremovenick", prio, PE.MODER): return no_access("gremovenick")
        raw = get_args(txt, "gremovenick", "grnick")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        for c in chats:
            with _db_lock:
                db.set_nickname(tid, c["chat_id"], None)
        reply(f"🗑 Глобальный ник {fmt_mention(tid, get_name(tid))} удалён ({len(chats)} бесед)")
        return

    if is_cmd(txt, "gremoverole", "grr"):
        if not can_use_cmd(chat_id, "gremoverole", prio, PE.MODER): return no_access("gremoverole")
        raw = get_args(txt, "gremoverole", "grr")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        for c in chats:
            with _db_lock:
                db.update_member_role(tid, c["chat_id"], None, 0)
        reply(f"🗑 Роль {fmt_mention(tid, get_name(tid))} снята глобально ({len(chats)} бесед)")
        return

    if is_cmd(txt, "gmoder"):
        if not can_use_cmd(chat_id, "gmoder", prio, PE.MODER): return no_access("gmoder")
        raw = get_args(txt, "gmoder")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        for c in chats:
            with _db_lock:
                db.upsert_member(tid, c["chat_id"])
                db.update_member_role(tid, c["chat_id"], None, PE.MODER)
        reply(f"⚔️ {fmt_mention(tid, get_name(tid))} глобально модератором ({len(chats)} бесед)")
        return

    # ==========================================================================
    #  АДМИНИСТРАТОР — 50+
    # ==========================================================================

    if is_cmd(txt, "moder"):
        if not can_use_cmd(chat_id, "moder", prio, PE.ADMIN): return no_access("moder")
        raw = get_args(txt, "moder")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.update_member_role(tid, chat_id, None, PE.MODER)
        reply(f"⚔️ {fmt_mention(tid, get_name(tid))} назначен модератором.")
        return

    if is_cmd(txt, "helper", "addhelper"):
        if not can_use_cmd(chat_id, "helper", prio, PE.ADMIN): return no_access("helper")
        raw = get_args(txt, "helper", "addhelper")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.update_member_role(tid, chat_id, None, PE.HELPER)
        reply(f"🔰 {fmt_mention(tid, get_name(tid))} назначен помощником.")
        return

    if is_cmd(txt, "admin", "addadmin"):
        if not can_use_cmd(chat_id, "admin", prio, PE.ADMIN): return no_access("admin")
        raw = get_args(txt, "admin", "addadmin")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.update_member_role(tid, chat_id, None, PE.ADMIN)
        reply(f"🛡 {fmt_mention(tid, get_name(tid))} назначен администратором.")
        return

    if is_cmd(txt, "gban", "gblock"):
        if not can_use_cmd(chat_id, "gban", prio, PE.ADMIN): return no_access("gban")
        raw = get_args(txt, "gban", "gblock")
        tid, days, reason = parse_ban_args(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        ban_ts   = ts_ban(days)
        unity_id = chat["unity_id"]
        with _db_lock:
            db.add_ban(tid, uid, reason, ban_ts, None, unity_id, "global")
            chats = db.get_chats_by_unity(unity_id)
        for c in chats:
            with _db_lock:
                db.add_ban(tid, uid, reason, ban_ts, c["chat_id"])
            kick(c["chat_id"], tid)
        t_name   = get_name(tid)
        dur_info = f"{days} дн." if days else "навсегда"
        reply(f"🚫 Глобальный бан {fmt_mention(tid, t_name)} {dur_info}\n📋 {reason} | {len(chats)} бесед")
        send_log(chat_id, f"🌐 Глобальный бан | {t_name} (id{tid}) | {dur_info} | {reason}")
        return

    if is_cmd(txt, "gunban", "gunblock"):
        if not can_use_cmd(chat_id, "gunban", prio, PE.ADMIN): return no_access("gunban")
        raw = get_args(txt, "gunban", "gunblock")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        unity_id = chat["unity_id"]
        with _db_lock:
            db.remove_ban(tid, None, unity_id)
            chats = db.get_chats_by_unity(unity_id)
        for c in chats:
            with _db_lock:
                db.remove_ban(tid, c["chat_id"])
        reply(f"✅ {fmt_mention(tid, get_name(tid))} глобально разбанен ({len(chats)} бесед)")
        return

    if is_cmd(txt, "gmute", "gm"):
        if not can_use_cmd(chat_id, "gmute", prio, PE.ADMIN): return no_access("gmute")
        raw = get_args(txt, "gmute", "gm")
        tid, minutes, reason = parse_mute_args(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        until_ts = ts_mute(minutes)
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        for c in chats:
            with _db_lock:
                db.upsert_member(tid, c["chat_id"])
                db.set_mute(tid, c["chat_id"], until_ts)
        reply(f"🔇 {fmt_mention(tid, get_name(tid))} замучен глобально\n⏱ До: {fmt_ts(until_ts)}")
        return

    if is_cmd(txt, "gms"):
        if not can_use_cmd(chat_id, "gms", prio, PE.ADMIN): return no_access("gms")
        raw = get_args(txt, "gms")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        for c in chats:
            with _db_lock:
                db.remove_mute(tid, c["chat_id"])
        reply(f"🔊 {fmt_mention(tid, get_name(tid))} размучен глобально ({len(chats)} бесед)")
        return

    if is_cmd(txt, "grole", "gsetrole"):
        if not can_use_cmd(chat_id, "grole", prio, PE.ADMIN): return no_access("grole")
        raw = get_args(txt, "grole", "gsetrole")
        tid, role_prio, role_name_str = parse_target_role(raw, reply_from_id)
        if not tid:
            reply("❌ Укажи ID и роль: /grole [ID] [роль]")
            return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        unity_id = chat["unity_id"]
        with _db_lock:
            chats = db.get_chats_by_unity(unity_id)
        assigned = 0
        for c in chats:
            with _db_lock:
                role = (
                    db.get_role_by_priority(role_prio, c["chat_id"])
                    if role_prio
                    else db.get_role_by_name(role_name_str, c["chat_id"])
                )
            if role:
                if role["priority"] >= prio:
                    reply(f"❌ Нельзя назначить роль [{role['priority']}] — она не ниже вашего приоритета [{prio}].")
                    return
                with _db_lock:
                    db.upsert_member(tid, c["chat_id"])
                    db.update_member_role(tid, c["chat_id"], role["id"], role["priority"])
                assigned += 1
        if assigned == 0:
            reply("❌ Роль не найдена ни в одной беседе объединения. Создай: /gnewrole")
            return
        rname = role_name_str or str(role_prio)
        reply(f"✅ {fmt_mention(tid, get_name(tid))} глобальная роль: {rname} ({assigned}/{len(chats)} бесед)")
        return

    if is_cmd(txt, "ghelper", "gaddhelper"):
        if not can_use_cmd(chat_id, "ghelper", prio, PE.ADMIN): return no_access("ghelper")
        raw = get_args(txt, "ghelper", "gaddhelper")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        for c in chats:
            with _db_lock:
                db.upsert_member(tid, c["chat_id"])
                db.update_member_role(tid, c["chat_id"], None, PE.HELPER)
        reply(f"🔰 {fmt_mention(tid, get_name(tid))} глобально помощник ({len(chats)} бесед)")
        return

    if is_cmd(txt, "del", "delete"):
        if not can_use_cmd(chat_id, "del", prio, PE.ADMIN): return no_access("del")
        if not reply_id:
            reply("❌ Ответь реплаем на сообщение для удаления.")
            return
        delete_msg(reply_id, chat_id)
        delete_msg(msg_id, chat_id, cmid=cmid)
        return

    if is_cmd(txt, "addtrigger"):
        if not can_use_cmd(chat_id, "addtrigger", prio, PE.ADMIN): return no_access("addtrigger")
        raw = get_args(txt, "addtrigger")
        if "|" not in raw:
            reply(
                "❌ Формат: /addtrigger [тип:]слово | ответ\n"
                "Типы: contains (по умолчанию), exact, startswith, regex"
            )
            return
        parts    = raw.split("|", 1)
        key_part = parts[0].strip()
        response = parts[1].strip()
        match_type = "contains"
        if ":" in key_part:
            t, kw = key_part.split(":", 1)
            t = t.strip().lower()
            if t in {"exact", "startswith", "regex", "contains"}:
                match_type = t
                key_part   = kw.strip()
        keyword = key_part.lower()
        if not keyword or not response:
            reply("❌ Слово и ответ не могут быть пустыми.")
            return
        with _db_lock:
            db.add_trigger(chat_id, keyword, response, uid, match_type)
        reply(f"✅ Триггер «{keyword}» [{match_type}] добавлен.")
        return

    if is_cmd(txt, "deltrigger"):
        if not can_use_cmd(chat_id, "deltrigger", prio, PE.ADMIN): return no_access("deltrigger")
        raw = get_args(txt, "deltrigger").strip()
        if not raw:
            reply("❌ Укажи слово: /deltrigger [слово]")
            return
        with _db_lock:
            ok = db.remove_trigger(chat_id, raw)
        reply(f"🗑 Триггер «{raw}» удалён." if ok else f"❌ Триггер «{raw}» не найден.")
        return

    if is_cmd(txt, "filter"):
        if not can_use_cmd(chat_id, "filter", prio, PE.ADMIN): return no_access("filter")
        raw = get_args(txt, "filter").lower().split()
        if len(raw) < 2:
            reply("❌ Формат: /filter [маты|ссылки|капс|голосовые|стикеры|пересылки] [вкл|выкл]")
            return
        ftype, flag = raw[0], raw[1]
        state     = 1 if flag in {"вкл", "on", "1"} else 0
        field_map = {
            "маты":      "filter_mat",
            "ссылки":    "filter_links",
            "капс":      "filter_caps",
            "голосовые": "filter_voice",
            "стикеры":   "filter_sticker",
            "пересылки": "filter_forward",
        }
        field = field_map.get(ftype)
        if not field:
            reply("❌ Тип фильтра: маты, ссылки, капс, голосовые, стикеры, пересылки")
            return
        with _db_lock:
            db.set_chat_field(chat_id, field, state)
        reply(f"🔧 Фильтр «{ftype}» {'включён' if state else 'выключен'}.")
        return

    if is_cmd(txt, "setcmd"):
        if not can_use_cmd(chat_id, "setcmd", prio, PE.OWNER): return no_access("setcmd")
        raw = get_args(txt, "setcmd").split()
        if len(raw) < 3:
            reply(
                "❌ Формат: /setcmd [команда] [приоритет|диапазон|все] [вкл|выкл]\n"
                "Примеры:\n"
                "  /setcmd ban 30 вкл\n"
                "  /setcmd syncban 100-104 вкл — только создатель"
            )
            return

        cmd_name     = canonical_cmd_name(raw[0])
        priority_arg = raw[1].lower().replace("–", "-").replace("—", "-")
        flag         = raw[2].lower()

        # Владелец может настраивать обычные приоритеты 0–99.
        # Создатель может настраивать абсолютно все системные приоритеты 0–105,
        # включая диапазоны 100–104 и собственный приоритет 105.
        max_priority = PE.CREATOR if prio >= PE.CREATOR else 99

        if priority_arg in {"все", "all", "*"}:
            priority_from, priority_to = 0, max_priority
        else:
            match = re.fullmatch(r"(\d+)(?:-(\d+))?", priority_arg)
            if not match:
                reply("❌ Приоритет укажите числом, диапазоном 100-104 или словом «все».")
                return
            priority_from = int(match.group(1))
            priority_to   = int(match.group(2) or priority_from)

        if priority_from > priority_to:
            reply("❌ Начало диапазона не может быть больше конца.")
            return
        if not 0 <= priority_from <= priority_to <= max_priority:
            if prio >= PE.CREATOR:
                reply(f"❌ Для создателя доступны приоритеты от 0 до {PE.CREATOR}.")
            else:
                reply("❌ Владельцу доступны приоритеты от 0 до 99. Приоритеты 100–105 настраивает только создатель.")
            return
        if cmd_name in _NEVER_OVERRIDABLE_COMMANDS:
            reply(f"❌ Права команды /{cmd_name} нельзя переопределять.")
            return
        if cmd_name in _CREATOR_MANAGED_COMMANDS and prio < PE.CREATOR:
            reply(f"❌ Права системной команды /{cmd_name} может менять только создатель бота.")
            return
        if flag not in {"вкл", "on", "1", "yes", "true", "выкл", "off", "0", "no", "false"}:
            reply("❌ Последний аргумент: вкл или выкл.")
            return

        allowed = flag in {"вкл", "on", "1", "yes", "true"}
        with _db_lock:
            for target_priority in range(priority_from, priority_to + 1):
                db.set_cmd_override(chat_id, cmd_name, target_priority, allowed)

        priority_label = (
            f"[{priority_from}]"
            if priority_from == priority_to
            else f"[{priority_from}–{priority_to}]"
        )
        reply(
            f"✅ Команда /{cmd_name} для приоритетов {priority_label}: "
            f"{'разрешена' if allowed else 'запрещена'}."
        )
        return

    if is_cmd(txt, "vip"):
        if not can_use_cmd(chat_id, "vip", prio, PE.ADMIN): return no_access("vip")
        raw = get_args(txt, "vip")
        parts = raw.split(maxsplit=2)
        tid, _ = get_target(parts[0] if parts else "", reply_from_id)
        if not tid: return need_id()
        badge = parts[1] if len(parts) > 1 else "⭐"
        with _db_lock:
            db.add_vip(tid, chat_id, badge, uid)
        reply(f"{badge} {fmt_mention(tid, get_name(tid))} получил VIP статус.")
        return

    if is_cmd(txt, "devip"):
        if not can_use_cmd(chat_id, "devip", prio, PE.ADMIN): return no_access("devip")
        raw = get_args(txt, "devip")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        with _db_lock:
            ok = db.remove_vip(tid, chat_id)
        reply(f"✅ VIP статус {fmt_mention(tid, get_name(tid))} снят." if ok else f"ℹ️ VIP не найден.")
        return

    if is_cmd(txt, "setbadge"):
        if not can_use_cmd(chat_id, "setbadge", prio, PE.ADMIN): return no_access("setbadge")
        raw = get_args(txt, "setbadge")
        tid, badge = parse_target_text(raw, reply_from_id)
        if not tid or not badge:
            reply("❌ Пример: /setbadge [ID] 🔥")
            return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.set_badge(tid, chat_id, badge)
        reply(f"🎖 Бейдж {fmt_mention(tid, get_name(tid))}: {badge}")
        return

    if is_cmd(txt, "removebadge"):
        if not can_use_cmd(chat_id, "removebadge", prio, PE.ADMIN): return no_access("removebadge")
        raw = get_args(txt, "removebadge")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        with _db_lock:
            db.set_badge(tid, chat_id, None)
        reply(f"🗑 Бейдж {fmt_mention(tid, get_name(tid))} удалён.")
        return

    if is_cmd(txt, "muteall"):
        if not can_use_cmd(chat_id, "muteall", prio, PE.ADMIN): return no_access("muteall")
        raw = get_args(txt, "muteall").strip()
        minutes = parse_duration_minutes(raw) if raw else DEFAULT_MUTE_MINUTES
        until_ts = ts_mute(minutes)
        with _db_lock:
            members = db.get_all_members(chat_id)
        count = 0
        for m in members:
            if m["user_id"] == uid:
                continue
            m_prio = PE.get(m["user_id"], chat_id)
            if m_prio >= prio:
                continue
            with _db_lock:
                db.set_mute(m["user_id"], chat_id, until_ts)
            count += 1
        reply(f"🔇 Замучено {count} участников на {fmt_dur(minutes * 60)}. До: {fmt_ts(until_ts)}")
        send_log(chat_id, f"🔇 MuteAll | {count} чел. | {fmt_dur(minutes * 60)} | кто: id{uid}")
        return

    if is_cmd(txt, "unmuteall"):
        if not can_use_cmd(chat_id, "unmuteall", prio, PE.ADMIN): return no_access("unmuteall")
        with _db_lock:
            members = db.get_all_members(chat_id)
        count = 0
        for m in members:
            if m.get("is_muted"):
                with _db_lock:
                    db.remove_mute(m["user_id"], chat_id)
                count += 1
        reply(f"🔊 Размучено {count} участников.")
        return

    if is_cmd(txt, "kickinactive"):
        if not can_use_cmd(chat_id, "kickinactive", prio, PE.ADMIN): return no_access("kickinactive")
        raw = get_args(txt, "kickinactive").strip()
        days = _int(raw) if raw else 30
        if not days or days <= 0:
            reply("❌ Укажи количество дней: /kickinactive 30")
            return
        with _db_lock:
            inactive = db.get_inactive(chat_id, days)
        kicked = 0
        skipped = 0
        for m in inactive:
            m_prio = PE.get(m["user_id"], chat_id)
            if m_prio >= prio or m["user_id"] == uid:
                skipped += 1
                continue
            ok_k, _ = kick(chat_id, m["user_id"])
            if ok_k:
                kicked += 1
        reply(
            f"👢 Неактивные (>{days} дней) кикнуты: {kicked}\n"
            f"Пропущено (выше по рангу): {skipped}"
        )
        send_log(chat_id, f"👢 KickInactive | >{days} дн. | кикнуто {kicked} | кто: id{uid}")
        return

    # ==========================================================================
    #  ГЛАВНЫЙ АДМИНИСТРАТОР — 70+
    # ==========================================================================

    if is_cmd(txt, "gadmin", "gaddadmin"):
        if not can_use_cmd(chat_id, "gadmin", prio, PE.CHIEF): return no_access("gadmin")
        raw = get_args(txt, "gadmin", "gaddadmin")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        for c in chats:
            with _db_lock:
                db.upsert_member(tid, c["chat_id"])
                db.update_member_role(tid, c["chat_id"], None, PE.ADMIN)
        reply(f"🛡 {fmt_mention(tid, get_name(tid))} глобально администратором ({len(chats)} бесед)")
        return

    if is_cmd(txt, "pin"):
        if not can_use_cmd(chat_id, "pin", prio, PE.CHIEF): return no_access("pin")
        if not reply_id and not reply_cmid:
            reply("❌ Ответь реплаем на сообщение для закрепления.")
            return
        try:
            if reply_cmid:
                vk.messages.pin(peer_id=chat_id, conversation_message_id=reply_cmid)
            else:
                vk.messages.pin(peer_id=chat_id, message_id=reply_id)
            reply("📌 Сообщение закреплено.")
        except Exception as e:
            reply(f"❌ Ошибка: {e}")
        return

    if is_cmd(txt, "unpin"):
        if not can_use_cmd(chat_id, "unpin", prio, PE.CHIEF): return no_access("unpin")
        try:
            vk.messages.unpin(peer_id=chat_id)
            reply("📌 Закреплённое сообщение снято.")
        except Exception as e:
            reply(f"❌ Ошибка: {e}")
        return

    if is_cmd(txt, "newrole"):
        if not can_use_cmd(chat_id, "newrole", prio, PE.CHIEF): return no_access("newrole")
        raw = get_args(txt, "newrole")
        role_prio, role_name_str = parse_role_args(raw)
        if not role_prio or not role_name_str:
            reply("❌ Пример: /newrole 25 Старший модератор\nДобавить значок: /newrole 25 Старший модератор | 🔥")
            return
        badge = None
        if "|" in role_name_str:
            parts = role_name_str.split("|", 1)
            role_name_str = parts[0].strip()
            badge = parts[1].strip()
        if not PE.can_create_role(prio, role_prio):
            reply(f"❌ Нельзя создать роль с приоритетом [{role_prio}].")
            return
        with _db_lock:
            existing = db.get_role_by_priority(role_prio, chat_id)
        if existing:
            reply(f"❌ Роль [{role_prio}] уже существует: {existing['name']}")
            return
        with _db_lock:
            role = db.create_role(role_name_str, role_prio, uid, chat_id, badge=badge)
        reply(f"✅ Роль создана: [{role['priority']}] {badge + ' ' if badge else ''}{role['name']}")
        return

    if is_cmd(txt, "gnewrole"):
        if not can_use_cmd(chat_id, "gnewrole", prio, PE.CHIEF): return no_access("gnewrole")
        raw = get_args(txt, "gnewrole")
        role_prio, role_name_str = parse_role_args(raw)
        if not role_prio or not role_name_str:
            reply("❌ Пример: /gnewrole 25 Старший модератор")
            return
        if not PE.can_create_role(prio, role_prio):
            reply(f"❌ Нельзя создать роль с приоритетом [{role_prio}].")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        unity_id = chat["unity_id"]
        with _db_lock:
            chats = db.get_chats_by_unity(unity_id)
        if not chats:
            reply("❌ В объединении нет зарегистрированных бесед.")
            return
        created = skipped = 0
        for c in chats:
            with _db_lock:
                existing = db.get_role_by_priority(role_prio, c["chat_id"])
            if existing:
                skipped += 1
                continue
            with _db_lock:
                db.create_role(role_name_str, role_prio, uid, c["chat_id"])
            created += 1
        parts = [f"✅ Роль [{role_prio}] {role_name_str} создана в {created}/{len(chats)} беседах."]
        if skipped:
            parts.append(f"ℹ️ Пропущено (уже есть): {skipped}")
        reply("\n".join(parts))
        return

    if is_cmd(txt, "delrole", "drole"):
        if not can_use_cmd(chat_id, "delrole", prio, PE.CHIEF): return no_access("delrole")
        raw = get_args(txt, "delrole", "drole")
        rp  = _int(raw.strip()) if raw else None
        if not rp:
            reply("❌ Укажи приоритет: /delrole 25")
            return
        if rp in BUILTIN_ROLE_PRIORITIES:
            reply("❌ Встроенные роли 100/80/60/40/20/0 удалять нельзя.")
            return
        with _db_lock:
            ok = db.delete_role(rp, chat_id)
        reply(f"🗑 Роль [{rp}] удалена." if ok else f"❌ Роль [{rp}] не найдена в этой беседе.")
        return

    if is_cmd(txt, "gdelrole", "gdrole"):
        if not can_use_cmd(chat_id, "gdelrole", prio, PE.CHIEF): return no_access("gdelrole")
        raw = get_args(txt, "gdelrole", "gdrole")
        rp  = _int(raw.strip()) if raw else None
        if not rp:
            reply("❌ Укажи приоритет: /gdelrole 25")
            return
        if rp in BUILTIN_ROLE_PRIORITIES:
            reply("❌ Встроенные роли 100/80/60/40/20/0 удалять нельзя.")
            return
        if not chat or not chat.get("unity_id"):
            reply("❌ Беседа не в объединении.")
            return
        with _db_lock:
            chats = db.get_chats_by_unity(chat["unity_id"])
        deleted = 0
        for c in chats:
            with _db_lock:
                if db.delete_role(rp, c["chat_id"]):
                    deleted += 1
        reply(f"🗑 Роль [{rp}] удалена из {deleted}/{len(chats)} бесед.")
        return

    if is_cmd(txt, "welcome"):
        if not can_use_cmd(chat_id, "welcome", prio, PE.CHIEF): return no_access("welcome")
        raw = get_args(txt, "welcome")
        with _db_lock:
            db.set_chat_field(chat_id, "welcome_text", raw)
        reply(
            f"✅ Приветствие установлено:\n{raw}"
            if raw else
            "🗑 Приветствие очищено.\n"
            "Переменные: {name} — упоминание участника"
        )
        return

    if is_cmd(txt, "goodbye"):
        if not can_use_cmd(chat_id, "goodbye", prio, PE.CHIEF): return no_access("goodbye")
        raw = get_args(txt, "goodbye")
        with _db_lock:
            db.set_chat_field(chat_id, "goodbye_text", raw)
        reply(f"✅ Прощание:\n{raw}" if raw else "🗑 Прощание очищено.")
        return

    if is_cmd(txt, "setrules", "srules"):
        if not can_use_cmd(chat_id, "setrules", prio, PE.CHIEF): return no_access("setrules")
        raw = get_args(txt, "setrules", "srules")
        with _db_lock:
            db.set_chat_field(chat_id, "rules_text", raw)
        reply("✅ Правила обновлены." if raw else "🗑 Правила очищены.")
        return

    if is_cmd(txt, "silence"):
        if not can_use_cmd(chat_id, "silence", prio, PE.CHIEF): return no_access("silence")
        raw = get_args(txt, "silence").lower()
        state = 1 if raw in {"вкл", "on", "1"} else (0 if raw in {"выкл", "off", "0"} else None)
        if state is None:
            with _db_lock:
                current = db.get_chat(chat_id)
            state = 0 if (current and current.get("silence_mode")) else 1
        with _db_lock:
            db.set_chat_field(chat_id, "silence_mode", state)
        reply(f"🔕 Режим тишины: {'включён — только модераторы могут писать' if state else 'выключен'}.")
        return

    if is_cmd(txt, "kickgroups"):
        if prio < PE.OWNER: return no_access("kickgroups")
        raw = get_args(txt, "kickgroups").lower().strip()
        if raw in {"true", "вкл", "on", "1"}:
            state = 1
        elif raw in {"false", "выкл", "off", "0"}:
            state = 0
        else:
            with _db_lock:
                cur = db.get_chat(chat_id)
            current_state = cur.get("kickgroups", 0) if cur else 0
            reply(f"🤖 Кик сообществ: {'✅ включён' if current_state else '❌ выключен'}\n"
                  f"Включить: /kickgroups true\nВыключить: /kickgroups false")
            return
        with _db_lock:
            db.set_chat_field(chat_id, "kickgroups", state)
        if state:
            reply("🤖 Кик сообществ включён — при добавлении любого сообщества оно автоматически кикается.")
        else:
            reply("🤖 Кик сообществ выключен — добавлять сообщества разрешено.")
        return

    if is_cmd(txt, "antiraid"):
        if not can_use_cmd(chat_id, "antiraid", prio, PE.CHIEF): return no_access("antiraid")
        raw = get_args(txt, "antiraid").lower()
        state = 1 if raw in {"вкл", "on", "1"} else (0 if raw in {"выкл", "off", "0"} else None)
        if state is None:
            with _db_lock:
                current = db.get_chat(chat_id)
            state = 0 if (current and current.get("antiraid")) else 1
        with _db_lock:
            db.set_chat_field(chat_id, "antiraid", state)
        reply(f"🛡 Антирейд: {'включён — новые участники автоматически кикаются' if state else 'выключен'}.")
        return

    if is_cmd(txt, "slowmode"):
        if not can_use_cmd(chat_id, "slowmode", prio, PE.CHIEF): return no_access("slowmode")
        raw = get_args(txt, "slowmode").lower().strip()
        if raw in {"выкл", "off", "0", "нет"}:
            with _db_lock:
                db.set_chat_field(chat_id, "slowmode_sec", 0)
            reply("⏳ Медленный режим выключен.")
            return
        mins = parse_duration_minutes(raw)
        if not mins:
            reply("❌ Укажи время: /slowmode 10с | 30с | 1м | 5м | выкл")
            return
        secs = mins * 60
        secs = max(1, min(secs, 21600))
        with _db_lock:
            db.set_chat_field(chat_id, "slowmode_sec", secs)
        reply(f"⏳ Медленный режим: 1 сообщение каждые {fmt_dur(secs)}.")
        return

    if is_cmd(txt, "flood", "antiflood"):
        if not can_use_cmd(chat_id, "flood", prio, PE.CHIEF): return no_access("flood")
        raw = get_args(txt, "flood", "antiflood").lower().strip()

        # Показать статус если нет аргументов
        if not raw:
            with _db_lock:
                cur = db.get_chat(chat_id)
            fl = cur.get("flood_limit", 0) if cur else 0
            fi = cur.get("flood_interval", 0) if cur else 0
            fm = cur.get("flood_mute_min", 5) if cur else 5
            if fl and fi:
                status = f"✅ Включён — не более {fl} сообщ. за {fi} сек., мут {fm} мин."
            else:
                status = "❌ Выключен"
            reply(
                f"🌊 Антифлуд: {status}\n\n"
                "Включить: /flood [сообщ] [секунд] [мин мута]\n"
                "Пример: /flood 5 10 — 5 сообщ. за 10с, мут 5мин\n"
                "Пример: /flood 5 10 3 — мут 3 мин.\n"
                "Выключить: /flood выкл"
            )
            return

        # Выключить
        if raw in {"выкл", "off", "0", "нет", "disable"}:
            with _db_lock:
                db.set_chat_field(chat_id, "flood_limit", 0)
                db.set_chat_field(chat_id, "flood_interval", 0)
            reply("🌊 Антифлуд выключен.")
            return

        # Включить: /flood [кол-во] [интервал сек] [мут мин (необяз.)]
        parts = raw.split()
        if len(parts) < 2:
            reply(
                "❌ Формат: /flood [сообщ] [секунд] [мин мута]\n"
                "Пример: /flood 5 10 — не более 5 сообщений за 10 секунд, мут 5 мин.\n"
                "Пример: /flood 5 10 3 — мут 3 мин.\n"
                "Выключить: /flood выкл"
            )
            return
        limit    = _int(parts[0])
        interval = _int(parts[1])
        mute_min = _int(parts[2]) if len(parts) >= 3 else 5
        if not limit or not interval or limit <= 0 or interval <= 0:
            reply("❌ Укажи число сообщений и интервал в секундах.")
            return
        if not mute_min or mute_min <= 0:
            mute_min = 5
        mute_min = min(mute_min, 1440)  # не более суток
        with _db_lock:
            db.set_chat_field(chat_id, "flood_limit", limit)
            db.set_chat_field(chat_id, "flood_interval", interval)
            db.set_chat_field(chat_id, "flood_mute_min", mute_min)
        reply(
            f"🌊 Антифлуд включён:\n"
            f"• Лимит: {limit} сообщений за {interval} сек.\n"
            f"• Нарушителю — мут {mute_min} мин.\n"
            f"Выключить: /flood выкл"
        )
        return

    if is_cmd(txt, "schedule"):
        if not can_use_cmd(chat_id, "schedule", prio, PE.CHIEF): return no_access("schedule")
        raw = get_args(txt, "schedule")
        if not raw:
            reply(
                "❌ Форматы:\n"
                "/schedule +30м | текст — через 30 минут\n"
                "/schedule +2ч каждые 1ч | текст — каждый час\n"
                "/schedule 14:30 | текст — сегодня в 14:30\n"
                "/schedule 25.01 10:00 | текст — 25 января в 10:00"
            )
            return
        send_at, repeat_sec, text = parse_schedule_args(raw)
        if not send_at or not text:
            reply("❌ Неверный формат времени или текст. Используй | для разделения.")
            return
        with _db_lock:
            sched = db.add_schedule(chat_id, text, send_at, repeat_sec, uid)
        rep_str = f" (повтор каждые {fmt_dur(repeat_sec)})" if repeat_sec else ""
        reply(f"⏰ Запланировано #{sched['id']}{rep_str}:\nОтправка: {fmt_ts(send_at)}\n📝 {text[:80]}")
        return

    if is_cmd(txt, "schedlist"):
        if not can_use_cmd(chat_id, "schedlist", prio, PE.CHIEF): return no_access("schedlist")
        with _db_lock:
            scheds = db.get_schedules(chat_id)
        if not scheds:
            reply("✅ Запланированных сообщений нет.")
            return
        lines = [
            f"  #{s['id']} {fmt_ts(s['send_at'])}"
            + (f" [каждые {fmt_dur(s['repeat_sec'])}]" if s.get('repeat_sec') else "")
            + f" — {s['text'][:40]}"
            for s in scheds
        ]
        reply(f"⏰ Расписание ({len(scheds)}):\n" + "\n".join(lines) + "\n\nУдалить: /delschedule [id]")
        return

    if is_cmd(txt, "delschedule"):
        if not can_use_cmd(chat_id, "delschedule", prio, PE.CHIEF): return no_access("delschedule")
        raw = get_args(txt, "delschedule").strip()
        sid = _int(raw)
        if not sid:
            reply("❌ Укажи ID: /delschedule [id]")
            return
        with _db_lock:
            ok = db.delete_schedule(sid, chat_id)
        reply(f"🗑 Расписание #{sid} удалено." if ok else f"❌ #{sid} не найдено.")
        return

    if is_cmd(txt, "temprole"):
        if not can_use_cmd(chat_id, "temprole", prio, PE.CHIEF): return no_access("temprole")
        raw = get_args(txt, "temprole")
        parts = raw.split(maxsplit=3)
        if len(parts) < 3:
            reply("❌ Формат: /temprole [ID] [приоритет] [время]\nПример: /temprole 123456 30 1ч")
            return
        tid = resolve_id(parts[0])
        rp  = _int(parts[1])
        dur_str = parts[2]
        mins = parse_duration_minutes(dur_str)
        if not tid or not rp or not mins:
            reply("❌ Неверный ID, приоритет или время.")
            return
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        if rp >= prio:
            reply(f"❌ Нельзя назначить временную роль [{rp}] — она не ниже вашего приоритета [{prio}].")
            return
        until_ts = int(time.time()) + mins * 60
        with _db_lock:
            db.upsert_member(tid, chat_id)
            m_row = db.get_member(tid, chat_id)
            prev_prio = m_row.get("priority", 0) if m_row else 0
            prev_role_id = m_row.get("role_id") if m_row else None
            role = db.get_role_by_priority(rp, chat_id)
        role_id = role["id"] if role else None
        with _db_lock:
            db.update_member_role(tid, chat_id, role_id, rp)
            db.set_temprole(tid, chat_id, role_id, until_ts, prev_prio, prev_role_id)
        t_name = get_name(tid)
        reply(
            f"⏱ {fmt_mention(tid, t_name)} временная роль [{rp}] на {fmt_dur(mins * 60)}\n"
            f"До: {fmt_ts(until_ts)}\n"
            f"Предыдущий приоритет: {prev_prio} (будет восстановлен)"
        )
        return

    # ==========================================================================
    #  ВЛАДЕЛЕЦ — 100+
    # ==========================================================================

    if is_cmd(txt, "kickall"):
        if not can_use_cmd(chat_id, "kickall", prio, PE.OWNER): return no_access("kickall")
        role_label = "Создатель" if uid == CREATOR_ID else PE.role_name(prio)
        send(
            chat_id,
            f"⚠️ /kickall — кик ВСЕХ участников!\n"
            f"Инициатор: [id{uid}|{get_name(uid)}] ({role_label})\n\n"
            f"{'Будут кикнуты все кроме вас.' if uid == CREATOR_ID else 'Будут кикнуты все кроме вас и создателя.'}\n\n"
            f"Нажмите кнопку для подтверждения:",
            keyboard=make_kickall_confirm_keyboard(chat_id, uid),
            reply_to=msg_id,
        )
        return

    if is_cmd(txt, "setowner"):
        if not can_use_cmd(chat_id, "setowner", prio, PE.OWNER): return no_access("setowner")
        if uid != CREATOR_ID:
            reply("❌ Только создатель бота может назначать владельца.")
            return
        raw = get_args(txt, "setowner")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        with _db_lock:
            db.upsert_member(tid, chat_id)
            db.set_chat_field(chat_id, "owner_id", tid)
        reply(f"🔱 {fmt_mention(tid, get_name(tid))} назначен владельцем беседы.")
        return

    if is_cmd(txt, "removeowner"):
        if not can_use_cmd(chat_id, "removeowner", prio, PE.OWNER): return no_access("removeowner")
        if uid != CREATOR_ID:
            reply("❌ Только создатель бота может снимать владельца.")
            return
        if not chat or not chat.get("owner_id"):
            reply("ℹ️ Владелец не установлен.")
            return
        old_owner = chat["owner_id"]
        with _db_lock:
            db.set_chat_field(chat_id, "owner_id", None)
        reply(f"🔱 Владелец [id{old_owner}|{get_name(old_owner)}] снят.")
        return

    if is_cmd(txt, "wipe"):
        if not can_use_cmd(chat_id, "wipe", prio, PE.OWNER): return no_access("wipe")
        raw = get_args(txt, "wipe").strip().lower()
        valid = {"warns", "bans", "roles", "nicks", "notes", "triggers", "logs", "stats"}
        if raw not in valid:
            reply(f"❌ Доступно: {', '.join(sorted(valid))}")
            return
        with _db_lock:
            count = db.wipe(chat_id, raw)
        reply(f"🗑 {raw.capitalize()} очищены ({count} записей).")
        return

    if is_cmd(txt, "setpublic"):
        if not can_use_cmd(chat_id, "setpublic", prio, PE.OWNER): return no_access("setpublic")
        raw = get_args(txt, "setpublic").strip()
        if not raw or raw.lower() in {"off", "выкл", "0", "нет"}:
            with _db_lock:
                db.set_chat_field(chat_id, "public_link", None)
            reply("🔓 Проверка подписки отключена.")
            return
        # Извлекаем короткое имя из ссылки: https://vk.com/club123 → club123
        _pub_raw = raw
        for _prefix in ("https://vk.com/", "http://vk.com/", "vk.com/"):
            if _pub_raw.startswith(_prefix):
                _pub_raw = _pub_raw[len(_prefix):]
                break
        _pub_raw = _pub_raw.strip("/").split("?")[0]
        # Проверяем что сообщество существует
        try:
            _res = vk.utils.resolveScreenName(screen_name=_pub_raw)
            if not _res or _res.get("type") not in ("group", "public"):
                reply(f"❌ Не удалось найти сообщество: {_pub_raw}\nУкажи ссылку на группу или паблик.")
                return
        except Exception as _e:
            reply(f"❌ Ошибка проверки сообщества: {_e}")
            return
        with _db_lock:
            db.set_chat_field(chat_id, "public_link", _pub_raw)
        reply(
            f"🔒 Проверка подписки включена.\n"
            f"📢 Сообщество: vk.com/{_pub_raw}\n"
            f"Участники без подписки не смогут писать в беседе.\n"
            f"Иммунитет: Владелец и Создатель."
        )
        return

    if is_cmd(txt, "setlog"):
        if not can_use_cmd(chat_id, "setlog", prio, PE.OWNER): return no_access("setlog")
        raw = get_args(txt, "setlog").strip()
        log_id = _int(raw) if raw else chat_id
        with _db_lock:
            db.set_chat_field(chat_id, "log_peer_id", log_id)
        reply(f"📋 Лог-беседа установлена: {log_id}")
        return

    if is_cmd(txt, "sync"):
        if not can_use_cmd(chat_id, "sync", prio, PE.OWNER): return no_access("sync")
        reply("🔄 Синхронизация участников...")
        try:
            resp  = vk.messages.getConversationMembers(peer_id=chat_id)
            count = 0
            for m in resp.get("items", []):
                if m.get("member_id", 0) > 0:
                    with _db_lock:
                        db.upsert_member(m["member_id"], chat_id)
                    count += 1
            reply(f"✅ Синхронизировано {count} участников.")
        except Exception as e:
            reply(f"❌ Ошибка синхронизации: {e}")
        return

    if is_cmd(txt, "autorole"):
        if not can_use_cmd(chat_id, "autorole", prio, PE.OWNER): return no_access("autorole")
        raw = get_args(txt, "autorole").strip()
        if not raw or raw.lower() in {"off", "выкл", "0"}:
            with _db_lock:
                db.set_chat_field(chat_id, "autorole_id", None)
            reply("🔧 Авторолль выключена.")
            return
        rp = _int(raw)
        with _db_lock:
            role = db.get_role_by_priority(rp, chat_id) if rp else db.get_role_by_name(raw, chat_id)
        if not role:
            reply("❌ Роль не найдена. Список: /roles")
            return
        with _db_lock:
            db.set_chat_field(chat_id, "autorole_id", role["id"])
        reply(f"✅ Авторолль: {role['name']} [{role['priority']}] — выдаётся всем новым участникам.")
        return

    if is_cmd(txt, "settings"):
        if not can_use_cmd(chat_id, "settings", prio, PE.OWNER): return no_access("settings")
        if not chat:
            reply("❌ Чат не зарегистрирован.")
            return
        unity_info = "нет"
        if chat.get("unity_id"):
            with _db_lock:
                u = db.get_unity(chat["unity_id"])
            unity_info = f"{u['name']} (ID: {u['id']})" if u else "?"
        filters = []
        if chat.get("filter_mat"):     filters.append("маты")
        if chat.get("filter_links"):   filters.append("ссылки")
        if chat.get("filter_caps"):    filters.append("капс")
        if chat.get("filter_voice"):   filters.append("голосовые")
        if chat.get("filter_forward"): filters.append("пересылки")
        if chat.get("filter_sticker"): filters.append("стикеры")
        with _db_lock:
            overrides = db.get_cmd_overrides(chat_id)
        slow     = chat.get("slowmode_sec", 0)
        flood_l  = chat.get("flood_limit", 0)
        flood_i  = chat.get("flood_interval", 0)
        reply(
            f"⚙️ Настройки беседы\n"
            f"────────────────────\n"
            f"🔕 Тишина: {'вкл' if chat.get('silence_mode') else 'выкл'}\n"
            f"🛡 Антирейд: {'вкл' if chat.get('antiraid') else 'выкл'}\n"
            f"⏳ Медленный режим: {fmt_dur(slow) if slow else 'выкл'}\n"
            f"🌊 Антифлуд: {f'{flood_l} сообщ./{flood_i}с' if flood_l else 'выкл'}\n"
            f"🔗 Объединение: {unity_info}\n"
            f"🔧 Фильтры: {', '.join(filters) if filters else 'нет'}\n"
            f"📋 Лог-беседа: {chat.get('log_peer_id') or 'не задана'}\n"
            f"👋 Приветствие: {'есть' if chat.get('welcome_text') else 'нет'}\n"
            f"🚪 Прощание: {'есть' if chat.get('goodbye_text') else 'нет'}\n"
            f"📜 Правила: {'есть' if chat.get('rules_text') else 'нет'}\n"
            f"🔐 Переопределений команд: {len(overrides)}\n"
            f"💬 Всего сообщений: {chat.get('msg_count', 0)}"
        )
        return

    if is_cmd(txt, "mentions"):
        if not can_use_cmd(chat_id, "mentions", prio, PE.OWNER): return no_access("mentions")
        with _db_lock:
            disabled = db.get_mention_disabled(chat_id)
        if not disabled:
            reply("✅ Все участники доступны для упоминания.")
            return
        lines = [f"  [id{r['user_id']}|id{r['user_id']}]" for r in disabled]
        reply(f"🔕 Отключили упоминания ({len(disabled)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "createunity"):
        if not can_use_cmd(chat_id, "createunity", prio, PE.OWNER): return no_access("createunity")
        raw = get_args(txt, "createunity").strip()
        if not raw:
            reply("❌ Укажи название: /createunity [название]")
            return
        with _db_lock:
            existing = db.get_unity_by_name(raw)
        if existing:
            reply(f"❌ Объединение «{raw}» уже существует (ID: {existing['id']}).")
            return
        with _db_lock:
            unity = db.create_unity(raw, uid)
        reply(f"🔗 Объединение «{unity['name']}» создано (ID: {unity['id']})\nПривяжи беседу: /addunity {unity['name']}")
        return

    if is_cmd(txt, "addunity"):
        if not can_use_cmd(chat_id, "addunity", prio, PE.OWNER): return no_access("addunity")
        raw = get_args(txt, "addunity").strip()
        if not raw:
            reply("❌ Укажи название объединения: /addunity [название]")
            return
        with _db_lock:
            unity = db.get_unity_by_name(raw)
        if not unity:
            reply(f"❌ Объединение «{raw}» не найдено.")
            return
        if uid != CREATOR_ID and unity.get("owner_id") != uid:
            reply("❌ Подключать беседы можно только к своему объединению.")
            return
        with _db_lock:
            db.set_chat_field(chat_id, "unity_id", unity["id"])
        reply(f"✅ Беседа привязана к «{unity['name']}» (ID: {unity['id']}).")
        return

    if is_cmd(txt, "removeunity", "runity"):
        if not can_use_cmd(chat_id, "removeunity", prio, PE.OWNER): return no_access("removeunity")
        with _db_lock:
            db.set_chat_field(chat_id, "unity_id", None)
        reply("✅ Беседа отвязана от объединения.")
        return

    if is_cmd(txt, "unity", "unites"):
        if not can_use_cmd(chat_id, "unity", prio, PE.OWNER): return no_access("unity")
        with _db_lock:
            unities = db.list_unities()
        if uid != CREATOR_ID:
            unities = [u for u in unities if u.get("owner_id") == uid]
        if not unities:
            reply("🔗 Объединений нет. Создай: /createunity [название]")
            return
        lines = [f"  [{u['id']}] {u['name']} — владелец: id{u['owner_id']}" for u in unities]
        reply(f"🔗 Объединения ({len(unities)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "deleteunity"):
        if not can_use_cmd(chat_id, "deleteunity", prio, PE.OWNER): return no_access("deleteunity")
        raw = get_args(txt, "deleteunity").strip()
        if not raw:
            reply("❌ Укажи название: /deleteunity [название]")
            return
        with _db_lock:
            unity = db.get_unity_by_name(raw)
        if not unity:
            reply(f"❌ Объединение «{raw}» не найдено.")
            return
        if uid != CREATOR_ID and unity.get("owner_id") != uid:
            reply("❌ Удалить объединение может только его создатель.")
            return
        with _db_lock:
            for linked_chat in db.get_chats_by_unity(unity["id"]):
                db.set_chat_field(linked_chat["chat_id"], "unity_id", None)
            db.delete_unity(unity["id"])
        reply(f"🗑 Объединение «{raw}» удалено.")
        return

    if is_cmd(txt, "blockcmd", "synccmd"):
        if not can_use_cmd(chat_id, "blockcmd", prio, PE.CREATOR): return no_access("blockcmd")
        raw = get_args(txt, "blockcmd", "synccmd")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        if tid == CREATOR_ID:
            reply("❌ Нельзя заблокировать создателя бота.")
            return
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        with _db_lock:
            db.set_cmd_blocked(tid, uid)
        t_name = get_name(tid)
        reply(f"🚫 {fmt_mention(tid, t_name)} лишён доступа к командам бота во всех беседах.")
        send_log(chat_id, f"🚫 BlockCMD | {t_name} (id{tid}) | кто: id{uid}")
        return

    if is_cmd(txt, "unblockcmd", "syncuncmd"):
        if not can_use_cmd(chat_id, "unblockcmd", prio, PE.CREATOR): return no_access("unblockcmd")
        raw = get_args(txt, "unblockcmd", "syncuncmd")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        with _db_lock:
            ok = db.remove_cmd_blocked(tid)
        t_name = get_name(tid)
        reply(
            f"✅ {fmt_mention(tid, t_name)} снова может использовать команды."
            if ok else
            f"ℹ️ {fmt_mention(tid, t_name)} не был заблокирован."
        )
        return

    if is_cmd(txt, "syncban"):
        if not can_use_cmd(chat_id, "syncban", prio, PE.CREATOR): return no_access("syncban")
        raw = get_args(txt, "syncban")
        tid, days, reason = parse_ban_args(raw, reply_from_id)
        if not tid: return need_id()
        if tid == CREATOR_ID:
            reply("❌ Нельзя забанить создателя бота.")
            return
        if anti_self(tid): return
        t_prio = PE.get(tid, chat_id)
        if not PE.can_punish(prio, t_prio):
            reply("❌ Недостаточно прав.")
            return
        ban_ts   = ts_ban(days)
        dur_info = f"{days} дн." if days else "навсегда"
        with _db_lock:
            all_chats = db.get_all_chats()
        ok = fail = 0
        for c in all_chats:
            try:
                with _db_lock:
                    db.add_ban(tid, uid, reason, ban_ts, c["chat_id"], scope="global")
                kick(c["chat_id"], tid)
                ok += 1
            except Exception as e:
                log.warning(f"[SYNCBAN] chat {c['chat_id']}: {e}")
                fail += 1
        t_name = get_name(tid)
        send(
            chat_id,
            f"🚫 SyncBan: {fmt_mention(tid, t_name)} забанен {dur_info}\n"
            f"📋 {reason}\n"
            f"✅ Беседы: {ok}/{len(all_chats)}"
            + (f"\n❌ Ошибки: {fail}" if fail else ""),
            keyboard=make_unban_keyboard(tid, chat_id, scope="global"),
            reply_to=msg_id,
        )
        return

    if is_cmd(txt, "syncunban"):
        if not can_use_cmd(chat_id, "syncunban", prio, PE.CREATOR): return no_access("syncunban")
        raw = get_args(txt, "syncunban")
        tid, _ = get_target(raw, reply_from_id)
        if not tid: return need_id()
        with _db_lock:
            all_chats = db.get_all_chats()
        unbanned = 0
        for c in all_chats:
            with _db_lock:
                if db.remove_ban(tid, c["chat_id"]):
                    unbanned += 1
        t_name = get_name(tid)
        reply(f"✅ SyncUnban: {fmt_mention(tid, t_name)} разбанен\nСнято банов в {unbanned}/{len(all_chats)} беседах.")
        return

    if is_cmd(txt, "find", "whois"):
        if not can_use_cmd(chat_id, "find", prio, PE.OWNER): return no_access("find")
        raw = get_args(txt, "find", "whois").strip()
        if not raw:
            reply("❌ Укажи ID, @ник или часть ника: /find [запрос]")
            return
        tid = resolve_id(raw)
        results = []
        if tid:
            with _db_lock:
                m_row = db.get_member(tid, chat_id)
            if m_row:
                results.append(m_row)
        else:
            with _db_lock:
                results = db.get_by_nick_part(chat_id, raw)
        if not results:
            reply(f"❌ Участник «{raw}» не найден в базе.")
            return
        lines = []
        for m in results[:5]:
            t_prio = PE.get(m["user_id"], chat_id)
            nick   = m.get("nickname") or "—"
            seen   = fmt_ts(m.get("last_seen", 0))
            lines.append(
                f"👤 [id{m['user_id']}|id{m['user_id']}]\n"
                f"   Роль: {PE.role_name(t_prio)}\n"
                f"   Ник: {nick}\n"
                f"   Сообщений: {m.get('msg_count', 0)}\n"
                f"   Активность: {seen}"
            )
        reply(f"🔍 Результаты поиска ({len(results)}):\n\n" + "\n\n".join(lines))
        return

    if is_cmd(txt, "listcmds", "cmdlist"):
        if not can_use_cmd(chat_id, "listcmds", prio, PE.OWNER): return no_access("listcmds")
        with _db_lock:
            overrides = db.get_cmd_overrides(chat_id)
        if not overrides:
            reply("✅ Переопределений команд нет.\nНастроить: /setcmd [команда] [приоритет|диапазон|все] [вкл|выкл]")
            return
        lines = [
            f"  /{o['command']} для [{o['priority']}]: {'✅ разрешена' if o['allowed'] else '❌ запрещена'}"
            for o in overrides
        ]
        reply(f"🔐 Переопределения команд ({len(overrides)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "unbanall"):
        if not can_use_cmd(chat_id, "unbanall", prio, PE.OWNER): return no_access("unbanall")
        raw = get_args(txt, "unbanall").strip().lower()
        if raw not in {"confirm", "подтверждаю"}:
            reply("⚠️ Это разбанит ВСЕХ забаненных в этой беседе!\nДля подтверждения: /unbanall подтверждаю")
            return
        with _db_lock:
            cur = db.ex("UPDATE bans SET active=0 WHERE chat_id=? AND active=1", (chat_id,))
        reply(f"✅ Снято банов: {cur.rowcount}")
        send_log(chat_id, f"🔓 UnbanAll | снято {cur.rowcount} банов | кто: id{uid}")
        return

    if is_cmd(txt, "clearblacklist"):
        if not can_use_cmd(chat_id, "clearblacklist", prio, PE.OWNER): return no_access("clearblacklist")
        with _db_lock:
            count = db.clear_blacklist(chat_id)
        reply(f"🗑 Блеклист очищен ({count} слов).")
        return

    if is_cmd(txt, "mutelog"):
        if not can_use_cmd(chat_id, "mutelog", prio, PE.OWNER): return no_access("mutelog")
        with _db_lock:
            mute_logs = db.all(
                "SELECT * FROM action_logs WHERE chat_id=? AND action='mute' "
                "ORDER BY created_at DESC LIMIT 15",
                (chat_id,),
            )
        if not mute_logs:
            reply("📋 Мутов не зафиксировано.")
            return
        lines = [
            f"  [{fmt_ts(l['created_at'])}] id{l['actor_id']} → id{l['target_id']} | {l['details']}"
            for l in mute_logs
        ]
        reply(f"📋 Последние муты ({len(mute_logs)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "banlog"):
        if not can_use_cmd(chat_id, "banlog", prio, PE.OWNER): return no_access("banlog")
        with _db_lock:
            ban_logs = db.all(
                "SELECT * FROM action_logs WHERE chat_id=? AND action='ban' "
                "ORDER BY created_at DESC LIMIT 15",
                (chat_id,),
            )
        if not ban_logs:
            reply("📋 Банов не зафиксировано.")
            return
        lines = [
            f"  [{fmt_ts(l['created_at'])}] id{l['actor_id']} → id{l['target_id']} | {l['details']}"
            for l in ban_logs
        ]
        reply(f"📋 Последние баны ({len(ban_logs)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "kicklog"):
        if not can_use_cmd(chat_id, "kicklog", prio, PE.OWNER): return no_access("kicklog")
        with _db_lock:
            kick_logs = db.all(
                "SELECT * FROM action_logs WHERE chat_id=? AND action='kick' "
                "ORDER BY created_at DESC LIMIT 15",
                (chat_id,),
            )
        if not kick_logs:
            reply("📋 Киков не зафиксировано.")
            return
        lines = [
            f"  [{fmt_ts(l['created_at'])}] id{l['actor_id']} → id{l['target_id']} | {l['details']}"
            for l in kick_logs
        ]
        reply(f"📋 Последние кики ({len(kick_logs)}):\n" + "\n".join(lines))
        return

    if is_cmd(txt, "dbinfo"):
        if not can_use_cmd(chat_id, "dbinfo", prio, PE.OWNER): return no_access("dbinfo")
        with _db_lock:
            total_members  = len(db.all("SELECT 1 FROM members"))
            total_bans     = len(db.all("SELECT 1 FROM bans WHERE active=1"))
            total_warns    = len(db.all("SELECT 1 FROM warns WHERE active=1"))
            total_triggers = len(db.all("SELECT 1 FROM triggers"))
            total_chats    = len(db.all("SELECT 1 FROM chats"))
            total_roles    = len(db.all("SELECT 1 FROM roles"))
            total_notes    = len(db.all("SELECT 1 FROM notes"))
            total_reports  = len(db.all("SELECT 1 FROM reports WHERE reviewed=0"))
        db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        elapsed = int(time.time()) - START_TIME
        reply(
            f"📊 Статистика базы данных\n"
            f"────────────────────\n"
            f"💾 Размер базы: {db_size // 1024} КБ\n"
            f"💬 Бесед: {total_chats}\n"
            f"👥 Участников: {total_members}\n"
            f"🏷 Ролей: {total_roles}\n"
            f"🚫 Активных банов: {total_bans}\n"
            f"⚠️ Активных варнов: {total_warns}\n"
            f"⚡ Триггеров: {total_triggers}\n"
            f"📝 Заметок: {total_notes}\n"
            f"📢 Новых жалоб: {total_reports}\n"
            f"⏱ Аптайм: {fmt_uptime(elapsed)}"
        )
        return

    # ==========================================================================

# ══════════════════════════════════════════════════════════════════════════════
#  ФОНОВЫЙ ПОТОК: ОЧИСТКА МУТОВ, БАНОВ, ВРЕМЕННЫХ РОЛЕЙ, РАСПИСАНИЕ
# ══════════════════════════════════════════════════════════════════════════════

_MUTE_CLEANUP_INTERVAL   = 30
_BAN_KICK_INTERVAL       = 120
_TEMPROLE_CHECK_INTERVAL = 60
_SCHEDULE_CHECK_INTERVAL = 30


def _expire_mutes() -> None:
    now = int(time.time())
    with _db_lock:
        expired = db.all(
            "SELECT m.user_id, m.chat_id FROM members m "
            "WHERE m.is_muted=1 AND m.mute_until>0 AND m.mute_until<=?",
            (now,),
        )
    for row in expired:
        user_id, chat_id = row["user_id"], row["chat_id"]
        try:
            with _db_lock:
                db.remove_mute(user_id, chat_id)
            send(chat_id, f"🔊 [id{user_id}|id{user_id}], мут снят — время истекло.")
            log.info(f"[MUTE-EXPIRE] user={user_id} chat={chat_id}")
        except Exception as e:
            log.warning(f"[MUTE-EXPIRE] user={user_id} chat={chat_id}: {e}")


def _kick_banned_members() -> None:
    with _db_lock:
        chats = db.get_all_chats()
    for chat in chats:
        chat_id = chat["chat_id"]
        try:
            resp = vk.messages.getConversationMembers(peer_id=chat_id)
            member_ids = [item["member_id"] for item in resp.get("items", []) if item.get("member_id", 0) > 0]
        except Exception as e:
            log.debug(f"[BAN-KICK] getMembers chat={chat_id}: {e}")
            continue
        for member_id in member_ids:
            try:
                with _db_lock:
                    ban = db.get_active_ban(member_id, chat_id)
                if not ban and chat.get("unity_id"):
                    with _db_lock:
                        ban = db.get_active_ban(member_id, None, chat["unity_id"])
                if ban:
                    ok_k, _ = kick(chat_id, member_id)
                    if ok_k:
                        log.info(f"[BAN-KICK] kicked user={member_id} chat={chat_id}")
            except Exception as e:
                log.debug(f"[BAN-KICK] user={member_id} chat={chat_id}: {e}")


def _expire_temproles() -> None:
    with _db_lock:
        expired = db.get_expired_temproles()
    for row in expired:
        user_id, chat_id = row["user_id"], row["chat_id"]
        prev_prio        = row.get("temprole_prev_priority", 0)
        prev_role_id     = row.get("temprole_prev_role_id")
        try:
            with _db_lock:
                db.update_member_role(user_id, chat_id, prev_role_id, prev_prio)
                db.clear_temprole(user_id, chat_id)
            send(chat_id, f"⏱ [id{user_id}|id{user_id}], временная роль снята. Приоритет восстановлен: {prev_prio}.")
            log.info(f"[TEMPROLE-EXPIRE] user={user_id} chat={chat_id}")
        except Exception as e:
            log.warning(f"[TEMPROLE-EXPIRE] user={user_id} chat={chat_id}: {e}")


def _process_schedules() -> None:
    with _db_lock:
        pending = db.get_pending_schedules()
    for sched in pending:
        try:
            if not send(sched["chat_id"], sched["text"]):
                log.warning(f"[SCHEDULE] #{sched['id']} не отправлено; будет повторная попытка.")
                continue
            with _db_lock:
                db.mark_schedule_sent(
                    sched["id"],
                    sched.get("repeat_sec", 0),
                    sched.get("send_at", 0),
                )
            log.info(f"[SCHEDULE] Sent #{sched['id']} to chat={sched['chat_id']}")
        except Exception as e:
            log.warning(f"[SCHEDULE] #{sched['id']}: {e}")


def _cleanup_loop() -> None:
    log.info("[CLEANUP] Фоновый поток запущен.")
    mute_c     = 0
    ban_c      = 0
    temprole_c = 0
    sched_c    = 0
    dedup_c    = 0
    _DEDUP_CLEANUP_INTERVAL = 300  # чистим кэш ID каждые 5 минут

    while True:
        try:
            time.sleep(1)

            mute_c += 1
            if mute_c >= _MUTE_CLEANUP_INTERVAL:
                mute_c = 0
                _expire_mutes()

            ban_c += 1
            if ban_c >= _BAN_KICK_INTERVAL:
                ban_c = 0
                _kick_banned_members()

            temprole_c += 1
            if temprole_c >= _TEMPROLE_CHECK_INTERVAL:
                temprole_c = 0
                _expire_temproles()

            sched_c += 1
            if sched_c >= _SCHEDULE_CHECK_INTERVAL:
                sched_c = 0
                _process_schedules()

            # Чистим таблицу processed_events — удаляем старше 2 минут
            dedup_c += 1
            if dedup_c >= _DEDUP_CLEANUP_INTERVAL:
                dedup_c = 0
                with _db_lock:
                    db.cleanup_old_events(120)

        except Exception as e:
            log.error(f"[CLEANUP] {e}")


# Ограниченный пул: флуд больше не создаёт бесконечное число потоков.
_EVENT_WORKERS = max(2, int(os.environ.get("BOT_WORKERS", "12")))
_EVENT_QUEUE_LIMIT = max(_EVENT_WORKERS, int(os.environ.get("BOT_EVENT_QUEUE", "200")))
_event_executor = ThreadPoolExecutor(max_workers=_EVENT_WORKERS, thread_name_prefix="vk-event")
_event_slots = threading.BoundedSemaphore(_EVENT_QUEUE_LIMIT)

def _submit_event(handler, event) -> bool:
    if not _event_slots.acquire(blocking=False):
        log.warning("[EVENT] Очередь переполнена, событие пропущено.")
        return False

    def _run() -> None:
        try:
            handler(event)
        except Exception:
            log.exception(f"[EVENT] Ошибка обработчика {getattr(handler, '__name__', handler)}")
        finally:
            _event_slots.release()

    try:
        _event_executor.submit(_run)
        return True
    except Exception:
        _event_slots.release()
        log.exception("[EVENT] Не удалось поставить событие в очередь.")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

# ── PID-лок: защита от двойного запуска ───────────────────────────────────────
_PID_FILE = Path("/tmp/norot_manager.pid")
_PID_HANDLE = None

def _acquire_pid_lock() -> None:
    """Безопасно запрещает второй экземпляр, не завершая посторонние процессы."""
    global _PID_HANDLE
    try:
        import fcntl
        _PID_HANDLE = _PID_FILE.open("a+")
        fcntl.flock(_PID_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _PID_HANDLE.seek(0)
        _PID_HANDLE.truncate()
        _PID_HANDLE.write(str(os.getpid()))
        _PID_HANDLE.flush()
        log.info(f"[STARTUP] Единственный экземпляр, PID {os.getpid()}.")
    except BlockingIOError:
        log.critical("[STARTUP] Бот уже запущен. Второй экземпляр остановлен.")
        sys.exit(1)
    except Exception as e:
        log.critical(f"[STARTUP] Не удалось создать PID-lock: {e}")
        sys.exit(1)

def _release_pid_lock() -> None:
    global _PID_HANDLE
    if _PID_HANDLE is None:
        return
    try:
        import fcntl
        fcntl.flock(_PID_HANDLE.fileno(), fcntl.LOCK_UN)
        _PID_HANDLE.close()
    except Exception:
        pass
    finally:
        _PID_HANDLE = None


# ── HTTP keepalive (aiohttp): предотвращает остановку на хосте ───────────────

async def _health_handler(request: _aiohttp_web.Request) -> _aiohttp_web.Response:
    return _aiohttp_web.Response(text="OK", content_type="text/plain")

def _start_keepalive_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run() -> None:
        app = _aiohttp_web.Application()
        app.router.add_get("/", _health_handler)
        app.router.add_get("/health", _health_handler)
        app.router.add_head("/", _health_handler)
        runner = _aiohttp_web.AppRunner(app)
        await runner.setup()
        site = _aiohttp_web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        log.info(f"[KEEPALIVE] aiohttp HTTP-сервер запущен на порту {port}")
        while True:
            await asyncio.sleep(3600)

    try:
        loop.run_until_complete(_run())
    except OSError as e:
        log.warning(f"[KEEPALIVE] Не удалось запустить HTTP-сервер: {e}")
    finally:
        loop.close()


def main() -> None:
    # Захватываем безопасный lock единственного экземпляра
    _acquire_pid_lock()

    try:
        log.info("=" * 60)
        log.info(f"  {BOT_NAME}")
        log.info(f"  Сообщество: {GROUP_NAME} (id{GROUP_ID})")
        log.info(f"  https://vk.com/club{GROUP_ID}")
        log.info(f"  Создатель: id{CREATOR_ID}")
        log.info(f"  Префикс: {PREFIX}")
        log.info("  Longpoll слушает события... (Ctrl+C для остановки)")
        log.info("=" * 60)

        # Keepalive HTTP-сервер (держит процесс живым на bothost.ru и аналогах)
        threading.Thread(target=_start_keepalive_server, daemon=True, name="keepalive").start()

        threading.Thread(target=_cleanup_loop, daemon=True, name="cleanup").start()

        # Безопасная проверка: MESSAGE_EVENT появился в vk_api >= 11.9
        _MSG_EVENT_TYPE = getattr(VkBotEventType, "MESSAGE_EVENT", None)
        if _MSG_EVENT_TYPE is None:
            log.warning("[MAIN] VkBotEventType.MESSAGE_EVENT не найден в установленной vk_api — "
                        "кнопки будут обрабатываться через строковое сравнение типа события.")

        while True:
            try:
                for event in longpoll.listen():
                    try:
                        if event.type == VkBotEventType.MESSAGE_NEW:
                            _submit_event(on_message, event)
                        elif (
                            (_MSG_EVENT_TYPE is not None and event.type == _MSG_EVENT_TYPE)
                            or (hasattr(event, "type") and str(event.type).lower() in ("message_event", "vkboteventtype.message_event"))
                            or (hasattr(event, "raw") and isinstance(event.raw, dict) and event.raw.get("type") == "message_event")
                        ):
                            _submit_event(handle_callback_event, event)
                    except Exception as e:
                        log.error(f"[MAIN] {e}")
            except KeyboardInterrupt:
                log.info("Остановка бота...")
                break
            except Exception as e:
                log.warning(f"[LONGPOLL] Обрыв соединения: {e}. Переподключение через 5 сек...")
                time.sleep(5)
    finally:
        try:
            _event_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            _event_executor.shutdown(wait=False)
        _release_pid_lock()


if __name__ == "__main__":
    main()
