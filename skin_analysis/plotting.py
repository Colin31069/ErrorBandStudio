from __future__ import annotations

import os
from itertools import cycle
from typing import Any, Sequence

import numpy as np

from .analysis import analyze_signal, load_capacitance_values, process_single_file
from .config import LINE_STYLE_CYCLE
from .csv_safety import spreadsheet_safe_cell
from .exclusions import current_excluded_file_names, unhonored_excluded_samples
from .filesystem import list_xlsx_files
from .metadata import load_experiment_metadata
from .models import (
    BaselineWarningStatus, ErrorBandGroup, ErrorBandPayload, ErrorBandSettings,
    ExperimentMetadata, PlotItem, PlotPayload, PlotSettings, ProcessedSignal,
)

NO_LEGEND_LABEL = "_nolegend_"
ERROR_BAND_UNITS = {
    "Delta": "Baseline-subtracted ΔC (pF)",
    "DeltaPct": "Relative change (%)",
    "Raw": "Capacitance (pF)",
}
ERROR_BAND_COLORS = ("#0072B2", "#009E73", "#E69F00", "#CC79A7", "#D55E00", "#56B4E9", "#332288", "#666666")


def validate_error_band_settings(settings: ErrorBandSettings) -> None:
    if settings.display_mode not in ERROR_BAND_UNITS:
        raise ValueError("不支援的顯示單位。")
    for name, value, strictly_positive in (
        ("取樣間隔", settings.sample_interval_sec, True),
        ("Baseline 長度", settings.baseline_duration_sec, True),
        ("施加時間", settings.drug_apply_time_sec, False),
        ("搜尋容許範圍", settings.drug_apply_tolerance_sec, False),
        ("Baseline 警示門檻", settings.baseline_warning_threshold_pct, False),
    ):
        if not np.isfinite(value) or value < 0 or (strictly_positive and value == 0):
            raise ValueError(f"{name}必須是{'大於' if strictly_positive else '不小於'} 0 的有限數值。")


def aggregate_error_band(
    group_name: str,
    signals: Sequence[ProcessedSignal],
    sample_names: Sequence[str],
    settings: ErrorBandSettings,
) -> ErrorBandGroup:
    """Compute pointwise statistics only where every included replicate exists."""
    validate_error_band_settings(settings)
    if not signals or len(signals) != len(sample_names):
        raise ValueError("沒有可分析的重複樣本，或樣本名稱數量不符。")
    aligned: list[tuple[np.ndarray, np.ndarray]] = []
    for signal in signals:
        x = signal.time_sec - signal.drop_time
        y = signal.capacitance
        if len(x) != len(y) or len(x) < 2 or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
            raise ValueError("樣本必須包含至少兩筆有效的時間與電容資料。")
        if np.any(np.diff(x) <= 0):
            raise ValueError("樣本時間必須依序遞增。")
        if settings.display_mode == "Delta":
            y = y - signal.initial_avg
        elif settings.display_mode == "DeltaPct":
            if abs(signal.initial_avg) < 1e-12:
                raise ValueError("Baseline 為 0，無法計算相對變化百分比。")
            y = (y - signal.initial_avg) / signal.initial_avg * 100.
        aligned.append((x, y))

    interval = settings.sample_interval_sec
    first_tick = int(np.ceil(max(x[0] for x, _ in aligned) / interval - 1e-9))
    last_tick = int(np.floor(min(x[-1] for x, _ in aligned) / interval + 1e-9))
    if last_tick <= first_tick or last_tick <= 0:
        raise ValueError("樣本對齊後沒有足夠的共同反應時間，請檢查施加時間與檔案長度。")
    time_sec = np.arange(first_tick, last_tick + 1, dtype=float) * interval
    # Interpolate within the shared interval; never extrapolate or pad short files.
    values = np.vstack([np.interp(time_sec, x, y) for x, y in aligned])
    mean = np.mean(values, axis=0)
    n = len(signals)
    warnings: list[str] = []
    if n > 1:
        sd = np.std(values, axis=0, ddof=1)
        sem = sd / np.sqrt(n)
    else:
        sd = np.full_like(mean, np.nan)
        sem = np.full_like(mean, np.nan)
        warnings.append(f"{group_name}：n=1，只顯示曲線；SD、SEM 無法估計。")
    return ErrorBandGroup(group_name, tuple(sample_names), time_sec, mean, sd, sem, tuple(warnings))


