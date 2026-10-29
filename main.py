#!/usr/bin/env python3
"""Точка входа Zapret DPI Manager (Qt Quick).

Запускает QML-интерфейс. Если PySide6 не установлен, печатает подсказку по
пакетам и выходит: без Qt Quick приложение работать не может.
"""
import os
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

INSTALL_HINT = (
    "Не найден PySide6 (Qt Quick). Установите зависимости:\n"
    "  • Arch/SteamOS: sudo pacman -S pyside6 qt6-declarative\n"
    "  • Debian/Ubuntu: sudo apt install python3-pyside6.qml \\n"
    "      qml6-module-qtquick-controls qml6-module-qtquick-layouts \\n"
    "      qml6-module-qtquick-effects\n"
    "  • Fedora: sudo dnf install python3-pyside6 qt6-qtdeclarative\n"
)


def main() -> None:
    try:
        import PySide6.QtCore  # noqa: F401
        import PySide6.QtQml  # noqa: F401
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        try:
            import subprocess

            subprocess.run(["notify-send", "Zapret DPI Manager", INSTALL_HINT],
                           timeout=5, check=False)
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)

    from qmlui.app import main as run_app

    run_app()


if __name__ == "__main__":
    main()
