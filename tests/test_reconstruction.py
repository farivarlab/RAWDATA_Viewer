"""Independent FFT and phase/noise-aware combination references."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np

from test_analysis import Scan
from siemens_explorer.analysis import build_cartesian_preview
from siemens_explorer.reconstruction import (adaptive_weights, centered_ifft,
                                            estimate_noise_covariance)


class CoilReader:
    def __init__(self, signals, ids=None):
        self.signals = signals
        self.ids = ids or [7, 19]
        self.reads = 0

    def channel_ids(self, measurement, scan):
        return self.ids

    def read_signal(self, measurement, scan, channel):
        self.reads += 1
        return self.signals[scan.index][channel]


def coil_fixture(images):
    images = np.asarray(images)
    axes = tuple(range(1, images.ndim))
    kspace = np.fft.fftshift(np.fft.fftn(np.fft.ifftshift(images, axes=axes), axes=axes), axes=axes)
    scans, signals = [], {}
    if images.ndim == 3:
        for y in range(images.shape[-2]):
            scans.append(Scan(y, y, samples=images.shape[-1], channels=images.shape[0],
                              center_line=images.shape[-2] // 2, center_column=images.shape[-1] // 2))
            signals[y] = kspace[:, y]
    else:
        for z in range(images.shape[-3]):
            for y in range(images.shape[-2]):
                index = len(scans)
                scan = Scan(index, y, partition=z, samples=images.shape[-1], channels=images.shape[0],
                            center_line=images.shape[-2] // 2, center_column=images.shape[-1] // 2)
                scans.append(SimpleNamespace(**vars(scan), center_partition=images.shape[-3] // 2))
                signals[index] = kspace[:, z, y]
    return CoilReader(signals, list(range(images.shape[0]))), scans, kspace


class ReconstructionTests(unittest.TestCase):
    def test_each_xy_checkbox_transforms_only_its_axis(self):
        rng = np.random.default_rng(8)
        image = rng.normal(size=(4, 8)) + 1j * rng.normal(size=(4, 8))
        for names, axes in (((), ()), (("x",), (1,)), (("y",), (0,)), (("x", "y"), (0, 1))):
            reader, scans, kspace = coil_fixture([image])
            preview = build_cartesian_preview(reader, None, scans, ifft_axes=names)
            # Sequential 1D transforms form an independent reference.
            expected = kspace[0].copy()
            for axis in axes:
                expected = np.fft.fftshift(np.fft.ifft(np.fft.ifftshift(expected, axes=axis), axis=axis), axes=axis)
            np.testing.assert_allclose(preview.image, np.abs(expected), atol=1e-5)
            np.testing.assert_allclose(preview.kspace, kspace[0], atol=1e-5)
            self.assertEqual(set(preview.metadata["ifft_axes"]), set(names))

    def test_z_recovers_known_volume_and_does_not_mix_lines_from_partitions(self):
        image = np.zeros((4, 4, 8), complex)
        image[0, 1, 2], image[2, 3, 6] = 3 + 2j, 8 - 1j
        reader, scans, kspace = coil_fixture([image])
        preview = build_cartesian_preview(reader, None, scans, ifft_axes=("x", "y", "z"))
        np.testing.assert_allclose(preview.image, np.abs(image), atol=1e-6)
        np.testing.assert_allclose(preview.kspace, kspace[0], atol=1e-6)
        self.assertEqual(preview.metadata["repeated_readouts"], 0)
        self.assertEqual(preview.metadata["partitions"], [0, 1, 2, 3])
        self.assertEqual(preview.metadata["array_axes"], ["z", "y", "x"])

    def test_z_alone_and_xz_leave_y_in_kspace(self):
        rng = np.random.default_rng(80)
        image = rng.normal(size=(4, 4, 8))
        for names, axes in ((("z",), (0,)), (("x", "z"), (0, 2)), (("y", "z"), (0, 1))):
            reader, scans, kspace = coil_fixture([image])
            preview = build_cartesian_preview(reader, None, scans, ifft_axes=names)
            expected = kspace[0].copy()
            for axis in axes:
                expected = np.fft.fftshift(np.fft.ifft(np.fft.ifftshift(expected, axes=axis), axis=axis), axes=axis)
            np.testing.assert_allclose(preview.image, np.abs(expected), atol=1e-5)

    def test_z_rejects_mixed_2d_slices_and_center_partitions(self):
        reader, scans, _ = coil_fixture([np.ones((4, 4, 4))])
        scans[-1].slice = 1
        with self.assertRaisesRegex(ValueError, "one slice"):
            build_cartesian_preview(reader, None, scans, ifft_axes=("z",))
        scans[-1].slice = 0
        scans[-1].center_partition += 1
        with self.assertRaisesRegex(ValueError, "center partitions"):
            build_cartesian_preview(reader, None, scans, ifft_axes=("z",))
        self.assertEqual(reader.reads, 0)

    def test_volume_and_adaptive_memory_limits_precede_payload_reads(self):
        reader, scans, _ = coil_fixture([np.ones((4, 4, 4)), np.ones((4, 4, 4))])
        for kwargs in ({"max_bytes": 1}, {"max_matrix": 2}, {"combination": "adaptive", "max_bytes": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_cartesian_preview(reader, None, scans, ifft_axes=("x", "y", "z"), **kwargs)
        self.assertEqual(reader.reads, 0)

    def test_adaptive_aligns_opposing_complex_channel_phases(self):
        image = np.arange(32).reshape(4, 8) + 1
        reader, scans, kspace = coil_fixture([image, -2j * image])
        preview = build_cartesian_preview(reader, None, scans, combination="adaptive", channel_index=1)
        np.testing.assert_allclose(preview.image, np.sqrt(5) * image, rtol=2e-6, atol=2e-6)
        np.testing.assert_allclose(preview.kspace, kspace[1], atol=1e-5)
        self.assertEqual(preview.channel_count, 2)
        self.assertEqual(reader.reads, len(scans) * 4)
        self.assertFalse(preview.metadata["noise_covariance_used"])
        self.assertTrue(any("uncorrelated" in warning for warning in preview.warnings))

    def test_correlated_noise_weights_match_regularized_roemer_reference(self):
        response = np.array([1 + 0.5j, -0.8j])
        covariance = np.array([[1, 0.5j], [-0.5j, 9]])
        image = np.ones((4, 8)) * 3
        reader, scans, _ = coil_fixture([value * image for value in response])
        preview = build_cartesian_preview(reader, None, scans, combination="adaptive", noise_covariance=covariance)
        regularized = 0.99 * covariance / (np.trace(covariance).real / 2) + 0.01 * np.eye(2)
        weight = np.linalg.solve(regularized, response)
        weight /= np.linalg.norm(weight)
        expected = abs(weight.conj() @ response) * image
        np.testing.assert_allclose(preview.image, expected, rtol=2e-6)
        # This deliberately noisy second coil should have less influence.
        self.assertLess(abs(weight[1]), abs(weight[0]))
        self.assertTrue(preview.metadata["noise_covariance_used"])
        noise_gain = np.sqrt((weight.conj() @ covariance @ weight).real)
        rss_direction = response / np.linalg.norm(response)
        rss_snr = abs(rss_direction.conj() @ response) / np.sqrt((rss_direction.conj() @ covariance @ rss_direction).real)
        self.assertGreater(abs(weight.conj() @ response) / noise_gain, rss_snr)

    def test_singular_covariance_produces_finite_weights(self):
        weights = adaptive_weights(np.ones((2, 2, 2), complex), np.ones((2, 2)))
        self.assertTrue(np.isfinite(weights).all())
        np.testing.assert_allclose(np.sum(np.abs(weights) ** 2, axis=0), 1, atol=1e-6)

    def test_interpolated_weights_follow_spatially_varying_complex_responses(self):
        y, x = np.mgrid[-1:1:64j, -1:1:64j]
        object_image = (2 + np.exp(-3 * (x * x + y * y))) * np.exp(0.2j * (x + y))
        responses = np.array([(1.2 + 0.3 * x) * np.exp(0.3j * y),
                              (0.9 - 0.25 * x) * np.exp(1j * (1.5 + 0.2 * y))])
        covariance = np.array([[1, 0.1 + 0.2j], [0.1 - 0.2j, 3]])
        reader, scans, _ = coil_fixture(responses * object_image)
        preview = build_cartesian_preview(reader, None, scans, combination="adaptive", noise_covariance=covariance)
        matrix = 0.99 * covariance / 2 + 0.01 * np.eye(2)
        weights = np.linalg.solve(matrix, responses.reshape(2, -1)).reshape(responses.shape)
        weights /= np.sqrt(np.sum(np.abs(weights) ** 2, axis=0))
        expected = np.abs(np.sum(weights.conj() * responses * object_image, axis=0))
        np.testing.assert_allclose(preview.image, expected, rtol=0.01)

    def test_adaptive_zero_calibration_regions_use_rss_instead_of_creating_holes(self):
        # Complex box means cancel exactly, despite nonzero image energy.
        image = np.tile([1, -1], (64, 32))
        reader, scans, _ = coil_fixture([image, 2j * image])
        preview = build_cartesian_preview(reader, None, scans, combination="adaptive")
        np.testing.assert_allclose(preview.image, np.sqrt(5), atol=1e-6)
        self.assertGreater(preview.metadata["adaptive_rss_fallback_fraction"], 0.99)

    def test_adaptive_volume_and_partial_transform_remain_finite(self):
        image = np.arange(64).reshape(4, 4, 4) + 1
        for axes in (("x", "y", "z"), ("x", "z")):
            reader, scans, _ = coil_fixture([image, -2j * image])
            adaptive = build_cartesian_preview(reader, None, scans, combination="adaptive", ifft_axes=axes)
            reader, scans, _ = coil_fixture([image, -2j * image])
            rss = build_cartesian_preview(reader, None, scans, combination="rss", ifft_axes=axes)
            np.testing.assert_allclose(adaptive.image, rss.image, atol=2e-5)

    def test_unknown_axis_or_combination_is_rejected(self):
        reader, scans, _ = coil_fixture([np.ones((4, 4))])
        for kwargs in ({"ifft_axes": ("x", "x")}, {"ifft_axes": ("slice",)}, {"combination": "magic"}):
            with self.assertRaises(ValueError):
                build_cartesian_preview(reader, None, scans, **kwargs)
        self.assertEqual(reader.reads, 0)


class NoiseCalibrationTests(unittest.TestCase):
    def test_noise_covariance_is_mean_centered_and_physical_ids_are_aligned(self):
        rng = np.random.default_rng(12)
        samples = rng.normal(size=(2, 96)) + 1j * rng.normal(size=(2, 96))
        samples = np.array([[1, 0], [0.3 + 0.2j, 2]]) @ samples
        samples += np.array([20 + 8j, -7j])[:, None]
        scans = [Scan(i, 0, samples=48, flags=("NOISEADJSCAN",), is_image=False) for i in range(2)]
        reader = CoilReader({i: samples[:, i * 48:(i + 1) * 48][::-1] for i in range(2)}, ids=[19, 7])
        result = estimate_noise_covariance(reader, SimpleNamespace(index=0, id=27), scans, [7, 19])
        centered = samples - samples.mean(axis=1, keepdims=True)
        expected = centered @ centered.conj().T / 95
        np.testing.assert_allclose(result.covariance, expected, atol=1e-10)
        self.assertEqual(result.metadata["noise_samples"], 96)

    def test_sample_and_readout_caps_bound_the_calibration(self):
        samples = np.tile(np.arange(64), (2, 1))
        scans = [Scan(i, 0, samples=64, flags=("NOISEADJSCAN",)) for i in range(20)]
        reader = CoilReader({i: samples for i in range(20)})
        result = estimate_noise_covariance(reader, None, scans, [7, 19], max_samples=100, max_readouts=3)
        self.assertEqual(result.metadata["noise_samples"], 100)
        self.assertEqual(reader.reads, 4)

    def test_wrong_physical_coils_are_not_used_as_noise_calibration(self):
        scan = Scan(0, 0, samples=64, flags=("NOISEADJSCAN",))
        reader = CoilReader({}, ids=[4, 5])
        result = estimate_noise_covariance(reader, None, [scan], [7, 19])
        self.assertIsNone(result.covariance)
        self.assertEqual(reader.reads, 0)

    def test_cancellation_from_reader_propagates(self):
        class Cancelled(CoilReader):
            def read_signal(self, *args):
                raise InterruptedError("Cancelled")
        scan = Scan(0, 0, samples=64, flags=("NOISEADJSCAN",))
        with self.assertRaises(InterruptedError):
            estimate_noise_covariance(Cancelled({}), None, [scan], [7, 19])


if __name__ == "__main__":
    unittest.main()
