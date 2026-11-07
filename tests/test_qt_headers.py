"""Hierarchical paths, value search, source navigation, and bounded header trees."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from siemens_explorer.qt_headers import HeaderTree

APP = QApplication.instance() or QApplication([])


class HeaderTreeTests(unittest.TestCase):
    def setUp(self):
        self.tree = HeaderTree()
        self.tree.set_buffer('sSliceArray.asSlice[0].dThickness = 3.5\nsSliceArray.asSlice[1].dThickness = 4\nalTE[0] = 4500\n<ParamString."PatientPosition"> { "HFS" }')

    def tearDown(self):
        self.tree.deleteLater()
        APP.processEvents()

    def test_array_paths_form_expandable_hierarchy_and_emit_original_path(self):
        root = self.tree.topLevelItem(0)
        self.assertEqual(root.text(0), "sSliceArray")
        leaf = root.child(0).child(0).child(0)
        self.assertEqual(leaf.text(0), "dThickness")
        self.assertEqual(leaf.text(1), "3.5")
        selected = []
        self.tree.parameter_selected.connect(selected.append)
        self.tree.setCurrentItem(leaf)
        self.assertEqual(selected, ["sSliceArray.asSlice[0].dThickness"])

    def test_search_matches_values_and_retains_ancestors(self):
        self.tree.filter_parameters("3.5")
        self.assertFalse(self.tree.topLevelItem(0).isHidden())
        self.assertTrue(self.tree.topLevelItem(1).isHidden())
        self.tree.filter_parameters("")
        self.assertFalse(self.tree.topLevelItem(1).isHidden())

    def test_large_buffers_limit_tree_size_without_interpreting_code(self):
        self.tree.set_buffer('\n'.join(f'array[{i}] = {i}' for i in range(5000)))
        self.assertTrue(self.tree.limited)
        self.assertEqual(self.tree.topLevelItem(0).childCount(), self.tree.MAX_FIELDS)


if __name__ == "__main__":
    unittest.main()
