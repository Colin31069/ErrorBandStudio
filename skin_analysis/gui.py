from __future__ import annotations

import os
from dataclasses import replace
from queue import Empty, SimpleQueue
import threading
import tkinter as tk
from itertools import cycle
from tkinter import filedialog, messagebox, simpledialog, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from .config import (
    COLOR_PALETTE,
    DEFAULT_BASELINE_DURATION_SEC,
    DEFAULT_BASELINE_WARNING_THRESHOLD_PCT,
    DEFAULT_MEDICINE_COUNT,
    DEFAULT_DRUG_APPLY_TIME_SEC,
    DEFAULT_DRUG_APPLY_TOLERANCE_SEC,
    DIXON_Q_EXCLUSION_METHOD,
    DEFAULT_ROOT_PATH,
    MAX_MEDICINES,
)
from .exclusions import current_excluded_samples, max_excluded_samples, unhonored_excluded_samples
from .filesystem import get_subfolders, list_xlsx_files, normalize_directory_path, resolve_directory_path
from .metadata import (
    default_experiment_metadata,
    load_experiment_metadata,
    metadata_file_path,
    save_experiment_metadata,
)
from .models import ErrorBandPayload, ErrorBandSettings, ExcludedSample, ExperimentMetadata, MedicineEntry, PlotPayload, PlotSettings, StatisticalAnalysisResult
from .plotting import (
    build_error_band_payload, build_overlay_legend_group_label, build_plot_payload,
    build_plot_title, render_error_band_plot, validate_error_band_settings, write_error_band_csv,
)

OVERLAY_LEGEND_FONT_SIZE = 22


EXPORT_FILETYPES = (
    ("PNG image", "*.png"),
    ("PDF document", "*.pdf"),
    ("SVG vector image", "*.svg"),
    ("TIFF image", "*.tif *.tiff"),
    ("JPEG image", "*.jpg *.jpeg"),
    ("WebP image", "*.webp"),
)
SUPPORTED_EXPORT_EXTENSIONS = {
    ".eps",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".pgf",
    ".png",
    ".ps",
    ".raw",
    ".rgba",
    ".svg",
    ".svgz",
    ".tif",
    ".tiff",
    ".webp",
}
DEFAULT_EXPORT_EXTENSION = ".png"


class RawDataViewerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Raw Data Viewer - v3.5")
        self.geometry("1400x900")

        self.root_path = DEFAULT_ROOT_PATH
        self.root_path_var = tk.StringVar(value=self.root_path)

        self.show_drop_lines_var = tk.BooleanVar(value=True)
        self.show_grid_var = tk.BooleanVar(value=True)
        self.overlay_mode_var = tk.BooleanVar(value=False)
        self.display_mode_var = tk.StringVar(value="Norm")
        self.group_color_var = tk.BooleanVar(value=True)
        self.baseline_duration_var = tk.StringVar(value=f"{DEFAULT_BASELINE_DURATION_SEC:.1f}")
        self.drug_apply_time_var = tk.StringVar(value=f"{DEFAULT_DRUG_APPLY_TIME_SEC:.1f}")
        self.drug_apply_tolerance_var = tk.StringVar(value=f"{DEFAULT_DRUG_APPLY_TOLERANCE_SEC:.1f}")
        self.baseline_warning_threshold_var = tk.StringVar(
            value=f"{DEFAULT_BASELINE_WARNING_THRESHOLD_PCT:.1f}"
        )

        self.legend_style_var = tk.StringVar(value="Detailed")
        self.leg_show_base = tk.BooleanVar(value=True)
        self.leg_show_delta = tk.BooleanVar(value=False)
        self.experiment_var = tk.StringVar(value="")
        self.custom_plot_title_var = tk.StringVar(value="")
        self.medicine_count_var = tk.StringVar(value=str(DEFAULT_MEDICINE_COUNT))
        self.medicine_name_vars = [tk.StringVar(value="") for _ in range(MAX_MEDICINES)]
        self.medicine_dose_vars = [tk.StringVar(value="") for _ in range(MAX_MEDICINES)]
        self.sample_exclusion_status_var = tk.StringVar(value="")

        self.color_cycler = cycle(COLOR_PALETTE)
        self.is_plotting = False
        self._control_widgets: list[tk.Widget] = []
        self._metadata_widgets: list[tk.Widget] = []
        self._medicine_row_frames: list[ttk.LabelFrame] = []
        self._sample_file_names: list[str] = []
        self._excluded_samples: list[ExcludedSample] = []
        self._dixon_suggested_reasons: dict[str, str] = {}
        self._is_loading_metadata = False
        self._metadata_unreadable_folder = ""
        self._metadata_expanded = False
        self._metadata_toggle_text = tk.StringVar(value="")
        self._warning_dialog: tk.Toplevel | None = None
        self._last_default_plot_title = ""
        self._overlay_legend_entries: dict[str, object] = {}
        self._classic_results: SimpleQueue = SimpleQueue()

        self.create_widgets()
        self.custom_plot_title_var.trace_add("write", self._on_custom_plot_title_changed)
        self.refresh_folder_structure(show_errors=False)
        self.after(80, self._poll_classic_results)

    def create_widgets(self) -> None:
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        control_panel = ttk.LabelFrame(main_frame, text="Experiment Selection", padding="8")
        control_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        self.control_canvas = tk.Canvas(control_panel, width=310, highlightthickness=0, borderwidth=0)
        control_scrollbar = ttk.Scrollbar(control_panel, orient=tk.VERTICAL, command=self.control_canvas.yview)
        controls_frame = ttk.Frame(self.control_canvas)

        controls_frame.bind(
            "<Configure>",
            lambda _event: self.control_canvas.configure(scrollregion=self.control_canvas.bbox("all")),
        )
        self.control_canvas.create_window((0, 0), window=controls_frame, anchor="nw")
        self.control_canvas.configure(yscrollcommand=control_scrollbar.set)
        self.control_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        control_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        controls_frame.bind("<Enter>", lambda _event: self.control_canvas.bind_all("<MouseWheel>", self._on_control_mousewheel))
        controls_frame.bind("<Leave>", lambda _event: self.control_canvas.unbind_all("<MouseWheel>"))

        ttk.Label(controls_frame, text="Root Path").pack(anchor=tk.W)
        root_path_frame = ttk.Frame(controls_frame)
        root_path_frame.pack(fill=tk.X, pady=(0, 8))

        self.root_path_entry = ttk.Entry(root_path_frame, textvariable=self.root_path_var)
        self.root_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.root_path_entry.bind("<Return>", self.refresh_folder_structure)
        self._control_widgets.append(self.root_path_entry)

        self.browse_root_btn = ttk.Button(root_path_frame, text="Browse...", command=self.select_root)
        self.browse_root_btn.pack(side=tk.RIGHT)
        self._control_widgets.append(self.browse_root_btn)

        ttk.Label(controls_frame, text="Experiment Folder").pack(anchor=tk.W)
        self.experiment_list = self._create_selection_list(controls_frame, self._on_experiment_selected, height=8)
        self.experiment_list.pack(fill=tk.X, pady=(0, 5))

        self.refresh_btn = ttk.Button(controls_frame, text="↻ Refresh List", command=self.refresh_folder_structure)
        self.refresh_btn.pack(fill=tk.X, pady=5)
        self._control_widgets.append(self.refresh_btn)

        ttk.Label(controls_frame, text="Plot Title (optional)").pack(anchor=tk.W, pady=(8, 0))
        self.custom_plot_title_entry = ttk.Entry(
            controls_frame,
            textvariable=self.custom_plot_title_var,
        )
        self.custom_plot_title_entry.pack(fill=tk.X, pady=(2, 0))
        self._control_widgets.append(self.custom_plot_title_entry)

        ttk.Separator(controls_frame, orient="horizontal").pack(fill="x", pady=10)

        self.metadata_section = ttk.Frame(controls_frame)
        self.metadata_section.pack(fill=tk.X, pady=5)

        self.metadata_toggle_btn = ttk.Button(
            self.metadata_section,
            textvariable=self._metadata_toggle_text,
            command=self._toggle_metadata_section,
        )
        self.metadata_toggle_btn.pack(fill=tk.X)
        self._control_widgets.append(self.metadata_toggle_btn)

        self.metadata_frame = ttk.LabelFrame(self.metadata_section, padding=5)
        self.metadata_frame.pack(fill=tk.X, pady=(5, 0))

        count_frame = ttk.Frame(self.metadata_frame)
        count_frame.pack(fill=tk.X)
        ttk.Label(count_frame, text="Medicine Count").pack(side=tk.LEFT)
        self.medicine_count_combo = ttk.Combobox(
            count_frame,
            textvariable=self.medicine_count_var,
            values=[str(index) for index in range(MAX_MEDICINES + 1)],
            state="readonly",
            width=5,
        )
        self.medicine_count_combo.pack(side=tk.RIGHT)
        self.medicine_count_combo.bind("<<ComboboxSelected>>", self._on_medicine_count_changed)
        self._metadata_widgets.append(self.medicine_count_combo)

        self.medicine_rows_container = ttk.Frame(self.metadata_frame)
        self.medicine_rows_container.pack(fill=tk.X, pady=(8, 0))
        self._build_medicine_rows()
        self._set_visible_medicine_rows(DEFAULT_MEDICINE_COUNT)
        self._set_metadata_expanded(False)

        ttk.Separator(controls_frame, orient="horizontal").pack(fill="x", pady=10)

        sample_exclusion_frame = ttk.LabelFrame(controls_frame, text="Sample Exclusion", padding=5)
        sample_exclusion_frame.pack(fill=tk.X, pady=5)

        ttk.Label(sample_exclusion_frame, textvariable=self.sample_exclusion_status_var).pack(anchor=tk.W)
        self.sample_exclusion_list = tk.Listbox(sample_exclusion_frame, height=6, exportselection=False)
        self.sample_exclusion_list.pack(fill=tk.X, pady=(4, 5))

        sample_exclusion_buttons = ttk.Frame(sample_exclusion_frame)
        sample_exclusion_buttons.pack(fill=tk.X)
        self.exclude_sample_btn = ttk.Button(
            sample_exclusion_buttons,
            text="Exclude Selected",
            command=self.exclude_selected_sample,
        )
        self.exclude_sample_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self._control_widgets.append(self.exclude_sample_btn)

        self.restore_sample_btn = ttk.Button(
            sample_exclusion_buttons,
            text="Restore Selected",
            command=self.restore_selected_sample,
        )
        self.restore_sample_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._control_widgets.append(self.restore_sample_btn)

        self.run_dixon_btn = ttk.Button(
            sample_exclusion_frame,
            text="Run Dixon Q",
            command=self.run_dixon_q_for_current_experiment,
        )
        self.run_dixon_btn.pack(fill=tk.X, pady=(5, 0))
        self._control_widgets.append(self.run_dixon_btn)
        self._refresh_sample_exclusion_list()

        ttk.Separator(controls_frame, orient="horizontal").pack(fill="x", pady=10)

        ttk.Label(controls_frame, text="Display Unit:").pack(anchor=tk.W)
        self.display_norm_rb = ttk.Radiobutton(
            controls_frame,
            text="Normalized (%)",
            variable=self.display_mode_var,
            value="Norm",
        )
        self.display_norm_rb.pack(anchor=tk.W)
        self._control_widgets.append(self.display_norm_rb)
        self.display_raw_rb = ttk.Radiobutton(
            controls_frame,
            text="Raw Data (pF)",
            variable=self.display_mode_var,
            value="Raw",
        )
        self.display_raw_rb.pack(anchor=tk.W)
        self._control_widgets.append(self.display_raw_rb)
        self.display_base_rb = ttk.Radiobutton(
            controls_frame,
            text="Baseline Only (Raw Baseline Window)",
            variable=self.display_mode_var,
            value="Base",
        )
        self.display_base_rb.pack(anchor=tk.W)
        self._control_widgets.append(self.display_base_rb)
        ttk.Separator(controls_frame, orient="horizontal").pack(fill="x", pady=10)

        timing_frame = ttk.LabelFrame(controls_frame, text="Timing Controls", padding=5)
        timing_frame.pack(fill=tk.X, pady=5)
        ttk.Label(timing_frame, text="Baseline Duration (s)").pack(anchor=tk.W)
        self.baseline_duration_spinbox = ttk.Spinbox(
            timing_frame,
            from_=0.1,
            to=600.0,
            increment=0.1,
            textvariable=self.baseline_duration_var,
            width=8,
        )
        self.baseline_duration_spinbox.pack(anchor=tk.W, pady=(2, 6))
        self._control_widgets.append(self.baseline_duration_spinbox)

        ttk.Label(timing_frame, text="Drug Apply Time (s)").pack(anchor=tk.W)
        self.drug_apply_time_spinbox = ttk.Spinbox(
            timing_frame,
            from_=0.0,
            to=600.0,
            increment=0.1,
            textvariable=self.drug_apply_time_var,
            width=8,
        )
        self.drug_apply_time_spinbox.pack(anchor=tk.W, pady=(2, 6))
        self._control_widgets.append(self.drug_apply_time_spinbox)

        ttk.Label(timing_frame, text="Apply Window +/- (s)").pack(anchor=tk.W)
        self.drug_apply_tolerance_spinbox = ttk.Spinbox(
            timing_frame,
            from_=0.0,
            to=600.0,
            increment=0.1,
            textvariable=self.drug_apply_tolerance_var,
            width=8,
        )
        self.drug_apply_tolerance_spinbox.pack(anchor=tk.W, pady=(2, 0))
        self._control_widgets.append(self.drug_apply_tolerance_spinbox)

        baseline_warning_frame = ttk.LabelFrame(controls_frame, text="Baseline Accuracy", padding=5)
        baseline_warning_frame.pack(fill=tk.X, pady=5)
        ttk.Label(baseline_warning_frame, text="Warning Threshold (%)").pack(anchor=tk.W)
        self.baseline_warning_spinbox = ttk.Spinbox(
            baseline_warning_frame,
            from_=0.0,
            to=100.0,
            increment=0.1,
            textvariable=self.baseline_warning_threshold_var,
            width=8,
        )
        self.baseline_warning_spinbox.pack(anchor=tk.W, pady=(2, 0))
        self._control_widgets.append(self.baseline_warning_spinbox)

        leg_frame = ttk.LabelFrame(controls_frame, text="Legend Customization", padding=5)
        leg_frame.pack(fill=tk.X, pady=5)

        self.legend_simple_rb = ttk.Radiobutton(
            leg_frame,
            text="Simple (e.g., N 1)",
            variable=self.legend_style_var,
            value="Simple",
        )
        self.legend_simple_rb.pack(anchor=tk.W)
        self._control_widgets.append(self.legend_simple_rb)
        self.legend_detailed_rb = ttk.Radiobutton(
            leg_frame,
            text="Detailed",
            variable=self.legend_style_var,
            value="Detailed",
        )
        self.legend_detailed_rb.pack(anchor=tk.W)
        self._control_widgets.append(self.legend_detailed_rb)

        detail_frame = ttk.Frame(leg_frame, padding=(15, 0, 0, 0))
        detail_frame.pack(fill=tk.X)
        self.leg_base_cb = ttk.Checkbutton(detail_frame, text="Baseline (Avg)", variable=self.leg_show_base)
        self.leg_base_cb.pack(anchor=tk.W)
        self._control_widgets.append(self.leg_base_cb)
        self.leg_delta_cb = ttk.Checkbutton(detail_frame, text="Delta (Δ)", variable=self.leg_show_delta)
        self.leg_delta_cb.pack(anchor=tk.W)
        self._control_widgets.append(self.leg_delta_cb)

        ttk.Label(controls_frame, text="Visual Options:", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10, 0))
        self.overlay_cb = ttk.Checkbutton(controls_frame, text="Overlay Mode", variable=self.overlay_mode_var)
        self.overlay_cb.pack(anchor=tk.W)
        self._control_widgets.append(self.overlay_cb)
        self.group_color_cb = ttk.Checkbutton(controls_frame, text="Experiment Color", variable=self.group_color_var)
        self.group_color_cb.pack(anchor=tk.W)
        self._control_widgets.append(self.group_color_cb)
        self.drop_lines_cb = ttk.Checkbutton(controls_frame, text="Show Drop Lines", variable=self.show_drop_lines_var)
        self.drop_lines_cb.pack(anchor=tk.W)
        self._control_widgets.append(self.drop_lines_cb)
        self.grid_cb = ttk.Checkbutton(
            controls_frame,
            text="Show Grid",
            variable=self.show_grid_var,
            command=self._on_grid_visibility_changed,
        )
        self.grid_cb.pack(anchor=tk.W)
        self._control_widgets.append(self.grid_cb)

        ttk.Separator(controls_frame, orient="horizontal").pack(fill="x", pady=(10, 10))
        self.plot_btn = ttk.Button(controls_frame, text="LOAD & PLOT", command=self.plot_data)
        self.plot_btn.pack(fill=tk.X, pady=(0, 5))
        self._control_widgets.append(self.plot_btn)
        self.clear_btn = ttk.Button(controls_frame, text="Clear Plot", command=self.clear_plot)
        self.clear_btn.pack(fill=tk.X, pady=5)
        self._control_widgets.append(self.clear_btn)
        self.export_btn = ttk.Button(controls_frame, text="Export Plot", command=self.export_plot)
        self.export_btn.pack(fill=tk.X, pady=5)
        self._control_widgets.append(self.export_btn)
        self.statistics_btn = ttk.Button(controls_frame, text="Statistics", command=self.open_statistics_dialog)
        self.statistics_btn.pack(fill=tk.X, pady=5)
        self._control_widgets.append(self.statistics_btn)

        plot_frame = ttk.Frame(main_frame)
        plot_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.toolbar_frame = ttk.Frame(plot_frame)
        self.toolbar_frame.pack(side=tk.TOP, fill=tk.X)

        self.canvas_frame = ttk.Frame(plot_frame)
        self.canvas_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.fig = Figure(figsize=(10, 7), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.canvas_frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.toolbar_frame, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side=tk.LEFT, fill=tk.X)
        self.clear_plot()

    def _build_medicine_rows(self) -> None:
        for index in range(MAX_MEDICINES):
            row_frame = ttk.LabelFrame(self.medicine_rows_container, text=f"Medicine {index + 1}", padding=5)

            ttk.Label(row_frame, text="Name").pack(anchor=tk.W)
            name_entry = ttk.Entry(row_frame, textvariable=self.medicine_name_vars[index])
            name_entry.pack(fill=tk.X, pady=(0, 5))
            name_entry.bind("<FocusOut>", self._on_metadata_field_blur)

            ttk.Label(row_frame, text="Dose").pack(anchor=tk.W)
            dose_entry = ttk.Entry(row_frame, textvariable=self.medicine_dose_vars[index])
            dose_entry.pack(fill=tk.X)
            dose_entry.bind("<FocusOut>", self._on_metadata_field_blur)

            self._medicine_row_frames.append(row_frame)
            self._metadata_widgets.extend([name_entry, dose_entry])

    def set_controls_enabled(self, enabled: bool) -> None:
        self.experiment_list.configure(state="normal" if enabled else "disabled")
        self.sample_exclusion_list.configure(state="normal" if enabled else "disabled")

        for widget in self._control_widgets + self._metadata_widgets:
            try:
                if enabled:
                    widget.state(["!disabled"])
                else:
                    widget.state(["disabled"])
            except tk.TclError:
                pass

        self.update_idletasks()

    def _create_selection_list(self, parent: ttk.Frame, callback, height: int = 2) -> tk.Listbox:
        listbox = tk.Listbox(parent, height=height, exportselection=False)
        listbox.bind("<<ListboxSelect>>", callback if callback is not None else lambda _event: None)
        return listbox

    def _update_metadata_toggle_label(self) -> None:
        prefix = "[-]" if self._metadata_expanded else "[+]"
        self._metadata_toggle_text.set(f"{prefix} Medicine Metadata")

    def _set_metadata_expanded(self, expanded: bool) -> None:
        self._metadata_expanded = expanded
        if expanded:
            if not self.metadata_frame.winfo_manager():
                self.metadata_frame.pack(fill=tk.X, pady=(5, 0))
        elif self.metadata_frame.winfo_manager():
            self.metadata_frame.pack_forget()
        self._update_metadata_toggle_label()

    def _toggle_metadata_section(self) -> None:
        self._set_metadata_expanded(not self._metadata_expanded)

    def _on_control_mousewheel(self, event) -> None:
        delta = 0
        if event.delta:
            delta = -1 * int(event.delta / 120)
        if delta:
            self.control_canvas.yview_scroll(delta, "units")

    def _set_list_values(self, listbox: tk.Listbox, variable: tk.StringVar, values: list[str]) -> None:
        listbox.delete(0, tk.END)
        for value in values:
            listbox.insert(tk.END, value)

        if not values:
            variable.set("")
            return

        current = variable.get()
        if current not in values:
            current = values[0]
        index = values.index(current)
        variable.set(current)
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(index)
        listbox.activate(index)
        listbox.see(index)

    def _selected_list_value(self, listbox: tk.Listbox) -> str:
        selection = listbox.curselection()
        if not selection:
            return ""
        return listbox.get(selection[0])

    def _current_experiment_dir(self) -> str:
        experiment_name = self.experiment_var.get().strip()
        if not experiment_name:
            return ""
        return os.path.join(self.root_path, experiment_name)

    def _current_sample_files(self) -> list[str]:
        folder_path = self._current_experiment_dir()
        if not folder_path or not os.path.isdir(folder_path):
            return []
        return list_xlsx_files(folder_path)

    def _get_medicine_count(self) -> int:
        try:
            return max(0, min(MAX_MEDICINES, int(self.medicine_count_var.get())))
        except (TypeError, ValueError):
            return DEFAULT_MEDICINE_COUNT

    def _set_visible_medicine_rows(self, count: int) -> None:
        for index, row_frame in enumerate(self._medicine_row_frames):
            if index < count:
                if not row_frame.winfo_manager():
                    row_frame.pack(fill=tk.X, pady=3)
            elif row_frame.winfo_manager():
                row_frame.pack_forget()

    def _clear_metadata_editor(self) -> None:
        self._apply_metadata_to_editor(default_experiment_metadata())

    def _apply_metadata_to_editor(self, metadata: ExperimentMetadata) -> None:
        self._is_loading_metadata = True
        try:
            count = max(0, min(MAX_MEDICINES, metadata.medicine_count))
            self.medicine_count_var.set(str(count))

            for name_var, dose_var in zip(self.medicine_name_vars, self.medicine_dose_vars):
                name_var.set("")
                dose_var.set("")

            for index, entry in enumerate(metadata.medicines[:count]):
                self.medicine_name_vars[index].set(entry.name)
                self.medicine_dose_vars[index].set(entry.dose)

            self._set_visible_medicine_rows(count)
            self._excluded_samples = list(metadata.excluded_samples)
            self._dixon_suggested_reasons.clear()
            if hasattr(self, "sample_exclusion_list"):
                self._refresh_sample_exclusion_list()
        finally:
            self._is_loading_metadata = False

    def _metadata_from_editor(self) -> ExperimentMetadata:
        count = self._get_medicine_count()
        medicines = [
            MedicineEntry(
                name=self.medicine_name_vars[index].get().strip(),
                dose=self.medicine_dose_vars[index].get().strip(),
            )
            for index in range(count)
        ]
        # Preserve recorded exclusions even if fewer files are currently present.
        excluded_samples = list(self._excluded_samples)
        return ExperimentMetadata(
            medicine_count=count,
            medicines=medicines,
            excluded_samples=excluded_samples,
        )

    def _refresh_sample_exclusion_list(self) -> None:
        if not hasattr(self, "sample_exclusion_list"):
            return

        files = self._current_sample_files()
        self._sample_file_names = files
        excluded_samples = current_excluded_samples(self._excluded_samples, files)
        excluded_reason_by_file = {entry.file_name: entry.reason for entry in excluded_samples}
        max_allowed = max_excluded_samples(len(files))

        self.sample_exclusion_list.delete(0, tk.END)
        for file_name in files:
            excluded_entry = next((entry for entry in excluded_samples if entry.file_name == file_name), None)
            reason = excluded_reason_by_file.get(file_name)
            if excluded_entry is not None:
                method_suffix = " [Dixon Q]" if excluded_entry.method == DIXON_Q_EXCLUSION_METHOD else ""
                suffix = f" - {reason}" if reason else ""
                self.sample_exclusion_list.insert(tk.END, f"[OUT] {file_name}{method_suffix}{suffix}")
            else:
                self.sample_exclusion_list.insert(tk.END, f"[IN]  {file_name}")

        included_count = len(files) - len(excluded_samples)
        displayed_max_allowed = max(max_allowed, len(excluded_samples))
        if not files:
            self.sample_exclusion_status_var.set("No .xlsx samples")
        else:
            self.sample_exclusion_status_var.set(
                f"n={len(files)}; included={included_count}; excluded={len(excluded_samples)}/{displayed_max_allowed}"
            )

    def _selected_sample_file(self) -> str:
        selection = self.sample_exclusion_list.curselection()
        if not selection:
            return ""
        index = selection[0]
        if index < 0 or index >= len(self._sample_file_names):
            return ""
        return self._sample_file_names[index]

    def _select_sample_file(self, file_name: str) -> None:
        if file_name not in self._sample_file_names:
            self._refresh_sample_exclusion_list()
        if file_name not in self._sample_file_names:
            return
        index = self._sample_file_names.index(file_name)
        self.sample_exclusion_list.selection_clear(0, tk.END)
        self.sample_exclusion_list.selection_set(index)
        self.sample_exclusion_list.activate(index)
        self.sample_exclusion_list.see(index)

    def exclude_selected_sample(self) -> None:
        file_name = self._selected_sample_file()
        if not file_name:
            messagebox.showwarning("No Sample Selected", "Please select a sample to exclude.")
            return

        if not self._metadata_is_safe_to_use(self._current_experiment_dir()):
            return

        files = self._current_sample_files()
        if file_name not in files:
            self._refresh_sample_exclusion_list()
            messagebox.showwarning("Sample Missing", "The selected sample is no longer in this folder.")
            return

        excluded_samples = current_excluded_samples(self._excluded_samples, files)
        if any(entry.file_name == file_name for entry in excluded_samples):
            messagebox.showinfo("Already Excluded", f"{file_name} is already excluded.")
            return

        suggested_reason = self._dixon_suggested_reasons.get(file_name, "")
        exclusion_method = DIXON_Q_EXCLUSION_METHOD if suggested_reason else ""
        max_allowed = max_excluded_samples(len(files))
        dixon_exception_allowed = (
            exclusion_method == DIXON_Q_EXCLUSION_METHOD
            and 3 <= len(files) < 5
            and not excluded_samples
        )
        if len(excluded_samples) >= max_allowed and not dixon_exception_allowed:
            messagebox.showwarning(
                "Exclusion Limit",
                f"This folder has n={len(files)}, so at most {max_allowed} sample(s) can be excluded.",
            )
            return

        reason = simpledialog.askstring(
            "Exclude Sample",
            f"Reason for excluding {file_name} (optional):",
            parent=self,
            initialvalue=suggested_reason,
        )
        if reason is None:
            return

        self._excluded_samples = [
            *self._excluded_samples,
            ExcludedSample(file_name=file_name, reason=reason.strip(), method=exclusion_method),
        ]
        self._dixon_suggested_reasons.pop(file_name, None)
        self._refresh_sample_exclusion_list()
        self._autosave_current_metadata()

    def restore_selected_sample(self) -> None:
        file_name = self._selected_sample_file()
        if not file_name:
            messagebox.showwarning("No Sample Selected", "Please select a sample to restore.")
            return

        if not self._metadata_is_safe_to_use(self._current_experiment_dir()):
            return

        files = self._current_sample_files()
        excluded_samples = current_excluded_samples(self._excluded_samples, files)
        if not any(entry.file_name == file_name for entry in excluded_samples):
            messagebox.showinfo("Not Excluded", f"{file_name} is already included.")
            return

        self._excluded_samples = [entry for entry in self._excluded_samples if entry.file_name.casefold() != file_name.casefold()]
        self._dixon_suggested_reasons.pop(file_name, None)
        self._refresh_sample_exclusion_list()
        self._autosave_current_metadata()

    def run_dixon_q_for_current_experiment(self) -> None:
        from .statistics import build_dixon_q_review_for_group

        if self.is_plotting:
            return

        experiment_name = self.experiment_var.get().strip()
        if not experiment_name:
            messagebox.showwarning("Missing Path", "Please select an experiment folder.")
            self.experiment_list.focus_set()
            return

        target_dir = self._current_experiment_dir()
        if not os.path.isdir(target_dir):
            messagebox.showerror("Error", "Experiment folder not found.")
            self.experiment_list.focus_set()
            return
        if not self._metadata_is_safe_to_use(target_dir):
            return

        files = list_xlsx_files(target_dir)
        if not files:
            messagebox.showinfo("Empty", "No .xlsx files found in this folder.")
            self.experiment_list.focus_set()
            return

        try:
            baseline_duration_sec, drug_apply_time_sec, drug_apply_tolerance_sec = self._get_analysis_timing_settings()
            baseline_warning_threshold_pct = self._get_baseline_warning_threshold_pct()
        except ValueError as exc:
            messagebox.showwarning("Invalid Settings", str(exc))
            return

        self._autosave_current_metadata()
        metadata = self._metadata_from_editor()

        self._set_plotting_state(True)

        def worker() -> None:
            try:
                _samples, recommendations, notes = build_dixon_q_review_for_group(
                    experiment_name,
                    target_dir,
                    metadata.excluded_samples,
                    baseline_warning_threshold_pct=baseline_warning_threshold_pct,
                    baseline_duration_sec=baseline_duration_sec,
                    drug_apply_time_sec=drug_apply_time_sec,
                    drug_apply_tolerance_sec=drug_apply_tolerance_sec,
                )
                self._classic_results.put((self._apply_dixon_q_result, (recommendations, notes, len(files))))
            except Exception as exc:
                self._classic_results.put((self._dixon_q_failed, (exc,)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_dixon_q_result(self, recommendations, notes: tuple[str, ...], file_count: int) -> None:
        from .statistics import format_dixon_exclusion_reason

        self._set_plotting_state(False)

        if not recommendations:
            if notes:
                self._show_warning_dialog("Dixon Q Review", ["[Dixon Q Review]", *notes, "", "No recommended exclusion."])
            else:
                messagebox.showinfo("Dixon Q Review", "No Dixon Q recommended exclusion for this experiment folder.")
            return

        recommendation = recommendations[0]
        reason = format_dixon_exclusion_reason(recommendation)
        self._dixon_suggested_reasons[recommendation.file_name] = reason
        self._refresh_sample_exclusion_list()
        self._select_sample_file(recommendation.file_name)

        lines = [
            "[Dixon Q Recommended Exclusion]",
            f"Sample: {recommendation.file_name}",
            f"Side: {recommendation.side}",
            f"Delta %: {recommendation.delta_percent:.4g}",
            f"Nearest Delta %: {recommendation.nearest_delta_percent:.4g}",
            f"Gap / range: {recommendation.gap_delta_percent:.4g} / {recommendation.range_delta_percent:.4g}",
            f"Q: {recommendation.q_statistic:.4g}",
            f"Critical value (alpha={recommendation.alpha:g}): {recommendation.critical_value:.4g}",
            "",
            "The sample has been selected in Sample Exclusion.",
            "Press Exclude Selected to save this recommendation with the Dixon Q reason.",
        ]
        if 3 <= file_count < 5:
            lines.append("Dixon Q exception: this n<5 group may exclude one Dixon-backed sample.")
        if notes:
            lines.extend(["", "Notes", *notes])
        self._show_warning_dialog("Dixon Q Recommendation", lines)

    def _dixon_q_failed(self, exc: Exception) -> None:
        self._set_plotting_state(False)
        messagebox.showerror("Dixon Q Error", f"Failed to run Dixon Q review.\n\n{exc}")

    def _metadata_is_safe_to_use(self, folder_path: str, show_errors: bool = True) -> bool:
        if folder_path == self._metadata_unreadable_folder:
            warning = "Repair the existing metadata JSON and refresh this experiment before continuing."
        else:
            metadata, warning = load_experiment_metadata(folder_path)
            if warning is None:
                unhonored = unhonored_excluded_samples(metadata.excluded_samples, list_xlsx_files(folder_path))
                if unhonored:
                    names = ", ".join(entry.file_name for entry in unhonored)
                    warning = f"Recorded exclusions cannot be honored at the current sample count: {names}"
            if warning:
                self._metadata_unreadable_folder = folder_path
        if warning:
            if show_errors:
                messagebox.showwarning(
                    "Metadata Unreadable",
                    f"Analysis and metadata saving are blocked for this experiment.\n\n{warning}",
                )
            return False
        return True

    def _autosave_current_metadata(self, show_errors: bool = True) -> bool:
        if self._is_loading_metadata:
            return False

        folder_path = self._current_experiment_dir()
        if not folder_path or not os.path.isdir(folder_path):
            return False
        if not self._metadata_is_safe_to_use(folder_path, show_errors=show_errors):
            return False

        try:
            save_experiment_metadata(folder_path, self._metadata_from_editor())
            return True
        except OSError as exc:
            if show_errors:
                messagebox.showwarning(
                    "Metadata Save Failed",
                    f"Could not save metadata for this experiment folder.\n\n{exc}",
                )
            return False

    def _load_selected_experiment_metadata(self) -> None:
        folder_path = self._current_experiment_dir()
        if not folder_path or not os.path.isdir(folder_path):
            self._metadata_unreadable_folder = ""
            self._clear_metadata_editor()
            return

        file_missing = not os.path.exists(metadata_file_path(folder_path))
        metadata, warning_message = load_experiment_metadata(folder_path)
        if warning_message is None:
            unhonored = unhonored_excluded_samples(metadata.excluded_samples, list_xlsx_files(folder_path))
            if unhonored:
                names = ", ".join(entry.file_name for entry in unhonored)
                warning_message = f"Recorded exclusions cannot be honored at the current sample count: {names}"
        self._metadata_unreadable_folder = folder_path if warning_message else ""
        self._apply_metadata_to_editor(metadata)

        if file_missing and warning_message is None:
            self._autosave_current_metadata(show_errors=False)

        if warning_message:
            messagebox.showwarning(
                "Metadata Unreadable",
                f"{warning_message}\n\nThe original file was preserved. Repair it and refresh this experiment before analyzing or saving metadata.",
            )

    def _on_experiment_selected(self, _event=None) -> None:
        new_value = self._selected_list_value(self.experiment_list)
        if new_value == self.experiment_var.get():
            return

        self._autosave_current_metadata()
        self.experiment_var.set(new_value)
        self.custom_plot_title_var.set("")
        self._load_selected_experiment_metadata()

    def _on_medicine_count_changed(self, _event=None) -> None:
        if self._is_loading_metadata:
            return

        self._set_visible_medicine_rows(self._get_medicine_count())
        self._autosave_current_metadata()

    def _on_metadata_field_blur(self, _event=None) -> None:
        if self._is_loading_metadata:
            return
        self._autosave_current_metadata()

    def _plot_has_data(self) -> bool:
        return bool(self.ax.lines or self.ax.collections or self.ax.patches)

    def _current_plot_title(self) -> str:
        custom_title = self.custom_plot_title_var.get().strip()
        if custom_title:
            return custom_title
        return self._last_default_plot_title

    def _sync_plot_title_from_editor(self) -> None:
        if not self._last_default_plot_title or not self._plot_has_data():
            return

        self.ax.set_title(self._current_plot_title(), fontsize=12)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _on_custom_plot_title_changed(self, *_args) -> None:
        self._sync_plot_title_from_editor()

    def _get_analysis_timing_settings(self) -> tuple[float, float, float]:
        try:
            baseline_duration_sec = float(self.baseline_duration_var.get())
            drug_apply_time_sec = float(self.drug_apply_time_var.get())
            drug_apply_tolerance_sec = float(self.drug_apply_tolerance_var.get())
        except (TypeError, ValueError) as exc:
            raise ValueError("Timing controls must contain valid numbers.") from exc

        if baseline_duration_sec <= 0:
            raise ValueError("Baseline Duration must be greater than 0 seconds.")
        if drug_apply_time_sec < 0:
            raise ValueError("Drug Apply Time must be 0 or greater.")
        if drug_apply_tolerance_sec < 0:
            raise ValueError("Apply Window +/- must be 0 or greater.")

        return baseline_duration_sec, drug_apply_time_sec, drug_apply_tolerance_sec

    def _get_baseline_warning_threshold_pct(self) -> float:
        try:
            value = float(self.baseline_warning_threshold_var.get())
        except (TypeError, ValueError) as exc:
            raise ValueError("Baseline Accuracy Threshold must be a valid number.") from exc

        if value < 0:
            raise ValueError("Baseline Accuracy Threshold must be 0 or greater.")
        return value

    def _set_plotting_state(self, plotting: bool) -> None:
        self.is_plotting = plotting
        self.set_controls_enabled(not plotting)
        self.config(cursor="watch" if plotting else "")
        self.update_idletasks()

    def select_root(self) -> None:
        initial_dir = self.root_path if os.path.isdir(self.root_path) else os.path.expanduser("~")
        folder = filedialog.askdirectory(initialdir=initial_dir)
        if folder:
            self.root_path_var.set(folder)
            self.refresh_folder_structure()

    def refresh_folder_structure(self, _event=None, show_errors: bool = True) -> bool:
        self._autosave_current_metadata()
        previous_experiment = self.experiment_var.get()

        requested_path = self.root_path_var.get()
        normalized_path = normalize_directory_path(requested_path)
        resolved_path = resolve_directory_path(requested_path)

        if resolved_path is None:
            self.root_path = normalized_path
            self.root_path_var.set(normalized_path)
            self._set_list_values(self.experiment_list, self.experiment_var, [])
            self.custom_plot_title_var.set("")
            self._clear_metadata_editor()
            if show_errors:
                if normalized_path:
                    messagebox.showwarning("Invalid Root Path", "Please paste or choose a valid root folder.")
                else:
                    messagebox.showwarning("Missing Root Path", "Please paste or choose a root folder.")
                self.root_path_entry.focus_set()
            return False

        self.root_path = resolved_path
        self.root_path_var.set(self.root_path)
        folders = get_subfolders(self.root_path)
        self._set_list_values(self.experiment_list, self.experiment_var, folders)
        if self.experiment_var.get() != previous_experiment:
            self.custom_plot_title_var.set("")
        if folders:
            self._load_selected_experiment_metadata()
        else:
            self.custom_plot_title_var.set("")
            self._clear_metadata_editor()
        return True

    def clear_plot(self) -> None:
        self.ax.clear()
        self._last_default_plot_title = ""
        self.ax.set_title("Ready", fontsize=14)
        self.ax.set_xlabel("Time from Drop (s)")
        self.ax.set_ylabel("Value")
        self._apply_grid_preference()
        self.color_cycler = cycle(COLOR_PALETTE)
        self._overlay_legend_entries.clear()
        self.ax.set_prop_cycle(None)
        self.canvas.draw_idle()

    def _apply_grid_preference(self) -> None:
        self.ax.grid(self.show_grid_var.get(), linestyle=":", alpha=0.6)

    def _on_grid_visibility_changed(self) -> None:
        self._apply_grid_preference()
        self.canvas.draw_idle()

    def _next_group_color(self, is_overlay: bool):
        if not is_overlay:
            return COLOR_PALETTE[0]
        return next(self.color_cycler)

    def _group_color_for_settings(self, settings: PlotSettings):
        if not settings.use_group_color:
            return None

        if settings.is_overlay:
            group_label = build_overlay_legend_group_label(settings)
            if group_label in self._overlay_legend_entries:
                return self._overlay_legend_entries[group_label]

        return self._next_group_color(settings.is_overlay)

    def plot_data(self) -> None:
        if self.is_plotting:
            return

        experiment_name = self.experiment_var.get().strip()
        if not experiment_name:
            messagebox.showwarning("Missing Path", "Please select an experiment folder.")
            self.experiment_list.focus_set()
            return

        target_dir = self._current_experiment_dir()
        if not os.path.exists(target_dir):
            messagebox.showerror("Error", "Experiment folder not found.")
            self.experiment_list.focus_set()
            return
        if not self._metadata_is_safe_to_use(target_dir):
            return

        all_files = list_xlsx_files(target_dir)
        if not all_files:
            messagebox.showinfo("Empty", "No .xlsx files found in this folder.")
            self.experiment_list.focus_set()
            return

        try:
            baseline_duration_sec, drug_apply_time_sec, drug_apply_tolerance_sec = self._get_analysis_timing_settings()
        except ValueError as exc:
            messagebox.showwarning("Invalid Timing", str(exc))
            self.baseline_duration_spinbox.focus_set()
            return

        try:
            baseline_warning_threshold_pct = self._get_baseline_warning_threshold_pct()
        except ValueError as exc:
            messagebox.showwarning("Invalid Threshold", str(exc))
            self.baseline_warning_spinbox.focus_set()
            return

        self._autosave_current_metadata()
        settings = PlotSettings(
            experiment_name=experiment_name,
            target_dir=target_dir,
            all_files=all_files,
            metadata=self._metadata_from_editor(),
            is_overlay=self.overlay_mode_var.get(),
            display_mode=self.display_mode_var.get(),
            use_group_color=self.group_color_var.get(),
            show_drop_lines=self.show_drop_lines_var.get(),
            leg_style=self.legend_style_var.get(),
            show_base=self.leg_show_base.get(),
            show_delta=self.leg_show_delta.get(),
            baseline_duration_sec=baseline_duration_sec,
            drug_apply_time_sec=drug_apply_time_sec,
            drug_apply_tolerance_sec=drug_apply_tolerance_sec,
            baseline_warning_threshold_pct=baseline_warning_threshold_pct,
            custom_title=self.custom_plot_title_var.get().strip(),
        )
        group_color = self._group_color_for_settings(settings)

        self._set_plotting_state(True)
        threading.Thread(
            target=self._plot_data_worker,
            args=(settings, group_color),
            daemon=True,
        ).start()

    def _plot_data_worker(self, settings: PlotSettings, group_color) -> None:
        try:
            payload = build_plot_payload(settings, group_color)
            self._classic_results.put((self._plot_data_apply, (payload,)))
        except Exception as exc:
            self._classic_results.put((self._plot_data_failed, (exc,)))

    def _poll_classic_results(self) -> None:
        """Dispatch worker results on Tk main loop; workers never call Tk."""
        try:
            while True:
                try:
                    callback, args = self._classic_results.get_nowait()
                except Empty:
                    break
                callback(*args)
        finally:
            if self.winfo_exists():
                self.after(80, self._poll_classic_results)

    def _plot_data_apply(self, payload: PlotPayload) -> None:
        settings = payload.settings

        if not settings.is_overlay:
            self.clear_plot()

        self._last_default_plot_title = build_plot_title(settings.experiment_name, settings.metadata)
        self.ax.set_title(self._current_plot_title(), fontsize=12)
        self.ax.set_xlabel("Time from Drop (s)")
        self.ax.set_ylabel(payload.y_unit)
        self._apply_grid_preference()

        count = 0
        first_line_color = None
        for item in payload.plot_items:
            count += 1
            line, = self.ax.plot(
                item.x_plot,
                item.y_plot,
                label=item.label_txt,
                color=item.line_color,
                linestyle=item.line_style,
                alpha=0.8,
                lw=1.5,
            )
            if first_line_color is None:
                first_line_color = line.get_color()

            if settings.display_mode != "Base" and settings.show_drop_lines:
                self.ax.axvline(x=item.drop_time, color=line.get_color(), ls="--", alpha=0.3)

        if count > 0:
            if settings.is_overlay and settings.use_group_color:
                self._update_overlay_group_legend(settings, first_line_color)
            else:
                self.ax.legend(fontsize="x-small", loc="upper left", bbox_to_anchor=(1.0, 1.0))
            self.fig.tight_layout()
            self.canvas.draw_idle()

        self._set_plotting_state(False)
        self._show_plot_warnings(payload)

    def _update_overlay_group_legend(self, settings: PlotSettings, color) -> None:
        group_label = build_overlay_legend_group_label(settings)
        if not group_label:
            return

        self._overlay_legend_entries[group_label] = color
        handles = [
            Patch(facecolor=entry_color, edgecolor=entry_color, label=entry_label)
            for entry_label, entry_color in self._overlay_legend_entries.items()
        ]
        self.ax.legend(
            handles=handles,
            fontsize=OVERLAY_LEGEND_FONT_SIZE,
            loc="upper left",
            bbox_to_anchor=(1.0, 1.0),
            handlelength=1.0,
            handleheight=1.0,
        )

    def _plot_data_failed(self, exc: Exception) -> None:
        self._set_plotting_state(False)
        self.experiment_list.focus_set()
        messagebox.showerror("Plot Error", f"Failed to load or plot data.\n\n{exc}")

    def _show_warning_dialog(self, title: str, lines: list[str]) -> None:
        if not lines:
            return

        if self._warning_dialog is not None and self._warning_dialog.winfo_exists():
            self._warning_dialog.destroy()

        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.resizable(True, True)

        def close_dialog() -> None:
            if self._warning_dialog is dialog:
                self._warning_dialog = None
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        text_widget = tk.Text(
            text_frame,
            wrap="word",
            height=min(18, max(8, len(lines) + 2)),
            yscrollcommand=scrollbar.set,
        )
        text_widget.insert("1.0", "\n".join(lines))
        text_widget.configure(state="disabled")
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=text_widget.yview)

        ttk.Button(frame, text="Close", command=close_dialog).pack(anchor=tk.E, pady=(8, 0))
        dialog.lift()
        dialog.focus_set()
        self._warning_dialog = dialog

    def _build_baseline_warning_lines(self, payload: PlotPayload) -> list[str]:
        flagged_items = [item for item in payload.plot_items if item.baseline_warning_status != "ok"]
        if not flagged_items:
            return []

        status_labels = {"warning": "注意", "inaccurate": "不準確"}
        warning_lines = [
            "[Baseline Accuracy]",
            "The selected baseline window may already be contaminated in these files:",
            f"Threshold: {payload.settings.baseline_warning_threshold_pct:.2f}%",
            "",
        ]

        for item in flagged_items:
            details = "；".join(item.baseline_warning_details) if item.baseline_warning_details else "baseline 偏移"
            warning_lines.append(
                f"N {item.sample_name}: {status_labels[item.baseline_warning_status]} - {details}"
            )

        return warning_lines

    def _build_timing_warning_lines(self, payload: PlotPayload) -> list[str]:
        flagged_items = [item for item in payload.plot_items if item.timing_warning_details]
        if not flagged_items:
            return []

        warning_lines = [
            "[Timing Adjustments]",
            (
                f"Requested timing: baseline {payload.settings.baseline_duration_sec:.1f}s, "
                f"apply {payload.settings.drug_apply_time_sec:.1f}s +/- "
                f"{payload.settings.drug_apply_tolerance_sec:.1f}s"
            ),
            "",
        ]

        for item in flagged_items:
            warning_lines.append(f"N {item.sample_name}: {'; '.join(item.timing_warning_details)}")

        return warning_lines

    def _show_plot_warnings(self, payload: PlotPayload) -> None:
        warning_lines = self._build_baseline_warning_lines(payload)
        timing_lines = self._build_timing_warning_lines(payload)

        if warning_lines and timing_lines:
            warning_lines.append("")
        warning_lines.extend(timing_lines)
        if payload.warnings:
            if warning_lines:
                warning_lines.append("")
            warning_lines.extend(["[Skipped Samples]", *payload.warnings])
        self._show_warning_dialog("Plot Warnings", warning_lines)

    def open_statistics_dialog(self) -> None:
        from .statistics import build_statistical_analysis, format_statistics_result, write_statistics_csv

        resolved_root = resolve_directory_path(self.root_path_var.get())
        if resolved_root is None:
            messagebox.showwarning("Invalid Root Path", "Please paste or choose a valid root folder.")
            self.root_path_entry.focus_set()
            return

        self.root_path = resolved_root
        self.root_path_var.set(resolved_root)
        folders = get_subfolders(resolved_root)
        if not folders:
            messagebox.showinfo("No Groups", "No experiment folders were found under this root path.")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Statistical Analysis")
        dialog.transient(self)
        dialog.geometry("980x680")
        dialog.resizable(True, True)

        state: dict[str, StatisticalAnalysisResult | None] = {"result": None}

        outer = ttk.Frame(dialog, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        controls = ttk.Frame(outer)
        controls.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(controls, text=f"Groups: {len(folders)} concentration folder(s)").pack(side=tk.LEFT)

        run_btn = ttk.Button(controls, text="Run One-way ANOVA")
        run_btn.pack(side=tk.LEFT, padx=(0, 6))
        export_btn = ttk.Button(controls, text="Export CSV", state="disabled")
        export_btn.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(controls, text="Close", command=dialog.destroy).pack(side=tk.RIGHT)

        text_frame = ttk.Frame(outer)
        text_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget = tk.Text(
            text_frame,
            wrap="word",
            yscrollcommand=scrollbar.set,
            font=("Menlo", 11),
        )
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=text_widget.yview)

        def set_text(content: str) -> None:
            text_widget.configure(state="normal")
            text_widget.delete("1.0", tk.END)
            text_widget.insert("1.0", content)
            text_widget.configure(state="disabled")

        def set_running(is_running: bool) -> None:
            run_btn.configure(state="disabled" if is_running else "normal")
            if is_running:
                export_btn.configure(state="disabled")
            elif state["result"] is not None:
                export_btn.configure(state="normal")

        def apply_statistics_result(result: StatisticalAnalysisResult) -> None:
            if not dialog.winfo_exists():
                return
            state["result"] = result
            set_text(format_statistics_result(result))
            set_running(False)

        def fail_statistics(exc: Exception) -> None:
            if not dialog.winfo_exists():
                return
            set_running(False)
            messagebox.showerror("Statistics Error", f"Failed to calculate statistics.\n\n{exc}", parent=dialog)

        def run_statistics() -> None:
            try:
                baseline_duration_sec, drug_apply_time_sec, drug_apply_tolerance_sec = self._get_analysis_timing_settings()
                baseline_warning_threshold_pct = self._get_baseline_warning_threshold_pct()
            except ValueError as exc:
                messagebox.showwarning("Invalid Settings", str(exc), parent=dialog)
                return

            state["result"] = None
            set_running(True)
            set_text("Running statistical analysis...\n\nThis scans every direct child folder under the current root.")

            def worker() -> None:
                try:
                    result = build_statistical_analysis(
                        resolved_root,
                        baseline_warning_threshold_pct=baseline_warning_threshold_pct,
                        baseline_duration_sec=baseline_duration_sec,
                        drug_apply_time_sec=drug_apply_time_sec,
                        drug_apply_tolerance_sec=drug_apply_tolerance_sec,
                    )
                    self._classic_results.put((apply_statistics_result, (result,)))
                except Exception as exc:
                    self._classic_results.put((fail_statistics, (exc,)))

            threading.Thread(target=worker, daemon=True).start()

        def export_statistics() -> None:
            result = state["result"]
            if result is None:
                messagebox.showwarning("No Results", "Run statistics before exporting.", parent=dialog)
                return

            path = filedialog.asksaveasfilename(
                parent=dialog,
                title="Export Statistics",
                defaultextension=".csv",
                filetypes=(("CSV file", "*.csv"), ("All files", "*.*")),
            )
            if not path:
                return
            if not path.lower().endswith(".csv"):
                path = f"{path}.csv"
            try:
                write_statistics_csv(result, path)
            except OSError as exc:
                messagebox.showerror("Export Failed", f"Could not save statistics CSV.\n\n{exc}", parent=dialog)
                return
            messagebox.showinfo("Exported", "Statistics CSV saved successfully.", parent=dialog)

        run_btn.configure(command=run_statistics)
        export_btn.configure(command=export_statistics)
        set_text(
            "Run one-way ANOVA across all concentration folders under the current root.\n\n"
            "Endpoint: Delta % = raw delta pF / each electrode baseline pF * 100.\n"
            "Grouping: each direct child folder under the current root path is one concentration group.\n"
            "This report includes descriptive statistics, robust outlier review, and one-way ANOVA.\n"
            "Outlier candidates are shown for review only and are not automatically excluded."
        )
        dialog.lift()
        run_btn.focus_set()

    def export_plot(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export Plot",
            defaultextension=DEFAULT_EXPORT_EXTENSION,
            filetypes=EXPORT_FILETYPES,
        )
        if path:
            _, extension = os.path.splitext(path)
            if extension.lower() not in SUPPORTED_EXPORT_EXTENSIONS:
                path = f"{path}{DEFAULT_EXPORT_EXTENSION}"
            self.fig.savefig(path, dpi=300, bbox_inches="tight")
            messagebox.showinfo("Exported", "Saved successfully.")


class ErrorBandViewerApp(tk.Tk):
    """Small, read-only workflow for plotting multiple replicate groups."""

    UNIT_OPTIONS = {
        "ΔC：扣除 baseline（pF）": "Delta",
        "相對變化（%）": "DeltaPct",
        "原始電容（pF）": "Raw",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("Error Band Studio · 誤差區域圖")
        self.geometry("1180x780")
        self.minsize(960, 660)
        self.path_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.unit_var = tk.StringVar(value=next(iter(self.UNIT_OPTIONS)))
        self.band_var = tk.StringVar(value="SD")
        self.grid_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="選擇資料根目錄，每個直接子資料夾就是一組實驗。")
        self.band_hint_var = tk.StringVar()
        self.timing_vars = {
            "sample_interval_sec": tk.StringVar(value="0.1"),
            "baseline_duration_sec": tk.StringVar(value="20"),
            "drug_apply_time_sec": tk.StringVar(value="25"),
            "drug_apply_tolerance_sec": tk.StringVar(value="5"),
        }
        self._data_root = ""
        self._groups: dict[str, bool] = {}
        self._labels: dict[str, str] = {}
        self._rows: dict[str, str] = {}
        self._payload: ErrorBandPayload | None = None
        self._settings_dirty = False
        self._busy = False
        self._results = SimpleQueue()
        self._poll_id: str | None = None
        self._controls: list[tk.Widget] = []
        self._combos: list[ttk.Combobox] = []
        self._advanced_open = False
        background = ttk.Style(self).lookup("TFrame", "background") or self.cget("background")
        self._background_color = background
        self._hint_color = "#9CA3AF" if sum(self.winfo_rgb(background)) < 3 * 32768 else "#667085"
        from matplotlib import rcParams
        from matplotlib.font_manager import fontManager

        available_fonts = {font.name for font in fontManager.ttflist}
        for font in ("Microsoft JhengHei", "PingFang TC", "PingFang HK", "Heiti TC", "Noto Sans CJK TC", "Arial Unicode MS"):
            if font in available_fonts:
                rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
                break
        self._create_band_widgets()
        self.title_var.trace_add("write", lambda *_: self._redraw())
        self.unit_var.trace_add("write", self._on_analysis_changed)
        self.path_var.trace_add("write", self._on_analysis_changed)
        for variable in self.timing_vars.values():
            variable.trace_add("write", self._on_analysis_changed)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._redraw()

    def _create_band_widgets(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        ttk.Label(outer, text="誤差區域圖", font=("TkDefaultFont", 20, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text="選資料夾 → 勾選組別 → 製圖與匯出", foreground=self._hint_color).grid(row=1, column=0, sticky="w", pady=(4, 14))
        body = ttk.Frame(outer)
        body.grid(row=2, column=0, sticky="nsew")
        left_column = ttk.Frame(body)
        left_column.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 18))
        scroll_area = ttk.Frame(left_column)
        scroll_area.pack(fill=tk.BOTH, expand=True)
        self.controls_canvas = tk.Canvas(scroll_area, width=310, highlightthickness=0, background=self._background_color)
        self.controls_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        controls_scrollbar = ttk.Scrollbar(scroll_area, orient=tk.VERTICAL, command=self.controls_canvas.yview)
        controls_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.controls_canvas.configure(yscrollcommand=controls_scrollbar.set)
        sidebar = ttk.Frame(self.controls_canvas)
        controls_window = self.controls_canvas.create_window((0, 0), window=sidebar, anchor="nw")
        sidebar.bind("<Configure>", lambda _: self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox("all")))
        self.controls_canvas.bind("<Configure>", lambda event: self.controls_canvas.itemconfigure(controls_window, width=event.width))
        self.bind("<MouseWheel>", self._scroll_controls)

        data = ttk.LabelFrame(sidebar, text="1  選擇資料", padding=10)
        data.pack(fill=tk.X)
        self.path_entry = ttk.Entry(data, textvariable=self.path_var, width=30)
        self.path_entry.pack(fill=tk.X)
        self.path_entry.bind("<Return>", lambda _: self.load_groups())
        self._controls.append(self.path_entry)
        buttons = ttk.Frame(data)
        buttons.pack(fill=tk.X, pady=(6, 0))
        self._button(buttons, "選擇資料夾…", self.choose_root).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(buttons, "重新整理", self.load_groups).pack(side=tk.LEFT, padx=(6, 0))

        groups = ttk.LabelFrame(sidebar, text="2  勾選比較組別", padding=10)
        groups.pack(fill=tk.X, pady=10)
        self.group_tree = ttk.Treeview(groups, columns=("use", "group", "files"), show="headings", height=6, selectmode="browse")
        for column, title, width in (("use", "選", 32), ("group", "組別／圖例名稱", 184), ("files", "檔案", 40)):
            self.group_tree.heading(column, text=title)
            self.group_tree.column(column, width=width, minwidth=width if column != "group" else 100, stretch=column == "group", anchor=tk.W if column == "group" else tk.CENTER)
        self.group_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(groups, orient=tk.VERTICAL, command=self.group_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.group_tree.configure(yscrollcommand=scrollbar.set)
        self.group_tree.bind("<ButtonRelease-1>", self._toggle_group)
        self.group_tree.bind("<Double-1>", self._rename_group)
        self.group_tree.bind("<space>", self._toggle_group)
        selection = ttk.Frame(sidebar)
        selection.pack(fill=tk.X)
        self._button(selection, "全選", lambda: self._select_groups(True)).pack(side=tk.LEFT)
        self._button(selection, "全不選", lambda: self._select_groups(False)).pack(side=tk.LEFT, padx=5)
        ttk.Label(sidebar, text="點選「選」欄勾選；雙擊名稱可修改圖例。", foreground=self._hint_color, wraplength=285).pack(anchor=tk.W, pady=(4, 10))

        options = ttk.LabelFrame(sidebar, text="3  圖表設定", padding=10)
        options.pack(fill=tk.X)
        ttk.Label(options, text="顯示單位").pack(anchor=tk.W)
        unit = ttk.Combobox(options, textvariable=self.unit_var, values=tuple(self.UNIT_OPTIONS), state="readonly", width=29)
        unit.pack(fill=tk.X, pady=(3, 8))
        self._combos.append(unit)
        band_row = ttk.Frame(options)
        band_row.pack(fill=tk.X)
        ttk.Label(band_row, text="陰影範圍").pack(side=tk.LEFT)
        band = ttk.Combobox(band_row, textvariable=self.band_var, values=("SD", "SEM"), state="readonly", width=8)
        band.pack(side=tk.RIGHT)
        band.bind("<<ComboboxSelected>>", lambda _: self._redraw())
        self._combos.append(band)
        ttk.Label(options, textvariable=self.band_hint_var, foreground=self._hint_color).pack(anchor=tk.W, pady=(4, 8))
        ttk.Label(options, text="圖表標題（可留空）").pack(anchor=tk.W)
        title = ttk.Entry(options, textvariable=self.title_var)
        title.pack(fill=tk.X, pady=(3, 8))
        self._controls.append(title)
        grid = ttk.Checkbutton(options, text="顯示格線", variable=self.grid_var, command=self._redraw)
        grid.pack(anchor=tk.W)
        self._controls.append(grid)

        self.advanced_button = self._button(sidebar, "▸ 進階：取樣與時間對齊", self._toggle_advanced)
        self.advanced_button.pack(fill=tk.X, pady=(10, 5))
        self.advanced_frame = ttk.Frame(sidebar, padding=(10, 0))
        for row, (name, label) in enumerate((
            ("sample_interval_sec", "取樣間隔（s）"),
            ("baseline_duration_sec", "Baseline 長度（s）"),
            ("drug_apply_time_sec", "施加時間（s）"),
            ("drug_apply_tolerance_sec", "搜尋 ±（s）"),
        )):
            ttk.Label(self.advanced_frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(self.advanced_frame, textvariable=self.timing_vars[name], width=10)
            entry.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=2)
            self._controls.append(entry)

        actions = ttk.Frame(left_column)
        actions.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self.plot_button = self._button(actions, "製作誤差區域圖", self.plot_data)
        self.plot_button.pack(fill=tk.X, ipady=5)
        exports = ttk.Frame(actions)
        exports.pack(fill=tk.X, pady=6)
        self.export_button = self._button(exports, "匯出圖片…", self.export_plot)
        self.export_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.csv_button = self._button(exports, "匯出 CSV…", self.export_csv)
        self.csv_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.export_button.configure(state="disabled")
        self.csv_button.configure(state="disabled")

        plot = ttk.Frame(body)
        plot.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.fig = Figure(figsize=(8, 5.5), dpi=100, layout="constrained")
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, plot, pack_toolbar=False)
        toolbar.pack(fill=tk.X)
        footer = ttk.Frame(outer)
        footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(footer, textvariable=self.status_var, wraplength=850).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.report_button = ttk.Button(footer, text="分析明細／注意事項", command=self.show_report, state="disabled")
        self.report_button.pack(side=tk.RIGHT, padx=(10, 0))
        self._controls.append(self.report_button)

    def _button(self, parent, text, command) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        self._controls.append(button)
        return button

    def _scroll_controls(self, event) -> None:
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self.controls_canvas:
                # macOS sends small deltas; Windows sends multiples of 120.
                delta = -int(event.delta / 120) if abs(event.delta) >= 120 else -int(event.delta)
                self.controls_canvas.yview_scroll(delta, "units")
                return
            widget = getattr(widget, "master", None)

    def choose_root(self) -> None:
        path = filedialog.askdirectory(parent=self, title="選擇包含各組實驗的根目錄", initialdir=self._data_root or os.path.expanduser("~"))
        if path:
            self.path_var.set(path)
            self.load_groups()

    def load_groups(self) -> bool:
        if self._busy:
            return False
        root = resolve_directory_path(self.path_var.get())
        if root is None:
            messagebox.showwarning("資料夾不存在", "請選擇或貼上有效的資料根目錄。", parent=self)
            return False
        self._data_root = root
        self.path_var.set(root)
        self._groups.clear()
        self._labels.clear()
        self._rows.clear()
        self.group_tree.delete(*self.group_tree.get_children())
        for name in get_subfolders(root):
            files = list_xlsx_files(os.path.join(root, name))
            if files:
                self._groups[name] = True
                self._labels[name] = name
                row = self.group_tree.insert("", tk.END, values=("✓", name, len(files)))
                self._rows[row] = name
        self._on_analysis_changed()
        self.status_var.set(f"找到 {len(self._groups)} 組實驗。勾選組別後按「製作誤差區域圖」。" if self._groups else "找不到實驗組；根目錄的直接子資料夾需包含 .xlsx。")
        return True

    def _toggle_group(self, event) -> None:
        if self._busy:
            return
        if event.keysym == "space":
            row = self.group_tree.focus()
        else:
            if self.group_tree.identify_column(event.x) != "#1":
                return
            row = self.group_tree.identify_row(event.y)
        if row not in self._rows:
            return
        name = self._rows[row]
        self._groups[name] = not self._groups[name]
        self.group_tree.set(row, "use", "✓" if self._groups[name] else "")
        self._on_analysis_changed()

    def _rename_group(self, event) -> None:
        if self._busy or self.group_tree.identify_column(event.x) != "#2":
            return
        row = self.group_tree.identify_row(event.y)
        if row not in self._rows:
            return
        name = self._rows[row]
        label = simpledialog.askstring("圖例名稱", f"{name} 的圖例名稱：", initialvalue=self._labels[name], parent=self)
        if label is not None:
            self._labels[name] = label.strip() or name
            self.group_tree.set(row, "group", self._labels[name])
            if self._payload:
                self._payload = replace(self._payload, groups=tuple(replace(group, legend_label=self._labels.get(group.group_name, group.group_name)) for group in self._payload.groups))
                self._redraw()

    def _select_groups(self, selected: bool) -> None:
        for row, name in self._rows.items():
            self._groups[name] = selected
            self.group_tree.set(row, "use", "✓" if selected else "")
        self._on_analysis_changed()

    def _toggle_advanced(self) -> None:
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self.advanced_frame.pack(fill=tk.X, after=self.advanced_button)
        else:
            self.advanced_frame.pack_forget()
        self.advanced_button.configure(text=("▾" if self._advanced_open else "▸") + " 進階：取樣與時間對齊")

    def _on_analysis_changed(self, *_args) -> None:
        if self._payload:
            self._settings_dirty = True
            self.export_button.configure(state="disabled")
            self.csv_button.configure(state="disabled")
            self.status_var.set("資料或分析設定已變更，請重新製圖後匯出。")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for control in self._controls:
            control.configure(state="disabled" if busy else "normal")
        for combo in self._combos:
            combo.configure(state="disabled" if busy else "readonly")
        if not busy:
            exportable = bool(self._payload and self._payload.groups and not self._settings_dirty)
            self.export_button.configure(state="normal" if exportable else "disabled")
            self.csv_button.configure(state="normal" if exportable else "disabled")
            self.report_button.configure(state="normal" if self._payload else "disabled")
        self.plot_button.configure(text="分析中…" if busy else "製作誤差區域圖")

    def plot_data(self) -> None:
        if self._busy:
            return
        if resolve_directory_path(self.path_var.get()) != self._data_root or not self._groups:
            if not self.load_groups():
                return
        selected = [name for name, included in self._groups.items() if included]
        if not selected:
            messagebox.showwarning("尚未選擇組別", "請至少勾選一組含有 Excel 資料的實驗。", parent=self)
            return
        try:
            settings = ErrorBandSettings(display_mode=self.UNIT_OPTIONS[self.unit_var.get()], **{name: float(variable.get()) for name, variable in self.timing_vars.items()})
            validate_error_band_settings(settings)
        except (ValueError, KeyError) as exc:
            messagebox.showwarning("設定無效", f"請檢查進階參數。\n\n{exc}", parent=self)
            return
        root, labels = self._data_root, dict(self._labels)
        self._set_busy(True)
        self.status_var.set("正在讀取 Excel、對齊反應起點並計算平均與誤差範圍…")

        def worker() -> None:
            try:
                payload = build_error_band_payload(root, selected, settings)
                payload = replace(payload, groups=tuple(replace(group, legend_label=labels[group.group_name]) for group in payload.groups))
                self._results.put((True, payload))
            except Exception as exc:
                self._results.put((False, exc))

        threading.Thread(target=worker, daemon=True).start()
        # Poll from Tk's main thread; the worker never calls Tk or Matplotlib.
        self._poll_id = self.after(80, self._poll_result)

    def _poll_result(self) -> None:
        self._poll_id = None
        try:
            success, result = self._results.get_nowait()
        except Empty:
            self._poll_id = self.after(80, self._poll_result)
            return
        if success:
            self._payload = result
            self._settings_dirty = False
            self._redraw()
            n = sum(group.n for group in result.groups)
            self.status_var.set(f"已製作 {len(result.groups)} 組、共 {n} 筆樣本。注意事項 {len(result.warnings)} 則；可開啟分析明細查看。" if result.groups else "沒有可製圖的有效資料；請查看分析明細。")
        else:
            self.status_var.set("分析失敗；請檢查檔案或設定後重試。")
            messagebox.showerror("分析失敗", str(result), parent=self)
        self._set_busy(False)

    def _redraw(self) -> None:
        self.band_hint_var.set("SD：顯示重複樣本的分散程度" if self.band_var.get() == "SD" else "SEM：顯示平均值的標準誤")
        if self._payload and self._payload.groups:
            render_error_band_plot(self.ax, self._payload, self.band_var.get(), self.title_var.get(), self.grid_var.get())
        else:
            self.ax.clear()
            self.ax.set_axis_off()
            self.ax.text(.5, .55, "Mean + error band", fontsize=22, color="#344054", ha="center", transform=self.ax.transAxes)
            self.ax.text(.5, .45, "Choose a data folder to begin", fontsize=12, color="#667085", ha="center", transform=self.ax.transAxes)
        # ax.clear does not restore axis visibility after an empty preview.
        if self._payload and self._payload.groups:
            self.ax.set_axis_on()
        self.canvas.draw_idle()

    def show_report(self) -> None:
        if not self._payload:
            return
        payload = self._payload
        lines = [f"資料根目錄：{payload.root_path}", "每個檔案視為一筆獨立重複樣本。", "各組僅使用全部納入樣本共有的時間範圍；不補值、不延伸尾端。", "SD 使用樣本標準差（ddof=1）；SEM = SD / √n。", f"取樣間隔：{payload.settings.sample_interval_sec:g} s；baseline：{payload.settings.baseline_duration_sec:g} s", f"施加時間：{payload.settings.drug_apply_time_sec:g} ± {payload.settings.drug_apply_tolerance_sec:g} s", ""]
        for group in payload.groups:
            lines.extend((f"{group.group_name}：n={group.n}；時間 {group.time_sec[0]:g} ～ {group.time_sec[-1]:g} s", "納入：" + ", ".join(group.sample_names), ""))
        lines.append("注意事項")
        lines.extend(payload.warnings or ("無。",))
        dialog = tk.Toplevel(self)
        dialog.title("分析明細／注意事項")
        dialog.geometry("760x500")
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text = tk.Text(frame, wrap="word", yscrollcommand=scroll.set)
        text.pack(fill=tk.BOTH, expand=True)
        scroll.configure(command=text.yview)
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")

    def export_plot(self) -> None:
        if not self._payload or not self._payload.groups or self._settings_dirty:
            return
        path = filedialog.asksaveasfilename(parent=self, title="匯出圖片", initialfile="error-band.png", defaultextension=".png", filetypes=EXPORT_FILETYPES[:3])
        if path:
            try:
                self.fig.savefig(path, dpi=300, bbox_inches="tight")
            except Exception as exc:
                messagebox.showerror("匯出失敗", str(exc), parent=self)
                return
            self.status_var.set(f"已匯出圖片：{os.path.basename(path)}")

    def export_csv(self) -> None:
        if not self._payload or not self._payload.groups or self._settings_dirty:
            return
        path = filedialog.asksaveasfilename(parent=self, title="匯出平均值與誤差範圍", initialfile="error-band.csv", defaultextension=".csv", filetypes=(("CSV", "*.csv"),))
        if path:
            try:
                write_error_band_csv(self._payload, path, self.band_var.get())
            except OSError as exc:
                messagebox.showerror("匯出失敗", str(exc), parent=self)
                return
            self.status_var.set(f"已匯出統計曲線：{os.path.basename(path)}")

    def _close(self) -> None:
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
        self.destroy()
