"""Numerical and grouping checks using independent, small synthetic readouts."""

from dataclasses import dataclass, replace
import unittest

import numpy as np

from siemens_explorer.analysis import build_cartesian_preview, scans_to_frame, select_scans


@dataclass
class Scan:
    index: int
    line: int
    samples: int = 4
    channels: int = 2
    partition: int = 0
    slice: int = 0
    echo: int = 0
    repetition: int = 0
    set: int = 0
    average: int = 0
    phase: int = 0
    segment: int = 0
    ida: int = 0
    idb: int = 0
    idc: int = 0
    idd: int = 0
    ide: int = 0
    flags: tuple = ()
    is_image: bool = True
    center_column: int = 2
    center_line: int = 2
    reflected: bool = False
    timestamp_ms: float = 0
    scan_counter: int = 0
    offset: int = 0
    length: int = 0


class FakeTwix:
    def __init__(self, signals):
        self.signals = signals

    def read_signal(self, measurement, scan, channel_index):
        return self.signals[scan.index] * (channel_index + 1)

    def channel_ids(self, measurement, scan):
        return list(range(scan.channels))


class AnalysisTests(unittest.TestCase):
    def test_inverse_fft_recovers_known_image(self):
        expected = np.array([[0, 1, 0, 0], [1, 2, 1, 0], [0, 1, 0, 0], [0, 0, 0, 0]], float)
        kspace = np.fft.fftshift(np.fft.fft2(np.fft.ifftshift(expected)))
        scans = [Scan(i, i) for i in range(4)]
        preview = build_cartesian_preview(FakeTwix(dict(enumerate(kspace))), None, scans)
        np.testing.assert_allclose(preview.image, expected, atol=1e-6)
        np.testing.assert_allclose(preview.kspace, kspace, atol=1e-6)
        self.assertTrue(preview.coverage.all())

    def test_missing_rows_zero_filled_and_reported(self):
        scan = Scan(0, 2)
        preview = build_cartesian_preview(FakeTwix({0: np.ones(4)}), None, [scan])
        self.assertEqual(preview.kspace.shape, (4, 4))
        self.assertEqual(preview.coverage.sum(), 4)
        self.assertEqual(preview.metadata["acquired_fraction"], 0.25)
        self.assertTrue(any("75.0%" in message for message in preview.warnings))

    def test_reflection_is_reversed(self):
        scans = [Scan(0, 2, reflected=True)]
        preview = build_cartesian_preview(FakeTwix({0: np.arange(4)}), None, scans)
        np.testing.assert_array_equal(preview.kspace[2], np.arange(4)[::-1])

    def test_duplicate_lines_averaged(self):
        scans = [Scan(0, 2), Scan(1, 2)]
        preview = build_cartesian_preview(FakeTwix({0: np.ones(4), 1: np.ones(4) * 3}), None, scans)
        np.testing.assert_array_equal(preview.kspace[2], np.full(4, 2))
        self.assertEqual(preview.metadata["repeated_readouts"], 1)

    def test_multi_coil_rss(self):
        scans = [Scan(i, i) for i in range(4)]
        twix = FakeTwix({i: np.ones(4) for i in range(4)})
        single = build_cartesian_preview(twix, None, scans)
        combined = build_cartesian_preview(twix, None, scans, all_channels=True, channel_index=1)
        np.testing.assert_allclose(combined.image, single.image * np.sqrt(5), atol=1e-6)
        np.testing.assert_allclose(combined.kspace, single.kspace * 2)
        self.assertEqual(combined.channel_count, 2)

    def test_partial_readouts_align_at_recorded_center(self):
        scans = [Scan(0, 2, center_column=1), Scan(1, 2, center_column=2)]
        twix = FakeTwix({0: np.array([1, 2, 3, 4]), 1: np.array([10, 20, 30, 40])})
        preview = build_cartesian_preview(twix, None, scans)
        self.assertEqual(preview.kspace.shape, (4, 6))
        np.testing.assert_array_equal(preview.kspace[2], [0, 10, 10.5, 16, 21.5, 4])
        self.assertEqual(preview.coverage.sum(), 5)

    def test_grouping_rejects_every_ambiguous_counter(self):
        scan = Scan(0, 0)
        for name in ("slice", "partition", "echo", "repetition", "set", "average", "phase", "segment", "ida", "idb", "idc", "idd", "ide"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"one {name}"):
                build_cartesian_preview(FakeTwix({}), None, [scan, replace(scan, index=1, **{name: 1})])

    def test_bounds_never_claim_encoded_matrix_or_sampling_completeness(self):
        scans = [Scan(i, i) for i in range(4)]
        preview = build_cartesian_preview(FakeTwix({i: np.ones(4) for i in range(4)}), None, scans)
        self.assertEqual(preview.metadata["sampling_completeness"], "unknown")
        self.assertFalse(preview.metadata["trajectory_verified"])
        self.assertTrue(preview.metadata["inferred_matrix"])
        self.assertTrue(any("phase oversampling" in warning for warning in preview.warnings))

    def test_limits_checked_before_loading_payload(self):
        scans = [Scan(0, 2)]
        for kwargs in ({"max_matrix": 2}, {"max_bytes": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_cartesian_preview(FakeTwix({}), None, scans, **kwargs)

    def test_reordered_or_replaced_physical_channels_are_rejected(self):
        class ChangingChannels(FakeTwix):
            def __init__(self, changed_ids):
                super().__init__({})  # Any payload read would fail this test.
                self.changed_ids = changed_ids

            def channel_ids(self, measurement, scan):
                return [7, 19] if scan.index == 0 else self.changed_ids

        for ids in ([19, 7], [7, 24]):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, "receiver channel IDs or channel order"):
                build_cartesian_preview(ChangingChannels(ids), None, [Scan(0, 0), Scan(1, 1)])

    def test_duplicate_physical_channel_ids_are_rejected(self):
        class DuplicateChannels(FakeTwix):
            def channel_ids(self, measurement, scan):
                return [7, 7]

        with self.assertRaisesRegex(ValueError, "duplicate receiver"):
            build_cartesian_preview(DuplicateChannels({}), None, [Scan(0, 0)])

    def test_invalid_and_nonimage_readouts(self):
        with self.assertRaisesRegex(ValueError, "imaging"):
            build_cartesian_preview(FakeTwix({}), None, [Scan(0, 0, is_image=False)])
        with self.assertRaisesRegex(ValueError, "non-finite"):
            build_cartesian_preview(FakeTwix({0: np.array([1, 2, np.nan, 4])}), None, [Scan(0, 0)])
        with self.assertRaisesRegex(ValueError, "unavailable"):
            build_cartesian_preview(FakeTwix({}), None, [Scan(0, 0)], channel_index=2)

    def test_table_retains_scan_identity_and_flags(self):
        frame = scans_to_frame([Scan(12, 4, flags=("NOISEADJSCAN",), is_image=False)])
        self.assertEqual(frame.index.tolist(), [12])
        self.assertEqual(frame.loc[12, "flags"], "NOISEADJSCAN")
        self.assertFalse(frame.loc[12, "is_image"])
        self.assertEqual(scans_to_frame([]).shape[0], 0)

    def test_counter_filtering(self):
        scans = [Scan(1, 5, echo=2), Scan(2, 6, echo=3), Scan(3, 7, echo=2)]
        self.assertEqual([s.index for s in select_scans(scans, {"echo": 2, "line": [5, 6]})], [1])
        self.assertEqual(select_scans(scans, {"echo": "All"}), scans)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            select_scans(scans, {"made_up": 4})


if __name__ == "__main__":
    unittest.main()
