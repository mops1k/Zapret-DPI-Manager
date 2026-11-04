"""TG WS Proxy: состояние, юнит systemd и управление службой.

Прокси нашего собственного движка (:mod:`tgws`) — это локальный MTProto-прокси
для Telegram, который гоняет трафик через WebSocket. Служба пользовательская
(``systemctl --user``), поэтому пароль sudo не нужен, а движок живёт прямо в
каталоге приложения и обновляется вместе с ним.

Состояние — ``utils/tgws.txt`` (тот же формат key=value, что `utils/warp.txt`).
Юнит — ``~/.config/systemd/user/tgws.service``; журнал — ``journalctl --user``
и файл ``~/.local/state/zapret_dpi_manager/tgws.log``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "tgws"
STATE_NAME = "tgws.txt"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1443

STATE_HEADER = "# TG WS Proxy (Zapret DPI Manager): состояние\n"
STATE_KEYS = (
    "enabled",
    "autostart",
    "host",
    "port",
    "secret",
    "cf_domains",
    "worker_domains",
    "fake_tls",
)
DEFAULT_STATE = {
    "enabled": "0",
    "autostart": "0",
    "host": DEFAULT_HOST,
    "port": str(DEFAULT_PORT),
    "secret": "",
    "cf_domains": "",
    "worker_domains": "",
    "fake_tls": "",
}

SECRET_RE = re.compile(r"^[0-9a-fA-F]{32}$")
HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
DOMAINS_RE = re.compile(r"^[A-Za-z0-9._,:* -]{0,1024}$")
FAKE_TLS_RE = re.compile(r"^[A-Za-z0-9.-]{0,253}$")


# --- Пути -------------------------------------------------------------------


def project_root() -> Path:
    """Корень приложения (каталог с main.py): core/tgws_manager.py → parents[1]."""
    return Path(__file__).resolve().parents[1]


def engine_dir() -> Path:
    return project_root() / "tgws"


def state_path() -> Path:
    return project_root() / "utils" / STATE_NAME


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def log_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "zapret_dpi_manager" / f"{SERVICE_NAME}.log"


def python_executable() -> str:
    """Интерпретатор для юнита: тот же, что у приложения, иначе системный."""
    executable = sys.executable or ""
    if executable and Path(executable).is_file():
        return executable
    return shutil.which("python3") or "/usr/bin/python3"


# --- Состояние --------------------------------------------------------------


def read_state() -> dict:
    """Читает utils/tgws.txt; при первом обращении создаёт его с секретом."""
    state = dict(DEFAULT_STATE)
    path = state_path()
    created = not path.is_file()
    if not created:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
            created = True
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in STATE_KEYS:
                state[key] = value.strip()
    state = _with_secret(state)
    if created:
        try:
            _write_raw(state)
        except OSError:
            pass
    return state


def _with_secret(state: dict) -> dict:
    if not SECRET_RE.match(state.get("secret", "")):
        state["secret"] = generate_secret()
    return state


def generate_secret() -> str:
    return os.urandom(16).hex()


def validate_state(state: dict) -> list[str]:
    problems: list[str] = []
    if str(state.get("enabled")) not in ("0", "1"):
        problems.append("enabled: ожидается 0 или 1")
    if str(state.get("autostart")) not in ("0", "1"):
        problems.append("autostart: ожидается 0 или 1")
    host = str(state.get("host", ""))
    if not HOST_RE.match(host):
        problems.append(f"адрес '{host}' недопустим")
    port = str(state.get("port", ""))
    if not port.isdigit() or not (1024 <= int(port) <= 65535):
        problems.append("порт: целое число от 1024 до 65535")
    if not SECRET_RE.match(str(state.get("secret", ""))):
        problems.append("секрет: 32 hex-символа")
    for key, title in (
        ("cf_domains", "CF-домены"),
        ("worker_domains", "CF worker-домены"),
    ):
        if not DOMAINS_RE.match(str(state.get(key, ""))):
            problems.append(f"{title}: недопустимые символы")
    if not FAKE_TLS_RE.match(str(state.get("fake_tls", ""))):
        problems.append("fake-TLS домен: недопустимые символы")
    return problems


def write_state(state: dict) -> None:
    merged = dict(DEFAULT_STATE)
    for key in STATE_KEYS:
        if key in state and state[key] is not None:
            merged[key] = str(state[key]).strip()
    problems = validate_state(merged)
    if problems:
        raise ValueError("; ".join(problems))
    _write_raw(merged)


def _write_raw(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [STATE_HEADER]
    lines += [f"{key}={state[key]}\n" for key in STATE_KEYS]
    path.write_text("".join(lines), encoding="utf-8")


def split_domains(value: str) -> list[str]:
    """Строка состояния → список доменов (через запятую/пробел)."""
    items = re.split(r"[,;\s]+", value or "")
    return [item for item in (part.strip() for part in items) if item]


# --- Параметры запуска ------------------------------------------------------


def build_command(state: dict) -> list[str]:
    """Аргументы движка из состояния (для юнита и ручного запуска)."""
    command = [
        python_executable(),
        "-m",
        "tgws",
        "--host",
        str(state.get("host", DEFAULT_HOST)),
        "--port",
        str(state.get("port", DEFAULT_PORT)),
        "--secret",
        str(state.get("secret", "")),
        "--log-file",
        str(log_path()),
    ]
    for domain in split_domains(str(state.get("cf_domains", ""))):
        command += ["--cfproxy-domain", domain]
    for domain in split_domains(str(state.get("worker_domains", ""))):
        command += ["--cfproxy-worker-domain", domain]
    fake_tls = str(state.get("fake_tls", "")).strip()
    if fake_tls:
        command += ["--fake-tls-domain", fake_tls]
    return command


def build_unit(state: dict) -> str:
    command = " ".join(_quote(arg) for arg in build_command(state))
    log_dir = log_path().parent
    return f"""[Unit]
