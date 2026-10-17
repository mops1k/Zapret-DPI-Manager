# -*- coding: utf-8 -*-
"""Дымовой тест всех окон после перевода на Material 3.

Запуск: XAUTHORITY=$(ls /run/user/1000/xauth_* | head -1) python3 tools/smoke_ui_material.py
Скрипт создаёт окна в реальном Tk, обновляет их и закрывает; модальные run() не вызываются.
"""
from __future__ import annotations

import os
import sys
import traceback

import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.theme import theme  # noqa: E402


class _HostStub:
    """Заглушка главного окна для окон, которым нужен MainWindowActions."""

    def __init__(self, root):
        self.root = root
        self.service_running = False

    def is_game_filter_enabled(self):
        return False

    def ensure_sudo_password(self):
        return False

    def show_status_message(self, *_args, **_kwargs):
        pass

    def update_game_filter_indicator(self):
        pass

    def _show_game_filter_warning(self, *_args, **_kwargs):
        pass

    def _perform_game_filter_toggle(self, *_args, **_kwargs):
        pass

    def restart_zapret_after_preset(self, *_args, **_kwargs):
        pass

    def hide_game_filter_tooltip(self, *_args, **_kwargs):
        pass

    def hide_icon_tooltip(self, *_args, **_kwargs):
        pass

    def hide_status_tooltip(self, *_args, **_kwargs):
        pass

    def _on_warning_accept(self, *_args, **_kwargs):
        pass

    def load_current_strategy(self):
        pass


def _close(widget) -> None:
    try:
        if widget is not None and widget.winfo_exists():
            widget.destroy()
    except tk.TclError:
        pass


def _iter_widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _iter_widgets(child)


def _check_button_clipping(target: tk.Misc, name: str, warnings: list[str]) -> None:
    """Ищет кнопки Material, которые Tk сжал: надпись на них обрезается."""
    from ui.components.material.button import MaterialButton

    for widget in _iter_widgets(target):
        if not isinstance(widget, MaterialButton):
            continue
        try:
            if widget.winfo_width() + 1 < widget.winfo_reqwidth():
                warnings.append(
                    f"{name}: кнопка {widget.cget('text')!r} сжата "
                    f"({widget.winfo_width()} < {widget.winfo_reqwidth()})"
                )
        except tk.TclError:
            continue


def _check_error_labels(target: tk.Misc, name: str, warnings: list[str]) -> None:
    """Ищет в окне метки с текстом об ошибке: они означают проглоченное исключение."""
    for widget in _iter_widgets(target):
        if not isinstance(widget, tk.Label):
            continue
        try:
            text = str(widget.cget("text"))
        except tk.TclError:
            continue
        if text.startswith("Ошибка") or text.startswith("❌ Ошибка"):
            warnings.append(f"{name}: метка ошибки — {text[:90]!r}")


