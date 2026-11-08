"""Changed artwork gets a new Windows icon identity without changing launch options."""

import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from siemens_explorer import qt_branding, windows_shortcut


APP = QApplication.instance() or QApplication([])


@unittest.skipUnless(os.name == "nt", "Windows shortcut integration")
class ShortcutIconTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "viewer's café project"
        self.resources = self.root / "siemens_explorer" / "resources"
        self.resources.mkdir(parents=True)
        self.logo = self.root / "logo.svg"
        self.resources_patch = patch.object(qt_branding, "RESOURCE_DIRECTORY", self.resources)
        self.resources_patch.start()
        self.addCleanup(self.resources_patch.stop)
        self.logo_patch = patch.object(qt_branding, "LOGO_FILE", self.logo)
        self.logo_patch.start()
        self.addCleanup(self.logo_patch.stop)

    def icon(self, color):
        self.logo.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="{color}"/></svg>')
        return qt_branding.application_icon()

    def powershell(self, command):
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n" + command],
            env=dict(os.environ, RDX_ICON_PROJECT_ROOT=str(self.root)),
            creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout.decode("utf-8-sig").strip()

    def test_changed_logo_versions_icon_and_unchanged_reload_reuses_it(self):
        (self.root / "Raw MRI Data Explorer.lnk").touch()
        with patch.object(qt_branding, "refresh_project_shortcuts") as refresh:
            first = qt_branding.update_windows_icon(self.icon("#bb3355"))
            before = first.stat().st_mtime_ns
            same = qt_branding.update_windows_icon(self.icon("#bb3355"))
            self.assertEqual(first, same)
            self.assertEqual(first.stat().st_mtime_ns, before)
            second = qt_branding.update_windows_icon(self.icon("#3377aa"))
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_file())
            self.assertEqual(second.read_bytes(), (self.resources / "app.ico").read_bytes())
            self.assertEqual(qt_branding.QIcon(str(second)).pixmap(32, 32).toImage().pixelColor(16, 16).name(), "#3377aa")
            refresh.assert_called_with(self.root, second)

    def test_real_shortcut_updates_only_icon_and_notifies_shell_once(self):
        qt_branding.update_windows_icon(self.icon("#bb3355"))
        target = self.root / ".venv" / "Scripts" / "pythonw.exe"
        target.parent.mkdir(parents=True)
        target.touch()
        self.powershell(r"""
        $root = $env:RDX_ICON_PROJECT_ROOT
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut((Join-Path $root 'Raw MRI Data Explorer.lnk'))
        $link.TargetPath = Join-Path $root '.venv\Scripts\pythonw.exe'
        $link.Arguments = '-m siemens_explorer --windowed'
        $link.WorkingDirectory = $root
        $link.Description = 'Keep my launch options'
        $link.IconLocation = (Join-Path $root 'siemens_explorer\resources\app.ico') + ',0'
        $link.Save()
        $other = $shell.CreateShortcut((Join-Path $root 'Other.lnk'))
        $other.TargetPath = Join-Path $env:WINDIR 'notepad.exe'
        $other.Save()
        """)
        unrelated = (self.root / "Other.lnk").read_bytes()
        with patch.object(windows_shortcut, "_notify_shortcut") as notify:
            path = qt_branding.update_windows_icon(self.icon("#3377aa"))
            notify.assert_called_once_with(str(self.root / "Raw MRI Data Explorer.lnk"))
            notify.reset_mock()
            self.assertEqual(windows_shortcut.refresh_project_shortcuts(self.root, path), [])
            notify.assert_not_called()
        result = json.loads(self.powershell(r"""
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $env:RDX_ICON_PROJECT_ROOT 'Raw MRI Data Explorer.lnk'))
        @{icon=$link.IconLocation; target=$link.TargetPath; arguments=$link.Arguments; directory=$link.WorkingDirectory; description=$link.Description} | ConvertTo-Json -Compress
        """))
        self.assertEqual(result["icon"], str(path) + ",0")
        self.assertEqual(Path(result["target"]), target)
        self.assertEqual(result["arguments"], "-m siemens_explorer --windowed")
        self.assertEqual(Path(result["directory"]), self.root)
        self.assertEqual(result["description"], "Keep my launch options")
        self.assertEqual((self.root / "Other.lnk").read_bytes(), unrelated)

    def test_read_only_icon_does_not_prevent_application_startup(self):
        with patch.object(qt_branding, "_write_icon", side_effect=PermissionError("read only")), patch.object(
            qt_branding, "refresh_project_shortcuts"
        ) as refresh:
            self.assertIsNone(qt_branding.update_windows_icon(self.icon("#3377aa")))
            refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
