from __future__ import annotations

import json
import os

from .config import DEFAULT_MEDICINE_COUNT, MAX_MEDICINES, METADATA_FILENAME
from .models import ExcludedSample, ExperimentMetadata, MedicineEntry


def metadata_file_path(folder_path: str) -> str:
    return os.path.join(folder_path, METADATA_FILENAME)


def default_experiment_metadata() -> ExperimentMetadata:
    return ExperimentMetadata(
        medicine_count=DEFAULT_MEDICINE_COUNT,
        medicines=[MedicineEntry(name="", dose="") for _ in range(DEFAULT_MEDICINE_COUNT)],
        excluded_samples=[],
    )


def _normalize_metadata(raw_data: object) -> ExperimentMetadata:
    if not isinstance(raw_data, dict):
        raise ValueError("Metadata content must be a JSON object.")

    medicine_count = raw_data.get("medicine_count", DEFAULT_MEDICINE_COUNT)
    medicines = raw_data.get("medicines", [])
    excluded_samples = raw_data.get("excluded_samples", [])

    if not isinstance(medicine_count, int):
        raise ValueError("medicine_count must be an integer.")
    if not isinstance(medicines, list):
        raise ValueError("medicines must be a list.")
    if not isinstance(excluded_samples, list):
        raise ValueError("excluded_samples must be a list.")

    medicine_count = max(0, min(MAX_MEDICINES, medicine_count))

    entries: list[MedicineEntry] = []
    for item in medicines[:medicine_count]:
        if not isinstance(item, dict):
            raise ValueError("Each medicine entry must be an object.")

        name = item.get("name", "")
        dose = item.get("dose", "")
        if not isinstance(name, str) or not isinstance(dose, str):
            raise ValueError("Medicine name and dose must be strings.")
        entries.append(MedicineEntry(name=name.strip(), dose=dose.strip()))

    while len(entries) < medicine_count:
        entries.append(MedicineEntry(name="", dose=""))

    excluded_entries: list[ExcludedSample] = []
    excluded_keys: dict[str, int] = {}
    for item in excluded_samples:
        if not isinstance(item, dict):
            raise ValueError("Each excluded sample entry must be an object.")

        file_name = item.get("file_name", "")
        reason = item.get("reason", "")
        method = item.get("method", "")
        if not isinstance(file_name, str) or not isinstance(reason, str) or not isinstance(method, str):
            raise ValueError("Excluded sample file_name, reason, and method must be strings.")

        normalized_file_name = os.path.basename(file_name.strip())
        if not normalized_file_name:
            continue

        entry = ExcludedSample(
            file_name=normalized_file_name,
            reason=reason.strip(),
            method=method.strip(),
        )
        key = normalized_file_name.casefold()
        if key in excluded_keys:
            existing_index = excluded_keys[key]
            existing_entry = excluded_entries[existing_index]
            if (not existing_entry.reason and entry.reason) or (not existing_entry.method and entry.method):
                excluded_entries[existing_index] = ExcludedSample(
                    file_name=existing_entry.file_name,
                    reason=existing_entry.reason or entry.reason,
                    method=existing_entry.method or entry.method,
                )
            continue

        excluded_keys[key] = len(excluded_entries)
        excluded_entries.append(entry)

    return ExperimentMetadata(
        medicine_count=medicine_count,
        medicines=entries,
        excluded_samples=excluded_entries,
    )


def load_experiment_metadata(folder_path: str) -> tuple[ExperimentMetadata, str | None]:
    path = metadata_file_path(folder_path)
    if not os.path.exists(path):
        return default_experiment_metadata(), None

    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            raw_data = json.load(file_obj)
        return _normalize_metadata(raw_data), None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return default_experiment_metadata(), f"Metadata file was reset because it could not be read: {exc}"


def save_experiment_metadata(folder_path: str, metadata: ExperimentMetadata) -> None:
    path = metadata_file_path(folder_path)
    medicine_count = max(0, min(MAX_MEDICINES, metadata.medicine_count))
    medicines = list(metadata.medicines[:medicine_count])
    while len(medicines) < medicine_count:
        medicines.append(MedicineEntry(name="", dose=""))

    payload = {
        "medicine_count": medicine_count,
        "medicines": [{"name": entry.name.strip(), "dose": entry.dose.strip()} for entry in medicines],
        "excluded_samples": [
            {
                "file_name": os.path.basename(entry.file_name.strip()),
                "reason": entry.reason.strip(),
                **({"method": entry.method.strip()} if entry.method.strip() else {}),
            }
            for entry in metadata.excluded_samples
            if os.path.basename(entry.file_name.strip())
        ],
    }

    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
