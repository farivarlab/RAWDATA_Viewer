"""Native Qt interaction checks using generated Twix data and a real event loop."""

import json
import io
from contextlib import redirect_stdout
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractButton, QMessageBox

from siemens_explorer.qt_app import MainWindow
from siemens_explorer.twix import TwixFile, create_demo_file


class NativeExplorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.temp = tempfile.TemporaryDirectory(prefix="test-twix-qt-")
        cls.root = Path(cls.temp.name)
        cls.demo = create_demo_file(cls.root / "synthetic_demo.dat")

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.temp.cleanup()

    def setUp(self):
        self.dialogs = [patch.object(QMessageBox, method, return_value=QMessageBox.StandardButton.Ok)
                        for method in ("critical", "warning", "information")]
        for item in self.dialogs:
            item.start()
        self.window = MainWindow(theme="light")
        self.window.resize(1280, 820)
        self.window.show()
        self.app.processEvents()
        self.window.open_path(self.demo)
        self.wait_idle()

    def tearDown(self):
        try:
            if self.window.busy:
                self.window.cancel_job()
                self.wait_idle()
            self.window.close()
            self.window.pool.waitForDone(5000)
            self.app.processEvents()
        finally:
            for item in reversed(self.dialogs):
                item.stop()

    def wait_for(self, condition, timeout=10):
        deadline = time.monotonic() + timeout
        while not condition():
            self.app.processEvents()
            QTest.qWait(2)
            if time.monotonic() > deadline:
                self.fail(f"Qt operation did not finish within {timeout} seconds")
        self.app.processEvents()

    def wait_idle(self):
        self.wait_for(lambda: not self.window.busy and self.window.pool.activeThreadCount() == 0)
        # Flush queued worker signals before asserting widget/model state.
        QTest.qWait(2)
        self.app.processEvents()

    def select_counter(self, name, value):
        combo = self.window.counter_widgets[name]
        index = combo.findData(value)
        self.assertGreaterEqual(index, 0, f"Missing {name}={value} option")
        combo.setCurrentIndex(index)
        self.app.processEvents()

    def select_tab(self, text):
        for index in range(self.window.tabs.count()):
            if text.casefold() in self.window.tabs.tabText(index).casefold():
                self.window.tabs.setCurrentIndex(index)
                self.app.processEvents()
                return
        self.fail(f"No tab contains {text!r}")

    def test_native_window_loads_headers_and_single_imaging_group(self):
        ui = self.window
        self.assertEqual(len(ui.scans), 260)
        self.assertTrue(ui.scan_index.complete)
        self.assertEqual(len(ui.filtered), 64)
        self.assertTrue(all(scan.is_image and scan.slice == 0 and scan.echo == 0 for scan in ui.filtered))
        self.assertEqual(ui.acq_model.rowCount(), 64)
        self.assertEqual(set(ui.acq_model.frame["line"]), set(range(64)))
        self.assertIsNotNone(ui.signal_view.signal)
        self.assertEqual(ui.signal_view.signal.shape, (128,))
        self.assertEqual(ui.signal_view.scan.index, ui.scan_spin.value())
        self.assertEqual(ui.tabs.count(), 7)
        self.assertIsNotNone(ui.menuBar())

    def test_slice_echo_and_channel_controls_change_actual_readouts(self):
        ui = self.window
        first = ui.signal_view.signal.copy()
        self.select_counter("echo", 1)
        echo = ui.signal_view.signal.copy()
        self.assertEqual(ui.signal_view.scan.echo, 1)
        np.testing.assert_allclose(np.abs(echo), np.abs(first) * 0.74, rtol=2e-5, atol=1e-6)
        self.select_counter("slice", 1)
        translated = ui.signal_view.signal.copy()
        self.assertEqual(ui.signal_view.scan.slice, 1)
        self.assertFalse(np.allclose(echo.real, translated.real))
        channel_index = ui.channel.findData(1)
        self.assertGreaterEqual(channel_index, 0)
        ui.channel.setCurrentIndex(channel_index)
        self.app.processEvents()
        self.assertEqual(ui.signal_view.channel_index, 1)
        self.assertFalse(np.allclose(translated, ui.signal_view.signal))
        self.assertEqual(set(ui.acq_model.frame["slice"]), {1})
        self.assertEqual(set(ui.acq_model.frame["echo"]), {1})

    def test_sorted_table_selection_retains_original_scan_identity(self):
        ui = self.window
        line_column = list(ui.acq_model.frame.columns).index("line")
        scan_column = list(ui.acq_model.frame.columns).index("scan")
        ui.acq_proxy.sort(line_column, Qt.SortOrder.DescendingOrder)
        self.app.processEvents()
        index = ui.acq_proxy.index(0, scan_column)
        expected = int(index.data(Qt.ItemDataRole.UserRole))
        self.assertEqual(ui.scans[expected].line, 63)
        ui.acq_table.setCurrentIndex(index)
        ui.acq_table.selectRow(0)
        self.app.processEvents()
        self.assertEqual(ui.scan_spin.value(), expected)
        self.assertEqual(ui.signal_view.scan.index, expected)

    def test_protocol_search_clears_and_raw_header_search_preserves_export(self):
        ui = self.window
        original_rows = ui.protocol_proxy.rowCount()
        self.assertGreater(original_rows, 2)
        ui.protocol_search.setText("alTE")
        self.app.processEvents()
        self.assertEqual(ui.protocol_proxy.rowCount(), 2)
        ui.protocol_search.clear()
        self.app.processEvents()
        self.assertEqual(ui.protocol_proxy.rowCount(), original_rows)
        ui.header_search.setText("alTE")
        self.app.processEvents()
        # Search may highlight a buffer or filter its lines; the original export
        # must always remain complete.
        output = self.root / "headers-search.txt"
        ui.export_to("headers", output)
        original = output.read_text(encoding="utf-8")
        self.assertIn("alTE[0]", original)
        self.assertIn("alTR[0]", original)
        ui.header_search.clear()
        self.app.processEvents()
        self.assertIn("alTR[0]", ui.raw_text.toPlainText())

    def test_csv_exports_respect_proxy_rows_and_signal_values(self):
        ui = self.window
        line_column = list(ui.acq_model.frame.columns).index("line")
        ui.acq_proxy.set_query("LASTSCANINSLICE")
        ui.acq_proxy.sort(line_column, Qt.SortOrder.DescendingOrder)
        self.app.processEvents()
        scan_path = self.root / "scans-filtered.csv"
        ui.export_to("scans", scan_path)
        exported = pd.read_csv(scan_path)
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported.line.tolist(), [63])
        self.assertEqual(exported["scan"].tolist(), ui.acq_proxy.filtered_frame()["scan"].tolist())
        signal_path = self.root / "readout.csv"
        ui.export_to("signal", signal_path)
        signal = pd.read_csv(signal_path)
        self.assertEqual(len(signal), 128)
        self.assertTrue({"real", "imaginary", "magnitude", "phase_rad"}.issubset(signal.columns))
        np.testing.assert_allclose(signal.real, ui.signal_view.signal.real, rtol=2e-6, atol=1e-6)
        np.testing.assert_allclose(signal.imaginary, ui.signal_view.signal.imag, rtol=2e-6, atol=1e-6)

    def test_metadata_export_contains_actual_measurement_and_protocol(self):
        path = self.root / "measurement.json"
        self.window.export_to("metadata", path)
        metadata = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["indexed_acquisitions"], 260)
        self.assertTrue(metadata["index_complete"])
        self.assertEqual(metadata["measurement"]["id"], self.window.measurement.id)
        self.assertEqual(metadata["measurement"]["protocol"]["alTE[0]"], 4500)

    def test_cartesian_preview_export_and_coil_change_invalidation(self):
        ui = self.window
        ui.build_preview()
        self.wait_idle()
        self.assertIsNotNone(ui.preview)
        self.assertEqual(ui.preview.kspace.shape, (64, 128))
        self.assertIs(ui.preview_view.preview, ui.preview)
        self.assertFalse(ui.preview.metadata["trajectory_verified"])
        path = self.root / "preview.npz"
        ui.export_to("preview", path)
        with np.load(path, allow_pickle=False) as saved:
            np.testing.assert_array_equal(saved["kspace"], ui.preview.kspace)
            np.testing.assert_array_equal(saved["image"], ui.preview.image)
            np.testing.assert_array_equal(saved["coverage"], ui.preview.coverage)
            self.assertGreater(len(saved["warnings"]), 0)
        ui.rss.setChecked(True)
        self.app.processEvents()
        self.assertIsNone(ui.preview)
        self.assertIsNone(ui.preview_view.preview)
        ui.build_preview()
        self.wait_idle()
        self.assertEqual(ui.preview.channel_count, 4)
        self.assertTrue(ui.preview.metadata["channel_ids_verified"])
        self.select_counter("slice", 1)
        self.assertIsNone(ui.preview)

    def test_native_plot_png_exports_are_readable_images(self):
        ui = self.window
        self.select_tab("Signal")
        signal = self.root / "signal.png"
        ui.export_to("signal_png", signal)
        image = QImage(str(signal))
        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 100)
        ui.build_preview()
        self.wait_idle()
        self.select_tab("K-space")
        preview = self.root / "preview.png"
        ui.export_to("preview_png", preview)
        image = QImage(str(preview))
        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 200)

    def test_empty_selection_clears_signal_and_preview(self):
        ui = self.window
        self.select_counter("slice", 1)
        ui.kind.setCurrentText("Noise")
        self.app.processEvents()
        self.assertEqual(len(ui.filtered), 0)
        self.assertEqual(ui.acq_model.rowCount(), 0)
        self.assertIsNone(ui.signal_view.signal)
        self.assertIsNone(ui.preview)
        ui.kind.setCurrentText("Image")
        self.app.processEvents()
        self.assertEqual(len(ui.filtered), 64)
        self.assertEqual(ui.signal_view.scan.slice, 1)

    def test_bad_new_file_preserves_previous_dataset_and_signal(self):
        ui = self.window
        previous = ui.twix
        signal = ui.signal_view.signal.copy()
        bad = self.root / "not_twix.dat"
        bad.write_bytes(b"not a valid Siemens raw file")
        ui.open_path(bad)
        self.wait_idle()
        self.assertIs(ui.twix, previous)
        self.assertEqual(len(ui.scans), 260)
        np.testing.assert_array_equal(ui.signal_view.signal, signal)

    def test_reindex_limit_exposes_partial_prefix_then_restores_full_index(self):
        ui = self.window
        ui.index_limit.setValue(100)
        ui.reindex()
        self.wait_idle()
        self.assertEqual(len(ui.scans), 100)
        self.assertFalse(ui.scan_index.complete)
        self.assertTrue(any("limit" in warning.casefold() for warning in ui.scan_index.warnings))
        ui.index_limit.setValue(250_000)
        ui.reindex()
        self.wait_idle()
        self.assertEqual(len(ui.scans), 260)
        self.assertTrue(ui.scan_index.complete)

    def test_workspace_has_no_demo_or_manual_preview_controls(self):
        ui = self.window
        self.assertFalse(hasattr(ui, "demo_action"))
        self.assertFalse(hasattr(ui, "preview_action"))
        self.assertFalse(hasattr(ui, "preview_button"))
        self.assertFalse(hasattr(ui, "open_demo"))
        texts = [action.text().casefold() for action in ui.findChildren(QAction)]
        texts.extend(button.text().casefold() for button in ui.findChildren(QAbstractButton))
        for text in texts:
            self.assertNotIn("demo", text)
            self.assertNotIn("synthetic", text)
            self.assertNotIn("build preview", text)
            self.assertNotIn("build cartesian preview", text)
        from siemens_explorer.__main__ import main
        help_text = io.StringIO()
        with redirect_stdout(help_text), self.assertRaises(SystemExit) as result:
            main(["--help"])
        self.assertEqual(result.exception.code, 0)
        self.assertNotIn("--demo", help_text.getvalue())

    def test_export_cannot_overwrite_open_raw_data(self):
        size_before = self.demo.stat().st_size
        with self.assertRaises(ValueError):
            self.window.export_to("headers", self.demo)
        self.assertEqual(self.demo.stat().st_size, size_before)
        self.assertEqual(len(self.window.scans), 260)

    def test_export_cannot_overwrite_source_through_a_hard_link(self):
        alias = self.root / "source_hardlink.dat"
        try:
            os.link(self.demo, alias)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Filesystem cannot create hard links: {exc}")
        try:
            size_before = self.demo.stat().st_size
            with self.assertRaises(ValueError):
                self.window.export_to("headers", alias)
            self.assertEqual(self.demo.stat().st_size, size_before)
            self.assertEqual(alias.stat().st_size, size_before)
            signal = self.window.twix.read_signal(self.window.measurement, self.window.signal_view.scan)
            np.testing.assert_array_equal(signal, self.window.signal_view.signal)
        finally:
            alias.unlink()

    def test_close_dataset_releases_read_handle_and_allows_reopen(self):
        ui = self.window
        original = ui.twix
        ui.close_dataset()
        self.app.processEvents()
        self.assertIsNone(ui.twix)
        self.assertIsNone(ui.signal_view.signal)
        self.assertIsNone(ui.preview)
        self.assertEqual(ui.acq_model.rowCount(), 0)
        self.assertEqual(len(ui.scans), 0)
        self.assertTrue(original._closed)
        ui.open_path(self.demo)
        self.wait_idle()
        self.assertEqual(len(ui.scans), 260)
        self.assertIsNot(ui.twix, original)

    def test_cancel_indexing_returns_control_without_leaving_worker(self):
        ui = self.window
        started = threading.Event()
        original = TwixFile.index_measurement

        def slow_index(raw, measurement, *args, progress=None, **kwargs):
            started.set()
            for count in range(1000):
                if progress is not None and progress(0.01, count) is False:
                    kwargs["max_scans"] = 1
                    return original(raw, measurement, progress=None, **kwargs)
                time.sleep(0.002)
            return original(raw, measurement, progress=progress, **kwargs)

        with patch.object(TwixFile, "index_measurement", slow_index):
            ui.open_path(self.demo)
            self.wait_for(started.is_set)
            self.assertTrue(ui.busy)
            ui.cancel_job()
            self.wait_idle()
        self.assertFalse(ui.busy)
        self.assertEqual(ui.pool.activeThreadCount(), 0)
        # A cancelled replacement may retain the previous dataset or its valid
        # indexed prefix; either result must have a coherent signal state.
        self.assertIn(len(ui.scans), (1, 260))
        if ui.signal_view.signal is not None:
            self.assertIn(ui.signal_view.scan, ui.scans)

    def test_close_during_job_defers_until_worker_finishes_and_closes_both_handles(self):
        ui = self.window
        started = threading.Event()
        opened = []
        original = TwixFile.index_measurement
        previous = ui.twix

        def slow_index(raw, measurement, *args, progress=None, **kwargs):
            opened.append(raw)
            started.set()
            for count in range(1000):
                if progress is not None and progress(0.01, count) is False:
                    kwargs["max_scans"] = 1
                    return original(raw, measurement, progress=None, **kwargs)
                time.sleep(0.002)
            return original(raw, measurement, progress=progress, **kwargs)

        with patch.object(TwixFile, "index_measurement", slow_index):
            ui.open_path(self.demo)
            self.wait_for(started.is_set)
            self.assertTrue(ui.busy)
            ui.close()
            self.wait_idle()
            self.wait_for(lambda: not ui.isVisible())
        self.assertEqual(ui.pool.activeThreadCount(), 0)
        self.assertIsNone(ui.twix)
        self.assertTrue(previous._closed)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0]._closed)

    def test_theme_switch_preserves_raw_signal_and_data(self):
        from siemens_explorer.qt_theme import DARK, apply_theme

        ui = self.window
        signal = ui.signal_view.signal.copy()
        if hasattr(ui, "set_theme"):
            ui.set_theme("dark")
        else:
            apply_theme(self.app, "dark")
        self.app.processEvents()
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window).name(), DARK["window"])
        np.testing.assert_array_equal(ui.signal_view.signal, signal)
        self.assertEqual(len(ui.scans), 260)


if __name__ == "__main__":
    unittest.main()
