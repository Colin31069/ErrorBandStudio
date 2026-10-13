from __future__ import annotations

import csv
import math
import os
from collections.abc import Iterable, Sequence

import numpy as np

from .analysis import process_single_file
from .config import (
    DEFAULT_BASELINE_DURATION_SEC,
    DEFAULT_BASELINE_WARNING_THRESHOLD_PCT,
    DEFAULT_DRUG_APPLY_TIME_SEC,
    DEFAULT_DRUG_APPLY_TOLERANCE_SEC,
    DIXON_Q_ALPHA,
    DIXON_Q_CRITICAL_VALUES,
    OUTLIER_MIN_GROUP_N,
    OUTLIER_MODIFIED_Z_THRESHOLD,
)
from .exclusions import current_excluded_samples, max_excluded_samples
from .filesystem import get_subfolders, list_xlsx_files
from .metadata import load_experiment_metadata
from .models import (
    AnovaResult,
    DixonQRecommendation,
    ExcludedSample,
    GroupStatistics,
    ProcessedSignal,
    StatisticalExclusion,
    StatisticalAnalysisResult,
    StatisticalOutlierCandidate,
    StatisticalSample,
    VarianceCheckResult,
)

try:
    from scipy import stats as scipy_stats
except ImportError:  # pragma: no cover - exercised in environments without scipy
    scipy_stats = None

SCIPY_AVAILABLE = scipy_stats is not None
MISSING_SCIPY_NOTE = "SciPy is not installed; p-values and 95% CIs are unavailable."
MODIFIED_Z_SCORE_SCALE = 0.6745
MAD_EPSILON = 1e-12


def _nan() -> float:
    return float("nan")


def _is_finite(value: float) -> bool:
    return math.isfinite(float(value))


def _format_float(value: float, digits: int = 4) -> str:
    if not _is_finite(value):
        return "NA"
    return f"{value:.{digits}g}"


def delta_percent_from_signal(signal: ProcessedSignal) -> float:
    if abs(signal.initial_avg) < 1e-12 or not _is_finite(signal.delta_capacitance):
        return _nan()
    return (signal.delta_capacitance / signal.initial_avg) * 100.0


def _values_for_group(samples: Sequence[StatisticalSample], group_name: str) -> np.ndarray:
    values = [sample.delta_percent for sample in samples if sample.group_name == group_name]
    return np.asarray(values, dtype=float)


def _samples_for_group(samples: Sequence[StatisticalSample], group_name: str) -> list[StatisticalSample]:
    return [
        sample
        for sample in samples
        if sample.group_name == group_name and _is_finite(sample.delta_percent)
    ]


def sample_file_name(sample: StatisticalSample) -> str:
    return sample.file_name or f"{sample.sample_name}.xlsx"


def _valid_groups(samples: Sequence[StatisticalSample]) -> list[str]:
    seen: list[str] = []
    for sample in samples:
        if sample.group_name not in seen:
            seen.append(sample.group_name)
    return seen


def _analysis_group_order(
    samples: Sequence[StatisticalSample],
    group_names: Sequence[str] | None,
) -> list[str]:
    if group_names is None:
        return _valid_groups(samples)

    seen: list[str] = []
    for group_name in group_names:
        if group_name not in seen:
            seen.append(group_name)
    for sample in samples:
        if sample.group_name not in seen:
            seen.append(sample.group_name)
    return seen


def summarize_group(group_name: str, values: Sequence[float]) -> GroupStatistics:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)

    if n == 0:
        return GroupStatistics(
            group_name=group_name,
            n=0,
            mean=_nan(),
            sd=_nan(),
            sem=_nan(),
            median=_nan(),
            q1=_nan(),
            q3=_nan(),
            iqr=_nan(),
            ci95_low=_nan(),
            ci95_high=_nan(),
            shapiro_statistic=_nan(),
            shapiro_p_value=_nan(),
            normality_note="No valid samples.",
        )

    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1)) if n >= 2 else _nan()
    sem = float(sd / math.sqrt(n)) if n >= 2 and _is_finite(sd) else _nan()
    median = float(np.median(arr))
    q1 = float(np.percentile(arr, 25))
    q3 = float(np.percentile(arr, 75))
    iqr = q3 - q1

    ci95_low = _nan()
    ci95_high = _nan()
    if n >= 2 and scipy_stats is not None and _is_finite(sem):
        t_crit = float(scipy_stats.t.ppf(0.975, df=n - 1))
        ci95_low = mean - t_crit * sem
        ci95_high = mean + t_crit * sem

    shapiro_statistic = _nan()
    shapiro_p_value = _nan()
    if n < 3:
        normality_note = "Normality not tested because n < 3."
    elif scipy_stats is None:
        normality_note = MISSING_SCIPY_NOTE
    else:
        shapiro_result = scipy_stats.shapiro(arr)
        shapiro_statistic = float(shapiro_result.statistic)
        shapiro_p_value = float(shapiro_result.pvalue)
        normality_note = "Shapiro-Wilk normality check."

    return GroupStatistics(
        group_name=group_name,
        n=n,
        mean=mean,
        sd=sd,
        sem=sem,
        median=median,
        q1=q1,
        q3=q3,
        iqr=iqr,
        ci95_low=ci95_low,
        ci95_high=ci95_high,
        shapiro_statistic=shapiro_statistic,
        shapiro_p_value=shapiro_p_value,
        normality_note=normality_note,
    )


