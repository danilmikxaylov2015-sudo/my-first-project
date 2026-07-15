#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram-бот-компилятор SA-MP/Open.MP:
принимает .pwn и возвращает .amx.

Pawn Compiler и стандартные include устанавливаются Dockerfile во время деплоя.
Сторонние include (YSI, streamer, sscanf2, a_mysql и т. п.) в комплект не входят.
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
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


TOKEN = (
    os.getenv("BOT_TOKEN", "8975361055:AAET6brDJIAonm58z-2CNCHG-1WEMuC0Rmc").strip()
    or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    or os.getenv("TOKEN", "").strip()
)

PAWNCC = Path(os.getenv("PAWN_COMPILER", "/usr/local/bin/pawncc"))
INCLUDE_DIR = Path(os.getenv("PAWN_INCLUDE_DIR", "/opt/pawn/include"))
PAWN_FLAGS = os.getenv("PAWN_FLAGS", "-d3").split()

MAX_PWN_SIZE = int(os.getenv("MAX_PWN_SIZE", str(10 * 1024 * 1024)))
MAX_AMX_SIZE = int(os.getenv("MAX_AMX_SIZE", str(48 * 1024 * 1024)))
COMPILE_TIMEOUT = int(os.getenv("COMPILE_TIMEOUT", "60"))
POLL_TIMEOUT = int(os.getenv("POLL_TIMEOUT", "30"))

ALLOWED_USER_IDS = {
    int(value.strip())
    for value in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if value.strip().isdigit()
}

SAFE_NAME_RE = re.compile(r"[^A-Za-zА-Яа-яЁё0-9_.() -]+")
INCLUDE_RE = re.compile(
    rb'(?im)^\s*#\s*(?:try)?include\s*[<"]\s*([^>"]+?)\s*[>"]'
)

API_BASE = ""
FILE_BASE = ""
UPDATE_OFFSET = 0


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def allowed(user_id: int | None) -> bool:
    return not ALLOWED_USER_IDS or (
        user_id is not None and user_id in ALLOWED_USER_IDS
    )


def safe_filename(name: str, fallback: str = "gamemode.pwn") -> str:
    result = SAFE_NAME_RE.sub("_", Path(name).name[:120]).strip(" .")
    return result or fallback


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{int(value)} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} Б"


def request_json(
    url: str,
    data: dict[str, Any] | None = None,
    timeout: int = 90,
) -> dict[str, Any]:
    headers = {
        "User-Agent": "BothostPawnCompilerBot/2.0",
        "Accept": "application/json",
    }
    body = None

    if data is not None:
        body = urllib.parse.urlencode(
            {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
                for key, value in data.items()
                if value is not None
            }
        ).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8", errors="replace"))

    if not isinstance(result, dict):
        raise RuntimeError("Telegram вернул некорректный JSON")
    return result


def tg_api(method: str, data: dict[str, Any] | None = None) -> Any:
    response = request_json(
        f"{API_BASE}/{method}",
        data or {},
        timeout=max(90, POLL_TIMEOUT + 15),
    )
    if not response.get("ok"):
        raise RuntimeError(
            f"Telegram API {method}: "
            f"{response.get('description', 'неизвестная ошибка')}"
        )
    return response.get("result")


