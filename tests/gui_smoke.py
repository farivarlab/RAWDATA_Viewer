"""Render a generated native Qt workspace for repeatable layout inspection."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from siemens_explorer.qt_app import MainWindow
from siemens_explorer.qt_theme import apply_theme
from siemens_explorer.twix import create_demo_file


def main():
    app = QApplication.instance() or QApplication([])
    # The offscreen platform has no Windows font database. Register fonts for
    # the render only; the normal Windows platform discovers them itself.
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("tahoma.ttf", "tahomabd.ttf", "consola.ttf"):
        path = fonts / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    app.setStyle("Fusion")
    apply_theme(app, "light")
    window = MainWindow()
    window.resize(1500, 940)
    window.show()

    def wait_for_job():
        deadline = time.monotonic() + 30
        def pending():
            return window.busy or window._pending_preview or window._preview_timer.isActive() or window.pool.activeThreadCount() > 0
        while pending() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if pending():
            raise RuntimeError("Qt operation timed out")
        if window.last_error:
            raise RuntimeError(window.last_error)

    fixture_directory = tempfile.TemporaryDirectory(prefix="qt-layout-fixture-")
    try:
        fixture = create_demo_file(Path(fixture_directory.name) / "test_fixture.dat")
        window.open_path(fixture)
        wait_for_job()
        window.tabs.setCurrentIndex(3)
        wait_for_job()
        window.step_slice(1)
        wait_for_job()
        window.channel_selector.slider.setValue(2)
        wait_for_job()
        app.processEvents()
        QTest.qWait(50)
        output = ROOT / "preview.png"
        if not window.grab().save(str(output), "PNG"):
            raise RuntimeError("Unable to save native layout preview")
        print(f"Qt workspace rendered: {output} ({len(window.scans)} acquisitions)")
        window.tabs.setCurrentIndex(6)
        wait_for_job()
        app.processEvents()
        QTest.qWait(50)
        blocks_output = ROOT / "preview-blocks.png"
        if not window.grab().save(str(blocks_output), "PNG"):
            raise RuntimeError("Unable to save native data-block preview")
        print(f"Qt data blocks rendered: {blocks_output}")
    finally:
        window.close()
        app.processEvents()
        fixture_directory.cleanup()


if __name__ == "__main__":
    main()