def main() -> int:
    failures: list[str] = []
    ok: list[str] = []
    warnings: list[str] = []

    def check(name, factory, closer=None):
        try:
            widget = factory()
            target = closer(widget) if closer else widget
            if isinstance(target, tk.Misc):
                target.update_idletasks()
                target.update()
                _check_button_clipping(target, name, warnings)
                _check_error_labels(target, name, warnings)
                _close(target)
            ok.append(name)
        except Exception as exc:  # noqa: BLE001 - дымовой тест собирает все ошибки
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    # --- Главное окно: его root используем для остальных окон ---
    import ui.windows.main_window as mw

    mw.MainWindow.check_dependencies_on_startup = lambda self: True
    mw.MainWindow.check_zapret_on_startup = lambda self: True
    mw.MainWindow.check_updates_on_startup = lambda self: None

    main_window = None
    root = None
    try:
        main_window = mw.MainWindow()
        root = main_window.root
        main_window.root.geometry("1000x760")
        main_window.root.update_idletasks()
        main_window.root.update()
        _check_button_clipping(main_window.root, "MainWindow", warnings)
        _check_error_labels(main_window.root, "MainWindow", warnings)
        ok.append("MainWindow")
        # переключение темы и пересборка
        main_window._on_theme_switch(False)
        main_window.root.update_idletasks()
        assert theme.mode == "light"
        main_window._on_theme_switch(True)
        main_window.root.update_idletasks()
        assert theme.mode == "dark"
        _check_button_clipping(main_window.root, "MainWindow(light)", warnings)
        ok.append("MainWindow.theme_switch")
        # меню настроек
        main_window.open_settings_menu()
        main_window.root.update_idletasks()
        assert main_window.settings_menu_open
        main_window.close_settings_menu()
        ok.append("MainWindow.settings_menu")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"MainWindow: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    if root is None:
        root = tk.Tk()
        root.geometry("1000x760")
        theme.attach(root)
        root.update()

    host = _HostStub(root)

    # --- Окна стратегий и тестера ---
    from ui.windows.custom_strategy_window import CustomStrategyWindow
    from ui.windows.bbdpi_strategy_window import BBDpiStrategyWindow
    from ui.windows.strategy_selector_window import (
        AutoSelectionWindow,
        StrategySelectionWindow,
        StrategySelectorWindow,
    )
    from ui.windows.strategy_tester_window import StrategyTesterWindow
    from ui.windows.strategy_window import StrategyWindow

    for cls in (
        StrategySelectorWindow,
        AutoSelectionWindow,
        StrategySelectionWindow,
        StrategyWindow,
        CustomStrategyWindow,
        BBDpiStrategyWindow,
    ):
        check(cls.__name__, lambda c=cls: c(root), lambda w: getattr(w, "root", None))

    check("StrategyTesterWindow", lambda: StrategyTesterWindow(root), lambda w: w.window)

    # --- Окна данных ---
    from ui.windows.dns_settings_window import DNSSettingsWindow
    from ui.windows.hostlist_settings_window import HostlistSettingsWindow
    from ui.windows.ipset_settings_window import (
        IpsetFilterWindow,
        IpsetMainWindow,
        IpsetSettingsWindow,
    )
    from ui.windows.service_unlock_window import ServiceUnlockWindow

    check("HostlistSettingsWindow", lambda: HostlistSettingsWindow(root), lambda w: w.create_window())
    for cls in (IpsetMainWindow, IpsetFilterWindow, IpsetSettingsWindow):
        check(cls.__name__, lambda c=cls: c(root), lambda w: w.create_window())
    check("DNSSettingsWindow", lambda: DNSSettingsWindow(root), lambda w: w.create_window())
    check("ServiceUnlockWindow", lambda: ServiceUnlockWindow(root), lambda w: w.create_window())

    # --- Проверка соединения (UI без запуска тестов) ---
    from ui.windows.connection_check_window import ConnectionCheckWindow

    def make_connection():
        window = ConnectionCheckWindow(root)
        window.window = tk.Toplevel(root)
        window.window.configure(bg=theme.color("surface"))
        window.setup_ui()
        return window

    check("ConnectionCheckWindow", make_connection, lambda w: w.window)

    # --- GameFilter ---
    from ui.windows.gamefilter_window import GameFilterWindow, GamePresetWindow
    from ui.windows.main.main_gamefilter_warning import show_game_filter_warning_dialog

    check("GameFilterWindow", lambda: GameFilterWindow(root, host), lambda w: w.root)
    check("GamePresetWindow", lambda: GamePresetWindow(root, host), lambda w: w.root)

    # --- Обновление, отчёт ---
    from ui.windows.report_window import ReportWindow, find_latest_report
    from ui.windows.update_window import UpdateProgressWindow, UpdateWindow
    from ui.windows.warp_window import WarpWindow

    check("UpdateWindow", lambda: UpdateWindow(root), lambda w: w.root)
    check("WarpWindow", lambda: WarpWindow(root), lambda w: w.create_window())

    def make_progress():
        window = UpdateProgressWindow(root, [])
        window.window = tk.Toplevel(root)
        window.window.configure(bg=theme.color("surface"))
        window.setup_ui()
        return window

    check("UpdateProgressWindow", make_progress, lambda w: w.window)

    latest = find_latest_report()
    if latest is not None:
        check("ReportWindow", lambda: ReportWindow(root, latest), lambda w: w.window)

    # --- Предупреждение GameFilter (модальное, закрываем сразу) ---
    def make_warning():
        show_game_filter_warning_dialog(host, "both")
        return None

    try:
        root.after(600, lambda: [w.destroy() for w in root.winfo_children() if isinstance(w, tk.Toplevel)])
        make_warning()
        ok.append("GameFilterWarning")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"GameFilterWarning: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    _ = _close
    if main_window is not None:
        _close(main_window.root)
    else:
        _close(root)

    print("OK:", ", ".join(ok))
    if warnings:
        print("\nWARNINGS (сжатые кнопки):")
        for line in warnings:
            print(" -", line)
    if failures:
        print("\nFAILURES:")
        for line in failures:
            print(" -", line)
        return 1
    print("\nSMOKE: все окна создаются без ошибок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