def send_message(
    chat_id: int,
    text: str,
    *,
    reply_to: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    if reply_to:
        payload["reply_parameters"] = {
            "message_id": reply_to,
            "allow_sending_without_reply": True,
        }
    return tg_api("sendMessage", payload)


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
    field_name: str,
    file_path: Path,
    sent_name: str,
) -> tuple[bytes, str]:
    boundary = f"----PawnBot{uuid.uuid4().hex}"
    output = io.BytesIO()

    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)

        output.write(f"--{boundary}\r\n".encode())
        output.write(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        output.write(str(value).encode("utf-8"))
        output.write(b"\r\n")

    mime = mimetypes.guess_type(sent_name)[0] or "application/octet-stream"
    output.write(f"--{boundary}\r\n".encode())
    output.write(
        (
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{sent_name}"\r\n'
        ).encode("utf-8")
    )
    output.write(f"Content-Type: {mime}\r\n\r\n".encode())

    with file_path.open("rb") as source:
        shutil.copyfileobj(source, output)

    output.write(b"\r\n")
    output.write(f"--{boundary}--\r\n".encode())
    return output.getvalue(), boundary


def send_document(
    chat_id: int,
    file_path: Path,
    sent_name: str,
    *,
    caption: str = "",
    reply_to: int | None = None,
) -> None:
    fields: dict[str, Any] = {
        "chat_id": chat_id,
        "caption": caption[:1024],
    }
    if reply_to:
        fields["reply_parameters"] = {
            "message_id": reply_to,
            "allow_sending_without_reply": True,
        }

    body, boundary = multipart_body(
        fields,
        "document",
        file_path,
        sent_name,
    )

    request = urllib.request.Request(
        f"{API_BASE}/sendDocument",
        data=body,
        headers={
            "User-Agent": "BothostPawnCompilerBot/2.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )

    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8", errors="replace"))

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram sendDocument: "
            f"{result.get('description', 'неизвестная ошибка')}"
        )


def download_telegram_file(file_id: str, destination: Path) -> None:
    info = tg_api("getFile", {"file_id": file_id})
    remote_path = info.get("file_path")
    size = int(info.get("file_size") or 0)

    if not remote_path:
        raise RuntimeError("Telegram не вернул путь к файлу")
    if size and size > MAX_PWN_SIZE:
        raise RuntimeError(
            f"Файл весит {human_size(size)}, лимит — {human_size(MAX_PWN_SIZE)}"
        )

    request = urllib.request.Request(
        f"{FILE_BASE}/{remote_path}",
        headers={"User-Agent": "BothostPawnCompilerBot/2.0"},
    )

    total = 0
    with urllib.request.urlopen(request, timeout=120) as response:
        with destination.open("wb") as output:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PWN_SIZE:
                    raise RuntimeError("Превышен лимит размера PWN")
                output.write(chunk)


def compiler_diagnostics() -> tuple[bool, str]:
    if not PAWNCC.exists():
        return False, f"Файл компилятора не найден: {PAWNCC}"
    if not os.access(PAWNCC, os.X_OK):
        return False, f"Нет права на запуск компилятора: {PAWNCC}"
    if not INCLUDE_DIR.is_dir():
        return False, f"Папка include не найдена: {INCLUDE_DIR}"
    if not (INCLUDE_DIR / "a_samp.inc").exists():
        return False, f"Не найден {INCLUDE_DIR / 'a_samp.inc'}"

    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = (
        "/usr/local/lib"
        + (
            os.pathsep + environment["LD_LIBRARY_PATH"]
            if environment.get("LD_LIBRARY_PATH")
            else ""
        )
    )

    try:
        result = subprocess.run(
            [str(PAWNCC)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            env=environment,
            check=False,
        )
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"

    text = result.stdout.decode("utf-8", errors="replace").strip()
    first_line = text.splitlines()[0] if text else "pawncc запускается"
    return True, first_line


def scan_includes(data: bytes) -> list[str]:
    result: list[str] = []
    for match in INCLUDE_RE.finditer(data):
        name = match.group(1).decode("utf-8", errors="replace").strip()
        if name and name not in result:
            result.append(name)
    return result[:50]


def compile_pwn(source: Path, output: Path) -> tuple[int, str]:
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = (
        "/usr/local/lib"
        + (
            os.pathsep + environment["LD_LIBRARY_PATH"]
            if environment.get("LD_LIBRARY_PATH")
            else ""
        )
    )

    command = [
        str(PAWNCC),
        str(source),
        f"-i{source.parent}{os.sep}",
        f"-i{INCLUDE_DIR}{os.sep}",
        f"-o{output}",
        *PAWN_FLAGS,
    ]

    kwargs: dict[str, Any] = {
        "cwd": str(source.parent),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "timeout": COMPILE_TIMEOUT,
        "env": environment,
        "check": False,
    }

    if os.name == "posix":
        def apply_limits() -> None:
            try:
                import resource
                resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
                resource.setrlimit(
                    resource.RLIMIT_FSIZE,
                    (MAX_AMX_SIZE, MAX_AMX_SIZE),
                )
                resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            except Exception:
                pass

        kwargs["preexec_fn"] = apply_limits

    result = subprocess.run(command, **kwargs)
    log = result.stdout.decode("utf-8", errors="replace").strip()
    return result.returncode, log


def send_log(
    chat_id: int,
    reply_to: int,
    title: str,
    log: str,
) -> None:
    full_text = f"{title}\n\n{log.strip() or 'Компилятор не вернул лог.'}"

    if len(full_text) <= 4000:
        send_message(chat_id, full_text, reply_to=reply_to)
        return

    with tempfile.TemporaryDirectory(prefix="pawn_log_") as temp:
        log_file = Path(temp) / "compile.log"
        log_file.write_text(full_text, encoding="utf-8")
        send_document(
            chat_id,
            log_file,
            "compile.log",
            caption=title,
            reply_to=reply_to,
        )


def command_name(text: str) -> str:
    first = text.strip().split(maxsplit=1)[0].lower()
    return first.split("@", maxsplit=1)[0]


def handle_command(message: dict[str, Any], command: str) -> None:
    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])
    user_id = (message.get("from") or {}).get("id")

    if not allowed(user_id):
        send_message(chat_id, "У вас нет доступа к боту.", reply_to=message_id)
        return

    if command in {"/start", "/help"}:
        send_message(
            chat_id,
            "Отправь SA-MP/Open.MP мод с расширением .pwn как документ.\n\n"
            "Бот скомпилирует его и пришлёт готовый .amx.\n\n"
            "/status — проверить компилятор\n\n"
            "Стандартные include уже установлены. Если мод использует YSI, "
            "streamer, sscanf2, a_mysql или другие сторонние include, "
            "их нужно отдельно добавить в Dockerfile.",
            reply_to=message_id,
        )
        return

    if command == "/status":
        ok, details = compiler_diagnostics()
        include_count = (
            len(list(INCLUDE_DIR.rglob("*.inc")))
            if INCLUDE_DIR.is_dir()
            else 0
        )
        send_message(
            chat_id,
            ("Компилятор работает." if ok else "Компилятор НЕ работает.")
            + f"\n\n{details}"
            + f"\nInclude-файлов: {include_count}"
            + f"\nПуть: {PAWNCC}",
            reply_to=message_id,
        )
        return

    send_message(chat_id, "Неизвестная команда.", reply_to=message_id)


def handle_document(message: dict[str, Any]) -> None:
    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])
    user_id = (message.get("from") or {}).get("id")

    if not allowed(user_id):
        send_message(chat_id, "У вас нет доступа к боту.", reply_to=message_id)
        return

    document = message.get("document") or {}
    original_name = safe_filename(
        document.get("file_name") or "gamemode.pwn"
    )

    if Path(original_name).suffix.lower() != ".pwn":
        send_message(
            chat_id,
            "Нужен файл с расширением .pwn.",
            reply_to=message_id,
        )
        return

    declared_size = int(document.get("file_size") or 0)
    if declared_size > MAX_PWN_SIZE:
        send_message(
            chat_id,
            f"Файл слишком большой: {human_size(declared_size)}.",
            reply_to=message_id,
        )
        return

    compiler_ok, compiler_info = compiler_diagnostics()
    if not compiler_ok:
        send_message(
            chat_id,
            "Компилятор не запустился:\n\n"
            f"{compiler_info}\n\n"
            "Проверь, что проект развёрнут именно через новый Dockerfile.",
            reply_to=message_id,
        )
        return

    status = send_message(
        chat_id,
        "Скачиваю PWN…",
        reply_to=message_id,
    )
    status_id = int(status["message_id"])

    with tempfile.TemporaryDirectory(prefix="pawn_compile_") as temp:
        workdir = Path(temp)

        # Внутреннее ASCII-имя исключает проблемы pawncc с кириллицей.
        source = workdir / "gamemode.pwn"
        output_name = f"{Path(original_name).stem}.amx"
        output = workdir / "gamemode.amx"

        try:
            download_telegram_file(document["file_id"], source)
            source_data = source.read_bytes()
            includes = scan_includes(source_data)

            text = "Компилирую мод…"
            if includes:
                text += "\nInclude: " + ", ".join(includes[:10])
            edit_message(chat_id, status_id, text)

            return_code, log = compile_pwn(source, output)

            if return_code != 0 or not output.is_file():
                edit_message(
                    chat_id,
                    status_id,
                    "Компиляция завершилась с ошибкой.",
                )
                send_log(
                    chat_id,
                    message_id,
                    f"Ошибка компиляции {original_name}, код {return_code}",
                    log,
                )
                return

            if output.stat().st_size > MAX_AMX_SIZE:
                edit_message(
                    chat_id,
                    status_id,
                    "Полученный AMX превышает допустимый размер.",
                )
                return

            warning_count = sum(
                "warning" in line.lower()
                for line in log.splitlines()
            )
            caption = (
                f"Готово: {output_name}\n"
                f"Размер: {human_size(output.stat().st_size)}"
            )
            if warning_count:
                caption += f"\nПредупреждений: {warning_count}"

            send_document(
                chat_id,
                output,
                output_name,
                caption=caption,
                reply_to=message_id,
            )
            delete_message(chat_id, status_id)

            if warning_count:
                send_log(
                    chat_id,
                    message_id,
                    "AMX создан, но есть предупреждения",
                    log,
                )

        except subprocess.TimeoutExpired:
            edit_message(
                chat_id,
                status_id,
                f"Компиляция дольше {COMPILE_TIMEOUT} секунд и остановлена.",
            )
        except Exception as error:
            logging.exception("Ошибка компиляции")
            edit_message(
                chat_id,
                status_id,
                f"Ошибка:\n{type(error).__name__}: {error}",
            )