def _classical_anova_effects(group_values: Sequence[np.ndarray]) -> tuple[float, float, float, float, float, float]:
    valid = [values[np.isfinite(values)] for values in group_values if values[np.isfinite(values)].size > 0]
    if len(valid) < 2:
        return _nan(), _nan(), _nan(), _nan(), _nan(), _nan()

    all_values = np.concatenate(valid)
    sample_count = int(all_values.size)
    group_count = len(valid)
    if sample_count <= group_count:
        return _nan(), _nan(), _nan(), _nan(), _nan(), _nan()

    grand_mean = float(np.mean(all_values))
    ss_between = float(sum(len(values) * ((float(np.mean(values)) - grand_mean) ** 2) for values in valid))
    ss_within = float(sum(np.sum((values - float(np.mean(values))) ** 2) for values in valid))
    ss_total = ss_between + ss_within
    df_between = float(group_count - 1)
    df_within = float(sample_count - group_count)
    ms_within = ss_within / df_within if df_within > 0 else _nan()
    eta_squared = ss_between / ss_total if ss_total > 0 else _nan()
    if ss_total > 0 and _is_finite(ms_within):
        omega_squared = max(0.0, (ss_between - (df_between * ms_within)) / (ss_total + ms_within))
    else:
        omega_squared = _nan()
    return ss_between, ss_within, df_between, df_within, eta_squared, omega_squared


def compute_variance_check(group_statistics: Sequence[GroupStatistics], samples: Sequence[StatisticalSample]) -> VarianceCheckResult:
    group_values = [
        _values_for_group(samples, group.group_name)
        for group in group_statistics
        if group.n >= 2
    ]
    if len(group_values) < 2:
        return VarianceCheckResult(
            method="Brown-Forsythe",
            statistic=_nan(),
            p_value=_nan(),
            note="Variance check not run because at least two groups need n >= 2.",
        )
    if scipy_stats is None:
        return VarianceCheckResult(
            method="Brown-Forsythe",
            statistic=_nan(),
            p_value=_nan(),
            note=MISSING_SCIPY_NOTE,
        )

    absolute_deviations = np.concatenate([
        np.abs(values - float(np.median(values)))
        for values in group_values
    ])
    if absolute_deviations.size == 0 or float(np.var(absolute_deviations)) <= 1e-15:
        return VarianceCheckResult(
            method="Brown-Forsythe",
            statistic=_nan(),
            p_value=_nan(),
            note="Variance check not run because median-centered deviations are identical.",
        )

    test_result = scipy_stats.levene(*group_values, center="median")
    return VarianceCheckResult(
        method="Brown-Forsythe",
        statistic=float(test_result.statistic),
        p_value=float(test_result.pvalue),
        note="Levene variance check centered on group medians.",
    )


def compute_one_way_anova(group_statistics: Sequence[GroupStatistics], samples: Sequence[StatisticalSample]) -> AnovaResult:
    group_values = [
        _values_for_group(samples, group.group_name)
        for group in group_statistics
        if group.n >= 2
    ]
    sample_count = int(sum(len(values) for values in group_values))
    group_count = len(group_values)
    ss_between, ss_within, df_between, df_within, eta_squared, omega_squared = _classical_anova_effects(group_values)

    if group_count < 2 or sample_count <= group_count:
        return AnovaResult(
            method="One-way ANOVA",
            group_count=group_count,
            sample_count=sample_count,
            statistic=_nan(),
            df_num=df_between,
            df_den=df_within,
            p_value=_nan(),
            eta_squared=eta_squared,
            omega_squared=omega_squared,
            note="ANOVA not run because at least two groups need n >= 2.",
        )

    ms_between = ss_between / df_between
    ms_within = ss_within / df_within
    if ms_within <= 0:
        statistic = _nan()
        p_value = _nan()
        note = "ANOVA not run because within-group variance is zero."
    else:
        statistic = ms_between / ms_within
        if scipy_stats is None:
            p_value = _nan()
            note = MISSING_SCIPY_NOTE
        else:
            p_value = float(scipy_stats.f.sf(statistic, df_between, df_within))
            note = "Classical one-way ANOVA."

    return AnovaResult(
        method="One-way ANOVA",
        group_count=group_count,
        sample_count=sample_count,
        statistic=statistic,
        df_num=df_between,
        df_den=df_within,
        p_value=p_value,
        eta_squared=eta_squared,
        omega_squared=omega_squared,
        note=note,
    )