def build_error_band_payload(
    root_path: str,
    group_names: Sequence[str],
    settings: ErrorBandSettings,
) -> ErrorBandPayload:
    validate_error_band_settings(settings)
    groups: list[ErrorBandGroup] = []
    warnings: list[str] = []
    for group_name in dict.fromkeys(group_names):
        folder = os.path.join(root_path, group_name)
        metadata, metadata_warning = load_experiment_metadata(folder)
        if metadata_warning:
            warnings.append(f"{group_name}：metadata 無法讀取，暫不納入此組，請修正後重試。{metadata_warning}")
            continue
        files = list_xlsx_files(folder)
        unhonored = unhonored_excluded_samples(metadata.excluded_samples, files)
        if unhonored:
            names = ", ".join(entry.file_name for entry in unhonored)
            warnings.append(f"{group_name}：排除名單與目前樣本數不相容，暫停此組以避免重新納入：{names}")
            continue
        excluded = current_excluded_file_names(metadata.excluded_samples, files)
        signals: list[ProcessedSignal] = []
        sample_names: list[str] = []
        group_warnings: list[str] = []
        for file_name in files:
            label = f"{group_name}/{file_name}"
            if file_name in excluded:
                reason = next((entry.reason for entry in metadata.excluded_samples if entry.file_name.casefold() == file_name.casefold()), "")
                group_warnings.append(f"{label}：依既有 metadata 排除。{reason}")
                continue
            try:
                data = load_capacitance_values(os.path.join(folder, file_name))
                signal = analyze_signal(
                    data,
                    baseline_warning_threshold_pct=settings.baseline_warning_threshold_pct,
                    baseline_duration_sec=settings.baseline_duration_sec,
                    drug_apply_time_sec=settings.drug_apply_time_sec,
                    drug_apply_tolerance_sec=settings.drug_apply_tolerance_sec,
                    sample_interval_sec=settings.sample_interval_sec,
                )
                if signal is None or signal.time_sec[-1] <= signal.drop_time:
                    raise ValueError("沒有施加後的資料，請檢查施加時間與檔案長度。")
                if settings.display_mode == "DeltaPct" and abs(signal.initial_avg) < 1e-12:
                    raise ValueError("Baseline 為 0，無法計算百分比。")
            except Exception as exc:
                group_warnings.append(f"{label}：未納入；{exc}")
                continue
            signals.append(signal)
            sample_names.append(file_name)
            group_warnings.extend(f"{label}：{detail}" for detail in signal.timing_warning_details)
            group_warnings.extend(f"{label}：Baseline {detail}" for detail in build_baseline_warning_details(signal))
        try:
            group = aggregate_error_band(group_name, signals, sample_names, settings)
        except ValueError as exc:
            warnings.extend(group_warnings)
            warnings.append(f"{group_name}：未繪製；{exc}")
            continue
        groups.append(group)
        warnings.extend(group_warnings)
        warnings.extend(group.warnings)
    return ErrorBandPayload(root_path, settings, tuple(groups), tuple(warnings))


def render_error_band_plot(ax, payload: ErrorBandPayload, band_type: str = "SD", title: str = "", show_grid: bool = False) -> None:
    if band_type not in ("SD", "SEM"):
        raise ValueError("誤差範圍必須為 SD 或 SEM。")
    ax.clear()
    for index, group in enumerate(payload.groups):
        color = ERROR_BAND_COLORS[index % len(ERROR_BAND_COLORS)]
        error = group.sd if band_type == "SD" else group.sem
        if group.n > 1:
            ax.fill_between(group.time_sec, group.mean - error, group.mean + error, color=color, alpha=.20, linewidth=0)
        ax.plot(group.time_sec, group.mean, color=color, linestyle=LINE_STYLE_CYCLE[index % len(LINE_STYLE_CYCLE)], linewidth=2., label=f"{group.legend_label or group.group_name} (n={group.n})")
    ax.axvline(0., color="#667085", linestyle="--", linewidth=1., alpha=.8)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(ERROR_BAND_UNITS[payload.settings.display_mode])
    ax.set_title(title.strip())
    if show_grid:
        ax.grid(True, alpha=.2)
    else:
        ax.grid(False)
    ax.margins(x=.01)
    if payload.groups:
        ax.legend(frameon=False, ncol=2 if len(payload.groups) >= 4 else 1)
        ax.text(.015, .98, f"Mean ± {band_type}", transform=ax.transAxes, va="top", color="#667085", fontsize=9)


