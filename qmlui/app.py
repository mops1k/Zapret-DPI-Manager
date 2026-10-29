# -*- coding: utf-8 -*-
"""Точка входа Qt Quick-версии интерфейса.

Запускает QGuiApplication, публикует в QML объекты ``Theme`` и ``Backend``
и загружает ``qml/main.qml``. Заменяет ``main.py`` после удаления Tkinter.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QML_DIR = PROJECT_ROOT / "qml"


def _prepare_imports() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _pick_font_family() -> str:
    """Первый доступный шрифт из цепочки Material-подобных."""
    from PySide6.QtGui import QFontDatabase

    available = set(QFontDatabase.families())
    for family in ("Inter", "Noto Sans", "Cantarell", "DejaVu Sans", "Liberation Sans"):
        if family in available:
            return family
    return "Sans Serif"


def _acquire_single_instance_lock():
    """Блокировка второго экземпляра (тот же файл, что у Tk-версии)."""
    try:
        import fcntl

        cache_dir = Path(
            os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
        ) / "zapret_dpi_manager"
        cache_dir.mkdir(parents=True, exist_ok=True)
        lock_file = open(cache_dir / "manager.lock", "w", encoding="utf-8")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        return lock_file
    except OSError:
        return None
    except Exception:  # noqa: BLE001 - без блокировки просто продолжаем
        return None


def _warn_already_running() -> None:
    """Сообщает, что менеджер уже запущен (уведомление рабочего стола)."""
    import shutil
    import subprocess

    message = "Zapret DPI Manager уже запущен."
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "Zapret DPI Manager", message],
                check=False,
                timeout=5,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    print(message)


def run() -> int:
    _prepare_imports()

    from core.app_logging import get_error_logger, setup_error_logging

    setup_error_logging()
    log = get_error_logger()

    lock = _acquire_single_instance_lock()
    if lock is None:
        _warn_already_running()
        return 0

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from qmlui.backend import Backend
    from qmlui.theme import get_qt_theme

    QQuickStyle.setStyle("Basic")

    app = QGuiApplication(sys.argv)
    app.setApplicationName("Zapret DPI Manager")
    app.setApplicationDisplayName("Zapret DPI Manager")
    app.setDesktopFileName("Zapret_DPI_Manager")
    app.setOrganizationName("ZapretDPI")

    font = QFont(_pick_font_family(), 10)
    app.setFont(font)

    icon_path = PROJECT_ROOT / "ico" / "zapret.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    theme = get_qt_theme()
    backend = Backend()

    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("Theme", theme)
    context.setContextProperty("Backend", backend)

    qml_file = QML_DIR / "main.qml"
    if not qml_file.is_file():
        log.error("Не найден файл интерфейса: %s", qml_file)
        print(f"Не найден файл интерфейса: {qml_file}", file=sys.stderr)
        return 1

    engine.load(qml_file.as_uri())
    if not engine.rootObjects():
        log.error("QML не загрузился: %s", qml_file)
        print("Ошибка загрузки QML (подробности выше)", file=sys.stderr)
        return 1

    def _cleanup() -> None:
        try:
            backend.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            lock.close()
        except Exception:  # noqa: BLE001
            pass

    app.aboutToQuit.connect(_cleanup)
    QTimer.singleShot(0, backend.refresh)
    QTimer.singleShot(0, backend.refreshWarp)

    if "--page=tester" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openTesterPage", Qt.AutoConnection))

    if "--page=check" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openCheckPage", Qt.AutoConnection))

    if "--page=dns" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openDnsPage", Qt.AutoConnection))

    if "--page=gamefilter" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openGameFilterPage", Qt.AutoConnection))

    if "--page=update" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openUpdatePage", Qt.AutoConnection))

    if "--page=unlock" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openUnlockPage", Qt.AutoConnection))

    if "--page=info" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openInfoPage", Qt.AutoConnection))

    if "--page=report" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openReportPage", Qt.AutoConnection))

    if "--page=ipset" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openIpsetPage", Qt.AutoConnection))

    if "--page=hostlist" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openHostlistPage", Qt.AutoConnection))

    if "--page=warp" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openWarpPage", Qt.AutoConnection))

    if "--page=strategy" in sys.argv:
        from PySide6.QtCore import QMetaObject, Qt

        window = engine.rootObjects()[0]
        QTimer.singleShot(400, lambda: QMetaObject.invokeMethod(
            window, "openStrategyPage", Qt.AutoConnection))

    if "--selftest" in sys.argv:
        return _run_selftest(app, engine, backend, theme, log)

    return app.exec()


def _run_selftest(app, engine, backend, theme, log) -> int:
    """Дымовой тест интерфейса: окно, мост, фокус и меню настроек.

    Запускается как ``qml_main.py --selftest``: проверяет, что окно создано,
    мост отдал состояние службы, работает навигация клавиатурой/геймпадом,
    открывается меню настроек и переключается тема. Возвращает код выхода.
    """
    import math
    import time

    from PySide6.QtCore import QObject, Qt, QTimer
    from PySide6.QtTest import QTest

    window = engine.rootObjects()[0]
    result = {"code": 1}

    def wait(ms: int) -> None:
        """Ожидание с обработкой событий: QTest.qWait держит GIL, а потокам
        моста нужно время, поэтому события крутим вручную."""
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

    def _popup_inside_window(win) -> bool:
        """Меню настроек не должно вылезать за окно (баг: уезжало влево)."""
        popup = win.findChild(QObject, "settingsPopup")
        if popup is None:
            return False
        px, py = popup.property("x"), popup.property("y")
        pw, ph = popup.property("width"), popup.property("height")
        ww, wh = win.property("width"), win.property("height")
        print(f"  [debug] меню: x={px} y={py} {pw}x{ph}, окно {ww}x{wh}", flush=True)
        return (px >= 0 and py >= 0
                and px + pw <= ww + 1 and py + ph <= wh + 1)

    def focus_item():
        return window.activeFocusItem()

    def focus_text() -> str:
        item = focus_item()
        if item is None:
            return ""
        # StrategyRow хранит имя в strategyName, у ComboBox — в displayText.
        return str(item.property("text")
                   or item.property("strategyName")
                   or item.property("displayText")
                   or "")

    def focus_icon() -> str:
        item = focus_item()
        if item is None:
            return ""
        icon = item.property("iconSource")
        # iconSource объявлен как url: PySide6 отдаёт QUrl, а не строку.
        if hasattr(icon, "toString"):
            return icon.toString()
        return str(icon or "")

    def check() -> None:
        print(f"окно: {window.property('width')}x{window.property('height')}", flush=True)
        print(f"служба: {backend.serviceState}, стратегия: {backend.strategyName}", flush=True)

        wait(600)  # первый опрос статуса и WARP идёт в фоновом потоке
        warp = dict(backend.warpInfo)
        print(f"  [debug] WARP: installed={warp.get('installed')} active={warp.get('active')} "
              f"title={warp.get('title')!r}", flush=True)
        checks = [
            ("окно создано", bool(window.property("width"))),
            ("служба прочитана", backend.serviceState != "unknown"),
            ("статус WARP прочитан", bool(warp.get("title"))),
            ("фокус на кнопке службы", "Zapret DPI" in focus_text()),
        ]
        QTest.keyClick(window, Qt.Key_Right)
        checks.append(("навигация вправо", "автозапуск" in focus_text()))
        QTest.keyClick(window, Qt.Key_Down)
        checks.append(("карточка WARP в цепочке", focus_text() == "Настроить"))
        QTest.keyClick(window, Qt.Key_Left)
        checks.append(("кнопка WARP", "WARP" in focus_text()))
        QTest.keyClick(window, Qt.Key_Down)
        checks.append(("навигация вниз", focus_text() == "Сменить"))
        QTest.keyClick(window, Qt.Key_Up)
        checks.append(("навигация вверх", "WARP" in focus_text()))
        QTest.keyClick(window, Qt.Key_Up)
        checks.append(("возврат к службе", "автозапуск" in focus_text()))
        QTest.keyClick(window, Qt.Key_Up)
        checks.append(("выход в панель", focus_icon().endswith("refresh.svg")))
        QTest.keyClick(window, Qt.Key_Right)   # панель: перезапуск -> GameFilter
        QTest.keyClick(window, Qt.Key_Right)   # -> настройки
        checks.append(("фокус на настройках", focus_icon().endswith("settings.svg")))

        QTest.keyClick(window, Qt.Key_Space)
        wait(300)
        checks.append(("меню настроек открыто", focus_text() == "Сменить стратегию"))
        checks.append(("меню внутри окна", _popup_inside_window(window)))
        QTest.keyClick(window, Qt.Key_Down)
        checks.append(("навигация в меню", focus_text() == "Проверка соединения"))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(250)
        checks.append(("меню закрыто", focus_icon().endswith("settings.svg")))

        before = theme.mode
        theme.toggleMode()
        wait(120)
        checks.append(("переключение темы", theme.mode != before))
        theme.setMode(before)

        # Экран выбора стратегии: переход, список, возврат.
        QTest.keyClick(window, Qt.Key_Down)  # настройки -> кнопка службы
        QTest.keyClick(window, Qt.Key_Down)  # -> кнопка WARP
        QTest.keyClick(window, Qt.Key_Down)  # -> «Сменить»
        checks.append(("фокус на «Сменить»", focus_text() == "Сменить"))
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        print(f"  [debug] страниц в стеке: {window.property('pageDepth')}", flush=True)
        backend.refreshStrategies()
        wait(700)
        strategies = list(backend.strategies)
        print(f"  [debug] стратегий из моста: {len(strategies)}", flush=True)
        checks.append(("список стратегий загружен", len(strategies) > 0))
        checks.append(("экран стратегий открыт", focus_icon().endswith("back.svg")))
        # Колонки зависят от ширины окна (в offscreen она другая) — берём у страницы.
        stack_item = window.findChild(QObject, "stack")
        strategy_page = stack_item.property("currentItem") if stack_item is not None else None
        columns = 1
        if strategy_page is not None:
            try:
                columns = int(strategy_page.property("columns") or 1)
            except (TypeError, ValueError):
                columns = 1
        per_column = math.ceil(len(strategies) / max(1, columns)) if strategies else 0
        QTest.keyClick(window, Qt.Key_Down)
        wait(150)
        checks.append(("фокус на первой стратегии",
                       bool(strategies) and focus_text() == strategies[0]["name"]))
        QTest.keyClick(window, Qt.Key_Down)
        wait(120)
        checks.append(("вниз по столбцу",
                       len(strategies) > 1 and focus_text() == strategies[1]["name"]))
        QTest.keyClick(window, Qt.Key_Right)
        wait(120)
        checks.append(("вправо в соседний столбец",
                       1 + per_column < len(strategies)
                       and focus_text() == strategies[1 + per_column]["name"]))
        QTest.keyClick(window, Qt.Key_Left)
        wait(120)
        checks.append(("влево обратно",
                       len(strategies) > 1 and focus_text() == strategies[1]["name"]))
        QTest.keyClick(window, Qt.Key_Up)
        wait(120)
        checks.append(("вверх в начало столбца",
                       bool(strategies) and focus_text() == strategies[0]["name"]))

        # Конструктор своей стратегии: вкладка «Свой пресет».
        QTest.keyClick(window, Qt.Key_Up)      # с первой стратегии -> «Готовые»
        QTest.keyClick(window, Qt.Key_Right)   # -> «Свой пресет»
        QTest.keyClick(window, Qt.Key_Space)
        wait(800)
        checks.append(("конструктор открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 3))
        categories = list(backend.strategyCategories)
        checks.append(("категории конструктора загружены", len(categories) > 0))
        QTest.keyClick(window, Qt.Key_Down)
        wait(200)
        checks.append(("фокус на первой категории", focus_text() == "Не выбирать"))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат к выбору стратегий",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))

        # BB DPI: вкладка → экран → конвертация строки ciadpi.
        QTest.keyClick(window, Qt.Key_Right)   # -> «Готовые»
        QTest.keyClick(window, Qt.Key_Right)   # -> «Свой пресет»
        QTest.keyClick(window, Qt.Key_Right)   # -> «BB DPI»
        QTest.keyClick(window, Qt.Key_Space)
        wait(800)
        checks.append(("экран BB DPI открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 3))
        backend.convertBbdpi("-s1 -f-1 -t8 -S", False)
        wait(1500)
        bbdpi = dict(backend.bbdpiResult)
        print(f"  [debug] BB DPI: ok={bbdpi.get('ok')} "
              f"rules={len(bbdpi.get('rules') or [])} "
              f"text={str(bbdpi.get('text'))[:70]!r}", flush=True)
        checks.append(("строка BB DPI сконвертирована",
                       bool(bbdpi.get("ok")) and len(bbdpi.get("rules") or []) > 0))
        checks.append(("конвертация проходит nfqws --dry-run",
                       not (bbdpi.get("problems") or [])))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с BB DPI", window.property("pageDepth") == 2))

        # Безопасная проверка применения: уже активная стратегия не меняет config.
        names = [item["name"] for item in strategies]
        current = backend.strategyName
        backend.applyStrategy(current if current in names else "НЕТ_ТАКОЙ_СТРАТЕГИИ")
        wait(600)
        expected = "уже активна" if current in names else "не найден"
        checks.append(("применение отклонено корректно", expected in backend.message))

        QTest.keyClick(window, Qt.Key_Escape)
        wait(600)
        checks.append(("возврат на главный экран",
                       focus_text() == "Сменить" and window.property("pageDepth") == 1))

        # Экран WARP: движок, списки, журнал, проверка версии.
        QTest.keyClick(window, Qt.Key_Up)      # «Сменить» -> кнопка WARP
        QTest.keyClick(window, Qt.Key_Right)   # -> «Настроить»
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        checks.append(("экран WARP открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        details = dict(backend.warpDetails)
        checks.append(("данные WARP загружены",
                       bool(details.get("installed_version"))
                       or details.get("installed") is False))
        checks.append(("списки диапазонов прочитаны",
                       len(details.get("lists") or []) > 0))
        backend.loadWarpJournal()
        wait(1200)
        checks.append(("журнал WARP загружен", len(backend.warpJournal) > 0))
        backend.refreshWarpUpstream()
        wait(6000)
        upstream = dict(backend.warpDetails)
        checks.append(("проверка версии usque отработала",
                       bool(upstream.get("upstream_version")
                            or upstream.get("upstream_error"))))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с экрана WARP", window.property("pageDepth") == 1))

        # Hostlist: из меню настроек.
        QTest.keyClick(window, Qt.Key_Up)      # «Сменить» -> кнопка WARP
        QTest.keyClick(window, Qt.Key_Up)      # -> автозапуск
        QTest.keyClick(window, Qt.Key_Up)      # -> панель (перезапуск)
        QTest.keyClick(window, Qt.Key_Right)
        QTest.keyClick(window, Qt.Key_Right)   # -> настройки
        QTest.keyClick(window, Qt.Key_Space)
        wait(300)
        QTest.keyClick(window, Qt.Key_Down)    # «Сменить стратегию» -> «Проверка соединения»
        QTest.keyClick(window, Qt.Key_Down)    # -> «Настройки Hostlist»
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        checks.append(("экран Hostlist открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        hostlist = dict(backend.hostlistState)
        checks.append(("данные Hostlist загружены",
                       len(hostlist.get("serviceNames") or []) == 4
                       and "blocked" in hostlist))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с Hostlist", window.property("pageDepth") == 1))

        # IPSet: из меню настроек.
        QTest.keyClick(window, Qt.Key_Up)      # «Сменить» -> кнопка WARP
        QTest.keyClick(window, Qt.Key_Up)      # -> автозапуск
        QTest.keyClick(window, Qt.Key_Up)      # -> панель
        QTest.keyClick(window, Qt.Key_Right)
        QTest.keyClick(window, Qt.Key_Right)   # -> настройки
        QTest.keyClick(window, Qt.Key_Space)
        wait(300)
        QTest.keyClick(window, Qt.Key_Down)    # «Проверка соединения»
        QTest.keyClick(window, Qt.Key_Down)    # «Настройки Hostlist»
        QTest.keyClick(window, Qt.Key_Down)    # «Настройки IPSet»
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        checks.append(("экран IPSet открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        ipset = dict(backend.ipsetState)
        checks.append(("данные IPSet загружены",
                       "filterMode" in ipset and "blockedCount" in ipset))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с IPSet", window.property("pageDepth") == 1))

        # DNS: из меню настроек.
        QTest.keyClick(window, Qt.Key_Up)      # «Сменить» -> кнопка WARP
        QTest.keyClick(window, Qt.Key_Up)      # -> автозапуск
        QTest.keyClick(window, Qt.Key_Up)      # -> панель
        QTest.keyClick(window, Qt.Key_Right)
        QTest.keyClick(window, Qt.Key_Right)   # -> настройки
        QTest.keyClick(window, Qt.Key_Space)
        wait(300)
        for _ in range(4):                     # до «Настройки DNS»
            QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        checks.append(("экран DNS открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        dns = dict(backend.dnsState)
        checks.append(("данные DNS загружены",
                       "connection" in dns and "servers" in dns))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с DNS", window.property("pageDepth") == 1))

        # GameFilter: кнопка на панели.
        QTest.keyClick(window, Qt.Key_Up)      # «Сменить» -> кнопка WARP
        QTest.keyClick(window, Qt.Key_Up)      # -> автозапуск
        QTest.keyClick(window, Qt.Key_Up)      # -> панель (перезапуск)
        QTest.keyClick(window, Qt.Key_Right)   # -> GameFilter
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        checks.append(("экран GameFilter открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        gamefilter = dict(backend.gameFilterState)
        checks.append(("данные GameFilter загружены",
                       len(gamefilter.get("presets") or []) > 0
                       and "enabled" in gamefilter))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с GameFilter", window.property("pageDepth") == 1))

        # Обновление: из меню настроек (7-й пункт).
        QTest.keyClick(window, Qt.Key_Up)      # «Сменить» -> кнопка WARP
        QTest.keyClick(window, Qt.Key_Up)      # -> автозапуск
        QTest.keyClick(window, Qt.Key_Up)      # -> панель
        QTest.keyClick(window, Qt.Key_Right)
        QTest.keyClick(window, Qt.Key_Right)   # -> настройки
        QTest.keyClick(window, Qt.Key_Space)
        wait(300)
        for _ in range(6):                     # до «Обновить Zapret»
            QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        checks.append(("экран обновления открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        versions = dict(backend.updateVersions)
        checks.append(("версии для обновления получены", bool(versions.get("app"))))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с обновления", window.property("pageDepth") == 1))

        # Разблокировка сервисов: из меню (6-й пункт).
        QTest.keyClick(window, Qt.Key_Up)
        QTest.keyClick(window, Qt.Key_Up)
        QTest.keyClick(window, Qt.Key_Up)
        QTest.keyClick(window, Qt.Key_Right)
        QTest.keyClick(window, Qt.Key_Right)
        QTest.keyClick(window, Qt.Key_Space)
        wait(300)
        for _ in range(5):                     # до «Разблокировать сервисы»
            QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        checks.append(("экран разблокировки открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        unlock = dict(backend.unlockState)
        checks.append(("категории разблокировки загружены",
                       len(unlock.get("categories") or []) > 0))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с разблокировки", window.property("pageDepth") == 1))

        # Информация: кнопка панели (открываем напрямую, чтобы не зависеть от порядка).
        from PySide6.QtCore import QMetaObject as _QMetaObject

        _QMetaObject.invokeMethod(window, "openInfoPage", Qt.AutoConnection)
        wait(700)
        checks.append(("экран информации открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        checks.append(("версия в информации есть", bool(dict(backend.info).get("version"))))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с информации", window.property("pageDepth") == 1))

        # Отчёт: открываем напрямую и загружаем первый отчёт.
        _QMetaObject.invokeMethod(window, "openReportPage", Qt.AutoConnection)
        wait(900)
        checks.append(("экран отчёта открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        checks.append(("список отчётов получен", len(backend.reports) > 0))
        if backend.reports:
            backend.loadReport(backend.reports[0]["path"])
            wait(700)
            report = dict(backend.reportData)
            checks.append(("отчёт загружен",
                           len(report.get("rows") or []) > 0
                           and "error" not in report))
        else:
            checks.append(("отчёт загружен", False))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с отчёта", window.property("pageDepth") == 1))

        # Проверка соединения: из меню настроек, запуск и остановка проверок.
        QTest.keyClick(window, Qt.Key_Up)      # «Сменить» -> кнопка WARP
        QTest.keyClick(window, Qt.Key_Up)      # -> автозапуск
        QTest.keyClick(window, Qt.Key_Up)      # -> панель
        QTest.keyClick(window, Qt.Key_Right)
        QTest.keyClick(window, Qt.Key_Right)   # -> настройки
        QTest.keyClick(window, Qt.Key_Space)
        wait(300)
        QTest.keyClick(window, Qt.Key_Down)    # «Сменить стратегию» -> «Проверка соединения»
        QTest.keyClick(window, Qt.Key_Space)
        wait(900)
        checks.append(("экран проверки соединения открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        backend.startConnectionCheck()
        wait(2600)
        checks.append(("проверка соединения запускается",
                       backend.checkRunning and len(backend.checkLog) > 0))
        backend.stopConnectionCheck()
        wait(900)
        checks.append(("проверка соединения останавливается", not backend.checkRunning))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с проверки соединения", window.property("pageDepth") == 1))

        # Автоподбор стратегий: страница и список стратегий (тест не запускаем —
        # он перезапускает службу и требует sudo).
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(window, "openTesterPage", Qt.AutoConnection)
        wait(900)
        checks.append(("экран автоподбора открыт",
                       focus_icon().endswith("back.svg")
                       and window.property("pageDepth") == 2))
        checks.append(("стратегии для теста доступны", len(backend.strategies) > 0))
        checks.append(("режимы теста доступны", len(backend.testerModes) == 2))
        QTest.keyClick(window, Qt.Key_Escape)
        wait(500)
        checks.append(("возврат с автоподбора", window.property("pageDepth") == 1))

        failed = [name for name, ok in checks if not ok]
        for name, ok in checks:
            print(f"  [{'OK' if ok else 'FAIL'}] {name}", flush=True)
        result["code"] = 1 if failed else 0
        app.quit()

    def check_safe() -> None:
        """Ошибка в сценарии не должна оставлять окно висеть: печатаем и выходим."""
        import traceback

        try:
            check()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            result["code"] = 1
            app.quit()

    QTimer.singleShot(1500, check_safe)
    app.exec()
    if result["code"] != 0:
        log.error("Самопроверка интерфейса не пройдена")
    return result["code"]



def main() -> None:
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        try:
            from core.app_logging import get_error_logger

            get_error_logger().exception("Неизвестная ошибка при запуске Qt-интерфейса")
        except Exception:  # noqa: BLE001
            pass
        print(f"Ошибка запуска: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
