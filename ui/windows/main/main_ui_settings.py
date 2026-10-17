"""Главное окно: меню настроек и действия (Material 3)."""
from __future__ import annotations

import tkinter as tk

from core.dpi_utils import fit_toplevel_to_content
from ui.components.material import MaterialButton, MaterialDivider, MaterialSwitch
from ui.theme import theme
from ui.windows.connection_check_window import ConnectionCheckWindow
from ui.windows.dns_settings_window import DNSSettingsWindow
from ui.windows.hostlist_settings_window import HostlistSettingsWindow
from ui.windows.ipset_settings_window import IpsetMainWindow
from ui.windows.service_unlock_window import ServiceUnlockWindow
from ui.windows.strategy_selector_window import StrategySelectorWindow
from ui.windows.update_window import show_update_window
from ui.integrations.zapret_uninstall import run_zapret_uninstall


class MainUISettingsMixin:
    """Выпадающая панель настроек M3 с пунктами и переключателем темы."""

    MENU_ITEMS = (
        ("Сменить стратегию", "open_service_window", "◆"),
        ("Проверка соединения", "open_connection_check", "⇅"),
        ("Настройки Hostlist", "open_hostlist_settings", "☰"),
        ("Настройки IPSet", "open_ipset_settings", "◎"),
        ("Настройки DNS", "open_dns_settings", "⇄"),
        ("Разблокировать сервисы", "open_service_unlock", "🔓"),
        ("WARP (usque)", "open_warp_settings", "🛰"),
        ("Обновить Zapret", "open_update_settings", "⬆"),
        ("Забыть пароль sudo", "forget_sudo_password", "🔑"),
        ("Удалить Zapret", "uninstall_zapret", "🗑"),
    )

    def open_settings_menu(self):
        """Открывает меню настроек под иконкой."""
        if self.settings_menu_open:
            return
        self.settings_menu_open = True
        t = theme

        try:
            menu_x = self.settings_icon.winfo_rootx() - t.px(200)
            menu_y = self.settings_icon.winfo_rooty() + self.settings_icon.winfo_height() + t.px(6)
        except tk.TclError:
            menu_x = menu_y = 0

        self.settings_menu = tk.Toplevel(self.root)
        self.settings_menu.wm_overrideredirect(True)
        self.settings_menu.configure(bg=t.color("surface_container"))

        card = tk.Frame(self.settings_menu, bg=t.color("surface_container"))
        card.pack(fill=tk.BOTH, expand=True, padx=t.px(2), pady=t.px(2))

        for label, method_name, icon in self.MENU_ITEMS:
            button = MaterialButton(
                card,
                text=label,
                icon=icon,
                command=self._menu_action(method_name),
                variant="text",
                anchor="w",
                parent_role="surface_container",
                padx=t.px(14),
                pady=t.px(10),
            )
            button.pack(fill=tk.X)

        MaterialDivider(card, bg_role="surface_container", inset=t.px(12)).pack(
            fill=tk.X, pady=t.px(6)
        )

        theme_row = tk.Frame(card, bg=t.color("surface_container"))
        theme_row.pack(fill=tk.X, padx=t.px(14), pady=(0, t.px(8)))
        MaterialSwitch(
            theme_row,
            "Тёмная тема",
            value=theme.is_dark,
            command=self._on_theme_switch,
            bg_role="surface_container",
        ).pack(anchor="w")

        self.settings_menu.update_idletasks()
        try:
            self.settings_menu.geometry(f"+{max(0, menu_x)}+{max(0, menu_y)}")
            fit_toplevel_to_content(
                self.settings_menu,
                min_width=t.px(240),
                min_height=t.px(120),
                margin_width=t.px(8),
                margin_height=t.px(12),
            )
        except tk.TclError:
            pass

        self.settings_menu.bind("<FocusOut>", lambda _e: self.close_settings_menu())
        self.root.bind("<Button-1>", self.check_close_settings_menu, add="+")

    def _menu_action(self, method_name: str):
        """Возвращает обработчик пункта меню (закрывает панель и вызывает метод)."""
        def handler():
            self.close_settings_menu()
            method = getattr(self, method_name, None)
            if callable(method):
                method()
        return handler

    def _on_theme_switch(self, is_dark: bool) -> None:
        """Переключает тёмную/светлую тему и пересобирает интерфейс."""
        self.close_settings_menu()
        new_mode = "dark" if is_dark else "light"
        theme.set_mode(new_mode, notify=False)
        rebuild = getattr(self, "rebuild_ui", None)
        if callable(rebuild):
            rebuild()
        else:
            theme.apply(self.root)

    def check_close_settings_menu(self, event):
        """Закрывает меню при клике вне его области."""
        if not (hasattr(self, "settings_menu") and self.settings_menu and self.settings_menu.winfo_exists()):
            return
        widget = event.widget
        while widget:
            if widget == self.settings_menu:
                return
            widget = getattr(widget, "master", None)
        if event.widget != self.settings_icon and not self.is_event_in_widget(event, self.settings_icon):
            self.close_settings_menu()
            self.hide_status_tooltip()

    def close_settings_menu(self):
        """Закрывает меню настроек."""
        if hasattr(self, "settings_menu") and self.settings_menu:
            try:
                self.settings_menu.destroy()
            except tk.TclError:
                pass
        self.settings_menu = None
        self.settings_menu_open = False
        try:
            self.root.unbind("<Button-1>")
        except tk.TclError:
            pass

    def is_event_in_widget(self, event, widget):
        """Проверяет, находится ли событие в области виджета."""
        try:
            x, y, width, height = (
                widget.winfo_rootx(),
                widget.winfo_rooty(),
                widget.winfo_width(),
                widget.winfo_height(),
            )
            return x <= event.x_root <= x + width and y <= event.y_root <= y + height
        except Exception:
            return False

    # -- открытие окон ------------------------------------------------------

    def open_service_window(self):
        """Открывает окно выбора типа стратегии."""
        self.close_settings_menu()
        selector_window = StrategySelectorWindow(self.root)
        selector_window.run()
        try:
            if self.root.winfo_exists():
                self.load_current_strategy()
        except tk.TclError:
            pass

    def open_connection_check(self):
        """Открывает окно проверки соединения."""
        self.close_settings_menu()
        ConnectionCheckWindow(self.root).run()

    def open_hostlist_settings(self):
        """Открывает окно настроек HOSTLIST."""
        self.close_settings_menu()
        HostlistSettingsWindow(self.root).run()

    def open_ipset_settings(self):
        """Открывает окно настроек IPset."""
        self.close_settings_menu()
        IpsetMainWindow(self.root).run()

    def open_dns_settings(self):
        """Открывает окно настроек DNS."""
        self.close_settings_menu()
        DNSSettingsWindow(self.root).run()

    def open_service_unlock(self):
        """Открывает окно разблокировки сервисов."""
        self.close_settings_menu()
        ServiceUnlockWindow(self.root).run()

    def open_warp_settings(self):
        """Открывает окно WARP (usque): движок, аккаунт, настройки, диагностика."""
        self.close_settings_menu()
        from ui.windows.warp_window import WarpWindow

        WarpWindow(self.root).run()
        self.check_warp_status()

    def open_update_settings(self):
        """Открывает окно обновления Zapret."""
        self.close_settings_menu()
        show_update_window(self.root)

    def forget_sudo_password(self):
        """Удаляет запомненный пароль sudo (кэш askpass)."""
        from core.sudo_helper import forget_cached_password, has_cached_password

        self.close_settings_menu()
        if not has_cached_password():
            self.show_status_message("Запомненного пароля нет", warning=True)
        elif forget_cached_password():
            self.show_status_message("Запомненный пароль sudo удалён", success=True)
        else:
            self.show_status_message("Не удалось удалить запомненный пароль", error=True)

    def uninstall_zapret(self):
        """Запускает удаление Zapret."""
        try:
            result = run_zapret_uninstall(self.root)
            if result:
                self.show_status_message("Zapret удален. Программа закроется...", success=True)
                self.root.after(2000, self.root.destroy)
            else:
                self.show_status_message("Удаление отменено или не удалось", warning=True)
        except ImportError as e:
            self.show_status_message(f"Ошибка импорта модуля удаления: {e}", error=True)
        except Exception as e:
            self.show_status_message(f"Ошибка при удалении: {e}", error=True)
