"""Scalable waveform identity for the raw-data desktop application."""

from pathlib import Path
from functools import lru_cache
import hashlib
import os
import struct
import tempfile

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .windows_shortcut import refresh_project_shortcuts


RESOURCE_DIRECTORY = Path(__file__).with_name("resources")
LOGO_FILE = RESOURCE_DIRECTORY.parent.parent / "logo.svg"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def logo_file():
    return LOGO_FILE if LOGO_FILE.is_file() else RESOURCE_DIRECTORY / "app-icon.svg"


@lru_cache(maxsize=8)
def _icon_from_svg(source):
    renderer = QSvgRenderer(QByteArray(source))
    if not renderer.isValid():
        raise ValueError("The logo must be a valid SVG.")
    icon = QIcon()
    for size in ICON_SIZES:
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(image)
    return icon


def application_icon() -> QIcon:
    """Read editable logo.svg afresh; reuse rasterizations of unchanged content."""
    try:
        return QIcon(_icon_from_svg(logo_file().read_bytes()))
    except (OSError, ValueError):
        return QIcon(_icon_from_svg((RESOURCE_DIRECTORY / "app-icon.svg").read_bytes()))


def update_windows_icon(icon):
    """Generate the ICO and give Windows shortcuts a fresh icon cache identity."""
    images = []
    for size in ICON_SIZES:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        # ICO entries describe physical pixels, independent of desktop scaling.
        icon.pixmap(QSize(size, size), 1.0).toImage().save(buffer, "PNG")
        images.append(bytes(data))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = []
    for size, data in zip(ICON_SIZES, images):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    data = header + b"".join(entries) + b"".join(images)
    path = RESOURCE_DIRECTORY / "app.ico"
    try:
        _write_icon(path, data)
    except OSError:
        return None
    project_root = RESOURCE_DIRECTORY.parent.parent
    if os.name == "nt" and any(project_root.glob("*.lnk")):
        try:
            # Explorer can retain the old image when app.ico is overwritten.
            # Keep old versions so copies of the shortcut keep a valid icon.
            cached = project_root / ".cache" / "icons" / f"app-{hashlib.sha256(data).hexdigest()[:20]}.ico"
            cached.parent.mkdir(parents=True, exist_ok=True)
            _write_icon(cached, data)
            path = cached
        except OSError:
            pass
        refresh_project_shortcuts(project_root, path)
    return path


def _write_icon(path, data):
    if path.is_file() and path.read_bytes() == data:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".ico.tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise
