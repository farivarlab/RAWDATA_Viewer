"""Bounded signal and Cartesian quick-look utilities for Siemens Twix data.

These functions deliberately avoid sequence-specific reconstruction. A Cartesian
preview places acquired samples at their MDH counters, averages repeated samples,
leaves missing samples zero, and performs a centered two-dimensional inverse FFT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


COUNTERS = (
    "line", "partition", "slice", "echo", "repetition", "set", "average", "phase", "segment",
    "ida", "idb", "idc", "idd", "ide",
)
GROUP_COUNTERS = tuple(counter for counter in COUNTERS if counter != "line")
TABLE_COLUMNS = (
    "scan", "line", "partition", "slice", "echo", "repetition", "set", "average", "phase",
    "segment", "ida", "idb", "idc", "idd", "ide", "samples", "channels", "is_image", "flags", "timestamp_ms", "scan_counter",
    "offset", "length", "center_column", "center_line", "center_partition", "reflected", "eval_mask",
)


@dataclass
class Preview:
    """Arrays and qualifications for a single two-dimensional Cartesian group."""

    kspace: np.ndarray
    image: np.ndarray
    coverage: np.ndarray
    lines: np.ndarray
    warnings: list[str] = field(default_factory=list)
    channel_count: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


def scans_to_frame(scans: Iterable[Any]) -> pd.DataFrame:
    """Return a scalar acquisition table whose index remains the parser scan ID."""
    rows = []
    indices = []
    for scan in scans:
        row = {name: getattr(scan, name, 0) for name in TABLE_COLUMNS}
        row["scan"] = int(scan.index)
        row["flags"] = ", ".join(getattr(scan, "flags", ()))
        row["is_image"] = bool(scan.is_image)
        row["reflected"] = bool(getattr(scan, "reflected", False))
        rows.append(row)
        indices.append(int(scan.index))
    return pd.DataFrame(rows, columns=TABLE_COLUMNS, index=pd.Index(indices, name="acquisition"))


def select_scans(scans: Iterable[Any], filters: Mapping[str, Any] | None = None) -> list[Any]:
    """Filter by exact MDH counter values; ``None`` and ``'All'`` mean unfiltered.

    A filter value may be a scalar or a list/tuple/set of accepted scalar values.
    Unknown fields raise instead of silently returning a misleading selection.
    """
    allowed = set(COUNTERS) | {"index", "samples", "channels", "is_image", "scan_counter"}
    active: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        if key not in allowed:
            raise ValueError(f"Unknown acquisition filter: {key}")
        if value is None or (isinstance(value, str) and value == "All"):
            continue
        active[key] = value

    def matches(scan: Any) -> bool:
        for key, value in active.items():
            actual = getattr(scan, key)
            if isinstance(value, (list, tuple, set, frozenset, np.ndarray)):
                if actual not in value:
                    return False
            elif actual != value:
                return False
        return True

    return [scan for scan in scans if matches(scan)]


def build_cartesian_preview(
    twix: Any,
    measurement: Any,
    scans: Iterable[Any],
    channel_index: int = 0,
    max_matrix: int = 2048,
    max_bytes: int = 128 * 1024 * 1024,
    all_channels: bool = False,
) -> Preview:
    """Build a bounded, zero-filled Cartesian quick look for one counter group.

    ``kspace`` always contains the selected coil. ``image`` is its magnitude, or
    the root-sum-of-squares magnitude across coils when ``all_channels`` is true.
    Reflection is undone before placing samples. The recorded center column and
    center line locate k-space zero; no interpolation, trajectory correction,
    oversampling removal, parallel imaging, or image orientation is applied.

    Display dimensions are inferred bounds, not a verified encoded matrix or a
    measure of scan completeness; phase oversampling and omitted outer lines
    cannot generally be recovered from counters alone. The caller must establish
    whether the acquisition actually uses Cartesian sampling.

    Multiple slices, echoes, repetitions, etc. are rejected: the caller must first
    select one group. Missing Cartesian samples remain zero and are exposed in
    ``coverage``. Repeated samples within that group are averaged explicitly.
    """
    supplied = list(scans)
    selected = [scan for scan in supplied if scan.is_image]
    if not selected:
        raise ValueError("Select at least one imaging acquisition for a Cartesian preview.")
    if not isinstance(channel_index, (int, np.integer)) or channel_index < 0:
        raise ValueError("Channel index must be a non-negative integer.")
    if max_matrix < 1 or max_bytes < 1:
        raise ValueError("Preview limits must be positive.")

    group: dict[str, int] = {}
    for name in GROUP_COUNTERS:
        values = sorted({int(getattr(scan, name, 0)) for scan in selected})
        if len(values) > 1:
            raise ValueError(f"Choose one {name} before reconstructing ({len(values)} values selected).")
        group[name] = values[0]

    channel_counts = {int(scan.channels) for scan in selected}
    if any(channel_index >= count for count in channel_counts):
        raise ValueError("The selected channel is unavailable in one or more acquisitions.")
    if all_channels and len(channel_counts) != 1:
        raise ValueError("Root-sum-of-squares requires a consistent channel count across acquisitions.")
    if len({int(scan.samples) for scan in selected}) != 1:
        raise ValueError("Select acquisitions with one readout sample count for a Cartesian preview.")

    centers = {int(scan.center_line) for scan in selected}
    if len(centers) != 1:
        raise ValueError("Selected acquisitions have inconsistent k-space center lines.")
    center_y = next(iter(centers))
    line_numbers = np.asarray([int(scan.line) for scan in selected], dtype=np.int64)
    columns = [int(scan.center_column) for scan in selected]
    samples = int(selected[0].samples)
    if samples < 1 or center_y < 0 or line_numbers.min() < 0:
        raise ValueError("Invalid negative counters or empty readout in selected acquisitions.")
    if any(column < 0 or column >= samples for column in columns):
        raise ValueError("A k-space center column falls outside the acquired readout.")

    # Symmetric extent around MDH k-space zero, large enough for every sample.
    # If a positive MDH center predicts a larger matrix, retain its missing rows
    # as zeros rather than stretching the acquired rows to fill the display.
    left = max(columns)
    right = max(samples - column for column in columns)
    nx = max(2 * left, 2 * right)
    center_x = nx // 2
    ny = max(2 * center_y, 2 * (int(line_numbers.max()) - center_y + 1))
    center_out_y = ny // 2
    if nx > max_matrix or ny > max_matrix:
        raise ValueError(f"Preview matrix {ny} × {nx} exceeds the {max_matrix} per-axis limit.")
    # FFT implementations can promote complex64 to complex128. Account for
    # inputs, shifted copies, counts, coverage, accumulator, and FFT temporaries.
    estimated_bytes = nx * ny * 96 + samples * 24
    if estimated_bytes > max_bytes:
        raise ValueError(
            f"Preview needs approximately {estimated_bytes / 1024**2:.1f} MiB; "
            f"the limit is {max_bytes / 1024**2:.1f} MiB. Select a smaller matrix."
        )

    # Equal channel counts do not guarantee equal receiver coils: compare the
    # physical MDH IDs as well as their ordinal positions before combining any
    # lines. A changed order is rejected explicitly rather than mixing coils.
    receiver_ids = None
    channel_id_reader = getattr(twix, "channel_ids", None)
    if callable(channel_id_reader):
        for scan in selected:
            ids = tuple(int(value) for value in channel_id_reader(measurement, scan))
            if len(ids) != scan.channels or len(set(ids)) != len(ids):
                raise ValueError(f"Acquisition {scan.index} has invalid or duplicate receiver channel IDs.")
            if receiver_ids is None:
                receiver_ids = ids
            elif ids != receiver_ids:
                raise ValueError(
                    "Selected acquisitions have different receiver channel IDs or channel order. "
                    "Select a group with consistent physical receive channels before building a preview."
                )

    warnings = [
        "Cartesian quick look: centered 2D inverse FFT; no trajectory, ramp-sampling, "
        "parallel-imaging, oversampling, or image-orientation correction.",
        "Sampling trajectory is not verified. Displayed matrix bounds are inferred from "
        "MDH centers and acquired counters; they do not establish the encoded matrix, "
        "phase oversampling, or sampling completeness.",
    ]
    omitted = len(supplied) - len(selected)
    if receiver_ids is None:
        warnings.append("The supplied reader cannot verify physical receiver-channel IDs; channels use ordinal positions.")
    if omitted:
        warnings.append(f"Excluded {omitted:,} non-imaging acquisitions.")
    duplicates = len(selected) - len(set(line_numbers.tolist()))
    if duplicates:
        warnings.append(f"Averaged {duplicates:,} repeated readouts at the same line counter.")

    coverage = np.zeros((ny, nx), dtype=bool)
    counts = np.zeros((ny, nx), dtype=np.uint32)
    positions = []
    for scan, column in zip(selected, columns):
        y = int(scan.line) - center_y + center_out_y
        x = center_x - column
        if x < 0 or x + samples > nx or y < 0 or y >= ny:
            raise ValueError("Selected acquisition cannot be placed within the bounded Cartesian matrix.")
        positions.append((y, x))
        counts[y, x:x + samples] += 1
        coverage[y, x:x + samples] = True

    filled_fraction = float(coverage.mean())
    if filled_fraction < 1:
        warnings.append(f"{100 * (1 - filled_fraction):.1f}% of the displayed matrix is unacquired and zero-filled.")
    if any(bool(scan.reflected) for scan in selected):
        warnings.append("Reflected readouts were reversed before sample placement.")

    channels = list(range(next(iter(channel_counts)))) if all_channels else [int(channel_index)]
    rss = np.zeros((ny, nx), dtype=np.float64)
    chosen = None
    for channel in channels:
        kspace = np.zeros((ny, nx), dtype=np.complex64)
        for scan, (y, x) in zip(selected, positions):
            signal = np.asarray(twix.read_signal(measurement, scan, channel), dtype=np.complex64)
            if signal.ndim != 1 or signal.size != samples:
                raise ValueError(f"Acquisition {scan.index} returned an unexpected readout shape.")
            if not np.isfinite(signal).all():
                raise ValueError(f"Acquisition {scan.index} contains non-finite complex samples.")
            if scan.reflected:
                signal = signal[::-1]
            kspace[y, x:x + samples] += signal
        np.divide(kspace, counts, out=kspace, where=counts > 0)
        if channel == channel_index:
            chosen = kspace
        transformed = np.fft.fftshift(np.fft.ifft2(np.fft.ifftshift(kspace)))
        rss += np.square(np.abs(transformed))
    if chosen is None:
        raise ValueError("The selected channel is unavailable for the requested coil combination.")

    image = np.sqrt(rss).astype(np.float32)
    return Preview(
        kspace=chosen,
        image=image,
        coverage=coverage,
        lines=np.arange(ny, dtype=np.int64) - center_out_y + center_y,
        warnings=warnings,
        channel_count=len(channels),
        metadata={
            **group,
            "shape": (ny, nx),
            "selected_channel": int(channel_index),
            "selected_channel_id": receiver_ids[channel_index] if receiver_ids is not None else None,
            "channel_ids": list(receiver_ids) if receiver_ids is not None else None,
            "channel_ids_verified": receiver_ids is not None,
            "combination": "root-sum-of-squares" if all_channels else "single-channel magnitude",
            "acquired_fraction": filled_fraction,
            "readouts": len(selected),
            "repeated_readouts": duplicates,
            "center_column": center_x,
            "center_line": center_y,
            "estimated_bytes": estimated_bytes,
            "inferred_matrix": True,
            "trajectory_verified": False,
            "sampling_completeness": "unknown",
        },
    )
