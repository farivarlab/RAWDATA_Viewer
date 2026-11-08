"""Refresh this project's Windows shortcuts without clearing the shell cache."""

import ctypes
import json
import os
from pathlib import Path
import subprocess


_UPDATE_SHORTCUTS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$root = $env:RDX_ICON_PROJECT_ROOT
$icon = $env:RDX_ICON_FILE + ',0'
$target = Join-Path $root '.venv\Scripts\pythonw.exe'
$shell = New-Object -ComObject WScript.Shell
$updated = @()
foreach ($file in Get-ChildItem -LiteralPath $root -Filter '*.lnk' -File) {
    $link = $shell.CreateShortcut($file.FullName)
    if ($link.TargetPath -ine $target -or $link.Arguments -notmatch '(^|\s)-m\s+siemens_explorer(\s|$)') { continue }
    if ($link.IconLocation -eq $icon) { continue }
    $link.IconLocation = $icon
    $link.Save()
    $updated += $file.FullName
}
ConvertTo-Json -InputObject @($updated) -Compress
"""


def _notify_shortcut(path):
    # SHCNE_UPDATEITEM / SHCNF_PATHW | SHCNF_FLUSHNOWAIT: only this item changed.
    notify = ctypes.WinDLL("shell32").SHChangeNotify
    notify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_void_p]
    notify.restype = None
    notify(0x2000, 0x0005 | 0x2000, str(path), None)


def refresh_project_shortcuts(project_root, icon_path):
    """Keep matching local .lnk files' launch options and replace only their icon."""
    if os.name != "nt":
        return []
    project_root = Path(project_root).resolve()
    try:
        if not any(project_root.glob("*.lnk")):
            return []
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _UPDATE_SHORTCUTS],
            env=dict(os.environ, RDX_ICON_PROJECT_ROOT=str(project_root), RDX_ICON_FILE=str(Path(icon_path).resolve())),
            creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True, timeout=8, check=True,
        )
        updated = json.loads(result.stdout.decode("utf-8-sig"))
        for path in updated:
            _notify_shortcut(path)
        return [Path(path) for path in updated]
    except (OSError, ValueError, subprocess.SubprocessError):
        # Shortcut maintenance must not prevent opening a read-only installation.
        return []
