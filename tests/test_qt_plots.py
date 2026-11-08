"""Numerical display-transform and reset checks for native Qt plot widgets."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from siemens_explorer.analysis import Preview
from siemens_explorer.qt_plots import PreviewView, SignalView
from siemens_explorer.qt_theme import apply_theme, theme_colors


_APP = QApplication.instance() or QApplication([])


class SignalViewTests(unittest.TestCase):
    def setUp(self):
        self.view = SignalView()
        self.signal = np.array([1 + 2j, -3 + 4j, 5 - 1j, -2 - 2j], dtype=np.complex64)
        self.scan = SimpleNamespace(index=7, line=3, slice=1, echo=2, center_column=2)

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        _APP.processEvents()

    def test_complex_components_and_original_signal_are_preserved(self):
        self.view.set_signal(self.signal, self.scan, channel_index=2, channel_id=42)
        self.view.mode.setCurrentText("Real + imaginary")
        curves = self.view.plot.listDataItems()
        self.assertEqual(len(curves), 2)
        np.testing.assert_array_equal(curves[0].getData()[1], self.signal.real)
        np.testing.assert_array_equal(curves[1].getData()[1], self.signal.imag)
        self.signal[:] = 0
        self.assertTrue(np.any(self.view.signal))
        self.assertIn("physical ID 42", self.view.info.text())

    def test_magnitude_phase_and_fft_are_actual_transforms(self):
        self.view.set_signal(self.signal, self.scan)
        for mode, expected in (
            ("Magnitude", np.abs(self.signal)),
            ("Phase", np.angle(self.signal)),
            ("FFT magnitude", np.abs(np.fft.fftshift(np.fft.fft(self.signal)))),
        ):
            self.view.mode.setCurrentText(mode)
            curves = self.view.plot.listDataItems()
            self.assertEqual(len(curves), 1)
            np.testing.assert_allclose(curves[0].getData()[1], expected)
        np.testing.assert_array_equal(curves[0].getData()[0], [-2, -1, 0, 1])

    def test_clear_removes_data_and_rejects_empty_export(self):
        self.view.set_signal(self.signal)
        self.view.clear("No matching acquisitions")
        self.assertIsNone(self.view.signal)
        self.assertFalse(self.view.mode.isEnabled())
        self.assertEqual(len(self.view.plot.listDataItems()), 0)
        self.assertEqual(self.view.info.text(), "No matching acquisitions")
        with self.assertRaises(ValueError):
            self.view.export_image("unused.png")

    def test_signal_plot_exports_a_native_png(self):
        self.view.set_signal(self.signal, self.scan)
        self.view.resize(800, 420)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "readout.png"
            self.view.export_image(path)
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


class PreviewViewTests(unittest.TestCase):
    def setUp(self):
        self.view = PreviewView()
        kspace = np.array([[1 + 2j, -3 + 4j, 5j], [2 - 2j, 0j, 3 + 1j]], dtype=np.complex64)
        self.preview = Preview(
            kspace=kspace, image=np.abs(kspace).astype(np.float32),
            coverage=np.array([[True, False, True], [True, True, False]]),
            lines=np.arange(2), warnings=["Trajectory not verified."], channel_count=4,
        )

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        _APP.processEvents()

    @staticmethod
    def wheel(target, angle_y=0, pixel_y=0, angle_x=0, modifiers=Qt.KeyboardModifier.NoModifier):
        position = target.rect().center()
        event = QWheelEvent(
            QPointF(position), QPointF(target.mapToGlobal(position)),
            QPoint(0, pixel_y), QPoint(angle_x, angle_y), Qt.MouseButton.NoButton,
            modifiers, Qt.ScrollPhase.NoScrollPhase, False,
        )
        event.setAccepted(False)
        QApplication.sendEvent(target, event)
        return event

    def test_image_shape_orientation_and_kspace_modes(self):
        self.view.set_preview(self.preview)
        self.assertEqual(self.view.image_view.imageItem.axisOrder, "row-major")
        np.testing.assert_array_equal(self.view.image_view.image, self.preview.image)
        bounds = self.view.image_view.imageItem.boundingRect()
        self.assertEqual((bounds.width(), bounds.height()), (3, 2))
        self.assertTrue(self.view.image_view.getView().getViewBox().state["yInverted"])
        for mode, expected in (
            ("Log magnitude", np.log1p(np.abs(self.preview.kspace))),
            ("Magnitude", np.abs(self.preview.kspace)),
            ("Phase", np.angle(self.preview.kspace)),
        ):
            self.view.mode.setCurrentText(mode)
            np.testing.assert_allclose(self.view.kspace_view.image, expected)
        self.assertIn("coil RSS", self.view.image_title.text())
        self.assertIn("66.7%", self.view.stats.text())
        self.assertIn("Trajectory not verified", self.view.notes.toPlainText())

    def test_theme_changes_update_plot_surfaces_and_preserve_original_arrays(self):
        self.view.set_preview(self.preview)
        signal_view = SignalView()
        signal = np.array([1 + 1j, 2 - 3j], dtype=np.complex64)
        signal_view.set_signal(signal)
        try:
            for theme in ("dark", "light", "cockpit", "modern-light"):
                apply_theme(_APP, theme)
                colors = theme_colors(theme)
                self.view.set_theme(colors)
                signal_view.set_theme(colors)
                _APP.processEvents()
                np.testing.assert_array_equal(self.view.image_view.image, self.preview.image)
                np.testing.assert_array_equal(signal_view.signal, signal)
                color = signal_view.plot.backgroundBrush().color().name()
                self.assertEqual(color, colors["plot_background"])
                self.assertEqual(self.view.image_view.getView().getAxis("left").textPen().color().name(), colors["plot_foreground"])
        finally:
            apply_theme(_APP, "light")
            signal_view.close()
            signal_view.deleteLater()

    def test_clear_resets_arrays_and_controls(self):
        self.view.set_preview(self.preview)
        self.view.clear("Filters changed")
        self.assertIsNone(self.view.preview)
        self.assertIsNone(self.view.kspace_view.image)
        self.assertIsNone(self.view.image_view.image)
        self.assertFalse(self.view.mode.isEnabled())
        self.assertEqual(self.view.stats.text(), "Filters changed")

    def test_notes_start_hidden_and_preview_updates_respect_the_toggle(self):
        self.view.resize(1000, 700)
        self.view.show()
        _APP.processEvents()
        self.assertTrue(self.view.notes.isHidden())
        self.view.set_preview(self.preview)
        self.assertTrue(self.view.notes.isHidden())
        self.view.set_notes_visible(True)
        _APP.processEvents()
        self.assertTrue(self.view.notes.isVisible())
        self.assertIn("Trajectory not verified", self.view.notes.toPlainText())
        self.view.clear("Changed filters")
        self.view.set_preview(self.preview)
        self.assertTrue(self.view.notes.isVisible())
        self.view.set_notes_visible(False)
        self.view.set_preview(self.preview)
        self.assertTrue(self.view.notes.isHidden())

    def test_notes_resize_and_toggling_reclaims_the_image_space(self):
        self.view.resize(1000, 700)
        self.view.show()
        _APP.processEvents()
        initial_image_height = self.view.splitter.height()
        self.view.set_notes_visible(True)
        _APP.processEvents()
        self.view.details_splitter.setSizes([350, 220])
        _APP.processEvents()
        chosen_height = self.view.notes.height()
        self.assertGreater(chosen_height, 105)
        self.assertLess(self.view.splitter.height(), initial_image_height)
        self.view.set_notes_visible(False)
        _APP.processEvents()
        self.assertEqual(self.view.splitter.height(), initial_image_height)
        self.view.set_notes_visible(True)
        _APP.processEvents()
        self.assertAlmostEqual(self.view.notes.height(), chosen_height, delta=2)

    def test_canvas_wheel_steps_slices_with_direction_and_multiple_notches(self):
        self.view.set_preview(self.preview)
        steps = []
        self.view.slice_step.connect(steps.append)
        self.view.set_slice_navigation_enabled(True)
        for target, delta in (
            (self.view.kspace_view.ui.graphicsView.viewport(), -120),
            (self.view.image_view.ui.graphicsView.viewport(), 120),
            (self.view.image_view.ui.graphicsView.viewport(), -360),
        ):
            event = self.wheel(target, angle_y=delta)
            self.assertTrue(event.isAccepted())
        self.assertEqual(steps, [1, -1, 3])
        self.assertIn("Ctrl+wheel: zoom", self.view.image_view.ui.graphicsView.toolTip())

    def test_high_resolution_wheel_accumulates_and_horizontal_does_not_step(self):
        self.view.set_preview(self.preview)
        steps = []
        self.view.slice_step.connect(steps.append)
        self.view.set_slice_navigation_enabled(True)
        target = self.view.image_view.ui.graphicsView.viewport()
        for delta in (-30, -30, -30):
            self.assertTrue(self.wheel(target, angle_y=delta).isAccepted())
        self.assertEqual(steps, [])
        self.wheel(target, angle_y=-30)
        self.assertEqual(steps, [1])
        for delta in (-10, -10, -20, 80):
            self.wheel(target, pixel_y=delta)
        self.assertEqual(steps, [1, 1, -2])
        self.wheel(target, angle_x=120)
        self.assertEqual(steps, [1, 1, -2])

    def test_ctrl_wheel_and_histograms_keep_zoom_without_slice_signals(self):
        self.view.set_preview(self.preview)
        self.view.resize(1000, 600)
        self.view.show()
        _APP.processEvents()
        steps = []
        self.view.slice_step.connect(steps.append)
        self.view.set_slice_navigation_enabled(True)
        target = self.view.image_view.ui.graphicsView.viewport()
        before = np.array(self.view.image_view.getView().getViewBox().viewRange())
        self.wheel(target, angle_y=120, modifiers=Qt.KeyboardModifier.ControlModifier)
        after = np.array(self.view.image_view.getView().getViewBox().viewRange())
        self.assertEqual(steps, [])
        self.assertFalse(np.allclose(before, after))
        for image_view in (self.view.kspace_view, self.view.image_view):
            self.wheel(image_view.getHistogramWidget().viewport(), angle_y=-120)
        self.assertEqual(steps, [])

    def test_slice_wheel_is_opt_in_and_disabling_resets_partial_gesture(self):
        self.view.set_preview(self.preview)
        steps = []
        self.view.slice_step.connect(steps.append)
        target = self.view.image_view.ui.graphicsView.viewport()
        self.wheel(target, angle_y=-120)
        self.assertEqual(steps, [])
        self.view.set_slice_navigation_enabled(True)
        self.wheel(target, angle_y=-60)
        self.view.set_slice_navigation_enabled(False)
        self.view.set_slice_navigation_enabled(True)
        self.wheel(target, angle_y=-60)
        self.assertEqual(steps, [])
        self.wheel(target, angle_y=-60)
        self.assertEqual(steps, [1])

    def test_slice_update_preserves_levels_and_zoom_for_matching_matrix(self):
        self.view.set_preview(self.preview)
        self.view.resize(1000, 600)
        self.view.show()
        _APP.processEvents()
        for i, image_view in enumerate((self.view.kspace_view, self.view.image_view)):
            image_view.setLevels(0.5 + i, 2.5 + i)
            image_view.getView().getViewBox().setRange(
                xRange=(0.4, 1.9), yRange=(0.2, 1.1), padding=0, disableAutoRange=True,
            )
        # User wheel/pan gestures settle their axis-layout updates before the
        # worker completes a subsequent slice preview.
        _APP.processEvents()
        levels = [v.getLevels() for v in (self.view.kspace_view, self.view.image_view)]
        ranges = [v.getView().getViewBox().viewRange() for v in (self.view.kspace_view, self.view.image_view)]
        next_slice = Preview(
            kspace=self.preview.kspace * 100, image=self.preview.image * 100,
            coverage=self.preview.coverage, lines=self.preview.lines,
            warnings=self.preview.warnings, channel_count=4,
        )
        self.view.set_preview(next_slice, preserve_view=True)
        _APP.processEvents()
        for i, image_view in enumerate((self.view.kspace_view, self.view.image_view)):
            np.testing.assert_allclose(image_view.getLevels(), levels[i])
            np.testing.assert_allclose(image_view.getView().getViewBox().viewRange(), ranges[i])
        np.testing.assert_array_equal(self.view.image_view.image, next_slice.image)

    def test_preserve_view_falls_back_to_fit_when_matrix_shape_changes(self):
        self.view.set_preview(self.preview)
        self.view.image_view.setLevels(123, 456)
        larger = np.arange(20, dtype=np.float32).reshape(4, 5) + 100
        next_preview = Preview(
            kspace=larger.astype(np.complex64), image=larger,
            coverage=np.ones_like(larger, dtype=bool), lines=np.arange(4),
        )
        self.view.set_preview(next_preview, preserve_view=True)
        np.testing.assert_allclose(self.view.image_view.getLevels(), (100, 119))

    def test_preview_image_export_writes_png_and_bad_shapes_are_rejected(self):
        self.view.set_preview(self.preview)
        self.view.resize(1000, 600)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preview.png"
            self.view.export_images(path)
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        bad = Preview(
            kspace=np.ones((2, 2)), image=np.ones((2, 3)), coverage=np.ones((2, 2)), lines=np.arange(2),
        )
        with self.assertRaises(ValueError):
            self.view.set_preview(bad)


if __name__ == "__main__":
    unittest.main()
