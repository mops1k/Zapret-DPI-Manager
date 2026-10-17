"""Главное окно: карточка WARP (usque) — статус, включение и окно настроек.

Ядро WARP живёт в :mod:`core.warp_manager`: карточка только читает состояние
(``utils/warp.txt`` и ``systemctl``) и выполняет согласованную последовательность
команд включения/выключения. Все длительные операции идут в фоновом потоке,
интерфейс при этом не блокируется.
"""
from __future__ import annotations

import threading
import tkinter as tk

from core.app_logging import get_error_logger
from core.service_manager import ServiceManager
from core.warp_manager import WarpManager, read_state
from ui.components.material import (
    MaterialCard,
    MaterialSwitch,
    ask_yes_no,
    outlined_button,
    show_message,
)
from ui.theme import theme

#: Значение /run/warp/transport -> подпись для человека.
TRANSPORT_LABELS = {
    "quic": "QUIC",
    "http2": "HTTP/2",
    "h2": "HTTP/2",
    "h3": "HTTP/3",
}

#: Хвост journalctl в диалоге обрезается: в message приходит до 8000 символов.
ERROR_DETAIL_LIMIT = 1800


def transport_label(value: str | None) -> str:
    """Подпись транспорта туннеля: ``quic`` -> QUIC, ``http2`` -> HTTP/2."""
    text = (value or "").strip()
    if not text:
        return "—"
    return TRANSPORT_LABELS.get(text.lower(), text)


