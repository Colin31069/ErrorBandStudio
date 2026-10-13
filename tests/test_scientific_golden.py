from __future__ import annotations

import math
import unittest

import numpy as np

from skin_analysis.analysis import analyze_signal
from skin_analysis.exclusions import current_excluded_samples, unhonored_excluded_samples
from skin_analysis.models import ExcludedSample
from skin_analysis.plotting import transform_signal_for_display
from skin_analysis.statistics import summarize_group


class ScientificGoldenRegressionTests(unittest.TestCase):
    """Numerical outcomes are fixed independently of the code under test."""

    @staticmethod
    def stepped_signal(before: float, after: float, crossing_index: int = 250):
        values = np.concatenate([np.full(crossing_index, before), np.full(120, after)])
        return analyze_signal(values)

    def test_window_first_crossing_baseline_delta_and_time_axis(self) -> None:
        signal = self.stepped_signal(10.0, 12.0)
        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal.initial_avg, 10.0, places=12)
        self.assertAlmostEqual(signal.drop_time, 25.0, places=12)
        self.assertAlmostEqual(signal.delta_capacitance, 2.0, places=12)
        self.assertEqual(signal.effective_baseline_points, 200)
        self.assertEqual(signal.drop_detection_source, "window")
        self.assertFalse(signal.drop_search_fallback_used)
        self.assertAlmostEqual(signal.time_sec[250], 25.0, places=12)

    def test_fallback_crossing_is_explicit_not_a_window_hit(self) -> None:
        signal = self.stepped_signal(10.0, 15.0, crossing_index=350)
        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal.drop_time, 35.0, places=12)
        self.assertAlmostEqual(signal.delta_capacitance, 5.0, places=12)
        self.assertEqual(signal.drop_detection_source, "fallback_auto")
        self.assertTrue(signal.drop_search_fallback_used)
        self.assertTrue(signal.timing_warning_details)

    def test_overlap_clamps_baseline_before_apply_search_window(self) -> None:
        values = np.concatenate([np.full(250, 10.0), np.full(120, 12.0)])
        signal = analyze_signal(values, baseline_duration_sec=30.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.effective_baseline_points, 200)
        self.assertTrue(signal.baseline_was_auto_shortened)
        self.assertAlmostEqual(signal.initial_avg, 10.0, places=12)

    def test_normalization_uses_per_sample_baseline_and_zero_is_rejected(self) -> None:
        signal = self.stepped_signal(10.0, 12.0)
        x, y, label = transform_signal_for_display(signal, "Norm")
        self.assertAlmostEqual(x[250], 0.0, places=12)
        self.assertAlmostEqual(y[0], 100.0, places=12)
        self.assertAlmostEqual(y[250], 120.0, places=12)
        self.assertEqual(label, "Δ:20.00%")

        zero_baseline = self.stepped_signal(0.0, 2.0)
        with self.assertRaisesRegex(ValueError, "zero baseline"):
            transform_signal_for_display(zero_baseline, "Norm")

    def test_sd_sem_and_mean_are_not_interchangeable_with_ci(self) -> None:
        summary = summarize_group("reference", [10.0, 20.0, 30.0])
        self.assertEqual(summary.n, 3)
        self.assertAlmostEqual(summary.mean, 20.0, places=12)
        self.assertAlmostEqual(summary.sd, 10.0, places=12)
        self.assertAlmostEqual(summary.sem, 10.0 / math.sqrt(3.0), places=12)
        self.assertAlmostEqual(summary.median, 20.0, places=12)
        self.assertGreater(summary.sd, summary.sem)

    def test_small_n_sd_sem_are_not_zero(self) -> None:
        summary = summarize_group("only_one", [5.0])
        self.assertEqual(summary.n, 1)
        self.assertTrue(math.isnan(summary.sd))
        self.assertTrue(math.isnan(summary.sem))

    def test_exclusion_count_change_never_silently_reincludes(self) -> None:
        recorded = [ExcludedSample("2.xlsx", "manual")]
        files_five = [f"{i}.xlsx" for i in range(1, 6)]
        files_four = files_five[:4]
        self.assertEqual([e.file_name for e in current_excluded_samples(recorded, files_five)], ["2.xlsx"])
        self.assertEqual(current_excluded_samples(recorded, files_four), [])
        self.assertEqual(unhonored_excluded_samples(recorded, files_four), recorded)


if __name__ == "__main__":
    unittest.main()
