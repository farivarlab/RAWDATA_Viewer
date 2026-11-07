"""Original SVG glyph visibility, state colors, scaling, and saved edits."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from siemens_explorer import qt_glyphs
from siemens_explorer.qt_glyphs import GLYPH_NAMES, clear_glyph_cache, glyph_icon


APP = QApplication.instance() or QApplication([])


class GlyphTests(unittest.TestCase):
    def tearDown(self):
        clear_glyph_cache()

    @staticmethod
    def visible_colors(image):
        return {image.pixelColor(x, y).name()
                for y in range(image.height()) for x in range(image.width())
                if image.pixelColor(x, y).alpha() > 180}

    def test_every_glyph_is_standalone_editable_svg_and_visible_at_control_sizes(self):
        for name in GLYPH_NAMES:
            with self.subTest(glyph=name):
                asset = qt_glyphs.GLYPH_DIRECTORY / f"{name}.svg"
                self.assertTrue(QSvgRenderer(str(asset)).isValid())
                for theme in ("light", "dark", "cockpit", "modern-light"):
                    icon = glyph_icon(name, theme)
                    self.assertFalse(icon.isNull())
                    for size in (16, 18, 24, 32):
                        image = icon.pixmap(size, size).toImage()
                        self.assertEqual((image.width(), image.height()), (size, size))
                        self.assertGreater(len(self.visible_colors(image)), 0)

    def test_palette_tint_and_disabled_state_are_explicit_and_distinct(self):
        colors = {"text": "#dce7ef", "disabled": "#687988",
                  "glyph_foreground": "#62d7e6", "glyph_disabled": "#91a3b6"}
        icon = glyph_icon("export", colors)
        enabled = self.visible_colors(icon.pixmap(32, 32, QIcon.Mode.Normal).toImage())
        disabled = self.visible_colors(icon.pixmap(32, 32, QIcon.Mode.Disabled).toImage())
        self.assertIn("#62d7e6", enabled)
        self.assertIn("#91a3b6", disabled)
        self.assertNotIn("#62d7e6", disabled)
        override = glyph_icon("close", colors, color="#000000")
        self.assertIn("#000000", self.visible_colors(override.pixmap(32, 32).toImage()))

    def test_fractional_dpi_uses_a_full_resolution_variant(self):
        icon = glyph_icon("open", "cockpit")
        pixmap = icon.pixmap(QSize(18, 18), 1.25)
        self.assertAlmostEqual(pixmap.devicePixelRatio(), 1.25)
        self.assertIn(pixmap.width(), (22, 23))
        self.assertGreater(len(self.visible_colors(pixmap.toImage())), 0)

    def test_saved_svg_edits_are_loaded_without_a_python_change(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(qt_glyphs, "GLYPH_DIRECTORY", Path(directory)):
            asset = Path(directory) / "close.svg"
            prefix = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18">'
            asset.write_text(prefix + '<path d="M3 3h12v12H3z" fill="currentColor"/></svg>', encoding="utf-8")
            first = glyph_icon("close", "cockpit").pixmap(32, 32).toImage()
            asset.write_text(prefix + '<circle cx="9" cy="9" r="3" fill="currentColor"/></svg>', encoding="utf-8")
            second = glyph_icon("close", "cockpit").pixmap(32, 32).toImage()
            self.assertNotEqual(first, second)

    def test_bad_names_and_missing_or_invalid_assets_have_clear_errors(self):
        with self.assertRaises(ValueError):
            glyph_icon("../outside")
        with tempfile.TemporaryDirectory() as directory, patch.object(qt_glyphs, "GLYPH_DIRECTORY", Path(directory)):
            with self.assertRaisesRegex(OSError, "missing.svg"):
                glyph_icon("missing")
            asset = Path(directory) / "broken.svg"
            asset.write_text("This is not SVG", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "broken.svg"):
                glyph_icon("broken")


if __name__ == "__main__":
    unittest.main()
