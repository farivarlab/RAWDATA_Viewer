"""Scalable identity, splash rendering, and desktop palette checks."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtGui import QFontDatabase, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QPushButton

from siemens_explorer.qt_branding import RESOURCE_DIRECTORY, application_icon
from siemens_explorer.qt_theme import apply_theme
from siemens_explorer.startup import create_splash, report_startup_error


_APP = QApplication.instance() or QApplication([])
_FONT = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "tahoma.ttf"
if _FONT.is_file():
    QFontDatabase.addApplicationFont(str(_FONT))


class BrandingTests(unittest.TestCase):
    def tearDown(self):
        apply_theme(_APP, "light")

    def test_application_logo_scales_without_missing_pixels(self):
        icon = application_icon()
        self.assertFalse(icon.isNull())
        for size in (16, 20, 24, 32, 48, 64, 128, 256):
            image = icon.pixmap(size, size).toImage()
            self.assertFalse(image.isNull())
            self.assertEqual((image.width(), image.height()), (size, size))
            colors = {image.pixelColor(x, y).name() for x in range(size) for y in range(size)}
            self.assertIn("#202020", colors)
            light_pixels = [image.pixelColor(x, y) for x in range(size) for y in range(size)
                            if image.pixelColor(x, y).alpha() > 200 and image.pixelColor(x, y).red() > 150]
            self.assertGreater(len(light_pixels), size // 2)
            self.assertTrue(all(color.red() == color.green() == color.blue() for color in light_pixels))
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
            self.assertGreater(len(colors), 5)

    def test_windows_icon_contains_sharp_sizes_for_taskbar_and_high_dpi(self):
        icon = QIcon(str(RESOURCE_DIRECTORY / "app.ico"))
        expected = (16, 20, 24, 32, 40, 48, 64, 128, 256)
        self.assertEqual(sorted(size.width() for size in icon.availableSizes()), list(expected))
        for size in expected:
            image = icon.pixmap(size, size).toImage()
            self.assertEqual((image.width(), image.height()), (size, size))
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_modern_splashes_follow_both_palettes_at_each_screen_scale(self):
        from siemens_explorer.qt_theme import theme_colors
        for theme, scale in ((theme, scale) for theme in ("cockpit", "modern-light") for scale in (1, 1.25, 1.5, 2)):
            colors = theme_colors(theme)
            screen = SimpleNamespace(devicePixelRatio=lambda: scale)
            with patch.object(_APP, "primaryScreen", return_value=screen):
                splash = create_splash(_APP, theme)
            try:
                pixmap = splash.pixmap()
                self.assertEqual(pixmap.devicePixelRatio(), scale)
                self.assertEqual((pixmap.width(), pixmap.height()), (round(360 * scale), round(132 * scale)))
                image = pixmap.toImage()
                self.assertEqual(image.pixelColor(round(3 * scale), round(3 * scale)).name(), colors["window"])
                self.assertEqual(image.pixelColor(round(25 * scale), round(104 * scale)).name(), colors["accent"])
                self.assertFalse(splash.windowIcon().isNull())
            finally:
                splash.close()
                splash.deleteLater()

    def test_early_startup_error_uses_default_theme_and_preserves_existing_theme(self):
        from siemens_explorer.qt_dialogs import QMessageBox
        with tempfile.TemporaryDirectory() as directory, patch.object(sys, "stderr", None), patch.object(QMessageBox, "critical"):
            _APP.setProperty("explorerTheme", None)
            report_startup_error(RuntimeError("Startup failure"), directory)
            self.assertEqual(_APP.property("explorerTheme"), "cockpit")
            apply_theme(_APP, "light")
            palette = _APP.palette()
            report_startup_error(RuntimeError("Startup failure"), directory)
            self.assertEqual(_APP.property("explorerTheme"), "light")
            self.assertEqual(_APP.palette(), palette)

    def test_classic_light_palette_and_dark_palette_keep_legible_selection(self):
        button = QPushButton("Open raw data")
        sizes = []
        for theme in ("light", "dark"):
            apply_theme(_APP, theme)
            button.ensurePolished()
            sizes.append(button.sizeHint())
            palette = _APP.palette()
            self.assertEqual(palette.color(QPalette.ColorRole.Highlight).name(), "#000080")
            self.assertEqual(palette.color(QPalette.ColorRole.HighlightedText).name(), "#ffffff")
            text = palette.color(QPalette.ColorRole.Text).lightness()
            background = palette.color(QPalette.ColorRole.Base).lightness()
            self.assertGreater(abs(text - background), 180)
            if theme == "light":
                self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), "#c0c0c0")
        self.assertEqual(sizes[0], sizes[1])
        button.deleteLater()


if __name__ == "__main__":
    unittest.main()
