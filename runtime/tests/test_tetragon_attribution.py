import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from serving_runtime.enforcement.attribution import SCHEMA, KERNEL_DATA, bind_event, read_identities
from serving_runtime.enforcement.events import parse_tetragon
from serving_runtime.enforcement.intent import parse_intent, IntentError
from serving_runtime.enforcement.tetragon import render_manifests
from serving_runtime.mvp.observation_feed import ObservationFeed
from tools.tetragon_identities import IdentityCollector, PROC_SCAN

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
    def test_lsm_without_data_requires_native_actor_and_unique_cri_container(self):
        raw, index = sample()
        event = raw.pop('process_kprobe')
        raw['process_lsm'] = event
        event.update(function_name='file_open', args=[{'file_arg': {'path': '/run/probe/file'}}])
        event.pop('data')
        self.assertEqual(parse_tetragon(raw, data_origin='synthetic', identity_index=index)['identity']['state'], 'unavailable')
        event['process'].update(exec_id='native-exec', binary='/usr/bin/python3', docker=CID[:16])
        result = parse_tetragon(raw, data_origin='synthetic', identity_index=index)
        self.assertEqual(result['identity']['state'], 'confirmed')
        self.assertEqual(result['identity']['source'], 'native_exec_cri_container')
        self.assertEqual(result['pod'], {'namespace': 'serving', 'pod': 'engine'})
        self.assertNotIn('data', raw['process_lsm'])
        event['process']['pod'] = {'namespace': 'other', 'uid': 'other'}
        self.assertEqual(parse_tetragon(raw, data_origin='synthetic', identity_index=index)['identity']['state'], 'conflict')

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

    def test_kernel_binary_accepts_only_inode_attested_aliases(self):
        raw, index = sample()
        row = index["records"][0]
        row.update(binary="/usr/local/bin/python3.12", binary_file_id="7:123",
                   binary_aliases=[{"path": "/usr/local/bin/python", "file_id": "7:123"}])
        process = raw["process_kprobe"]["process"]
        for binary in (row["binary"], "/usr/local/bin/python"):
            process["binary"] = binary
            enriched, identity = bind_event(raw, index)
            self.assertEqual(identity["state"], "confirmed")
            self.assertEqual(enriched["process_kprobe"]["process"]["binary"], row["binary"])
        process["binary"] = "/usr/local/bin/unrecorded"
        self.assertEqual(bind_event(raw, index)[1]["reason"], "native_binary_conflict")
        process["binary"] = "/usr/local/bin/python"
        for aliases in ([{"path": process["binary"], "file_id": "7:124"}],
                        [process["binary"]], [{"path": process["binary"]}]):
            row["binary_aliases"] = aliases
            self.assertEqual(bind_event(raw, index)[1]["reason"], "native_binary_conflict")
        row["binary_aliases"] = [{"path": process["binary"], "file_id": "7:123"}]
        del row["binary_file_id"]
        self.assertEqual(bind_event(raw, index)[1]["reason"], "native_binary_conflict")

    def test_identity_reader_rejects_aliases_without_matching_file_proof(self):
        _, index = sample()
        row = index["records"][0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"identities.json"
            for change, expected in (({}, "ok"),
                    ({"binary_file_id": "7:123", "binary_aliases": [{"path": "/bin/python", "file_id": "7:123"}]}, "ok"),
                    ({"binary_aliases": [{"path": "/bin/python", "file_id": "7:124"}]}, "error"),
                    ({"binary_aliases": [{"path": row["binary"]}]}, "error"),
                    ({"binary_aliases": ["/bin/python"]}, "error")):
                row.update(change)
                path.write_text(json.dumps(index))
                self.assertEqual(read_identities(path, context="kind-test", now=AT)["state"], expected)

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
            receipt_path = root/"tetragon-health-receipts.json"
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o640)
            receipt_path.chmod(0o600)  # Upgrade a receipt written by the previous Gateway.
            feed._save_health_receipt(receipt["nodes"]["node"])
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(json.loads(receipt_path.read_text()), receipt)

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
                value = "\0".join([CID, "77", "pid:[123]", "1000", "12", "/usr/bin/python3", "100",
                                   "7:123", "/usr/bin/python", ""])
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
            self.assertEqual(result["records"][0]["binary_aliases"],
                             [{"path": "/usr/bin/python", "file_id": "7:123"}])
        self.assertTrue(all(c[:3] == ["kubectl", "--context", "kind-test"] for c in calls))
        self.assertFalse(any(any(v in c for v in ("apply", "delete", "set-mode")) for c in calls))


class ProcScanAliasTests(unittest.TestCase):
    """Execute the production shell against scoped fake procfs, without Kubernetes."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root/"proc"/"77"
        self.proc.mkdir(parents=True)
        self.container = self.root/"container"
        self.bin = self.container/"usr/local/bin"
        self.bin.mkdir(parents=True)
        self.exe = self.bin/"python3.12"
        self.exe.write_bytes(b"executable")
        (self.bin/"python").symlink_to("python3.12")
        (self.proc/"root").symlink_to(self.container)
        (self.proc/"exe").symlink_to(self.exe)
        (self.proc/"ns").mkdir()
        (self.proc/"ns/pid").symlink_to("pid:[123]")
        (self.proc/"cgroup").write_text("0::/cri-containerd-"+CID+".scope\n")
        (self.proc/"stat").write_text("77 (python) "+"0 "*19+"1000 0\n")
        (self.proc/"status").write_text("NSpid:\t77\t12\n")
        (self.proc/"environ").write_bytes(b"SECRET=do-not-store\0PATH=/usr/local/bin:/usr/bin\0")

    def scan(self, argv0):
        (self.proc/"cmdline").write_bytes(argv0.encode()+b"\0--token=do-not-store\0")
        script = PROC_SCAN.replace("root=/procRoot", "root="+shlex.quote(str(self.proc.parent)))
        result = subprocess.run(["sh", "-c", script, "test-proc-scan", CID],
                                capture_output=True, text=True, check=True)
        self.assertNotIn("do-not-store", result.stdout)
        fields = result.stdout.split("\0")
        self.assertEqual(len(fields), 10, result.stderr)
        self.assertEqual(fields[7], f"{self.exe.stat().st_dev}:{self.exe.stat().st_ino}")
        return fields[8]

    def test_absolute_and_path_resolved_symlinks_have_same_inode(self):
        for argv0 in ("/usr/local/bin/python", "python"):
            self.assertEqual(self.scan(argv0), "/usr/local/bin/python")

    def test_forged_argv0_different_inode_or_missing_path_has_no_alias(self):
        (self.bin/"impostor").write_bytes(self.exe.read_bytes())
        for argv0 in ("/usr/local/bin/impostor", "impostor", "/missing", "relative/python", ""):
            self.assertEqual(self.scan(argv0), "")

    def test_absolute_symlink_is_resolved_in_process_root(self):
        (self.bin/"python").unlink()
        (self.bin/"python").symlink_to("/usr/local/bin/python3.12")
        self.assertEqual(self.scan("python"), "/usr/local/bin/python")

    def test_hardlink_is_proven_but_symlink_loop_is_not(self):
        (self.bin/"hardlink").hardlink_to(self.exe)
        self.assertEqual(self.scan("hardlink"), "/usr/local/bin/hardlink")
        (self.bin/"loop").symlink_to("loop")
        self.assertEqual(self.scan("loop"), "")


if __name__ == "__main__":
    unittest.main()
