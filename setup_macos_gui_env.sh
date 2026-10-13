#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv-gui"

choose_python() {
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(choose_python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "No suitable Python interpreter found."
  echo "Install Python 3.12+ first, then rerun this script."
  exit 1
fi

PYTHON_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Using ${PYTHON_BIN} (${PYTHON_VERSION})"

MACOS_VERSION="$(sw_vers -productVersion 2>/dev/null || true)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"

if [[ "${MACOS_MAJOR}" =~ ^[0-9]+$ ]] && (( MACOS_MAJOR >= 15 )) && [[ "${PYTHON_VERSION}" < "3.12" ]]; then
  echo "Python ${PYTHON_VERSION} is not supported for this GUI on macOS ${MACOS_VERSION}."
  echo "Install Python 3.12 or 3.13, then rerun this script."
  exit 1
elif [[ "${PYTHON_VERSION}" < "3.12" ]]; then
  echo "Warning: Python ${PYTHON_VERSION} is older than the recommended 3.12+."
  echo "You can still create the venv, but Tk/macOS issues may persist."
fi

if ! "${PYTHON_BIN}" -c 'import tkinter' >/dev/null 2>&1; then
  echo "${PYTHON_BIN} does not include Tkinter support."
  echo "If this is Homebrew Python ${PYTHON_VERSION}, install the matching Tk package:"
  echo "  brew install python-tk@${PYTHON_VERSION}"
  echo "Then rerun this script."
  exit 1
fi

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "${ROOT_DIR}/requirements-gui.txt"

cat <<EOF

Environment ready at ${VENV_DIR}

Activate:
  source "${VENV_DIR}/bin/activate"

Smoke test:
  python "${ROOT_DIR}/tk_macos_smoke_test.py"

Main app:
  python "${ROOT_DIR}/main.py"
EOF
