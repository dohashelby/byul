import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from serving_runtime.enforcement.attribution import SCHEMA, KERNEL_DATA, bind_event, read_identities
from serving_runtime.enforcement.events import parse_tetragon
from serving_runtime.enforcement.intent import parse_intent, IntentError
from serving_runtime.enforcement.tetragon import render_manifests
from serving_runtime.mvp.observation_feed import ObservationFeed
from tools.tetragon_identities import IdentityCollector

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 6, tzinfo=timezone.utc)
CID = "a" * 64


def sample():
    row = {"node": "node", "namespace": "serving", "pod_name": "engine", "pod_uid": "uid",
           "container_name": "engine", "container_id": CID, "binary": "/usr/bin/python3",
           "node_pid": 77, "ns_pid": 12, "pidns": 123, "start_ticks": 1000, "clock_ticks": 100,
           "stable": True, "labels": {"app": "engine"}}
    index = {"schema_version": SCHEMA, "context": "kind-test", "generated_at": AT.isoformat(),
             "state": "ok", "records": [row], "policies": [{"name": "runtime-tg-secret-01", "namespace": "serving",
                 "selector": {"app": "engine"}, "kernel_identity": True}]}
    raw = {"time": AT.isoformat(), "node_name": "node", "process_kprobe": {
        "process": {"pid": 42, "flags": "unknown"}, "function_name": "security_file_permission",
        "policy_name": "runtime-tg-secret-01", "action": "KPROBE_ACTION_POST",
        "args": [{"file_arg": {"path": "/run/probe/file"}}, {"int_arg": 4}],
        "data": [{"label": "runtime_pid", "int_arg": 42}, {"label": "runtime_start_ns", "size_arg": "10005000000"},
                 {"label": "runtime_pidns", "uint_arg": 123}]}}
    return raw, index


class AttributionTests(unittest.TestCase):
    def test_child_process_binding_survives_missing_native_cache_without_inventing_exec_id(self):
        raw, index = sample()
        observation = parse_tetragon(raw, data_origin="real", identity_index=index)
        self.assertEqual(observation["binary"], "/usr/bin/python3")
        self.assertEqual(observation["pod"], {"namespace": "serving", "pod": "engine"})
        self.assertEqual(observation["identity"]["source"], "kernel_task_procfs")
        self.assertEqual(observation["identity"]["ns_pid"], 12)
        self.assertEqual(observation["identity"]["pid"], 42)
        self.assertIsNone(observation["identity"]["exec_id"])
        self.assertNotIn("pod", raw["process_kprobe"]["process"])

    def test_pid_reuse_other_nodes_and_policy_mismatches_never_bind(self):
        for change in ({"start_ticks": 1001}, {"node": "other"}, {"pidns": 124},
                       {"stable": False}, {"namespace": "other"}, {"labels": {}}):
            raw, index = sample()
            index["records"][0].update(change)
            with self.subTest(change=change):
                self.assertNotEqual(bind_event(raw, index)[1]["state"], "confirmed")
        raw, index = sample()
        index["policies"][0]["kernel_identity"] = False
        self.assertEqual(bind_event(raw, index)[1]["state"], "unavailable")

    def test_ambiguous_tick_or_shared_namespace_is_not_assigned(self):
        raw, index = sample()
        index["records"].append({**index["records"][0], "node_pid": 78, "container_id": "b"*64})
        self.assertEqual(bind_event(raw, index)[1]["state"], "ambiguous")

    def test_conflicting_native_workload_cannot_authorize_engine_classification(self):
        raw, index = sample()
        raw["process_kprobe"]["process"]["pod"] = {"namespace": "other", "name": "engine", "uid": "other-uid"}
        result = parse_tetragon(raw, data_origin="real", identity_index=index)
        self.assertEqual(result["identity"]["state"], "conflict")
        self.assertIsNone(result["pod"])

    def test_native_exec_container_id_is_unique_and_node_bound(self):
        _, index = sample()
        raw = {"time": AT.isoformat(), "node_name": "node", "process_exec": {"process": {
            "pid": 42, "exec_id": "native-exec", "binary": "/bin/worker", "docker": CID[:16]}}}
        enriched, identity = bind_event(raw, index)
        self.assertEqual(identity["source"], "native_exec_cri_container")
        self.assertEqual(enriched["process_exec"]["process"]["binary"], "/bin/worker")
        self.assertEqual(enriched["process_exec"]["process"]["pid"], 42)
        index["records"].append({**index["records"][0], "container_id": CID[:16]+"b"*48})
        self.assertEqual(bind_event(raw, index)[1]["state"], "ambiguous")

    def test_stale_and_cross_cluster_sources_and_old_events_are_not_reused(self):
        raw, index = sample()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"identities.json"
            path.write_text(json.dumps(index))
            self.assertEqual(read_identities(path, context="kind-test", now=AT)["state"], "ok")
            self.assertEqual(read_identities(path, context="other", now=AT)["state"], "error")
            self.assertEqual(read_identities(path, context="kind-test", now=AT+timedelta(seconds=21))["state"], "error")
        raw["time"] = (AT-timedelta(seconds=31)).isoformat()
        self.assertEqual(bind_event(raw, index)[1]["reason"], "event_outside_identity_window")

    def test_opt_in_renderer_preserves_default_bytes_and_instruments_all_file_rules(self):
        self.assertEqual(KERNEL_DATA[1]['resolve'],'group_leader.start_boottime')
        document = json.loads((ROOT/"deploy/lab/intent.lab-v1.1.json").read_text())
        before = render_manifests(parse_intent(document))
        document["tetragon_kernel_identity"] = False
        self.assertEqual(before, render_manifests(parse_intent(document)))
        document["tetragon_kernel_identity"] = True
        for policy in render_manifests(parse_intent(document)).values():
            self.assertEqual(policy["spec"]["kprobes"][0]["data"], KERNEL_DATA)
        document["tetragon_kernel_identity"] = "true"
        with self.assertRaises(IntentError):
            parse_intent(document)

    def test_live_feed_projects_attribution_without_saving_command_arguments(self):
        raw, index = sample()
        at = datetime.now(timezone.utc).isoformat()
        raw["time"] = index["generated_at"] = at
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"identities.json").write_text(json.dumps(index))
            (root/"feed.jsonl").write_text(json.dumps(raw)+"\n")
            feed = ObservationFeed(kind="tetragon", path=root/"feed.jsonl", data_dir=root, data_origin="real",
                environment=None, run_id=None, identity_path=root/"identities.json", kube_context="kind-test")
            feed.collect()
            stored = json.loads((root/"observations.jsonl").read_text())
            self.assertEqual(stored["identity"]["state"], "confirmed")
            self.assertNotIn("arguments", json.dumps(stored))

    def test_verified_health_pulses_are_bounded_and_do_not_become_security_events(self):
        raw,index=sample()
        at=datetime.now(timezone.utc).isoformat()
        raw["time"]=index["generated_at"]=at
        event=raw["process_kprobe"]
        event["policy_name"]="runtime-tg-health-01-sentinel"
        event["args"][0]["file_arg"]["path"]="/run/byul-runtime-sentinel/read.txt"
        index["policies"][0]["name"]=event["policy_name"]
        index["records"][0]["container_name"]="sentinel"
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/"ids").write_text(json.dumps(index));(root/"feed").write_text(json.dumps(raw)+"\n")
            feed=ObservationFeed(kind="tetragon",path=root/"feed",data_dir=root,data_origin="real",environment=None,
                run_id=None,identity_path=root/"ids",kube_context="kind-test")
            feed.collect()
            self.assertFalse((root/"observations.jsonl").exists())
            receipt=json.loads((root/"tetragon-health-receipts.json").read_text())
            self.assertEqual(len(receipt["nodes"]),1)
            self.assertEqual(receipt["nodes"]["node"]["identity"]["state"],"confirmed")

    def test_missing_exec_metadata_is_preserved_as_unknown_when_correlation_is_enabled(self):
        _,index=sample()
        raw={"time":AT.isoformat(),"node_name":"node","process_exec":{"process":{"pid":42}}}
        normalized=parse_tetragon(raw,data_origin="real",identity_index=index)
        self.assertIsNone(normalized["binary"])
        self.assertIsNone(normalized["allowed_binary"])
        self.assertEqual(normalized["identity"]["state"],"unavailable")