def clip_message(message: str, limit: int = ERROR_DETAIL_LIMIT) -> str:
    """Обрезает длинный текст ошибки для диалога."""
    text = (message or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… текст усечён, полный вывод — в журнале WARP и error.log"


class MainWarpMixin:
    """Карточка WARP в главном окне (рядом с блоком службы zapret)."""

    WARP_POLL_MS = 5000

    # -- состояние ----------------------------------------------------------

    @property
    def warp_manager(self) -> WarpManager:
        """Ленивый WarpManager: карточка создаётся без сетевых запросов."""
        manager = getattr(self, "_warp_manager_instance", None)
        if manager is None:
            manager = WarpManager()
            self._warp_manager_instance = manager
        return manager

    def _warp_widgets_alive(self) -> bool:
        """Виджеты карточки могут быть уже уничтожены при rebuild_ui()."""
        label = getattr(self, "warp_state_label", None)
        try:
            return bool(label) and label.winfo_exists()
        except (tk.TclError, AttributeError):
            return False

    # -- разметка -----------------------------------------------------------

    def build_warp_card(self, parent, t=None) -> None:
        """Собирает карточку WARP (вызывается из setup_ui главного окна)."""
        t = t or theme
        card_bg = "surface_container_low"

        self.warp_card = MaterialCard(
            parent,
            variant="elevated",
            title="WARP (usque)",
            subtitle="Туннель Cloudflare WARP: движок usque и служба warp.service",
        )
        self.warp_card.pack(fill=tk.X, pady=(0, t.space("md")))

        self.warp_state_label = tk.Label(
            self.warp_card.content,
            text="Проверка состояния…",
            anchor="w",
            justify="left",
            wraplength=t.px(520),
            **theme.text("body_large", bg_role=card_bg),
        )
        self.warp_state_label.pack(fill=tk.X)

        self.warp_detail_label = tk.Label(
            self.warp_card.content,
            text="",
            anchor="w",
            justify="left",
            wraplength=t.px(520),
            **theme.text("body_small", bg_role=card_bg, fg_role="on_surface_variant"),
        )
        self.warp_detail_label.pack(fill=tk.X, pady=(t.px(2), 0))

        row = tk.Frame(self.warp_card.content, bg=t.color(card_bg))
        row.pack(fill=tk.X, pady=(t.space("md"), 0))

        self.warp_switch = MaterialSwitch(
            row,
            "WARP включён",
            value=False,
            command=self.on_warp_switch,
            bg_role=card_bg,
        )
        self.warp_switch.pack(side=tk.LEFT)

        self.warp_settings_button = outlined_button(
            row,
            "Настроить",
            self.open_warp_settings,
            parent_role=card_bg,
        )
        self.warp_settings_button.pack(side=tk.RIGHT)

        if not getattr(self, "_warp_polling_started", False):
            self._warp_polling_started = True
            self._safe_after(self.check_warp_status, 50)
            self._safe_after(self.schedule_warp_status_update, self.WARP_POLL_MS)

    # -- статус -------------------------------------------------------------

    def check_warp_status(self) -> None:
        """Обновляет карточку WARP: не установлен / остановлен / активен."""
        if not self._warp_widgets_alive():
            return
        try:
            manager = self.warp_manager
            installed = manager.is_installed()
            status = manager.status()
            state = read_state()
        except Exception as e:
            get_error_logger().error("Статус WARP: %s", e, exc_info=True)
            self._set_warp_card_text(
                "Статус WARP неизвестен",
                f"Не удалось прочитать состояние: {e}",
                role="warning",
            )
            return

        iface = state.get("iface") or "—"
        mode_label = "полный туннель" if state.get("mode") != "select" else "только выбранные списки"
        if status == "active":
            transport = transport_label(WarpManager.active_transport())
            endpoint = WarpManager.active_endpoint() or "—"
            probe_failure = WarpManager.tunnel_probe_failure()
            if WarpManager.tunnel_connected():
                self._set_warp_card_text(
                    f"WARP активен · {transport} · {iface}",
                    f"Туннель: подключён · endpoint: {endpoint} · режим: {mode_label}",
                    role="success",
                )
            elif probe_failure:
                self._set_warp_card_text(
                    "WARP подключился, но данные не проходят",
                    "Самопроверка не прошла: маршруты сняты автоматически, интернет идёт напрямую. "
                    "Попробуйте другой транспорт или включите «лечить внешний трафик туннеля через zapret».",
                    role="error",
                )
            else:
                self._set_warp_card_text(
                    f"WARP активен · {transport} · {iface}",
                    "Туннель: данные не подтверждены — служба активна, но подключение не состоялось. "
                    "Загляните в журнал в окне WARP.",
                    role="warning",
                )
            active = True
        elif installed:
            probe_failure = WarpManager.tunnel_probe_failure()
            if probe_failure:
                detail = (
                    "Последняя попытка: соединение устанавливалось, но данные не проходили — "
                    "маршруты сняты автоматически, интернет идёт напрямую. Попробуйте другой "
                    "транспорт или включите «лечить внешний трафик туннеля через zapret»."
                )
            else:
                detail = f"Туннель: не подключён · интерфейс: {iface} · режим: {mode_label}"
            self._set_warp_card_text("WARP остановлен", detail, role="error")
            active = False
        else:
            self._set_warp_card_text(
                "WARP не установлен",
                "Движок usque не установлен: откройте «Настроить» и установите движок.",
                role="on_surface_variant",
            )
            active = False

        if not getattr(self, "_warp_busy", False):
            try:
                self.warp_switch.set(active)
                self.warp_switch.set_enabled(installed)
            except (tk.TclError, AttributeError):
                pass

    def _set_warp_card_text(self, text: str, detail: str, role: str = "on_surface") -> None:
        """Красит статус карточки токенами темы (как у карточки службы zapret)."""
        color = theme.color(role)
        try:
            self.warp_state_label.config(
                text=text,
                fg=theme.color("on_surface") if role == "success" else color,
            )
            self.warp_detail_label.config(text=detail)
        except tk.TclError:
            pass

    def schedule_warp_status_update(self) -> None:
        """Периодически обновляет карточку WARP (5 с, как у службы zapret)."""
        try:
            self.check_warp_status()
        except Exception as e:
            get_error_logger().error("Обновление статуса WARP: %s", e, exc_info=True)
        finally:
            self._safe_after(self.schedule_warp_status_update, self.WARP_POLL_MS)

    # -- включение и выключение --------------------------------------------

    def on_warp_switch(self, value: bool) -> None:
        """Переключатель карточки: включает или выключает WARP."""
        if getattr(self, "_warp_busy", False) or not self._warp_widgets_alive():
            return
        if not self.ensure_sudo_password():
            try:
                self.warp_switch.set(not value)
            except (tk.TclError, AttributeError):
                pass
            return

        start_zapret = self._warp_offer_zapret() if value else False

        self._warp_busy = True
        try:
            self.warp_switch.set_enabled(False)
            self.warp_settings_button.config(state=tk.DISABLED)
        except (tk.TclError, AttributeError):
            pass

        self.show_status_message("Включение WARP…" if value else "Выключение WARP…")
        thread = threading.Thread(
            target=self._warp_toggle_thread,
            args=(bool(value), start_zapret),
            daemon=True,
        )
        thread.start()

    def _warp_offer_zapret(self) -> bool:
        """Предлагает включить zapret перед подъёмом туннеля.

        Проверено на канале с блокировкой MASQUE: без активного zapret туннель
        подключается, но данные через него не идут, и самопроверка снимает
        маршруты. Поэтому при выключенном zapret предлагаем его включить.
        """
        if str(read_state().get("help_tunnel")) != "1":
            return False
        try:
            if ServiceManager().get_service_status() == "active":
                return False
        except Exception as e:
            get_error_logger().error("Статус zapret перед включением WARP: %s", e, exc_info=True)
            return False
        return bool(
            ask_yes_no(
                getattr(self, "root", None),
                "Включить zapret вместе с WARP?",
                "«Лечение внешнего трафика туннеля» работает только при активном zapret: "
                "иначе WARP может подключиться, но данные через туннель не пойдут и маршруты "
                "будут сняты автоматически.",
                kind="warning",
                yes_text="Включить zapret",
                no_text="Только WARP",
            )
        )

    def _warp_toggle_thread(self, enable: bool, start_zapret: bool = False) -> None:
        try:
            if enable:
                ok, message = self._warp_enable(start_zapret)
            else:
                ok, message = self._warp_disable()
        except Exception as e:
            get_error_logger().error("Переключение WARP: %s", e, exc_info=True)
            ok, message = False, str(e)
        self._safe_after(lambda: self._finish_warp_toggle(enable, ok, message))

    def _finish_warp_toggle(self, enable: bool, ok: bool, message: str) -> None:
        self._warp_busy = False
        try:
            self.warp_settings_button.config(state=tk.NORMAL)
        except (tk.TclError, AttributeError):
            pass

        action = "включён" if enable else "выключен"
        if ok:
            self.show_status_message(f"WARP {action}", success=True)
        else:
            self.show_status_message(
                f"Не удалось изменить состояние WARP: {clip_message(message, 160)}",
                error=True,
            )
            try:
                parent = self.root
            except AttributeError:
                parent = None
            show_message(
                parent,
                "Ошибка WARP",
                f"WARP не {action}: операция завершилась ошибкой.",
                kind="error",
                detail=clip_message(message),
            )
        self.check_warp_status()

    def _warp_enable(self, start_zapret: bool = False) -> tuple[bool, str]:
        """Включение: состояние -> скрипты -> zapret -> автозапуск -> старт.

        zapret приводится в рабочее состояние ДО подъёма туннеля: иначе первые
        пакеты к endpoint'ам успели бы попасть под правила стратегии пользователя
        и сломать рукопожатие MASQUE, а без активного zapret данные через туннель
        на каналах с блокировкой MASQUE вообще не идут.
        """
        manager = self.warp_manager
        if not manager.is_installed():
            return False, (
                "движок usque не установлен: откройте окно WARP («Настроить»), "
                "установите движок и включите WARP снова"
            )

        state = read_state()
        state["enabled"] = "1"

        ok, message = manager.apply_settings(state)
        if not ok:
            return False, message

        if not manager.unit_exists() or manager.runtime_outdated():
            # Юнит ставится один раз, но сами скрипты после обновления приложения
            # в /opt/warp не попадают: обновляем их перед запуском туннеля.
            ok, message = manager.install_runtime()
            if not ok:
                return False, message

        if start_zapret:
            ok, message = ServiceManager().start_service()
            if not ok:
                return False, f"не удалось запустить zapret с правилами WARP: {message}"
        else:
            ok, message = manager.restart_zapret_if_active()
            if not ok:
                return False, f"не удалось перезапустить zapret с правилами WARP: {message}"

        ok, message = manager.enable()
        if not ok:
            return False, f"не удалось включить автозапуск warp.service: {message}"

        # Юнит типа oneshot+RemainAfterExit может остаться в состоянии active
        # (exited) без живого туннеля: тогда `systemctl start` ничего не делает.
        if manager.status() == "active":
            ok, message = manager.restart()
        else:
            ok, message = manager.start()
        if not ok:
            return False, message

        return True, "WARP включён"

    def _warp_disable(self) -> tuple[bool, str]:
        """Выключение: состояние -> остановка -> снятие автозапуска -> zapret."""
        manager = self.warp_manager
        state = read_state()
        state["enabled"] = "0"

        ok, message = manager.apply_settings(state)
        if not ok:
            return False, message

        ok, message = manager.stop()
        if not ok:
            return False, message

        ok, message = manager.disable()
        if not ok:
            return False, f"служба остановлена, но автозапуск не отключился: {message}"

        ok, message = manager.restart_zapret_if_active()
        if not ok:
            return False, f"WARP остановлен, но zapret не перезапустился: {message}"

        return True, "WARP выключен"

    # -- окно настроек ------------------------------------------------------

    def open_warp_settings(self) -> None:
        """Открывает окно WARP: движок, аккаунт, настройки и диагностика."""
        try:
            self.close_settings_menu()
        except Exception:
            pass

        from ui.windows.warp_window import WarpWindow

        window = WarpWindow(self.root)
        window.run()
        self.check_warp_status()
