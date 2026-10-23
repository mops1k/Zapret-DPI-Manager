"""Обновление стратегий и payload'ов из Flowseal без полного обновления приложения.

Источник — репозиторий Flowseal/zapret-discord-youtube (ветка main). Скачивается
один tarball ветки, из него берутся .bat-стратегии (конвертируются в формат
менеджера модулем core/flowseal_convert.py), payload'ы bin/*.bin и списки
lists/*.txt (мержатся: строки источника добавляются к нашим, ничего не удаляется).

Что НЕ трогается:
  * files/lists/*_user.txt — пользовательские списки;
  * config.txt и utils/* — текущий выбор стратегии;
  * существующие .bin, которых нет в источнике (например, наш
    quic_initial_dbankcloud_ru.bin);
  * файлы стратегий, которые не удалось сконвертировать без ошибок.

Скрипты службы (zapret/system/starter.sh, stopper.sh) обновляются из нашего
репозитория (raw/main) только если отличаются: без этого новые плейсхолдеры
payload'ов не появятся в /opt/zapret. Перед записью делается резервная копия
в ~/.cache/zapret_dpi_manager/.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from core.app_logging import get_error_logger
from core.github_fetch import fetch_bytes
from core.flowseal_convert import (
    FLOWSEAL_BIN_FILES,
    FLOWSEAL_LIST_FILES,
    NAME_MAP,
    convert_bat_text,
    known_placeholders,
    starter_path,
)
from core.manager_config import GITHUB_RAW_URL
from core.service_manager import ServiceManager
from core.sudo_helper import run_sudo, sudo_available

FLOWSEAL_REPO = "Flowseal/zapret-discord-youtube"
FLOWSEAL_BRANCH = "main"
# Atom-лента коммитов: не подпадает под лимит GitHub API (60 запросов/час на IP).
FLOWSEAL_ATOM_URL = f"https://github.com/{FLOWSEAL_REPO}/commits/{FLOWSEAL_BRANCH}.atom"
FLOWSEAL_COMMIT_API = f"https://api.github.com/repos/{FLOWSEAL_REPO}/commits/{FLOWSEAL_BRANCH}"
FLOWSEAL_TARBALL_URL = (
    f"https://codeload.github.com/{FLOWSEAL_REPO}/tar.gz/refs/heads/{FLOWSEAL_BRANCH}"
)

COMMIT_SHA_RE = re.compile(r"Grit::Commit/([0-9a-f]{40})")

# Скрипты службы, которые могут меняться вместе со стратегиями (новые плейсхолдеры).
SYSTEM_FILES = ("starter.sh", "stopper.sh")

VERSION_FILE_NAME = "strategies_version.txt"
BACKUP_PREFIX = "strategies_backup_"
USER_AGENT = "Zapret-DPI-Manager/2"


def manager_dir() -> Path:
    """Каталог установленного менеджера."""
    return Path.home() / "Zapret_DPI_Manager"


def cache_dir() -> Path:
    """Каталог состояния приложения (резервные копии, версии)."""
    return Path.home() / ".cache" / "zapret_dpi_manager"


def _http_get(url: str, timeout: float = 20.0) -> bytes:
    """GET с User-Agent; исключения не глотает — их разбирает вызывающий код."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _http_error_text(exc: Exception, what: str) -> str:
    """Человекочитаемая причина сетевой ошибки."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return f"{what}: не найдено (HTTP 404)"
        if exc.code == 403:
            return f"{what}: GitHub отклонил запрос (HTTP 403, вероятно лимит запросов)"
        return f"{what}: сервер вернул HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        detail = str(reason) if reason is not None else str(exc)
        return f"{what}: нет связи ({detail})"
    return f"{what}: {exc}"


def _write_text_atomic(path: Path, text: str) -> None:
    """Пишет текст через временный файл рядом, чтобы не оставить обрезанный файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _merge_list_file(local_path: Path, remote_text: str) -> int:
    """Добавляет строки источника, которых нет локально. Возвращает число добавленных."""
    local_lines: list[str] = []
    if local_path.is_file():
        local_lines = [line.rstrip("\r") for line in local_path.read_text(encoding="utf-8").splitlines()]

    existing = {line.strip() for line in local_lines if line.strip()}
    added: list[str] = []
    for raw in remote_text.splitlines():
        line = raw.strip()
        if not line or line in existing:
            continue
        existing.add(line)
        added.append(line)

    if added:
        text = "\n".join(local_lines + added) + "\n"
        _write_text_atomic(local_path, text)
    return len(added)


