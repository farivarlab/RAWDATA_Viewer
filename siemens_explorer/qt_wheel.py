"""One-step hover-wheel navigation shared by lists and sliders."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QSlider


class WheelSteps:
    """Accumulate partial wheel/trackpad gestures; wheel down advances."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._angle = self._pixels = 0

    @staticmethod
    def delta(event):
        return event.angleDelta().y() or event.pixelDelta().y()

    def consume(self, event):
        if event.phase() == Qt.ScrollPhase.ScrollBegin:
            self.reset()
        angle, pixels = event.angleDelta().y(), event.pixelDelta().y()
        if angle:
            self._pixels = 0
            self._angle += angle
            notches = int(self._angle / 120)
            self._angle -= notches * 120
        elif pixels:
            self._angle = 0
            self._pixels += pixels
            notches = int(self._pixels / 40)
            self._pixels -= notches * 40
        else:
            if event.phase() == Qt.ScrollPhase.ScrollEnd:
                self.reset()
            return None
        if event.phase() == Qt.ScrollPhase.ScrollEnd:
            self.reset()
        return -notches

    def discard_at_limit(self, value, minimum, maximum, event):
        delta = self.delta(event)
        if (value <= minimum and delta > 0) or (value >= maximum and delta < 0):
            self.reset()


class WheelSlider(QSlider):
    """Hovering and wheeling changes one slider position per mouse notch."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._wheel = WheelSteps()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.rangeChanged.connect(self.reset_wheel)

    def reset_wheel(self, *_args):
        self._wheel.reset()

    def wheelEvent(self, event):
        if not self.isEnabled():
            self._wheel.reset()
            event.ignore()
            return
        steps = self._wheel.consume(event)
        if steps is None:
            event.ignore()
            return
        if steps:
            self.setValue(self.value() + steps * self.singleStep())
        self._wheel.discard_at_limit(self.value(), self.minimum(), self.maximum(), event)
        event.accept()

    def leaveEvent(self, event):
        self._wheel.reset()
        super().leaveEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled() and hasattr(self, "_wheel"):
            self._wheel.reset()
        super().changeEvent(event)
