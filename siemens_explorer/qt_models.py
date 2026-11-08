"""Read-only Qt table models and cancellable background jobs.

The source model never reorders a DataFrame. Sorting and free-text filtering
belong to the proxy, so an acquisition retains its original scanner-row identity
when the user sorts, selects, or exports a table.
"""

from __future__ import annotations

import math
import numbers
import threading
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QRunnable,
    QSortFilterProxyModel,
    Qt,
    Signal,
    Slot,
)


def _scalar(value: Any) -> Any:
    """Normalize pandas/NumPy scalar values without evaluating header strings."""
    if isinstance(value, np.generic):
        value = value.item()
    return value


def _missing(value: Any) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    if isinstance(value, numbers.Real):
        return math.isnan(value) if isinstance(value, float) else False
    return False


def _display(value: Any) -> str:
    value = _scalar(value)
    if _missing(value):
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, numbers.Real) and not isinstance(value, numbers.Integral):
        return f"{value:.7g}"
    return str(value)


class DataFrameModel(QAbstractTableModel):
    """Expose a DataFrame through Qt's virtual, non-editable table interface.

    ``frame`` is held by reference and must not be mutated behind the model. Call
    :meth:`set_frame` when replacing data. Its index is retained for row identity;
    the vertical header and :meth:`row_id` expose that original index.
    """

    def __init__(self, frame: pd.DataFrame | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._frame = pd.DataFrame() if frame is None else frame
        if not isinstance(self._frame, pd.DataFrame):
            raise TypeError("Table data must be a pandas DataFrame.")
        self.revision = 0

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    def set_frame(self, frame: pd.DataFrame) -> None:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("Table data must be a pandas DataFrame.")
        self.beginResetModel()
        self._frame = frame
        self.revision += 1
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._frame)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._frame.columns)

    def value_at(self, row: int, column: int) -> Any:
        return _scalar(self._frame.iat[row, column])

    def row_id(self, row: int) -> Any:
        return _scalar(self._frame.index[row])

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._frame) or not 0 <= index.column() < len(self._frame.columns):
            return None
        value = self.value_at(index.row(), index.column())
        if role == Qt.ItemDataRole.DisplayRole:
            return _display(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            return "" if _missing(value) else str(value)
        if role == Qt.ItemDataRole.UserRole:
            return None if _missing(value) else value
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if isinstance(value, numbers.Number) else Qt.AlignmentFlag.AlignLeft))
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._frame.columns):
            return str(self._frame.columns[section])
        if orientation == Qt.Orientation.Vertical and 0 <= section < len(self._frame):
            return str(self._frame.index[section])
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class DataFrameFilter(QSortFilterProxyModel):
    """Literal case-insensitive row search and native-value table sorting.

    Search matches a cell's original text rather than treating scanner strings as
    regular expressions. A column-wise match cache keeps Qt's per-row callback
    inexpensive for large acquisition indices. The source frame remains unchanged.
    """

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._query = ""
        self._cache_key = None
        self._matches = None
        self.setDynamicSortFilter(True)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    @property
    def query(self) -> str:
        return self._query

    @Slot(str)
    def set_query(self, query: str) -> None:
        normalized = str(query).strip().casefold()
        if normalized == self._query:
            return
        modern_filter_change = hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange")
        if modern_filter_change:
            self.beginFilterChange()
        self._query = normalized
        self._cache_key = None
        self._build_matches()
        if modern_filter_change:
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:  # Qt 6.7 / 6.8 compatibility.
            self.invalidateFilter()

    def _build_matches(self) -> None:
        source = self.sourceModel()
        if not isinstance(source, DataFrameModel):
            self._matches = None
            self._cache_key = None
            return
        key = (id(source), source.revision, self._query)
        if key == self._cache_key:
            return
        self._cache_key = key
        if not self._query:
            self._matches = None
            return
        frame = source.frame
        matches = np.zeros(len(frame), dtype=bool)
        # Process one column at a time, avoiding an all-cells string matrix.
        for position in range(len(frame.columns)):
            values = frame.iloc[:, position].astype(str).str.casefold()
            matches |= values.str.contains(self._query, regex=False, na=False).to_numpy(dtype=bool)
            if matches.all():
                break
        self._matches = matches

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._query:
            return True
        self._build_matches()
        if self._matches is not None:
            return bool(0 <= source_row < len(self._matches) and self._matches[source_row])
        # Permit ordinary Qt table models too, with no assumptions about pandas.
        model = self.sourceModel()
        if model is None:
            return False
        return any(
            self._query in str(model.data(model.index(source_row, column, source_parent), Qt.ItemDataRole.DisplayRole) or "").casefold()
            for column in range(model.columnCount(source_parent))
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        source = self.sourceModel()
        if isinstance(source, DataFrameModel):
            a = source.value_at(left.row(), left.column())
            b = source.value_at(right.row(), right.column())
        else:
            a = source.data(left, self.sortRole())
            b = source.data(right, self.sortRole())
        if _missing(a):
            return False
        if _missing(b):
            return True
        if isinstance(a, numbers.Real) and isinstance(b, numbers.Real):
            return bool(a < b)
        # Type grouping gives mixed text/numeric columns a total, stable order.
        if isinstance(a, numbers.Real) != isinstance(b, numbers.Real):
            return isinstance(a, numbers.Real)
        return str(a).casefold() < str(b).casefold()

    def filtered_frame(self) -> pd.DataFrame:
        """Copy the exact visible proxy rows, in their current sorted order."""
        source = self.sourceModel()
        if not isinstance(source, DataFrameModel):
            raise TypeError("filtered_frame requires a DataFrameModel source.")
        if source.columnCount() == 0:
            return source.frame.iloc[:0].copy()
        rows = [self.mapToSource(self.index(row, 0)).row() for row in range(self.rowCount())]
        return source.frame.iloc[rows].copy()


class WorkerSignals(QObject):
    """Signals from a worker; connect to QObject slots for queued UI delivery."""

    success = Signal(object)
    error = Signal(str)
    progress = Signal(float, int)
    finished = Signal()


class Worker(QRunnable):
    """Run ``function(cancel_event, report_progress)`` in a QThreadPool.

    Set ``worker.cancel`` (or its ``cancellation`` alias) to request cancellation.
    The function decides whether to return a valid partial result or abort. The
    progress callback returns ``False`` once cancellation has been requested,
    matching the Twix reader's cooperative cancellation contract. ``finished``
    always fires, including after exceptions. This class never touches widgets.
    """

    def __init__(self, function: Callable[[threading.Event, Callable[[float, int], bool]], Any]):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()
        self.cancel = threading.Event()
        self.cancellation = self.cancel
        self.cancel_event = self.cancel
        self.setAutoDelete(True)

    def _report(self, fraction: float, count: int = 0) -> bool:
        fraction = float(fraction)
        if not math.isfinite(fraction):
            fraction = 0.0
        self.signals.progress.emit(min(1.0, max(0.0, fraction)), max(0, int(count)))
        return not self.cancel.is_set()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(self.cancel, self._report)
        except Exception as exc:
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.success.emit(result)
        finally:
            self.signals.finished.emit()
