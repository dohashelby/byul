"""Host publication and Gateway dashboard connections, without cluster access."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

from serving_runtime.enforcement.intent import load_intent
from serving_runtime.enforcement.tetragon import comparable_spec, render_monitor_manifests
from serving_runtime.enforcement.verification import KERNEL_IDENTITY_DATA, reassess_record
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.lab_report import LabReportSource
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.policy_state import external_enforcement
from serving_runtime.mvp.tetragon_state import read_tetragon_state
from tools.k8s_lab_check import main as lab_main, publish_report
from tools.tetragon_public import public_snapshot
from tools.tetragon_status import StatusCollector

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    return {"schema_version": "runtime-tetragon-state/1.0", "context": "kind-runtime-lab",
        "generated_at": datetime.now(timezone.utc).isoformat(), "state": "ok", "fresh": True,
        "agents": [{"node": "private-host", "pod_uid": "private-agent", "restart_count": 0}],
        "policies": [{"node": "private-host", "namespace": "serving-lab", "name": "policy",
                      "id": "1", "state": "enabled", "mode": "monitor", "spec_sha256": "a" * 64}],
        "capabilities": [], "transitions": [], "verifications": []}


class PublicStateTests(unittest.TestCase):
    def test_public_trial_preserves_reassessment_without_promoting_missing_evidence(self):
        at = datetime.now(timezone.utc) - timedelta(seconds=5)
        trial = {"id": "synthetic-proof", "context": "kind-runtime-lab", "data_origin": "synthetic",
            "node": "private-host", "namespace": "test", "policy": "trial", "method": "override_lsm",
            "pod_uid": "private-pod", "main_pid": 42, "probe_path": "/run/probe/file", "policy_id": "7",
            "started_at": at.isoformat(), "finished_at": (at + timedelta(seconds=4)).isoformat(),
            "identity_probe_data": KERNEL_IDENTITY_DATA,
            "task_identity": {"pod_uid": "private-pod", "namespace": "test", "container_id": "private-container",
                              "node_pid": 42, "ns_pid": 1, "pidns": 123, "start_ticks": 1000, "clock_ticks": 100},
            "policy_actions": [{"action": "Override", "argError": -1}, {"action": "Sigkill"}],
            "facts": dict.fromkeys(("main_process", "collector_complete", "monitor_read_ok", "restore_read_ok",
                                    "cleanup_ok", "service_unavailable", "container_restarted", "service_recovered"), True),
            "events": [{"time": (at + timedelta(seconds=1)).isoformat(), "policy": "trial", "pod_uid": "private-pod",
                        "pid": 42, "ns_pid": 1, "function": "security_file_permission", "action": "KPROBE_ACTION_SIGKILL",
                        "return_value": -1, "path": "/run/probe/file", "access_mask": 4, "identity_source": "kernel_task",
                        "kernel_task": {"pid": 42, "pidns": 123, "start_ns": "10005000000"}}],
            "steps": [{"at": at.isoformat(), "target_mode": "enforce", "ok": True,
                       "before": {"mode": "monitor", "id": "7", "state": "enabled"},
                       "after": {"mode": "enforce", "id": "7", "state": "enabled"}},
                      {"at": (at + timedelta(seconds=3)).isoformat(), "target_mode": "monitor", "ok": True,
                       "before": {"mode": "enforce", "id": "7", "state": "enabled"},
                       "after": {"mode": "monitor", "id": "7", "state": "enabled"}}],
            "termination": {"exitCode": 137, "reason": "Error", "finishedAt": (at + timedelta(seconds=2)).isoformat()}}
        expected = reassess_record(trial)["verification"]
        self.assertEqual(expected["file_read_denial"], "confirmed")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public.json"
            raw = snapshot()
            raw["verifications"] = [trial]
            path.write_text(json.dumps(public_snapshot(raw)))
            self.assertEqual(read_tetragon_state(path)["verifications"][0]["verification"], expected)
            trial["events"] = []
            trial["verification"] = expected  # A label alone is never proof.
            path.write_text(json.dumps(public_snapshot(raw)))
            self.assertEqual(read_tetragon_state(path)["verifications"][0]["verification"]["file_read_denial"], "unknown")

    def test_allowlist_excludes_secrets_and_opaque_host_ids_even_in_nested_evidence(self):
        raw = snapshot()
        raw.update(token="private-token", error="private-token private-host", argv=["private-token"])
        raw["policies"][0]["load_error"] = "private-token"
        raw["monitoring"] = {"state": "recovering", "epochs": {"private-host": "private-agent"},
            "event_verified_at": {"private-host": raw["generated_at"]},
            "reasons": ["waiting_for_attributed_event", "private-token"],
            "open_gap": {"opened_at": raw["generated_at"], "reason": "private-token"}}
        raw["verifications"] = [{"node": "private-host", "pod_uid": "private-pod",
            "task_identity": {"pod_uid": "private-pod", "container_id": "private-container", "node_pid": 42},
            "events": [{"pod_uid": "private-pod", "exec_id": "private-exec", "argv": ["private-token"]}],
            "error": "private-token", "facts": {"event_bound": True, "token": "private-token"}}]
        safe = public_snapshot(raw)
        encoded = json.dumps(safe)
        for secret in ("private-token", "private-host", "private-agent", "private-pod", "private-container", "private-exec"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(safe["agents"][0]["node"], safe["policies"][0]["node"])
        trial = safe["verifications"][0]
        self.assertEqual(trial["pod_uid"], trial["task_identity"]["pod_uid"])
        self.assertEqual(trial["pod_uid"], trial["events"][0]["pod_uid"])
        self.assertEqual(raw["agents"][0]["node"], "private-host")

    def test_collector_publishes_atomic_0640_copy_preserving_setgid_and_private_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            shared.mkdir()
            shared.chmod(0o2750)
            public = shared / "state.json"
            public.write_text('"previous"')
            output = root / "private/state.json"
            collector = StatusCollector("ctx", output, root / "evidence", public_output=public,
                runner=lambda _: subprocess.CompletedProcess([], 1, "", "private-token"))
            collector.capabilities = [{"node": "private-host", "checked_at": "now", "available": ["monitor"],
                                       "diagnostics": "private-token"}]
            original_replace = Path.replace
            observed = []

            def replace(path, target):
                if target == public:
                    self.assertEqual(public.read_text(), '"previous"')
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
                    self.assertEqual(path.stat().st_gid, shared.stat().st_gid)
                    observed.append(True)
                return original_replace(path, target)

            previous_umask = os.umask(0o002)
            try:
                with patch.object(Path, "replace", replace):
                    collector.collect()
            finally:
                os.umask(previous_umask)
            self.assertEqual(observed, [True])
            self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o2750)
            self.assertEqual(stat.S_IMODE(public.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
            self.assertIn("private-host", output.read_text())
            self.assertNotIn("private-host", public.read_text())
            self.assertFalse(public.with_name("state.json.next").exists())
            self.assertEqual(read_tetragon_state(public, context="ctx")["state"], "error")

    def test_failed_replace_preserves_previous_public_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public.json"
            public.write_text('"previous"')
            collector = StatusCollector("ctx", root / "private.json", root / "evidence", public_output=public,
                runner=lambda _: subprocess.CompletedProcess([], 1, "", ""))
            original_replace = Path.replace

            def fail_public(path, target):
                if target == public:
                    raise OSError("replacement failed")
                return original_replace(path, target)

            with patch.object(Path, "replace", fail_public), self.assertRaises(OSError):
                collector.collect()
            self.assertEqual(public.read_text(), '"previous"')


class ExternalEnforcementTests(unittest.TestCase):
    expected = {("serving-lab", "policy"): "a" * 64}

    def test_absent_error_stale_unbound_and_fresh_modes(self):
        for state, expected in ((None, "not_connected"), ("not_configured", "not_connected"),
                                ("error", "error"), ("stale", "unknown")):
            with self.subTest(state=state):
                sample = None if state is None else {**snapshot(), "state": state}
                self.assertEqual(external_enforcement(sample, self.expected), expected)
        self.assertEqual(external_enforcement(snapshot(), None), "unknown")
        self.assertEqual(external_enforcement({**snapshot(), "fresh": False}, self.expected), "unknown")
        for mode in ("monitor", "monitor_only", "enforce", "unknown"):
            with self.subTest(mode=mode):
                sample = snapshot()
                sample["policies"][0]["mode"] = mode
                self.assertEqual(external_enforcement(sample, self.expected), "unknown" if mode == "unknown" else mode)

    def test_mismatch_missing_disabled_duplicate_and_incomplete_nodes_are_unknown(self):
        for change in ({"spec_sha256": "b" * 64}, {"namespace": "other"}, {"name": "other"},
                       {"state": "disabled"}, {"state": "load_error"}, {"load_error": "failed"}):
            with self.subTest(change=change):
                sample = snapshot()
                sample["policies"][0].update(change)
                self.assertEqual(external_enforcement(sample, self.expected), "unknown")
        for field, value in (("agents", []), ("agents", [{"node": None}]), ("policies", [])):
            self.assertEqual(external_enforcement({**snapshot(), field: value}, self.expected), "unknown")
        sample = snapshot()
        sample["policies"] *= 2
        self.assertEqual(external_enforcement(sample, self.expected), "unknown")
        sample = snapshot()
        sample["agents"].append({"node": "second-node"})
        self.assertEqual(external_enforcement(sample, self.expected), "unknown")
        sample["policies"].append({**sample["policies"][0], "node": "second-node", "mode": "enforce"})
        self.assertEqual(external_enforcement(sample, self.expected), "mixed")

    def test_gateway_compares_startup_bundle_intent_with_public_state_and_shared_freshness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_path = ROOT / "examples/mvp/policy.json"
            policy_state = load_policy(policy_path)
            policy = policy_state.policy
            intent_path = root / "intent.json"
            intent_path.write_bytes((ROOT / "deploy/lab/intent.lab.json").read_bytes())
            bundle = {"policy_ref": {"policy_id": policy.policy_id, "policy_version": policy.policy_version,
                                     "sha256": "b" * 64},
                      "files": [{"role": "gateway_policy", "path": "runtime-policy.json",
                                 "sha256": policy_state.sha256, "applied": True},
                                {"role": "enforcement_intent", "path": "intent.json",
                                 "sha256": hashlib.sha256(intent_path.read_bytes()).hexdigest(), "applied": True}]}
            public = root / "shared/state.json"
            config = MVPConfig(policy_path=str(policy_path), data_dir=str(root / "data"),
                observation_intent_path=str(intent_path), policy_bundle=bundle, tetragon_state_path=str(public),
                observation_feeds=(("tetragon", str(root / "feed.jsonl")),))
            app = GatewayApp(config, policy_state, ("127.0.0.1", 1))
            self.assertEqual(app.policy_status()["external_enforcement"], "not_connected")
            sample = snapshot()
            sample["policies"] = []
            for manifest in render_monitor_manifests(load_intent(intent_path)).values():
                digest = hashlib.sha256(json.dumps(comparable_spec(manifest["spec"]), sort_keys=True,
                                                   separators=(",", ":")).encode()).hexdigest()
                sample["policies"].append({"node": "private-host", **{k: manifest["metadata"][k] for k in ("name", "namespace")},
                                          "id": "1", "state": "enabled", "mode": "monitor", "spec_sha256": digest})
            public.parent.mkdir()
            public.write_text(json.dumps(public_snapshot(sample)))
            self.assertEqual(app.policy_status()["external_enforcement"], "monitor")
            with patch.object(app._status_engine_client, "status", return_value={"healthy": False}), \
                    patch.object(GatewayApp, "address", new_callable=PropertyMock, return_value=("127.0.0.1", 1)):
                status = app.status()
            self.assertEqual(status["policy"]["external_enforcement"], "monitor")
            self.assertEqual(status["policy_state"]["external_enforcement"], "monitor")
            unbound_bundles = [None]
            for field in ("policy_id", "policy_version"):
                unbound_bundles.append({**bundle, "policy_ref": {**bundle["policy_ref"], field: "other"}})
            unbound_bundles.extend([
                {**bundle, "files": [{**bundle["files"][0], "sha256": "0" * 64}, bundle["files"][1]]},
                {**bundle, "files": [bundle["files"][1]]},
                {**bundle, "files": [bundle["files"][0], {**bundle["files"][1], "applied": False}]},
            ])
            for unbound in unbound_bundles:
                unbound_app = GatewayApp(MVPConfig(policy_path=str(policy_path), data_dir=str(root / "unbound"),
                    observation_intent_path=str(intent_path), policy_bundle=unbound, tetragon_state_path=str(public),
                    observation_feeds=(("tetragon", str(root / "feed.jsonl")),)), policy_state, ("127.0.0.1", 1))
                self.assertEqual(unbound_app.policy_status()["external_enforcement"], "unknown")
            # A mutable file on disk cannot change the startup expectations.
            intent_path.write_text("{}")
            self.assertEqual(app.policy_status()["external_enforcement"], "monitor")
            sample["generated_at"] = (datetime.now(timezone.utc) - timedelta(seconds=21)).isoformat()
            public.write_text(json.dumps(public_snapshot(sample)))
            self.assertEqual(app.policy_status()["external_enforcement"], "unknown")
            public.write_text("{")
            self.assertEqual(app.policy_status()["external_enforcement"], "error")
            with patch.object(Path, "read_text", side_effect=PermissionError):
                self.assertEqual(app.policy_status()["external_enforcement"], "error")


class LabPublicationTests(unittest.TestCase):
    def test_cli_rejects_unpublishable_options_before_constructing_checker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for options in (("--public-report-dir", str(root / "missing")),
                            ("--public-report-dir", str(root), "--sigkill-probe-check")):
                with self.subTest(options=options), patch("tools.k8s_lab_check.LabChecker") as checker, \
                        redirect_stderr(io.StringIO()):
                    self.assertEqual(lab_main(["--state-dir", str(root / "private"), *options]), 2)
                    checker.assert_not_called()

    def test_publication_is_0640_atomic_and_keeps_report_target_and_original_times(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o2750)
            self.assertEqual(LabReportSource(root).read()["state"], "missing")
            report = {"schema": "serving-runtime-k8s-lab-report/1.0", "run_id": "old-run",
                "started_at": "2020-01-01T00:00:00Z", "finished_at": "2020-01-01T00:01:00Z",
                "context": "lab-context", "namespace": "serving-lab", "overall": "partial",
                "cases": [{"id": f"L-{i:02d}", "title": "case", "status": "not_run", "summary": "not run",
                           "evidence": [], "commands": [{"stdout": "private-raw-output"}]} for i in range(1, 7)]}
            original_replace = Path.replace
            replacements = []

            def replace(path, target):
                self.assertEqual(LabReportSource(root).read()["state"], "missing")
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
                replacements.append(target)
                return original_replace(path, target)

            with patch.object(Path, "replace", replace):
                path = publish_report(report, root)
            self.assertEqual(replacements, [path])
            result = LabReportSource(root).read()
            self.assertEqual(result["state"], "ok")
            for key in ("run_id", "started_at", "finished_at", "context", "namespace"):
                self.assertEqual(result["report"][key], report[key])
            self.assertNotIn("private-raw-output", path.read_text())
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o2750)
            self.assertEqual(path.stat().st_gid, root.stat().st_gid)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            path.unlink()
            with patch("tools.k8s_lab_check.LabChecker") as checker, redirect_stdout(io.StringIO()):
                checker.return_value.run.return_value = (report, (root / "private.json", root / "private.md"))
                self.assertEqual(lab_main(["--state-dir", str(root / "private"), "--public-report-dir", str(root)]), 1)
                checker.return_value.run.assert_called_once()
            self.assertEqual(LabReportSource(root).read()["report"]["run_id"], "old-run")

    def test_aws_gateway_connects_public_sources_on_readonly_mount(self):
        text = (ROOT / "deploy/gpu-lab/gateway.yaml").read_text()
        self.assertIn("- --tetragon-state\n            - /srv/serving/observation/tetragon-status/tetragon-state.json", text)
        self.assertIn("- --lab-report\n            - /srv/serving/observation/lab-reports", text)
        self.assertIn("mountPath: /srv/serving/observation, readOnly: true", text)
        self.assertNotIn("/var/lib/serving-runtime", text)