def handle_message(message: dict[str, Any]) -> None:
    text = message.get("text")
    if isinstance(text, str) and text.startswith("/"):
        handle_command(message, command_name(text))
    elif message.get("document"):
        handle_document(message)
    else:
        chat_id = int(message["chat"]["id"])
        message_id = int(message["message_id"])
        user_id = (message.get("from") or {}).get("id")
        if allowed(user_id):
            send_message(
                chat_id,
                "Отправь файл .pwn как документ.",
                reply_to=message_id,
            )


def poll_forever() -> None:
    global UPDATE_OFFSET

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
                if message:
                    try:
                        handle_message(message)
                    except Exception:
                        logging.exception("Ошибка обработки сообщения")

        except KeyboardInterrupt:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError) as error:
            logging.error("Ошибка Telegram-соединения: %s", error)
            time.sleep(5)
        except Exception:
            logging.error("Ошибка polling:\n%s", traceback.format_exc())
            time.sleep(5)


def main() -> None:
    global API_BASE, FILE_BASE

    setup_logging()

    if sys.version_info < (3, 10):
        raise RuntimeError("Нужен Python 3.10 или новее")
    if not TOKEN or ":" not in TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN")

    API_BASE = f"https://api.telegram.org/bot{TOKEN}"
    FILE_BASE = f"https://api.telegram.org/file/bot{TOKEN}"

    me = tg_api("getMe")
    logging.info("Запущен бот @%s", me.get("username", "unknown"))

    ok, details = compiler_diagnostics()
    if ok:
        logging.info("Pawn Compiler: %s", details)
    else:
        logging.error("Pawn Compiler не готов: %s", details)

    poll_forever()


if __name__ == "__main__":
    main()
