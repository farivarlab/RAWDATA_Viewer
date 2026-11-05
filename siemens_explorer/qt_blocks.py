"""Native, bounded block maps of Twix layout and acquisition/channel records."""

from __future__ import annotations

from math import ceil

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QBrush, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)


COLORS = {
    "container": "#d8dce1", "measurement": "#9cc5e6", "header": "#b9a8d6",
    "channel": "#9ed4c2", "noise": "#e2c697", "sync": "#d7d7d7",
    "selected": "#2369a0", "text": "#202b35",
}


class _Block(QGraphicsRectItem):
    def __init__(self, rectangle, metadata, color, callback=None):
        super().__init__(rectangle)
        self.metadata = metadata
        self.callback = callback
        self.setData(0, metadata)
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor("#708090"), 1))
        self.setAcceptHoverEvents(True)
        self.setToolTip(metadata.get("tooltip", ""))
        if callback is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.callback is not None:
            self.callback(self.metadata["scan_index"], self.metadata["channel_index"])
            event.accept()
            return
        super().mousePressEvent(event)


class _BlockView(QGraphicsView):
    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                current = self.transform().m11()
                factor = 1.15 if delta > 0 else 1 / 1.15
                if 0.08 <= current * factor <= 8:
                    self.scale(factor, factor)
                event.accept()
                return
        super().wheelEvent(event)


