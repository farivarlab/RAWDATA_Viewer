"""Predictable one-position hover-wheel navigation, independent of focus."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from siemens_explorer.qt_wheel import WheelSlider
from wheel_events import wheel


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class WheelSliderTests(unittest.TestCase):
    def setUp(self):
        self.slider = WheelSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10)
        self.slider.setValue(5)
        self.slider.show()
        APP.processEvents()
        self.slider.clearFocus()
        self.changed = []
        self.slider.valueChanged.connect(self.changed.append)

    def tearDown(self):
        self.slider.close()
        self.slider.deleteLater()
        QTest.qWait(1)

    def test_one_notch_steps_once_without_focus(self):
        self.assertFalse(self.slider.hasFocus())
        self.assertTrue(wheel(self.slider, angle=-120).isAccepted())
        self.assertEqual(self.slider.value(), 6)
        self.assertFalse(self.slider.hasFocus())
        wheel(self.slider, angle=240)
        self.assertEqual(self.slider.value(), 4)
        self.assertEqual(self.changed, [6, 4])

    def test_partial_angle_and_trackpad_gestures_accumulate(self):
        wheel(self.slider, angle=-60)
        self.assertEqual(self.slider.value(), 5)
        wheel(self.slider, angle=-60)
        self.assertEqual(self.slider.value(), 6)
        wheel(self.slider, pixels=-20)
        self.assertEqual(self.slider.value(), 6)
        wheel(self.slider, pixels=-20)
        self.assertEqual(self.slider.value(), 7)

    def test_limits_do_not_accumulate_outward_motion(self):
        self.slider.setValue(10)
        self.changed.clear()
        wheel(self.slider, angle=-60)
        wheel(self.slider, angle=120)
        self.assertEqual(self.changed, [9])
        wheel(self.slider, angle=2400)
        self.assertEqual(self.slider.value(), 0)

    def test_leave_and_disabled_state_discard_partial_steps(self):
        wheel(self.slider, angle=-60)
        APP.sendEvent(self.slider, QEvent(QEvent.Type.Leave))
        wheel(self.slider, angle=-60)
        self.assertEqual(self.slider.value(), 5)
        self.slider.setEnabled(False)
        wheel(self.slider, angle=-120)
        self.slider.setEnabled(True)
        wheel(self.slider, angle=-60)
        self.assertEqual(self.slider.value(), 5)

    def test_horizontal_only_wheel_does_not_change_value(self):
        self.assertFalse(wheel(self.slider, horizontal=120).isAccepted())
        self.assertEqual(self.slider.value(), 5)

    def test_new_trackpad_gesture_starts_with_no_partial_step(self):
        wheel(self.slider, pixels=-20, phase=Qt.ScrollPhase.ScrollBegin)
        wheel(self.slider, phase=Qt.ScrollPhase.ScrollEnd)
        wheel(self.slider, pixels=-20, phase=Qt.ScrollPhase.ScrollBegin)
        self.assertEqual(self.slider.value(), 5)
        wheel(self.slider, pixels=-20, phase=Qt.ScrollPhase.ScrollUpdate)
        self.assertEqual(self.slider.value(), 6)


if __name__ == "__main__":
    unittest.main()
