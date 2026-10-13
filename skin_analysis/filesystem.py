from __future__ import annotations

import os
import re


def natural_sort_key(value: str) -> list[int | str]:
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"([0-9]+)", value)]


def normalize_directory_path(path: str) -> str:
    stripped = path.strip()
    if not stripped:
        return ""

    expanded = os.path.expanduser(stripped)
    return os.path.abspath(os.path.normpath(expanded))


def resolve_directory_path(path: str) -> str | None:
    normalized = normalize_directory_path(path)
    if not normalized or not os.path.isdir(normalized):
        return None
    return normalized


def get_subfolders(path: str) -> list[str]:
    if not os.path.isdir(path):
        return []

    folders = [name for name in os.listdir(path) if os.path.isdir(os.path.join(path, name))]
    return sorted(folders, key=natural_sort_key)


def list_xlsx_files(path: str) -> list[str]:
    if not os.path.isdir(path):
        return []

    files = [
        name
        for name in os.listdir(path)
        if name.lower().endswith(".xlsx") and not name.startswith("~$") and os.path.isfile(os.path.join(path, name))
    ]
    return sorted(files, key=natural_sort_key)
