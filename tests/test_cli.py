"""Check the installed package and command from outside the repository."""

from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstalledCommandTests(unittest.TestCase):
    def run_command(self, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=tempfile.gettempdir(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )

    def test_module_help_works_outside_repository(self) -> None:
        result = self.run_command(sys.executable, "-I", "-m", "serving_runtime", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: serving-runtime", result.stdout)
        self.assertIn("--version", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_installed_command_reports_package_version(self) -> None:
        command = Path(sys.executable).parent / "serving-runtime"
        result = self.run_command(str(command), "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"serving-runtime {version('serving-runtime')}")
        self.assertEqual(result.stderr, "")

    def test_unknown_option_is_rejected(self) -> None:
        result = self.run_command(sys.executable, "-I", "-m", "serving_runtime", "--unknown-option-for-test")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--unknown-option-for-test", result.stderr)


if __name__ == "__main__":
    unittest.main()
