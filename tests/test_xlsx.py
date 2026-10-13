from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from skin_analysis.analysis import load_capacitance_values, process_single_file, read_xlsx_single
from tests.test_error_bands import write_xlsx


class XlsxReaderTests(unittest.TestCase):
    def test_reads_numeric_column_and_ignores_trailing_blank_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "data.xlsx"
            write_xlsx(file, [10., "11.5", 12., None, None])
            np.testing.assert_allclose(load_capacitance_values(str(file)), [10., 11.5, 12.])

    def test_rejects_missing_internal_cells_instead_of_changing_time_axis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "data.xlsx"
            for values in ([10., None, 12.], [10., "text", 12.], [10., True, 12.]):
                with self.subTest(values=values):
                    write_xlsx(file, values)
                    with self.assertRaises(ValueError):
                        load_capacitance_values(str(file))
                    self.assertIsNone(process_single_file(str(file)))

    def test_missing_column_empty_and_corrupt_files_fail_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "data.xlsx"
            write_xlsx(file, [10., 11.], column="other")
            self.assertIsNone(read_xlsx_single(str(file)))
            write_xlsx(file, [])
            self.assertIsNone(process_single_file(str(file)))
            file.write_bytes(b"broken workbook")
            self.assertIsNone(read_xlsx_single(str(file)))


if __name__ == "__main__":
    unittest.main()
