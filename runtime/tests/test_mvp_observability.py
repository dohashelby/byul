"""Environment/run scope, live feeds, lab reports, and Cilium status tests."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from serving_runtime.cli import main
from serving_runtime.enforcement.events import (
    ObservationQuery,
    parse_hubble_loss,
    parse_tetragon,
    transform_lines,
    transform_lines_with_loss,
)
from serving_runtime.mvp.cilium_status import (
    CiliumPolicyCollector,
    policy_command,
)
from serving_runtime.mvp.collectors import Collector
from serving_runtime.mvp.lab_report import LabReportSource
from serving_runtime.mvp.observation_feed import (
    HEARTBEAT_SCHEMA,
    ObservationFeed,
    ObservationFeedIdle,
)
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.request_records import RequestRecords
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.validator import Decision
from tools.k8s_observation_feed import (
    EXPECTED_CONTEXT,
    assert_safe_context,
    hubble_command,
    hubble_port_forward_command,
    tetragon_command,
    run as run_observation_feed,
    write_heartbeats,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/enforcement"


def lab_report(run_id: str = "k8s-lab-unit") -> dict[str, object]:
    return {
        "schema": "serving-runtime-k8s-lab-report/1.0",
        "run_id": run_id,
        "started_at": "2026-10-24T00:00:00+00:00",
        "finished_at": "2026-10-24T00:01:00+00:00",
        "context": "kind-runtime-lab",
        "namespace": "serving-lab",
        "cleanup_requested": False,
        "overall": "partial",
        "cases": [
            {
                "id": f"L-{index:02d}",
                "title": f"case {index}",
                "status": "passed" if index < 6 else "not_run",
                "summary": f"summary {index}",
                "evidence": [f"/evidence/L-{index:02d}.json"],
                "commands": [{"stdout": "must not be exposed"}],
                "limitation": None,
                "note": None,
            }
            for index in range(1, 7)
        ],
    }


class EnvironmentAndActionTests(unittest.TestCase):
    def test_hubble_lost_events_are_separate_from_flow_observations(self) -> None:
        lines = (FIXTURES / "hubble_lost_events.synthetic.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        accepted, rejected, losses = transform_lines_with_loss(
            lines, kind="hubble", data_origin="synthetic"
        )
        self.assertEqual((accepted, rejected), ([], []))
        self.assertEqual([item["num_events_lost"] for item in losses], [7, 3])
        self.assertEqual(losses[0]["source"], "HUBBLE_RING_BUFFER")
        self.assertEqual(
            parse_hubble_loss(json.loads(lines[0]))["schema"],
            "runtime-hubble-loss/1.0",
        )

    def test_environment_and_run_are_optional_and_preserved_when_supplied(self) -> None:
        line = (FIXTURES / "hubble_flows.synthetic.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[0]
        scoped, rejected = transform_lines(
            [line],
            kind="hubble",
            data_origin="real",
            environment="kind-lab-wsl",
            run_id="k8s-lab-unit",
        )
        legacy, _ = transform_lines([line], kind="hubble", data_origin="real")
        self.assertEqual(rejected, [])
        self.assertEqual(scoped[0]["environment"], "kind-lab-wsl")
        self.assertEqual(scoped[0]["run_id"], "k8s-lab-unit")
        self.assertNotIn("environment", legacy[0])
        self.assertNotIn("run_id", legacy[0])

    def test_tetragon_action_is_mapped_but_raw_value_is_retained(self) -> None:
        event = json.loads(
            (FIXTURES / "tetragon_events.synthetic.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[2]
        )
        event["process_kprobe"]["action"] = "KPROBE_ACTION_OVERRIDE"
        enforce = parse_tetragon(event, data_origin="real")
        self.assertEqual(enforce["action_mode"], "enforce")
        self.assertEqual(enforce["action_raw"], "KPROBE_ACTION_OVERRIDE")
        event["process_kprobe"]["action"] = "KPROBE_ACTION_POST"
        self.assertEqual(parse_tetragon(event, data_origin="real")["action_mode"], "monitor")
        event["process_kprobe"]["action"] = "KPROBE_ACTION_FUTURE"
        self.assertEqual(parse_tetragon(event, data_origin="real")["action_mode"], "unknown")

    def test_ingest_cli_adds_scope_without_making_it_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            status = main(
                [
                    "enforce",
                    "ingest",
                    "--kind",
                    "hubble",
                    "--input",
                    str(FIXTURES / "hubble_flows.synthetic.jsonl"),
                    "--origin",
                    "synthetic",
                    "--data-dir",
                    temporary,
                    "--environment",
                    "kind-lab-wsl",
                    "--run-id",
                    "manual-run",
                ]
            )
            self.assertEqual(status, 0)
            records, _ = JsonlStore(Path(temporary) / "observations.jsonl").read_all()
            self.assertEqual(records[0]["environment"], "kind-lab-wsl")
            self.assertEqual(records[0]["run_id"], "manual-run")

    def test_request_records_carry_gateway_environment_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy = load_policy(ROOT / "examples/mvp/policy.json")
            records = RequestRecords(
                temporary,
                "session",
                lambda: policy,
                "vllm",
                "docker-cpu",
                "s3b-cpu-run",
            )
            record = records.build(
                "final",
                "a" * 32,
                Decision("ALLOWED", 200, "ALLOWED", None, "ok", "test-model", 1),
                attempted=True,
                backend_receipt="not_available",
                response="completed",
                error_code=None,
            )
            self.assertEqual(record["environment"], "docker-cpu")
            self.assertEqual(record["run_id"], "s3b-cpu-run")

    def test_hubble_dashboard_view_reserves_space_and_prioritizes_drops(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = JsonlStore(Path(temporary) / "observations.jsonl")
            store.append(
                {
                    "source": "hubble",
                    "kind": "network_flow",
                    "observed_at": "2026-10-23T23:59:59+00:00",
                    "verdict": "dropped",
                    "data_origin": "real",
                }
            )
            for second in range(60):
                store.append(
                    {
                        "source": "hubble",
                        "kind": "network_flow",
                        "observed_at": f"2026-10-24T00:00:{second:02d}+00:00",
                        "verdict": "allowed",
                        "data_origin": "real",
                    }
                )
            payload = ObservationQuery(store.path).query({})
            self.assertNotIn(
                "dropped", {item["verdict"] for item in payload["observations"]}
            )
            self.assertEqual(
                payload["hubble_observations"][0]["verdict"], "dropped"
            )
            self.assertEqual(len(payload["hubble_observations"]), 50)


class ObservationFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "hubble.jsonl"
        self.data_dir = self.root / "data"
        self.lines = (FIXTURES / "hubble_flows.synthetic.jsonl").read_bytes().splitlines()

    def feed(
        self,
        *,
        heartbeat_path: Path | None = None,
        heartbeat_stale_after_s: float = 15,
    ) -> ObservationFeed:
        return ObservationFeed(
            kind="hubble",
            path=self.path,
            data_dir=self.data_dir,
            data_origin="real",
            environment="kind-lab-wsl",
            run_id="feed-run",
            heartbeat_path=heartbeat_path,
            heartbeat_stale_after_s=heartbeat_stale_after_s,
        )

    def write_heartbeat(
        self,
        path: Path,
        *,
        state: str = "running",
        started_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        path.write_text(
            json.dumps(
                {
                    "schema": HEARTBEAT_SCHEMA,
                    "kind": "hubble",
                    "feed_path": str(self.path),
                    "started_at": started_at or now,
                    "updated_at": updated_at or now,
                    "state": state,
                    "processes": [
                        {
                            "name": "hubble",
                            "running": state == "running",
                            "returncode": None if state == "running" else 0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_loss_lines_are_counted_not_stored_as_observations(self) -> None:
        heartbeat = self.root / "hubble.heartbeat.json"
        now = datetime.now(timezone.utc)
        loss = {
            "lost_events": {
                "source": "PERF_EVENT_RING_BUFFER",
                "num_events_lost": "5",
                "cpu": 1,
            },
            "time": now.isoformat(),
            "node_name": "runtime-node",
        }
        self.path.write_text(json.dumps(loss) + "\n", encoding="utf-8")
        self.write_heartbeat(
            heartbeat,
            started_at=(now - timedelta(seconds=1)).isoformat(),
            updated_at=now.isoformat(),
        )
        feed = self.feed(heartbeat_path=heartbeat)
        value, _ = feed.collect()
        records, corrupt = feed.store.read_all()
        self.assertEqual((records, corrupt), ([], []))
        self.assertEqual(value["accepted"], 0)
        self.assertEqual(value["lost_events_total"], 5)
        self.assertEqual(
            value["lost_events_by_source"], {"PERF_EVENT_RING_BUFFER": 5}
        )
        self.assertTrue(value["loss_detected"])

        collector = Collector(
            "observation_feed_hubble",
            feed.collect,
            interval_s=1,
            stale_after_s=15,
        )
        collector.collect()
        # No new line does not erase the fresh loss signal.
        self.assertEqual(collector.snapshot()["state"], "partial")

    def test_zero_means_processed_without_loss_not_confirmed_loss_free(self) -> None:
        heartbeat = self.root / "hubble.heartbeat.json"
        now = datetime.now(timezone.utc)
        flow = json.loads(self.lines[0])
        flow["flow"]["time"] = now.isoformat()
        self.path.write_text(json.dumps(flow) + "\n", encoding="utf-8")
        self.write_heartbeat(
            heartbeat,
            started_at=(now - timedelta(seconds=1)).isoformat(),
            updated_at=now.isoformat(),
        )
        value, _ = self.feed(heartbeat_path=heartbeat).collect()
        self.assertEqual(value["lost_events_total"], 0)
        self.assertEqual(value["lost_events_by_source"], {})
        self.assertFalse(value["loss_detected"])

    def records(self) -> list[dict[str, object]]:
        records, corrupt = JsonlStore(self.data_dir / "observations.jsonl").read_all()
        self.assertEqual(corrupt, [])
        return records

    def test_append_partial_line_idle_and_restart_offset(self) -> None:
        self.path.write_bytes(self.lines[0] + b"\n" + self.lines[1][:20])
        feed = self.feed()
        value, origin = feed.collect()
        self.assertEqual(origin, "real")
        self.assertEqual((value["processed"], value["accepted"]), (1, 1))
        self.assertEqual(self.records()[0]["environment"], "kind-lab-wsl")
        with self.assertRaises(ObservationFeedIdle):
            feed.collect()
        with self.path.open("ab") as output:
            output.write(self.lines[1][20:] + b"\n")
        value, _ = feed.collect()
        self.assertEqual(value["accepted"], 1)
        self.assertEqual(len(self.records()), 2)
        with self.assertRaises(ObservationFeedIdle):
            self.feed().collect()

    def test_rotation_truncation_corrupt_lines_and_event_key_dedupe(self) -> None:
        self.path.write_bytes(self.lines[0] + b"\n")
        feed = self.feed()
        feed.collect()
        rotated = self.root / "hubble.old.jsonl"
        os.replace(self.path, rotated)
        self.path.write_bytes(self.lines[0] + b"\n" + b"not-json\n")
        value, _ = feed.collect()
        self.assertEqual(value["mode"], "rotated")
        self.assertEqual(value["duplicates"], 1)
        self.assertEqual(value["rejected"], 1)
        self.assertEqual(len(self.records()), 1)
        self.path.write_bytes(self.lines[1] + b"\n")
        value, _ = feed.collect()
        self.assertEqual(value["mode"], "truncated")
        self.assertEqual(value["accepted"], 1)
        self.assertEqual(len(self.records()), 2)
        (self.data_dir / "observation_feed_hubble.json").unlink()
        reset = self.feed()
        value, _ = reset.collect()
        self.assertEqual(value["duplicates"], 1)
        self.assertEqual(len(self.records()), 2)

    def test_idle_feed_does_not_refresh_success_and_becomes_stale(self) -> None:
        self.path.write_bytes(self.lines[0] + b"\n")
        collector = Collector(
            "observation_feed_hubble",
            self.feed().collect,
            interval_s=0.01,
            stale_after_s=0.02,
        )
        collector.collect()
        success = collector.snapshot()
        collector.collect()
        idle = collector.snapshot()
        self.assertEqual(idle["state"], "ok")
        self.assertEqual(idle["last_success_at"], success["last_success_at"])
        time.sleep(0.03)
        stale = collector.snapshot()
        self.assertEqual(stale["state"], "stale")
        self.assertFalse(stale["last_value"]["writer"]["configured"])

    def test_live_heartbeat_keeps_a_quiet_feed_healthy_and_stopped_is_error(self) -> None:
        self.path.write_bytes(b"")
        heartbeat = self.root / "hubble.heartbeat.json"
        self.write_heartbeat(heartbeat)
        collector = Collector(
            "observation_feed_hubble",
            self.feed(
                heartbeat_path=heartbeat, heartbeat_stale_after_s=1
            ).collect,
            interval_s=0.01,
            stale_after_s=0.02,
        )
        collector.collect()
        first = collector.snapshot()
        self.assertEqual(first["state"], "ok")
        self.assertTrue(first["last_value"]["idle"])
        self.assertTrue(first["last_value"]["writer"]["configured"])
        time.sleep(0.03)
        self.write_heartbeat(heartbeat)
        collector.collect()
        self.assertEqual(collector.snapshot()["state"], "ok")

        self.write_heartbeat(heartbeat, state="stopped")
        collector.collect()
        stopped = collector.snapshot()
        self.assertEqual(stopped["state"], "error")
        self.assertIn("not running", stopped["last_error"])

    def test_expired_heartbeat_and_pre_run_event_are_rejected_separately(self) -> None:
        self.path.write_bytes(self.lines[0] + b"\n")
        heartbeat = self.root / "hubble.heartbeat.json"
        old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.write_heartbeat(heartbeat, updated_at=old)
        with self.assertRaisesRegex(OSError, "expired"):
            self.feed(
                heartbeat_path=heartbeat, heartbeat_stale_after_s=1
            ).collect()

        event = json.loads(self.lines[0])
        observed_at = datetime.fromisoformat(
            event["flow"]["time"].replace("Z", "+00:00")
        )
        started_at = (observed_at + timedelta(seconds=1)).isoformat()
        self.write_heartbeat(heartbeat, started_at=started_at)
        value, _ = self.feed(heartbeat_path=heartbeat).collect()
        self.assertEqual(value["accepted"], 0)
        self.assertEqual(value["out_of_window"], 1)
        self.assertEqual(value["corrupt_lines_total"], 0)
        self.assertEqual(self.records(), [])


class LabReportTests(unittest.TestCase):
    def test_not_configured_missing_malformed_and_latest_directory(self) -> None:
        self.assertEqual(LabReportSource(None).read()["state"], "not_configured")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(LabReportSource(root / "missing.json").read()["state"], "missing")
            bad = root / "bad.json"
            bad.write_text("{bad", encoding="utf-8")
            self.assertEqual(LabReportSource(bad).read()["state"], "error")
            bad.unlink()
            first = root / "first.json"
            second = root / "second.json"
            first.write_text(json.dumps(lab_report("first")), encoding="utf-8")
            second.write_text(json.dumps(lab_report("second")), encoding="utf-8")
            os.utime(first, ns=(1, 1))
            os.utime(second, ns=(2, 2))
            payload = LabReportSource(root).read()
            self.assertEqual(payload["state"], "ok")
            self.assertEqual(payload["report"]["run_id"], "second")
            self.assertNotIn("commands", payload["report"]["cases"][0])

    def test_task_check_cases_are_optional_but_unknown_ids_are_rejected(self) -> None:
        def task_case(case_id: str) -> dict[str, object]:
            return {"id": case_id, "title": case_id, "status": "passed", "summary": "ok", "evidence": []}

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            # --task-checks adds L-07/L-08; the dashboard must still read it.
            document = lab_report("task-checks")
            document["cases"] += [task_case("L-07"), task_case("L-08")]
            path.write_text(json.dumps(document), encoding="utf-8")
            payload = LabReportSource(path).read()
            self.assertEqual(payload["state"], "ok")
            self.assertEqual([item["id"] for item in payload["report"]["cases"]][-2:], ["L-07", "L-08"])
            for bad_cases in (
                lab_report("x")["cases"] + [task_case("L-09")],
                lab_report("x")["cases"] + [task_case("L-07"), task_case("L-07")],
                lab_report("x")["cases"][:5] + [task_case("L-07")],
            ):
                with self.subTest(ids=[item["id"] for item in bad_cases]):
                    document["cases"] = bad_cases
                    path.write_text(json.dumps(document), encoding="utf-8")
                    self.assertEqual(LabReportSource(path).read()["state"], "error")


class CiliumCollectorTests(unittest.TestCase):
    def test_fixed_commands_and_safe_object_view(self) -> None:
        calls: list[list[str]] = []
        response = {
            "items": [
                {
                    "metadata": {"name": "runtime-engine-ingress"},
                    "status": {
                        "nodes": {
                            "runtime-lab-control-plane": {
                                "enforcing": True,
                                "error": "",
                                "lastUpdated": "2026-10-24T00:00:00Z",
                            }
                        },
                        "conditions": [
                            {
                                "type": "Valid",
                                "status": "True",
                                "reason": "Imported",
                                "message": "ok",
                            }
                        ]
                    },
                }
            ]
        }

        def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

        value, origin = CiliumPolicyCollector(timeout_s=1, runner=runner).collect()
        self.assertEqual(calls, [policy_command()])
        self.assertEqual(origin, "kubernetes_api")
        self.assertTrue(value["configured"])
        self.assertEqual(value["policies"][0]["name"], "runtime-engine-ingress")
        self.assertEqual(value["policies"][0]["node_status"]["enforcing"], 1)
        self.assertIn("not proof", value["meaning"])

    def test_configured_context_is_used_for_policy_query(self) -> None:
        def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, '{"items": []}', "")

        value, _ = CiliumPolicyCollector(
            timeout_s=1, context="formal-cluster", runner=runner
        ).collect()
        self.assertEqual(value["context"], "formal-cluster")


class FeedHelperTests(unittest.TestCase):
    def test_helper_commands_use_explicit_validated_context(self) -> None:
        hubble = hubble_command(since="2026-10-24T00:00:00+00:00")
        self.assertEqual(hubble[:4], ["hubble", "observe", "--follow", "-o"])
        self.assertIn("--since", hubble)
        self.assertEqual(hubble[-2:], ["--verdict", "DROPPED"])
        self.assertNotIn("--verdict", hubble_command(include_forwarded=True))
        self.assertEqual(
            hubble_port_forward_command(),
            ["cilium", "hubble", "port-forward", "--context", EXPECTED_CONTEXT],
        )
        self.assertIn(EXPECTED_CONTEXT, tetragon_command())
        self.assertIn("--tail=0", tetragon_command())
        called = False

        def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal called
            called = True
            return subprocess.CompletedProcess([], 0, EXPECTED_CONTEXT, "")

        assert_safe_context("production", runner=runner)
        self.assertFalse(called)
        with self.assertRaises(ValueError):
            assert_safe_context("-unsafe", runner=runner)

    def test_initial_context_timeout_is_reported_by_cli(self) -> None:
        from tools import k8s_observation_feed as helper

        timeout = subprocess.TimeoutExpired(["kubectl"], 10)
        with (
            patch.object(helper, "run", side_effect=timeout),
            patch.object(helper.sys, "stderr", new_callable=io.StringIO) as stderr,
        ):
            result = helper.main(["--output-dir", "unused"])

        self.assertEqual(result, 1)
        self.assertIn("timed out after 10 seconds", stderr.getvalue())

    def test_helper_writes_atomic_per_feed_process_heartbeat(self) -> None:
        class Process:
            @staticmethod
            def poll() -> None:
                return None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            heartbeat = root / "hubble.heartbeat.json"
            feed = root / "hubble.jsonl"
            write_heartbeats(
                {
                    "hubble": (
                        heartbeat,
                        feed,
                        "2026-10-24T00:00:00+00:00",
                        [("hubble", Process())],
                    )
                },
                state="running",
            )
            document = json.loads(heartbeat.read_text(encoding="utf-8"))
            self.assertEqual(document["schema"], HEARTBEAT_SCHEMA)
            self.assertEqual(document["state"], "running")
            self.assertTrue(document["processes"][0]["running"])

    def test_stop_signals_terminate_all_child_processes(self) -> None:
        for stop_signal in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=stop_signal):
                handlers: dict[int, object] = {}
                processes = [FakeProcess(), FakeProcess(), FakeProcess()]

                def fake_signal(signum: int, handler: object) -> object:
                    previous = handlers.get(signum, signal.SIG_DFL)
                    handlers[signum] = handler
                    return previous

                def tick_then_signal(_supervisor) -> None:
                    handler = handlers[stop_signal]
                    assert callable(handler)
                    handler(stop_signal, None)

                with (
                    tempfile.TemporaryDirectory() as temporary,
                    patch("tools.k8s_observation_feed.assert_safe_context"),
                    patch("tools.k8s_observation_feed._wait_for_hubble"),
                    patch("tools.k8s_observation_feed._start", side_effect=processes),
                    patch("tools.k8s_observation_feed.signal.signal", side_effect=fake_signal),
                    patch("tools.k8s_observation_feed._Supervisor.tick", tick_then_signal),
                ):
                    result = run_observation_feed(Path(temporary), EXPECTED_CONTEXT, max_ticks=3)
                    heartbeat = json.loads((Path(temporary) / "tetragon.heartbeat.json").read_text())

                self.assertEqual(result, 0)
                self.assertTrue(all(process.terminated for process in processes))
                self.assertEqual(heartbeat["state"], "stopped")
                self.assertEqual(handlers[signal.SIGTERM], signal.SIG_DFL)
                self.assertEqual(handlers[signal.SIGHUP], signal.SIG_DFL)

    def test_tetragon_backfill_uses_since_time_on_reconnect(self) -> None:
        self.assertIn("--tail=0", tetragon_command())
        command = tetragon_command(since_time="2026-10-24T13:07:36Z")
        self.assertIn("--since-time=2026-10-24T13:07:36Z", command)
        self.assertNotIn("--tail=0", command)


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -signal.SIGTERM

    def kill(self) -> None:
        self.returncode = -signal.SIGKILL


class FeedReconnectTests(unittest.TestCase):
    def setUp(self) -> None:
        import threading

        from tools import k8s_observation_feed as helper

        self.helper = helper
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = 1000.0
        self.argv: list[list[str]] = []
        self.started: list[FakeProcess] = []
        self.start_error: Exception | None = None
        self.start_returncode: int | None = None
        sink = (self.root / "sink").open("ab")
        self.addCleanup(sink.close)
        self.group = helper._FeedGroup(
            "tetragon",
            feed_path=self.root / "tetragon.jsonl",
            heartbeat_path=self.root / "tetragon.heartbeat.json",
            gap_path=self.root / "tetragon.gaps.jsonl",
            children=[helper._Child("tetragon", lambda since: helper.tetragon_command(since_time=since), sink, sink)],
        )
        self.supervisor = helper._Supervisor(
            [self.group],
            context=helper.EXPECTED_CONTEXT,
            stop_requested=threading.Event(),
            clock=lambda: self.now,
        )

    def fake_start(self, argv, _output, _error):
        if self.start_error is not None:
            raise self.start_error
        self.argv.append(list(argv))
        process = FakeProcess()
        process.returncode = self.start_returncode
        self.started.append(process)
        return process

    def heartbeat(self) -> dict:
        self.supervisor.write()
        return json.loads(self.group.heartbeat_path.read_text(encoding="utf-8"))

    def gaps(self) -> list[dict]:
        return [json.loads(line) for line in self.group.gap_path.read_text(encoding="utf-8").splitlines()]

    def test_exit_opens_gap_then_reconnects_with_backfill(self) -> None:
        with (
            patch.object(self.helper, "_start", side_effect=self.fake_start),
            patch.object(self.helper, "assert_safe_context") as context_check,
        ):
            self.supervisor.start_group(self.group, since=None)
            self.assertIn("--tail=0", self.argv[0])
            context_check.assert_not_called()
            self.assertEqual(self.heartbeat()["state"], "running")

            self.supervisor.tick()  # healthy check records last_healthy_at
            healthy = self.group.last_healthy_at
            self.started[0].returncode = 0  # kubectl logs --follow ended by itself
            self.supervisor.tick()
            heartbeat = self.heartbeat()
            # Backfill starts before the last healthy check, not at detection.
            from datetime import datetime as _dt
            backfill = _dt.strptime(heartbeat["open_gap"]["backfill_since"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            self.assertLess(backfill, healthy)
            self.assertEqual(heartbeat["open_gap"]["last_healthy_at"], healthy.isoformat())
            self.assertEqual(heartbeat["state"], "reconnecting")
            self.assertEqual(heartbeat["open_gap"]["cause"], [{"name": "tetragon", "returncode": 0}])
            self.assertEqual(self.gaps()[0]["event"], "gap_opened")

            self.supervisor.tick()  # backoff not elapsed yet
            self.assertEqual(len(self.argv), 1)
            self.now += 1.0
            self.supervisor.tick()
            self.assertEqual(len(self.argv), 2)
            since = self.gaps()[0]["backfill_since"]
            self.assertIn(f"--since-time={since}", self.argv[1])
            context_check.assert_called_once_with(self.helper.EXPECTED_CONTEXT)

        heartbeat = self.heartbeat()
        self.assertEqual(heartbeat["state"], "running")
        self.assertEqual(heartbeat["reconnect_count"], 1)
        self.assertIsNone(heartbeat["open_gap"])
        self.assertEqual(heartbeat["last_gap"]["backfill"], "requested_unverified")
        self.assertEqual([gap["event"] for gap in self.gaps()], ["gap_opened", "gap_closed"])
        self.assertEqual(heartbeat["started_at"], self.group.started_at)

    def test_failed_reconnects_back_off_and_record_the_error(self) -> None:
        with patch.object(self.helper, "_start", side_effect=self.fake_start), patch.object(self.helper, "assert_safe_context"):
            self.supervisor.start_group(self.group, since=None)
            self.started[0].returncode = 1
            self.supervisor.tick()
            self.start_error = OSError("kubectl missing")
            waits = []
            for _ in range(7):
                self.now = self.group.next_attempt
                self.supervisor.tick()
                waits.append(self.group.backoff_s)
            self.assertEqual(waits, [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0])
            self.assertEqual(self.group.open_gap["attempts"], 7)
            self.assertIn("kubectl missing", self.group.open_gap["last_error"])
            self.assertEqual(self.heartbeat()["state"], "reconnecting")
            self.start_error = None
            self.now = self.group.next_attempt
            self.supervisor.tick()
            self.assertEqual(self.group.state, "running")
            self.assertEqual(self.group.backoff_s, 1.0)

    def test_immediately_exited_child_does_not_close_the_gap(self) -> None:
        with (
            patch.object(self.helper, "_start", side_effect=self.fake_start),
            patch.object(self.helper, "assert_safe_context"),
        ):
            self.supervisor.start_group(self.group, since=None)
            self.started[0].returncode = 0
            self.supervisor.tick()
            self.start_returncode = 0
            self.now = self.group.next_attempt
            self.supervisor.tick()
        self.assertEqual(self.group.state, "reconnecting")
        self.assertIsNotNone(self.group.open_gap)
        self.assertEqual(self.group.reconnect_count, 0)
        self.assertIn("child exited during start", self.group.open_gap["last_error"])

    def test_gateway_treats_reconnecting_writer_as_unavailable(self) -> None:
        feed_path = self.root / "tetragon.jsonl"
        feed_path.write_text("", encoding="utf-8")
        feed = ObservationFeed(
            kind="tetragon",
            path=feed_path,
            data_dir=self.root / "data",
            data_origin="real",
            environment=None,
            run_id=None,
            heartbeat_path=self.group.heartbeat_path,
        )
        with patch.object(self.helper, "_start", side_effect=self.fake_start), patch.object(self.helper, "assert_safe_context"):
            self.supervisor.start_group(self.group, since=None)
            self.supervisor.write()
            value, _origin = feed.collect()
            self.assertEqual(value["writer"]["reconnect_count"], 0)
            self.started[0].returncode = 0
            self.supervisor.tick()
            self.supervisor.write()
            with self.assertRaisesRegex(OSError, "reconnecting"):
                feed.collect()
            self.now += 1.0
            self.supervisor.tick()
            self.supervisor.write()
        value, _origin = feed.collect()
        self.assertEqual(value["writer"]["reconnect_count"], 1)
        self.assertEqual(value["writer"]["last_gap"]["backfill"], "requested_unverified")

if __name__ == "__main__":
    unittest.main()
