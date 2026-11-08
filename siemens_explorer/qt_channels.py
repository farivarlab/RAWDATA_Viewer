"""One-row receive-channel selection for the Data selection form."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QSpinBox, QWidget

from .qt_wheel import WheelSlider


class ChannelSelector(QWidget):
    """Select a channel ordinal, retaining physical receiver IDs in tooltips.

    User slider/numeric changes emit channel_selected. Synchronization from the
    containing window is silent, including during a partial wheel gesture.
    """

    channel_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("channelSelector")
        self._channel_ids: list[int | None] = []
        self._current = 0
        self._navigation_enabled = True
        self.slider = WheelSlider(Qt.Orientation.Horizontal, self)
        self.slider.setObjectName("receiveChannelSlider")
        self.slider.setAccessibleName("Receive channel slider")
        self.slider.setMinimumWidth(55)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.valueChanged.connect(self._value_changed)
        # Pressing the current thumb also selects this individual channel when
        # the image is displaying a combined-channel reconstruction.
        self.slider.sliderPressed.connect(self._activate_current)
        self.spin = QSpinBox(self)
        self.spin.setObjectName("receiveChannelValue")
        self.spin.setAccessibleName("Receive channel number")
        self.spin.setFixedWidth(72)
        self.spin.setKeyboardTracking(False)
        self.spin.valueChanged.connect(self._value_changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.minimum_label, self.maximum_label = QLabel(), QLabel()
        for endpoint in (self.minimum_label, self.maximum_label):
            endpoint.setObjectName("sliderEndpoint")
        self.minimum_label.setToolTip("First receive-channel ordinal.")
        self.maximum_label.setToolTip("Last receive-channel ordinal in this measurement.")
        layout.addWidget(self.minimum_label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.maximum_label)
        layout.addWidget(self.spin)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusProxy(self.slider)
        self._sync()

    @property
    def channel_ids(self):
        return list(self._channel_ids)

    def current_channel(self):
        return self._current

    def set_channels(self, channel_ids, selected=0):
        """Replace available receivers without emitting a user selection."""
        self._channel_ids = [None if value is None else int(value) for value in channel_ids]
        self.slider.reset_wheel()
        self.set_current_channel(selected)

    def set_current_channel(self, ordinal):
        target = max(0, min(int(ordinal), len(self._channel_ids) - 1))
        if target != self.slider.value():
            self.slider.reset_wheel()
        self._current = target
        self._sync()

    def clear(self):
        self.set_channels([])

    def set_navigation_enabled(self, enabled):
        if self._navigation_enabled != bool(enabled):
            self.slider.reset_wheel()
        self._navigation_enabled = bool(enabled)
        self._sync()

    def step_channel(self, delta):
        if not self._navigation_enabled or not self.isEnabled() or not self._channel_ids:
            return False
        target = max(0, min(self._current + int(delta), len(self._channel_ids) - 1))
        if target == self._current:
            return False
        self.slider.setValue(target)
        return True

    def _value_changed(self, ordinal):
        if not self._navigation_enabled or not self.isEnabled() or not self._channel_ids:
            self._sync()
            return
        changed = ordinal != self._current
        self.set_current_channel(ordinal)
        if changed:
            self.channel_selected.emit(self._current)

    def _activate_current(self):
        if self._channel_ids and self._navigation_enabled and self.isEnabled():
            self.channel_selected.emit(self._current)

    def _sync(self):
        count = len(self._channel_ids)
        self.minimum_label.setText("0" if count else "—")
        self.maximum_label.setText(str(count - 1) if count else "—")
        with QSignalBlocker(self.slider), QSignalBlocker(self.spin):
            for control in (self.slider, self.spin):
                control.setRange(0, max(0, count - 1))
                control.setValue(self._current)
                control.setEnabled(self._navigation_enabled and count > 0)
        physical = self._channel_ids[self._current] if count else None
        tooltip = (f"Channel {self._current} (zero-based) · {count} channels\n"
                   f"Physical receiver ID {physical if physical is not None else 'unavailable'}\n"
                   "Drag or wheel to select a channel; type its number to jump.\n"
                   "Wheel down advances; Ctrl+Left / Ctrl+Right also steps between channels."
                   if count else "No receive channels. Open a measurement to select a channel.")
        for control in (self, self.slider, self.spin):
            control.setToolTip(tooltip)
