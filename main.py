"""
СИНДИКАТ — VK Mini App для Bothost в одном файле.

Что находится в main.py:
- автоматическая установка FastAPI и Uvicorn при первом запуске;
- интерфейс Mini App (HTML/CSS/JavaScript);
- Python-сервер и игровое API;
- SQLite-база игроков;
- проверка подписи параметров запуска VK.

НАСТРОЙКА БЕЗ .env И БЕЗ ТЕРМИНАЛА:
1. Найдите ниже VK_APP_SECRET и вставьте защищённый ключ VK Mini App.
2. Загрузите файл под именем main.py на Bothost.
3. В панели Bothost включите «Использовать домен» и укажите порт 8000.
4. В настройках VK Mini App укажите выданный Bothost HTTPS-адрес.

Не публикуйте файл с настоящим VK_APP_SECRET в открытом репозитории.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import os
import random
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator
from urllib.parse import parse_qsl, urlencode


# =============================================================
# АВТОУСТАНОВКА БИБЛИОТЕК
# Bothost запускает main.py, а файл сам установит недостающие пакеты.
# =============================================================
REQUIRED_PACKAGES = (
    "fastapi==0.139.2",
    "uvicorn==0.51.0",
)


def ensure_dependencies() -> None:
    try:
        import fastapi  # noqa: F401
        import pydantic  # noqa: F401
        import uvicorn  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    print("[SETUP] Устанавливаю FastAPI и Uvicorn автоматически...", flush=True)
    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                *REQUIRED_PACKAGES,
            ]
        )
    except Exception as exc:
        raise RuntimeError(
            "Не удалось автоматически установить библиотеки. "
            "Проверьте логи сборки Bothost."
        ) from exc

    importlib.invalidate_caches()
    print("[SETUP] Библиотеки установлены. Перезапускаю приложение...", flush=True)
    os.execv(sys.executable, [sys.executable, *sys.argv])


ensure_dependencies()

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import uvicorn


# =============================================================
# НАСТРОЙКИ — ВСТАВЬ КЛЮЧ ПРЯМО СЮДА
# =============================================================
APP_TITLE = "СИНДИКАТ"
VK_APP_SECRET = "qPWcQYjIDRKw6n2s1kgm"
DEV_MODE = False

# Bothost сам передаёт PORT из панели. Файл .env для этого не нужен.
PORT = int(os.environ.get("PORT", "8000"))
ENERGY_REGEN_SECONDS = 5 * 60

# На Bothost /app/data используется для постоянного хранения файлов.
DATA_DIR = Path("/app/data") if Path("/app").is_dir() else Path(__file__).with_name("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "vk_miniapp_game.db"

SECRET_IS_CONFIGURED = bool(
    VK_APP_SECRET.strip()
    and not VK_APP_SECRET.startswith("ВСТАВЬ_")
)

app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None)


class LaunchRequest(BaseModel):
    launch_params: str = ""


class GameRequest(LaunchRequest):
    item: str | None = Field(default=None, max_length=32)


class GameDB:
    def __init__(self, path: Path) -> None:
        self.lock = RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS players (
                    vk_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1,
                    xp INTEGER NOT NULL DEFAULT 0,
                    money INTEGER NOT NULL DEFAULT 500,
                    energy INTEGER NOT NULL DEFAULT 10,
                    max_energy INTEGER NOT NULL DEFAULT 10,
                    energy_updated INTEGER NOT NULL,
                    hp INTEGER NOT NULL DEFAULT 100,
                    max_hp INTEGER NOT NULL DEFAULT 100,
                    attack INTEGER NOT NULL DEFAULT 10,
                    defense INTEGER NOT NULL DEFAULT 2,
                    medkits INTEGER NOT NULL DEFAULT 1,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    rating INTEGER NOT NULL DEFAULT 1000,
                    last_daily_day INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                )
                """
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            finally:
                cursor.close()

    def ensure_player(self, vk_id: int, name: str) -> None:
        now = int(time.time())
        safe_name = (name or f"Игрок {vk_id}").strip()[:40]
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO players
                (vk_id, name, energy_updated, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (vk_id, safe_name, now, now),
            )
            self.conn.execute(
                "UPDATE players SET name = ? WHERE vk_id = ?",
                (safe_name, vk_id),
            )

    def _prepare(self, cur: sqlite3.Cursor, vk_id: int) -> sqlite3.Row:
        row = cur.execute("SELECT * FROM players WHERE vk_id = ?", (vk_id,)).fetchone()
        if row is None:
            raise LookupError("Игрок не найден")

        now = int(time.time())
        if row["energy"] < row["max_energy"]:
            gained = max(0, (now - row["energy_updated"]) // ENERGY_REGEN_SECONDS)
            if gained:
                energy = min(row["max_energy"], row["energy"] + gained)
                updated = now if energy >= row["max_energy"] else (
                    row["energy_updated"] + gained * ENERGY_REGEN_SECONDS
                )
                cur.execute(
                    "UPDATE players SET energy = ?, energy_updated = ? WHERE vk_id = ?",
                    (energy, updated, vk_id),
                )

        return cur.execute("SELECT * FROM players WHERE vk_id = ?", (vk_id,)).fetchone()

    @staticmethod
    def serialize(row: sqlite3.Row) -> dict:
        next_level_xp = row["level"] * 100
        return {
            "vk_id": row["vk_id"],
            "name": row["name"],
            "level": row["level"],
            "xp": row["xp"],
            "next_level_xp": next_level_xp,
            "money": row["money"],
            "energy": row["energy"],
            "max_energy": row["max_energy"],
            "hp": row["hp"],
            "max_hp": row["max_hp"],
            "attack": row["attack"],
            "defense": row["defense"],
            "medkits": row["medkits"],
            "wins": row["wins"],
            "losses": row["losses"],
            "rating": row["rating"],
        }

    def profile(self, vk_id: int) -> dict:
        with self.transaction() as cur:
            return self.serialize(self._prepare(cur, vk_id))

    def battle(self, vk_id: int) -> tuple[dict, dict]:
        with self.transaction() as cur:
            row = self._prepare(cur, vk_id)
            if row["energy"] < 1:
                raise ValueError("Недостаточно энергии. Одна единица восстановится через 5 минут.")
            if row["hp"] <= 0:
                raise ValueError("Сначала восстанови здоровье аптечкой.")

            enemy_level = max(1, row["level"] + random.choice([-1, 0, 0, 1]))
            enemy_names = ["Уличный рейдер", "Дрон охраны", "Наёмник", "Кибер-громила"]
            enemy_name = random.choice(enemy_names)
            enemy_hp = 45 + enemy_level * 12 + random.randint(-5, 8)
            enemy_attack = 7 + enemy_level * 3

            player_hp = row["hp"]
            start_hp = player_hp
            rounds = 0
            log: list[str] = []

            while player_hp > 0 and enemy_hp > 0 and rounds < 20:
                rounds += 1
                player_damage = max(1, row["attack"] + random.randint(-2, 5))
                enemy_hp -= player_damage
                log.append(f"Ты нанёс {player_damage} урона")
                if enemy_hp <= 0:
                    break

                enemy_damage = max(1, enemy_attack + random.randint(-2, 3) - row["defense"])
                player_hp -= enemy_damage
                log.append(f"Враг нанёс {enemy_damage} урона")

            won = enemy_hp <= 0
            hp_after = max(0, player_hp)
            spent_hp = start_hp - hp_after

            if won:
                reward = random.randint(80, 135) + enemy_level * 15
                gained_xp = random.randint(24, 38) + enemy_level * 3
                new_xp = row["xp"] + gained_xp
                new_level = row["level"]
                max_hp = row["max_hp"]
                max_energy = row["max_energy"]
                attack = row["attack"]

                while new_xp >= new_level * 100:
                    new_xp -= new_level * 100
                    new_level += 1
                    max_hp += 10
                    max_energy += 1
                    attack += 2
                    hp_after = max_hp

                cur.execute(
                    """
                    UPDATE players
                    SET energy = energy - 1,
                        energy_updated = CASE WHEN energy = max_energy THEN ? ELSE energy_updated END,
                        hp = ?, money = money + ?, xp = ?, level = ?,
                        max_hp = ?, max_energy = ?, attack = ?,
                        wins = wins + 1, rating = rating + ?
                    WHERE vk_id = ?
                    """,
                    (
                        int(time.time()), hp_after, reward, new_xp, new_level,
                        max_hp, max_energy, attack, 8 + enemy_level, vk_id,
                    ),
                )
                result = {
                    "won": True,
                    "title": "Победа!",
                    "text": f"{enemy_name} повержен. +{reward} кредитов, +{gained_xp} опыта.",
                    "enemy": enemy_name,
                    "rounds": rounds,
                    "damage_taken": spent_hp,
                    "log": log[-6:],
                }
            else:
                cur.execute(
                    """
                    UPDATE players
                    SET energy = energy - 1,
                        energy_updated = CASE WHEN energy = max_energy THEN ? ELSE energy_updated END,
                        hp = 0, losses = losses + 1,
                        rating = MAX(0, rating - 10)
                    WHERE vk_id = ?
                    """,
                    (int(time.time()), vk_id),
                )
                result = {
                    "won": False,
                    "title": "Поражение",
                    "text": f"{enemy_name} оказался сильнее. Используй аптечку и улучши снаряжение.",
                    "enemy": enemy_name,
                    "rounds": rounds,
                    "damage_taken": start_hp,
                    "log": log[-6:],
                }

            updated = self._prepare(cur, vk_id)
            return self.serialize(updated), result

    def daily(self, vk_id: int) -> tuple[dict, str]:
        day = int(time.time() // 86400)
        with self.transaction() as cur:
            row = self._prepare(cur, vk_id)
            if row["last_daily_day"] == day:
                raise ValueError("Сегодня ежедневная награда уже получена.")
            reward = 250 + row["level"] * 25
            cur.execute(
                "UPDATE players SET money = money + ?, last_daily_day = ? WHERE vk_id = ?",
                (reward, day, vk_id),
            )
            return self.serialize(self._prepare(cur, vk_id)), f"Получено {reward} кредитов."

    def heal(self, vk_id: int) -> tuple[dict, str]:
        with self.transaction() as cur:
            row = self._prepare(cur, vk_id)
            if row["medkits"] < 1:
                raise ValueError("Аптечек нет. Купи аптечку в магазине.")
            if row["hp"] >= row["max_hp"]:
                raise ValueError("Здоровье уже полное.")
            healed = min(60, row["max_hp"] - row["hp"])
            cur.execute(
                "UPDATE players SET hp = hp + ?, medkits = medkits - 1 WHERE vk_id = ?",
                (healed, vk_id),
            )
            return self.serialize(self._prepare(cur, vk_id)), f"Восстановлено {healed} HP."

    def buy(self, vk_id: int, item: str) -> tuple[dict, str]:
        prices = {"weapon": 700, "armor": 600, "medkit": 180}
        if item not in prices:
            raise ValueError("Неизвестный товар.")
        price = prices[item]

        with self.transaction() as cur:
            row = self._prepare(cur, vk_id)
            if row["money"] < price:
                raise ValueError(f"Нужно {price} кредитов.")

            if item == "weapon":
                cur.execute(
                    "UPDATE players SET money = money - ?, attack = attack + 3 WHERE vk_id = ?",
                    (price, vk_id),
                )
                message = "Оружие улучшено: +3 к атаке."
            elif item == "armor":
                cur.execute(
                    "UPDATE players SET money = money - ?, defense = defense + 2 WHERE vk_id = ?",
                    (price, vk_id),
                )
                message = "Броня улучшена: +2 к защите."
            else:
                cur.execute(
                    "UPDATE players SET money = money - ?, medkits = medkits + 1 WHERE vk_id = ?",
                    (price, vk_id),
                )
                message = "Аптечка добавлена в инвентарь."

            return self.serialize(self._prepare(cur, vk_id)), message

    def leaderboard(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT vk_id, name, level, rating, wins
                FROM players
                ORDER BY rating DESC, wins DESC, level DESC
                LIMIT 10
                """
            ).fetchall()
            return [dict(row) for row in rows]


db = GameDB(DB_PATH)


def verify_launch_params(raw_params: str) -> dict[str, str]:
    params = dict(parse_qsl(raw_params.lstrip("?"), keep_blank_values=True))

    if DEV_MODE and "dev_user_id" in params:
        try:
            dev_id = int(params["dev_user_id"])
        except ValueError as exc:
            raise HTTPException(400, "Некорректный dev_user_id") from exc
        return {"vk_user_id": str(dev_id), "vk_first_name": "Тестовый", "vk_last_name": "Игрок"}

    if not SECRET_IS_CONFIGURED:
        raise HTTPException(500, "В main.py не вставлен VK_APP_SECRET")

    received_sign = params.get("sign", "")
    if not received_sign:
        raise HTTPException(401, "В параметрах запуска отсутствует подпись VK")

    vk_params = sorted((key, value) for key, value in params.items() if key.startswith("vk_"))
    query_string = urlencode(vk_params)
    digest = hmac.new(
        VK_APP_SECRET.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_sign = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    if not hmac.compare_digest(expected_sign, received_sign):
        raise HTTPException(401, "Подпись параметров запуска VK не прошла проверку")

    if "vk_user_id" not in params:
        raise HTTPException(401, "Не найден vk_user_id")
    return params


def get_identity(raw_params: str) -> tuple[int, str]:
    params = verify_launch_params(raw_params)
    try:
        vk_id = int(params["vk_user_id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(401, "Некорректный ID пользователя") from exc

    first = params.get("vk_first_name", "")
    last = params.get("vk_last_name", "")
    name = f"{first} {last}".strip() or f"Игрок {vk_id}"
    return vk_id, name


def success(player: dict | None = None, message: str = "", **extra: object) -> dict:
    payload: dict[str, object] = {"ok": True, "message": message}
    if player is not None:
        payload["player"] = player
    payload.update(extra)
    return payload


def run_game_action(request: GameRequest, action: str) -> dict:
    vk_id, name = get_identity(request.launch_params)
    db.ensure_player(vk_id, name)
    try:
        if action == "battle":
            player, battle = db.battle(vk_id)
            return success(player, battle["text"], battle=battle)
        if action == "daily":
            player, message = db.daily(vk_id)
            return success(player, message)
        if action == "heal":
            player, message = db.heal(vk_id)
            return success(player, message)
        if action == "buy":
            player, message = db.buy(vk_id, request.item or "")
            return success(player, message)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(404, "Действие не найдено")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": APP_TITLE,
        "port": PORT,
        "database": str(DB_PATH),
        "vk_secret_configured": SECRET_IS_CONFIGURED,
    }


@app.post("/api/init")
def api_init(request: LaunchRequest) -> dict:
    vk_id, name = get_identity(request.launch_params)
    db.ensure_player(vk_id, name)
    return success(db.profile(vk_id), "Игра загружена", leaderboard=db.leaderboard())


@app.post("/api/battle")
def api_battle(request: GameRequest) -> dict:
    return run_game_action(request, "battle")


@app.post("/api/daily")
def api_daily(request: GameRequest) -> dict:
    return run_game_action(request, "daily")


@app.post("/api/heal")
def api_heal(request: GameRequest) -> dict:
    return run_game_action(request, "heal")


@app.post("/api/buy")
def api_buy(request: GameRequest) -> dict:
    return run_game_action(request, "buy")


@app.post("/api/leaderboard")
def api_leaderboard(request: LaunchRequest) -> dict:
    vk_id, name = get_identity(request.launch_params)
    db.ensure_player(vk_id, name)
    return success(leaderboard=db.leaderboard())


INDEX_HTML = r'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#090d18">
  <title>СИНДИКАТ</title>
  <script src="https://unpkg.com/@vkontakte/vk-bridge/dist/browser.min.js"></script>
  <style>
    :root {
      --bg: #070a12;
      --panel: #101625;
      --panel-2: #171f33;
      --line: rgba(255,255,255,.09);
      --text: #f5f7ff;
      --muted: #99a4bc;
      --accent: #7c5cff;
      --accent-2: #00d6c9;
      --danger: #ff4d6d;
      --success: #39dd91;
      --shadow: 0 18px 50px rgba(0,0,0,.35);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 15% -10%, rgba(124,92,255,.25), transparent 34%),
        radial-gradient(circle at 100% 20%, rgba(0,214,201,.14), transparent 28%),
        var(--bg);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button { font: inherit; }
    .app { width: min(100%, 760px); margin: 0 auto; padding: 18px 16px 110px; }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .brand small { color: var(--accent-2); letter-spacing: .18em; font-weight: 800; }
    .brand h1 { margin: 3px 0 0; font-size: 26px; letter-spacing: .03em; }
    .avatar {
      width: 48px; height: 48px; border-radius: 16px; display: grid; place-items: center;
      background: linear-gradient(145deg, var(--accent), #3b2d8e); font-weight: 900;
      box-shadow: 0 10px 30px rgba(124,92,255,.28);
    }
    .hero {
      margin-top: 18px; padding: 18px; border: 1px solid var(--line); border-radius: 24px;
      background: linear-gradient(145deg, rgba(23,31,51,.95), rgba(13,18,31,.95));
      box-shadow: var(--shadow); overflow: hidden; position: relative;
    }
    .hero::after {
      content: ""; position: absolute; width: 180px; height: 180px; right: -70px; top: -90px;
      border: 1px solid rgba(0,214,201,.24); border-radius: 50%; box-shadow: 0 0 50px rgba(0,214,201,.08);
    }
    .level-row, .bar-label, .stats-grid, .section-title { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
    .level-badge { color: var(--accent-2); font-weight: 800; }
    .player-name { font-size: 22px; font-weight: 800; margin-top: 6px; }
    .bar-label { margin-top: 15px; color: var(--muted); font-size: 13px; }
    .bar { height: 10px; background: rgba(255,255,255,.07); border-radius: 999px; overflow: hidden; margin-top: 7px; }
    .bar > span { height: 100%; display: block; border-radius: inherit; transition: width .3s ease; }
    .xp { background: linear-gradient(90deg, var(--accent), #a790ff); }
    .hp { background: linear-gradient(90deg, var(--danger), #ff8298); }
    .energy { background: linear-gradient(90deg, var(--accent-2), #68fff4); }
    .stats-grid { margin-top: 15px; align-items: stretch; }
    .stat { flex: 1; padding: 12px; border-radius: 16px; background: rgba(255,255,255,.045); border: 1px solid var(--line); }
    .stat span { display: block; color: var(--muted); font-size: 12px; }
    .stat strong { display: block; margin-top: 4px; font-size: 18px; }
    .section { margin-top: 22px; }
    .section-title h2 { margin: 0; font-size: 18px; }
    .section-title span { color: var(--muted); font-size: 13px; }
    .battle-card {
      margin-top: 12px; padding: 18px; border-radius: 22px; border: 1px solid rgba(255,77,109,.25);
      background: linear-gradient(145deg, rgba(255,77,109,.10), rgba(16,22,37,.96));
    }
    .enemy { display: flex; align-items: center; gap: 13px; }
    .enemy-icon { width: 56px; height: 56px; border-radius: 18px; display: grid; place-items: center; font-size: 28px; background: rgba(255,77,109,.12); }
    .enemy strong { display: block; font-size: 18px; }
    .enemy span { color: var(--muted); font-size: 13px; }
    .primary, .secondary, .danger-btn {
      border: 0; border-radius: 16px; padding: 14px 16px; font-weight: 800; cursor: pointer;
      transition: transform .12s ease, opacity .12s ease; min-height: 50px;
    }
    button:active { transform: scale(.98); }
    button:disabled { opacity: .55; cursor: wait; }
    .primary { color: white; background: linear-gradient(135deg, var(--accent), #5d42dd); box-shadow: 0 12px 28px rgba(124,92,255,.23); }
    .danger-btn { color: white; background: linear-gradient(135deg, var(--danger), #c72e50); width: 100%; margin-top: 16px; }
    .secondary { color: var(--text); background: var(--panel-2); border: 1px solid var(--line); }
    .actions { margin-top: 12px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .shop { margin-top: 12px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
    .shop-item { padding: 14px 10px; border-radius: 18px; border: 1px solid var(--line); background: var(--panel); text-align: left; color: var(--text); cursor: pointer; }
    .shop-item b { display: block; margin-top: 7px; }
    .shop-item small { display: block; margin-top: 5px; color: var(--muted); }
    .ranking { margin-top: 12px; border: 1px solid var(--line); border-radius: 20px; overflow: hidden; }
    .rank { display: grid; grid-template-columns: 34px 1fr auto; gap: 10px; align-items: center; padding: 13px 14px; background: rgba(16,22,37,.8); border-bottom: 1px solid var(--line); }
    .rank:last-child { border-bottom: 0; }
    .rank-num { color: var(--accent-2); font-weight: 900; }
    .rank small { color: var(--muted); display: block; margin-top: 2px; }
    .toast {
      position: fixed; z-index: 20; left: 50%; bottom: 88px; transform: translate(-50%, 20px);
      width: min(calc(100% - 32px), 680px); padding: 13px 15px; border-radius: 15px;
      background: rgba(19,26,43,.97); border: 1px solid var(--line); box-shadow: var(--shadow);
      opacity: 0; pointer-events: none; transition: .22s ease;
    }
    .toast.show { opacity: 1; transform: translate(-50%, 0); }
    .toast.error { border-color: rgba(255,77,109,.45); }
    .battle-result { display: none; margin-top: 12px; padding: 15px; border-radius: 18px; background: rgba(0,0,0,.18); border: 1px solid var(--line); }
    .battle-result.show { display: block; }
    .battle-result h3 { margin: 0 0 7px; }
    .battle-log { color: var(--muted); font-size: 13px; line-height: 1.55; margin-top: 9px; }
    .loading { min-height: 70vh; display: grid; place-items: center; color: var(--muted); text-align: center; }
    .spinner { width: 42px; height: 42px; border: 4px solid rgba(255,255,255,.09); border-top-color: var(--accent); border-radius: 50%; animation: spin .8s linear infinite; margin: 0 auto 14px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (max-width: 520px) {
      .shop { grid-template-columns: 1fr; }
      .stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); }
      .stat { padding: 10px 8px; }
      .stat strong { font-size: 16px; }
    }
  </style>
</head>
<body>
  <main class="app">
    <div id="loading" class="loading"><div><div class="spinner"></div><div>Подключаемся к Синдикату…</div></div></div>
    <div id="game" hidden>
      <header class="topbar">
        <div class="brand"><small>NEON DISTRICT</small><h1>СИНДИКАТ</h1></div>
        <div class="avatar" id="avatar">?</div>
      </header>

      <section class="hero">
        <div class="level-row"><span class="level-badge" id="level">УРОВЕНЬ 1</span><strong id="money">500 ₡</strong></div>
        <div class="player-name" id="name">Игрок</div>
        <div class="bar-label"><span>Опыт</span><span id="xpText">0 / 100</span></div>
        <div class="bar"><span class="xp" id="xpBar" style="width:0%"></span></div>
        <div class="bar-label"><span>Здоровье</span><span id="hpText">100 / 100</span></div>
        <div class="bar"><span class="hp" id="hpBar" style="width:100%"></span></div>
        <div class="bar-label"><span>Энергия</span><span id="energyText">10 / 10</span></div>
        <div class="bar"><span class="energy" id="energyBar" style="width:100%"></span></div>
        <div class="stats-grid">
          <div class="stat"><span>Атака</span><strong id="attack">10</strong></div>
          <div class="stat"><span>Защита</span><strong id="defense">2</strong></div>
          <div class="stat"><span>Рейтинг</span><strong id="rating">1000</strong></div>
        </div>
      </section>

      <section class="section">
        <div class="section-title"><h2>Боевой сектор</h2><span>−1 энергия</span></div>
        <div class="battle-card">
          <div class="enemy"><div class="enemy-icon">☠️</div><div><strong>Случайный противник</strong><span>Сложность зависит от твоего уровня</span></div></div>
          <button class="danger-btn" data-action="battle">⚔️ НАЧАТЬ БОЙ</button>
          <div class="battle-result" id="battleResult"><h3 id="battleTitle"></h3><div id="battleText"></div><div class="battle-log" id="battleLog"></div></div>
        </div>
        <div class="actions">
          <button class="primary" data-action="daily">🎁 Награда</button>
          <button class="secondary" data-action="heal">💊 Аптечка <span id="medkits">1</span></button>
        </div>
      </section>

      <section class="section">
        <div class="section-title"><h2>Чёрный рынок</h2><span>Усиления навсегда</span></div>
        <div class="shop">
          <button class="shop-item" data-buy="weapon">🗡️<b>Оружие +3</b><small>700 кредитов</small></button>
          <button class="shop-item" data-buy="armor">🛡️<b>Броня +2</b><small>600 кредитов</small></button>
          <button class="shop-item" data-buy="medkit">💊<b>Аптечка</b><small>180 кредитов</small></button>
        </div>
      </section>

      <section class="section">
        <div class="section-title"><h2>Рейтинг города</h2><span>Топ-10</span></div>
        <div class="ranking" id="ranking"></div>
      </section>
    </div>
  </main>
  <div class="toast" id="toast" role="status"></div>

  <script>
    const launchParams = location.search.slice(1);
    let player = null;
    let busy = false;

    const $ = (id) => document.getElementById(id);
    const clampPercent = (value, max) => Math.max(0, Math.min(100, max ? value / max * 100 : 0));

    async function initBridge() {
      try {
        if (window.vkBridge) {
          await window.vkBridge.send('VKWebAppInit');
          await window.vkBridge.send('VKWebAppSetViewSettings', {
            status_bar_style: 'light',
            action_bar_color: '#090d18',
            navigation_bar_color: '#090d18'
          });
        }
      } catch (_) {}
    }

    async function api(path, body = {}) {
      const response = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({launch_params: launchParams, ...body})
      });
      const data = await response.json().catch(() => ({detail: 'Сервер вернул некорректный ответ'}));
      if (!response.ok) throw new Error(data.detail || 'Ошибка запроса');
      return data;
    }

    function renderPlayer(p) {
      player = p;
      $('name').textContent = p.name;
      $('avatar').textContent = p.name.trim().charAt(0).toUpperCase();
      $('level').textContent = `УРОВЕНЬ ${p.level}`;
      $('money').textContent = `${p.money.toLocaleString('ru-RU')} ₡`;
      $('xpText').textContent = `${p.xp} / ${p.next_level_xp}`;
      $('xpBar').style.width = `${clampPercent(p.xp, p.next_level_xp)}%`;
      $('hpText').textContent = `${p.hp} / ${p.max_hp}`;
      $('hpBar').style.width = `${clampPercent(p.hp, p.max_hp)}%`;
      $('energyText').textContent = `${p.energy} / ${p.max_energy}`;
      $('energyBar').style.width = `${clampPercent(p.energy, p.max_energy)}%`;
      $('attack').textContent = p.attack;
      $('defense').textContent = p.defense;
      $('rating').textContent = p.rating;
      $('medkits').textContent = p.medkits;
    }

    function renderRanking(items) {
      $('ranking').innerHTML = items.length ? items.map((item, index) => `
        <div class="rank">
          <div class="rank-num">#${index + 1}</div>
          <div><strong>${escapeHtml(item.name)}</strong><small>Уровень ${item.level} · Побед ${item.wins}</small></div>
          <strong>${item.rating}</strong>
        </div>`).join('') : '<div class="rank"><div>Рейтинг пока пуст</div></div>';
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
    }

    function toast(message, error = false) {
      const el = $('toast');
      el.textContent = message;
      el.classList.toggle('error', error);
      el.classList.add('show');
      clearTimeout(toast.timer);
      toast.timer = setTimeout(() => el.classList.remove('show'), 2800);
    }

    function setBusy(value) {
      busy = value;
      document.querySelectorAll('button').forEach(btn => btn.disabled = value);
    }

    async function refreshRanking() {
      const data = await api('/api/leaderboard');
      renderRanking(data.leaderboard || []);
    }

    async function perform(path, body = {}) {
      if (busy) return;
      setBusy(true);
      try {
        const data = await api(path, body);
        if (data.player) renderPlayer(data.player);
        toast(data.message || 'Готово');
        if (data.battle) {
          $('battleResult').classList.add('show');
          $('battleTitle').textContent = data.battle.title;
          $('battleText').textContent = data.battle.text;
          $('battleLog').innerHTML = (data.battle.log || []).map(escapeHtml).join('<br>');
        }
        await refreshRanking();
      } catch (error) {
        toast(error.message, true);
      } finally {
        setBusy(false);
      }
    }

    document.addEventListener('click', (event) => {
      const actionButton = event.target.closest('[data-action]');
      if (actionButton) perform(`/api/${actionButton.dataset.action}`);
      const buyButton = event.target.closest('[data-buy]');
      if (buyButton) perform('/api/buy', {item: buyButton.dataset.buy});
    });

    async function start() {
      await initBridge();
      try {
        const data = await api('/api/init');
        renderPlayer(data.player);
        renderRanking(data.leaderboard || []);
        $('loading').hidden = true;
        $('game').hidden = false;
      } catch (error) {
        $('loading').innerHTML = `<div><strong>Не удалось открыть игру</strong><p>${escapeHtml(error.message)}</p></div>`;
      }
    }

    start();
  </script>
</body>
</html>'''


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
