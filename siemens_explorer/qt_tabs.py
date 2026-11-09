"""Workspace tabs with a bottom separator interrupted by the active tab."""

from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QTabBar


class WorkspaceTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setExpanding(False)
        self.setDrawBase(False)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        color = self.palette().color(QPalette.ColorRole.Mid)
        bottom = self.height() - 1
        # Fill the bottom row like a stylesheet border. A centered pen stroke
        # can extend a device pixel upward at fractional display scales.
        current = self.currentIndex()
        if current < 0 or not self.isTabVisible(current):
            painter.fillRect(0, bottom, self.width(), 1, color)
            return
        selected = self.tabRect(current)
        # The border connects inactive tabs and unused row space; the active
        # tab stays visually joined to the display beneath it.
        if selected.left() > 0:
            painter.fillRect(0, bottom, min(self.width(), selected.left()), 1, color)
        if selected.right() < self.width() - 1:
            left = max(0, selected.right() + 1)
            painter.fillRect(left, bottom, self.width() - left, 1, color)