def write_error_band_csv(payload: ErrorBandPayload, path: str, band_type: str = "SD") -> None:
    import csv

    if band_type not in ("SD", "SEM"):
        raise ValueError("誤差範圍必須為 SD 或 SEM。")
    with open(path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("group", "time_sec", "n", "mean", "sd", "sem", "band_type", "lower", "upper", "unit", "sample_files", "sample_interval_sec", "baseline_duration_sec", "drug_apply_time_sec", "drug_apply_tolerance_sec", "warnings", "legend_label"))
        for group in payload.groups:
            for time_sec, mean, sd, sem in zip(group.time_sec, group.mean, group.sd, group.sem):
                error = sd if band_type == "SD" else sem
                writer.writerow(tuple(spreadsheet_safe_cell(cell) for cell in (
                    group.group_name, f"{time_sec:.10g}", group.n, mean,
                    sd if group.n > 1 else "", sem if group.n > 1 else "", band_type,
                    mean - error if group.n > 1 else "", mean + error if group.n > 1 else "",
                    ERROR_BAND_UNITS[payload.settings.display_mode], "; ".join(group.sample_names),
                    payload.settings.sample_interval_sec, payload.settings.baseline_duration_sec,
                    payload.settings.drug_apply_time_sec, payload.settings.drug_apply_tolerance_sec,
                    " | ".join(payload.warnings),
                    group.legend_label or group.group_name,
                )))


def display_mode_to_y_unit(display_mode: str) -> str:
    if display_mode == "Norm":
        return "Normalized (%)"
    if display_mode == "Base":
        return "Baseline Capacitance (pF)"
    return "Raw Capacitance (pF)"


def transform_signal_for_display(
    signal: ProcessedSignal,
    display_mode: str,
) -> tuple[np.ndarray, np.ndarray, str]:
    # Align plotted time to the detected drop so overlayed traces share t=0.
    x_plot = signal.time_sec - signal.drop_time
    y_plot = signal.capacitance
    base = signal.initial_avg
    delta_raw = signal.delta_capacitance

    if display_mode == "Norm":
        if abs(base) < 1e-12:
            raise ValueError("Cannot normalize a signal with a zero baseline.")
        y_plot = (signal.capacitance / base) * 100
        delta_pct = (((base + delta_raw) / base) * 100) - 100
        delta_str = f"Δ:{delta_pct:.2f}%"
    elif display_mode == "Base":
        limit = min(signal.effective_baseline_points, len(signal.capacitance))
        x_plot = x_plot[:limit]
        y_plot = signal.capacitance[:limit]
        delta_str = f"Δ:{delta_raw:.2f}pF"
    else:
        delta_str = f"Δ:{delta_raw:.2f}pF"

    return x_plot, y_plot, delta_str


def build_baseline_warning_details(signal: ProcessedSignal) -> tuple[str, ...]:
    details: list[str] = []
    if signal.baseline_tail_warning_hit:
        details.append(f"尾端均值偏移 {signal.baseline_tail_offset_pct:+.2f}%")
    if signal.baseline_rise_warning_hit:
        details.append(f"連續上升至 {signal.baseline_rise_offset_pct:+.2f}%")
    return tuple(details)


def warning_status_suffix(status: BaselineWarningStatus) -> str:
    if status == "warning":
        return " [注意]"
    if status == "inaccurate":
        return " [不準確]"
    return ""


def build_medicine_legend_summary(metadata: ExperimentMetadata) -> str:
    parts: list[str] = []
    for entry in metadata.medicines[: metadata.medicine_count]:
        name = entry.name.strip()
        dose = entry.dose.strip()
        if name and dose:
            parts.append(f"{name} {dose}")
        elif name:
            parts.append(name)
        elif dose:
            parts.append(dose)
    return " / ".join(parts)


