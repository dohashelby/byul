"""Candidate/approval separation and deploy wiring, without a cluster or approval of live values."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.assets import check, load_manifest
from serving_runtime.mvp.collectors import load_declaration, compare_baseline, CollectorManager
from serving_runtime.mvp.policy_state import load_revocations, evaluate_policy
from serving_runtime.mvp.policy import load_policy
from tools import verification_material as material
from tools import aws_environment_candidate as environment
from tools.aws_lab_preflight import CommandResult

ROOT = Path(__file__).resolve().parents[1]


class VerificationMaterialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.candidates = self.root / "candidates"
        self.approved = self.root / "approved"
        for directory in (self.candidates, self.approved):
            directory.mkdir(mode=0o2750)
            directory.chmod(0o2750)
        self.models = self.root / "models"
        self.models.mkdir()
        (self.models / "tokenizer.json").write_text("tokenizer-v1")
        (self.models / "weights.bin").write_bytes(b"weights-v1")

    def save_candidate(self, kind="assets", document=None):
        value = material.assets_candidate(self.models, str(self.models)) if document is None else material.candidate(kind, document)
        path = self.candidates / (kind + ".json")
        material.atomic_write(path, material.encode(value))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return path, digest

    def test_candidate_never_acts_as_asset_approval_and_publish_hash_is_exact(self):
        path, digest = self.save_candidate()
        self.assertFalse((self.approved / "asset-manifest.json").exists())
        with self.assertRaises(ValueError):
            load_manifest(path)
        with self.assertRaises(ValueError):
            material.approve(path, self.approved, "0" * 64, "reviewer", "reviewed")
        self.assertEqual(list(self.approved.iterdir()), [])
        material.approve(path, self.approved, digest, "reviewer", "reviewed hashes against external evidence")
        target = self.approved / "asset-manifest.json"
        self.assertTrue(check(load_manifest(target))["intact"])
        (self.models / "weights.bin").write_bytes(b"changed")
        self.assertFalse(check(load_manifest(target))["intact"])
        for output in (path, *self.approved.iterdir()):
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o640)
            self.assertEqual(output.stat().st_gid, output.parent.stat().st_gid)
        receipt = material.read_json(next(self.approved.glob("*.approval.json")))
        self.assertEqual(receipt["document_sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(receipt["candidate_sha256"], digest)

    def test_missing_review_or_changed_candidate_cannot_be_published(self):
        path, digest = self.save_candidate()
        for reviewer, reason in (("", "reason"), ("reviewer", " ")):
            with self.assertRaises(ValueError):
                material.approve(path, self.approved, digest, reviewer, reason)
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            material.approve(path, self.approved, digest, "reviewer", "reason")
        self.assertFalse(list(self.approved.iterdir()))

    def test_existing_approval_is_preserved_without_explicit_replacement(self):
        path, digest = self.save_candidate()
        material.approve(path, self.approved, digest, "reviewer", "initial review")
        with self.assertRaises(ValueError):
            material.approve(path, self.approved, digest, "reviewer", "another review")
        material.approve(path, self.approved, digest, "reviewer", "explicit replacement", replace=True)
        self.assertEqual(len(list(self.approved.glob("asset-manifest.json"))), 1)

    def test_symlink_or_unreadable_partial_asset_tree_is_not_candidate(self):
        (self.models / "linked").symlink_to(self.models / "weights.bin")
        with self.assertRaises(ValueError):
            material.assets_candidate(self.models)
        (self.models / "linked").unlink()
        with patch.object(material, "_walk", return_value=([], [], [], 1)):
            with self.assertRaises(ValueError):
                material.assets_candidate(self.models)

    def test_candidate_atomic_write_cannot_overwrite_or_follow_link(self):
        path, _ = self.save_candidate()
        old = path.read_bytes()
        with self.assertRaises(FileExistsError):
            material.atomic_write(path, b"changed")
        link = self.candidates / "link"
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            material.atomic_write(link, b"changed", replace=True)
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(list(self.candidates.glob(".verification-*")), [])

    def test_baseline_and_manifest_require_separate_explicit_approval(self):
        observed = {"schema_version": environment.SCHEMA, "declaration_fields": {"model.id": "test-model", "option.max_model_len": 2048}}
        for kind in ("baseline", "manifest"):
            value = material.declaration_candidate(observed, kind)
            path = self.candidates / (kind + ".json")
            material.atomic_write(path, material.encode(value))
            self.assertEqual(load_declaration(path, kind=kind).state, "error")
            material.approve(path, self.approved, hashlib.sha256(path.read_bytes()).hexdigest(), "reviewer", "scope reviewed")
        baseline = load_declaration(self.approved / "baseline.json", kind="baseline")
        manifest = load_declaration(self.approved / "manifest.json", kind="manifest")
        comparison = compare_baseline(baseline, manifest, CollectorManager([]))
        self.assertEqual([r["state"] for r in comparison["rows"] if r["source"] == "declared_manifest"], ["match", "match"])
        self.assertTrue(all(r["state"] == "not_collected" for r in comparison["rows"] if r["source"] == "engine_api"))

    def test_missing_approved_baseline_is_unconfigured_but_read_error_is_error(self):
        path = self.approved / "baseline.json"
        state = load_declaration(path, kind="baseline")
        self.assertEqual(state.state, "not_configured")
        self.assertEqual(state.error_code, "BASELINE_NOT_FOUND")
        with patch.object(Path, "read_bytes", side_effect=PermissionError):
            self.assertEqual(load_declaration(path, kind="baseline").state, "error")

    def test_asset_missing_approval_reports_unapproved(self):
        app = SimpleNamespace(config=SimpleNamespace(asset_manifest_path=self.approved / "asset-manifest.json"))
        with self.assertRaisesRegex(ValueError, "ASSET_MANIFEST_NOT_APPROVED"):
            GatewayApp._collect_asset_integrity(app)

    def test_empty_revocations_missing_and_permission_error_have_different_results(self):
        target = self.approved / "revocations.json"
        missing = load_revocations(target)
        self.assertEqual((missing.state, missing.error_code), ("unknown", "REVOCATIONS_NOT_FOUND"))
        doc = {"schema_version": "runtime-mvp-revocations/1.0", "revoked": []}
        path, digest = self.save_candidate("revocations", doc)
        material.approve(path, self.approved, digest, "reviewer", "explicitly reviewed empty list")
        empty = load_revocations(target)
        self.assertEqual((empty.state, len(empty.entries)), ("ok", 0))
        with patch.object(Path, "read_bytes", side_effect=PermissionError):
            self.assertEqual(load_revocations(target).error_code, "REVOCATIONS_READ_ERROR")
        policy = load_policy(ROOT / "examples/mvp/policy.json")
        self.assertFalse(evaluate_policy(policy, missing)["gateway_active"])
        self.assertEqual(evaluate_policy(policy, empty)["validity"][1]["result"], "not_revoked")

    def test_invalid_candidate_approval_does_not_change_active_file(self):
        path, digest = self.save_candidate("revocations", {"schema_version": "runtime-mvp-revocations/1.0", "revoked": []})
        material.approve(path, self.approved, digest, "reviewer", "reviewed")
        target = self.approved / "revocations.json"
        old = target.read_bytes()
        path.write_bytes(material.encode(material.candidate("revocations", {"revoked": []})))
        with self.assertRaises(ValueError):
            material.approve(path, self.approved, hashlib.sha256(path.read_bytes()).hexdigest(), "reviewer", "reviewed", replace=True)
        self.assertEqual(target.read_bytes(), old)
        path.write_text("[]")
        with self.assertRaisesRegex(ValueError, "NOT_A_CANDIDATE"):
            material.approve(path, self.approved, hashlib.sha256(path.read_bytes()).hexdigest(), "reviewer", "reviewed", replace=True)
        self.assertEqual(target.read_bytes(), old)

    def test_cli_does_not_echo_failed_input_or_subprocess_output(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            result = material.main(["approve", "--candidate", str(self.root / "private-input"), "--expect-sha256", "bad", "--reviewer", "x", "--reason", "x"])
        self.assertEqual(result, 1)
        self.assertNotIn("private-input", out.getvalue())

    def test_gpu_lab_wires_approved_files_and_same_model_snapshot_read_only(self):
        gateway = (ROOT / "deploy/gpu-lab/gateway.yaml").read_text()
        engine = (ROOT / "deploy/gpu-lab-sglang/engine.yaml").read_text()
        for option, filename in (("asset-manifest", "asset-manifest.json"), ("baseline", "baseline.json"),
                                 ("manifest", "manifest.json"), ("revocations", "revocations.json")):
            self.assertIn("- --" + option + "\n            - /etc/serving/verification/" + filename, gateway)
        self.assertIn("mountPath: /etc/serving/verification, readOnly: true", gateway)
        self.assertIn("mountPath: /models, readOnly: true", gateway)
        model = "/srv/serving/models/7ae557604adf67be50417f59c2c2f167def9a775"
        self.assertIn("hostPath: {path: " + model + ", type: Directory}", gateway)
        self.assertIn("hostPath: {path: " + model + ", type: Directory}", engine)
        self.assertIn("readOnlyRootFilesystem: true", gateway)
        self.assertIn("runAsUser: 10000", gateway)
        self.assertNotIn("/verification/candidates", gateway)


if __name__ == "__main__":
    unittest.main()
