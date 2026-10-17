import tkinter as tk
import threading

from core.manager_config import VERSION_CONFIG
from core.zapret_updater import ZapretBundleUpdater
from ui.components.material import (
    LinearProgress,
    MaterialCard,
    TopAppBar,
    filled_button,
    outlined_button,
    text_button,
    tonal_button,
)
from ui.theme import theme
from core.dpi_utils import (
    center_toplevel_on_parent,
    place_toplevel_centered_on_parent,
    safe_grab_set,
    set_window_size_to_fit_content,
    wait_window_safely,
)


def append_release_notes_to_log(log_fn, version: str | None, notes: str | None) -> None:
    """Пишет описание релиза в лог окна обновления."""
    text = (notes or "").strip()
    if not text:
        return
    ver = (version or "").strip()
    header = f"📋 Описание релиза v{ver}:" if ver else "📋 Описание релиза:"
    log_fn(header)
    for line in text.splitlines():
        log_fn(line)
    log_fn("")


class UpdateWindow:
    def __init__(self, parent, *, pending_update=None):
        self.parent = parent
        self.pending_update = pending_update
        self.root = tk.Toplevel(parent)
        self.setup_window()

        self.bundle_updater = ZapretBundleUpdater()

        self.bundle_update_available = False
        self.bundle_update_data = None
        self.bundle_version = None

        self.setup_ui()
        self.root.update_idletasks()
        try:
            self.root.minsize(1, 1)
        except tk.TclError:
            pass
        set_window_size_to_fit_content(
            self.root,
            min_width=360,
            min_height=320,
            margin_width=8,
            margin_height=12,
        )
        center_toplevel_on_parent(self.root, self.parent)

        self.root.after_idle(self._refresh_source_versions)

        if self.pending_update:
            self.root.after_idle(self._apply_pending_update)

    def _apply_pending_update(self):
        """Подставляет данные из уведомления о старте и показывает описание в логе."""
        pu = self.pending_update
        if not pu:
            return
        available = pu.get("available")
        if not available:
            return

        self.clear_log()
        notes = (pu.get("release_notes") or "").strip()
        if notes:
            append_release_notes_to_log(self.log_message, available, notes)
        else:
            thread = threading.Thread(
                target=self._fetch_and_log_notes_thread,
                args=(available,),
                daemon=True,
            )
            thread.start()

        self.update_application(
            preloaded_version=available, preloaded_url=pu.get("download_url")
        )

    def _fetch_and_log_notes_thread(self, version: str) -> None:
        try:
            from core.github_release import fetch_release_notes_for_version

            notes = fetch_release_notes_for_version(version)

            def _append():
                append_release_notes_to_log(self.log_message, version, notes)

            self._safe_after(_append)
        except Exception as e:
            self._safe_after(
                lambda err=e: self.log_message(f"⚠️ Не удалось загрузить описание релиза: {err}"),
            )

    def setup_window(self):
        self.root.title("Обновление")
        self.root.configure(bg=theme.color('surface'))
        self.root.transient(self.parent)
        safe_grab_set(self.root)

    def setup_ui(self):
        """Интерфейс окна обновления в стиле Material 3."""
        main_frame = tk.Frame(self.root, bg=theme.color("surface"))
        main_frame.pack(fill=tk.X, padx=theme.space("lg"), pady=theme.space("lg"))

        self.app_bar = TopAppBar(
            main_frame,
            title="Обновление",
            subtitle="Приложение, стратегии и движок — по отдельности",
            bg_role="surface",
        )
        self.app_bar.pack(fill=tk.X, pady=(0, theme.space("md")))

        versions_text = f"Версия программы: {VERSION_CONFIG['current_version']}"
        self.version_label = tk.Label(
            main_frame,
            text=versions_text,
            anchor="w",
            **theme.text("body_medium", fg_role="success"),
        )
        self.version_label.pack(fill=tk.X, pady=(0, theme.space("sm")))

        self.source_version_label = tk.Label(
            main_frame,
            text="",
            anchor="w",
            **theme.text("body_small", fg_role="on_surface_variant"),
        )
        self.source_version_label.pack(fill=tk.X, pady=(0, theme.space("md")))

        self.app_btn = filled_button(main_frame, "Обновить приложение", self.update_application)
        self.app_btn.pack(anchor=tk.CENTER, pady=(0, theme.space("xs")))

        self.strategies_btn = tonal_button(
            main_frame, "Обновить стратегии", self.update_strategies
        )
        self.strategies_btn.pack(anchor=tk.CENTER, pady=(0, theme.space("xs")))

        self.engine_btn = outlined_button(main_frame, "Обновить движок", self.update_engine)
        self.engine_btn.pack(anchor=tk.CENTER, pady=(0, theme.space("xs")))

        # WARP (usque) обновляется отдельно: движок скачивается из релизов usque.
        self.usque_btn = outlined_button(
            main_frame, "Обновить движок WARP", self.update_usque
        )
        self.usque_btn.pack(anchor=tk.CENTER, pady=(0, theme.space("md")))

        log_card = MaterialCard(main_frame, variant="elevated", title="Лог обновлений")
        log_card.pack(fill=tk.X)

        self.log_text = tk.Text(
            log_card.content,
            height=6,
            width=48,
            bg=theme.color("surface_container_lowest"),
            fg=theme.color("on_surface"),
            wrap=tk.WORD,
            font=theme.font("body_small", mono=True),
            highlightthickness=0,
            borderwidth=0,
            relief=tk.FLAT,
            padx=theme.px(8),
            pady=theme.px(6),
        )
        self.log_text.pack(fill=tk.X)

        close_frame = tk.Frame(main_frame, bg=theme.color("surface"))
        close_frame.pack(fill=tk.X, pady=(theme.space("sm"), 0))
        self.close_btn = text_button(close_frame, "Назад", self.close_window)
        self.close_btn.pack(anchor=tk.CENTER)

    def log_message(self, message):
        """Добавляет сообщение в лог (безопасно из любого потока)."""
        def _append():
            try:
                if not self.root.winfo_exists():
                    return
                self.log_text.insert(tk.END, f"{message}\n")
                self.log_text.see(tk.END)
            except tk.TclError:
                pass

        if threading.current_thread() is threading.main_thread():
            _append()
        else:
            self._safe_after(_append)

    def _safe_after(self, func, delay=0):
        """Планирует вызов в главном потоке, не падая на уничтоженном окне."""
        try:
            if self.root.winfo_exists():
                self.root.after(delay, func)
        except (tk.TclError, RuntimeError):
            pass

    # --- три отдельные операции обновления -----------------------------------

    def _set_buttons_state(self, enabled: bool):
        """Блокирует или разблокирует все кнопки окна."""
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in (self.app_btn, self.strategies_btn, self.engine_btn, self.usque_btn):
            try:
                button.config(state=state)
            except tk.TclError:
                pass

    def _refresh_source_versions(self):
        """Показывает локальные версии стратегий и движка nfqws."""
        try:
            from core.engine_updater import NfqwsUpdater, read_version
            from core.strategies_updater import FlowsealStrategiesUpdater

            sha = FlowsealStrategiesUpdater().local_version()
            strategies = f"стратегии: {sha[:8]}" if sha else "стратегии: версия неизвестна"

            updater = NfqwsUpdater()
            binary = updater.installed_binary()
            if not binary.is_file():
                binary = updater.local_binary()
            version = read_version(binary) if binary.is_file() else None
            engine = f"движок nfqws: {version}" if version else "движок nfqws: не установлен"

            text = f"{strategies} · {engine}"
        except Exception as e:
            text = f"версии недоступны: {e}"

        def _apply():
            try:
                if self.root.winfo_exists():
                    self.source_version_label.config(text=text)
            except tk.TclError:
                pass

        self._safe_after(_apply)

    def _source_progress(self, message, percent=None):
        """Прогресс операции в общий лог."""
        if percent is not None:
            self.log_message(f"   [{percent}%] {message}")
        else:
            self.log_message(f"   {message}")

    def update_application(self, preloaded_version=None, preloaded_url=None):
        """Проверяет обновление приложения и, если оно есть, обновляет полный пакет."""
        self._set_buttons_state(False)
        if preloaded_url is None:
            self.clear_log()

        thread = threading.Thread(
            target=self._update_application_thread,
            args=(preloaded_version, preloaded_url),
            daemon=True,
        )
        thread.start()

    def _update_application_thread(self, preloaded_version=None, preloaded_url=None):
        try:
            if preloaded_url:
                self.bundle_version = preloaded_version
                self.bundle_update_available = True
                self.bundle_update_data = {"download_url": preloaded_url}
                self.log_message(f"📦 Обновление приложения до v{preloaded_version}...")
            else:
                self.log_message("🔍 Проверка обновлений приложения...")
                version, info, error = self.bundle_updater.check_for_updates_detailed()
                if error:
                    self.log_message(f"❌ Не удалось проверить обновления: {error}")
                    return
                if not version:
                    self.log_message("🎉 Установлена последняя версия")
                    return
                self.bundle_version = version
                self.bundle_update_available = True
                self.bundle_update_data = info
                self.log_message(f"📢 Доступно обновление: v{version}")
                self._log_release_notes_from_api(version)

            self._update_all_thread()
        except Exception as e:
            self.log_message(f"❌ Ошибка обновления приложения: {e}")
        finally:
            self._safe_after(lambda: self._set_buttons_state(True))

    def update_strategies(self):
        """Обновляет только стратегии и payload'ы из Flowseal."""
        self._set_buttons_state(False)
        self.clear_log()
        thread = threading.Thread(target=self._update_strategies_thread, daemon=True)
        thread.start()

    def _update_strategies_thread(self):
        try:
            from core.strategies_updater import FlowsealStrategiesUpdater

            self.log_message("🔄 Обновление стратегий из Flowseal...")
            report = FlowsealStrategiesUpdater(progress_callback=self._source_progress).update(
                apply=True
            )
            for line in FlowsealStrategiesUpdater.format_report(report):
                self.log_message(line)
            self._safe_after(self._refresh_source_versions)
        except Exception as e:
            self.log_message(f"❌ Ошибка обновления стратегий: {e}")
        finally:
            self._safe_after(lambda: self._set_buttons_state(True))

    def update_engine(self):
        """Обновляет только движок nfqws."""
        self._set_buttons_state(False)
        self.clear_log()
        thread = threading.Thread(target=self._update_engine_thread, daemon=True)
        thread.start()

    def _update_engine_thread(self):
        try:
            from core.engine_updater import NfqwsUpdater

            self.log_message("🔄 Проверка движка nfqws...")
            report = NfqwsUpdater(progress_callback=self._source_progress).update(apply=True)
            for line in NfqwsUpdater.format_report(report):
                self.log_message(line)
            self._safe_after(self._refresh_source_versions)
        except Exception as e:
            self.log_message(f"❌ Ошибка обновления движка: {e}")
        finally:
            self._safe_after(lambda: self._set_buttons_state(True))

    def update_usque(self):
        """Обновляет движок WARP (usque) из его GitHub-релизов."""
        self._set_buttons_state(False)
        self.clear_log()
        thread = threading.Thread(target=self._update_usque_thread, daemon=True)
        thread.start()

    def _update_usque_thread(self):
        try:
            from core.warp_manager import WarpManager

            manager = WarpManager(progress=self._source_progress)
            self.log_message("🔄 Проверка движка WARP (usque)...")
            installed = manager.installed_version()
            upstream = manager.upstream_version()
            self.log_message(
                f"   установлен: {installed or 'нет'}, последний релиз: {upstream or 'неизвестен'}"
            )
            if not manager.is_installed() and not upstream:
                self.log_message(f"❌ Релиз usque недоступен: {manager.release_error or 'нет связи'}")
                return
            ok, message = manager.update_usque()
            self.log_message(("✅ " if ok else "❌ ") + message)
            if ok and manager.status() == "active":
                self.log_message("ℹ️ Служба WARP перезапущена с новым движком.")
            self._safe_after(self._refresh_source_versions)
        except Exception as e:
            self.log_message(f"❌ Ошибка обновления движка WARP: {e}")
        finally:
            self._safe_after(lambda: self._set_buttons_state(True))

    def clear_log(self):
        """Очищает лог"""
        self.log_text.delete(1.0, tk.END)

    def _log_release_notes_from_api(self, version: str) -> None:
        try:
            from core.github_release import fetch_release_notes_for_version

            notes = fetch_release_notes_for_version(version)
            append_release_notes_to_log(self.log_message, version, notes)
        except Exception as e:
            self.log_message(f"⚠️ Не удалось загрузить описание релиза: {e}")

    def _update_all_thread(self):
        """Поток для обновления полного пакета"""
        try:
            self.log_message("\n🔄 Начинаю обновление компонентов...")

            success_count = 0

            if self.bundle_update_available and self.bundle_update_data:
                self.log_message(f"\n📦 Обновление до v{self.bundle_version}...")
                download_url = self.bundle_update_data.get("download_url")
                if download_url:

                    def progress_callback(message, percent):
                        if percent is not None:
                            self.log_message(f"   [{percent}%] {message}")
                        else:
                            self.log_message(f"   {message}")

                    success = self.bundle_updater.update_bundle(
                        download_url, self.root, progress_callback
                    )
                    if success:
                        self.log_message(f"✅ Обновление до v{self.bundle_version} завершено!")
                        success_count += 1
                        self.bundle_update_available = False
                    else:
                        self.log_message("❌ Не удалось выполнить обновление")
                else:
                    self.log_message("❌ URL пакета не найден")

            self.log_message(f"\n📊 Обновление завершено. Успешных шагов: {success_count}")

            if success_count > 0:
                self._safe_after(self.restart_manager)

        except Exception as e:
            self.log_message(f"❌ Ошибка при обновлении: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            self._safe_after(lambda: self._set_buttons_state(True))

    def restart_manager(self):
        """Перезапускает менеджер и закрывает окна, если они ещё живы."""
        from core.manager_updater import ManagerUpdater
        ManagerUpdater().restart_manager()
        for widget in (self.root, self.parent):
            try:
                if widget is not None and widget.winfo_exists():
                    widget.destroy()
            except tk.TclError:
                pass

    def close_window(self):
        self.root.destroy()

    def run(self):
        wait_window_safely(self.root)


def show_update_window(parent, *, pending_update=None):
    window = UpdateWindow(parent, pending_update=pending_update)
    window.run()


class UpdateProgressWindow:
    def __init__(self, parent, update_tasks):
        """
        Окно прогресса обновления.

        Args:
            parent: родительское окно
            update_tasks: список задач обновления (используется только bundle):
                [{
                    'name': 'Zapret DPI Manager',
                    'updater_class': 'ZapretBundleUpdater',
                    'download_url': 'url'
                }, ...]
        """
        self.parent = parent
        self.update_tasks = update_tasks
        self.current_task_index = 0
        self.window = None
        self.is_updating = False
        self.manager_updated = False  # Флаг, что bundle применён (нужен перезапуск)
        self.bundle_updater = ZapretBundleUpdater()

    def run(self):
        """Запускает окно прогресса"""
        self.window = tk.Toplevel(self.parent)
        self.window.title("Обновление")
        self.window.configure(bg=theme.color('surface'))
        self.window.transient(self.parent)
        safe_grab_set(self.window)

        self.setup_ui()
        place_toplevel_centered_on_parent(
            self.window, self.parent, min_width=340, min_height=160, margin_width=8, margin_height=12
        )
        self.start_update_process()

        self.window.protocol("WM_DELETE_WINDOW", self.on_close)
        wait_window_safely(self.window)

    def setup_ui(self):
        """Настраивает UI окна прогресса в стиле Material 3."""
        main_frame = tk.Frame(self.window, bg=theme.color("surface"))
        main_frame.pack(fill=tk.BOTH, expand=True, padx=theme.space("lg"), pady=theme.space("lg"))

        self.app_bar = TopAppBar(
            main_frame,
            title="Обновление компонентов",
            subtitle="Не закрывайте окно до завершения",
            bg_role="surface",
        )
        self.app_bar.pack(fill=tk.X, pady=(0, theme.space("md")))

        self.task_label = tk.Label(
            main_frame,
            text="Подготовка к обновлению...",
            anchor="w",
            justify="left",
            **theme.text("title_small", fg_role="primary"),
        )
        self.task_label.pack(fill=tk.X, pady=(0, theme.space("sm")))

        self.status_label = tk.Label(
            main_frame,
            text="",
            anchor="w",
            justify="left",
            wraplength=theme.px(420),
            **theme.text("body_small", fg_role="on_surface_variant"),
        )
        self.status_label.pack(fill=tk.X, pady=(0, theme.space("md")))

        self.progress = LinearProgress(main_frame, width=350, bg_role="surface")
        self.progress.pack(fill=tk.X)

    def start_update_process(self):
        """Запускает процесс обновления"""
        self.is_updating = True
        thread = threading.Thread(target=self._update_thread, daemon=True)
        thread.start()

    def _update_thread(self):
        """Поток для выполнения обновлений"""
        try:
            total_tasks = len(self.update_tasks)

            overall_progress = 0

            for i, task in enumerate(self.update_tasks):
                if not self.is_updating:
                    break

                self.current_task_index = i
                task_name = task['name']
                updater_class = task['updater_class']
                download_url = task['download_url']

                self._update_task_info(f"Обновление {task_name} ({i+1}/{total_tasks})")
                self._update_status(f"Начинаю обновление {task_name}...")
                print(f"\n🔄 Начинаю обновление {task_name}...")

                if updater_class == 'ZapretBundleUpdater':
                    success = self._update_bundle(download_url, task_name, overall_progress, total_tasks, i)
                    if success:
                        self.manager_updated = True
                else:
                    success = False
                    print(f"❌ Неизвестный класс обновления: {updater_class}")

                overall_progress = int((i + 1) / total_tasks * 100)
                self._update_progress_bar(overall_progress)

                if success:
                    print(f"✅ {task_name} успешно обновлен!")
                    self._update_status(f"{task_name} обновлен успешно")
                else:
                    print(f"❌ Не удалось обновить {task_name}")
                    self._update_status(f"{task_name}: ошибка обновления")

            if self.is_updating:
                print("\n🎉 Обновление завершено!")
                self._update_task_info("Обновление завершено")
                self._update_status("Все компоненты успешно обновлены")

                self._update_progress_bar(100)

                if self.manager_updated:
                    self._show_restart_message()
                else:
                    self._safe_after(self.window.destroy, 2000)
            else:
                print("\n⏹️ Обновление отменено пользователем")
                self._update_task_info("Обновление отменено")
                self._update_status("Операция прервана пользователем")
                self._safe_after(self.window.destroy, 800)

        except Exception as e:
            print(f"\n❌ Ошибка при обновлении: {str(e)}")
            import traceback
            traceback.print_exc()
            self._update_status(f"Ошибка: {str(e)}")

    def _update_bundle(self, download_url, task_name, base_progress, total_tasks, task_index):
        """Полное обновление менеджера и службы одним архивом."""
        try:
            progress_map = {
                "Остановка": 10,
                "Скачивание": 25,
                "Распаковка": 40,
                "Применение обновления менеджера": 55,
                "Копирование файлов системы": 60,
                "Копирование бинарных": 70,
                "Создание службы systemd": 80,
                "Обновление systemd": 85,
                "Включение автозапуска": 90,
                "Запуск службы": 95,
                "Полное обновление завершено": 100,
            }

            def progress_callback(message, percent):
                step_progress = percent if percent is not None else 0
                for key, value in progress_map.items():
                    if key in message:
                        step_progress = value
                        break
                task_internal_progress = step_progress
                task_weight = 100 / total_tasks
                previous_tasks_progress = task_index * task_weight
                current_task_progress = task_internal_progress * (task_weight / 100)
                overall_progress = int(previous_tasks_progress + current_task_progress)
                self._safe_after(lambda p=overall_progress: self._update_progress_bar(p))
                self._safe_after(lambda m=message, s=step_progress: self._update_progress_message(m, s))

            print("  📦 Полное обновление пакета...")
            return self.bundle_updater.update_bundle(
                download_url,
                self.window,
                progress_callback,
                cancel_check=lambda: not self.is_updating,
            )

        except Exception as e:
            print(f"  ❌ Ошибка полного обновления: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def _safe_after(self, func, delay=0):
        """Планирует вызов в главном потоке, не падая на уничтоженном окне."""
        try:
            if self.window.winfo_exists():
                self.window.after(delay, func)
        except (tk.TclError, RuntimeError):
            pass

    def _update_task_info(self, text):
        """Обновляет информацию о текущей задаче"""
        self._safe_after(lambda: self.task_label.config(text=text))

    def _update_status(self, text):
        """Обновляет статус обновления"""
        self._safe_after(lambda: self.status_label.config(text=text))

    def _update_progress_message(self, message, percent=None):
        """Обновляет сообщение о прогрессе"""
        if percent is not None:
            text = f"[{percent}%] {message}"
        else:
            text = message

        self.status_label.config(text=text)
        print(f"    {message}")

    def _update_progress_bar(self, percent):
        """Обновляет линейный прогресс Material 3."""
        try:
            self.window.update_idletasks()
        except tk.TclError:
            pass
        self.progress.set(max(0, min(100, percent)))

    def _show_restart_message(self):
        """Показывает сообщение о необходимости перезапуска"""
        self._update_task_info("Обновление завершено")
        self._update_status("Перезапуск программы...")

        self._safe_after(self._restart_manager, 2000)

    def _restart_manager(self):
        """Перезапускает менеджер"""
        try:
            from core.manager_updater import ManagerUpdater
            manager_updater = ManagerUpdater()
            print("🔄 Перезапускаю менеджер...")
            manager_updater.restart_manager()

            self.window.destroy()
            if self.parent:
                self.parent.destroy()

        except Exception as e:
            print(f"❌ Ошибка при перезапуске менеджера: {e}")
            self.window.destroy()

    def cancel_update(self):
        """Отменяет обновление: флаг проверяется до начала изменения установки."""
        self.is_updating = False
        print("\n⏹️ Запрошена отмена обновления")
        self._update_task_info("Отмена обновления...")
        self._update_status("Отмена будет выполнена до изменения установки")

    def on_close(self):
        """Обработчик закрытия окна"""
        if self.is_updating:
            from ui.components.custom_messagebox import ask_yesno
            if ask_yesno(self.window, "Отмена обновления",
                         "Обновление еще не завершено. Вы уверены, что хотите отменить?"):
                # Окно закроет поток обновления, когда безопасно завершит работу.
                self.cancel_update()
        else:
            self.window.destroy()

    def close_window(self):
        """Закрывает окно"""
        self.window.destroy()


def show_update_progress_window(parent, update_tasks):
    """Показывает окно прогресса обновления"""
    window = UpdateProgressWindow(parent, update_tasks)
    window.run()
