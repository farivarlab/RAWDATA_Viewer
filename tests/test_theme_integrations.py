"""Live asset reloads for block maps and startup, including broken-theme errors."""

import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem

from siemens_explorer import qt_theme, startup
from siemens_explorer.qt_blocks import DataBlocksView
from siemens_explorer.qt_dialogs import QMessageBox
from siemens_explorer.qt_theme import ThemeAssetError


APP = QApplication.instance() or QApplication([])


class EditableThemeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="editable-theme-integrations-")
        self.directory = Path(self.temporary.name) / "themes"
        shutil.copytree(qt_theme.THEME_DIRECTORY, self.directory)

    def tearDown(self):
        self.temporary.cleanup()
        qt_theme.apply_theme(APP, "cockpit")

    def edit_palette(self, **colors):
        path = self.directory / "cockpit.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["colors"].update(colors)
        path.write_text(json.dumps(document), encoding="utf-8")

    def test_block_reload_changes_existing_items_without_rebuilding_or_moving(self):
        view = DataBlocksView()
        scan = SimpleNamespace(index=7, offset=512, length=512, channels=2, samples=16,
                               flags=(), is_image=True, line=0, slice=0, echo=0)
        measurement = SimpleNamespace(index=0, format="VD", offset=0, length=8192,
                                      header_length=512, data_offset=512)
        raw = SimpleNamespace(size=8192, measurements=[measurement])
        try:
            view.resize(800, 500)
            view.show()
            view.set_data(raw, measurement, [scan], selected_scan=scan, selected_channel=1)
            APP.processEvents()
            view.view.scale(2.0, 2.0)
            before_items = {id(item) for item in view.scene.items()}
            before_transform = view.view.transform()
            before_scroll = (view.view.horizontalScrollBar().value(), view.view.verticalScrollBar().value())
            self.edit_palette(block_canvas="#102938", block_channel="#345678", block_text="#fedcba")
            with patch.object(qt_theme, "THEME_DIRECTORY", self.directory):
                view.set_theme("cockpit")
            self.assertEqual(view.scene.backgroundBrush().color().name(), "#102938")
            self.assertEqual({id(item) for item in view.scene.items()}, before_items)
            self.assertEqual(view.view.transform(), before_transform)
            self.assertEqual((view.view.horizontalScrollBar().value(), view.view.verticalScrollBar().value()), before_scroll)
            self.assertEqual((view.selected_scan, view.selected_channel), (7, 1))
            channels = [item for item in view.scene.items() if isinstance(item.data(0), dict) and item.data(0).get("kind") == "channel"]
            self.assertTrue(channels)
            self.assertTrue(all(item.brush().color().name() == "#345678" for item in channels))
            labels = [item for item in view.scene.items() if isinstance(item, QGraphicsSimpleTextItem)]
            self.assertTrue(all(item.brush().color().name() == "#fedcba" for item in labels))
            before_colors = view._colors.copy()
            snapshot = qt_theme.load_theme("cockpit", self.directory)
            self.edit_palette(block_canvas="broken")
            # A validated snapshot remains usable if an editor changes the
            # files while the rest of the application is applying that theme.
            with patch.object(qt_theme, "THEME_DIRECTORY", self.directory):
                view.set_theme("cockpit", assets=snapshot)
            self.assertEqual(view._colors, before_colors)
            with patch.object(qt_theme, "THEME_DIRECTORY", self.directory), self.assertRaises(ThemeAssetError):
                view.set_theme("cockpit")
            self.assertEqual(view._colors, before_colors)
            self.assertEqual(view.view.transform(), before_transform)
        finally:
            view.close()
            view.deleteLater()
            QTest.qWait(1)

    def test_splash_reads_edited_palette_instead_of_imported_snapshot(self):
        self.edit_palette(window="#123456", accent="#abcdef")
        with patch.object(qt_theme, "THEME_DIRECTORY", self.directory):
            splash = startup.create_splash(APP)
        try:
            pixmap = splash.pixmap()
            scale = pixmap.devicePixelRatio()
            image = pixmap.toImage()
            self.assertEqual(image.pixelColor(round(3 * scale), round(3 * scale)).name(), "#123456")
            self.assertEqual(image.pixelColor(round(25 * scale), round(104 * scale)).name(), "#abcdef")
        finally:
            splash.close()
            splash.deleteLater()
            QTest.qWait(1)

    def test_broken_theme_during_early_startup_uses_independent_native_error(self):
        self.edit_palette(window="broken")
        APP.setProperty("explorerTheme", None)
        with patch.object(qt_theme, "THEME_DIRECTORY", self.directory), patch.object(sys, "stderr", None), patch.object(
            startup, "_show_native_error"
        ) as fallback, patch.object(QMessageBox, "critical") as themed:
            log = startup.report_startup_error(RuntimeError("Initial startup failure"), self.temporary.name)
        fallback.assert_called_once()
        themed.assert_not_called()
        self.assertIn("Invalid color", fallback.call_args.args[0])
        self.assertIn("Initial startup failure", log.read_text(encoding="utf-8"))
        self.assertIn("Invalid color", log.read_text(encoding="utf-8"))

    def test_failed_themed_error_dialog_also_falls_back_without_retrying(self):
        APP.setProperty("explorerTheme", "cockpit")
        with patch.object(sys, "stderr", None), patch.object(QMessageBox, "critical", side_effect=ThemeAssetError("Broken caption asset")), patch.object(
            startup, "_show_native_error"
        ) as fallback:
            log = startup.report_startup_error(RuntimeError("Startup failure"), self.temporary.name)
        fallback.assert_called_once()
        self.assertIn("Broken caption asset", fallback.call_args.args[0])
        self.assertIn("Broken caption asset", log.read_text(encoding="utf-8"))

    def test_invalid_palette_on_a_fresh_import_still_reports_a_native_error(self):
        package_source = Path(startup.__file__).parent
        package_root = Path(self.temporary.name) / "fresh-import"
        copied_package = package_root / package_source.name
        shutil.copytree(package_source, copied_package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (copied_package / "resources" / "themes" / "cockpit.json").write_text("{invalid JSON", encoding="utf-8")
        code = """
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from siemens_explorer import startup
captured = []
startup._show_native_error = captured.append
sys.stderr = None
log = startup.report_startup_error(RuntimeError('Initial startup failure'), Path(sys.argv[1]) / 'logs')
assert len(captured) == 1
assert 'Invalid JSON' in captured[0]
assert 'Initial startup failure' in log.read_text(encoding='utf-8')
print('Fresh-import fallback passed')
"""
        result = subprocess.run([sys.executable, "-c", code, str(package_root)], capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Fresh-import fallback passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
