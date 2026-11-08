"""Per-user appearance and window layout preferences, separate from raw data."""

from PySide6.QtCore import QSettings

from .qt_theme import THEME_NAMES


LAYOUT_VERSION = 1


def open_settings():
    """Use a stable application identity, including before the splash appears."""
    settings = QSettings(QSettings.Format.NativeFormat, QSettings.Scope.UserScope,
                         "Local Imaging Tools", "Raw MRI Data Explorer")
    if not settings.contains("appearance/theme"):
        legacy = QSettings(QSettings.Format.NativeFormat, QSettings.Scope.UserScope,
                           "Local Imaging Tools", "Siemens Raw Data Explorer")
        for key in ("appearance/theme", "window/geometry", "window/docks"):
            if legacy.contains(key):
                settings.setValue(key, legacy.value(key))
        settings.sync()
    return settings


def saved_theme(settings):
    name = settings.value("appearance/theme") if settings is not None else None
    return name if name in THEME_NAMES else "cockpit"
