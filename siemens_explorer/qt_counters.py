"""Compact counter sliders whose positions follow observed scanner values."""

from __future__ import annotations

from numbers import Integral

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QAbstractSpinBox, QHBoxLayout, QLabel, QSizePolicy, QSpinBox, QWidget

from .qt_wheel import WheelSlider


class _CounterSpinBox(QSpinBox):
    def __init__(self, selector):
        super().__init__(selector)
        self.selector = selector

    def stepBy(self, steps):
        if self.isEnabled():
            self.selector.setCurrentIndex(self.selector.currentIndex() + int(steps))

    def stepEnabled(self):
        flags = QAbstractSpinBox.StepEnabledFlag.StepNone
        if self.isEnabled() and self.selector.count() > 1:
            if self.selector.currentIndex() > 0:
                flags |= QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
            if self.selector.currentIndex() < self.selector.count() - 1:
                flags |= QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
        return flags

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class CounterSlider(QWidget):
    """Choose All or an observed counter, never an interpolated scanner value.

    The small QComboBox-compatible selection API lets the containing workspace
    keep one source of truth for its counter filters and slice navigator.
    """

    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, int | None]] = []
        self._data_indices = {}
        self._maximum = -1
        self._minimum = None
        self._index = -1
        self.slider = WheelSlider(Qt.Orientation.Horizontal, self)
        self.slider.setObjectName("counterSlider")
        self.slider.setMinimumWidth(55)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTracking(True)
        self.slider.valueChanged.connect(self.setCurrentIndex)
        self.spin = _CounterSpinBox(self)
        self.spin.setObjectName("counterValue")
        self.spin.setFixedWidth(72)
        self.spin.setKeyboardTracking(False)
        self.spin.setSpecialValueText("All")
        self.spin.valueChanged.connect(self._numeric_changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.minimum_label, self.maximum_label = QLabel(), QLabel()
        for endpoint in (self.minimum_label, self.maximum_label):
            endpoint.setObjectName("sliderEndpoint")
        self.minimum_label.setToolTip("Minimum observed value; All is an extra slider position.")
        self.maximum_label.setToolTip("Maximum observed value in this measurement and data type.")
        layout.addWidget(self.minimum_label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.maximum_label)
        layout.addWidget(self.spin)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusProxy(self.slider)
        self.setToolTip("Drag or wheel over the slider to browse observed values. Wheel down advances; the first position is All. Type a value to jump; unavailable values select the nearest observed value.")
        self._sync()

    def setAccessibleName(self, name):
        super().setAccessibleName(name)
        if hasattr(self, "slider"):
            self.slider.setAccessibleName(f"{name} slider")
            self.spin.setAccessibleName(f"{name} value")

    def setToolTip(self, text):
        super().setToolTip(text)
        if hasattr(self, "slider"):
            self.slider.setToolTip(text)
            self.spin.setToolTip(text)

    def addItem(self, text, data=None):
        if data is not None:
            if isinstance(data, bool) or not isinstance(data, Integral) or data < 0:
                raise ValueError("Counter values must be non-negative integers or None for All.")
            data = int(data)
            self._maximum = max(self._maximum, data)
            self._minimum = data if self._minimum is None else min(self._minimum, data)
        self._data_indices.setdefault(data, len(self._items))
        self._items.append((str(text), data))
        if self._index < 0:
            self._index = 0
            self._sync()
            self.currentIndexChanged.emit(0)
        else:
            self._sync()

    def set_values(self, values, select_first=False):
        """Replace observed values in one pass, emitting at most one selection."""
        normalized = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
                raise ValueError("Counter values must be non-negative integers.")
            normalized.append(int(value))
        normalized = sorted(set(normalized))
        self.slider.reset_wheel()
        previous_index, previous_data = self._index, self.currentData()
        self._items = [("All", None)] + [(str(value), value) for value in normalized]
        self._data_indices = {data: index for index, (_, data) in enumerate(self._items)}
        self._maximum = normalized[-1] if normalized else -1
        self._minimum = normalized[0] if normalized else None
        self._index = 1 if select_first and normalized else 0
        self._sync()
        if self._index != previous_index or self.currentData() != previous_data:
            self.currentIndexChanged.emit(self._index)

    def clear(self):
        self.slider.reset_wheel()
        changed = self._index != -1
        self._items.clear()
        self._data_indices.clear()
        self._maximum = -1
        self._minimum = None
        self._index = -1
        self._sync()
        if changed:
            self.currentIndexChanged.emit(-1)

    def count(self):
        return len(self._items)

    def currentData(self):
        return self.itemData(self._index)

    def currentIndex(self):
        return self._index

    def currentText(self):
        return self.itemText(self._index)

    def findData(self, value, *_args):
        return self._data_indices.get(value, -1)

    def itemData(self, index, *_args):
        return self._items[index][1] if 0 <= index < len(self._items) else None

    def itemText(self, index):
        return self._items[index][0] if 0 <= index < len(self._items) else ""

    def setCurrentText(self, text):
        index = next((i for i, (caption, _) in enumerate(self._items) if caption == str(text)), -1)
        if index >= 0:
            self.setCurrentIndex(index)

    def setCurrentIndex(self, index):
        target = max(0, min(int(index), len(self._items) - 1)) if self._items else -1
        if target != self.slider.value():
            self.slider.reset_wheel()
        changed = target != self._index
        self._index = target
        self._sync()
        if changed:
            self.currentIndexChanged.emit(target)

    def _numeric_changed(self, value):
        if not self._items:
            return
        if value == -1:
            target = self.findData(None)
        else:
            numeric = [(index, data) for index, (_, data) in enumerate(self._items) if data is not None]
            target = min(numeric, key=lambda item: (abs(item[1] - value), item[1]))[0] if numeric else self.findData(None)
        self.setCurrentIndex(max(0, target))

    def _sync(self):
        self.minimum_label.setText(str(self._minimum) if self._minimum is not None else "—")
        self.maximum_label.setText(str(self._maximum) if self._maximum >= 0 else "—")
        with QSignalBlocker(self.slider), QSignalBlocker(self.spin):
            self.slider.setRange(0, max(0, len(self._items) - 1))
            self.slider.setValue(max(0, self._index))
            self.spin.setRange(-1, self._maximum)
            current = self.currentData()
            self.spin.setValue(-1 if current is None else current)
            usable = len(self._items) > 1
            self.slider.setEnabled(usable)
            self.spin.setEnabled(usable)
