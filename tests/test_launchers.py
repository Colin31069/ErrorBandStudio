from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skin_analysis import launcher


PROJECT = Path(__file__).resolve().parents[1]
ZSH = shutil.which("zsh")


class LauncherConfigurationTests(unittest.TestCase):
    def check_config(self, contents: str) -> tuple[int, ...]:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "pyvenv.cfg"
            config.write_text(contents)
            with patch.object(launcher, "GUI_VENV_CONFIG", config):
                return launcher._python_version_from_venv_config()

    def test_reads_standard_venv_version(self) -> None:
        self.assertEqual(self.check_config("home = /python/bin\nversion = 3.13.13\n"), (3, 13, 13))

    def test_reads_uv_venv_version_info(self) -> None:
        self.assertEqual(self.check_config("implementation = CPython\nversion_info = 3.13.13\n"), (3, 13, 13))

    def test_uv_environment_is_accepted_as_safe_on_macos_15(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "pyvenv.cfg"
            config.write_text("version_info = 3.13.13\n")
            with patch.object(launcher, "GUI_VENV_CONFIG", config), patch.object(launcher.platform, "mac_ver", return_value=("15.7.4", (), "")):
                self.assertTrue(launcher._gui_venv_is_safe_for_macos())


@unittest.skipUnless(ZSH, "macOS source launcher requires zsh")
class MacSourceLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binaries = self.root / "bin"
        self.binaries.mkdir()
        shutil.copy2(PROJECT / "run_macos.command", self.root / "run_macos.command")

    def python(self, destination: Path, label: str, compatible: bool = True) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-c" ]; then\n'
            f"  exit {0 if compatible else 1}\n"
            'elif [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
            '  /bin/mkdir -p "$3/bin"\n'
            '  /bin/cp "$0" "$3/bin/python"\n'
            'elif [ "$1" = "main.py" ]; then\n'
            f"  printf '%s\\n' '{label}' > chosen-python.txt\n"
            'else\n'
            '  exit 2\n'
            'fi\n',
            encoding="utf-8",
        )
        destination.chmod(0o755)

    def run_launcher(self) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PATH"] = str(self.binaries)
        return subprocess.run(
            [ZSH, "-f", str(self.root / "run_macos.command")],
            cwd=self.root, env=env, capture_output=True, text=True, input="\n", timeout=10,
        )

    def test_skips_incompatible_python_and_uses_working_version(self) -> None:
        self.python(self.binaries / "python3.13", "broken-3.13", compatible=False)
        self.python(self.binaries / "python3.12", "working-3.12")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "chosen-python.txt").read_text().strip(), "working-3.12")

    def test_falls_back_to_generic_python3_with_supported_runtime(self) -> None:
        self.python(self.binaries / "python3", "generic-python3")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "chosen-python.txt").read_text().strip(), "generic-python3")

    def test_incompatible_gui_venv_does_not_mask_working_python(self) -> None:
        self.python(self.root / ".venv-gui/bin/python", "broken-venv", compatible=False)
        self.python(self.binaries / "python3.13", "working-3.13")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "chosen-python.txt").read_text().strip(), "working-3.13")

    def test_existing_band_venv_without_tk_stops_before_dependency_install(self) -> None:
        self.python(self.root / ".venv-band/bin/python", "broken-band", compatible=False)
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tk", result.stdout)
        self.assertFalse((self.root / "chosen-python.txt").exists())

    def test_no_compatible_python_explains_requirements(self) -> None:
        self.python(self.binaries / "python3.13", "broken", compatible=False)
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.12", result.stdout)
        self.assertIn("Tk", result.stdout)


if __name__ == "__main__":
    unittest.main()