def _dixon_recommendation_for_group(group_name: str, group_samples: Sequence[StatisticalSample]) -> tuple[DixonQRecommendation | None, str | None]:
    sorted_samples = sorted(group_samples, key=lambda sample: sample.delta_percent)
    n = len(sorted_samples)
    if n < min(DIXON_Q_CRITICAL_VALUES):
        return None, f"Group '{group_name}' has n={n}; Dixon Q10 needs n >= {min(DIXON_Q_CRITICAL_VALUES)}."
    if n > max(DIXON_Q_CRITICAL_VALUES):
        return None, f"Group '{group_name}' has n={n}; Dixon Q10 table is available only up to n={max(DIXON_Q_CRITICAL_VALUES)}."

    low_sample = sorted_samples[0]
    low_neighbor = sorted_samples[1]
    high_neighbor = sorted_samples[-2]
    high_sample = sorted_samples[-1]
    range_delta_percent = high_sample.delta_percent - low_sample.delta_percent
    if not _is_finite(range_delta_percent) or range_delta_percent <= 0:
        return None, f"Group '{group_name}' Dixon Q10 not run because Delta % range is zero."

    critical_value = DIXON_Q_CRITICAL_VALUES[n]
    low_gap = low_neighbor.delta_percent - low_sample.delta_percent
    high_gap = high_sample.delta_percent - high_neighbor.delta_percent
    low_q = low_gap / range_delta_percent
    high_q = high_gap / range_delta_percent
    low_passes = low_q > critical_value
    high_passes = high_q > critical_value

    if low_passes and high_passes and math.isclose(low_q, high_q, rel_tol=1e-12, abs_tol=1e-12):
        return None, (
            f"Group '{group_name}' has tied low/high Dixon Q10 candidates; no single recommendation was made."
        )
    if low_passes and (not high_passes or low_q > high_q):
        selected_sample = low_sample
        nearest_delta_percent = low_neighbor.delta_percent
        gap_delta_percent = low_gap
        q_statistic = low_q
        side = "low"
    elif high_passes:
        selected_sample = high_sample
        nearest_delta_percent = high_neighbor.delta_percent
        gap_delta_percent = high_gap
        q_statistic = high_q
        side = "high"
    else:
        return None, None

    return (
        DixonQRecommendation(
            group_name=selected_sample.group_name,
            sample_name=selected_sample.sample_name,
            file_name=sample_file_name(selected_sample),
            side=side,
            delta_percent=selected_sample.delta_percent,
            nearest_delta_percent=nearest_delta_percent,
            gap_delta_percent=gap_delta_percent,
            range_delta_percent=range_delta_percent,
            q_statistic=float(q_statistic),
            critical_value=critical_value,
            alpha=DIXON_Q_ALPHA,
            warnings=selected_sample.warnings,
            note="Dixon Q10 recommends exclusion review; not automatically excluded.",
        ),
        None,
    )


def compute_dixon_q_review(
    group_statistics: Sequence[GroupStatistics],
    samples: Sequence[StatisticalSample],
) -> tuple[list[DixonQRecommendation], tuple[str, ...]]:
    recommendations: list[DixonQRecommendation] = []
    notes: list[str] = []
    for summary in group_statistics:
        group_samples = _samples_for_group(samples, summary.group_name)
        recommendation, note = _dixon_recommendation_for_group(summary.group_name, group_samples)
        if recommendation is not None:
            recommendations.append(recommendation)
        if note:
            notes.append(note)
    return recommendations, tuple(notes)


def compute_dixon_sensitivity_anova(
    samples: Sequence[StatisticalSample],
    group_names: Sequence[str],
    recommendations: Sequence[DixonQRecommendation],
) -> AnovaResult | None:
    if not recommendations:
        return None

    recommended_keys = {
        (recommendation.group_name, recommendation.file_name.casefold())
        for recommendation in recommendations
    }
    sensitivity_samples = [
        sample
        for sample in samples
        if (sample.group_name, sample_file_name(sample).casefold()) not in recommended_keys
    ]
    sensitivity_groups = [
        summarize_group(group_name, _values_for_group(sensitivity_samples, group_name))
        for group_name in group_names
    ]
    anova = compute_one_way_anova(sensitivity_groups, sensitivity_samples)
    return AnovaResult(
        method="One-way ANOVA (Dixon sensitivity)",
        group_count=anova.group_count,
        sample_count=anova.sample_count,
        statistic=anova.statistic,
        df_num=anova.df_num,
        df_den=anova.df_den,
        p_value=anova.p_value,
        eta_squared=anova.eta_squared,
        omega_squared=anova.omega_squared,
        note=f"{anova.note} Preview excludes {len(recommendations)} Dixon Q recommendation(s).",
    )


