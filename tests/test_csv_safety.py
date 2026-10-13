from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from skin_analysis.csv_safety import spreadsheet_safe_cell


class CsvSafetyTests(unittest.TestCase):
    def test_formula_like_labels_are_literal_text(self) -> None:
        for label in ("=1+1", "+HYPERLINK('a')", "-cmd|' /C", "@SUM(A1:A2)", "  =1+2"):
            with self.subTest(label=label):
                self.assertEqual(spreadsheet_safe_cell(label), "'" + label)

    def test_numeric_columns_are_not_rewritten(self) -> None:
        for value in (12.5, -4.5, "-4.5", "+1.0", "-1.2e-5", "NaN", "normal_file.xlsx"):
            with self.subTest(value=value):
                self.assertEqual(spreadsheet_safe_cell(value), value)

    def test_statistics_csv_is_safe_and_identifies_exact_source_file(self) -> None:
        from skin_analysis.statistics import analyze_delta_percent_samples, write_statistics_csv
        from tests.test_statistics import make_sample

        risky_group = "=HYPERLINK(\"https://example.invalid\")"
        sample = make_sample(risky_group, -2.0, "@malicious")
        result = analyze_delta_percent_samples([sample], root_path="=cmd")
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "out.csv"
            write_statistics_csv(result, str(path))
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))

        self.assertIn(["root_path", "'=cmd"], rows)
        self.assertTrue(any(row and row[0] == "'" + risky_group for row in rows))
        header = next(row for row in rows if row and row[0] == "group" and "delta_capacitance" in row)
        self.assertEqual(header[-1], "file_name")
        sample_row = next(row for row in rows if row and row[0] == "'" + risky_group and "@malicious" in str(row))
        self.assertIn("'@malicious", sample_row)
        self.assertEqual(sample_row[-1], "'@malicious.xlsx")


if __name__ == "__main__":
    unittest.main()
