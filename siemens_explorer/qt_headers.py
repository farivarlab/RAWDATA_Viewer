"""Bounded, searchable navigation of stored header parameter paths."""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from .twix import parse_protocol


class HeaderTree(QTreeWidget):
    parameter_selected = Signal(str)
    MAX_FIELDS = 4096

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rawHeaderTree")
        self.setAccessibleName("Raw header parameter tree")
        self.setColumnCount(2)
        self.setHeaderLabels(["Parameter", "Value"])
        self.setColumnWidth(0, 210)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setMinimumWidth(220)
        self.limited = False
        self.currentItemChanged.connect(self._selected)
        self.setToolTip("Expand stored ASCCONV paths and scalar header parameters. Select a value to locate it in the original buffer.")

    def set_buffer(self, text):
        self.setUpdatesEnabled(False)
        try:
            self.clear()
            parameters = parse_protocol(text)
            self.limited = len(parameters) > self.MAX_FIELDS
            nodes = {}
            general = None
            for number, (path, value) in enumerate(parameters.items()):
                if number >= self.MAX_FIELDS:
                    break
                parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
                if len(parts) > 32:
                    parts = parts[:31] + [".".join(parts[31:])]
                parent = self.invisibleRootItem()
                if len(parts) == 1:
                    if general is None:
                        general = QTreeWidgetItem(self, ["General", ""])
                    parent = general
                for depth, part in enumerate(parts):
                    prefix = ("__general__", *parts) if len(parts) == 1 else tuple(parts[:depth + 1])
                    if prefix not in nodes:
                        nodes[prefix] = QTreeWidgetItem(parent, [part, ""])
                    parent = nodes[prefix]
                parent.setText(1, re.sub(r"\s+", " ", str(value))[:300])
                parent.setData(0, Qt.ItemDataRole.UserRole, path)
                parent.setToolTip(0, path)
                parent.setToolTip(1, str(value))
            if general is not None:
                general.setText(1, f"{general.childCount():,} fields")
            self.collapseAll()
        finally:
            self.setUpdatesEnabled(True)

    def filter_parameters(self, query):
        query = str(query).strip().casefold()

        def visit(item):
            children = [visit(item.child(index)) for index in range(item.childCount())]
            path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
            match = not query or query in f"{path} {item.text(1)}".casefold() or any(children)
            item.setHidden(not match)
            if query and any(children):
                item.setExpanded(True)
            return match

        for index in range(self.topLevelItemCount()):
            visit(self.topLevelItem(index))

    def _selected(self, item, previous):
        path = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if path:
            self.parameter_selected.emit(path)
