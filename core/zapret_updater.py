from __future__ import annotations

import os
import tempfile
import tarfile
import shutil
import time
import urllib.error
import urllib.request

from core.app_logging import get_error_logger
from core.github_fetch import fetch_bytes
from core.sudo_helper import run_sudo, sudo_available
from core.updater_base import BaseUpdater
from core.manager_config import VERSION_CONFIG
from core.manager_updater import ManagerUpdater
from core.platform_info import (
    is_valve_steamos,
    distro_log_label,
    os_release_id_normalized,
    detect_fwtype,
    ZAPRET_SYSTEMD_UNIT_DIR,
    ZAPRET_SYSTEMD_UNIT_PATH,
    ZAPRET_SYSTEMD_UNIT_PATH_LEGACY,
)

class ZapretUpdater(BaseUpdater):
    def __init__(self):
        super().__init__(
            version_url=VERSION_CONFIG["version_url"],
            current_version=VERSION_CONFIG["current_version"],
            name="zapret службы",
        )
        self.is_steamos = self.check_if_steamos()

    def check_if_steamos(self):
        """Только официальная SteamOS Valve (ID=steamos)."""
        print(
            f"Проверка Valve SteamOS… Дистрибутив: {distro_log_label()} "
            f"(ID={os_release_id_normalized() or '?'})"
        )
        return is_valve_steamos()

    def get_sudo_password(self, parent_window=None):
        """Совместимость: пароль вводит системный askpass при каждом `sudo -A`.

        Возвращает строку-маркер, если sudo и хелпер доступны, иначе None.
        Сам пароль приложение не получает и не хранит.
        """
        if not sudo_available():
            print("sudo или askpass-хелпер (core/askpass.py) недоступны")
            return None
        return "askpass"

    def run_with_sudo(self, command, password=None, description=""):
        """Выполняет команду через sudo -A (пароль спрашивает системный askpass)."""
        if description:
            print(f"Выполнение: {description}")

        if not sudo_available():
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': 'sudo или askpass-хелпер недоступны',
            }

        code, stdout, stderr = run_sudo(command, timeout=300)
        return {
            'returncode': code,
            'stdout': stdout,
            'stderr': stderr,
        }

    def stop_and_remove_zapret(self, password, progress_callback=None):
        """Останавливает и удаляет старую версию zapret.

        Возвращает False, если не выполнен критичный шаг (unit, /opt/zapret,
        снятие защиты SteamOS) — тогда вызывающий код обязан прервать обновление.
        """
        commands = [
            (['systemctl', 'stop', 'zapret'], "Остановка службы zapret", False),
            (['systemctl', 'disable', 'zapret'], "Отключение автозапуска", False),
            (["rm", "-f", ZAPRET_SYSTEMD_UNIT_PATH], "Удаление unit из /etc/systemd/system", True),
            (
                ["rm", "-f", ZAPRET_SYSTEMD_UNIT_PATH_LEGACY],
                "Удаление устаревшего unit из /usr (при возможности)",
                False,
            ),
            (['rm', '-rf', '/opt/zapret/'], "Удаление директории zapret", True)
        ]

        if self.is_steamos:
            commands.insert(0, (['steamos-readonly', 'disable'], "Отключение защиты SteamOS", True))

        critical_failed = []
        for cmd, description, critical in commands:
            if progress_callback:
                progress_callback(description, None)

            result = self.run_with_sudo(cmd, password, description)
            if result['returncode'] != 0:
                print(f"Предупреждение при {description}: {result['stderr']}")
                if critical:
                    critical_failed.append(description)

        if critical_failed:
            get_error_logger().error(
                "Не удалось выполнить критичные шаги удаления: %s",
                "; ".join(critical_failed),
            )
            return False

        return True

    def backup_opt_zapret(self, password, temp_dir):
        """Делает бэкап /opt/zapret и unit-файла перед удалением.

        True — если бэкап создан или сохранять было нечего.
        """
        try:
            if os.path.isdir('/opt/zapret'):
                archive = os.path.join(temp_dir, 'opt-zapret-backup.tar.gz')
                result = self.run_with_sudo(
                    ['tar', 'czf', archive, '-C', '/opt', 'zapret'],
                    password,
                    "Резервная копия /opt/zapret...",
                )
                if not result or result['returncode'] != 0 or not os.path.exists(archive):
                    print("Не удалось создать резервную копию /opt/zapret")
                    return False
            if os.path.isfile(ZAPRET_SYSTEMD_UNIT_PATH):
                unit_backup = os.path.join(temp_dir, 'zapret.service.bak')
                result = self.run_with_sudo(
                    ['cp', ZAPRET_SYSTEMD_UNIT_PATH, unit_backup],
                    password,
                    "Резервная копия unit-файла...",
                )
                if not result or result['returncode'] != 0:
                    print("Не удалось сохранить unit-файл")
                    return False
            return True
        except Exception as e:
            print(f"Ошибка резервного копирования: {e}")
            return False

    def restore_opt_zapret(self, password, temp_dir):
        """Восстанавливает /opt/zapret и unit из бэкапа, затем запускает службу."""
        try:
            archive = os.path.join(temp_dir, 'opt-zapret-backup.tar.gz')
            unit_backup = os.path.join(temp_dir, 'zapret.service.bak')
            if not os.path.exists(archive):
                print("Нет резервной копии /opt/zapret для отката")
                return False

            self.run_with_sudo(['rm', '-rf', '/opt/zapret'], password, "Очистка /opt/zapret...")
            result = self.run_with_sudo(
                ['tar', 'xzf', archive, '-C', '/opt'], password, "Восстановление /opt/zapret..."
            )
            if not result or result['returncode'] != 0:
                print("Не удалось восстановить /opt/zapret")
                return False

            if os.path.exists(unit_backup):
                self.run_with_sudo(
                    ['cp', unit_backup, ZAPRET_SYSTEMD_UNIT_PATH],
                    password,
                    "Восстановление unit-файла...",
                )

            self.run_with_sudo(['systemctl', 'daemon-reload'], password, "daemon-reload...")
            self.run_with_sudo(['systemctl', 'start', 'zapret'], password, "Запуск прежней службы...")
            print("Откат к прежней установке выполнен")
            return True
        except Exception as e:
            print(f"Ошибка отката: {e}")
            return False

    def copy_zapret_files(self, extract_dir, password, progress_callback=None):
        """Копирует файлы zapret в /opt/zapret"""
        try:
            if progress_callback:
                progress_callback("Создание директории /opt/zapret...", 50)

            result = self.run_with_sudo(
                ['mkdir', '-p', '/opt/zapret'],
                password,
                "Создание директории /opt/zapret..."
            )

            if not result or result['returncode'] != 0:
                print("Не удалось создать /opt/zapret")
                return False

            system_dir = None
            for root, dirs, files in os.walk(extract_dir):
                if 'system' in dirs:
                    system_dir = os.path.join(root, 'system')
                    break

            if not system_dir:
                print("Не найдена папка 'system' в архиве")
                return False

            if progress_callback:
                progress_callback("Копирование файлов системы...", 60)

            required_system_files = {"FWTYPE", "starter.sh", "stopper.sh"}
            failed_required = []
            for item in os.listdir(system_dir):
                src = os.path.join(system_dir, item)
                dst = os.path.join('/opt/zapret', item)

                if os.path.isfile(src):
                    result = self.run_with_sudo(
                        ['cp', src, dst],
                        password,
                        f"Копирование {item}..."
                    )
                elif os.path.isdir(src):
                    result = self.run_with_sudo(
                        ['cp', '-r', src, dst],
                        password,
                        f"Копирование папки {item}..."
                    )
                else:
                    continue

                if not result or result['returncode'] != 0:
                    print(f"Не удалось скопировать {item}")
                    if item in required_system_files:
                        failed_required.append(item)

            if failed_required:
                get_error_logger().error(
                    "Не скопированы обязательные файлы службы: %s",
                    ", ".join(sorted(failed_required)),
                )
                return False

            if progress_callback:
                progress_callback("Копирование бинарных файлов...", 70)

            arch = os.uname().machine
            bin_dirs = {
                'x86_64': 'x86_64',
                'i386': 'x86',
                'i686': 'x86',
                'armv7l': 'arm',
                'armv6l': 'arm',
                'aarch64': 'arm64'
            }

            bin_dir_name = bin_dirs.get(arch)
            if not bin_dir_name:
                print(f"Неизвестная архитектура: {arch}")
                get_error_logger().error("Неизвестная архитектура для nfqws: %s", arch)
                return False

            bins_dir = None
            for root, dirs, files in os.walk(extract_dir):
                if 'bins' in dirs:
                    bins_dir = os.path.join(root, 'bins')
                    break

            if not bins_dir:
                print("В архиве нет каталога bins")
                get_error_logger().error("В архиве обновления нет каталога bins")
                return False

            nfqws_path = os.path.join(bins_dir, bin_dir_name, 'nfqws')
            if not os.path.exists(nfqws_path):
                print(f"Нет бинарника nfqws для архитектуры {bin_dir_name}")
                get_error_logger().error(
                    "Нет бинарника nfqws для архитектуры %s", bin_dir_name
                )
                return False

            result = self.run_with_sudo(
                ['cp', nfqws_path, '/opt/zapret/nfqws'],
                password,
                "Копирование бинарного файла nfqws..."
            )
            if not result or result['returncode'] != 0:
                print("Не удалось скопировать nfqws")
                get_error_logger().error("Не удалось скопировать nfqws в /opt/zapret")
                return False

            chmod_result = self.run_with_sudo(
                ['chmod', '+x', '/opt/zapret/nfqws'], password, "chmod +x nfqws..."
            )
            if not chmod_result or chmod_result['returncode'] != 0:
                print("Не удалось выставить права на nfqws")
                get_error_logger().error("Не удалось chmod +x /opt/zapret/nfqws")
                return False

            # Единый источник истины: FWTYPE определяется по доступным бинарникам.
            fwtype = detect_fwtype()
            local_fwtype = os.path.join(tempfile.gettempdir(), "zapret_fwtype")
            with open(local_fwtype, "w", encoding="utf-8") as fh:
                fh.write(fwtype + "\n")
            result = self.run_with_sudo(
                ['cp', local_fwtype, '/opt/zapret/FWTYPE'],
                password,
                "Создание файла FWTYPE...",
            )
            if not result or result['returncode'] != 0:
                get_error_logger().error("Не удалось записать /opt/zapret/FWTYPE (%s)", fwtype)
                return False

            self.run_with_sudo(['chmod', '-R', 'o+r', '/opt/zapret/'], password)

            return True

        except Exception as e:
            print(f"Ошибка при копировании файлов: {e}")
            return False

    def update_manager_config(self, extract_dir):
        """Обновляет файл конфигурации менеджера"""
        try:
            config_path = None

            for root, dirs, files in os.walk(extract_dir):
                if 'manager_config.py' in files:
                    config_path = os.path.join(root, 'manager_config.py')
                    break

            if not config_path:
                print("Файл manager_config.py не найден в архиве")
                return

            home_dir = os.path.expanduser("~")

            target_dir = os.path.join(home_dir, "Zapret_DPI_Manager", "core")
            target_path = os.path.join(target_dir, "manager_config.py")

            os.makedirs(target_dir, exist_ok=True)
            shutil.copy2(config_path, target_path)

            print(f"Файл конфигурации обновлен: {target_path}")

        except Exception as e:
            print(f"Ошибка при обновлении конфигурации: {e}")

    def create_service_file(self, password, progress_callback=None):
        """Создает файл службы systemd"""
        try:
            if progress_callback:
                progress_callback("Создание службы systemd...", 80)

            service_content = """[Unit]
Description=zapret
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/zapret
ExecStart=/bin/bash /opt/zapret/starter.sh
ExecStop=/bin/bash /opt/zapret/stopper.sh

[Install]
WantedBy=multi-user.target
"""

            temp_service = tempfile.NamedTemporaryFile(mode='w', delete=False)
            temp_service.write(service_content)
            temp_service.close()

            result_mk = self.run_with_sudo(
                ["mkdir", "-p", ZAPRET_SYSTEMD_UNIT_DIR],
                password,
                "Подготовка каталога /etc/systemd/system…",
            )
            if not result_mk or result_mk["returncode"] != 0:
                print("Не удалось создать каталог для службы")
                os.unlink(temp_service.name)
                return False

            result = self.run_with_sudo(
                ["cp", temp_service.name, ZAPRET_SYSTEMD_UNIT_PATH],
                password,
                "Копирование файла службы...",
            )

            os.unlink(temp_service.name)

            if not result or result["returncode"] != 0:
                print("Не удалось создать службу")
                return False

            self.run_with_sudo(
                ["chmod", "644", ZAPRET_SYSTEMD_UNIT_PATH],
                password,
                "Права на файл службы...",
            )
            self.run_with_sudo(
                ["rm", "-f", ZAPRET_SYSTEMD_UNIT_PATH_LEGACY],
                password,
                "Удаление устаревшего unit из /usr…",
            )

            return True

        except Exception as e:
            print(f"Ошибка при создании службы: {e}")
            return False

    def enable_service(self, password, progress_callback=None):
        """Включает и запускает службу"""
        try:
            if progress_callback:
                progress_callback("Обновление systemd...", 85)

            result = self.run_with_sudo(
                ['systemctl', 'daemon-reload'],
                password,
                "Обновление systemd..."
            )

            if not result or result['returncode'] != 0:
                print("Не удалось обновить systemd")
                return False

            if progress_callback:
                progress_callback("Включение автозапуска...", 90)

            result = self.run_with_sudo(
                ['systemctl', 'enable', 'zapret.service'],
                password,
                "Включение автозапуска службы..."
            )

            if not result or result['returncode'] != 0:
                print("Не удалось включить автозапуск")

            if progress_callback:
                progress_callback("Запуск службы...", 95)

            result = self.run_with_sudo(
                ['systemctl', 'start', 'zapret.service'],
                password,
                "Запуск службы Zapret..."
            )

            if not result or result['returncode'] != 0:
                err = (result or {}).get('stderr', '')
                print(f"Не удалось запустить службу: {err}")
                get_error_logger().error("systemctl start zapret вернул ошибку: %s", err)
                return False

            if progress_callback:
                progress_callback("Служба успешно запущена", 100)

            time.sleep(2)
            check_result = self.run_with_sudo(
                ['systemctl', 'is-active', 'zapret'],
                password,
                "Проверка статуса службы..."
            )

            state = ((check_result or {}).get('stdout') or '').strip()
            if not check_result or check_result['returncode'] != 0 or state != 'active':
                print(f"Служба не в состоянии active: {state!r}")
                get_error_logger().error(
                    "Служба zapret не в состоянии active после запуска: %r", state
                )
                return False

            return True

        except Exception as e:
            print(f"Ошибка при включении службы: {e}")
            return False

    def lock_steamos_system(self, password):
        """Блокирует файловую систему SteamOS (только для SteamOS)"""
        if not self.is_steamos:
            print("Пропускаем блокировку: система не SteamOS")
            return True

        print("Блокировка файловой системы SteamOS...")
        result = self.run_with_sudo(
            ['steamos-readonly', 'enable'],
            password,
            "Включение защиты SteamOS..."
        )

        if not result or result['returncode'] != 0:
            print(f"Предупреждение: Не удалось заблокировать систему: {result['stderr'] if result else 'No result'}")
            return True  # Возвращаем True, чтобы не прерывать процесс

        print("Система успешно заблокирована")
        return True

    def install_zapret_from_local_bundle(self, bundle_root, password, progress_callback=None):
        """
        Устанавливает службу из bundle_root/zapret/ (system/, bins/).
        Вызывать после stop_and_remove_zapret и наката файлов менеджера.
        """
        try:
            zapret_sub = os.path.join(bundle_root, "zapret")
            if not os.path.isdir(os.path.join(zapret_sub, "system")):
                print("В пакете нет каталога zapret/system")
                return False
            if not self.copy_zapret_files(zapret_sub, password, progress_callback):
                return False
            self.update_manager_config(bundle_root)
            if not self.create_service_file(password, progress_callback):
                return False
            if not self.enable_service(password, progress_callback):
                return False
            return True
        finally:
            # На SteamOS защита файловой системы возвращается в любом случае.
            self.lock_steamos_system(password)

    def install_zapret_service_from_bundle_root(
        self, bundle_root: str, password: str, progress_callback=None
    ) -> bool:
        """
        Только служба Zapret из распакованного полного пакета (каталог с main.py и zapret/).
        Без обновления файлов менеджера в домашней папке.
        """
        if not is_valid_bundle_root(bundle_root):
            print("Неверный корень полного пакета (нужны main.py и zapret/system)")
            return False
        if not self.stop_and_remove_zapret(password, progress_callback):
            return False
        zapret_sub = os.path.join(bundle_root, "zapret")
        if not os.path.isdir(os.path.join(zapret_sub, "system")):
            print("В пакете нет каталога zapret/system")
            try:
                self.lock_steamos_system(password)
            except Exception:
                pass
            return False
        if not self.copy_zapret_files(zapret_sub, password, progress_callback):
            try:
                self.lock_steamos_system(password)
            except Exception:
                pass
            return False
        if not self.create_service_file(password, progress_callback):
            try:
                self.lock_steamos_system(password)
            except Exception:
                pass
            return False
        if not self.enable_service(password, progress_callback):
            try:
                self.lock_steamos_system(password)
            except Exception:
                pass
            return False
        self.lock_steamos_system(password)
        return True


