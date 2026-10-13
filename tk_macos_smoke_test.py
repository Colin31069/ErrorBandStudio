import tkinter as tk
from tkinter import ttk


def main():
    root = tk.Tk()
    root.title("Tk macOS Smoke Test")
    root.geometry("420x260")

    selected = tk.StringVar(value="Option 1")
    radio = tk.StringVar(value="A")
    checked = tk.BooleanVar(value=False)
    compat_mode = tk.BooleanVar(value=True)

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frame, text="Combobox").pack(anchor=tk.W)
    combo = ttk.Combobox(frame, values=["Option 1", "Option 2", "Option 3"])
    combo.pack(fill=tk.X, pady=(0, 12))
    combo.set(selected.get())

    ttk.Checkbutton(frame, text="Compatibility Mode", variable=compat_mode).pack(anchor=tk.W)
    ttk.Checkbutton(frame, text="Checkbutton Test", variable=checked).pack(anchor=tk.W, pady=(8, 8))
    ttk.Radiobutton(frame, text="Radio A", variable=radio, value="A").pack(anchor=tk.W)
    ttk.Radiobutton(frame, text="Radio B", variable=radio, value="B").pack(anchor=tk.W)

    status = ttk.Label(frame, text="")
    status.pack(anchor=tk.W, pady=(16, 0))

    def sync_combo_mode(*_args):
        value = combo.get()
        combo.configure(state="normal" if compat_mode.get() else "readonly")
        if value:
            combo.set(value)
        status.config(
            text=f"combo={combo.get() or '-'} | checked={checked.get()} | radio={radio.get()} | mode={combo.cget('state')}"
        )

    def on_combo_selected(_event=None):
        selected.set(combo.get())
        sync_combo_mode()

    compat_mode.trace_add("write", sync_combo_mode)
    checked.trace_add("write", sync_combo_mode)
    radio.trace_add("write", sync_combo_mode)
    combo.bind("<<ComboboxSelected>>", on_combo_selected)
    combo.bind("<Button-1>", lambda _event: combo.focus_set(), add="+")

    root.after(100, lambda: (root.focus_force(), combo.focus_set(), sync_combo_mode()))
    root.mainloop()


if __name__ == "__main__":
    main()
