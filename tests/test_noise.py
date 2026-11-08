"""Raw-noise identity, bounds, and acquisition-order regression checks."""

from dataclasses import dataclass, replace
import unittest
from unittest.mock import patch

import numpy as np

from siemens_explorer.noise import build_noise_preview


@dataclass
class Scan:
    index: int
    samples: int = 4
    channels: int = 2
    flags: tuple = ("NOISEADJSCAN",)
    line: int = 0
    reflected: bool = False


class Reader:
    def __init__(self, values, ids=None):
        self.values = values
        self.ids = ids or {}
        self.reads = []
        self.id_reads = []

    def channel_ids(self, measurement, scan):
        self.id_reads.append(scan.index)
        return self.ids.get(scan.index, [7, 42])

    def read_signal(self, measurement, scan, channel):
        self.reads.append((scan.index, channel))
        return self.values[scan.index]


class NoisePreviewTests(unittest.TestCase):
    def test_rows_keep_acquisition_identity_without_fft_reversal_or_averaging(self):
        first = np.array([1 + 2j, -3 + 4j, 5 - 1j, 7j], dtype=np.complex64)
        second = first * 3
        reader = Reader({17: first, 5: second})
        scans = [Scan(17, reflected=True), Scan(5)]
        with patch("numpy.fft.ifft2", side_effect=AssertionError("Noise is not reconstructed")):
            preview = build_noise_preview(reader, None, scans, channel_index=1)
        np.testing.assert_array_equal(preview.kspace, [first, second])
        np.testing.assert_array_equal(preview.image, np.abs([first, second]))
        np.testing.assert_array_equal(preview.lines, [17, 5])
        self.assertTrue(preview.coverage.all())
        self.assertEqual(reader.reads, [(17, 1), (5, 1)])
        self.assertEqual(preview.metadata["selected_channel_id"], 42)
        self.assertEqual(preview.metadata["display_kind"], "noise")
        first[:] = 0
        self.assertTrue(preview.kspace[0].any())

    def test_memory_and_header_limits_fail_before_any_payload_or_identity_reads(self):
        scan = Scan(0)
        cases = [
            ({"max_bytes": 1}, [scan]),
            ({"channel_index": 2}, [scan]),
            ({}, [scan, replace(scan, index=1, samples=5)]),
            ({}, [replace(scan, samples=0)]),
            ({}, [replace(scan, channels=0)]),
        ]
        for options, scans in cases:
            with self.subTest(options=options, scans=scans):
                reader = Reader({})
                with self.assertRaises(ValueError):
                    build_noise_preview(reader, None, scans, **options)
                self.assertEqual(reader.reads, [])
                self.assertEqual(reader.id_reads, [])

    def test_physical_channel_mismatch_and_invalid_ids_fail_before_payloads(self):
        for ids in ([42, 7], [7, 99], [7, 7], [7], [-1, 42]):
            with self.subTest(ids=ids):
                reader = Reader({}, {1: ids})
                with self.assertRaisesRegex(ValueError, "receiver channel IDs"):
                    build_noise_preview(reader, None, [Scan(0), Scan(1)], channel_index=1)
                self.assertEqual(reader.reads, [])

    def test_only_selected_physical_channel_needs_to_be_consistent(self):
        scans = [Scan(0), Scan(1, channels=3)]
        reader = Reader({0: np.ones(4), 1: np.ones(4)}, {1: [12, 42, 99]})
        preview = build_noise_preview(reader, None, scans, channel_index=1)
        self.assertEqual(preview.metadata["selected_channel_id"], 42)

    def test_display_limit_excludes_payloads_beyond_the_visible_prefix(self):
        reader = Reader({0: np.ones(4), 1: np.ones(4)})
        scans = [Scan(0), Scan(1), Scan(2, samples=8192)]
        preview = build_noise_preview(reader, None, scans, max_readouts=2)
        self.assertEqual(reader.reads, [(0, 0), (1, 0)])
        self.assertEqual(reader.id_reads, [0, 1])
        self.assertEqual(preview.metadata["total_noise_readouts"], 3)
        self.assertEqual(preview.metadata["shown_noise_readouts"], 2)
        self.assertTrue(any("first 2 of 3" in warning for warning in preview.warnings))

    def test_non_noise_records_are_excluded_and_empty_noise_selection_is_rejected(self):
        reader = Reader({3: np.ones(4)})
        preview = build_noise_preview(reader, None, [Scan(0, flags=()), Scan(3)])
        self.assertEqual(reader.reads, [(3, 0)])
        self.assertTrue(any("Excluded 1" in warning for warning in preview.warnings))
        with self.assertRaisesRegex(ValueError, "noise acquisition"):
            build_noise_preview(Reader({}), None, [Scan(0, flags=())])

    def test_non_finite_samples_are_retained_and_qualified(self):
        values = np.array([1 + 2j, complex(np.nan, 1), complex(2, np.inf), 0j], dtype=np.complex64)
        reader = Reader({0: values})
        preview = build_noise_preview(reader, None, [Scan(0)])
        np.testing.assert_array_equal(preview.kspace[0], values)
        self.assertEqual(preview.metadata["non_finite_samples"], 2)
        self.assertTrue(any("non-finite" in warning for warning in preview.warnings))

    def test_bad_readout_shape_and_cancellation_propagate(self):
        for values in (np.ones(3), np.ones((1, 4))):
            with self.subTest(shape=values.shape), self.assertRaisesRegex(ValueError, "unexpected noise readout shape"):
                build_noise_preview(Reader({0: values}), None, [Scan(0)])

        class Cancelled(Reader):
            def read_signal(self, measurement, scan, channel):
                raise InterruptedError("Cancelled")

        with self.assertRaisesRegex(InterruptedError, "Cancelled"):
            build_noise_preview(Cancelled({}), None, [Scan(0)])

    def test_unverified_reader_is_marked_and_argument_limits_are_validated(self):
        class OrdinalReader:
            def read_signal(self, measurement, scan, channel):
                return np.ones(4, dtype=np.complex64)

        preview = build_noise_preview(OrdinalReader(), None, [Scan(0)])
        self.assertFalse(preview.metadata["channel_ids_verified"])
        self.assertIsNone(preview.metadata["selected_channel_id"])
        self.assertTrue(any("could not be verified" in warning for warning in preview.warnings))
        for options in ({"channel_index": -1}, {"channel_index": True}, {"max_readouts": 0},
                        {"max_bytes": 0}, {"max_readouts": 2.5}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                build_noise_preview(OrdinalReader(), None, [Scan(0)], **options)


if __name__ == "__main__":
    unittest.main()
