"""Classic-caption controls and stable, user-resizable sidebar geometry."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from siemens_explorer.qt_app import MainWindow
from siemens_explorer.qt_chrome import frame_hit_test
from siemens_explorer.qt_theme import apply_theme


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class ClassicChromeTests(unittest.TestCase):
    def setUp(self):
        apply_theme(APP, "light")
        self.window = MainWindow(theme="light")
        self.window.resize(1280, 820)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(1)

    def test_title_and_menus_remain_connected(self):
        window = self.window
        self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(window.title_bar.isVisible())
        self.assertEqual(len(window.menuBar().actions()), 4)
        window.setWindowTitle("measurement.dat — Raw MRI Data Explorer")
        self.assertEqual(window.title_bar.title.text(), window.windowTitle())
        self.assertEqual(window.title_bar.title.toolTip(), window.windowTitle())

    def test_window_buttons_and_double_click(self):
        window = self.window
        window.title_bar.maximize_button.click()
        APP.processEvents()
        self.assertTrue(window.isMaximized())
        window.title_bar.maximize_button.click()
        APP.processEvents()
        self.assertFalse(window.isMaximized())
        window.title_bar.minimize_button.click()
        APP.processEvents()
        self.assertTrue(window.isMinimized())
        window.showNormal()
        APP.processEvents()
        QTest.mouseDClick(window.title_bar, Qt.MouseButton.LeftButton)
        APP.processEvents()
        self.assertTrue(window.isMaximized())
        window.title_bar.close_button.click()
        APP.processEvents()
        self.assertFalse(window.isVisible())

    def test_black_caption_text_in_both_activation_states(self):
        bar = self.window.title_bar
        for active, expected in ((True, "#c0c0c0"), (False, "#b0b0b0")):
            bar.setProperty("windowActive", active)
            bar.style().unpolish(bar)
            bar.style().polish(bar)
            APP.processEvents()
            self.assertEqual(bar.grab().toImage().pixelColor(100, 1).name(), expected)
            self.assertEqual(bar.title.palette().color(QPalette.ColorRole.WindowText).name(), "#000000")

    def test_fullscreen_keeps_the_classic_caption_out_of_the_view(self):
        self.window.toggle_fullscreen()
        APP.processEvents()
        self.assertTrue(self.window.isFullScreen())
        self.assertFalse(self.window.title_bar.isVisible())
        self.window.toggle_fullscreen()
        APP.processEvents()
        self.assertTrue(self.window.title_bar.isVisible())

    def test_measurements_resize_and_stay_put_while_scrolling_selection(self):
        window = self.window
        heights = []
        for requested in (190, 270):
            total = window.explorer_dock.height() + window.selection_dock.height()
            window.resizeDocks([window.explorer_dock, window.selection_dock],
                               [requested, total - requested], Qt.Orientation.Vertical)
            APP.processEvents()
            chosen = window.measurement_tree.height()
            heights.append(chosen)
            scrollbar = window.selection_scroll.verticalScrollBar()
            for position in (scrollbar.maximum(), 0, scrollbar.maximum(), 0):
                scrollbar.setValue(position)
                APP.processEvents()
                self.assertEqual(window.measurement_tree.height(), chosen)
        self.assertGreater(heights[1], heights[0])

    def test_native_resize_edges_include_corners_and_exclude_content(self):
        for point, expected in (((1, 1), 13), ((998, 1), 14), ((1, 698), 16), ((998, 698), 17),
                                ((1, 350), 10), ((998, 350), 11), ((500, 1), 12), ((500, 698), 15),
                                ((500, 350), None), ((-1, 350), None)):
            self.assertEqual(frame_hit_test(*point, 1000, 700), expected)

    def test_floating_panel_uses_matching_caption_and_can_redock(self):
        dock = self.window.selection_dock
        APP.processEvents()
        self.assertIsNone(dock.titleBarWidget())
        self.assertFalse(dock.floating_title_bar.isVisible())
        for _ in range(2):
            dock.setFloating(True)
            APP.processEvents()
            self.assertIs(dock.titleBarWidget(), dock.floating_title_bar)
            self.assertTrue(dock.floating_title_bar.isVisible())
            self.assertFalse(dock.floating_title_bar.maximize_button.isVisible())
            QTest.mouseDClick(dock.floating_title_bar, Qt.MouseButton.LeftButton)
            APP.processEvents()
            self.assertFalse(dock.isFloating())
            self.assertIsNone(dock.titleBarWidget())
            self.assertFalse(dock.floating_title_bar.isVisible())

    def test_floating_panel_close_and_view_toggle_preserve_contents(self):
        dock = self.window.selection_dock
        contents = self.window.data_selection
        view = next(action.menu() for action in self.window.menuBar().actions() if action.text() == "&View")
        self.assertIn(dock.toggleViewAction(), view.actions())
        dock.setFloating(True)
        APP.processEvents()
        dock.floating_title_bar.close_button.click()
        APP.processEvents()
        self.assertFalse(dock.isVisible())
        self.assertTrue(self.window.explorer_dock.isVisible())
        self.assertFalse(dock.toggleViewAction().isChecked())
        dock.toggleViewAction().trigger()
        APP.processEvents()
        self.assertTrue(dock.isVisible())
        self.assertTrue(dock.floating_title_bar.isVisible())
        self.assertIs(dock.widget(), contents)
        self.assertTrue(dock.toggleViewAction().isChecked())


if __name__ == "__main__":
    unittest.main()
