#!/bin/zsh
set -e
cd "${0:A:h}"

supports_gui() {
    "$1" -c 'import sys, tkinter; raise SystemExit(sys.version_info < (3, 12))' >/dev/null 2>&1
}

stop_with_message() {
    echo "$1"
    echo "You can also use the macOS .app ZIP without installing Python."
    read "?Press Enter to close." || true
    exit 1
}

if [[ -x .venv-gui/bin/python ]] && supports_gui .venv-gui/bin/python && .venv-gui/bin/python -c 'import numpy, matplotlib, openpyxl' >/dev/null 2>&1; then
    exec .venv-gui/bin/python main.py
fi
if [[ ! -x .venv-band/bin/python ]]; then
    band_python=""
    for candidate in python3.13 python3.12 python3.14 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && supports_gui "$candidate"; then
            band_python="$candidate"
            break
        fi
    done
    if [[ -z "$band_python" ]]; then
        stop_with_message "Install Python 3.12 or newer with Tk support. No compatible Python was found."
    fi
    if ! "$band_python" -m venv .venv-band; then
        stop_with_message "Could not create the Python environment. Review the error above."
    fi
fi
if ! supports_gui .venv-band/bin/python; then
    stop_with_message "The existing .venv-band needs Python 3.12+ with Tk. Repair its Python/Tk installation or recreate this environment."
fi
if ! .venv-band/bin/python -c 'import numpy, matplotlib, openpyxl' >/dev/null 2>&1; then
    if ! .venv-band/bin/python -m pip install -r requirements-error-band.txt; then
        stop_with_message "Dependency setup failed. Review the error above."
    fi
    if ! .venv-band/bin/python -c 'import numpy, matplotlib, openpyxl'; then
        stop_with_message "Python dependencies are unavailable after setup. Review the error above."
    fi
fi
exec .venv-band/bin/python main.py
