"""Native channel-list interaction and silent application synchronization."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from siemens_explorer.qt_channels import ChannelBrowser


_APP = QApplication.instance() or QApplication([])


class ChannelBrowserTests(unittest.TestCase):
    def setUp(self):
        self.browser = ChannelBrowser()
        self.browser.resize(300, 240)
        self.browser.show()
        self.selected = []
        self.browser.channel_selected.connect(self.selected.append)
        _APP.processEvents()

    def tearDown(self):
        self.browser.close()
        self.browser.deleteLater()
        _APP.processEvents()

    def test_physical_ids_and_ordinal_positions_are_distinct_and_silent(self):
        self.browser.set_channels([7, 12, 42], selected=1)
        self.assertEqual(self.browser.channel_ids, [7, 12, 42])
        self.assertEqual(self.browser.list.currentRow(), 1)
        self.assertEqual(self.browser.list.item(0).text(), "Channel 0  ·  ID 7")
        self.assertEqual(self.browser.list.item(2).text(), "Channel 2  ·  ID 42")
        self.assertEqual(self.browser.count.text(), "2 / 3 channels")
        self.assertEqual(self.selected, [])
        self.browser.set_current_channel(2)
        self.assertEqual(self.browser.list.currentRow(), 2)
        self.assertEqual(self.selected, [])
        self.assertFalse(self.browser.next_button.isEnabled())
        self.assertTrue(self.browser.prev_button.isEnabled())

    def test_native_mouse_click_selects_one_channel_and_emits_once(self):
        self.browser.set_channels([7, 12, 42])
        _APP.processEvents()
        item = self.browser.list.item(2)
        QTest.mouseClick(self.browser.list.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.browser.list.visualItemRect(item).center())
        _APP.processEvents()
        self.assertEqual(self.browser.list.currentRow(), 2)
        self.assertEqual(self.selected, [2])
        self.assertEqual(self.browser.list.selectedItems(), [item])

    def test_native_arrow_keys_and_step_buttons_emit_exact_ordinal(self):
        self.browser.set_channels([7, 12, 42])
        self.browser.list.setFocus()
        QTest.keyClick(self.browser.list, Qt.Key.Key_Down)
        self.assertEqual(self.selected, [1])
        self.browser.next_button.click()
        self.assertEqual(self.selected, [1, 2])
        self.browser.prev_button.click()
        self.assertEqual(self.selected, [1, 2, 1])
        self.assertEqual(self.browser.count.text(), "2 / 3 channels")

    def test_step_clamps_at_both_boundaries_without_extra_signals(self):
        self.browser.set_channels([7, 12, 42])
        self.assertFalse(self.browser.step_channel(-1))
        self.assertTrue(self.browser.step_channel(20))
        self.assertEqual(self.selected, [2])
        self.assertFalse(self.browser.step_channel(1))
        self.assertTrue(self.browser.step_channel(-20))
        self.assertEqual(self.selected, [2, 0])
        self.assertFalse(self.browser.prev_button.isEnabled())

    def test_missing_physical_id_remains_selectable_and_replacement_clamps(self):
        self.browser.set_channels([7, None, 42], selected=20)
        self.assertEqual(self.browser.list.currentRow(), 2)
        self.assertIn("ID unavailable", self.browser.list.item(1).text())
        self.browser.set_current_channel(1)
        self.assertEqual(self.browser.list.currentRow(), 1)
        self.assertTrue(self.browser.list.item(1).flags() & Qt.ItemFlag.ItemIsEnabled)
        self.browser.set_channels([99], selected=2)
        self.assertEqual(self.browser.list.currentRow(), 0)
        self.assertEqual(self.selected, [])
        self.assertFalse(self.browser.next_button.isEnabled())
        self.assertFalse(self.browser.prev_button.isEnabled())

    def test_clear_and_loading_disable_interaction(self):
        self.browser.set_channels([7, 12, 42])
        self.browser.set_navigation_enabled(False)
        self.assertFalse(self.browser.list.isEnabled())
        self.assertFalse(self.browser.prev_button.isEnabled())
        self.assertFalse(self.browser.next_button.isEnabled())
        self.assertFalse(self.browser.step_channel(1))
        self.browser.set_navigation_enabled(True)
        self.assertTrue(self.browser.list.isEnabled())
        self.assertTrue(self.browser.next_button.isEnabled())
        self.browser.clear()
        self.assertEqual(self.browser.channel_ids, [])
        self.assertEqual(self.browser.list.count(), 0)
        self.assertEqual(self.browser.count.text(), "No channels")
        self.assertFalse(self.browser.list.isEnabled())
        self.assertEqual(self.selected, [])


if __name__ == "__main__":
    unittest.main()