class FlowsealStrategiesUpdater:
    """Проверка и применение обновления стратегий из Flowseal."""

    def __init__(self, root: Path | None = None, progress_callback=None, opt_dir: Path | None = None, service_manager=None):
        self.root = Path(root) if root else manager_dir()
        self.progress = progress_callback or (lambda message, percent=None: None)
        # /opt/zapret и менеджер службы вынесены в параметры: так обновление
        # можно проверить на временном каталоге, не трогая установку.
        self.opt_dir = Path(opt_dir) if opt_dir else Path("/opt/zapret")
        self._service_manager = service_manager

    # --- пути ---------------------------------------------------------------

    @property
    def strategy_dir(self) -> Path:
        return self.root / "files" / "strategy"

    @property
    def bin_dir(self) -> Path:
        return self.root / "files" / "bin"

    @property
    def list_dir(self) -> Path:
        return self.root / "files" / "lists"

    @property
    def system_dir(self) -> Path:
        return self.root / "zapret" / "system"

    @property
    def version_file(self) -> Path:
        return self.root / "utils" / VERSION_FILE_NAME

    def local_version(self) -> str | None:
        """SHA коммита Flowseal, от которого получены локальные стратегии."""
        try:
            value = self.version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def _save_version(self, sha: str) -> None:
        _write_text_atomic(self.version_file, sha + "\n")

    # --- проверка обновления ------------------------------------------------

    def check_for_update(self) -> tuple[str | None, str | None]:
        """Возвращает (sha последнего коммита Flowseal, ошибка).

        Основной способ — Atom-лента коммитов (без лимита API); GitHub API
        используется как резервный, если лента недоступна.
        """
        sha, error = self._check_via_atom()
        if sha:
            return sha, None
        api_sha, api_error = self._check_via_api()
        if api_sha:
            return api_sha, None
        return None, error or api_error

    def _check_via_atom(self) -> tuple[str | None, str | None]:
        try:
            text = _http_get(FLOWSEAL_ATOM_URL, timeout=15).decode("utf-8", errors="replace")
        except Exception as exc:
            return None, _http_error_text(exc, "Проверка стратегий Flowseal (лента коммитов)")
        match = COMMIT_SHA_RE.search(text)
        if not match:
            return None, "в ленте коммитов Flowseal не найден SHA"
        return match.group(1), None

    def _check_via_api(self) -> tuple[str | None, str | None]:
        try:
            payload = json.loads(_http_get(FLOWSEAL_COMMIT_API, timeout=15).decode("utf-8"))
            sha = (payload or {}).get("sha")
            if not sha:
                return None, "GitHub вернул ответ без SHA коммита"
            return sha, None
        except Exception as exc:
            message = _http_error_text(exc, "Проверка стратегий Flowseal")
            get_error_logger().error("Проверка стратегий Flowseal: %s", exc, exc_info=True)
            return None, message

    def has_update(self) -> tuple[bool, str | None, str | None]:
        """(есть ли обновление, sha, ошибка)."""
        sha, error = self.check_for_update()
        if error:
            return False, None, error
        local = self.local_version()
        return sha != local, sha, None

    # --- применение ---------------------------------------------------------

    def update(self, apply: bool = True, force: bool = False) -> dict:
        """Скачивает набор Flowseal и применяет его (или только проверяет при apply=False)."""
        report: dict = {
            "flowseal_sha": None,
            "up_to_date": False,
            "strategies_updated": [],
            "strategies_unchanged": [],
            "strategies_skipped": {},
            "bins_added": [],
            "lists_added": {},
            "system_files_updated": [],
            "service_restarted": False,
            "backup_path": None,
            "applied": False,
            "errors": [],
        }

        sha, error = self.check_for_update()
        if error:
            report["errors"].append(error)
            return report
        report["flowseal_sha"] = sha

        if sha == self.local_version() and not force:
            report["up_to_date"] = True
            return report

        temp_dir = tempfile.mkdtemp(prefix="flowseal_update_")
        try:
            self.progress("Скачивание набора стратегий Flowseal...", 10)
            archive_path = os.path.join(temp_dir, "flowseal.tar.gz")
            try:
                self._download(FLOWSEAL_TARBALL_URL, archive_path)
            except Exception as exc:
                report["errors"].append(_http_error_text(exc, "Скачивание набора Flowseal"))
                return report

            self.progress("Распаковка архива Flowseal...", 25)
            extract_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            with tarfile.open(archive_path, "r:gz") as tar:
                try:
                    tar.extractall(path=extract_dir, filter="data")
                except TypeError:  # Python < 3.12
                    tar.extractall(path=extract_dir)

            source_root = self._find_source_root(Path(extract_dir))
            if source_root is None:
                report["errors"].append("в архиве Flowseal не найден корень репозитория")
                return report

            if apply:
                # Полное обновление приложения обновляет только /opt/zapret,
                # поэтому локальная копия скриптов службы может отставать.
                self._sync_system_from_opt()

            allowed = known_placeholders(self._local_starter())
            strategies, skipped = self._convert_strategies(source_root, allowed)
            report["strategies_skipped"] = skipped

            bins_to_copy = self._collect_bins(source_root)
            list_payloads = self._collect_lists(source_root)
            system_payloads = self._collect_system_files()

            if not apply:
                report["strategies_updated"] = sorted(strategies)
                report["bins_added"] = [path.name for path in bins_to_copy]
                report["lists_added"] = {name: -1 for name in list_payloads}
                report["system_files_updated"] = sorted(system_payloads)
                return report

            if not strategies and not bins_to_copy and not list_payloads and not system_payloads:
                report["up_to_date"] = True
                self._save_version(sha)
                return report

            self.progress("Резервная копия текущих файлов...", 35)
            report["backup_path"] = self._backup()

            self.progress("Запись стратегий...", 50)
            for name, rules in sorted(strategies.items()):
                target = self.strategy_dir / name
                new_text = "\n".join(rules) + "\n"
                if target.is_file() and target.read_text(encoding="utf-8") == new_text:
                    report["strategies_unchanged"].append(name)
                    continue
                _write_text_atomic(target, new_text)
                report["strategies_updated"].append(name)

            self.progress("Обновление payload'ов...", 65)
            self.bin_dir.mkdir(parents=True, exist_ok=True)
            for src in bins_to_copy:
                shutil.copy2(src, self.bin_dir / src.name)
                report["bins_added"].append(src.name)

            self.progress("Дополнение списков...", 75)
            for name, text in list_payloads.items():
                added = _merge_list_file(self.list_dir / name, text)
                if added:
                    report["lists_added"][name] = added

            self.progress("Проверка скриптов службы...", 85)
            report["system_files_updated"] = self._apply_system_files(system_payloads)

            self._save_version(sha)
            report["applied"] = True

            needs_restart = bool(
                report["bins_added"] or report["lists_added"] or report["system_files_updated"]
            )
            if needs_restart and self._service_active():
                self.progress("Перезапуск службы zapret...", 95)
                manager = self._service_manager or ServiceManager()
                report["service_restarted"] = bool(manager.restart_service())

            self.progress("Обновление стратегий завершено", 100)
            return report
        except Exception as exc:
            get_error_logger().error("Обновление стратегий Flowseal: %s", exc, exc_info=True)
            report["errors"].append(str(exc))
            return report
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # --- внутренние шаги ----------------------------------------------------

    def _download(self, url: str, dest: str) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=180) as response, open(dest, "wb") as out:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            raise RuntimeError("архив Flowseal пустой или не скачан")

    @staticmethod
    def _find_source_root(extract_dir: Path) -> Path | None:
        """Корень распакованного tarball (единственный подкаталог)."""
        if (extract_dir / "bin").is_dir() and any(extract_dir.glob("*.bat")):
            return extract_dir
        candidates = [p for p in extract_dir.iterdir() if p.is_dir()]
        for candidate in candidates:
            if (candidate / "bin").is_dir() and any(candidate.glob("*.bat")):
                return candidate
        return candidates[0] if candidates else None

    def _local_starter(self) -> Path:
        """Актуальный starter.sh: сначала действующий /opt/zapret, затем установка, затем пакет.

        Полное обновление приложения копирует zapret/ только в /opt/zapret
        (ManagerUpdater исключает этот каталог), поэтому локальная копия в
        ~/Zapret_DPI_Manager может быть старше и не знать новых плейсхолдеров.
        """
        for candidate in (self.opt_dir / "starter.sh", self.system_dir / "starter.sh"):
            if candidate.is_file():
                return candidate
        return starter_path()

    def _sync_system_from_opt(self) -> list[str]:
        """Подтягивает локальную копию скриптов службы из /opt/zapret, если она отстала."""
        synced: list[str] = []
        for name in SYSTEM_FILES:
            installed = self.opt_dir / name
            if not installed.is_file():
                continue
            try:
                installed_text = installed.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            local = self.system_dir / name
            try:
                if local.is_file() and local.read_text(encoding="utf-8", errors="replace") == installed_text:
                    continue
            except OSError:
                pass
            _write_text_atomic(local, installed_text)
            synced.append(name)
        return synced

    def _convert_strategies(
        self, source_root: Path, allowed: set[str]
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """Конвертирует .bat; несовместимые стратегии возвращает отдельно."""
        strategies: dict[str, list[str]] = {}
        skipped: dict[str, list[str]] = {}
        for bat in sorted(source_root.glob("*.bat")):
            name = NAME_MAP.get(bat.stem)
            if not name:
                continue
            rules, problems = convert_bat_text(
                bat.read_text(encoding="utf-8", errors="replace"), known=allowed
            )
            if problems:
                skipped[name] = problems
            else:
                strategies[name] = rules
        return strategies, skipped

    def _collect_bins(self, source_root: Path) -> list[Path]:
        """Payload'ы источника, которых нет локально или которые отличаются."""
        source_bin = source_root / "bin"
        result: list[Path] = []
        if not source_bin.is_dir():
            return result
        for name in FLOWSEAL_BIN_FILES:
            src = source_bin / name
            if not src.is_file():
                continue
            local = self.bin_dir / name
            if local.is_file() and local.read_bytes() == src.read_bytes():
                continue
            result.append(src)
        return result

    def _collect_lists(self, source_root: Path) -> dict[str, str]:
        """Тексты списков источника (без *_user.txt и backup-файлов)."""
        source_lists = source_root / "lists"
        result: dict[str, str] = {}
        if not source_lists.is_dir():
            return result
        for name in FLOWSEAL_LIST_FILES:
            src = source_lists / name
            if src.is_file():
                result[name] = src.read_text(encoding="utf-8", errors="replace")
        return result

    def _collect_system_files(self) -> dict[str, str]:
        """Актуальные starter.sh/stopper.sh из нашего репозитория (raw/main)."""
        result: dict[str, str] = {}
        for name in SYSTEM_FILES:
            url = f"{GITHUB_RAW_URL}/zapret/system/{name}"
            try:
                remote = fetch_bytes(url, timeout=20).decode("utf-8")
            except Exception as exc:
                get_error_logger().error("Чтение %s из репозитория: %s", url, exc, exc_info=True)
                continue
            local = self.system_dir / name
            if local.is_file() and local.read_text(encoding="utf-8", errors="replace") == remote:
                continue
            result[name] = remote
        return result

    def _apply_system_files(self, payloads: dict[str, str]) -> list[str]:
        """Записывает скрипты службы локально и в /opt/zapret (если он есть)."""
        updated: list[str] = []
        if not payloads:
            return updated
        self.system_dir.mkdir(parents=True, exist_ok=True)
        has_opt = self.opt_dir.is_dir() and sudo_available()
        for name, text in sorted(payloads.items()):
            _write_text_atomic(self.system_dir / name, text)
            updated.append(name)
            if not has_opt:
                continue
            temp_path = Path(tempfile.gettempdir()) / f"zapret_{name}"
            temp_path.write_text(text, encoding="utf-8")
            result = run_sudo(["cp", str(temp_path), str(self.opt_dir / name)], timeout=60)
            code, _out, err = result
            if code != 0:
                get_error_logger().error(
                    "Не удалось обновить %s: %s", self.opt_dir / name, (err or "").strip()
                )
        return updated

    def _service_active(self) -> bool:
        try:
            manager = self._service_manager or ServiceManager()
            return manager.get_service_status() == "active"
        except Exception:
            return False

    def _backup(self) -> str | None:
        """Резервная копия данных и скриптов перед изменением."""
        try:
            cache_dir().mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            archive = cache_dir() / f"{BACKUP_PREFIX}{stamp}.tar.gz"
            paths = [p for p in (self.strategy_dir, self.bin_dir, self.list_dir, self.system_dir) if p.exists()]
            if not paths:
                return None
            with tarfile.open(archive, "w:gz") as tar:
                for path in paths:
                    tar.add(path, arcname=str(path.relative_to(self.root)))
            return str(archive)
        except Exception as exc:
            get_error_logger().error("Резервная копия стратегий: %s", exc, exc_info=True)
            return None

    # --- отчёт --------------------------------------------------------------

    @staticmethod
    def format_report(report: dict) -> list[str]:
        """Отчёт в виде строк для лога окна обновления."""
        lines: list[str] = []
        sha = (report.get("flowseal_sha") or "")[:8]
        if report.get("up_to_date"):
            lines.append(f"✅ Стратегии уже актуальны (Flowseal {sha or 'без изменений'})")
            return lines
        if report.get("strategies_updated"):
            lines.append(f"📄 Обновлено стратегий: {len(report['strategies_updated'])}")
        if report.get("strategies_unchanged"):
            lines.append(f"• Без изменений: {len(report['strategies_unchanged'])}")
        for name, problems in sorted((report.get("strategies_skipped") or {}).items()):
            lines.append(f"⚠️ {name} пропущена: {'; '.join(problems)}")
        if report.get("bins_added"):
            lines.append(f"📦 Payload'ов обновлено: {len(report['bins_added'])}")
        for name, count in sorted((report.get("lists_added") or {}).items()):
            lines.append(f"📋 {name}: добавлено строк {count}")
        if report.get("system_files_updated"):
            lines.append(f"🔧 Скрипты службы обновлены: {', '.join(report['system_files_updated'])}")
        if report.get("service_restarted"):
            lines.append("🔄 Служба zapret перезапущена")
        if report.get("backup_path"):
            lines.append(f"💾 Резервная копия: {report['backup_path']}")
        for error in report.get("errors") or []:
            lines.append(f"❌ {error}")
        if not lines:
            lines.append("Нет изменений")
        return lines
