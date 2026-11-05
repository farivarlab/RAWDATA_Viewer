"""Compact native receive-channel browsing controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)


class ChannelBrowser(QWidget):
    """Browse ordinal channel positions while showing physical receiver IDs.

    Only interaction through the list or step buttons emits ``channel_selected``.
    The synchronization methods are deliberately silent so a containing window
    can mirror its selection without starting another read or reconstruction.
    """

    channel_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("channelBrowser")
        self._channel_ids: list[int | None] = []
        self._navigation_enabled = True

        self.list = QListWidget(self)
        self.list.setObjectName("receiveChannelList")
        self.list.setAccessibleName("Individual receive channels")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumHeight(95)
        self.list.setMaximumHeight(150)
        self.list.setAlternatingRowColors(True)
        self.list.currentRowChanged.connect(self._selection_changed)

        self.prev_button = QPushButton("‹", self)
        self.prev_button.setObjectName("previousChannelButton")
        self.prev_button.setAccessibleName("Previous receive channel")
        self.prev_button.setToolTip("Previous receive channel")
        self.prev_button.setFixedWidth(30)
        self.prev_button.clicked.connect(lambda: self.step_channel(-1))
        self.next_button = QPushButton("›", self)
        self.next_button.setObjectName("nextChannelButton")
        self.next_button.setAccessibleName("Next receive channel")
        self.next_button.setToolTip("Next receive channel")
        self.next_button.setFixedWidth(30)
        self.next_button.clicked.connect(lambda: self.step_channel(1))
        self.count = QLabel(self)
        self.count.setObjectName("receiveChannelPosition")
        self.count.setTextFormat(Qt.TextFormat.PlainText)
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)

        steps = QHBoxLayout()
        steps.setContentsMargins(0, 0, 0, 0)
        steps.addWidget(self.prev_button)
        steps.addWidget(self.count, 1)
        steps.addWidget(self.next_button)
        hint = QLabel("Select a channel to view it individually.", self)
        hint.setObjectName("muted")
        hint.setTextFormat(Qt.TextFormat.PlainText)
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.list)
        layout.addLayout(steps)
        layout.addWidget(hint)
        self._update_controls()

    @property
    def channel_ids(self) -> list[int | None]:
        return list(self._channel_ids)

    def set_channels(self, channel_ids: list[int | None], selected: int = 0) -> None:
        """Replace the available ordinal positions without emitting selection."""
        ids = [None if value is None else int(value) for value in channel_ids]
        previous = self.list.blockSignals(True)
        try:
            self._channel_ids = ids
            self.list.clear()
            for ordinal, physical_id in enumerate(ids):
                suffix = f"ID {physical_id}" if physical_id is not None else "ID unavailable"
                item = QListWidgetItem(f"Channel {ordinal}  ·  {suffix}")
                item.setData(Qt.ItemDataRole.UserRole, ordinal)
                item.setToolTip(f"Channel position {ordinal}\nPhysical receiver {suffix}")
                self.list.addItem(item)
            if ids:
                self.list.setCurrentRow(max(0, min(int(selected), len(ids) - 1)))
                self.list.scrollToItem(self.list.currentItem())
        finally:
            self.list.blockSignals(previous)
        self._update_controls()

    def set_current_channel(self, ordinal: int) -> None:
        """Silently mirror an ordinal selection from another application control."""
        if not self._channel_ids:
            return
        row = max(0, min(int(ordinal), len(self._channel_ids) - 1))
        previous = self.list.blockSignals(True)
        try:
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.currentItem())
        finally:
            self.list.blockSignals(previous)
        self._update_controls()

    def clear(self) -> None:
        self.set_channels([])

    def set_navigation_enabled(self, enabled: bool) -> None:
        self._navigation_enabled = bool(enabled)
        self._update_controls()

    def step_channel(self, delta: int) -> bool:
        if not self._navigation_enabled or not self.isEnabled() or not self._channel_ids:
            return False
        current = self.list.currentRow()
        target = max(0, min(current + int(delta), len(self._channel_ids) - 1))
        if target == current:
            return False
        self.list.setCurrentRow(target)
        self.list.scrollToItem(self.list.currentItem())
        return True

    def _selection_changed(self, row: int) -> None:
        self._update_controls()
        if 0 <= row < len(self._channel_ids):
            self.channel_selected.emit(row)

    def _update_controls(self) -> None:
        count = len(self._channel_ids)
        row = self.list.currentRow()
        enabled = self._navigation_enabled and count > 0
        self.list.setEnabled(enabled)
        self.prev_button.setEnabled(enabled and row > 0)
        self.next_button.setEnabled(enabled and 0 <= row < count - 1)
        self.count.setText(f"{row + 1} / {count} channels" if count else "No channels")
