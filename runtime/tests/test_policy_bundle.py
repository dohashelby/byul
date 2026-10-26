"""LAB policy-bundle verification, CLI conflicts, and status exposure."""

from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.cli import main
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.policy_bundle import PolicyBundleError, verify_policy_bundle


ROOT = Path(__file__).resolve().parents[1]
SOURCE_BUNDLE = ROOT.parent / "contracts" / "examples" / "bundle"
NOW = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)


def _copy_bundle(parent: Path) -> Path:
    destination = parent / "bundle"
    shutil.copytree(SOURCE_BUNDLE, destination)
    return destination


def _manifest(directory: Path) -> dict[str, object]:
    return json.loads((directory / "bundle.json").read_text(encoding="utf-8"))


def _write_manifest(directory: Path, document: dict[str, object]) -> None:
    (directory / "bundle.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _entry(document: dict[str, object], role: str) -> dict[str, object]:
    files = document["files"]
    assert isinstance(files, list)
    return next(item for item in files if isinstance(item, dict) and item.get("role") == role)


def _refresh_entry(directory: Path, entry: dict[str, object]) -> None:
    raw = (directory / str(entry["path"])).read_bytes()
    entry["bytes"] = len(raw)
    entry["sha256"] = hashlib.sha256(raw).hexdigest()


class PolicyBundleVerificationTests(unittest.TestCase):
    def test_valid_contract_example_bundle(self) -> None:
        bundle = verify_policy_bundle(SOURCE_BUNDLE, now=NOW)
        self.assertEqual(bundle.bundle_id, "bundle-local-policy-001-3")
        self.assertEqual(bundle.target_ref, "mock-engine-001")
        self.assertEqual(
            {item.role for item in bundle.files},
            {"gateway_policy", "policy_approvals"},
        )
        self.assertEqual(len(bundle.sha256), 64)

    def test_duplicate_json_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            path = directory / "bundle.json"
            raw = path.read_text(encoding="utf-8").replace(
                '"bundle_id":', '"bundle_id": "duplicate",\n  "bundle_id":', 1
            )
            path.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(PolicyBundleError, "bundle.json.*중복 JSON 키"):
                verify_policy_bundle(directory, now=NOW)

    def test_non_finite_json_number_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            path = directory / "bundle.json"
            raw = path.read_text(encoding="utf-8").replace(
                '"created_at": "2026-10-25T00:05:00Z"', '"created_at": NaN', 1
            )
            path.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(PolicyBundleError, "bundle.json.*유효한 JSON"):
                verify_policy_bundle(directory, now=NOW)

    def test_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            document = _manifest(directory)
            _entry(document, "gateway_policy")["sha256"] = "0" * 64
            _write_manifest(directory, document)
            with self.assertRaisesRegex(PolicyBundleError, "runtime-policy.json.*SHA-256"):
                verify_policy_bundle(directory, now=NOW)

    def test_byte_count_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            document = _manifest(directory)
            entry = _entry(document, "gateway_policy")
            entry["bytes"] = int(entry["bytes"]) + 1
            _write_manifest(directory, document)
            with self.assertRaisesRegex(PolicyBundleError, "runtime-policy.json.*bytes"):
                verify_policy_bundle(directory, now=NOW)

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            document = _manifest(directory)
            _entry(document, "gateway_policy")["path"] = "../runtime-policy.json"
            _write_manifest(directory, document)
            with self.assertRaisesRegex(PolicyBundleError, "bundle.json.*안전한 상대 경로"):
                verify_policy_bundle(directory, now=NOW)

    def test_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            directory = _copy_bundle(parent)
            outside = parent / "outside-policy.json"
            outside.write_bytes((directory / "runtime-policy.json").read_bytes())
            os.symlink(outside, directory / "escape.json")
            document = _manifest(directory)
            _entry(document, "gateway_policy")["path"] = "escape.json"
            _write_manifest(directory, document)
            with self.assertRaisesRegex(PolicyBundleError, "escape.json.*밖"):
                verify_policy_bundle(directory, now=NOW)

    def test_wrong_inner_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            policy_path = directory / "runtime-policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["schema_version"] = "runtime-mvp/1.0"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            document = _manifest(directory)
            _refresh_entry(directory, _entry(document, "gateway_policy"))
            _write_manifest(directory, document)
            with self.assertRaisesRegex(PolicyBundleError, "runtime-policy.json.*schema_version"):
                verify_policy_bundle(directory, now=NOW)

    def test_missing_gateway_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            document = _manifest(directory)
            files = document["files"]
            assert isinstance(files, list)
            document["files"] = [item for item in files if item.get("role") != "gateway_policy"]
            _write_manifest(directory, document)
            with self.assertRaisesRegex(PolicyBundleError, "gateway_policy"):
                verify_policy_bundle(directory, now=NOW)

    def test_duplicate_role_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            document = _manifest(directory)
            _entry(document, "policy_approvals")["role"] = "gateway_policy"
            _write_manifest(directory, document)
            with self.assertRaisesRegex(PolicyBundleError, "role이 중복"):
                verify_policy_bundle(directory, now=NOW)

    def test_expired_validity_is_rejected(self) -> None:
        with self.assertRaisesRegex(PolicyBundleError, "bundle.json.*만료"):
            verify_policy_bundle(
                SOURCE_BUNDLE,
                now=datetime(2028, 1, 1, tzinfo=timezone.utc),
            )

    def test_target_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            document = _manifest(directory)
            document["target_ref"] = "different-target"
            _write_manifest(directory, document)
            with self.assertRaisesRegex(PolicyBundleError, "runtime-policy.json.*target_id"):
                verify_policy_bundle(directory, now=NOW)


class PolicyBundleIntegrationTests(unittest.TestCase):
    def test_mvp_cli_uses_verified_startup_snapshot(self) -> None:
        captured: dict[str, object] = {}

        def inspect_config(config: MVPConfig) -> None:
            policy_path = Path(config.policy_path)
            approvals_path = Path(str(config.policy_approvals_path))
            self.assertTrue(policy_path.is_file())
            self.assertTrue(approvals_path.is_file())
            captured["policy"] = policy_path.read_bytes()
            captured["approvals"] = approvals_path.read_bytes()
            captured["bundle"] = config.policy_bundle

        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            document = _manifest(directory)
            document["validity"] = {"valid_from": None, "valid_until": None}
            _write_manifest(directory, document)
            expected_policy = (directory / "runtime-policy.json").read_bytes()
            expected_approvals = (directory / "policy-approvals.json").read_bytes()
            with patch("serving_runtime.mvp.app.run", side_effect=inspect_config):
                result = main(["mvp", "--policy-bundle", str(directory)])
        self.assertEqual(result, 0)
        self.assertEqual(captured["policy"], expected_policy)
        self.assertEqual(captured["approvals"], expected_approvals)
        self.assertIsInstance(captured["bundle"], dict)

    def test_policy_bundle_target_is_used_with_host_pod_snapshot(self) -> None:
        captured: dict[str, object] = {}

        def inspect_config(config: MVPConfig) -> None:
            captured["target"] = json.loads(Path(str(config.target_path)).read_text())
            captured["snapshot"] = config.target_pod_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            target = {
                "schema_version": "runtime-target/1.0",
                "target_id": "mock-engine-001",
                "engine": {"kind": "mock", "version": "mock-1.0"},
                "model": {"id": "test-model"},
                "kubernetes": {
                    "context": "kind-runtime-lab",
                    "namespace": "serving-lab",
                    "selector": {"app": "serving-engine"},
                    "container": "engine",
                },
            }
            target_path = directory / "target.json"
            target_path.write_text(json.dumps(target), encoding="utf-8")
            document = _manifest(directory)
            document["validity"] = {"valid_from": None, "valid_until": None}
            raw = target_path.read_bytes()
            document["files"].append(
                {
                    "role": "target",
                    "path": "target.json",
                    "schema_version": "runtime-target/1.0",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                }
            )
            _write_manifest(directory, document)
            with patch("serving_runtime.mvp.app.run", side_effect=inspect_config):
                result = main(
                    [
                        "mvp",
                        "--policy-bundle",
                        str(directory),
                        "--target-pod-snapshot",
                        "/srv/serving/observation/target-pods.json",
                    ]
                )
        self.assertEqual(result, 0)
        self.assertEqual(captured["target"], target)
        self.assertEqual(captured["snapshot"], "/srv/serving/observation/target-pods.json")

    def test_policy_bundle_conflicts_with_explicit_role_option(self) -> None:
        for option in (
            "--policy",
            "--policy-approvals",
            "--target",
            "--observation-exceptions",
            "--observation-intent",
        ):
            with self.subTest(option=option):
                error = io.StringIO()
                with redirect_stderr(error), self.assertRaises(SystemExit) as caught:
                    main(["mvp", "--policy-bundle", "bundle-dir", option, "value.json"])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("--policy-bundle", error.getvalue())
                self.assertIn(option, error.getvalue())

    def test_verified_enforcement_roles_stay_not_applied_without_tetragon(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            document = _manifest(directory)
            files = document["files"]
            assert isinstance(files, list)
            for role, name, schema in (
                ("enforcement_intent", "intent.json", "runtime-enforcement-intent/1.1"),
                ("exec_exceptions", "exceptions.json", "runtime-exec-exceptions/1.0"),
            ):
                path = directory / name
                path.write_text(json.dumps({"schema_version": schema}), encoding="utf-8")
                raw = path.read_bytes()
                files.append(
                    {
                        "role": role,
                        "path": name,
                        "schema_version": schema,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw),
                    }
                )
            _write_manifest(directory, document)
            bundle = verify_policy_bundle(directory, now=NOW)
        options, status = bundle.mvp_options(())
        self.assertNotIn("observation_intent_path", options)
        self.assertNotIn("observation_exceptions_path", options)
        applied = {item["role"]: item["applied"] for item in status["files"]}
        self.assertFalse(applied["enforcement_intent"])
        self.assertFalse(applied["exec_exceptions"])
        applied_options, applied_status = bundle.mvp_options(
            (("tetragon", "tetragon.jsonl"),)
        )
        self.assertIn("observation_intent_path", applied_options)
        self.assertIn("observation_exceptions_path", applied_options)
        applied = {
            item["role"]: item["applied"] for item in applied_status["files"]
        }
        self.assertTrue(applied["enforcement_intent"])
        self.assertTrue(applied["exec_exceptions"])

    def test_materialized_files_keep_verified_startup_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = _copy_bundle(Path(temporary))
            bundle = verify_policy_bundle(directory, now=NOW)
            with bundle.materialized_files() as role_paths:
                snapshot = Path(role_paths["gateway_policy"])
                expected = snapshot.read_bytes()
                (directory / "runtime-policy.json").write_bytes(b"changed after verify")
                self.assertEqual(snapshot.read_bytes(), expected)
                options, _status = bundle.mvp_options((), role_paths=role_paths)
                self.assertEqual(Path(options["policy_path"]).read_bytes(), expected)
            self.assertFalse(snapshot.exists())

    def test_bundle_status_exposes_applied_files_without_approval_claim(self) -> None:
        bundle = verify_policy_bundle(SOURCE_BUNDLE, now=NOW)
        options, bundle_status = bundle.mvp_options(())
        self.assertNotIn("approval", bundle_status)
        with tempfile.TemporaryDirectory() as temporary:
            config = MVPConfig(
                gateway_port=0,
                data_dir=temporary,
                policy_path=options["policy_path"],
                policy_approvals_path=options["policy_approvals_path"],
                policy_bundle=bundle_status,
            )
            gateway = GatewayApp(
                config,
                load_policy(config.policy_path),
                ("127.0.0.1", 1),
            )

            class FakeServer:
                server_address = ("127.0.0.1", 18080)

                @staticmethod
                def connection_status() -> dict[str, int]:
                    return {"active": 0, "limit": 64, "rejected": 0}

            gateway._server = FakeServer()  # type: ignore[assignment]
            status = gateway.status()
            policy = gateway.policy_status()
        self.assertEqual(status["policy_bundle"], bundle_status)
        self.assertEqual(policy["policy_bundle"], bundle_status)
        self.assertTrue(all(item["applied"] for item in bundle_status["files"]))
        self.assertEqual(bundle_status["policy_ref"]["policy_id"], "local-policy-001")

    def test_unused_bundle_status_is_null(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = MVPConfig(data_dir=temporary)
            gateway = GatewayApp(config, load_policy(ROOT / "examples/mvp/policy.json"), ("127.0.0.1", 1))
            self.assertIsNone(gateway.policy_status()["policy_bundle"])


if __name__ == "__main__":
    unittest.main()
