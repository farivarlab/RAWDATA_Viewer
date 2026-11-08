"""Workspace tabs with a bottom separator interrupted by the active tab."""

from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QTabBar


class WorkspaceTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setExpanding(False)
        self.setDrawBase(False)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Mid), 1))
        bottom = self.height() - 1
        current = self.currentIndex()
        if current < 0 or not self.isTabVisible(current):
            painter.drawLine(0, bottom, self.width() - 1, bottom)
            return
        selected = self.tabRect(current)
        # The border connects inactive tabs and unused row space; the active
        # tab stays visually joined to the display beneath it.
        if selected.left() > 0:
            painter.drawLine(0, bottom, min(self.width() - 1, selected.left() - 1), bottom)
        if selected.right() < self.width() - 1:
            painter.drawLine(max(0, selected.right() + 1), bottom, self.width() - 1, bottom)
