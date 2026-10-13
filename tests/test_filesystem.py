from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skin_analysis.filesystem import (
    get_subfolders,
    list_xlsx_files,
    natural_sort_key,
    normalize_directory_path,
    resolve_directory_path,
)


class FilesystemTests(unittest.TestCase):
    def test_natural_sort_orders_numbers_like_humans_expect(self) -> None:
        values = ["sample10", "sample2", "sample1"]
        ordered = sorted(values, key=natural_sort_key)
        self.assertEqual(ordered, ["sample1", "sample2", "sample10"])

    def test_get_subfolders_returns_sorted_directory_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "folder10").mkdir()
            (base / "folder2").mkdir()
            (base / "folder1").mkdir()
            (base / "notes.txt").write_text("ignore me", encoding="utf-8")

            self.assertEqual(get_subfolders(tmp_dir), ["folder1", "folder2", "folder10"])

    def test_missing_path_returns_empty_lists(self) -> None:
        self.assertEqual(get_subfolders("/path/that/does/not/exist"), [])
        self.assertEqual(list_xlsx_files("/path/that/does/not/exist"), [])

    def test_normalize_directory_path_trims_expands_and_returns_absolute_path(self) -> None:
        self.assertEqual(normalize_directory_path("  ~/  "), str(Path.home()))

    def test_normalize_directory_path_collapses_relative_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            nested = Path(tmp_dir) / "nested"
            nested.mkdir()

            raw_path = f"  {nested}/../nested  "
            self.assertEqual(normalize_directory_path(raw_path), normalize_directory_path(str(nested)))

    def test_resolve_directory_path_accepts_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertEqual(resolve_directory_path(f"  {tmp_dir}  "), normalize_directory_path(tmp_dir))

    def test_resolve_directory_path_rejects_missing_directory(self) -> None:
        self.assertIsNone(resolve_directory_path("/path/that/does/not/exist"))

    def test_list_xlsx_files_filters_and_sorts_excel_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "2.xlsx").write_text("", encoding="utf-8")
            (base / "10.xlsx").write_text("", encoding="utf-8")
            (base / "1.xlsx").write_text("", encoding="utf-8")
            (base / "notes.csv").write_text("", encoding="utf-8")
            (base / "~$1.xlsx").write_text("Excel lock", encoding="utf-8")

            self.assertEqual(list_xlsx_files(tmp_dir), ["1.xlsx", "2.xlsx", "10.xlsx"])


if __name__ == "__main__":
    unittest.main()