class IdentityCollectorTests(unittest.TestCase):
    def test_procfs_is_read_inside_matching_node_and_data_is_scoped(self):
        calls = []
        def runner(command):
            calls.append(command)
            if "sh" in command:
                value = "\0".join([CID, "77", "pid:[123]", "1000", "12", "/usr/bin/python3", "100", ""])
            elif "tracingpoliciesnamespaced" in command:
                value = {"items": [{"metadata": {"name": "runtime-tg-secret-01"}, "spec": {
                    "kprobes": [{"data": KERNEL_DATA}], "podSelector": {"matchLabels": {"app": "engine"}}}}]}
            elif "app.kubernetes.io/name=tetragon" in command:
                value = {"items": [{"metadata": {"name": "agent", "uid": "agent-uid"}, "spec": {"nodeName": "node"},
                    "status": {"containerStatuses": [{"name": "tetragon", "ready": True, "restartCount": 0}]}}]}
            else:
                value = {"items": [{"metadata": {"name": "engine", "uid": "uid", "labels": {"app": "engine"}},
                    "spec": {"nodeName": "node"}, "status": {"phase": "Running", "containerStatuses": [{
                        "name": "engine", "containerID": "containerd://"+CID, "state": {"running": {"startedAt": AT.isoformat()}}}]}}]}
            return SimpleNamespace(returncode=0, stdout=value if isinstance(value, str) else json.dumps(value))
        with tempfile.TemporaryDirectory() as directory:
            result = IdentityCollector("kind-test", "serving", "app=engine", Path(directory)/"state.json", runner=runner).collect()
            self.assertEqual(result["state"], "ok")
            self.assertEqual(result["records"][0]["ns_pid"], 12)
            self.assertTrue(result["policies"][0]["kernel_identity"])
        self.assertTrue(all(c[:3] == ["kubectl", "--context", "kind-test"] for c in calls))
        self.assertFalse(any(any(v in c for v in ("apply", "delete", "set-mode")) for c in calls))


if __name__ == "__main__":
    unittest.main()
