import re
import urllib.error

from core.app_logging import get_error_logger
from core.github_fetch import FetchError, fetch_bytes


class BaseUpdater:
    def __init__(self, version_url, current_version, name="Объект"):
        self.version_url = version_url
        self.current_version = current_version
        self.name = name

    def check_for_updates_detailed(self):
        """Как check_for_updates, но различает «нет обновлений» и ошибку проверки.

        Возвращает (latest_version, info, error): error=None — проверка удалась
        (в том числе когда обновлений нет); иначе текст причины.
        """
        try:
            content = fetch_bytes(self.version_url, timeout=10).decode('utf-8').strip()
        except FetchError as e:
            return None, None, str(e)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, None, "манифест версии ещё не опубликован (HTTP 404)"
            return None, None, f"сервер вернул HTTP {e.code}"
        except urllib.error.URLError as e:
            detail = str(e.reason) if e.reason is not None else str(e)
            return None, None, f"нет связи: {detail}"
        except Exception as e:
            get_error_logger().error(
                "Проверка обновлений (%s): %s", self.name, e, exc_info=True
            )
            return None, None, str(e)

        lines = content.split('\n')
        if len(lines) >= 2:
            latest_version = lines[0].strip()
            download_url = lines[1].strip()

            if self.is_newer_version(latest_version, self.current_version):
                return latest_version, {
                    'download_url': download_url,
                    'description': f'Доступна новая версия {self.name}: {latest_version}'
                }, None

        return None, None, None

    def check_for_updates(self):
        """Проверяет наличие обновлений через простой txt файл"""
        latest_version, info, _error = self.check_for_updates_detailed()
        return latest_version, info

    @staticmethod
    def _version_parts(value: str) -> tuple[int, ...]:
        """«2.7.10» → (2, 7, 10); нечисловые куски считаются нулём."""
        cleaned = (value or "").strip().lstrip("vV")
        parts = []
        for chunk in re.split(r"[._\-+]", cleaned):
            parts.append(int(chunk) if chunk.isdigit() else 0)
        return tuple(parts)

    def is_newer_version(self, latest, current):
        """Сравнение по числовым компонентам: 2.7.10 новее 2.7.9."""
        return self._version_parts(latest) > self._version_parts(current)
