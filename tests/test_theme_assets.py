"""Reload editable palettes/styles and preserve the running theme on bad edits."""

import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import shutil
import tempfile
import unittest

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from siemens_explorer.qt_theme import (
    COCKPIT, DARK, LIGHT, THEME_DIRECTORY, ThemeAssetError,
    apply_theme, apply_theme_assets, load_theme, theme_colors, theme_stylesheet,
)


APP = QApplication.instance() or QApplication([])


class ThemeAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="editable-theme-")
        self.folder = Path(self.temp.name) / "themes"
        shutil.copytree(THEME_DIRECTORY, self.folder)
        self.path = self.folder / "cockpit.json"
        self.document = json.loads(self.path.read_text(encoding="utf-8"))
        apply_theme(APP, "light")

    def tearDown(self):
        apply_theme(APP, "light")
        self.temp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.document), encoding="utf-8")

    def test_all_bundled_themes_preserve_existing_desktop_colors_and_templates(self):
        for name, expected, snapshot in (
            ("light", "#c0c0c0", LIGHT),
            ("dark", "#303030", DARK),
            ("cockpit", "#191919", COCKPIT),
            ("modern-light", "#f1f1f1", theme_colors("modern-light")),
        ):
            assets = load_theme(name)
            self.assertEqual(assets.colors["window"], expected)
            self.assertEqual(assets.colors, snapshot)
            self.assertNotIn("%(", assets.stylesheet)
            self.assertNotIn("%(", assets.caption_stylesheet)
            self.assertIn(assets.colors["caption_text"], assets.caption_stylesheet)
            self.assertEqual(theme_stylesheet(name), assets.stylesheet)
            self.assertTrue(all(key in assets.colors for key in ("plot_background", "block_noise", "glyph_foreground")))

    def test_palette_font_and_stylesheet_edits_are_loaded_fresh_without_python_changes(self):
        self.document["colors"]["window"] = "#112233"
        self.document["colors"]["accent"] = "#55bb99"
        self.document["colors"]["caption_text"] = "#ccddee"
        self.document["font"]["point_size"] = 11
        self.save()
        qss = self.folder / "cockpit.qss"
        qss.write_text(qss.read_text(encoding="utf-8") + "\nQPushButton { padding-left: 12px; }\n", encoding="utf-8")
        assets = apply_theme(APP, "cockpit", self.folder)
        self.assertEqual(APP.palette().color(QPalette.ColorRole.Window).name(), "#112233")
        self.assertEqual(APP.font().pointSize(), 11)
        self.assertIn("padding-left: 12px", APP.styleSheet())
        self.assertIn("#ccddee", assets.caption_stylesheet)
        self.assertEqual(APP.property("explorerThemeColors")["accent"], "#55bb99")
        self.document["colors"]["accent"] = "#aa55bb"
        self.save()
        self.assertEqual(theme_colors("cockpit", self.folder)["accent"], "#aa55bb")
        # Reading a changed file does not itself alter the applied theme.
        self.assertEqual(APP.property("explorerThemeColors")["accent"], "#55bb99")

    def assert_invalid_edit_is_atomic(self):
        old_palette, old_font, old_qss = APP.palette(), APP.font(), APP.styleSheet()
        old_name, old_colors = APP.property("explorerTheme"), APP.property("explorerThemeColors")
        with self.assertRaises(ThemeAssetError) as raised:
            apply_theme(APP, "cockpit", self.folder)
        self.assertIn(str(self.folder), str(raised.exception))
        self.assertEqual(APP.palette(), old_palette)
        self.assertEqual(APP.font(), old_font)
        self.assertEqual(APP.styleSheet(), old_qss)
        self.assertEqual(APP.property("explorerTheme"), old_name)
        self.assertEqual(APP.property("explorerThemeColors"), old_colors)

    def test_preloaded_snapshot_can_be_applied_without_mixing_later_file_edits(self):
        self.document["colors"]["window"] = "#223344"
        self.save()
        snapshot = load_theme("cockpit", self.folder)
        self.document["colors"]["window"] = "#556677"
        self.save()
        apply_theme_assets(APP, snapshot)
        self.assertEqual(APP.palette().color(QPalette.ColorRole.Window).name(), "#223344")
        self.assertEqual(APP.property("explorerThemeColors")["window"], "#223344")
        self.assertEqual(theme_colors("cockpit", self.folder)["window"], "#556677")

    def test_invalid_json_color_and_missing_required_color_leave_current_theme_intact(self):
        for edit in ("json", "color", "missing"):
            with self.subTest(edit=edit):
                document = json.loads((THEME_DIRECTORY / "cockpit.json").read_text(encoding="utf-8"))
                if edit == "color":
                    document["colors"]["window"] = "#not-a-color"
                elif edit == "missing":
                    del document["colors"]["caption_text"]
                self.path.write_text("{" if edit == "json" else json.dumps(document), encoding="utf-8")
                self.assert_invalid_edit_is_atomic()

    def test_missing_unknown_or_broken_stylesheet_leaves_current_theme_intact(self):
        for contents in (None, "QWidget { color: %(does_not_exist)s; }", "QWidget { color: %(text)s;", "QWidget { color: %(text)x; }"):
            with self.subTest(contents=contents):
                qss = self.folder / "cockpit.qss"
                if contents is None:
                    qss.unlink()
                else:
                    qss.write_text(contents, encoding="utf-8")
                self.assert_invalid_edit_is_atomic()

    def test_caption_asset_and_template_path_are_validated_before_applying(self):
        caption = self.folder / "caption-cockpit.qss"
        caption.write_text("QWidget { background: %(misspelled)s; }", encoding="utf-8")
        self.assert_invalid_edit_is_atomic()
        self.document["caption_stylesheet"] = "../outside.qss"
        self.save()
        self.assert_invalid_edit_is_atomic()


if __name__ == "__main__":
    unittest.main()