class DataBlocksView(QWidget):
    """Inspect layout without reading or reconstructing any signal payloads.

    Acquisition rows are schematic; byte offsets and lengths are exact header
    facts. The file-layout bars use byte-proportional widths, with a one-pixel
    minimum for tiny blocks. Clicking a channel selects its original scan index
    and ordinal channel position, independently of table sort order.
    """

    block_selected = Signal(int, int)
    MAX_ACQUISITIONS = 256
    MAX_CHANNEL_BLOCKS = 4096

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dataBlocksView")
        self.raw = None
        self.measurement = None
        self.scans = []
        self.selected_scan = None
        self.selected_channel = 0
        self._channel_items = {}
        self._visible_acquisitions = 0
        self._visible_channels = 0

        self.mode = QComboBox()
        self.mode.setObjectName("dataBlockMode")
        self.mode.setAccessibleName("Data block representation")
        self.mode.addItems(["Acquisition × channel", "File layout"])
        self.mode.currentIndexChanged.connect(self._render)
        fit = QPushButton("Fit width")
        fit.setObjectName("fitDataBlocks")
        fit.clicked.connect(self.fit_view)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Representation:"))
        controls.addWidget(self.mode)
        controls.addWidget(fit)
        controls.addStretch()
        self.summary = QLabel()
        self.summary.setObjectName("dataBlocksSummary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.scene = QGraphicsScene(self)
        # This diagram uses dark labels on pastel blocks; retain a light canvas
        # when the surrounding application switches to its dark theme.
        self.scene.setBackgroundBrush(QBrush(QColor("#f5f6f7")))
        self.view = _BlockView(self.scene, self)
        self.view.setBackgroundBrush(QBrush(QColor("#f5f6f7")))
        self.view.setObjectName("dataBlockCanvas")
        self.view.setAccessibleName("Raw data block map")
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.view.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setMinimumHeight(240)
        hint = QLabel("Click a channel block to inspect its readout · Hover for byte offsets and lengths · Scroll to browse · Ctrl+scroll to zoom")
        hint.setObjectName("muted")
        hint.setTextFormat(Qt.TextFormat.PlainText)
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(controls)
        layout.addWidget(self.summary)
        layout.addWidget(self.view, 1)
        layout.addWidget(hint)
        self.clear()

    def set_data(self, raw, measurement, scans, selected_scan=None, selected_channel=0):
        if raw is None or measurement is None:
            self.clear()
            return
        self.raw = raw
        self.measurement = measurement
        self.scans = list(scans)
        self.selected_scan = getattr(selected_scan, "index", selected_scan)
        if self.selected_scan is None and self.scans:
            self.selected_scan = self.scans[0].index
        self.selected_channel = max(0, int(selected_channel))
        self._render()

    def clear(self):
        self.raw = self.measurement = None
        self.scans = []
        self.selected_scan = None
        self.selected_channel = 0
        self._channel_items = {}
        self._visible_acquisitions = self._visible_channels = 0
        self.scene.clear()
        self.scene.setSceneRect(QRectF(0, 0, 900, 280))
        self.summary.setText("Open a dataset to inspect its measurement, acquisition, and channel blocks.")
        self._text("No data blocks available", 18, 28, bold=True)

    def _text(self, text, x, y, parent=None, bold=False, color=None):
        item = QGraphicsSimpleTextItem(str(text), parent)
        font = QFont("Tahoma", 9)
        font.setBold(bold)
        item.setFont(font)
        item.setBrush(QBrush(QColor(color or COLORS["text"])))
        item.setPos(x, y)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if parent is None:
            self.scene.addItem(item)
        return item

    def _block(self, x, y, width, height, metadata, color, caption="", clickable=False):
        item = _Block(QRectF(x, y, max(1.0, width), height), metadata, color,
                      self._clicked if clickable else None)
        self.scene.addItem(item)
        if caption:
            # Text is not drawn where the real byte extent is too narrow.
            if width >= 65:
                self._text(caption, x + 5, y + 6, parent=item)
        if clickable:
            self._channel_items.setdefault((metadata["scan_index"], metadata["channel_index"]), []).append(item)
        return item

    def _render(self, *_):
        if self.raw is None or self.measurement is None:
            return
        self.scene.clear()
        self._channel_items = {}
        self._visible_acquisitions = self._visible_channels = 0
        if self.mode.currentIndex() == 1:
            self._render_file()
        else:
            self._render_acquisitions()
        bounds = self.scene.itemsBoundingRect().adjusted(-12, -12, 18, 18)
        self.scene.setSceneRect(bounds)
        self.set_selection(self.selected_scan, self.selected_channel, redraw=False)
        self.fit_view()

    def _render_acquisitions(self):
        self._text("Acquisition headers and receive-channel payloads", 12, 8, bold=True)
        self._text("Schematic rows; each green tile is one channel's complex float32 readout.", 12, 29)
        y = 61
        shown = self.scans[:self.MAX_ACQUISITIONS]
        for scan in shown:
            if self._visible_channels >= self.MAX_CHANNEL_BLOCKS:
                break
            index = int(scan.index)
            count = max(0, int(getattr(scan, "channels", 0)))
            samples = max(0, int(getattr(scan, "samples", 0)))
            flags = tuple(getattr(scan, "flags", ()))
            is_sync = not count or "SYNCDATA" in flags
            heading = f"Acquisition {index}   |   line {getattr(scan, 'line', 0)}   slice {getattr(scan, 'slice', 0)}   echo {getattr(scan, 'echo', 0)}   |   {count} channels × {samples} samples"
            self._text(heading, 12, y, bold=True)
            self._visible_acquisitions += 1
            y += 23
            if is_sync:
                metadata = {"kind": "sync", "scan_index": index, "offset": int(scan.offset), "length": int(scan.length),
                            "tooltip": f"Acquisition {index}\nOffset: {scan.offset:,} bytes\nLength: {scan.length:,} bytes\nFlags: {', '.join(flags) or 'None'}\nNo decoded channel payloads"}
                self._block(12, y, 850, 35, metadata, COLORS["sync"], "Synchronization / auxiliary block; no complex channel tiles")
                y += 49
                continue
            remaining = self.MAX_CHANNEL_BLOCKS - self._visible_channels
            displayed = min(count, remaining)
            vb = self.measurement.format == "VB"
            scan_header = 0 if vb else 192
            channel_header = 128 if vb else 32
            if scan_header:
                self._block(12, y, 90, 36, {"kind": "scan_header", "scan_index": index, "offset": int(scan.offset), "length": scan_header,
                            "tooltip": f"Acquisition {index} MDH\nOffset: {scan.offset:,} bytes\nLength: {scan_header} bytes"}, COLORS["header"], "MDH 192 B")
            columns = 8
            for channel in range(displayed):
                channel_offset = int(scan.offset) + scan_header + channel * (channel_header + samples * 8)
                payload_offset = channel_offset + channel_header
                payload_length = samples * 8
                metadata = {"kind": "channel", "scan_index": index, "channel_index": channel,
                            "offset": channel_offset, "length": channel_header + payload_length,
                            "header_length": channel_header, "payload_offset": payload_offset, "payload_length": payload_length,
                            "samples": samples,
                            "tooltip": f"Acquisition {index} · channel ordinal {channel}\nChannel block offset: {channel_offset:,} bytes\nChannel header: {channel_header} bytes\nPayload offset: {payload_offset:,} bytes\nPayload length: {payload_length:,} bytes\nComplex float32 samples: {samples:,}\nFlags: {', '.join(flags) or 'None'}"}
                color = COLORS["channel"] if getattr(scan, "is_image", False) else COLORS["noise"]
                self._block(112 + (channel % columns) * 94, y + (channel // columns) * 42, 87, 36,
                            metadata, color, f"Channel {channel}", clickable=True)
                self._visible_channels += 1
            if displayed < count:
                self._text(f"{count - displayed:,} further channel blocks omitted by display limit", 112, y + ceil(displayed / columns) * 42)
                y += 20
            y += max(1, ceil(displayed / columns)) * 42 + 16
        if not shown:
            self._text("No acquisitions match the current filters.", 12, y)
        self.summary.setText(
            f"Showing {self._visible_acquisitions:,} of {len(self.scans):,} filtered acquisitions · "
            f"{self._visible_channels:,} channel blocks · Limits: {self.MAX_ACQUISITIONS} acquisitions / {self.MAX_CHANNEL_BLOCKS:,} channels. Narrow the acquisition filters to inspect other blocks."
        )

    def _render_file(self):
        width, left = 1100.0, 12.0
        file_size = max(0, int(getattr(self.raw, "size", 0)))
        self._text("Physical file layout", left, 8, bold=True)
        self._text(f"File length: {file_size:,} bytes. Bars are byte-proportional; tiny blocks have a one-pixel minimum.", left, 30)
        self._block(left, 59, width, 42, {"kind": "container", "offset": 0, "length": file_size,
                    "tooltip": f"Entire file\nOffset: 0 bytes\nLength: {file_size:,} bytes"}, COLORS["container"])
        measurements = list(getattr(self.raw, "measurements", ()))
        for measurement in measurements[:64]:
            offset, length = max(0, int(measurement.offset)), max(0, int(measurement.length))
            if not file_size or offset >= file_size:
                continue
            length = min(length, file_size - offset)
            self._block(left + width * offset / file_size, 59, width * length / file_size, 42,
                        {"kind": "measurement", "measurement_index": int(measurement.index), "offset": offset, "length": length,
                         "tooltip": f"Measurement {measurement.index + 1} · ID {measurement.id}\nOffset: {offset:,} bytes\nLength: {length:,} bytes\nProtocol: {getattr(measurement, 'protocol_name', '')}"},
                        COLORS["measurement"], f"Measurement {measurement.index + 1}")
        self._text("Gray: container / unindexed bytes     Blue: measurements     Purple: headers     Green: channel samples", left, 113)
        m = self.measurement
        m_length = max(0, int(m.length))
        header = min(max(0, int(m.header_length)), m_length)
        self._text(f"Selected measurement {m.index + 1} · ID {m.id}", left, 155, bold=True)
        self._text(f"Offset {m.offset:,} · header {header:,} bytes · acquisition region {max(0, m_length - header):,} bytes", left, 177)
        if m_length:
            header_width = width * header / m_length
            self._block(left, 205, header_width, 45, {"kind": "protocol_header", "offset": int(m.offset), "length": header,
                        "tooltip": f"Measurement protocol header\nOffset: {m.offset:,} bytes\nLength: {header:,} bytes"}, COLORS["header"], "Protocol header")
            self._block(left + header_width, 205, width - header_width, 45,
                        {"kind": "acquisition_region", "offset": int(m.data_offset), "length": m_length - header,
                         "tooltip": f"Acquisition data and terminal blocks\nOffset: {m.data_offset:,} bytes\nLength: {m_length - header:,} bytes"}, COLORS["measurement"], "Acquisition data / terminal blocks")
        scan = next((item for item in self.scans if item.index == self.selected_scan), self.scans[0] if self.scans else None)
        if scan is None:
            self._text("No matching acquisition selected.", left, 290)
        else:
            self._text(f"Acquisition {scan.index} · channel-header and payload detail", left, 292, bold=True)
            self._text(f"Offset {scan.offset:,} bytes · length {scan.length:,} bytes", left, 314)
            self._render_scan_strip(scan, left, 343, width)
        self.summary.setText(f"{len(measurements):,} measurements · {len(self.scans):,} filtered acquisitions · Hover blocks for exact byte boundaries. Channel payloads are not loaded.")

    def _render_scan_strip(self, scan, left, y, width):
        length = max(0, int(scan.length))
        if not length:
            return
        self._block(left, y, width, 48, {"kind": "scan_region", "offset": int(scan.offset), "length": length,
                    "tooltip": f"Acquisition {scan.index}\nOffset: {scan.offset:,} bytes\nLength: {length:,} bytes"}, COLORS["container"])
        vb = self.measurement.format == "VB"
        mdh, channel_header = (0, 128) if vb else (192, 32)
        count = max(0, int(getattr(scan, "channels", 0)))
        samples = max(0, int(getattr(scan, "samples", 0)))
        if mdh:
            self._block(left, y, width * min(mdh, length) / length, 48,
                        {"kind": "scan_header", "offset": int(scan.offset), "length": mdh,
                         "tooltip": f"Acquisition MDH · {mdh} bytes"}, COLORS["header"], "MDH")
        displayed = min(count, 64)
        for channel in range(displayed):
            relative = mdh + channel * (channel_header + samples * 8)
            if relative >= length:
                break
            payload_length = min(samples * 8, max(0, length - relative - channel_header))
            self._block(left + width * relative / length, y, width * min(channel_header, length - relative) / length, 48,
                        {"kind": "channel_header", "offset": int(scan.offset) + relative, "length": channel_header,
                         "tooltip": f"Channel {channel} header\nOffset: {scan.offset + relative:,} bytes\nLength: {channel_header} bytes"}, COLORS["header"])
            metadata = {"kind": "channel", "scan_index": int(scan.index), "channel_index": channel,
                        "offset": int(scan.offset) + relative, "length": channel_header + payload_length,
                        "header_length": channel_header, "payload_offset": int(scan.offset) + relative + channel_header,
                        "payload_length": payload_length, "samples": samples,
                        "tooltip": f"Acquisition {scan.index} · channel ordinal {channel}\nPayload offset: {scan.offset + relative + channel_header:,} bytes\nPayload length: {payload_length:,} bytes\nComplex float32 samples: {samples:,}"}
            self._block(left + width * (relative + channel_header) / length, y, width * payload_length / length, 48,
                        metadata, COLORS["channel"], f"Ch {channel}", clickable=True)
        if count > displayed:
            self._text(f"Detail strip shows the first {displayed} of {count:,} channels.", left, y + 60)

    def _clicked(self, scan_index, channel_index):
        self.set_selection(scan_index, channel_index, redraw=False)
        self.block_selected.emit(scan_index, channel_index)

    def set_selection(self, scan_index, channel_index=0, redraw=True):
        scan_index = getattr(scan_index, "index", scan_index)
        changed_scan = scan_index != self.selected_scan
        self.selected_scan = scan_index
        self.selected_channel = max(0, int(channel_index))
        if redraw and changed_scan and self.mode.currentIndex() == 1:
            self._render()
            return
        for key, items in self._channel_items.items():
            selected = key == (self.selected_scan, self.selected_channel)
            for item in items:
                item.setPen(QPen(QColor(COLORS["selected"] if selected else "#708090"), 3 if selected else 1))

    def fit_view(self):
        bounds = self.scene.sceneRect()
        if bounds.isEmpty() or self.view.viewport().width() <= 1:
            return
        if self.mode.currentIndex() == 1:
            self.view.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.view.resetTransform()
            scale = min(1.2, max(0.08, (self.view.viewport().width() - 14) / max(1, bounds.width())))
            self.view.scale(scale, scale)
            self.view.verticalScrollBar().setValue(0)
