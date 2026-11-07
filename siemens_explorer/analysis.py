"""Bounded signal and Cartesian quick-look utilities for Siemens Twix data.

These functions deliberately avoid sequence-specific reconstruction. A Cartesian
preview places acquired samples at their MDH counters, averages repeated samples,
leaves missing samples zero, and optionally transforms the X, Y, and Z axes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from .reconstruction import (NoiseCalibration, adaptive_weights, calibration_grid,
                             centered_ifft, coarse_response, interpolate_response)


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
    """Arrays for one Cartesian group, in Y/X or Z/Y/X order."""

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
    combination: str | None = None,
    ifft_axes: Iterable[str] = ("x", "y"),
    noise_covariance: np.ndarray | None = None,
) -> Preview:
    """Build a bounded, zero-filled Cartesian quick look for one counter group.

    ``kspace`` always contains the selected coil. ``image`` is its magnitude, or
    the root-sum-of-squares magnitude across coils when ``all_channels`` is true.
    Reflection is undone before placing samples. The recorded center column and
    center line locate k-space zero; no k-space interpolation, trajectory correction,
    oversampling removal, parallel imaging, or image orientation is applied.

    Display dimensions are inferred bounds, not a verified encoded matrix or a
    measure of scan completeness; phase oversampling and omitted outer lines
    cannot generally be recovered from counters alone. The caller must establish
    whether the acquisition actually uses Cartesian sampling.

    ``combination`` is single, rss, or adaptive. Adaptive estimates smooth channel
    responses on a bounded grid, then streams a second pass with noise-weighted
    matched-filter weights. A reader may provide noise_calibration(measurement,
    receiver_ids); otherwise equal uncorrelated noise is assumed explicitly.

    Only checked axes are transformed; X is readout, Y is line, Z is partition.
    Z requests a volume across partitions, never across separate 2D slices.
    Multiple slices, echoes, repetitions, etc. are rejected. Missing samples are
    zero-filled and exposed in ``coverage``. Repeated samples are averaged.
    """
    supplied = list(scans)
    selected = [scan for scan in supplied if scan.is_image]
    if not selected:
        raise ValueError("Select at least one imaging acquisition for a Cartesian preview.")
    if not isinstance(channel_index, (int, np.integer)) or channel_index < 0:
        raise ValueError("Channel index must be a non-negative integer.")
    if max_matrix < 1 or max_bytes < 1:
        raise ValueError("Preview limits must be positive.")
    combination = combination or ("rss" if all_channels else "single")
    if combination not in ("single", "rss", "adaptive"):
        raise ValueError("Choose single, rss, or adaptive channel combination.")
    names = tuple(ifft_axes)
    if len(set(names)) != len(names) or any(name not in ("x", "y", "z") for name in names):
        raise ValueError("Inverse FFT axes must be unique X, Y, or Z names in lower case.")
    names = tuple(name for name in ("x", "y", "z") if name in names)
    volume = "z" in names
    combine = combination != "single"

    group: dict[str, int] = {}
    for name in GROUP_COUNTERS:
        if name == "partition" and volume:
            continue
        values = sorted({int(getattr(scan, name, 0)) for scan in selected})
        if len(values) > 1:
            raise ValueError(f"Choose one {name} before reconstructing ({len(values)} values selected).")
        group[name] = values[0]

    channel_counts = {int(scan.channels) for scan in selected}
    if any(channel_index >= count for count in channel_counts):
        raise ValueError("The selected channel is unavailable in one or more acquisitions.")
    if combine and len(channel_counts) != 1:
        raise ValueError("Channel combination requires a consistent channel count across acquisitions.")
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
    nz, center_z = 1, 0
    if volume:
        centers_z = {int(getattr(scan, "center_partition", 0)) for scan in selected}
        partitions = {int(scan.partition) for scan in selected}
        if len(centers_z) != 1 or min(partitions) < 0 or min(centers_z) < 0:
            raise ValueError("Selected acquisitions have inconsistent or invalid k-space center partitions.")
        center_z = next(iter(centers_z))
        nz = max(2 * center_z, 2 * (max(partitions) - center_z + 1))
        if partitions == {0} and center_z == 0:
            nz = 1
    shape = (nz, ny, nx) if volume else (ny, nx)
    axes = tuple({"x": len(shape) - 1, "y": len(shape) - 2, "z": 0}[name] for name in names)
    if max(shape) > max_matrix:
        raise ValueError(f"Preview matrix {' × '.join(map(str, shape))} exceeds the {max_matrix} per-axis limit.")
    # FFT implementations can promote complex64 to complex128. Account for
    # inputs, shifted copies, counts, coverage, accumulator, and FFT temporaries.
    grid = calibration_grid(shape)
    channel_count = next(iter(channel_counts))
    estimated_bytes = int(np.prod(shape)) * (192 if combination == "adaptive" else 96) + samples * 24
    if combination == "adaptive":
        # Coarse maps/linear algebra, covariance calibration block, FFT and
        # interpolation temporaries, without allocating a full coil stack.
        estimated_bytes += channel_count * int(np.prod(grid)) * 96 + channel_count ** 2 * 96
        estimated_bytes += channel_count * 8192 * 16
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
        f"Cartesian quick look: inverse FFT axes {', '.join(name.upper() for name in names) or 'none'}; no trajectory, ramp-sampling, "
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
    duplicates = len(selected) - len({(int(scan.partition), int(scan.line)) if volume else int(scan.line) for scan in selected})
    if duplicates:
        warnings.append(f"Averaged {duplicates:,} repeated readouts at the same Cartesian position.")

    coverage = np.zeros(shape, dtype=bool)
    counts = np.zeros(shape, dtype=np.uint32)
    positions = []
    for scan, column in zip(selected, columns):
        y = int(scan.line) - center_y + center_out_y
        x = center_x - column
        if x < 0 or x + samples > nx or y < 0 or y >= ny:
            raise ValueError("Selected acquisition cannot be placed within the bounded Cartesian matrix.")
        row = (int(scan.partition) - center_z + nz // 2, y) if volume else (y,)
        if volume and not 0 <= row[0] < nz:
            raise ValueError("A partition cannot be placed within the bounded Cartesian volume.")
        position = (*row, slice(x, x + samples))
        positions.append(position)
        counts[position] += 1
        coverage[position] = True

    filled_fraction = float(coverage.mean())
    if filled_fraction < 1:
        warnings.append(f"{100 * (1 - filled_fraction):.1f}% of the displayed matrix is unacquired and zero-filled.")
    if any(bool(scan.reflected) for scan in selected):
        warnings.append("Reflected readouts were reversed before sample placement.")

    channels = list(range(channel_count)) if combine else [int(channel_index)]

    def read_coil(channel):
        kspace = np.zeros(shape, dtype=np.complex64)
        for scan, position in zip(selected, positions):
            signal = np.asarray(twix.read_signal(measurement, scan, channel), dtype=np.complex64)
            if signal.ndim != 1 or signal.size != samples:
                raise ValueError(f"Acquisition {scan.index} returned an unexpected readout shape.")
            if not np.isfinite(signal).all():
                raise ValueError(f"Acquisition {scan.index} contains non-finite complex samples.")
            if scan.reflected:
                signal = signal[::-1]
            kspace[position] += signal
        np.divide(kspace, counts, out=kspace, where=counts > 0)
        return kspace

    calibration = NoiseCalibration(noise_covariance, {
        "noise_source": "supplied noise covariance" if noise_covariance is not None else "equal uncorrelated noise assumption"
    })
    weights = None
    if combination == "adaptive":
        provider = getattr(twix, "noise_calibration", None)
        if noise_covariance is None and receiver_ids is not None and callable(provider):
            calibration = provider(measurement, receiver_ids)
        warnings.extend(calibration.warnings)
        if calibration.covariance is None:
            warnings.append("Adaptive weights assume equal, uncorrelated receiver noise; no measured noise covariance was applied.")
        responses = np.empty((len(channels), *grid), dtype=np.complex64)
        for channel in channels:
            kspace = read_coil(channel)
            responses[channel] = coarse_response(centered_ifft(kspace, axes), grid)
        weights = adaptive_weights(responses, calibration.covariance)
        del responses, kspace
        warnings.append("Adaptive combination uses estimated coarse channel responses and a regularized noise-weighted matched filter; optimal SNR depends on these estimates. Magnitudes retain relative signal units, not absolute SNR.")
        if len(names) < len(shape):
            warnings.append("Adaptive responses are estimated in the selected hybrid/k-space domain because some inverse FFT axes are off.")

    rss = np.zeros(shape, dtype=np.float64)
    coherent = np.zeros(shape, dtype=np.complex64) if weights is not None else None
    weight_energy = np.zeros(shape, dtype=np.float32) if weights is not None else None
    chosen = None
    for channel in channels:
        kspace = read_coil(channel)
        if channel == channel_index:
            chosen = kspace
        transformed = centered_ifft(kspace, axes)
        rss += np.square(np.abs(transformed))
        if weights is not None:
            weight = interpolate_response(weights[channel], shape)
            coherent += np.conj(weight) * transformed
            weight_energy += np.abs(weight) ** 2
    if chosen is None:
        raise ValueError("The selected channel is unavailable for the requested coil combination.")

    image = np.sqrt(rss).astype(np.float32)
    fallback = 0.0
    if weights is not None:
        valid = weight_energy > 1e-12
        np.divide(np.abs(coherent), np.sqrt(weight_energy), out=image, where=valid)
        fallback = float(np.mean(~valid))
        if fallback:
            warnings.append(f"RSS fallback at {100 * fallback:.2f}% of positions without usable adaptive response estimates.")
    return Preview(
        kspace=chosen,
        image=image,
        coverage=coverage,
        lines=np.arange(ny, dtype=np.int64) - center_out_y + center_y,
        warnings=warnings,
        channel_count=len(channels),
        metadata={
            **group,
            "shape": shape,
            "array_axes": ["z", "y", "x"] if volume else ["y", "x"],
            "ifft_axes": list(names),
            "partitions": (np.arange(nz) - nz // 2 + center_z).tolist() if volume else [group["partition"]],
            "selected_channel": int(channel_index),
            "selected_channel_id": receiver_ids[channel_index] if receiver_ids is not None else None,
            "channel_ids": list(receiver_ids) if receiver_ids is not None else None,
            "channel_ids_verified": receiver_ids is not None,
            "combination": {"single": "single-channel magnitude", "rss": "root-sum-of-squares", "adaptive": "adaptive noise-weighted"}[combination],
            "combination_method": combination,
            **(dict(calibration.metadata, noise_covariance_used=calibration.covariance is not None,
                    adaptive_grid=list(grid), adaptive_rss_fallback_fraction=fallback,
                    covariance_shrinkage=0.01, weight_normalization="unit Euclidean norm") if weights is not None else {}),
            "acquired_fraction": filled_fraction,
            "readouts": len(selected),
            "repeated_readouts": duplicates,
            "center_column": center_x,
            "center_line": center_y,
            "center_partition": center_z if volume else int(getattr(selected[0], "center_partition", 0)),
            "estimated_bytes": estimated_bytes,
            "inferred_matrix": True,
            "trajectory_verified": False,
            "sampling_completeness": "unknown",
        },
    )
