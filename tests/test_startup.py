"""Startup visibility, error reporting, and Windows GUI-launch arguments."""

import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from siemens_explorer.qt_dialogs import QMessageBox

from siemens_explorer import __main__ as entry
from siemens_explorer.startup import create_splash, report_startup_error


APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.preferences_directory = tempfile.TemporaryDirectory()
        self.preferences = QSettings(str(Path(self.preferences_directory.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.settings_patch = patch("siemens_explorer.preferences.open_settings", return_value=self.preferences)
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.preferences_directory.cleanup()

    def test_startup_screen_paints_before_main_window_load(self):
        splash = create_splash(APP)
        try:
            splash.show()
            APP.processEvents()
            self.assertTrue(splash.isVisible())
            self.assertFalse(splash.pixmap().isNull())
            self.assertLess(splash.width(), 400)
            self.assertLess(splash.height(), 160)
            self.assertEqual(splash.objectName(), "startupSplash")
        finally:
            splash.close()
            splash.deleteLater()

    def test_windowless_failure_is_logged_and_explained_in_a_dialog(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(QMessageBox, "critical") as dialog, patch.object(sys, "stderr", None):
            output = report_startup_error(RuntimeError("Unable to load imaging components"), directory)
            self.assertIn("Unable to load imaging components", output.read_text(encoding="utf-8"))
            dialog.assert_called_once()
            self.assertIn(str(output), dialog.call_args.args[2])

    def test_constructor_failure_closes_splash_and_reports_error(self):
        from siemens_explorer import qt_app
        for theme in ("cockpit", "modern-light"):
            splash = create_splash(APP, theme)
            with patch("siemens_explorer.startup.create_splash", return_value=splash), patch.object(
                qt_app, "MainWindow", side_effect=RuntimeError("Bad startup configuration")
            ) as constructor, patch("siemens_explorer.startup.report_startup_error") as report:
                self.assertEqual(entry.main(["--windowed", "--theme", theme]), 1)
                self.assertEqual(constructor.call_args.kwargs["theme"], theme)
                self.assertFalse(splash.isVisible())
                self.assertIn("Bad startup configuration", str(report.call_args.args[0]))
            splash.deleteLater()

    @unittest.skipUnless(sys.platform == "win32", "Windows GUI launcher")
    def test_launcher_uses_pythonw_and_preserves_a_path_with_spaces(self):
        command = r'''
        $script:RDX_IconBefore = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $env:RDX_PROJECT_ROOT 'Raw MRI Data Explorer.lnk')).IconLocation
        function Start-Process {
            [CmdletBinding()]
            param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle)
            $iconAfter = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $env:RDX_PROJECT_ROOT 'Raw MRI Data Explorer.lnk')).IconLocation
            @{ executable=$FilePath; arguments=$ArgumentList; directory=$WorkingDirectory; style=$WindowStyle; iconBefore=$script:RDX_IconBefore; iconAfter=$iconAfter } |
                ConvertTo-Json | Set-Content -LiteralPath $env:RDX_LAUNCH_CAPTURE -Encoding UTF8
        }
        $launchOptions = @{DataFile='C:\MRI data\measurement file.dat'}
        if ($env:RDX_LAUNCH_THEME) { $launchOptions.Theme = $env:RDX_LAUNCH_THEME }
        & (Join-Path $env:RDX_PROJECT_ROOT 'start.ps1') @launchOptions
        '''
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "launch.json"
            for theme in ("", "dark"):
                env = dict(os.environ, RDX_PROJECT_ROOT=str(ROOT), RDX_LAUNCH_CAPTURE=str(capture), RDX_LAUNCH_THEME=theme)
                result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                                        env=env, capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stderr)
                launch = json.loads(capture.read_text(encoding="utf-8-sig"))
                self.assertTrue(launch["executable"].endswith("pythonw.exe"))
                self.assertEqual(Path(launch["directory"]), ROOT)
                self.assertIn('"C:\\MRI data\\measurement file.dat"', launch["arguments"])
                if launch["iconBefore"]:
                    self.assertEqual(launch["iconAfter"], launch["iconBefore"])
                if theme:
                    self.assertIn('"--theme" "dark"', launch["arguments"])
                else:
                    self.assertNotIn("--theme", launch["arguments"])

    def test_saved_theme_is_used_for_splash_and_explicit_launch_can_override_it(self):
        from siemens_explorer import qt_app
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("appearance/theme", "modern-light")
            for arguments, expected in ((["--windowed"], "modern-light"), (["--theme", "dark"], "dark")):
                with patch("siemens_explorer.preferences.open_settings", return_value=settings), patch(
                    "siemens_explorer.startup.create_splash"
                ) as splash, patch.object(qt_app, "MainWindow") as constructor, patch.object(APP, "exec", return_value=0):
                    self.assertEqual(entry.main(arguments), 0)
                    self.assertEqual(APP.property("explorerTheme"), expected)
                    self.assertEqual(splash.call_args.args[1], expected)
                    self.assertEqual(constructor.call_args.kwargs["theme"], expected)
                    self.assertIs(constructor.call_args.kwargs["settings"], settings)
                    constructor.return_value.show_startup.assert_called_once_with(windowed="--windowed" in arguments)


if __name__ == "__main__":
    unittest.main()
