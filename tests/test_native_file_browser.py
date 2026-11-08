"""Application Open/Save use the native backend without custom frame changes."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QFileDialog as QtFileDialog
from siemens_explorer import qt_dialogs
from siemens_explorer.qt_dialogs import QFileDialog


APP = QApplication.instance() or QApplication([])


class NativeFileBrowserTests(unittest.TestCase):
    def test_open_forwards_parent_path_filters_and_native_selection(self):
        selection = (r"C:\MRI data\measurement file.dat", "Twix (*.dat)")
        options = QtFileDialog.Option.ReadOnly
        with patch.object(qt_dialogs, "_use_native_file_browser", return_value=True), patch.object(
            QtFileDialog, "getOpenFileName", return_value=selection
        ) as native:
            result = QFileDialog.getOpenFileName(None, "Open raw data", r"C:\MRI data", "Twix (*.dat);;All files (*)", "Twix (*.dat)", options)
            self.assertEqual(result, selection)
            native.assert_called_once_with(None, "Open raw data", r"C:\MRI data", "Twix (*.dat);;All files (*)", "Twix (*.dat)", options)

    def test_save_keeps_default_filename_and_native_cancel_result(self):
        with patch.object(qt_dialogs, "_use_native_file_browser", return_value=True), patch.object(
            QtFileDialog, "getSaveFileName", return_value=("", "")
        ) as native:
            self.assertEqual(QFileDialog.getSaveFileName(None, "Export", r"C:\MRI data\noise.npz", "NPZ (*.npz)"), ("", ""))
            native.assert_called_once_with(None, "Export", r"C:\MRI data\noise.npz", "NPZ (*.npz)", "", QtFileDialog.Option(0))

    def test_native_backend_suppresses_decoration_until_it_returns(self):
        def choose(*args):
            self.assertGreater(qt_dialogs._constructing_dialogs, 0)
            raise RuntimeError("Backend failure")
        with patch.object(qt_dialogs, "_use_native_file_browser", return_value=True), patch.object(
            QtFileDialog, "getOpenFileName", side_effect=choose
        ), self.assertRaisesRegex(RuntimeError, "Backend failure"):
            QFileDialog.getOpenFileName()
        self.assertEqual(qt_dialogs._constructing_dialogs, 0)

    def test_headless_or_explicit_widget_dialog_keeps_the_fallback(self):
        with patch.object(APP, "platformName", return_value="windows"):
            self.assertTrue(qt_dialogs._use_native_file_browser(QtFileDialog.Option(0)))
            self.assertFalse(qt_dialogs._use_native_file_browser(QtFileDialog.Option.DontUseNativeDialog))
        with patch.object(APP, "platformName", return_value="offscreen"):
            self.assertFalse(qt_dialogs._use_native_file_browser(QtFileDialog.Option(0)))


if __name__ == "__main__":
    unittest.main()
