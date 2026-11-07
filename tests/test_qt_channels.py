"""Compact channel-slider interaction and silent application synchronization."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from siemens_explorer.qt_channels import ChannelSelector
from wheel_events import wheel


_APP = QApplication.instance() or QApplication([])


class ChannelSelectorTests(unittest.TestCase):
    def setUp(self):
        self.selector = ChannelSelector()
        self.selector.resize(300, 36)
        self.selector.show()
        self.selected = []
        self.selector.channel_selected.connect(self.selected.append)
        _APP.processEvents()

    def tearDown(self):
        self.selector.close()
        self.selector.deleteLater()
        _APP.processEvents()

    def test_physical_ids_and_ordinal_positions_are_distinct_and_silent(self):
        self.selector.set_channels([7, 12, 42], selected=1)
        self.assertEqual(self.selector.channel_ids, [7, 12, 42])
        self.assertEqual(self.selector.slider.value(), 1)
        self.assertEqual(self.selector.spin.value(), 1)
        self.assertEqual(self.selector.minimum_label.text(), "0")
        self.assertEqual(self.selector.maximum_label.text(), "2")
        self.assertIn("Physical receiver ID 12", self.selector.slider.toolTip())
        self.assertIn("3 channels", self.selector.toolTip())
        self.assertEqual(self.selected, [])
        self.selector.set_current_channel(2)
        self.assertEqual(self.selector.slider.value(), 2)
        self.assertEqual(self.selector.spin.value(), 2)
        self.assertIn("Physical receiver ID 42", self.selector.spin.toolTip())
        self.assertEqual(self.selected, [])

    def test_native_thumb_click_activates_the_current_channel_once(self):
        self.selector.set_channels([7, 12, 42], selected=1)
        slider = self.selector.slider
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        point = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                              QStyle.SubControl.SC_SliderHandle, slider).center()
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(self.selected, [1])

    def test_direct_channel_jump_handles_large_arrays_and_clamps(self):
        self.selector.set_channels(list(range(1000, 1108)), selected=9)
        self.assertEqual(self.selector.spin.value(), 9)
        self.assertEqual(self.selector.spin.maximum(), 107)
        self.selector.spin.setValue(104)
        self.assertEqual(self.selector.slider.value(), 104)
        self.assertEqual(self.selected, [104])
        self.assertIn("Physical receiver ID 1104", self.selector.toolTip())
        self.selector.spin.setValue(1000)
        self.assertEqual(self.selector.slider.value(), 107)
        self.assertEqual(self.selected, [104, 107])

    def test_hover_wheel_changes_selection_and_numeric_input_without_focus(self):
        self.selector.set_channels(list(range(108)), selected=18)
        self.selector.slider.clearFocus()
        self.selector.spin.clearFocus()
        self.assertTrue(wheel(self.selector.slider, angle=-120).isAccepted())
        self.assertEqual(self.selector.slider.value(), 19)
        self.assertEqual(self.selector.spin.value(), 19)
        self.assertEqual(self.selected, [19])
        wheel(self.selector.slider, angle=240)
        self.assertEqual(self.selector.slider.value(), 17)
        self.assertEqual(self.selected, [19, 17])

    def test_partial_wheel_and_trackpad_motion_preserve_remainders(self):
        self.selector.set_channels(list(range(108)), selected=18)
        target = self.selector.slider
        wheel(target, angle=-150)
        self.selector.set_current_channel(19)
        wheel(target, angle=-90)
        self.assertEqual(self.selected, [19, 20])
        wheel(target, pixels=-20)
        self.assertEqual(self.selector.slider.value(), 20)
        wheel(target, pixels=-20)
        self.assertEqual(self.selector.slider.value(), 21)
        self.assertFalse(wheel(target, horizontal=120).isAccepted())

    def test_wheel_clamps_and_loading_or_replacement_discards_partial_steps(self):
        self.selector.set_channels([7, 12, 42], selected=2)
        target = self.selector.slider
        wheel(target, angle=-60)
        wheel(target, angle=120)
        self.assertEqual(self.selector.slider.value(), 1)
        wheel(target, angle=-60)
        self.selector.set_navigation_enabled(False)
        wheel(target, angle=-120)
        self.assertEqual(self.selector.slider.value(), 1)
        self.selector.set_navigation_enabled(True)
        wheel(target, angle=-60)
        self.assertEqual(self.selector.slider.value(), 1)
        self.selector.set_channels([99], selected=0)
        wheel(target, angle=-120)
        self.assertEqual(self.selector.slider.value(), 0)

    def test_typing_a_channel_waits_for_commit_before_selecting(self):
        self.selector.set_channels(list(range(108)))
        self.selector.spin.setFocus()
        self.selector.spin.selectAll()
        QTest.keyClicks(self.selector.spin, "104")
        self.assertEqual(self.selected, [])
        QTest.keyClick(self.selector.spin, Qt.Key.Key_Return)
        self.assertEqual(self.selector.slider.value(), 104)
        self.assertEqual(self.selected, [104])

    def test_native_arrow_keys_and_navigation_emit_exact_ordinal(self):
        self.selector.set_channels([7, 12, 42])
        self.selector.slider.setFocus()
        QTest.keyClick(self.selector.slider, Qt.Key.Key_Right)
        self.assertEqual(self.selected, [1])
        self.selector.step_channel(1)
        self.assertEqual(self.selected, [1, 2])
        self.selector.step_channel(-1)
        self.assertEqual(self.selected, [1, 2, 1])

    def test_step_clamps_at_both_boundaries_without_extra_signals(self):
        self.selector.set_channels([7, 12, 42])
        self.assertFalse(self.selector.step_channel(-1))
        self.assertTrue(self.selector.step_channel(20))
        self.assertEqual(self.selected, [2])
        self.assertFalse(self.selector.step_channel(1))
        self.assertTrue(self.selector.step_channel(-20))
        self.assertEqual(self.selected, [2, 0])
        self.assertEqual(self.selector.spin.value(), 0)

    def test_missing_physical_id_remains_selectable_and_replacement_clamps(self):
        self.selector.set_channels([7, None, 42], selected=20)
        self.assertEqual(self.selector.slider.value(), 2)
        self.selector.set_current_channel(1)
        self.assertIn("ID unavailable", self.selector.toolTip())
        self.assertTrue(self.selector.slider.isEnabled())
        self.selector.set_channels([99], selected=2)
        self.assertEqual(self.selector.slider.value(), 0)
        self.assertEqual(self.selected, [])
        self.assertEqual(self.selector.spin.maximum(), 0)

    def test_clear_and_loading_disable_interaction(self):
        self.selector.set_channels([7, 12, 42])
        self.selector.set_navigation_enabled(False)
        self.assertFalse(self.selector.slider.isEnabled())
        self.assertFalse(self.selector.spin.isEnabled())
        self.assertFalse(self.selector.step_channel(1))
        self.selector.set_navigation_enabled(True)
        self.assertTrue(self.selector.slider.isEnabled())
        self.assertTrue(self.selector.spin.isEnabled())
        self.selector.clear()
        self.assertEqual(self.selector.channel_ids, [])
        self.assertIn("No receive channels", self.selector.toolTip())
        self.assertFalse(self.selector.slider.isEnabled())
        self.assertEqual(self.selected, [])


if __name__ == "__main__":
    unittest.main()
