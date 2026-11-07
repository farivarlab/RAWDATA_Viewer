"""Theme and dock-layout persistence without touching the user's preferences."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import threading
import time
import unittest

from PySide6.QtCore import QByteArray, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication

from siemens_explorer.preferences import LAYOUT_VERSION, saved_theme
from siemens_explorer.qt_app import MainWindow


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="viewer-preferences-")
        self.path = Path(self.directory.name) / "preferences.ini"
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if window.busy:
                window.cancel_job()
                window.pool.waitForDone(5000)
                APP.processEvents()
            window.close()
            window.deleteLater()
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def settings(self):
        return QSettings(str(self.path), QSettings.Format.IniFormat)

    def window(self, theme=None, *, show=True):
        window = MainWindow(theme=theme, settings=self.settings())
        self.windows.append(window)
        if show:
            window.show_startup(windowed=True)
            APP.processEvents()
        return window

    def wait_for(self, condition):
        deadline = time.monotonic() + 10
        while not condition():
            APP.processEvents()
            time.sleep(0.002)
            if time.monotonic() > deadline:
                self.fail("Preference close did not finish")
        APP.processEvents()

    def test_theme_saved_at_close_and_explicit_theme_override(self):
        first = self.window()
        first.set_theme("modern-light")
        first.close()
        self.assertEqual(saved_theme(self.settings()), "modern-light")
        second = self.window()
        self.assertEqual(second.theme_name, "modern-light")
        self.assertEqual(APP.property("explorerTheme"), "modern-light")
        self.assertEqual([a.data() for a in second.theme_group.actions() if a.isChecked()], ["modern-light"])
        second.close()
        overridden = self.window("dark")
        self.assertEqual(overridden.theme_name, "dark")
        overridden.close()
        self.assertEqual(saved_theme(self.settings()), "dark")

    def test_floating_geometry_and_independently_hidden_explorer_round_trip(self):
        first = self.window("modern-light")
        first.selection_dock.setFloating(True)
        first.selection_dock.setGeometry(60, 80, 330, 440)
        first.explorer_dock.close()
        APP.processEvents()
        old = first.selection_dock.geometry()
        first.close()
        self.assertIsInstance(self.settings().value("window/docks"), QByteArray)
        second = self.window()
        dock = second.selection_dock
        self.assertTrue(dock.isFloating())
        self.assertTrue(dock.isVisible())
        self.assertFalse(second.explorer_dock.isVisible())
        self.assertFalse(second.explorer_dock.toggleViewAction().isChecked())
        self.assertLessEqual(abs(dock.x() - old.x()), 8)
        self.assertLessEqual(abs(dock.y() - old.y()), 8)
        self.assertLessEqual(abs(dock.width() - old.width()), 8)
        self.assertLessEqual(abs(dock.height() - old.height()), 8)
        self.assertEqual(dock.floating_title_bar.title.palette().windowText().color().name(), "#252525")
        second.explorer_dock.toggleViewAction().trigger()
        APP.processEvents()
        self.assertTrue(second.explorer_dock.isVisible())

    def test_closed_floating_panel_stays_hidden_until_restored_from_view(self):
        first = self.window()
        first.selection_dock.setFloating(True)
        first.selection_dock.close()
        first.close()
        second = self.window()
        self.assertFalse(second.selection_dock.isVisible())
        self.assertFalse(second.selection_dock.toggleViewAction().isChecked())
        self.assertTrue(second.selection_dock.isFloating())
        second.selection_dock.toggleViewAction().trigger()
        APP.processEvents()
        self.assertTrue(second.selection_dock.isFloating())
        self.assertTrue(second.selection_dock.isVisible())

    def test_dock_side_and_sizes_are_restored(self):
        first = self.window()
        first.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, first.selection_dock)
        first.resizeDocks([first.selection_dock], [350], Qt.Orientation.Horizontal)
        APP.processEvents()
        width = first.selection_dock.width()
        main_width = first.width()
        first.close()
        second = self.window()
        self.assertEqual(second.dockWidgetArea(second.selection_dock), Qt.DockWidgetArea.RightDockWidgetArea)
        self.assertEqual(second.dockWidgetArea(second.explorer_dock), Qt.DockWidgetArea.LeftDockWidgetArea)
        if abs(second.width() - main_width) <= 10:
            self.assertLessEqual(abs(second.selection_dock.width() - width), 10)
        else:
            # The offscreen test screen is smaller than the initial window;
            # Qt clamps the restored geometry and then fits its dock widths.
            self.assertLess(second.width(), main_width)
            self.assertGreaterEqual(second.selection_dock.width(), second.selection_dock.minimumWidth())
            self.assertLessEqual(second.selection_dock.width(), width)

    def test_corrupt_or_unknown_preferences_fall_back_to_default_layout(self):
        settings = self.settings()
        settings.setValue("appearance/theme", "a removed theme")
        settings.setValue("window/geometry", QByteArray(b"not geometry"))
        settings.setValue("window/docks", QByteArray(b"not dock state"))
        settings.sync()
        window = self.window()
        self.assertEqual(window.theme_name, "cockpit")
        self.assertFalse(window._restored_geometry)
        self.assertFalse(window.selection_dock.isFloating())
        self.assertTrue(window.selection_dock.isVisible())
        self.assertTrue(window.explorer_dock.isVisible())
        self.assertFalse(window.last_error)

    def test_future_layout_version_keeps_default_docks(self):
        first = self.window()
        first.selection_dock.setFloating(True)
        incompatible = first.saveState(LAYOUT_VERSION + 1)
        first.close()
        settings = self.settings()
        settings.setValue("window/docks", incompatible)
        settings.sync()
        second = self.window()
        self.assertFalse(second.selection_dock.isFloating())
        self.assertEqual(second.dockWidgetArea(second.selection_dock), Qt.DockWidgetArea.LeftDockWidgetArea)

    def test_windowed_launch_overrides_saved_fullscreen_and_matches_menu(self):
        first = self.window()
        first.toggle_fullscreen()
        APP.processEvents()
        self.assertTrue(first.isFullScreen())
        first.close()
        second = self.window(show=False)
        self.assertTrue(second._restored_geometry)
        second.show_startup(windowed=True)
        APP.processEvents()
        self.assertFalse(second.isFullScreen())
        self.assertFalse(second.isMaximized())
        self.assertFalse(second.fullscreen_action.isChecked())
        self.assertTrue(second.title_bar.isVisible())

    def test_detached_panel_saved_on_removed_monitor_is_moved_back_on_screen(self):
        first = self.window()
        first.selection_dock.setFloating(True)
        first.selection_dock.move(100000, 100000)
        first.close()
        second = self.window()
        self.assertTrue(second.selection_dock.isFloating())
        self.assertTrue(any(screen.availableGeometry().intersects(second.selection_dock.frameGeometry())
                            for screen in APP.screens()))

    def test_busy_close_saves_after_worker_completion(self):
        window = self.window("modern-light")
        release = threading.Event()
        window._start_job("test", lambda cancel, progress: release.wait(5))
        window.close()
        APP.processEvents()
        self.assertFalse(self.settings().contains("window/docks"))
        self.assertTrue(window.isVisible())
        release.set()
        self.wait_for(lambda: not window.busy and not window.isVisible())
        self.assertTrue(self.settings().contains("window/docks"))
        self.assertEqual(saved_theme(self.settings()), "modern-light")


if __name__ == "__main__":
    unittest.main()
