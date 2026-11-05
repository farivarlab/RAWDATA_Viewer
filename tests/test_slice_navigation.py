"""Slice browsing through native controls, real Twix counters, and async previews."""

import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from siemens_explorer import qt_app
from siemens_explorer.qt_app import MainWindow
from siemens_explorer.twix import TwixFile, create_demo_file


class SliceNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.temp = tempfile.TemporaryDirectory(prefix="test-twix-navigation-")
        cls.root = Path(cls.temp.name)
        cls.demo = create_demo_file(cls.root / "synthetic_demo.dat")
        cls.sparse = cls.make_counter_fixture("noncontiguous.dat", "slice")
        cls.partitions = cls.make_counter_fixture("partitions.dat", "partition")
        cls.single = cls.make_counter_fixture("single_slice.dat", "single")

    @classmethod
    def make_counter_fixture(cls, name, mode):
        """Patch actual VD MDH counters without altering complex payloads."""
        path = create_demo_file(cls.root / name)
        with TwixFile(path) as raw:
            measurement = raw.measurements[0]
            scans = raw.index_measurement(measurement).scans
        with path.open("r+b") as output:
            for scan in scans:
                if not scan.is_image:
                    continue
                if mode == "slice":
                    slice_value, partition_value = (3 if scan.slice == 0 else 9), 0
                elif mode == "partition":
                    slice_value, partition_value = 0, (2 if scan.slice == 0 else 5)
                else:
                    slice_value, partition_value = 0, 0
                output.seek(scan.offset + 56)
                output.write(struct.pack("<HH", slice_value, partition_value))
        return path

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.temp.cleanup()

    def setUp(self):
        self.window = MainWindow(theme="light")
        self.window.resize(1280, 820)
        self.window.show()
        self.window.open_path(self.demo)
        self.wait_settled()

    def tearDown(self):
        if self.window.busy:
            self.window.cancel_job()
            self.wait_settled()
        self.window.close()
        self.window.pool.waitForDone(5000)
        self.app.processEvents()

    def wait_for(self, condition, timeout=12):
        deadline = time.monotonic() + timeout
        while not condition():
            self.app.processEvents()
            QTest.qWait(3)
            if time.monotonic() > deadline:
                self.fail(f"Slice operation did not settle within {timeout} seconds")
        self.app.processEvents()

    def wait_settled(self):
        def settled():
            return (
                not self.window.busy
                and self.window.pool.activeThreadCount() == 0
                and not self.window._preview_timer.isActive()
                and not self.window._pending_preview
            )

        self.wait_for(settled)
        QTest.qWait(3)
        self.app.processEvents()

    def select_counter(self, name, value):
        combo = self.window.counter_widgets[name]
        index = combo.findData(value)
        self.assertGreaterEqual(index, 0, f"Missing {name}={value}")
        combo.setCurrentIndex(index)
        self.app.processEvents()

    def open_fixture(self, path):
        self.window.open_path(path)
        self.wait_settled()

    def build_preview(self):
        self.window.build_preview()
        self.wait_settled()
        self.assertIsNotNone(self.window.preview)

    def test_slider_spin_buttons_and_counter_dropdown_stay_synchronized(self):
        ui = self.window
        self.assertEqual(ui._slice_values, [0, 1])
        self.assertEqual(ui.slice_axis.currentData(), "slice")
        self.assertEqual((ui.slice_slider.minimum(), ui.slice_slider.maximum()), (0, 1))
        self.assertEqual((ui.slice_spin.minimum(), ui.slice_spin.maximum()), (0, 2))
        self.assertEqual((ui.slice_slider.value(), ui.slice_spin.value()), (0, 1))
        self.assertFalse(ui.previous_slice_button.isEnabled())
        self.assertTrue(ui.next_slice_button.isEnabled())
        first = ui.signal_view.signal.copy()
        ui.next_slice_button.click()
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 1)
        self.assertEqual((ui.slice_slider.value(), ui.slice_spin.value()), (1, 2))
        self.assertEqual(ui.signal_view.scan.slice, 1)
        self.assertFalse(np.allclose(first, ui.signal_view.signal))
        self.assertTrue(ui.previous_slice_button.isEnabled())
        self.assertFalse(ui.next_slice_button.isEnabled())
        ui.slice_slider.setValue(0)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 0)
        self.assertEqual(ui.slice_spin.value(), 1)
        ui.slice_spin.setValue(2)
        self.wait_settled()
        self.assertEqual(ui.slice_slider.value(), 1)
        self.select_counter("slice", 0)
        self.wait_settled()
        self.assertEqual((ui.slice_slider.value(), ui.slice_spin.value()), (0, 1))
        ui.previous_slice_button.click()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 0)

    def test_image_scroll_signal_updates_slice_and_rebuilds_actual_preview(self):
        ui = self.window
        self.build_preview()
        first = ui.preview.image.copy()
        ui.preview_view.slice_step.emit(1)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 1)
        self.assertEqual(ui.preview.metadata["slice"], 1)
        self.assertIs(ui.preview_view.preview, ui.preview)
        self.assertFalse(np.allclose(first, ui.preview.image))
        ui.preview_view.slice_step.emit(-1)
        self.wait_settled()
        self.assertEqual(ui.preview.metadata["slice"], 0)
        np.testing.assert_allclose(first, ui.preview.image, rtol=2e-6, atol=1e-7)

    def test_slice_change_in_preview_tab_builds_without_explicit_button(self):
        ui = self.window
        self.assertIsNone(ui.preview)
        ui.tabs.setCurrentIndex(3)
        ui.navigate_slice(1)
        self.wait_settled()
        self.assertIsNotNone(ui.preview)
        self.assertEqual(ui.preview.metadata["slice"], 1)
        self.assertEqual(ui.preview.metadata["echo"], 0)

    def test_noncontiguous_scanner_values_use_ordinal_navigation_and_clamp(self):
        ui = self.window
        self.open_fixture(self.sparse)
        self.assertEqual(ui._slice_values, [3, 9])
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 3)
        self.assertEqual(ui.slice_spin.value(), 1)
        ui.navigate_slice(1)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 9)
        self.assertEqual(ui.signal_view.scan.slice, 9)
        self.assertEqual(ui.slice_spin.value(), 2)
        ui.step_slice(20)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 9)
        ui.navigate_slice(-100)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 3)
        self.assertEqual(ui.slice_spin.value(), 1)
        self.build_preview()
        ui.step_slice(1)
        self.wait_settled()
        self.assertEqual(ui.preview.metadata["slice"], 9)

    def test_single_slice_3d_data_automatically_navigates_partitions(self):
        ui = self.window
        self.open_fixture(self.partitions)
        self.assertEqual(ui.slice_axis.currentData(), "partition")
        self.assertEqual(ui._slice_values, [2, 5])
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 0)
        self.assertEqual(ui.counter_widgets["partition"].currentData(), 2)
        self.build_preview()
        first = ui.preview.image.copy()
        ui.step_slice(1)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["partition"].currentData(), 5)
        self.assertEqual(ui.preview.metadata["partition"], 5)
        self.assertFalse(np.allclose(first, ui.preview.image))

    def test_single_value_axis_disables_navigation(self):
        ui = self.window
        self.open_fixture(self.single)
        self.assertEqual(ui._slice_values, [0])
        self.assertFalse(ui.slice_slider.isEnabled())
        self.assertFalse(ui.slice_spin.isEnabled())
        self.assertFalse(ui.previous_slice_button.isEnabled())
        self.assertFalse(ui.next_slice_button.isEnabled())
        ui.step_slice(1)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 0)

    def test_all_counter_selection_can_enter_navigation_from_either_end(self):
        ui = self.window
        self.select_counter("slice", None)
        self.wait_settled()
        self.assertIsNone(ui.counter_widgets["slice"].currentData())
        self.assertEqual(ui.slice_spin.value(), 0)
        self.assertEqual(len(ui.filtered), 128)
        ui.step_slice(1)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 0)
        self.assertEqual(ui.slice_spin.value(), 1)
        self.select_counter("slice", None)
        ui.step_slice(-1)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 1)
        self.assertEqual(ui.slice_spin.value(), 2)

    def test_navigation_is_disabled_while_opening_a_replacement_dataset(self):
        ui = self.window
        started, release = threading.Event(), threading.Event()
        original = TwixFile.index_measurement

        def delayed(raw, measurement, *args, **kwargs):
            started.set()
            release.wait(3)
            return original(raw, measurement, *args, **kwargs)

        try:
            with patch.object(TwixFile, "index_measurement", delayed):
                ui.open_path(self.sparse)
                self.wait_for(started.is_set)
                self.assertTrue(ui.busy)
                self.assertFalse(ui.slice_slider.isEnabled())
                self.assertFalse(ui.slice_spin.isEnabled())
                self.assertFalse(ui.previous_slice_button.isEnabled())
                self.assertFalse(ui.next_slice_button.isEnabled())
                ui.step_slice(1)
                self.assertEqual(ui.counter_widgets["slice"].currentData(), 0)
                release.set()
                self.wait_settled()
        finally:
            release.set()
        self.assertEqual(ui._slice_values, [3, 9])
        self.assertEqual(ui.counter_widgets["slice"].currentData(), 3)

    def test_page_keys_navigate_and_clamp_when_slider_has_focus(self):
        ui = self.window
        ui.activateWindow()
        ui.slice_slider.setFocus()
        QTest.qWait(10)
        for key, expected in ((Qt.Key.Key_PageUp, 0), (Qt.Key.Key_PageDown, 1),
                              (Qt.Key.Key_PageDown, 1), (Qt.Key.Key_PageUp, 0)):
            QTest.keyClick(ui.slice_slider, key)
            self.wait_settled()
            self.assertEqual(ui.counter_widgets["slice"].currentData(), expected)

    def test_navigation_preserves_echo_channel_and_coil_combination(self):
        ui = self.window
        self.select_counter("echo", 1)
        channel = ui.channel.findData(1)
        ui.channel.setCurrentIndex(channel)
        ui.rss.setChecked(True)
        self.build_preview()
        ui.step_slice(1)
        self.wait_settled()
        self.assertEqual(ui.counter_widgets["echo"].currentData(), 1)
        self.assertEqual(ui.channel.currentData(), 1)
        self.assertTrue(ui.rss.isChecked())
        self.assertEqual(ui.preview.metadata["slice"], 1)
        self.assertEqual(ui.preview.metadata["echo"], 1)
        self.assertEqual(ui.preview.metadata["selected_channel"], 1)
        self.assertEqual(ui.preview.channel_count, 4)

    def test_navigation_preserves_zoom_and_window_levels(self):
        ui = self.window
        self.build_preview()
        image = ui.preview_view.image_view
        kspace = ui.preview_view.kspace_view
        image.getView().getViewBox().setRange(xRange=(20, 70), yRange=(10, 50), padding=0)
        kspace.getView().getViewBox().setRange(xRange=(30, 80), yRange=(15, 45), padding=0)
        image.setLevels(0.15, 0.65)
        kspace.setLevels(1.0, 5.0)
        self.app.processEvents()
        image_range = np.asarray(image.getView().getViewBox().viewRange())
        kspace_range = np.asarray(kspace.getView().getViewBox().viewRange())
        image_levels = image.getHistogramWidget().getLevels()
        kspace_levels = kspace.getHistogramWidget().getLevels()
        ui.step_slice(1)
        self.wait_settled()
        self.assertEqual(ui.preview.metadata["slice"], 1)
        np.testing.assert_allclose(image.getView().getViewBox().viewRange(), image_range, rtol=1e-6, atol=1e-6)
        np.testing.assert_allclose(kspace.getView().getViewBox().viewRange(), kspace_range, rtol=1e-6, atol=1e-6)
        np.testing.assert_allclose(image.getHistogramWidget().getLevels(), image_levels, rtol=1e-6, atol=1e-6)
        np.testing.assert_allclose(kspace.getHistogramWidget().getLevels(), kspace_levels, rtol=1e-6, atol=1e-6)

    def test_rapid_navigation_coalesces_latest_slice_without_showing_stale_result(self):
        ui = self.window
        self.build_preview()
        started, release = threading.Event(), threading.Event()
        worker_threads = []
        original = qt_app.build_cartesian_preview
        main_thread = threading.get_ident()

        def delayed(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            started.set()
            release.wait(3)
            return original(*args, **kwargs)

        heartbeats = []
        heartbeat = QTimer(ui)
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: heartbeats.append(time.monotonic()))
        heartbeat.start()
        try:
            with patch.object(qt_app, "build_cartesian_preview", delayed), patch.object(
                ui.preview_view, "set_preview", wraps=ui.preview_view.set_preview
            ) as displayed:
                ui.step_slice(1)
                self.wait_for(started.is_set)
                self.assertTrue(ui.busy)
                self.assertTrue(ui.slice_slider.isEnabled())
                self.assertNotEqual(worker_threads[0], main_thread)
                before = time.monotonic()
                ui.step_slice(-1)
                ui.step_slice(1)
                ui.step_slice(-1)
                self.assertLess(time.monotonic() - before, 0.5)
                baseline = len(heartbeats)
                # Let the debounce expire while the obsolete worker is still
                # blocked, exercising continuation after a slow slice load.
                self.wait_for(lambda: len(heartbeats) >= baseline + 15)
                self.assertTrue(ui._pending_preview)
                self.assertFalse(ui._preview_timer.isActive())
                self.assertEqual(ui.counter_widgets["slice"].currentData(), 0)
                release.set()
                self.wait_settled()
                self.assertEqual(ui.preview.metadata["slice"], 0)
                self.assertIs(ui.preview_view.preview, ui.preview)
                self.assertEqual(ui._preview_timer.interval(), 100)
                for call in displayed.call_args_list:
                    self.assertEqual(call.args[0].metadata["slice"], 0)
        finally:
            release.set()
            heartbeat.stop()


if __name__ == "__main__":
    unittest.main()
