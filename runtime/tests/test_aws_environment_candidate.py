"""Live environment draft collection with a fake kubectl runner; no AWS access."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from serving_runtime.mvp.assets import snapshot
from tools import aws_environment_candidate as tool
from tools.aws_lab_preflight import CommandResult

ROOT = Path(__file__).resolve().parents[1]


def pod(name, container, node="node-private", digest="a", args=None, volumes=None):
    return {"metadata": {"name": name, "uid": "private-uid"},
            "spec": {"nodeName": node, "containers": [{"name": container, "args": args or [],
                 "volumeMounts": [{"name": "model", "mountPath": "/models", "readOnly": True}, {"name": "cache", "mountPath": "/cache"}]}], "volumes": volumes or []},
            "status": {"phase": "Running", "containerStatuses": [{"name": container, "ready": True,
                       "state": {"running": {}}, "imageID": "containerd://sha256:" + digest * 64,
                       "containerID": "private-container"}]}}


class FakeRunner:
    def __init__(self, model_root):
        self.calls = []
        engine = pod("engine-private", "engine", args=["--max-model-len", "2048"],
                     volumes=[{"name": "model", "hostPath": {"path": str(model_root)}}])
        self.pods = {"app=serving-engine": {"items": [engine]},
                     "app.kubernetes.io/name=tetragon": {"items": [pod("tetra-private", "tetragon")]},
                     "k8s-app=cilium": {"items": [pod("cilium-private", "cilium-agent")]}}
        self.fail = False

    def run(self, argv, *, timeout=8):
        self.calls.append(argv)
        if self.fail:
            return CommandResult(124, "RAW_PRIVATE_OUTPUT", "RAW_PRIVATE_ERROR")
        if "pods" in argv:
            selector = argv[argv.index("-l") + 1]
            return CommandResult(0, json.dumps(self.pods[selector]))
        if "nodes" in argv:
            return CommandResult(0, json.dumps({"items": [{"metadata": {"name": "node-private"},
                                     "status": {"nodeInfo": {"kernelVersion": "6.8.0-lab"}}}]}))
        if "nvidia-smi" in argv:
            return CommandResult(0, "NVIDIA Test GPU, 570.1\n")
        if "python3" in argv:
            return CommandResult(0, json.dumps({"version": "0.31.0", "model_id": "test-model"}))
        if "tetra" in argv:
            return CommandResult(0, "tetra version: v1.7.1\n")
        if "cilium-dbg" in argv:
            return CommandResult(0, "cilium-cli: v1.18.3\n")
        raise AssertionError(argv)


class EnvironmentCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model = self.root / "not-a-proven-model-revision"
        self.model.mkdir()
        (self.model / "tokenizer.json").write_text("tokenizer")
        (self.model / "weights.bin").write_bytes(b"weights")
        self.target = self.root / "target.json"
        target = json.loads((ROOT / "deploy/gpu-lab/target.aws-lab.json").read_text())
        target["engine"]["image_digest"] = "sha256:" + "b" * 64  # declared; never used as observed
        self.target.write_text(json.dumps(target))
        self.args = SimpleNamespace(target_registration=self.target, environment_id="lab-env", revision=2,
                                    gateway_policy=ROOT / "examples/mvp/policy.json")
        self.runner = FakeRunner(self.model)

    def test_live_digest_kernel_gpu_and_versions_are_observed_without_null_or_approval(self):
        value = tool.collect(self.args, self.runner)
        self.assertEqual(value["environment"]["target"]["engine"]["image_digest"], "sha256:" + "a" * 64)
        self.assertEqual(value["environment"]["target"]["engine"]["version"], "0.31.0")
        self.assertEqual(value["environment"]["target"]["runtime"]["gpu_count"], 1)
        self.assertEqual(value["observations"]["kernel"]["value"], "6.8.0-lab")
        self.assertEqual(value["observations"]["tetragon_version"]["value"], "1.7.1")
        self.assertEqual(value["observations"]["cilium_version"]["value"], "1.18.3")
        self.assertEqual(value["declaration_fields"]["option.max_model_len"], 2048)
        self.assertFalse(value["import_ready"])
        self.assertEqual(value["approval"], "not_approved")
        raw = json.dumps(value)
        for forbidden in (": null", "node-private", "engine-private", "private-uid", "private-container", "not-a-proven-model-revision"):
            self.assertNotIn(forbidden, raw)
        self.assertIn("/target/model/revision", value["unconfirmed"])
        self.assertNotIn("revision", value["environment"]["target"]["model"])
        self.assertTrue(all(c[0] == "kubectl" for c in self.runner.calls))
        self.assertFalse(any(word in c for c in self.runner.calls for word in ("apply", "patch", "delete", "create", "restart", "secret", "secrets")))

    def test_inventory_hash_uses_existing_update_all_scope_contract(self):
        value = tool.collect(self.args, self.runner)
        files = [{"path": f["path"], "bytes": f["size"], "sha256": f["sha256"]} for f in snapshot(self.model)["files"]]
        expected = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(value["environment"]["target"]["model"]["inventory_sha256"], expected)

    def test_failed_queries_do_not_reuse_declared_digest_or_claim_absence(self):
        self.runner.fail = True
        value = tool.collect(self.args, self.runner)
        self.assertEqual(value["environment"]["target"]["engine"], {})
        self.assertNotIn("engine.image_digest", value["declaration_fields"])
        unknown = value["unconfirmed"]["/target/engine/image_digest"]
        self.assertEqual(unknown["display"], "미확인")
        self.assertIn("조회 실패", unknown["reason"])
        self.assertNotIn("RAW_PRIVATE", json.dumps(value))
        self.assertNotIn(": null", json.dumps(value))

    def test_multiple_engine_pods_prevent_picking_a_digest_or_exec_target(self):
        document = self.runner.pods["app=serving-engine"]
        document["items"].append(copy.deepcopy(document["items"][0]))
        value = tool.collect(self.args, self.runner)
        self.assertNotIn("image_digest", value["environment"]["target"]["engine"])
        self.assertFalse(any("exec" in c for c in self.runner.calls))

    def test_missing_live_digest_never_falls_back_to_registered_digest(self):
        self.runner.pods["app=serving-engine"]["items"][0]["status"]["containerStatuses"][0]["imageID"] = "engine:tag"
        value = tool.collect(self.args, self.runner)
        self.assertNotIn("image_digest", value["environment"]["target"]["engine"])
        self.assertIn("/target/engine/image_digest", value["unconfirmed"])

    def test_agents_on_other_node_do_not_supply_versions(self):
        self.runner.pods["k8s-app=cilium"]["items"][0]["spec"]["nodeName"] = "other-node"
        value = tool.collect(self.args, self.runner)
        self.assertEqual(value["observations"]["cilium_version"]["state"], "unconfirmed")

    def test_prerelease_version_is_not_truncated_to_stable_release(self):
        self.assertEqual(tool._safe_version("0.31.0rc1.dev2+build"), "0.31.0rc1.dev2+build")
        self.assertEqual(tool._safe_version("tetra version: v1.7.1"), "1.7.1")

    def test_model_read_failure_is_explicit_and_does_not_use_asset_manifest_hash(self):
        with patch.object(tool, "snapshot", side_effect=PermissionError):
            value = tool.collect(self.args, self.runner)
        self.assertNotIn("inventory_sha256", value["environment"]["target"]["model"])
        self.assertIn("호스트 snapshot", value["unconfirmed"]["/target/model/inventory_sha256"]["reason"])

    def test_partial_environment_uses_contract_paths_without_extra_fields(self):
        # Unknowns have a sidecar because the unchanged contract forbids their
        # textual markers in digest/integer fields. The draft is NOT importable.
        value = tool.collect(self.args, self.runner)
        environment = value["environment"]
        self.assertEqual(set(environment), {"schema_version", "environment_id", "revision", "created_at", "data_origin", "target", "deployment", "facts"})
        self.assertNotIn("kernel", environment)
        self.assertNotIn("tetragon_version", environment["facts"])
        self.assertNotIn("source_commit", environment["target"]["engine"])


if __name__ == "__main__":
    unittest.main()
