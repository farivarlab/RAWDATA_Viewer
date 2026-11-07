"""Launch the native Qt desktop application."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Native Qt explorer for Siemens MRI Twix .dat files.")
    parser.add_argument("file", nargs="?", type=Path, help="Optional .dat file to open")
    parser.add_argument("--theme", choices=("cockpit", "modern-light", "light", "dark"), help="Override the saved theme: modern dark/light or classic light/dark")
    parser.add_argument("--windowed", action="store_true", help="Start in a resizable window instead of maximized")
    args = parser.parse_args(argv)
    if args.file is not None and not args.file.expanduser().is_file():
        if sys.stderr is None:
            from .startup import report_startup_error
            report_startup_error(FileNotFoundError(f"File does not exist: {args.file}"))
            return 1
        parser.error(f"File does not exist: {args.file}")
    from .startup import create_splash, report_startup_error
    splash = None
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from .qt_theme import apply_theme
        from .qt_branding import application_icon, update_windows_icon
        from .preferences import open_settings, saved_theme

        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setApplicationName("Raw MRI Data Explorer")
        app.setOrganizationName("Local Imaging Tools")
        icon = application_icon()
        app.setWindowIcon(icon)
        update_windows_icon(icon)
        app.setStyle("Fusion")
        settings = open_settings()
        theme = args.theme or saved_theme(settings)
        apply_theme(app, theme)
        splash = create_splash(app, theme)
        splash.show()
        app.processEvents()
        # Load the imaging libraries only after the startup screen has painted.
        from .qt_app import MainWindow
        window = MainWindow(theme=theme, settings=settings)
        window.show_startup(windowed=args.windowed)
        splash.finish(window)
        if args.file:
            QTimer.singleShot(0, lambda: window.open_path(args.file))
        return app.exec()
    except Exception as error:
        if splash is not None:
            splash.close()
        report_startup_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
