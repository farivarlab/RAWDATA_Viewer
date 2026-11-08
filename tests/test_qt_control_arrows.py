"""Rendered arrows and their stepping behavior in both classic palettes."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QLineEdit, QPushButton, QScrollBar, QSpinBox, QStyle,
    QStyleOptionComboBox, QStyleOptionSlider, QStyleOptionSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from siemens_explorer.qt_theme import apply_theme


_APP = QApplication.instance() or QApplication([])


class ControlArrowTests(unittest.TestCase):
    def tearDown(self):
        apply_theme(_APP, "light")

    def spin_rect(self, widget, control):
        option = QStyleOptionSpinBox()
        widget.initStyleOption(option)
        return widget.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, control, widget)

    def assert_glyph_color(self, widget, rect, color):
        # Ignore the beveled frame so a border cannot stand in for a missing glyph.
        rect = rect.adjusted(4, 3, -4, -3)
        image = widget.grab().toImage()
        scale = image.devicePixelRatio()
        colors = {image.pixelColor(x, y).name()
                  for x in range(round(rect.left() * scale), round((rect.right() + 1) * scale))
                  for y in range(round(rect.top() * scale), round((rect.bottom() + 1) * scale))}
        self.assertIn(color, colors)

    def test_spinners_render_arrows_and_step_in_both_themes(self):
        for theme, color in (("light", "#000000"), ("dark", "#eeeeee"), ("cockpit", "#eeeeee"), ("modern-light", "#000000")):
            apply_theme(_APP, theme)
            for cls in (QSpinBox, QDoubleSpinBox):
                with self.subTest(theme=theme, widget=cls.__name__):
                    widget = cls()
                    widget.setRange(0, 10)
                    widget.setValue(5)
                    widget.resize(160, 30)
                    widget.show()
                    _APP.processEvents()
                    up = self.spin_rect(widget, QStyle.SubControl.SC_SpinBoxUp)
                    down = self.spin_rect(widget, QStyle.SubControl.SC_SpinBoxDown)
                    self.assert_glyph_color(widget, up, color)
                    self.assert_glyph_color(widget, down, color)
                    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=up.center())
                    self.assertEqual(widget.value(), 6)
                    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=down.center())
                    self.assertEqual(widget.value(), 5)
                    widget.setValue(10)
                    self.assert_glyph_color(widget, up, "#808080")
                    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=up.center())
                    self.assertEqual(widget.value(), 10)
                    widget.setValue(0)
                    self.assert_glyph_color(widget, down, "#808080")
                    widget.close()
                    widget.deleteLater()
                    _APP.processEvents()

    def test_combo_arrows_remain_visible_when_disabled(self):
        for theme, color in (("light", "#000000"), ("dark", "#eeeeee"), ("cockpit", "#eeeeee"), ("modern-light", "#000000")):
            with self.subTest(theme=theme):
                apply_theme(_APP, theme)
                widget = QComboBox()
                widget.addItems(["All", "0"])
                widget.resize(160, 30)
                widget.show()
                _APP.processEvents()
                option = QStyleOptionComboBox()
                widget.initStyleOption(option)
                rect = widget.style().subControlRect(
                    QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, widget)
                self.assert_glyph_color(widget, rect, color)
                widget.setEnabled(False)
                self.assert_glyph_color(widget, rect, "#808080")
                widget.close()
                widget.deleteLater()
                _APP.processEvents()

    def test_embedded_arrow_buttons_and_scrollbars_remain_visible(self):
        for theme, color in (("light", "#000000"), ("dark", "#eeeeee"), ("cockpit", "#eeeeee"), ("modern-light", "#000000")):
            with self.subTest(theme=theme):
                apply_theme(_APP, theme)
                host = QWidget()
                layout = QVBoxLayout(host)
                buttons = []
                for arrow in (Qt.ArrowType.UpArrow, Qt.ArrowType.DownArrow,
                              Qt.ArrowType.LeftArrow, Qt.ArrowType.RightArrow):
                    button = QToolButton()
                    button.setArrowType(arrow)
                    button.setFixedSize(30, 30)
                    layout.addWidget(button)
                    buttons.append(button)
                bar = QScrollBar(Qt.Orientation.Vertical)
                bar.setFixedSize(18, 160)
                bar.setRange(0, 100)
                bar.setValue(50)
                layout.addWidget(bar)
                host.show()
                _APP.processEvents()
                for button in buttons:
                    self.assert_glyph_color(button, button.rect().adjusted(2, 2, -2, -2), color)
                    button.setEnabled(False)
                    self.assert_glyph_color(button, button.rect().adjusted(2, 2, -2, -2), "#808080")
                for control, delta in ((QStyle.SubControl.SC_ScrollBarAddLine, 1),
                                       (QStyle.SubControl.SC_ScrollBarSubLine, -1)):
                    option = QStyleOptionSlider()
                    bar.initStyleOption(option)
                    rect = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, control, bar)
                    start = bar.value()
                    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=rect.center())
                    self.assertEqual(bar.value(), start + delta)
                host.close()
                host.deleteLater()
                _APP.processEvents()

    def test_cockpit_palette_is_dark_and_switching_restores_classic_font(self):
        from PySide6.QtGui import QPalette
        apply_theme(_APP, "cockpit")
        self.assertEqual(_APP.property("explorerTheme"), "cockpit")
        self.assertEqual(_APP.font().family(), "Segoe UI")
        palette = _APP.palette()
        self.assertLess(palette.color(QPalette.ColorRole.Window).lightness(), 50)
        self.assertGreater(palette.color(QPalette.ColorRole.Text).lightness(), 210)
        self.assertGreater(palette.color(QPalette.ColorRole.HighlightedText).lightness()
                           - palette.color(QPalette.ColorRole.Highlight).lightness(), 150)
        apply_theme(_APP, "light")
        self.assertEqual(_APP.property("explorerTheme"), "light")
        self.assertEqual(_APP.font().family(), "Tahoma")

    def test_everyday_controls_are_readable_without_oversizing_caption_buttons(self):
        for theme in ("light", "dark", "cockpit", "modern-light"):
            with self.subTest(theme=theme):
                apply_theme(_APP, theme)
                host = QWidget()
                layout = QVBoxLayout(host)
                button = QPushButton("Auto levels")
                tool = QToolButton()
                tool.setText("Open")
                combo = QComboBox()
                combo.addItem("Log magnitude")
                controls = [button, tool, QLineEdit("Value"), combo, QSpinBox()]
                for control in controls:
                    layout.addWidget(control)
                caption = QToolButton()
                caption.setProperty("captionButton", True)
                caption.setStyleSheet("padding: 0; border: 1px solid gray;")
                caption.setFixedSize(26, 22)
                layout.addWidget(caption)
                host.show()
                _APP.processEvents()
                for control in controls:
                    self.assertGreaterEqual(control.height(), 28)
                    self.assertLessEqual(control.height(), 34)
                self.assertEqual(caption.width(), 26)
                self.assertGreaterEqual(caption.height(), 20)
                self.assertLessEqual(caption.height(), 22)
                host.close()
                host.deleteLater()
                _APP.processEvents()


if __name__ == "__main__":
    unittest.main()