def compute_outlier_review(
    group_statistics: Sequence[GroupStatistics],
    samples: Sequence[StatisticalSample],
    excluded_samples: Sequence[StatisticalExclusion] | None = None,
) -> tuple[list[StatisticalOutlierCandidate], tuple[str, ...]]:
    candidates: list[StatisticalOutlierCandidate] = []
    notes: list[str] = []
    excluded_count_by_group: dict[str, int] = {}
    for excluded_sample in excluded_samples or ():
        excluded_count_by_group[excluded_sample.group_name] = excluded_count_by_group.get(excluded_sample.group_name, 0) + 1

    for summary in group_statistics:
        group_samples = _samples_for_group(samples, summary.group_name)
        n = len(group_samples)
        if n < OUTLIER_MIN_GROUP_N:
            notes.append(
                f"Group '{summary.group_name}' has n={n}; robust outlier review needs n >= {OUTLIER_MIN_GROUP_N}."
            )
            continue

        group_candidates: list[StatisticalOutlierCandidate] = []
        zero_mad_count = 0
        group_values = np.asarray([sample.delta_percent for sample in group_samples], dtype=float)

        for index, sample in enumerate(group_samples):
            peer_values = np.delete(group_values, index)
            peer_median = float(np.median(peer_values))
            peer_mad = float(np.median(np.abs(peer_values - peer_median)))
            if not _is_finite(peer_mad) or peer_mad <= MAD_EPSILON:
                zero_mad_count += 1
                continue

            delta_from_peer_median = sample.delta_percent - peer_median
            modified_z_score = MODIFIED_Z_SCORE_SCALE * delta_from_peer_median / peer_mad
            if abs(modified_z_score) < OUTLIER_MODIFIED_Z_THRESHOLD:
                continue

            group_candidates.append(
                StatisticalOutlierCandidate(
                    group_name=sample.group_name,
                    sample_name=sample.sample_name,
                    delta_percent=sample.delta_percent,
                    peer_median=peer_median,
                    peer_mad=peer_mad,
                    delta_from_peer_median=delta_from_peer_median,
                    modified_z_score=float(modified_z_score),
                    warnings=sample.warnings,
                    note=(
                        f"abs(modified z) >= {OUTLIER_MODIFIED_Z_THRESHOLD:g}; "
                        "review raw curve; not automatically excluded."
                    ),
                )
            )

        if zero_mad_count == n:
            notes.append(
                f"Group '{summary.group_name}' robust outlier review could not compute modified z-scores because peer MAD was zero."
            )
        elif zero_mad_count:
            notes.append(
                f"Group '{summary.group_name}' skipped {zero_mad_count} sample(s) in robust outlier review because peer MAD was zero."
            )

        original_n_estimate = n + excluded_count_by_group.get(summary.group_name, 0)
        max_allowed = max_excluded_samples(original_n_estimate)
        if group_candidates and len(group_candidates) > max_allowed:
            notes.append(
                f"Group '{summary.group_name}' has {len(group_candidates)} outlier candidate(s), above the manual exclusion cap of {max_allowed}; review whether the whole group has a quality problem."
            )
        elif group_candidates and len(group_candidates) + excluded_count_by_group.get(summary.group_name, 0) > max_allowed:
            notes.append(
                f"Group '{summary.group_name}' already has {excluded_count_by_group[summary.group_name]} excluded sample(s); excluding every candidate would exceed the manual exclusion cap of {max_allowed}."
            )

        candidates.extend(group_candidates)

    return candidates, tuple(notes)


def analyze_delta_percent_samples(
    samples: Sequence[StatisticalSample],
    root_path: str = "",
    group_names: Sequence[str] | None = None,
    excluded_samples: Sequence[StatisticalExclusion] | None = None,
) -> StatisticalAnalysisResult:
    groups = _analysis_group_order(samples, group_names)
    group_statistics = [
        summarize_group(group, _values_for_group(samples, group))
        for group in groups
    ]
    warnings: list[str] = []

    if not SCIPY_AVAILABLE:
        warnings.append(MISSING_SCIPY_NOTE)
    for summary in group_statistics:
        if summary.n < 2:
            warnings.append(f"Group '{summary.group_name}' has n={summary.n}; one-way ANOVA needs n >= 2.")
        elif summary.n < 3:
            warnings.append(f"Group '{summary.group_name}' has n={summary.n}; Shapiro-Wilk normality needs n >= 3.")

    variance_check = compute_variance_check(group_statistics, samples)
    anova = compute_one_way_anova(group_statistics, samples)
    dixon_recommendations, dixon_review_notes = compute_dixon_q_review(group_statistics, samples)
    dixon_sensitivity_anova = compute_dixon_sensitivity_anova(samples, groups, dixon_recommendations)
    outlier_candidates, outlier_review_notes = compute_outlier_review(
        group_statistics,
        samples,
        excluded_samples=excluded_samples,
    )

    return StatisticalAnalysisResult(
        root_path=root_path,
        samples=list(samples),
        excluded_samples=list(excluded_samples or []),
        group_statistics=group_statistics,
        variance_check=variance_check,
        anova=anova,
        dixon_recommendations=dixon_recommendations,
        dixon_review_notes=dixon_review_notes,
        dixon_sensitivity_anova=dixon_sensitivity_anova,
        outlier_candidates=outlier_candidates,
        outlier_review_notes=outlier_review_notes,
        warnings=tuple(warnings),
        scipy_available=SCIPY_AVAILABLE,
    )


