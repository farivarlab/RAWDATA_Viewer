"""Small, memory-bounded helpers for the Cartesian inspection preview.

Adaptive combination uses an estimated spatial matched filter (Roemer weights),
not a sequence-specific or parallel-imaging reconstruction. The covariance and
coarse channel-response estimates determine how close it is to optimal SNR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class NoiseCalibration:
    covariance: np.ndarray | None = None
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def centered_ifft(values, axes):
    """Transform only the named array axes, leaving every other axis untouched."""
    axes = tuple(axes)
    if not axes:
        return np.asarray(values)
    return np.fft.fftshift(
        np.fft.ifftn(np.fft.ifftshift(values, axes=axes), axes=axes), axes=axes,
    )


def calibration_grid(shape):
    return tuple(min(size, 16 if len(shape) == 3 and axis == 0 else 32)
                 for axis, size in enumerate(shape))


def coarse_response(values, shape):
    """Complex box averages on a small grid, retaining physical grid centers."""
    result = np.asarray(values, dtype=np.complex64)
    for axis, size in enumerate(shape):
        if result.shape[axis] == size:
            continue
        edges = np.linspace(0, result.shape[axis], size + 1, dtype=int)
        result = np.add.reduceat(result, edges[:-1], axis=axis)
        divisor = np.diff(edges).reshape(tuple(size if i == axis else 1 for i in range(result.ndim)))
        result = (result / divisor).astype(np.complex64)
    return result


def interpolate_response(values, shape):
    """Multilinear interpolation from box centers; clamp outside the end bins."""
    result = values
    for axis, size in enumerate(shape):
        small = result.shape[axis]
        if small == size:
            continue
        edges = np.linspace(0, size, small + 1, dtype=int)
        centers = (edges[:-1] + edges[1:] - 1) / 2
        coordinate = np.interp(np.arange(size), centers, np.arange(small))
        lower = np.floor(coordinate).astype(int)
        upper = np.minimum(lower + 1, small - 1)
        fraction = (coordinate - lower).astype(np.float32)
        fraction = fraction.reshape(tuple(size if i == axis else 1 for i in range(result.ndim)))
        result = np.take(result, lower, axis=axis) * (1 - fraction) + np.take(result, upper, axis=axis) * fraction
    return result


def adaptive_weights(responses, covariance=None):
    """Return unit-length, noise-weighted matched-filter weights on a coarse grid.

    w = inverse(Psi) s / ||inverse(Psi) s||. The output is w^H m, so arbitrary
    receiver phases are aligned. Unit Euclidean norm preserves relative signal
    units; the combined magnitude is not an absolute SNR map.
    """
    responses = np.asarray(responses, dtype=np.complex64)
    channels = responses.shape[0]
    flat = responses.reshape(channels, -1).copy()
    reference = int(np.argmax(np.sum(np.abs(flat).astype(np.float64) ** 2, axis=1)))
    # Remove the object's common phase before interpolating the channel weights.
    flat *= np.exp(-1j * np.angle(flat[reference]))
    if covariance is None:
        weighted = flat
    else:
        matrix = np.asarray(covariance, dtype=np.complex128)
        if matrix.shape != (channels, channels) or not np.isfinite(matrix).all():
            raise ValueError("Noise covariance must be a finite channel-by-channel matrix.")
        matrix = (matrix + matrix.conj().T) / 2
        scale = float(np.trace(matrix).real / channels)
        if scale <= 0:
            raise ValueError("Noise covariance has no positive variance.")
        # Shrinkage prevents unstable weights from limited or redundant coils.
        matrix = 0.99 * matrix / scale + 0.01 * np.eye(channels)
        eigenvalues, vectors = np.linalg.eigh(matrix)
        eigenvalues = np.maximum(eigenvalues, max(float(eigenvalues.max()), 1) * 1e-6)
        weighted = vectors @ ((vectors.conj().T @ flat) / eigenvalues[:, None])
    norm = np.sqrt(np.sum(np.abs(weighted) ** 2, axis=0))
    np.divide(weighted, norm, out=weighted, where=norm > 0)
    return weighted.reshape(responses.shape).astype(np.complex64)


def estimate_noise_covariance(twix, measurement, scans, receiver_ids, *, max_samples=8192, max_readouts=32):
    """Stream matching NOISEADJSCAN samples, aligning by physical channel ID.

    One acquisition is retained at a time. A complex sample is an observation;
    the pooled channel mean is removed before forming the unbiased covariance.
    """
    receiver_ids = tuple(receiver_ids)
    channels = len(receiver_ids)
    if not channels or len(set(receiver_ids)) != channels or max_samples < 2 or max_readouts < 1:
        raise ValueError("Invalid noise-calibration limits or receiver IDs.")
    cross = np.zeros((channels, channels), dtype=np.complex128)
    total = np.zeros(channels, dtype=np.complex128)
    count = readouts = 0
    for scan in scans:
        if "NOISEADJSCAN" not in scan.flags or count >= max_samples or readouts >= max_readouts:
            continue
        ids = tuple(twix.channel_ids(measurement, scan))
        if len(set(ids)) != len(ids) or not set(receiver_ids).issubset(ids):
            continue
        length = min(int(scan.samples), max_samples - count)
        # Bound malformed/exceptionally large readouts before requesting payload.
        if scan.samples > 65536 or scan.channels > 4096 or length < 1:
            continue
        block = np.empty((channels, length), dtype=np.complex128)
        for channel, physical_id in enumerate(receiver_ids):
            signal = np.asarray(twix.read_signal(measurement, scan, ids.index(physical_id)))
            if signal.ndim != 1 or signal.size != scan.samples or not np.isfinite(signal).all():
                raise ValueError("Noise calibration contains invalid complex samples.")
            block[channel] = signal[:length]
        cross += block @ block.conj().T
        total += block.sum(axis=1)
        count += length
        readouts += 1
    if count < max(32, channels + 1):
        return NoiseCalibration(warnings=["No sufficient matching noise calibration; adaptive combination assumes equal, uncorrelated receiver noise."])
    covariance = (cross - np.outer(total, total.conj()) / count) / (count - 1)
    if not np.isfinite(covariance).all() or float(np.trace(covariance).real) <= 0:
        raise ValueError("Noise calibration has no finite positive variance.")
    return NoiseCalibration(covariance, {
        "noise_source": "measured NOISEADJSCAN",
        "noise_measurement_index": getattr(measurement, "index", None),
        "noise_measurement_id": getattr(measurement, "id", None),
        "noise_samples": count, "noise_readouts": readouts,
    })
