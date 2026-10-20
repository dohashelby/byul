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

    def test_file_info_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "확인할 파일.bin"
            path.write_bytes(b"abc")
            command = Path(sys.executable).parent / "serving-runtime"
            result = self.run_command(str(command), "file-info", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("크기(bytes): 3", result.stdout)
            self.assertIn("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", result.stdout)
            self.assertEqual(result.stderr, "")
            self.assertEqual(path.read_bytes(), b"abc")

    def test_missing_file_fails_without_success_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing"
            result = self.run_command(sys.executable, "-I", "-m", "serving_runtime", "file-info", str(path))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("파일 확인 실패", result.stderr)

    def test_directory_fails_without_success_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_command(sys.executable, "-I", "-m", "serving_runtime", "file-info", directory)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("파일 확인 실패", result.stderr)

    def test_file_info_requires_path(self) -> None:
        result = self.run_command(sys.executable, "-I", "-m", "serving_runtime", "file-info")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
