"""Neptune entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QLabel
from qfluentwidgets import Theme, setTheme, setThemeColor

from neptune.core import paths
from neptune.core.module import ModuleRegistry
from neptune.core.runtime import Runtime
from neptune.core.settings import Settings
from neptune.features.boostgauge import BoostGaugeModule
from neptune.features.car import CarModule
from neptune.features.dragy import DragyModule
from neptune.features.engine import EngineModule
from neptune.features.presets import PresetsModule
from neptune.features.settings import SettingsModule
from neptune.features.suspension import SuspensionModule
from neptune.features.tunes import TunesModule
from neptune.features.turbo import TurboModule
from neptune.ui import theme as T
from neptune.ui.shell import Shell

APP_ID = "Neptune.FH6.Tool"
SPLASH_WIDTH = 420


def build_registry(settings: Settings) -> ModuleRegistry:
    """Register every feature. Adding a feature is one import and one line."""
    registry = ModuleRegistry()

    engine = registry.register(EngineModule(settings))
    turbo = registry.register(TurboModule(settings))
    engine.bind_turbo(turbo)

    registry.register(SuspensionModule(settings))
    registry.register(CarModule(settings))
    registry.register(DragyModule(settings))
    registry.register(BoostGaugeModule(settings))
    registry.register(TunesModule(registry, settings))
    registry.register(PresetsModule(registry, settings))
    registry.register(SettingsModule(registry, settings))

    return registry


def main() -> int:
    if sys.platform == "win32":
        try:
            from ctypes import windll

            windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    application = QApplication(sys.argv)
    application.setApplicationName("Neptune")
    application.setStyle("Fusion")
    application.setFont(T.ui_font())
    setTheme(Theme.DARK)
    setThemeColor(T.ACCENT)
    application.setStyleSheet(T.stylesheet())

    # Shown for as long as Shell takes to build its pages, so the main window is only ever
    # shown fully formed (see Shell.ready).
    splash_path = paths.asset("icons/splash.png")
    splash_pixmap = QPixmap(splash_path) if splash_path else QPixmap()
    if splash_pixmap.isNull():
        splash_pixmap = QPixmap(*T.WINDOW_DEFAULT)
        splash_pixmap.fill(QColor(T.BG))
    else:
        splash_pixmap = splash_pixmap.scaledToWidth(
            SPLASH_WIDTH, Qt.TransformationMode.SmoothTransformation
        )

    splash = QLabel()
    splash.setWindowFlags(Qt.WindowType.SplashScreen | Qt.WindowType.WindowStaysOnTopHint)
    splash.setPixmap(splash_pixmap)
    splash.setFixedSize(splash_pixmap.size())
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        centre = screen.geometry().center()
        splash.move(centre.x() - splash.width() // 2, centre.y() - splash.height() // 2)
    splash.show()
    application.processEvents()

    settings = Settings()
    registry = build_registry(settings)
    runtime = Runtime(registry)

    shell = Shell(registry, runtime, settings)
    shell.ready.connect(splash.close)
    shell.ready.connect(shell.show)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
