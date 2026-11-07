"""Load the desktop's editable JSON palettes and Qt stylesheet assets."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette


RESOURCE_DIRECTORY = Path(__file__).with_name("resources")
THEME_DIRECTORY = RESOURCE_DIRECTORY / "themes"
THEME_CHOICES = (("cockpit", "Modern dark"), ("modern-light", "Modern light"),
                 ("light", "Classic light"), ("dark", "Classic dark"))
THEME_NAMES = tuple(name for name, _ in THEME_CHOICES)
MODERN_THEMES = frozenset(("cockpit", "modern-light"))
_TOKEN = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_BASE_COLORS = frozenset((
    "window", "panel", "base", "toolbar", "text", "muted", "border", "divider",
    "hover", "selected", "selected_text", "accent", "control", "alternate", "disabled",
    "shine", "shadow", "deep", "focus", "tab_inactive", "tab_text", "tab_hover",
))
_EXTRA_COLORS = frozenset((
    "caption_active", "caption_inactive", "caption_text", "caption_border", "caption_button",
    "caption_hover", "caption_pressed", "caption_close_hover", "caption_shine", "caption_deep",
    "glyph_foreground", "glyph_disabled", "plot_background", "plot_foreground", "plot_accent",
    "plot_secondary", "plot_axis", "plot_center", "block_container", "block_measurement",
    "block_header", "block_channel", "block_noise", "block_sync", "block_selected", "block_text",
    "block_canvas", "block_border",
    "plot_magnitude_0", "plot_magnitude_1", "plot_magnitude_2", "plot_magnitude_3", "plot_magnitude_4", "plot_magnitude_5",
    "plot_phase_0", "plot_phase_1", "plot_phase_2", "plot_phase_3", "plot_phase_4",
))


class ThemeAssetError(ValueError):
    """An editable theme file cannot be loaded or validated."""


@dataclass(frozen=True)
class ThemeAssets:
    name: str
    title: str
    colors: dict[str, str]
    font_family: str
    font_size: int
    stylesheet: str
    caption_stylesheet: str


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise ThemeAssetError(f"Unable to read theme asset {path}: {error}") from error


def _asset_path(directory, filename, field):
    if not isinstance(filename, str) or not filename or Path(filename).is_absolute():
        raise ThemeAssetError(f"{field} must name a .qss file inside {directory}")
    path = (directory / filename).resolve()
    if path.suffix.casefold() != ".qss" or not path.is_relative_to(directory.resolve()):
        raise ThemeAssetError(f"{field} must name a .qss file inside {directory}")
    return path


def _render_stylesheet(path, tokens):
    template = _read_text(path)
    unknown = sorted(set(_TOKEN.findall(template)) - tokens.keys())
    if unknown:
        raise ThemeAssetError(f"Unknown palette token in {path}: {', '.join(unknown)}")
    rendered = _TOKEN.sub(lambda match: tokens[match.group(1)], template)
    if "%(" in rendered:
        raise ThemeAssetError(f"Malformed palette token in {path}; use %(color_name)s")
    # Catch common accidental edits before Qt can discard the old style.
    rules = re.sub(r"/\*.*?\*/", "", rendered, flags=re.DOTALL)
    rules = re.sub(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', "", rules, flags=re.VERBOSE)
    depth = 0
    for character in rules:
        if character == "{":
            depth += 1
            if depth > 1:
                raise ThemeAssetError(f"Nested or unbalanced stylesheet braces in {path}")
        elif character == "}":
            depth -= 1
            if depth < 0:
                raise ThemeAssetError(f"Unbalanced stylesheet braces in {path}")
    if depth or "{" not in rules:
        raise ThemeAssetError(f"Missing or unbalanced stylesheet braces in {path}")
    return rendered


def load_theme(name="cockpit", theme_directory=None):
    """Read and validate fresh assets without changing the running application."""
    if name not in THEME_NAMES:
        raise ThemeAssetError(f"Unknown theme {name!r}; choose {', '.join(THEME_NAMES)}")
    directory = Path(theme_directory) if theme_directory is not None else THEME_DIRECTORY
    path = directory / f"{name}.json"
    try:
        document = json.loads(_read_text(path))
    except json.JSONDecodeError as error:
        raise ThemeAssetError(f"Invalid JSON in {path}, line {error.lineno}: {error.msg}") from error
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ThemeAssetError(f"{path} must be a theme object with schema_version 1")
    colors = document.get("colors")
    if not isinstance(colors, dict):
        raise ThemeAssetError(f"{path} needs a colors object")
    missing = sorted((_BASE_COLORS | _EXTRA_COLORS) - colors.keys())
    if missing:
        raise ThemeAssetError(f"Missing colors in {path}: {', '.join(missing)}")
    for key, value in colors.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ThemeAssetError(f"Invalid color token name in {path}: {key!r}")
        if not isinstance(value, str) or not _COLOR.fullmatch(value) or not QColor(value).isValid():
            raise ThemeAssetError(f"Invalid color {key!r} in {path}: use #RRGGBB")
    font = document.get("font")
    if not isinstance(font, dict) or not isinstance(font.get("family"), str) or not font["family"].strip():
        raise ThemeAssetError(f"{path} needs a font family")
    font_size = font.get("point_size")
    if type(font_size) is not int or not 6 <= font_size <= 32:
        raise ThemeAssetError(f"font.point_size in {path} must be an integer from 6 to 32")
    arrow_variant = document.get("arrow_variant")
    if arrow_variant not in ("light", "dark"):
        raise ThemeAssetError(f"arrow_variant in {path} must be light or dark")
    title = document.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ThemeAssetError(f"{path} needs a title")
    tokens = dict(colors)
    for direction in ("up", "down", "left", "right"):
        tokens[f"arrow_{direction}"] = (RESOURCE_DIRECTORY / f"arrow-{direction}-{arrow_variant}.svg").as_posix()
        tokens[f"arrow_{direction}_disabled"] = (RESOURCE_DIRECTORY / f"arrow-{direction}-disabled.svg").as_posix()
    checkmark = "checkmark-light.svg" if QColor(colors["accent"]).lightness() < 128 else "checkmark.svg"
    tokens["checkmark"] = (RESOURCE_DIRECTORY / checkmark).as_posix()
    stylesheet = _render_stylesheet(_asset_path(directory, document.get("stylesheet"), "stylesheet"), tokens)
    caption = _render_stylesheet(_asset_path(directory, document.get("caption_stylesheet"), "caption_stylesheet"), tokens)
    return ThemeAssets(name, title, dict(colors), font["family"], font_size, stylesheet, caption)


def theme_colors(name="cockpit", theme_directory=None):
    """Return a fresh copy of a theme's editable palette."""
    return load_theme(name, theme_directory).colors


