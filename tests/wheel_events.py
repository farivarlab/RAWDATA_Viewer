"""Send real Qt wheel events to the widget under a test pointer."""

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication


def wheel(target, angle=0, pixels=0, horizontal=0, phase=Qt.ScrollPhase.NoScrollPhase):
    position = target.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(target.mapToGlobal(position)),
                        QPoint(0, pixels), QPoint(horizontal, angle), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, phase, False)
    event.setAccepted(False)
    QApplication.sendEvent(target, event)
    return event
