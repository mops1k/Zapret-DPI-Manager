"""Главное окно: разметка Material 3."""
from __future__ import annotations

import os
import tkinter as tk

from ui.components.material import (
    MaterialCard,
    MaterialButton,
    TopAppBar,
    filled_button,
    outlined_button,
    tonal_button,
)
from ui.theme import theme
from ui.windows.info_window import show_info_dialog

#: Ширина колонки контента: на Steam Deck (1280x800) окно остаётся читаемым.
CONTENT_MAX_WIDTH = 760


class MainUILayoutMixin:
    """Верхняя панель, карточки статуса и стратегии, кнопки управления."""

    def setup_ui(self):
        t = theme
        self.main_frame = tk.Frame(self.root, bg=t.color("surface"))
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=t.space("xl"), pady=t.space("lg"))

        content = tk.Frame(self.main_frame, bg=t.color("surface"))
        content.pack(fill=tk.BOTH, expand=True)
        self.content_frame = content

        # --- Верхняя панель приложения ---
        self.app_bar = TopAppBar(
            content,
            title="Zapret DPI Manager",
            subtitle="Обход блокировок DPI",
            bg_role="surface",
        )
        self.app_bar.pack(fill=tk.X, pady=(0, t.space("lg")))

        # Статус службы и перезапуск — ведущая зона панели.
        self.status_indicator = tk.Label(
            self.app_bar.leading,
            text="⬤",
            cursor="hand2",
            **theme.text("title_medium", fg_role="error"),
        )
        self.app_bar.add_leading_widget(self.status_indicator, padx=(0, t.space("xs")))
        self.status_indicator.bind("<Enter>", self.show_status_tooltip)
        self.status_indicator.bind("<Leave>", self.hide_status_tooltip)

        self.restart_icon = MaterialButton(
            self.app_bar.leading,
            text="",
            icon="↻",
            command=self.restart_zapret_service_properly,
            variant="icon",
            padx=t.px(10),
            pady=t.px(8),
        )
        self.app_bar.add_leading_widget(self.restart_icon, padx=(0, t.space("sm")))
        self.restart_icon.bind(
            "<Enter>", lambda e: self.show_icon_tooltip(e, "Перезапустить службу")
        )
        self.restart_icon.bind("<Leave>", lambda e: self.hide_icon_tooltip())

        # Действия справа: GameFilter, настройки, руководство, информация, донат.
        self.game_filter_indicator = MaterialButton(
            self.app_bar.actions,
            text="",
            icon="⌨",
            variant="icon",
            padx=t.px(10),
            pady=t.px(8),
        )
        self.app_bar.add_action_widget(self.game_filter_indicator, padx=(0, t.space("xs")))
        self.game_filter_indicator.bind("<Button-1>", self.toggle_game_filter, add="+")
        self.game_filter_indicator.bind("<Enter>", self.show_game_filter_tooltip, add="+")
        self.game_filter_indicator.bind("<Leave>", self.hide_game_filter_tooltip, add="+")

        self.settings_icon = MaterialButton(
            self.app_bar.actions,
            text="",
            icon="⚙",
            command=self.toggle_settings_menu,
            variant="icon",
            padx=t.px(10),
            pady=t.px(8),
        )
        self.app_bar.add_action_widget(self.settings_icon, padx=(0, t.space("xs")))
        self.settings_icon.bind(
            "<Enter>", lambda e: self.show_icon_tooltip(e, "Открыть меню настроек")
        )
        self.settings_icon.bind("<Leave>", lambda e: self.hide_icon_tooltip())

        self.book_icon = MaterialButton(
            self.app_bar.actions,
            text="",
            icon="🗏",
            command=self.open_user_guide,
            variant="icon",
            padx=t.px(10),
            pady=t.px(8),
        )
        self.app_bar.add_action_widget(self.book_icon, padx=(0, t.space("xs")))
        self.book_icon.bind(
            "<Enter>", lambda e: self.show_icon_tooltip(e, "Открыть руководство пользователя")
        )
        self.book_icon.bind("<Leave>", lambda e: self.hide_icon_tooltip())

        self.info_icon = MaterialButton(
            self.app_bar.actions,
            text="",
            icon="🛈",
            command=lambda: show_info_dialog(self.root),
            variant="icon",
            padx=t.px(10),
            pady=t.px(8),
        )
        self.app_bar.add_action_widget(self.info_icon, padx=(0, t.space("xs")))
        self.info_icon.bind(
            "<Enter>", lambda e: self.show_icon_tooltip(e, "Информация о программе")
        )
        self.info_icon.bind("<Leave>", lambda e: self.hide_icon_tooltip())

        # --- Карточка службы ---
        self.service_card = MaterialCard(
            content,
            variant="elevated",
            title="Служба Zapret DPI",
            subtitle="Движок nfqws и правила NFQUEUE",
        )
        self.service_card.pack(fill=tk.X, pady=(0, t.space("md")))

        service_row = tk.Frame(self.service_card.content, bg=t.color("surface_container_low"))
        service_row.pack(fill=tk.X)

        self.service_state_label = tk.Label(
            service_row,
            text="Проверка состояния…",
            anchor="w",
            **theme.text("body_large", bg_role="surface_container_low"),
        )
        self.service_state_label.pack(fill=tk.X)

        # Кнопки — отдельной строкой: в одну строку со статусом надписи не помещались
        # и Tk сжимал кнопки, обрезая текст.
        self.service_buttons_row = tk.Frame(
            self.service_card.content, bg=t.color("surface_container_low")
        )
        self.service_buttons_row.pack(fill=tk.X, pady=(t.space("md"), 0))

        self.zapret_button = filled_button(
            self.service_buttons_row,
            "Запустить Zapret DPI",
            self.toggle_zapret,
            parent_role="surface_container_low",
            padx=t.px(14),
        )

        self.autostart_button = tonal_button(
            self.service_buttons_row,
            "Включить автозапуск",
            self.toggle_autostart,
            parent_role="surface_container_low",
            padx=t.px(14),
        )

        self._service_buttons_vertical = None
        self._layout_service_buttons(force=True)
        self.service_buttons_row.bind("<Configure>", self._on_service_buttons_configure)

        # --- Карточка WARP (usque) ---
        # Туннель Cloudflare WARP управляется отдельно от службы zapret.
        self.build_warp_card(content, t)

        # --- Карточка стратегии ---
        self.strategy_card = MaterialCard(
            content,
            variant="outlined",
            title="Текущая стратегия",
            subtitle="Набор аргументов nfqws из config.txt",
        )
        self.strategy_card.pack(fill=tk.X)

        strategy_row = tk.Frame(self.strategy_card.content, bg=t.color("surface"))
        strategy_row.pack(fill=tk.X)

        strategy_caption = tk.Label(
            strategy_row,
            text="Стратегия:",
            **theme.text("body_medium", fg_role="on_surface_variant"),
        )
        strategy_caption.pack(side=tk.LEFT, padx=(0, t.space("xs")))

        self.strategy_value = tk.Label(
            strategy_row,
            text="Загрузка...",
            anchor="w",
            **theme.text("title_medium", fg_role="primary"),
        )
        self.strategy_value.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.strategy_change_button = outlined_button(
            strategy_row,
            "Сменить",
            self.open_service_window,
            parent_role="surface",
        )
        self.strategy_change_button.pack(side=tk.RIGHT)

        # --- Статусная строка ---
        self.status_message = tk.Label(
            self.main_frame,
            text="",
            anchor="w",
            **theme.text("body_small", fg_role="on_surface_variant"),
        )
        self.status_message.pack(fill=tk.X, side=tk.BOTTOM, pady=(t.space("sm"), 0))

    # -- служебное ----------------------------------------------------------

    def _layout_service_buttons(self, force: bool = False) -> None:
        """Раскладывает кнопки службы: в строку, а при нехватке ширины — в столбец.

        Tk сжимает Canvas-кнопки, обрезая надписи, поэтому при узком окне кнопки
        переносятся друг под друга.
        """
        row = getattr(self, "service_buttons_row", None)
        if row is None:
            return
        try:
            width = row.winfo_width()
        except tk.TclError:
            return
        needed = (
            self.zapret_button.winfo_reqwidth()
            + self.autostart_button.winfo_reqwidth()
            + theme.space("sm")
        )
        vertical = width > 1 and width < needed
        if not force and vertical == getattr(self, "_service_buttons_vertical", None):
            return
        self._service_buttons_vertical = vertical
        for button in (self.zapret_button, self.autostart_button):
            button.pack_forget()
        if vertical:
            self.zapret_button.pack(fill=tk.X, pady=(0, theme.space("sm")))
            self.autostart_button.pack(fill=tk.X)
        else:
            self.zapret_button.pack(side=tk.LEFT, padx=(0, theme.space("sm")))
            self.autostart_button.pack(side=tk.LEFT)

    def _on_service_buttons_configure(self, event=None) -> None:
        if event is not None and event.widget is not self.service_buttons_row:
            return
        self._layout_service_buttons()

    def _strategy_label_alive(self) -> bool:
        """Виджет стратегии может быть уже уничтожен: окно закрывают с открытым селектором."""
        try:
            return bool(self.strategy_value) and self.strategy_value.winfo_exists()
        except (tk.TclError, AttributeError):
            return False

    def load_current_strategy(self):
        """Загружает и отображает текущую стратегию из файла name_strategy.txt"""
        if not self._strategy_label_alive():
            return
        try:
            manager_dir = os.path.expanduser("~/Zapret_DPI_Manager")
            name_strategy_file = os.path.join(manager_dir, "utils", "name_strategy.txt")
            config_file = os.path.join(manager_dir, "config.txt")

            name_strategy_exists = os.path.exists(name_strategy_file)
            config_exists = os.path.exists(config_file)

            strategy_name = "Не выбрано"

            if name_strategy_exists and config_exists:
                with open(name_strategy_file, "r", encoding="utf-8") as f:
                    name_content = f.read().strip()
                with open(config_file, "r", encoding="utf-8") as f:
                    config_content = f.read().strip()

                if name_content and config_content:
                    strategy_name = name_content
                else:
                    strategy_name = "Не выбрано"
                    if not config_content and name_content:
                        with open(name_strategy_file, "w", encoding="utf-8") as f:
                            f.write("")
            elif name_strategy_exists:
                with open(name_strategy_file, "r", encoding="utf-8") as f:
                    name_content = f.read().strip()
                strategy_name = name_content if name_content else "Не выбрано"
            else:
                os.makedirs(os.path.dirname(name_strategy_file), exist_ok=True)
                with open(name_strategy_file, "w", encoding="utf-8") as f:
                    f.write("")
                strategy_name = "Не выбрано"

            if not self._strategy_label_alive():
                return
            self.strategy_value.config(text=strategy_name)

        except Exception as e:
            print(f"Ошибка загрузки стратегии: {e}")
            if self._strategy_label_alive():
                self.strategy_value.config(text="Не выбрано")

    def toggle_settings_menu(self, event=None):
        """Открывает/закрывает меню настроек"""
        if self.settings_menu_open:
            self.close_settings_menu()
        else:
            self.open_settings_menu()

    def open_user_guide(self, event=None):
        """Открывает руководство пользователя в браузере"""
        import webbrowser

        url = (
            "https://github.com/mops1k/Zapret-DPI-Manager/blob/main/"
            "docs/user-guide-zapret-dpi-manager-steam-deck-ru.md"
        )
        try:
            webbrowser.open(url)
            self.show_status_message("Открываю руководство пользователя...", success=True)
        except Exception as e:
            error_msg = f"Не удалось открыть руководство: {e}"
            print(f"❌ {error_msg}")
            self.show_status_message(error_msg, error=True)
