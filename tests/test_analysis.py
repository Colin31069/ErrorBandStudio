from __future__ import annotations

import unittest

import numpy as np

from skin_analysis.analysis import analyze_signal
from skin_analysis.config import (
    DEFAULT_BASELINE_DURATION_SEC,
    DT_SEC,
    INITIAL_BASELINE_POINTS,
)


class AnalyzeSignalTests(unittest.TestCase):
    def test_configurable_sample_interval_updates_detection_and_time_axis(self) -> None:
        signal = analyze_signal(
            np.array([10., 10., 10., 10., 12., 13.]),
            baseline_duration_sec=1.,
            drug_apply_time_sec=2.,
            drug_apply_tolerance_sec=.5,
            sample_interval_sec=.5,
        )
        assert signal is not None
        self.assertEqual(signal.effective_baseline_points, 2)
        self.assertEqual(signal.drop_time, 2.)
        np.testing.assert_allclose(signal.time_sec, np.arange(6) * .5)

    def test_baseline_uses_configured_window_for_long_signal(self) -> None:
        baseline = np.full(INITIAL_BASELINE_POINTS, 10.0)
        tail = np.full(120, 13.0)
        signal = analyze_signal(np.concatenate([baseline, tail]))

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.initial_avg, 10.0)
        self.assertAlmostEqual(signal.drop_time, INITIAL_BASELINE_POINTS * DT_SEC)
        self.assertEqual(signal.effective_baseline_points, INITIAL_BASELINE_POINTS)
        self.assertEqual(signal.drop_detection_source, "window")
        self.assertEqual(signal.baseline_warning_status, "ok")

    def test_short_signal_uses_full_length_as_baseline(self) -> None:
        samples = np.array([2.0, 4.0, 6.0, 8.0], dtype=float)
        signal = analyze_signal(samples)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.initial_avg, float(np.mean(samples)))
        self.assertTrue(np.isnan(signal.delta_capacitance))
        self.assertAlmostEqual(signal.drop_time, len(samples) * DT_SEC)
        self.assertEqual(signal.effective_baseline_points, len(samples))
        self.assertEqual(signal.baseline_warning_status, "ok")

    def test_threshold_crossing_detection_uses_first_crossing_after_baseline(self) -> None:
        baseline = np.full(INITIAL_BASELINE_POINTS, 5.0)
        after = np.array([5.0, 5.0, 5.0, 8.5, 9.0], dtype=float)
        signal = analyze_signal(np.concatenate([baseline, after]))

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.drop_time, (INITIAL_BASELINE_POINTS + 3) * DT_SEC)
        self.assertEqual(signal.drop_detection_source, "window")

    def test_no_crossing_falls_back_to_baseline_boundary(self) -> None:
        samples = np.full(INITIAL_BASELINE_POINTS + 10, 7.0)
        signal = analyze_signal(samples)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.drop_time, INITIAL_BASELINE_POINTS * DT_SEC)
        self.assertEqual(signal.drop_detection_source, "fallback_auto")
        self.assertTrue(signal.drop_search_fallback_used)

    def test_delta_uses_last_hundred_points_when_available(self) -> None:
        baseline = np.full(INITIAL_BASELINE_POINTS, 10.0)
        middle = np.full(50, 11.0)
        tail = np.full(100, 14.0)
        signal = analyze_signal(np.concatenate([baseline, middle, tail]))

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.delta_capacitance, 4.0)

    def test_clean_baseline_window_stays_ok(self) -> None:
        baseline = np.full(INITIAL_BASELINE_POINTS, 10.0)
        signal = analyze_signal(np.concatenate([baseline, np.full(100, 12.0)]), baseline_warning_threshold_pct=2.0)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.baseline_warning_status, "ok")
        self.assertFalse(signal.baseline_tail_warning_hit)
        self.assertFalse(signal.baseline_rise_warning_hit)

    def test_default_timing_matches_legacy_runtime_settings(self) -> None:
        baseline = np.full(INITIAL_BASELINE_POINTS, 10.0)
        after = np.array([10.0, 10.0, 10.0, 13.0], dtype=float)
        signal = analyze_signal(np.concatenate([baseline, after]))

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.effective_baseline_duration_sec, DEFAULT_BASELINE_DURATION_SEC)
        self.assertEqual(signal.drop_detection_source, "window")
        self.assertAlmostEqual(signal.drop_time, (INITIAL_BASELINE_POINTS + 3) * DT_SEC)

    def test_custom_baseline_duration_changes_initial_average(self) -> None:
        first_baseline = np.full(100, 10.0)
        later_baseline = np.full(100, 12.0)
        after = np.full(150, 13.0)
        signal = analyze_signal(
            np.concatenate([first_baseline, later_baseline, after]),
            baseline_duration_sec=10.0,
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.initial_avg, 10.0)
        self.assertEqual(signal.effective_baseline_points, 100)

    def test_apply_window_finds_early_drop_before_old_twenty_second_boundary(self) -> None:
        baseline = np.full(150, 5.0)
        lead_in = np.array([5.0, 5.0], dtype=float)
        onset = np.array([8.0, 8.5, 9.0], dtype=float)
        tail = np.full(120, 9.5)
        signal = analyze_signal(
            np.concatenate([baseline, lead_in, onset, tail]),
            baseline_duration_sec=15.0,
            drug_apply_time_sec=16.0,
            drug_apply_tolerance_sec=2.0,
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.drop_detection_source, "window")
        self.assertAlmostEqual(signal.drop_time, 15.2)

    def test_overlapping_baseline_window_is_auto_shortened(self) -> None:
        clean_baseline = np.full(100, 10.0)
        contaminated_tail = np.full(100, 12.0)
        tail = np.full(100, 14.0)
        signal = analyze_signal(
            np.concatenate([clean_baseline, contaminated_tail, tail]),
            baseline_duration_sec=20.0,
            drug_apply_time_sec=15.0,
            drug_apply_tolerance_sec=5.0,
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertTrue(signal.baseline_was_auto_shortened)
        self.assertEqual(signal.effective_baseline_points, 100)
        self.assertAlmostEqual(signal.initial_avg, 10.0)
        self.assertTrue(any("Baseline shortened" in detail for detail in signal.timing_warning_details))

    def test_apply_window_at_zero_clamps_baseline_to_minimum_sample(self) -> None:
        samples = np.concatenate([np.array([10.0]), np.full(20, 12.0)])
        signal = analyze_signal(
            samples,
            baseline_duration_sec=20.0,
            drug_apply_time_sec=0.0,
            drug_apply_tolerance_sec=0.0,
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.effective_baseline_points, 1)
        self.assertTrue(signal.baseline_was_auto_shortened)
        self.assertAlmostEqual(signal.initial_avg, 10.0)
        self.assertTrue(any("minimum baseline length" in detail for detail in signal.timing_warning_details))

    def test_missing_in_window_hit_falls_back_to_automatic_search(self) -> None:
        baseline = np.full(100, 5.0)
        quiet = np.full(200, 5.0)
        onset = np.array([8.0, 8.5, 9.0], dtype=float)
        tail = np.full(80, 9.5)
        signal = analyze_signal(
            np.concatenate([baseline, quiet, onset, tail]),
            baseline_duration_sec=10.0,
            drug_apply_time_sec=15.0,
            drug_apply_tolerance_sec=1.0,
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.drop_detection_source, "fallback_auto")
        self.assertTrue(signal.drop_search_fallback_used)
        self.assertAlmostEqual(signal.drop_time, 30.0)
        self.assertTrue(any("used automatic fallback search" in detail for detail in signal.timing_warning_details))

    def test_baseline_quality_uses_effective_baseline_segment_after_shortening(self) -> None:
        clean_baseline = np.full(100, 10.0)
        rising_segment = np.linspace(10.0, 12.0, 100, dtype=float)
        tail = np.full(100, 13.0)
        signal = analyze_signal(
            np.concatenate([clean_baseline, rising_segment, tail]),
            baseline_warning_threshold_pct=2.0,
            baseline_duration_sec=20.0,
            drug_apply_time_sec=15.0,
            drug_apply_tolerance_sec=5.0,
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.effective_baseline_points, 100)
        self.assertEqual(signal.baseline_warning_status, "ok")

    def test_tail_only_contamination_returns_warning(self) -> None:
        baseline = np.concatenate([np.full(180, 10.0), np.full(20, 10.3)])
        signal = analyze_signal(np.concatenate([baseline, np.full(100, 12.0)]), baseline_warning_threshold_pct=2.0)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.baseline_warning_status, "warning")
        self.assertTrue(signal.baseline_tail_warning_hit)
        self.assertFalse(signal.baseline_rise_warning_hit)
        self.assertGreater(signal.baseline_tail_offset_pct, 2.0)

    def test_sustained_rise_only_returns_warning(self) -> None:
        ramp = np.linspace(10.0, 10.5, 25, dtype=float)
        baseline = np.concatenate([np.full(50, 10.0), ramp, np.full(125, 10.0)])
        signal = analyze_signal(np.concatenate([baseline, np.full(100, 12.0)]), baseline_warning_threshold_pct=2.0)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.baseline_warning_status, "warning")
        self.assertFalse(signal.baseline_tail_warning_hit)
        self.assertTrue(signal.baseline_rise_warning_hit)
        self.assertGreater(signal.baseline_rise_offset_pct, 2.0)

    def test_tail_and_sustained_rise_return_inaccurate(self) -> None:
        baseline = np.concatenate([np.full(170, 10.0), np.linspace(10.0, 10.6, 30, dtype=float)])
        signal = analyze_signal(np.concatenate([baseline, np.full(100, 12.0)]), baseline_warning_threshold_pct=2.0)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.baseline_warning_status, "inaccurate")
        self.assertTrue(signal.baseline_tail_warning_hit)
        self.assertTrue(signal.baseline_rise_warning_hit)

    def test_threshold_changes_warning_classification(self) -> None:
        baseline = np.concatenate([np.full(180, 10.0), np.full(20, 10.3)])
        low_threshold_signal = analyze_signal(
            np.concatenate([baseline, np.full(100, 12.0)]),
            baseline_warning_threshold_pct=2.0,
        )
        high_threshold_signal = analyze_signal(
            np.concatenate([baseline, np.full(100, 12.0)]),
            baseline_warning_threshold_pct=3.0,
        )

        self.assertIsNotNone(low_threshold_signal)
        self.assertIsNotNone(high_threshold_signal)
        assert low_threshold_signal is not None
        assert high_threshold_signal is not None
        self.assertEqual(low_threshold_signal.baseline_warning_status, "warning")
        self.assertEqual(high_threshold_signal.baseline_warning_status, "ok")


if __name__ == "__main__":
    unittest.main()
