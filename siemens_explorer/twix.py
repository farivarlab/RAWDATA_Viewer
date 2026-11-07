"""Bounded, lazy reader for Siemens MRI Twix VB and VD/VE ``.dat`` files.

Only protocol text and scan headers are read while indexing. Complex samples
are loaded one readout/channel at a time. This is an inspection reader: it does
not apply ramp-sampling regridding, oversampling removal or coil corrections.

Binary layout references (schema facts, independent implementation):
https://github.com/pehses/twixtools/blob/master/twixtools/mdh_def.py
https://github.com/pehses/mapVBVD/blob/main/mapVBVD.m
"""

from __future__ import annotations

import io
import os
import re
import struct
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np


class TwixError(ValueError):
    """The input is unsupported, malformed or truncated."""


@dataclass(slots=True)
class Measurement:
    index: int
    id: int
    offset: int
    length: int
    header_length: int
    data_offset: int
    format: str
    protocol_name: str = ""
    patient_name: str = ""
    buffers: dict[str, str] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    protocol: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Scan:
    index: int
    offset: int
    length: int
    samples: int
    channels: int
    line: int
    partition: int
    slice: int
    echo: int
    repetition: int
    set: int
    average: int
    phase: int
    segment: int
    flags: tuple[str, ...]
    is_image: bool
    center_column: int
    center_line: int
    center_partition: int
    reflected: bool
    timestamp_ms: float
    scan_counter: int
    eval_mask: int
    ida: int = 0
    idb: int = 0
    idc: int = 0
    idd: int = 0
    ide: int = 0
    cutoff_pre: int = 0
    cutoff_post: int = 0


@dataclass(slots=True)
class ScanIndex:
    scans: list[Scan]
    complete: bool
    bytes_scanned: int
    warnings: list[str]


_FLAGS = {
    0: "ACQEND", 1: "RTFEEDBACK", 2: "HPFEEDBACK", 3: "ONLINE",
    4: "OFFLINE", 5: "SYNCDATA", 8: "LASTSCANINCONCAT",
    10: "RAWDATACORRECTION", 11: "LASTSCANINMEAS", 12: "SCANSCALEFACTOR",
    13: "2NDHADAMARPULSE", 14: "REFPHASESTABSCAN", 15: "PHASESTABSCAN",
    16: "D3FFT", 17: "SIGNREV", 18: "PHASEFFT", 19: "SWAPPED",
    20: "POSTSHAREDLINE", 21: "PHASCOR", 22: "PATREFSCAN",
    23: "PATREFANDIMASCAN", 24: "REFLECT", 25: "NOISEADJSCAN",
    26: "SHARENOW", 27: "LASTMEASUREDLINE", 28: "FIRSTSCANINSLICE",
    29: "LASTSCANINSLICE", 30: "TREFFECTIVEBEGIN", 31: "TREFFECTIVEEND",
    32: "REF_POSITION", 33: "SLC_AVERAGED", 34: "TAGFLAG1",
    35: "CT_NORMALIZE", 36: "SCAN_FIRST", 37: "SCAN_LAST",
    38: "SLICE_ACCEL_REFSCAN", 39: "SLICE_ACCEL_PHASCOR",
    40: "FIRST_SCAN_IN_BLADE", 41: "LAST_SCAN_IN_BLADE", 42: "LAST_BLADE_IN_TR",
    44: "PACE", 45: "RETRO_LASTPHASE", 46: "RETRO_ENDOFMEAS",
    47: "RETRO_REPEATTHISHEARTBEAT", 48: "RETRO_REPEATPREVHEARTBEAT",
    49: "RETRO_ABORTSCANNOW", 50: "RETRO_LASTHEARTBEAT", 51: "RETRO_DUMMYSCAN",
    52: "RETRO_ARRDETDISABLED", 53: "B1_CONTROLLOOP", 54: "SKIP_ONLINE_PHASCOR",
    55: "SKIP_REGRIDDING", 56: "VOP", 61: "WIP_1", 62: "WIP_2", 63: "WIP_3",
}
_NOT_IMAGE = sum(1 << bit for bit in (0, 1, 2, 5, 14, 15, 21, 25, 60))
_MAX_HEADER = 64 * 1024 * 1024
_MAX_BUFFERS = 4096
_DMA_MASK = (1 << 25) - 1


