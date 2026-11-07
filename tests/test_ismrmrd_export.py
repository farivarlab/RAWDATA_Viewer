"""Read exported acquisitions back through the official ISMRMRD library."""

import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

import ismrmrd
import numpy as np

from siemens_explorer.ismrmrd_export import export_ismrmrd
from siemens_explorer.twix import TwixFile, create_demo_file


class ISMRMRDExportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="test-ismrmrd-")
        self.root = Path(self.folder.name)
        self.source = create_demo_file(self.root / "source.dat")
        self.raw = TwixFile(self.source)
        self.m = self.raw.measurements[0]
        self.scans = self.raw.index_measurement(self.m).scans
        self.target = self.root / "result.h5"

    def tearDown(self):
        self.raw.close()
        self.folder.cleanup()

    def test_all_complex_channels_counters_flags_and_buffers_round_trip(self):
        result = export_ismrmrd(self.raw, self.m, self.scans, self.target)
        self.assertEqual(result["acquisitions"], len(self.scans))
        ds = ismrmrd.Dataset(str(self.target), create_if_needed=False)
        try:
            header = ismrmrd.xsd.CreateFromDocument(ds.read_xml_header())
            self.assertEqual(ds.number_of_acquisitions(), len(self.scans))
            for index in (0, 4, 70, 200, len(self.scans)-1):
                original = self.scans[index]
                acq = ds.read_acquisition(index)
                for channel in range(original.channels):
                    np.testing.assert_array_equal(acq.data[channel], self.raw.read_signal(self.m, original, channel))
                self.assertEqual(acq.idx.kspace_encode_step_1, original.line)
                self.assertEqual(acq.idx.slice, original.slice)
                self.assertEqual(acq.idx.contrast, original.echo)
                self.assertEqual(acq.center_sample, original.center_column)
                self.assertEqual(acq.isFlagSet(ismrmrd.ACQ_IS_NOISE_MEASUREMENT), "NOISEADJSCAN" in original.flags)
            buffers = {item.name.removeprefix("SiemensBuffer_"): item.value.decode() for item in header.userParameters.userParameterBase64}
            self.assertEqual(buffers, self.m.buffers)
            self.assertTrue(ds.number_of_arrays("siemens_mdh") > 0)
        finally:
            ds.close()

    def test_receiver_ids_are_mapped_and_reordered_consistently(self):
        self.raw.close()
        ids = [127, 7, 99, 42]
        with self.source.open("r+b") as output:
            for scan in self.scans:
                for channel, physical in enumerate(ids):
                    output.seek(scan.offset + 192 + channel * (32 + scan.samples * 8) + 24)
                    output.write(struct.pack("<H", physical))
        self.raw = TwixFile(self.source)
        self.m = self.raw.measurements[0]
        scans = self.raw.index_measurement(self.m).scans
        export_ismrmrd(self.raw, self.m, scans[:5], self.target, index_complete=False)
        ds = ismrmrd.Dataset(str(self.target), create_if_needed=False)
        try:
            header = ismrmrd.xsd.CreateFromDocument(ds.read_xml_header())
            self.assertEqual([item.coilName for item in header.acquisitionSystemInformation.coilLabel],
                             ["Siemens receive ID 7", "Siemens receive ID 42", "Siemens receive ID 99", "Siemens receive ID 127"])
            acq = ds.read_acquisition(4)
            for row, original_channel in enumerate((1, 3, 2, 0)):
                np.testing.assert_array_equal(acq.data[row], self.raw.read_signal(self.m, scans[4], original_channel))
                self.assertTrue(acq.isChannelActive(row))
            notes = json.loads(header.userParameters.userParameterString[0].value)
            self.assertFalse(notes["index_complete"])
        finally:
            ds.close()

    def test_mdh_geometry_uses_siemens_quaternion_phase_read_order(self):
        self.raw.close()
        scan = self.scans[4]
        with self.source.open("r+b") as output:
            output.seek(scan.offset + 100)
            output.write(struct.pack("<7f", 10, 20, 30, 1, 0, 0, 0))
        self.raw = TwixFile(self.source)
        self.m = self.raw.measurements[0]
        scan = self.raw.index_measurement(self.m).scans[4]
        export_ismrmrd(self.raw, self.m, [scan], self.target)
        ds = ismrmrd.Dataset(str(self.target), create_if_needed=False)
        try:
            acq = ds.read_acquisition(0)
            np.testing.assert_array_equal(acq.position, [10,20,30])
            np.testing.assert_array_equal(acq.read_dir, [0,1,0])
            np.testing.assert_array_equal(acq.phase_dir, [1,0,0])
            np.testing.assert_array_equal(acq.slice_dir, [0,0,1])
        finally:
            ds.close()

    def test_cancel_during_payload_preserves_existing_target_and_cleans_temporary(self):
        self.target.write_bytes(b"keep existing export")
        cancel = threading.Event()
        original = self.raw.read_signal

        def interrupted(*args):
            values = original(*args)
            cancel.set()
            return values

        with patch.object(self.raw, "read_signal", side_effect=interrupted), self.assertRaises(InterruptedError):
            export_ismrmrd(self.raw, self.m, self.scans, self.target, cancel=cancel)
        self.assertEqual(self.target.read_bytes(), b"keep existing export")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_memory_limits_and_source_overwrite_fail_before_payload_reads(self):
        with patch.object(self.raw, "read_signal", side_effect=AssertionError("payload read")):
            with self.assertRaisesRegex(ValueError, "memory"):
                export_ismrmrd(self.raw, self.m, self.scans, self.target, max_acquisition_bytes=1)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                export_ismrmrd(self.raw, self.m, self.scans, self.source)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
