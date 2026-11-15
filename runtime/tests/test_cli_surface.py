"""Freeze the existing CLI surface and its fresh-process help behavior."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests/fixtures/cli-surface-before-refactor.json"
spec = importlib.util.spec_from_file_location("_byul_cli_surface_contract", ROOT / "scripts/cli_surface.py")
surface = importlib.util.module_from_spec(spec)
spec.loader.exec_module(surface)


class CliSurfaceContractTests(unittest.TestCase):
    def test_complete_surface_equals_before(self):
        before = json.loads(BASELINE.read_text(encoding="utf-8"))
        original_path = sys.path[:]
        try:
            with patch.dict(os.environ, {"COLUMNS": "100"}), patch("importlib.metadata.version") as version:
                version.return_value = "0+contract"
                actual = surface.dump(ROOT / "src")
        finally:
            sys.path[:] = original_path
        self.assertEqual(surface.summary(actual)["arguments_total"], 155)
        # Approved environment-dependent default: preserve the checkout/tools rule.
        tools = next(a for a in before["parsers"]["agent"]["actions"] if a.get("dest") == "tools_dir")
        self.assertEqual(Path(tools["default"]).name, "tools")
        tools["default"] = str(ROOT / "tools")
        problems, allowed = surface.compare(before, actual, set(), {"mvp --config"}, {"mvp --kube-context"})
        self.assertEqual(problems, [])
        self.assertEqual(len(allowed), 2)

    def test_unapproved_argument_and_help_changes_are_rejected(self):
        before = json.loads(BASELINE.read_text(encoding="utf-8"))
        for kind in ("default", "help"):
            with self.subTest(kind=kind):
                after = copy.deepcopy(before)
                if kind == "default":
                    option = next(a for a in after["parsers"]["mvp"]["actions"] if a.get("dest") == "gateway_port")
                    option["default"] += 1
                else:
                    after["parsers"][""]["help_text"] += " "
                problems, allowed = surface.compare(before, after, set(), set())
                self.assertTrue(problems)
                self.assertEqual(allowed, [])

    def test_help_and_empty_command_keep_existing_import_boundary(self):
        script = """import contextlib, io, json, sys
from unittest.mock import patch
with patch("importlib.metadata.version", return_value="0+contract"):
 from serving_runtime.cli import main
 out, err = io.StringIO(), io.StringIO()
 with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
  try:
   code = main(ARGS)
  except SystemExit as error:
   code = error.code
 print(json.dumps({"code":code,"out":out.getvalue(),"err":err.getvalue(),
  "gateway_app":"serving_runtime.gateway.app" in sys.modules,
  "devtools":"serving_runtime.devtools" in sys.modules,
  "enforcement":"serving_runtime.enforcement" in sys.modules}))
"""
        outputs = []
        for arguments in (["--help"], []):
            with self.subTest(arguments=arguments):
                env = os.environ.copy()
                env["PYTHONPATH"] = str(ROOT / "src")
                result = subprocess.run(
                    [sys.executable, "-B", "-c", script.replace("ARGS", repr(arguments))],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                report = json.loads(result.stdout)
                self.assertEqual(report["code"], 0)
                self.assertEqual(report["err"], "")
                self.assertIn("usage: serving-runtime", report["out"])
                self.assertFalse(report["gateway_app"])
                self.assertFalse(report["devtools"])
                # Existing agent parser registration already imports enforcement.
                self.assertTrue(report["enforcement"])
                outputs.append(report["out"])
        self.assertEqual(outputs[0], outputs[1])
