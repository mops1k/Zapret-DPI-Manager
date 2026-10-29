"""Настройка записи ошибок приложения в ~/.local/state/zapret_dpi_manager."""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
) / "zapret_dpi_manager"
LOG_FILE_PATH = LOG_DIR / "error.log"
_LOGGER_NAME = "zapret_dpi_manager.error"

_setup_done = False


def get_error_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def setup_error_logging() -> None:
    """Пишет только ERROR+ в файл; перехватывает необработанные исключения.

    Хук Tk-колбэков ставит Tk-адаптер интерфейса (``ui/dialogs_tk.py``),
    поэтому ядро не импортирует tkinter.
    """
    global _setup_done
    if _setup_done:
        return
    _setup_done = True

    log = get_error_logger()
    log.setLevel(logging.ERROR)
    log.propagate = False

    if not log.handlers:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(LOG_DIR, 0o700)
        except OSError:
            pass
        handler = RotatingFileHandler(
            LOG_FILE_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setLevel(logging.ERROR)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        log.addHandler(handler)
        try:
            os.chmod(LOG_FILE_PATH, 0o600)
        except OSError:
            pass

    _orig_sys_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_traceback):
        if exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
            _orig_sys_excepthook(exc_type, exc_value, exc_traceback)
            return
        log.error("Необработанное исключение", exc_info=(exc_type, exc_value, exc_traceback))
        _orig_sys_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _excepthook

    if hasattr(threading, "excepthook"):
        _orig_threading_excepthook = threading.excepthook

        def _threading_excepthook(args):
            log.error(
                "Необработанное исключение в потоке %r",
                getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            _orig_threading_excepthook(args)

        threading.excepthook = _threading_excepthook
