"""Native Qt model identity, sorting/filtering, and worker-delivery checks."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import unittest

import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, QThreadPool, Qt, Slot
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from siemens_explorer.qt_models import DataFrameFilter, DataFrameModel, Worker


APP = QApplication.instance() or QApplication([])


class TableTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {"scan": [10, 2, 100, 7], "line": [9, 0, 20, 3], "flags": ["ONLINE", "NOISEADJSCAN", "PATREF[0]", "online"], "value": [1.25, np.nan, 20.0, 3.0]},
            index=pd.Index([1010, 1002, 1100, 1007], name="original"),
        )
        self.source = DataFrameModel(self.frame)
        self.proxy = DataFrameFilter()
        self.proxy.setSourceModel(self.source)

    def test_source_is_read_only_and_retains_original_index(self):
        self.assertEqual(self.source.rowCount(), 4)
        self.assertEqual(self.source.columnCount(), 4)
        self.assertEqual(self.source.headerData(0, Qt.Orientation.Vertical), "1010")
        self.assertEqual(self.source.row_id(1), 1002)
        self.assertEqual(self.source.data(self.source.index(0, 3)), "1.25")
        self.assertEqual(self.source.data(self.source.index(1, 3)), "")
        self.assertEqual(self.source.data(self.source.index(0, 0), Qt.ItemDataRole.UserRole), 10)
        self.assertFalse(self.source.flags(self.source.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)

    def test_proxy_sort_is_numeric_and_source_rows_stay_unchanged(self):
        self.proxy.sort(0, Qt.SortOrder.AscendingOrder)
        result = self.proxy.filtered_frame()
        self.assertEqual(result.scan.tolist(), [2, 7, 10, 100])
        self.assertEqual(result.index.tolist(), [1002, 1007, 1010, 1100])
        self.assertEqual(self.source.frame.scan.tolist(), [10, 2, 100, 7])
        self.proxy.sort(0, Qt.SortOrder.DescendingOrder)
        self.assertEqual(self.proxy.filtered_frame().scan.tolist(), [100, 10, 7, 2])

    def test_free_text_is_case_insensitive_literal_and_clears(self):
        self.proxy.set_query("OnLiNe")
        self.assertEqual(self.proxy.filtered_frame().scan.tolist(), [10, 7])
        self.proxy.set_query("PATREF[0]")
        self.assertEqual(self.proxy.filtered_frame().scan.tolist(), [100])
        self.proxy.set_query("does not exist")
        self.assertEqual(self.proxy.rowCount(), 0)
        self.proxy.set_query("")
        self.assertEqual(self.proxy.rowCount(), 4)

    def test_export_retains_filter_sort_order_and_original_identity(self):
        self.proxy.set_query("online")
        self.proxy.sort(0, Qt.SortOrder.AscendingOrder)
        selected_source = self.proxy.mapToSource(self.proxy.index(0, 0))
        self.assertEqual(self.source.row_id(selected_source.row()), 1007)
        exported = self.proxy.filtered_frame()
        self.assertEqual(exported.index.tolist(), [1007, 1010])
        self.assertEqual(exported.scan.tolist(), [7, 10])
        self.assertIn("7,3,online,3.0", exported.to_csv(index=False))

    def test_reset_recomputes_active_filter_and_empty_models_are_valid(self):
        self.proxy.set_query("NOISE")
        self.assertEqual(self.proxy.rowCount(), 1)
        self.source.set_frame(pd.DataFrame({"scan": [88, 91], "flags": ["ONLINE", "NOISE"]}, index=[788, 791]))
        self.assertEqual(self.proxy.filtered_frame().index.tolist(), [791])
        self.source.set_frame(pd.DataFrame(columns=["scan", "flags"]))
        self.assertEqual(self.proxy.rowCount(), 0)
        self.proxy.set_query("")
        self.assertEqual(list(self.proxy.filtered_frame().columns), ["scan", "flags"])
        self.source.set_frame(pd.DataFrame())
        self.assertTrue(self.proxy.filtered_frame().empty)

    def test_uint64_evaluation_masks_sort_exactly(self):
        values = [2**63 + 100, 2**63, 2**63 + 1]
        self.source.set_frame(pd.DataFrame({"eval_mask": np.array(values, dtype=np.uint64)}))
        self.proxy.sort(0, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.proxy.filtered_frame().eval_mask.tolist(), sorted(values))


class Collector(QObject):
    def __init__(self):
        super().__init__()
        self.values = []
        self.errors = []
        self.progress_values = []
        self.completed = False
        self.thread_ids = []

    @Slot(object)
    def success(self, value):
        self.values.append(value)
        self.thread_ids.append(threading.get_ident())

    @Slot(str)
    def error(self, value):
        self.errors.append(value)
        self.thread_ids.append(threading.get_ident())

    @Slot(float, int)
    def progress(self, fraction, count):
        self.progress_values.append((fraction, count))
        self.thread_ids.append(threading.get_ident())

    @Slot()
    def finished(self):
        self.completed = True
        self.thread_ids.append(threading.get_ident())


class WorkerTests(unittest.TestCase):
    def run_worker(self, worker):
        collector = Collector()
        worker.signals.success.connect(collector.success)
        worker.signals.error.connect(collector.error)
        worker.signals.progress.connect(collector.progress)
        worker.signals.finished.connect(collector.finished)
        pool = QThreadPool()
        pool.setMaxThreadCount(1)
        pool.start(worker)
        for _ in range(200):
            if collector.completed:
                break
            QTest.qWait(5)
        self.assertTrue(pool.waitForDone(1000))
        APP.processEvents()
        self.assertTrue(collector.completed)
        return collector

    def test_background_result_and_progress_arrive_on_main_thread(self):
        main_thread = threading.get_ident()

        def function(cancel, progress):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertFalse(cancel.is_set())
            self.assertTrue(progress(0.5, 12))
            return {"readouts": 12}

        collector = self.run_worker(Worker(function))
        self.assertEqual(collector.values, [{"readouts": 12}])
        self.assertEqual(collector.progress_values, [(0.5, 12)])
        self.assertFalse(collector.errors)
        self.assertEqual(set(collector.thread_ids), {main_thread})

    def test_exception_reports_error_and_still_finishes(self):
        def function(cancel, progress):
            raise ValueError("truncated header")

        collector = self.run_worker(Worker(function))
        self.assertEqual(collector.errors, ["ValueError: truncated header"])
        self.assertFalse(collector.values)

    def test_cancellation_is_cooperative_and_prefix_is_returned(self):
        worker = Worker(lambda cancel, progress: {"partial": cancel.is_set(), "continue": progress(0.25, 3)})
        worker.cancellation.set()
        self.assertIs(worker.cancel, worker.cancel_event)
        collector = self.run_worker(worker)
        self.assertEqual(collector.values, [{"partial": True, "continue": False}])


if __name__ == "__main__":
    unittest.main()
