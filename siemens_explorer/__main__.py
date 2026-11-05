"""Launch the native Qt desktop application."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Native Qt explorer for Siemens MRI Twix .dat files.")
    parser.add_argument("file", nargs="?", type=Path, help="Optional .dat file to open")
    parser.add_argument("--theme", choices=("light", "dark"), default="light", help="Desktop theme")
    parser.add_argument("--windowed", action="store_true", help="Start in a resizable window instead of maximized")
    args = parser.parse_args(argv)
    if args.file is not None and not args.file.expanduser().is_file():
        parser.error(f"File does not exist: {args.file}")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from .qt_theme import apply_theme
    from .qt_branding import application_icon
    from .qt_app import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Siemens Raw Data Explorer")
    app.setOrganizationName("Local Imaging Tools")
    app.setWindowIcon(application_icon())
    app.setStyle("Fusion")
    apply_theme(app, args.theme)
    window = MainWindow(theme=args.theme)
    if args.windowed:
        window.show()
    else:
        window.showMaximized()
    if args.file:
        QTimer.singleShot(0, lambda: window.open_path(args.file))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