def theme_stylesheet(name="cockpit", theme_directory=None):
    return load_theme(name, theme_directory).stylesheet


def apply_theme(app, name="cockpit", theme_directory=None):
    """Validate all referenced files before applying any palette or style change."""
    return apply_theme_assets(app, load_theme(name, theme_directory))


def apply_theme_assets(app, assets):
    """Apply one already-loaded snapshot after callers preflight other assets."""
    if not isinstance(assets, ThemeAssets):
        raise TypeError("assets must be a loaded ThemeAssets snapshot")
    colors = assets.colors
    palette = QPalette()
    for role, key in {
        QPalette.ColorRole.Window: "window", QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "base", QPalette.ColorRole.AlternateBase: "alternate",
        QPalette.ColorRole.Text: "text", QPalette.ColorRole.Button: "control",
        QPalette.ColorRole.ButtonText: "text", QPalette.ColorRole.Highlight: "selected",
        QPalette.ColorRole.Accent: "accent",
        QPalette.ColorRole.HighlightedText: "selected_text", QPalette.ColorRole.ToolTipBase: "base",
        QPalette.ColorRole.ToolTipText: "text", QPalette.ColorRole.PlaceholderText: "muted",
        QPalette.ColorRole.Light: "shine", QPalette.ColorRole.Mid: "border",
        QPalette.ColorRole.Midlight: "toolbar", QPalette.ColorRole.Dark: "shadow",
        QPalette.ColorRole.Shadow: "deep",
    }.items():
        palette.setColor(role, QColor(colors[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    font = QFont(assets.font_family, assets.font_size)
    app.setProperty("explorerTheme", assets.name)
    app.setProperty("explorerThemeColors", dict(colors))
    app.setPalette(palette)
    app.setFont(font)
    app.setStyleSheet(assets.stylesheet)
    if hasattr(app.styleHints(), "setColorScheme"):
        scheme = Qt.ColorScheme.Light if QColor(colors["window"]).lightness() >= 128 else Qt.ColorScheme.Dark
        app.styleHints().setColorScheme(scheme)
    return assets


# Compatibility snapshots for callers that only need the bundled initial colors.
# Reloadable UI code should use theme_colors/load_theme rather than these dicts.
LIGHT = theme_colors("light")
DARK = theme_colors("dark")
COCKPIT = theme_colors("cockpit")
