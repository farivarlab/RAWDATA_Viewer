"""Reader tests use independently assembled little-endian binary fixtures."""

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from siemens_explorer.twix import TwixError, TwixFile, create_demo_file, parse_protocol


def protocol_header(length=1024, text='tProtocolName = "Test GRE"\nalTE[0] = 4500\n'):
    payload = text.encode() + b"\0"
    buffer = b"MeasYaps\0" + struct.pack("<I", len(payload)) + payload
    return struct.pack("<II", length, 1) + buffer + bytes(length - 8 - len(buffer))


def adc(vd, arrays, *, mask=0, line=0, slice=0, echo=0, extras=(0, 0, 0, 0, 0)):
    arrays = [np.asarray(data, dtype="<c8") for data in arrays]
    samples, channels = len(arrays[0]), len(arrays)
    channel_size = (32 if vd else 128) + samples * 8
    length = (192 if vd else 0) + channels * channel_size
    output = bytearray(192) if vd else bytearray()
    headers = [output] if vd else []
    if not vd:
        headers = [bytearray(128) for _ in arrays]
    for i, header in enumerate(headers):
        extra = 20 if vd else 0
        struct.pack_into("<I", header, 0, length if vd else channel_size)
        struct.pack_into("<I", header, 8, 100 + line)
        struct.pack_into("<I", header, 12, 400)
        struct.pack_into("<QHH", header, 20 + extra, mask, samples, channels)
        counters = (line, 0, slice, 0, echo, 0, 0, 0, 0) + extras
        struct.pack_into("<14H", header, 32 + extra, *counters)
        struct.pack_into("<HH", header, 60 + extra, 1, 2)
        struct.pack_into("<H", header, 64 + extra, samples // 2)
        struct.pack_into("<HH", header, 76 + extra, 3, 0)
        if not vd:
            struct.pack_into("<H", header, 124, 7 + i * 12)
    for i, data in enumerate(arrays):
        if vd:
            channel = bytearray(32)
            struct.pack_into("<I", channel, 0, channel_size)
            struct.pack_into("<H", channel, 24, 7 + i * 12)
            output.extend(channel)
        else:
            output.extend(headers[i])
        output.extend(data.tobytes())
    return bytes(output)


def end_block(vd):
    out = bytearray(192 if vd else 128)
    struct.pack_into("<I", out, 0, len(out))
    struct.pack_into("<Q", out, 40 if vd else 20, 1)
    return out


def raid(*measurements):
    out = bytearray(10240)
    struct.pack_into("<II", out, 0, 0, len(measurements))
    for i, measurement in enumerate(measurements):
        struct.pack_into("<IIQQ", out, 8 + i * 152, i + 10, i + 20, len(out), len(measurement))
        out.extend(measurement)
    return bytes(out)


class ProtocolTests(unittest.TestCase):
    def test_scalar_paths_and_xprotocol_without_evaluation(self):
        result = parse_protocol('''### ASCCONV BEGIN ###
tProtocolName = "GRE with spaces"
sSliceArray.asSlice[0].dReadoutFOV = 220.5
alTE[0] = 4500
ucMode = 0x10
tDanger = "__import__('os').remove('anything')"
### ASCCONV END ###
<ParamDouble."FieldStrength"> { <Precision> 6 3.0 }
<ParamString."System"> { "Prisma" }
''')
        self.assertEqual(result["tProtocolName"], "GRE with spaces")
        self.assertEqual(result["sSliceArray.asSlice[0].dReadoutFOV"], 220.5)
        self.assertEqual(result["alTE[0]"], 4500)
        self.assertEqual(result["ucMode"], 16)
        self.assertEqual(result["FieldStrength"], 3.0)
        self.assertEqual(result["System"], "Prisma")
        self.assertIn("__import__", result["tDanger"])


class BinaryReaderTests(unittest.TestCase):
    def setUp(self):
        self.a = np.array([1 + 2j, 3 - 4j, -5 + 6j, 0.125 - 0.25j], dtype=np.complex64)
        self.b = self.a * 2j

    def test_vb_multicoil_strides_ids_and_counter_fields(self):
        raw = protocol_header(10240) + adc(False, [self.a, self.b], line=3, slice=4, echo=2, extras=(1, 2, 3, 4, 5)) + end_block(False)
        with TwixFile(raw) as file:
            self.assertEqual(file.format, "VB")
            self.assertEqual(file.measurements[0].protocol_name, "Test GRE")
            result = file.index_measurement(0)
            self.assertTrue(result.complete)
            self.assertEqual(len(result.scans), 1)
            scan = result.scans[0]
            self.assertEqual((scan.line, scan.slice, scan.echo), (3, 4, 2))
            self.assertEqual((scan.ida, scan.idb, scan.idc, scan.idd, scan.ide), (1, 2, 3, 4, 5))
            self.assertEqual((scan.cutoff_pre, scan.cutoff_post), (1, 2))
            self.assertEqual(scan.timestamp_ms, 1000)
            self.assertEqual(file.channel_ids(0, scan), [7, 19])
            np.testing.assert_array_equal(file.read_signal(0, scan, 0), self.a)
            np.testing.assert_array_equal(file.read_signal(0, scan, 1), self.b)

    def test_small_vb_header_does_not_misidentify_as_raid(self):
        with TwixFile(protocol_header() + adc(False, [self.a]) + end_block(False)) as file:
            self.assertEqual(file.format, "VB")
            self.assertEqual(len(file.index_measurement(0).scans), 1)

    def test_vd_multiraid_and_raw_reflected_signal(self):
        first = protocol_header() + adc(True, [self.a, self.b], mask=1 << 24, line=6) + end_block(True)
        second = protocol_header() + adc(True, [self.b], line=2) + end_block(True)
        with TwixFile(raid(first, second)) as file:
            self.assertEqual(file.format, "VD/VE")
            self.assertEqual(len(file.measurements), 2)
            for i in (0, 1):
                self.assertTrue(file.index_measurement(i).complete)
            scan = file.index_measurement(0).scans[0]
            self.assertTrue(scan.reflected)
            self.assertTrue(scan.is_image)
            self.assertEqual(scan.center_line, 3)
            np.testing.assert_array_equal(file.read_signal(0, scan), self.a)
            self.assertEqual(file.channel_ids(0, scan), [7, 19])
            with self.assertRaisesRegex(TwixError, "outside"):
                file.read_signal(1, scan)

    def test_eval_mask_noise_reference_and_high_bit(self):
        masks = [1 << 25, 1 << 21, 1 << 22, (1 << 22) | (1 << 23), 1 << 63]
        raw = raid(protocol_header() + b"".join(adc(True, [self.a], mask=m) for m in masks) + end_block(True))
        with TwixFile(raw) as file:
            scans = file.index_measurement(0).scans
            self.assertEqual([s.is_image for s in scans], [False, False, False, True, True])
            self.assertIn("NOISEADJSCAN", scans[0].flags)
            self.assertIn("WIP_3", scans[4].flags)

    def test_sync_blocks_are_indexed_but_not_treated_as_samples(self):
        sync = bytearray(384)
        struct.pack_into("<I", sync, 0, 384)
        struct.pack_into("<QHH", sync, 40, 1 << 5, 123, 4)
        raw = raid(protocol_header() + sync + adc(True, [self.a]) + end_block(True))
        with TwixFile(raw) as file:
            result = file.index_measurement(0)
            self.assertTrue(result.complete)
            self.assertEqual(len(result.scans), 2)
            self.assertFalse(result.scans[0].is_image)
            self.assertEqual(result.scans[0].samples, 0)
            with self.assertRaises(TwixError):
                file.read_signal(0, result.scans[0])

    def test_packed_dma_length_does_not_skip_acquisitions(self):
        block = bytearray(adc(True, [self.a]))
        struct.pack_into("<I", block, 0, 2 * len(block) | (1 << 25))
        raw = raid(protocol_header() + block + adc(True, [self.b]) + end_block(True))
        with TwixFile(raw) as file:
            result = file.index_measurement(0)
            self.assertTrue(result.complete)
            self.assertEqual(len(result.scans), 2)
            np.testing.assert_array_equal(file.read_signal(0, result.scans[1]), self.b)

    def test_truncated_file_preserves_valid_prefix(self):
        raw = raid(protocol_header() + adc(True, [self.a]) + adc(True, [self.b]) + end_block(True))
        raw = raw[:-(192 + 8)]
        with TwixFile(raw) as file:
            result = file.index_measurement(0)
            self.assertFalse(result.complete)
            self.assertEqual(len(result.scans), 1)
            self.assertTrue(any("Truncated" in warning for warning in result.warnings))
            np.testing.assert_array_equal(file.read_signal(0, result.scans[0]), self.a)

    def test_multiraid_truncation_preserves_earlier_complete_measurement(self):
        measurement = protocol_header() + adc(True, [self.a]) + end_block(True)
        complete = raid(measurement, measurement, measurement)
        first_end = 10240 + len(measurement)
        # No later header, a partial eight-byte prefix, and a partial text header.
        for available_tail in (0, 4, 8, 100):
            with self.subTest(available_tail=available_tail):
                with TwixFile(complete[:first_end + available_tail]) as file:
                    self.assertEqual(file.format, "VD/VE")
                    self.assertEqual(len(file.measurements), 1)
                    result = file.index_measurement(0)
                    self.assertTrue(result.complete)
                    self.assertTrue(any("omitted 2 unavailable trailing RAID" in warning for warning in result.warnings))
                    np.testing.assert_array_equal(file.read_signal(0, result.scans[0]), self.a)

    def test_last_raid_measurement_zero_length_can_be_inferred(self):
        measurement = protocol_header() + adc(True, [self.a]) + end_block(True)
        raw = bytearray(raid(measurement, measurement))
        struct.pack_into("<Q", raw, 8 + 152 + 16, 0)
        with TwixFile(raw) as file:
            self.assertEqual(len(file.measurements), 2)
            self.assertTrue(file.index_measurement(1).complete)
            self.assertIn("inferred", file.measurements[1].warnings[0])

    def test_limit_and_cancel_keep_valid_prefixes(self):
        raw = raid(protocol_header() + adc(True, [self.a]) * 4 + end_block(True))
        with TwixFile(raw) as file:
            limited = file.index_measurement(0, max_scans=2)
            self.assertEqual(len(limited.scans), 2)
            self.assertFalse(limited.complete)
            self.assertIn("limited", limited.warnings[0])
            cancelled = file.index_measurement(0, progress=lambda fraction, count: False)
            self.assertEqual(len(cancelled.scans), 1)
            self.assertIn("cancelled", cancelled.warnings[0])

    def test_closed_handles_invalid_channel_and_bad_header(self):
        file = TwixFile(raid(protocol_header() + adc(True, [self.a]) + end_block(True)))
        scan = file.index_measurement(0).scans[0]
        with self.assertRaises(TwixError):
            file.read_signal(0, scan, 2)
        file.close()
        with self.assertRaisesRegex(TwixError, "closed"):
            file.read_signal(0, scan)
        for raw in [b"", b"1234", bytes(200), b"not a siemens raw file"]:
            with self.subTest(raw=raw[:10]), self.assertRaises(TwixError):
                TwixFile(raw)
        bad_header = bytearray(protocol_header())
        struct.pack_into("<I", bad_header, 17, 99999999)
        with self.assertRaisesRegex(TwixError, "exceeds"):
            TwixFile(raid(bad_header + end_block(True)))

    def test_demo_path_is_lazy_and_has_distinct_slices_and_echoes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = create_demo_file(Path(folder) / "synthetic.dat")
            with TwixFile(path) as file:
                self.assertEqual(file.path, path.resolve())
                calls = []
                read = file._read

                def tracked(offset, length, **kwargs):
                    calls.append(length)
                    return read(offset, length, **kwargs)

                file._read = tracked
                result = file.index_measurement(0)
                self.assertTrue(result.complete)
                self.assertEqual(len(result.scans), 260)
                self.assertLessEqual(max(calls), 192)
                images = [s for s in result.scans if s.is_image]
                self.assertEqual(len(images), 256)
                self.assertEqual({s.slice for s in images}, {0, 1})
                self.assertEqual({s.echo for s in images}, {0, 1})
                first_echo = next(s for s in images if s.slice == 0 and s.echo == 0 and s.line == 32)
                second_echo = next(s for s in images if s.slice == 0 and s.echo == 1 and s.line == 32)
                a = file.read_signal(0, first_echo)
                b = file.read_signal(0, second_echo)
                self.assertEqual(a.dtype, np.complex64)
                self.assertEqual(a.size, 128)
                np.testing.assert_allclose(b, a * 0.74, rtol=1e-6, atol=1e-6)
                self.assertEqual(file.measurements[0].metadata["tr_us"], 12000)


if __name__ == "__main__":
    unittest.main()
