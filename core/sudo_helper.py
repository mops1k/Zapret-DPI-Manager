"""Единая обёртка запуска команд через sudo.

В приложении (Qt) пароль спрашивает провайдер — Qt-модалка. Пароль живёт
только на время одного привилегированного действия и передаётся sudo через
stdin (``sudo -S``), в argv/env не попадает. Без провайдера (CLI, установщик,
``askpass.py``) работает прежний системный askpass: ``sudo -A``.

Политика запоминания:
- «Запомнить» (галочка в модалке) — пароль в ``~/.cache/zapret_dpi_manager/
  sudo.cred`` (0600); следующие действия берут его из файла без вопросов.
- Без галочки — пароль держится только на одно действие; после него
  ``clear_session_password()`` (его вызывает мост) заставляет спросить снова.
  Системный sudo-timestamp при этом не решает за приложение: без файла пароль
  запрашивается, даже если sudo недавно разблокирован.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

ASKPASS_PATH = Path(__file__).resolve().with_name("askpass.py")

#: Таймаут служебных проб (``sudo -n true``) и проверки пароля.
PROBE_TIMEOUT = 10.0
CHECK_TIMEOUT = 30.0

_provider: Optional[Callable[[], Optional[str]]] = None
_session_password: Optional[str] = None
_password_source: Optional[str] = None  # "cache" | "prompt"
_passwordless: Optional[bool] = None
_lock = threading.RLock()


# --- провайдер пароля из GUI ------------------------------------------------


def set_password_provider(provider: Optional[Callable[[], Optional[str]]]) -> None:
    """Подключает источник пароля (Qt-модалку). ``None`` — вернуть askpass."""
    global _provider, _session_password, _password_source, _passwordless
    with _lock:
        _provider = provider
        _session_password = None
        _password_source = None
        _passwordless = None


def clear_password_provider() -> None:
    set_password_provider(None)


def clear_session_password() -> None:
    """Забывает пароль текущего действия (файл «Запомнить» не трогает)."""
    global _session_password, _password_source
    with _lock:
        _session_password = None
        _password_source = None


def has_session_password() -> bool:
    """Есть ли пароль текущего действия (без файла «Запомнить»)."""
    with _lock:
        return _session_password is not None


# --- файл «Запомнить» -------------------------------------------------------


def read_cached_password() -> Optional[str]:
    """Пароль из файла «Запомнить» (None, если файла нет)."""
    from core.askpass import read_cached_password as _read

    return _read()


def save_cached_password(password: str) -> None:
    """Сохраняет пароль в файл «Запомнить» (0600, владелец — пользователь)."""
    from core.askpass import save_cached_password as _save

    _save(password)


def cache_file() -> Path:
    """Файл запомненного пароля (0600, владелец — пользователь)."""
    from core.askpass import CACHE_FILE

    return CACHE_FILE


def has_cached_password() -> bool:
    return cache_file().is_file()


def forget_cached_password() -> bool:
    """Удаляет запомненный пароль. True — файл был и удалён."""
    try:
        cache_file().unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def forget_saved_password() -> bool:
    """«Забыть пароль sudo»: файл + пароль текущего действия."""
    removed = forget_cached_password()
    clear_session_password()
    return removed


# --- проверка введённого пароля --------------------------------------------

_AUTH_FAIL_MARKERS = (
    "incorrect password",
    "sorry, try again",
    "authentication failure",
    "неверный пароль",
    "попробуйте",
)
_NEEDS_PASSWORD_MARKERS = (
    "a password is required",
    "no tty present",
    "a terminal is required",
    "требуется пароль",
)


def _is_auth_failure(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(marker in low for marker in _AUTH_FAIL_MARKERS)


def _needs_password(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(marker in low for marker in _NEEDS_PASSWORD_MARKERS)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _drop_cache_on_auth_failure(stderr: str) -> None:
    if _is_auth_failure(stderr):
        forget_cached_password()


def check_sudo_password(password: str) -> tuple[bool, str]:
    """Проверяет пароль настоящей аутентификацией sudo.

    ``-k`` обязателен: с живым системным timestamp sudo принял бы любой пароль.
    Возвращает (успех, текст ошибки для модалки).
    """
    if not password:
        return False, "Введите пароль sudo"
    code, _out, err = _sudo(
        ["-k", "-S", "-p", "", "true"], timeout=CHECK_TIMEOUT, input=password + "\n"
    )
    if code == 0:
        return True, ""
    _drop_cache_on_auth_failure(err)
    if _is_auth_failure(err):
        return False, "Неверный пароль sudo"
    return False, _first_line(err) or "Не удалось проверить пароль"


# --- запуск ----------------------------------------------------------------


def sudo_available() -> bool:
    """Есть ли sudo и сам хелпер (без него sudo -A не сможет спросить пароль)."""
    return bool(shutil.which("sudo")) and ASKPASS_PATH.is_file()


def askpass_env() -> dict:
    """Окружение с SUDO_ASKPASS (нужен и sudo, и самому хелперу)."""
    env = os.environ.copy()
    env["SUDO_ASKPASS"] = str(ASKPASS_PATH)
    return env


def _sudo(
    argv,
    timeout: float = 60,
    capture: bool = True,
    input: Optional[str] = None,
):
    """Запускает ``sudo <argv>``; при ``input`` пароль идёт в stdin."""
    cmd = ["sudo", *[str(arg) for arg in argv]]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            input=input,
        )
    except subprocess.TimeoutExpired:
        return 1, "", "Таймаут выполнения команды"
    except OSError as e:
        return 1, "", str(e)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _run_askpass(argv, timeout: float, capture: bool):
    """CLI-фолбэк: ``sudo -A`` с системным askpass (zenity/kdialog)."""
    cmd = ["sudo", "-A", *[str(arg) for arg in argv]]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            env=askpass_env(),
        )
    except subprocess.TimeoutExpired:
        return 1, "", "Таймаут выполнения команды"
    except OSError as e:
        return 1, "", str(e)
    if proc.returncode != 0:
        _drop_cache_on_auth_failure(proc.stderr or "")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _detect_passwordless() -> bool:
    """sudo работает без пароля даже без timestamp (NOPASSWD).

    ``-k`` сбрасывает системный timestamp, ``-n`` запрещает запрос пароля:
    успех означает настоящий passwordless, а не свежий кэш sudo.
    """
    code, _out, _err = _sudo(["-k", "-n", "true"], timeout=PROBE_TIMEOUT)
    return code == 0


def _ensure_password() -> tuple[Optional[str], str]:
    """Пароль для действия: из файла «Запомнить» или из провайдера (модалка)."""
    global _session_password, _password_source, _passwordless
    with _lock:
        if _session_password is not None:
            return _session_password, _password_source or ""
        if _passwordless is None:
            if read_cached_password() is None:
                _passwordless = _detect_passwordless()
            else:
                _passwordless = False
        if _passwordless:
            return None, "passwordless"
        cached = read_cached_password()
        if cached:
            _session_password = cached
            _password_source = "cache"
            return cached, "cache"
        provider = _provider
        if provider is None:
            return None, "askpass"
        password = provider()
        if not password:
            return None, "cancelled"
        _session_password = password
        _password_source = "prompt"
        return password, "prompt"


def _execute(argv, password: str, timeout: float, capture: bool):
    """Команда с паролем: ``sudo -n`` при живом timestamp, иначе ``sudo -S``."""
    code, _out, _err = _sudo(["-n", "true"], timeout=PROBE_TIMEOUT)
    if code == 0:
        code, out, err = _sudo(["-n", *argv], timeout=timeout, capture=capture)
        if code == 0 or not _needs_password(err):
            return code, out, err
        # Timestamp истёк между пробой и командой — повторяем с паролем.
    return _sudo(
        ["-S", "-p", "", *argv],
        timeout=timeout,
        capture=capture,
        input=password + "\n",
    )


def _run_provider(argv, timeout: float, capture: bool):
    retried_cache = False
    while True:
        password, source = _ensure_password()
        if source == "passwordless":
            return _sudo(["-n", *argv], timeout=timeout, capture=capture)
        if not password:
            return 1, "", "Ввод пароля отменён"
        code, out, err = _execute(argv, password, timeout, capture)
        if code == 0:
            return code, out, err
        if not _is_auth_failure(err):
            return code, out, err
        # Пароль не подошёл: сохранённый — удаляем и спрашиваем заново.
        clear_session_password()
        if source != "cache" or retried_cache:
            return code, out, err
        forget_cached_password()
        retried_cache = True


def run_sudo(argv, timeout: float = 60, capture: bool = True):
    """Выполняет команду от root.

    Возвращает (returncode, stdout, stderr); при таймауте/ошибке запуска —
    код 1 и текст причины в stderr. С провайдером пароль спрашивает GUI,
    без провайдера — системный askpass (``sudo -A``).
    """
    if _provider is None:
        return _run_askpass(argv, timeout, capture)
    return _run_provider(argv, timeout, capture)


def popen_sudo(argv, **kwargs):
    """Popen для потоковых задач: ``sudo -A`` с тем же окружением (CLI)."""
    cmd = ["sudo", "-A", *[str(arg) for arg in argv]]
    kwargs.setdefault("env", askpass_env())
    return subprocess.Popen(cmd, **kwargs)


def check_sudo() -> bool:
    """Проверяет, что sudo действительно работает (пароль спрашивает хелпер)."""
    if not sudo_available():
        return False
    code, _, _ = run_sudo(["true"], timeout=300)
    return code == 0
