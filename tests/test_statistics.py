from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from skin_analysis.metadata import save_experiment_metadata
from skin_analysis.models import ExcludedSample, ExperimentMetadata, MedicineEntry, ProcessedSignal, StatisticalSample
from skin_analysis.statistics import (
    SCIPY_AVAILABLE,
    analyze_delta_percent_samples,
    collect_delta_percent_samples,
    delta_percent_from_signal,
    format_statistics_result,
    statistics_result_to_csv_rows,
    summarize_group,
)


def make_signal(**overrides) -> ProcessedSignal:
    defaults = dict(
        time_sec=np.array([0.0, 0.1, 0.2], dtype=float),
        capacitance=np.array([10.0, 11.0, 12.0], dtype=float),
        drop_time=0.1,
        delta_capacitance=2.0,
        initial_avg=10.0,
        effective_baseline_points=2,
        effective_baseline_duration_sec=0.2,
        baseline_was_auto_shortened=False,
        drop_detection_source="window",
        drop_search_fallback_used=False,
        timing_warning_details=(),
        baseline_warning_status="ok",
        baseline_tail_offset_pct=0.0,
        baseline_rise_offset_pct=0.0,
        baseline_tail_warning_hit=False,
        baseline_rise_warning_hit=False,
    )
    defaults.update(overrides)
    return ProcessedSignal(**defaults)


def make_sample(group: str, value: float, sample_name: str = "1", warnings: tuple[str, ...] = ()) -> StatisticalSample:
    return StatisticalSample(
        group_name=group,
        sample_name=sample_name,
        delta_percent=value,
        delta_capacitance=value / 10.0,
        baseline=10.0,
        drop_time=20.0,
        baseline_warning_status="ok",
        drop_detection_source="window",
        warnings=warnings,
        file_name=f"{sample_name}.xlsx",
    )


