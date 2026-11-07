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

from .qt_theme import load_theme


_COLOR_ROLES = ("container", "measurement", "header", "channel", "noise", "sync",
                "selected", "text", "canvas", "border")


def _block_colors(assets):
    return {role: assets.colors[f"block_{role}"] for role in _COLOR_ROLES}


# Compatibility snapshots; live views reload the assets in set_theme.
COLORS = _block_colors(load_theme("light"))
COCKPIT_COLORS = _block_colors(load_theme("cockpit"))


class _Block(QGraphicsRectItem):
    def __init__(self, rectangle, metadata, color, callback=None, border=None):
        super().__init__(rectangle)
        self.metadata = metadata
        self.callback = callback
        self.setData(0, metadata)
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor(border or color), 1))
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
        self.theme_name = "light"
        assets = load_theme(self.theme_name)
        self._colors = _block_colors(assets)
        self._font_family, self._font_size = assets.font_family, assets.font_size

        self.mode = QComboBox()
        self.mode.setObjectName("dataBlockMode")
        self.mode.setAccessibleName("Data block representation")
        self.mode.setToolTip("Acquisition rows are schematic. File-layout bars are proportional to byte lengths, with a one-pixel minimum for tiny blocks.")
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
        self.scene.setBackgroundBrush(QBrush(QColor(self._colors["canvas"])))
        self.view = _BlockView(self.scene, self)
        self.view.setBackgroundBrush(QBrush(QColor(self._colors["canvas"])))
        self.view.setObjectName("dataBlockCanvas")
        self.view.setAccessibleName("Raw data block map")
        self.view.setToolTip("Click a channel block to inspect its readout. Hover for byte offsets and lengths. Scroll to browse; Ctrl+scroll to zoom. Channel payloads are not loaded for this map.")
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.view.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setMinimumHeight(240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(controls)
        layout.addWidget(self.summary)
        layout.addWidget(self.view, 1)
        self.clear()

    def set_theme(self, name, assets=None):
        """Recolor the current diagram without rebuilding it or moving the view."""
        assets = load_theme(name) if assets is None else assets
        if assets.name != name:
            raise ValueError("The block-map theme must match its preloaded theme assets.")
        colors = _block_colors(assets)
        self.theme_name = name
        self._colors = colors
        self._font_family, self._font_size = assets.font_family, assets.font_size
        background = QBrush(QColor(self._colors["canvas"]))
        self.scene.setBackgroundBrush(background)
        self.view.setBackgroundBrush(background)
        for item in self.scene.items():
            if isinstance(item, _Block):
                item.setBrush(QBrush(QColor(self._colors[item.color_role])))
                item.setPen(QPen(QColor(self._colors["border"]), 1))
            elif isinstance(item, QGraphicsSimpleTextItem):
                item.setBrush(QBrush(QColor(item.explicit_color or self._colors["text"])))
                font = QFont(self._font_family, self._font_size)
                font.setBold(item.font().bold())
                item.setFont(font)
        self.set_selection(self.selected_scan, self.selected_channel, redraw=False)

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
        self.view.resetTransform()
        self.view.horizontalScrollBar().setValue(0)
        self.view.verticalScrollBar().setValue(0)
        self.summary.setText("No dataset")
        self.summary.setToolTip("")
        self._text("No data blocks available", 18, 28, bold=True)

    def _text(self, text, x, y, parent=None, bold=False, color=None):
        item = QGraphicsSimpleTextItem(str(text), parent)
        font = QFont(self._font_family, self._font_size)
        font.setBold(bold)
        item.setFont(font)
        item.explicit_color = color
        item.setBrush(QBrush(QColor(color or self._colors["text"])))
        item.setPos(x, y)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if parent is None:
            self.scene.addItem(item)
        return item

    def _block(self, x, y, width, height, metadata, color, caption="", clickable=False):
        item = _Block(QRectF(x, y, max(1.0, width), height), metadata, self._colors[color],
                      self._clicked if clickable else None, border=self._colors["border"])
        item.color_role = color
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
        y = 12
        omitted_channels = False
        shown = self.scans[:self.MAX_ACQUISITIONS]
        for scan in shown:
            if self._visible_channels >= self.MAX_CHANNEL_BLOCKS:
                break
            index = int(scan.index)
            count = max(0, int(getattr(scan, "channels", 0)))
            samples = max(0, int(getattr(scan, "samples", 0)))
            flags = tuple(getattr(scan, "flags", ()))
            is_sync = not count or not samples or "SYNCDATA" in flags
            heading = f"Acquisition {index}   |   line {getattr(scan, 'line', 0)}   slice {getattr(scan, 'slice', 0)}   echo {getattr(scan, 'echo', 0)}   |   {count} channels × {samples} samples"
            self._text(heading, 12, y, bold=True)
            self._visible_acquisitions += 1
            y += 23
            if is_sync:
                metadata = {"kind": "sync", "scan_index": index, "offset": int(scan.offset), "length": int(scan.length),
                            "tooltip": f"Acquisition {index}\nOffset: {scan.offset:,} bytes\nLength: {scan.length:,} bytes\nFlags: {', '.join(flags) or 'None'}\nNo decoded channel payloads"}
                self._block(12, y, 850, 35, metadata, "sync", "Synchronization / auxiliary block; no complex channel tiles")
                y += 49
                continue
            remaining = self.MAX_CHANNEL_BLOCKS - self._visible_channels
            displayed = min(count, remaining)
            vb = self.measurement.format == "VB"
            scan_header = 0 if vb else 192
            channel_header = 128 if vb else 32
            if scan_header:
                self._block(12, y, 90, 36, {"kind": "scan_header", "scan_index": index, "offset": int(scan.offset), "length": scan_header,
                            "tooltip": f"Acquisition {index} MDH\nOffset: {scan.offset:,} bytes\nLength: {scan_header} bytes"}, "header", "MDH 192 B")
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
                color = "channel" if getattr(scan, "is_image", False) else "noise"
                self._block(112 + (channel % columns) * 94, y + (channel // columns) * 42, 87, 36,
                            metadata, color, f"Channel {channel}", clickable=True)
                self._visible_channels += 1
            if displayed < count:
                omitted_channels = True
                self._text(f"{count - displayed:,} further channel blocks omitted by display limit", 112, y + ceil(displayed / columns) * 42)
                y += 20
            y += max(1, ceil(displayed / columns)) * 42 + 16
        if not shown:
            self._text("No acquisitions match the current filters.", 12, y)
        limited = self._visible_acquisitions < len(self.scans) or omitted_channels
        self.summary.setText(
            f"{self._visible_acquisitions:,} of {len(self.scans):,} acquisitions · "
            f"{self._visible_channels:,} channel blocks" + (" · Display limit reached" if limited else "")
        )
        self.summary.setToolTip(
            f"Limits: {self.MAX_ACQUISITIONS} acquisitions / {self.MAX_CHANNEL_BLOCKS:,} channel blocks. "
            "Narrow the acquisition filters to inspect other blocks. Rows are schematic; each channel tile represents complex float32 samples."
        )

    def _render_file(self):
        width, left = 1100.0, 12.0
        file_size = max(0, int(getattr(self.raw, "size", 0)))
        self._text("Physical file layout", left, 8, bold=True)
        self._text(f"{file_size:,} bytes", left, 30)
        self._block(left, 59, width, 42, {"kind": "container", "offset": 0, "length": file_size,
                    "tooltip": f"Entire file\nOffset: 0 bytes\nLength: {file_size:,} bytes"}, "container")
        measurements = list(getattr(self.raw, "measurements", ()))
        for measurement in measurements[:64]:
            offset, length = max(0, int(measurement.offset)), max(0, int(measurement.length))
            if not file_size or offset >= file_size:
                continue
            length = min(length, file_size - offset)
            self._block(left + width * offset / file_size, 59, width * length / file_size, 42,
                        {"kind": "measurement", "measurement_index": int(measurement.index), "offset": offset, "length": length,
                         "tooltip": f"Measurement {measurement.index + 1} · ID {measurement.id}\nOffset: {offset:,} bytes\nLength: {length:,} bytes\nProtocol: {getattr(measurement, 'protocol_name', '')}"},
                        "measurement", f"Measurement {measurement.index + 1}")
        self._text("Gray: container / unindexed bytes     Blue: measurements     Purple: headers     Green: channel samples", left, 113)
        m = self.measurement
        m_length = max(0, int(m.length))
        header = min(max(0, int(m.header_length)), m_length)
        self._text(f"Selected measurement {m.index + 1} · ID {m.id}", left, 155, bold=True)
        self._text(f"Offset {m.offset:,} · header {header:,} bytes · acquisition region {max(0, m_length - header):,} bytes", left, 177)
        if m_length:
            header_width = width * header / m_length
            self._block(left, 205, header_width, 45, {"kind": "protocol_header", "offset": int(m.offset), "length": header,
                        "tooltip": f"Measurement protocol header\nOffset: {m.offset:,} bytes\nLength: {header:,} bytes"}, "header", "Protocol header")
            self._block(left + header_width, 205, width - header_width, 45,
                        {"kind": "acquisition_region", "offset": int(m.data_offset), "length": m_length - header,
                         "tooltip": f"Acquisition data and terminal blocks\nOffset: {m.data_offset:,} bytes\nLength: {m_length - header:,} bytes"}, "measurement", "Acquisition data / terminal blocks")
        scan = next((item for item in self.scans if item.index == self.selected_scan), self.scans[0] if self.scans else None)
        if scan is None:
            self._text("No matching acquisition selected.", left, 290)
        else:
            self._text(f"Acquisition {scan.index} · channel-header and payload detail", left, 292, bold=True)
            self._text(f"Offset {scan.offset:,} bytes · length {scan.length:,} bytes", left, 314)
            self._render_scan_strip(scan, left, 343, width)
        self.summary.setText(f"{len(measurements):,} measurements · {len(self.scans):,} filtered acquisitions" + (" · First 64 measurements shown" if len(measurements) > 64 else ""))
        self.summary.setToolTip("Hover blocks for exact byte boundaries. Channel payloads are not loaded. Bars are proportional to byte lengths, with a one-pixel minimum for tiny blocks.")

    def _render_scan_strip(self, scan, left, y, width):
        length = max(0, int(scan.length))
        if not length:
            return
        self._block(left, y, width, 48, {"kind": "scan_region", "offset": int(scan.offset), "length": length,
                    "tooltip": f"Acquisition {scan.index}\nOffset: {scan.offset:,} bytes\nLength: {length:,} bytes"}, "container")
        if not getattr(scan, "samples", 0) or not getattr(scan, "channels", 0) or "SYNCDATA" in getattr(scan, "flags", ()):
            self._text("Synchronization / auxiliary data; no channel payloads", left, y + 60)
            return
        vb = self.measurement.format == "VB"
        mdh, channel_header = (0, 128) if vb else (192, 32)
        count = max(0, int(getattr(scan, "channels", 0)))
        samples = max(0, int(getattr(scan, "samples", 0)))
        if mdh:
            self._block(left, y, width * min(mdh, length) / length, 48,
                        {"kind": "scan_header", "offset": int(scan.offset), "length": mdh,
                         "tooltip": f"Acquisition MDH · {mdh} bytes"}, "header", "MDH")
        displayed = min(count, 64)
        first_channel = max(0, min(self.selected_channel - displayed // 2, count - displayed))
        for channel in range(first_channel, first_channel + displayed):
            relative = mdh + channel * (channel_header + samples * 8)
            if relative >= length:
                break
            payload_length = min(samples * 8, max(0, length - relative - channel_header))
            self._block(left + width * relative / length, y, width * min(channel_header, length - relative) / length, 48,
                        {"kind": "channel_header", "offset": int(scan.offset) + relative, "length": channel_header,
                         "tooltip": f"Channel {channel} header\nOffset: {scan.offset + relative:,} bytes\nLength: {channel_header} bytes"}, "header")
            metadata = {"kind": "channel", "scan_index": int(scan.index), "channel_index": channel,
                        "offset": int(scan.offset) + relative, "length": channel_header + payload_length,
                        "header_length": channel_header, "payload_offset": int(scan.offset) + relative + channel_header,
                        "payload_length": payload_length, "samples": samples,
                        "tooltip": f"Acquisition {scan.index} · channel ordinal {channel}\nPayload offset: {scan.offset + relative + channel_header:,} bytes\nPayload length: {payload_length:,} bytes\nComplex float32 samples: {samples:,}"}
            self._block(left + width * (relative + channel_header) / length, y, width * payload_length / length, 48,
                        metadata, "channel", f"Ch {channel}", clickable=True)
        if count > displayed:
            self._text(f"Channels {first_channel}–{first_channel + displayed - 1} of {count:,}", left, y + 60)

    def _clicked(self, scan_index, channel_index):
        self.set_selection(scan_index, channel_index, redraw=False)
        self.block_selected.emit(scan_index, channel_index)

    def set_selection(self, scan_index, channel_index=0, redraw=True):
        scan_index = getattr(scan_index, "index", scan_index)
        changed_scan = scan_index != self.selected_scan
        changed_channel = channel_index != self.selected_channel
        self.selected_scan = scan_index
        self.selected_channel = max(0, int(channel_index))
        if redraw and self.mode.currentIndex() == 1 and (changed_scan or (changed_channel and (scan_index, self.selected_channel) not in self._channel_items)):
            self._render()
            return
        for key, items in self._channel_items.items():
            selected = key == (self.selected_scan, self.selected_channel)
            for item in items:
                item.setPen(QPen(QColor(self._colors["selected"] if selected else self._colors["border"]), 3 if selected else 1))

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
