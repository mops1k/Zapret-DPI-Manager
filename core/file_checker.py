#!/usr/bin/env python3
import subprocess
import os
import tempfile
import tarfile
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from core.app_logging import get_error_logger
from core.zapret_updater import find_bundle_root, get_bundle_download_url
from core.dialog_port import dialog_port

# Файлы, которые могут содержать пользовательские данные: при восстановлении
# существующие версии не перезаписываются.
PROTECTED_FILE_NAMES = {
    "config.txt",
    "list-general.txt",
    "ipset-all.txt",
    "list-general_user.txt",
    "list-exclude_user.txt",
    "ipset-all_user.txt",
    "ipset-exclude_user.txt",
}


class ZapretFileChecker:
    def __init__(
        self,
        root_window=None,
        *,
        show_info_fn: Callable[[str, str], None] | None = None,
    ):
        self.root = root_window
        self._show_info_fn = show_info_fn
        self.progress_window = None
        self.progress_handle = None
        self.current_task = ""
        self.sudo_password = None
        self.debug_log = []  # Лог для дебага

        # URL единого архива (zapret_updater.tar.gz) подтягивается по требованию
        # из version.txt — отдельных fallback-каналов нет.
        self.zapret_archive_url: str | None = None

        # Базовые пути
        self.home_dir = Path.home()
        self.manager_dir = self.home_dir / "Zapret_DPI_Manager"

        # Список обязательных файлов и папок для проверки
        self.required_files = [
            # Основные исполняемые файлы
            self.manager_dir / "main.py",
            self.manager_dir / "core" / "manager_config.py",
            self.manager_dir / "core" / "service_manager.py",
            self.manager_dir / "core" / "zapret_checker.py",
            self.manager_dir / "core" / "dependency_checker.py",

            # Интерфейс Qt Quick и диалоговый порт
            self.manager_dir / "qmlui" / "app.py",
            self.manager_dir / "qmlui" / "backend.py",
            self.manager_dir / "qmlui" / "theme.py",
            self.manager_dir / "qml" / "main.qml",
            self.manager_dir / "core" / "askpass.py",
            self.manager_dir / "core" / "sudo_helper.py",
            self.manager_dir / "core" / "dialog_port.py",

            # Файлы конфигурации (name_strategy.txt создаётся в рантайме — не обязателен)
            self.manager_dir / "utils" / "chosen_strategies.txt",

            # Иконка
            self.manager_dir / "ico" / "zapret.png",
        ]

        # Проверяемые папки
        self.required_dirs = [
            self.manager_dir / "files" / "bin",
            self.manager_dir / "files" / "lists",
            self.manager_dir / "files" / "strategy",
            self.manager_dir / "core",
            self.manager_dir / "ui" / "windows",
            self.manager_dir / "ui" / "components",
            self.manager_dir / "ico",
            self.manager_dir / "utils",
        ]

        # Минимальное количество файлов в папках (соответствует составу релиза;
        # завышенные пороги приводили к бесконечному «восстановлению»).
        self.min_files_in_dir = {
            self.manager_dir / "files" / "bin": 9,
            self.manager_dir / "files" / "lists": 15,
            self.manager_dir / "files" / "strategy": 22,
            self.manager_dir / "core": 20,
            self.manager_dir / "ico": 1,
            self.manager_dir / "utils": 3,
            self.manager_dir / "ui" / "windows": 14,
            self.manager_dir / "ui" / "components": 2,
        }

    def log_debug(self, message):
        """Добавляет сообщение в лог дебага"""
        timestamp = time.strftime("%H:%M:%S")
        log_msg = f"[{timestamp}] {message}"
        self.debug_log.append(log_msg)
        print(log_msg)

    def show_info(self, title, message):
        """Показывает информационное сообщение"""
        if self._show_info_fn is not None:
            self._show_info_fn(title, message)
            return
        dialog_port().show_info(str(title), str(message))

    def create_progress_window(self, title="Прогресс"):
        """Окно прогресса создаёт порт диалогов (Tk — в ui, Qt — в qmlui)."""
        self.progress_handle = dialog_port().open_progress(title)
        return self.progress_handle

    def update_progress(self, task, progress_percent):
        """Обновляет прогресс через порт диалогов."""
        handle = getattr(self, "progress_handle", None)
        if handle is not None:
            handle.update(str(task), progress_percent)

    def close_progress_window(self):
        """Закрывает прогресс, если он открыт."""
        handle = getattr(self, "progress_handle", None)
        if handle is not None:
            handle.close()
            self.progress_handle = None
    def get_current_progress(self):
        """Рассчитывает текущий прогресс на основе этапа"""
        if self.current_task == "check":
            return 20
        elif self.current_task == "download":
            return 40
        elif self.current_task == "extract":
            return 60
        elif self.current_task == "restore":
            return 80
        elif self.current_task == "cleanup":
            return 90
        else:
            return 0

    def check_files(self):
        """Проверяет наличие всех необходимых файлов и папок"""
        self.log_debug("=== ПРОВЕРКА ФАЙЛОВ ZAPRET DPI MANAGER ===")

        missing_files = []
        missing_dirs = []

        # Проверяем основные файлы
        self.update_progress("Проверка основных файлов...", 10)
        for file_path in self.required_files:
            if not file_path.exists():
                missing_files.append(str(file_path))
                self.log_debug(f"✗ Отсутствует файл: {file_path}")
            else:
                self.log_debug(f"✓ Файл присутствует: {file_path}")

        # Проверяем папки и минимальное количество файлов в них
        self.update_progress("Проверка папок...", 30)
        for dir_path in self.required_dirs:
            if not dir_path.exists():
                missing_dirs.append(str(dir_path))
                self.log_debug(f"✗ Отсутствует папка: {dir_path}")
            else:
                # Проверяем минимальное количество файлов если задано
                if dir_path in self.min_files_in_dir:
                    file_count = sum(1 for _ in dir_path.iterdir() if _.is_file())
                    min_required = self.min_files_in_dir[dir_path]
                    if file_count < min_required:
                        missing_dirs.append(str(dir_path))
                        self.log_debug(f"✗ В папке недостаточно файлов: {dir_path} ({file_count} из {min_required})")
                    else:
                        self.log_debug(f"✓ Папка в порядке: {dir_path} ({file_count} файлов)")
                else:
                    self.log_debug(f"✓ Папка присутствует: {dir_path}")

        # Проверяем главный скрипт
        main_script = self.manager_dir / "main.py"
        if not main_script.exists():
            missing_files.append(str(main_script))
            self.log_debug("✗ Главный скрипт main.py отсутствует")
        else:
            self.log_debug("✓ Главный скрипт main.py присутствует")

        return missing_files, missing_dirs
    def download_archive(self, temp_dir):
        """Скачивает единый архив zapret_updater.tar.gz по ссылке из version.txt"""
        self.current_task = "download"

        try:
            if not self.zapret_archive_url:
                self.zapret_archive_url = get_bundle_download_url()

            if not self.zapret_archive_url:
                self.log_debug("Не удалось получить URL архива из version.txt")
                get_error_logger().error(
                    "Восстановление файлов менеджера: не удалось получить URL из version.txt"
                )
                return None

            archive_path = os.path.join(temp_dir, "zapret_updater.tar.gz")

            self.update_progress("Скачивание архива...", 40)

            # -f: считать HTTP-ошибки (404 и т.п.) ошибкой; таймауты обязательны.
            curl_cmd = [
                'curl', '-f', '-L', '--connect-timeout', '10', '--max-time', '120',
                '-o', archive_path, self.zapret_archive_url,
            ]

            result = subprocess.run(
                curl_cmd,
                capture_output=True,
                text=True,
                timeout=150,
            )
            result = {
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr
            }

            if not result or result['returncode'] != 0:
                err = result['stderr'] if result else 'No result'
                self.log_debug(f"Ошибка скачивания: {err}")
                get_error_logger().error(
                    "Восстановление файлов менеджера: ошибка curl (%s): %s",
                    self.zapret_archive_url,
                    err,
                )
                return None

            # Проверяем, что архив существует и не пустой
            if not os.path.exists(archive_path) or os.path.getsize(archive_path) == 0:
                self.log_debug("Скачанный архив пустой или не существует")
                get_error_logger().error(
                    "Восстановление файлов менеджера: архив пуст или отсутствует после curl"
                )
                return None

            self.update_progress("Архив успешно скачан", 50)
            return archive_path

        except Exception as e:
            self.log_debug(f"Ошибка при скачивании архива: {e}")
            get_error_logger().exception("Восстановление файлов менеджера: скачивание архива")
            return None

    def extract_archive(self, archive_path, temp_dir):
        """Извлекает архив"""
        self.current_task = "extract"

        try:
            self.update_progress("Извлечение архива...", 55)

            extract_dir = os.path.join(temp_dir, "zapret_extracted")
            os.makedirs(extract_dir, exist_ok=True)

            # Извлекаем архив
            with tarfile.open(archive_path, 'r:gz') as tar:
                try:
                    tar.extractall(path=extract_dir, filter="data")
                except TypeError:  # Python < 3.12
                    tar.extractall(path=extract_dir)

            self.update_progress("Архив успешно извлечен", 60)
            return extract_dir

        except Exception as e:
            self.log_debug(f"Ошибка при извлечении архива: {e}")
            return None

    def restore_missing_files(self, extract_dir, missing_files, missing_dirs):
        """Восстанавливает недостающие файлы и папки"""
        self.current_task = "restore"

        restored_count = 0

        # Сначала создаем недостающие папки
        self.update_progress("Создание недостающих папок...", 65)
        for dir_path_str in missing_dirs:
            dir_path = Path(dir_path_str)
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                self.log_debug(f"✓ Создана папка: {dir_path}")
                restored_count += 1
            except Exception as e:
                self.log_debug(f"✗ Ошибка создания папки {dir_path}: {e}")

        # Восстанавливаем недостающие файлы
        self.update_progress("Восстановление файлов...", 70)

        bundle_root = find_bundle_root(str(extract_dir))
        if bundle_root:
            source_dir = Path(bundle_root)
        else:
            source_dir = Path(extract_dir)

        self.log_debug(f"Источник для восстановления: {source_dir}")

        # Копируем недостающие файлы
        for file_path_str in missing_files:
            file_path = Path(file_path_str)

            # Определяем относительный путь для поиска в архиве
            rel_path = None
            for base_path in [self.manager_dir, self.home_dir]:
                try:
                    rel_path = file_path.relative_to(base_path)
                    break
                except ValueError:
                    continue

            if not rel_path:
                self.log_debug(f"✗ Не могу определить относительный путь для: {file_path}")
                continue

            # Ищем файл в архиве
            source_file = source_dir / rel_path

            if source_file.exists():
                # Создаем целевую папку если её нет
                file_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    # Копируем файл
                    shutil.copy2(source_file, file_path)
                    self.log_debug(f"✓ Восстановлен файл: {file_path}")
                    restored_count += 1
                except Exception as e:
                    self.log_debug(f"✗ Ошибка копирования {file_path}: {e}")
            else:
                self.log_debug(f"✗ Файл не найден в архиве: {rel_path}")

        # Восстанавливаем содержимое папок с недостаточным количеством файлов
        self.update_progress("Восстановление содержимого папок...", 80)
        for dir_path_str in missing_dirs:
            dir_path = Path(dir_path_str)

            # Определяем относительный путь папки
            rel_dir_path = None
            for base_path in [self.manager_dir, self.home_dir]:
                try:
                    rel_dir_path = dir_path.relative_to(base_path)
                    break
                except ValueError:
                    continue

            if not rel_dir_path:
                continue

            # Ищем папку в архиве
            source_dir_path = source_dir / rel_dir_path

            if source_dir_path.exists() and source_dir_path.is_dir():
                # Копируем все файлы из папки
                copied_files = 0
                for source_file in source_dir_path.iterdir():
                    if source_file.is_file():
                        target_file = dir_path / source_file.name
                        # Файлы с пользовательскими данными не затираем.
                        if source_file.name in PROTECTED_FILE_NAMES and target_file.exists():
                            self.log_debug(f"↷ Пропуск {source_file.name} (пользовательские данные)")
                            continue
                        try:
                            shutil.copy2(source_file, target_file)
                            copied_files += 1
                        except Exception as e:
                            self.log_debug(f"✗ Ошибка копирования {source_file.name}: {e}")

                if copied_files > 0:
                    self.log_debug(f"✓ Восстановлено {copied_files} файлов в папке: {dir_path}")
                    restored_count += copied_files

        return restored_count

    def fix_zapret_files(self):
        """Основная функция проверки и восстановления файлов"""
        self.log_debug("=== НАЧАЛО ПРОВЕРКИ И ВОССТАНОВЛЕНИЯ ФАЙЛОВ ===")

        try:
            # 1. Проверяем файлы (без окна прогресса)
            self.current_task = "check"
            missing_files, missing_dirs = self.check_files()

            if not missing_files and not missing_dirs:
                self.log_debug("Все файлы присутствуют, восстановление не требуется")
                return True  # Просто возвращаем True, без окон

            # 2. Показываем окно уведомления о недостающих файлах
            missing_count = len(missing_files) + len(missing_dirs)
            self.log_debug(f"Обнаружено {missing_count} недостающих элементов")

            # Показываем уведомление пользователю
            self.show_info("Проверка файлов Zapret DPI Manager",
                         f"Обнаружены отсутствующие или поврежденные файлы.\n\n"
                         f"Найдено недостающих элементов: {missing_count}\n"
                         f"• Файлы: {len(missing_files)}\n"
                         f"• Папки: {len(missing_dirs)}\n\n"
                         f"Будет выполнено автоматическое скачивание и восстановление файлов.")

            # 4. Создаем окно прогресса ТОЛЬКО при восстановлении
            if self.root:
                self.progress_window = self.create_progress_window("Восстановление файлов Zapret")
                if self.progress_window:
                    self.update_progress("Подготовка к восстановлению...", 0)

            # 5. Создаем временную директорию
            temp_dir = tempfile.mkdtemp(prefix="zapret_fix_")
            self.log_debug(f"Временная директория: {temp_dir}")

            try:
                # 6. Скачиваем архив
                self.update_progress("Скачивание архива с файлами...", 30)
                archive_path = self.download_archive(temp_dir)

                if not archive_path:
                    self.log_debug("Не удалось скачать архив")
                    self.close_progress_window()
                    self.show_info("Ошибка скачивания",
                                 "Не удалось скачать архив с файлами.\n"
                                 "Проверьте подключение к интернету; если релиз ещё не "
                                 "опубликован, восстановление недоступно.")
                    return False

                # 7. Извлекаем архив
                self.update_progress("Распаковка архива...", 50)
                extract_dir = self.extract_archive(archive_path, temp_dir)

                if not extract_dir:
                    self.log_debug("Не удалось извлечь архив")
                    self.close_progress_window()
                    self.show_info("Ошибка извлечения",
                                 "Не удалось извлечь архив.\n"
                                 "Архив может быть поврежден.")
                    return False

                # 8. Восстанавливаем файлы
                self.update_progress("Восстановление недостающих файлов...", 60)
                restored_count = self.restore_missing_files(extract_dir, missing_files, missing_dirs)

                # 9. Проверяем результат
                self.update_progress("Проверка результата...", 90)
                missing_files_after, missing_dirs_after = self.check_files()

                still_missing = len(missing_files_after) + len(missing_dirs_after)

                # 10. Очищаем временную папку
                self.current_task = "cleanup"
                self.update_progress("Очистка временных файлов...", 95)
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    self.log_debug(f"Очищена временная директория: {temp_dir}")
                except Exception as e:
                    self.log_debug(f"Ошибка очистки временной директории: {e}")

                # 11. Завершаем
                self.update_progress("Завершение...", 100)
                self.close_progress_window()

                if still_missing == 0:
                    self.show_info("Восстановление завершено",
                                 f"Все файлы успешно восстановлены!\n"
                                 f"Восстановлено элементов: {restored_count}")
                    return True
                else:
                    self.show_info("Восстановление частично завершено",
                                 f"Восстановлено {restored_count} элементов, но {still_missing} все еще отсутствуют.\n\n"
                                 f"Отсутствуют:\n"
                                 f"Файлы: {len(missing_files_after)}\n"
                                 f"Папки: {len(missing_dirs_after)}")
                    return False

            except Exception as e:
                self.log_debug(f"Ошибка в процессе восстановления: {e}")
                self.close_progress_window()
                self.show_info("Ошибка восстановления",
                             f"Произошла ошибка при восстановлении файлов:\n\n{str(e)}")
                return False

        except Exception as e:
            self.log_debug(f"Критическая ошибка: {e}")
            import traceback
            self.log_debug(f"Трейс ошибки: {traceback.format_exc()}")
            self.close_progress_window()
            self.show_info("Ошибка",
                         f"Произошла критическая ошибка:\n\n{str(e)}")
            return False

    def run_check(self):
        """Запускает проверку и восстановление файлов"""
        # Сначала быстрая проверка без окон
        missing_files, missing_dirs = self.check_files()

        # Если все файлы на месте - возвращаем True без окон
        if not missing_files and not missing_dirs:
            self.log_debug("Все файлы присутствуют")
            return True

        # Если файлы отсутствуют - запускаем полный процесс с окнами
        return self.fix_zapret_files()


def run_file_check(root_window=None, *, show_info_fn: Callable[[str, str], None] | None = None):
    """
    Запускает проверку и восстановление файлов Zapret DPI Manager.

    Диалоги и прогресс идут через core.dialog_port.
    """
    checker = ZapretFileChecker(root_window, show_info_fn=show_info_fn)
    return checker.run_check()


if __name__ == "__main__":
    # Тестирование
    checker = ZapretFileChecker()
    result = checker.run_check()

    if result:
        print("✓ Проверка и восстановление файлов завершены успешно")
    else:
        print("✗ Проверка и восстановление файлов завершены с ошибками")
