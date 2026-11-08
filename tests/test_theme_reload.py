"""Editable assets update a populated workspace without changing its data/view."""

import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from siemens_explorer import qt_app
from siemens_explorer.analysis import Preview
from siemens_explorer.qt_app import MainWindow
from siemens_explorer.qt_theme import THEME_DIRECTORY, ThemeAssetError, apply_theme, load_theme


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class ThemeReloadTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()
        self.window.resize(1280, 820)
        self.window.show()
        self.signal = np.array([1+2j, 3-4j, 5+6j], dtype=np.complex64)
        self.window.signal_view.set_signal(self.signal)
        matrix = np.arange(16, dtype=np.float32).reshape(4, 4)
        self.preview = Preview(matrix.astype(np.complex64), matrix, np.ones((4, 4), dtype=bool), np.arange(4))
        self.window.preview_view.set_preview(self.preview)
        self.window.preview_view.kspace_view.setLevels(1, 3)
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(1)
        apply_theme(APP, "cockpit")

    def test_reload_recolors_captions_plots_blocks_and_preserves_arrays_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "themes"
            shutil.copytree(THEME_DIRECTORY, target)
            path = target / "cockpit.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["colors"].update(caption_text="#acef98", plot_accent="#ffe199", plot_background="#18251a", block_canvas="#1e2820")
            path.write_text(json.dumps(document), encoding="utf-8")
            assets = load_theme("cockpit", target)
            with patch.object(qt_app, "load_theme", return_value=assets):
                self.window.reload_theme_action.trigger()
            APP.processEvents()
            self.assertEqual(self.window.title_bar.title.palette().windowText().color().name(), "#acef98")
            self.assertEqual(self.window.signal_view.plot.backgroundBrush().color().name(), "#18251a")
            self.assertEqual(self.window.signal_view.plot.listDataItems()[0].opts["pen"].color().name(), "#ffe199")
            self.assertEqual(self.window.blocks_view.scene.backgroundBrush().color().name(), "#1e2820")
            np.testing.assert_array_equal(self.window.signal_view.signal, self.signal)
            self.assertIs(self.window.preview_view.preview, self.preview)
            np.testing.assert_allclose(self.window.preview_view.kspace_view.getLevels(), [1, 3])

    def test_invalid_edited_theme_keeps_applied_theme_and_reports_asset_error(self):
        before = APP.palette().color(QPalette.ColorRole.Window).name()
        stylesheet = APP.styleSheet()
        with patch.object(qt_app, "load_theme", side_effect=ThemeAssetError("cockpit.json: invalid color")):
            self.window.reload_theme_action.trigger()
        self.assertEqual(APP.palette().color(QPalette.ColorRole.Window).name(), before)
        self.assertEqual(APP.styleSheet(), stylesheet)
        self.assertEqual(self.window.theme_name, "cockpit")
        self.assertIn("cockpit.json", self.window.notice.text())
        np.testing.assert_array_equal(self.window.signal_view.signal, self.signal)

    def test_editable_logo_reloads_main_caption_and_new_dialog_icons(self):
        from siemens_explorer import qt_branding
        from siemens_explorer.qt_dialogs import QMessageBox
        with tempfile.TemporaryDirectory() as directory:
            logo = Path(directory) / "logo.svg"
            logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x="1" y="1" width="30" height="30" fill="#bb3355"/></svg>')
            with patch.object(qt_branding, "LOGO_FILE", logo), patch.object(qt_app, "update_windows_icon"):
                self.window.reload_theme_action.trigger()
                APP.processEvents()
                dialog = QMessageBox(self.window)
                try:
                    for icon in (self.window.windowIcon(), self.window.title_bar.system_button.icon(), dialog.windowIcon()):
                        self.assertEqual(icon.pixmap(32, 32).toImage().pixelColor(16, 16).name(), "#bb3355")
                finally:
                    dialog.close()
                    dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