@lru_cache(maxsize=512)
def _flags(mask: int) -> tuple[str, ...]:
    return tuple(_FLAGS.get(bit, f"BIT_{bit}") for bit in range(64) if mask & (1 << bit))


def _decode(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _value(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1]
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        return int(raw, 16) if raw.lower().startswith(("0x", "-0x")) else int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def parse_protocol(text: str) -> dict:
    """Extract flat ASCCONV paths and scalar XProtocol parameters safely.

    ASCCONV array indices stay zero-based and units stay as stored by Siemens
    (for example, ``alTE[0]`` is usually microseconds). Raw buffers are retained
    separately; nested XProtocol maps/arrays are intentionally not interpreted.
    """
    result = {}
    for line in text.replace("\x00", "").splitlines():
        match = re.match(r"^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[\d+\])*)\s*=\s*(.*?)\s*$", line)
        if match:
            result[match[1]] = _value(match[2])
    pattern = r'<Param(?:String|Long|Double|Bool)\."([^"\n]+)">\s*\{([^{}]*)\}'
    for match in re.finditer(pattern, text):
        raw = re.sub(r"<Precision>\s*\d+", "", match[2]).strip()
        # Other tagged attributes aren't parameter values.
        raw = re.sub(r"<[^>]+>[^\n]*", "", raw).strip()
        if raw:
            result.setdefault(match[1], _value(raw))
    return result


class TwixFile:
    """Open an on-disk path (recommended for large data) or uploaded bytes.

    Use as a context manager or call ``close`` to release the file handle.
    ``progress`` callbacks receive ``(fraction, scan_count)`` and may return
    ``False`` to cancel indexing while keeping the valid prefix of acquisitions.
    All counters exposed by this reader are zero-based scanner values.
    """

    def __init__(self, source: str | os.PathLike | bytes | bytearray | memoryview):
        self.path: Path | None = None
        self._lock = threading.RLock()
        self._closed = False
        if isinstance(source, (bytes, bytearray, memoryview)):
            self._file = io.BytesIO(source)
            self.size = len(source)
        else:
            self.path = Path(source).expanduser().resolve()
            self._file = self.path.open("rb")
            self.size = os.fstat(self._file.fileno()).st_size
        try:
            self.format, self.measurements = self._inspect()
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._lock:
            if not self._closed:
                self._file.close()
                self._closed = True

    def _read(self, offset: int, length: int, *, end: int | None = None) -> bytes:
        limit = self.size if end is None else min(end, self.size)
        if offset < 0 or length < 0 or offset + length > limit:
            raise TwixError(f"Truncated data: need {length:,} bytes at offset {offset:,} (end {limit:,}).")
        with self._lock:
            if self._closed:
                raise TwixError("This raw-data file is closed. Reopen the file to continue.")
            self._file.seek(offset)
            data = self._file.read(length)
        if len(data) != length:
            raise TwixError(f"File changed or ended while reading offset {offset:,}.")
        return data

    def _raid_entries(self, count: int) -> tuple[list[tuple], str] | None:
        if not 1 <= count <= 64 or 8 + count * 152 > self.size:
            return None
        entries, tail_warning = [], ""
        table_end = 8 + count * 152
        for i in range(count):
            data = self._read(8 + i * 152, 152)
            meas_id, file_id, offset, length = struct.unpack_from("<IIQQ", data)
            # Some exports leave the final length zero; infer it from EOF.
            inferred = length == 0 and i == count - 1
            if inferred:
                length = self.size - offset
            truncated = offset + length > self.size
            if truncated:
                length = self.size - offset
            if offset < table_end:
                return None
            if length < 8:
                if entries and offset + max(length, 0) >= self.size:
                    tail_warning = (
                        f"File is truncated: omitted {count - i} unavailable trailing RAID "
                        "measurement(s) whose protocol headers were not completely recorded."
                    )
                    break
                return None
            entries.append((meas_id, file_id, offset, length, _decode(data[24:88]), _decode(data[88:152]), inferred, truncated))
        spans = sorted((entry[2], entry[2] + entry[3]) for entry in entries)
        if any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1)):
            return None
        return entries, tail_warning

    def _inspect(self) -> tuple[str, list[Measurement]]:
        if self.size < 8:
            raise TwixError("Not a Siemens Twix file: the file is shorter than its header.")
        first, second = struct.unpack("<II", self._read(0, 8))
        raid_result = self._raid_entries(second) if first < 10000 else None
        tail_warning = ""
        if raid_result is not None:
            fmt = "VD/VE"
            entries, tail_warning = raid_result
        elif first >= 8 and first <= min(_MAX_HEADER, self.size):
            fmt = "VB"
            entries = [(0, 0, 0, self.size, "", "", False, False)]
        else:
            raise TwixError("Unsupported or malformed Siemens raw-data header. Expected a VB header or VD/VE RAID table.")
        measurements = []
        for i, (meas_id, file_id, offset, length, patient, protocol_name, inferred, truncated) in enumerate(entries):
            header_len, count = struct.unpack("<II", self._read(offset, 8, end=offset + length))
            if fmt == "VD/VE" and truncated and 8 <= header_len <= _MAX_HEADER and header_len > length and measurements:
                tail_warning = (
                    f"File is truncated: omitted {second - i} unavailable trailing RAID "
                    "measurement(s) whose protocol headers were not completely recorded."
                )
                break
            if not 8 <= header_len <= min(length, _MAX_HEADER):
                raise TwixError(f"Measurement {i}: invalid protocol header length {header_len:,}.")
            if count > _MAX_BUFFERS:
                raise TwixError(f"Measurement {i}: implausible protocol buffer count {count:,}.")
            header = self._read(offset, header_len, end=offset + length)
            pos, buffers = 8, {}
            for b in range(count):
                zero = header.find(b"\0", pos, min(pos + 256, header_len))
                if zero < 0 or zero + 5 > header_len:
                    raise TwixError(f"Measurement {i}: missing buffer name or length in protocol header.")
                name = _decode(header[pos:zero]) or f"Buffer{b}"
                buffer_len = struct.unpack_from("<I", header, zero + 1)[0]
                pos = zero + 5
                if pos + buffer_len > header_len:
                    raise TwixError(f"Measurement {i}: protocol buffer {name!r} exceeds its header.")
                key = name if name not in buffers else f"{name}_{b}"
                buffers[key] = header[pos:pos + buffer_len].decode("utf-8", errors="replace").rstrip("\0")
                pos += buffer_len
            protocol = {}
            for text in buffers.values():
                protocol.update(parse_protocol(text))
            protocol_name = str(protocol.get("tProtocolName", protocol.get("ProtocolName", protocol_name or f"Measurement {i + 1}")))
            patient = patient or str(protocol.get("tPatientName", protocol.get("PatientName", "")))
            metadata = {
                "measurement_id": meas_id, "file_id": file_id,
                "protocol_name": protocol_name,
                "sequence": protocol.get("tSequenceFileName", protocol.get("SequenceFileName", "")),
                "software_version": protocol.get("sProtConsistencyInfo.tBaselineString", protocol.get("SoftwareVersions", "")),
                "base_resolution": protocol.get("sKSpace.lBaseResolution"),
                "phase_encoding_lines": protocol.get("sKSpace.lPhaseEncodingLines"),
                "partitions": protocol.get("sKSpace.lPartitions"),
                "slices": protocol.get("sSliceArray.lSize"),
                "tr_us": protocol.get("alTR[0]"), "te_us": protocol.get("alTE[0]"),
                "flip_angle_deg": protocol.get("adFlipAngleDegree[0]"),
                "field_strength_t": protocol.get("sProtConsistencyInfo.flNominalB0"),
            }
            warnings = ["RAID measurement length was zero; inferred from file size."] if inferred else []
            if truncated:
                warnings.append("RAID measurement extends past EOF; indexing will stop at the available file boundary.")
            measurements.append(Measurement(i, meas_id, offset, length, header_len, offset + header_len, fmt,
                                            protocol_name, patient, buffers, metadata, protocol, warnings))
        if tail_warning:
            for measurement in measurements:
                measurement.warnings.append(tail_warning)
        return fmt, measurements

    def _measurement(self, measurement: Measurement | int) -> Measurement:
        if isinstance(measurement, int):
            try:
                return self.measurements[measurement]
            except IndexError as exc:
                raise TwixError("Measurement index is out of range.") from exc
        if not any(measurement is item for item in self.measurements):
            raise TwixError("The measurement belongs to another raw-data file.")
        return measurement

    def index_measurement(self, measurement: Measurement | int, max_scans: int = 1_000_000,
                          progress: Callable[[float, int], object] | None = None) -> ScanIndex:
        """Index scan headers; report incomplete/truncated files without losing a valid prefix."""
        measurement = self._measurement(measurement)
        if max_scans < 1:
            raise ValueError("max_scans must be positive")
        vd = measurement.format != "VB"
        mdh_len, extra = (192, 20) if vd else (128, 0)
        pos, end = measurement.data_offset, measurement.offset + measurement.length
        scans, warnings, complete = [], list(measurement.warnings), False
        last_reported = -1.0
        while pos < end:
            if len(scans) >= max_scans:
                warnings.append(f"Index limited to {max_scans:,} acquisitions. Increase the limit to read more.")
                break
            try:
                header = self._read(pos, mdh_len, end=end)
            except TwixError as exc:
                warnings.append(str(exc))
                break
            declared = struct.unpack_from("<I", header)[0] & _DMA_MASK
            mask = struct.unpack_from("<Q", header, 20 + extra)[0]
            if mask & 1:
                if declared and pos + declared > end:
                    warnings.append(f"Truncated ACQEND block at byte {pos:,}.")
                else:
                    complete = True
                    pos += declared or mdh_len
                break
            if declared == 0:
                warnings.append(f"Zero-length block at byte {pos:,}; no ACQEND marker was found.")
                break
            samples, channels = struct.unpack_from("<HH", header, 28 + extra)
            is_sync = bool(mask & (1 << 5))
            if is_sync:
                block_len = declared
                samples, channels = 0, 0
            else:
                if not samples or not channels or channels > 4096:
                    warnings.append(f"Invalid sample/channel count ({samples} / {channels}) at byte {pos:,}.")
                    break
                # DMA length may describe packed ADCs; canonical sizes identify each ADC.
                block_len = (192 if vd else 0) + channels * ((32 if vd else 128) + samples * 8)
            if block_len < mdh_len or pos + block_len > end:
                warnings.append(f"Truncated or invalid acquisition at byte {pos:,}: needs {block_len:,} bytes.")
                break
            counters = struct.unpack_from("<14H", header, 32 + extra)
            center_column = struct.unpack_from("<H", header, 64 + extra)[0]
            center_line, center_partition = struct.unpack_from("<HH", header, 76 + extra)
            cutoff_pre, cutoff_post = struct.unpack_from("<HH", header, 60 + extra)
            image = not bool(mask & _NOT_IMAGE) and not bool(mask & (1 << 22) and not mask & (1 << 23))
            scans.append(Scan(
                index=len(scans), offset=pos, length=block_len, samples=samples, channels=channels,
                line=counters[0], average=counters[1], slice=counters[2], partition=counters[3],
                echo=counters[4], phase=counters[5], repetition=counters[6], set=counters[7], segment=counters[8],
                ida=counters[9], idb=counters[10], idc=counters[11], idd=counters[12], ide=counters[13],
                cutoff_pre=cutoff_pre, cutoff_post=cutoff_post,
                flags=_flags(mask), is_image=image, center_column=center_column, center_line=center_line,
                center_partition=center_partition, reflected=bool(mask & (1 << 24)),
                timestamp_ms=struct.unpack_from("<I", header, 12)[0] * 2.5,
                scan_counter=struct.unpack_from("<I", header, 8)[0], eval_mask=mask))
            pos += block_len
            fraction = (pos - measurement.data_offset) / max(1, end - measurement.data_offset)
            if progress and (fraction - last_reported >= 0.01 or len(scans) % 4096 == 0):
                last_reported = fraction
                if progress(min(1.0, fraction), len(scans)) is False:
                    warnings.append("Indexing cancelled; only the valid prefix is shown.")
                    break
        else:
            warnings.append("Measurement ended without an ACQEND marker; the file may be incomplete.")
        if progress:
            progress(1.0 if complete else min(1.0, (pos - measurement.data_offset) / max(1, end - measurement.data_offset)), len(scans))
        return ScanIndex(scans, complete, max(0, min(pos, end) - measurement.data_offset), warnings)

    def _channel_location(self, measurement: Measurement, scan: Scan, channel_index: int) -> tuple[int, int]:
        if not isinstance(channel_index, (int, np.integer)) or not 0 <= channel_index < scan.channels:
            raise TwixError(f"Channel index must be between 0 and {scan.channels - 1}.")
        if not scan.samples or "SYNCDATA" in scan.flags:
            raise TwixError("This block contains synchronization data, not complex signal samples.")
        end = measurement.offset + measurement.length
        if scan.offset < measurement.data_offset or scan.offset + scan.length > end:
            raise TwixError("Acquisition lies outside the selected measurement.")
        vd = measurement.format != "VB"
        channel_header_len = 32 if vd else 128
        channel_offset = scan.offset + (192 if vd else 0) + channel_index * (channel_header_len + scan.samples * 8)
        header = self._read(channel_offset, channel_header_len, end=min(end, scan.offset + scan.length))
        channel_id = struct.unpack_from("<H", header, 24 if vd else 124)[0]
        if vd:
            declared = struct.unpack_from("<I", header)[0] & _DMA_MASK
            # Some scanner versions omit this field; a nonzero short length is corrupt.
            if declared and declared < 32 + scan.samples * 8:
                raise TwixError(f"Channel {channel_index} payload is shorter than its sample count.")
        else:
            channel_samples = struct.unpack_from("<H", header, 28)[0]
            if channel_samples != scan.samples:
                raise TwixError("VB channels have inconsistent sample counts; cannot decode this acquisition.")
        return channel_offset + channel_header_len, channel_id

    def read_mdh(self, measurement: Measurement | int, scan: Scan) -> bytes:
        """Read the stored acquisition header for geometry/export metadata."""
        measurement = self._measurement(measurement)
        if scan.offset < measurement.data_offset or scan.offset + scan.length > measurement.offset + measurement.length:
            raise TwixError("Acquisition falls outside the selected measurement.")
        return self._read(scan.offset, 128 if measurement.format == "VB" else 192,
                          end=measurement.offset + measurement.length)

    def channel_ids(self, measurement: Measurement | int, scan: Scan) -> list[int]:
        measurement = self._measurement(measurement)
        return [self._channel_location(measurement, scan, c)[1] for c in range(scan.channels)]

    def read_signal(self, measurement: Measurement | int, scan: Scan, channel_index: int = 0) -> np.ndarray:
        """Return complex64 samples in acquisition order (REFLECT is not corrected)."""
        measurement = self._measurement(measurement)
        offset, _ = self._channel_location(measurement, scan, channel_index)
        raw = self._read(offset, scan.samples * 8, end=scan.offset + scan.length)
        return np.frombuffer(raw, dtype="<c8").copy()