# --- Полный пакет (менеджер + служба, один архив) ---

BUNDLE_DIR_NAME = "zapret_updater"


def is_valid_bundle_root(path: str) -> bool:
    """В корне пакета: main.py и zapret/system/ (bins — в zapret/bins/)."""
    return bool(
        path
        and os.path.isfile(os.path.join(path, "main.py"))
        and os.path.isdir(os.path.join(path, "zapret", "system"))
    )


def find_bundle_root(extract_dir: str) -> str | None:
    """
    Корень пакета после распаковки:

    1) Сам ``extract_dir``, если архив собран изнутри папки пакета (``tar ... .``) —
       в корне сразу ``main.py`` и ``zapret/system/``.
    2) Иначе подкаталог ``zapret_updater/`` (без учёта регистра), если архив
       собран как ``tar ... zapret_updater``.
    """
    if is_valid_bundle_root(extract_dir):
        return extract_dir

    candidates: list[str] = []
    direct = os.path.join(extract_dir, BUNDLE_DIR_NAME)
    if os.path.isdir(direct):
        candidates.append(direct)
    try:
        for name in os.listdir(extract_dir):
            if name.lower() == BUNDLE_DIR_NAME.lower():
                p = os.path.join(extract_dir, name)
                if os.path.isdir(p) and p not in candidates:
                    candidates.append(p)
    except OSError:
        pass

    for cand in candidates:
        if is_valid_bundle_root(cand):
            return cand
    return None


