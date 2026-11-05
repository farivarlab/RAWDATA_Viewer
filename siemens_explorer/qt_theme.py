"""Classic silver-gray desktop controls with Windows 95-style bevels."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette


LIGHT = dict(window="#c0c0c0", panel="#c0c0c0", base="#ffffff", toolbar="#c0c0c0",
             text="#000000", muted="#505050", border="#808080", divider="#808080",
             hover="#d4d4d4", selected="#000080", selected_text="#ffffff", accent="#000080",
             control="#c0c0c0", alternate="#f0f0f0", disabled="#808080",
             shine="#ffffff", shadow="#808080", deep="#404040", focus="#000000")
DARK = dict(window="#303030", panel="#383838", base="#1b1b1b", toolbar="#383838",
            text="#eeeeee", muted="#c0c0c0", border="#777777", divider="#606060",
            hover="#484848", selected="#000080", selected_text="#ffffff", accent="#b8cfff",
            control="#383838", alternate="#252525", disabled="#999999",
            shine="#777777", shadow="#202020", deep="#101010", focus="#ffffff")


def apply_theme(app, name="light"):
    colors = DARK if name == "dark" else LIGHT
    palette = QPalette()
    for role, key in {
        QPalette.ColorRole.Window: "window", QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "base", QPalette.ColorRole.AlternateBase: "alternate",
        QPalette.ColorRole.Text: "text", QPalette.ColorRole.Button: "control",
        QPalette.ColorRole.ButtonText: "text", QPalette.ColorRole.Highlight: "selected",
        QPalette.ColorRole.HighlightedText: "selected_text", QPalette.ColorRole.ToolTipBase: "base",
        QPalette.ColorRole.ToolTipText: "text", QPalette.ColorRole.PlaceholderText: "muted",
        QPalette.ColorRole.Light: "shine", QPalette.ColorRole.Mid: "border",
        QPalette.ColorRole.Midlight: "toolbar", QPalette.ColorRole.Dark: "shadow",
        QPalette.ColorRole.Shadow: "deep",
    }.items():
        palette.setColor(role, QColor(colors[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    app.setPalette(palette)
    app.setFont(QFont("Tahoma", 9))
    app.setStyleSheet("""
    QMainWindow { background: %(panel)s; }
    QMenuBar, QMenu { background: %(control)s; color: %(text)s; }
    QMenuBar::item { padding: 3px 7px; }
    QMenuBar::item:selected, QMenu::item:selected { background: %(selected)s; color: %(selected_text)s; }
    QMenu::item { padding: 4px 25px 4px 8px; }
    QMenu { border-top: 2px solid %(shine)s; border-left: 2px solid %(shine)s; border-right: 2px solid %(deep)s; border-bottom: 2px solid %(deep)s; }
    QToolBar { background: %(toolbar)s; border-top: 1px solid %(shine)s; border-bottom: 1px solid %(shadow)s; spacing: 3px; padding: 3px; }
    QToolBar::separator { width: 2px; border-left: 1px solid %(shadow)s; border-right: 1px solid %(shine)s; margin: 3px; }
    QToolButton, QPushButton { color: %(text)s; background: %(control)s; border-top: 2px solid %(shine)s; border-left: 2px solid %(shine)s; border-right: 2px solid %(deep)s; border-bottom: 2px solid %(deep)s; border-radius: 0; padding: 3px 7px; }
    QToolButton:hover, QPushButton:hover { background: %(hover)s; }
    QToolButton:pressed, QPushButton:pressed, QToolButton:checked { border-top-color: %(deep)s; border-left-color: %(deep)s; border-right-color: %(shine)s; border-bottom-color: %(shine)s; }
    QToolButton:disabled, QPushButton:disabled { color: %(disabled)s; }
    QPushButton:default { outline: 1px solid %(focus)s; }
    QLineEdit, QComboBox, QSpinBox { background: %(base)s; color: %(text)s; padding: 3px; border-top: 2px solid %(shadow)s; border-left: 2px solid %(shadow)s; border-right: 2px solid %(shine)s; border-bottom: 2px solid %(shine)s; border-radius: 0; selection-background-color: %(selected)s; selection-color: %(selected_text)s; }
    QComboBox QAbstractItemView { background: %(base)s; color: %(text)s; selection-background-color: %(selected)s; selection-color: %(selected_text)s; }
    QDockWidget::title { padding: 4px 7px; background: %(panel)s; color: %(text)s; border-top: 1px solid %(shine)s; border-bottom: 1px solid %(shadow)s; font-weight: bold; }
    QGroupBox { border-top: 1px solid %(shadow)s; border-left: 1px solid %(shadow)s; border-bottom: 1px solid %(shine)s; border-right: 1px solid %(shine)s; border-radius: 0; margin-top: 9px; padding: 7px; }
    QGroupBox::title { subcontrol-origin: margin; left: 6px; }
    QTreeView, QTableView, QListView, QPlainTextEdit { background: %(base)s; color: %(text)s; border-top: 2px solid %(shadow)s; border-left: 2px solid %(shadow)s; border-right: 2px solid %(shine)s; border-bottom: 2px solid %(shine)s; alternate-background-color: %(alternate)s; gridline-color: %(divider)s; selection-background-color: %(selected)s; selection-color: %(selected_text)s; }
    QTreeView::item, QTreeWidget::item { padding: 3px; }
    QHeaderView::section { background: %(panel)s; color: %(text)s; padding: 4px; border-top: 1px solid %(shine)s; border-left: 1px solid %(shine)s; border-right: 1px solid %(shadow)s; border-bottom: 1px solid %(shadow)s; }
    QTabWidget::pane { border-top: 2px solid %(shine)s; border-left: 2px solid %(shine)s; border-right: 2px solid %(shadow)s; border-bottom: 2px solid %(shadow)s; }
    QTabBar::tab { background: %(panel)s; color: %(text)s; padding: 5px 12px; border-top: 2px solid %(shine)s; border-left: 2px solid %(shine)s; border-right: 1px solid %(shadow)s; border-bottom: 1px solid %(shadow)s; }
    QTabBar::tab:selected { border-bottom-color: %(panel)s; font-weight: bold; }
    QLabel#section { font-weight: 600; padding: 4px 0; border-bottom: 1px solid %(divider)s; }
    QLabel#muted { color: %(muted)s; }
    QLabel#datasetHeader { padding: 5px 8px; background: %(panel)s; border-top: 1px solid %(shine)s; border-bottom: 1px solid %(shadow)s; }
    QLabel#welcomeTitle { font-size: 15pt; font-weight: 600; }
    QLabel#notice { padding: 7px; background: %(base)s; color: %(text)s; border: 1px solid %(shadow)s; }
    QSplitter::handle { background: %(panel)s; border-left: 1px solid %(shine)s; border-right: 1px solid %(shadow)s; width: 4px; height: 4px; }
    QStatusBar { border-top: 1px solid %(shadow)s; background: %(panel)s; }
    QStatusBar::item { border-top: 1px solid %(shadow)s; border-left: 1px solid %(shadow)s; border-right: 1px solid %(shine)s; border-bottom: 1px solid %(shine)s; }
    QProgressBar { background: %(base)s; color: %(text)s; border-top: 1px solid %(shadow)s; border-left: 1px solid %(shadow)s; border-right: 1px solid %(shine)s; border-bottom: 1px solid %(shine)s; text-align: center; }
    QProgressBar::chunk { background: %(selected)s; width: 8px; margin: 1px; }
    QScrollArea { border: none; }
    """ % colors)
    if hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if name == "dark" else Qt.ColorScheme.Light)
