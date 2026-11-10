"""Local assembly checks; no Kubernetes or AWS operations."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.enforcement.attribution import KERNEL_DATA
from tools.prepare_tetragon_deployment import prepare, ROOT
from tools.tetragon_trial import atomic_json
from tools.tetragon_identities import IdentityCollector
from tools.tetragon_status import StatusCollector
from tools import k8s_observation_feed as feed
from test_mvp_observability import FakeProcess


class DeploymentTests(unittest.TestCase):
    def test_scopes_identity_policy_image_and_gateway_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            record = prepare(ROOT / "deploy/gpu-lab/intent.aws-lab.json", "python@sha256:" + "a"*64, output)
            self.assertEqual(record['status_service'], 'serving-runtime-tetragon-status.service')
            self.assertFalse((ROOT/'deploy/gpu-lab/host/byul-tetragon-status.service').exists())
            self.assertNotIn('--hubble-only', (ROOT/'deploy/gpu-lab/host/byul-observation-feed.service').read_text())
            documents = [json.loads(path.read_text()) for path in output.glob("tetragon-*.json")]
            self.assertEqual(set(record["policies"]), {d["metadata"]["name"] for d in documents})
            service = (output / "byul-tetragon-stream.service").read_text()
            for document in documents:
                self.assertIn("--policy " + document["metadata"]["name"], service)
                self.assertTrue(all(p["data"] == KERNEL_DATA for p in document["spec"]["kprobes"]))
            self.assertNotIn("runtime-tg-deny-01", service)
            health = next(d for d in documents if d["metadata"]["name"] == "runtime-tg-health-01")
            self.assertEqual(health["spec"]["podSelector"]["matchLabels"], {"app": "byul-tetragon-sentinel"})
            self.assertEqual(health["spec"]["kprobes"][0]["selectors"][0]["matchActions"], [{"action": "Post"}])
            sentinel = json.loads((output / "sentinel.json").read_text())["spec"]["template"]["spec"]
            self.assertTrue(all(c["image"] == record["sentinel_image"] for c in sentinel["containers"] + sentinel["initContainers"]))
            # The reviewed Python 3.12 image's /proc/exe is canonical. An
            # interpreter symlink would conflict with exact kernel identity.
            self.assertTrue(all(c["command"][0] == "/usr/local/bin/python3.12"
                                for c in sentinel["containers"] + sentinel["initContainers"]))
            with self.assertRaises(ValueError):
                prepare(ROOT / "deploy/gpu-lab/intent.aws-lab.json", record["sentinel_image"], output)

    def test_discovery_gap_heartbeat_is_group_readable(self):
        from tools.tetragon_stream import discovery_pending
        with tempfile.TemporaryDirectory() as temporary, patch.object(feed, "_reader_gid", os.getgid()):
            discovery_pending(temporary, "byul-aws-lab", "2026-11-06T07:00:00Z", "gap", first=True)
            path = Path(temporary) / "tetragon.heartbeat.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(json.loads(path.read_text())["state"], "reconnecting")

    def test_additional_engine_selector_constraints_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = json.loads((ROOT / "deploy/gpu-lab/intent.aws-lab.json").read_text())
            document["engine"]["labels"]["lab-profile"] = "dev"
            path = root / "intent.json"
            path.write_text(json.dumps(document))
            output = root / "out"
            prepare(path, "python@sha256:" + "a"*64, output)
            engine = json.loads((output / "tetragon-tg-secret-01.json").read_text())
            self.assertEqual(engine["spec"]["podSelector"]["matchLabels"], document["engine"]["labels"])
            document["engine"]["labels"]["app"] = "unrelated"
            path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                prepare(path, "python@sha256:" + "a"*64, root / "rejected")

    def test_mutable_sentinel_tag_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                prepare(ROOT / "deploy/gpu-lab/intent.aws-lab.json", "python:latest", Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_atomic_snapshot_reader_group_survives_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            for n in range(2):
                atomic_json(path, {"n": n}, reader_gid=os.getgid())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
                self.assertEqual(path.stat().st_gid, os.getgid())
            self.assertFalse(list(Path(temporary).glob("*.next")))

    def test_snapshot_collectors_publish_group_readable_even_on_source_failure(self):
        class Result:
            returncode = 1
            stdout = ""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity.json"
            status = root / "state.json"
            IdentityCollector("ctx", "serving-lab", "app=serving-engine", identity,
                              runner=lambda _: Result(), reader_gid=os.getgid()).collect()
            StatusCollector("ctx", status, root, runner=lambda _: Result(), reader_gid=os.getgid()).collect()
            for path in (identity, status):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
                self.assertEqual(json.loads(path.read_text())["state"], "error")

    def test_hubble_only_does_not_open_or_spawn_legacy_tetragon(self):
        processes = [FakeProcess(), FakeProcess()]
        with (tempfile.TemporaryDirectory() as temporary,
              patch.object(feed, "assert_safe_context"), patch.object(feed, "_wait_for_hubble"),
              patch.object(feed, "_start", side_effect=processes) as start):
            root = Path(temporary)
            self.assertEqual(feed.run(root, "byul-aws-lab", hubble_only=True, max_ticks=0), 0)
            self.assertEqual(start.call_count, 2)
            self.assertFalse(list(root.glob("tetragon*")))
            self.assertTrue((root / "hubble.heartbeat.json").exists())
