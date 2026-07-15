#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Однофайловый Telegram-бот для компиляции SA-MP/Open.MP gamemode:
.pwn -> .amx

Подходит для Linux-хостинга, включая Bothost.
Никакие Python-библиотеки устанавливать не нужно.

При первом запуске бот автоматически скачивает:
1. Pawn Compiler 3.10.10 для Linux;
2. стандартные Pawn includes;
3. стандартные SA-MP includes 0.3.7 R2.

Настройка токена:
- предпочтительно задать переменную окружения BOT_TOKEN;
- либо вставить токен ниже в поле TOKEN_FALLBACK.

Важно:
Если исходник содержит нестандартные #include, например:
    YSI, streamer, sscanf2, a_mysql, foreach, pawn.cmd
то соответствующие .inc тоже должны быть установлены на сервере.
Если весь код действительно находится в одном .pwn и используется только
<a_samp> и стандартная библиотека Pawn, дополнительных файлов не нужно.
"""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# НАСТРОЙКИ
# ---------------------------------------------------------------------------

# Если Bothost не передаёт токен как BOT_TOKEN, вставь его между кавычками.
TOKEN_FALLBACK = ""

BOT_TOKEN = (
    os.getenv("BOT_TOKEN", "8975361055:AAET6brDJIAonm58z-2CNCHG-1WEMuC0Rmc").strip()
    or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    or os.getenv("TOKEN", "").strip()
    or TOKEN_FALLBACK.strip()
)

# Можно ограничить доступ одним или несколькими Telegram ID:
# ALLOWED_USER_IDS=123456789,987654321
# Пустое значение разрешает пользоваться ботом всем.
ALLOWED_USER_IDS = {
    int(value.strip())
    for value in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if value.strip().isdigit()
}

BASE_DIR = Path(__file__).resolve().parent
TOOLCHAIN_DIR = Path(
    os.getenv("PAWN_TOOLCHAIN_DIR", str(BASE_DIR / ".pawn_toolchain"))
).resolve()

COMPILER_VERSION = "3.10.10"
COMPILER_URL = (
    "https://github.com/pawn-lang/compiler/releases/download/"
    "v3.10.10/pawnc-3.10.10-linux.tar.gz"
)
PAWN_STDLIB_URL = (
    "https://github.com/pawn-lang/pawn-stdlib/"
    "archive/refs/heads/master.zip"
)
SAMP_STDLIB_URL = (
    "https://github.com/pawn-lang/samp-stdlib/"
    "archive/refs/tags/0.3.7-R2-2-1.zip"
)

MAX_PWN_SIZE = int(os.getenv("MAX_PWN_SIZE", str(10 * 1024 * 1024)))
MAX_AMX_SIZE = int(os.getenv("MAX_AMX_SIZE", str(48 * 1024 * 1024)))
COMPILE_TIMEOUT = int(os.getenv("COMPILE_TIMEOUT", "45"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "90"))
POLL_TIMEOUT = int(os.getenv("POLL_TIMEOUT", "30"))

# Дополнительные параметры pawncc можно задать строкой:
# PAWN_FLAGS=-d3 -O1
PAWN_FLAGS = os.getenv("PAWN_FLAGS", "-d3").split()

SAFE_FILENAME_RE = re.compile(r"[^A-Za-zА-Яа-яЁё0-9_.() -]+")
INCLUDE_RE = re.compile(
    rb"(?im)^\s*#\s*(?:try)?include\s*[<\"]\s*([^>\"]+?)\s*[>\"]"
)

API_BASE = ""
FILE_BASE = ""
UPDATE_OFFSET = 0

COMPILER_PATH: Path | None = None
INCLUDE_DIR: Path | None = None
LIB_DIR: Path | None = None
INSTALL_ERROR = ""


# ---------------------------------------------------------------------------
# СЛУЖЕБНЫЕ ФУНКЦИИ
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def safe_filename(filename: str, fallback: str = "gamemode.pwn") -> str:
    name = Path(filename).name[:120]
    name = SAFE_FILENAME_RE.sub("_", name).strip(" .")
    return name or fallback


def allowed(user_id: int | None) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id is not None and user_id in ALLOWED_USER_IDS


def human_size(size: int) -> str:
    units = ("Б", "КБ", "МБ", "ГБ")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} {unit}"
        value /= 1024
    return f"{size} Б"


def request_json(
    url: str,
    *,
    data: dict[str, Any] | None = None,
    timeout: int = DOWNLOAD_TIMEOUT,
) -> dict[str, Any]:
    headers = {
        "User-Agent": "PawnCompilerTelegramBot/1.0",
        "Accept": "application/json",
    }

    encoded = None
    if data is not None:
        encoded = urllib.parse.urlencode(
            {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
                for key, value in data.items()
                if value is not None
            }
        ).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=encoded, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()

    result = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(result, dict):
        raise RuntimeError("Сервер вернул неожиданный JSON")
    return result


def tg_api(method: str, data: dict[str, Any] | None = None) -> Any:
    result = request_json(
        f"{API_BASE}/{method}",
        data=data or {},
        timeout=max(POLL_TIMEOUT + 10, DOWNLOAD_TIMEOUT),
    )
    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API {method}: {result.get('description', 'unknown error')}"
        )
    return result.get("result")


def send_message(
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    if reply_to_message_id:
        data["reply_parameters"] = {
            "message_id": reply_to_message_id,
            "allow_sending_without_reply": True,
        }
    if parse_mode:
        data["parse_mode"] = parse_mode
    return tg_api("sendMessage", data)


def edit_message(chat_id: int, message_id: int, text: str) -> None:
    try:
        tg_api(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text[:4096],
            },
        )
    except Exception:
        logging.exception("Не удалось изменить сообщение")


def delete_message(chat_id: int, message_id: int) -> None:
    try:
        tg_api(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
        )
    except Exception:
        pass


def multipart_body(
    fields: dict[str, Any],
    file_field: str,
    file_path: Path,
    sent_filename: str,
) -> tuple[bytes, str]:
    boundary = f"----PawnBot{uuid.uuid4().hex}"
    buffer = io.BytesIO()

    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        buffer.write(f"--{boundary}\r\n".encode())
        buffer.write(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        buffer.write(str(value).encode("utf-8"))
        buffer.write(b"\r\n")

    content_type = (
        mimetypes.guess_type(sent_filename)[0] or "application/octet-stream"
    )
    buffer.write(f"--{boundary}\r\n".encode())
    buffer.write(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{sent_filename}"\r\n'
        ).encode("utf-8")
    )
    buffer.write(f"Content-Type: {content_type}\r\n\r\n".encode())
    with file_path.open("rb") as source:
        shutil.copyfileobj(source, buffer)
    buffer.write(b"\r\n")
    buffer.write(f"--{boundary}--\r\n".encode())

    return buffer.getvalue(), boundary


def send_document(
    chat_id: int,
    file_path: Path,
    sent_filename: str,
    *,
    caption: str = "",
    reply_to_message_id: int | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "chat_id": chat_id,
        "caption": caption[:1024],
    }
    if reply_to_message_id:
        fields["reply_parameters"] = {
            "message_id": reply_to_message_id,
            "allow_sending_without_reply": True,
        }

    body, boundary = multipart_body(
        fields,
        "document",
        file_path,
        sent_filename,
    )

    request = urllib.request.Request(
        f"{API_BASE}/sendDocument",
        data=body,
        headers={
            "User-Agent": "PawnCompilerTelegramBot/1.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        result = json.loads(response.read().decode("utf-8", errors="replace"))

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API sendDocument: "
            f"{result.get('description', 'unknown error')}"
        )
    return result["result"]


def download_url(url: str, destination: Path, max_size: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "PawnCompilerTelegramBot/1.0"},
    )

    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        length_header = response.headers.get("Content-Length")
        if length_header and int(length_header) > max_size:
            raise RuntimeError(
                f"Скачиваемый файл слишком большой: {length_header} байт"
            )

        total = 0
        with destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    raise RuntimeError(
                        f"Превышен предел загрузки {human_size(max_size)}"
                    )
                output.write(chunk)


def download_telegram_file(file_id: str, destination: Path) -> None:
    info = tg_api("getFile", {"file_id": file_id})
    file_path = info.get("file_path")
    file_size = int(info.get("file_size") or 0)

    if not file_path:
        raise RuntimeError("Telegram не вернул путь к файлу")
    if file_size and file_size > MAX_PWN_SIZE:
        raise RuntimeError(
            f"Файл слишком большой: {human_size(file_size)}. "
            f"Лимит: {human_size(MAX_PWN_SIZE)}."
        )

    download_url(
        f"{FILE_BASE}/{file_path}",
        destination,
        MAX_PWN_SIZE,
    )


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()

    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if destination_root not in target.parents and target != destination_root:
                raise RuntimeError("Небезопасный путь внутри tar-архива")
        tar.extractall(destination)


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()

    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if destination_root not in target.parents and target != destination_root:
                raise RuntimeError("Небезопасный путь внутри zip-архива")
        zf.extractall(destination)


def copy_includes(source_root: Path, target_include: Path) -> int:
    files = list(source_root.rglob("*.inc"))
    if not files:
        return 0

    common_root = source_root
    children = [path for path in source_root.iterdir()] if source_root.exists() else []
    directories = [path for path in children if path.is_dir()]
    direct_includes = list(source_root.glob("*.inc"))

    # GitHub-архивы обычно имеют одну корневую папку.
    if not direct_includes and len(directories) == 1:
        common_root = directories[0]

    count = 0
    for source in common_root.rglob("*.inc"):
        relative = source.relative_to(common_root)
        destination = target_include / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        count += 1
    return count


def find_file(root: Path, name: str) -> Path | None:
    matches = [path for path in root.rglob(name) if path.is_file()]
    return matches[0] if matches else None


def install_toolchain(force: bool = False) -> tuple[Path, Path, Path]:
    global COMPILER_PATH, INCLUDE_DIR, LIB_DIR, INSTALL_ERROR

    compiler_marker = TOOLCHAIN_DIR / ".installed"
    compiler = find_file(TOOLCHAIN_DIR, "pawncc") if TOOLCHAIN_DIR.exists() else None
    include_dir = TOOLCHAIN_DIR / "include"
    library = find_file(TOOLCHAIN_DIR, "libpawnc.so") if TOOLCHAIN_DIR.exists() else None

    if (
        not force
        and compiler_marker.exists()
        and compiler
        and include_dir.is_dir()
        and library
    ):
        compiler.chmod(compiler.stat().st_mode | 0o111)
        COMPILER_PATH = compiler
        INCLUDE_DIR = include_dir
        LIB_DIR = library.parent
        INSTALL_ERROR = ""
        return compiler, include_dir, library.parent

    logging.info("Установка Pawn toolchain...")
    INSTALL_ERROR = ""

    temp_parent = TOOLCHAIN_DIR.parent
    temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="pawn_install_",
        dir=str(temp_parent),
    ) as temp_name:
        temp = Path(temp_name)
        new_toolchain = temp / "toolchain"
        new_toolchain.mkdir()

        compiler_archive = temp / "compiler.tar.gz"
        pawn_stdlib_archive = temp / "pawn-stdlib.zip"
        samp_stdlib_archive = temp / "samp-stdlib.zip"

        logging.info("Скачивание Pawn Compiler %s", COMPILER_VERSION)
        download_url(COMPILER_URL, compiler_archive, 20 * 1024 * 1024)
        safe_extract_tar(compiler_archive, new_toolchain)

        compiler = find_file(new_toolchain, "pawncc")
        library = find_file(new_toolchain, "libpawnc.so")
        if not compiler or not library:
            raise RuntimeError(
                "В архиве Pawn Compiler не найдены pawncc и libpawnc.so"
            )
        compiler.chmod(compiler.stat().st_mode | 0o111)

        include_dir = new_toolchain / "include"
        include_dir.mkdir(parents=True, exist_ok=True)

        # Берём include, уже находящиеся в архиве компилятора.
        for candidate in list(new_toolchain.rglob("include")):
            if candidate.is_dir() and candidate != include_dir:
                copy_includes(candidate, include_dir)

        logging.info("Скачивание стандартных Pawn includes")
        download_url(PAWN_STDLIB_URL, pawn_stdlib_archive, 20 * 1024 * 1024)
        pawn_extract = temp / "pawn_stdlib"
        safe_extract_zip(pawn_stdlib_archive, pawn_extract)
        pawn_count = copy_includes(pawn_extract, include_dir)

        logging.info("Скачивание стандартных SA-MP includes")
        download_url(SAMP_STDLIB_URL, samp_stdlib_archive, 30 * 1024 * 1024)
        samp_extract = temp / "samp_stdlib"
        safe_extract_zip(samp_stdlib_archive, samp_extract)
        samp_count = copy_includes(samp_extract, include_dir)

        if not (include_dir / "a_samp.inc").exists():
            raise RuntimeError("После установки отсутствует a_samp.inc")

        marker_text = (
            f"compiler={COMPILER_VERSION}\n"
            f"pawn_includes={pawn_count}\n"
            f"samp_includes={samp_count}\n"
            f"installed_at={int(time.time())}\n"
        )
        (new_toolchain / ".installed").write_text(
            marker_text,
            encoding="utf-8",
        )

        if TOOLCHAIN_DIR.exists():
            shutil.rmtree(TOOLCHAIN_DIR)
        shutil.move(str(new_toolchain), str(TOOLCHAIN_DIR))

    compiler = find_file(TOOLCHAIN_DIR, "pawncc")
    library = find_file(TOOLCHAIN_DIR, "libpawnc.so")
    include_dir = TOOLCHAIN_DIR / "include"

    if not compiler or not library or not include_dir.is_dir():
        raise RuntimeError("Toolchain установлен не полностью")

    compiler.chmod(compiler.stat().st_mode | 0o111)
    COMPILER_PATH = compiler
    INCLUDE_DIR = include_dir
    LIB_DIR = library.parent
    INSTALL_ERROR = ""

    logging.info("Pawn toolchain установлен: %s", compiler)
    return compiler, include_dir, library.parent


def ensure_toolchain() -> tuple[Path, Path, Path]:
    global INSTALL_ERROR
    try:
        return install_toolchain(force=False)
    except Exception as error:
        INSTALL_ERROR = f"{type(error).__name__}: {error}"
        logging.exception("Ошибка установки Pawn toolchain")
        raise


def resource_limiter():
    if os.name != "posix":
        return None

    def apply_limits() -> None:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (35, 35))
            resource.setrlimit(
                resource.RLIMIT_AS,
                (384 * 1024 * 1024, 384 * 1024 * 1024),
            )
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (MAX_AMX_SIZE, MAX_AMX_SIZE),
            )
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except Exception:
            pass

    return apply_limits


def scan_includes(source_data: bytes) -> list[str]:
    values: list[str] = []
    for match in INCLUDE_RE.finditer(source_data):
        try:
            value = match.group(1).decode("utf-8", errors="replace").strip()
        except Exception:
            continue
        if value and value not in values:
            values.append(value)
    return values[:50]


def compile_pwn(source: Path, output: Path) -> tuple[int, str]:
    compiler, include_dir, lib_dir = ensure_toolchain()

    command = [
        str(compiler),
        str(source),
        f"-i{include_dir}{os.sep}",
        f"-o{output}",
        *PAWN_FLAGS,
    ]

    environment = os.environ.copy()
    old_ld_path = environment.get("LD_LIBRARY_PATH", "")
    environment["LD_LIBRARY_PATH"] = (
        str(lib_dir)
        if not old_ld_path
        else f"{lib_dir}{os.pathsep}{old_ld_path}"
    )

    kwargs: dict[str, Any] = {
        "cwd": str(source.parent),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": False,
        "timeout": COMPILE_TIMEOUT,
        "env": environment,
    }

    limiter = resource_limiter()
    if limiter:
        kwargs["preexec_fn"] = limiter

    completed = subprocess.run(command, **kwargs)
    log = completed.stdout.decode("utf-8", errors="replace").strip()
    return completed.returncode, log


def locate_amx(workdir: Path, expected: Path) -> Path | None:
    if expected.is_file():
        return expected

    candidates = sorted(
        workdir.rglob("*.amx"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def send_compile_log(
    chat_id: int,
    reply_to: int,
    title: str,
    log: str,
) -> None:
    log = log.strip() or "Компилятор не вернул текст ошибки."
    text = f"{title}\n\n{log}"

    if len(text) <= 4000:
        send_message(
            chat_id,
            text,
            reply_to_message_id=reply_to,
        )
        return

    with tempfile.TemporaryDirectory(prefix="pawn_log_") as temp_name:
        log_path = Path(temp_name) / "compile.log"
        log_path.write_text(text, encoding="utf-8")
        send_document(
            chat_id,
            log_path,
            "compile.log",
            caption=title,
            reply_to_message_id=reply_to,
        )


# ---------------------------------------------------------------------------
# ОБРАБОТКА TELEGRAM
# ---------------------------------------------------------------------------

def command_name(text: str) -> str:
    first = text.strip().split(maxsplit=1)[0].lower()
    return first.split("@", maxsplit=1)[0]


def handle_command(message: dict[str, Any], command: str) -> None:
    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])
    user = message.get("from") or {}
    user_id = user.get("id")

    if not allowed(user_id):
        send_message(
            chat_id,
            "У вас нет доступа к этому боту.",
            reply_to_message_id=message_id,
        )
        return

    if command in ("/start", "/help"):
        send_message(
            chat_id,
            "Отправь gamemode с расширением .pwn как документ.\n\n"
            "Бот сам установит Pawn Compiler и стандартные include, "
            "скомпилирует весь мод и пришлёт готовый .amx.\n\n"
            "Команды:\n"
            "/status — состояние компилятора\n"
            "/reinstall — переустановить компилятор и include\n\n"
            "Если появится ошибка «cannot read from file», значит в моде "
            "используется сторонний include, которого нет в одном .pwn.",
            reply_to_message_id=message_id,
        )
        return

    if command == "/status":
        compiler_text = str(COMPILER_PATH) if COMPILER_PATH else "не установлен"
        include_count = (
            len(list(INCLUDE_DIR.rglob("*.inc")))
            if INCLUDE_DIR and INCLUDE_DIR.exists()
            else 0
        )
        send_message(
            chat_id,
            "Статус Pawn-компилятора\n\n"
            f"Версия: {COMPILER_VERSION}\n"
            f"pawncc: {compiler_text}\n"
            f"Include-файлов: {include_count}\n"
            f"Лимит PWN: {human_size(MAX_PWN_SIZE)}\n"
            f"Таймаут: {COMPILE_TIMEOUT} сек.\n"
            f"Последняя ошибка установки: {INSTALL_ERROR or 'нет'}",
            reply_to_message_id=message_id,
        )
        return

    if command == "/reinstall":
        status = send_message(
            chat_id,
            "Переустанавливаю Pawn Compiler и стандартные include…",
            reply_to_message_id=message_id,
        )
        try:
            install_toolchain(force=True)
            edit_message(
                chat_id,
                int(status["message_id"]),
                "Готово. Pawn Compiler и стандартные include переустановлены.",
            )
        except Exception as error:
            edit_message(
                chat_id,
                int(status["message_id"]),
                f"Ошибка переустановки:\n{type(error).__name__}: {error}",
            )
        return

    send_message(
        chat_id,
        "Неизвестная команда. Отправь /start.",
        reply_to_message_id=message_id,
    )


def handle_document(message: dict[str, Any]) -> None:
    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])
    user = message.get("from") or {}
    user_id = user.get("id")

    if not allowed(user_id):
        send_message(
            chat_id,
            "У вас нет доступа к этому боту.",
            reply_to_message_id=message_id,
        )
        return

    document = message.get("document") or {}
    filename = safe_filename(document.get("file_name") or "gamemode.pwn")
    suffix = Path(filename).suffix.lower()

    if suffix != ".pwn":
        send_message(
            chat_id,
            "Нужен именно файл .pwn, отправленный как документ.",
            reply_to_message_id=message_id,
        )
        return

    declared_size = int(document.get("file_size") or 0)
    if declared_size > MAX_PWN_SIZE:
        send_message(
            chat_id,
            f"Файл слишком большой: {human_size(declared_size)}. "
            f"Лимит: {human_size(MAX_PWN_SIZE)}.",
            reply_to_message_id=message_id,
        )
        return

    status = send_message(
        chat_id,
        "Скачиваю PWN и запускаю компиляцию…",
        reply_to_message_id=message_id,
    )
    status_id = int(status["message_id"])

    with tempfile.TemporaryDirectory(prefix="pawn_job_") as temp_name:
        workdir = Path(temp_name)
        # Внутреннее ASCII-имя исключает проблемы pawncc с кириллицей в пути.
        source = workdir / "gamemode.pwn"
        output_name = f"{Path(filename).stem}.amx"
        output = workdir / output_name

        try:
            download_telegram_file(document["file_id"], source)
            source_data = source.read_bytes()
            includes = scan_includes(source_data)

            edit_message(
                chat_id,
                status_id,
                "Компилирую мод…"
                + (
                    "\nInclude: " + ", ".join(includes[:8])
                    if includes
                    else "\nInclude в исходнике не найдены."
                ),
            )

            return_code, compiler_log = compile_pwn(source, output)
            amx = locate_amx(workdir, output)

            if return_code != 0 or amx is None:
                edit_message(chat_id, status_id, "Компиляция завершилась с ошибкой.")
                send_compile_log(
                    chat_id,
                    message_id,
                    f"Ошибка компиляции {filename} (код {return_code})",
                    compiler_log,
                )
                return

            amx_size = amx.stat().st_size
            if amx_size > MAX_AMX_SIZE:
                edit_message(
                    chat_id,
                    status_id,
                    f"AMX получился слишком большим: {human_size(amx_size)}.",
                )
                return

            caption = (
                f"Готово: {output_name}\n"
                f"Размер: {human_size(amx_size)}"
            )
            if compiler_log:
                warning_lines = [
                    line for line in compiler_log.splitlines()
                    if "warning" in line.lower()
                ]
                if warning_lines:
                    caption += f"\nПредупреждений: {len(warning_lines)}"

            send_document(
                chat_id,
                amx,
                output_name,
                caption=caption,
                reply_to_message_id=message_id,
            )
            delete_message(chat_id, status_id)

            if compiler_log and "warning" in compiler_log.lower():
                send_compile_log(
                    chat_id,
                    message_id,
                    "AMX создан, но компилятор выдал предупреждения",
                    compiler_log,
                )

        except subprocess.TimeoutExpired:
            edit_message(
                chat_id,
                status_id,
                f"Компиляция остановлена: превышен таймаут "
                f"{COMPILE_TIMEOUT} секунд.",
            )
        except Exception as error:
            logging.exception("Ошибка обработки PWN")
            edit_message(
                chat_id,
                status_id,
                f"Ошибка:\n{type(error).__name__}: {error}",
            )


def handle_message(message: dict[str, Any]) -> None:
    text = message.get("text")
    if isinstance(text, str) and text.startswith("/"):
        handle_command(message, command_name(text))
        return

    if message.get("document"):
        handle_document(message)
        return

    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])
    user_id = (message.get("from") or {}).get("id")

    if allowed(user_id):
        send_message(
            chat_id,
            "Отправь файл .pwn как документ.",
            reply_to_message_id=message_id,
        )


def poll_forever() -> None:
    global UPDATE_OFFSET

    logging.info("Бот запущен. Long polling активен.")

    while True:
        try:
            updates = tg_api(
                "getUpdates",
                {
                    "offset": UPDATE_OFFSET,
                    "timeout": POLL_TIMEOUT,
                    "allowed_updates": ["message"],
                },
            )

            for update in updates:
                UPDATE_OFFSET = max(
                    UPDATE_OFFSET,
                    int(update["update_id"]) + 1,
                )
                message = update.get("message")
                if not message:
                    continue

                try:
                    handle_message(message)
                except Exception:
                    logging.exception("Ошибка обработки Telegram update")

        except urllib.error.HTTPError as error:
            logging.error("HTTP error Telegram: %s", error)
            time.sleep(5)
        except urllib.error.URLError as error:
            logging.error("Network error Telegram: %s", error)
            time.sleep(5)
        except KeyboardInterrupt:
            raise
        except Exception:
            logging.error("Ошибка polling:\n%s", traceback.format_exc())
            time.sleep(5)


def main() -> None:
    global API_BASE, FILE_BASE

    setup_logging()

    if sys.version_info < (3, 10):
        raise RuntimeError("Нужен Python 3.10 или новее")

    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        raise RuntimeError(
            "Не найден токен Telegram-бота. "
            "Задай BOT_TOKEN или вставь токен в TOKEN_FALLBACK."
        )

    API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
    FILE_BASE = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

    me = tg_api("getMe")
    logging.info(
        "Telegram-бот: @%s",
        me.get("username", "unknown"),
    )

    try:
        ensure_toolchain()
    except Exception:
        # Бот всё равно запускается: установку можно повторить через /reinstall.
        logging.error(
            "Toolchain не установлен при старте. "
            "Бот запущен для показа ошибки через /status и /reinstall."
        )

    poll_forever()


if __name__ == "__main__":
    main()