def build_overlay_legend_group_label(settings: PlotSettings) -> str:
    medicine_summary = build_medicine_legend_summary(settings.metadata)
    if medicine_summary:
        return medicine_summary
    return settings.experiment_name.strip()


def build_legend_label(
    sample_name: str,
    settings: PlotSettings,
    base: float,
    delta_str: str,
    warning_status: BaselineWarningStatus,
) -> str:
    if settings.is_overlay and settings.use_group_color:
        return NO_LEGEND_LABEL

    prefix = f"N {sample_name}{warning_status_suffix(warning_status)}"

    if settings.leg_style == "Simple":
        return prefix

    info_parts: list[str] = []
    if settings.show_base:
        info_parts.append(f"Base:{base:.2f} pF")
    if settings.show_delta:
        info_parts.append(delta_str)

    if info_parts:
        return prefix + " (" + ", ".join(info_parts) + ")"
    return prefix


def build_plot_title(
    experiment_name: str,
    metadata: ExperimentMetadata,
    custom_title: str = "",
) -> str:
    custom_title = custom_title.strip()
    if custom_title:
        return custom_title

    title_parts = [experiment_name]
    for entry in metadata.medicines[: metadata.medicine_count]:
        name = entry.name.strip()
        dose = entry.dose.strip()
        if not name and not dose:
            continue
        if name and dose:
            title_parts.append(f"{name}: {dose}")
        elif name:
            title_parts.append(name)
        else:
            title_parts.append(dose)
    return " | ".join(title_parts)


def build_plot_item(
    signal: ProcessedSignal,
    sample_name: str,
    settings: PlotSettings,
    line_style: str,
    line_color: Any | None,
) -> PlotItem:
    x_plot, y_plot, delta_str = transform_signal_for_display(signal, settings.display_mode)
    label_txt = build_legend_label(
        sample_name,
        settings,
        signal.initial_avg,
        delta_str,
        signal.baseline_warning_status,
    )
    return PlotItem(
        sample_name=sample_name,
        x_plot=x_plot,
        y_plot=y_plot,
        label_txt=label_txt,
        drop_time=0.0,
        line_style=line_style,
        line_color=line_color,
        effective_baseline_duration_sec=signal.effective_baseline_duration_sec,
        drop_detection_source=signal.drop_detection_source,
        timing_warning_details=signal.timing_warning_details,
        baseline_warning_status=signal.baseline_warning_status,
        baseline_warning_details=build_baseline_warning_details(signal),
    )


def build_plot_payload(settings: PlotSettings, group_color: Any | None) -> PlotPayload:
    style_cycler = cycle(LINE_STYLE_CYCLE)
    plot_items: list[PlotItem] = []
    warnings: list[str] = []
    excluded_file_names = current_excluded_file_names(settings.metadata.excluded_samples, settings.all_files)

    for fname in settings.all_files:
        if fname in excluded_file_names:
            continue

        file_path = os.path.join(settings.target_dir, fname)
        signal = process_single_file(
            file_path,
            baseline_warning_threshold_pct=settings.baseline_warning_threshold_pct,
            baseline_duration_sec=settings.baseline_duration_sec,
            drug_apply_time_sec=settings.drug_apply_time_sec,
            drug_apply_tolerance_sec=settings.drug_apply_tolerance_sec,
        )
        if signal is None:
            warnings.append(f"{fname}: 無法解析或分析 Excel 資料；請檢查欄名、缺值及資料格式。")
            continue

        line_style = next(style_cycler) if settings.use_group_color else "-"
        line_color = group_color if settings.use_group_color else None
        sample_name = os.path.splitext(fname)[0]
        try:
            plot_items.append(build_plot_item(signal, sample_name, settings, line_style, line_color))
        except ValueError as exc:
            warnings.append(f"{fname}: {exc}")

    return PlotPayload(
        settings=settings,
        title=build_plot_title(settings.experiment_name, settings.metadata, settings.custom_title),
        y_unit=display_mode_to_y_unit(settings.display_mode),
        plot_items=plot_items,
        warnings=tuple(warnings),
    )
