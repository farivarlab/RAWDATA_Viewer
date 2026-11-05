"""Scalable identity and classic-theme integration checks."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import unittest

from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from siemens_explorer.qt_branding import application_icon, brand_header
from siemens_explorer.qt_theme import apply_theme


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
        for size in (16, 32, 64):
            image = icon.pixmap(size, size).toImage()
            self.assertFalse(image.isNull())
            self.assertEqual((image.width(), image.height()), (size, size))
            colors = {image.pixelColor(x, y).name() for x in range(size) for y in range(size)}
            self.assertIn("#000080", colors)
            self.assertIn("#00ffff", colors)
            self.assertGreater(len(colors), 5)

    def test_brand_header_contains_logo_title_and_wordmark(self):
        header = brand_header()
        header.resize(700, 46)
        header.show()
        _APP.processEvents()
        try:
            title = header.findChild(QLabel, "brandTitle")
            subtitle = header.findChild(QLabel, "brandSubtitle")
            self.assertEqual(title.text(), "RAW DATA EXPLORER")
            self.assertIn("Siemens MRI", subtitle.text())
            for name in ("brandLogo", "brandWordmark"):
                self.assertFalse(header.findChild(QLabel, name).pixmap().isNull())
            self.assertEqual(header.height(), 46)
            self.assertGreater(header.findChild(QLabel, "brandWordmark").x(), title.x())
        finally:
            header.close()
            header.deleteLater()

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
