"""Observed-value slider and numeric-field interactions for raw-data counters."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from siemens_explorer.qt_counters import CounterSlider
from siemens_explorer.qt_theme import apply_theme
from wheel_events import wheel


APP = QApplication.instance() or QApplication([])


class CounterSliderTests(unittest.TestCase):
    def setUp(self):
        apply_theme(APP, "cockpit")
        self.counter = CounterSlider()
        self.counter.resize(230, 30)
        self.counter.show()
        self.changes = []
        self.counter.currentIndexChanged.connect(self.changes.append)
        self.populate([3, 9, 42])
        self.changes.clear()
        APP.processEvents()

    def tearDown(self):
        self.counter.close()
        self.counter.deleteLater()
        QTest.qWait(1)

    def populate(self, values):
        self.counter.clear()
        self.counter.addItem("All", None)
        for value in values:
            self.counter.addItem(str(value), value)

    def test_all_and_observed_values_share_one_selection(self):
        self.assertIsNone(self.counter.currentData())
        self.assertEqual(self.counter.currentText(), "All")
        self.assertEqual(self.counter.minimum_label.text(), "3")
        self.assertEqual(self.counter.maximum_label.text(), "42")
        self.assertEqual(self.counter.spin.text(), "All")
        self.counter.slider.setValue(2)
        self.assertEqual(self.counter.currentData(), 9)
        self.assertEqual(self.counter.spin.value(), 9)
        self.assertEqual(self.changes, [2])
        self.counter.spin.setValue(-1)
        self.assertIsNone(self.counter.currentData())
        self.assertEqual(self.counter.slider.value(), 0)
        self.assertEqual(self.changes, [2, 0])

    def test_numeric_arrows_step_through_actual_values_including_all(self):
        for expected in (3, 9, 42, 42):
            self.counter.spin.stepUp()
            self.assertEqual(self.counter.currentData(), expected)
        for expected in (9, 3, None, None):
            self.counter.spin.stepDown()
            self.assertEqual(self.counter.currentData(), expected)
        self.assertEqual(self.changes, [1, 2, 3, 2, 1, 0])

    def test_typed_value_commits_once_and_snaps_to_nearest_observed_value(self):
        self.counter.spin.setFocus()
        self.counter.spin.selectAll()
        QTest.keyClicks(self.counter.spin, "8")
        self.assertIsNone(self.counter.currentData())
        QTest.keyClick(self.counter.spin, Qt.Key.Key_Return)
        self.assertEqual(self.counter.currentData(), 9)
        self.assertEqual(self.counter.spin.value(), 9)
        self.assertEqual(self.changes, [2])
        self.counter.spin.selectAll()
        QTest.keyClicks(self.counter.spin, "41")
        QTest.keyClick(self.counter.spin, Qt.Key.Key_Return)
        self.assertEqual(self.counter.currentData(), 42)
        self.assertEqual(self.changes, [2, 3])

    def test_keyboard_and_mouse_slider_movement_map_to_observed_values(self):
        self.counter.slider.setFocus()
        QTest.keyClick(self.counter.slider, Qt.Key.Key_Right)
        self.assertEqual(self.counter.currentData(), 3)
        QTest.keyClick(self.counter.slider, Qt.Key.Key_End)
        self.assertEqual(self.counter.currentData(), 42)
        QTest.keyClick(self.counter.slider, Qt.Key.Key_Home)
        self.assertIsNone(self.counter.currentData())
        QTest.mouseClick(self.counter.slider, Qt.MouseButton.LeftButton, pos=self.counter.slider.rect().center())
        self.assertIsNotNone(self.counter.currentData())

    def test_empty_and_all_only_controls_disable_without_invalid_numeric_values(self):
        self.counter.clear()
        self.assertEqual(self.counter.currentIndex(), -1)
        self.assertFalse(self.counter.slider.isEnabled())
        self.assertFalse(self.counter.spin.isEnabled())
        self.counter.addItem("All", None)
        self.assertFalse(self.counter.slider.isEnabled())
        self.assertEqual(self.counter.spin.text(), "All")
        self.counter.addItem("0", 0)
        self.assertTrue(self.counter.slider.isEnabled())
        self.counter.slider.setValue(1)
        self.assertEqual(self.counter.currentData(), 0)
        self.assertEqual(self.counter.spin.value(), 0)

    def test_parent_loading_state_disables_inputs_and_preserves_selection(self):
        self.counter.setCurrentIndex(2)
        self.changes.clear()
        self.counter.setEnabled(False)
        self.assertFalse(self.counter.slider.isEnabled())
        self.assertFalse(self.counter.spin.isEnabled())
        self.counter.spin.stepUp()
        self.assertEqual(self.counter.currentData(), 9)
        self.assertEqual(self.changes, [])
        self.counter.setEnabled(True)
        self.assertTrue(self.counter.slider.isEnabled())
        self.counter.spin.stepUp()
        self.assertEqual(self.counter.currentData(), 42)

    def test_replacement_and_programmatic_updates_are_consistent_and_blockable(self):
        self.counter.blockSignals(True)
        self.populate([5, 20])
        self.counter.setCurrentText("20")
        self.counter.blockSignals(False)
        self.assertEqual(self.changes, [])
        self.assertEqual(self.counter.currentData(), 20)
        self.assertEqual(self.counter.findData(20), 2)
        self.assertEqual(self.counter.itemData(1), 5)
        self.assertEqual(self.counter.itemText(1), "5")
        self.counter.setCurrentIndex(999)
        self.assertEqual(self.changes, [])
        self.counter.setCurrentIndex(-99)
        self.assertIsNone(self.counter.currentData())
        self.assertEqual(self.changes, [0])
        self.counter.setCurrentIndex(0)
        self.assertEqual(self.changes, [0])

    def test_slider_hover_wheel_changes_observed_values_without_focus(self):
        self.counter.setCurrentIndex(2)
        self.counter.slider.clearFocus()
        self.counter.spin.clearFocus()
        self.assertTrue(wheel(self.counter.slider, angle=120).isAccepted())
        self.assertEqual(self.counter.currentData(), 3)
        wheel(self.counter.slider, angle=-240)
        self.assertEqual(self.counter.currentData(), 42)
        wheel(self.counter.slider, angle=360)
        self.assertIsNone(self.counter.currentData())

    def test_numeric_field_keeps_focus_only_wheel_behavior(self):
        self.counter.setCurrentIndex(2)
        self.counter.slider.clearFocus()
        self.counter.spin.clearFocus()
        self.assertFalse(wheel(self.counter.spin, angle=120).isAccepted())
        self.assertEqual(self.counter.currentData(), 9)
        self.counter.spin.setFocus()
        APP.processEvents()
        self.assertTrue(self.counter.spin.hasFocus())
        event = QWheelEvent(QPointF(self.counter.spin.rect().center()), QPointF(), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(self.counter.spin, event)
        self.assertEqual(self.counter.currentData(), 42)

    def test_bulk_replacement_handles_large_observed_sets_with_one_emission(self):
        self.counter.set_values(range(0, 20_000, 2), select_first=True)
        self.assertEqual(self.counter.count(), 10_001)
        self.assertEqual(self.counter.currentData(), 0)
        self.assertEqual(self.changes, [1])
        self.assertEqual(self.counter.findData(19_998), 10_000)
        self.assertEqual(self.counter.findData(19_999), -1)
        self.assertEqual(self.counter.spin.maximum(), 19_998)
        self.counter.slider.setValue(10_000)
        self.assertEqual(self.counter.currentData(), 19_998)
        self.assertEqual(self.changes, [1, 10_000])
        self.counter.set_values([7], select_first=True)
        self.assertEqual(self.counter.currentData(), 7)
        self.assertEqual(self.counter.spin.maximum(), 7)
        self.assertEqual(self.changes, [1, 10_000, 1])
        self.counter.set_values([])
        self.assertIsNone(self.counter.currentData())
        self.assertFalse(self.counter.slider.isEnabled())


if __name__ == "__main__":
    unittest.main()
