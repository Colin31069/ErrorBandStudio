from __future__ import annotations

from collections.abc import Sequence

from .config import DIXON_Q_EXCLUSION_METHOD
from .models import ExcludedSample


def max_excluded_samples(sample_count: int) -> int:
    if sample_count < 5:
        return 0
    return sample_count // 5


def is_dixon_q_exclusion(excluded_sample: ExcludedSample) -> bool:
    return excluded_sample.method.strip().casefold() == DIXON_Q_EXCLUSION_METHOD


def dixon_q_exception_allowed(sample_count: int) -> bool:
    return 3 <= sample_count < 5


def current_excluded_samples(
    excluded_samples: Sequence[ExcludedSample],
    all_files: Sequence[str],
) -> list[ExcludedSample]:
    allowed_count = max_excluded_samples(len(all_files))
    dixon_exception_available = dixon_q_exception_allowed(len(all_files))
    if allowed_count <= 0 and not dixon_exception_available:
        return []

    file_names_by_key = {file_name.casefold(): file_name for file_name in all_files}
    seen: set[str] = set()
    current: list[ExcludedSample] = []
    dixon_exception_used = False

    for excluded_sample in excluded_samples:
        key = excluded_sample.file_name.casefold()
        if key not in file_names_by_key or key in seen:
            continue

        is_dixon_exception = (
            allowed_count <= 0
            and dixon_exception_available
            and not dixon_exception_used
            and is_dixon_q_exclusion(excluded_sample)
        )
        if len(current) >= allowed_count and not is_dixon_exception:
            continue

        current.append(
            ExcludedSample(
                file_name=file_names_by_key[key],
                reason=excluded_sample.reason.strip(),
                method=excluded_sample.method.strip(),
            )
        )
        if is_dixon_exception:
            dixon_exception_used = True
        seen.add(key)
        if len(current) >= allowed_count and not dixon_exception_available:
            break

    return current


def current_excluded_file_names(
    excluded_samples: Sequence[ExcludedSample],
    all_files: Sequence[str],
) -> set[str]:
    return {
        excluded_sample.file_name
        for excluded_sample in current_excluded_samples(excluded_samples, all_files)
    }
