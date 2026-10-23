"""Обновление движка nfqws из ImMALWARE/zapret-linux-easy.

Источник — готовые статические сборки bol-van (репозиторий ImMALWARE кладёт
их в bins/<arch>/nfqws). Скачивается бинарник для текущей архитектуры, он
проверяется запуском `--version` (бинарник не должен требовать root), затем
заменяются /opt/zapret/nfqws и локальная копия zapret/bins/<arch>/nfqws.
Служба перезапускается только если была активна.

Проверка версии: nfqws печатает строку вида «github version v72.12 (hash)».
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
from pathlib import Path

from core.app_logging import get_error_logger
from core.github_fetch import FetchError, fetch_to_file
from core.service_manager import ServiceManager
from core.sudo_helper import run_sudo, sudo_available

BASE_URL = "https://raw.githubusercontent.com/ImMALWARE/zapret-linux-easy/main/bins"

# os.uname().machine -> каталог в источнике.
ARCH_MAP = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "i386": "x86",
    "i686": "x86",
    "armv7l": "arm",
    "armv6l": "arm",
    "aarch64": "arm64",
    "arm64": "arm64",
    "riscv64": "riscv64",
}

VERSION_RE = re.compile(r"version\s+(v[0-9][\w.\-]*)")
BACKUP_PREFIX = "nfqws_backup_"


def manager_dir() -> Path:
    return Path.home() / "Zapret_DPI_Manager"


def cache_dir() -> Path:
    return Path.home() / ".cache" / "zapret_dpi_manager"


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def read_version(binary: Path) -> str | None:
    """Версия из `nfqws --version` (None, если бинарник не запускается)."""
    try:
        proc = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = VERSION_RE.search((proc.stdout or "") + (proc.stderr or ""))
    return match.group(1) if match else None


class NfqwsUpdater:
    """Проверка и применение обновления движка nfqws."""

    def __init__(
        self,
        root: Path | None = None,
        opt_dir: Path | None = None,
        progress_callback=None,
        service_manager=None,
    ):
        self.root = Path(root) if root else manager_dir()
        self.opt_dir = Path(opt_dir) if opt_dir else Path("/opt/zapret")
        self.progress = progress_callback or (lambda message, percent=None: None)
        self._service_manager = service_manager

    # --- пути ---------------------------------------------------------------

    @property
    def arch(self) -> str | None:
        return ARCH_MAP.get(os.uname().machine)

    def local_binary(self) -> Path:
        """Копия движка внутри менеджера (её кладёт установщик/полное обновление)."""
        return self.root / "zapret" / "bins" / (self.arch or "unknown") / "nfqws"

    def installed_binary(self) -> Path:
        """Действующий движок службы."""
        return self.opt_dir / "nfqws"

    def download_url(self) -> str | None:
        return f"{BASE_URL}/{self.arch}/nfqws" if self.arch else None

    # --- проверка и применение ---------------------------------------------

    def update(self, apply: bool = True, force: bool = False) -> dict:
        report: dict = {
            "arch": self.arch,
            "download_url": self.download_url(),
            "old_version": None,
            "new_version": None,
            "up_to_date": False,
            "installed_opt": False,
            "installed_local": False,
            "service_restarted": False,
            "backup_path": None,
            "applied": False,
            "errors": [],
        }

        if not self.arch:
            report["errors"].append(f"неизвестная архитектура: {os.uname().machine}")
            return report

        installed = self.installed_binary()
        local = self.local_binary()
        report["old_version"] = read_version(installed) or read_version(local)
        old_sha = _sha256(installed) or _sha256(local)

        temp_dir = Path(tempfile.mkdtemp(prefix="nfqws_update_"))
        try:
            self.progress(f"Скачивание движка nfqws ({self.arch})...", 15)
            candidate = temp_dir / "nfqws"
            try:
                self._download(self.download_url(), candidate)
            except Exception as exc:
                report["errors"].append(self._error_text(exc))
                return report

            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
            new_version = read_version(candidate)
            if not new_version:
                report["errors"].append(
                    "скачанный nfqws не запускается (--version не вернул версию)"
                )
                return report
            report["new_version"] = new_version
            new_sha = _sha256(candidate)

            if new_sha and old_sha == new_sha and not force:
                report["up_to_date"] = True
                return report

            if not apply:
                report["applied"] = False
                return report

            self.progress("Резервная копия текущего движка...", 40)
            report["backup_path"] = self._backup([installed, local])

            if installed.is_file() or self.opt_dir.is_dir():
                self.progress("Установка движка в /opt/zapret...", 60)
                report["installed_opt"] = self._install_to_opt(candidate)

            self.progress("Обновление локальной копии движка...", 75)
            report["installed_local"] = self._install_local(candidate)

            if report["installed_opt"] and self._service_active():
                self.progress("Перезапуск службы zapret...", 90)
                manager = self._service_manager or ServiceManager()
                report["service_restarted"] = bool(manager.restart_service())

            report["applied"] = bool(report["installed_opt"] or report["installed_local"])
            self.progress("Обновление движка завершено", 100)
            return report
        except Exception as exc:
            get_error_logger().error("Обновление движка nfqws: %s", exc, exc_info=True)
            report["errors"].append(str(exc))
            return report
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # --- внутренние шаги ----------------------------------------------------

    @staticmethod
    def _error_text(exc: Exception) -> str:
        if isinstance(exc, FetchError):
            return str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code == 404:
                return "движок nfqws не найден в источнике (HTTP 404)"
            return f"источник вернул HTTP {exc.code}"
        if isinstance(exc, urllib.error.URLError):
            reason = exc.reason
            detail = str(reason) if reason is not None else str(exc)
            return f"нет связи с источником движка ({detail})"
        return f"не удалось скачать движок: {exc}"

    @staticmethod
    def _download(url: str, dest: Path) -> None:
        fetch_to_file(url, dest, timeout=180)
        if not dest.exists() or dest.stat().st_size == 0:
            raise RuntimeError("скачанный движок пустой")

    def _install_to_opt(self, candidate: Path) -> bool:
        """Копирует движок в /opt/zapret через sudo и выставляет +x."""
        if not sudo_available():
            get_error_logger().error("sudo/askpass недоступны: движок в /opt/zapret не обновлён")
            return False
        code, _out, err = run_sudo(
            ["cp", str(candidate), str(self.installed_binary())], timeout=120
        )
        if code != 0:
            get_error_logger().error(
                "Не удалось скопировать nfqws в %s: %s", self.installed_binary(), (err or "").strip()
            )
            return False
        code, _out, err = run_sudo(["chmod", "+x", str(self.installed_binary())], timeout=60)
        if code != 0:
            get_error_logger().error(
                "Не удалось chmod +x %s: %s", self.installed_binary(), (err or "").strip()
            )
            return False
        return True

    def _install_local(self, candidate: Path) -> bool:
        """Обновляет копию движка внутри менеджера (без sudo)."""
        try:
            target = self.local_binary()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
            return True
        except OSError as exc:
            get_error_logger().error("Не удалось обновить локальную копию nfqws: %s", exc)
            return False

    def _service_active(self) -> bool:
        try:
            manager = self._service_manager or ServiceManager()
            return manager.get_service_status() == "active"
        except Exception:
            return False

    def _backup(self, paths: list[Path]) -> str | None:
        """Сохраняет текущие копии движка рядом с кэшем приложения."""
        try:
            existing = [p for p in paths if p.is_file()]
            if not existing:
                return None
            cache_dir().mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target_dir = cache_dir() / f"{BACKUP_PREFIX}{stamp}"
            target_dir.mkdir(parents=True, exist_ok=True)
            for path in existing:
                name = "nfqws-opt" if path == self.installed_binary() else "nfqws-local"
                shutil.copy2(path, target_dir / name)
            return str(target_dir)
        except OSError as exc:
            get_error_logger().error("Резервная копия движка nfqws: %s", exc)
            return None

    # --- отчёт --------------------------------------------------------------

    @staticmethod
    def format_report(report: dict) -> list[str]:
        lines: list[str] = []
        if report.get("up_to_date"):
            version = report.get("old_version") or "?"
            lines.append(f"✅ Движок nfqws уже актуален ({version}, {report.get('arch')})")
            return lines
        if report.get("applied"):
            old = report.get("old_version") or "?"
            new = report.get("new_version") or "?"
            lines.append(f"⚙️ Движок nfqws обновлён: {old} → {new} ({report.get('arch')})")
        if report.get("installed_opt"):
            lines.append("• /opt/zapret/nfqws заменён")
        if report.get("installed_local"):
            lines.append("• локальная копия zapret/bins обновлена")
        if report.get("service_restarted"):
            lines.append("🔄 Служба zapret перезапущена")
        if report.get("backup_path"):
            lines.append(f"💾 Резервная копия движка: {report['backup_path']}")
        for error in report.get("errors") or []:
            lines.append(f"❌ {error}")
        if not lines:
            lines.append("Нет изменений")
        return lines
