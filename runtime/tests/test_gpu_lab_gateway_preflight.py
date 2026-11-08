"""Gateway host prerequisites with temporary files only; no AWS or Kubernetes."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.policy import load_policy
from tools import gpu_lab_gateway_preflight as preflight

ROOT = Path(__file__).resolve().parents[1]


class GatewayPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.approved = self.root / "approved"
        self.model = self.root / "model"
        self.approved.mkdir()
        self.model.mkdir()
        self.revocations = self.approved / "revocations.json"
        self.empty = {"schema_version": "runtime-mvp-revocations/1.0", "revoked": []}

    def checks(self):
        return {c.name: c for c in preflight.collect(self.approved, self.model)}

    def test_missing_approval_blocks_without_creating_or_approving_empty_list(self):
        checks = self.checks()
        self.assertFalse(checks["revocations"].ok)
        self.assertEqual(checks["revocations"].reason_code, "REVOCATIONS_NOT_FOUND")
        self.assertEqual(list(self.approved.iterdir()), [])

    def test_valid_empty_approved_list_passes_without_changing_files(self):
        self.revocations.write_text(json.dumps(self.empty))
        before = self.revocations.stat()
        self.assertTrue(all(c.ok for c in self.checks().values()))
        after = self.revocations.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_mode),
                         (after.st_ino, after.st_mtime_ns, after.st_mode))
        self.assertEqual(list(self.approved.iterdir()), [self.revocations])

    def test_both_missing_host_directories_are_reported_and_not_created(self):
        self.approved.rmdir()
        self.model.rmdir()
        checks = self.checks()
        self.assertEqual(checks["verification_directory"].reason_code, "VERIFICATION_DIRECTORY_NOT_FOUND")
        self.assertEqual(checks["model_directory"].reason_code, "MODEL_DIRECTORY_NOT_FOUND")
        self.assertFalse(self.approved.exists())
        self.assertFalse(self.model.exists())

    def test_regular_file_cannot_stand_in_for_either_host_directory(self):
        for directory, name in ((self.approved, "verification_directory"), (self.model, "model_directory")):
            with self.subTest(name=name):
                directory.rmdir()
                directory.write_text("not a directory")
                self.assertTrue(self.checks()[name].reason_code.endswith("_NOT_DIRECTORY"))
                directory.unlink()
                directory.mkdir()

    def test_invalid_json_schema_and_candidate_envelope_are_rejected(self):
        for raw, code in (("{", "MALFORMED_REVOCATIONS"),
                          ('{"schema_version":"runtime-mvp-revocations/1.0","revoked":[],"revoked":[]}', "MALFORMED_REVOCATIONS"),
                          (json.dumps({"revoked": []}), "INVALID_REVOCATIONS"),
                          (json.dumps({"schema_version": "runtime-verification-candidate/1.0", "document": self.empty}), "INVALID_REVOCATIONS")):
            with self.subTest(code=code):
                self.revocations.write_text(raw)
                check = self.checks()["revocations"]
                self.assertFalse(check.ok)
                self.assertEqual(check.reason_code, code)

    def test_permission_error_is_reason_code_without_raw_error_output(self):
        self.revocations.write_text(json.dumps(self.empty))
        with patch.object(Path, "read_bytes", side_effect=PermissionError("private error text")):
            check = self.checks()["revocations"]
        self.assertEqual(check.reason_code, "REVOCATIONS_READ_ERROR")

    def test_symlink_and_special_revocation_file_are_rejected_before_read(self):
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.empty))
        self.revocations.symlink_to(outside)
        self.assertEqual(self.checks()["revocations"].reason_code, "REVOCATIONS_INVALID_PATH")
        self.revocations.unlink()
        os.mkfifo(self.revocations)
        with patch.object(preflight, "load_revocations") as loader:
            self.assertEqual(self.checks()["revocations"].reason_code, "REVOCATIONS_NOT_REGULAR_FILE")
        loader.assert_not_called()

    def test_model_path_must_be_absolute_and_not_a_symlink(self):
        checks = preflight.collect(self.approved, "relative/model")
        self.assertEqual(checks[1].reason_code, "MODEL_DIRECTORY_INVALID_PATH")
        link = self.root / "linked-model"
        link.symlink_to(self.model, target_is_directory=True)
        self.assertEqual(preflight.collect(self.approved, link)[1].reason_code, "MODEL_DIRECTORY_INVALID_PATH")

    def test_cli_requires_explicit_model_path_and_returns_failure_then_success(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            preflight.build_parser().parse_args([])
        output = io.StringIO()
        argv = ["--approved-dir", str(self.approved), "--model-root", str(self.model)]
        with redirect_stdout(output):
            self.assertEqual(preflight.main(argv), 1)
        self.assertIn("REVOCATIONS_NOT_FOUND", output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        self.revocations.write_text(json.dumps(self.empty))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(preflight.main(argv), 0)

    def test_missing_optional_verification_files_do_not_deactivate_inference(self):
        self.revocations.write_text(json.dumps(self.empty))
        config = MVPConfig(policy_path=str(ROOT / "examples/mvp/policy.json"), data_dir=str(self.root / "data"),
                           revocations_path=str(self.revocations), asset_manifest_path=str(self.approved / "asset-manifest.json"),
                           baseline_path=str(self.approved / "baseline.json"), manifest_path=str(self.approved / "manifest.json"))
        app = GatewayApp(config, load_policy(config.policy_path), engine_address=("127.0.0.1", 8088))
        self.assertEqual(app.baseline_status()["baseline"]["state"], "not_configured")
        self.assertEqual(app.baseline_status()["manifest"]["state"], "not_configured")
        asset = next(c for c in app._collectors.collectors if c.source == "asset_integrity")
        asset.collect()
        self.assertEqual(asset.snapshot()["state"], "error")
        self.assertIn("ASSET_MANIFEST_NOT_APPROVED", asset.snapshot()["last_error"])
        self.assertTrue(app.policy_status()["gateway_active"])
        self.revocations.unlink()
        blocked = GatewayApp(config, load_policy(config.policy_path), engine_address=("127.0.0.1", 8088))
        self.assertFalse(blocked.policy_status()["gateway_active"])


if __name__ == "__main__":
    unittest.main()
