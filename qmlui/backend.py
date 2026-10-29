# -*- coding: utf-8 -*-
"""Мост между Python-ядром и QML.

Объект ``Backend`` публикуется в QML как контекстное свойство ``Backend``.
Все операции, которые могут занять время (systemctl, sudo, чтение файлов),
выполняются в отдельном потоке; результат возвращается в главный поток через
сигналы Qt, поэтому интерфейс не замирает.
"""
from __future__ import annotations

import os
import re
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from core.app_logging import get_error_logger
from core.manager_config import GITHUB_BASE_URL, VERSION_CONFIG
from core.service_manager import ServiceManager
from core.sudo_helper import sudo_available

MANAGER_DIR = Path.home() / "Zapret_DPI_Manager"
CONFIG_FILE = MANAGER_DIR / "config.txt"
NAME_FILE = MANAGER_DIR / "utils" / "name_strategy.txt"
STRATEGY_DIR = MANAGER_DIR / "files" / "strategy"
WORKING_FILE = MANAGER_DIR / "utils" / "working_strategies.txt"

GUIDE_URL = (
    f"{GITHUB_BASE_URL}/blob/main/docs/user-guide-zapret-dpi-manager-steam-deck-ru.md"
)

#: Предел длины текста ошибки службы в статусной строке.
ERROR_LIMIT = 700

STATE_LABELS = {
    "active": "Работает",
    "inactive": "Остановлена",
    "failed": "Ошибка запуска",
    "activating": "Запускается…",
    "deactivating": "Останавливается…",
    "unknown": "Состояние неизвестно",
}


def _shorten(message: str, limit: int = ERROR_LIMIT) -> str:
    text = (message or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 60].rstrip() + f"\n… Полный вывод: {_log_path()}"


def _log_path() -> str:
    from core.app_logging import LOG_FILE_PATH

    return str(LOG_FILE_PATH)


def read_strategy_name() -> str:
    """Имя текущей стратегии из utils/name_strategy.txt (как в Tk-версии)."""
    try:
        if not NAME_FILE.is_file():
            NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
            NAME_FILE.write_text("", encoding="utf-8")
            return "Не выбрано"
        name = NAME_FILE.read_text(encoding="utf-8").strip()
        config_empty = True
        if CONFIG_FILE.is_file():
            config_empty = not CONFIG_FILE.read_text(encoding="utf-8").strip()
        if not name or config_empty:
            return "Не выбрано"
        return name
    except OSError:
        return "Не выбрано"


def read_working_strategies() -> set[str]:
    """Имена стратегий, отмеченных тестером как рабочие."""
    try:
        if not WORKING_FILE.is_file():
            return set()
        names = set()
        for line in WORKING_FILE.read_text(encoding="utf-8").splitlines():
            name = line.strip().lstrip("\ufeff").strip()
            if name:
                names.add(name)
        return names
    except OSError:
        return set()


def load_strategy_names() -> dict:
    """Сохранённые варианты конструктора стратегии."""
    from core.strategy_data import load_strategy_names as _load

    return _load()


def read_strategies() -> list[dict]:
    """Список стратегий вида [{'name': ..., 'working': bool}]."""
    try:
        from core.strategy_data import natural_sort_key
    except Exception:  # noqa: BLE001 - сортировка не критична
        natural_sort_key = lambda text: text.lower()  # noqa: E731

    try:
        if not STRATEGY_DIR.is_dir():
            return []
        working = read_working_strategies()
        names = [
            entry.name
            for entry in STRATEGY_DIR.iterdir()
            if entry.is_file() and not entry.name.startswith(".")
        ]
        names.sort(key=natural_sort_key)
        return [{"name": name, "working": name in working} for name in names]
    except OSError:
        return []


HOSTLIST_DIR = MANAGER_DIR / "files" / "lists"
LIST_GENERAL = HOSTLIST_DIR / "list-general.txt"
LIST_GENERAL_USER = HOSTLIST_DIR / "list-general_user.txt"
LIST_EXCLUDE_USER = HOSTLIST_DIR / "list-exclude_user.txt"
LIST_TELEGRAM_USER = HOSTLIST_DIR / "list-telegram_user.txt"

#: Предустановленные сервисы (как в Tk-окне Hostlist).
HOSTLIST_SERVICES: dict[str, list[str]] = {
    "WhatsApp": [
        "api.whatsapp.com", "chat.whatsapp.com", "w1.web.whatsapp.com",
        "w2.web.whatsapp.com", "w3.web.whatsapp.com", "w4.web.whatsapp.com",
        "w5.web.whatsapp.com", "w6.web.whatsapp.com", "w7.web.whatsapp.com",
        "w8.web.whatsapp.com", "wa.me", "web.whatsapp.com", "whatsapp.com",
        "whatsapp.net", "www.whatsapp.com",
    ],
    "Rockstar/Epic Games": [
        "cdn1.unrealengine.com", "cdn2.unrealengine.com",
        "epicgames-download1.akamaized.net", "fortnite.com", "rockstargames.com",
    ],
    "Github": [
        "github.com", "raw.githubusercontent.com", "gist.githubusercontent.com",
        "githubusercontent.com", "gitlab.com", "raw.gitlab.com",
        "snippets.gitlab.com", "git.sr.ht",
    ],
    "Telegram": [
        "telegram.org", "telegram.me", "telegram.dog", "telegram.space", "t.me",
        "telegra.ph", "graph.org", "telesco.pe", "tg.dev", "cdn-telegram.org",
        "telegram-cdn.org", "comments.app",
    ],
}

_DOMAIN_RE = re.compile(r"^(\*\.)?([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z]{2,}$")


def _clean_domain(value: str) -> str:
    """Убирает протокол, путь и порт из строки домена."""
    if not value or value.strip().startswith("#"):
        return value
    cleaned = re.sub(r"^[a-zA-Z]+://", "", value.strip())
    cleaned = cleaned.split("/")[0]
    return cleaned.split(":")[0]


def _is_valid_domain(value: str) -> tuple[bool, str]:
    if value.startswith("#") or not value.strip():
        return True, ""
    if not _DOMAIN_RE.match(value):
        return False, "некорректный формат домена"
    if len(value) > 253:
        return False, "слишком длинное имя"
    for part in value.split("."):
        if part.startswith("-") or part.endswith("-"):
            return False, "часть домена начинается или кончается дефисом"
        if len(part) > 63:
            return False, "часть домена слишком длинная"
    return True, ""


