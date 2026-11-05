"""Individual receive-channel browsing using real Twix payloads and Qt events."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from siemens_explorer import qt_app
from siemens_explorer.qt_app import MainWindow
from siemens_explorer.twix import TwixFile, create_demo_file


class ChannelNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.temp = tempfile.TemporaryDirectory(prefix="test-channel-browser-")
        cls.path = create_demo_file(Path(cls.temp.name) / "channel_fixture.dat")
        cls.physical_ids = [7, 12, 42, 99]
        with TwixFile(cls.path) as raw:
            measurement = raw.measurements[0]
            scans = raw.index_measurement(measurement).scans
        with cls.path.open("r+b") as output:
            for scan in scans:
                for ordinal, physical_id in enumerate(cls.physical_ids):
                    channel_header = scan.offset + 192 + ordinal * (32 + scan.samples * 8)
                    output.seek(channel_header + 24)
                    output.write(struct.pack("<H", physical_id))

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.temp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.window.resize(1280, 820)
        self.window.show()
        self.window.open_path(self.path)
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
                self.fail("Channel operation did not finish in time")
        self.app.processEvents()

    def wait_settled(self):
        ui = self.window
        self.wait_for(lambda: not ui.busy and ui.pool.activeThreadCount() == 0
                      and not ui._pending_preview and not ui._preview_timer.isActive())
        QTest.qWait(3)
        self.app.processEvents()

    def select_counter(self, name, value):
        combo = self.window.counter_widgets[name]
        target = combo.findData(value)
        self.assertGreaterEqual(target, 0)
        combo.setCurrentIndex(target)
        self.wait_settled()

    def select_channel(self, ordinal):
        self.window.channel_browser.list.setCurrentRow(ordinal)
        self.wait_settled()

    def open_preview(self):
        self.window.tabs.setCurrentIndex(3)
        self.wait_settled()
        self.assertIsNotNone(self.window.preview)

    def test_browser_lists_actual_physical_ids_and_one_click_updates_signal(self):
        ui = self.window
        self.assertEqual(list(ui.channel_browser.channel_ids), self.physical_ids)
        self.assertEqual(ui.channel_browser.list.count(), 4)
        first = ui.signal_view.signal.copy()
        for ordinal, physical_id in enumerate(self.physical_ids):
            item_text = ui.channel_browser.list.item(ordinal).text()
            self.assertIn(str(ordinal), item_text)
            self.assertIn(f"ID {physical_id}", item_text)
        self.select_channel(2)
        self.assertEqual(ui.channel.currentData(), 2)
        self.assertEqual(ui.signal_view.channel_index, 2)
        self.assertEqual(ui.signal_view.channel_id, 42)
        self.assertFalse(np.allclose(first, ui.signal_view.signal))
        expected = ui.twix.read_signal(ui.measurement, ui.current_scan(), 2)
        np.testing.assert_array_equal(ui.signal_view.signal, expected)

    def test_individual_channel_automatically_updates_kspace_without_build_button(self):
        ui = self.window
        self.open_preview()
        initial = ui.preview.kspace.copy()
        self.select_channel(1)
        self.assertEqual(ui.preview.metadata["selected_channel"], 1)
        self.assertEqual(ui.preview.metadata["selected_channel_id"], 12)
        self.assertEqual(ui.preview.channel_count, 1)
        self.assertFalse(np.allclose(initial, ui.preview.kspace))
        self.assertIs(ui.preview_view.preview, ui.preview)

    def test_channel_selection_leaves_combined_mode_for_individual_coil(self):
        ui = self.window
        self.open_preview()
        ui.rss.setChecked(True)
        self.wait_settled()
        self.assertEqual(ui.preview.channel_count, 4)
        self.select_channel(2)
        self.assertFalse(ui.rss.isChecked())
        self.assertEqual(ui.preview.channel_count, 1)
        self.assertEqual(ui.preview.metadata["selected_channel_id"], 42)

    def test_clicking_highlighted_channel_leaves_combined_mode(self):
        ui = self.window
        self.open_preview()
        ui.rss.setChecked(True)
        self.wait_settled()
        self.assertEqual(ui.preview.channel_count, 4)
        browser = ui.channel_browser.list
        current = browser.currentItem()
        point = browser.visualItemRect(current).center()
        QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.wait_settled()
        self.assertFalse(ui.rss.isChecked())
        self.assertEqual(ui.preview.channel_count, 1)
        self.assertEqual(ui.preview.metadata["selected_channel"], 0)

    def test_channel_steps_clamp_and_preserve_slice_echo_and_views(self):
        ui = self.window
        self.select_counter("slice", 1)
        self.select_counter("echo", 1)
        self.open_preview()
        image = ui.preview_view.image_view
        image.getView().getViewBox().setRange(xRange=(20, 70), yRange=(10, 50), padding=0)
        image.setLevels(0.1, 0.6)
        self.app.processEvents()
        image_range = np.asarray(image.getView().getViewBox().viewRange())
        levels = image.getHistogramWidget().getLevels()
        ui.step_channel(-100)
        self.wait_settled()
        self.assertEqual(ui.channel.currentData(), 0)
        ui.step_channel(1)
        self.wait_settled()
        self.assertEqual(ui.channel.currentData(), 1)
        self.assertEqual(ui.channel_browser.list.currentRow(), 1)
        self.assertEqual(ui.preview.metadata["slice"], 1)
        self.assertEqual(ui.preview.metadata["echo"], 1)
        np.testing.assert_allclose(image.getView().getViewBox().viewRange(), image_range, rtol=1e-6, atol=1e-6)
        np.testing.assert_allclose(image.getHistogramWidget().getLevels(), levels, rtol=1e-6, atol=1e-6)
        ui.step_channel(100)
        self.wait_settled()
        self.assertEqual(ui.channel.currentData(), 3)
        ui.step_channel(1)
        self.wait_settled()
        self.assertEqual(ui.channel.currentData(), 3)

    def test_previous_next_actions_work_with_channel_list_focus(self):
        ui = self.window
        ui.activateWindow()
        ui.channel_browser.list.setFocus()
        QTest.qWait(10)
        for key, expected in ((Qt.Key.Key_Left, 0), (Qt.Key.Key_Right, 1),
                              (Qt.Key.Key_Left, 0)):
            QTest.keyClick(ui.channel_browser.list, key, Qt.KeyboardModifier.ControlModifier)
            self.wait_settled()
            self.assertEqual(ui.channel.currentData(), expected)

    def test_rapid_channel_changes_keep_latest_result_and_gui_responsive(self):
        ui = self.window
        self.open_preview()
        started, release = threading.Event(), threading.Event()
        original = qt_app.build_cartesian_preview

        def delayed(*args, **kwargs):
            started.set()
            release.wait(3)
            return original(*args, **kwargs)

        heartbeats = []
        timer = QTimer(ui)
        timer.setInterval(10)
        timer.timeout.connect(lambda: heartbeats.append(time.monotonic()))
        timer.start()
        try:
            with patch.object(qt_app, "build_cartesian_preview", delayed), patch.object(
                ui.preview_view, "set_preview", wraps=ui.preview_view.set_preview
            ) as displayed:
                ui.channel_browser.list.setCurrentRow(1)
                self.wait_for(started.is_set)
                self.assertTrue(ui.busy)
                self.assertTrue(ui.channel_browser.isEnabled())
                ui.channel_browser.list.setCurrentRow(2)
                ui.channel_browser.list.setCurrentRow(3)
                baseline = len(heartbeats)
                self.wait_for(lambda: len(heartbeats) >= baseline + 5)
                self.assertEqual(ui.channel.currentData(), 3)
                release.set()
                self.wait_settled()
                self.assertEqual(ui.preview.metadata["selected_channel"], 3)
                self.assertEqual(ui.preview.metadata["selected_channel_id"], 99)
                for call in displayed.call_args_list:
                    self.assertEqual(call.args[0].metadata["selected_channel"], 3)
        finally:
            release.set()
            timer.stop()

    def test_close_dataset_clears_channel_browser(self):
        ui = self.window
        ui.close_dataset()
        self.app.processEvents()
        self.assertEqual(ui.channel_browser.list.count(), 0)
        self.assertFalse(ui.channel_browser.isEnabled())

    def test_data_blocks_open_without_payload_reads_and_select_original_channel(self):
        ui = self.window
        line_column = list(ui.acq_model.frame.columns).index("line")
        ui.acq_proxy.sort(line_column, Qt.SortOrder.DescendingOrder)
        target_scan = next(scan for scan in ui.filtered if scan.line == 63)
        with patch.object(ui.twix, "read_signal", side_effect=AssertionError("Unexpected payload read")) as reads:
            ui.tabs.setCurrentIndex(6)
            self.wait_settled()
            reads.assert_not_called()
        blocks = []
        for item in ui.blocks_view.scene.items():
            metadata = item.data(0)
            if isinstance(metadata, dict) and metadata.get("kind") == "channel":
                blocks.append(metadata)
        self.assertTrue(any(block["scan_index"] == target_scan.index and block["channel_index"] == 2 for block in blocks))
        ui.blocks_view.block_selected.emit(target_scan.index, 2)
        self.wait_settled()
        self.assertEqual(ui.tabs.currentIndex(), 2)
        self.assertEqual(ui.scan_spin.value(), target_scan.index)
        self.assertEqual(ui.channel.currentData(), 2)
        self.assertEqual(ui.channel_browser.list.currentRow(), 2)
        self.assertEqual(ui.signal_view.channel_id, 42)
        expected = ui.twix.read_signal(ui.measurement, target_scan, 2)
        np.testing.assert_array_equal(ui.signal_view.signal, expected)
        ui.close_dataset()
        self.app.processEvents()
        self.assertIsNone(ui.blocks_view.raw)
        self.assertEqual(ui.blocks_view.scans, [])


if __name__ == "__main__":
    unittest.main()
