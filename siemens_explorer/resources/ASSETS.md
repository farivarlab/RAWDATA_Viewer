# Editable appearance assets

The application reads its appearance from these files; changing Python code is
not required for palette, typography, spacing, borders, or glyph artwork.

## Themes

`themes/cockpit.json` defines Modern dark; `themes/modern-light.json` defines
Modern light. They share `cockpit.qss` and `caption-cockpit.qss` for consistent
control sizes. `themes/light.json` and `themes/dark.json` define the classic
appearances. Each JSON names a global stylesheet and a title-bar stylesheet.

- `colors`: named `#RRGGBB` values. Keep the existing keys; extra color keys are
  allowed. `caption_*` controls window bars, `plot_*` controls data views,
  `block_*` controls block diagrams, and `glyph_*` supplies glyph colors.
- `font.family` and `font.point_size`: desktop typeface and size in points.
- `arrow_variant`: `light` or `dark`, selecting the existing arrow SVG artwork.
- `stylesheet` and `caption_stylesheet`: relative `.qss` filenames kept inside
  `themes/`. Classic light and dark share the classic templates.

Modern block maps use muted blue for measurements, purple for headers, green
for channel samples, and amber for noise, with neutral container/sync blocks.
Their `block_*` colors are independently editable in each modern theme JSON.

QSS is Qt's CSS-like stylesheet format. A token such as `%(accent)s` uses the
matching color from JSON. Spacing, padding, borders, corner radii, and control
sizes are editable directly in QSS. Arrow path tokens and `%(checkmark)s` are
provided automatically. Keep the exact token spelling and balanced rule braces.

For example, change `colors.accent` in `cockpit.json` to adjust its muted teal accent,
or change the `QToolButton, QPushButton` rule in `cockpit.qss` to adjust button
padding. Keep readable contrast and enough button area for mouse input.
The one-pixel dock-panel outlines use `colors.border`, including floating windows.

After saving edits, use the application's theme-asset reload command or restart
the app. Reload reads the files afresh. Invalid JSON, missing colors/templates,
bad color values, unknown tokens, and unbalanced QSS braces produce a filename
and error explanation; validation occurs before the current theme is changed.

## Glyphs and identity

SVG artwork is editable in a text editor or vector editor. Keep its viewBox and
transparent background so it remains sharp at different display scales. The
waveform `app-icon.svg` is used by the app; `app.ico` is its multi-size Windows
shortcut/taskbar counterpart and must be regenerated if the logo changes.

Directional arrows, `checkmark.svg`, and `checkmark-light.svg` remain editable
SVGs in this directory. The checkmark switches between dark and light artwork
to contrast with the selected accent color.
Application-specific action and window glyphs live under `glyphs/` when present;
their `currentColor` strokes follow `glyph_foreground` and `glyph_disabled` in the
selected JSON palette. The SVG root color makes them visible in ordinary vector
editors too. Saving changes and using theme-asset reload refreshes the artwork.

## Editable application logo

Edit `logo.svg` at the project root. The program rereads this SVG on launch and
on **Reload appearance assets**; window captions, About dialogs, splash screens,
and application icons use it. The multi-size Windows ICO is generated from the
same artwork. Matching project-folder Windows shortcuts are repointed to a
versioned icon in `.cache/icons` and Explorer is notified on launch/reload.
Keep the SVG self-contained with a viewBox and transparency.
