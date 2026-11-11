#!/usr/bin/env python3
"""SUDO_ASKPASS-хелпер (CLI-фолбэк): системный диалог и пароль в stdout.

Приложение (GUI) пароль спрашивает Qt-модалкой и передаёт его sudo через
stdin (см. core/sudo_helper.py); этот хелпер остаётся для CLI-инструментов и
любых вызовов без провайдера. Запускается sudo как $SUDO_ASKPASS. Порядок
диалогов: zenity → kdialog → systemd-ask-password → ввод с tty.

Запоминание пароля: после ввода пользователю предлагается галочка-вопрос
«Запомнить пароль». При согласии пароль сохраняется в
~/.cache/zapret_dpi_manager/sudo.cred (0600, владелец — пользователь), и
следующие вызовы берут его оттуда без диалога. Файл удаляется кнопкой
«Забыть пароль sudo» в настройках менеджера или автоматически при неверном
пароле (см. core/sudo_helper.py).
"""

from __future__ import annotations

import getpass
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

TITLE = "Zapret DPI Manager"
PROMPT = "Введите пароль администратора (sudo)"


def user_home() -> Path:
    """Домашний каталог реального пользователя (хелпер работает от root)."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path(os.environ.get("HOME", "/root"))


CACHE_DIR = user_home() / ".cache" / "zapret_dpi_manager"
CACHE_FILE = CACHE_DIR / "sudo.cred"


def read_cached_password() -> str | None:
    try:
        password = CACHE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return password or None


def save_cached_password(password: str) -> None:
    """Сохраняет пароль от имени пользователя (0600)."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(password + "\n", encoding="utf-8")
        os.chmod(CACHE_FILE, 0o600)
        os.chmod(CACHE_DIR, 0o700)
        uid = os.environ.get("SUDO_UID")
        gid = os.environ.get("SUDO_GID")
        if uid and gid:
            os.chown(CACHE_FILE, int(uid), int(gid))
            os.chown(CACHE_DIR, int(uid), int(gid))
    except (OSError, ValueError):
        pass


def _run(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    password = proc.stdout.rstrip("\n")
    return password or None


def ask_password() -> str | None:
    if shutil.which("zenity"):
        password = _run(["zenity", "--password", f"--title={TITLE}", f"--text={PROMPT}"])
        if password:
            return password
    if shutil.which("kdialog"):
        password = _run(["kdialog", "--password", PROMPT, "--title", TITLE])
        if password:
            return password
    if shutil.which("systemd-ask-password"):
        password = _run(["systemd-ask-password", "--icon=utilities-terminal", PROMPT])
        if password:
            return password
    try:
        return getpass.getpass(f"{PROMPT}: ") or None
    except Exception:
        return None


def ask_remember() -> bool:
    """Галочка «Запомнить пароль» (zenity/kdialog не умеют чекбокс — задаём вопрос)."""
    question = "Запомнить пароль sudo для следующих запусков?"
    if shutil.which("zenity"):
        try:
            proc = subprocess.run(
                [
                    "zenity", "--question", f"--title={TITLE}", f"--text={question}",
                    "--ok-label=Запомнить", "--cancel-label=Только сейчас",
                ],
                capture_output=True, text=True, timeout=300,
            )
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    if shutil.which("kdialog"):
        try:
            proc = subprocess.run(
                ["kdialog", "--yesno", question, "--title", TITLE],
                capture_output=True, text=True, timeout=300,
            )
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return False


def main() -> int:
    cached = read_cached_password()
    if cached:
        sys.stdout.write(cached + "\n")
        return 0

    password = ask_password()
    if not password:
        print("Ввод пароля отменён", file=sys.stderr)
        return 1

    if ask_remember():
        save_cached_password(password)

    sys.stdout.write(password + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
