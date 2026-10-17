#!/usr/bin/env python3
from __future__ import annotations

import os
import tkinter as tk

from core.service_manager import ServiceManager
from ui.theme import theme
from ui.windows.main.main_chrome import MainChromeMixin
from ui.windows.main.main_gamefilter import MainGameFilterMixin
from ui.windows.main.main_service import MainServiceMixin
from ui.windows.main.main_startup import MainStartupMixin
from ui.windows.main.main_tooltips import MainTooltipsMixin
from ui.windows.main.main_ui import MainUIMixin
from ui.windows.main.main_updates import MainUpdatesMixin
from ui.windows.main.main_warp import MainWarpMixin


class MainWindow(
    MainChromeMixin,
    MainTooltipsMixin,
    MainStartupMixin,
    MainUpdatesMixin,
    MainUIMixin,
    MainGameFilterMixin,
    MainServiceMixin,
    MainWarpMixin,
):
    """Главное окно: композиция миксинов из ui.windows.main."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        theme.attach(self.root)
        self.setup_window_properties()
        self.root.title("Zapret DPI Manager")

        # Переменные для состояния кнопок
        self.zapret_running = False
        self.autostart_enabled = False
        self.service_running = False  # Статус службы
        self.settings_menu_open = False  # Флаг для меню настроек
        self.restarting = False  # Флаг перезапуска службы

        self.service_manager = ServiceManager()

        # Всплывающие подсказки инициализируем ДО setup_ui(): обработчики
        # наведения обращаются к этим атрибутам.
        self.status_tooltip = None
        self.game_filter_tooltip = None

        # Путь к файлу gamefilter.enable
        home_dir = os.path.expanduser("~")
        self.game_filter_file = os.path.join(home_dir, "Zapret_DPI_Manager", "utils", "gamefilter.enable")

        self.setup_ui()
        self._apply_main_window_size()
        # Сначала проверяем зависимости
        self.check_dependencies_on_startup()
        # Затем проверяем zapret
        self.check_zapret_on_startup()
        # Проверка и создание обязательных файлов в files/lists/
        self.ensure_lists_files()
        # Проверяем целостность файлов
        # self.check_files_on_startup()
        self.load_current_strategy()
        self.check_service_status()  # Проверяем статус службы при запуске
        self.update_game_filter_indicator()  # GameFilter / активный пресет игры (маркер в utils)
        self.schedule_status_update()  # Запускаем периодическую проверку
        self.root.after(100, self.check_updates_on_startup)

        # Bind событий фокус
        self.root.bind("<FocusIn>", self.on_focus_in)
        self.root.bind("<FocusOut>", self.on_focus_out)

    def run(self) -> None:
        """Запускает главное окно"""
        self.root.mainloop()

    def rebuild_ui(self) -> None:
        """Пересобирает интерфейс после смены темы, сохраняя состояние окна."""
        try:
            self.close_settings_menu()
        except Exception:
            pass
        for widget in self.root.winfo_children():
            try:
                widget.destroy()
            except tk.TclError:
                pass
        self.settings_menu = None
        self.settings_menu_open = False
        self.status_tooltip = None
        self.game_filter_tooltip = None
        self.icon_tooltip = None

        theme.apply(self.root)
        self.setup_ui()
        self._apply_main_window_size()
        self.load_current_strategy()
        self.check_service_status()
        self.update_game_filter_indicator()


if __name__ == "__main__":
    app = MainWindow()
    app.run()
