param(
    [string]$DataFile = "",
    [ValidateSet("light", "dark")][string]$Theme = "light"
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$envPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $envPython)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -m venv .venv
    } else {
        $launcher = Get-Command python -ErrorAction SilentlyContinue
        if (-not $launcher) { throw "Install Python 3.10 or newer, then run start.ps1 again." }
        & $launcher.Source -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment. Install Python 3.10 or newer." }
}
& $envPython -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(p) for p in ('PySide6','pyqtgraph','numpy','pandas')) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $envPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. See the pip output above." }
}
$appArgs = @("-m", "siemens_explorer", "--theme", $Theme)
if ($DataFile) { $appArgs += $DataFile }
& $envPython @appArgs
