#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Black City RP Telegram Control Bot
Один файл. requirements.txt и .env не нужны.
При первом запуске бот сам установит PyMySQL в папку _vendor рядом с собой.
"""

from __future__ import annotations

import html
import importlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ============================================================================
# НАСТРОЙКИ — ЗАПОЛНИТЬ ПЕРЕД ПЕРВЫМ ЗАПУСКОМ
# ============================================================================
BOT_TOKEN = "8975361055:AAET6brDJIAonm58z-2CNCHG-1WEMuC0Rmc"
OWNER_TELEGRAM_ID = 8343382233  # свой цифровой Telegram ID, например 123456789

# AUTO = бот попробует найти mysql.ini рядом, в scriptfiles/ и на уровень выше.
MYSQL_INI_PATH = "AUTO"

# Используются только если mysql.ini не найден.
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_USER = "user43657"
MYSQL_PASSWORD = "xIfKW3iQ6k7j"
MYSQL_DATABASE = "user43657"

# Только личные сообщения. В группах команды управления игнорируются.
PRIVATE_ONLY = True

# Интервал доставки результатов из игры.
RESULT_CHECK_SECONDS = 2

# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
VENDOR_DIR = BASE_DIR / "_vendor"
LOG_FILE = BASE_DIR / "telegram_bot.log"
CONFIRM_TTL = 180


def log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def ensure_pymysql() -> None:
    """Устанавливает PyMySQL локально при первом запуске."""
    VENDOR_DIR.mkdir(exist_ok=True)
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    try:
        import pymysql  # noqa: F401
        return
    except ImportError:
        pass

    log("PyMySQL не найден. Запускаю автоматическую установку...")
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--target",
        str(VENDOR_DIR),
        "PyMySQL>=1.1,<2",
    ]
    try:
        subprocess.check_call(command)
    except Exception as exc:
        raise RuntimeError(
            "Не удалось автоматически установить PyMySQL. "
            "На хостинге должен быть доступен pip и исходящие HTTPS-подключения."
        ) from exc
    importlib.invalidate_caches()
    import pymysql  # noqa: F401
    log("PyMySQL установлен в локальную папку _vendor.")


ensure_pymysql()
import pymysql  # type: ignore  # noqa: E402
from pymysql.cursors import DictCursor  # type: ignore  # noqa: E402


def locate_mysql_ini() -> Optional[Path]:
    if MYSQL_INI_PATH and MYSQL_INI_PATH.upper() != "AUTO":
        path = Path(MYSQL_INI_PATH)
        if not path.is_absolute():
            path = BASE_DIR / path
        return path if path.exists() else None
    candidates = [
        BASE_DIR / "mysql.ini",
        BASE_DIR / "scriptfiles" / "mysql.ini",
        BASE_DIR.parent / "scriptfiles" / "mysql.ini",
        BASE_DIR.parent / "mysql.ini",
    ]
    return next((p for p in candidates if p.exists()), None)


def read_mysql_settings() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "host": MYSQL_HOST,
        "port": int(MYSQL_PORT),
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "database": MYSQL_DATABASE,
    }
    path = locate_mysql_ini()
    if not path:
        log("mysql.ini не найден — используются настройки из начала tg_admin_bot.py.")
        return result

    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lower()] = value.strip().strip('"').strip("'")
    result["host"] = values.get("host", result["host"])
    result["port"] = int(values.get("port", result["port"]))
    result["user"] = values.get("username", values.get("user", result["user"]))
    result["password"] = values.get("password", result["password"])
    result["database"] = values.get("database", result["database"])
    log(f"MySQL-настройки загружены из {path}.")
    return result


DB_CONFIG = read_mysql_settings()


def db_connect():
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=int(DB_CONFIG["port"]),
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
        read_timeout=15,
        write_timeout=15,
        cursorclass=DictCursor,
    )


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS telegram_links (
      telegram_id BIGINT NOT NULL,
      account_id INT UNSIGNED NOT NULL,
      linked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY(telegram_id),
      UNIQUE KEY uq_tg_links_account(account_id),
      CONSTRAINT fk_tg_links_account FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_link_codes (
      account_id INT UNSIGNED NOT NULL,
      code CHAR(8) NOT NULL,
      expires_at DATETIME NOT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(account_id),
      UNIQUE KEY uq_tg_link_code(code),
      KEY idx_tg_link_expire(expires_at),
      CONSTRAINT fk_tg_code_account FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_staff (
      telegram_id BIGINT NOT NULL,
      account_id INT UNSIGNED NULL,
      role ENUM('viewer','manager','owner') NOT NULL DEFAULT 'viewer',
      max_admin_level TINYINT UNSIGNED NOT NULL DEFAULT 0,
      enabled TINYINT(1) NOT NULL DEFAULT 1,
      added_by BIGINT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY(telegram_id),
      UNIQUE KEY uq_tg_staff_account(account_id),
      CONSTRAINT fk_tg_staff_account FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_actions (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      telegram_id BIGINT NOT NULL,
      actor_account_id INT UNSIGNED NOT NULL,
      target_account_id INT UNSIGNED NULL,
      action VARCHAR(32) NOT NULL,
      value INT NOT NULL DEFAULT 0,
      extra_value INT NOT NULL DEFAULT 0,
      reason VARCHAR(128) NOT NULL DEFAULT '',
      payload VARCHAR(255) NOT NULL DEFAULT '',
      status ENUM('pending','processing','done','failed','cancelled') NOT NULL DEFAULT 'pending',
      result_text VARCHAR(255) NOT NULL DEFAULT '',
      notified TINYINT(1) NOT NULL DEFAULT 0,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      claimed_at DATETIME NULL,
      processed_at DATETIME NULL,
      PRIMARY KEY(id),
      KEY idx_tg_action_queue(status,id),
      KEY idx_tg_action_notify(telegram_id,notified,status),
      CONSTRAINT fk_tg_action_actor FOREIGN KEY(actor_account_id) REFERENCES accounts(id) ON DELETE RESTRICT,
      CONSTRAINT fk_tg_action_target FOREIGN KEY(target_account_id) REFERENCES accounts(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def ensure_database_schema() -> None:
    with db_connect() as db:
        with db.cursor() as cur:
            for statement in SCHEMA_STATEMENTS:
                cur.execute(statement)
            cur.execute("DELETE FROM telegram_link_codes WHERE expires_at <= NOW()")
            if OWNER_TELEGRAM_ID > 0:
                cur.execute(
                    """
                    INSERT INTO telegram_staff(telegram_id,role,max_admin_level,enabled)
                    VALUES(%s,'owner',6,1)
                    ON DUPLICATE KEY UPDATE role='owner',max_admin_level=6,enabled=1
                    """,
                    (OWNER_TELEGRAM_ID,),
                )
    log("Telegram-таблицы проверены/созданы автоматически.")


def api_call(method: str, data: Optional[Dict[str, Any]] = None, timeout: int = 40) -> Dict[str, Any]:
    if data is None:
        data = {}
    encoded: Dict[str, str] = {}
    for key, value in data.items():
        encoded[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=urllib.parse.urlencode(encoded).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API {method}: {payload}")
    return payload


def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> None:
    data: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    api_call("sendMessage", data)


def answer_callback(callback_id: str, text: str = "", alert: bool = False) -> None:
    api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text, "show_alert": alert})


def command_name(text: str) -> str:
    first = text.strip().split(maxsplit=1)[0].lower()
    return first.split("@", 1)[0]


def get_linked_account(telegram_id: int) -> Optional[Dict[str, Any]]:
    with db_connect() as db, db.cursor() as cur:
        cur.execute(
            """
            SELECT a.id,a.name,a.level,a.exp,a.admin_level,a.vip_level,a.age,a.sex,a.nationality,
                   IFNULL(a.faction_id,0) faction_id,a.faction_rank,a.family_id,a.family_rank,
                   a.online,a.online_minutes,a.money,a.bank,a.last_login_at,a.registered_at
            FROM telegram_links l JOIN accounts a ON a.id=l.account_id
            WHERE l.telegram_id=%s LIMIT 1
            """,
            (telegram_id,),
        )
        return cur.fetchone()


def get_staff(telegram_id: int) -> Optional[Dict[str, Any]]:
    with db_connect() as db, db.cursor() as cur:
        cur.execute(
            """
            SELECT s.telegram_id,s.account_id,s.role,s.max_admin_level,s.enabled,
                   a.name,a.admin_level AS game_admin
            FROM telegram_staff s
            LEFT JOIN accounts a ON a.id=s.account_id
            WHERE s.telegram_id=%s LIMIT 1
            """,
            (telegram_id,),
        )
        row = cur.fetchone()
        return row if row and int(row.get("enabled") or 0) == 1 else None


def is_manager(staff: Optional[Dict[str, Any]]) -> bool:
    return bool(staff and staff.get("account_id") and staff.get("role") in ("manager", "owner"))


def require_manager(chat_id: int, telegram_id: int) -> Optional[Dict[str, Any]]:
    staff = get_staff(telegram_id)
    if not is_manager(staff):
        send_message(chat_id, "⛔ Команда доступна только привязанному управляющему или владельцу.")
        return None
    return staff


def find_account(name: str) -> Optional[Dict[str, Any]]:
    with db_connect() as db, db.cursor() as cur:
        cur.execute(
            """
            SELECT id,name,level,exp,admin_level,vip_level,age,sex,nationality,
                   IFNULL(faction_id,0) faction_id,faction_rank,family_id,family_rank,
                   online,online_minutes,money,bank,last_login_at,registered_at
            FROM accounts WHERE name=%s LIMIT 1
            """,
            (name,),
        )
        return cur.fetchone()


def faction_name(faction_id: int) -> str:
    if faction_id <= 0:
        return "Нет"
    with db_connect() as db, db.cursor() as cur:
        cur.execute("SELECT name FROM factions WHERE id=%s LIMIT 1", (faction_id,))
        row = cur.fetchone()
        return str(row["name"]) if row else f"ID {faction_id}"


def profile_text(row: Dict[str, Any]) -> str:
    sex = "Мужской" if int(row.get("sex") or 0) == 0 else "Женский"
    online = "🟢 В игре" if int(row.get("online") or 0) else "⚫ Не в игре"
    faction_id = int(row.get("faction_id") or 0)
    last_login = row.get("last_login_at") or "—"
    return (
        f"👤 <b>{html.escape(str(row['name']))}</b>\n"
        f"SQL ID: <code>{row['id']}</code>\n"
        f"Уровень: <b>{row['level']}</b> | EXP: {row['exp']}\n"
        f"Админ-уровень: <b>{row['admin_level']}</b> | VIP: {row['vip_level']}\n"
        f"Возраст: <b>{row['age']}</b> | Пол: {sex}\n"
        f"Национальность: {html.escape(str(row.get('nationality') or '—'))}\n"
        f"Организация: {html.escape(faction_name(faction_id))} ({faction_id})\n"
        f"Ранг: {row.get('faction_rank') or 0}\n"
        f"Семья: {row.get('family_id') or 0} | Ранг: {row.get('family_rank') or 0}\n"
        f"Деньги: ${int(row.get('money') or 0):,}\n"
        f"Банк: ${int(row.get('bank') or 0):,}\n"
        f"Онлайн всего: {row.get('online_minutes') or 0} мин.\n"
        f"Статус: {online}\n"
        f"Последний вход: {html.escape(str(last_login))}"
    ).replace(",", " ")


def list_factions() -> str:
    with db_connect() as db, db.cursor() as cur:
        cur.execute(
            """
            SELECT f.id,f.name,a.name AS leader_name
            FROM factions f LEFT JOIN accounts a ON a.id=f.leader_account_id
            ORDER BY f.id
            """
        )
        rows = cur.fetchall()
    lines = ["🏛 <b>Организации</b>"]
    for row in rows:
        leader = row.get("leader_name") or "свободна"
        lines.append(f"<code>{row['id']}</code> — {html.escape(str(row['name']))} — {html.escape(str(leader))}")
    return "\n".join(lines)


def queue_action(
    telegram_id: int,
    actor_account_id: int,
    action: str,
    target_account_id: Optional[int] = None,
    value: int = 0,
    extra_value: int = 0,
    reason: str = "",
    payload: str = "",
) -> int:
    with db_connect() as db, db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO telegram_actions
              (telegram_id,actor_account_id,target_account_id,action,value,extra_value,reason,payload)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (telegram_id, actor_account_id, target_account_id, action, value, extra_value, reason[:128], payload[:255]),
        )
        return int(cur.lastrowid)


CONFIRMATIONS: Dict[str, Dict[str, Any]] = {}


def make_confirmation(chat_id: int, telegram_id: int, title: str, action_data: Dict[str, Any]) -> None:
    token = secrets.token_urlsafe(8)[:10]
    CONFIRMATIONS[token] = {
        "chat_id": chat_id,
        "telegram_id": telegram_id,
        "expires": time.time() + CONFIRM_TTL,
        "data": action_data,
    }
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Подтвердить", "callback_data": f"yes:{token}"},
            {"text": "❌ Отмена", "callback_data": f"no:{token}"},
        ]]
    }
    send_message(chat_id, title + "\n\nПодтвердить действие?", keyboard)


def process_confirmation(callback: Dict[str, Any]) -> None:
    callback_id = str(callback["id"])
    sender_id = int(callback["from"]["id"])
    data = str(callback.get("data") or "")
    if ":" not in data:
        answer_callback(callback_id, "Некорректная кнопка", True)
        return
    decision, token = data.split(":", 1)
    pending = CONFIRMATIONS.pop(token, None)
    if not pending or pending["expires"] < time.time():
        answer_callback(callback_id, "Подтверждение устарело", True)
        return
    if sender_id != int(pending["telegram_id"]):
        answer_callback(callback_id, "Эта кнопка не для вас", True)
        return
    if decision != "yes":
        answer_callback(callback_id, "Отменено")
        send_message(int(pending["chat_id"]), "❌ Действие отменено.")
        return
    staff = get_staff(sender_id)
    if not is_manager(staff):
        answer_callback(callback_id, "Права были изменены", True)
        return
    action_data = pending["data"]
    action_id = queue_action(
        telegram_id=sender_id,
        actor_account_id=int(staff["account_id"]),
        **action_data,
    )
    answer_callback(callback_id, "Передано игровому серверу")
    send_message(int(pending["chat_id"]), f"⏳ Действие <code>#{action_id}</code> передано игровому моду. Результат придёт сюда автоматически.")


def cleanup_confirmations() -> None:
    now = time.time()
    for token in [key for key, value in CONFIRMATIONS.items() if value["expires"] < now]:
        CONFIRMATIONS.pop(token, None)


def deliver_results() -> None:
    with db_connect() as db, db.cursor() as cur:
        cur.execute(
            """
            SELECT id,telegram_id,status,result_text,action
            FROM telegram_actions
            WHERE notified=0 AND status IN('done','failed','cancelled')
            ORDER BY id LIMIT 25
            """
        )
        rows = cur.fetchall()
        for row in rows:
            icon = "✅" if row["status"] == "done" else "❌"
            text = row.get("result_text") or f"Операция завершена: {row['status']}"
            try:
                send_message(int(row["telegram_id"]), f"{icon} <b>Действие #{row['id']}</b>\n{html.escape(str(text))}")
                cur.execute("UPDATE telegram_actions SET notified=1 WHERE id=%s", (row["id"],))
            except Exception as exc:
                log(f"Не удалось доставить результат #{row['id']}: {exc}")


def help_text(staff: Optional[Dict[str, Any]]) -> str:
    base = (
        "🤖 <b>Black City RP — управление</b>\n\n"
        "/link CODE — привязать аккаунт после /tgcode в игре\n"
        "/profile — мой игровой профиль\n"
        "/unlink — отвязать Telegram\n"
        "/help — помощь"
    )
    if staff and staff.get("account_id"):
        base += (
            "\n\n<b>Команды персонала</b>\n"
            "/player Nick_Name — профиль игрока\n"
            "/factions — организации и лидеры\n"
            "/setadmin Nick_Name 1-5 причина\n"
            "/unadmin Nick_Name причина\n"
            "/setleader Nick_Name ID_организации причина\n"
            "/templeader Nick_Name ID_организации часы причина\n"
            "/unleader Nick_Name причина\n"
            "/aad сообщение — сообщение на весь сервер\n"
            "/actions — последние операции"
        )
    if staff and staff.get("role") == "owner":
        base += (
            "\n\n<b>Владелец</b>\n"
            "/staffadd TELEGRAM_ID viewer|manager MAX_LEVEL\n"
            "/staffdel TELEGRAM_ID\n"
            "/stafflist"
        )
    return base


def handle_link(chat_id: int, telegram_id: int, text: str) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or len(parts[1].strip()) != 8 or not parts[1].strip().isdigit():
        send_message(chat_id, "Использование: <code>/link 12345678</code>\nКод получается в игре командой /tgcode.")
        return
    code = parts[1].strip()
    with db_connect() as db:
        db.begin()
        try:
            with db.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.account_id,a.name FROM telegram_link_codes c
                    JOIN accounts a ON a.id=c.account_id
                    WHERE c.code=%s AND c.expires_at>NOW() FOR UPDATE
                    """,
                    (code,),
                )
                row = cur.fetchone()
                if not row:
                    db.rollback()
                    send_message(chat_id, "❌ Код не найден или уже истёк. Создай новый через /tgcode в игре.")
                    return
                account_id = int(row["account_id"])
                cur.execute("DELETE FROM telegram_links WHERE telegram_id=%s OR account_id=%s", (telegram_id, account_id))
                cur.execute("INSERT INTO telegram_links(telegram_id,account_id) VALUES(%s,%s)", (telegram_id, account_id))
                cur.execute("DELETE FROM telegram_link_codes WHERE account_id=%s", (account_id,))
                if telegram_id == OWNER_TELEGRAM_ID:
                    cur.execute(
                        """
                        INSERT INTO telegram_staff(telegram_id,account_id,role,max_admin_level,enabled)
                        VALUES(%s,%s,'owner',6,1)
                        ON DUPLICATE KEY UPDATE account_id=VALUES(account_id),role='owner',max_admin_level=6,enabled=1
                        """,
                        (telegram_id, account_id),
                    )
                else:
                    cur.execute("UPDATE telegram_staff SET account_id=%s WHERE telegram_id=%s", (account_id, telegram_id))
            db.commit()
            send_message(chat_id, f"✅ Telegram привязан к аккаунту <b>{html.escape(str(row['name']))}</b>.")
        except Exception:
            db.rollback()
            raise


