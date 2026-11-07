param(
    [string]$DataFile = "",
    [ValidateSet("light", "dark", "cockpit", "modern-light")][string]$Theme
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$logDirectory = Join-Path $PSScriptRoot ".cache"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$launcherLog = Join-Path $logDirectory "launcher.log"
try {
    $envPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $envPython)) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            & $launcher.Source -3 -m venv .venv *> $launcherLog
        } else {
            $launcher = Get-Command python -ErrorAction SilentlyContinue
            if (-not $launcher) { throw "Install Python 3.10 or newer, then run the launcher again." }
            & $launcher.Source -m venv .venv *> $launcherLog
        }
        if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment. Install Python 3.10 or newer." }
    }
    & $envPython -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(p) for p in ('PySide6','pyqtgraph','numpy','pandas','ismrmrd')) else 1)"
    if ($LASTEXITCODE -ne 0) {
        & $envPython -m pip --disable-pip-version-check install -r (Join-Path $PSScriptRoot "requirements.txt") *> $launcherLog
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Details: $launcherLog" }
    }
    $guiPython = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path -LiteralPath $guiPython)) { throw "The Python environment does not contain pythonw.exe. Recreate .venv with a Windows Python installation." }
    # The normal desktop entry starts the GUI interpreter directly, avoiding
    # both a console window and the bootstrap check on subsequent launches.
    $shortcutShell = New-Object -ComObject WScript.Shell
    $legacyShortcut = Join-Path $PSScriptRoot "Raw Data Explorer.lnk"
    $namedShortcut = Join-Path $PSScriptRoot "Raw MRI Data Explorer.lnk"
    if ((Test-Path -LiteralPath $legacyShortcut) -and -not (Test-Path -LiteralPath $namedShortcut)) {
        Move-Item -LiteralPath $legacyShortcut -Destination $namedShortcut
    }
    $shortcut = $shortcutShell.CreateShortcut((Join-Path $PSScriptRoot "Raw MRI Data Explorer.lnk"))
    $shortcut.TargetPath = $guiPython
    $shortcut.Arguments = "-m siemens_explorer"
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = (Join-Path $PSScriptRoot "siemens_explorer\resources\app.ico") + ",0"
    $shortcut.Description = "Open Raw MRI Data Explorer without a terminal"
    $shortcut.Save()
    $appArgs = @("-m", "siemens_explorer")
    if ($Theme) { $appArgs += @("--theme", $Theme) }
    if ($DataFile) { $appArgs += $DataFile }
    $quotedArgs = @($appArgs | ForEach-Object {
        if ($_ -match '"') { throw "A launch argument contains an invalid quote." }
        '"' + $_ + '"'
    }) -join ' '
    Start-Process -FilePath $guiPython -ArgumentList $quotedArgs -WorkingDirectory $PSScriptRoot -WindowStyle Normal -ErrorAction Stop | Out-Null
} catch {
    $_ | Out-String | Add-Content -LiteralPath $launcherLog
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show("The viewer could not start.`n`n$($_.Exception.Message)`n`nDetails: $launcherLog", "Raw MRI Data Explorer", 'OK', 'Error') | Out-Null
    exit 1
}