class StatisticsTests(unittest.TestCase):
    def test_delta_percent_uses_each_signal_baseline(self) -> None:
        self.assertAlmostEqual(delta_percent_from_signal(make_signal()), 20.0)
        self.assertTrue(math.isnan(delta_percent_from_signal(make_signal(initial_avg=0.0))))

    def test_summarize_group_returns_descriptive_statistics(self) -> None:
        summary = summarize_group("0.1pct", [1.0, 2.0, 3.0])

        self.assertEqual(summary.group_name, "0.1pct")
        self.assertEqual(summary.n, 3)
        self.assertAlmostEqual(summary.mean, 2.0)
        self.assertAlmostEqual(summary.sd, 1.0)
        self.assertAlmostEqual(summary.sem, 1.0 / math.sqrt(3))
        self.assertAlmostEqual(summary.median, 2.0)
        self.assertAlmostEqual(summary.q1, 1.5)
        self.assertAlmostEqual(summary.q3, 2.5)
        self.assertAlmostEqual(summary.iqr, 1.0)

    def test_analyze_delta_percent_samples_builds_one_way_anova_result(self) -> None:
        samples = [
            make_sample("control", 1.0, "c1"),
            make_sample("control", 2.0, "c2"),
            make_sample("control", 3.0, "c3"),
            make_sample("low", 2.0, "l1"),
            make_sample("low", 3.0, "l2"),
            make_sample("low", 4.0, "l3"),
            make_sample("high", 7.0, "h1"),
            make_sample("high", 8.0, "h2"),
            make_sample("high", 9.0, "h3"),
        ]

        result = analyze_delta_percent_samples(samples, root_path="/tmp/root")

        self.assertEqual([summary.group_name for summary in result.group_statistics], ["control", "low", "high"])
        self.assertEqual(result.anova.method, "One-way ANOVA")
        self.assertEqual(result.anova.group_count, 3)
        self.assertEqual(result.anova.sample_count, 9)
        self.assertGreater(result.anova.eta_squared, 0)

        if SCIPY_AVAILABLE:
            self.assertFalse(math.isnan(result.anova.p_value))
        else:
            self.assertTrue(math.isnan(result.anova.p_value))
            self.assertTrue(any("SciPy is not installed" in warning for warning in result.warnings))

    def test_dixon_q_flags_high_endpoint_recommendation(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("drug", 10.0, "d1"),
                make_sample("drug", 11.0, "d2"),
                make_sample("drug", 12.0, "d3"),
                make_sample("drug", 13.0, "d4"),
                make_sample("drug", 50.0, "d5", warnings=("baseline warning",)),
            ]
        )

        self.assertEqual(len(result.dixon_recommendations), 1)
        recommendation = result.dixon_recommendations[0]
        self.assertEqual(recommendation.sample_name, "d5")
        self.assertEqual(recommendation.file_name, "d5.xlsx")
        self.assertEqual(recommendation.side, "high")
        self.assertAlmostEqual(recommendation.q_statistic, 37.0 / 40.0)
        self.assertAlmostEqual(recommendation.critical_value, 0.710)
        self.assertEqual(recommendation.warnings, ("baseline warning",))
        self.assertIsNotNone(result.dixon_sensitivity_anova)

    def test_dixon_q_flags_low_endpoint_recommendation(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("drug", 1.0, "d1"),
                make_sample("drug", 40.0, "d2"),
                make_sample("drug", 41.0, "d3"),
                make_sample("drug", 42.0, "d4"),
                make_sample("drug", 43.0, "d5"),
            ]
        )

        self.assertEqual(len(result.dixon_recommendations), 1)
        recommendation = result.dixon_recommendations[0]
        self.assertEqual(recommendation.sample_name, "d1")
        self.assertEqual(recommendation.side, "low")
        self.assertAlmostEqual(recommendation.q_statistic, 39.0 / 42.0)

    def test_dixon_q_does_not_recommend_when_below_critical_value(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("drug", 10.0, "d1"),
                make_sample("drug", 11.0, "d2"),
                make_sample("drug", 12.0, "d3"),
                make_sample("drug", 13.0, "d4"),
                make_sample("drug", 14.0, "d5"),
            ]
        )

        self.assertEqual(result.dixon_recommendations, [])
        self.assertIsNone(result.dixon_sensitivity_anova)

    def test_dixon_q_skips_unsupported_group_sizes_and_zero_range(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("small", 1.0, "s1"),
                make_sample("small", 2.0, "s2"),
                *[make_sample("large", float(index), f"l{index}") for index in range(1, 12)],
                make_sample("flat", 5.0, "f1"),
                make_sample("flat", 5.0, "f2"),
                make_sample("flat", 5.0, "f3"),
            ],
            group_names=["small", "large", "flat"],
        )

        self.assertEqual(result.dixon_recommendations, [])
        self.assertTrue(any("needs n >= 3" in note for note in result.dixon_review_notes))
        self.assertTrue(any("only up to n=10" in note for note in result.dixon_review_notes))
        self.assertTrue(any("range is zero" in note for note in result.dixon_review_notes))

    def test_outlier_review_skips_groups_below_minimum_n(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("drug", 10.0, "d1"),
                make_sample("drug", 10.2, "d2"),
                make_sample("drug", 9.8, "d3"),
                make_sample("drug", 40.0, "d4"),
            ]
        )

        self.assertEqual(result.outlier_candidates, [])
        self.assertTrue(any("robust outlier review needs n >= 5" in note for note in result.outlier_review_notes))

    def test_outlier_review_flags_robust_delta_percent_candidate(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("drug", 10.0, "d1"),
                make_sample("drug", 10.2, "d2"),
                make_sample("drug", 9.8, "d3"),
                make_sample("drug", 10.1, "d4"),
                make_sample("drug", 40.0, "d5", warnings=("baseline warning",)),
            ]
        )

        self.assertEqual(len(result.outlier_candidates), 1)
        candidate = result.outlier_candidates[0]
        self.assertEqual(candidate.group_name, "drug")
        self.assertEqual(candidate.sample_name, "d5")
        self.assertAlmostEqual(candidate.peer_median, 10.05)
        self.assertAlmostEqual(candidate.peer_mad, 0.1)
        self.assertGreater(abs(candidate.modified_z_score), 3.5)
        self.assertEqual(candidate.warnings, ("baseline warning",))
        self.assertIn("not automatically excluded", candidate.note)

    def test_collect_delta_percent_samples_groups_by_folder_and_skips_bad_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            control = root / "0pct"
            treatment = root / "1pct"
            control.mkdir()
            treatment.mkdir()

            pd.DataFrame({"pF - Plot 0": np.concatenate([np.full(50, 10.0), np.full(120, 12.0)])}).to_excel(
                control / "1.xlsx",
                index=False,
            )
            pd.DataFrame({"pF - Plot 0": np.concatenate([np.full(50, 10.0), np.full(120, 15.0)])}).to_excel(
                treatment / "1.xlsx",
                index=False,
            )
            pd.DataFrame({"wrong": [1, 2, 3]}).to_excel(treatment / "bad.xlsx", index=False)

            samples, warnings, _excluded_samples, _group_names = collect_delta_percent_samples(
                tmp_dir,
                baseline_duration_sec=5.0,
                drug_apply_time_sec=10.0,
                drug_apply_tolerance_sec=1.0,
            )

        self.assertEqual([sample.group_name for sample in samples], ["0pct", "1pct"])
        self.assertAlmostEqual(samples[0].delta_percent, 20.0)
        self.assertAlmostEqual(samples[1].delta_percent, 50.0)
        self.assertTrue(any("bad.xlsx could not be analyzed" in warning for warning in warnings))

    def test_collect_delta_percent_samples_skips_metadata_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            group = root / "1pct"
            group.mkdir()

            for index in range(1, 6):
                pd.DataFrame({"pF - Plot 0": np.concatenate([np.full(50, 10.0), np.full(120, 12.0)])}).to_excel(
                    group / f"{index}.xlsx",
                    index=False,
                )

            save_experiment_metadata(
                str(group),
                ExperimentMetadata(
                    medicine_count=1,
                    medicines=[MedicineEntry(name="", dose="")],
                    excluded_samples=[ExcludedSample(file_name="3.xlsx", reason="bad contact")],
                ),
            )

            samples, warnings, excluded_samples, group_names = collect_delta_percent_samples(
                tmp_dir,
                baseline_duration_sec=5.0,
                drug_apply_time_sec=10.0,
                drug_apply_tolerance_sec=1.0,
            )

        self.assertEqual([sample.sample_name for sample in samples], ["1", "2", "4", "5"])
        self.assertEqual(len(excluded_samples), 1)
        self.assertEqual(excluded_samples[0].group_name, "1pct")
        self.assertEqual(excluded_samples[0].file_name, "3.xlsx")
        self.assertEqual(excluded_samples[0].reason, "bad contact")
        self.assertEqual(excluded_samples[0].method, "")
        self.assertEqual(group_names, ["1pct"])
        self.assertEqual(warnings, ())

        result = analyze_delta_percent_samples(
            samples,
            group_names=group_names,
            excluded_samples=excluded_samples,
        )
        self.assertEqual(result.group_statistics[0].n, 4)
        self.assertIn("1pct/3.xlsx: bad contact", format_statistics_result(result))
        self.assertIn(["1pct", "3.xlsx", "", "bad contact"], statistics_result_to_csv_rows(result))

    def test_statistics_result_to_csv_rows_contains_export_sections(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("control", 1.0, "c1"),
                make_sample("control", 2.0, "c2"),
                make_sample("drug", 3.0, "d1"),
                make_sample("drug", 4.0, "d2"),
            ]
        )

        rows = statistics_result_to_csv_rows(result)
        first_cells = [row[0] for row in rows if row]

        self.assertIn("Descriptive Statistics", first_cells)
        self.assertIn("Dixon Q Review", first_cells)
        self.assertIn("Recommended Dixon Exclusions", first_cells)
        self.assertIn("Outlier Review Candidates", first_cells)
        self.assertIn("One-way ANOVA", first_cells)
        self.assertIn("ANOVA Sensitivity After Dixon Recommendations", first_cells)
        self.assertIn("Per-Sample Delta %", first_cells)

    def test_statistics_report_and_csv_include_outlier_review_candidates(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("drug", 10.0, "d1"),
                make_sample("drug", 10.2, "d2"),
                make_sample("drug", 9.8, "d3"),
                make_sample("drug", 10.1, "d4"),
                make_sample("drug", 40.0, "d5"),
            ]
        )

        formatted = format_statistics_result(result)
        rows = statistics_result_to_csv_rows(result)

        self.assertIn("Outlier Review", formatted)
        self.assertIn("Candidate count: 1", formatted)
        self.assertIn("not automatically excluded", formatted)
        self.assertIn(
            [
                "group",
                "sample",
                "delta_percent",
                "peer_median",
                "delta_from_peer_median",
                "peer_mad",
                "modified_z_score",
                "quality_warnings",
                "note",
            ],
            rows,
        )
        self.assertTrue(any(row[:2] == ["drug", "d5"] for row in rows))

    def test_statistics_report_and_csv_include_dixon_and_sensitivity_sections(self) -> None:
        result = analyze_delta_percent_samples(
            [
                make_sample("control", 1.0, "c1"),
                make_sample("control", 2.0, "c2"),
                make_sample("control", 3.0, "c3"),
                make_sample("drug", 10.0, "d1"),
                make_sample("drug", 11.0, "d2"),
                make_sample("drug", 12.0, "d3"),
                make_sample("drug", 13.0, "d4"),
                make_sample("drug", 50.0, "d5"),
            ],
            group_names=["control", "drug"],
        )

        formatted = format_statistics_result(result)
        rows = statistics_result_to_csv_rows(result)

        self.assertIn("Dixon Q Review", formatted)
        self.assertIn("Recommended Dixon Exclusions: 1", formatted)
        self.assertIn("ANOVA Sensitivity After Dixon Recommendations", formatted)
        self.assertIsNotNone(result.dixon_sensitivity_anova)
        self.assertEqual(result.dixon_sensitivity_anova.sample_count, 7)
        self.assertTrue(any(row[:2] == ["drug", "d5.xlsx"] for row in rows))


if __name__ == "__main__":
    unittest.main()
