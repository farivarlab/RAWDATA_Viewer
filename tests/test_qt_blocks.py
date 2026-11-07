"""Bounded native block-map layout and selection against real Twix headers."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from siemens_explorer.qt_blocks import DataBlocksView
from siemens_explorer.qt_theme import apply_theme
from siemens_explorer.twix import TwixFile, create_demo_file


_APP = QApplication.instance() or QApplication([])


class DataBlocksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="twix-block-map-")
        cls.path = create_demo_file(Path(cls.temporary.name) / "blocks.dat")
        cls.raw = TwixFile(cls.path)
        cls.measurement = cls.raw.measurements[0]
        cls.scans = cls.raw.index_measurement(cls.measurement).scans

    @classmethod
    def tearDownClass(cls):
        cls.raw.close()
        cls.temporary.cleanup()

    def setUp(self):
        self.widget = DataBlocksView()
        self.widget.resize(1200, 700)
        self.widget.show()
        self.selected = []
        self.widget.block_selected.connect(lambda scan, channel: self.selected.append((scan, channel)))
        _APP.processEvents()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _APP.processEvents()

    def blocks(self, kind):
        return [item for item in self.widget.scene.items() if isinstance(item.data(0), dict) and item.data(0).get("kind") == kind]

    def test_vd_tiles_use_real_scan_and_channel_boundaries(self):
        # Merely giving metadata must not decode any channel samples.
        source = SimpleNamespace(size=self.raw.size, measurements=self.raw.measurements,
                                 read_signal=lambda *_: self.fail("Block layout read payload samples"))
        scan = self.scans[10]
        self.widget.set_data(source, self.measurement, [scan], selected_scan=scan, selected_channel=2)
        self.assertEqual(self.widget._visible_acquisitions, 1)
        self.assertEqual(self.widget._visible_channels, 4)
        tiles = self.blocks("channel")
        self.assertEqual(len(tiles), 4)
        channel = next(item for item in tiles if item.data(0)["channel_index"] == 2)
        facts = channel.data(0)
        expected = scan.offset + 192 + 2 * (32 + scan.samples * 8)
        self.assertEqual(facts["offset"], expected)
        self.assertEqual(facts["payload_offset"], expected + 32)
        self.assertEqual(facts["payload_length"], scan.samples * 8)
        self.assertEqual(facts["header_length"], 32)
        self.assertEqual(facts["scan_index"], scan.index)
        self.assertEqual(channel.pen().width(), 3)
        self.assertIn(f"{expected:,}", channel.toolTip())
        self.assertEqual(self.selected, [])

    def test_vb_channel_mdh_stride_is_128_bytes(self):
        scan = replace(self.scans[5], offset=4000, length=4 * (128 + 128 * 8))
        measurement = replace(self.measurement, format="VB")
        self.widget.set_data(self.raw, measurement, [scan])
        tile = next(item for item in self.blocks("channel") if item.data(0)["channel_index"] == 3)
        facts = tile.data(0)
        self.assertEqual(facts["offset"], 4000 + 3 * (128 + 128 * 8))
        self.assertEqual(facts["payload_offset"], facts["offset"] + 128)
        self.assertEqual(facts["header_length"], 128)
        self.assertEqual(self.blocks("scan_header"), [])

    def test_native_channel_click_emits_original_scan_and_ordinal(self):
        scan = self.scans[34]
        self.widget.set_data(self.raw, self.measurement, [scan])
        _APP.processEvents()
        item = next(block for block in self.blocks("channel") if block.data(0)["channel_index"] == 3)
        position = self.widget.view.mapFromScene(item.rect().center())
        QTest.mouseClick(self.widget.view.viewport(), Qt.MouseButton.LeftButton, pos=position)
        _APP.processEvents()
        self.assertEqual(self.selected, [(scan.index, 3)])
        self.assertEqual(item.pen().width(), 3)
        self.widget.set_selection(scan.index, 0)
        self.assertEqual(self.selected, [(scan.index, 3)])
        self.assertEqual(item.pen().width(), 1)

    def test_file_layout_has_measurement_and_header_byte_proportions(self):
        scan = self.scans[20]
        self.widget.set_data(self.raw, self.measurement, [scan], selected_scan=scan)
        self.widget.mode.setCurrentText("File layout")
        measurement = self.blocks("measurement")[0]
        self.assertAlmostEqual(measurement.rect().width(), 1100 * self.measurement.length / self.raw.size)
        header = self.blocks("protocol_header")[0]
        self.assertEqual(header.data(0)["offset"], self.measurement.offset)
        self.assertAlmostEqual(header.rect().width(), max(1, 1100 * self.measurement.header_length / self.measurement.length))
        self.assertEqual(len(self.blocks("channel")), 4)
        self.assertIn("payloads are not loaded", self.widget.summary.toolTip())

    def test_sync_and_variable_channel_counts_have_no_fabricated_tiles(self):
        image = self.scans[10]
        short = replace(image, index=900, channels=2)
        sync = replace(image, index=901, channels=0, samples=0, flags=("SYNCDATA",), is_image=False)
        self.widget.set_data(self.raw, self.measurement, [short, sync])
        self.assertEqual(len(self.blocks("channel")), 2)
        self.assertEqual(len(self.blocks("sync")), 1)
        self.assertEqual(self.widget._visible_acquisitions, 2)
        self.assertIn("No decoded channel payloads", self.blocks("sync")[0].toolTip())

    def test_scene_is_bounded_for_large_indices_and_many_channels(self):
        scan = self.scans[10]
        self.widget.set_data(self.raw, self.measurement, [replace(scan, index=i) for i in range(2000)])
        self.assertEqual(self.widget._visible_acquisitions, 256)
        self.assertEqual(self.widget._visible_channels, 256 * 4)
        self.assertIn("256 of 2,000", self.widget.summary.text())
        self.assertIn("Display limit reached", self.widget.summary.text())
        self.widget.set_data(self.raw, self.measurement, [replace(scan, channels=65535)])
        self.assertEqual(self.widget._visible_channels, 4096)
        self.assertEqual(len(self.blocks("channel")), 4096)
        self.assertLess(len(self.widget.scene.items()), 9000)
        self.assertIn("further channel blocks omitted", " ".join(item.text() for item in self.widget.scene.items() if hasattr(item, "text")))

    def test_file_strip_tracks_selected_channel_beyond_the_first_64(self):
        scan = replace(self.scans[10], channels=108, length=192 + 108 * (32 + self.scans[10].samples * 8))
        self.widget.set_data(self.raw, self.measurement, [scan], selected_channel=107)
        self.widget.mode.setCurrentText("File layout")
        self.assertEqual(len(self.blocks("channel")), 64)
        selected = self.widget._channel_items[(scan.index, 107)][0]
        self.assertEqual(selected.pen().width(), 3)
        self.assertEqual(selected.data(0)["offset"], scan.offset + 192 + 107 * (32 + scan.samples * 8))
        self.widget.set_selection(scan.index, 0)
        self.assertEqual(len(self.blocks("channel")), 64)
        self.assertEqual(self.widget._channel_items[(scan.index, 0)][0].pen().width(), 3)

    def test_file_strip_never_invents_channels_for_sync_or_empty_readouts(self):
        image = self.scans[10]
        for scan in (replace(image, flags=("SYNCDATA",)), replace(image, samples=0)):
            self.widget.set_data(self.raw, self.measurement, [scan])
            for mode in (0, 1):
                self.widget.mode.setCurrentIndex(mode)
                self.assertEqual(self.blocks("channel"), [])
                self.assertEqual(self.widget._channel_items, {})

    def test_clearing_a_zoomed_scrolled_map_keeps_the_empty_state_visible(self):
        scan = self.scans[10]
        self.widget.set_data(self.raw, self.measurement, [replace(scan, index=i) for i in range(300)])
        self.widget.view.scale(5, 5)
        self.widget.view.verticalScrollBar().setValue(self.widget.view.verticalScrollBar().maximum())
        self.widget.clear()
        _APP.processEvents()
        text = next(item for item in self.widget.scene.items() if hasattr(item, "text"))
        self.assertTrue(self.widget.view.viewport().rect().contains(self.widget.view.mapFromScene(text.pos())))
        self.assertEqual(self.widget.view.transform().m11(), 1)

    def test_empty_missing_and_zero_length_inputs_remain_usable(self):
        self.widget.set_data(self.raw, self.measurement, [])
        self.assertEqual(self.blocks("channel"), [])
        self.assertIn("0 of 0", self.widget.summary.text())
        zero = replace(self.measurement, offset=0, length=0, header_length=0, data_offset=0)
        self.widget.set_data(SimpleNamespace(size=0, measurements=[zero]), zero, [])
        self.widget.mode.setCurrentText("File layout")
        self.assertTrue(self.widget.scene.sceneRect().isValid())
        self.widget.set_data(None, None, [])
        self.assertEqual(self.widget._channel_items, {})
        self.assertEqual("No dataset", self.widget.summary.text())

    def test_diagram_labels_remain_high_contrast_in_both_application_themes(self):
        self.widget.set_data(self.raw, self.measurement, [self.scans[5]])

        def luminance(color):
            channels = [color.redF(), color.greenF(), color.blueF()]
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return sum(weight * channel for weight, channel in zip((0.2126, 0.7152, 0.0722), linear))

        try:
            for theme in ("light", "dark", "cockpit", "modern-light"):
                apply_theme(_APP, theme)
                self.widget.set_theme(theme)
                _APP.processEvents()
                background = luminance(self.widget.scene.backgroundBrush().color())
                texts = [item for item in self.widget.scene.items() if hasattr(item, "text") and item.parentItem() is None]
                self.assertGreater(len(texts), 0)
                for item in texts:
                    foreground = luminance(item.brush().color())
                    contrast = (max(background, foreground) + 0.05) / (min(background, foreground) + 0.05)
                    self.assertGreaterEqual(contrast, 4.5)
                for item in self.blocks("channel"):
                    background = luminance(item.brush().color())
                    for label in item.childItems():
                        foreground = luminance(label.brush().color())
                        contrast = (max(background, foreground) + 0.05) / (min(background, foreground) + 0.05)
                        self.assertGreaterEqual(contrast, 4.5)
        finally:
            apply_theme(_APP, "light")

    def test_cockpit_theme_preserves_selection_zoom_scroll_and_graphics_items(self):
        scans = self.scans[5:65]
        selected_scan = scans[8]
        self.widget.set_data(self.raw, self.measurement, scans, selected_scan=selected_scan, selected_channel=2)
        self.widget.view.scale(1.8, 1.8)
        self.widget.view.horizontalScrollBar().setValue(50)
        self.widget.view.verticalScrollBar().setValue(400)
        _APP.processEvents()
        items = list(self.widget.scene.items())
        transform = self.widget.view.transform()
        scroll = (self.widget.view.horizontalScrollBar().value(), self.widget.view.verticalScrollBar().value())
        raw = self.widget.raw
        stored_scans = self.widget.scans
        selected = self.widget._channel_items[(selected_scan.index, 2)][0]
        for theme, canvas, outline in (("cockpit", "#161616", "#d4d4d4"),
                                       ("modern-light", "#f9f9f9", "#3f3f3f"),
                                       ("light", "#f5f6f7", "#2369a0")):
            self.widget.set_theme(theme)
            _APP.processEvents()
            self.assertEqual(self.widget.scene.backgroundBrush().color().name(), canvas)
            self.assertEqual(selected.pen().color().name(), outline)
            self.assertEqual(selected.pen().width(), 3)
            self.assertEqual(list(self.widget.scene.items()), items)
            self.assertEqual(self.widget.view.transform(), transform)
            self.assertEqual((self.widget.view.horizontalScrollBar().value(), self.widget.view.verticalScrollBar().value()), scroll)
            self.assertIs(self.widget.raw, raw)
            self.assertIs(self.widget.scans, stored_scans)
            self.assertEqual((self.widget.selected_scan, self.widget.selected_channel), (selected_scan.index, 2))

    def test_cockpit_colors_apply_to_new_and_cleared_maps(self):
        self.widget.set_theme("cockpit")
        self.widget.set_data(self.raw, self.measurement, self.scans[5:7])
        self.assertEqual(self.widget.scene.backgroundBrush().color().name(), "#161616")
        channel = self.blocks("channel")[0]
        self.assertEqual(channel.brush().color().name(), "#2f2f2f")
        self.widget.clear()
        text = next(item for item in self.widget.scene.items() if hasattr(item, "text"))
        self.assertEqual(text.brush().color().name(), "#dcdcdc")


if __name__ == "__main__":
    unittest.main()
