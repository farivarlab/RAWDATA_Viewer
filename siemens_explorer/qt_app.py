"""Native Qt desktop workspace for Siemens MRI Twix raw-data exploration."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import QByteArray, QRect, Qt, QThreadPool, QTimer, Slot, QSize, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMenu, QMenuBar, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QStyle, QTabWidget,
    QTableView, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import __version__
from .analysis import COUNTERS, build_cartesian_preview, scans_to_frame
from .noise import build_noise_preview
from .reconstruction import NoiseCalibration, estimate_noise_covariance
from .qt_models import DataFrameFilter, DataFrameModel, Worker
from .qt_channels import ChannelSelector
from .qt_counters import CounterSlider
from .qt_wheel import WheelSlider
from .qt_chrome import ClassicDockWidget as QDockWidget, ClassicTitleBar, install_native_frame, native_frame_event
from .qt_dialogs import QFileDialog, QMessageBox
from .qt_branding import application_icon, logo_file, update_windows_icon
from .qt_glyphs import GLYPH_NAMES, clear_glyph_cache, glyph_icon
from .qt_blocks import DataBlocksView
from .qt_plots import PreviewView, SignalView
from .qt_headers import HeaderTree
from .qt_tabs import WorkspaceTabBar
from .qt_theme import apply_theme, apply_theme_assets, load_theme, THEME_DIRECTORY, THEME_CHOICES, THEME_NAMES
from .twix import TwixFile
from .preferences import LAYOUT_VERSION, saved_theme
from .ismrmrd_export import export_ismrmrd


# Siemens Twix array order (Col, Cha, Lin, Par, Sli, Ave, Phs, Eco, Rep,
# Set, Seg, Ida...Ide). Readout columns/lines remain in the data display.
SELECTION_COUNTER_ORDER = (
    "partition", "slice", "average", "phase", "echo", "repetition", "set", "segment",
    "ida", "idb", "idc", "idd", "ide",
)


def label(text="", name=None, wrap=False):
    item = QLabel(str(text))
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(wrap)
    if name:
        item.setObjectName(name)
    return item


def size_label(size):
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:,.1f} {unit}" if unit != "B" else f"{value:,.0f} B"
        value /= 1024


def table_view(model, name):
    table = QTableView()
    table.setObjectName(name)
    table.setModel(model)
    table.setAlternatingRowColors(True)
    table.setSortingEnabled(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    # Align row boundaries at common Windows scales such as 125% and 150%.
    table.verticalHeader().setDefaultSectionSize(24)
    table.horizontalHeader().setDefaultSectionSize(100)
    table.horizontalHeader().setMinimumSectionSize(55)
    table.horizontalHeader().setSectionsMovable(True)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    return table


class MainWindow(QMainWindow):
    """A local, read-only viewer with one cancellable background job at a time."""

    def __init__(self, theme=None, *, settings=None):
        super().__init__()
        self._settings = settings
        self._preferences_saved = False
        self._restored_geometry = False
        self.theme_name = theme if theme in THEME_NAMES else saved_theme(settings)
        app = QApplication.instance()
        if app.property("explorerTheme") != self.theme_name:
            apply_theme(app, self.theme_name)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowTitle("Raw MRI Data Explorer")
        self.setWindowIcon(application_icon())
        self.setObjectName("rawDataExplorer")
        self.setDockNestingEnabled(True)
        self.resize(1460, 920)
        self.setMinimumSize(1020, 680)
        self.setContentsMargins(3, 3, 3, 3)
        self._create_window_chrome()
        self.setAcceptDrops(True)
        self.twix = None
        self.measurement = None
        self.scan_index = None
        self.scans = []
        self.frame = pd.DataFrame()
        self.filtered = []
        self.preview = None
        self.source_name = ""
        self.busy = False
        self.last_error = ""
        self._updating = False
        self._job = None
        self._job_kind = ""
        self._close_pending = False
        self._slice_values = []
        self._blocks_dirty = True
        self._navigation_change = False
        self._automatic_preview = False
        self._pending_preview = False
        self._preview_revision = 0
        self._active_preview_revision = -1
        self._preserve_preview_view = False
        self._preview_cache = OrderedDict()
        self._preview_cache_bytes = 0
        self._noise_cache = {}
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._create_actions()
        self._create_explorer()
        self._create_workspace()
        self._create_menus()
        self._create_toolbar()
        self._create_statusbar()
        self._set_enabled()
        self._restore_preferences()

    def _restore_preferences(self):
        if self._settings is None:
            return
        geometry = self._settings.value("window/geometry")
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self._restored_geometry = self.restoreGeometry(geometry)
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        state = self._settings.value("window/docks")
        if isinstance(state, QByteArray) and not state.isEmpty():
            default = self.saveState(LAYOUT_VERSION)
            if not self.restoreState(state, LAYOUT_VERSION):
                self.restoreState(default, LAYOUT_VERSION)
        # A removed monitor must not leave a floating panel out of reach.
        screens = QApplication.screens()
        for dock in (self.explorer_dock, self.selection_dock):
            if dock.isFloating() and screens:
                frame = dock.frameGeometry()
                caption = QRect(frame.x(), frame.y(), frame.width(), min(32, frame.height()))
                visible = [screen.availableGeometry().intersected(caption) for screen in screens]
                if not any(rect.width() >= 80 and rect.height() >= 20 for rect in visible):
                    dock.move(self.screen().availableGeometry().topLeft())
        self.fullscreen_action.setChecked(self.isFullScreen())

    def show_startup(self, *, windowed=False):
        """Restore the last window mode, retaining a windowed launch override."""
        if windowed:
            self.showNormal()
        elif self._restored_geometry:
            self.show()
        else:
            self.showMaximized()
        self.fullscreen_action.setChecked(self.isFullScreen())

    def _save_preferences(self):
        if self._settings is None or self._preferences_saved:
            return
        # Save before close_dataset changes the widgets or Qt hides the docks.
        self._settings.setValue("appearance/theme", self.theme_name)
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/docks", self.saveState(LAYOUT_VERSION))
        self._settings.sync()
        self._preferences_saved = True

    def _create_window_chrome(self):
        header = QWidget(self)
        header.setObjectName("windowChrome")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        self.title_bar = ClassicTitleBar(self)
        self._application_menu_bar = QMenuBar(header)
        self._application_menu_bar.setNativeMenuBar(False)
        header_layout.addWidget(self.title_bar)
        header_layout.addWidget(self._application_menu_bar)
        self.setMenuWidget(header)

    def menuBar(self):
        # Preserve the standard API while the caption and menu share a header.
        bar = getattr(self, "_application_menu_bar", None)
        return bar if bar is not None else super().menuBar()

    def showEvent(self, event):
        super().showEvent(event)
        install_native_frame(self)

    def nativeEvent(self, event_type, message):
        result = native_frame_event(self, message)
        return result if result is not None else super().nativeEvent(event_type, message)

    def mousePressEvent(self, event):
        # Windows uses native hit testing; this also supports Qt system resizing
        # on other desktop platforms when the pointer is on our outer border.
        if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized() and not self.isFullScreen():
            point = event.position()
            edges = Qt.Edge(0)
            if point.x() < 5:
                edges |= Qt.Edge.LeftEdge
            elif point.x() >= self.width() - 5:
                edges |= Qt.Edge.RightEdge
            if point.y() < 5:
                edges |= Qt.Edge.TopEdge
            elif point.y() >= self.height() - 5:
                edges |= Qt.Edge.BottomEdge
            handle = self.windowHandle()
            if edges and handle is not None and handle.startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)

    def _action(self, text, callback, shortcut=None, icon=None):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if icon is not None:
            action.setIcon(self.style().standardIcon(icon))
        return action

    def _create_actions(self):
        self.open_action = self._action("Open raw data…", self.open_dialog, "Ctrl+O", QStyle.StandardPixmap.SP_DialogOpenButton)
        self.close_action = self._action("Close dataset", self.close_dataset, "Ctrl+W", QStyle.StandardPixmap.SP_DialogCloseButton)
        self._update_close_icon()
        self.quit_action = self._action("Exit", self.close, "Alt+F4")
        self.reindex_action = self._action("Re-index measurement", self.reindex, "F5", QStyle.StandardPixmap.SP_BrowserReload)
        self.reindex_action.setToolTip("Rescan the selected measurement's acquisition headers using Maximum readouts. Raw sample data is unchanged.")
        self.previous_channel_action = self._action("Previous channel", lambda: self.step_channel(-1), "Ctrl+Left")
        self.next_channel_action = self._action("Next channel", lambda: self.step_channel(1), "Ctrl+Right")
        self.previous_slice_action = self._action("Previous slice", lambda: self.step_slice(-1), "PgUp")
        self.next_slice_action = self._action("Next slice", lambda: self.step_slice(1), "PgDown")
        self.cancel_action = self._action("Cancel operation", self.cancel_job, "Escape")
        self.export_actions = {}
        for key, title in {
            "scans": "Acquisition table CSV…", "signal": "Complex readout CSV…",
            "metadata": "Measurement metadata JSON…", "headers": "Raw headers TXT…",
            "preview": "Preview arrays NPZ…", "signal_png": "Signal plot PNG…",
            "preview_png": "Current data display PNG…",
            "ismrmrd": "ISMRMRD dataset…",
        }.items():
            self.export_actions[key] = self._action(title, lambda checked=False, kind=key: self.export_dialog(kind))
        self.export_actions["ismrmrd"].setToolTip("Export all indexed ADC acquisitions and channels of this measurement to ISMRMRD HDF5, with raw buffers and metadata.")
        self.about_action = self._action("About…", self.about)
        self.fullscreen_action = self._action("Full screen", self.toggle_fullscreen, "F11")
        self.fullscreen_action.setCheckable(True)

    def _create_explorer(self):
        self.explorer_dock = QDockWidget("Dataset explorer", self)
        self.explorer_dock.setObjectName("datasetExplorerDock")
        self.explorer_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.explorer_dock.setMinimumWidth(290)
        self.explorer_dock.setMaximumWidth(500)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(7, 7, 7, 7)
        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("filePath")
        self.path_edit.setAccessibleName("Raw data file path")
        self.path_edit.setPlaceholderText("Open or drop a Siemens .dat file")
        self.path_edit.returnPressed.connect(lambda: self.open_path(self.path_edit.text()))
        browse = QToolButton()
        self.browse_button = browse
        browse.setIcon(glyph_icon("browse", self.theme_name))
        browse.setToolTip("Open Siemens Twix raw data (Ctrl+O)")
        browse.setAccessibleName("Browse raw data")
        browse.clicked.connect(self.open_dialog)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        layout.addLayout(path_row)
        layout.addWidget(label("Measurements", "section"))
        self.measurement_tree = QTreeWidget()
        self.measurement_tree.setObjectName("measurementTree")
        self.measurement_tree.setAccessibleName("Measurements")
        self.measurement_tree.setHeaderHidden(True)
        self.measurement_tree.setMinimumHeight(60)
        self.measurement_tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.measurement_tree.setRootIsDecorated(True)
        self.measurement_tree.currentItemChanged.connect(self._measurement_changed)
        layout.addWidget(self.measurement_tree, 1)
        self.explorer_dock.setWidget(body)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.explorer_dock)

        self.selection_dock = QDockWidget("Data selection", self)
        self.selection_dock.setObjectName("dataSelectionDock")
        self.selection_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.selection_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                                       | QDockWidget.DockWidgetFeature.DockWidgetMovable
                                       | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.selection_dock.setMinimumWidth(290)
        self.selection_dock.setMaximumWidth(500)
        self.data_selection = QWidget()
        self.data_selection.setObjectName("dataSelection")
        selection_layout = QVBoxLayout(self.data_selection)
        selection_layout.setContentsMargins(7, 7, 7, 7)
        self.selection_scroll = QScrollArea()
        self.selection_scroll.setObjectName("dataSelectionScroll")
        self.selection_scroll.setWidgetResizable(True)
        self.selection_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        selection_fields = QWidget()
        form = QFormLayout(selection_fields)
        form.setContentsMargins(0, 0, 4, 0)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(6)
        self.kind = QComboBox()
        self.kind.setObjectName("acquisitionType")
        self.kind.setAccessibleName("Acquisition type")
        self.kind.addItems(["Image", "All", "Noise", "Phase correction", "Calibration", "Sync / feedback"])
        self.kind.currentIndexChanged.connect(self._type_changed)
        self.kind.setToolTip("Noise searches the file's other measurements when the selected measurement has no noise readouts.")
        form.addRow("Data type", self.kind)
        self.channel_selector = ChannelSelector()
        self.channel_selector.channel_selected.connect(self.select_channel)
        form.addRow("Channel", self.channel_selector)
        self.counter_widgets = {}
        for name in SELECTION_COUNTER_ORDER:
            caption = f"ID{name[-1]}" if name.startswith("id") else name.title()
            combo = CounterSlider()
            combo.setObjectName(f"counter_{name}")
            combo.setAccessibleName(f"{caption} counter")
            combo.setToolTip(f"Drag or wheel over the slider to browse observed {name} values. Wheel down advances. The leftmost position is All; typed values snap to the nearest available counter.")
            combo.addItem("All", None)
            combo.currentIndexChanged.connect(self._filters_changed)
            self.counter_widgets[name] = combo
            form.addRow(caption, combo)
        self.reset_filters_button = QPushButton("Reset selection")
        self.reset_filters_button.setToolTip("Select the first imaging slice, echo, and acquisition group.")
        self.reset_filters_button.clicked.connect(self.reset_filters)
        form.addRow(self.reset_filters_button)
        self.channel = QComboBox(self.data_selection)
        self.channel.setObjectName("channelOrdinal")
        self.channel.setAccessibleName("Receive channel ordinal")
        self.channel.addItem("0", 0)
        self.channel.currentIndexChanged.connect(self._channel_changed)
        self.channel.hide()
        self.index_limit = QSpinBox()
        self.index_limit.setObjectName("indexLimit")
        self.index_limit.setAccessibleName("Index acquisition limit")
        self.index_limit.setRange(100, 2_000_000)
        self.index_limit.setSingleStep(10000)
        self.index_limit.setValue(250_000)
        self.index_limit.setGroupSeparatorShown(True)
        self.index_limit.setToolTip("Maximum acquisition headers to index. Increase this limit and re-index to inspect a larger measurement.")
        form.addRow("Index limit", self.index_limit)
        reindex_button = QPushButton("Re-index measurement")
        reindex_button.clicked.connect(self.reindex)
        self.reindex_button = reindex_button
        reindex_button.setToolTip(self.reindex_action.toolTip())
        form.addRow(reindex_button)
        self.selection_scroll.setWidget(selection_fields)
        selection_layout.addWidget(self.selection_scroll)
        self.selection_dock.setWidget(self.data_selection)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.selection_dock)
        self.splitDockWidget(self.explorer_dock, self.selection_dock, Qt.Orientation.Vertical)
        self.resizeDocks([self.explorer_dock, self.selection_dock], [190, 600], Qt.Orientation.Vertical)

    def _create_workspace(self):
        workspace = QWidget()
        main = QVBoxLayout(workspace)
        self._workspace_layout = main
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(2)
        self.dataset_header = label("No dataset open  |  Siemens MRI Twix VB / VD / VE", "datasetHeader")
        self.dataset_header.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.dataset_header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        main.addWidget(self.dataset_header)
        navigation = QWidget()
        self.slice_navigation = navigation
        navigation.setObjectName("sliceNavigationBar")
        navigation_layout = QHBoxLayout(navigation)
        navigation_layout.setContentsMargins(8, 3, 8, 5)
        self.slice_axis = QComboBox()
        self.slice_axis.setObjectName("sliceNavigationAxis")
        self.slice_axis.setAccessibleName("Slice navigation dimension")
        self.slice_axis.addItem("Slice", "slice")
        self.slice_axis.addItem("Partition", "partition")
        self.slice_axis.currentIndexChanged.connect(self._navigation_axis_changed)
        self.previous_slice_button = QPushButton("‹")
        self.previous_slice_button.setObjectName("previousSlice")
        self.previous_slice_button.setAccessibleName("Previous slice")
        self.previous_slice_button.setToolTip("Previous slice (Page Up)")
        self.previous_slice_button.setFixedWidth(34)
        self.previous_slice_button.clicked.connect(lambda: self.step_slice(-1))
        self.next_slice_button = QPushButton("›")
        self.next_slice_button.setObjectName("nextSlice")
        self.next_slice_button.setAccessibleName("Next slice")
        self.next_slice_button.setToolTip("Next slice (Page Down)")
        self.next_slice_button.setFixedWidth(34)
        self.next_slice_button.clicked.connect(lambda: self.step_slice(1))
        self.slice_slider = WheelSlider(Qt.Orientation.Horizontal)
        self.slice_slider.setObjectName("sliceSlider")
        self.slice_slider.setAccessibleName("Slice position")
        self.slice_slider.setRange(0, 0)
        self.slice_slider.setTracking(True)
        self.slice_slider.setMinimumWidth(100)
        self.slice_slider.valueChanged.connect(self.navigate_slice)
        self.slice_spin = QSpinBox()
        self.slice_spin.setObjectName("slicePositionSpin")
        self.slice_spin.setAccessibleName("Slice number")
        self.slice_spin.setRange(0, 0)
        self.slice_spin.setSpecialValueText("All")
        self.slice_spin.setToolTip("Position among the observed slices or partitions. The scanner counter is shown beside it.")
        self.slice_spin.setMinimumWidth(65)
        self.slice_spin.valueChanged.connect(self._slice_spin_changed)
        self.slice_position = label("No slices", "muted")
        self.slice_position.setMinimumWidth(140)
        navigation_layout.addWidget(self.slice_axis)
        navigation_layout.addWidget(self.previous_slice_button)
        self.slice_minimum = label("—", "sliderEndpoint")
        self.slice_maximum = label("—", "sliderEndpoint")
        navigation_layout.addWidget(self.slice_minimum)
        navigation_layout.addWidget(self.slice_slider, 1)
        navigation_layout.addWidget(self.slice_maximum)
        navigation_layout.addWidget(self.next_slice_button)
        navigation_layout.addWidget(self.slice_spin)
        navigation_layout.addWidget(self.slice_position)
        self.notice = label("", "notice", True)
        self.notice.hide()
        main.addWidget(self.notice)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("workspaceTabs")
        self.tabs.setTabBar(WorkspaceTabBar(self.tabs))
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        main.addWidget(self.tabs, 1)
        main.addWidget(navigation)
        self.setCentralWidget(workspace)

        self.overview_stack = QStackedWidget()
        welcome = QWidget()
        welcome_layout = QVBoxLayout(welcome)
        welcome_layout.setContentsMargins(40, 40, 40, 40)
        welcome_layout.addStretch()
        title = label("Raw MRI Data Explorer", "welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(title)
        intro = label("Open or drop a Siemens Twix .dat file.", "muted", True)
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(intro)
        buttons = QHBoxLayout()
        buttons.addStretch()
        for text, callback in (("Open raw data…", self.open_dialog),):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch()
        welcome_layout.addLayout(buttons)
        welcome_layout.addStretch()
        self.overview_stack.addWidget(welcome)
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.summary_model = DataFrameModel(parent=self)
        self.summary_table = table_view(self.summary_model, "measurementSummary")
        self.summary_table.setSortingEnabled(False)
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.setColumnWidth(0, 190)
        self.dimensions_model = DataFrameModel(parent=self)
        dims = table_view(self.dimensions_model, "imagingDimensions")
        dims.setSortingEnabled(False)
        dims.horizontalHeader().setStretchLastSection(True)
        for title, table in (("Measurement properties", self.summary_table), ("Observed imaging dimensions", dims)):
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.addWidget(label(title, "section"))
            column_layout.addWidget(table, 1)
            split.addWidget(column)
        split.setSizes([700, 350])
        detail_layout.addWidget(split, 1)
        details.setToolTip("Cartesian previews infer matrix bounds from acquisition counters; sequence-specific corrections and scanner image orientation are not applied.")
        self.overview_stack.addWidget(details)
        self.tabs.addTab(self.overview_stack, "Overview")

        acquisitions = QWidget()
        acq_layout = QVBoxLayout(acquisitions)
        self.acq_search = QLineEdit()
        self.acq_search.setObjectName("acquisitionSearch")
        self.acq_search.setAccessibleName("Search acquisitions")
        self.acq_search.setPlaceholderText("Filter visible rows by counter, evaluation flag, offset, or other value…")
        self.acq_search.setClearButtonEnabled(True)
        acq_layout.addWidget(self.acq_search)
        self.acq_model = DataFrameModel(parent=self)
        self.acq_proxy = DataFrameFilter(self)
        self.acq_proxy.setSourceModel(self.acq_model)
        self.acq_search.textChanged.connect(self.acq_proxy.set_query)
        self.acq_table = table_view(self.acq_proxy, "acquisitionTable")
        self.acq_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.acq_table.selectionModel().currentRowChanged.connect(self._table_selected)
        self.acq_table.doubleClicked.connect(lambda _: self.tabs.setCurrentIndex(2))
        acq_layout.addWidget(self.acq_table, 1)
        self.acq_table.setToolTip("Select a row to inspect its readout; double-click to open Signal. CSV export uses this table's current search and sort order.")
        self.tabs.addTab(acquisitions, "Acquisitions")

        signal_tab = QWidget()
        signal_layout = QVBoxLayout(signal_tab)
        signal_layout.setContentsMargins(6, 6, 6, 6)
        self.signal_view = SignalView()
        scan_controls = self.signal_view.controls
        scan_controls.insertWidget(0, label("Acquisition:"))
        self.scan_spin = QSpinBox()
        self.scan_spin.setObjectName("acquisitionIndex")
        self.scan_spin.setAccessibleName("Acquisition index")
        self.scan_spin.setRange(0, 0)
        self.scan_spin.setMinimumWidth(110)
        self.scan_spin.setToolTip("Acquisition in the full current index. Selecting a table row updates this value.")
        self.scan_spin.valueChanged.connect(self._scan_changed)
        scan_controls.insertWidget(1, self.scan_spin)
        signal_layout.addWidget(self.signal_view, 1)
        self.tabs.addTab(signal_tab, "Signal")

        preview_tab = QWidget()
        preview_layout = QVBoxLayout(preview_tab)
        preview_layout.setContentsMargins(6, 6, 6, 6)
        self.rss = QCheckBox("All channels")
        self.rss.setObjectName("combineCoils")
        self.rss.toggled.connect(self._reconstruction_changed)
        self.rss.setToolTip("Combine every receive channel. Select a channel in the sidebar to return to its individual image.")
        self.coil_method = QComboBox()
        self.coil_method.setObjectName("coilCombinationMethod")
        self.coil_method.setAccessibleName("Channel combination method")
        self.coil_method.addItem("RSS", "rss")
        self.coil_method.addItem("Optimal (adaptive)", "adaptive")
        self.coil_method.setToolTip("RSS combines magnitudes. Optimal estimates phase-aware channel weights and uses matching noise scans when available; otherwise it assumes equal, uncorrelated noise. It is an estimated matched filter, not an absolute SNR map.")
        self.coil_method.currentIndexChanged.connect(self._reconstruction_changed)
        self.ifft_checks = {}
        self.reconstruction_controls = QWidget()
        reconstruction_layout = QHBoxLayout(self.reconstruction_controls)
        reconstruction_layout.setContentsMargins(0, 0, 0, 0)
        reconstruction_layout.addWidget(self.rss)
        reconstruction_layout.addWidget(self.coil_method)
        reconstruction_layout.addSpacing(14)
        reconstruction_layout.addWidget(label("Inverse FFT:"))
        for name, description in (("x", "Readout / sample axis"), ("y", "Phase-encode / line axis"), ("z", "Partition axis across a 3D acquisition; separate 2D slices are never combined")):
            check = QCheckBox(name.upper())
            check.setObjectName(f"ifft{name.upper()}")
            check.setAccessibleName(f"Inverse FFT {name.upper()}")
            check.setChecked(name != "z")
            check.setToolTip(description + ". Uncheck to leave this axis in k-space.")
            check.toggled.connect(self._reconstruction_changed)
            self.ifft_checks[name] = check
            reconstruction_layout.addWidget(check)
        reconstruction_layout.addStretch()
        self.preview_view = PreviewView()
        self.preview_view.slice_step.connect(self.step_slice)
        preview_layout.addWidget(self.reconstruction_controls)
        preview_layout.addWidget(self.preview_view, 1)
        self.tabs.addTab(preview_tab, "K-space && image")

        protocol_tab = QWidget()
        protocol_layout = QVBoxLayout(protocol_tab)
        self.protocol_search = QLineEdit()
        self.protocol_search.setObjectName("protocolSearch")
        self.protocol_search.setAccessibleName("Search protocol")
        self.protocol_search.setPlaceholderText("Search protocol paths or values, e.g. alTE or lBaseResolution…")
        self.protocol_search.setClearButtonEnabled(True)
        self.protocol_model = DataFrameModel(parent=self)
        self.protocol_proxy = DataFrameFilter(self)
        self.protocol_proxy.setSourceModel(self.protocol_model)
        self.protocol_search.textChanged.connect(self.protocol_proxy.set_query)
        self.protocol_table = table_view(self.protocol_proxy, "protocolTable")
        self.protocol_table.horizontalHeader().setStretchLastSection(True)
        self.protocol_table.setColumnWidth(0, 420)
        protocol_layout.addWidget(self.protocol_search)
        protocol_layout.addWidget(self.protocol_table, 1)
        self.protocol_table.setToolTip("Original Siemens paths, zero-based array indices, and stored units. alTE / alTR are typically microseconds; no unit conversion is applied.")
        self.tabs.addTab(protocol_tab, "Protocol")

        raw_tab = QWidget()
        raw_layout = QVBoxLayout(raw_tab)
        raw_controls = QHBoxLayout()
        raw_controls.addWidget(label("Buffer:"))
        self.raw_buffer = QComboBox()
        self.raw_buffer.setObjectName("rawHeaderBuffer")
        self.raw_buffer.setAccessibleName("Raw header buffer")
        self.raw_buffer.setMinimumWidth(160)
        self.raw_buffer.currentIndexChanged.connect(self._buffer_changed)
        raw_controls.addWidget(self.raw_buffer)
        self.header_search = QLineEdit()
        self.header_search.setObjectName("rawHeaderSearch")
        self.header_search.setAccessibleName("Find in raw header")
        self.header_search.setPlaceholderText("Find text in this buffer…")
        self.header_search.setClearButtonEnabled(True)
        self.header_search.textChanged.connect(self._find_header_from_start)
        self.header_search.returnPressed.connect(self.find_next_header)
        raw_controls.addWidget(self.header_search, 1)
        next_button = QPushButton("Find next")
        next_button.clicked.connect(self.find_next_header)
        raw_controls.addWidget(next_button)
        self.raw_limit_notice = label("First 2 MiB shown", "muted")
        self.raw_limit_notice.setToolTip("This header is too large to display in full. Export retains its entire original text.")
        self.raw_limit_notice.hide()
        raw_controls.addWidget(self.raw_limit_notice)
        raw_layout.addLayout(raw_controls)
        self.raw_text = QPlainTextEdit()
        self.raw_text.setObjectName("rawHeaderText")
        self.raw_text.setAccessibleName("Original raw header text")
        self.raw_text.setReadOnly(True)
        self.raw_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_text.setFont(QFont("Consolas", 10))
        self.header_tree = HeaderTree()
        self.header_tree.parameter_selected.connect(self._jump_header_parameter)
        self.header_search.textChanged.connect(self.header_tree.filter_parameters)
        raw_splitter = QSplitter(Qt.Orientation.Horizontal)
        raw_splitter.setObjectName("rawHeaderSplitter")
        raw_splitter.setChildrenCollapsible(False)
        raw_splitter.addWidget(self.header_tree)
        raw_splitter.addWidget(self.raw_text)
        raw_splitter.setSizes([320, 760])
        raw_layout.addWidget(raw_splitter, 1)
        self.tabs.addTab(raw_tab, "Raw headers")
        self.blocks_view = DataBlocksView()
        self.blocks_view.set_theme(self.theme_name)
        colors = QApplication.instance().property("explorerThemeColors")
        self.signal_view.set_theme(colors)
        self.preview_view.set_theme(colors)
        self.blocks_view.block_selected.connect(self._block_selected)
        self.tabs.addTab(self.blocks_view, "Data blocks")

    def _create_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([self.open_action, self.close_action])
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("&Export")
        export_menu.addActions(list(self.export_actions.values()))
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.explorer_dock.toggleViewAction())
        view_menu.addAction(self.selection_dock.toggleViewAction())
        self.preview_notes_action = QAction("Preview notes", self)
        self.preview_notes_action.setCheckable(True)
        self.preview_notes_action.setToolTip("Show preview notes and reconstruction limitations; drag the divider above them to resize.")
        self.preview_notes_action.toggled.connect(self.preview_view.set_notes_visible)
        self.preview_notes_action.triggered.connect(lambda checked: self.tabs.setCurrentIndex(3) if checked else None)
        view_menu.addAction(self.preview_notes_action)
        view_menu.addAction(self.fullscreen_action)
        view_menu.addActions([self.previous_slice_action, self.next_slice_action])
        view_menu.addActions([self.previous_channel_action, self.next_channel_action])
        view_menu.addSeparator()
        theme_menu = view_menu.addMenu("Appearance")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        for name, title in THEME_CHOICES:
            action = QAction(title, self)
            action.setData(name)
            action.setCheckable(True)
            action.setChecked(name == self.theme_name)
            action.triggered.connect(lambda checked=False, selected=name: self.set_theme(selected))
            self.theme_group.addAction(action)
            theme_menu.addAction(action)
        theme_menu.addSeparator()
        self.reload_theme_action = self._action("Reload appearance assets", self.reload_theme_assets, "Ctrl+Shift+R")
        theme_menu.addAction(self.reload_theme_action)
        theme_menu.addAction(self._action("Open theme assets folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(THEME_DIRECTORY.parent)))))
        theme_menu.addAction(self._action("Open logo folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logo_file().parent)))))
        for index in range(self.tabs.count()):
            action = self._action(self.tabs.tabText(index).replace("&&", "&"), lambda checked=False, target=index: self.tabs.setCurrentIndex(target), f"Ctrl+{index + 1}")
            view_menu.addAction(action)
        tools = self.menuBar().addMenu("&Tools")
        tools.addActions([self.reindex_action, self.cancel_action])
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self.about_action)

    def _create_toolbar(self):
        toolbar = QToolBar("Main tools", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setMovable(False)
        toolbar.addActions([self.open_action, self.close_action])
        toolbar.addSeparator()
        export_button = QToolButton()
        self.export_button = export_button
        export_button.setText("Export")
        export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        export_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(export_button)
        menu.addActions(list(self.export_actions.values()))
        export_button.setMenu(menu)
        export_button.setAccessibleName("Export data")
        toolbar.addWidget(export_button)
        self.addToolBar(toolbar)
        self._update_control_glyphs()

    def _create_statusbar(self):
        self.statusBar().showMessage("Ready · Open a Siemens MRI Twix .dat file")
        self.progress = QProgressBar()
        self.progress.setObjectName("operationProgress")
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(160)
        self.progress.setMaximumHeight(17)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelOperation")
        self.cancel_button.clicked.connect(self.cancel_job)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_button)
        read_only = label("Read-only", "muted")
        read_only.setToolTip("Raw data files are opened read-only. This viewer is for research and inspection.")
        self.statusBar().addPermanentWidget(read_only)

    def _set_enabled(self):
        has_data = self.twix is not None
        idle = not self.busy
        self.open_action.setEnabled(idle)
        self.close_action.setEnabled(has_data and idle)
        self.reindex_action.setEnabled(has_data and idle)
        self.reindex_button.setEnabled(has_data and idle)
        self.measurement_tree.setEnabled(idle)
        self.path_edit.setEnabled(idle)
        self.index_limit.setEnabled(idle)
        for widget in [self.kind, self.reset_filters_button]:
            widget.setEnabled(bool(self.scans) and idle)
        navigating = idle or self._job_kind == "preview"
        axis = self.slice_axis.currentData() or "slice"
        can_navigate = bool(self.scans) and navigating and not self._close_pending
        for widget in self.counter_widgets.values():
            widget.setEnabled(can_navigate)
        self.channel.setEnabled(can_navigate)
        self.channel_selector.setEnabled(can_navigate)
        self.channel_selector.set_navigation_enabled(can_navigate)
        channel_navigation = can_navigate and len(self.channel_selector.channel_ids) > 1
        self.previous_channel_action.setEnabled(channel_navigation)
        self.next_channel_action.setEnabled(channel_navigation)
        self.slice_axis.setEnabled(can_navigate)
        volume_active = self.tabs.currentIndex() == 3 and self.ifft_checks["z"].isChecked() and self.preview_view.volume_depth > 0
        multiple = (self.preview_view.volume_depth if volume_active else len(self._slice_values)) > 1 and can_navigate
        self.slice_slider.setEnabled(multiple)
        self.slice_spin.setEnabled(multiple)
        current = self.counter_widgets[axis].currentData()
        position = self._slice_values.index(current) if current in self._slice_values else -1
        if volume_active:
            position = self.preview_view.plane_index
        previous = multiple and (position < 0 or position > 0)
        following = multiple and (position < 0 or position < (self.preview_view.volume_depth if volume_active else len(self._slice_values)) - 1)
        self.previous_slice_button.setEnabled(previous)
        # Keep shortcuts active at the ends so a focused slider cannot handle
        # Page Up/Down with its own (opposite-direction) page-step behavior.
        self.previous_slice_action.setEnabled(multiple)
        self.next_slice_button.setEnabled(following)
        self.next_slice_action.setEnabled(multiple)
        self.counter_widgets[axis].setEnabled(can_navigate)
        self.preview_view.set_slice_navigation_enabled(multiple)
        self.scan_spin.setEnabled(bool(self.scans) and idle)
        imaging = self.kind.currentText() != "Noise"
        z_available = bool(self.scans) and (len({s.partition for s in self.scans if s.is_image}) > 1
                                          or any(s.is_image and s.center_partition > 0 for s in self.scans))
        if not z_available and self.ifft_checks["z"].isChecked():
            self.ifft_checks["z"].blockSignals(True)
            self.ifft_checks["z"].setChecked(False)
            self.ifft_checks["z"].blockSignals(False)
        self.rss.setEnabled(has_data and navigating and imaging)
        self.coil_method.setEnabled(has_data and navigating and imaging and self.rss.isChecked())
        self.reconstruction_controls.setVisible(imaging)
        for name, check in self.ifft_checks.items():
            check.setEnabled(can_navigate and imaging and (name != "z" or z_available))
        self.ifft_checks["z"].setToolTip("Inverse FFT across acquired partitions (Z), never across separate 2D slices." if z_available else "No partition-encoded volume in this measurement. Z inverse FFT requires acquired partitions; separate 2D slices are not a Z encoding.")
        if self.ifft_checks["z"].isChecked():
            self.counter_widgets["partition"].setEnabled(False)
            self.counter_widgets["partition"].setToolTip("Z inverse FFT uses all acquired partitions for the selected slice and counter group.")
        else:
            self.counter_widgets["partition"].setToolTip("Select an acquired partition, or All.")
        self.slice_navigation.setVisible(imaging and not (self.tabs.currentIndex() == 3 and self.ifft_checks["z"].isChecked()))
        self.preview_view.volume_navigation.setVisible(imaging and self.ifft_checks["z"].isChecked()
                                                       and self.preview_view.volume_depth > 0)
        self.acq_table.setEnabled(idle)
        self.blocks_view.setEnabled(idle or self._job_kind == "preview")
        self.cancel_action.setEnabled(self.busy)
        self.cancel_button.setVisible(self.busy)
        self.progress.setVisible(self.busy)
        signal_ready = self.signal_view.signal is not None
        for kind, action in self.export_actions.items():
            enabled = has_data and idle
            if kind in {"signal", "signal_png"}:
                enabled = enabled and signal_ready
            elif kind in {"preview", "preview_png"}:
                enabled = enabled and self.preview is not None
            action.setEnabled(enabled)

    def _message(self, text, error=False):
        self.statusBar().showMessage(str(text))
        if error:
            self.last_error = str(text)
            self.notice.setText(str(text))
            self.notice.show()

    def _start_job(self, kind, function):
        if self.busy:
            return False
        self.busy = True
        self.last_error = ""
        self.notice.hide()
        self._job_kind = kind
        self.progress.setValue(0)
        self._job = Worker(function)
        self._job.signals.success.connect(self._job_success, Qt.ConnectionType.QueuedConnection)
        self._job.signals.error.connect(self._job_error, Qt.ConnectionType.QueuedConnection)
        self._job.signals.progress.connect(self._job_progress, Qt.ConnectionType.QueuedConnection)
        self._job.signals.finished.connect(self._job_finished, Qt.ConnectionType.QueuedConnection)
        self._set_enabled()
        self.pool.start(self._job)
        return True

    @Slot(float, int)
    def _job_progress(self, fraction, count):
        self.progress.setValue(round(fraction * 1000))
        verb = "Reading selected data" if self._job_kind == "preview" else "Exporting ISMRMRD" if self._job_kind == "export_ismrmrd" else "Indexing acquisition headers"
        self.statusBar().showMessage(f"{verb} · {count:,} readouts · {fraction:.0%}")

    @Slot(object)
    def _job_success(self, result):
        if self._close_pending:
            if isinstance(result, dict) and "raw" in result:
                result["raw"].close()
            return
        try:
            if self._job_kind == "open":
                old = self.twix
                self.twix = result["raw"]
                self.source_name = self.twix.path.name
                if old:
                    old.close()
                self.path_edit.setText(str(self.twix.path))
                self._populate_measurements(result["measurement"])
                self._adopt_index(result)
                self.tabs.setCurrentIndex(0)
            elif self._job_kind == "index":
                self._adopt_index(result)
            elif self._job_kind == "find_noise":
                if result is not None:
                    self.tabs.setCurrentIndex(3)
                    self._adopt_index(result)
                else:
                    self._message("No noise readouts found within the current index limit.")
            elif self._job_kind == "preview":
                if self._active_preview_revision != self._preview_revision or self._job.cancel.is_set():
                    return
                self._display_preview(result)
                self._remember_preview(self._active_preview_key, result)
            elif self._job_kind == "export_ismrmrd":
                self._message(f"Exported {result['path'].name} · {result['acquisitions']:,} ADC acquisitions · {result['skipped']:,} non-ADC records skipped")
        except Exception as exc:
            self._message(f"Unable to display result: {exc}", error=True)

    @Slot(str)
    def _job_error(self, text):
        if self._job_kind == "preview" and self._pending_preview:
            return  # A newer slice superseded this operation.
        if self._job and self._job.cancel.is_set():
            self._message("Operation cancelled. The current dataset is unchanged.")
        else:
            self._message(f"Unable to {self._job_kind}: {text}", error=True)
        if self._job_kind == "preview":
            self.preview_view.clear("Display unavailable for this selection.")
            self._preserve_preview_view = False

    @Slot()
    def _job_finished(self):
        kind = self._job_kind
        self.busy = False
        self._job = None
        self._job_kind = ""
        self._set_enabled()
        if self._close_pending:
            QTimer.singleShot(0, self.close)
        elif self._pending_preview:
            if not self._preview_timer.isActive():
                QTimer.singleShot(0, self._refresh_preview)
        elif kind in {"index", "find_noise", "export_ismrmrd"} and self.tabs.currentIndex() == 3 and self.preview is None:
            self._queue_preview()

    def cancel_job(self):
        self._preview_timer.stop()
        self._pending_preview = False
        self._automatic_preview = False
        if self._job:
            self._job.cancel.set()
            self.cancel_button.setEnabled(False)
            self._message("Cancelling after the current read…")

    def open_dialog(self):
        if self.busy:
            return
        directory = str(self.twix.path.parent) if self.twix else str(Path.cwd())
        filename, _ = QFileDialog.getOpenFileName(self, "Open Siemens MRI raw data", directory, "Siemens Twix raw data (*.dat);;All files (*)")
        if filename:
            self.open_path(filename)

    def open_path(self, path):
        if self.busy:
            return False
        value = str(path).strip().strip('"')
        source = Path(value).expanduser()
        if not value or not source.is_file():
            self._message(f"File does not exist: {value or '(no path supplied)'}", error=True)
            return False
        return self._open_source(source.resolve())

    def _open_source(self, source):
        maximum = self.index_limit.value()

        def work(cancel, progress):
            if cancel.is_set():
                raise InterruptedError("Cancelled")
            raw = TwixFile(source)
            try:
                measurement = raw.measurements[-1]
                if cancel.is_set():
                    raise InterruptedError("Cancelled")
                index = raw.index_measurement(measurement, max_scans=maximum, progress=progress)
                frame = scans_to_frame(index.scans)
                return {"raw": raw, "measurement": measurement, "index": index, "frame": frame}
            except Exception:
                raw.close()
                raise

        self._message("Reading measurement directory and headers…")
        self.cancel_button.setEnabled(True)
        return self._start_job("open", work)

    def _populate_measurements(self, current):
        self._updating = True
        self.measurement_tree.clear()
        root = QTreeWidgetItem([self.source_name])
        root.setIcon(0, application_icon())
        self.measurement_tree.addTopLevelItem(root)
        selected = None
        for measurement in self.twix.measurements:
            item = QTreeWidgetItem([f"{measurement.index + 1:02d}  {measurement.protocol_name or 'Measurement'}  · ID {measurement.id}"])
            item.setData(0, Qt.ItemDataRole.UserRole, measurement.index)
            item.setToolTip(0, f"Offset {measurement.offset:,} bytes\nLength {size_label(measurement.length)}")
            root.addChild(item)
            if measurement.index == current.index:
                selected = item
        root.setExpanded(True)
        if selected:
            self.measurement_tree.setCurrentItem(selected)
        self._updating = False

    def _measurement_changed(self, item, previous):
        if self._updating or self.busy or not item or not self.twix:
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is not None and (not self.measurement or index != self.measurement.index):
            self.reindex(index)

    def reindex(self, measurement_index=None):
        if self.busy or not self.twix:
            return False
        if isinstance(measurement_index, bool) or measurement_index is None:
            measurement_index = self.measurement.index
        raw = self.twix
        measurement = next(m for m in raw.measurements if m.index == measurement_index)
        maximum = self.index_limit.value()
        same_measurement = measurement.index == self.measurement.index
        selection = {"data_type": self.kind.currentText(), "channel": int(self.channel.currentData() or 0), "counter_values": {name: combo.currentData() for name, combo in self.counter_widgets.items()}} if same_measurement else {}

        def work(cancel, progress):
            index = raw.index_measurement(measurement, max_scans=maximum, progress=progress)
            return {"measurement": measurement, "index": index, "frame": scans_to_frame(index.scans), **selection}

        self.cancel_button.setEnabled(True)
        self._message("Indexing acquisition headers; signal samples stay on disk…")
        return self._start_job("index", work)

    def _adopt_index(self, result):
        self._preview_timer.stop()
        self._pending_preview = False
        self._automatic_preview = False
        self._preview_cache.clear()
        self._noise_cache.clear()
        self._preview_cache_bytes = 0
        self._updating = True
        self.measurement = result["measurement"]
        self.scan_index = result["index"]
        self.scans = self.scan_index.scans
        self.frame = result["frame"]
        self.acq_search.clear()
        self.protocol_search.clear()
        self.header_search.clear()
        self.kind.setCurrentText(result.get("data_type", "Image" if any(s.is_image for s in self.scans) else "Noise" if any("NOISEADJSCAN" in s.flags for s in self.scans) else "All"))
        self._populate_counter_choices()
        for name, value in result.get("counter_values", {}).items():
            combo = self.counter_widgets[name]
            position = combo.findData(value)
            if position >= 0:
                combo.setCurrentIndex(position)
        for tree_index in range(self.measurement_tree.topLevelItemCount()):
            root = self.measurement_tree.topLevelItem(tree_index)
            for child_index in range(root.childCount()):
                item = root.child(child_index)
                if item.data(0, Qt.ItemDataRole.UserRole) == self.measurement.index:
                    noise_count = sum("NOISEADJSCAN" in s.flags for s in self.scans)
                    suffix = f" · {noise_count:,} noise" if noise_count else ""
                    item.setText(0, f"{self.measurement.index + 1:02d}  {self.measurement.protocol_name or 'Measurement'}  · ID {self.measurement.id}{suffix}")
                    self.measurement_tree.setCurrentItem(item)
        imaging = [s for s in self.scans if s.is_image] or self.scans
        use_partition = len({s.slice for s in imaging}) <= 1 and len({s.partition for s in imaging}) > 1
        self.slice_axis.setCurrentIndex(self.slice_axis.findData("partition" if use_partition else "slice"))
        self.channel.clear()
        for i in range(max((s.channels for s in self.scans), default=1)):
            self.channel.addItem(str(i), i)
        if not self.channel.count():
            self.channel.addItem("0", 0)
        selected_channel = self.channel.findData(result.get("channel", 0))
        self.channel.setCurrentIndex(max(0, selected_channel))
        self.scan_spin.setRange(0, max(0, len(self.scans) - 1))
        self.raw_buffer.clear()
        self.raw_buffer.addItems(list(self.measurement.buffers))
        for preferred in ("MeasYaps", "Phoenix", "Meas"):
            index = self.raw_buffer.findText(preferred)
            if index >= 0:
                self.raw_buffer.setCurrentIndex(index)
                break
        protocol = pd.DataFrame([{"Parameter": str(k), "Value": str(v)} for k, v in sorted(self.measurement.protocol.items())], columns=["Parameter", "Value"])
        self.protocol_model.set_frame(protocol)
        self._updating = False
        self._filters_changed()
        self._buffer_changed()
        self._update_overview()
        self.dataset_header.setText(f"{self.source_name}  |  {self.twix.format}  |  {size_label(self.twix.size)}  |  {self.measurement.protocol_name or 'Unnamed protocol'}")
        self.setWindowTitle(f"{self.source_name} — Raw MRI Data Explorer")
        warnings = list(dict.fromkeys([*self.measurement.warnings, *self.scan_index.warnings]))
        if warnings:
            self.notice.setText("\n".join(warnings))
            self.notice.show()
        else:
            self.notice.hide()
        status = "Index complete" if self.scan_index.complete else "Partial index"
        self._message(f"{status} · {len(self.scans):,} acquisitions · {size_label(self.scan_index.bytes_scanned)} indexed")

    def _type_mask(self, kind=None):
        kind = self.kind.currentText() if kind is None else kind
        if kind == "Image":
            return self.frame["is_image"].astype(bool)
        patterns = {"Noise": "NOISEADJSCAN", "Phase correction": "PHASCOR|PHASESTAB", "Calibration": "PATREF", "Sync / feedback": "SYNCDATA|RTFEEDBACK|HPFEEDBACK"}
        if kind in patterns:
            return self.frame["flags"].str.contains(patterns[kind], regex=True, na=False)
        return pd.Series(True, index=self.frame.index, dtype=bool)

    def _populate_counter_choices(self):
        source = [self.scans[int(i)] for i in self.frame.index[self._type_mask()]] if self.scans else []
        for name, combo in self.counter_widgets.items():
            values = sorted({int(getattr(s, name, 0)) for s in source})
            combo.set_values(values, select_first=self.kind.currentText() == "Image")

    def _type_changed(self, *_):
        if self._updating or not self.twix:
            return
        self._updating = True
        try:
            self._populate_counter_choices()
            if self.kind.currentText() == "Noise":
                self.rss.setChecked(False)
        finally:
            self._updating = False
        self._filters_changed()
        if self.kind.currentText() == "Noise":
            self.tabs.setCurrentIndex(3)
            if not self._type_mask("Noise").any() and len(self.twix.measurements) > 1:
                self._find_noise_measurement()

    def _find_noise_measurement(self):
        raw = self.twix
        candidates = [m for m in raw.measurements if m.index != self.measurement.index]
        maximum = self.index_limit.value()
        def work(cancel, progress):
            for position, measurement in enumerate(candidates):
                if cancel.is_set():
                    raise InterruptedError("Cancelled")
                def report(fraction, count):
                    return progress((position + fraction) / len(candidates), count)
                index = raw.index_measurement(measurement, max_scans=maximum, progress=report)
                if cancel.is_set():
                    raise InterruptedError("Cancelled")
                if any("NOISEADJSCAN" in s.flags for s in index.scans):
                    return {"measurement": measurement, "index": index, "frame": scans_to_frame(index.scans), "data_type": "Noise"}
            return None
        self._message("Looking for noise readouts in the other measurements…")
        return self._start_job("find_noise", work)

    def _update_overview(self):
        m = self.measurement
        values = {"Protocol": m.protocol_name or "Not available", "File": str(self.twix.path), "Format": self.twix.format,
                  "File size": size_label(self.twix.size), "Measurement ID": m.id,
                  "Measurement offset": f"{m.offset:,} bytes", "Measurement length": size_label(m.length),
                  "Header length": size_label(m.header_length), "Header buffers": len(m.buffers),
                  "Protocol parameters": len(m.protocol), "Indexed acquisitions": len(self.scans),
                  "Index coverage": "Complete" if self.scan_index.complete else "Partial — increase index limit and re-index"}
        values.update(m.metadata)
        self.summary_model.set_frame(pd.DataFrame({"Property": list(values), "Value": [str(v) for v in values.values()]}))
        rows = []
        for name in COUNTERS:
            values = sorted({getattr(s, name, 0) for s in self.scans if s.is_image})
            rows.append({"Counter": name, "Unique": len(values), "Range": f"{values[0]} … {values[-1]}" if values else "—"})
        self.dimensions_model.set_frame(pd.DataFrame(rows))
        self.overview_stack.setCurrentIndex(1)

    def reset_filters(self):
        self._updating = True
        self.kind.setCurrentText("Image" if any(s.is_image for s in self.scans) else "All")
        self._populate_counter_choices()
        self.acq_search.clear()
        self._updating = False
        self._filters_changed()

    def _filters_changed(self, *_):
        if self._updating:
            return
        preserve = self._navigation_change or self.sender() in self.counter_widgets.values()
        self._updating = True
        try:
            if not self.scans:
                view = self.frame.iloc[:0]
            else:
                mask = pd.Series(True, index=self.frame.index)
                for name, combo in self.counter_widgets.items():
                    value = combo.currentData()
                    if value is not None:
                        mask &= self.frame[name] == value
                mask &= self._type_mask()
                view = self.frame[mask]
            self.filtered = [self.scans[int(i)] for i in view.index]
            self.acq_model.set_frame(view)
            if self.filtered:
                self.scan_spin.setValue(self.filtered[len(self.filtered) // 2].index)
            self.dataset_header.setToolTip(
                f"{self.twix.path if self.twix else ''}\n"
                f"{len(self.scans):,} indexed acquisitions · {len(self.filtered):,} selected readouts · "
                f"{max((s.channels for s in self.scans), default=0)} channels (maximum). Counters are zero-based."
            )
        finally:
            self._updating = False
        self._refresh_slice_navigation()
        self.tabs.setTabText(3, "Noise" if self.kind.currentText() == "Noise" else "K-space && image")
        self._refresh_channel_selector()
        self._blocks_dirty = True
        if self.tabs.currentIndex() == 6:
            self._update_blocks()
        self.invalidate_preview(preserve_view=preserve)
        if self.filtered:
            if self.busy and self._job_kind == "preview":
                self.signal_view.clear("Selected slice is loading…")
            else:
                self._render_signal()
        else:
            self.signal_view.clear("No acquisitions match the current counter and type filters.")
        self._set_enabled()

    def _table_selected(self, current, previous):
        if self._updating or self.busy or not current.isValid():
            return
        source = self.acq_proxy.mapToSource(current)
        self.scan_spin.setValue(int(self.acq_model.row_id(source.row())))
        self._render_signal()

    def _scan_changed(self, *_):
        if not self._updating and not self.busy:
            self._render_signal()

    def _channel_changed(self, *_):
        if not self._updating:
            self.select_channel(int(self.channel.currentData() or 0))

    def select_channel(self, ordinal, focus=True):
        if self._updating or not self.twix or self._close_pending or (self.busy and self._job_kind != "preview"):
            return False
        ordinal = int(ordinal)
        if not 0 <= ordinal < len(self.channel_selector.channel_ids):
            return False
        self._updating = True
        try:
            self.channel.setCurrentIndex(self.channel.findData(ordinal))
            self.channel_selector.set_current_channel(ordinal)
            self.rss.setChecked(False)
        finally:
            self._updating = False
        if focus and self.tabs.currentIndex() != 2:
            self.tabs.setCurrentIndex(3 if self.kind.currentText() == "Noise" or any(s.is_image for s in self.filtered) else 2)
        self.invalidate_preview(preserve_view=True)
        if self.busy and self._job_kind == "preview":
            self.signal_view.clear("Selected channel is loading…")
        else:
            self._render_signal()
        return True

    def step_channel(self, delta):
        if not self.channel_selector.channel_ids:
            return False
        current = int(self.channel.currentData() or 0)
        target = max(0, min(current + int(delta), len(self.channel_selector.channel_ids) - 1))
        if target == current and not self.rss.isChecked():
            return False
        return self.select_channel(target)

    def _refresh_channel_selector(self):
        if not self.twix or not self.scans:
            self.channel_selector.clear()
            return
        try:
            scan = self.current_scan()
            ids = self.twix.channel_ids(self.measurement, scan) if scan.channels else []
        except Exception:
            ids = [None] * self.current_scan().channels
        ordinal = int(self.channel.currentData() or 0)
        if ids and ordinal >= len(ids):
            previous = self._updating
            self._updating = True
            self.channel.setCurrentIndex(self.channel.findData(0))
            self._updating = previous
            ordinal = 0
        if ids != self.channel_selector.channel_ids:
            self.channel_selector.set_channels(ids, selected=ordinal)
        else:
            self.channel_selector.set_current_channel(ordinal)

    def current_scan(self):
        index = self.scan_spin.value()
        if not self.twix or not 0 <= index < len(self.scans):
            raise ValueError("Select an indexed acquisition first.")
        return self.scans[index]

    def _render_signal(self):
        if not self.twix or not self.scans:
            self.signal_view.clear()
            return
        try:
            scan = self.current_scan()
            channel = int(self.channel.currentData() or 0)
            signal = self.twix.read_signal(self.measurement, scan, channel)
            channel_ids = self.twix.channel_ids(self.measurement, scan)
            self.signal_view.set_signal(signal, scan=scan, channel_index=channel, channel_id=channel_ids[channel])
            if channel_ids != self.channel_selector.channel_ids:
                self.channel_selector.set_channels(channel_ids, selected=channel)
            else:
                self.channel_selector.set_current_channel(channel)
            self.blocks_view.set_selection(scan.index, channel)
        except Exception as exc:
            self.signal_view.clear(f"Readout unavailable: {exc}")
        self._set_enabled()

    def invalidate_preview(self, *_, preserve_view=False):
        self._preview_revision += 1
        self.preview = None
        automatic = self._automatic_preview or self.tabs.currentIndex() == 3
        valid = bool(self.filtered) and (self.kind.currentText() == "Noise" or any(s.is_image for s in self.filtered))
        if preserve_view and automatic and valid and self.preview_view.preview is not None:
            self._preserve_preview_view = True
            self._message("Updating preview…")
        else:
            self._preserve_preview_view = False
            self.preview_view.clear("No noise readouts in this measurement" if self.kind.currentText() == "Noise" and not self.filtered else "No preview")
        if automatic and valid:
            self._queue_preview()
        else:
            self._pending_preview = False
            self._preview_timer.stop()
            if self.busy and self._job_kind == "preview":
                self._job.cancel.set()
        self._set_enabled()

    def _reconstruction_changed(self, *_):
        if not self._updating:
            self.invalidate_preview(preserve_view=True)

    def _reconstruction_scans(self):
        if not self.ifft_checks["z"].isChecked() or self.kind.currentText() == "Noise":
            return list(self.filtered)
        # The acquisition table keeps its raw-partition filter; a Z transform
        # needs the entire partition encoding for this one slice/counter group.
        mask = self._type_mask().copy()
        for name, control in self.counter_widgets.items():
            if name != "partition" and control.currentData() is not None:
                mask &= self.frame[name] == control.currentData()
        return [self.scans[int(i)] for i in self.frame.index[mask]]

    def build_preview(self, focus=True):
        if self.busy or not self.twix or not self.filtered:
            return False
        self._automatic_preview = True
        if focus:
            self.tabs.setCurrentIndex(3)
        self._preview_timer.stop()
        self._pending_preview = False
        key = self._preview_key()
        if key in self._preview_cache:
            self._preview_cache.move_to_end(key)
            self._display_preview(self._preview_cache[key])
            self._set_enabled()
            return True
        self._active_preview_revision = self._preview_revision
        self._active_preview_key = key
        raw, measurement, scans = self.twix, self.measurement, self._reconstruction_scans()
        channel = int(self.channel.currentData() or 0)
        combine = self.rss.isChecked()
        combination = self.coil_method.currentData() if combine else "single"
        ifft_axes = tuple(name for name, check in self.ifft_checks.items() if check.isChecked())
        noise = self.kind.currentText() == "Noise"
        indexed_scans, maximum = list(self.scans), self.index_limit.value()
        noise_cache = self._noise_cache

        def work(cancel, progress):
            # Interpose cancellation between individual channel/header reads.
            total_reads = max(1, len(scans) if noise else sum(s.channels if combine else 1 for s in scans if s.is_image))
            if combination == "adaptive" and not noise:
                total_reads = total_reads * 2 + 32 * max((s.channels for s in scans), default=1)
            class Reader:
                done = 0

                def channel_ids(self, m, s):
                    if cancel.is_set():
                        raise InterruptedError("Cancelled")
                    return raw.channel_ids(m, s)

                def read_signal(self, m, s, c):
                    if cancel.is_set():
                        raise InterruptedError("Cancelled")
                    value = raw.read_signal(m, s, c)
                    self.done += 1
                    if self.done == 1 or self.done % 32 == 0 or self.done == total_reads:
                        progress(min(self.done / total_reads, 0.99), self.done)
                    return value

                def noise_calibration(self, m, receiver_ids):
                    key = (m.index, tuple(receiver_ids))
                    if key in noise_cache:
                        return noise_cache[key]
                    # Prefer this measurement, then the closest preceding RAID
                    # measurement. Never pool unrelated noise measurements.
                    candidates = [m] + sorted((other for other in raw.measurements if other.index != m.index),
                                              key=lambda other: (other.index > m.index, abs(other.index - m.index)))
                    result = NoiseCalibration()
                    rejected = []
                    for candidate in candidates:
                        if cancel.is_set():
                            raise InterruptedError("Cancelled")
                        if candidate.index == m.index:
                            source = indexed_scans
                        else:
                            def report(fraction, count):
                                if cancel.is_set():
                                    raise InterruptedError("Cancelled")
                            source = raw.index_measurement(candidate, max_scans=maximum, progress=report).scans
                        try:
                            result = estimate_noise_covariance(self, candidate, source, receiver_ids)
                        except ValueError as error:
                            rejected.append(f"Noise calibration in measurement {candidate.index} was rejected: {error}")
                            continue
                        if result.covariance is not None:
                            break
                    result.warnings.extend(rejected)
                    if result.covariance is None:
                        result.metadata["noise_source"] = "equal uncorrelated noise assumption"
                    if cancel.is_set():
                        raise InterruptedError("Cancelled")
                    # Small bounded cache, separate from the image/volume cache.
                    if len(noise_cache) >= 4:
                        noise_cache.pop(next(iter(noise_cache)))
                    noise_cache[key] = result
                    return result

            preview = (build_noise_preview(Reader(), measurement, scans, channel_index=channel) if noise else
                       build_cartesian_preview(Reader(), measurement, scans, channel_index=channel,
                                               combination=combination, ifft_axes=ifft_axes))
            if cancel.is_set():
                raise InterruptedError("Cancelled")
            return preview

        self.cancel_button.setEnabled(True)
        self._message("Reading noise samples…" if noise else "Building a bounded Cartesian preview…")
        return self._start_job("preview", work)

    def _preview_key(self):
        return (self.measurement.index, self.kind.currentText(),
                tuple(combo.currentData() for combo in self.counter_widgets.values()),
                int(self.channel.currentData() or 0), self.rss.isChecked(), self.coil_method.currentData(),
                tuple(name for name, check in self.ifft_checks.items() if check.isChecked()))

    def _remember_preview(self, key, preview):
        def used(item):
            return sum(getattr(item, name).nbytes for name in ("kspace", "image", "coverage", "lines"))
        if key in self._preview_cache:
            self._preview_cache_bytes -= used(self._preview_cache.pop(key))
        size = used(preview)
        if size > 32 * 1024 * 1024:
            return
        self._preview_cache[key] = preview
        self._preview_cache_bytes += size
        while len(self._preview_cache) > 8 or self._preview_cache_bytes > 32 * 1024 * 1024:
            _, old = self._preview_cache.popitem(last=False)
            self._preview_cache_bytes -= used(old)

    def _display_preview(self, preview):
        self.preview = preview
        self.preview_view.set_preview(preview, preserve_view=self._preserve_preview_view)
        noise = preview.metadata.get("display_kind") == "noise"
        self._preserve_preview_view = False
        self._render_signal()
        self._message("Noise readouts ready" if noise else "Cartesian preview ready")

    def _queue_preview(self):
        if self._close_pending or not self.twix or not self.filtered:
            return
        if self.busy and self._job_kind != "preview":
            return
        self._pending_preview = True
        self._preview_timer.start()
        if self.busy and self._job_kind == "preview":
            self._job.cancel.set()

    def _refresh_preview(self):
        if not self._pending_preview or self._close_pending:
            return
        if self.busy:
            return  # _job_finished restarts this timer for the latest selection.
        self._pending_preview = False
        if self.twix and self.filtered:
            self.build_preview(focus=False)

    def _tab_changed(self, index):
        if hasattr(self, "preview_view"):
            layout = self.preview_view.image_layout if index == 3 else self._workspace_layout
            if self.slice_navigation.parentWidget() is not layout.parentWidget():
                layout.addWidget(self.slice_navigation)
                self.slice_navigation.setVisible(self.kind.currentText() != "Noise" and not (index == 3 and self.ifft_checks["z"].isChecked()))
        if index == 6 and hasattr(self, "blocks_view"):
            self._update_blocks()
        if index == 3 and not self._updating and hasattr(self, "preview_view") and self.twix:
            self._automatic_preview = True
            if self.preview is None:
                self._queue_preview()
        if hasattr(self, "cancel_button"):
            self._set_enabled()

    def _update_blocks(self):
        if not self.twix or not self.measurement:
            self.blocks_view.clear()
            return
        if self._blocks_dirty:
            scan = self.current_scan() if self.scans else None
            self.blocks_view.set_data(self.twix, self.measurement, self.filtered,
                                      selected_scan=scan, selected_channel=int(self.channel.currentData() or 0))
            self._blocks_dirty = False

    def _block_selected(self, scan_index, channel_index):
        if not 0 <= scan_index < len(self.scans) or (self.busy and self._job_kind != "preview"):
            return
        self._updating = True
        self.scan_spin.setValue(scan_index)
        self._updating = False
        self._refresh_channel_selector()
        self.select_channel(channel_index, focus=False)
        self.tabs.setCurrentIndex(2)
        if not self.busy:
            self._render_signal()

    def _navigation_axis_changed(self, *_):
        if not self._updating:
            self._refresh_slice_navigation()
            self._set_enabled()

    def _refresh_slice_navigation(self):
        axis = self.slice_axis.currentData() or "slice"
        values = []
        if self.scans:
            mask = pd.Series(True, index=self.frame.index)
            for name, combo in self.counter_widgets.items():
                value = combo.currentData()
                if name != axis and value is not None:
                    mask &= self.frame[name] == value
            mask &= self._type_mask()
            values = sorted(int(v) for v in self.frame.loc[mask, axis].unique())
        self._slice_values = values
        self.slice_minimum.setText(str(values[0]) if values else "—")
        self.slice_maximum.setText(str(values[-1]) if values else "—")
        previous = self._updating
        self._updating = True
        try:
            current = self.counter_widgets[axis].currentData()
            position = values.index(current) if current in values else -1
            self.slice_slider.setRange(0, max(0, len(values) - 1))
            self.slice_slider.setValue(max(0, position))
            self.slice_spin.setRange(0, len(values))
            self.slice_spin.setValue(position + 1)
            self.slice_position.setText(f"of {len(values)} · counter {current}" if position >= 0 else (f"All {len(values)} {axis}s" if values else f"No {axis}s"))
            self.slice_slider.setToolTip(f"Wheel down/up: next/previous {axis} · Page Up / Page Down")
        finally:
            self._updating = previous

    def _slice_spin_changed(self, value):
        if self._updating:
            return
        if value > 0:
            self.navigate_slice(value - 1)
        elif not self.busy or self._job_kind == "preview":
            axis = self.slice_axis.currentData() or "slice"
            self.counter_widgets[axis].setCurrentIndex(0)

    def navigate_slice(self, position):
        if self._updating or not self._slice_values or (self.busy and self._job_kind != "preview") or self._close_pending:
            return False
        position = max(0, min(int(position), len(self._slice_values) - 1))
        axis = self.slice_axis.currentData() or "slice"
        combo = self.counter_widgets[axis]
        target = combo.findData(self._slice_values[position])
        if target < 0 or target == combo.currentIndex():
            return False
        self._navigation_change = True
        try:
            combo.setCurrentIndex(target)
        finally:
            self._navigation_change = False
        return True

    def step_slice(self, delta):
        if self.tabs.currentIndex() == 3 and self.ifft_checks["z"].isChecked() and self.preview_view.volume_depth > 1:
            return self.preview_view.set_plane(self.preview_view.plane_index + int(delta))
        if not self._slice_values:
            return False
        axis = self.slice_axis.currentData() or "slice"
        current = self.counter_widgets[axis].currentData()
        if current in self._slice_values:
            position = self._slice_values.index(current) + int(delta)
        else:
            position = 0 if delta > 0 else len(self._slice_values) - 1
        return self.navigate_slice(position)

    def _buffer_changed(self, *_):
        if self._updating:
            return
        text = self.measurement.buffers.get(self.raw_buffer.currentText(), "") if self.measurement else ""
        limit = 2 * 1024 * 1024
        self.raw_text.setPlainText(text[:limit])
        self.raw_text.setToolTip(f"{len(text):,} characters. Export contains the full original buffer text.")
        self.header_tree.set_buffer(text[:limit])
        self.header_tree.filter_parameters(self.header_search.text())
        self.raw_limit_notice.setText("Display limited")
        self.raw_limit_notice.setToolTip("The source view is limited to 2 MiB and the tree to 4,096 parameters. Header export retains the full original buffers.")
        self.raw_limit_notice.setVisible(len(text) > limit or self.header_tree.limited)
        self._find_header_from_start()

    def _jump_header_parameter(self, path):
        cursor = self.raw_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.raw_text.setTextCursor(cursor)
        self.raw_text.find(path)

    def _find_header_from_start(self, *_):
        cursor = self.raw_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.raw_text.setTextCursor(cursor)
        if self.header_search.text():
            self.find_next_header()

    def find_next_header(self):
        query = self.header_search.text()
        if not query:
            return
        if not self.raw_text.find(query):
            cursor = self.raw_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.raw_text.setTextCursor(cursor)
            if not self.raw_text.find(query):
                self.statusBar().showMessage("No matching text in the displayed buffer.", 4000)

    def export_dialog(self, kind):
        specifications = {
            "scans": ("acquisitions.csv", "CSV (*.csv)"), "signal": ("readout.csv", "CSV (*.csv)"),
            "metadata": ("measurement.json", "JSON (*.json)"), "headers": ("raw_headers.txt", "Text (*.txt)"),
            "preview": ("cartesian_preview.npz", "NumPy archive (*.npz)"),
            "signal_png": ("signal.png", "PNG image (*.png)"), "preview_png": ("preview.png", "PNG image (*.png)"),
            "ismrmrd": ("raw_dataset.h5", "ISMRMRD HDF5 (*.h5 *.mrd)"),
        }
        default, filter_text = specifications[kind]
        path, _ = QFileDialog.getSaveFileName(self, "Export raw-data inspection", default, filter_text)
        if path:
            try:
                if kind == "ismrmrd":
                    self.export_ismrmrd(path)
                    return
                self.export_to(kind, path)
                self._message(f"Exported {Path(path).name}")
            except Exception as exc:
                self._message(f"Export failed: {exc}", error=True)

    def export_ismrmrd(self, path):
        if self.busy or not self.twix:
            raise ValueError("Wait for the current operation and open a measurement before exporting.")
        raw, measurement, scans = self.twix, self.measurement, list(self.scans)
        complete = self.scan_index.complete
        self._preview_timer.stop()
        self._pending_preview = False

        def work(cancel, progress):
            return export_ismrmrd(raw, measurement, scans, path, index_complete=complete,
                                 cancel=cancel, progress=progress)

        self._message("Exporting indexed ADC acquisitions to ISMRMRD…")
        return self._start_job("export_ismrmrd", work)

    def export_to(self, kind, path):
        if self.busy:
            raise ValueError("Wait for the current operation before exporting.")
        if not self.twix:
            raise ValueError("Open a dataset first.")
        path = Path(path).expanduser().resolve()
        if path == self.twix.path or (path.exists() and path.samefile(self.twix.path)):
            raise ValueError("Exports cannot overwrite the open source raw-data file.")
        if kind == "scans":
            self.acq_proxy.filtered_frame().to_csv(path, index=False)
        elif kind == "signal":
            scan = self.current_scan()
            channel = int(self.channel.currentData() or 0)
            signal = self.twix.read_signal(self.measurement, scan, channel)
            pd.DataFrame({"sample": np.arange(len(signal)), "real": signal.real, "imaginary": signal.imag, "magnitude": np.abs(signal), "phase_rad": np.angle(signal)}).to_csv(path, index=False)
        elif kind == "metadata":
            metadata = {"source": self.source_name, "format": self.twix.format,
                        "size_bytes": self.twix.size, "measurement": asdict(self.measurement),
                        "indexed_acquisitions": len(self.scans), "index_complete": self.scan_index.complete,
                        "index_warnings": self.scan_index.warnings}
            path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        elif kind == "headers":
            path.write_text("\n\n".join(f"===== {name} =====\n{text}" for name, text in self.measurement.buffers.items()), encoding="utf-8")
        elif kind == "preview":
            if self.preview is None:
                raise ValueError("Build a preview first.")
            with path.open("wb") as output:
                arrays = ({"raw_samples": self.preview.kspace, "magnitude": self.preview.image, "acquisition_indices": self.preview.lines}
                          if self.preview.metadata.get("display_kind") == "noise" else
                          {"kspace": self.preview.kspace, "image": self.preview.image, "coverage": self.preview.coverage, "lines": self.preview.lines})
                np.savez_compressed(output, **arrays, metadata=json.dumps(self.preview.metadata, default=str),
                                    warnings=np.asarray(self.preview.warnings, dtype=str))
        elif kind == "signal_png":
            self.signal_view.export_image(path)
        elif kind == "preview_png":
            self.preview_view.export_images(path)
        elif kind == "ismrmrd":
            export_ismrmrd(self.twix, self.measurement, self.scans, path, index_complete=self.scan_index.complete)
        else:
            raise ValueError(f"Unknown export type: {kind}")
        return path

    def close_dataset(self):
        if self.busy:
            return False
        self._preview_timer.stop()
        self._pending_preview = False
        self._automatic_preview = False
        self._preserve_preview_view = False
        self._preview_revision += 1
        self._preview_cache.clear()
        self._noise_cache.clear()
        self._preview_cache_bytes = 0
        if self.twix:
            self.twix.close()
        self.twix = self.measurement = self.scan_index = self.preview = None
        self.scans, self.filtered = [], []
        self.frame = pd.DataFrame()
        self.source_name = ""
        self._updating = True
        self.measurement_tree.clear()
        self.acq_model.set_frame(pd.DataFrame())
        self.protocol_model.set_frame(pd.DataFrame(columns=["Parameter", "Value"]))
        self.raw_buffer.clear()
        self.raw_text.clear()
        self.header_tree.clear()
        self.raw_text.setToolTip("")
        self.raw_limit_notice.hide()
        self.acq_search.clear()
        self.protocol_search.clear()
        self.header_search.clear()
        self.scan_spin.setRange(0, 0)
        for combo in self.counter_widgets.values():
            combo.clear()
            combo.addItem("All", None)
        self.channel.clear()
        self.channel.addItem("0", 0)
        self.channel_selector.clear()
        self.blocks_view.clear()
        self._blocks_dirty = True
        self._updating = False
        self._refresh_slice_navigation()
        self.signal_view.clear()
        self.preview_view.clear()
        self.overview_stack.setCurrentIndex(0)
        self.tabs.setCurrentIndex(0)
        self.dataset_header.setText("No dataset open  |  Siemens MRI Twix VB / VD / VE")
        self.dataset_header.setToolTip("")
        self.notice.hide()
        self.setWindowTitle("Raw MRI Data Explorer")
        self._set_enabled()
        self._message("Dataset closed")
        return True

    def set_theme(self, name):
        selected = name if name in THEME_NAMES else "cockpit"
        try:
            assets = load_theme(selected)
            for glyph in GLYPH_NAMES:
                glyph_icon(glyph, assets.colors)
        except (ValueError, OSError) as error:
            self._message(str(error), error=True)
            for action in self.theme_group.actions():
                action.setChecked(action.data() == self.theme_name)
            return False
        self.theme_name = selected
        apply_theme_assets(QApplication.instance(), assets)
        self._update_control_glyphs(assets.colors)
        self.blocks_view.set_theme(self.theme_name, assets=assets)
        self.signal_view.set_theme(assets.colors)
        self.preview_view.set_theme(assets.colors)
        for widget in QApplication.instance().allWidgets():
            if isinstance(widget, ClassicTitleBar):
                widget.set_theme(self.theme_name, assets=assets)
        for action in self.theme_group.actions():
            action.setChecked(action.data() == self.theme_name)
        self.notice.hide()
        return True

    def reload_theme_assets(self):
        clear_glyph_cache()
        if self.set_theme(self.theme_name):
            icon = application_icon()
            QApplication.instance().setWindowIcon(icon)
            for widget in QApplication.instance().topLevelWidgets():
                widget.setWindowIcon(icon)
            for dock in (self.explorer_dock, self.selection_dock):
                dock.setWindowIcon(icon)
            if self.measurement_tree.topLevelItemCount():
                self.measurement_tree.topLevelItem(0).setIcon(0, icon)
            update_windows_icon(icon)
            self._message("Appearance assets reloaded")

    def _update_close_icon(self):
        self.close_action.setIcon(glyph_icon("close", self.theme_name))

    def _update_control_glyphs(self, colors=None):
        theme = colors or self.theme_name
        for action, name in ((self.open_action, "open"), (self.close_action, "close"), (self.reindex_action, "reindex")):
            action.setIcon(glyph_icon(name, theme))
        controls = ((self.export_button, "export"), (self.browse_button, "browse"),
                    (self.reindex_button, "reindex"), (self.reset_filters_button, "reindex"),
                    (self.previous_slice_button, "previous"), (self.next_slice_button, "next"),
                    (self.signal_view.reset_button, "fit"), (self.preview_view.reset_button, "fit"),
                    (self.preview_view.auto_levels_button, "auto-levels"))
        for button, name in controls:
            button.setIcon(glyph_icon(name, theme))
            if name in {"previous", "next"}:
                button.setText("")

    def toggle_fullscreen(self, checked=False):
        if self.isFullScreen():
            self.showMaximized()
            self.fullscreen_action.setChecked(False)
        else:
            self.showFullScreen()
            self.fullscreen_action.setChecked(True)

    def about(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("About Raw MRI Data Explorer")
        dialog.setIconPixmap(application_icon().pixmap(48, 48))
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"Raw MRI Data Explorer {__version__}\n\nTarget: Siemens Twix RAWDATA (.dat), VB and VD/VE.\nNative Python / Qt viewer for read-only inspection, individual receive channels, and Cartesian quick looks.\n\nSequence-specific reconstruction and scanner image orientation are not applied.")
        dialog.exec()

    def dragEnterEvent(self, event):
        if not self.busy and event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.busy:
            return
        for url in event.mimeData().urls():
            if url.isLocalFile() and Path(url.toLocalFile()).is_file():
                self.open_path(url.toLocalFile())
                event.acceptProposedAction()
                break

    def closeEvent(self, event):
        if self.busy:
            self._close_pending = True
            self.cancel_job()
            self._message("Finishing the current read before closing…")
            event.ignore()
            return
        self._save_preferences()
        self.close_dataset()
        event.accept()
