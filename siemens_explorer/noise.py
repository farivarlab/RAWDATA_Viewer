"""Bounded raw-noise matrices for inspecting a single receive channel.

Rows remain individual acquisitions in their supplied order. The ``Preview``
container is shared with the image view, but its ``image`` array is only sample
magnitude here: no Fourier transform or spatial reconstruction is performed.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from .analysis import Preview


def build_noise_preview(
    twix: Any,
    measurement: Any,
    scans: Iterable[Any],
    channel_index: int = 0,
    max_readouts: int = 4096,
    max_bytes: int = 128 * 1024 * 1024,
) -> Preview:
    """Read a bounded matrix of ``NOISEADJSCAN`` samples for one channel.

    Non-noise records are excluded. Repeated line counters are independent
    rows, and reflection flags do not reverse noise samples. Bounds and channel
    identities are checked before signal payloads are read. Read/cancellation
    exceptions from the supplied reader propagate to the caller.
    """
    for name, value in (("Channel index", channel_index), ("Readout limit", max_readouts), ("Memory limit", max_bytes)):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
            raise ValueError(f"{name} must be an integer.")
    channel_index, max_readouts, max_bytes = int(channel_index), int(max_readouts), int(max_bytes)
    if channel_index < 0:
        raise ValueError("Channel index must be non-negative.")
    if max_readouts < 1 or max_bytes < 1:
        raise ValueError("Noise preview limits must be positive.")

    supplied = list(scans)
    noise = [scan for scan in supplied if "NOISEADJSCAN" in getattr(scan, "flags", ())]
    if not noise:
        raise ValueError("Select at least one noise acquisition (NOISEADJSCAN).")
    selected = noise[:max_readouts]
    samples = int(selected[0].samples)
    if samples < 1 or any(int(scan.samples) < 1 or int(scan.channels) < 1 for scan in selected):
        raise ValueError("Noise acquisitions must contain complex samples and receive channels.")
    if any(int(scan.samples) != samples for scan in selected):
        raise ValueError("Select noise acquisitions with one readout sample count.")
    if any(channel_index >= int(scan.channels) for scan in selected):
        raise ValueError("The selected channel is unavailable in one or more noise acquisitions.")

    rows = len(selected)
    # Raw complex64, magnitude float32, coverage bool, acquisition IDs, and a
    # conservative per-readout allowance for reader/conversion temporaries.
    estimated_bytes = rows * samples * 13 + rows * 8 + samples * 32
    if estimated_bytes > max_bytes:
        raise ValueError(
            f"Noise preview needs approximately {estimated_bytes / 1024**2:.1f} MiB; "
            f"the limit is {max_bytes / 1024**2:.1f} MiB. Select fewer noise readouts."
        )

    physical_id = None
    identity_verified = False
    channel_id_reader = getattr(twix, "channel_ids", None)
    if callable(channel_id_reader):
        for scan in selected:
            ids = tuple(int(value) for value in channel_id_reader(measurement, scan))
            if len(ids) != int(scan.channels) or len(set(ids)) != len(ids) or any(value < 0 for value in ids):
                raise ValueError(f"Acquisition {scan.index} has invalid or duplicate receiver channel IDs.")
            current_id = ids[channel_index]
            if not identity_verified:
                physical_id, identity_verified = current_id, True
            elif current_id != physical_id:
                raise ValueError(
                    "Selected noise acquisitions have different physical receiver channel IDs "
                    "at the selected channel position. Select a consistent receive channel."
                )

    raw = np.empty((rows, samples), dtype=np.complex64)
    non_finite = 0
    for row, scan in enumerate(selected):
        values = np.asarray(twix.read_signal(measurement, scan, channel_index), dtype=np.complex64)
        if values.ndim != 1 or values.size != samples:
            raise ValueError(f"Acquisition {scan.index} returned an unexpected noise readout shape.")
        non_finite += int(np.count_nonzero(~np.isfinite(values)))
        raw[row] = values

    warnings = ["Raw noise samples in acquisition order; no image reconstruction is applied."]
    if len(noise) > rows:
        warnings.append(f"Showing the first {rows:,} of {len(noise):,} noise readouts (display limit).")
    if len(supplied) > len(noise):
        warnings.append(f"Excluded {len(supplied) - len(noise):,} non-noise acquisitions.")
    if not identity_verified:
        warnings.append("Physical receiver-channel IDs could not be verified; the selected channel uses its ordinal position.")
    if non_finite:
        warnings.append(f"Retained {non_finite:,} non-finite complex samples in the raw arrays.")

    return Preview(
        kspace=raw,
        image=np.abs(raw),
        coverage=np.ones(raw.shape, dtype=bool),
        lines=np.asarray([int(scan.index) for scan in selected], dtype=np.int64),
        warnings=warnings,
        channel_count=1,
        metadata={
            "display_kind": "noise",
            "selected_channel": channel_index,
            "selected_channel_id": physical_id,
            "channel_ids_verified": identity_verified,
            "total_noise_readouts": len(noise),
            "shown_noise_readouts": rows,
            "readouts": rows,
            "shape": raw.shape,
            "estimated_bytes": estimated_bytes,
            "non_finite_samples": non_finite,
            "reconstruction": "none",
            "combination": "raw single-channel noise samples",
        },
    )
