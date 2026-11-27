# PyInstaller spec for the standalone Windows build used by the Scoop manifest.
#
# Build from the repository root with the project's virtual environment:
#   .venv\Scripts\python.exe -m pip install pyinstaller
#   .venv\Scripts\python.exe -m PyInstaller packaging/pyinstaller/raw-mri-data-explorer.spec --noconfirm
#
# The result is a one-folder bundle in dist/Raw MRI Data Explorer/. A folder
# (rather than a one-file exe) starts faster because nothing is unpacked to a
# temporary directory on every launch.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
NAME = "Raw MRI Data Explorer"

datas = collect_data_files("siemens_explorer", includes=["resources/**/*"])
# ismrmrd validates its XML header against a bundled schema at runtime.
datas += collect_data_files("ismrmrd")
hiddenimports = collect_submodules("siemens_explorer") + collect_submodules("ismrmrd")

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    # Qt modules the viewer never loads; excluding them trims the bundle.
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtQuick", "PySide6.QtQml", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = ROOT / "siemens_explorer" / "resources" / "app.ico"
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=str(icon) if icon.is_file() else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