def _read_list(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return ""


def _is_telegram_rule(line: str) -> bool:
    return "{list_telegram}" in line or "{ipset_telegram}" in line


def read_hostlist_state() -> dict:
    """Текущее состояние окна Hostlist: домены, сервисы, обход Telegram."""
    blocked = _read_list(LIST_GENERAL_USER)
    unblocked = _read_list(LIST_EXCLUDE_USER)
    base = {line.strip() for line in _read_list(LIST_GENERAL).splitlines() if line.strip()}
    telegram_user = {line.strip() for line in _read_list(LIST_TELEGRAM_USER).splitlines()
                     if line.strip()}
    try:
        config_lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        config_lines = []

    services = {}
    for name in ("WhatsApp", "Rockstar/Epic Games", "Github"):
        services[name] = bool(base & set(HOSTLIST_SERVICES[name]))
    services["Telegram"] = bool(telegram_user & set(HOSTLIST_SERVICES["Telegram"]))
    return {
        "blocked": blocked,
        "unblocked": unblocked,
        "services": services,
        "serviceNames": list(HOSTLIST_SERVICES.keys()),
        "telegramRules": any(_is_telegram_rule(line) for line in config_lines),
        "telegramDomains": len([line for line in telegram_user if line]),
        "blockedCount": len([line for line in blocked.splitlines()
                             if line.strip() and not line.strip().startswith("#")]),
        "unblockedCount": len([line for line in unblocked.splitlines()
                               if line.strip() and not line.strip().startswith("#")]),
    }


def _apply_telegram_rules(enabled: bool) -> int:
    """Включает/выключает обход Telegram в config.txt, возвращает число строк."""
    from core.flowseal_convert import manager_extra_rules

    try:
        lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    lines = [line for line in lines if not _is_telegram_rule(line)]
    if enabled:
        existing = set(lines)
        for rule in manager_extra_rules():
            if rule not in existing:
                lines.append(rule)
    while lines and not lines[-1].strip():
        lines.pop()
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len([line for line in lines if _is_telegram_rule(line)])


def save_hostlist(values: dict) -> tuple[bool, str, str]:
    """Сохраняет домены, сервисы и обход Telegram (логика Tk-окна Hostlist)."""
    blocked_raw = str(values.get("blocked") or "")
    unblocked_raw = str(values.get("unblocked") or "")
    errors: list[str] = []

    def prepare(text: str, label: str) -> list[str]:
        cleaned: list[str] = []
        for number, line in enumerate(text.splitlines(), 1):
            if line.strip() and not line.strip().startswith("#"):
                value = _clean_domain(line)
                ok, reason = _is_valid_domain(value)
                if not ok:
                    errors.append(f"{label}, строка {number}: {value} — {reason}")
                cleaned.append(value)
            else:
                cleaned.append(line)
        return cleaned

    blocked_lines = prepare(blocked_raw, "Заблокированные")
    unblocked_lines = prepare(unblocked_raw, "Незаблокированные")
    if errors:
        return False, "Данные не сохранены:\n" + "\n".join(errors[:6]), "error"

    selected = dict(values.get("services") or {})
    try:
        HOSTLIST_DIR.mkdir(parents=True, exist_ok=True)
        LIST_GENERAL_USER.write_text("\n".join(blocked_lines), encoding="utf-8")
        LIST_EXCLUDE_USER.write_text("\n".join(unblocked_lines), encoding="utf-8")

        # Сервисы: убираем все сервисные домены и добавляем только выбранные.
        current = {line.strip() for line in blocked_lines if line.strip()}
        all_service_domains: set[str] = set()
        for domains in HOSTLIST_SERVICES.values():
            all_service_domains.update(domains)
        current -= all_service_domains
        for name, domains in HOSTLIST_SERVICES.items():
            if name == "Telegram":
                continue
            if selected.get(name):
                current.update(domains)
        LIST_GENERAL_USER.write_text(
            "\n".join(sorted(current)) + ("\n" if current else ""), encoding="utf-8")

        telegram_domains = HOSTLIST_SERVICES["Telegram"]
        LIST_TELEGRAM_USER.write_text(
            "\n".join(sorted(telegram_domains)) + "\n"
            if selected.get("Telegram") else "", encoding="utf-8")

        rules = 0
        try:
            rules = _apply_telegram_rules(bool(selected.get("telegramRules")))
        except Exception as exc:  # noqa: BLE001
            return False, f"Домены сохранены, но config.txt не обновлён: {exc}", "warning"
    except OSError as exc:
        return False, f"Не удалось сохранить домены: {exc}", "error"

    chosen = [name for name in ("WhatsApp", "Rockstar/Epic Games", "Github", "Telegram")
              if selected.get(name)]
    return True, ("Сохранено. Сервисы: " + (", ".join(chosen) or "ни одного")
                  + f" · доменов: {len(current)} · строк обхода Telegram: {rules}"), "success"


def _curl_head(url: str, extra: list[str] | None = None, timeout: int = 12) -> tuple[bool, str]:
    """HEAD-запрос curl; возвращает (успех, статус/ошибка)."""
    import subprocess

    command = ["curl", "-s", "-I", "--connect-timeout", "5", "--max-time", "10",
               "--location", "-H",
               "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", url]
    if extra:
        command[3:3] = extra
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "таймаут"
    except OSError as exc:
        return False, str(exc)
    first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
    parts = first.split()
    status = parts[1] if len(parts) > 1 else ""
    if proc.returncode == 0 and status:
        return True, status
    return False, (proc.stderr or "нет ответа").splitlines()[0][:80]


def run_connection_checks(cancel, emit) -> dict:
    """Проверки соединения как в Tk-окне: сеть, интернет, YouTube, Discord, Telegram."""
    import subprocess

    def say(text: str, kind: str = "on_surface") -> None:
        if not cancel.is_set():
            emit(text, kind)

    services: dict = {"youtube": None, "discord": None, "telegram": None}
    summary = ""
    try:
        try:
            status = subprocess.run(["systemctl", "is-active", "zapret"],
                                    capture_output=True, text=True, timeout=3).stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            status = "неизвестно"
        if status == "active":
            say("Zapret: служба активна", "success")
        else:
            say(f"Zapret: служба {status or 'не активна'}", "warning")

        # 1. Локальная сеть: шлюз и пинг.
        say("Проверка локальной сети…", "primary")
        gateway = ""
        try:
            route = subprocess.run(["ip", "route", "show", "default"],
                                   capture_output=True, text=True, timeout=10).stdout.strip()
            parts = route.splitlines()[0].split() if route else []
            if len(parts) >= 3:
                gateway = parts[2]
        except (subprocess.TimeoutExpired, OSError):
            gateway = ""
        if gateway:
            try:
                ping_ok = subprocess.run(["ping", "-c", "2", "-W", "1", gateway],
                                         capture_output=True, text=True, timeout=10).returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                ping_ok = False
            say(f"Шлюз {gateway}: " + ("доступен" if ping_ok else "недоступен"),
                "success" if ping_ok else "error")
        else:
            say("Шлюз не найден", "error")

        if cancel.is_set():
            return {"summary": "Проверка отменена", "services": services}

        # 2. Интернет.
        say("Проверка интернет-соединения…", "primary")
        internet_ok = 0
        for name, url in (("Google", "https://www.google.com"),
                          ("Cloudflare", "https://1.1.1.1"),
                          ("Yandex", "https://ya.ru")):
            if cancel.is_set():
                return {"summary": "Проверка отменена", "services": services}
            ok, detail = _curl_head(url, timeout=8)
            if ok and int(detail) < 400:
                internet_ok += 1
                say(f"{name}: HTTP {detail}", "success")
            else:
                say(f"{name}: {detail}", "error")
        say(f"Интернет: доступен ({internet_ok}/3)", "success" if internet_ok else "error")

        # 3. YouTube.
        say("Проверка YouTube…", "primary")
        youtube_tests = [
            ("youtube.com", "https://www.youtube.com/"),
            ("generate_204", "https://rr2---sn-axq7sn7z.googlevideo.com/generate_204"),
            ("YouTube Images", "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"),
        ]
        youtube_ok = 0
        for name, url in youtube_tests:
            if cancel.is_set():
                return {"summary": "Проверка отменена", "services": services}
            ok, detail = _curl_head(url, timeout=14)
            if ok:
                youtube_ok += 1
                say(f"  {name}: HTTP {detail}", "success")
            else:
                say(f"  {name}: {detail}", "error")
        services["youtube"] = youtube_ok > 0
        say("YouTube: " + ("доступен" if youtube_ok else "недоступен"),
            "success" if youtube_ok else "error")

        # 4. Discord.
        say("Проверка Discord…", "primary")
        discord_tests = [
            ("Discord Website", "https://discord.com/"),
            ("Discord API", "https://discord.com/api/v9/gateway"),
        ]
        discord_ok = 0
        for name, url in discord_tests:
            if cancel.is_set():
                return {"summary": "Проверка отменена", "services": services}
            ok, detail = _curl_head(url, timeout=14)
            if ok:
                discord_ok += 1
                say(f"  {name}: HTTP {detail}", "success")
            else:
                say(f"  {name}: {detail}", "error")
        services["discord"] = discord_ok > 0
        say("Discord: " + ("доступен" if discord_ok else "недоступен"),
            "success" if discord_ok else "error")

        # 5. Telegram.
        say("Проверка Telegram…", "primary")
        telegram_tests = [
            ("web.telegram.org", "https://web.telegram.org/"),
            ("api.telegram.org", "https://api.telegram.org/"),
            ("t.me", "https://t.me/"),
        ]
        telegram_ok = 0
        for name, url in telegram_tests:
            if cancel.is_set():
                return {"summary": "Проверка отменена", "services": services}
            ok, detail = _curl_head(url, timeout=14)
            if ok:
                telegram_ok += 1
                say(f"  {name}: HTTP {detail}", "success")
            else:
                say(f"  {name}: {detail}", "error")
        services["telegram"] = telegram_ok > 0
        say("Telegram: " + ("доступен" if telegram_ok else "недоступен"),
            "success" if telegram_ok else "error")

        def label(value) -> str:
            return "OK" if value else "нет"

        summary = (f"YouTube: {label(services['youtube'])} · "
                   f"Discord: {label(services['discord'])} · "
                   f"Telegram: {label(services['telegram'])}")
        say(summary, "primary")
    except Exception as exc:  # noqa: BLE001
        summary = f"Ошибка проверки: {exc}"
        say(summary, "error")
    return {"summary": summary, "services": services}


def _pick_best_strategy(results: list[dict], mode: str) -> str:
    """Выбирает лучшую стратегию по результатам автоподбора."""
    def score(item: dict) -> tuple:
        passed = sum(1 for key in ("youtube_passed", "discord_passed", "telegram_passed")
                     if item.get(key) is True)
        rate = float(item.get("success_rate") or 0)
        failures = 1 if item.get("critical_fail") else 0
        return (passed, -failures, rate)

    if not results:
        return ""
    ordered = sorted(results, key=score, reverse=True)
    best = ordered[0]
    if best.get("success_rate") is None and not best.get("youtube_passed"):
        return ""
    if mode != "YouTube/Discord" and float(best.get("success_rate") or 0) < 60:
        return ""
    return str(best.get("strategy") or "")


IPSET_ALL_USER = HOSTLIST_DIR / "ipset-all_user.txt"
IPSET_EXCLUDE_USER = HOSTLIST_DIR / "ipset-exclude_user.txt"
IPSET_TELEGRAM_USER = HOSTLIST_DIR / "ipset-telegram_user.txt"
IPSET_ALL = HOSTLIST_DIR / "ipset-all.txt"
IPSET_ALL_UTILS = MANAGER_DIR / "utils" / "ipset-all.txt"
IPSET_STUB = "203.0.113.113/32"


def _validate_ip_entry(value: str) -> tuple[bool, str]:
    """Проверяет IP, CIDR или IPv4-диапазон a-b."""
    import ipaddress

    if value.startswith("#") or not value.strip():
        return True, ""
    text = value.strip()
    if text.count("-") == 1 and ":" not in text:
        start, end = (part.strip() for part in text.split("-", 1))
        try:
            first = ipaddress.IPv4Address(start)
            last = ipaddress.IPv4Address(end)
        except ValueError:
            return False, "некорректный диапазон IPv4"
        if int(first) > int(last):
            return False, "начало диапазона больше конца"
        return True, ""
    try:
        ipaddress.ip_network(text, strict=False)
        return True, ""
    except ValueError:
        return False, "некорректный IP-адрес или подсеть"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return ""


def _filter_mode() -> str:
    """Режим фильтра ipset-all.txt: any (пусто), none (заглушка), loaded (utils)."""
    content = _read_text(IPSET_ALL).strip()
    if not content:
        return "any"
    if content == IPSET_STUB:
        return "none"
    utils_content = _read_text(IPSET_ALL_UTILS).strip()
    if utils_content and set(content.split()) == set(utils_content.split()):
        return "loaded"
    return "loaded"


def read_ipset_state() -> dict:
    blocked = _read_text(IPSET_ALL_USER)
    unblocked = _read_text(IPSET_EXCLUDE_USER)
    telegram = _read_text(IPSET_TELEGRAM_USER)

    def count(text: str) -> int:
        return len([line for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")])

    return {
        "blocked": blocked,
        "unblocked": unblocked,
        "telegram": telegram,
        "blockedCount": count(blocked),
        "unblockedCount": count(unblocked),
        "telegramCount": count(telegram),
        "filterMode": _filter_mode(),
    }


def save_ipset(values: dict) -> tuple[bool, str, str]:
    """Сохраняет пользовательские диапазоны в ipset-*_user.txt."""
    errors: list[str] = []

    def check(text: str, label: str) -> str:
        for number, line in enumerate(text.splitlines(), 1):
            value = line.strip()
            if not value:
                continue
            ok, reason = _validate_ip_entry(value)
            if not ok:
                errors.append(f"{label}, строка {number}: {value} — {reason}")
        return text

    blocked = check(str(values.get("blocked") or ""), "Заблокированные")
    unblocked = check(str(values.get("unblocked") or ""), "Незаблокированные")
    telegram = check(str(values.get("telegram") or ""), "Telegram")
    if errors:
        return False, "Данные не сохранены:\n" + "\n".join(errors[:6]), "error"

    try:
        HOSTLIST_DIR.mkdir(parents=True, exist_ok=True)
        IPSET_ALL_USER.write_text(blocked, encoding="utf-8")
        IPSET_EXCLUDE_USER.write_text(unblocked, encoding="utf-8")
        IPSET_TELEGRAM_USER.write_text(telegram, encoding="utf-8")
    except OSError as exc:
        return False, f"Не удалось сохранить диапазоны: {exc}", "error"

    def count(text: str) -> int:
        return len([line for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")])

    return True, (f"Сохранено: заблокированные {count(blocked)}, исключения {count(unblocked)}, "
                  f"Telegram {count(telegram)}"), "success"


def apply_ipset_filter(mode: str) -> tuple[bool, str, str]:
    """Применяет режим фильтра ipset-all.txt и перезапускает службу."""
    import shutil
    import time

    mode = (mode or "").strip().lower()
    if mode not in ("any", "loaded", "none"):
        return False, f"неизвестный режим фильтра: {mode}", "error"

    HOSTLIST_DIR.mkdir(parents=True, exist_ok=True)
    if IPSET_ALL.is_file():
        backup = IPSET_ALL.with_name(f"{IPSET_ALL.name}.bak.{time.strftime('%Y%m%d_%H%M%S')}")
        try:
            shutil.copy2(IPSET_ALL, backup)
        except OSError as exc:
            return False, f"не удалось создать бэкап ipset: {exc}", "error"

    if mode == "any":
        IPSET_ALL.write_text("", encoding="utf-8")
        message = "Режим «any» применён"
    elif mode == "none":
        IPSET_ALL.write_text(IPSET_STUB, encoding="utf-8")
        message = "Режим «none» применён"
    else:
        if not IPSET_ALL_UTILS.is_file():
            return False, f"файл {IPSET_ALL_UTILS} не найден", "error"
        IPSET_ALL.write_text(_read_text(IPSET_ALL_UTILS), encoding="utf-8")
        message = "Режим «loaded» применён"

    ok, restart_message = ServiceManager().restart_service()
    if ok:
        return True, message + ", служба перезапущена", "success"
    return False, message + ", но служба не перезапущена: " + _shorten(restart_message, 200), "warning"


DNS_STATE_EMPTY = {"connection": "", "device": "", "servers": [], "raw": "", "resolv": ""}


def _run_command(argv: list[str], sudo: bool = False, timeout: int = 60) -> tuple[int, str, str]:
    """Запуск команды (через run_sudo при необходимости)."""
    import subprocess

    if sudo:
        from core.sudo_helper import run_sudo

        return run_sudo(argv, timeout=timeout)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _active_connection() -> dict | None:
    """Активное соединение NetworkManager (wifi предпочтительнее)."""
    code, out, _err = _run_command(
        ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"], timeout=15)
    if code != 0:
        return None
    connections = []
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3:
            connections.append({"name": parts[0], "device": parts[1], "type": parts[2]})
    for item in connections:
        if item["type"] == "wifi":
            return item
    return connections[0] if connections else None


def read_dns_state() -> dict:
    """Текущие DNS активного соединения и вывод resolvectl."""
    state = dict(DNS_STATE_EMPTY)
    connection = _active_connection()
    if connection is None:
        return state
    state["connection"] = connection["name"]
    state["device"] = connection["device"]
    code, out, _err = _run_command(
        ["nmcli", "-g", "ipv4.dns", "connection", "show", connection["name"]], timeout=15)
    if code == 0:
        servers = [item.strip() for item in out.replace(",", " ").split() if item.strip()]
        state["servers"] = servers
        state["raw"] = " ".join(servers)
    code, out, _err = _run_command(["resolvectl", "dns"], timeout=10)
    if code == 0:
        lines = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].endswith(":"):
                lines.append(f"{parts[0][:-1]}: {' '.join(parts[2:])}")
        state["resolv"] = " · ".join(lines[:3])
    return state


def apply_dns(servers: str) -> tuple[bool, str, str]:
    """Ставит статические DNS на активное соединение и переподключает его."""
    connection = _active_connection()
    if connection is None:
        return False, "Не найдено активное подключение", "error"
    name = connection["name"]
    values = " ".join([item.strip() for item in str(servers).replace(",", " ").split()
                       if item.strip()])
    if not values:
        return False, "Укажите DNS-серверы, например 1.1.1.1 8.8.8.8", "error"
    for item in values.split():
        ok, reason = _validate_ip_entry(item)
        if not ok:
            return False, f"Некорректный DNS-сервер {item}: {reason}", "error"

    code, _out, err = _run_command(
        ["nmcli", "connection", "modify", name,
         "ipv4.dns", values, "ipv4.ignore-auto-dns", "yes",
         "ipv6.ignore-auto-dns", "yes"], sudo=True)
    if code != 0:
        return False, f"nmcli не сохранил настройки: {err.strip()[:200]}", "error"
    _run_command(["nmcli", "connection", "down", name], sudo=True)
    code, _out, err = _run_command(["nmcli", "connection", "up", name], sudo=True, timeout=90)
    if code != 0:
        return False, f"Не удалось переподключить соединение: {err.strip()[:200]}", "error"
    _run_command(["resolvectl", "flush-caches"], sudo=True, timeout=20)
    return True, f"DNS применены для {name}: {values}", "success"


def reset_dns() -> tuple[bool, str, str]:
    """Возвращает автоматические DNS (DHCP)."""
    connection = _active_connection()
    if connection is None:
        return False, "Не найдено активное подключение", "error"
    name = connection["name"]
    code, _out, err = _run_command(
        ["nmcli", "connection", "modify", name,
         "ipv4.ignore-auto-dns", "no", "ipv6.ignore-auto-dns", "no"], sudo=True)
    if code != 0:
        return False, f"nmcli не изменил настройки: {err.strip()[:200]}", "error"
    _run_command(["nmcli", "connection", "modify", name, "ipv4.dns", ""], sudo=True)
    _run_command(["nmcli", "connection", "modify", name, "ipv6.dns", ""], sudo=True)
    _run_command(["nmcli", "connection", "down", name], sudo=True)
    code, _out, err = _run_command(["nmcli", "connection", "up", name], sudo=True, timeout=90)
    if code != 0:
        run_sudo_restore = ["nmcli", "connection", "up", name]
        _run_command(run_sudo_restore, sudo=True, timeout=90)
        return False, f"Не удалось переподключить соединение: {err.strip()[:200]}", "error"
    _run_command(["resolvectl", "flush-caches"], sudo=True, timeout=20)
    return True, "DNS снова получаются автоматически (DHCP)", "success"


GAMEFILTER_ENABLE_FILE = MANAGER_DIR / "utils" / "gamefilter.enable"
GAMEFILTER_MODE_FILE = MANAGER_DIR / "utils" / "gamefilter.mode"
LISTS_DIR = MANAGER_DIR / "files" / "lists"
GAMES_DATA_DIR = MANAGER_DIR / "utils" / "for games"


def _preset_read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")]
    except OSError:
        return []


def _preset_write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(chr(10).join(lines) + (chr(10) if lines else ""), encoding="utf-8")


def _preset_remove_domains(source: Path, target: Path) -> None:
    domains = set(_preset_read_lines(source))
    if not domains:
        return
    current = _preset_read_lines(target)
    _preset_write_lines(target, [item for item in current if item not in domains])


def _preset_set_ipset_none() -> None:
    path = LISTS_DIR / "ipset-all.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("203.0.113.113/32", encoding="utf-8")


def _clear_active_game_preset_disk() -> None:
    """Снимает активный пресет с диска (без перезапуска службы)."""
    from core.game_presets import (clear_active_preset, games_data_file,
                                   get_active_preset_id, get_manager_dir,
                                   remove_preset_lines_from_config,
                                   restore_gamefilter_for_preset)

    manager_dir = get_manager_dir()
    active = get_active_preset_id(manager_dir)
    if active:
        if active == "roblox":
            _preset_remove_domains(Path(games_data_file(manager_dir, "list-general_roblox.txt")),
                                   LISTS_DIR / "list-general.txt")
            _preset_set_ipset_none()
        elif active == "fall_guys":
            _preset_remove_domains(Path(games_data_file(manager_dir, "list-general_fallguys.txt")),
                                   LISTS_DIR / "list-general_user.txt")
            _preset_remove_domains(Path(games_data_file(manager_dir, "ipset-all_fallguys.txt")),
                                   LISTS_DIR / "ipset-all_user.txt")
        else:
            remove_preset_lines_from_config(active, manager_dir)
        if active == "elite_dangerous":
            _preset_set_ipset_none()
        restore_gamefilter_for_preset(active, manager_dir)
    clear_active_preset(manager_dir)


def read_gamefilter_state() -> dict:
    """Состояние GameFilter: включён ли, режим протоколов, активный пресет."""
    from core.game_filter_settings import read_game_filter_protocol_mode
    from core.game_presets import GAME_PRESETS, get_active_preset_id

    try:
        mode = read_game_filter_protocol_mode()
    except Exception:  # noqa: BLE001
        mode = "both"
    try:
        active = get_active_preset_id() or ""
    except Exception:  # noqa: BLE001
        active = ""
    presets = [{
        "id": preset_id,
        "name": preset["name"],
        "tcp": preset.get("game_filter_tcp"),
        "udp": preset.get("game_filter_udp"),
    } for preset_id, preset in GAME_PRESETS.items()]
    return {
        "enabled": GAMEFILTER_ENABLE_FILE.is_file(),
        "mode": mode,
        "activePreset": active,
        "presets": presets,
    }


def set_gamefilter(enabled: bool, mode: str) -> tuple[bool, str, str]:
    """Включает/выключает отдельный GameFilter."""
    from core.game_filter_settings import (normalize_game_filter_protocol_mode,
                                           remove_game_filter_protocol_mode_file,
                                           write_game_filter_protocol_mode)
    from core.service_manager import ServiceManager

    try:
        GAMEFILTER_ENABLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if enabled:
            _clear_active_game_preset_disk()
            GAMEFILTER_ENABLE_FILE.write_text("", encoding="utf-8")
            normalized = normalize_game_filter_protocol_mode(mode)
            write_game_filter_protocol_mode(normalized)
            message = f"GameFilter включён ({normalized.upper()})"
        else:
            if GAMEFILTER_ENABLE_FILE.is_file():
                GAMEFILTER_ENABLE_FILE.unlink()
            remove_game_filter_protocol_mode_file()
            message = "GameFilter выключен"
    except OSError as exc:
        return False, f"Не удалось изменить состояние GameFilter: {exc}", "error"

    ok, restart_message = ServiceManager().restart_service()
    if ok:
        return True, message + ", служба перезапущена", "success"
    return False, message + ", но служба не перезапущена: " + _shorten(restart_message, 200), "warning"


def apply_game_preset(preset_id: str) -> tuple[bool, str, str]:
    """Применяет игровой пресет: строки и порты в config.txt, маркер в utils."""
    from core.game_filter_settings import disable_standalone_gamefilter
    from core.game_presets import (GAME_PRESETS, games_data_file, get_active_preset_id,
                                   get_manager_dir, remove_preset_lines_from_config,
                                   restore_gamefilter_for_preset, set_active_preset,
                                   substitute_gamefilter_in_config)
    from core.service_manager import ServiceManager

    preset = GAME_PRESETS.get(preset_id)
    if preset is None:
        return False, "Пресет не найден", "error"

    manager_dir = get_manager_dir()
    active_before = get_active_preset_id(manager_dir)
    had_standalone = disable_standalone_gamefilter(manager_dir)
    try:
        if active_before and active_before != preset_id:
            if active_before == "roblox":
                _preset_remove_domains(Path(games_data_file(manager_dir, "list-general_roblox.txt")),
                                       LISTS_DIR / "list-general.txt")
                _preset_set_ipset_none()
            elif active_before == "fall_guys":
                _preset_remove_domains(
                    Path(games_data_file(manager_dir, "list-general_fallguys.txt")),
                    LISTS_DIR / "list-general_user.txt")
                _preset_remove_domains(Path(games_data_file(manager_dir, "ipset-all_fallguys.txt")),
                                       LISTS_DIR / "ipset-all_user.txt")
            else:
                remove_preset_lines_from_config(active_before, manager_dir)
            if active_before == "elite_dangerous":
                _preset_set_ipset_none()
            restore_gamefilter_for_preset(active_before, manager_dir)

        tcp = preset.get("game_filter_tcp")
        udp = preset.get("game_filter_udp")
        if tcp is not None and udp is not None:
            substitute_gamefilter_in_config(tcp, udp, manager_dir)

        remove_preset_lines_from_config(preset_id, manager_dir)
        if preset_id == "roblox":
            _preset_append_domains(games_data_file(manager_dir, "list-general_roblox.txt"),
                                   LISTS_DIR / "list-general.txt")
            _preset_append_entries(games_data_file(manager_dir, "ipset-all_roblox.txt"),
                                   LISTS_DIR / "ipset-all_user.txt")
        elif preset_id == "fall_guys":
            _preset_append_domains(games_data_file(manager_dir, "list-general_fallguys.txt"),
                                   LISTS_DIR / "list-general_user.txt")
            _preset_append_entries(games_data_file(manager_dir, "ipset-all_fallguys.txt"),
                                   LISTS_DIR / "ipset-all_user.txt")
        elif preset_id == "elite_dangerous":
            IP = (MANAGER_DIR / "utils" / "ipset-all.txt")
            if IP.is_file():
                (LISTS_DIR / "ipset-all.txt").write_text(IP.read_text(encoding="utf-8"),
                                                         encoding="utf-8")

        lines = preset.get("lines") or []
        if lines:
            existing = ""
            if CONFIG_FILE.is_file():
                existing = CONFIG_FILE.read_text(encoding="utf-8")
            CONFIG_FILE.write_text(chr(10).join(lines) + chr(10) + existing, encoding="utf-8")
        set_active_preset(preset_id, manager_dir)
    except OSError as exc:
        return False, f"Не удалось применить пресет: {exc}", "error"

    ok, restart_message = ServiceManager().restart_service()
    message = f"Пресет «{preset['name']}» применён"
    if had_standalone:
        message += " · отдельный GameFilter выключен"
    if ok:
        return True, message + ", служба перезапущена", "success"
    return False, message + ", но служба не перезапущена: " + _shorten(restart_message, 200), "warning"


def _preset_append_domains(source: str, target: Path) -> None:
    domains = _preset_read_lines(Path(source))
    if not domains:
        return
    current = _preset_read_lines(target)
    merged = current + [item for item in domains if item not in current]
    _preset_write_lines(target, merged)


def _preset_append_entries(source: str, target: Path) -> None:
    entries = _preset_read_lines(Path(source))
    if not entries:
        return
    current = _preset_read_lines(target)
    merged = current + [item for item in entries if item not in current]
    _preset_write_lines(target, merged)


def clear_game_preset() -> tuple[bool, str, str]:
    """Снимает активный пресет игры."""
    from core.service_manager import ServiceManager

    try:
        _clear_active_game_preset_disk()
    except OSError as exc:
        return False, f"Не удалось снять пресет: {exc}", "error"
    ok, restart_message = ServiceManager().restart_service()
    if ok:
        return True, "Пресет снят, служба перезапущена", "success"
    return False, "Пресет снят, но служба не перезапущена: " + _shorten(restart_message, 200), "warning"


REPORTS_DIR = MANAGER_DIR / "utils" / "reports"

UNLOCK_MARKER = "# Разблокировка сервисов (добавлено Zapret_DPI_Manager)"


def _report_verdict(row: dict, mode: str) -> tuple[str, str]:
    """Вердикт стратегии: (ключ, подпись) — как classify в Tk-окне отчёта."""
    if row.get("error"):
        return "failed", "Ошибка"
    rate = float(row.get("success_rate") or 0)
    youtube = row.get("youtube_passed")
    discord = row.get("discord_passed")
    telegram = row.get("telegram_passed")
    if (mode in ("YouTube/Discord", "mixed")
            or youtube is not None or discord is not None or telegram is not None):
        values = [value for value in (youtube, discord, telegram) if value is not None]
        if not values:
            return "unknown", "Неизвестно"
        if all(value is True for value in values):
            return "working", "Рабочая"
        if all(value is not True for value in values):
            return "failed", "Нерабочая"
        return "partial", "Частичная"
    if rate < 60:
        return "failed", "Нерабочая"
    return "working", "Рабочая"


def list_reports() -> list[dict]:
    """Отчёты автоподбора из utils/reports (*.json), свежие сверху."""
    items: list[dict] = []
    if not REPORTS_DIR.is_dir():
        return items
    for path in sorted(REPORTS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime,
                       reverse=True):
        payload: dict = {}
        try:
            import json

            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        results = payload.get("results") or []
        items.append({
            "path": str(path),
            "name": path.name,
            "generated_at": str(payload.get("generated_at") or ""),
            "mode": str(payload.get("mode") or "standard"),
            "strategies": len(results),
            "best": (results[0].get("name") if results else ""),
        })
    return items


def read_report(path: str) -> dict:
    """Данные одного отчёта для таблицы."""
    import json

    file_path = Path(path)
    if not file_path.is_file():
        return {"error": "Отчёт не найден", "rows": []}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"Не удалось прочитать отчёт: {exc}", "rows": []}
    mode = str(payload.get("mode") or "standard")
    rows = []
    for item in payload.get("results") or []:
        verdict_key, verdict_label = _report_verdict(item, mode)
        rows.append({
            "name": item.get("name") or "—",
            "successRate": float(item.get("success_rate") or 0),
            "successful": int(item.get("successful") or 0),
            "failed": int(item.get("failed") or 0),
            "blocked": int(item.get("blocked") or 0),
            "total": int(item.get("total") or 0),
            "youtube": item.get("youtube_passed"),
            "discord": item.get("discord_passed"),
            "telegram": item.get("telegram_passed"),
            "verdict": verdict_key,
            "verdictLabel": verdict_label,
            "error": str(item.get("error") or ""),
            "targets": [{
                "name": target.get("name") or "—",
                "success": bool(target.get("success")),
                "blocked": bool(target.get("blocked")),
            } for target in (item.get("targets") or [])],
        })
    rows.sort(key=lambda item: item["successRate"], reverse=True)
    return {
        "path": str(file_path),
        "generatedAt": str(payload.get("generated_at") or ""),
        "mode": mode,
        "rows": rows,
    }


def read_info() -> dict:
    """Данные экрана «Информация»."""
    return {
        "version": str(VERSION_CONFIG.get("current_version") or ""),
        "github": GITHUB_BASE_URL,
        "guide": GUIDE_URL,
        "managerDir": str(MANAGER_DIR),
    }


def read_update_versions() -> dict:
    """Версии: приложение, стратегии (SHA Flowseal), движок nfqws."""
    import subprocess

    strategies = ""
    version_file = MANAGER_DIR / "utils" / "strategies_version.txt"
    try:
        strategies = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        strategies = ""
    engine = ""
    import os
    import platform

    machine = os.uname().machine
    binary = MANAGER_DIR / "zapret" / "bins" / machine / "nfqws"
    if binary.is_file():
        try:
            proc = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                                  timeout=5)
            engine = (proc.stderr or proc.stdout or "").strip().splitlines()[0][:60]
        except (subprocess.TimeoutExpired, OSError):
            engine = ""
    elif (MANAGER_DIR / "zapret" / "bins").is_dir():
        engine = f"нет для {machine}"
    return {
        "app": str(VERSION_CONFIG.get("current_version") or ""),
        "strategies": strategies[:12] if strategies else "не обновлялись",
        "engine": engine or "неизвестно",
        "arch": platform.machine(),
    }


def run_update(kind: str, log) -> tuple[bool, str, str]:
    """Обновление приложения, стратегий или движка (в фоновом потоке моста)."""
    kind = (kind or "").strip().lower()
    if kind == "app":
        from core.zapret_updater import ZapretBundleUpdater

        updater = ZapretBundleUpdater()
        version, info, error = updater.check_for_updates_detailed()
        current = str(VERSION_CONFIG.get("current_version") or "")
        if error:
            return False, f"Не удалось проверить обновление: {error}", "error"
        if not version or version == current:
            return True, f"Установлена последняя версия {current}", "success"
        url = (info or {}).get("download_url") or (info or {}).get("url") or ""
        if not url:
            return False, f"Для версии {version} не найден адрес архива", "error"
        log(f"Скачивание {version}…")

        def progress(text: str, percent=None) -> None:
            if percent is None:
                log(str(text))
            else:
                log(f"{text} ({int(percent)}%)")

        ok = updater.update_bundle(url, None, progress_callback=progress)
        if ok:
            return True, f"Приложение обновлено до {version}. Перезапустите менеджер.", "success"
        return False, "Обновление приложения не удалось (см. журнал)", "error"

    if kind == "strategies":
        from core.strategies_updater import FlowsealStrategiesUpdater

        updater = FlowsealStrategiesUpdater(progress_callback=lambda text, *a: log(str(text)))
        result = updater.update(apply=True)
        if isinstance(result, dict) and result.get("updated"):
            return True, "Стратегии обновлены из Flowseal", "success"
        detail = (result or {}).get("message") if isinstance(result, dict) else ""
        return True, detail or "Стратегии уже актуальны", "success"

    if kind == "engine":
        from core.engine_updater import NfqwsUpdater

        updater = NfqwsUpdater(progress_callback=lambda text, *a: log(str(text)))
        result = updater.update(apply=True)
        if isinstance(result, dict) and result.get("error"):
            return False, str(result.get("error")), "error"
        if isinstance(result, dict) and result.get("updated"):
            return True, "Движок nfqws обновлён", "success"
        detail = (result or {}).get("message") if isinstance(result, dict) else ""
        return True, detail or "Движок уже актуален", "success"

    return False, f"Неизвестный тип обновления: {kind}", "error"


def read_unlock_state() -> dict:
    """Категории разблокировки и текущие записи в /etc/hosts."""
    from core.service_data import SERVICE_CATEGORIES, get_category_domains

    hosts_text = ""
    try:
        hosts_text = Path("/etc/hosts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        hosts_text = ""
    present = set()
    for line in hosts_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.strip().startswith("#"):
            present.add(parts[1])
    categories = []
    for category in SERVICE_CATEGORIES:
        domains = get_category_domains(category) or {}
        selected = any(domain in present for domain in domains)
        categories.append({
            "id": category,
            "name": _unlock_category_label(category),
            "count": len(domains),
            "selected": selected,
        })
    return {"categories": categories, "hostsPath": "/etc/hosts"}


_UNLOCK_LABELS = {
    "chatgpt": "ChatGPT", "gemini": "Gemini", "claude": "Claude", "copilot": "Copilot",
    "grok": "Grok", "instagram": "Instagram", "facebook": "Facebook", "telegram": "Telegram",
    "tiktok": "TikTok", "spotify": "Spotify", "rutracker": "Rutracker", "rutor": "Rutor",
    "notion": "Notion", "soundcloud": "SoundCloud", "twitch": "Twitch", "discord": "Discord",
    "other": "Прочее",
}


def _unlock_category_label(category: str) -> str:
    return _UNLOCK_LABELS.get(category, category)


def apply_unlock(selected_ids, log=None) -> tuple[bool, str, str]:
    """Записывает выбранные категории в /etc/hosts (marker-блок, бэкап, атомарно)."""
    import tempfile
    import time

    from core.service_data import SERVICE_CATEGORIES, get_category_domains
    from core.sudo_helper import run_sudo

    selected = [str(item) for item in (selected_ids or []) if str(item) in SERVICE_CATEGORIES]
    our_domains: dict[str, str] = {}
    for category in SERVICE_CATEGORIES:
        our_domains.update(get_category_domains(category) or {})

    code, current, err = run_sudo(["cat", "/etc/hosts"], timeout=30)
    if code != 0:
        return False, f"Не удалось прочитать /etc/hosts: {err.strip()[:200]}", "error"

    kept: list[str] = []
    for line in current.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        parts = stripped.split()
        if (not stripped.startswith("#") and len(parts) >= 2
                and parts[1] in our_domains and parts[0] == our_domains[parts[1]]):
            continue  # наша запись — будет перезаписана
        if stripped == UNLOCK_MARKER:
            continue
        kept.append(line)

    while kept and not kept[-1].strip():
        kept.pop()
    if any(item.strip() == UNLOCK_MARKER for item in kept):
        kept = [item for item in kept if item.strip() != UNLOCK_MARKER]

    lines = list(kept)
    if selected:
        entries = []
        for category in selected:
            for domain, ip in (get_category_domains(category) or {}).items():
                entries.append(f"{ip}\t{domain}")
        if entries:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(UNLOCK_MARKER)
            lines.extend(sorted(entries))

    content = chr(10).join(lines) + chr(10)
    backup = f"/etc/hosts.bak.{time.strftime('%Y%m%d_%H%M%S')}"
    run_sudo(["cp", "/etc/hosts", backup], timeout=30)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as temp:
        temp.write(content)
        temp_path = temp.name
    try:
        code, _out, err = run_sudo(["cp", temp_path, "/etc/hosts.zapret.new"], timeout=30)
        if code == 0:
            code, _out, err = run_sudo(["mv", "/etc/hosts.zapret.new", "/etc/hosts"], timeout=30)
        if code != 0:
            return False, f"Не удалось записать /etc/hosts: {err.strip()[:200]}", "error"
        run_sudo(["chmod", "644", "/etc/hosts"], timeout=30)
        run_sudo(["resolvectl", "flush-caches"], timeout=20)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    count = len(selected)
    if count == 0:
        return True, "Разблокировка снята: записи удалены из /etc/hosts", "success"
    return True, f"Записано категорий: {count} · бэкап {backup}", "success"


TRANSPORT_LABELS = {"quic": "QUIC", "http2": "HTTP/2", "h2": "HTTP/2", "h3": "HTTP/3"}

WARP_TOS_URL = "https://www.cloudflare.com/application/terms/"

WARP_TRANSPORTS = [
    {"label": "Авто (QUIC → HTTP/2)", "value": "auto"},
    {"label": "QUIC", "value": "quic"},
    {"label": "HTTP/2", "value": "http2"},
]

WARP_DNS_CHOICES = [
    {"label": "Регистрировать DNS туннеля (link)", "value": "link"},
    {"label": "Не трогать системный DNS (keep)", "value": "keep"},
]


def build_warp_details() -> dict:
    """Данные окна WARP: движок, аккаунт, состояние, списки диапазонов."""
    from core.warp_manager import (DEFAULT_SNI, WarpManager,
                                   available_ipset_lists, read_state)

    state = read_state()
    details: dict = {
        "state": state,
        "default_sni": DEFAULT_SNI,
        "transports": WARP_TRANSPORTS,
        "dns_choices": WARP_DNS_CHOICES,
        "lists": [],
        "installed": False,
        "installed_version": "",
        "account": False,
        "runtime_outdated": False,
    }
    try:
        manager = WarpManager()
        details.update(
            installed=bool(manager.is_installed()),
            installed_version=manager.installed_version() or "",
            account=bool(manager.account_exists()),
            runtime_outdated=bool(manager.runtime_outdated()),
            lists=available_ipset_lists(),
            status=manager.status(),
        )
    except Exception as exc:  # noqa: BLE001
        details["error"] = str(exc)
    return details


def _transport_label(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    return TRANSPORT_LABELS.get(text.lower(), text)


def build_warp_status() -> dict:
    """Состояние WARP для карточки главного окна (как в Tk-версии)."""
    from core.warp_manager import WarpManager, read_state

    payload = {
        "installed": False, "active": False, "title": "Статус WARP неизвестен",
        "detail": "", "kind": "on_surface_variant",
    }
    try:
        manager = WarpManager()
        installed = bool(manager.is_installed())
        status = manager.status()
        state = read_state()
    except Exception as exc:  # noqa: BLE001
        payload["detail"] = f"Не удалось прочитать состояние: {exc}"
        payload["kind"] = "warning"
        return payload

    iface = state.get("iface") or "—"
    mode_label = "полный туннель" if state.get("mode") != "select" \
        else "только выбранные списки"
    payload["installed"] = installed
    payload["iface"] = iface
    payload["mode"] = mode_label

    if status == "active":
        transport = _transport_label(WarpManager.active_transport())
        endpoint = WarpManager.active_endpoint() or "—"
        payload["active"] = True
        payload["transport"] = transport
        payload["endpoint"] = endpoint
        if WarpManager.tunnel_connected():
            payload.update(
                title=f"WARP активен · {transport} · {iface}",
                detail=f"Туннель: подключён · endpoint: {endpoint} · режим: {mode_label}",
                kind="success",
            )
        elif WarpManager.tunnel_probe_failure():
            payload.update(
                title="WARP подключился, но данные не проходят",
                detail="Самопроверка не прошла: маршруты сняты автоматически, "
                       "интернет идёт напрямую. Попробуйте другой транспорт или включите "
                       "«лечить внешний трафик туннеля через zapret».",
                kind="error",
            )
        else:
            payload.update(
                title=f"WARP активен · {transport} · {iface}",
                detail="Туннель: данные не подтверждены — служба активна, "
                       "но подключение не состоялось.",
                kind="warning",
            )
    elif installed:
        probe_failure = WarpManager.tunnel_probe_failure()
        payload.update(
            title="WARP остановлен",
            detail="Последняя попытка: данные через туннель не проходили — маршруты сняты "
                   "автоматически." if probe_failure
                   else f"Туннель: не подключён · интерфейс: {iface} · режим: {mode_label}",
            kind="error",
        )
    else:
        payload.update(
            title="WARP не установлен",
            detail="Движок usque не установлен: откройте «Настроить» и установите движок.",
            kind="on_surface_variant",
        )
    return payload


def _warp_enable(start_zapret: bool = False) -> tuple[bool, str]:
    """Включение WARP: состояние → скрипты → zapret → автозапуск → старт."""
    from core.warp_manager import WarpManager, read_state

    manager = WarpManager()
    if not manager.is_installed():
        return False, ("движок usque не установлен: откройте окно WARP («Настроить»), "
                       "установите движок и включите WARP снова")
    state = read_state()
    state["enabled"] = "1"
    ok, message = manager.apply_settings(state)
    if not ok:
        return False, message
    if not manager.unit_exists() or manager.runtime_outdated():
        ok, message = manager.install_runtime()
        if not ok:
            return False, message
    if start_zapret:
        ok, message = ServiceManager().start_service()
        if not ok:
            return False, f"не удалось запустить zapret с правилами WARP: {message}"
    else:
        ok, message = manager.restart_zapret_if_active()
        if not ok:
            return False, f"не удалось перезапустить zapret с правилами WARP: {message}"
    ok, message = manager.enable()
    if not ok:
        return False, f"не удалось включить автозапуск warp.service: {message}"
    if manager.status() == "active":
        ok, message = manager.restart()
    else:
        ok, message = manager.start()
    if not ok:
        return False, message
    return True, "WARP включён"


def _warp_disable() -> tuple[bool, str]:
    """Выключение WARP: состояние → остановка → снятие автозапуска → zapret."""
    from core.warp_manager import WarpManager, read_state

    manager = WarpManager()
    state = read_state()
    state["enabled"] = "0"
    ok, message = manager.apply_settings(state)
    if not ok:
        return False, message
    ok, message = manager.stop()
    if not ok:
        return False, message
    ok, message = manager.disable()
    if not ok:
        return False, f"служба остановлена, но автозапуск не отключился: {message}"
    ok, message = manager.restart_zapret_if_active()
    if not ok:
        return False, f"WARP остановлен, но zapret не перезапустился: {message}"
    return True, "WARP выключен"


class Backend(QObject):
    """Состояние приложения и действия над службой zapret."""

    serviceStateChanged = Signal()
    autostartChanged = Signal()
    strategyChanged = Signal()
    busyChanged = Signal()
    messageChanged = Signal()
    strategiesChanged = Signal()
    strategyBusyChanged = Signal()
    customSelectionsChanged = Signal()
    bbdpiChanged = Signal()
    warpChanged = Signal()
    warpBusyChanged = Signal()
    warpDetailsChanged = Signal()
    hostlistChanged = Signal()
    dnsChanged = Signal()
    gameFilterChanged = Signal()
    reportsChanged = Signal()
    reportDataChanged = Signal()
    infoChanged = Signal()
    updateVersionsChanged = Signal()
    unlockChanged = Signal()
    updateLogChanged = Signal()
    updateStateChanged = Signal()
    ipsetChanged = Signal()
    checkLogChanged = Signal()
    checkStateChanged = Signal()
    testerLogChanged = Signal()
    testerStateChanged = Signal()
    warpJournalChanged = Signal()
    warpTraceChanged = Signal()

    # Внутренние сигналы: поднимаются из рабочих потоков, принимаются в главном.
    _statusReady = Signal(str, bool, bool)
    _strategyReady = Signal(str)
    _operationFinished = Signal(str, bool, str)
    _strategiesReady = Signal("QVariantList")
    _applyFinished = Signal(bool, str, str)
    _customSelectionsReady = Signal("QVariantMap")
    _bbdpiReady = Signal("QVariantMap")
    _bbdpiApplyFinished = Signal(bool, str, str)
    _warpReady = Signal("QVariantMap")
    _warpOpFinished = Signal(bool, str, str)
    _warpDetailsReady = Signal("QVariantMap")
    _warpActionResult = Signal(bool, str, str)
    _warpJournalReady = Signal(str)
    _warpTraceReady = Signal(str)
    _hostlistReady = Signal("QVariantMap")
    _hostlistSaved = Signal(bool, str, str)
    _ipsetReady = Signal("QVariantMap")
    _ipsetSaved = Signal(bool, str, str)
    _checkLineReady = Signal(str, str)
    _checkFinished = Signal("QVariantMap")
    _dnsReady = Signal("QVariantMap")
    _dnsSaved = Signal(bool, str, str)
    _gameFilterReady = Signal("QVariantMap")
    _gameFilterSaved = Signal(bool, str, str)
    _reportsReady = Signal("QVariantList")
    _reportReady = Signal("QVariantMap")
    _infoReady = Signal("QVariantMap")
    _versionsReady = Signal("QVariantMap")
    _unlockReady = Signal("QVariantMap")
    _unlockSaved = Signal(bool, str, str)
    _updateLineReady = Signal(str)
    _updateFinished = Signal(bool, str, str)
    _testerLineReady = Signal(str)
    _testerFinished = Signal("QVariantMap")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._log = get_error_logger()
        self._service = ServiceManager()
        self._state = "unknown"
        self._autostart = False
        self._exists = True
        self._strategy = "Загрузка…"
        self._busy = False
        self._strategy_busy = False
        self._strategies: list[dict] = []
        self._custom_selections: dict[str, str] = {}
        self._bbdpi: dict = {}
        self._warp: dict = {}
        self._warp_details: dict = {}
        self._hostlist: dict = {}
        self._dns: dict = {}
        self._reports: list[dict] = []
        self._report: dict = {}
        self._info: dict = {}
        self._update_versions: dict = {}
        self._unlock: dict = {}
        self._update_log: list[dict] = []
        self._update_running = False
        self._update_cancel = threading.Event()
        self._gamefilter: dict = {}
        self._dns_busy = False
        self._gamefilter_busy = False
        self._ipset: dict = {}
        self._ipset_busy = False
        self._check_log: list[dict] = []
        self._check_running = False
        self._check_summary = ""
        self._check_services: dict = {}
        self._check_cancel = threading.Event()
        self._tester_log: list[dict] = []
        self._tester_running = False
        self._tester_best = ""
        self._tester_report = ""
        self._tester_summary = ""
        self._tester_stop = threading.Event()
        self._tester_obj = None
        self._warp_journal = ""
        self._warp_trace = ""
        self._warp_busy = False
        self._hostlist_busy = False
        self._message = ""
        self._message_kind = "info"
        self._threads: list[threading.Thread] = []

        self._statusReady.connect(self._apply_status)
        self._strategyReady.connect(self._apply_strategy)
        self._operationFinished.connect(self._apply_operation)
        self._strategiesReady.connect(self._apply_strategies)
        self._applyFinished.connect(self._apply_strategy_result)
        self._customSelectionsReady.connect(self._apply_custom_selections)
        self._bbdpiReady.connect(self._apply_bbdpi)
        self._bbdpiApplyFinished.connect(self._apply_strategy_result)
        self._warpReady.connect(self._apply_warp)
        self._warpOpFinished.connect(self._apply_warp_result)
        self._warpDetailsReady.connect(self._apply_warp_details)
        self._warpActionResult.connect(self._apply_warp_result)
        self._warpJournalReady.connect(self._apply_warp_journal)
        self._warpTraceReady.connect(self._apply_warp_trace)
        self._hostlistReady.connect(self._apply_hostlist)
        self._hostlistSaved.connect(self._apply_hostlist_saved)
        self._dnsReady.connect(self._apply_dns)
        self._dnsSaved.connect(self._apply_dns_saved)
        self._gameFilterReady.connect(self._apply_gamefilter)
        self._gameFilterSaved.connect(self._apply_gamefilter_saved)
        self._reportsReady.connect(self._apply_reports)
        self._reportReady.connect(self._apply_report)
        self._infoReady.connect(self._apply_info)
        self._versionsReady.connect(self._apply_versions)
        self._unlockReady.connect(self._apply_unlock)
        self._unlockSaved.connect(self._apply_unlock_saved)
        self._updateLineReady.connect(self._apply_update_line)
        self._updateFinished.connect(self._apply_update_finished)
        self._ipsetReady.connect(self._apply_ipset)
        self._ipsetSaved.connect(self._apply_ipset_saved)
        self._checkLineReady.connect(self._apply_check_line)
        self._checkFinished.connect(self._apply_check_finished)
        self._testerLineReady.connect(self._apply_tester_line)
        self._testerFinished.connect(self._apply_tester_finished)

        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        threading.Thread(target=self._poll_once, daemon=True).start()

    # -- свойства -----------------------------------------------------------

    @Property(str, notify=serviceStateChanged)
    def serviceState(self) -> str:
        return self._state

    @Property(bool, notify=serviceStateChanged)
    def serviceActive(self) -> bool:
        return self._state == "active"

    @Property(str, notify=serviceStateChanged)
    def serviceStateLabel(self) -> str:
        return STATE_LABELS.get(self._state, STATE_LABELS["unknown"])

    @Property(bool, notify=serviceStateChanged)
    def serviceExists(self) -> bool:
        return self._exists

    @Property(bool, notify=autostartChanged)
    def autostartEnabled(self) -> bool:
        return self._autostart

    @Property(str, notify=strategyChanged)
    def strategyName(self) -> str:
        return self._strategy

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=messageChanged)
    def message(self) -> str:
        return self._message

    @Property(str, notify=messageChanged)
    def messageKind(self) -> str:
        return self._message_kind

    @Property("QVariantList", notify=strategiesChanged)
    def strategies(self) -> list[dict]:
        return self._strategies

    @Property(bool, notify=strategyBusyChanged)
    def strategyBusy(self) -> bool:
        return self._strategy_busy

    @Property("QVariantMap", notify=bbdpiChanged)
    def bbdpiResult(self) -> dict:
        return self._bbdpi

    @Slot(str, bool)
    def convertBbdpi(self, command: str, useHostlist: bool = False) -> None:
        """Предпросмотр: строка ciadpi → аргументы nfqws + проверка nfqws --dry-run."""
        if self._strategy_busy:
            return
        self._set_strategy_busy(True)
        self._spawn(self._convert_bbdpi_worker, command, bool(useHostlist))

    @Slot(str, str, bool)
    def applyBbdpi(self, name: str, command: str, useHostlist: bool = False) -> None:
        """Сохраняет сконвертированную стратегию в files/strategy и применяет её."""
        if self._strategy_busy:
            return
        self._set_strategy_busy(True)
        self._spawn(self._apply_bbdpi_worker, name, command, bool(useHostlist))

    def _apply_bbdpi(self, payload) -> None:
        data = dict(payload)
        self._bbdpi = data
        self.bbdpiChanged.emit()

    @Property("QVariantMap", notify=warpChanged)
    def warpInfo(self) -> dict:
        return self._warp

    @Property(bool, notify=warpBusyChanged)
    def warpBusy(self) -> bool:
        return self._warp_busy

    @Property("QVariantMap", notify=warpDetailsChanged)
    def warpDetails(self) -> dict:
        return self._warp_details

    @Property(str, notify=warpJournalChanged)
    def warpJournal(self) -> str:
        return self._warp_journal

    @Property(str, notify=warpTraceChanged)
    def warpTrace(self) -> str:
        return self._warp_trace

    @Slot()
    def refreshWarp(self) -> None:
        self._spawn(self._warp_status_worker)

    @Slot(result=bool)
    def warpNeedsZapret(self) -> bool:
        """Нужно ли включить zapret перед подъёмом туннеля (help_tunnel=1)."""
        try:
            from core.warp_manager import read_state

            if str(read_state().get("help_tunnel")) != "1":
                return False
            return self._service.get_service_status() != "active"
        except Exception:  # noqa: BLE001
            return False

    @Slot(bool, bool)
    def setWarpEnabled(self, enabled: bool, startZapret: bool = False) -> None:
        if self._warp_busy:
            return
        self._set_warp_busy(True)
        self._spawn(self._warp_operation_worker, bool(enabled), bool(startZapret))

    def _set_warp_busy(self, value: bool) -> None:
        if self._warp_busy == value:
            return
        self._warp_busy = value
        self.warpBusyChanged.emit()

    def _apply_warp(self, payload) -> None:
        data = dict(payload)
        self._warp = data
        self.warpChanged.emit()

    def _apply_warp_result(self, ok: bool, message: str, kind: str) -> None:
        self._set_warp_busy(False)
        self._set_message(message, kind)
        self.refreshWarp()
        self.refresh()

    def _warp_status_worker(self) -> None:
        self._warpReady.emit(build_warp_status())

    def _warp_operation_worker(self, enabled: bool, start_zapret: bool) -> None:
        try:
            if enabled:
                ok, message = _warp_enable(start_zapret)
            else:
                ok, message = _warp_disable()
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка переключения WARP")
            ok, message = False, str(exc)
        kind = "success" if ok else "error"
        text = f"WARP {'включён' if enabled else 'выключен'}" if ok \
            else f"Не удалось изменить состояние WARP: {_shorten(message, 300)}"
        self._warpOpFinished.emit(bool(ok), text, kind)

    @Slot()
    def refreshWarpDetails(self) -> None:
        """Локальные данные окна WARP: версии, аккаунт, состояние, списки."""
        self._spawn(lambda: self._warpDetailsReady.emit(build_warp_details()))

    @Slot()
    def refreshWarpUpstream(self) -> None:
        """Последняя версия usque на GitHub (сеть, в фоне)."""
        if self._warp_busy:
            return
        self._set_warp_busy(True)
        self._spawn(self._warp_upstream_worker)

    def _warp_upstream_worker(self) -> None:
        from core.warp_manager import WarpManager

        manager = WarpManager()
        details = dict(self._warp_details)
        try:
            details["upstream_version"] = manager.upstream_version() or ""
            details["upstream_error"] = manager.release_error() or ""
        except Exception as exc:  # noqa: BLE001
            details["upstream_error"] = str(exc)
        self._warpDetailsReady.emit(details)
        self._set_warp_busy(False)

    @Slot()
    def loadWarpJournal(self) -> None:
        from core.warp_manager import WarpManager

        self._spawn(lambda: self._warpJournalReady.emit(
            WarpManager().journal_tail(80)))

    @Slot()
    def checkWarpExternalIp(self) -> None:
        from core.warp_manager import WarpManager

        def worker() -> None:
            data = WarpManager.trace_info()
            if not data:
                text = "Cloudflare trace недоступен"
            else:
                text = (f"Внешний адрес: {data.get('ip', '—')} · "
                        f"warp={data.get('warp', '—')} · loc={data.get('loc', '—')}")
            self._warpTraceReady.emit(text)

        self._spawn(worker)

    @Slot()
    def installWarpEngine(self) -> None:
        self._run_warp_action("Установка движка usque…",
                              lambda m: m.install_usque())

    @Slot()
    def updateWarpEngine(self) -> None:
        self._run_warp_action("Обновление движка usque…",
                              lambda m: m.update_usque())

    @Slot(bool)
    def registerWarpAccount(self, accept_tos: bool) -> None:
        if not accept_tos:
            self._set_message("Регистрация возможна только после согласия с условиями "
                              "Cloudflare WARP", "warning")
            return
        self._run_warp_action("Регистрация аккаунта Cloudflare WARP…",
                              lambda m: m.register_account(True))

    @Slot()
    def deleteWarpAccount(self) -> None:
        self._run_warp_action("Удаление аккаунта WARP…", lambda m: m.delete_account())

    @Slot()
    def warpEmergencyCleanup(self) -> None:
        self._run_warp_action("Аварийная очистка состояния WARP…",
                              lambda m: m.emergency_cleanup())

    @Slot()
    def uninstallWarp(self) -> None:
        self._run_warp_action("Удаление подсистемы WARP…", lambda m: m.uninstall())

    @Slot()
    def openWarpTos(self) -> None:
        try:
            webbrowser.open(WARP_TOS_URL)
            self._set_message("Открываю условия Cloudflare WARP…", "success")
        except Exception as exc:  # noqa: BLE001
            self._set_message(f"Не удалось открыть условия: {exc}", "error")

    @Slot("QVariantMap")
    def saveWarpSettings(self, values) -> None:
        """Сохраняет настройки туннеля в utils/warp.txt и обновляет правила zapret."""
        if self._warp_busy:
            return
        data = {str(k): str(v) for k, v in dict(values).items()}
        self._set_warp_busy(True)
        self._spawn(self._save_warp_settings_worker, data)

    def _save_warp_settings_worker(self, values: dict) -> None:
        from core.warp_manager import DEFAULT_SNI, read_state, validate_state

        try:
            state = read_state()
            state["mode"] = values.get("mode") or "full"
            state["transport"] = values.get("transport") or "auto"
            state["sni"] = (values.get("sni") or "").strip() or DEFAULT_SNI
            state["mtu"] = (values.get("mtu") or "").strip()
            state["dns"] = values.get("dns") or "link"
            state["help_tunnel"] = "1" if str(values.get("help_tunnel", "1")) == "1" else "0"
            state["lists"] = values.get("lists") or ""
            state["exclude"] = values.get("exclude") or ""
            state["uids"] = (values.get("uids") or "").strip()

            if state["mode"] == "select" and not state["lists"] and not state["uids"]:
                self._warpActionResult.emit(
                    False,
                    "Селективный режим: отметьте хотя бы один список или укажите uidrange",
                    "warning")
                return

            problems = validate_state(state)
            if problems:
                self._warpActionResult.emit(
                    False, "Настройки не сохранены: " + "; ".join(problems), "error")
                return

            from core.warp_manager import WarpManager

            manager = WarpManager()
            ok, message = manager.apply_settings(state)
            if not ok:
                self._warpActionResult.emit(False, message, "error")
                return
            ok, message = manager.restart_zapret_if_active()
            if ok:
                self._warpActionResult.emit(True, f"Настройки сохранены · {message}", "success")
            else:
                self._warpActionResult.emit(
                    False, f"Настройки сохранены, но zapret не перезапустился: {message}",
                    "warning")
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка сохранения настроек WARP")
            self._warpActionResult.emit(False, f"Ошибка сохранения настроек WARP: {exc}", "error")

    def _run_warp_action(self, status: str, func) -> None:
        if self._warp_busy:
            return
        self._set_warp_busy(True)
        self._set_message(status, "info")

        def worker() -> None:
            from core.warp_manager import WarpManager

            try:
                ok, message = func(WarpManager())
            except Exception as exc:  # noqa: BLE001
                self._log.exception("Операция WARP не удалась")
                ok, message = False, str(exc)
            self._warpActionResult.emit(bool(ok), str(message or ""),
                                        "success" if ok else "error")
            self._warpDetailsReady.emit(build_warp_details())

        self._spawn(worker)

    def _apply_warp_details(self, payload) -> None:
        data = dict(payload)
        self._warp_details = data
        self.warpDetailsChanged.emit()

    def _apply_warp_journal(self, text: str) -> None:
        self._warp_journal = str(text or "")
        self.warpJournalChanged.emit()

    def _apply_warp_trace(self, text: str) -> None:
        self._warp_trace = str(text or "")
        self.warpTraceChanged.emit()

    @Property("QVariantMap", notify=hostlistChanged)
    def hostlistState(self) -> dict:
        return self._hostlist

    @Property("QVariantMap", notify=ipsetChanged)
    def ipsetState(self) -> dict:
        return self._ipset

    @Property("QVariantMap", notify=dnsChanged)
    def dnsState(self) -> dict:
        return self._dns

    @Property("QVariantMap", notify=gameFilterChanged)
    def gameFilterState(self) -> dict:
        return self._gamefilter

    @Slot()
    def refreshDns(self) -> None:
        self._spawn(lambda: self._dnsReady.emit(read_dns_state()))

    @Slot(str)
    def applyDns(self, servers: str) -> None:
        if self._dns_busy:
            return
        self._dns_busy = True
        self._spawn(self._dns_worker, str(servers), False)

    @Slot()
    def resetDns(self) -> None:
        if self._dns_busy:
            return
        self._dns_busy = True
        self._spawn(self._dns_worker, "", True)

    def _dns_worker(self, servers: str, reset: bool) -> None:
        try:
            if reset:
                ok, message, kind = reset_dns()
            else:
                ok, message, kind = apply_dns(servers)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка DNS")
            ok, message, kind = False, f"Не удалось изменить DNS: {exc}", "error"
        self._dns_busy = False
        self._dnsSaved.emit(ok, message, kind)

    def _apply_dns(self, payload) -> None:
        self._dns = dict(payload)
        self.dnsChanged.emit()

    def _apply_dns_saved(self, ok: bool, message: str, kind: str) -> None:
        self._set_message(message, kind)
        self.refreshDns()

    @Slot()
    def refreshGameFilter(self) -> None:
        self._spawn(lambda: self._gameFilterReady.emit(read_gamefilter_state()))

    @Slot(bool, str)
    def setGameFilter(self, enabled: bool, mode: str) -> None:
        if self._gamefilter_busy:
            return
        self._gamefilter_busy = True
        self._spawn(self._gamefilter_worker, bool(enabled), str(mode or "both"))

    @Slot(str)
    def applyGamePreset(self, preset_id: str) -> None:
        if self._gamefilter_busy:
            return
        self._gamefilter_busy = True
        self._spawn(self._gamepreset_worker, str(preset_id))

    @Slot()
    def clearGamePreset(self) -> None:
        if self._gamefilter_busy:
            return
        self._gamefilter_busy = True
        self._spawn(self._gamepreset_clear_worker)

    def _gamefilter_worker(self, enabled: bool, mode: str) -> None:
        try:
            ok, message, kind = set_gamefilter(enabled, mode)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка GameFilter")
            ok, message, kind = False, f"Не удалось изменить GameFilter: {exc}", "error"
        self._gamefilter_busy = False
        self._gameFilterSaved.emit(ok, message, kind)

    def _gamepreset_worker(self, preset_id: str) -> None:
        try:
            ok, message, kind = apply_game_preset(preset_id)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка применения пресета игры")
            ok, message, kind = False, f"Не удалось применить пресет: {exc}", "error"
        self._gamefilter_busy = False
        self._gameFilterSaved.emit(ok, message, kind)

    def _gamepreset_clear_worker(self) -> None:
        try:
            ok, message, kind = clear_game_preset()
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка снятия пресета игры")
            ok, message, kind = False, f"Не удалось снять пресет: {exc}", "error"
        self._gamefilter_busy = False
        self._gameFilterSaved.emit(ok, message, kind)

    def _apply_gamefilter(self, payload) -> None:
        self._gamefilter = dict(payload)
        self.gameFilterChanged.emit()

    def _apply_gamefilter_saved(self, ok: bool, message: str, kind: str) -> None:
        self._set_message(message, kind)
        self.refreshGameFilter()

    @Property("QVariantList", notify=reportsChanged)
    def reports(self) -> list[dict]:
        return self._reports

    @Property("QVariantMap", notify=reportDataChanged)
    def reportData(self) -> dict:
        return self._report

    @Property("QVariantMap", notify=infoChanged)
    def info(self) -> dict:
        return self._info

    @Property("QVariantMap", notify=updateVersionsChanged)
    def updateVersions(self) -> dict:
        return self._update_versions

    @Property(bool, notify=updateStateChanged)
    def updateRunning(self) -> bool:
        return self._update_running

    @Property("QVariantList", notify=updateLogChanged)
    def updateLog(self) -> list[dict]:
        return self._update_log

    @Property("QVariantMap", notify=unlockChanged)
    def unlockState(self) -> dict:
        return self._unlock

    @Slot()
    def refreshReports(self) -> None:
        self._spawn(lambda: self._reportsReady.emit(list_reports()))

    @Slot(str)
    def loadReport(self, path: str) -> None:
        self._spawn(lambda: self._reportReady.emit(read_report(str(path))))

    @Slot()
    def refreshInfo(self) -> None:
        self._infoReady.emit(read_info())

    @Slot()
    def refreshUpdateVersions(self) -> None:
        self._spawn(lambda: self._versionsReady.emit(read_update_versions()))

    @Slot(str)
    def startUpdate(self, kind: str) -> None:
        if self._update_running:
            return
        self._update_running = True
        self._update_log = []
        self.updateStateChanged.emit()
        self.updateLogChanged.emit()
        self._spawn(self._update_worker, str(kind))

    @Slot(str)
    def cancelUpdate(self) -> None:
        self._update_cancel.set()

    @Slot()
    def refreshUnlock(self) -> None:
        self._spawn(lambda: self._unlockReady.emit(read_unlock_state()))

    @Slot("QVariantList")
    def saveUnlock(self, selected) -> None:
        ids = [str(item) for item in (selected or [])]
        self._spawn(self._unlock_worker, ids)

    def _update_worker(self, kind: str) -> None:
        self._update_cancel.clear()

        def log(text: str) -> None:
            self._updateLineReady.emit(str(text))

        try:
            ok, message, level = run_update(kind, log)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка обновления")
            ok, message, level = False, f"Ошибка обновления: {exc}", "error"
        self._update_running = False
        self._updateFinished.emit(ok, message, level)

    def _unlock_worker(self, ids: list[str]) -> None:
        try:
            ok, message, level = apply_unlock(ids)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка разблокировки сервисов")
            ok, message, level = False, f"Не удалось обновить /etc/hosts: {exc}", "error"
        self._unlockSaved.emit(ok, message, level)

    def _apply_reports(self, payload) -> None:
        self._reports = [dict(item) for item in (payload or [])]
        self.reportsChanged.emit()

    def _apply_report(self, payload) -> None:
        self._report = dict(payload)
        self.reportDataChanged.emit()

    def _apply_info(self, payload) -> None:
        self._info = dict(payload)
        self.infoChanged.emit()

    def _apply_versions(self, payload) -> None:
        self._update_versions = dict(payload)
        self.updateVersionsChanged.emit()

    def _apply_unlock(self, payload) -> None:
        self._unlock = dict(payload)
        self.unlockChanged.emit()

    def _apply_unlock_saved(self, ok: bool, message: str, level: str) -> None:
        self._set_message(message, level)
        self.refreshUnlock()

    def _apply_update_line(self, text: str) -> None:
        line = str(text or "").strip()
        if line:
            self._update_log = self._update_log + [{"text": line}]
            self.updateLogChanged.emit()

    def _apply_update_finished(self, ok: bool, message: str, level: str) -> None:
        self._update_running = False
        self.updateStateChanged.emit()
        self._set_message(message, level)
        self.refreshUpdateVersions()

    @Property("QVariantList", notify=testerLogChanged)
    def testerLog(self) -> list[dict]:
        return self._tester_log

    @Property(bool, notify=testerStateChanged)
    def testerRunning(self) -> bool:
        return self._tester_running

    @Property(str, notify=testerStateChanged)
    def testerBest(self) -> str:
        return self._tester_best

    @Property(str, notify=testerStateChanged)
    def testerReport(self) -> str:
        return self._tester_report

    @Property(str, notify=testerStateChanged)
    def testerSummary(self) -> str:
        return self._tester_summary

    @Property("QVariantList", constant=True)
    def testerModes(self) -> list[dict]:
        return [
            {"label": "Стандартный (YouTube + Discord + Telegram)", "value": "standard"},
            {"label": "Только YouTube/Discord/Telegram", "value": "YouTube/Discord"},
        ]

    @Slot(str, "QVariantList")
    def startStrategyTest(self, mode: str, strategies) -> None:
        if self._tester_running:
            return
        names = [str(item) for item in (strategies or [])]
        self._tester_stop.clear()
        self._tester_log = []
        self._tester_best = ""
        self._tester_report = ""
        self._tester_summary = ""
        self._tester_running = True
        self.testerStateChanged.emit()
        self.testerLogChanged.emit()
        self._spawn(self._strategy_test_worker, str(mode or "standard"), names)

    @Slot()
    def stopStrategyTest(self) -> None:
        self._tester_stop.set()
        if self._tester_obj is not None:
            try:
                self._tester_obj.stop_testing()
            except Exception:  # noqa: BLE001
                pass

    @Slot(str)
    def applyBestStrategy(self, name: str) -> None:
        from core.strategy_data import reapply_active_preset_to_config

        if not name:
            self._set_message("Лучшая стратегия ещё не определена", "warning")
            return
        path = MANAGER_DIR / "files" / "strategy" / name
        if not path.is_file():
            self._set_message(f"Файл стратегии не найден: {name}", "error")
            return
        try:
            content = path.read_text(encoding="utf-8")
            if len(content.strip()) < 10:
                self._set_message(f"Файл стратегии пустой: {name}", "error")
                return
            suffix = "" if content.endswith(chr(10)) else chr(10)
            CONFIG_FILE.write_text(content + suffix, encoding="utf-8")
            (MANAGER_DIR / "utils" / "name_strategy.txt").write_text(name, encoding="utf-8")
            reapply_active_preset_to_config(str(MANAGER_DIR))
        except OSError as exc:
            self._set_message(f"Не удалось применить стратегию: {exc}", "error")
            return
        self._set_message(f"Стратегия «{name}» записана в config.txt", "success")
        self.refresh()

    def _apply_tester_line(self, text: str) -> None:
        line = str(text or "").rstrip()
        if not line:
            return
        kind = "on_surface"
        if "❌" in line or "ОШИБКА" in line:
            kind = "error"
        elif "⚠" in line:
            kind = "warning"
        elif "✅" in line:
            kind = "success"
        elif line.startswith("🎯") or line.startswith("📋") or line.startswith("🚀"):
            kind = "primary"
        self._tester_log = self._tester_log + [{"text": line, "kind": kind}]
        self.testerLogChanged.emit()

    def _apply_tester_finished(self, payload) -> None:
        data = dict(payload)
        self._tester_running = False
        self._tester_best = str(data.get("best") or "")
        self._tester_report = str(data.get("report") or "")
        self._tester_summary = str(data.get("summary") or "")
        self.testerStateChanged.emit()
        if self._tester_best:
            self._set_message(f"Автоподбор завершён · лучшая: {self._tester_best}", "success")
        else:
            self._set_message(self._tester_summary or "Автоподбор завершён без результата",
                              "warning")

    def _strategy_test_worker(self, mode: str, strategies: list[str]) -> None:
        import asyncio
        import sys

        from core.strategy_tester import StrategyTester

        class _Redirect:
            def __init__(self, emit) -> None:
                self._emit = emit

            def write(self, text: str) -> None:
                for piece in str(text).splitlines():
                    if piece.strip():
                        self._emit(piece)

            def flush(self) -> None:
                pass

        old_stdout = sys.stdout
        report = ""
        best = ""
        summary = ""
        try:
            tester = StrategyTester(str(MANAGER_DIR))
            self._tester_obj = tester
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            sys.stdout = _Redirect(self._testerLineReady.emit)
            results = loop.run_until_complete(
                tester.run_full_test(mode, strategies or None,
                                     stop_callback=lambda: self._tester_stop.is_set()))
            sys.stdout = old_stdout
            if results:
                best = _pick_best_strategy(results, mode)
                try:
                    report = tester.generate_report(results) or ""
                except Exception as exc:  # noqa: BLE001
                    report = ""
                    self._testerLineReady.emit(f"⚠️ Отчёт не сформирован: {exc}")
                summary = f"Проверено стратегий: {len(results)}" + (
                    f" · лучшая: {best}" if best else " · рабочей не найдено")
            else:
                summary = ("Тестирование остановлено" if self._tester_stop.is_set()
                           else "Стратегии не найдены")
        except Exception as exc:  # noqa: BLE001
            sys.stdout = old_stdout
            self._log.exception("Автоподбор стратегий не удался")
            summary = f"Ошибка автоподбора: {exc}"
        finally:
            sys.stdout = old_stdout
            self._tester_obj = None
        self._testerFinished.emit({"best": best, "report": report, "summary": summary})

    @Property("QVariantList", notify=checkLogChanged)
    def checkLog(self) -> list[dict]:
        return self._check_log

    @Property(bool, notify=checkStateChanged)
    def checkRunning(self) -> bool:
        return self._check_running

    @Property(str, notify=checkStateChanged)
    def checkSummary(self) -> str:
        return self._check_summary

    @Property("QVariantMap", notify=checkStateChanged)
    def checkServices(self) -> dict:
        return self._check_services

    @Slot()
    def startConnectionCheck(self) -> None:
        if self._check_running:
            return
        self._check_cancel.clear()
        self._check_log = []
        self._check_summary = ""
        self._check_services = {}
        self._check_running = True
        self.checkStateChanged.emit()
        self.checkLogChanged.emit()
        self._spawn(self._connection_check_worker)

    @Slot()
    def stopConnectionCheck(self) -> None:
        self._check_cancel.set()

    @Slot()
    def clearConnectionCheck(self) -> None:
        self._check_log = []
        self._check_summary = ""
        self._check_services = {}
        self.checkLogChanged.emit()
        self.checkStateChanged.emit()

    def _apply_check_line(self, text: str, kind: str) -> None:
        self._check_log = self._check_log + [{"text": text, "kind": kind}]
        self.checkLogChanged.emit()

    def _apply_check_finished(self, payload) -> None:
        data = dict(payload)
        self._check_running = False
        self._check_summary = str(data.get("summary") or "")
        self._check_services = dict(data.get("services") or {})
        self.checkStateChanged.emit()

    def _connection_check_worker(self) -> None:
        result = run_connection_checks(self._check_cancel,
                                       lambda text, kind: self._checkLineReady.emit(text, kind))
        self._checkFinished.emit(result)

    @Slot()
    def refreshIpset(self) -> None:
        self._spawn(lambda: self._ipsetReady.emit(read_ipset_state()))

    @Slot("QVariantMap")
    def saveIpset(self, values) -> None:
        if self._ipset_busy:
            return
        data = {str(k): str(v) for k, v in dict(values).items()}
        self._ipset_busy = True
        self._spawn(self._save_ipset_worker, data)

    @Slot(str)
    def applyIpsetFilter(self, mode: str) -> None:
        if self._ipset_busy:
            return
        self._ipset_busy = True
        self._spawn(self._apply_ipset_filter_worker, str(mode))

    def _save_ipset_worker(self, values: dict) -> None:
        try:
            ok, message, kind = save_ipset(values)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка сохранения IPSet")
            ok, message, kind = False, f"Не удалось сохранить диапазоны: {exc}", "error"
        self._ipset_busy = False
        self._check_log: list[dict] = []
        self._check_running = False
        self._check_summary = ""
        self._check_services: dict = {}
        self._check_cancel = threading.Event()
        self._tester_log: list[dict] = []
        self._tester_running = False
        self._tester_best = ""
        self._tester_report = ""
        self._tester_summary = ""
        self._tester_stop = threading.Event()
        self._tester_obj = None
        self._ipsetSaved.emit(ok, message, kind)

    def _apply_ipset_filter_worker(self, mode: str) -> None:
        try:
            ok, message, kind = apply_ipset_filter(mode)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка применения режима IPSet")
            ok, message, kind = False, f"Ошибка применения режима: {exc}", "error"
        self._ipset_busy = False
        self._check_log: list[dict] = []
        self._check_running = False
        self._check_summary = ""
        self._check_services: dict = {}
        self._check_cancel = threading.Event()
        self._tester_log: list[dict] = []
        self._tester_running = False
        self._tester_best = ""
        self._tester_report = ""
        self._tester_summary = ""
        self._tester_stop = threading.Event()
        self._tester_obj = None
        self._ipsetSaved.emit(ok, message, kind)

    def _apply_ipset(self, payload) -> None:
        data = dict(payload)
        self._ipset = data
        self.ipsetChanged.emit()

    def _apply_ipset_saved(self, ok: bool, message: str, kind: str) -> None:
        self._set_message(message, kind)
        self.refreshIpset()

    @Slot()
    def refreshHostlist(self) -> None:
        self._spawn(lambda: self._hostlistReady.emit(read_hostlist_state()))

    @Slot("QVariantMap")
    def saveHostlist(self, values) -> None:
        if self._hostlist_busy:
            return
        data = {str(k): v for k, v in dict(values).items()}
        self._hostlist_busy = True
        self._spawn(self._save_hostlist_worker, data)

    def _save_hostlist_worker(self, values: dict) -> None:
        try:
            ok, message, kind = save_hostlist(values)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка сохранения hostlist")
            ok, message, kind = False, f"Не удалось сохранить домены: {exc}", "error"
        self._hostlist_busy = False
        self._hostlistSaved.emit(ok, message, kind)

    def _apply_hostlist(self, payload) -> None:
        data = dict(payload)
        self._hostlist = data
        self.hostlistChanged.emit()

    def _apply_hostlist_saved(self, ok: bool, message: str, kind: str) -> None:
        self._set_message(message, kind)
        self.refreshHostlist()

    @Property("QVariantList", constant=True)
    def strategyCategories(self) -> list[dict]:
        from core.strategy_data import STRATEGY_OPTIONS

        return [
            {"name": category, "options": list(options.keys())}
            for category, options in STRATEGY_OPTIONS.items()
        ]

    @Property("QVariantMap", notify=customSelectionsChanged)
    def customSelections(self) -> dict:
        return self._custom_selections

    @Slot(str, str, result=str)
    def commandFor(self, category: str, option: str) -> str:
        from core.strategy_data import get_strategy_command

        try:
            return get_strategy_command(category, option) or ""
        except Exception:  # noqa: BLE001
            return ""

    @Slot("QVariantMap")
    def applyCustomStrategy(self, selections) -> None:
        """Сохраняет выбранные варианты (chosen_strategies.txt, config.txt) и перезапускает службу."""
        if self._strategy_busy:
            return
        data = {str(key): str(value) for key, value in dict(selections).items()}
        self._set_strategy_busy(True)
        self._spawn(self._apply_custom_worker, data)

    @Slot()
    def reloadCustomSelections(self) -> None:
        self._spawn(
            lambda: self._customSelectionsReady.emit(
                {str(k): str(v) for k, v in dict(load_strategy_names()).items()}
            )
        )

    def _apply_custom_selections(self, selections) -> None:
        data = {str(key): str(value) for key, value in dict(selections).items()}
        if data == self._custom_selections:
            return
        self._custom_selections = data
        self.customSelectionsChanged.emit()

    def _apply_custom_worker(self, selections: dict) -> None:
        try:
            ok, message, kind = self._apply_custom_blocking(selections)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка применения своей стратегии")
            ok, message, kind = False, f"Ошибка применения стратегии: {exc}", "error"
        self._applyFinished.emit(ok, message, kind)

    def _apply_custom_blocking(self, selections: dict) -> tuple[bool, str, str]:
        from core.strategy_data import save_strategy_names

        chosen = {k: v for k, v in selections.items() if v and v != "Не выбирать"}
        if not chosen:
            return False, "Не выбрано ни одной категории", "warning"
        if not sudo_available():
            return False, "Для применения стратегии требуется sudo (нет askpass-хелпера)", "error"
        if not save_strategy_names(chosen):
            return False, "Не удалось сохранить стратегию в config.txt", "error"
        self._customSelectionsReady.emit(selections)
        self._strategyReady.emit(read_strategy_name())
        ok, message = self._service.restart_service()
        if ok:
            return True, "Своя стратегия применена, служба перезапущена", "success"
        return False, f"Стратегия применена, но служба не перезапущена: {_shorten(message)}", "warning"

    @Property(bool, constant=True)
    def sudoAvailable(self) -> bool:
        return sudo_available()

    @Property(str, constant=True)
    def version(self) -> str:
        return VERSION_CONFIG["current_version"]

    @Property(str, constant=True)
    def managerDir(self) -> str:
        return str(MANAGER_DIR)

    # -- внутренние помощники ----------------------------------------------

    def _set_busy(self, value: bool) -> None:
        if self._busy == value:
            return
        self._busy = value
        self.busyChanged.emit()

    def _set_message(self, text: str, kind: str = "info") -> None:
        self._message = text
        self._message_kind = kind
        self.messageChanged.emit()

    def _apply_status(self, state: str, autostart: bool, exists: bool) -> None:
        changed = (state, autostart, exists) != (self._state, self._autostart, self._exists)
        self._state = state
        self._autostart = autostart
        self._exists = exists
        if changed:
            self.serviceStateChanged.emit()
            self.autostartChanged.emit()

    def _apply_strategy(self, name: str) -> None:
        if name == self._strategy:
            return
        self._strategy = name
        self.strategyChanged.emit()

    def _apply_strategies(self, strategies) -> None:
        data = [dict(item) for item in strategies]
        if data == self._strategies:
            return
        self._strategies = data
        self.strategiesChanged.emit()

    def _set_strategy_busy(self, value: bool) -> None:
        if self._strategy_busy == value:
            return
        self._strategy_busy = value
        self.strategyBusyChanged.emit()

    def _apply_strategy_result(self, ok: bool, message: str, kind: str) -> None:
        self._set_strategy_busy(False)
        self._set_message(message, kind)
        self.refresh()
        self.refreshStrategies()

    def _apply_operation(self, action: str, ok: bool, message: str) -> None:
        self._set_busy(False)
        if ok:
            labels = {
                "start": "Служба Zapret запущена",
                "stop": "Служба Zapret остановлена",
                "restart": "Служба Zapret перезапущена",
                "autostart-on": "Автозапуск включён",
                "autostart-off": "Автозапуск отключён",
            }
            self._set_message(labels.get(action, "Готово"), "success")
        else:
            self._set_message(_shorten(message) or "Операция не выполнена", "error")
        self.refresh()

    def _spawn(self, target, *args) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]

    def _poll_once(self) -> None:
        """Чтение статуса службы и автозапуска (без sudo)."""
        try:
            state = self._service.get_service_status()
            autostart = self._service.get_autostart_status()
            exists = self._service.check_service_exists()
        except Exception as exc:  # noqa: BLE001 - статус не должен ронять UI
            self._log.exception("Не удалось прочитать статус службы")
            self._statusReady.emit("unknown", self._autostart, self._exists)
            self._set_message(f"Не удалось прочитать статус службы: {exc}", "error")
            return
        self._statusReady.emit(state, bool(autostart), bool(exists))
        self._strategyReady.emit(read_strategy_name())
        self._warpReady.emit(build_warp_status())

    def _run_operation(self, action: str) -> None:
        self._set_busy(True)
        self._spawn(self._operation_worker, action)

    def _operation_worker(self, action: str) -> None:
        try:
            if action == "start":
                ok, message = self._service.start_service()
            elif action == "stop":
                ok, message = self._service.stop_service()
            elif action == "restart":
                ok, message = self._service.restart_service()
            elif action in ("autostart-on", "autostart-off"):
                ok, message = (
                    self._service.enable_autostart()
                    if action == "autostart-on"
                    else self._service.disable_autostart()
                )
            else:
                ok, message = False, f"неизвестное действие: {action}"
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка операции со службой: %s", action)
            ok, message = False, str(exc)
        self._operationFinished.emit(action, bool(ok), str(message or ""))

    # -- слоты для QML ------------------------------------------------------

    @Slot()
    def refresh(self) -> None:
        if self._busy:
            return
        self._spawn(self._poll_once)

    @Slot()
    def reloadStrategy(self) -> None:
        self._spawn(lambda: self._strategyReady.emit(read_strategy_name()))

    @Slot()
    def refreshStrategies(self) -> None:
        self._spawn(lambda: self._strategiesReady.emit(read_strategies()))

    @Slot(str)
    def applyStrategy(self, name: str) -> None:
        """Применяет стратегию: config.txt, name_strategy.txt, пресет, перезапуск."""
        if self._strategy_busy or not name:
            return
        self._set_strategy_busy(True)
        self._spawn(self._apply_strategy_worker, name)

    def _apply_strategy_worker(self, name: str) -> None:
        try:
            ok, message, kind = self._apply_strategy_blocking(name)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка применения стратегии: %s", name)
            ok, message, kind = False, f"Ошибка применения стратегии: {exc}", "error"
        self._applyFinished.emit(ok, message, kind)

    def _apply_strategy_blocking(self, name: str) -> tuple[bool, str, str]:
        strategy_file = STRATEGY_DIR / name
        if not strategy_file.is_file():
            return False, f"Файл стратегии не найден: {name}", "error"
        if name == read_strategy_name():
            return False, "Эта стратегия уже активна", "warning"
        if not sudo_available():
            return False, "Для применения стратегии требуется sudo (нет askpass-хелпера)", "error"

        content = strategy_file.read_text(encoding="utf-8").strip()
        NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not content:
            NAME_FILE.write_text("", encoding="utf-8")
            CONFIG_FILE.write_text("", encoding="utf-8")
            applied = "Не выбрано"
        else:
            NAME_FILE.write_text(name, encoding="utf-8")
            CONFIG_FILE.write_text(content + "\n", encoding="utf-8")
            try:
                from core.game_presets import reapply_active_preset_to_config

                reapply_active_preset_to_config(str(MANAGER_DIR))
            except Exception:  # noqa: BLE001 - пресет не должен срывать применение
                self._log.exception("Не удалось применить игровой пресет к config.txt")
            applied = name

        self._strategyReady.emit(applied)
        ok, message = self._service.restart_service()
        if ok:
            return True, f"Стратегия «{applied}» применена, служба перезапущена", "success"
        return (
            False,
            f"Стратегия «{applied}» применена, но служба не перезапущена: {_shorten(message)}",
            "warning",
        )

    @Slot()
    def toggleService(self) -> None:
        if self._busy:
            return
        self._run_operation("stop" if self._state == "active" else "start")

    @Slot()
    def startService(self) -> None:
        if not self._busy:
            self._run_operation("start")

    @Slot()
    def stopService(self) -> None:
        if not self._busy:
            self._run_operation("stop")

    @Slot()
    def restartService(self) -> None:
        if not self._busy:
            self._run_operation("restart")

    @Slot()
    def toggleAutostart(self) -> None:
        if self._busy:
            return
        self._run_operation("autostart-off" if self._autostart else "autostart-on")

    @Slot(str, str)
    def showMessage(self, text: str, kind: str = "info") -> None:
        self._set_message(text, kind or "info")

    @Slot()
    def openUserGuide(self) -> None:
        try:
            webbrowser.open(GUIDE_URL)
            self._set_message("Открываю руководство пользователя…", "success")
        except Exception as exc:  # noqa: BLE001
            self._set_message(f"Не удалось открыть руководство: {exc}", "error")

    @Slot()
    def forgetSudoPassword(self) -> None:
        from core.sudo_helper import forget_cached_password

        if forget_cached_password():
            self._set_message("Сохранённый пароль sudo удалён", "success")
        else:
            self._set_message("Сохранённого пароля sudo нет", "info")

    @Slot()
    def quit(self) -> None:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is not None:
            app.quit()

    # -- BB DPI (ciadpi → nfqws) -------------------------------------------

    def _convert_bbdpi_worker(self, command: str, use_hostlist: bool) -> None:
        from core.bbdpi_convert import convert, dry_run

        payload: dict = {
            "command": command, "hostlist": use_hostlist, "ok": False,
            "text": "", "rules": [], "unsupported": [], "warnings": [], "problems": [],
        }
        try:
            result = convert(command, use_hostlist=use_hostlist)
            payload.update(
                ok=bool(result.ok),
                text=result.text,
                rules=list(result.rules),
                unsupported=list(result.unsupported),
                warnings=list(result.warnings),
            )
            if result.ok:
                payload["problems"] = list(dry_run(list(result.rules)))
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка конвертации BB DPI")
            payload["warnings"] = [f"Ошибка конвертации: {exc}"]
        self._bbdpiReady.emit(payload)
        self._set_strategy_busy(False)

    def _apply_bbdpi_worker(self, name: str, command: str, use_hostlist: bool) -> None:
        from core.bbdpi_convert import convert, dry_run, safe_strategy_name

        try:
            ok, message, kind = self._apply_bbdpi_blocking(
                name, command, use_hostlist, convert, dry_run, safe_strategy_name)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Ошибка применения стратегии BB DPI")
            ok, message, kind = False, f"Ошибка применения стратегии BB DPI: {exc}", "error"
        self._bbdpiApplyFinished.emit(ok, message, kind)

    def _apply_bbdpi_blocking(self, name, command, use_hostlist, convert, dry_run, safe_name):
        if not sudo_available():
            return False, "Для применения стратегии требуется sudo (нет askpass-хелпера)", "error"
        result = convert(command, use_hostlist=use_hostlist)
        if not result.ok:
            return False, "Строка не преобразована в правила nfqws", "error"
        problems = dry_run(list(result.rules))
        if problems:
            return False, "nfqws не принимает результат: " + "; ".join(problems[:3]), "error"
        if not STRATEGY_DIR.is_dir():
            return False, f"Не найден каталог менеджера: {MANAGER_DIR}", "error"

        safe = safe_name(name or "BBDI_Custom")
        content = result.text + "\n"
        try:
            STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
            NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
            (STRATEGY_DIR / safe).write_text(content, encoding="utf-8")
            NAME_FILE.write_text(safe, encoding="utf-8")
            CONFIG_FILE.write_text(content, encoding="utf-8")
            from core.game_presets import reapply_active_preset_to_config

            reapply_active_preset_to_config(str(MANAGER_DIR))
        except OSError as exc:
            return False, f"Не удалось сохранить стратегию «{safe}»: {exc}", "error"

        self._strategyReady.emit(safe)
        ok, message = self._service.restart_service()
        if ok:
            return True, f"Стратегия «{safe}» применена, служба перезапущена", "success"
        return False, f"Стратегия «{safe}» сохранена, но служба не перезапущена: {_shorten(message)}", "warning"

    @Slot()
    def shutdown(self) -> None:
        self._timer.stop()
