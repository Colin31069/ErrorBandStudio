"""Build a minimal native distribution or a small cross-platform source ZIP."""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from importlib.util import find_spec
from pathlib import Path

from create_demo_data import create_demo_data


PROJECT = Path(__file__).resolve().parent


def make_source_zip(output: Path, examples: Path) -> Path:
    target = output / "ErrorBandStudio-source.zip"
    files = [PROJECT / name for name in (
        "main.py", "requirements-error-band.txt", "run_windows.bat", "run_macos.command",
        "build_windows.bat", "build_portable.py", "create_demo_data.py", "error_band.spec", "README_PORTABLE.md",
    )]
    files.extend(sorted((PROJECT / "skin_analysis").glob("*.py")))
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in files:
            archive.write(file, Path("ErrorBandStudio") / file.relative_to(PROJECT))
        for file in sorted(examples.rglob("*.xlsx")):
            archive.write(file, Path("ErrorBandStudio/ExampleData") / file.relative_to(examples))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true", help="Create a small ZIP that requires Python; do not build a native app.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "dist")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    examples = output / "ExampleData"
    create_demo_data(examples)
    source = make_source_zip(output, examples)
    print(f"Source ZIP: {source} ({source.stat().st_size / 1024 / 1024:.2f} MiB)", flush=True)
    if args.source_only:
        return
    if sys.platform not in ("win32", "darwin"):
        raise SystemExit("Native builds are configured for Windows and macOS. Use --source-only on this platform.")
    if find_spec("PyInstaller") is None:
        raise SystemExit("Install the build dependency first: python -m pip install pyinstaller")
    build_env = os.environ.copy()
    build_env["PYINSTALLER_CONFIG_DIR"] = str(PROJECT / "build" / "pyinstaller-cache")
    build_env.setdefault("MPLCONFIGDIR", str(PROJECT / "build" / "matplotlib-cache"))
    build_env.setdefault("XDG_CACHE_HOME", str(PROJECT / "build" / "font-cache"))
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(output),
        "--workpath", str(PROJECT / "build" / "error-band"), str(PROJECT / "error_band.spec"),
    ], cwd=PROJECT, env=build_env, check=True)
    if sys.platform == "darwin":
        app_path = output / "ErrorBandStudio.app"
        # Remove only Finder/resource-fork metadata from our generated app.
        for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
            subprocess.run(["/usr/bin/xattr", "-dr", attribute, str(app_path)], capture_output=True, check=False)
        subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app_path)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app_path)], check=True)
    suffix = f"macOS-{platform.machine()}" if sys.platform == "darwin" else "Windows"
    share_folder = output / f"ErrorBandStudio-{suffix}"
    if share_folder.exists():
        shutil.rmtree(share_folder)
    share_folder.mkdir(exist_ok=True)
    if sys.platform == "darwin":
        shutil.copytree(output / "ErrorBandStudio.app", share_folder / "ErrorBandStudio.app", dirs_exist_ok=True, symlinks=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(share_folder / "ErrorBandStudio.app")], check=True)
    else:
        shutil.copytree(output / "ErrorBandStudio", share_folder, dirs_exist_ok=True)
    shutil.copytree(examples, share_folder / "ExampleData", dirs_exist_ok=True)
    shutil.copy2(PROJECT / "README_PORTABLE.md", share_folder / "README.md")
    archive_path = output / f"{share_folder.name}.zip"
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(share_folder), str(archive_path)], check=True)
    else:
        shutil.make_archive(str(archive_path.with_suffix("")), "zip", root_dir=output, base_dir=share_folder.name)
    print(f"Native ZIP: {archive_path} ({archive_path.stat().st_size / 1024 / 1024:.2f} MiB)", flush=True)
    print("Windows builds must be built and tested on Windows. macOS builds use this Mac's architecture.", flush=True)


if __name__ == "__main__":
    main()
