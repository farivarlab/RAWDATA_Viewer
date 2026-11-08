"""Streaming, atomic ISMRMRD HDF5 export of indexed Twix ADC acquisitions.

Acquired samples remain unchanged. Unsupported sync/PMU records are counted in
the XML notes; vendor buffers and ADC MDHs are preserved for later inspection.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile

import numpy as np


COUNTER_MAP = {"line": "kspace_encode_step_1", "partition": "kspace_encode_step_2",
               "average": "average", "slice": "slice", "echo": "contrast", "phase": "phase",
               "repetition": "repetition", "set": "set", "segment": "segment"}


def _number(protocol, keys, default=0):
    for key in keys:
        value = protocol.get(key)
        if value is not None:
            match = re.match(r"\s*([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)", str(value))
            if match:
                result = float(match[1])
                if math.isfinite(result) and result >= 0:
                    return result
    return default


def _xml_header(m, scans, physical_ids, complete, skipped):
    """Describe inferred bounds and retained vendor facts without inventing geometry."""
    from ismrmrd import xsd
    p = m.protocol
    imaging = [s for s in scans if s.is_image] or scans
    nx = max(s.samples for s in imaging)
    cy = max(s.center_line for s in imaging)
    cz = max(s.center_partition for s in imaging)
    ny = max(1, 2 * cy, max(s.line for s in imaging) + 1)
    nz = max(1, 2 * cz, max(s.partition for s in imaging) + 1)
    rx = max(1, int(_number(p, ("sKSpace.lBaseResolution", "lBaseResolution"), nx)))
    ry = max(1, int(_number(p, ("sKSpace.lPhaseEncodingLines", "lPhaseEncodingLines"), ny)))
    fovx = _number(p, ("sSliceArray.asSlice[0].dReadoutFOV", "dReadoutFOV", "RoFOV"))
    fovy = _number(p, ("sSliceArray.asSlice[0].dPhaseFOV", "dPhaseFOV", "PeFOV"))
    fovz = _number(p, ("sSliceArray.asSlice[0].dThickness", "dThickness"))
    limits = {}
    for source, target in COUNTER_MAP.items():
        values = [int(getattr(s, source)) for s in imaging]
        limits[target.replace("encode", "encoding")] = xsd.limitType(
            minimum=min(values), maximum=max(values),
            center=min(max(values), max(min(values), cy if source == "line" else cz if source == "partition" else min(values))))
    limits["kspace_encoding_step_1"] = xsd.limitType(minimum=0, maximum=ny - 1, center=cy)
    limits["kspace_encoding_step_2"] = xsd.limitType(minimum=0, maximum=nz - 1, center=cz)
    if max(nx, ny, nz, rx, ry) > 65535:
        raise ValueError("Inferred matrix bounds exceed ISMRMRD's 16-bit matrix dimensions.")
    for index, source in enumerate(("ida", "idb", "idc", "idd", "ide")):
        values = [int(getattr(s, source, 0)) for s in imaging]
        limits[f"user_{index}"] = xsd.limitType(minimum=min(values), maximum=max(values), center=min(values))
    trajectory_code = int(_number(p, ("sKSpace.ucTrajectory", "ucTrajectory")))
    trajectory = {1: xsd.trajectoryType.CARTESIAN, 2: xsd.trajectoryType.RADIAL,
                  4: xsd.trajectoryType.SPIRAL}.get(trajectory_code, xsd.trajectoryType.OTHER)
    notes = {
        "scope": "All indexed ADC acquisitions of the selected measurement, independent of display filters",
        "index_complete": bool(complete), "skipped_non_adc_records": skipped,
        "matrix": "Encoded bounds inferred from ADC samples, MDH centers and counters; recon matrix uses protocol values when present",
        "unknown_values": "Missing FOV, frequency, dwell or geometry are zero; no trajectories are synthesized",
        "noise_dwell": "Unknown (zero); sequence-specific noise timing is not inferred",
        "user_int": ["Siemens eval mask low int32", "Siemens eval mask high int32", "source acquisition index", "valid MDH quaternion"],
        "idx_user": ["IDa", "IDb", "IDc", "IDd", "IDe", "center line", "center partition"],
        "channel_mapping": physical_ids,
        "vendor_headers": "SiemensBuffer_* XML base64 fields and siemens_mdh uint16 array (original little-endian ADC MDH bytes)",
    }
    header = xsd.ismrmrdHeader(
        experimentalConditions=xsd.experimentalConditionsType(H1resonanceFrequency_Hz=int(_number(p, ("sTXSPEC.asNucleusInfo[0].lFrequency", "lFrequency")))),
        acquisitionSystemInformation=xsd.acquisitionSystemInformationType(systemVendor="Siemens", receiverChannels=len(physical_ids),
            coilLabel=[xsd.coilLabelType(coilNumber=i, coilName=f"Siemens receive ID {value}") for i, value in enumerate(physical_ids)]),
        encoding=[xsd.encodingType(
            encodedSpace=xsd.encodingSpaceType(matrixSize=xsd.matrixSizeType(x=nx, y=ny, z=nz),
                fieldOfView_mm=xsd.fieldOfViewMm(x=fovx * nx / rx, y=fovy * ny / ry, z=fovz)),
            reconSpace=xsd.encodingSpaceType(matrixSize=xsd.matrixSizeType(x=rx, y=ry, z=nz),
                fieldOfView_mm=xsd.fieldOfViewMm(x=fovx, y=fovy, z=fovz)),
            encodingLimits=xsd.encodingLimitsType(**limits), trajectory=trajectory)],
        userParameters=xsd.userParametersType(
            userParameterString=[xsd.userParameterStringType(name="RawMRIDataExplorer_Export", value=json.dumps(notes)),
                                 xsd.userParameterStringType(name="SiemensProtocolName", value=m.protocol_name)],
            userParameterLong=[xsd.userParameterLongType(name="SiemensMeasurementID", value=int(m.id))],
            userParameterBase64=[xsd.userParameterBase64Type(name=f"SiemensBuffer_{name}", value=text.encode("utf-8")) for name, text in m.buffers.items()] ))
    position = p.get("tPatientPosition", p.get("PatientPosition"))
    if position in {item.value for item in xsd.patientPositionType}:
        header.measurementInformation = xsd.measurementInformationType(measurementID=str(m.id), protocolName=m.protocol_name,
                                                                       patientPosition=xsd.patientPositionType(position))
    return xsd.ToXML(header, encoding="utf-8")


def export_ismrmrd(twix, measurement, scans, path, *, index_complete=True, cancel=None, progress=None,
                   max_acquisition_bytes=64 * 1024 * 1024):
    """Stream the supplied index's ADCs to the ISMRMRD /dataset group.

    Display filters, REFLECT correction, FFT settings, and channel combination
    do not alter exported samples. Physical IDs map to consistent MRD channel
    numbers; XML notes and original MDHs preserve the vendor-specific details.

    cancel is a threading.Event; progress(fraction, count) may also return
    False to abort with InterruptedError. Validation occurs before payload reads.
    A same-directory temporary HDF5 file is closed before atomic replacement,
    so failure/cancellation preserves any existing destination. Returns a dict
    with path, exported acquisitions, and the number of skipped non-ADC records.
    """
    import ismrmrd
    path = Path(path).expanduser().resolve()
    source = getattr(twix, "path", None)
    if source is not None and (path == Path(source).resolve() or (path.exists() and path.samefile(source))):
        raise ValueError("Exports cannot overwrite the source Twix file.")
    supplied = list(scans)
    adc = [s for s in supplied if s.samples > 0 and s.channels > 0 and "SYNCDATA" not in s.flags]
    if not adc:
        raise ValueError("No indexed ADC acquisitions to export.")

    last_reported = -1.0

    def checkpoint(fraction, count):
        nonlocal last_reported
        if cancel is not None and cancel.is_set():
            raise InterruptedError("ISMRMRD export cancelled")
        if progress is not None and (fraction - last_reported >= 0.0025 or fraction == 1):
            last_reported = fraction
            if progress(fraction, count) is False:
                raise InterruptedError("ISMRMRD export cancelled")

    receivers = set()
    for index, scan in enumerate(adc):
        checkpoint(0.1 * index / len(adc), index)
        if not 0 < scan.samples <= 65535 or not 0 < scan.channels <= 1024:
            raise ValueError("ISMRMRD requires at most 65,535 samples and 1,024 channels per acquisition.")
        if scan.samples * scan.channels * 32 > max_acquisition_bytes:
            raise ValueError("One acquisition exceeds the ISMRMRD export working-memory limit.")
        if not 0 <= scan.center_column < scan.samples or scan.cutoff_pre + scan.cutoff_post > scan.samples:
            raise ValueError("Invalid center sample or ADC discard counts.")
        ids = tuple(twix.channel_ids(measurement, scan))
        if len(ids) != scan.channels or len(set(ids)) != len(ids):
            raise ValueError("Invalid physical receiver IDs in an acquisition.")
        receivers.update(ids)
        if len(receivers) > 1024:
            raise ValueError("ISMRMRD supports at most 1,024 distinct receiver channels.")
    physical_ids = sorted(receivers)
    receiver_map = {value: index for index, value in enumerate(physical_ids)}
    xml = _xml_header(measurement, adc, physical_ids, index_complete, len(supplied) - len(adc))
    # Siemens stores ADC dwell in ns; MRD sample_time_us expects microseconds.
    dwell = _number(measurement.protocol, ("sRXSPEC.alDwellTime[0]", "alDwellTime")) / 1000
    flag_names = {
        "NOISEADJSCAN": "ACQ_IS_NOISE_MEASUREMENT", "PHASCOR": "ACQ_IS_PHASECORR_DATA",
        "PHASESTABSCAN": "ACQ_IS_PHASE_STABILIZATION", "REFPHASESTABSCAN": "ACQ_IS_PHASE_STABILIZATION_REFERENCE",
        "RTFEEDBACK": "ACQ_IS_RTFEEDBACK_DATA", "HPFEEDBACK": "ACQ_IS_HPFEEDBACK_DATA",
        "REFLECT": "ACQ_IS_REVERSE", "LASTSCANINMEAS": "ACQ_LAST_IN_MEASUREMENT",
        "FIRSTSCANINSLICE": "ACQ_FIRST_IN_SLICE", "LASTSCANINSLICE": "ACQ_LAST_IN_SLICE",
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=path.stem + "-", suffix=".h5.tmp", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
        dataset = ismrmrd.Dataset(str(temporary), "dataset", mode="w")
        try:
            dataset.write_xml_header(xml)
            for index, scan in enumerate(adc):
                checkpoint(0.1 + 0.9 * index / len(adc), index)
                acq = ismrmrd.Acquisition()
                acq.resize(scan.samples, scan.channels)
                acq.version = 1
                acq.available_channels = len(physical_ids)
                acq.scan_counter = int(scan.scan_counter)
                acq.center_sample = scan.center_column
                acq.discard_pre, acq.discard_post = scan.cutoff_pre, scan.cutoff_post
                acq.sample_time_us = 0 if "NOISEADJSCAN" in scan.flags else dwell
                for source_name, target in COUNTER_MAP.items():
                    setattr(acq.idx, target, int(getattr(scan, source_name)))
                for user, source_name in enumerate(("ida", "idb", "idc", "idd", "ide")):
                    acq.idx.user[user] = int(getattr(scan, source_name, 0))
                acq.idx.user[5], acq.idx.user[6] = scan.center_line, scan.center_partition
                acq.user_int[0] = ctypes.c_int32(scan.eval_mask & 0xffffffff).value
                acq.user_int[1] = ctypes.c_int32(scan.eval_mask >> 32).value
                acq.user_int[2] = scan.index
                for source_flag, target in flag_names.items():
                    if source_flag in scan.flags:
                        acq.setFlag(getattr(ismrmrd, target))
                if "PATREFANDIMASCAN" in scan.flags:
                    acq.setFlag(ismrmrd.ACQ_IS_PARALLEL_CALIBRATION_AND_IMAGING)
                elif "PATREFSCAN" in scan.flags:
                    acq.setFlag(ismrmrd.ACQ_IS_PARALLEL_CALIBRATION)
                mdh = twix.read_mdh(measurement, scan)
                acq.measurement_uid, _, acq.acquisition_time_stamp, pmu = struct.unpack_from("<4I", mdh, 4)
                acq.physiology_time_stamp[0] = pmu
                position_offset = 96 if measurement.format == "VB" else 100
                position = struct.unpack_from("<3f", mdh, position_offset)
                quaternion = np.asarray(struct.unpack_from("<4f", mdh, position_offset + 12))
                if np.isfinite(position).all():
                    acq.position[:] = position
                norm = np.linalg.norm(quaternion)
                if np.isfinite(norm) and norm > 1e-8:
                    quaternion /= norm
                    # Siemens stores WXYZ and exchanges read/phase relative to
                    # MRD's XYZW direction helper (as in the official converter).
                    phase, read, normal = ismrmrd.quaternion_to_directions(quaternion[[1, 2, 3, 0]])
                    acq.read_dir[:], acq.phase_dir[:], acq.slice_dir[:] = read, phase, normal
                    acq.user_int[3] = 1
                if measurement.format != "VB":
                    acq.patient_table_position[:] = struct.unpack_from("<3i", mdh, 24)
                ids = twix.channel_ids(measurement, scan)
                order = sorted(range(scan.channels), key=lambda channel: receiver_map[ids[channel]])
                for row, channel in enumerate(order):
                    checkpoint(0.1 + 0.9 * index / len(adc), index)
                    values = np.asarray(twix.read_signal(measurement, scan, channel), dtype=np.complex64)
                    if values.shape != (scan.samples,):
                        raise ValueError("Unexpected ADC payload shape during export.")
                    acq.data[row] = values
                    acq.setChannelActive(receiver_map[ids[channel]])
                dataset.append_acquisition(acq)
                dataset.append_array("siemens_mdh", np.frombuffer(mdh, dtype="<u2"))
        finally:
            dataset.close()
        checkpoint(1.0, len(adc))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"path": path, "acquisitions": len(adc), "skipped": len(supplied) - len(adc)}
