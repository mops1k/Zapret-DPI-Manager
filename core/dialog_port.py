# -*- coding: utf-8 -*-
"""Порт диалогов: ядро не зависит от GUI-библиотеки.

Модули ``core/`` показывают сообщения и прогресс только через этот порт.
Реализацию регистрирует интерфейс: Tk-адаптер живёт в ``ui/dialogs_tk.py``,
Qt-адаптер — в ``qmlui`` (или консольный по умолчанию, если GUI не нужен).

Проект «Zapret DPI Manager» © Aleksandr Kvintilyanov.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ProgressHandle(Protocol):
    """Дескриптор окна/панели прогресса."""

    def update(self, text: str, percent: Optional[float] = None) -> None:
        """Обновляет текст задачи и, если задан, процент."""

    def close(self) -> None:
        """Закрывает окно прогресса."""


@runtime_checkable
class DialogPort(Protocol):
    """Диалоги, которые ядро просит у интерфейса."""

    def show_info(self, title: str, message: str) -> None:
        """Информационное сообщение."""

    def ask_yes_no(self, title: str, message: str) -> bool:
        """Вопрос «Да/Нет»."""

    def ask_yes_no_cancel(self, title: str, message: str) -> Optional[bool]:
        """Вопрос «Да/Нет/Отмена» (None — отмена)."""

    def open_progress(self, title: str, initial: float = 0) -> Optional[ProgressHandle]:
        """Открывает окно прогресса; None — прогресс показывать негде."""


class _NullProgress:
    """Заглушка прогресса: ничего не показывает."""

    def update(self, text: str, percent: Optional[float] = None) -> None:
        pass

    def close(self) -> None:
        pass


class ConsoleDialogPort:
    """Порт для не-GUI режима: печатает в stdout, вопросы считаются отказом."""

    def show_info(self, title: str, message: str) -> None:
        print(f"{title}: {message}", flush=True)

    def ask_yes_no(self, title: str, message: str) -> bool:
        print(f"{title}: {message} → нет (неинтерактивный режим)", flush=True)
        return False

    def ask_yes_no_cancel(self, title: str, message: str) -> Optional[bool]:
        print(f"{title}: {message} → отмена (неинтерактивный режим)", flush=True)
        return None

    def open_progress(self, title: str, initial: float = 0) -> Optional[ProgressHandle]:
        print(f"{title}…", flush=True)
        return _NullProgress()


_port: DialogPort = ConsoleDialogPort()


def bind_dialog_port(port: DialogPort) -> None:
    """Регистрирует реализацию порта (вызывает интерфейс при старте)."""
    global _port
    _port = port


def reset_dialog_port() -> None:
    """Возвращает консольную реализацию (для тестов)."""
    global _port
    _port = ConsoleDialogPort()


def dialog_port() -> DialogPort:
    """Текущий порт диалогов."""
    return _port
