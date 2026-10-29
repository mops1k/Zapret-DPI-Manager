# -*- coding: utf-8 -*-
"""Токены Material 3: цветовые схемы, типографика, отступы, формы, elevation.

Схемы соответствуют ролям Material Design 3 (primary, surface-container-*,
outline, error) с дополнительными ролями success/warning, которых в M3 нет.
Проект «Zapret DPI Manager» © Aleksandr Kvintilyanov.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

# --- Цветовые схемы ---------------------------------------------------------

DARK_SCHEME: Dict[str, str] = {
    # Primary
    "primary": "#86D1F5",
    "on_primary": "#00344A",
    "primary_container": "#004D69",
    "on_primary_container": "#C4E7FF",
    "primary_fixed_dim": "#86D1F5",
    # Secondary
    "secondary": "#B8C8D4",
    "on_secondary": "#22323C",
    "secondary_container": "#384852",
    "on_secondary_container": "#D4E4F0",
    # Tertiary
    "tertiary": "#C9C2E6",
    "on_tertiary": "#302B4A",
    "tertiary_container": "#474163",
    "on_tertiary_container": "#E5DFFF",
    # Error
    "error": "#FFB4AB",
    "on_error": "#690005",
    "error_container": "#93000A",
    "on_error_container": "#FFDAD6",
    # Success (роль проекта, в M3 отсутствует)
    "success": "#86D992",
    "on_success": "#00390F",
    "success_container": "#00531C",
    "on_success_container": "#A2F6AE",
    # Warning (роль проекта)
    "warning": "#EBC46A",
    "on_warning": "#3F2E00",
    "warning_container": "#5B4300",
    "on_warning_container": "#FFDF9B",
    # Surface
    "surface": "#101418",
    "on_surface": "#E0E3E7",
    "surface_dim": "#101418",
    "surface_bright": "#363A3E",
    "surface_container_lowest": "#0B0F12",
    "surface_container_low": "#181C20",
    "surface_container": "#1C2024",
    "surface_container_high": "#262A2E",
    "surface_container_highest": "#313539",
    "surface_variant": "#40484C",
    "on_surface_variant": "#C0C8CC",
    # Outline
    "outline": "#8A9296",
    "outline_variant": "#40484C",
    # Inverse
    "inverse_surface": "#E0E3E7",
    "inverse_on_surface": "#2D3135",
    "inverse_primary": "#00658A",
    # Прочее
    "scrim": "#000000",
    "shadow": "#000000",
    "transparent": "",
}

LIGHT_SCHEME: Dict[str, str] = {
    # Primary
    "primary": "#00658A",
    "on_primary": "#FFFFFF",
    "primary_container": "#C4E7FF",
    "on_primary_container": "#001E2D",
    "primary_fixed_dim": "#00658A",
    # Secondary
    "secondary": "#4C616C",
    "on_secondary": "#FFFFFF",
    "secondary_container": "#CFE6F3",
    "on_secondary_container": "#071E28",
    # Tertiary
    "tertiary": "#645D80",
    "on_tertiary": "#FFFFFF",
    "tertiary_container": "#E9DFFF",
    "on_tertiary_container": "#1F1A37",
    # Error
    "error": "#BA1A1A",
    "on_error": "#FFFFFF",
    "error_container": "#FFDAD6",
    "on_error_container": "#410002",
    # Success
    "success": "#2E6C3F",
    "on_success": "#FFFFFF",
    "success_container": "#B2F2B8",
    "on_success_container": "#002109",
    # Warning
    "warning": "#7A5900",
    "on_warning": "#FFFFFF",
    "warning_container": "#FFDF9B",
    "on_warning_container": "#261A00",
    # Surface
    "surface": "#F6FAFD",
    "on_surface": "#171C1F",
    "surface_dim": "#D6DBDE",
    "surface_bright": "#F6FAFD",
    "surface_container_lowest": "#FFFFFF",
    "surface_container_low": "#F0F4F7",
    "surface_container": "#EAEEF2",
    "surface_container_high": "#E4E8EC",
    "surface_container_highest": "#DEE3E7",
    "surface_variant": "#DBE3E8",
    "on_surface_variant": "#3F484C",
    # Outline
    "outline": "#6F787C",
    "outline_variant": "#BFC8CC",
    # Inverse
    "inverse_surface": "#2B3135",
    "inverse_on_surface": "#ECF1F5",
    "inverse_primary": "#86D1F5",
    # Прочее
    "scrim": "#000000",
    "shadow": "#000000",
    "transparent": "",
}

SCHEMES: Dict[str, Dict[str, str]] = {"dark": DARK_SCHEME, "light": LIGHT_SCHEME}

#: Пары «роль — контрастный текст на ней» для быстрого доступа.
ON_COLOR: Dict[str, str] = {
    "primary": "on_primary",
    "primary_container": "on_primary_container",
    "secondary": "on_secondary",
    "secondary_container": "on_secondary_container",
    "tertiary": "on_tertiary",
    "tertiary_container": "on_tertiary_container",
    "error": "on_error",
    "error_container": "on_error_container",
    "success": "on_success",
    "success_container": "on_success_container",
    "warning": "on_warning",
    "warning_container": "on_warning_container",
    "surface": "on_surface",
    "surface_container_lowest": "on_surface",
    "surface_container_low": "on_surface",
    "surface_container": "on_surface",
    "surface_container_high": "on_surface",
    "surface_container_highest": "on_surface",
    "inverse_surface": "inverse_on_surface",
    "surface_variant": "on_surface_variant",
}

# --- Типографика (Material 3) ----------------------------------------------

#: роль -> (размер в pt, межстрочный интервал в pt)
TYPE_SCALE: Dict[str, Tuple[int, int]] = {
    "display_large": (36, 44),
    "display_medium": (30, 38),
    "display_small": (26, 34),
    "headline_large": (28, 36),
    "headline_medium": (24, 32),
    "headline_small": (21, 28),
    "title_large": (20, 28),
    "title_medium": (16, 24),
    "title_small": (14, 20),
    "body_large": (15, 24),
    "body_medium": (13, 20),
    "body_small": (12, 16),
    "label_large": (13, 20),
    "label_medium": (12, 16),
    "label_small": (11, 16),
}

#: Роли, которые по умолчанию рисуются полужирным (в Tk это читается лучше).
BOLD_ROLES = {"headline_large", "headline_medium", "headline_small", "title_large"}

#: Синонимы для удобства вызовов.
TYPE_ALIASES: Dict[str, str] = {
    "display": "display_medium",
    "headline": "headline_small",
    "title": "title_medium",
    "body": "body_medium",
    "label": "label_medium",
}

# --- Отступы, формы, движение ----------------------------------------------

#: Шаг сетки отступов в dp (space-1 … space-6).
SPACING: Dict[str, int] = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}

#: Радиусы скруглений в dp.
RADII: Dict[str, int] = {
    "none": 0,
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 28,
    "full": 999,
}

#: Прозрачность state layer (M3).
STATE_OPACITY: Dict[str, float] = {
    "hover": 0.08,
    "focus": 0.10,
    "press": 0.10,
    "selected": 0.12,
    "dragged": 0.16,
    "disabled_content": 0.38,
    "disabled_container": 0.12,
}

#: Длительности анимаций в мс.
DURATION: Dict[str, int] = {"short": 120, "medium": 220, "long": 400}

#: Elevation-уровни M3 -> поверхность (в Tk тень не поддерживается, используем
#: тональные поверхности и границу).
ELEVATION_SURFACE: Dict[int, str] = {
    0: "surface",
    1: "surface_container_low",
    2: "surface_container",
    3: "surface_container_high",
    4: "surface_container_high",
    5: "surface_container_highest",
}


def on_role(role: str) -> str:
    """Возвращает имя контрастной роли для роли фона."""
    return ON_COLOR.get(role, "on_surface")


def scheme_for(mode: str) -> Dict[str, str]:
    """Схема по режиму; неизвестный режим трактуется как тёмный."""
    return SCHEMES.get(mode, DARK_SCHEME)


@dataclass(frozen=True)
class TypeStyle:
    """Готовая типографическая роль: размер, интерлиньяж, вес."""

    role: str
    size: int
    line_height: int
    bold: bool = False