Description=Telegram MTProto ↔ WebSocket proxy (Zapret DPI Manager)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={project_root()}
ExecStart={command}
Restart=on-failure
RestartSec=3
TimeoutStopSec=10
# логи: journalctl --user -u {SERVICE_NAME}; файл {log_path()}

[Install]
WantedBy=default.target
"""


def _quote(arg: str) -> str:
    if not arg:
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", arg):
        return arg
    return '"' + arg.replace('"', '\\"') + '"'


def build_link(state: dict) -> str:
    """Ссылка tg://proxy для Telegram Desktop."""
    host = str(state.get("host", DEFAULT_HOST))
    port = str(state.get("port", DEFAULT_PORT))
    secret = str(state.get("secret", ""))
    fake_tls = str(state.get("fake_tls", "")).strip()
    if fake_tls:
        return (
            f"tg://proxy?server={host}&port={port}"
            f"&secret=ee{secret}{fake_tls.encode('ascii').hex()}"
        )
    return f"tg://proxy?server={host}&port={port}&secret=dd{secret}"


def build_share_link(state: dict) -> str:
    """Ссылка для копирования (тот же tg://-адрес)."""
    return build_link(state)


# --- Управление службой (systemctl --user) ----------------------------------


def _run(argv: list[str], timeout: float = 20.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, "", "таймаут выполнения команды"
    except OSError as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def systemctl(*args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    return _run(["systemctl", "--user", *args], timeout=timeout)


def journalctl(lines: int = 80) -> str:
    code, out, err = _run(
        [
            "journalctl",
            "--user",
            "-u",
            f"{SERVICE_NAME}.service",
            "-n",
            str(max(1, min(lines, 500))),
            "--no-pager",
            "-o",
            "short-iso",
        ],
        timeout=10,
    )
    text = (out or "") + (("\n" + err) if err.strip() else "")
    return text.strip() or "(журнал пуст)"


def is_installed() -> bool:
    return unit_path().is_file()


def is_active() -> bool:
    code, out, _ = systemctl("is-active", f"{SERVICE_NAME}.service", timeout=5)
    return code == 0 and out.strip() == "active"


def is_enabled() -> bool:
    code, out, _ = systemctl("is-enabled", f"{SERVICE_NAME}.service", timeout=5)
    return code == 0 and out.strip() in ("enabled", "static", "enabled-runtime")


def engine_ready() -> bool:
    return (engine_dir() / "proxy.py").is_file() and (
        engine_dir() / "crypto.py"
    ).is_file()


def write_unit(state: dict) -> Path:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_unit(state), encoding="utf-8")
    log_path().parent.mkdir(parents=True, exist_ok=True)
    systemctl("daemon-reload", timeout=15)
    return path


def remove_unit() -> None:
    try:
        unit_path().unlink()
    except FileNotFoundError:
        pass
    systemctl("daemon-reload", timeout=15)


def start() -> tuple[bool, str]:
    code, out, err = systemctl("start", f"{SERVICE_NAME}.service", timeout=30)
    if code == 0 and is_active():
        return True, "прокси включён"
    return False, _combined(out, err) or "служба не перешла в active"


def stop() -> tuple[bool, str]:
    code, out, err = systemctl("stop", f"{SERVICE_NAME}.service", timeout=30)
    if code == 0:
        return True, "прокси выключен"
    return False, _combined(out, err)


def restart() -> tuple[bool, str]:
    code, out, err = systemctl("restart", f"{SERVICE_NAME}.service", timeout=30)
    if code == 0 and is_active():
        return True, "прокси перезапущен"
    return False, _combined(out, err) or "служба не перешла в active"


def set_enabled(enabled: bool) -> tuple[bool, str]:
    action = "enable" if enabled else "disable"
    code, out, err = systemctl(action, f"{SERVICE_NAME}.service", timeout=20)
    if code == 0:
        return True, "автозапуск включён" if enabled else "автозапуск выключен"
    return False, _combined(out, err)


def enable_linger() -> tuple[bool, str]:
    """Разрешает user-службам работать после выхода из сессии."""
    user = os.environ.get("USER") or Path.home().name
    code, out, err = _run(["loginctl", "enable-linger", user], timeout=15)
    if code == 0:
        return True, "служба будет работать и после выхода из системы"
    return False, _combined(out, err)


def linger_enabled() -> bool:
    user = os.environ.get("USER") or Path.home().name
    code, out, _ = _run(["loginctl", "show-user", user, "-p", "Linger"], timeout=5)
    return code == 0 and "Linger=yes" in out


def log_tail(lines: int = 120) -> str:
    """Хвост журнала плюс файловый лог, если он новее."""
    parts = []
    try:
        if log_path().is_file():
            content = log_path().read_text(encoding="utf-8", errors="replace")
            parts.append("\n".join(content.splitlines()[-lines:]))
    except OSError:
        pass
    parts.append(journalctl(lines))
    return "\n\n".join(part for part in parts if part.strip())


def status() -> dict:
    """Данные для карточки/страницы интерфейса."""
    state = read_state()
    active = is_active()
    installed = is_installed()
    enabled = is_enabled()
    ready = engine_ready()
    title = "Прокси остановлен"
    if not ready:
        title = "Движок прокси не найден"
    elif active:
        title = "Прокси работает"
    return {
        "installed": installed,
        "active": active,
        "enabled": enabled,
        "engine_ready": ready,
        "linger": linger_enabled(),
        "title": title,
        "port": state.get("port", str(DEFAULT_PORT)),
        "host": state.get("host", DEFAULT_HOST),
        "secret": state.get("secret", ""),
        "link": build_link(state),
        "cf_domains": state.get("cf_domains", ""),
        "worker_domains": state.get("worker_domains", ""),
        "fake_tls": state.get("fake_tls", ""),
        "log_path": str(log_path()),
    }


def enable_start(state: dict) -> tuple[bool, str]:
    """Включение: записать состояние → юнит → автозапуск → старт."""
    write_state(state)
    write_unit(state)
    ok, message = set_enabled(True)
    if not ok:
        return False, f"не удалось включить автозапуск: {message}"
    ok, message = start()
    if not ok:
        return False, _combined(message, log_tail(40))
    state["enabled"] = "1"
    write_state(state)
    return True, "прокси включён"


def disable_stop(state: dict) -> tuple[bool, str]:
    """Выключение: остановка, снятие автозапуска, enabled=0."""
    ok, message = stop()
    set_enabled(False)
    state["enabled"] = "0"
    write_state(state)
    if not ok:
        return False, message
    return True, "прокси выключен"


def uninstall() -> tuple[bool, str]:
    """Снимает службу и удаляет состояние прокси (для удаления менеджера)."""
    problems: list[str] = []
    try:
        stop()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"остановка: {exc}")
    try:
        set_enabled(False)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"автозапуск: {exc}")
    try:
        remove_unit()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"юнит: {exc}")
    for path in (state_path(), log_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            problems.append(f"{path.name}: {exc}")
    if problems:
        return False, "; ".join(problems)
    return True, "прокси удалён"


def _combined(*chunks: str) -> str:
    parts = [str(chunk).strip() for chunk in chunks if chunk and str(chunk).strip()]
    return "\n".join(parts)