def collect_delta_percent_samples_for_group(
    group_name: str,
    group_dir: str,
    files: Sequence[str],
    excluded_file_names: set[str],
    baseline_warning_threshold_pct: float = DEFAULT_BASELINE_WARNING_THRESHOLD_PCT,
    baseline_duration_sec: float = DEFAULT_BASELINE_DURATION_SEC,
    drug_apply_time_sec: float = DEFAULT_DRUG_APPLY_TIME_SEC,
    drug_apply_tolerance_sec: float = DEFAULT_DRUG_APPLY_TOLERANCE_SEC,
) -> tuple[list[StatisticalSample], tuple[str, ...]]:
    samples: list[StatisticalSample] = []
    warnings: list[str] = []
    for file_name in files:
        if file_name in excluded_file_names:
            continue

        file_path = os.path.join(group_dir, file_name)
        signal = process_single_file(
            file_path,
            baseline_warning_threshold_pct=baseline_warning_threshold_pct,
            baseline_duration_sec=baseline_duration_sec,
            drug_apply_time_sec=drug_apply_time_sec,
            drug_apply_tolerance_sec=drug_apply_tolerance_sec,
        )
        sample_name = os.path.splitext(file_name)[0]
        if signal is None:
            warnings.append(f"{group_name}/{file_name} could not be analyzed.")
            continue

        delta_percent = delta_percent_from_signal(signal)
        if not _is_finite(delta_percent):
            warnings.append(f"{group_name}/{file_name} has an invalid Delta % and was skipped.")
            continue

        sample_warnings: list[str] = []
        if signal.baseline_warning_status != "ok":
            sample_warnings.append(f"baseline {signal.baseline_warning_status}")
        if signal.timing_warning_details:
            sample_warnings.extend(signal.timing_warning_details)

        samples.append(
            StatisticalSample(
                group_name=group_name,
                sample_name=sample_name,
                delta_percent=delta_percent,
                delta_capacitance=signal.delta_capacitance,
                baseline=signal.initial_avg,
                drop_time=signal.drop_time,
                baseline_warning_status=signal.baseline_warning_status,
                drop_detection_source=signal.drop_detection_source,
                warnings=tuple(sample_warnings),
                file_name=file_name,
            )
        )
    return samples, tuple(warnings)


def build_dixon_q_review_for_group(
    group_name: str,
    group_dir: str,
    excluded_samples: Sequence[ExcludedSample],
    baseline_warning_threshold_pct: float = DEFAULT_BASELINE_WARNING_THRESHOLD_PCT,
    baseline_duration_sec: float = DEFAULT_BASELINE_DURATION_SEC,
    drug_apply_time_sec: float = DEFAULT_DRUG_APPLY_TIME_SEC,
    drug_apply_tolerance_sec: float = DEFAULT_DRUG_APPLY_TOLERANCE_SEC,
) -> tuple[list[StatisticalSample], list[DixonQRecommendation], tuple[str, ...]]:
    files = list_xlsx_files(group_dir)
    current_exclusions = current_excluded_samples(excluded_samples, files)
    excluded_file_names = {entry.file_name for entry in current_exclusions}
    samples, warnings = collect_delta_percent_samples_for_group(
        group_name,
        group_dir,
        files,
        excluded_file_names,
        baseline_warning_threshold_pct=baseline_warning_threshold_pct,
        baseline_duration_sec=baseline_duration_sec,
        drug_apply_time_sec=drug_apply_time_sec,
        drug_apply_tolerance_sec=drug_apply_tolerance_sec,
    )
    group_statistics = [summarize_group(group_name, _values_for_group(samples, group_name))]
    recommendations, dixon_notes = compute_dixon_q_review(group_statistics, samples)
    return samples, recommendations, tuple(warnings) + dixon_notes


def collect_delta_percent_samples(
    root_path: str,
    baseline_warning_threshold_pct: float = DEFAULT_BASELINE_WARNING_THRESHOLD_PCT,
    baseline_duration_sec: float = DEFAULT_BASELINE_DURATION_SEC,
    drug_apply_time_sec: float = DEFAULT_DRUG_APPLY_TIME_SEC,
    drug_apply_tolerance_sec: float = DEFAULT_DRUG_APPLY_TOLERANCE_SEC,
) -> tuple[list[StatisticalSample], tuple[str, ...], list[StatisticalExclusion], list[str]]:
    samples: list[StatisticalSample] = []
    excluded_samples: list[StatisticalExclusion] = []
    warnings: list[str] = []
    group_names = get_subfolders(root_path)
    for group_name in group_names:
        group_dir = os.path.join(root_path, group_name)
        files = list_xlsx_files(group_dir)
        if not files:
            warnings.append(f"Group '{group_name}' has no .xlsx files.")
            continue

        metadata, metadata_warning = load_experiment_metadata(group_dir)
        if metadata_warning:
            warnings.append(f"Group '{group_name}' metadata warning: {metadata_warning}")
        current_exclusions = current_excluded_samples(metadata.excluded_samples, files)
        excluded_file_names = {entry.file_name for entry in current_exclusions}
        excluded_samples.extend(
            StatisticalExclusion(
                group_name=group_name,
                file_name=entry.file_name,
                reason=entry.reason,
                method=entry.method,
            )
            for entry in current_exclusions
        )

        group_samples, group_warnings = collect_delta_percent_samples_for_group(
            group_name,
            group_dir,
            files,
            excluded_file_names,
            baseline_warning_threshold_pct=baseline_warning_threshold_pct,
            baseline_duration_sec=baseline_duration_sec,
            drug_apply_time_sec=drug_apply_time_sec,
            drug_apply_tolerance_sec=drug_apply_tolerance_sec,
        )
        samples.extend(group_samples)
        warnings.extend(group_warnings)

    return samples, tuple(warnings), excluded_samples, group_names


