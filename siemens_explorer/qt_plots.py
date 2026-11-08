"""Native pyqtgraph views for individual readouts and Cartesian previews.

All axes show stored sample indices or inferred matrix pixels. These widgets do
not assign scanner image orientation or physical-space coordinates to a preview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSplitter,
    QApplication, QSpinBox, QVBoxLayout, QWidget,
)
from .qt_theme import theme_colors
from .qt_wheel import WheelSlider


def _active_colors():
    app = QApplication.instance()
    return dict(app.property("explorerThemeColors") or theme_colors(app.property("explorerTheme") or "cockpit")) if app else theme_colors()


def _plain_label(text: str = "", parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _style_plot(plot: pg.PlotWidget | pg.PlotItem, colors=None) -> None:
    colors = colors or _active_colors()
    item = plot.getPlotItem() if isinstance(plot, pg.PlotWidget) else plot
    for name in ("bottom", "left"):
        axis = item.getAxis(name)
        axis.setPen(colors["plot_axis"])
        axis.setTextPen(colors["plot_foreground"])
    item.setMenuEnabled(False)
    item.showGrid(x=True, y=True, alpha=0.12)
    item.hideButtons()


class SignalView(QWidget):
    """A native complex readout plot with deterministic display transforms."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("signalView")
        self.signal: np.ndarray | None = None
        self.scan: Any = None
        self.channel_index = 0
        self.channel_id: int | None = None
        self._colors = _active_colors()

        self.mode = QComboBox(self)
        self.mode.setObjectName("signalModeCombo")
        self.mode.setAccessibleName("Signal view")
        self.mode.addItems(["Magnitude", "Real + imaginary", "Phase", "FFT magnitude"])
        self.mode.currentIndexChanged.connect(self._render)
        self.reset_button = QPushButton("Fit readout", self)
        self.reset_button.setObjectName("fitSignalButton")
        self.reset_button.setToolTip("Reset the readout zoom and position.")
        self.reset_button.clicked.connect(self._fit)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Display:"))
        controls.addWidget(self.mode)
        controls.addWidget(self.reset_button)
        controls.addStretch()
        self.controls = controls

        self.info = _plain_label(parent=self)
        self.info.setObjectName("signalInfo")
        self.plot = pg.PlotWidget(background=self._colors["plot_background"])
        self.plot.setObjectName("signalPlot")
        self.plot.setAccessibleName("Complex signal plot")
        self.plot.setMinimumHeight(220)
        self.plot.addLegend(offset=(12, 12), labelTextColor=self._colors["plot_foreground"], brush=self._colors["plot_background"])
        _style_plot(self.plot)
        self.plot.setToolTip("Drag to pan; scroll to zoom. The dashed line marks the recorded center column.")
        self.mode.setToolTip("Choose how to display the stored complex samples.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(controls)
        layout.addWidget(self.info)
        layout.addWidget(self.plot, 1)
        self.clear()

    def set_theme(self, colors):
        self._colors = dict(colors)
        self.plot.setBackground(colors["plot_background"])
        _style_plot(self.plot, colors)
        legend = self.plot.plotItem.legend
        if legend is not None:
            legend.opts["labelTextColor"] = colors["plot_foreground"]
            legend.setBrush(pg.mkBrush(colors["plot_background"]))
            for _, label in legend.items:
                label.setText(label.text, color=colors["plot_foreground"])
        for index, curve in enumerate(self.plot.listDataItems()):
            curve.setPen(pg.mkPen(colors["plot_secondary" if index else "plot_accent"], width=1.5))
        for item in self.plot.items():
            if isinstance(item, pg.InfiniteLine):
                item.setPen(pg.mkPen(colors["plot_center"], width=1, style=Qt.PenStyle.DashLine))

    def set_signal(
        self,
        signal: np.ndarray,
        scan: Any = None,
        channel_index: int = 0,
        channel_id: int | None = None,
    ) -> None:
        values = np.asarray(signal)
        if values.ndim != 1 or values.size == 0:
            raise ValueError("A signal view requires a non-empty one-dimensional readout.")
        self.signal = np.array(values, dtype=np.complex64, copy=True)
        self.scan = scan
        self.channel_index = int(channel_index)
        self.channel_id = None if channel_id is None else int(channel_id)
        self.mode.setEnabled(True)
        self.reset_button.setEnabled(True)
        pieces = []
        if scan is not None:
            pieces.extend([
                f"Acquisition {scan.index}", f"line {scan.line}",
                f"slice {scan.slice}", f"echo {scan.echo}",
            ])
        pieces.append(f"channel ordinal {self.channel_index}")
        if self.channel_id is not None:
            pieces.append(f"physical ID {self.channel_id}")
        pieces.append(f"{values.size:,} complex samples")
        finite = np.isfinite(self.signal)
        if finite.all():
            rms = np.sqrt(np.mean(np.square(np.abs(self.signal).astype(np.float64))))
            pieces.append(f"RMS {rms:.5g}")
        else:
            pieces.append(f"{np.count_nonzero(~finite):,} non-finite samples")
        self.info.setText("  |  ".join(pieces))
        self._render()

    def clear(self, message: str = "Select an acquisition to inspect its complex readout.") -> None:
        self.signal = None
        self.scan = None
        self.channel_id = None
        self.plot.clear()
        if self.plot.plotItem.legend is not None:
            self.plot.plotItem.legend.clear()
        self.plot.setLabel("bottom", "Sample index")
        self.plot.setLabel("left", "Amplitude", units="stored units")
        self.info.setText(message)
        self.mode.setEnabled(False)
        self.reset_button.setEnabled(False)

    def _fit(self) -> None:
        self.plot.enableAutoRange()

    def _render(self, *_args) -> None:
        if self.signal is None:
            return
        self.plot.clear()
        if self.plot.plotItem.legend is not None:
            self.plot.plotItem.legend.clear()
        signal = self.signal
        x = np.arange(signal.size)
        mode = self.mode.currentText()
        self.plot.setLabel("bottom", "Sample index")
        self.plot.setLabel("left", "Amplitude", units="stored units")
        curve_options = {"connect": "finite"}
        if mode == "Real + imaginary":
            self.plot.plot(x, signal.real, name="Real", pen=pg.mkPen(self._colors["plot_accent"], width=1.5), **curve_options)
            self.plot.plot(x, signal.imag, name="Imaginary", pen=pg.mkPen(self._colors["plot_secondary"], width=1.5), **curve_options)
        else:
            values = np.abs(signal)
            if mode == "Phase":
                values = np.angle(signal)
                self.plot.setLabel("left", "Phase", units="radians")
            elif mode == "FFT magnitude":
                values = np.abs(np.fft.fftshift(np.fft.fft(signal)))
                x = np.arange(signal.size) - signal.size // 2
                self.plot.setLabel("bottom", "Frequency bin (centered)")
            self.plot.plot(x, values, name=mode, pen=pg.mkPen(self._colors["plot_accent"], width=1.5), **curve_options)
        if self.scan is not None and mode != "FFT magnitude":
            marker = pg.InfiniteLine(
                pos=int(self.scan.center_column), angle=90,
                pen=pg.mkPen(self._colors["plot_center"], width=1, style=Qt.PenStyle.DashLine),
            )
            marker.setZValue(-1)
            self.plot.addItem(marker)
        self.plot.enableAutoRange()

    def export_image(self, path: str | Path) -> None:
        if self.signal is None:
            raise ValueError("Select a signal before exporting its plot.")
        from pyqtgraph.exporters import ImageExporter

        ImageExporter(self.plot.plotItem).export(str(path))


class PreviewView(QWidget):
    """Side-by-side inferred k-space and magnitude IFFT / coil RSS views."""

    slice_step = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("previewView")
        self.preview: Any = None
        self.plane_index = 0
        self._setting_plane = False
        self._colors = _active_colors()
        self._slice_navigation_enabled = False
        self._wheel_angle_remainder = 0
        self._wheel_pixel_remainder = 0
        self.mode = QComboBox(self)
        self.mode.setObjectName("kspaceModeCombo")
        self.mode.setAccessibleName("Data display")
        self.mode.addItems(["Log magnitude", "Magnitude", "Phase"])
        self.mode.currentIndexChanged.connect(self._render_kspace)
        self.auto_levels_button = QPushButton("Auto levels", self)
        self.auto_levels_button.setObjectName("autoPreviewLevelsButton")
        self.auto_levels_button.setToolTip("Reset both images' window and level to fit their data.")
        self.auto_levels_button.clicked.connect(self._auto_levels)
        self.reset_button = QPushButton("Fit view", self)
        self.reset_button.setObjectName("fitPreviewButton")
        self.reset_button.setToolTip("Fit the displayed data without changing window and level.")
        self.reset_button.clicked.connect(self._fit)
        self.controls = QHBoxLayout()
        self.controls.addWidget(QLabel("Display:"))
        self.controls.addWidget(self.mode)
        self.controls.addWidget(self.auto_levels_button)
        self.controls.addWidget(self.reset_button)
        self.controls.addStretch()
        self.mode.setToolTip("K-space display transform. Stored data is unchanged.")

        self.kspace_title = _plain_label("K-space · log(1 + magnitude)")
        self.image_title = _plain_label("Image · magnitude IFFT")
        self.kspace_view = self._make_image_view("kspaceImage", "Readout matrix column", "Phase-encode matrix row")
        self.image_view = self._make_image_view("magnitudeImage", "Pixel column", "Pixel row")
        # HistogramLUTWidget has its own QGraphicsView. Filtering only these two
        # image-canvas viewports leaves histogram/gradient interactions intact.
        self._slice_viewports = (
            self.kspace_view.ui.graphicsView.viewport(),
            self.image_view.ui.graphicsView.viewport(),
        )
        for viewport in self._slice_viewports:
            viewport.installEventFilter(self)
        left, right = QWidget(), QWidget()
        for panel, title, view in (
            (left, self.kspace_title, self.kspace_view),
            (right, self.image_title, self.image_view),
        ):
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            panel_layout.addWidget(title)
            panel_layout.addWidget(view, 1)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("previewImageSplitter")
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([500, 500])
        self.stats = _plain_label(parent=self)
        self.stats.setObjectName("previewStats")
        self.notes = QPlainTextEdit(self)
        self.notes.setObjectName("previewNotes")
        self.notes.setReadOnly(True)
        self.notes.setMinimumHeight(48)
        self.notes.setToolTip("Preview notes and reconstruction limitations. Drag the divider above to resize.")
        self.notes.setAccessibleName("Cartesian preview qualifications")
        self.details_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.details_splitter.setObjectName("previewDetailsSplitter")
        self.details_splitter.setChildrenCollapsible(False)
        self.details_splitter.setHandleWidth(6)
        self.image_panel = QWidget()
        self.image_layout = QVBoxLayout(self.image_panel)
        self.image_layout.setContentsMargins(0, 0, 0, 0)
        self.image_layout.setSpacing(3)
        self.image_layout.addWidget(self.splitter, 1)
        self.volume_navigation = QWidget()
        self.volume_navigation.setObjectName("volumeNavigation")
        volume_layout = QHBoxLayout(self.volume_navigation)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.addWidget(QLabel("Z plane:"))
        self.volume_slider = WheelSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volumePlaneSlider")
        self.volume_slider.setAccessibleName("Reconstructed Z plane")
        self.volume_slider.setToolTip("Reconstructed Z plane, not a raw partition counter. Wheel down/up to change plane.")
        self.volume_slider.valueChanged.connect(self._plane_changed)
        self.volume_minimum, self.volume_maximum = QLabel("1"), QLabel()
        for endpoint in (self.volume_minimum, self.volume_maximum):
            endpoint.setObjectName("sliderEndpoint")
        volume_layout.addWidget(self.volume_minimum)
        volume_layout.addWidget(self.volume_slider, 1)
        volume_layout.addWidget(self.volume_maximum)
        self.volume_spin = QSpinBox()
        self.volume_spin.setObjectName("volumePlanePosition")
        self.volume_spin.setAccessibleName("Reconstructed Z plane position")
        self.volume_spin.setToolTip("Plane position is 1-based. Exported NumPy volume indices start at zero.")
        self.volume_spin.setFixedWidth(72)
        self.volume_spin.setKeyboardTracking(False)
        self.volume_spin.valueChanged.connect(lambda value: self._plane_changed(value - 1))
        volume_layout.addWidget(self.volume_spin)
        self.volume_position = QLabel()
        volume_layout.addWidget(self.volume_position)
        self.image_layout.addWidget(self.volume_navigation)
        self.volume_navigation.hide()
        self.details_splitter.addWidget(self.image_panel)
        self.details_splitter.addWidget(self.notes)
        self.details_splitter.setStretchFactor(0, 1)
        self.details_splitter.setStretchFactor(1, 0)
        self.details_splitter.handle(1).setToolTip("Drag to resize Preview notes")
        self._notes_height = 100
        self.notes.hide()
        self._update_canvas_tooltips()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(self.controls)
        layout.addWidget(self.details_splitter, 1)
        layout.addWidget(self.stats)
        self.clear()

    def set_theme(self, colors):
        self._colors = dict(colors)
        for view in (self.kspace_view, self.image_view):
            view.setStyleSheet(f"background: {colors['plot_background']};")
            view.ui.graphicsView.setBackground(colors["plot_background"])
            view.getView().getViewBox().setBackgroundColor(colors["plot_background"])
            _style_plot(view.getView(), colors)
            histogram = view.getHistogramWidget()
            histogram.setBackground(colors["plot_background"])
            histogram.item.axis.setPen(colors["plot_axis"])
            histogram.item.axis.setTextPen(colors["plot_foreground"])
            gradient = histogram.item.gradient
            gradient.tickPen = pg.mkPen(colors["plot_foreground"])
            for tick, _ in gradient.listTicks():
                tick.pen = tick.currentPen = gradient.tickPen
                tick.hoverPen = pg.mkPen(colors["plot_accent"], width=2)
                tick.update()
            histogram.item.fillHistogram(color=colors["plot_axis"])
            histogram.item.plot.setPen(pg.mkPen(colors["plot_foreground"]))
            region_color = pg.mkColor(colors["plot_axis"])
            region_color.setAlpha(35)
            histogram.item.region.setBrush(region_color)
            region_color.setAlpha(60)
            histogram.item.region.setHoverBrush(region_color)
            for line in histogram.item.region.lines:
                line.setPen(pg.mkPen(colors["plot_foreground"]))
        if self.preview is not None:
            self._render_kspace(preserve_view=True)

    def set_notes_visible(self, visible: bool) -> None:
        """Keep qualifications available without taking image space by default."""
        if visible == (not self.notes.isHidden()):
            return
        if visible:
            self.notes.show()
            available = max(1, self.details_splitter.height() - self.details_splitter.handleWidth())
            self.details_splitter.setSizes([max(1, available - self._notes_height), self._notes_height])
        else:
            height = self.details_splitter.sizes()[1]
            if height > 0:
                self._notes_height = height
            self.notes.hide()

    def _update_canvas_tooltips(self) -> None:
        axis = "Z plane" if self.volume_depth else "slice"
        navigation = f"Wheel: previous/next {axis} · Ctrl+wheel: zoom" if self._slice_navigation_enabled else "Scroll to zoom"
        noise = self.preview is not None and self.preview.metadata.get("display_kind") == "noise"
        axes = "Rows are readouts in acquisition order; columns are sample indices." if noise else "Axes are inferred matrix pixels."
        tooltip = f"{navigation} · Drag to pan. {axes}"
        for view in (self.kspace_view, self.image_view):
            view.ui.graphicsView.setToolTip(tooltip)
            view.getHistogramWidget().setToolTip("Drag the histogram bounds to change window and level; edit the gradient to change colors.")

    @staticmethod
    def _make_image_view(name: str, x_label: str, y_label: str) -> pg.ImageView:
        plot = pg.PlotItem()
        _style_plot(plot)
        plot.setLabel("bottom", x_label)
        plot.setLabel("left", y_label)
        plot.invertY(True)
        plot.setAspectLocked(True)
        # ImageView 0.14 reads an explicitly supplied item's current image in its
        # constructor, so seed one pixel before clearing the finished widget.
        image_item = pg.ImageItem(np.zeros((1, 1), dtype=np.float32), axisOrder="row-major")
        view = pg.ImageView(view=plot, imageItem=image_item)
        view.setObjectName(name)
        view.setMinimumSize(220, 220)
        colors = _active_colors()
        view.setStyleSheet(f"background: {colors['plot_background']};")
        plot.getViewBox().setBackgroundColor(colors["plot_background"])
        view.getView().setMenuEnabled(False)
        view.ui.roiBtn.hide()
        view.ui.menuBtn.hide()
        histogram = view.getHistogramWidget()
        # Keep image-canvas geometry stable when histogram tick labels change
        # between slices; an aspect-locked ViewBox otherwise changes its range.
        histogram.setFixedWidth(115)
        histogram.item.axis.setPen(colors["plot_axis"])
        histogram.item.axis.setTextPen(colors["plot_foreground"])
        histogram.item.gradient.loadPreset("grey")
        return view

    def set_slice_navigation_enabled(self, enabled: bool) -> None:
        """Use ordinary image-canvas wheel events for slice stepping.

        Positive ``slice_step`` values request later slices; negative values
        request earlier slices. Ctrl+wheel always retains pyqtgraph's zoom.
        """
        self._slice_navigation_enabled = bool(enabled)
        self._wheel_angle_remainder = 0
        self._wheel_pixel_remainder = 0
        self._update_canvas_tooltips()

    def eventFilter(self, watched, event) -> bool:
        if (
            self._slice_navigation_enabled
            and watched in self._slice_viewports
            and event.type() == QEvent.Type.Wheel
        ):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._wheel_angle_remainder = 0
                self._wheel_pixel_remainder = 0
                return False
            delta = event.angleDelta().y()
            if delta:
                self._wheel_angle_remainder += delta
                steps = int(self._wheel_angle_remainder / 120)
                self._wheel_angle_remainder -= steps * 120
            else:
                delta = event.pixelDelta().y()
                if not delta:
                    # Horizontal-only scrolling is not a slice gesture.
                    return False
                self._wheel_pixel_remainder += delta
                steps = int(self._wheel_pixel_remainder / 40)
                self._wheel_pixel_remainder -= steps * 40
            if steps:
                # Conventional DICOM interaction: wheel down advances slices.
                self.slice_step.emit(-steps)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _capture_image_view(view: pg.ImageView) -> tuple[tuple, list]:
        return tuple(view.getLevels()), [list(axis) for axis in view.getView().getViewBox().viewRange()]

    @staticmethod
    def _restore_image_view(view: pg.ImageView, state: tuple[tuple, list]) -> None:
        levels, ranges = state
        view.setLevels(*levels)
        view.getView().getViewBox().setRange(
            xRange=ranges[0], yRange=ranges[1], padding=0, disableAutoRange=True,
        )

    @property
    def volume_depth(self):
        return self.preview.image.shape[0] if self.preview is not None and self.preview.image.ndim == 3 else 0

    def _plane_changed(self, index):
        if not self._setting_plane:
            self.set_plane(index)

    def set_plane(self, index):
        if not self.volume_depth:
            return False
        index = max(0, min(int(index), self.volume_depth - 1))
        if index == self.plane_index:
            return False
        self.set_preview(self.preview, preserve_view=True, plane_index=index)
        return True

    def set_preview(self, preview: Any, preserve_view: bool = False, plane_index: int | None = None) -> None:
        """Display a Y/X matrix or one plane from a Z/Y/X volume.

        Shape-compatible updates retain zoom and histogram levels when requested.
        Noise uses acquisition/sample labels; finite display copies leave the
        original complex arrays available unchanged for numerical export.
        """
        kspace, image = np.asarray(preview.kspace), np.asarray(preview.image)
        if kspace.ndim not in (2, 3) or image.ndim != kspace.ndim or kspace.shape != image.shape or not kspace.size:
            raise ValueError("A preview requires matching, non-empty 2D or 3D k-space and image arrays.")
        if np.asarray(preview.coverage).shape != image.shape:
            raise ValueError("The preview coverage mask must match the image shape.")
        noise = preview.metadata.get("display_kind") == "noise"
        previous_noise = self.preview is not None and self.preview.metadata.get("display_kind") == "noise"
        keep_view = bool(
            preserve_view and self.preview is not None
            and noise == previous_noise
            and np.asarray(self.preview.image).shape[-2:] == image.shape[-2:]
            and self.image_view.image is not None and self.kspace_view.image is not None
        )
        image_state = self._capture_image_view(self.image_view) if keep_view else None
        old_shape = self.preview.image.shape if self.preview is not None else None
        self.preview = preview
        volume = image.ndim == 3
        if volume:
            self.plane_index = max(0, min(plane_index if plane_index is not None else
                                         self.plane_index if preserve_view and old_shape == image.shape else image.shape[0] // 2,
                                         image.shape[0] - 1))
            image = image[self.plane_index]
            self._setting_plane = True
            try:
                self.volume_slider.setRange(0, self.volume_depth - 1)
                self.volume_maximum.setText(str(self.volume_depth))
                self.volume_slider.setValue(self.plane_index)
                self.volume_spin.setRange(1, self.volume_depth)
                self.volume_spin.setValue(self.plane_index + 1)
                self.volume_position.setText(f"of {self.volume_depth}")
            finally:
                self._setting_plane = False
        else:
            self.plane_index = 0
        self.volume_navigation.setVisible(volume)
        self._update_canvas_tooltips()
        self.splitter.widget(1).setVisible(not noise)
        self.kspace_view.getView().setLabel("bottom", "Sample index" if noise else "Readout matrix column")
        self.kspace_view.getView().setLabel("left", "Noise readout row" if noise else "Phase-encode matrix row")
        self.mode.setEnabled(True)
        self.auto_levels_button.setEnabled(True)
        self.reset_button.setEnabled(True)
        axes = preview.metadata.get("ifft_axes", ["x", "y"])
        domain = "Image" if "x" in axes and "y" in axes else "Hybrid" if axes else "K-space"
        method = preview.metadata.get("combination_method", "rss" if preview.channel_count > 1 else "single")
        self.image_title.setText(f"{domain} · " + {"adaptive": "optimal (adaptive)", "rss": "coil RSS", "single": "magnitude"}[method])
        if method == "single" and "selected_channel" in preview.metadata:
            physical = preview.metadata.get("selected_channel_id")
            self.image_title.setText(f"{domain} · channel {preview.metadata['selected_channel']}" +
                                     (f" · ID {physical}" if physical is not None else ""))
        self.image_view.getView().setLabel("bottom", "X pixel" if "x" in axes else "Kx matrix column")
        self.image_view.getView().setLabel("left", "Y pixel" if "y" in axes else "Ky matrix row")
        if volume:
            self.image_title.setText(self.image_title.text() + f" · Z plane {self.plane_index + 1}")
        self.image_title.setToolTip(" · ".join(["Inverse FFT: " + (", ".join(name.upper() for name in axes) or "none"),
                                                   preview.metadata.get("noise_source", "")]).rstrip(" ·"))
        self.image_view.setImage(
            np.nan_to_num(image, copy=True).astype(np.float32),
            axes={"x": 1, "y": 0}, autoLevels=not keep_view, autoRange=not keep_view,
            levels=image_state[0] if image_state else None,
        )
        self.image_view.getHistogramWidget().item.gradient.loadPreset("grey")
        self._render_kspace(preserve_view=keep_view)
        if image_state is not None:
            self._restore_image_view(self.image_view, image_state)
        rows, cols = image.shape
        percentage = 100 * float(np.mean(preview.coverage))
        if noise:
            channel = preview.metadata.get("selected_channel", 0)
            physical = preview.metadata.get("selected_channel_id")
            self.stats.setText(f"{rows:,} noise readouts × {cols:,} samples  |  channel {channel}" + (f" · ID {physical}" if physical is not None else ""))
            self.stats.setToolTip("Rows follow acquisition order; columns are the recorded complex samples. Individual readouts are available in Signal.")
        else:
            self.stats.setText(
            (f"{self.volume_depth:,} × " if volume else "") + f"{rows:,} × {cols:,}  |  {percentage:.1f}% sampled  |  "
            f"{preview.channel_count} {'channels' if preview.channel_count != 1 else 'channel'}"
            )
            self.stats.setToolTip("Acquired positions within the displayed matrix bounds. This is not a measure of scan completeness.")
            if method == "adaptive":
                measured = preview.metadata.get("noise_covariance_used", False)
                self.stats.setText(self.stats.text() + ("  |  measured noise" if measured else "  |  assumed noise"))
                self.stats.setToolTip(self.stats.toolTip() + "\n" + preview.metadata.get("noise_source", "Equal, uncorrelated receiver noise assumed.") +
                                      (f" · {preview.metadata['noise_samples']:,} samples" if "noise_samples" in preview.metadata else ""))
        qualifications = list(preview.warnings)
        if not qualifications:
            qualifications = [
                "Cartesian inspection preview only. Trajectory and image orientation are not verified; "
                "matrix coverage is not a measure of scan completeness."
            ]
        self.notes.setPlainText("\n".join(f"• {message}" for message in qualifications))

    def clear(self, message: str = "Open imaging data to view the selected receive channel.") -> None:
        self.preview = None
        self.plane_index = 0
        self.volume_navigation.hide()
        self.splitter.widget(1).show()
        self.kspace_view.clear()
        self.image_view.clear()
        self.stats.setText(message)
        self.notes.setPlainText(
            "The image is a zero-filled Cartesian inspection preview. No scanner orientation, "
            "trajectory correction, oversampling removal, or parallel-imaging reconstruction is applied."
        )
        self.mode.setEnabled(False)
        self.auto_levels_button.setEnabled(False)
        self.reset_button.setEnabled(False)

    def _render_kspace(self, *_args, preserve_view: bool = False) -> None:
        if self.preview is None:
            return
        state = self._capture_image_view(self.kspace_view) if preserve_view else None
        mode = self.mode.currentText()
        kspace = self.preview.kspace[self.plane_index] if self.preview.kspace.ndim == 3 else self.preview.kspace
        values = np.abs(kspace)
        if mode == "Phase":
            values = np.angle(kspace)
            self.kspace_title.setText("K-space · phase (radians)")
            colors = [self._colors[f"plot_phase_{i}"] for i in range(5)]
        elif mode == "Magnitude":
            self.kspace_title.setText("K-space · magnitude")
            colors = [self._colors[f"plot_magnitude_{i}"] for i in range(6)]
        else:
            values = np.log1p(values)
            self.kspace_title.setText("K-space · log(1 + magnitude)")
            colors = [self._colors[f"plot_magnitude_{i}"] for i in range(6)]
        if self.preview.metadata.get("display_kind") == "noise":
            self.kspace_title.setText(self.kspace_title.text().replace("K-space", "Noise · raw samples"))
        elif self.preview.kspace.ndim == 3:
            partitions = self.preview.metadata.get("partitions", list(range(self.volume_depth)))
            self.kspace_title.setText(self.kspace_title.text() + f" · raw partition {partitions[self.plane_index]}")
        self.kspace_view.setImage(
            np.nan_to_num(values, copy=True).astype(np.float32),
            axes={"x": 1, "y": 0}, autoLevels=not preserve_view, autoRange=not preserve_view,
            levels=state[0] if state else None,
        )
        cmap = pg.ColorMap(np.linspace(0, 1, len(colors)), [pg.mkColor(color) for color in colors])
        self.kspace_view.setColorMap(cmap)
        if state is not None:
            self._restore_image_view(self.kspace_view, state)

    def _auto_levels(self) -> None:
        if self.preview is not None:
            self.kspace_view.autoLevels()
            self.image_view.autoLevels()

    def _fit(self) -> None:
        if self.preview is not None:
            self.kspace_view.autoRange()
            self.image_view.autoRange()

    def export_images(self, path: str | Path) -> None:
        """Save this native widget, including both images and their labels, as PNG."""
        if self.preview is None:
            raise ValueError("Build a preview before exporting its image.")
        if not self.grab().save(str(path), "PNG"):
            raise OSError(f"Could not write preview image: {path}")
