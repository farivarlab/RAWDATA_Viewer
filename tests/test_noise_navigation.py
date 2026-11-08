"""Noise is discoverable across RAID measurements without imaging-only filters."""

import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import struct
import tempfile
import time
import unittest

import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from siemens_explorer.qt_app import MainWindow
from test_twix import adc, end_block, protocol_header, raid


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class NoiseNavigationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "measurements.dat"
        self.noise = np.arange(16, dtype=np.float32) + 1j * np.arange(16, dtype=np.float32)[::-1]
        imaging = bytearray(adc(True, [self.noise, self.noise * 2], line=0))
        struct.pack_into("<H", imaging, 58, 9)  # imaging partition unavailable in noise
        first = protocol_header() + imaging + b"".join(
            adc(True, [self.noise * n, self.noise * (n + 2)], mask=1 << 25, line=n)
            for n in (1, 2)
        ) + end_block(True)
        last = protocol_header() + adc(True, [self.noise, self.noise * 2]) + end_block(True)
        self.path.write_bytes(raid(first, last))
        self.window = MainWindow()
        self.window.resize(1280, 820)
        self.window.show()
        self.window.open_path(self.path)
        self.settle()

    def tearDown(self):
        self.settle()
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(1)
        self.directory.cleanup()

    def settle(self):
        deadline = time.monotonic() + 12
        while self.window.busy or self.window._pending_preview or self.window._preview_timer.isActive() or self.window.pool.activeThreadCount():
            APP.processEvents()
            time.sleep(0.002)  # release the GIL for the real reader worker
            if time.monotonic() > deadline:
                self.fail(f"Timed out: {self.window._job_kind}: {self.window.last_error}")
        APP.processEvents()
        self.assertFalse(self.window.last_error, self.window.last_error)

    def noise_mode(self):
        self.window.kind.setCurrentText("Noise")
        self.settle()

    def test_noise_search_selects_other_measurement_and_displays_raw_matrix(self):
        ui = self.window
        self.assertEqual(ui.measurement.index, 1)
        self.noise_mode()
        self.assertEqual(ui.measurement.index, 0)
        self.assertEqual(len(ui.filtered), 2)
        self.assertEqual(ui.measurement_tree.currentItem().data(0, 256), 0)
        self.assertIsNone(ui.counter_widgets["partition"].currentData())
        self.assertEqual(ui.counter_widgets["partition"].findData(9), -1)
        self.assertEqual(ui.tabs.currentIndex(), 3)
        self.assertTrue(ui.preview_view.splitter.widget(1).isHidden())
        self.assertFalse(ui.slice_navigation.isVisible())
        np.testing.assert_array_equal(ui.preview.kspace, [self.noise, self.noise * 2])
        self.assertIn("Noise", ui.preview_view.kspace_title.text())
        self.assertIn("2 noise readouts", ui.preview_view.stats.text())
        self.assertFalse(ui.rss.isVisible())

    def test_channel_selection_and_reindex_preserve_noise_mode(self):
        ui = self.window
        self.noise_mode()
        ui.select_channel(1)
        self.settle()
        self.assertEqual(ui.tabs.currentIndex(), 3)
        self.assertEqual(ui.preview.metadata["selected_channel_id"], 19)
        expected = ui.preview.kspace.copy()
        ui.reindex()
        self.settle()
        self.assertEqual(ui.kind.currentText(), "Noise")
        self.assertEqual(ui.channel.currentData(), 1)
        np.testing.assert_array_equal(ui.preview.kspace, expected)

    def test_changing_type_discards_incompatible_imaging_counter_filters(self):
        ui = self.window
        ui.reindex(0)
        self.settle()
        self.assertEqual(ui.kind.currentText(), "Image")
        self.assertEqual(ui.counter_widgets["partition"].currentData(), 9)
        self.noise_mode()
        self.assertEqual(len(ui.filtered), 2)
        self.assertEqual(ui.counter_widgets["partition"].findData(9), -1)
        self.assertEqual(ui.preview.metadata["display_kind"], "noise")

    def test_noise_exports_recorded_samples_and_imaging_mode_restores_two_panes(self):
        ui = self.window
        self.noise_mode()
        target = Path(self.directory.name) / "noise.npz"
        ui.export_to("preview", target)
        with np.load(target, allow_pickle=False) as arrays:
            np.testing.assert_array_equal(arrays["raw_samples"], ui.preview.kspace)
            np.testing.assert_array_equal(arrays["acquisition_indices"], [1, 2])
            self.assertNotIn("image", arrays.files)
            self.assertEqual(json.loads(str(arrays["metadata"]))["display_kind"], "noise")
        ui.kind.setCurrentText("Image")
        self.settle()
        self.assertTrue(ui.preview_view.splitter.widget(1).isVisible())
        self.assertTrue(ui.slice_navigation.isVisible())
        self.assertEqual(ui.counter_widgets["partition"].currentData(), 9)
        self.assertNotEqual(ui.preview.metadata.get("display_kind"), "noise")


if __name__ == "__main__":
    unittest.main()