def handle_staff_owner(chat_id: int, telegram_id: int, text: str, command: str) -> bool:
    staff = get_staff(telegram_id)
    if not staff or staff.get("role") != "owner":
        return False
    if command == "/stafflist":
        with db_connect() as db, db.cursor() as cur:
            cur.execute(
                """
                SELECT s.telegram_id,s.role,s.max_admin_level,s.enabled,a.name
                FROM telegram_staff s LEFT JOIN accounts a ON a.id=s.account_id
                ORDER BY FIELD(s.role,'owner','manager','viewer'),s.telegram_id
                """
            )
            rows = cur.fetchall()
        lines = ["👮 <b>Telegram-персонал</b>"]
        for row in rows:
            lines.append(
                f"<code>{row['telegram_id']}</code> — {row['role']} — max {row['max_admin_level']} — "
                f"{html.escape(str(row.get('name') or 'не привязан'))} — {'ON' if row['enabled'] else 'OFF'}"
            )
        send_message(chat_id, "\n".join(lines))
        return True
    if command == "/staffadd":
        parts = text.split(maxsplit=3)
        if len(parts) != 4 or not parts[1].isdigit() or parts[2] not in ("viewer", "manager") or not parts[3].isdigit():
            send_message(chat_id, "Использование: <code>/staffadd TELEGRAM_ID viewer|manager MAX_LEVEL</code>")
            return True
        target_tg = int(parts[1])
        role = parts[2]
        max_level = max(0, min(5, int(parts[3]))) if role == "manager" else 0
        with db_connect() as db, db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telegram_staff(telegram_id,role,max_admin_level,enabled,added_by)
                VALUES(%s,%s,%s,1,%s)
                ON DUPLICATE KEY UPDATE role=VALUES(role),max_admin_level=VALUES(max_admin_level),enabled=1,added_by=VALUES(added_by)
                """,
                (target_tg, role, max_level, telegram_id),
            )
        send_message(chat_id, "✅ Пользователь добавлен. Теперь он должен привязать игровой аккаунт через /link.")
        return True
    if command == "/staffdel":
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].isdigit():
            send_message(chat_id, "Использование: <code>/staffdel TELEGRAM_ID</code>")
            return True
        target_tg = int(parts[1])
        if target_tg == OWNER_TELEGRAM_ID:
            send_message(chat_id, "❌ Нельзя удалить владельца из настроек бота.")
            return True
        with db_connect() as db, db.cursor() as cur:
            cur.execute("DELETE FROM telegram_staff WHERE telegram_id=%s", (target_tg,))
        send_message(chat_id, "✅ Доступ удалён.")
        return True
    return False


def handle_message(message: Dict[str, Any]) -> None:
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = int(chat.get("id") or 0)
    telegram_id = int(sender.get("id") or 0)
    text = str(message.get("text") or "").strip()
    if not chat_id or not telegram_id or not text.startswith("/"):
        return
    if PRIVATE_ONLY and chat.get("type") != "private":
        return

    command = command_name(text)
    staff = get_staff(telegram_id)

    if command in ("/start", "/help"):
        send_message(chat_id, help_text(staff))
        return
    if command == "/link":
        handle_link(chat_id, telegram_id, text)
        return
    if command == "/unlink":
        with db_connect() as db, db.cursor() as cur:
            cur.execute("DELETE FROM telegram_links WHERE telegram_id=%s", (telegram_id,))
            cur.execute("UPDATE telegram_staff SET account_id=NULL WHERE telegram_id=%s", (telegram_id,))
        send_message(chat_id, "✅ Привязка удалена.")
        return
    if command == "/profile":
        row = get_linked_account(telegram_id)
        send_message(chat_id, profile_text(row) if row else "❌ Аккаунт не привязан. В игре используй /tgcode, затем здесь /link CODE.")
        return

    if handle_staff_owner(chat_id, telegram_id, text, command):
        return

    if command == "/player":
        if not staff or not staff.get("account_id"):
            send_message(chat_id, "⛔ Нет доступа персонала.")
            return
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            send_message(chat_id, "Использование: <code>/player Nick_Name</code>")
            return
        row = find_account(parts[1].strip())
        send_message(chat_id, profile_text(row) if row else "❌ Игрок не найден.")
        return
    if command == "/factions":
        if not staff or not staff.get("account_id"):
            send_message(chat_id, "⛔ Нет доступа персонала.")
            return
        send_message(chat_id, list_factions())
        return
    if command == "/actions":
        if not staff or not staff.get("account_id"):
            send_message(chat_id, "⛔ Нет доступа персонала.")
            return
        with db_connect() as db, db.cursor() as cur:
            cur.execute(
                """
                SELECT id,action,status,result_text,created_at FROM telegram_actions
                WHERE telegram_id=%s ORDER BY id DESC LIMIT 10
                """,
                (telegram_id,),
            )
            rows = cur.fetchall()
        lines = ["🧾 <b>Последние действия</b>"]
        for row in rows:
            lines.append(f"#{row['id']} {row['action']} — {row['status']} — {html.escape(str(row.get('result_text') or ''))}")
        send_message(chat_id, "\n".join(lines))
        return

    manager = require_manager(chat_id, telegram_id)
    if manager is None:
        return

    if command == "/setadmin":
        parts = text.split(maxsplit=3)
        if len(parts) != 4 or not parts[2].isdigit():
            send_message(chat_id, "Использование: <code>/setadmin Nick_Name 1-5 причина</code>")
            return
        target = find_account(parts[1])
        level = int(parts[2])
        reason = parts[3].strip()
        if not target or level < 1 or level > 5 or len(reason) < 3:
            send_message(chat_id, "❌ Проверь ник, уровень 1–5 и причину минимум 3 символа.")
            return
        make_confirmation(
            chat_id, telegram_id,
            f"Выдать <b>{level}</b> уровень админки игроку <b>{html.escape(str(target['name']))}</b>?\nПричина: {html.escape(reason)}",
            {"action": "set_admin", "target_account_id": int(target["id"]), "value": level, "reason": reason},
        )
        return
    if command == "/unadmin":
        parts = text.split(maxsplit=2)
        if len(parts) != 3:
            send_message(chat_id, "Использование: <code>/unadmin Nick_Name причина</code>")
            return
        target = find_account(parts[1])
        reason = parts[2].strip()
        if not target or len(reason) < 3:
            send_message(chat_id, "❌ Игрок не найден или причина слишком короткая.")
            return
        make_confirmation(
            chat_id, telegram_id,
            f"Снять админку с <b>{html.escape(str(target['name']))}</b>?\nПричина: {html.escape(reason)}",
            {"action": "unadmin", "target_account_id": int(target["id"]), "reason": reason},
        )
        return
    if command == "/setleader":
        parts = text.split(maxsplit=3)
        if len(parts) != 4 or not parts[2].isdigit():
            send_message(chat_id, "Использование: <code>/setleader Nick_Name ID_организации причина</code>")
            return
        target = find_account(parts[1])
        faction_id = int(parts[2])
        reason = parts[3].strip()
        if not target or faction_id < 1 or faction_id > 18 or len(reason) < 3:
            send_message(chat_id, "❌ Проверь ник, ID организации 1–18 и причину.")
            return
        make_confirmation(
            chat_id, telegram_id,
            f"Назначить <b>{html.escape(str(target['name']))}</b> лидером организации <b>{faction_id}</b>?\nПричина: {html.escape(reason)}",
            {"action": "set_leader", "target_account_id": int(target["id"]), "value": faction_id, "reason": reason},
        )
        return
    if command == "/templeader":
        parts = text.split(maxsplit=4)
        if len(parts) != 5 or not parts[2].isdigit() or not parts[3].isdigit():
            send_message(chat_id, "Использование: <code>/templeader Nick_Name ID_организации часы причина</code>")
            return
        target = find_account(parts[1])
        faction_id = int(parts[2])
        hours = int(parts[3])
        reason = parts[4].strip()
        if not target or faction_id < 1 or faction_id > 18 or hours < 1 or hours > 168 or len(reason) < 3:
            send_message(chat_id, "❌ Проверь ник, организацию 1–18, срок 1–168 часов и причину.")
            return
        make_confirmation(
            chat_id, telegram_id,
            f"Выдать <b>{html.escape(str(target['name']))}</b> временную лидерку организации <b>{faction_id}</b> на <b>{hours} ч.</b>?\nПричина: {html.escape(reason)}",
            {"action": "temp_leader", "target_account_id": int(target["id"]), "value": faction_id, "extra_value": hours, "reason": reason},
        )
        return
    if command == "/unleader":
        parts = text.split(maxsplit=2)
        if len(parts) != 3:
            send_message(chat_id, "Использование: <code>/unleader Nick_Name причина</code>")
            return
        target = find_account(parts[1])
        reason = parts[2].strip()
        if not target or len(reason) < 3:
            send_message(chat_id, "❌ Игрок не найден или причина слишком короткая.")
            return
        make_confirmation(
            chat_id, telegram_id,
            f"Снять лидерку с <b>{html.escape(str(target['name']))}</b>?\nПричина: {html.escape(reason)}",
            {"action": "unleader", "target_account_id": int(target["id"]), "reason": reason},
        )
        return
    if command == "/aad":
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or len(parts[1].strip()) < 3 or len(parts[1].strip()) > 120:
            send_message(chat_id, "Использование: <code>/aad сообщение 3–120 символов</code>")
            return
        action_id = queue_action(
            telegram_id=telegram_id,
            actor_account_id=int(manager["account_id"]),
            action="aad",
            payload=parts[1].strip(),
            reason="Сообщение из Telegram",
        )
        send_message(chat_id, f"⏳ Сообщение передано серверу, действие <code>#{action_id}</code>.")
        return

    send_message(chat_id, help_text(staff))


def validate_config() -> None:
    if not BOT_TOKEN or BOT_TOKEN.startswith("PASTE_") or ":" not in BOT_TOKEN:
        raise RuntimeError("Вставьте токен Telegram-бота в BOT_TOKEN в начале tg_admin_bot.py.")
    if int(OWNER_TELEGRAM_ID) <= 0:
        raise RuntimeError("Укажите свой цифровой Telegram ID в OWNER_TELEGRAM_ID.")


def set_bot_commands() -> None:
    commands = [
        {"command": "profile", "description": "Мой игровой профиль"},
        {"command": "link", "description": "Привязать игровой аккаунт"},
        {"command": "player", "description": "Проверить игрока"},
        {"command": "factions", "description": "Список организаций"},
        {"command": "actions", "description": "Последние действия"},
        {"command": "help", "description": "Список команд"},
    ]
    try:
        api_call("setMyCommands", {"commands": commands})
    except Exception as exc:
        log(f"Не удалось установить меню команд: {exc}")


def main() -> None:
    validate_config()
    ensure_database_schema()
    me = api_call("getMe")["result"]
    log(f"Бот @{me.get('username')} запущен. Long polling активен.")
    set_bot_commands()

    offset = 0
    last_result_check = 0.0
    while True:
        try:
            now = time.time()
            if now - last_result_check >= RESULT_CHECK_SECONDS:
                deliver_results()
                cleanup_confirmations()
                last_result_check = now
            updates = api_call(
                "getUpdates",
                {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
                timeout=35,
            )["result"]
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                try:
                    if "callback_query" in update:
                        process_confirmation(update["callback_query"])
                    elif "message" in update:
                        handle_message(update["message"])
                except Exception as exc:
                    log(f"Ошибка обработки update {update.get('update_id')}: {exc}\n{traceback.format_exc()}")
                    msg = update.get("message")
                    if msg and msg.get("chat", {}).get("id"):
                        try:
                            send_message(int(msg["chat"]["id"]), "❌ Внутренняя ошибка. Подробности записаны в telegram_bot.log.")
                        except Exception:
                            pass
        except KeyboardInterrupt:
            log("Бот остановлен.")
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, pymysql.MySQLError) as exc:
            log(f"Временная ошибка сети/БД: {exc}. Повтор через 5 секунд.")
            time.sleep(5)
        except Exception as exc:
            log(f"Неожиданная ошибка: {exc}\n{traceback.format_exc()}")
            time.sleep(5)


if __name__ == "__main__":
    main()
