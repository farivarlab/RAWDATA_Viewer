"""A lightweight startup screen and visible errors for windowless launches."""

from pathlib import Path
import sys
import tempfile
import traceback


def create_splash(app, theme="cockpit"):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import QSplashScreen
    from .qt_branding import application_icon
    from .qt_theme import load_theme

    assets = load_theme(theme)
    colors = assets.colors
    width, height = 360, 132
    scale = app.primaryScreen().devicePixelRatio() if app.primaryScreen() else 1
    image = QPixmap(round(width * scale), round(height * scale))
    image.setDevicePixelRatio(scale)
    image.fill(QColor(colors["window"]))
    painter = QPainter(image)
    painter.setPen(QColor(colors["border"]))
    painter.drawRect(0, 0, width - 1, height - 1)
    application_icon().paint(painter, 20, 25, 40, 40)
    painter.setPen(QColor(colors["text"]))
    painter.setFont(QFont(assets.font_family, assets.font_size + 3, QFont.Weight.DemiBold))
    painter.drawText(76, 43, "Raw MRI Data Explorer")
    painter.setFont(QFont(assets.font_family, assets.font_size))
    painter.setPen(QColor(colors["muted"]))
    painter.drawText(76, 64, "Loading…")
    painter.drawText(76, 86, "Not For Diagnostic Use")
    painter.fillRect(20, 103, 320, 3, QColor(colors["base"]))
    painter.fillRect(20, 103, 72, 3, QColor(colors["accent"]))
    painter.end()
    splash = QSplashScreen(image, Qt.WindowType.WindowStaysOnTopHint)
    splash.setObjectName("startupSplash")
    splash.setAccessibleName("Starting Raw MRI Data Explorer. Not For Diagnostic Use")
    splash.setWindowIcon(application_icon())
    return splash


def report_startup_error(error, log_directory=None):
    """Save diagnostic detail and show an error even when Python has no console."""
    detail = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    paths = [Path(log_directory)] if log_directory else [
        Path(__file__).resolve().parents[1] / ".cache",
        Path(tempfile.gettempdir()) / "RawDataExplorer",
    ]
    log_path = None
    for directory in paths:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            log_path = directory / "startup.log"
            log_path.write_text(detail, encoding="utf-8")
            break
        except OSError:
            log_path = None
    message = f"The viewer could not start.\n\n{error}"
    if log_path:
        message += f"\n\nDetails were saved to:\n{log_path}"
    if sys.stderr is not None:
        print(detail, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication
        from .qt_dialogs import QMessageBox
        app = QApplication.instance() or QApplication([])
        if not app.property("explorerTheme"):
            from .qt_theme import apply_theme
            apply_theme(app, "cockpit")
        QMessageBox.critical(None, "Raw MRI Data Explorer — startup error", message)
    except (ImportError, ValueError, OSError, RuntimeError) as dialog_error:
        # ThemeAssetError inherits ValueError. This path must work even when a
        # corrupt theme prevents importing qt_theme or constructing its dialog.
        extra = f"\n\nThe themed error dialog could not be displayed:\n{dialog_error}"
        if log_path:
            try:
                with log_path.open("a", encoding="utf-8") as output:
                    output.write(extra)
            except OSError:
                pass
        _show_native_error(message + extra)
    return log_path


def _show_native_error(message):
    """Keep fatal startup feedback independent of editable theme assets."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Raw MRI Data Explorer — startup error", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)