def create_demo_file(path: str | os.PathLike) -> Path:
    """Write a synthetic VD/VE Cartesian MRI phantom, never clinical/patient data.

    The file has one RAID measurement, two slices, two echoes, 64 phase-encoding
    lines, 128 complex samples and four simulated coils. Noise scans precede images.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    nx, ny, ncoil = 128, 64, 4
    x, y = np.meshgrid(np.linspace(-1, 1, nx, endpoint=False), np.linspace(-1, 1, ny, endpoint=False))
    body = ((x / 0.68) ** 2 + (y / 0.88) ** 2 < 1).astype(float) * 0.72
    body -= (((x + 0.18) / 0.22) ** 2 + ((y - 0.05) / 0.48) ** 2 < 1) * 0.40
    body -= (((x - 0.20) / 0.18) ** 2 + ((y + 0.03) / 0.42) ** 2 < 1) * 0.31
    body += np.exp(-((x + 0.25) ** 2 + (y + 0.34) ** 2) / 0.016) * 0.45
    coils = []
    for c in range(ncoil):
        angle = c * np.pi / 2
        sensitivity = (0.72 + 0.23 * (x * np.cos(angle) + y * np.sin(angle))) * np.exp(1j * (angle + x * 0.4 + y * 0.25))
        coils.append(np.fft.fftshift(np.fft.fft2(np.fft.ifftshift(body * sensitivity))).astype("<c8"))
    kspace = np.stack(coils)
    text = "\n".join([
        "### ASCCONV BEGIN ###", 'tProtocolName = "SYNTHETIC_GRE_PHANTOM"',
        'tSequenceFileName = "synthetic/gre"',
        'sProtConsistencyInfo.tBaselineString = "syngo MR E11 (synthetic demo)"',
        "sProtConsistencyInfo.flNominalB0 = 3.0", "sKSpace.lBaseResolution = 128",
        "sKSpace.lPhaseEncodingLines = 64", "sKSpace.lPartitions = 1", "sSliceArray.lSize = 2",
        "sSliceArray.asSlice[0].dReadoutFOV = 240.0", "sSliceArray.asSlice[0].dPhaseFOV = 240.0",
        "sSliceArray.asSlice[0].dThickness = 5.0", "sKSpace.ucTrajectory = 1",
        "alTR[0] = 12000", "alTE[0] = 4500", "alTE[1] = 7500", "lContrasts = 2", "adFlipAngleDegree[0] = 15.0",
        "sRXSPEC.alDwellTime[0] = 10000", "### ASCCONV END ###", ""])
    encoded = text.encode("utf-8") + b"\0"
    protocol = b"MeasYaps\0" + struct.pack("<I", len(encoded)) + encoded
    header_len = ((8 + len(protocol) + 511) // 512) * 512
    scan_len = 192 + ncoil * (32 + nx * 8)
    noise_count = 4
    image_count = ny * 2 * 2
    measurement_length = header_len + (image_count + noise_count) * scan_len + 192
    offset = 10240
    raid = bytearray(offset)
    struct.pack_into("<II", raid, 0, 0, 1)
    struct.pack_into("<IIQQ", raid, 8, 1, 1, offset, measurement_length)
    raid[32:32 + len(b"SYNTHETIC PHANTOM")] = b"SYNTHETIC PHANTOM"
    raid[96:96 + len(b"SYNTHETIC_GRE_PHANTOM")] = b"SYNTHETIC_GRE_PHANTOM"
    rng = np.random.default_rng(2026)
    with path.open("wb") as out:
        out.write(raid)
        out.write(struct.pack("<II", header_len, 1))
        out.write(protocol)
        out.write(bytes(header_len - 8 - len(protocol)))
        for acq in range(image_count + noise_count):
            noise = acq < noise_count
            image_index = max(0, acq - noise_count)
            line = image_index % ny
            slice_index = image_index // (ny * 2)
            echo = (image_index // ny) % 2
            mask = (1 << 25) if noise else (1 << 3)
            if not noise and line == 0:
                mask |= 1 << 28
            if not noise and line == ny - 1:
                mask |= 1 << 29
            if acq == image_count + noise_count - 1:
                mask |= 1 << 11
            header = bytearray(192)
            struct.pack_into("<IIII", header, 0, scan_len, 1, acq, 1000 + 5 * acq)
            struct.pack_into("<QHH", header, 40, mask, nx, ncoil)
            struct.pack_into("<H", header, 52, line)
            struct.pack_into("<H", header, 56, slice_index)
            struct.pack_into("<H", header, 60, echo)
            struct.pack_into("<H", header, 84, nx // 2)
            struct.pack_into("<HH", header, 96, ny // 2, 0)
            out.write(header)
            for c in range(ncoil):
                ch = bytearray(32)
                struct.pack_into("<III", ch, 0, 32 + nx * 8, 1, acq)
                struct.pack_into("<H", ch, 24, c)
                out.write(ch)
                # Slice 1 is translated and echo 1 attenuated, exercising filter controls.
                phase = np.exp(-2j * np.pi * (np.arange(nx) - nx // 2) * slice_index * 5 / nx)
                signal = ((rng.standard_normal(nx) + 1j * rng.standard_normal(nx)) * 0.1 if noise
                          else kspace[c, line] * phase * (0.74 ** echo))
                out.write(np.asarray(signal, dtype="<c8").tobytes())
        end_header = bytearray(192)
        struct.pack_into("<I", end_header, 0, 192)
        struct.pack_into("<Q", end_header, 40, 1)
        out.write(end_header)
    return path
