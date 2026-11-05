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
    QComboBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)


BACKGROUND = "#1e1e1e"
FOREGROUND = "#d6d6d6"
ACCENT = "#68c7d2"
SECONDARY = "#efbd79"


def _plain_label(text: str = "", parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _style_plot(plot: pg.PlotWidget | pg.PlotItem) -> None:
    item = plot.getPlotItem() if isinstance(plot, pg.PlotWidget) else plot
    for name in ("bottom", "left"):
        axis = item.getAxis(name)
        axis.setPen("#777777")
        axis.setTextPen(FOREGROUND)
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

        self.mode = QComboBox(self)
        self.mode.setObjectName("signalModeCombo")
        self.mode.setAccessibleName("Signal view")
        self.mode.addItems(["Magnitude", "Real + imaginary", "Phase", "FFT magnitude"])
        self.mode.currentIndexChanged.connect(self._render)
        self.reset_button = QPushButton("Fit readout", self)
        self.reset_button.setObjectName("fitSignalButton")
        self.reset_button.clicked.connect(self._fit)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Display:"))
        controls.addWidget(self.mode)
        controls.addWidget(self.reset_button)
        controls.addStretch()

        self.info = _plain_label(parent=self)
        self.info.setObjectName("signalInfo")
        self.plot = pg.PlotWidget(background=BACKGROUND)
        self.plot.setObjectName("signalPlot")
        self.plot.setAccessibleName("Complex signal plot")
        self.plot.setMinimumHeight(220)
        self.plot.addLegend(offset=(12, 12), labelTextColor=FOREGROUND, brush=(30, 30, 30, 190))
        _style_plot(self.plot)
        self.hint = _plain_label("Drag to pan · scroll to zoom · Fit readout resets the view. Dashed line: recorded center column.")
        self.hint.setObjectName("signalHint")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(controls)
        layout.addWidget(self.info)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.hint)
        self.clear()

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
            self.plot.plot(x, signal.real, name="Real", pen=pg.mkPen(ACCENT, width=1.5), **curve_options)
            self.plot.plot(x, signal.imag, name="Imaginary", pen=pg.mkPen(SECONDARY, width=1.5), **curve_options)
        else:
            values = np.abs(signal)
            if mode == "Phase":
                values = np.angle(signal)
                self.plot.setLabel("left", "Phase", units="radians")
            elif mode == "FFT magnitude":
                values = np.abs(np.fft.fftshift(np.fft.fft(signal)))
                x = np.arange(signal.size) - signal.size // 2
                self.plot.setLabel("bottom", "Frequency bin (centered)")
            self.plot.plot(x, values, name=mode, pen=pg.mkPen(ACCENT, width=1.5), **curve_options)
        if self.scan is not None and mode != "FFT magnitude":
            marker = pg.InfiniteLine(
                pos=int(self.scan.center_column), angle=90,
                pen=pg.mkPen("#8b9296", width=1, style=Qt.PenStyle.DashLine),
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
        self._slice_navigation_enabled = False
        self._wheel_angle_remainder = 0
        self._wheel_pixel_remainder = 0
        self.mode = QComboBox(self)
        self.mode.setObjectName("kspaceModeCombo")
        self.mode.setAccessibleName("K-space display")
        self.mode.addItems(["Log magnitude", "Magnitude", "Phase"])
        self.mode.currentIndexChanged.connect(self._render_kspace)
        self.auto_levels_button = QPushButton("Auto levels", self)
        self.auto_levels_button.setObjectName("autoPreviewLevelsButton")
        self.auto_levels_button.clicked.connect(self._auto_levels)
        self.reset_button = QPushButton("Fit images", self)
        self.reset_button.setObjectName("fitPreviewButton")
        self.reset_button.clicked.connect(self._fit)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("K-space display:"))
        controls.addWidget(self.mode)
        controls.addWidget(self.auto_levels_button)
        controls.addWidget(self.reset_button)
        controls.addStretch()

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
        self.notes = QTextEdit(self)
        self.notes.setObjectName("previewNotes")
        self.notes.setReadOnly(True)
        self.notes.setAcceptRichText(False)
        self.notes.setMaximumHeight(105)
        self.notes.setMinimumHeight(64)
        self.notes.setAccessibleName("Cartesian preview qualifications")
        self.hint = _plain_label("Drag to pan · scroll to zoom · adjust each histogram to change window/level. Axes are inferred matrix pixels.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(controls)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(self.stats)
        layout.addWidget(self.notes)
        layout.addWidget(self.hint)
        self.clear()

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
        view.setStyleSheet("background: #1e1e1e;")
        plot.getViewBox().setBackgroundColor(BACKGROUND)
        view.getView().setMenuEnabled(False)
        view.ui.roiBtn.hide()
        view.ui.menuBtn.hide()
        histogram = view.getHistogramWidget()
        # Keep image-canvas geometry stable when histogram tick labels change
        # between slices; an aspect-locked ViewBox otherwise changes its range.
        histogram.setFixedWidth(115)
        histogram.item.axis.setPen("#777777")
        histogram.item.axis.setTextPen(FOREGROUND)
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
        if enabled:
            self.hint.setText(
                "Wheel: previous/next slice · Ctrl+wheel: zoom · Drag to pan · "
                "Histogram: window/level. Axes are inferred matrix pixels."
            )
        else:
            self.hint.setText(
                "Drag to pan · scroll to zoom · adjust each histogram to change window/level. "
                "Axes are inferred matrix pixels."
            )

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

    def set_preview(self, preview: Any, preserve_view: bool = False) -> None:
        kspace, image = np.asarray(preview.kspace), np.asarray(preview.image)
        if kspace.ndim != 2 or image.ndim != 2 or kspace.shape != image.shape or not kspace.size:
            raise ValueError("A preview requires matching, non-empty 2D k-space and image arrays.")
        if np.asarray(preview.coverage).shape != image.shape:
            raise ValueError("The preview coverage mask must match the image shape.")
        keep_view = bool(
            preserve_view and self.preview is not None
            and np.asarray(self.preview.image).shape == image.shape
            and self.image_view.image is not None and self.kspace_view.image is not None
        )
        image_state = self._capture_image_view(self.image_view) if keep_view else None
        self.preview = preview
        self.mode.setEnabled(True)
        self.auto_levels_button.setEnabled(True)
        self.reset_button.setEnabled(True)
        self.image_title.setText("Image · coil RSS" if preview.channel_count > 1 else "Image · magnitude IFFT")
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
        self.stats.setText(
            f"Matrix {rows:,} × {cols:,}  |  acquired positions within displayed bounds: "
            f"{percentage:.1f}%  |  image channels: {preview.channel_count}"
        )
        qualifications = list(preview.warnings)
        if not qualifications:
            qualifications = [
                "Cartesian inspection preview only. Trajectory and image orientation are not verified; "
                "matrix coverage is not a measure of scan completeness."
            ]
        self.notes.setPlainText("\n".join(f"• {message}" for message in qualifications))

    def clear(self, message: str = "Open imaging data to view the selected receive channel.") -> None:
        self.preview = None
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
        values = np.abs(self.preview.kspace)
        if mode == "Phase":
            values = np.angle(self.preview.kspace)
            self.kspace_title.setText("K-space · phase (radians)")
            colors = ["#5456ba", "#54cbd4", "#f4e49a", "#d96264", "#5456ba"]
        elif mode == "Magnitude":
            self.kspace_title.setText("K-space · magnitude")
            colors = ["#440154", "#414487", "#2a788e", "#22a884", "#7ad151", "#fde725"]
        else:
            values = np.log1p(values)
            self.kspace_title.setText("K-space · log(1 + magnitude)")
            colors = ["#440154", "#414487", "#2a788e", "#22a884", "#7ad151", "#fde725"]
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
