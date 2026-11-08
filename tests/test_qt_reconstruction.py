"""Reconstruction controls, caches, and volume browsing through actual Twix I/O."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import json
import struct
import tempfile
import time
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from siemens_explorer.qt_app import MainWindow
from siemens_explorer.twix import TwixFile, create_demo_file
from wheel_events import wheel


class QtReconstructionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.temp = tempfile.TemporaryDirectory(prefix="test-twix-reconstruction-")
        cls.path = create_demo_file(Path(cls.temp.name) / "coils.dat")
        cls.no_noise = create_demo_file(Path(cls.temp.name) / "no_noise.dat")
        with TwixFile(cls.no_noise) as raw:
            scans = raw.index_measurement(0).scans
        with cls.no_noise.open("r+b") as output:
            for scan in scans:
                if "NOISEADJSCAN" in scan.flags:
                    output.seek(scan.offset + 40)
                    output.write(struct.pack("<Q", 1 << 21))  # Phase correction
        cls.volume = create_demo_file(Path(cls.temp.name) / "volume.dat")
        with TwixFile(cls.volume) as raw:
            scans = raw.index_measurement(0).scans
        # Two formerly separate 2D slices become two partition encodes of one
        # 3D acquisition. This fixture tests the UI plumbing, not image quality.
        with cls.volume.open("r+b") as output:
            for scan in scans:
                if scan.is_image:
                    output.seek(scan.offset + 56)
                    output.write(struct.pack("<HH", 0, scan.slice))
                    output.seek(scan.offset + 98)
                    output.write(struct.pack("<H", 1))

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.temp.cleanup()

    def setUp(self):
        self.ui = MainWindow()
        self.ui.resize(1280, 820)
        self.ui.show()
        self.ui.open_path(self.path)
        self.wait_settled()
        self.ui.tabs.setCurrentIndex(3)
        self.wait_settled()

    def tearDown(self):
        if self.ui.busy:
            self.ui.cancel_job()
            self.wait_settled()
        self.ui.close()
        self.ui.pool.waitForDone(5000)
        self.ui.deleteLater()
        self.app.processEvents()

    def wait_settled(self):
        deadline = time.monotonic() + 20
        while self.ui.busy or self.ui.pool.activeThreadCount() or self.ui._preview_timer.isActive() or self.ui._pending_preview:
            self.app.processEvents()
            time.sleep(0.003)
            if time.monotonic() > deadline:
                self.fail("Reconstruction did not settle")
        self.app.processEvents()
        self.assertFalse(self.ui.last_error, self.ui.last_error)

    def choose_optimal(self):
        self.ui.coil_method.setCurrentIndex(self.ui.coil_method.findData("adaptive"))
        self.ui.rss.setChecked(True)
        self.wait_settled()

    def test_optimal_combines_all_channels_and_uses_matching_measured_noise(self):
        self.choose_optimal()
        self.assertEqual(self.ui.preview.metadata["combination_method"], "adaptive")
        self.assertTrue(self.ui.preview.metadata["noise_covariance_used"])
        self.assertGreater(self.ui.preview.metadata["noise_samples"], 32)
        self.assertEqual(self.ui.preview.channel_count, 4)
        self.assertIn("measured noise", self.ui.preview_view.stats.text())
        self.assertTrue(self.ui.select_channel(2))
        self.wait_settled()
        self.assertFalse(self.ui.rss.isChecked())
        self.assertEqual(self.ui.preview.channel_count, 1)
        self.assertIn("channel 2", self.ui.preview_view.image_title.text())

    def test_xy_controls_update_data_domain_and_have_separate_cache_entries(self):
        full = self.ui.preview.image.copy()
        original_key = self.ui._preview_key()
        self.ui.ifft_checks["y"].setChecked(False)
        self.wait_settled()
        self.assertEqual(self.ui.preview.metadata["ifft_axes"], ["x"])
        self.assertIn("Hybrid", self.ui.preview_view.image_title.text())
        self.assertNotEqual(original_key, self.ui._preview_key())
        self.ui.ifft_checks["x"].setChecked(False)
        self.wait_settled()
        np.testing.assert_allclose(self.ui.preview.image, np.abs(self.ui.preview.kspace), atol=1e-5)
        self.assertIn("K-space", self.ui.preview_view.image_title.text())
        self.ui.ifft_checks["x"].setChecked(True)
        self.ui.ifft_checks["y"].setChecked(True)
        self.wait_settled()
        np.testing.assert_array_equal(self.ui.preview.image, full)
        self.assertEqual(original_key, self.ui._preview_key())

    def test_missing_noise_is_reported_as_an_assumption(self):
        self.ui.open_path(self.no_noise)
        self.wait_settled()
        self.ui.tabs.setCurrentIndex(3)
        self.wait_settled()
        self.choose_optimal()
        self.assertFalse(self.ui.preview.metadata["noise_covariance_used"])
        self.assertIn("assumed noise", self.ui.preview_view.stats.text())
        self.assertIn("uncorrelated", self.ui.preview.metadata["noise_source"])

    def test_rapid_option_changes_keep_latest_axes_and_combination(self):
        self.ui.rss.setChecked(True)
        self.ui.coil_method.setCurrentIndex(1)
        self.ui.ifft_checks["x"].setChecked(False)
        self.ui.ifft_checks["y"].setChecked(False)
        self.ui.rss.setChecked(False)
        self.ui.ifft_checks["y"].setChecked(True)
        self.wait_settled()
        self.assertEqual(self.ui.preview.metadata["ifft_axes"], ["y"])
        self.assertEqual(self.ui.preview.metadata["combination_method"], "single")

    def test_separate_2d_slices_do_not_enable_z_transform(self):
        self.assertGreater(len({scan.slice for scan in self.ui.scans}), 1)
        self.assertFalse(self.ui.ifft_checks["z"].isEnabled())
        self.assertTrue(self.ui.ifft_checks["x"].isChecked())
        self.assertTrue(self.ui.ifft_checks["y"].isChecked())

    def test_z_gathers_all_partitions_and_places_navigation_below_images(self):
        self.ui.open_path(self.volume)
        self.wait_settled()
        self.ui.tabs.setCurrentIndex(3)
        self.wait_settled()
        self.assertTrue(self.ui.ifft_checks["z"].isEnabled())
        self.assertEqual({scan.partition for scan in self.ui.filtered}, {0})
        self.ui.ifft_checks["z"].setChecked(True)
        self.wait_settled()
        preview = self.ui.preview
        self.assertEqual(preview.image.ndim, 3)
        self.assertEqual(preview.image.shape[0], 2)
        self.assertEqual(preview.metadata["readouts"], 128)
        self.assertFalse(self.ui.counter_widgets["partition"].isEnabled())
        self.assertTrue(self.ui.slice_navigation.isHidden())
        view = self.ui.preview_view
        self.assertTrue(view.volume_navigation.isVisible())
        self.assertIs(view.volume_navigation.parentWidget(), view.image_panel)
        self.assertGreaterEqual(view.volume_navigation.y(), view.splitter.geometry().bottom())
        self.assertTrue(self.ui.step_slice(-1))
        self.assertEqual(view.plane_index, 0)
        self.assertIs(self.ui.preview, preview)  # No rereading or rebuilding.
        np.testing.assert_array_equal(view.image_view.image, preview.image[0])
        self.assertIn("raw partition 0", view.kspace_title.text())
        self.assertIn("Z plane 1", view.image_title.text())
        self.assertEqual(view.volume_spin.value(), 1)
        exported = Path(self.temp.name) / "volume.npz"
        self.ui.export_to("preview", exported)
        with np.load(exported, allow_pickle=False) as saved:
            np.testing.assert_array_equal(saved["image"], preview.image)
            np.testing.assert_array_equal(saved["kspace"], preview.kspace)
            metadata = json.loads(str(saved["metadata"]))
            self.assertEqual(metadata["ifft_axes"], ["x", "y", "z"])
            self.assertEqual(metadata["array_axes"], ["z", "y", "x"])
        wheel(view.volume_slider, angle=-120)
        self.assertEqual(view.plane_index, 1)
        wheel(view.image_view.ui.graphicsView.viewport(), angle=120)
        self.assertEqual(view.plane_index, 0)
        self.ui.ifft_checks["z"].setChecked(False)
        # Hide output-plane controls immediately while the last volume remains
        # visible during the asynchronous return to a raw-partition preview.
        self.assertTrue(view.volume_navigation.isHidden())
        self.wait_settled()
        self.assertEqual(self.ui.preview.image.ndim, 2)
        self.assertTrue(self.ui.counter_widgets["partition"].isEnabled())
        self.assertTrue(self.ui.slice_navigation.isVisible())
        self.assertTrue(view.volume_navigation.isHidden())

    def test_noise_mode_hides_fft_and_combination_controls(self):
        self.choose_optimal()
        self.ui.kind.setCurrentText("Noise")
        self.wait_settled()
        self.assertTrue(self.ui.reconstruction_controls.isHidden())
        self.assertEqual(self.ui.preview.metadata["display_kind"], "noise")
        self.assertEqual(self.ui.preview.metadata["reconstruction"], "none")


if __name__ == "__main__":
    unittest.main()
