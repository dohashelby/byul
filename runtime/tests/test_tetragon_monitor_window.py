"""Complete monitor windows, human reviews, fail-closed boundaries and agent handoff."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.tetragon_monitor_window import MonitorWindowGenerator, atomic_json, digest, main, read_bytes
from serving_runtime.operations.agent import Agent, Config
from serving_runtime.operations.safety import evaluate
from serving_runtime.enforcement.intent import parse_intent
from tests.test_operations_agent import intent_document


class MonitorWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.time = datetime(2026, 11, 6, tzinfo=timezone.utc)
        self.intent = parse_intent(intent_document())
        self.name, self.node = "runtime-tg-secret-01", "node-01"
        self.status, self.events, self.output, self.reviews = [self.root / name for name in
            ("state.json", "observations.jsonl", "windows.json", "reviews.json")]
        self.snapshot = {"schema_version": "runtime-tetragon-state/1.0", "context": "test", "state": "ok", "fresh": True,
            "generated_at": self.time.isoformat(),
            "policies": [{"name": self.name, "namespace": self.intent.namespace, "node": self.node,
                "id": "7", "spec_sha256": "a" * 64, "method": "override_lsm", "mode": "monitor", "state": "enabled"}],
            "capabilities": [{"node": self.node, "kernel": "test-kernel", "available": ["override_lsm"]}],
            "verifications": [{"data_origin": "real", "node": self.node, "method": "override_lsm",
                "policy_spec_sha256": "a" * 64, "kernel": "test-kernel", "verification": {"status": "passed"},
                "source_report_sha256": "c" * 64}],
            "monitoring": {"state": "healthy", "history": [], "open_gap": None,
                **dict.fromkeys(("policies_verified", "writer_verified", "identities_verified", "events_verified"), True)}}
        self.events.write_text("")
        self.generator = self.new_generator()

    def new_generator(self):
        return MonitorWindowGenerator(context="test", namespace=self.intent.namespace, policies=[self.name],
            status=self.status, observations=self.events, output=self.output, reviews=self.reviews)

    def collect(self, seconds=0):
        self.snapshot["generated_at"] = (self.time + timedelta(seconds=seconds)).isoformat()
        atomic_json(self.status, self.snapshot)
        return self.generator.collect(now=self.time + timedelta(seconds=seconds))

    def event(self, seconds=1, **extra):
        return {"schema": "runtime-observation/1.0", "source": "tetragon", "data_origin": "real",
            "policy_name": self.name, "observed_at": (self.time + timedelta(seconds=seconds)).isoformat(),
            "pod": {"namespace": self.intent.namespace}, "kind": "file_access", "path": "/run/test-token",
            "identity": {"state": "confirmed", "context": "test", "node": self.node,
                         "container_name": "engine", "exec_id": "test-actor"}, **extra}

    def append(self, event):
        with self.events.open("a") as out:
            out.write(json.dumps(event) + "\n")

    def ready(self, value, seconds=10):
        return evaluate(self.snapshot, self.name, self.intent, value, 10,
                        now=self.time + timedelta(seconds=seconds))

    def test_continuous_empty_window_and_safety_duration_and_until(self):
        start = self.collect()["windows"][0]["since"]
        self.assertFalse(self.ready(self.collect(5), 5)["checks"]["monitor_period"])
        result = self.collect(10)
        window = result["windows"][0]
        self.assertEqual(window["since"], start)
        self.assertTrue(window["continuous"])
        self.assertEqual(window["events_sha256"], digest([]))
        self.assertTrue(all(self.ready(result)["checks"].values()))
        broken = deepcopy(result)
        broken["windows"][0]["continuous"] = False
        self.assertFalse(self.ready(broken)["checks"]["monitor_period"])
        # Wall time does not refresh the status file's confirmation timestamp.
        later = self.generator.collect(now=self.time + timedelta(seconds=15))
        self.assertEqual(later["windows"][0]["until"], window["until"])

    def test_gap_is_not_eligible_before_recovery_and_restarts(self):
        self.collect()
        self.snapshot["monitoring"].update(state="recovering", open_gap={"opened_at": self.time.isoformat()})
        self.assertEqual(self.collect(5)["windows"], [])
        self.snapshot["monitoring"].update(state="healthy", open_gap=None)
        result = self.collect(10)
        self.assertEqual(result["windows"][0]["since"], result["windows"][0]["until"])
        self.assertFalse(self.ready(result)["checks"]["monitor_period"])

    def test_gap_closed_between_samples_restarts(self):
        self.collect()
        self.snapshot["monitoring"]["history"] = [{"opened_at": "gap", "closed_at": "recovered"}]
        result = self.collect(10)
        self.assertEqual(result["windows"][0]["since"], result["windows"][0]["until"])

    def test_spec_id_method_kernel_and_agent_epoch_restart(self):
        for target, field, value in [("policies", "spec_sha256", "b" * 64), ("policies", "id", "8"),
                                      ("policies", "method", "sigkill_fd_install"), ("capabilities", "kernel", "new-kernel")]:
            with self.subTest(field=field):
                self.generator = self.new_generator()
                self.collect()
                old = self.snapshot[target][0][field]
                self.snapshot[target][0][field] = value
                result = self.collect(10)
                self.assertEqual(result["windows"][0]["since"], result["windows"][0]["until"])
                self.snapshot[target][0][field] = old
        self.generator = self.new_generator()
        self.collect()
        self.snapshot["agents"] = [{"node": self.node, "pod_uid": "new-agent", "restart_count": 1}]
        result = self.collect(10)
        self.assertEqual(result["windows"][0]["since"], result["windows"][0]["until"])

    def test_enforce_and_recorded_transition_restart(self):
        self.collect()
        self.snapshot["policies"][0]["mode"] = "enforce"
        self.assertEqual(self.collect(5)["windows"], [])
        self.snapshot["policies"][0]["mode"] = "monitor"
        result = self.collect(10)
        self.assertEqual(result["windows"][0]["since"], result["windows"][0]["until"])
        self.snapshot["transitions"] = [{"policy": self.name, "namespace": self.intent.namespace,
                                         "requested_mode": "enforce", "at": self.time.isoformat()}]
        result = self.collect(15)
        self.assertEqual(result["windows"][0]["since"], result["windows"][0]["until"])

    def test_real_confirmed_policy_node_namespace_scope_and_sentinel_excluded(self):
        self.collect()
        accepted = self.event()
        self.append(accepted)
        for event in [self.event(data_origin="synthetic"), self.event(policy_name="other"),
                      self.event(observation_purpose="health_check"), self.event(seconds=-1), self.event(seconds=11),
                      self.event(pod={"namespace": "other"})]:
            self.append(event)
        for changes in [{"state": "unavailable"}, {"node": "other"}, {"context": "other"}, {"container_name": "sentinel"}]:
            event = self.event()
            event["identity"].update(changes)
            self.append(event)
        result = self.collect(10)
        self.assertEqual(result["windows"][0]["event_count"], 1)
        self.assertEqual(result["windows"][0]["events_sha256"], digest([accepted]))
        self.assertFalse(self.ready(result)["checks"]["monitor_period"])

    def test_human_partial_full_false_positive_and_invalid_reviews(self):
        self.collect()
        self.append(self.event(1))
        self.append(self.event(2))
        value = self.collect(10)
        entries = json.loads(self.generator.catalog.read_text())["windows"][0]["events"]
        rows = [{"event_sha256": e["event_sha256"], "verdict": "not_false_positive"} for e in entries]
        self.assertEqual(value["windows"][0]["reviewed_event_count"], 0)
        atomic_json(self.reviews, {"reviewer_token_id": "reviewer-01", "events": rows[:1]})
        value = self.collect(10)
        self.assertEqual(value["windows"][0]["reviewed_event_count"], 1)
        self.assertFalse(self.ready(value)["checks"]["monitor_period"])
        atomic_json(self.reviews, {"reviewer_token_id": "reviewer-01", "events": rows})
        value = self.collect(10)
        self.assertTrue(self.ready(value)["checks"]["monitor_period"])
        self.assertEqual(self.ready(value)["monitor_windows"][0]["reviewed_event_count"], 2)
        rows[0]["verdict"] = "false_positive"
        atomic_json(self.reviews, {"reviewer_token_id": "reviewer-01", "events": rows})
        value = self.collect(10)
        self.assertEqual(value["windows"][0]["false_positive_count"], 1)
        self.assertFalse(self.ready(value)["checks"]["monitor_period"])
        for body in [{"reviewer_token_id": "bad/token", "events": rows},
                     {"reviewer_token_id": "reviewer-01", "events": rows + rows}]:
            atomic_json(self.reviews, body)
            self.assertEqual(self.collect(10)["windows"][0]["reviewed_event_count"], 0)

    def test_review_digest_cannot_be_reused_for_new_window(self):
        self.collect()
        self.append(self.event(10))
        self.collect(10)
        entry = json.loads(self.generator.catalog.read_text())["windows"][0]["events"][0]
        atomic_json(self.reviews, {"reviewer_token_id": "reviewer-01", "events": [
            {"event_sha256": entry["event_sha256"], "verdict": "not_false_positive"}]})
        self.snapshot["policies"][0]["id"] = "new-id"
        value = self.collect(10)
        self.assertEqual(value["windows"][0]["event_count"], 1)
        self.assertEqual(value["windows"][0]["reviewed_event_count"], 0)

    def test_stale_missing_unreadable_malformed_and_future_status_fail_closed(self):
        self.collect()
        self.assertEqual(self.generator.collect(now=self.time + timedelta(seconds=21))["windows"], [])
        self.assertEqual(self.collect(22)["windows"][0]["since"], (self.time + timedelta(seconds=22)).isoformat())
        for content in [b"not-json", b"[]", b'{"bad":true}', b'{"identity": []}\n']:
            self.collect()
            self.events.write_bytes(content)
            self.assertEqual(self.collect(5)["windows"], [])
            self.events.write_text("")
        self.events.unlink()
        self.assertEqual(self.collect(5)["windows"], [])
        self.events.write_text("")
        with patch("tools.tetragon_monitor_window.read_bytes", side_effect=PermissionError):
            self.assertEqual(self.collect(5)["windows"], [])
        self.collect(10)
        self.assertEqual(self.generator.collect(now=self.time)["windows"], [])

    def test_source_rewrite_truncate_and_restart_do_not_resume_history(self):
        for operation in ("truncate", "replace", "overwrite"):
            with self.subTest(operation=operation):
                self.events.write_text("")
                self.generator = self.new_generator()
                self.collect()
                self.append(self.event())
                self.collect(5)
                if operation == "truncate":
                    self.events.write_text("")
                elif operation == "replace":
                    temporary = self.root / "new.jsonl"
                    temporary.write_bytes(self.events.read_bytes())
                    temporary.replace(self.events)
                else:
                    self.events.write_text(self.events.read_text().replace("test-actor", "new--actor"))
                result = self.collect(10)
                self.assertEqual(result["windows"][0]["since"], result["windows"][0]["until"])
        self.generator = self.new_generator()
        result = self.collect(15)
        self.assertEqual(result["windows"][0]["since"], result["windows"][0]["until"])

    def test_multiple_nodes_and_safety_id_namespace_mode_binding(self):
        self.snapshot["policies"].append({**self.snapshot["policies"][0], "node": "node-02"})
        self.snapshot["capabilities"].append({**self.snapshot["capabilities"][0], "node": "node-02"})
        self.collect()
        self.append(self.event())
        value = self.collect(10)
        self.assertEqual([r["event_count"] for r in value["windows"]], [1, 0])
        for field in ("policy_id", "namespace", "mode"):
            bad = deepcopy(value)
            bad["windows"][1][field] = "wrong"
            self.assertFalse(self.ready(bad)["checks"]["monitor_period"])

    def test_catalog_write_failure_and_ambiguous_status_fail_closed(self):
        self.collect()
        real_write = atomic_json

        def write(path, value):
            if path == self.generator.catalog:
                raise OSError("injected catalog failure")
            return real_write(path, value)

        with patch("tools.tetragon_monitor_window.atomic_json", side_effect=write):
            self.assertEqual(self.collect(10)["windows"], [])
        self.assertEqual(json.loads(self.output.read_text())["windows"], [])
        self.assertFalse(self.ready(json.loads(self.output.read_text()))["checks"]["monitor_period"])
        self.snapshot["policies"].append({**self.snapshot["policies"][0], "mode": "enforce"})
        self.assertEqual(self.collect(15)["windows"], [])

    def test_atomic_write_permissions_previous_content_and_agent_read(self):
        self.collect()
        self.collect(10)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.generator.catalog.stat().st_mode), 0o600)
        before = self.output.read_bytes()
        with patch("tools.tetragon_monitor_window.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                atomic_json(self.output, {"windows": []})
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".windows.json.*")), [])
        agent = Agent(Mock(), Config("server-01", "/unused", "test", self.intent.namespace))
        agent.config = replace(agent.config, review_file=str(self.output))
        review = agent.review()
        self.assertTrue(self.ready(review)["checks"]["monitor_period"])
        with patch.object(agent, "snapshot", return_value=self.snapshot):
            row = agent.readiness({"bundle_id": "test", "approval": "verified"}, self.intent)[0]
        self.assertEqual(row["monitor_windows"][0]["event_count"], 0)
        self.assertEqual(row["monitor_windows"][0]["since"], self.time.isoformat())
        self.output.chmod(0o644)
        self.assertEqual(agent.review(), {})
        self.output.chmod(0o600)
        target = self.root / "old.json"
        self.output.rename(target)
        self.output.symlink_to(target)
        self.assertEqual(agent.review(), {})
        atomic_json(self.output, {"windows": []})
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(self.output.is_symlink())

    def test_explicit_owner_trust_preserves_default_and_write_restrictions(self):
        real_fstat = os.fstat

        def gateway_owner(fd):
            info = list(real_fstat(fd))
            info[4] = 10000
            return os.stat_result(info)

        with patch("tools.tetragon_monitor_window.os.getuid", return_value=10001), \
             patch("tools.tetragon_monitor_window.os.fstat", side_effect=gateway_owner):
            for owners in ((), (10002,)):
                with self.assertRaises(ValueError):
                    read_bytes(self.events, 1024, trusted_owner_uids=owners)
            self.assertEqual(read_bytes(self.events, 1024, trusted_owner_uids=(10002, 10000))[0], b"")
            self.assertEqual(self.collect()["state"], "unavailable")
            self.generator.trusted_owner_uids = frozenset({10000})
            self.assertEqual(self.collect()["state"], "ok")
            for mode in (0o664, 0o646):
                self.events.chmod(mode)
                self.assertEqual(self.collect()["state"], "unavailable")
            self.events.chmod(0o644)
            target = self.root/"renamed.jsonl"
            self.events.rename(target)
            self.events.symlink_to(target)
            self.assertEqual(self.collect()["state"], "unavailable")

    def test_cli_passes_repeated_explicit_trusted_owner_uids(self):
        generator = Mock(output=self.output)
        generator.collect.return_value = {"state": "ok"}
        args = ["monitor", "--context", "test", "--namespace", self.intent.namespace,
                "--policy", self.name, "--status", str(self.status), "--observations", str(self.events),
                "--output", str(self.output), "--trusted-owner-uid", "10000", "--trusted-owner-uid", "10002"]
        with patch("sys.argv", args), \
             patch("tools.tetragon_monitor_window.MonitorWindowGenerator", return_value=generator) as constructor:
            self.assertEqual(main(), 0)
        self.assertEqual(constructor.call_args.kwargs["trusted_owner_uids"], [10000, 10002])


if __name__ == "__main__":
    unittest.main()
