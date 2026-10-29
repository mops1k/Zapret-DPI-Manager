# -*- coding: utf-8 -*-
"""Тема Material 3 для Qt Quick.

Токены (цветовые схемы, типографика, отступы, радиусы) берутся из общего
файла ``qmlui/tokens.py`` (раньше он жил в ui/theme, загрузчик ниже
уже ищет его первым).

В QML объект доступен как контекстное свойство ``Theme``::

    color: Theme.colors.surface
    radius: Theme.radii.lg
    font.pixelSize: Theme.typeSize("body_medium")
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from PySide6.QtCore import Property, QObject, Signal, Slot

#: Корень проекта (каталог с main.py): qmlui/theme.py -> parents[1].
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_DIR = PROJECT_ROOT / "utils"
SETTINGS_FILE = SETTINGS_DIR / "theme.txt"

VALID_MODES = ("dark", "light")
DEFAULT_MODE = "dark"

_TOKEN_CANDIDATES = (
    Path(__file__).with_name("tokens.py"),
    PROJECT_ROOT / "qmlui" / "tokens.py",
)


def _load_tokens() -> ModuleType:
    """Загружает модуль токенов по пути, не импортируя Tk-пакет ui.theme."""
    for path in _TOKEN_CANDIDATES:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("zapret_theme_tokens", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        # Модуль обязан быть в sys.modules до exec: dataclass ищет себя там.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    raise ImportError(
        "не найден файл токенов темы: ожидался qmlui/tokens.py"
    )


tokens = _load_tokens()


class QtTheme(QObject):
    """Цвета, отступы, радиусы и типографика для QML."""

    #: Один сигнал на все карты: при смене режима QML пересчитывает привязки.
    changed = Signal()
    modeChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mode = DEFAULT_MODE
        self._colors: dict[str, str] = {}
        self.load()

    # -- режим --------------------------------------------------------------

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        return self._mode

    @Property(bool, notify=modeChanged)
    def dark(self) -> bool:
        return self._mode == "dark"

    def load(self) -> str:
        """Читает режим из utils/theme.txt (или из ZAPRET_THEME)."""
        env_mode = os.environ.get("ZAPRET_THEME", "").strip().lower()
        if env_mode in VALID_MODES:
            self._mode = env_mode
        else:
            try:
                raw = SETTINGS_FILE.read_text(encoding="utf-8").strip().lower()
            except OSError:
                raw = ""
            self._mode = raw if raw in VALID_MODES else DEFAULT_MODE
        self._rebuild()
        return self._mode

    def save(self) -> bool:
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(self._mode + "\n", encoding="utf-8")
            return True
        except OSError:
            return False

    def _rebuild(self) -> None:
        scheme = tokens.LIGHT_SCHEME if self._mode == "light" else tokens.DARK_SCHEME
        # Пустая строка в схеме означает «прозрачный»: в QML это "transparent".
        self._colors = {
            role: (value or "transparent") for role, value in scheme.items()
        }

    def _set_mode(self, mode: str) -> bool:
        mode = (mode or "").strip().lower()
        if mode not in VALID_MODES or mode == self._mode:
            return False
        self._mode = mode
        self._rebuild()
        self.save()
        self.modeChanged.emit()
        self.changed.emit()
        return True

    @Slot(str)
    def setMode(self, mode: str) -> None:
        self._set_mode(mode)

    @Slot()
    def toggleMode(self) -> None:
        self._set_mode("light" if self._mode == "dark" else "dark")

    # -- карты токенов ------------------------------------------------------

    @Property("QVariantMap", notify=changed)
    def colors(self) -> dict[str, str]:
        return self._colors

    @Property("QVariantMap", constant=True)
    def spaces(self) -> dict[str, int]:
        return dict(tokens.SPACING)

    @Property("QVariantMap", constant=True)
    def radii(self) -> dict[str, int]:
        return dict(tokens.RADII)

    @Property("QVariantMap", constant=True)
    def durations(self) -> dict[str, int]:
        return dict(tokens.DURATION)

    @Property("QVariantMap", constant=True)
    def stateOpacity(self) -> dict[str, float]:
        return dict(tokens.STATE_OPACITY)

    @Property("QVariantMap", constant=True)
    def typeScale(self) -> dict[str, dict[str, int | bool]]:
        bold = set(getattr(tokens, "BOLD_ROLES", set()))
        return {
            role: {"size": int(size), "line": int(line), "bold": role in bold}
            for role, (size, line) in tokens.TYPE_SCALE.items()
        }

    # -- удобные вызовы -----------------------------------------------------

    @Slot(str, result=str)
    def color(self, role: str) -> str:
        return self._colors.get(role, self._colors.get("surface", "#000000"))

    @Slot(str, result=int)
    def space(self, step: str = "md") -> int:
        return int(tokens.SPACING.get(step, tokens.SPACING["md"]))

    @Slot(str, result=int)
    def radius(self, name: str = "md") -> int:
        return int(tokens.RADII.get(name, tokens.RADII["md"]))

    @Slot(str, result=int)
    def typeSize(self, role: str = "body_medium") -> int:
        size, _line = tokens.TYPE_SCALE.get(
            role, tokens.TYPE_SCALE["body_medium"]
        )
        return int(size)

    @Slot(str, result=int)
    def lineHeight(self, role: str = "body_medium") -> int:
        _size, line = tokens.TYPE_SCALE.get(
            role, tokens.TYPE_SCALE["body_medium"]
        )
        return int(line)


_theme: QtTheme | None = None


def get_qt_theme() -> QtTheme:
    """Синглтон темы: один режим и одни цвета на всё приложение."""
    global _theme
    if _theme is None:
        _theme = QtTheme()
    return _theme
