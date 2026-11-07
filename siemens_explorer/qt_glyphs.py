"""Original, editable SVG controls rendered in the current theme's colors.

SVG files are valid standalone artwork. Their ``currentColor`` strokes are
resolved from the theme at load time; geometry can be edited without Python.
"""

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
import re

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


GLYPH_DIRECTORY = Path(__file__).with_name("resources") / "glyphs"
GLYPH_NAMES = (
    "open", "close", "reindex", "export", "browse", "previous", "next",
    "fit", "auto-levels", "minimize", "maximize", "restore",
)


def _color(value, label):
    color = QColor(value)
    if not color.isValid():
        raise ValueError(f"Invalid {label} glyph color: {value!r}")
    return color.name()


def _theme_colors(theme):
    if isinstance(theme, Mapping):
        return theme
    app = QApplication.instance()
    if theme is None and app is not None and app.property("explorerThemeColors"):
        return app.property("explorerThemeColors")
    name = theme or (app.property("explorerTheme") if app is not None else None) or "cockpit"
    from . import qt_theme
    return qt_theme.theme_colors(name)


@lru_cache(maxsize=128)
def _render_icon(source, normal, disabled):
    icon = QIcon()
    # Explicit disabled artwork avoids the platform style washing light glyphs
    # into the dark panel. Multiple sizes also keep fractional-DPI controls crisp.
    for mode, color in ((QIcon.Mode.Normal, normal), (QIcon.Mode.Disabled, disabled)):
        renderer = QSvgRenderer(QByteArray(source.replace("currentColor", color).encode("utf-8")))
        if not renderer.isValid():
            raise ValueError("Invalid SVG glyph artwork")
        for size in (16, 18, 20, 22, 24, 26, 32, 48, 64, 128):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


def glyph_icon(name, theme=None, *, color=None, disabled_color=None, background=None):
    """Return an original glyph using a theme name or an explicit palette mapping.

    ``color`` and ``disabled_color`` override theme colors. ``background`` is
    accepted for caption compatibility; the artwork itself stays transparent.
    Reading the SVG on each request makes saved edits available on theme reload,
    while unchanged source/color combinations reuse their rendered icon.
    """
    if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9-]*", name) is None:
        raise ValueError(f"Invalid glyph name: {name!r}")
    path = GLYPH_DIRECTORY / f"{name}.svg"
    colors = _theme_colors(theme)
    normal = _color(color or colors.get("glyph_foreground", colors["text"]), "foreground")
    disabled = _color(disabled_color or colors.get("glyph_disabled", colors["disabled"]), "disabled")
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise OSError(f"Unable to read glyph asset {path}: {error}") from error
    try:
        return QIcon(_render_icon(source, normal, disabled))
    except ValueError as error:
        raise ValueError(f"Invalid glyph asset {path}") from error


def clear_glyph_cache():
    """Discard rendered variants before reloading edited assets."""
    _render_icon.cache_clear()
