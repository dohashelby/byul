"""Host Pod log to phase timeline, without Kubernetes or GPU."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.k8s_observation_feed import EnginePhaseTimelineWriter
from serving_runtime.mvp.target import KubernetesSelector
from serving_runtime.enforcement.execpolicy import ExecPolicy, parse_timeline
from serving_runtime.enforcement.intent import load_intent


ROOT = Path(__file__).resolve().parents[1]
LOG = (
    b"2026-11-02T10:00:00Z INFO 11-02 19:00:00 Loading model from scratch...\n"
    b"2026-11-02T10:00:03Z INFO 11-02 19:00:03 Model loading took 3 seconds\n"
    b"2026-11-02T10:00:04Z INFO 11-02 19:00:04 Application startup complete.\n"
)
SELECTOR = KubernetesSelector("byul-aws-lab", "serving-lab", (("app", "serving-engine"),), "engine")


class Runner:
    def __init__(self) -> None:
        self.uid = "pod-a"
        self.log = LOG
        self.fail_logs = False
        self.count = 1
        self.uid_after_logs = None
        self.log_commands: list[list[str]] = []
        self.log_bytes = 0

    def __call__(self, argv, **_kwargs):
        if "pods" in argv:
            items = [{"metadata": {"name": "engine-1", "uid": self.uid},
                      "status": {"phase": "Running"}} for _ in range(self.count)]
            return subprocess.CompletedProcess(argv, 0, json.dumps({"items": items}).encode(), b"")
        self.log_commands.append(argv)
        if self.fail_logs:
            return subprocess.CompletedProcess(argv, 1, b"", b"query failed")
        assert "--timestamps=true" in argv
        assert "-c" in argv and argv[argv.index("-c") + 1] == "engine"
        limit = int(next(item.split("=", 1)[1] for item in argv if item.startswith("--limit-bytes=")))
        since = next((item.split("=", 1)[1] for item in argv if item.startswith("--since-time=")), None)
        lines = self.log.splitlines(keepends=True)
        if since is not None:
            boundary = datetime.fromisoformat(since.replace("Z", "+00:00"))
            lines = [line for line in lines if _timestamp(line) is None or _timestamp(line) >= boundary]
        output = b"".join(lines)[:limit]
        self.log_bytes += len(output)
        if self.uid_after_logs is not None:
            self.uid = self.uid_after_logs
            self.uid_after_logs = None
        return subprocess.CompletedProcess(argv, 0, output, b"")


def _timestamp(line: bytes):
    try:
        return datetime.fromisoformat(line.split(b" ", 1)[0].decode().replace("Z", "+00:00"))
    except (UnicodeError, ValueError):
        return None


def writer(path: Path, runner: Runner) -> EnginePhaseTimelineWriter:
    return EnginePhaseTimelineWriter(path, SELECTOR, runner=runner)


class EnginePhaseFeedTests(unittest.TestCase):
    def test_incremental_boundary_failure_uid_and_target_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = Runner()
            feed = writer(Path(directory) / "timeline.json", runner)
            first = feed.collect_once()
            self.assertEqual(first["collector"]["state"], "running")
            self.assertEqual([row["phase"] for row in first["entries"]], ["loading", "inferring"])
            self.assertEqual(first["entries"][0]["from"], "2026-11-02T10:00:00+00:00")
            self.assertEqual(first["entries"][1]["basis"], "engine_signal")
            self.assertFalse(any(arg.startswith("--since-time=") for arg in runner.log_commands[0]))
            self.assertEqual(feed.collect_once()["entries"], first["entries"])
            self.assertTrue(any(arg.startswith("--since-time=2026-11-02T10:00:03.999999")
                                for arg in runner.log_commands[-1]))
            runner.log += LOG.splitlines(keepends=True)[-1]
            self.assertEqual(feed.collect_once()["entries"], first["entries"])
            self.assertEqual(feed.collect_once()["extraction"]["matched_signals"]["inferring_start"], 1)
            runner.fail_logs = True
            self.assertEqual(feed.collect_once()["collector"]["state"], "log_error")
            runner.fail_logs = False
            runner.log += b"2026-11-02T10:00:05Z INFO [shutdown] API server: shutdown triggered\n"
            recovered = feed.collect_once()
            self.assertEqual(recovered["entries"][-1]["until"], "2026-11-02T10:00:05+00:00")
            runner.uid_after_logs = "pod-race"
            self.assertEqual(feed.collect_once()["collector"]["state"], "log_error")
            runner.uid, runner.log = "pod-b", LOG.splitlines(keepends=True)[-1]
            replacement = feed.collect_once()
            self.assertEqual(replacement["collector"]["pod_uid"], "pod-b")
            self.assertNotIn("loading", [row["phase"] for row in replacement["entries"]])
            runner.count = 0
            self.assertEqual(feed.collect_once()["collector"]["state"], "no_target")
            runner.count = 2
            self.assertEqual(feed.collect_once()["collector"]["state"], "multiple_targets")
            self.assertEqual(feed.collect_once()["entries"], [])

    def test_bad_lines_partial_tail_out_of_order_and_request_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = Runner()
            runner.log = (LOG.splitlines(keepends=True)[0]
                + b"2026-11-02T10:00:01Z \xff\xfe\n"
                + b"untimestamped Application startup complete.\n"
                + b"2026-13-02T10:00:01Z INFO ignored\n"
                + b"2026-11-02T10:00:02Z INFO request_logger Received request prompt='Application startup complete.'\n"
                + LOG.splitlines(keepends=True)[1]
                + b"2026-11-02T10:00:02Z INFO Application startup complete.\n"
                + LOG.splitlines(keepends=True)[2]
                + b"2026-11-02T10:00:06Z INFO 11-02 19:00:06 Loading model from scratch...\n"
                + b"2026-11-02T10:00:07Z INFO 11-02 19:00:07 Model loading took 1 seconds\n"
                + b"2026-11-02T10:00:08Z INF \xe2\x82")
            feed = writer(Path(directory) / "timeline.json", runner)
            document = feed.collect_once()
            self.assertEqual(document["collector"]["state"], "running")
            self.assertEqual(document["extraction"]["skipped_lines"], {
                "invalid_utf8": 1, "missing_timestamp": 1, "invalid_timestamp": 1,
                "out_of_order": 1, "partial_tail": 1,
            })
            self.assertEqual(document["extraction"]["matched_signals"]["inferring_start"], 1)
            self.assertEqual(document["entries"][-1]["phase"], "loading")
            self.assertNotIn("prompt", json.dumps(document))
            parse_timeline(document)
            runner.log = LOG + b"2026-11-02T10:00:09Z INFO Loading model from scratch...\n2026-11-02T10:00:"
            partial_time = writer(Path(directory) / "partial-time.json", runner).collect_once()
            self.assertEqual(partial_time["collector"]["state"], "running")
            self.assertEqual(partial_time["extraction"]["skipped_lines"]["partial_tail"], 1)
            runner.log = LOG + b"2026-11-02T10:00:05Z INFO 11-02 19:00:05 Loading model from scratch...\n"
            # A fresh writer sees no previous run and should use the kubectl
            # absolute time, never the engine's local 19:00 time as UTC.
            fresh = writer(Path(directory) / "fresh.json", runner).collect_once()
            self.assertEqual(fresh["entries"][0]["from"], "2026-11-02T10:00:00+00:00")

    def test_limit_restart_marks_history_incomplete_until_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("tools.k8s_observation_feed.PHASE_LOG_LIMIT_BYTES", 160):
            runner = Runner()
            runner.log = LOG + b"2026-11-02T10:00:05Z INFO request_logger Received request prompt='x'\n" * 4
            feed = writer(Path(directory) / "timeline.json", runner)
            first = feed.collect_once()
            self.assertEqual(first["collector"]["state"], "incomplete_history")
            self.assertTrue(first["extraction"]["history_incomplete"])
            self.assertEqual(first["entries"], [])
            # Restart does a capped reconstruction of the same Pod, then a
            # subsequent since-time fetch; neither invents the old phase.
            restarted = writer(Path(directory) / "restart.json", runner)
            self.assertEqual(restarted.collect_once()["collector"]["state"], "incomplete_history")
            runner.log += (b"2026-11-02T10:00:10Z INFO Loading model from scratch...\n"
                           b"2026-11-02T10:00:11Z INFO Model loading took 1 seconds\n")
            # Advance through the bounded backlog until a complete fetch sees
            # the new start. Each query remains capped.
            document = None
            for _ in range(8):
                document = restarted.collect_once()
                if document["collector"]["state"] == "running":
                    break
            self.assertIsNotNone(document)
            self.assertEqual(document["collector"]["state"], "running")
            self.assertEqual(document["entries"][-1]["phase"], "loading")
            self.assertEqual(document["entries"][-1]["until"], "2026-11-02T10:00:11+00:00")
            self.assertLessEqual(runner.log_bytes, 160 * len(runner.log_commands))
            parse_timeline(document)

    def test_stale_15_seconds_is_unknown_and_suspect_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeline.json"
            runner = Runner()
            feed = writer(path, runner)
            document = feed.collect_once()
            intent = load_intent(ROOT / "deploy/gpu-lab/intent.aws-lab.json")
            policy = ExecPolicy(intent, timeline_path=path)
            fresh = policy.classify_exec(binary="/usr/bin/uname", parent=None, image=None,
                                         observed_at="2026-11-02T10:00:05Z")
            self.assertEqual((fresh["phase"], fresh["exec_verdict"]), (["inferring"], "outside_allowlist"))
            document["collector"]["written_at"] = (datetime.now(timezone.utc) - timedelta(seconds=16)).isoformat()
            path.write_text(json.dumps(document), encoding="utf-8")
            stale = ExecPolicy(intent, timeline_path=path).classify_exec(
                binary="/usr/bin/uname", parent=None, image=None,
                observed_at="2026-11-02T10:00:05Z")
            self.assertEqual((stale["phase"], stale["exec_verdict"], stale["phase_source_error"]),
                             (["unknown"], "phase_unknown", "stale"))
            self.assertEqual(ExecPolicy(intent, timeline_path=path).classify_exec(
                binary="/bin/sh", parent=None, image=None,
                observed_at="2026-11-02T10:00:05Z")["exec_verdict"], "suspect_binary")
