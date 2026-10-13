from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skin_analysis.metadata import load_experiment_metadata, metadata_file_path, save_experiment_metadata
from skin_analysis.models import ExcludedSample, ExperimentMetadata, MedicineEntry


class MetadataTests(unittest.TestCase):
    def test_missing_metadata_file_returns_default_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            metadata, warning = load_experiment_metadata(tmp_dir)

            self.assertIsNone(warning)
            self.assertEqual(metadata.medicine_count, 1)
            self.assertEqual(len(metadata.medicines), 1)
            self.assertEqual(metadata.medicines[0], MedicineEntry(name="", dose=""))
            self.assertEqual(metadata.excluded_samples, [])

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            metadata = ExperimentMetadata(
                medicine_count=2,
                medicines=[
                    MedicineEntry(name="lanolin", dose="1% 5mL"),
                    MedicineEntry(name="plastik 70", dose="1.5uL"),
                ],
                excluded_samples=[
                    ExcludedSample(file_name="3.xlsx", reason="baseline drift"),
                    ExcludedSample(file_name="5.xlsx", reason="Dixon Q", method="dixon_q"),
                ],
            )

            save_experiment_metadata(tmp_dir, metadata)
            loaded, warning = load_experiment_metadata(tmp_dir)

            self.assertIsNone(warning)
            self.assertEqual(loaded, metadata)

    def test_invalid_json_falls_back_to_default_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            Path(metadata_file_path(tmp_dir)).write_text("{not valid json", encoding="utf-8")

            metadata, warning = load_experiment_metadata(tmp_dir)

            self.assertIsNotNone(warning)
            self.assertEqual(metadata.medicine_count, 1)
            self.assertEqual(metadata.medicines, [MedicineEntry(name="", dose="")])
            self.assertEqual(metadata.excluded_samples, [])

    def test_metadata_without_exclusions_loads_as_empty_exclusion_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            Path(metadata_file_path(tmp_dir)).write_text(
                """
{
  "medicine_count": 1,
  "medicines": [{"name": "lanolin", "dose": "1%"}]
}
""",
                encoding="utf-8",
            )

            metadata, warning = load_experiment_metadata(tmp_dir)

            self.assertIsNone(warning)
            self.assertEqual(metadata.excluded_samples, [])

    def test_duplicate_excluded_samples_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            Path(metadata_file_path(tmp_dir)).write_text(
                """
{
  "medicine_count": 1,
  "medicines": [{"name": "", "dose": ""}],
  "excluded_samples": [
    {"file_name": "3.xlsx", "reason": ""},
    {"file_name": "3.xlsx", "reason": "baseline drift"},
    {"file_name": "4.xlsx", "reason": "bad contact"}
  ]
}
""",
                encoding="utf-8",
            )

            metadata, warning = load_experiment_metadata(tmp_dir)

            self.assertIsNone(warning)
            self.assertEqual(
                metadata.excluded_samples,
                [
                    ExcludedSample(file_name="3.xlsx", reason="baseline drift"),
                    ExcludedSample(file_name="4.xlsx", reason="bad contact"),
                ],
            )

    def test_excluded_sample_method_is_optional_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            Path(metadata_file_path(tmp_dir)).write_text(
                """
{
  "medicine_count": 1,
  "medicines": [{"name": "", "dose": ""}],
  "excluded_samples": [
    {"file_name": "2.xlsx", "reason": "old manual reason"},
    {"file_name": "3.xlsx", "reason": "Dixon Q", "method": "dixon_q"}
  ]
}
""",
                encoding="utf-8",
            )

            metadata, warning = load_experiment_metadata(tmp_dir)

            self.assertIsNone(warning)
            self.assertEqual(
                metadata.excluded_samples,
                [
                    ExcludedSample(file_name="2.xlsx", reason="old manual reason"),
                    ExcludedSample(file_name="3.xlsx", reason="Dixon Q", method="dixon_q"),
                ],
            )

    def test_invalid_excluded_samples_falls_back_to_default_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            Path(metadata_file_path(tmp_dir)).write_text(
                """
{
  "medicine_count": 1,
  "medicines": [{"name": "", "dose": ""}],
  "excluded_samples": "3.xlsx"
}
""",
                encoding="utf-8",
            )

            metadata, warning = load_experiment_metadata(tmp_dir)

            self.assertIsNotNone(warning)
            self.assertEqual(metadata.medicine_count, 1)
            self.assertEqual(metadata.excluded_samples, [])


    def test_classic_unreadable_metadata_is_not_autosaved_or_used_after_external_repair(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from skin_analysis.gui import RawDataViewerApp
        from skin_analysis.metadata import default_experiment_metadata

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(metadata_file_path(tmp_dir))
            original = b'{broken json\n'
            path.write_bytes(original)

            editor = SimpleNamespace(
                _is_loading_metadata=False,
                _metadata_unreadable_folder="",
                _current_experiment_dir=lambda: tmp_dir,
                _apply_metadata_to_editor=Mock(),
                _autosave_current_metadata=Mock(),
                _metadata_from_editor=default_experiment_metadata,
            )
            with patch("skin_analysis.gui.messagebox.showwarning") as warning:
                RawDataViewerApp._load_selected_experiment_metadata(editor)
            warning.assert_called_once()
            editor._autosave_current_metadata.assert_not_called()
            self.assertEqual(editor._metadata_unreadable_folder, tmp_dir)
            self.assertEqual(path.read_bytes(), original)

            editor._metadata_is_safe_to_use = lambda folder, show_errors=True: (
                RawDataViewerApp._metadata_is_safe_to_use(editor, folder, show_errors)
            )
            self.assertFalse(RawDataViewerApp._autosave_current_metadata(editor, show_errors=False))
            self.assertEqual(path.read_bytes(), original)

            # Even after an external repair, the stale editor cannot overwrite the repaired file.
            repaired = '{"medicine_count": 1, "medicines": [], "excluded_samples": [{"file_name": "2.xlsx", "reason": "bad contact"}]}'
            path.write_text(repaired, encoding="utf-8")
            self.assertFalse(RawDataViewerApp._autosave_current_metadata(editor, show_errors=False))
            self.assertEqual(path.read_text(encoding="utf-8"), repaired)

            with patch("skin_analysis.gui.messagebox.showwarning") as warning:
                RawDataViewerApp._load_selected_experiment_metadata(editor)
            warning.assert_not_called()
            self.assertEqual(editor._metadata_unreadable_folder, "")
            loaded_metadata = editor._apply_metadata_to_editor.call_args.args[0]
            self.assertEqual(loaded_metadata.excluded_samples[0].file_name, "2.xlsx")

    def test_classic_autosave_rejects_metadata_corrupted_since_last_load(self) -> None:
        from types import SimpleNamespace

        from skin_analysis.gui import RawDataViewerApp
        from skin_analysis.metadata import default_experiment_metadata

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(metadata_file_path(tmp_dir))
            original = b'{"excluded_samples": "invalid schema"}'
            path.write_bytes(original)
            editor = SimpleNamespace(
                _is_loading_metadata=False,
                _metadata_unreadable_folder="",
                _current_experiment_dir=lambda: tmp_dir,
                _metadata_from_editor=default_experiment_metadata,
            )
            editor._metadata_is_safe_to_use = lambda folder, show_errors=True: (
                RawDataViewerApp._metadata_is_safe_to_use(editor, folder, show_errors)
            )
            self.assertFalse(RawDataViewerApp._autosave_current_metadata(editor, show_errors=False))
            self.assertEqual(editor._metadata_unreadable_folder, tmp_dir)
            self.assertEqual(path.read_bytes(), original)

    def test_classic_missing_metadata_still_initializes_defaults(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock

        from skin_analysis.gui import RawDataViewerApp

        with tempfile.TemporaryDirectory() as tmp_dir:
            editor = SimpleNamespace(
                _metadata_unreadable_folder="",
                _current_experiment_dir=lambda: tmp_dir,
                _apply_metadata_to_editor=Mock(),
                _autosave_current_metadata=Mock(),
            )
            RawDataViewerApp._load_selected_experiment_metadata(editor)
            self.assertEqual(editor._metadata_unreadable_folder, "")
            editor._autosave_current_metadata.assert_called_once_with(show_errors=False)


    def test_atomic_save_failure_preserves_original_json_and_cleans_temp(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp_dir:
            original = ExperimentMetadata(1, [MedicineEntry("original", "1%")],
                                          [ExcludedSample(file_name="2.xlsx", reason="bad contact")])
            save_experiment_metadata(tmp_dir, original)
            path = Path(metadata_file_path(tmp_dir))
            original_bytes = path.read_bytes()
            new_metadata = ExperimentMetadata(1, [MedicineEntry("changed", "2%")], [])
            with patch("skin_analysis.metadata.os.replace", side_effect=OSError("simulated replacement failure")):
                with self.assertRaisesRegex(OSError, "simulated replacement failure"):
                    save_experiment_metadata(tmp_dir, new_metadata)
            self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual(list(Path(tmp_dir).glob("*.tmp")), [])

    def test_classic_metadata_editor_retains_dormant_exclusions(self) -> None:
        from types import SimpleNamespace
        from skin_analysis.gui import RawDataViewerApp

        old = ExcludedSample(file_name="2.xlsx", reason="bad contact")
        field = SimpleNamespace(get=lambda: "")
        editor = SimpleNamespace(
            _get_medicine_count=lambda: 1,
            medicine_name_vars=[field],
            medicine_dose_vars=[field],
            _excluded_samples=[old],
        )
        metadata = RawDataViewerApp._metadata_from_editor(editor)
        self.assertEqual(metadata.excluded_samples, [old])

    def test_classic_load_blocks_metadata_exclusions_invalidated_by_file_count(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from skin_analysis.gui import RawDataViewerApp

        with tempfile.TemporaryDirectory() as tmp_dir:
            for index in range(1, 5):
                (Path(tmp_dir) / f"{index}.xlsx").touch()
            save_experiment_metadata(tmp_dir, ExperimentMetadata(
                1, [MedicineEntry("", "")], [ExcludedSample("2.xlsx", "manual")],
            ))
            editor = SimpleNamespace(
                _metadata_unreadable_folder="",
                _current_experiment_dir=lambda: tmp_dir,
                _apply_metadata_to_editor=Mock(),
                _autosave_current_metadata=Mock(),
            )
            with patch("skin_analysis.gui.messagebox.showwarning") as warning:
                RawDataViewerApp._load_selected_experiment_metadata(editor)
            warning.assert_called_once()
            self.assertEqual(editor._metadata_unreadable_folder, tmp_dir)
            editor._autosave_current_metadata.assert_not_called()

if __name__ == "__main__":
    unittest.main()