def build_statistical_analysis(
    root_path: str,
    baseline_warning_threshold_pct: float = DEFAULT_BASELINE_WARNING_THRESHOLD_PCT,
    baseline_duration_sec: float = DEFAULT_BASELINE_DURATION_SEC,
    drug_apply_time_sec: float = DEFAULT_DRUG_APPLY_TIME_SEC,
    drug_apply_tolerance_sec: float = DEFAULT_DRUG_APPLY_TOLERANCE_SEC,
) -> StatisticalAnalysisResult:
    samples, collection_warnings, excluded_samples, group_names = collect_delta_percent_samples(
        root_path,
        baseline_warning_threshold_pct=baseline_warning_threshold_pct,
        baseline_duration_sec=baseline_duration_sec,
        drug_apply_time_sec=drug_apply_time_sec,
        drug_apply_tolerance_sec=drug_apply_tolerance_sec,
    )
    result = analyze_delta_percent_samples(
        samples,
        root_path=root_path,
        group_names=group_names,
        excluded_samples=excluded_samples,
    )
    return StatisticalAnalysisResult(
        root_path=result.root_path,
        samples=result.samples,
        excluded_samples=result.excluded_samples,
        group_statistics=result.group_statistics,
        variance_check=result.variance_check,
        anova=result.anova,
        dixon_recommendations=result.dixon_recommendations,
        dixon_review_notes=result.dixon_review_notes,
        dixon_sensitivity_anova=result.dixon_sensitivity_anova,
        outlier_candidates=result.outlier_candidates,
        outlier_review_notes=result.outlier_review_notes,
        warnings=tuple(collection_warnings) + result.warnings,
        scipy_available=result.scipy_available,
    )


def _format_descriptive_line(summary: GroupStatistics) -> str:
    return (
        f"{summary.group_name}: n={summary.n}, mean={_format_float(summary.mean)}%, "
        f"SD={_format_float(summary.sd)}%, SEM={_format_float(summary.sem)}%, "
        f"median={_format_float(summary.median)}%, IQR={_format_float(summary.iqr)}%, "
        f"95% CI [{_format_float(summary.ci95_low)}%, {_format_float(summary.ci95_high)}%]"
    )


def _format_anova_line(result: AnovaResult) -> str:
    return (
        f"{result.method}: F={_format_float(result.statistic)}, "
        f"df=({_format_float(result.df_num)}, {_format_float(result.df_den)}), "
        f"p={_format_float(result.p_value)}, eta2={_format_float(result.eta_squared)}, "
        f"omega2={_format_float(result.omega_squared)}; {result.note}"
    )


def _format_dixon_q_rule() -> str:
    return (
        f"Q10; alpha={DIXON_Q_ALPHA:g}; n={min(DIXON_Q_CRITICAL_VALUES)}-{max(DIXON_Q_CRITICAL_VALUES)}; "
        "Q = gap / range; checks one low or high endpoint only."
    )


def format_dixon_exclusion_reason(recommendation: DixonQRecommendation) -> str:
    return (
        f"Dixon Q10 alpha={recommendation.alpha:g}: {recommendation.side} endpoint, "
        f"Q={_format_float(recommendation.q_statistic)} > critical={_format_float(recommendation.critical_value)}, "
        f"Delta %={_format_float(recommendation.delta_percent)}."
    )


def _format_dixon_recommendation_line(recommendation: DixonQRecommendation) -> str:
    quality_warnings = "; ".join(recommendation.warnings) if recommendation.warnings else "None"
    return (
        f"{recommendation.group_name}/{recommendation.file_name}: side={recommendation.side}, "
        f"delta={_format_float(recommendation.delta_percent)}%, "
        f"nearest={_format_float(recommendation.nearest_delta_percent)}%, "
        f"gap={_format_float(recommendation.gap_delta_percent)}%, "
        f"range={_format_float(recommendation.range_delta_percent)}%, "
        f"Q={_format_float(recommendation.q_statistic)}, "
        f"critical={_format_float(recommendation.critical_value)}; "
        f"quality warnings={quality_warnings}; {recommendation.note}"
    )


def _format_outlier_review_rule() -> str:
    return (
        f"Delta % only; group n >= {OUTLIER_MIN_GROUP_N}; "
        f"leave-one-out peer median/MAD; abs(modified z) >= {OUTLIER_MODIFIED_Z_THRESHOLD:g}; "
        "not automatically excluded."
    )


def _format_outlier_candidate_line(candidate: StatisticalOutlierCandidate) -> str:
    quality_warnings = "; ".join(candidate.warnings) if candidate.warnings else "None"
    return (
        f"{candidate.group_name}/{candidate.sample_name}: delta={_format_float(candidate.delta_percent)}%, "
        f"peer median={_format_float(candidate.peer_median)}%, "
        f"diff={_format_float(candidate.delta_from_peer_median)}%, "
        f"peer MAD={_format_float(candidate.peer_mad)}%, "
        f"modified z={_format_float(candidate.modified_z_score)}; "
        f"quality warnings={quality_warnings}; {candidate.note}"
    )


