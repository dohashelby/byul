from dataclasses import replace
import copy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest

from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.mvp.asset_binding import manifest_from_update_inventory, check_execution_binding, _canonical
from serving_runtime.mvp.assets import check, load_manifest
try:
    from guard.adapters.model_inventory import inventory
except ImportError:
    inventory = None

ROOT = Path(__file__).resolve().parents[1]


class AssetBindingTest(unittest.TestCase):
    @unittest.skipIf(inventory is None, "UPDATE package is required for inventory integration")
    def test_update_all_inventory_round_trip_and_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "model"
            root.mkdir()
            for name in ("config.json", "tokenizer.json", "chat_template.jinja", "model.safetensors"):
                (root / name).write_text(name)
            original = inventory(root)
            converted = manifest_from_update_inventory(original, str(root))
            out = Path(temporary) / "approved.json"
            out.write_text(json.dumps(converted))
            self.assertTrue(check(load_manifest(out))["intact"])
            (root / "tokenizer.json").write_text("different")
            self.assertEqual(check(load_manifest(out))["counts"]["changed"], 1)
            (root / "config.json").unlink()
            self.assertEqual(check(load_manifest(out))["counts"]["missing"], 1)

    @unittest.skipIf(inventory is None, "UPDATE package is required for inventory integration")
    def test_reject_partial_hash_drift_duplicate_and_symlink_consumption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "model"
            root.mkdir()
            (root / "tokenizer.json").write_text("ok")
            original = inventory(root)
            for update in ({"scope": "vllm"}, {"inventory_sha256": "0" * 64}, {"total_bytes": True}):
                with self.assertRaises(ValueError):
                    manifest_from_update_inventory({**original, **update}, str(root))
            duplicate = {**original, "files": original["files"] * 2}
            with self.assertRaises(ValueError):
                manifest_from_update_inventory(duplicate, str(root))
            target = Path(temporary) / "blob"
            target.write_text("ok")
            (root / "tokenizer.json").unlink()
            (root / "tokenizer.json").symlink_to(target)
            converted = manifest_from_update_inventory(inventory(root), str(root))
            out = Path(temporary) / "manifest.json"
            out.write_text(json.dumps(converted))
            result = check(load_manifest(out))
            self.assertFalse(result["intact"])
            self.assertEqual(result["counts"]["symlink"], 1)

    def setup_binding(self):
        target = json.loads((ROOT / "tests/fixtures/bundle-server-vllm.json").read_text())["target"]
        target["model"]["revision"] = "7ae557604adf67be50417f59c2c2f167def9a775"
        intent = parse_intent(json.loads((ROOT / "deploy/gpu-lab/intent.aws-lab.json").read_text().replace("REPLACE_IMAGE_DIGEST_HEX", "a" * 64).replace("REPLACE_PROFILE", "test")))
        pod = {"metadata": {"namespace": "serving-lab", "labels": {"app": "serving-engine"}, "uid": "pod1"},
               "spec": {"containers": [{"name": "engine", "command": ["vllm"], "args": ["serve", "/models"],
                         "volumeMounts": [{"name": "model", "mountPath": "/models", "readOnly": True},
                                          {"name": "cache", "mountPath": "/cache"}]}],
                        "volumes": [{"name": "model", "hostPath": {"path": "/srv/model"}},
                                    {"name": "cache", "hostPath": {"path": intent.assets.generation_path}}]},
               "status": {"phase": "Running", "containerStatuses": [{"name": "engine", "containerID": "containerd://1", "imageID": "repo@sha256:" + "a" * 64,
                           "restartCount": 0, "state": {"running": {"startedAt": "2026-11-06T00:00:00Z"}}}]}}
        return target, intent, pod

    def binding(self, target, intent, pod):
        return check_execution_binding(pod, target, intent, model_host_path="/srv/model", expected_command=["vllm"], expected_args=["serve", "/models"])

    def test_instance_mount_generation_and_launch_bound(self):
        target, intent, pod = self.setup_binding()
        self.assertEqual(self.binding(target, intent, pod)["state"], "match")
        changes = [
            (lambda p: p["spec"]["containers"][0]["args"].append("--unsafe"), "launch_mismatch"),
            (lambda p: p["spec"]["containers"][0]["volumeMounts"][0].update(readOnly=False), "model_mount_mismatch"),
            (lambda p: p["spec"]["volumes"][1]["hostPath"].update(path="/old-cache"), "cache_mount_mismatch"),
            (lambda p: p["status"]["containerStatuses"][0].update(imageID="repo@sha256:" + "b" * 64), "running_image_mismatch"),
            (lambda p: p["status"]["containerStatuses"][0].pop("containerID"), "instance_identity_incomplete"),
            (lambda p: p["spec"]["containers"][0]["volumeMounts"].append({"name": "override", "mountPath": "/models/tokenizer.json"}), "model_mount_mismatch"),
        ]
        for mutate, issue in changes:
            candidate = copy.deepcopy(pod)
            mutate(candidate)
            self.assertIn(issue, self.binding(target, intent, candidate)["issues"])
        target["model"]["revision"] = "other"
        self.assertIn("cache_generation_target_mismatch", self.binding(target, intent, pod)["issues"])

    def test_intent_engine_scope_matches_selected_pod_and_container(self):
        target, intent, pod = self.setup_binding()
        for engine in (replace(intent.engine, labels={"app": "another-engine"}),
                       replace(intent.engine, container_name="other-container")):
            result = self.binding(target, replace(intent, engine=engine), pod)
            self.assertIn("intent_engine_target_mismatch", result["issues"])

    def test_container_restart_produces_distinct_evidence_identity(self):
        target, intent, pod = self.setup_binding()
        before = self.binding(target, intent, pod)
        pod["status"]["containerStatuses"][0].update(containerID="containerd://2", restartCount=1)
        after = self.binding(target, intent, pod)
        self.assertEqual(before["pod_uid"], after["pod_uid"])
        self.assertNotEqual(before["container_id"], after["container_id"])

    @unittest.skipIf(inventory is None, "UPDATE package is required for inventory integration")
    def test_tool_uses_frozen_input_bytes_when_source_rotates(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("runtime_asset_check", ROOT / "tools/runtime_asset_check.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        target, intent, pod = self.setup_binding()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model"
            model.mkdir()
            (model / "config.json").write_text("config")
            intent_path = root / "intent.json"
            intent_raw = (ROOT / "deploy/gpu-lab/intent.aws-lab.json").read_text().replace("REPLACE_IMAGE_DIGEST_HEX", "a" * 64).replace("REPLACE_PROFILE", "test").encode()
            inputs = {"inventory": inventory(model), "target": target, "pod": pod,
                      "approved-launch": {"command": ["vllm"], "args": ["serve", "/models"]}}
            for name, document in inputs.items():
                (root / (name + ".json")).write_text(json.dumps(document))
            intent_path.write_bytes(intent_raw)
            arguments = []
            for name in ("inventory", "target", "pod", "approved-launch", "intent"):
                arguments += ["--" + name, str(root / (name + ".json"))]
            arguments += ["--root", str(model), "--model-host-path", "/srv/model",
                          "--manifest-out", str(root / "manifest.json"), "--report-out", str(root / "report.json")]
            original_write = tool._write_new
            def rotate_after_manifest(path, document):
                original_write(path, document)
                if path.endswith("manifest.json"):
                    intent_path.write_bytes(intent_raw.replace(b'"serving-lab"', b'"another-namespace"'))
            with patch.object(tool, "_write_new", side_effect=rotate_after_manifest):
                self.assertEqual(tool.main(arguments), 0)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["intent_sha256"], hashlib.sha256(intent_raw).hexdigest())
            self.assertNotEqual(report["intent_sha256"], hashlib.sha256(intent_path.read_bytes()).hexdigest())
            self.assertEqual(report["worker_consumption"], "unverified")
