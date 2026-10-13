from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np
from openpyxl import Workbook

from skin_analysis.metadata import save_experiment_metadata
from skin_analysis.models import ErrorBandPayload, ErrorBandSettings, ExcludedSample, ExperimentMetadata
from skin_analysis.plotting import aggregate_error_band, build_error_band_payload, write_error_band_csv
from tests.test_plotting import make_signal


def write_xlsx(path: Path, values, column="pF - Plot 0") -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([column])
    for value in values:
        sheet.append([value])
    workbook.save(path)
    workbook.close()


class ErrorBandTests(unittest.TestCase):
    def test_subtracts_each_baseline_before_mean_and_sample_sd(self) -> None:
        signals = [
            make_signal(capacitance=np.array([10., 10., 11., 13.]), initial_avg=10.),
            make_signal(capacitance=np.array([20., 20., 23., 25.]), initial_avg=20.),
        ]
        group = aggregate_error_band("2 uL", signals, ("1.xlsx", "2.xlsx"), ErrorBandSettings())
        np.testing.assert_allclose(group.mean, [0., 0., 2., 4.])
        np.testing.assert_allclose(group.sd, [0., 0., np.sqrt(2), np.sqrt(2)])
        np.testing.assert_allclose(group.sem, [0., 0., 1., 1.])
        self.assertEqual(group.n, 2)

    def test_relative_change_uses_each_sample_baseline(self) -> None:
        signals = [
            make_signal(capacitance=np.array([10., 10., 12., 14.]), initial_avg=10.),
            make_signal(capacitance=np.array([20., 20., 22., 24.]), initial_avg=20.),
        ]
        group = aggregate_error_band("group", signals, ("1", "2"), ErrorBandSettings(display_mode="DeltaPct"))
        np.testing.assert_allclose(group.mean, [0., 0., 15., 30.])

    def test_uses_intersection_after_drop_alignment_without_padding(self) -> None:
        signals = [
            make_signal(time_sec=np.arange(5) * .1, capacitance=np.array([0., 1., 2., 3., 4.]), drop_time=.2, initial_avg=0.),
            make_signal(capacitance=np.array([10., 20., 30., 40.]), drop_time=.1, initial_avg=0.),
        ]
        group = aggregate_error_band("group", signals, ("1", "2"), ErrorBandSettings(display_mode="Raw"))
        np.testing.assert_allclose(group.time_sec, [-.1, 0., .1, .2], atol=1e-12)
        np.testing.assert_allclose(group.mean, [5.5, 11., 16.5, 22.])

    def test_single_sample_has_no_defined_error_band(self) -> None:
        group = aggregate_error_band("group", [make_signal()], ("1",), ErrorBandSettings())
        self.assertTrue(np.all(np.isnan(group.sd)))
        self.assertTrue(np.all(np.isnan(group.sem)))
        self.assertTrue(any("n=1" in warning for warning in group.warnings))

    def test_zero_baseline_rejected_for_percent_mode(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_error_band("group", [make_signal(initial_avg=0.)], ("1",), ErrorBandSettings(display_mode="DeltaPct"))

    def test_no_post_drop_data_rejected(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_error_band("group", [make_signal(drop_time=10.)], ("1",), ErrorBandSettings())

    def test_invalid_settings_rejected(self) -> None:
        for kwargs in ({"sample_interval_sec": 0.}, {"baseline_duration_sec": float("nan")}, {"display_mode": "invalid"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                aggregate_error_band("group", [make_signal()], ("1",), ErrorBandSettings(**kwargs))

    def test_payload_respects_exclusions_reports_bad_files_and_does_not_write_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "2 uL"
            folder.mkdir()
            for index in range(1, 6):
                write_xlsx(folder / f"{index}.xlsx", [10.] * 30 + [10. + index] * 30)
            write_xlsx(folder / "bad.xlsx", [10., None, 12.])
            (folder / "~$1.xlsx").write_bytes(b"Excel lock")
            metadata = ExperimentMetadata(0, [], [ExcludedSample("2.xlsx", "bad contact")])
            save_experiment_metadata(str(folder), metadata)
            original_metadata = (folder / ".skin_analysis_metadata.json").read_bytes()
            settings = ErrorBandSettings(baseline_duration_sec=1., drug_apply_time_sec=3., drug_apply_tolerance_sec=.5)
            payload = build_error_band_payload(str(root), [folder.name], settings)
            self.assertEqual(payload.groups[0].sample_names, ("1.xlsx", "3.xlsx", "4.xlsx", "5.xlsx"))
            self.assertEqual(payload.groups[0].n, 4)
            self.assertTrue(any("bad.xlsx" in warning for warning in payload.warnings))
            self.assertTrue(any("2.xlsx" in warning for warning in payload.warnings))
            self.assertFalse(any("~$" in warning for warning in payload.warnings))
            self.assertEqual((folder / ".skin_analysis_metadata.json").read_bytes(), original_metadata)

    def test_corrupt_metadata_skips_group_instead_of_reincluding_excluded_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "group"
            folder.mkdir()
            write_xlsx(folder / "1.xlsx", [10.] * 30 + [12.] * 30)
            (folder / ".skin_analysis_metadata.json").write_text("{bad", encoding="utf-8")
            payload = build_error_band_payload(directory, ["group"], ErrorBandSettings())
            self.assertFalse(payload.groups)
            self.assertTrue(payload.warnings)

    def test_csv_contains_band_bounds_sample_names_and_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "group"
            folder.mkdir()
            for index, response in enumerate((12., 14.), 1):
                write_xlsx(folder / f"{index}.xlsx", [10.] * 30 + [response] * 30)
            settings = ErrorBandSettings(baseline_duration_sec=1., drug_apply_time_sec=3., drug_apply_tolerance_sec=.5)
            payload = build_error_band_payload(directory, ["group"], settings)
            output = Path(directory) / "result.csv"
            write_error_band_csv(payload, str(output), "SEM")
            with output.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            row = next(row for row in rows if float(row["time_sec"]) == 0.)
            self.assertEqual(row["n"], "2")
            self.assertEqual(row["band_type"], "SEM")
            self.assertEqual(row["unit"], "Baseline-subtracted ΔC (pF)")
            self.assertEqual(row["sample_files"], "1.xlsx; 2.xlsx")
            self.assertAlmostEqual(float(row["mean"]), 3.)
            self.assertAlmostEqual(float(row["lower"]), 2.)
            self.assertAlmostEqual(float(row["upper"]), 4.)


    def test_error_band_csv_escapes_untrusted_labels_without_changing_numeric_columns(self) -> None:
        settings = ErrorBandSettings()
        group = aggregate_error_band(
            "=1+1", [make_signal(), make_signal(initial_avg=12.0)],
            ("+trial.xlsx", "ordinary.xlsx"), settings,
        )
        payload = ErrorBandPayload("", settings, (group,), ("=HYPERLINK(1)",))
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "safe.csv"
            write_error_band_csv(payload, str(result_path), "SD")
            with result_path.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
        self.assertTrue(rows)
        row = rows[0]
        self.assertEqual(row["group"], "'=1+1")
        self.assertEqual(row["sample_files"], "'+trial.xlsx; ordinary.xlsx")
        self.assertEqual(row["warnings"], "'=HYPERLINK(1)")
        self.assertEqual(row["legend_label"], "'=1+1")
        self.assertEqual(row["n"], "2")
        self.assertAlmostEqual(float(row["mean"]), -1.0)
        self.assertEqual(row["time_sec"], "-0.2")

    def test_error_band_skips_group_if_recorded_exclusion_no_longer_fits_sample_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "shrunk"
            folder.mkdir()
            for index in range(1, 5):
                write_xlsx(folder / f"{index}.xlsx", [10.0] * 30 + [12.0] * 30)
            save_experiment_metadata(str(folder), ExperimentMetadata(
                medicine_count=1,
                medicines=[],
                excluded_samples=[ExcludedSample("2.xlsx", "bad contact")],
            ))
            settings = ErrorBandSettings(baseline_duration_sec=1.0, drug_apply_time_sec=3.0, drug_apply_tolerance_sec=0.5)
            payload = build_error_band_payload(directory, ["shrunk"], settings)
            self.assertFalse(payload.groups)
            self.assertTrue(any("2.xlsx" in warning and "暫停" in warning for warning in payload.warnings))
            self.assertTrue((folder / ".skin_analysis_metadata.json").exists())

if __name__ == "__main__":
    unittest.main()
