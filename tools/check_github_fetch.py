#!/usr/bin/env python3
"""Проверка загрузчика GitHub (core/github_fetch.py) без обращения к сети.

Подменяет транспорт и архивный загрузчик, поэтому проверяет именно логику:
порядок провайдеров, окончательность HTTP 404, паузу api.github.com после 403,
извлечение файла из архива, зеркало и откат на последнюю удачную копию.

Использование:
    python3 tools/check_github_fetch.py
    python3 tools/check_github_fetch.py -v
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
import tempfile
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import github_fetch as gh  # noqa: E402

RAW_URL = "https://raw.githubusercontent.com/mops1k/Zapret-DPI-Manager/main/version.txt"
RAW_URL_2 = "https://raw.githubusercontent.com/ImMALWARE/zapret-linux-easy/main/bins/x86_64/nfqws"
BINARY_URL = "https://github.com/mops1k/Zapret-DPI-Manager/releases/latest/download/zapret.tar.gz"

_checks = 0
_failures: list[str] = []
_verbose = False


def check(name: str, condition: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if condition:
        if _verbose:
            print(f"  ok   {name}")
        return
    _failures.append(f"{name}{(': ' + detail) if detail else ''}")
    print(f"  FAIL {name}{(': ' + detail) if detail else ''}")


def _isolate_cache(root: Path) -> None:
    gh.cache_dir = lambda: root  # type: ignore[assignment]


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", code, "boom", {}, io.BytesIO(b""))


def _offline(_url: str, **_kwargs):
    raise urllib.error.URLError("нет сети")


def _offline_archive(_url: str, _dest: Path, _timeout: float) -> None:
    raise urllib.error.URLError("нет сети")


def test_parse() -> None:
    print("parse_raw_url")
    loc = gh.parse_raw_url(RAW_URL)
    check("raw URL разобран", loc is not None)
    check("owner/repo/ref/path", loc == gh.RawLocation("mops1k", "Zapret-DPI-Manager", "main", "version.txt"))
    check("вложенный путь", gh.parse_raw_url(RAW_URL_2).path == "bins/x86_64/nfqws")
    check("github.com/.../raw/ не raw-ссылка", gh.parse_raw_url(
        "https://github.com/mops1k/Zapret-DPI-Manager/raw/main/version.txt") is None)
    check("посторонний URL не raw-ссылка", gh.parse_raw_url(BINARY_URL) is None)


def test_chain_fallbacks() -> None:
    print("цепочка провайдеров")
    calls: list[str] = []
    original = gh._get
    original_download = gh._download_archive
    try:
        def fake_get(url: str, **_kwargs):
            calls.append(url)
            if "raw.githubusercontent.com" in url:
                raise urllib.error.URLError("Connection reset by peer")
            if "api.github.com" in url:
                return b"9.9.9\n"
            raise AssertionError(f"неожиданный URL: {url}")

        gh._get = fake_get
        with tempfile.TemporaryDirectory() as tmp:
            _isolate_cache(Path(tmp))
            gh.reset_provider_state()
            payload = gh.fetch_bytes(RAW_URL, timeout=1)
            check("ответ от api.github.com", payload == b"9.9.9\n", repr(payload))
            check("raw опрошен первым", "raw.githubusercontent.com" in calls[0], calls[0])
            check("api вторым", "api.github.com" in calls[1], calls[1])
            gh._get = original
            check("успешный ответ закэширован", gh._cache_load(RAW_URL) == b"9.9.9\n")

            # Сеть пропала полностью — отдаётся последняя удачная копия.
            gh._get = _offline
            gh._download_archive = _offline_archive
            gh.reset_provider_state()
            os.environ["ZAPRET_GH_MIRROR"] = "off"
            payload = gh.fetch_bytes(RAW_URL, timeout=1)
            check("откат на кэш", payload == b"9.9.9\n", repr(payload))
    finally:
        gh._get = original
        gh._download_archive = original_download
        os.environ.pop("ZAPRET_GH_MIRROR", None)


def test_404_is_final() -> None:
    print("HTTP 404")
    calls: list[str] = []
    original = gh._get
    original_download = gh._download_archive
    try:
        def fake_get(url: str, **_kwargs):
            calls.append(url)
            raise _http_error(404)

        gh._get = fake_get
        with tempfile.TemporaryDirectory() as tmp:
            _isolate_cache(Path(tmp))
            gh.reset_provider_state()
            try:
                gh.fetch_bytes(RAW_URL, timeout=1)
                raised = None
            except urllib.error.HTTPError as exc:
                raised = exc
            except Exception as exc:  # noqa: BLE001
                raised = exc
            check("404 проброшен как HTTPError", isinstance(raised, urllib.error.HTTPError))
            check("после 404 цепочка остановлена", len(calls) == 1, str(calls))
    finally:
        gh._get = original
        gh._download_archive = original_download


def test_api_pause() -> None:
    print("пауза api.github.com после 403")
    original = gh._get
    original_download = gh._download_archive
    try:
        gh._download_archive = _offline_archive
        os.environ["ZAPRET_GH_MIRROR"] = "off"
        def fake_get(url: str, **_kwargs):
            if "api.github.com" in url:
                raise _http_error(403)
            raise urllib.error.URLError("нет сети")

        gh._get = fake_get
        with tempfile.TemporaryDirectory() as tmp:
            _isolate_cache(Path(tmp))
            gh.reset_provider_state()
            try:
                gh.fetch_bytes(RAW_URL, timeout=1)
            except gh.FetchError:
                pass
            calls: list[str] = []
            gh._get = lambda url, **kwargs: (calls.append(url), b"ok")[1]
            gh.fetch_bytes(RAW_URL, timeout=1)
            check("api пропущен при повторе", not any("api.github.com" in c for c in calls), str(calls))
    finally:
        gh._get = original
        gh._download_archive = original_download
        gh.reset_provider_state()
        os.environ.pop("ZAPRET_GH_MIRROR", None)


def test_archive() -> None:
    print("архив codeload")
    original_get = gh._get
    original_download = gh._download_archive
    downloads: list[str] = []

    def make_archive(dest: Path, member: str, payload: bytes) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, "w:gz") as tar:
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))

    def fake_download(url: str, dest: Path, _timeout: float) -> None:
        downloads.append(url)
        make_archive(dest, "Zapret-DPI-Manager-main/zapret/system/starter.sh", b"#!/bin/bash\n")

    try:
        gh._get = _offline
        gh._download_archive = fake_download
        gh.reset_provider_state()
        os.environ["ZAPRET_GH_MIRROR"] = "off"
        with tempfile.TemporaryDirectory() as tmp:
            _isolate_cache(Path(tmp))
            url = "https://raw.githubusercontent.com/mops1k/Zapret-DPI-Manager/main/zapret/system/starter.sh"
            payload = gh.fetch_bytes(url, timeout=1)
            check("файл извлечён из архива", payload == b"#!/bin/bash\n", repr(payload))
            gh.fetch_bytes(url, timeout=1)
            check("архив скачан один раз", len(downloads) == 1, str(downloads))
            try:
                missing = url.replace("starter.sh", "нет-такого.sh")
                gh.fetch_bytes(missing, timeout=1)
                found = False
            except gh.FetchError:
                found = True
            check("отсутствующий в архиве файл — ошибка", found)
    finally:
        gh._get = original_get
        gh._download_archive = original_download
        os.environ.pop("ZAPRET_GH_MIRROR", None)


def test_mirror() -> None:
    print("зеркало")
    loc = gh.parse_raw_url(RAW_URL)
    default = gh._mirror_url(loc)
    check("по умолчанию jsDelivr", default == "https://cdn.jsdelivr.net/gh/mops1k/Zapret-DPI-Manager@main/version.txt", default)
    os.environ["ZAPRET_GH_MIRROR"] = "off"
    check("off выключает зеркало", gh._mirror_url(loc) is None)
    os.environ["ZAPRET_GH_MIRROR"] = "https://ghproxy.net/"
    check("префиксное зеркало", gh._mirror_url(loc) == "https://ghproxy.net/" + RAW_URL, gh._mirror_url(loc))
    os.environ["ZAPRET_GH_MIRROR"] = "https://mirror.local/{owner}/{repo}/{ref}/{path}"
    check("шаблон зеркала", gh._mirror_url(loc) == "https://mirror.local/mops1k/Zapret-DPI-Manager/main/version.txt")
    os.environ.pop("ZAPRET_GH_MIRROR", None)


def test_plain_and_file() -> None:
    print("несырые URL и запись в файл")
    original = gh._get
    calls: list[str] = []
    try:
        def fake_get(url: str, **_kwargs):
            calls.append(url)
            return b"payload"

        gh._get = fake_get
        gh.reset_provider_state()
        with tempfile.TemporaryDirectory() as tmp:
            _isolate_cache(Path(tmp))
            check("обычный GET", gh.fetch_bytes(BINARY_URL, timeout=1) == b"payload")
            check("один запрос без провайдеров", len(calls) == 1, str(calls))
            dest = Path(tmp) / "out" / "file.bin"
            size = gh.fetch_to_file(RAW_URL, dest, timeout=1)
            check("файл записан", dest.read_bytes() == b"payload" and size == len(b"payload"))
    finally:
        gh._get = original


def main() -> int:
    global _verbose
    parser = argparse.ArgumentParser(description="Проверка core/github_fetch.py без сети")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _verbose = args.verbose

    test_parse()
    test_chain_fallbacks()
    test_404_is_final()
    test_api_pause()
    test_archive()
    test_mirror()
    test_plain_and_file()

    if _failures:
        print(f"\nПровалено {len(_failures)} из {_checks} проверок:")
        for item in _failures:
            print(f"  - {item}")
        return 1
    print(f"\nВсе {_checks} проверок прошли")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
