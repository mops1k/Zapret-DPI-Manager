"""WARP (usque) для Zapret DPI Manager: состояние, установка, аккаунт, служба.

Туннель поднимает usque (MIT, github.com/Diniboy1123/usque) в режиме
`nativetun` — это MASQUE/Connect-IP к Cloudflare WARP. Модуль отвечает за:

* пользовательское состояние — ``utils/warp.txt`` (см. :data:`WARP_STATE_KEYS`);
* установку usque в ``/opt/warp`` с проверкой SHA256 из ``checksums.txt``
  последнего релиза (бинарник не входит в поставку приложения);
* регистрацию аккаунта только после явного согласия с условиями Cloudflare;
* скрипты и юнит: ``/opt/warp/*.sh``, ``/etc/systemd/system/warp.service``;
* start/stop/enable/disable службы ``warp.service`` через ``run_sudo``;
* правила zapret для внешнего трафика туннеля — ``utils/warp_rules.txt``
  и список endpoint'ов ``files/lists/ipset-warp.txt``.

Службы zapret и WARP независимы и могут быть активны одновременно: правила
zapret исключают интерфейс туннеля (внутренний трафик) и, если «лечение»
выключено, endpoint'ы туннеля (внешний трафик).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from core.app_logging import get_error_logger
from core.sudo_helper import run_sudo, sudo_available

# --- Источник usque -----------------------------------------------------
GITHUB_REPO = "Diniboy1123/usque"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "Zapret-DPI-Manager/2"

# os.uname().machine -> суффикс архитектуры в имени ассета релиза.
# Сверено с релизом usque v4.2.1: linux-ассеты только amd64, arm64, armv5,
# armv6, armv7, mips64le, mips64, mipsle, mips (386/riscv64 не публикуются).
ARCH_MAP = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "armv7",
    "armv7": "armv7",
    "armv6l": "armv6",
    "armv6": "armv6",
    "armv5l": "armv5",
    "armv5": "armv5",
    "mips64": "mips64",
    "mips64el": "mips64le",
    "mipsel": "mipsle",
    "mips": "mips",
}

# --- Пути ---------------------------------------------------------------
OPT_DIR = Path("/opt/warp")
USQUE_BIN = OPT_DIR / "usque"
ACCOUNT_FILE = OPT_DIR / "account.json"
VERSION_FILE = OPT_DIR / "version"
UNIT_PATH = Path("/etc/systemd/system/warp.service")
SERVICE_NAME = "warp"

STATE_NAME = "warp.txt"
RULES_NAME = "warp_rules.txt"
IPSET_NAME = "ipset-warp.txt"

# --- Значения по умолчанию для utils/warp.txt --------------------------
DEFAULT_IFACE = "warp0"
DEFAULT_SNI = "consumer-masque.cloudflareclient.com"
DEFAULT_MTU = 1280
# Публичные адреса Cloudflare WARP: запасной список, если аккаунт ещё не
# зарегистрирован (точные endpoint'ы берём из account.json при регистрации).
FALLBACK_ENDPOINTS = ("162.159.198.1", "162.159.198.2")

DEFAULT_STATE = {
    "enabled": "0",
    "mode": "full",
    "transport": "auto",
    "sni": DEFAULT_SNI,
    "iface": DEFAULT_IFACE,
    "mtu": str(DEFAULT_MTU),
    "dns": "link",
    "help_tunnel": "1",
    "lists": "ipset-all",
    "exclude": "ipset-exclude",
    "uids": "",
}

# Порядок ключей = порядок строк в файле состояния.
WARP_STATE_KEYS = (
    "enabled",
    "mode",
    "transport",
    "sni",
    "iface",
    "mtu",
    "dns",
    "help_tunnel",
    "lists",
    "exclude",
    "uids",
)

STATE_HEADER = (
    "# WARP (usque) — состояние Zapret DPI Manager.\n"
    "# Читает /opt/warp/warp_start.sh; правится приложением (окно WARP).\n"
)

# Диапазон приоритетов правил селективного режима: всё, что мы добавляем,
# снимается аварийной очисткой, даже если /run/warp/iprules потерян.
CLEANUP_PRIO_MIN = 10000
CLEANUP_PRIO_MAX = 12999
RULES_CLEANUP = (
    "for fam in '' -6; do "
    "ip $fam rule show 2>/dev/null | "
    "sed -n 's/^\\([0-9]\\{5\\}\\):.*/\\1/p' | "
    "while read -r prio; do "
    'case "$prio" in 1[0-2][0-9][0-9][0-9]) '
    'ip $fam rule del priority "$prio" >/dev/null 2>&1 || true ;; esac; '
    "done; done"
)

IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
SNI_RE = re.compile(r"^[A-Za-z0-9.*_-]{1,253}$")
LISTS_RE = re.compile(r"^[A-Za-z0-9._,:-]{0,512}$")
UIDS_RE = re.compile(r"^[0-9,-]{0,128}$")
VERSION_RE = re.compile(r"v?([0-9]+(?:\.[0-9]+)+)")


def manager_dir() -> Path:
    """Каталог установки менеджера (как в core.engine_updater)."""
    return Path.home() / "Zapret_DPI_Manager"


def utils_dir() -> Path:
    return manager_dir() / "utils"


def lists_dir() -> Path:
    return manager_dir() / "files" / "lists"


def repo_dir() -> Path:
    """Каталог с файлами поставки (warp/system/*)."""
    return Path(__file__).resolve().parents[1]


def cache_dir() -> Path:
    path = Path.home() / ".cache" / "zapret_dpi_manager"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    return utils_dir() / STATE_NAME


def rules_path() -> Path:
    return utils_dir() / RULES_NAME


def ipset_path() -> Path:
    return lists_dir() / IPSET_NAME


def available_ipset_lists() -> list[str]:
    """Имена ipset-списков для селективного режима (без .txt и без *_user).

    Пользовательские дополнения (`<имя>_user.txt`) скрипт подхватывает сам,
    поэтому в интерфейсе показываем только базовые имена.
    """
    result: list[str] = []
    try:
        for path in sorted(lists_dir().glob("ipset-*.txt")):
            name = path.stem
            if name.endswith("_user") or name == IPSET_NAME.removesuffix(".txt"):
                continue
            result.append(name)
    except OSError:
        return []
    return result


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


# --- Состояние ----------------------------------------------------------
def read_state() -> dict:
    """Состояние WARP из utils/warp.txt (недостающие ключи — по умолчанию)."""
    state = dict(DEFAULT_STATE)
    path = state_path()
    if not path.is_file():
        return state
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return state
    for raw in text.splitlines():
        line = raw.rstrip("\r").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in WARP_STATE_KEYS:
            state[key] = value.strip()
    return state


def validate_state(state: dict) -> list[str]:
    """Проверяет значения состояния; возвращает список проблем."""
    problems: list[str] = []
    if str(state.get("mode")) not in ("full", "select"):
        problems.append("режим: ожидается full или select")
    if str(state.get("transport")) not in ("auto", "quic", "http2"):
        problems.append("транспорт: ожидается auto, quic или http2")
    if str(state.get("dns")) not in ("link", "keep"):
        problems.append("DNS: ожидается link или keep")
    if str(state.get("help_tunnel")) not in ("0", "1"):
        problems.append("«лечить туннель»: ожидается 0 или 1")
    iface = str(state.get("iface", ""))
    if not IFACE_RE.match(iface):
        problems.append(f"имя интерфейса '{iface}' недопустимо")
    sni = str(state.get("sni", ""))
    if sni and not SNI_RE.match(sni):
        problems.append(f"SNI '{sni}' содержит недопустимые символы")
    mtu = str(state.get("mtu", ""))
    if not mtu.isdigit() or not (576 <= int(mtu) <= 1500):
        problems.append("MTU: целое число от 576 до 1500")
    if not LISTS_RE.match(str(state.get("lists", ""))):
        problems.append("списки: допустимы буквы, цифры, «,», «.», «:», «-», «_»")
    if not LISTS_RE.match(str(state.get("exclude", ""))):
        problems.append("исключения: допустимы буквы, цифры, «,», «.», «:», «-», «_»")
    if not UIDS_RE.match(str(state.get("uids", ""))):
        problems.append("пользователи (uidrange): только цифры, «,» и «-»")
    if str(state.get("mode")) == "select" and not str(state.get("lists", "")).strip() and not str(
        state.get("uids", "")
    ).strip():
        problems.append("селективный режим: нужен хотя бы один список диапазонов или uidrange")
    return problems


def write_state(state: dict) -> None:
    """Записывает utils/warp.txt (только известные ключи, валидированные)."""
    merged = dict(DEFAULT_STATE)
    for key in WARP_STATE_KEYS:
        if key in state and state[key] is not None:
            merged[key] = str(state[key]).strip()
    problems = validate_state(merged)
    if problems:
        raise ValueError("; ".join(problems))
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [STATE_HEADER]
    lines += [f"{key}={merged[key]}\n" for key in WARP_STATE_KEYS]
    path.write_text("".join(lines), encoding="utf-8")


# --- Правила zapret для внешнего трафика туннеля -------------------------
# Профили nfqws подставляются в начало ARGS (starter.sh): nfqws применяет
# первый совпавший профиль, поэтому трафик endpoint'ов получает именно эту
# обработку. Проверено `nfqws --dry-run`.
QUIC_PROFILE = (
    "--filter-udp=443 --ipset={ipset_warp} "
    "--dpi-desync=fake --dpi-desync-fake-quic={quicgoogle} --new"
)
HTTP2_PROFILE = (
    "--filter-tcp=443 --ipset={ipset_warp} "
    "--dpi-desync=fake,split2 --dpi-desync-fake-tls={tlsgoogle} --new"
)


def warp_profiles(transport: str) -> list[str]:
    """Профили nfqws для включённого транспорта (auto — оба)."""
    if transport == "quic":
        return [QUIC_PROFILE]
    if transport == "http2":
        return [HTTP2_PROFILE]
    return [QUIC_PROFILE, HTTP2_PROFILE]


def write_rules(state: dict | None = None) -> bool:
    """Пишет utils/warp_rules.txt при help_tunnel=1, иначе удаляет его.

    Возвращает True, если файл правил активен (трафик endpoint'ов лечится
    через zapret), и False, если endpoint'ы просто исключаются из NFQUEUE.
    """
    state = state or read_state()
    path = rules_path()
    if str(state.get("help_tunnel")) != "1":
        if path.exists():
            path.unlink()
        return False
    lines = warp_profiles(str(state.get("transport", "auto")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def read_ipset() -> list[str]:
    """IP endpoint'ов из files/lists/ipset-warp.txt (без комментариев)."""
    path = ipset_path()
    if not path.is_file():
        return list(FALLBACK_ENDPOINTS)
    result: list[str] = []
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line and line not in result:
                result.append(line)
    except OSError:
        return list(FALLBACK_ENDPOINTS)
    return result or list(FALLBACK_ENDPOINTS)


def write_ipset(endpoints) -> None:
    """Обновляет files/lists/ipset-warp.txt (endpoint'ы Cloudflare WARP)."""
    unique = [ip for ip in dict.fromkeys(str(e).strip() for e in endpoints) if ip]
    if not unique:
        unique = list(FALLBACK_ENDPOINTS)
    path = ipset_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Endpoint'ы WARP (usque). Файл обновляет окно WARP приложения.\n"
    path.write_text(header + "".join(f"{ip}\n" for ip in unique), encoding="utf-8")


# --- Менеджер -----------------------------------------------------------
class WarpManager:
    """Установка usque, аккаунт WARP и служба warp.service."""

    def __init__(self, progress=None):
        self.progress = progress or (lambda _message, _percent: None)
        self._release: dict | None = None
        self._release_error: str | None = None

    # --- общие помощники ---
    def _run_sudo(self, argv, timeout: int = 60):
        if not sudo_available():
            return False, "sudo или askpass-хелпер недоступны"
        code, out, err = run_sudo(argv, timeout=timeout)
        if code == 0:
            return True, (out or "").strip()
        parts = [p.strip() for p in (err, out) if (p or "").strip()]
        return False, "\n".join(parts) if parts else f"(код выхода {code})"

    @staticmethod
    def _run(argv, timeout: int = 10):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "Таймаут выполнения команды"
        except OSError as e:
            return False, str(e)
        if proc.returncode == 0:
            return True, (proc.stdout or "").strip()
        return False, (proc.stderr or proc.stdout or "").strip()

    @property
    def arch(self) -> str | None:
        return ARCH_MAP.get(os.uname().machine)

    def is_installed(self) -> bool:
        return USQUE_BIN.is_file() and os.access(USQUE_BIN, os.X_OK)

    def account_exists(self) -> bool:
        return ACCOUNT_FILE.is_file()

    def installed_version(self) -> str | None:
        """Версия установленного usque — из /opt/warp/version (пишем при установке)."""
        try:
            text = VERSION_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return text or None

    # --- релиз и установка usque ---
    def latest_release(self, force: bool = False) -> dict | None:
        """Описание последнего релиза usque (кэш в пределах экземпляра)."""
        if self._release is not None and not force:
            return self._release
        request = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                self._release = json.loads(response.read().decode("utf-8"))
            self._release_error = None
        except urllib.error.HTTPError as e:
            self._release_error = (
                "релизы usque не найдены (HTTP 404)"
                if e.code == 404
                else f"GitHub вернул HTTP {e.code}"
            )
        except urllib.error.URLError as e:
            detail = str(e.reason) if e.reason is not None else str(e)
            self._release_error = f"нет связи с GitHub: {detail}"
        except Exception as e:  # pragma: no cover - защитная ветка
            get_error_logger().error("Релиз usque: %s", e, exc_info=True)
            self._release_error = str(e)
        return self._release

    @property
    def release_error(self) -> str | None:
        return self._release_error

    def upstream_version(self) -> str | None:
        release = self.latest_release()
        if not release:
            return None
        tag = str(release.get("tag_name") or "").strip()
        match = VERSION_RE.search(tag)
        return match.group(1) if match else (tag or None)

    def _find_assets(self, release: dict) -> tuple[str | None, str | None, str | None]:
        """(url архива, имя архива, url checksums) для текущей архитектуры."""
        arch = self.arch
        if not arch:
            return None, None, None
        assets = release.get("assets") or []
        archive = None
        checksums = None
        expected = f"linux_{arch}.zip"
        for asset in assets:
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            low = name.lower()
            if low == "checksums.txt" or ("checksum" in low and low.endswith(".txt")):
                checksums = checksums or url
                continue
            if low.endswith(expected) or (low.endswith(".zip") and expected in low):
                archive = archive or (url, name)
        url = archive[0] if archive else None
        name = archive[1] if archive else None
        return url, name, checksums

    @staticmethod
    def _download(url: str, dest: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=180) as response, open(dest, "wb") as out:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        if not dest.exists() or dest.stat().st_size == 0:
            raise RuntimeError("скачанный файл пустой")

    @staticmethod
    def _expected_digest(checksums: Path, archive_name: str) -> str | None:
        """SHA256 архива из goreleaser-файла checksums.txt."""
        try:
            for raw in checksums.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = raw.split()
                if len(parts) >= 2 and parts[-1].lstrip("*") == archive_name:
                    return parts[0].strip().lower()
        except OSError:
            return None
        return None

    def runtime_outdated(self) -> bool:
        """Разошлись ли скрипты и юнит в /opt/warp с файлами поставки.

        Полное обновление приложения меняет файлы в ~/Zapret_DPI_Manager и не
        трогает /opt/warp: без этой проверки служба продолжала бы работать по
        старой копии скриптов (так и случилось при проверке селективного режима).
        """
        source = repo_dir() / "warp" / "system"
        for name in ("warp_start.sh", "warp_up.sh", "warp_down.sh", "warp_stop.sh"):
            original = sha256_file(source / name)
            installed = sha256_file(OPT_DIR / name)
            if original and original != installed:
                return True
        unit_original = sha256_file(source / "warp.service")
        if unit_original and unit_original != sha256_file(UNIT_PATH):
            return True
        return False

    def install_runtime(self) -> tuple[bool, str]:
        """Копирует скрипты и юнит WARP в систему (/opt/warp, /etc/systemd/system)."""
        source = repo_dir() / "warp" / "system"
        scripts = sorted(source.glob("*.sh"))
        unit = source / "warp.service"
        if not scripts or not unit.is_file():
            return False, f"в поставке нет файлов WARP: {source}"
        ok, message = self._run_sudo(["mkdir", "-p", str(OPT_DIR)], timeout=30)
        if not ok:
            return False, f"не удалось создать {OPT_DIR}: {message}"
        for script in scripts:
            ok, message = self._run_sudo(
                ["install", "-m", "755", str(script), str(OPT_DIR / script.name)], timeout=60
            )
            if not ok:
                return False, f"не удалось установить {script.name}: {message}"
        ok, message = self._run_sudo(
            ["install", "-m", "644", str(unit), str(UNIT_PATH)], timeout=60
        )
        if not ok:
            return False, f"не удалось установить юнит {UNIT_PATH}: {message}"
        self._run_sudo(["systemctl", "daemon-reload"], timeout=60)
        return True, "скрипты и юнит WARP установлены"

    def install_usque(self) -> tuple[bool, str]:
        """Скачивает usque последнего релиза, проверяет SHA256 и ставит в /opt/warp."""
        arch = self.arch
        if not arch:
            return False, f"архитектура {os.uname().machine} не поддерживается usque"

        self.progress("Проверка последнего релиза usque...", 5)
        release = self.latest_release(force=True)
        if not release:
            return False, self._release_error or "не удалось получить релиз usque"
        version = self.upstream_version() or "?"
        archive_url, archive_name, checksums_url = self._find_assets(release)
        if not archive_url or not archive_name:
            return False, f"в релизе usque {version} нет сборки для {arch}"
        if not checksums_url:
            return False, f"в релизе usque {version} нет файла checksums.txt"

        cache = cache_dir() / "warp"
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / archive_name
        checksums = cache / "checksums.txt"
        try:
            self.progress(f"Скачивание usque {version} ({arch})...", 20)
            self._download(archive_url, archive)
            self.progress("Скачивание контрольных сумм...", 45)
            self._download(checksums_url, checksums)
        except Exception as e:
            get_error_logger().error("Скачивание usque: %s", e, exc_info=True)
            detail = getattr(e, "reason", None) or e
            return False, f"не удалось скачать usque: {detail}"

        expected = self._expected_digest(checksums, archive_name)
        if not expected:
            return False, f"в checksums.txt нет суммы для {archive_name}"
        actual = sha256_file(archive)
        if actual != expected:
            return False, (
                f"SHA256 не совпал для {archive_name}: ожидалось {expected[:16]}…, "
                f"получено {(actual or '?')[:16]}… — установка отменена"
            )

        candidate = cache / "usque"
        try:
            self.progress("Распаковка архива...", 65)
            with zipfile.ZipFile(archive) as zf:
                member = None
                for info in zf.infolist():
                    if Path(info.filename).name == "usque":
                        member = info
                        break
                if member is None:
                    names = ", ".join(Path(i.filename).name for i in zf.infolist()[:10])
                    return False, f"в архиве нет бинарника usque (есть: {names})"
                with zf.open(member) as src, open(candidate, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        except Exception as e:
            get_error_logger().error("Распаковка usque: %s", e, exc_info=True)
            return False, f"не удалось распаковать архив: {e}"

        self.progress("Установка usque в /opt/warp...", 80)
        ok, message = self._run_sudo(["mkdir", "-p", str(OPT_DIR)], timeout=30)
        if not ok:
            return False, f"не удалось создать {OPT_DIR}: {message}"
        # Бэкап текущего движка: установка идёт через временное имя и mv, но
        # предыдущая версия должна остаться под рукой.
        if USQUE_BIN.is_file():
            backup = cache / "usque_backup"
            try:
                shutil.copy2(USQUE_BIN, backup)
            except OSError as e:
                get_error_logger().error("Бэкап usque не удался: %s", e)
        staging = OPT_DIR / "usque.new"
        ok, message = self._run_sudo(
            ["install", "-m", "755", str(candidate), str(staging)], timeout=120
        )
        if not ok:
            return False, f"не удалось установить usque: {message}"
        # Атомарная замена: обрыв на этом шаге не оставит обрезанный движок.
        ok, message = self._run_sudo(["mv", "-f", str(staging), str(USQUE_BIN)], timeout=60)
        if not ok:
            return False, f"не удалось заменить usque: {message}"

        version_tmp = cache / "version"
        version_tmp.write_text(f"{version}\n", encoding="utf-8")
        self._run_sudo(
            ["install", "-m", "644", str(version_tmp), str(VERSION_FILE)], timeout=60
        )
        ok, message = self.install_runtime()
        if not ok:
            return False, message
        self.progress("usque установлен", 100)
        return True, f"usque {version} ({arch}) установлен в {OPT_DIR}"

    # --- аккаунт ---
    @staticmethod
    def _json_field(text: str, key: str) -> str | None:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', text)
        return match.group(1) if match else None

    def register_account(self, accept_tos: bool) -> tuple[bool, str]:
        """Регистрирует аккаунт WARP: `usque register --accept-tos`.

        Выполняется от имени пользователя (сеть и обход DPI — как у остальных
        запросов приложения), затем файл ставится в /opt/warp/account.json
        с правами 0600 root.
        """
        if not accept_tos:
            return False, "Нужно подтвердить согласие с условиями Cloudflare WARP"
        if not self.is_installed():
            return False, "usque не установлен: сначала установите движок WARP"
        cache = cache_dir() / "warp"
        cache.mkdir(parents=True, exist_ok=True)
        candidate = cache / "account.json"
        if candidate.exists():
            candidate.unlink()
        self.progress("Регистрация аккаунта WARP...", 30)
        output = ""
        proc = None
        for attempt in (1, 2, 3):
            if attempt > 1:
                self.progress(f"Регистрация аккаунта WARP (попытка {attempt})...", 30)
                time.sleep(4)
            try:
                proc = subprocess.run(
                    [str(USQUE_BIN), "register", "--accept-tos", "-c", str(candidate)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                output = "регистрация не уложилась в 120 с (нет связи с Cloudflare?)"
                continue
            except OSError as e:
                return False, str(e)
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            if proc.returncode == 0 and candidate.is_file():
                break
        if proc is None or proc.returncode != 0 or not candidate.is_file():
            get_error_logger().error("usque register: %s", output)
            hint = (
                " Если провайдер блокирует Cloudflare, включите zapret (лечение внешнего "
                "трафика туннеля) и повторите регистрацию."
            )
            code = proc.returncode if proc is not None else "?"
            return False, f"usque register завершился с кодом {code}: {output or 'нет вывода'}.{hint}"

        text = candidate.read_text(encoding="utf-8", errors="replace")
        endpoints = [
            ip
            for ip in (
                self._json_field(text, "endpoint_v4"),
                self._json_field(text, "endpoint_h2_v4"),
                self._json_field(text, "endpoint_v6"),
                self._json_field(text, "endpoint_h2_v6"),
            )
            if ip
        ]
        write_ipset(endpoints)

        self.progress("Установка аккаунта в /opt/warp...", 80)
        ok, message = self._run_sudo(["mkdir", "-p", str(OPT_DIR)], timeout=30)
        if not ok:
            return False, f"не удалось создать {OPT_DIR}: {message}"
        ok, message = self._run_sudo(
            ["install", "-m", "600", "-o", "root", "-g", "root", str(candidate), str(ACCOUNT_FILE)],
            timeout=60,
        )
        if not ok:
            return False, f"не удалось установить аккаунт: {message}"
        self.progress("Аккаунт WARP готов", 100)
        return True, "аккаунт WARP зарегистрирован"

    def delete_account(self) -> tuple[bool, str]:
        """Удаляет аккаунт (при следующем включении потребуется регистрация)."""
        if not self.account_exists():
            return True, "аккаунт отсутствует"
        return self._run_sudo(["rm", "-f", str(ACCOUNT_FILE)], timeout=30)

    # --- служба ---
    def journal_tail(self, lines: int = 40, max_chars: int = 12000) -> str:
        """Хвост журнала warp.service и тегов warp/warp-usque (окно диагностики)."""
        chunks = []
        for argv in (
            ["journalctl", "-u", f"{SERVICE_NAME}.service", "-n", str(lines), "--no-pager"],
            ["journalctl", "-t", "warp", "-t", "warp-usque", "-n", str(lines), "--no-pager"],
        ):
            code, out, err = run_sudo(argv, timeout=25)
            text = (out or err or "").strip() or f"(пусто, код выхода {code})"
            chunks.append(f"$ {' '.join(argv)}\n{text}")
        result = "\n\n".join(chunks)
        if len(result) > max_chars:
            result = result[: max_chars - 24] + "\n… [фрагмент усечён]"
        return result

    def _failure_context(self, max_chars: int = 8000) -> str:
        chunks = []
        for argv in (
            ["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"],
            ["journalctl", "-u", f"{SERVICE_NAME}.service", "-n", "40", "--no-pager"],
            ["journalctl", "-t", "warp", "-t", "warp-usque", "-n", "40", "--no-pager"],
        ):
            _ok, out = self._run_sudo(argv, timeout=20)
            text = (out or "").strip()
            if text:
                chunks.append(f"$ {' '.join(argv)}\n{text}")
        result = "\n\n".join(chunks)
        if len(result) > max_chars:
            result = result[: max_chars - 24] + "\n… [фрагмент усечён]"
        return result

    def status(self) -> str:
        ok, out = self._run(["systemctl", "is-active", SERVICE_NAME], timeout=5)
        value = (out or "").strip()
        if value in ("active", "inactive", "failed", "activating", "deactivating"):
            return value
        return "unknown" if not ok or not value else value

    def autostart_enabled(self) -> bool:
        ok, out = self._run(["systemctl", "is-enabled", SERVICE_NAME], timeout=5)
        return bool(ok and (out or "").strip().lower() == "enabled")

    def unit_exists(self) -> bool:
        ok, out = self._run(["systemctl", "list-unit-files", f"{SERVICE_NAME}.service"], timeout=5)
        return bool(ok and f"{SERVICE_NAME}.service" in out)

    def _wait_active(self, timeout: float = 130) -> tuple[bool, str]:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = self.status()
            if last == "active":
                return True, "active"
            if last in ("failed", "inactive", "unknown"):
                break
            time.sleep(0.5)
        context = self._failure_context()
        head = f"служба WARP не в состоянии active после запуска: {last!r}"
        return False, f"{head}\n\n{context}" if context else head

    def start(self) -> tuple[bool, str]:
        ok, message = self._run_sudo(["systemctl", "start", SERVICE_NAME], timeout=140)
        if not ok:
            context = self._failure_context()
            full = (message or "systemctl вернул ошибку").strip()
            get_error_logger().error("systemctl start warp:\n%s", f"{full}\n\n{context}")
            return False, f"{full}\n\n{context}" if context else full
        return self._wait_active()

    def stop(self) -> tuple[bool, str]:
        ok, message = self._run_sudo(["systemctl", "stop", SERVICE_NAME], timeout=60)
        if not ok:
            return False, message
        return True, "остановлено"

    def restart(self) -> tuple[bool, str]:
        ok, message = self._run_sudo(["systemctl", "restart", SERVICE_NAME], timeout=140)
        if not ok:
            context = self._failure_context()
            full = (message or "systemctl вернул ошибку").strip()
            return False, f"{full}\n\n{context}" if context else full
        return self._wait_active()

    def enable(self) -> tuple[bool, str]:
        return self._run_sudo(["systemctl", "enable", SERVICE_NAME], timeout=60)

    def disable(self) -> tuple[bool, str]:
        return self._run_sudo(["systemctl", "disable", SERVICE_NAME], timeout=60)

    def daemon_reload(self) -> tuple[bool, str]:
        return self._run_sudo(["systemctl", "daemon-reload"], timeout=60)

    # --- состояние туннеля ---
    @staticmethod
    def tunnel_connected() -> bool:
        return Path("/run/warp/connected").is_file()

    @staticmethod
    def active_transport() -> str | None:
        try:
            value = Path("/run/warp/transport").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    @staticmethod
    def tunnel_probe_failure() -> str | None:
        """Причина неудачной самопроверки данных через туннель.

        Файл last_probe_failed оставляет warp_start.sh, когда соединение
        установилось, но данные не проходят (типичный случай — блокировка
        MASQUE провайдером): полный туннель тогда снимается автоматически.
        """
        for path in (Path("/run/warp/last_probe_failed"), Path("/run/warp/probe_failed")):
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            return text or "самопроверка данных через туннель не прошла"
        return None

    @staticmethod
    def active_endpoint() -> str | None:
        try:
            value = Path("/run/warp/endpoint").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    @staticmethod
    def trace_info(timeout: float = 12) -> dict | None:
        """Данные Cloudflare trace (ip, warp, loc) — проверка внешнего адреса."""
        request = urllib.request.Request(
            "https://www.cloudflare.com/cdn-cgi/trace", headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
        except Exception as e:
            get_error_logger().error("Cloudflare trace: %s", e)
            return None
        data = {}
        for line in text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
        return data or None

    # --- связка с zapret ---
    def apply_settings(self, state: dict | None = None) -> tuple[bool, str]:
        """Сохраняет состояние, правила и список endpoint'ов для zapret."""
        state = read_state() if state is None else state
        problems = validate_state(state)
        if problems:
            return False, "; ".join(problems)
        write_state(state)
        help_tunnel = write_rules(state)
        if not ipset_path().is_file():
            write_ipset(FALLBACK_ENDPOINTS)
        mode = "лечение через zapret" if help_tunnel else "исключение из zapret"
        return True, f"настройки WARP сохранены ({mode})"

    def restart_zapret_if_active(self) -> tuple[bool, str]:
        """Перезапускает zapret, чтобы он учёл интерфейс и endpoint'ы туннеля."""
        from core.service_manager import ServiceManager

        manager = ServiceManager()
        if manager.get_service_status() != "active":
            return True, "zapret не активен — перезапуск не нужен"
        return manager.restart_service()

    # --- обслуживание ---
    def update_usque(self) -> tuple[bool, str]:
        """Обновляет движок usque; активную службу останавливает и поднимает снова."""
        was_active = self.status() == "active"
        if was_active:
            ok, message = self.stop()
            if not ok:
                return False, f"не удалось остановить службу перед обновлением: {message}"
        ok, message = self.install_usque()
        if not ok:
            if was_active:
                self.start()
            return False, message
        if not was_active:
            return True, message
        ok, start_message = self.start()
        if not ok:
            return False, f"{message}, но служба не поднялась: {start_message}"
        return True, f"{message}; служба перезапущена"

    def rules_in_range(self) -> list[str]:
        """Наши правила маршрутизации (приоритеты 10000-12999), без sudo."""
        result: list[str] = []
        for argv in (["ip", "rule", "show"], ["ip", "-6", "rule", "show"]):
            ok, out = self._run(argv, timeout=10)
            if not ok:
                continue
            for line in (out or "").splitlines():
                priority = line.split(":", 1)[0].strip()
                if priority.isdigit() and CLEANUP_PRIO_MIN <= int(priority) <= CLEANUP_PRIO_MAX:
                    result.append(line.strip())
        return result

    def emergency_cleanup(self) -> tuple[bool, str]:
        """Снимает всё, что WARP мог оставить: службу, маршруты, правила, DNS.

        Нужна, когда состояние системы разошлось с состоянием приложения:
        туннель мёртв, а маршруты или правила остались.
        """
        steps: list[str] = []
        before = len(self.rules_in_range())

        if (OPT_DIR / "warp_stop.sh").is_file():
            ok, message = self._run_sudo(["bash", str(OPT_DIR / "warp_stop.sh")], timeout=120)
            steps.append("служба остановлена, маршруты и DNS сняты" if ok else f"stop: {message}")
        else:
            steps.append("скриптов в /opt/warp нет — чищу напрямую")
            for argv in (
                ["ip", "route", "del", "default", "dev", "warp0", "table", "100"],
                ["ip", "link", "del", "warp0"],
            ):
                self._run_sudo(argv, timeout=30)
        # Правила могли остаться без файла /run/warp/iprules.
        self._run_sudo(["bash", "-c", RULES_CLEANUP], timeout=60)

        after = len(self.rules_in_range())
        steps.append(f"наших правил маршрутизации: было {before}, осталось {after}")

        state = read_state()
        state["enabled"] = "0"
        try:
            self.apply_settings(state)
            steps.append("состояние: WARP выключен")
        except (ValueError, OSError) as e:
            steps.append(f"состояние не сохранено: {e}")

        ok, message = self.restart_zapret_if_active()
        steps.append(f"zapret: {message}" if ok else f"zapret: {message}")

        report = "; ".join(steps)
        ok = after == 0
        return ok, report

    def uninstall(self, remove_engine: bool = True) -> tuple[bool, str]:
        """Удаляет подсистему WARP: службу, юнит, скрипты, аккаунт и состояние.

        Аккаунт Cloudflare будет потерян: после установки заново потребуется
        повторная регистрация.
        """
        steps: list[str] = []
        self.emergency_cleanup()
        steps.append("состояние и правила сняты")

        self._run_sudo(["systemctl", "disable", SERVICE_NAME], timeout=60)
        self._run_sudo(["systemctl", "stop", SERVICE_NAME], timeout=90)
        ok, message = self._run_sudo(["rm", "-f", str(UNIT_PATH)], timeout=30)
        steps.append(f"юнит удалён: {UNIT_PATH}" if ok else f"юнит: {message}")
        self._run_sudo(["systemctl", "daemon-reload"], timeout=60)

        if remove_engine:
            ok, message = self._run_sudo(["rm", "-rf", str(OPT_DIR)], timeout=120)
            steps.append(f"каталог {OPT_DIR} удалён" if ok else f"{OPT_DIR}: {message}")

        for path in (state_path(), rules_path()):
            try:
                path.unlink()
                steps.append(f"{path.name} удалён")
            except FileNotFoundError:
                continue
            except OSError as e:
                steps.append(f"{path.name}: {e}")

        self._run_sudo(["rm", "-rf", "/run/warp"], timeout=30)
        self.restart_zapret_if_active()
        return True, "; ".join(steps)
