"""Список Cloudflare-доменов для фолбэка (аналог cfproxy-domains апстрима).

Список живёт в репозитории апстрима и периодически меняется, поэтому движок
скачивает его с GitHub (через тот же кэш, что и остальное приложение) и
держит в памяти; при недоступности сети используется зашитый запасной набор
из :mod:`tgws.config`.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List

from .config import CFPROXY_DEFAULT_DOMAINS

log = logging.getLogger("tgws.cf")

LIST_URL = (
    "https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/"
    ".github/cfproxy-domains.txt"
)
USER_AGENT = "Zapret-DPI-Manager-tgws/1.0"
REFRESH_INTERVAL = 6 * 3600.0


def cache_path() -> Path:
    import os

    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "zapret_dpi_manager" / "tgws_cfproxy_domains.txt"


def _parse(text: str) -> List[str]:
    domains: List[str] = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "/" in line or " " in line:
            continue
        if "." not in line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        domains.append(line)
    return domains


def _read_cache() -> List[str]:
    try:
        return _parse(cache_path().read_text(encoding="utf-8"))
    except OSError:
        return []


def _write_cache(domains: List[str]) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(domains) + "\n", encoding="utf-8")
    except OSError as exc:
        log.debug("не удалось сохранить кэш CF-доменов: %s", exc)


def _download(url: str, timeout: float) -> bytes:
    """Скачивание через общую цепочку core.github_fetch, если она доступна."""
    try:
        from core.github_fetch import fetch_bytes

        return fetch_bytes(url, timeout=timeout)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - цепочка сама сообщает о причине
        log.debug("github_fetch не справился: %r", exc)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_domains(timeout: float = 10.0) -> List[str]:
    """Скачивает актуальный список; при ошибке — пустой список."""
    try:
        text = _download(LIST_URL, timeout).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("список CF-доменов недоступен: %s", exc)
        return []
    domains = _parse(text)
    if domains:
        _write_cache(domains)
    return domains


def initial_domains() -> List[str]:
    """Домены на старте: кэш → зашитый список."""
    return _read_cache() or list(CFPROXY_DEFAULT_DOMAINS)


def start_refresh(interval: float = REFRESH_INTERVAL) -> threading.Thread:
    """Фоновое обновление списка (демон-поток, сеть — в своём потоке)."""

    def _loop() -> None:
        while True:
            try:
                domains = fetch_domains()
                if domains:
                    balancer.update_domains(domains)
                    log.info("CF-доменов загружено: %d", len(domains))
            except Exception as exc:  # noqa: BLE001 - фоновая задача не падает
                log.debug("обновление CF-доменов не удалось: %r", exc)
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name="tgws-cf-domains", daemon=True)
    thread.start()
    return thread


class DomainBalancer:
    """Выбирает базовый Cloudflare-домен для медиа-фолбэка по DC."""

    def __init__(self) -> None:
        self._domains: List[str] = list(CFPROXY_DEFAULT_DOMAINS)
        self._active: dict[int, str] = {}
        self._lock = threading.Lock()

    @property
    def domains(self) -> List[str]:
        with self._lock:
            return list(self._domains)

    def update_domains(self, domains: List[str]) -> None:
        cleaned = _parse("\n".join(domains))
        if not cleaned:
            return
        with self._lock:
            self._domains = cleaned
            self._active.clear()

    def active_for(self, dc: int) -> str:
        with self._lock:
            return self._active.get(dc, "")

    def ordered_for(self, dc: int) -> List[str]:
        with self._lock:
            active = self._active.get(dc)
            domains = list(self._domains)
        if active and active in domains:
            domains.remove(active)
            domains.insert(0, active)
        return domains

    def mark_working(self, dc: int, domain: str) -> None:
        with self._lock:
            self._active[dc] = domain


balancer = DomainBalancer()
