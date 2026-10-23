"""Скачивание файлов GitHub с обходом недоступных хостов.

В РФ `raw.githubusercontent.com` периодически недоступен, хотя `github.com`
отвечает. Поэтому прямой GET заменён цепочкой провайдеров:

1. `raw.githubusercontent.com` — быстрый путь;
2. `api.github.com/.../contents` с `Accept: application/vnd.github.raw` — другой
   хост и другой SNI, отдаёт сырые байты (включая бинарники). Без токена лимит
   60 запросов в час на IP, поэтому при HTTP 403 провайдер отключается на десять
   минут и запросы идут дальше по цепочке;
3. `codeload.github.com` — архив репозитория (`tar.gz`), файл вынимается из tar.
   Архив кэшируется на `(owner, repo, ref)`, поэтому пачка файлов из одного
   репозитория скачивается один раз;
4. зеркало: `cdn.jsdelivr.net/gh/...` по умолчанию, иначе шаблон из переменной
   окружения `ZAPRET_GH_MIRROR` (или `off` — выключить);
5. последняя удачная копия из `~/.cache/zapret_dpi_manager/gh_cache` — чтобы при
   полном отказе сети обновление не падало, а работало на прошлых данных.

Ссылка вида `https://github.com/<owner>/<repo>/raw/<ref>/<path>` сама контент не
отдаёт (302 на `raw.githubusercontent.com`), поэтому как запасной путь не
используется.

URL, не являющиеся raw-ссылками GitHub, скачиваются как раньше — обычным GET,
исключения пробрасываются без изменений.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core.app_logging import get_error_logger
from core.manager_config import GITHUB_MIRROR_TEMPLATE

USER_AGENT = "Zapret-DPI-Manager/2"
DEFAULT_TIMEOUT = 20.0
ARCHIVE_TIMEOUT = 120.0
ARCHIVE_TTL = 15 * 60  # архив перекачивается, если старше 15 минут
ARCHIVE_MAX_BYTES = 256 * 1024 * 1024
CACHE_MAX_AGE = 30 * 24 * 3600  # последняя удачная копия живёт 30 дней
API_DISABLE_SECONDS = 600  # пауза провайдера api.github.com после HTTP 403

_RAW_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$",
    re.IGNORECASE,
)

_lock = threading.Lock()
_api_off_until = 0.0


class FetchError(Exception):
    """Все провайдеры отказали; в тексте перечислены причины по каждому."""


class ProviderSkip(Exception):
    """Провайдер сознательно пропущен (выключен или временно отключён)."""


@dataclass(frozen=True)
class RawLocation:
    """Разобранная raw-ссылка GitHub."""

    owner: str
    repo: str
    ref: str
    path: str

    @property
    def url(self) -> str:
        return f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.ref}/{self.path}"


def parse_raw_url(url: str) -> RawLocation | None:
    """raw.githubusercontent.com/{owner}/{repo}/{ref}/{path} → RawLocation.

    Ветка/тег/хеш должны быть без слэшей: ссылку с `refs/heads/feature/x`
    разобрать однозначно нельзя, такие URL уходят в обычный GET.
    """
    match = _RAW_RE.match((url or "").strip())
    if not match:
        return None
    owner, repo, ref, path = match.groups()
    return RawLocation(owner=owner, repo=repo, ref=ref, path=path)


# --- транспорт (подменяется в тестах) ---------------------------------------


def _default_get(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


_get = _default_get


# --- провайдеры -------------------------------------------------------------


def _fetch_raw(location: RawLocation, timeout: float) -> bytes:
    return _get(location.url, timeout=timeout)


def _api_url(location: RawLocation) -> str:
    path = urllib.parse.quote(location.path, safe="/")
    ref = urllib.parse.quote(location.ref, safe="")
    return f"https://api.github.com/repos/{location.owner}/{location.repo}/contents/{path}?ref={ref}"


def _fetch_api(location: RawLocation, timeout: float) -> bytes:
    global _api_off_until
    with _lock:
        if _api_off_until > time.time():
            raise ProviderSkip("api.github.com временно отключён (был HTTP 403, лимит запросов)")
    try:
        return _get(
            _api_url(location),
            timeout=timeout,
            headers={"Accept": "application/vnd.github.raw"},
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            with _lock:
                _api_off_until = time.time() + API_DISABLE_SECONDS
        raise


def _archive_candidates(location: RawLocation) -> list[str]:
    base = f"https://codeload.github.com/{location.owner}/{location.repo}/tar.gz"
    ref = urllib.parse.quote(location.ref, safe="")
    return [f"{base}/{ref}", f"{base}/refs/heads/{ref}", f"{base}/refs/tags/{ref}"]


def _archive_cache_path(location: RawLocation) -> Path:
    key = hashlib.sha1(f"{location.owner}/{location.repo}/{location.ref}".encode()).hexdigest()
    return cache_dir() / "gh_archive" / f"{key}.tar.gz"


def _download_archive(url: str, dest: Path, timeout: float) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = 0
        with open(dest, "wb") as out:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > ARCHIVE_MAX_BYTES:
                    raise FetchError(f"архив больше {ARCHIVE_MAX_BYTES // (1024 * 1024)} МБ")
                out.write(chunk)


def _archive_file(location: RawLocation, timeout: float) -> Path:
    """Архив репозитория с дисковым кэшем на (owner, repo, ref)."""
    cached = _archive_cache_path(location)
    fresh = cached.is_file() and (time.time() - cached.stat().st_mtime) < ARCHIVE_TTL
    if fresh:
        return cached

    last_error: Exception | None = None
    for url in _archive_candidates(location):
        temp_dir = Path(tempfile.mkdtemp(prefix="gh_archive_"))
        temp_file = temp_dir / "repo.tar.gz"
        try:
            _download_archive(url, temp_file, timeout)
            cached.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_file, cached)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 404:
                raise
            continue
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return cached

    if last_error is not None:
        raise last_error
    raise FetchError("архив репозитория недоступен")


def _extract_member(archive: Path, path: str) -> bytes:
    suffix = "/" + path.lstrip("/")
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.isfile() and member.name.endswith(suffix):
                handle = tar.extractfile(member)
                if handle is not None:
                    return handle.read()
    raise FetchError(f"в архиве нет файла {path}")


def _fetch_archive(location: RawLocation, timeout: float) -> bytes:
    archive = _archive_file(location, timeout)
    return _extract_member(archive, location.path)


def mirror_template() -> str:
    """Шаблон зеркала: переменная окружения важнее настройки в коде."""
    return (os.environ.get("ZAPRET_GH_MIRROR") or GITHUB_MIRROR_TEMPLATE).strip()


def _mirror_url(location: RawLocation) -> str | None:
    template = mirror_template()
    if not template or template.lower() in {"off", "none", "0"}:
        return None
    if "{" not in template:
        # Префиксное зеркало вида https://ghproxy.net/
        return template.rstrip("/") + "/" + location.url
    return template.format(
        owner=location.owner,
        repo=location.repo,
        ref=location.ref,
        path=location.path,
        url=location.url,
    )


def _fetch_mirror(location: RawLocation, timeout: float) -> bytes:
    url = _mirror_url(location)
    if not url:
        raise ProviderSkip("зеркало отключено")
    return _get(url, timeout=timeout)


PROVIDERS = (_fetch_raw, _fetch_api, _fetch_archive, _fetch_mirror)


# --- кэш последней удачной копии -------------------------------------------


def cache_dir() -> Path:
    return Path.home() / ".cache" / "zapret_dpi_manager"


def _cache_paths(url: str) -> tuple[Path, Path]:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    root = cache_dir() / "gh_cache"
    return root / f"{key}.bin", root / f"{key}.json"


def _cache_store(url: str, payload: bytes, source: str) -> None:
    body, meta = _cache_paths(url)
    try:
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(payload)
        meta.write_text(
            json.dumps(
                {"url": url, "source": source, "fetched_at": int(time.time()), "size": len(payload)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        get_error_logger().error("Кэш GitHub (%s): %s", url, exc)


def _cache_load(url: str) -> bytes | None:
    body, meta = _cache_paths(url)
    try:
        if not body.is_file():
            return None
        if (time.time() - body.stat().st_mtime) > CACHE_MAX_AGE:
            return None
        payload = body.read_bytes()
    except OSError:
        return None
    if not payload:
        return None
    try:
        stored_url = json.loads(meta.read_text(encoding="utf-8")).get("url")
        if stored_url and stored_url != url:
            return None
    except (OSError, ValueError):
        pass
    return payload


# --- публичный API ----------------------------------------------------------


def _describe(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        return str(reason) if reason is not None else str(exc)
    return str(exc) or exc.__class__.__name__


def _provider_name(provider) -> str:
    return {
        _fetch_raw: "raw.githubusercontent.com",
        _fetch_api: "api.github.com",
        _fetch_archive: "codeload.github.com",
        _fetch_mirror: "зеркало",
    }.get(provider, provider.__name__)


def fetch_bytes(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> bytes:
    """Скачивает URL; для raw-ссылок GitHub перебирает провайдеров по цепочке."""
    location = parse_raw_url(url)
    if location is None:
        return _get(url, timeout=timeout)

    failures: list[str] = []
    for provider in PROVIDERS:
        name = _provider_name(provider)
        try:
            payload = provider(location, timeout)
        except ProviderSkip as exc:
            failures.append(f"{name}: {exc}")
            continue
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Файла нет в ветке — остальные провайдеры ответят тем же.
                raise
            failures.append(f"{name}: {_describe(exc)}")
            continue
        except Exception as exc:
            failures.append(f"{name}: {_describe(exc)}")
            continue
        if payload:
            _cache_store(url, payload, name)
            return payload
        failures.append(f"{name}: пустой ответ")

    if use_cache:
        cached = _cache_load(url)
        if cached is not None:
            return cached

    detail = "; ".join(failures) or "нет доступных провайдеров"
    get_error_logger().error("Не удалось скачать %s: %s", url, detail)
    raise FetchError(f"GitHub недоступен ({detail})")


def fetch_to_file(
    url: str,
    dest: str | Path,
    *,
    timeout: float = ARCHIVE_TIMEOUT,
    use_cache: bool = True,
) -> int:
    """Скачивает URL в файл; возвращает размер записанных данных."""
    payload = fetch_bytes(url, timeout=timeout, use_cache=use_cache)
    if not payload:
        raise FetchError("источник вернул пустой файл")
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return len(payload)


def reset_provider_state() -> None:
    """Сбрасывает паузу api.github.com (для тестов и ручной проверки)."""
    global _api_off_until
    with _lock:
        _api_off_until = 0.0