def download_http_to_file(
    url: str,
    dest_path: str,
    *,
    timeout: float = 120.0,
    reporthook=None,
) -> None:
    """
    Скачивает URL в файл с таймаутом (в отличие от urlretrieve).

    reporthook(blocknum, block_size, total_size) — как у urlretrieve;
    total_size = -1, если размер неизвестен (нет Content-Length).
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Zapret-DPI-Manager/2"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        cl = resp.headers.get("Content-Length")
        try:
            total_size = int(cl) if cl else -1
        except (TypeError, ValueError):
            total_size = -1
        block_size = 64 * 1024
        blocknum = 0
        with open(dest_path, "wb") as out:
            while True:
                chunk = resp.read(block_size)
                if not chunk:
                    break
                out.write(chunk)
                blocknum += 1
                if reporthook:
                    reporthook(blocknum, block_size, total_size)


def validate_bundle_structure(bundle_root: str) -> bool:
    if is_valid_bundle_root(bundle_root):
        return True
    mp = os.path.join(bundle_root, "main.py")
    if not os.path.isfile(mp):
        print(f"В пакете нет main.py в корне: {bundle_root!r}")
    else:
        print(f"Нет каталога zapret/system в: {bundle_root!r}")
    return False


def read_bundle_manifest(
    version_url: str | None = None, timeout: float = 10
) -> tuple[str | None, str | None]:
    """Первая и вторая строки version.txt: версия и URL архива полного пакета."""
    url = version_url or VERSION_CONFIG["version_url"]
    try:
        content = fetch_bytes(url, timeout=timeout).decode("utf-8").strip()
        lines = content.split("\n")
        if len(lines) >= 2:
            ver = lines[0].strip()
            download = lines[1].strip()
            if download:
                return ver, download
    except Exception as e:
        print(f"Манифест полного пакета недоступен ({url}): {e}")
        get_error_logger().error(
            "Манифест полного пакета недоступен (%s): %s",
            url,
            e,
            exc_info=True,
        )
    return None, None


def get_bundle_download_url(
    version_url: str | None = None, timeout: float = 10
) -> str | None:
    _, dl = read_bundle_manifest(version_url=version_url, timeout=timeout)
    return dl


class ZapretBundleUpdater(BaseUpdater):
    def __init__(self):
        super().__init__(
            version_url=VERSION_CONFIG["version_url"],
            current_version=VERSION_CONFIG["current_version"],
            name="Zapret DPI Manager",
        )

    def update_bundle(self, download_url, parent_window, progress_callback=None, cancel_check=None):
        """Скачивает один архив, обновляет менеджер (без zapret/) и службу из zapret/.

        cancel_check — callable без аргументов; отмена учитывается только ДО
        изменения установки (после начала удаления прерывать нельзя).
        """
        print("=== ПОЛНОЕ ОБНОВЛЕНИЕ (МЕНЕДЖЕР + СЛУЖБА) ===")

        def _cancelled():
            return bool(cancel_check and cancel_check())

        zapret_u = ZapretUpdater()
        password = zapret_u.get_sudo_password(parent_window)
        if not password:
            if progress_callback:
                progress_callback("Пароль не введён, отмена обновления", None)
            return False

        temp_dir = tempfile.mkdtemp(prefix="zapret_bundle_")
        try:
            if _cancelled():
                if progress_callback:
                    progress_callback("Обновление отменено пользователем", None)
                return False

            archive_path = os.path.join(temp_dir, "bundle.tar.gz")
            extract_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)

            if progress_callback:
                progress_callback("Скачивание полного пакета...", 15)

            def download_progress(count, block_size, total_size):
                if progress_callback and total_size > 0:
                    pct = min(int(count * block_size * 100 / total_size), 100)
                    progress_callback(f"Скачивание... {pct}%", 15 + int(25 * pct / 100))

            try:
                download_http_to_file(
                    download_url,
                    archive_path,
                    timeout=120.0,
                    reporthook=download_progress,
                )
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    msg = (
                        "Релиз с архивом обновления ещё не опубликован (HTTP 404). "
                        "Обновление отменено, текущая установка не тронута."
                    )
                else:
                    msg = (
                        f"Сервер вернул ошибку HTTP {e.code}. "
                        "Обновление отменено, текущая установка не тронута."
                    )
                print(msg)
                get_error_logger().error("Скачивание bundle (%s): %s", download_url, msg)
                if progress_callback:
                    progress_callback(msg, None)
                return False
            except urllib.error.URLError as e:
                reason = e.reason
                detail = str(reason) if reason is not None else str(e)
                msg = (
                    "Не удалось скачать архив обновления. Проверьте интернет и DNS. "
                    f"({detail})"
                )
                print(msg)
                get_error_logger().error(
                    "Скачивание bundle (%s): %s",
                    download_url,
                    msg,
                    exc_info=True,
                )
                if progress_callback:
                    progress_callback(msg, None)
                return False

            if not os.path.exists(archive_path) or os.path.getsize(archive_path) == 0:
                print("Архив пустой или не скачан")
                get_error_logger().error(
                    "Архив обновления пустой или отсутствует после скачивания: %s",
                    download_url,
                )
                return False

            if progress_callback:
                progress_callback("Распаковка архива...", 45)
            with tarfile.open(archive_path, "r:gz") as tar:
                try:
                    tar.extractall(path=extract_dir, filter="data")
                except TypeError:  # Python < 3.12
                    tar.extractall(path=extract_dir)

            bundle_root = find_bundle_root(extract_dir)
            if not bundle_root or not validate_bundle_structure(bundle_root):
                print(
                    "Неверная структура архива обновления: нужны main.py и zapret/system/ "
                    "(и zapret/bins/), либо то же внутри папки zapret_updater/."
                )
                get_error_logger().error(
                    "Неверная структура архива обновления (ожидались main.py и zapret/system/)"
                )
                return False

            # Архив скачан и проверен — только теперь трогаем установку.
            if _cancelled():
                msg = "Обновление отменено до изменения установки — служба не тронута"
                print(msg)
                if progress_callback:
                    progress_callback(msg, None)
                return False

            if progress_callback:
                progress_callback("Остановка службы и подготовка...", 55)

            if not zapret_u.backup_opt_zapret(password, temp_dir):
                msg = (
                    "Не удалось создать резервную копию установки. "
                    "Обновление отменено, служба не тронута."
                )
                print(msg)
                get_error_logger().error(msg)
                if progress_callback:
                    progress_callback(msg, None)
                return False

            if not zapret_u.stop_and_remove_zapret(password, progress_callback):
                msg = (
                    "Не удалось полностью удалить прежнюю установку. "
                    "Обновление прервано до установки новой версии."
                )
                print(msg)
                get_error_logger().error(msg)
                if progress_callback:
                    progress_callback(msg, None)
                return False

            manager_u = ManagerUpdater()
            if not manager_u.apply_from_directory(
                bundle_root,
                progress_callback,
                extra_exclude_prefixes=["zapret"],
            ):
                zapret_u.restore_opt_zapret(password, temp_dir)
                return False

            if not zapret_u.install_zapret_from_local_bundle(
                bundle_root, password, progress_callback
            ):
                zapret_u.restore_opt_zapret(password, temp_dir)
                return False

            if progress_callback:
                progress_callback("Полное обновление завершено", 100)
            return True
        except Exception as e:
            print(f"Ошибка полного обновления: {e}")
            get_error_logger().exception("Ошибка полного обновления (bundle)")
            try:
                if password:
                    zapret_u.lock_steamos_system(password)
            except Exception:
                pass
            return False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
