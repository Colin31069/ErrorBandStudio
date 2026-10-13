from __future__ import annotations

import os
import platform
import re
import sys
import traceback
from importlib import metadata
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
GUI_VENV_DIR = PROJECT_DIR / ".venv-gui"
GUI_VENV_PYTHON = GUI_VENV_DIR / "bin" / "python"
GUI_VENV_CONFIG = GUI_VENV_DIR / "pyvenv.cfg"
RELAUNCH_ENV_VAR = "SKIN_ANALYSIS_GUI_VENV_RELAUNCHED"
MIN_DEPENDENCY_VERSIONS = {
    "numpy": (2, 0),
    "matplotlib": (3, 9),
    "openpyxl": (3, 1),
}


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", version.split("+", 1)[0])
    return tuple(int(part) for part in parts[:3])


def _macos_version_tuple() -> tuple[int, ...]:
    version = platform.mac_ver()[0]
    return _version_tuple(version) if version else ()


def _python_version_from_venv_config() -> tuple[int, ...]:
    if not GUI_VENV_CONFIG.exists():
        return ()

    for line in GUI_VENV_CONFIG.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() in ("version", "version_info"):
            return _version_tuple(value.strip())
    return ()


def _macos_needs_python_312_or_newer(python_version: tuple[int, ...]) -> bool:
    macos_version = _macos_version_tuple()
    return bool(macos_version) and macos_version >= (15,) and python_version < (3, 12)


def _inside_gui_venv() -> bool:
    return Path(sys.prefix).resolve() == GUI_VENV_DIR.resolve()


def _gui_venv_is_safe_for_macos() -> bool:
    venv_version = _python_version_from_venv_config()
    return bool(venv_version) and not _macos_needs_python_312_or_newer(venv_version)


def _current_environment_needs_gui_venv() -> bool:
    if getattr(sys, "frozen", False) or _inside_gui_venv() or not GUI_VENV_PYTHON.exists():
        return False

    try:
        installed_versions = {
            package: _version_tuple(metadata.version(package))
            for package in MIN_DEPENDENCY_VERSIONS
        }
    except metadata.PackageNotFoundError:
        return True

    return any(
        installed_versions[package] < minimum
        for package, minimum in MIN_DEPENDENCY_VERSIONS.items()
    )


def _is_numpy_abi_failure(exc: BaseException) -> bool:
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return any(
        marker in details
        for marker in (
            "_ARRAY_API",
            "numpy.core.multiarray failed to import",
            "compiled using NumPy 1.x",
        )
    )


def _is_missing_tkinter_failure(exc: BaseException) -> bool:
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return "No module named '_tkinter'" in details


def _relaunch_with_gui_venv() -> None:
    if not GUI_VENV_PYTHON.exists():
        return
    if not _gui_venv_is_safe_for_macos():
        return
    if os.environ.get(RELAUNCH_ENV_VAR) == "1":
        return
    if _inside_gui_venv():
        return

    env = os.environ.copy()
    env[RELAUNCH_ENV_VAR] = "1"
    os.execvpe(str(GUI_VENV_PYTHON), [str(GUI_VENV_PYTHON), *sys.argv], env)


def _check_macos_gui_runtime() -> None:
    current_python_version = sys.version_info[:3]
    if not _macos_needs_python_312_or_newer(current_python_version):
        return

    if not _inside_gui_venv() and _gui_venv_is_safe_for_macos():
        _relaunch_with_gui_venv()
        return

    message = (
        "This macOS/Python Tk runtime is known to crash before the app can open.\n\n"
        f"Detected macOS: {platform.mac_ver()[0] or 'unknown'}\n"
        f"Detected Python: {platform.python_version()}\n\n"
        "Install Python 3.13 with matching Tk support, then rebuild the GUI environment:\n"
        "  cd \"Python version\"\n"
        "  rm -rf .venv-gui\n"
        "  ./setup_macos_gui_env.sh\n"
        "  ./.venv-gui/bin/python main.py"
    )
    raise SystemExit(message)


def _load_app_class():
    try:
        from skin_analysis.gui import ErrorBandViewerApp, RawDataViewerApp
    except (AttributeError, ImportError) as exc:
        if _is_missing_tkinter_failure(exc):
            message = (
                "Unable to import Tkinter because this Python installation does not "
                "include the _tkinter extension.\n\n"
                "If you installed Python with Homebrew, install the matching Tk package "
                "and rebuild the GUI environment:\n"
                "  brew install python-tk@3.13\n"
                "  cd \"Python version\"\n"
                "  rm -rf .venv-gui\n"
                "  ./setup_macos_gui_env.sh\n"
                "  ./.venv-gui/bin/python main.py"
            )
            raise SystemExit(message) from exc
        if _is_numpy_abi_failure(exc):
            _relaunch_with_gui_venv()
            message = (
                "Unable to import the GUI dependencies because the current Python "
                "environment has an incompatible NumPy/Matplotlib install.\n\n"
                "From the Python version folder, run:\n"
                "  ./setup_macos_gui_env.sh\n"
                "  ./.venv-gui/bin/python main.py"
            )
            raise SystemExit(message) from exc
        raise

    if "--classic" in sys.argv:
        if getattr(sys, "frozen", False):
            raise SystemExit("The portable edition contains Error Band Studio only. Use the source edition for --classic.")
        return RawDataViewerApp
    return ErrorBandViewerApp


def run() -> None:
    _check_macos_gui_runtime()

    if _current_environment_needs_gui_venv():
        _relaunch_with_gui_venv()

    app_class = _load_app_class()
    app = app_class()
    app.mainloop()
