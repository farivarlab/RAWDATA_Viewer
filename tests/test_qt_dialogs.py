"""Modal results, cancellation, child prompts, and shared caption behavior."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from contextlib import contextmanager
import subprocess
import sys
import tempfile
import unittest

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox as QtMessageBox

from siemens_explorer.qt_dialogs import QFileDialog, QMessageBox
from siemens_explorer.qt_theme import apply_theme


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class ClassicDialogTests(unittest.TestCase):
    @contextmanager
    def modal_action(self, dialog_type, action, title=None):
        """Wait for the intended modal and fail instead of leaving exec blocked."""
        failures = []
        poll = QTimer()
        deadline = QTimer()
        deadline.setSingleShot(True)

        def dismiss_modals():
            # done() also dismisses message boxes that have no escape button.
            for widget in APP.topLevelWidgets():
                if isinstance(widget, QDialog) and widget.isVisible():
                    widget.done(QDialog.DialogCode.Rejected)

        def choose():
            dialog = APP.activeModalWidget()
            if not isinstance(dialog, dialog_type) or (title is not None and dialog.windowTitle() != title):
                return
            poll.stop()
            try:
                action(dialog)
            except BaseException as error:
                failures.append(error)
                dismiss_modals()

        def timed_out():
            poll.stop()
            failures.append(AssertionError(f"Timed out waiting for {dialog_type.__name__} modal action"))
            dismiss_modals()

        poll.timeout.connect(choose)
        deadline.timeout.connect(timed_out)
        poll.start(10)
        deadline.start(3000)
        try:
            yield
        finally:
            poll.stop()
            deadline.stop()
        if failures:
            raise failures[0]

    def tearDown(self):
        for widget in APP.topLevelWidgets():
            if isinstance(widget, QDialog):
                widget.close()
                widget.deleteLater()
        QTest.qWait(1)
        apply_theme(APP, "light")

    def test_message_caption_survives_content_changes_and_rejects_on_close(self):
        box = QMessageBox()
        box.setWindowTitle("Preview failed")
        box.setText("A long message that updates the native message layout.")
        box.setDetailedText("Original diagnostic detail")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.show()
        APP.processEvents()
        self.assertTrue(box.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertIs(box.layout().menuBar(), box.title_bar)
        self.assertEqual(box.title_bar.title.text(), "Preview failed")
        self.assertTrue(box.title_bar.isVisible())
        self.assertFalse(box.title_bar.minimize_button.isVisible())
        self.assertFalse(box.title_bar.maximize_button.isVisible())
        box.title_bar.close_button.click()
        APP.processEvents()
        self.assertEqual(box.result(), QMessageBox.StandardButton.No)
        self.assertFalse(box.isVisible())

    def test_information_and_question_helpers_preserve_modal_button_results(self):
        observed = []
        for helper, button in ((QMessageBox.information, QMessageBox.StandardButton.Ok),
                               (QMessageBox.question, QMessageBox.StandardButton.No)):
            def choose(dialog, button=button):
                observed.append((isinstance(dialog, QMessageBox), dialog.title_bar.isVisible(),
                                 dialog.textFormat(), dialog.title_bar.title.text()))
                dialog.button(button).click()
            with self.modal_action(QMessageBox, choose, "Message"):
                result = helper(None, "Message", "Text with <literal> values")
            self.assertEqual(result, button)
        self.assertEqual(observed, [(True, True, Qt.TextFormat.PlainText, "Message")] * 2)

    def test_file_cancel_returns_empty_selection_and_uses_classic_caption(self):
        observed = []
        def cancel(dialog):
            observed.append((dialog.testOption(QFileDialog.Option.DontUseNativeDialog),
                             dialog.title_bar.isVisible(), dialog.title_bar.maximize_button.isVisible()))
            dialog.title_bar.close_button.click()
        with self.modal_action(QFileDialog, cancel, "Open raw data"):
            self.assertEqual(QFileDialog.getOpenFileName(None, "Open raw data"), ("", ""))
        self.assertEqual(observed, [(True, True, True)])

    def test_modal_callback_failure_dismisses_dialog_instead_of_hanging(self):
        def fail(_dialog):
            raise RuntimeError("Modal callback failed")
        with self.assertRaisesRegex(RuntimeError, "Modal callback failed"):
            with self.modal_action(QMessageBox, fail, "Callback failure"):
                QMessageBox.information(None, "Callback failure", "A test callback will dismiss this dialog.")

    def test_open_and_save_helpers_preserve_paths_with_spaces_and_filters(self):
        with tempfile.TemporaryDirectory(prefix="dialog test ") as directory:
            source = Path(directory) / "measurement file.dat"
            source.write_bytes(b"test")
            destination = Path(directory) / "export file.npz"
            for helper, path, name_filter in (
                (QFileDialog.getOpenFileName, source, "Raw data (*.dat)"),
                (QFileDialog.getSaveFileName, destination, "Preview (*.npz)"),
            ):
                def accept(dialog, path=path):
                    dialog.selectFile(str(path))
                    dialog.accept()
                with self.modal_action(QFileDialog, accept, "Select file"):
                    selected, selected_filter = helper(None, "Select file", str(path), name_filter)
                # Qt expands Windows 8.3 aliases, including CI's RUNNER~1.
                self.assertEqual(Path(selected).resolve(), path.resolve())
                self.assertEqual(selected_filter, name_filter)
            self.assertFalse(destination.exists())

    def test_qt_created_child_prompts_also_receive_the_shared_caption(self):
        chooser = QFileDialog()
        chooser.show()
        APP.processEvents()
        prompt = QInputDialog(chooser)
        prompt.setWindowTitle("New folder")
        prompt.setLabelText("Folder name")
        prompt.show()
        APP.processEvents()
        self.assertTrue(prompt.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertIs(prompt.layout().menuBar(), prompt.title_bar)
        self.assertTrue(prompt.title_bar.isVisible())
        prompt.title_bar.close_button.click()
        APP.processEvents()
        self.assertEqual(prompt.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(chooser.isVisible())

    def test_file_overwrite_confirmation_uses_classic_caption_and_can_be_declined(self):
        observed = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.npz"
            path.write_bytes(b"unchanged")
            chooser = QFileDialog(None, "Save preview", str(path))
            chooser.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            chooser.setFileMode(QFileDialog.FileMode.AnyFile)
            def decline(prompt):
                observed.append((prompt.title_bar.isVisible(), prompt.windowFlags() & Qt.WindowType.FramelessWindowHint))
                prompt.button(QMessageBox.StandardButton.No).click()
                QTimer.singleShot(0, chooser.reject)
            QTimer.singleShot(0, chooser.accept)
            with self.modal_action(QtMessageBox, decline):
                chooser.exec()
            self.assertEqual(observed, [(True, Qt.WindowType.FramelessWindowHint)])
            self.assertEqual(path.read_bytes(), b"unchanged")

    def test_caption_theme_switch_changes_existing_and_new_dialogs(self):
        box = QMessageBox()
        for name, expected in (("cockpit", "#eeeeee"), ("modern-light", "#252525"),
                               ("light", "#000000"), ("dark", "#000000")):
            APP.setProperty("explorerTheme", name)
            box.title_bar.set_theme(name)
            fresh = QMessageBox()
            fresh.show()
            box.show()
            APP.processEvents()
            for dialog in (box, fresh):
                self.assertEqual(dialog.title_bar.title.palette().windowText().color().name(), expected)
            fresh.close()
            fresh.deleteLater()
        APP.setProperty("explorerTheme", "light")

    def test_popup_lifetime_does_not_emit_errors_during_interpreter_shutdown(self):
        code = """