def format_statistics_result(result: StatisticalAnalysisResult) -> str:
    lines = [
        "Statistical Analysis - Delta %",
        f"Root: {result.root_path or 'N/A'}",
        f"SciPy available: {'yes' if result.scipy_available else 'no'}",
        "",
        "Warnings",
    ]
    if result.warnings:
        lines.extend(f"- {warning}" for warning in result.warnings)
    else:
        lines.append("- None")

    lines.extend(["", "Excluded Samples"])
    if result.excluded_samples:
        for excluded_sample in result.excluded_samples:
            reason = excluded_sample.reason or "No reason provided"
            method = f" [{excluded_sample.method}]" if excluded_sample.method else ""
            lines.append(f"{excluded_sample.group_name}/{excluded_sample.file_name}{method}: {reason}")
    else:
        lines.append("None")

    lines.extend(["", "Dixon Q Review", f"Rule: {_format_dixon_q_rule()}"])
    if result.dixon_review_notes:
        lines.extend(f"- {note}" for note in result.dixon_review_notes)
    if result.dixon_recommendations:
        lines.append(f"Recommended Dixon Exclusions: {len(result.dixon_recommendations)}")
        lines.extend(_format_dixon_recommendation_line(recommendation) for recommendation in result.dixon_recommendations)
    else:
        lines.append("No Dixon Q recommended exclusions.")

    lines.extend(["", "Outlier Review", f"Rule: {_format_outlier_review_rule()}"])
    if result.outlier_review_notes:
        lines.extend(f"- {note}" for note in result.outlier_review_notes)
    if result.outlier_candidates:
        lines.append(f"Candidate count: {len(result.outlier_candidates)}")
        lines.extend(_format_outlier_candidate_line(candidate) for candidate in result.outlier_candidates)
    else:
        lines.append("No robust outlier candidates.")

    lines.extend(["", "Descriptive Statistics"])
    if result.group_statistics:
        lines.extend(_format_descriptive_line(summary) for summary in result.group_statistics)
    else:
        lines.append("No valid groups.")

    lines.extend(
        [
            "",
            "Assumption Checks",
            f"{result.variance_check.method}: statistic={_format_float(result.variance_check.statistic)}, "
            f"p={_format_float(result.variance_check.p_value)}; {result.variance_check.note}",
        ]
    )
    for summary in result.group_statistics:
        lines.append(
            f"{summary.group_name} Shapiro-Wilk: W={_format_float(summary.shapiro_statistic)}, "
            f"p={_format_float(summary.shapiro_p_value)}; {summary.normality_note}"
        )

    lines.extend(["", "One-way ANOVA", _format_anova_line(result.anova)])
    lines.extend(["", "ANOVA Sensitivity After Dixon Recommendations"])
    if result.dixon_sensitivity_anova is None:
        lines.append("Not run because Dixon Q made no recommended exclusions.")
    else:
        lines.append(_format_anova_line(result.dixon_sensitivity_anova))

    lines.extend(["", "Per-Sample Delta %"])
    if result.samples:
        for sample in result.samples:
            warnings = "; ".join(sample.warnings) if sample.warnings else "None"
            lines.append(
                f"{sample.group_name}/{sample.sample_name}: delta={_format_float(sample.delta_percent)}%, "
                f"baseline={_format_float(sample.baseline)} pF, raw delta={_format_float(sample.delta_capacitance)} pF, "
                f"drop={_format_float(sample.drop_time)}s, warnings={warnings}"
            )
    else:
        lines.append("No valid samples.")

    return "\n".join(lines)


def _rows_for_group_statistics(result: StatisticalAnalysisResult) -> Iterable[list[str]]:
    yield ["Descriptive Statistics"]
    yield [
        "group",
        "n",
        "mean_delta_percent",
        "sd",
        "sem",
        "median",
        "q1",
        "q3",
        "iqr",
        "ci95_low",
        "ci95_high",
        "shapiro_w",
        "shapiro_p",
        "note",
    ]
    for summary in result.group_statistics:
        yield [
            summary.group_name,
            str(summary.n),
            str(summary.mean),
            str(summary.sd),
            str(summary.sem),
            str(summary.median),
            str(summary.q1),
            str(summary.q3),
            str(summary.iqr),
            str(summary.ci95_low),
            str(summary.ci95_high),
            str(summary.shapiro_statistic),
            str(summary.shapiro_p_value),
            summary.normality_note,
        ]


def _rows_for_outlier_review(result: StatisticalAnalysisResult) -> Iterable[list[str]]:
    yield ["Outlier Review Candidates"]
    yield ["rule", _format_outlier_review_rule()]
    if result.outlier_review_notes:
        yield ["Notes"]
        for note in result.outlier_review_notes:
            yield [note]
    yield [
        "group",
        "sample",
        "delta_percent",
        "peer_median",
        "delta_from_peer_median",
        "peer_mad",
        "modified_z_score",
        "quality_warnings",
        "note",
    ]
    if result.outlier_candidates:
        for candidate in result.outlier_candidates:
            yield [
                candidate.group_name,
                candidate.sample_name,
                str(candidate.delta_percent),
                str(candidate.peer_median),
                str(candidate.delta_from_peer_median),
                str(candidate.peer_mad),
                str(candidate.modified_z_score),
                "; ".join(candidate.warnings),
                candidate.note,
            ]
    else:
        yield ["None"]


