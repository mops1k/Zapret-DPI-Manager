"""Окно WARP (usque): движок, аккаунт, настройки и диагностика туннеля.

Вся работа с системой идёт через :class:`core.warp_manager.WarpManager`
(установка usque, аккаунт, служба, журнал) — окно только собирает интерфейс,
показывает прогресс и выполняет длительные операции в фоновом потоке.

Проект «Zapret DPI Manager» © Aleksandr Kvintilyanov.
"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser

from core.app_logging import get_error_logger
from core.dpi_utils import place_toplevel_centered_on_parent, safe_grab_set, wait_window_safely
from core.warp_manager import DEFAULT_SNI, WarpManager, read_state, validate_state
from ui.components.material import (
    LinearProgress,
    MaterialCard,
    MaterialCheckbox,
    MaterialDropdown,
    MaterialRadio,
    MaterialSwitch,
    MaterialTextField,
    ThinScrollbar,
    TopAppBar,
    ask_yes_no,
    filled_button,
    outlined_button,
    show_message,
    text_button,
    tonal_button,
)
from ui.theme import theme
from ui.windows.main.main_warp import transport_label

#: Условия Cloudflare, которые подтверждает `usque register --accept-tos`.
TOS_URL = "https://www.cloudflare.com/application/terms/"

#: Транспорт WARP: подпись в интерфейсе -> значение в utils/warp.txt.
TRANSPORT_CHOICES = (
    ("Авто (QUIC → HTTP/2)", "auto"),
    ("QUIC", "quic"),
    ("HTTP/2", "http2"),
)

#: DNS туннеля: подпись -> значение в utils/warp.txt.
DNS_CHOICES = (
    ("Регистрировать DNS туннеля в системе (link)", "link"),
    ("Не трогать системный DNS (keep)", "keep"),
)

#: Состояние warp.service -> подпись.
SERVICE_STATES = {
    "active": "активна",
    "inactive": "остановлена",
    "failed": "ошибка запуска",
    "activating": "запускается",
    "deactivating": "останавливается",
    "unknown": "не найдена",
}

#: Обрезка хвоста journalctl для диалога.
ERROR_DETAIL_LIMIT = 1800


def _as_result(result) -> tuple[bool, str]:
    """Приводит ``(ok, message)`` из WarpManager к паре значений."""
    if isinstance(result, tuple) and len(result) == 2:
        return bool(result[0]), str(result[1])
    return bool(result), "" if result else "операция завершилась без результата"


def _clip(message: str, limit: int = ERROR_DETAIL_LIMIT) -> str:
    """Обрезает длинный текст ошибки (в message бывает хвост journalctl)."""
    text = (message or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… текст усечён, полный вывод — в журнале WARP и error.log"


class WarpWindow:
    """Окно WARP: движок usque, аккаунт Cloudflare, настройки туннеля, диагностика."""

    JOURNAL_LINES = 80
    #: Карточки с фоном surface_container_low (variant="elevated").
    ELEVATED_BG = "surface_container_low"
    #: Карточки с фоном surface (variant="outlined").
    PLAIN_BG = "surface"

    def __init__(self, parent):
        self.parent = parent
        self.window: tk.Toplevel | None = None
        #: Совместимость с окнами Material 3 (см. update_window.py).
        self.root: tk.Toplevel | None = None
        self.manager = WarpManager(progress=self._on_progress)
        self._busy = False
        self._mode_var = tk.StringVar(value="full")

    # -- окно ---------------------------------------------------------------

    def create_window(self):
        """Создаёт окно (повторный вызов возвращает уже созданное)."""
        if self.window is not None:
            try:
                if self.window.winfo_exists():
                    return self.window
            except tk.TclError:
                pass

        self.window = tk.Toplevel(self.parent)
        self.root = self.window
        self.window.title("WARP (usque)")
        self.window.configure(bg=theme.color("surface"))
        try:
            self.window.transient(self.parent)
        except tk.TclError:
            pass
        safe_grab_set(self.window)
        self.window.bind("<Escape>", lambda _e: self.close())
        self.window.bind("<Destroy>", self._on_destroy, add="+")

        self.setup_ui()
        self.refresh_all()
        self._apply_geometry()
        return self.window

    def run(self):
        """Показывает окно модально до закрытия."""
        self.create_window()
        wait_window_safely(self.window, self.parent)

    def close(self):
        """Закрывает окно."""
        try:
            if self.window is not None and self.window.winfo_exists():
                self.window.destroy()
        except tk.TclError:
            pass

    def _apply_geometry(self) -> None:
        """Размер по экрану: содержимое окна прокручивается."""
        t = theme
        try:
            self.window.update_idletasks()
            screen_w = max(1, int(self.window.winfo_screenwidth()))
            screen_h = max(1, int(self.window.winfo_screenheight()))
        except tk.TclError:
            return
        width = max(t.px(460), min(t.px(680), int(screen_w * 0.8)))
        height = max(t.px(420), min(t.px(760), int(screen_h * 0.85)))
        try:
            self.window.minsize(t.px(440), t.px(380))
        except tk.TclError:
            pass
        place_toplevel_centered_on_parent(
            self.window,
            self.parent,
            min_width=width,
            min_height=height,
            margin_width=t.px(16),
            margin_height=t.px(16),
            fixed_content_size=(width, height),
            immediate=True,
        )

    # -- разметка -----------------------------------------------------------

    def setup_ui(self) -> None:
        """Собирает интерфейс окна в стиле Material 3."""
        t = theme

        header = tk.Frame(self.window, bg=t.color("surface"))
        header.pack(fill=tk.X, padx=t.space("lg"), pady=(t.space("lg"), t.space("sm")))
        self.app_bar = TopAppBar(
            header,
            title="WARP (usque)",
            subtitle="Полный туннель Cloudflare WARP рядом с zapret",
            bg_role="surface",
        )
        self.app_bar.pack(fill=tk.X)

        status_box = tk.Frame(self.window, bg=t.color("surface"))
        status_box.pack(fill=tk.X, padx=t.space("lg"))
        self.status_label = tk.Label(
            status_box,
            text="Готово",
            anchor="w",
            justify="left",
            wraplength=t.px(560),
            **theme.text("body_small", fg_role="on_surface_variant"),
        )
        self.status_label.pack(fill=tk.X)
        self.progress = LinearProgress(status_box, width=520, bg_role="surface")
        self.progress.pack(fill=tk.X, pady=(t.space("xs"), t.space("sm")))

        # Кнопка закрытия резервирует место до прокручиваемой области.
        footer = tk.Frame(self.window, bg=t.color("surface"))
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=t.space("lg"), pady=t.space("lg"))
        self.close_button = text_button(footer, "Назад", self.close, parent_role="surface")
        self.close_button.pack(anchor=tk.CENTER)

        body = tk.Frame(self.window, bg=t.color("surface"))
        body.pack(fill=tk.BOTH, expand=True, padx=t.space("lg"))

        self.scrollbar = ThinScrollbar(body, command=None, bg_role="surface")
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas = tk.Canvas(
            body,
            bg=t.color("surface"),
            highlightthickness=0,
            borderwidth=0,
            yscrollcommand=self.scrollbar.set,
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar._command = self.canvas.yview

        self.content = tk.Frame(self.canvas, bg=t.color("surface"))
        self._content_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfig(self._content_id, width=e.width)
        )
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(sequence, self._on_mousewheel)

        self._build_engine_card()
        self._build_account_card()
        self._build_settings_card()
        self._build_diagnostics_card()

    def _row(self, parent, bg_role: str, pady_key: str = "sm") -> tk.Frame:
        """Строка внутри карточки (кнопки, поля)."""
        row = tk.Frame(parent, bg=theme.color(bg_role))
        row.pack(fill=tk.X, pady=(theme.space(pady_key), 0))
        return row

    def _note(self, parent, text: str, bg_role: str, role: str = "on_surface_variant") -> tk.Label:
        """Пояснение под элементом карточки."""
        label = tk.Label(
            parent,
            text=text,
            anchor="w",
            justify="left",
            wraplength=theme.px(520),
            **theme.text("body_small", bg_role=bg_role, fg_role=role),
        )
        label.pack(fill=tk.X, pady=(theme.px(2), 0))
        return label

    def _pack_buttons(self, row: tk.Frame, buttons) -> None:
        """Кнопки в столбик: в узком окне Tk сжимает Canvas-кнопки и режет надписи."""
        for button in buttons:
            button.pack(fill=tk.X, pady=(0, theme.space("xs")))

    # -- карточка: движок usque --------------------------------------------

    def _build_engine_card(self) -> None:
        bg = self.ELEVATED_BG
        card = MaterialCard(
            self.content,
            variant="elevated",
            title="Движок usque",
            subtitle="Клиент MASQUE/Connect-IP (MIT, Diniboy1123/usque) из GitHub-релизов",
        )
        card.pack(fill=tk.X, pady=(0, theme.space("md")))

        self.engine_installed_label = tk.Label(
            card.content,
            text="Установленная версия: …",
            anchor="w",
            justify="left",
            wraplength=theme.px(520),
            **theme.text("body_medium", bg_role=bg),
        )
        self.engine_installed_label.pack(fill=tk.X)

        self.engine_upstream_label = tk.Label(
            card.content,
            text="Последняя версия: проверка…",
            anchor="w",
            justify="left",
            wraplength=theme.px(520),
            **theme.text("body_small", bg_role=bg, fg_role="on_surface_variant"),
        )
        self.engine_upstream_label.pack(fill=tk.X, pady=(theme.px(2), 0))

        row = self._row(card.content, bg)
        self.install_button = filled_button(
            row, "Установить движок", self.install_engine, parent_role=bg
        )
        self._pack_buttons(row, (self.install_button,))

        self._note(
            card.content,
            "Установка скачивает сборку под текущую архитектуру, проверяет SHA256 "
            "и кладёт бинарник в /opt/warp (нужен пароль sudo).",
            bg,
        )

    # -- карточка: аккаунт WARP --------------------------------------------

    def _build_account_card(self) -> None:
        bg = self.PLAIN_BG
        card = MaterialCard(
            self.content,
            variant="outlined",
            title="Аккаунт Cloudflare WARP",
            subtitle="Регистрация выполняется только после согласия с условиями Cloudflare",
        )
        card.pack(fill=tk.X, pady=(0, theme.space("md")))

        self.account_label = tk.Label(
            card.content,
            text="Аккаунт: проверка…",
            anchor="w",
            justify="left",
            **theme.text("body_medium", bg_role=bg),
        )
        self.account_label.pack(fill=tk.X)

        self.tos_checkbox = MaterialCheckbox(
            card.content,
            "Я принимаю условия Cloudflare WARP",
            value=False,
            command=self._on_tos_changed,
            bg_role=bg,
        )
        self.tos_checkbox.pack(anchor="w", pady=(theme.space("sm"), 0))

        self.tos_link = tk.Label(
            card.content,
            text="Открыть условия Cloudflare WARP ↗",
            anchor="w",
            cursor="hand2",
            **theme.text("body_small", bg_role=bg, fg_role="primary"),
        )
        self.tos_link.pack(anchor="w", pady=(theme.px(2), 0))
        self.tos_link.bind("<Button-1>", lambda _e: self._open_tos())

        row = self._row(card.content, bg)
        self.register_button = filled_button(
            row,
            "Зарегистрировать аккаунт",
            self.register_account,
            parent_role=bg,
            state=tk.DISABLED,
        )
        self.delete_account_button = outlined_button(
            row, "Удалить аккаунт", self.delete_account, parent_role=bg
        )
        self._pack_buttons(row, (self.register_button, self.delete_account_button))

        self._note(
            card.content,
            "Без галочки согласия регистрация недоступна: usque вызывается с --accept-tos. "
            "Аккаунт сохраняется в /opt/warp/account.json (0600 root).",
            bg,
        )

    # -- карточка: настройки туннеля ---------------------------------------

    def _build_settings_card(self) -> None:
        bg = self.ELEVATED_BG
        state = read_state()
        card = MaterialCard(
            self.content,
            variant="elevated",
            title="Настройки туннеля",
            subtitle="Сохраняются в utils/warp.txt и читаются /opt/warp/warp_start.sh",
        )
        card.pack(fill=tk.X, pady=(0, theme.space("md")))

        # Режим: селективный появится на этапе 2 — сейчас только полный туннель.
        self.mode_radio = MaterialRadio(
            card.content,
            "Полный туннель — весь трафик идёт через WARP",
            variable=self._mode_var,
            value="full",
            bg_role=bg,
        )
        self.mode_radio.pack(anchor="w")
        self.mode_radio.set_enabled(False)
        self._note(
            card.content,
            "Селективный режим (выбор сайтов и списков) появится на этапе 2 и пока недоступен — "
            "переключателя для него нет намеренно.",
            bg,
        )

        self.transport_dropdown = MaterialDropdown(
            card.content,
            values=[label for label, _value in TRANSPORT_CHOICES],
            value=self._label_for(TRANSPORT_CHOICES, state.get("transport", "auto")),
            label="Транспорт",
            width=34,
            bg_role=bg,
        )
        self.transport_dropdown.pack(fill=tk.X, pady=(theme.space("sm"), 0))
        self._note(
            card.content,
            "Авто: сначала QUIC, при неудаче HTTP/2 (выбор делает warp_start.sh).",
            bg,
        )

        self.sni_field = MaterialTextField(
            card.content, label="SNI (MASQUE-сервер)", width=44, bg_role=bg
        )
        self.sni_field.pack(fill=tk.X, pady=(theme.space("sm"), 0))
        self.sni_field.insert(0, state.get("sni") or DEFAULT_SNI)
        self._note(card.content, f"По умолчанию {DEFAULT_SNI}.", bg)

        self.mtu_field = MaterialTextField(card.content, label="MTU (576–1500)", width=12, bg_role=bg)
        self.mtu_field.pack(fill=tk.X, pady=(theme.space("sm"), 0))
        self.mtu_field.insert(0, state.get("mtu") or "1280")
        self._note(card.content, "usque по умолчанию использует 1280: меньше риска фрагментации.", bg)

        self.dns_dropdown = MaterialDropdown(
            card.content,
            values=[label for label, _value in DNS_CHOICES],
            value=self._label_for(DNS_CHOICES, state.get("dns", "link")),
            label="DNS туннеля",
            width=34,
            bg_role=bg,
        )
        self.dns_dropdown.pack(fill=tk.X, pady=(theme.space("sm"), 0))
        self._note(
            card.content,
            "link — DNS туннеля регистрируются в системе (resolvectl), keep — системный DNS не трогаем.",
            bg,
        )

        self.help_switch = MaterialSwitch(
            card.content,
            "Лечить внешний трафик туннеля через zapret",
            value=str(state.get("help_tunnel", "1")) == "1",
            bg_role=bg,
        )
        self.help_switch.pack(anchor="w", pady=(theme.space("sm"), 0))
        self._note(
            card.content,
            "Включено: nfqws обрабатывает трафик endpoint'ов WARP профилем выбранного транспорта. "
            "Выключено: endpoint'ы просто исключаются из NFQUEUE.",
            bg,
        )

        row = self._row(card.content, bg)
        self.save_button = filled_button(
            row, "Сохранить настройки", self.save_settings, parent_role=bg
        )
        self._pack_buttons(row, (self.save_button,))

        self._note(
            card.content,
            "Правила zapret для туннеля обновляются сразу, но применяются при следующем "
            "перезапуске zapret: включение WARP перезапускает его автоматически.",
            bg,
        )

    # -- карточка: диагностика ---------------------------------------------

    def _build_diagnostics_card(self) -> None:
        bg = self.PLAIN_BG
        card = MaterialCard(
            self.content,
            variant="outlined",
            title="Диагностика",
            subtitle="Фактическое состояние туннеля и журнал службы",
        )
        card.pack(fill=tk.X, pady=(0, theme.space("md")))

        self.diag_label = tk.Label(
            card.content,
            text="Проверка состояния…",
            anchor="w",
            justify="left",
            wraplength=theme.px(520),
            **theme.text("body_small", bg_role=bg),
        )
        self.diag_label.pack(fill=tk.X)

        self.trace_label = tk.Label(
            card.content,
            text="Внешний адрес: не проверялся",
            anchor="w",
            justify="left",
            wraplength=theme.px(520),
            **theme.text("body_small", bg_role=bg, fg_role="on_surface_variant"),
        )
        self.trace_label.pack(fill=tk.X, pady=(theme.space("sm"), 0))

        row = self._row(card.content, bg)
        self.check_ip_button = tonal_button(
            row, "Проверить внешний адрес", self.check_external_ip, parent_role=bg
        )
        self.refresh_diag_button = outlined_button(
            row, "Обновить диагностику", self.refresh_diagnostics, parent_role=bg
        )
        self.journal_button = outlined_button(
            row, "Показать журнал", self.load_journal, parent_role=bg
        )
        self._pack_buttons(
            row, (self.check_ip_button, self.refresh_diag_button, self.journal_button)
        )

        self.journal_text = tk.Text(
            card.content,
            height=9,
            width=56,
            wrap=tk.WORD,
            bg=theme.color("surface_container_lowest"),
            fg=theme.color("on_surface"),
            font=theme.font("body_small", mono=True),
            highlightthickness=0,
            borderwidth=0,
            relief=tk.FLAT,
            padx=theme.px(8),
            pady=theme.px(6),
            state=tk.DISABLED,
        )
        self.journal_text.pack(fill=tk.X, pady=(theme.space("sm"), 0))
        self._note(
            card.content,
            "journalctl -u warp.service и записи с тегами warp / warp-usque.",
            bg,
        )

    # -- вспомогательное ----------------------------------------------------

    @staticmethod
    def _label_for(choices, value: str) -> str:
        """Подпись для значения из utils/warp.txt."""
        for label, item in choices:
            if item == value:
                return label
        return choices[0][0]

    @staticmethod
    def _value_for(choices, label: str, fallback: str) -> str:
        """Значение для выбранной подписи."""
        for text, item in choices:
            if text == label:
                return item
        return fallback

    def _safe_after(self, func, delay: int = 0) -> None:
        """Планирует вызов в главном потоке, не падая на уничтоженном окне."""
        try:
            if self.window is not None and self.window.winfo_exists():
                self.window.after(delay, func)
        except (tk.TclError, RuntimeError):
            pass

    def _on_mousewheel(self, event) -> None:
        """Прокрутка содержимого окна (X11: Button-4/5, Windows/macOS: delta)."""
        if getattr(event, "num", None) == 4:
            units = -1
        elif getattr(event, "num", None) == 5:
            units = 1
        else:
            units = -1 if getattr(event, "delta", 0) > 0 else 1
        try:
            self.canvas.yview_scroll(units, "units")
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        """Снимает глобальную привязку колеса при закрытии окна."""
        if event is not None and getattr(event, "widget", None) is not self.window:
            return
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                self.window.unbind_all(sequence)
            except (tk.TclError, AttributeError):
                pass

    def _set_status(self, text: str, role: str = "on_surface_variant") -> None:
        """Статусная строка окна (прогресс и результаты операций)."""
        try:
            self.status_label.config(text=text, fg=theme.color(role))
        except tk.TclError:
            pass

    def _refresh_action_states(self) -> None:
        """Доступность кнопок: операция в работе, наличие движка и аккаунта."""
        try:
            installed = self.manager.is_installed()
            account = self.manager.account_exists()
        except Exception as e:
            get_error_logger().error("Состояние WARP: %s", e, exc_info=True)
            installed = False
            account = False

        busy = self._busy
        states = {
            self.install_button: not busy,
            self.register_button: (not busy) and installed and self.tos_checkbox.get(),
            self.delete_account_button: (not busy) and account,
            self.save_button: not busy,
            self.check_ip_button: not busy,
            self.refresh_diag_button: not busy,
            self.journal_button: not busy,
        }
        for button, enabled in states.items():
            try:
                button.config(state=tk.NORMAL if enabled else tk.DISABLED)
            except tk.TclError:
                pass
        try:
            self.tos_checkbox.set_enabled(not busy)
        except tk.TclError:
            pass

        try:
            self.install_button.config(
                text="Обновить движок" if installed else "Установить движок"
            )
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool, status: str = "") -> None:
        """Блокирует кнопки на время длительной операции."""
        self._busy = bool(busy)
        try:
            if busy:
                self._set_status(status or "Операция выполняется…")
                self.progress.start()
            else:
                self.progress.stop()
                self.progress.set(0)
        except tk.TclError:
            pass
        self._refresh_action_states()

    def _start_task(self, status: str, worker, on_result) -> bool:
        """Запускает длительную операцию в фоне; интерфейс не блокируется."""
        if self._busy:
            self._set_status("Дождитесь завершения текущей операции", role="warning")
            return False
        self._set_busy(True, status)

        def run():
            result = None
            error = None
            try:
                result = worker()
            except Exception as e:  # noqa: BLE001 - ошибку показываем пользователю
                error = e
                get_error_logger().error("WARP: %s", e, exc_info=True)
            self._safe_after(lambda: self._finish_task(result, error, on_result))

        threading.Thread(target=run, daemon=True).start()
        return True

    def _finish_task(self, result, error, on_result) -> None:
        self._set_busy(False)
        if error is not None:
            self._operation_failed("Операция WARP не удалась", str(error))
            return
        if on_result is None:
            return
        try:
            on_result(result)
        except Exception as e:  # noqa: BLE001 - ошибку показываем пользователю
            get_error_logger().error("WARP: %s", e, exc_info=True)
            self._operation_failed("Операция WARP не удалась", str(e))

    def _operation_failed(self, title: str, message: str) -> None:
        """Показывает ошибку диалогом, полный текст уходит в error.log."""
        text = (message or "").strip()
        get_error_logger().error("%s: %s", title, text or "(пустое сообщение)")
        self._set_status(title, role="error")
        show_message(
            self.window,
            title,
            "Операция завершилась ошибкой.",
            kind="error",
            detail=_clip(text),
        )

    # -- прогресс -----------------------------------------------------------

    def _on_progress(self, message: str, percent=None) -> None:
        """Прогресс установки (вызывается WarpManager из фонового потока)."""
        self._safe_after(lambda: self._apply_progress(message, percent))

    def _apply_progress(self, message: str, percent) -> None:
        self._set_status(message or "Операция выполняется…")
        if percent is None:
            return
        try:
            self.progress.stop()
            self.progress.set(percent)
        except tk.TclError:
            pass

    # -- данные окна --------------------------------------------------------

    def refresh_all(self) -> None:
        """Первичное заполнение окна."""
        self.refresh_versions()
        self.refresh_account()
        self.refresh_diagnostics()
        self._refresh_action_states()

    def refresh_versions(self) -> None:
        """Установленная версия usque и последняя версия из GitHub (в фоне)."""
        try:
            installed_version = self.manager.installed_version()
            installed = self.manager.is_installed()
        except Exception as e:
            get_error_logger().error("Версия usque: %s", e, exc_info=True)
            installed_version, installed = None, False

        if installed_version:
            text = f"Установленная версия: {installed_version}"
        elif installed:
            text = "Движок установлен (версия неизвестна)"
        else:
            text = "Движок usque не установлен"
        try:
            self.engine_installed_label.config(text=text)
        except tk.TclError:
            pass

        self._refresh_action_states()
        self._load_upstream_version()

    def _load_upstream_version(self) -> None:
        """Последняя версия usque — сетевой запрос, поэтому в фоне."""

        def run():
            manager = WarpManager()
            try:
                version = manager.upstream_version()
                error = manager.release_error
            except Exception as e:  # noqa: BLE001 - показываем причину в окне
                get_error_logger().error("Релиз usque: %s", e, exc_info=True)
                version, error = None, str(e)
            self._safe_after(lambda: self._apply_upstream_version(version, error))

        threading.Thread(target=run, daemon=True).start()

    def _apply_upstream_version(self, version, error) -> None:
        if version:
            text = f"Последняя версия: {version}"
        elif error:
            text = f"Последняя версия: не удалось получить — {error}"
        else:
            text = "Последняя версия: не удалось определить"
        try:
            self.engine_upstream_label.config(text=text)
        except tk.TclError:
            pass

    def refresh_account(self) -> None:
        """Аккаунт зарегистрирован или нет."""
        try:
            exists = self.manager.account_exists()
        except Exception as e:
            get_error_logger().error("Аккаунт WARP: %s", e, exc_info=True)
            exists = False
        try:
            self.account_label.config(
                text="Аккаунт зарегистрирован" if exists else "Аккаунт не зарегистрирован",
                fg=theme.color("on_surface") if exists else theme.color("on_surface_variant"),
            )
        except tk.TclError:
            pass
        self._refresh_action_states()

    def refresh_diagnostics(self) -> None:
        """Диагностика: служба, туннель, транспорт, endpoint, интерфейс."""
        try:
            manager = self.manager
            status = manager.status()
            autostart = manager.autostart_enabled()
            installed = manager.is_installed()
            connected = WarpManager.tunnel_connected()
            transport = WarpManager.active_transport()
            endpoint = WarpManager.active_endpoint()
            state = read_state()
        except Exception as e:
            get_error_logger().error("Диагностика WARP: %s", e, exc_info=True)
            self._set_status(f"Диагностика недоступна: {e}", role="error")
            return

        lines = (
            f"Движок usque: {'установлен' if installed else 'не установлен'}",
            f"Служба warp.service: {SERVICE_STATES.get(status, status)}",
            f"Автозапуск: {'включён' if autostart else 'выключен'}",
            f"Туннель: {'подключён' if connected else 'не подключён'}",
            f"Транспорт: {transport_label(transport)}",
            f"Endpoint: {endpoint or '—'}",
            f"Интерфейс: {state.get('iface')} · режим: полный туннель · "
            f"DNS: {state.get('dns')} · help_tunnel: {state.get('help_tunnel')}",
        )
        try:
            self.diag_label.config(text="\n".join(lines))
        except tk.TclError:
            pass

    # -- операции -----------------------------------------------------------

    def install_engine(self) -> None:
        """Устанавливает или обновляет движок usque (в фоне)."""
        self._start_task("Установка движка usque…", self.manager.install_usque, self._after_install)

    def _after_install(self, result) -> None:
        ok, message = _as_result(result)
        self.refresh_versions()
        if ok:
            self._set_status(message, role="success")
            show_message(self.window, "Движок usque", message, kind="success")
        else:
            self._operation_failed("Не удалось установить движок usque", message)

    def _on_tos_changed(self, _checked: bool) -> None:
        """Галочка согласия управляет доступностью регистрации."""
        self._refresh_action_states()

    def _open_tos(self) -> None:
        """Открывает условия Cloudflare WARP в браузере."""
        try:
            webbrowser.open(TOS_URL)
        except Exception as e:  # noqa: BLE001 - показываем причину пользователю
            get_error_logger().error("Открытие условий Cloudflare: %s", e, exc_info=True)
            self._set_status(f"Не удалось открыть условия: {e}", role="warning")

    def register_account(self) -> None:
        """Регистрирует аккаунт WARP: обязательна галочка согласия."""
        accepted = bool(self.tos_checkbox.get())
        if not accepted:
            self._set_status("Регистрация без согласия с условиями Cloudflare WARP невозможна", role="warning")
            show_message(
                self.window,
                "Нужно согласие",
                "Регистрация аккаунта WARP доступна только после подтверждения согласия "
                "с условиями Cloudflare WARP.",
                kind="warning",
            )
            return
        self._start_task(
            "Регистрация аккаунта WARP…",
            lambda: self.manager.register_account(accepted),
            self._after_account_operation,
        )

    def delete_account(self) -> None:
        """Удаляет аккаунт WARP после подтверждения."""
        try:
            exists = self.manager.account_exists()
        except Exception:
            exists = False
        if not exists:
            self._set_status("Аккаунт WARP не зарегистрирован", role="warning")
            return
        confirmed = ask_yes_no(
            self.window,
            "Удалить аккаунт WARP?",
            "Файл /opt/warp/account.json будет удалён: туннель перестанет подниматься, "
            "пока аккаунт не зарегистрируют снова.",
            kind="warning",
            yes_text="Удалить",
            no_text="Отмена",
        )
        if not confirmed:
            return
        self._start_task(
            "Удаление аккаунта WARP…", self.manager.delete_account, self._after_account_operation
        )

    def _after_account_operation(self, result) -> None:
        ok, message = _as_result(result)
        self.refresh_account()
        if ok:
            self._set_status(message, role="success")
            show_message(self.window, "Аккаунт WARP", message, kind="success")
        else:
            self._operation_failed("Операция с аккаунтом WARP не удалась", message)

    def save_settings(self) -> None:
        """Сохраняет настройки в utils/warp.txt и правила zapret для туннеля."""
        state = read_state()
        state["mode"] = "full"
        state["transport"] = self._value_for(
            TRANSPORT_CHOICES, self.transport_dropdown.get(), "auto"
        )
        state["sni"] = (self.sni_field.get() or "").strip() or DEFAULT_SNI
        state["mtu"] = (self.mtu_field.get() or "").strip()
        state["dns"] = self._value_for(DNS_CHOICES, self.dns_dropdown.get(), "link")
        state["help_tunnel"] = "1" if self.help_switch.get() else "0"

        problems = validate_state(state)
        if problems:
            self._set_status("Настройки не сохранены: " + "; ".join(problems), role="error")
            show_message(
                self.window,
                "Проверьте настройки",
                "Значения не сохранены.",
                kind="error",
                detail="; ".join(problems),
            )
            return

        try:
            ok, message = self.manager.apply_settings(state)
        except ValueError as e:
            ok, message = False, str(e)
        except OSError as e:
            ok, message = False, f"не удалось записать utils/warp.txt: {e}"

        if ok:
            self._set_status(message, role="success")
            self.refresh_diagnostics()
            # Новые правила для endpoint'ов читаются starter.sh при старте,
            # поэтому уже запущенный zapret надо перезапустить.
            self._restart_zapret_after_settings()
        else:
            self._operation_failed("Не удалось сохранить настройки WARP", message)

    def _restart_zapret_after_settings(self) -> None:
        """Перезапускает zapret, если он активен (в фоне)."""
        self._start_task(
            "Перезапуск zapret с новыми правилами WARP…",
            self.manager.restart_zapret_if_active,
            self._after_zapret_restart,
        )

    def _after_zapret_restart(self, result) -> None:
        ok, message = _as_result(result)
        if ok:
            self._set_status(f"Настройки сохранены · {message}", role="success")
        else:
            self._operation_failed("Настройки сохранены, но zapret не перезапустился", message)

    def check_external_ip(self) -> None:
        """Внешний адрес и warp=on/off из Cloudflare trace (в фоне)."""
        self._start_task(
            "Запрос cloudflare.com/cdn-cgi/trace…",
            WarpManager.trace_info,
            self._on_trace_result,
        )

    def _on_trace_result(self, data) -> None:
        if not isinstance(data, dict) or not data:
            self._set_status("Cloudflare trace недоступен: нет связи или туннель не поднят", role="warning")
            try:
                self.trace_label.config(
                    text="Внешний адрес: не удалось получить данные Cloudflare trace",
                    fg=theme.color("warning"),
                )
            except tk.TclError:
                pass
            return

        ip = data.get("ip") or "—"
        warp = (data.get("warp") or "off").strip()
        loc = data.get("loc") or "—"
        colo = data.get("colo") or ""
        text = f"Внешний адрес: {ip} · warp={warp} · loc={loc}"
        if colo:
            text += f" · colo={colo}"
        try:
            self.trace_label.config(
                text=text, fg=theme.color("success" if warp == "on" else "warning")
            )
        except tk.TclError:
            pass
        self._set_status(
            "Туннель WARP активен (warp=on)" if warp == "on" else "warp=off: трафик идёт мимо туннеля",
            role="success" if warp == "on" else "warning",
        )

    def load_journal(self) -> None:
        """Читает журнал warp.service и теги warp/warp-usque (в фоне)."""
        self._start_task("Чтение журнала warp…", self._read_journal, self._on_journal_loaded)

    def _read_journal(self) -> str:
        """journalctl по службе и тегам через WarpManager."""
        return self.manager.journal_tail(lines=self.JOURNAL_LINES)

    def _on_journal_loaded(self, text) -> None:
        self._set_status("Журнал обновлён", role="success")
        try:
            self.journal_text.config(state=tk.NORMAL)
            self.journal_text.delete("1.0", tk.END)
            self.journal_text.insert(tk.END, text or "(журнал пуст)")
            self.journal_text.see(tk.END)
            self.journal_text.config(state=tk.DISABLED)
        except tk.TclError:
            pass