from PySide6.QtWidgets import QApplication, QInputDialog
from siemens_explorer.qt_dialogs import QFileDialog, QMessageBox
app = QApplication([])
chooser = QFileDialog()
chooser.show()
app.processEvents()
prompt = QInputDialog(chooser)
prompt.show()
box = QMessageBox()
box.show()
app.processEvents()
prompt.close()
box.close()
chooser.close()
app.processEvents()
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("already deleted", result.stderr)

    @unittest.skipUnless(sys.platform == "win32", "Native Windows dialog frame")
    def test_windows_file_dialog_retains_resize_and_maximize_after_constructor_polish(self):
        code = """
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from siemens_explorer.qt_dialogs import QFileDialog, QMessageBox
from siemens_explorer.qt_chrome import _windows_api, native_frame_event
app = QApplication([])
seed = QMessageBox()
seed.setText('An earlier error installs the popup filter')
seed.show()
app.processEvents()
seed.close()
seed.deleteLater()
QTest.qWait(1)
chooser = QFileDialog(None, 'Open raw data')
chooser.show()
QTest.qWait(50)
ctypes, wintypes, user32, get_style, _, MonitorInfo, _ = _windows_api()
hwnd = int(chooser.winId())
assert chooser._classic_resizable and chooser._classic_allow_maximize
style = get_style(hwnd, -16)
assert style & 0x40000 and style & 0x10000 and not style & 0x20000
rect = wintypes.RECT()
assert user32.GetWindowRect(hwnd, ctypes.byref(rect))
message = wintypes.MSG()
message.hWnd = hwnd
message.message = 0x84
message.lParam = ((rect.top + 1) & 0xffff) << 16 | ((rect.left + 1) & 0xffff)
assert native_frame_event(chooser, ctypes.addressof(message)) == (True, 13)
info = MonitorInfo()
info.cbSize = ctypes.sizeof(info)
assert user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
chooser.showMaximized()
QTest.qWait(50)
assert user32.GetWindowRect(hwnd, ctypes.byref(rect))
assert (rect.left, rect.top, rect.right, rect.bottom) == (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom)
chooser.showNormal()
box = QMessageBox(chooser)
box.setText('Test error')
box.show()
QTest.qWait(30)
assert not get_style(int(box.winId()), -16) & 0x40000
assert box.title_bar.isVisible() and not box.title_bar.maximize_button.isVisible()
box.close()
chooser.close()
app.processEvents()
"""
        environment = dict(os.environ, QT_QPA_PLATFORM="windows")
        result = subprocess.run([sys.executable, "-c", code], env=environment,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