def _rows_for_dixon_q_review(result: StatisticalAnalysisResult) -> Iterable[list[str]]:
    yield ["Dixon Q Review"]
    yield ["rule", _format_dixon_q_rule()]
    if result.dixon_review_notes:
        yield ["Notes"]
        for note in result.dixon_review_notes:
            yield [note]
    yield ["Recommended Dixon Exclusions"]
    yield [
        "group",
        "file",
        "sample",
        "side",
        "delta_percent",
        "nearest_delta_percent",
        "gap_delta_percent",
        "range_delta_percent",
        "q_statistic",
        "critical_value",
        "alpha",
        "quality_warnings",
        "note",
    ]
    if result.dixon_recommendations:
        for recommendation in result.dixon_recommendations:
            yield [
                recommendation.group_name,
                recommendation.file_name,
                recommendation.sample_name,
                recommendation.side,
                str(recommendation.delta_percent),
                str(recommendation.nearest_delta_percent),
                str(recommendation.gap_delta_percent),
                str(recommendation.range_delta_percent),
                str(recommendation.q_statistic),
                str(recommendation.critical_value),
                str(recommendation.alpha),
                "; ".join(recommendation.warnings),
                recommendation.note,
            ]
    else:
        yield ["None"]


def statistics_result_to_csv_rows(result: StatisticalAnalysisResult) -> list[list[str]]:
    rows: list[list[str]] = [
        ["Statistical Analysis - Delta %"],
        ["root_path", result.root_path],
        ["scipy_available", "yes" if result.scipy_available else "no"],
        [],
        ["Warnings"],
    ]
    if result.warnings:
        rows.extend([[warning] for warning in result.warnings])
    else:
        rows.append(["None"])

    rows.extend(
        [
            [],
            ["Excluded Samples"],
            ["group", "file", "method", "reason"],
        ]
    )
    if result.excluded_samples:
        for excluded_sample in result.excluded_samples:
            rows.append([excluded_sample.group_name, excluded_sample.file_name, excluded_sample.method, excluded_sample.reason])
    else:
        rows.append(["None"])

    rows.append([])
    rows.extend(_rows_for_dixon_q_review(result))
    rows.append([])
    rows.extend(_rows_for_outlier_review(result))
    rows.append([])
    rows.extend(_rows_for_group_statistics(result))
    rows.extend(
        [
            [],
            ["Assumption Checks"],
            ["method", "statistic", "p_value", "note"],
            [
                result.variance_check.method,
                str(result.variance_check.statistic),
                str(result.variance_check.p_value),
                result.variance_check.note,
            ],
            [],
            ["One-way ANOVA"],
            ["method", "group_count", "sample_count", "df_num", "df_den", "statistic", "p_value", "eta_squared", "omega_squared", "note"],
            [
                result.anova.method,
                str(result.anova.group_count),
                str(result.anova.sample_count),
                str(result.anova.df_num),
                str(result.anova.df_den),
                str(result.anova.statistic),
                str(result.anova.p_value),
                str(result.anova.eta_squared),
                str(result.anova.omega_squared),
                result.anova.note,
            ],
            [],
            ["ANOVA Sensitivity After Dixon Recommendations"],
            ["method", "group_count", "sample_count", "df_num", "df_den", "statistic", "p_value", "eta_squared", "omega_squared", "note"],
        ]
    )
    if result.dixon_sensitivity_anova is None:
        rows.append(["None"])
    else:
        rows.append(
            [
                result.dixon_sensitivity_anova.method,
                str(result.dixon_sensitivity_anova.group_count),
                str(result.dixon_sensitivity_anova.sample_count),
                str(result.dixon_sensitivity_anova.df_num),
                str(result.dixon_sensitivity_anova.df_den),
                str(result.dixon_sensitivity_anova.statistic),
                str(result.dixon_sensitivity_anova.p_value),
                str(result.dixon_sensitivity_anova.eta_squared),
                str(result.dixon_sensitivity_anova.omega_squared),
                result.dixon_sensitivity_anova.note,
            ]
        )
    rows.extend(
        [
            [],
            ["Per-Sample Delta %"],
            [
                "group",
                "sample",
                "delta_percent",
                "delta_capacitance",
                "baseline",
                "drop_time",
                "baseline_warning_status",
                "drop_detection_source",
                "warnings",
            ],
        ]
    )
    for sample in result.samples:
        rows.append(
            [
                sample.group_name,
                sample.sample_name,
                str(sample.delta_percent),
                str(sample.delta_capacitance),
                str(sample.baseline),
                str(sample.drop_time),
                sample.baseline_warning_status,
                sample.drop_detection_source,
                "; ".join(sample.warnings),
            ]
        )
    return rows


def write_statistics_csv(result: StatisticalAnalysisResult, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerows(statistics_result_to_csv_rows(result))
