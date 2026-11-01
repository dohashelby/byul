"""Host/Gateway tests for runtime-target-pods-snapshot/1.0."""

from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.cli import main
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import (
    DEFAULT_COLLECTOR_INTERVAL_S,
    DEFAULT_COLLECTOR_STALE_AFTER_S,
    MVPConfig,
)
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.target import (
    POD_SNAPSHOT_MAX_BYTES,
    POD_SNAPSHOT_SCHEMA,
    TargetPodSnapshotCollector,
    TargetRegistry,
    load_target,
    pods_command,
)
from tools import k8s_observation_feed as feed


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy.json"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def target_document(*, kubernetes: bool = True) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-target/1.0",
        "target_id": "mock-engine-001",
        "engine": {"kind": "mock", "version": "mock-1.0"},
        "model": {"id": "test-model"},
    }
    if kubernetes:
        document["kubernetes"] = {
            "context": "kind-runtime-lab",
            "namespace": "serving-lab",
            "selector": {"app": "serving-engine"},
            "container": "engine",
        }
    return document


def raw_pod(uid: str, container_id: str) -> dict[str, object]:
    return {
        "metadata": {
            "name": f"engine-{uid}",
            "uid": uid,
            "labels": {"app": "serving-engine", "generation": "one"},
            "annotations": {"secret.example/token": "must-not-survive"},
        },
        "spec": {
            "nodeName": "gpu-node",
            "containers": [
                {
                    "name": "engine",
                    "args": ["--api-key", "must-not-survive"],
                    "env": [{"name": "SECRET", "value": "must-not-survive"}],
                }
            ],
        },
        "status": {
            "phase": "Running",
            "startTime": "2026-10-26T11:59:00Z",
            "conditions": [
                {"type": "Ready", "status": "True", "message": "must-not-survive"}
            ],
            "containerStatuses": [
                {
                    "name": "engine",
                    "containerID": container_id,
                    "imageID": "registry/engine@sha256:" + "a" * 64,
                    "image": "registry/engine:0.31.0",
                    "ready": True,
                    "restartCount": 0,
                    "state": {"running": {"startedAt": "2026-10-26T11:59:00Z"}},
                    "allocatedResources": {"nvidia.com/gpu": "1"},
                }
            ],
        },
    }


def snapshot(items: list[object], **updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": POD_SNAPSHOT_SCHEMA,
        "context": "kind-runtime-lab",
        "namespace": "serving-lab",
        "selector": {"app": "serving-engine"},
        "collected_at": NOW.isoformat(),
        "state": "ok",
        "error": None,
        "items": items,
    }
    document.update(updates)
    return document


class FakeRunner:
    def __init__(self, result: subprocess.CompletedProcess[str]) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return self.result


class TargetSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target_path = self.root / "target.json"
        self.target_path.write_text(json.dumps(target_document()), encoding="utf-8")
        self.registration = load_target(self.target_path)
        assert self.registration.kubernetes is not None

    def write_snapshot(self, document: object) -> Path:
        path = self.root / "target-pods.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def collector(self, path: Path) -> TargetPodSnapshotCollector:
        return TargetPodSnapshotCollector(
            path,
            self.registration.kubernetes,
            stale_after_s=30,
            now=lambda: NOW,
        )

    def test_host_writer_uses_fixed_argv_atomic_replace_and_reduces_fields(self) -> None:
        path = self.root / "target-pods.json"
        path.write_text("old", encoding="utf-8")
        old_inode = path.stat().st_ino
        runner = FakeRunner(
            subprocess.CompletedProcess(
                [], 0, json.dumps({"items": [raw_pod("uid-1", "containerd://one")]}), ""
            )
        )
        writer = feed.TargetPodSnapshotWriter(
            path,
            self.registration.kubernetes,
            interval_s=feed.DEFAULT_TARGET_INTERVAL_S,
            runner=runner,
        )
        with patch.object(feed, "_reader_gid", None):
            written = writer.collect_once()
        self.assertEqual(written["state"], "ok")
        self.assertNotEqual(path.stat().st_ino, old_inode)
        self.assertFalse(list(self.root.glob(".*.tmp")))
        encoded = path.read_text(encoding="utf-8")
        self.assertNotIn("must-not-survive", encoded)
        stored = json.loads(encoded)
        self.assertEqual(stored["items"][0]["status"]["conditions"], [{"type": "Ready", "status": "True"}])
        self.assertEqual(stored["items"][0]["status"]["containerStatuses"][0]["state"], {"running": {}})
        argv, kwargs = runner.calls[0]
        self.assertEqual(argv, pods_command(self.registration.kubernetes))
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["timeout"], feed.TARGET_QUERY_TIMEOUT_S)

    def test_host_failure_replaces_good_items_with_error_without_details(self) -> None:
        path = self.write_snapshot(snapshot([raw_pod("old", "containerd://old")]))
        runner = FakeRunner(subprocess.CompletedProcess([], 7, "", "token=must-not-log"))
        writer = feed.TargetPodSnapshotWriter(
            path,
            self.registration.kubernetes,
            interval_s=feed.DEFAULT_TARGET_INTERVAL_S,
            runner=runner,
        )
        with patch.object(feed, "_reader_gid", None):
            writer.collect_once()
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["state"], "error")
        self.assertEqual(stored["error"], {"class": "KubectlExit", "code": "kubectl_exit"})
        self.assertNotIn("items", stored)
        self.assertNotIn("must-not-log", json.dumps(stored))

    def test_host_timeout_replaces_good_items_with_explicit_error(self) -> None:
        path = self.write_snapshot(snapshot([raw_pod("old", "containerd://old")]))

        def timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        writer = feed.TargetPodSnapshotWriter(
            path,
            self.registration.kubernetes,
            interval_s=feed.DEFAULT_TARGET_INTERVAL_S,
            runner=timeout,
        )
        with patch.object(feed, "_reader_gid", None):
            writer.collect_once()
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["state"], "error")
        self.assertEqual(
            stored["error"],
            {"class": "TimeoutExpired", "code": "kubectl_timeout"},
        )
        self.assertNotIn("items", stored)

    def test_gateway_accepts_matching_fresh_snapshot_and_marks_transport(self) -> None:
        path = self.write_snapshot(snapshot([feed._reduce_pod(raw_pod("uid-1", "containerd://one"))]))
        value, origin = self.collector(path).collect()
        self.assertEqual((value["state"], origin), ("ok", "kubernetes_api"))
        self.assertEqual(value["origin_note"], "host_snapshot")
        self.assertEqual(value["instance"]["pod_uid"], "uid-1")

    def test_host_snapshot_keeps_failed_pod_visible_without_selecting_it(self) -> None:
        failed = raw_pod("failed", "containerd://old")
        failed["status"]["phase"] = "Failed"
        running = raw_pod("current", "containerd://current")
        path = self.root / "target-pods.json"
        runner = FakeRunner(subprocess.CompletedProcess([], 0, json.dumps({"items": [failed, running]}), ""))
        writer = feed.TargetPodSnapshotWriter(
            path, self.registration.kubernetes,
            interval_s=feed.DEFAULT_TARGET_INTERVAL_S, runner=runner,
        )
        with patch.object(feed, "_reader_gid", None):
            writer.collect_once()
        value, _ = self.collector(path).collect()
        self.assertEqual(value["state"], "ok")
        self.assertEqual(value["instance"]["pod_uid"], "current")
        self.assertEqual({pod["phase"] for pod in value["pods"]}, {"Failed", "Running"})

    def test_gateway_rejects_schema_mismatch_registration_mismatch_and_stale(self) -> None:
        item = feed._reduce_pod(raw_pod("uid-1", "containerd://one"))
        cases = {
            "schema": snapshot([item], schema="runtime-target-pods-snapshot/2.0"),
            "context": snapshot([item], context="another-cluster"),
            "namespace": snapshot([item], namespace="another-namespace"),
            "selector": snapshot([item], selector={"app": "another-engine"}),
            "stale": snapshot(
                [item], collected_at=(NOW - timedelta(seconds=31)).isoformat()
            ),
        }
        for name, document in cases.items():
            with self.subTest(name=name):
                with self.assertRaises((OSError, ValueError)):
                    self.collector(self.write_snapshot(document)).collect()

    def test_gateway_rejects_oversize_and_symlink(self) -> None:
        path = self.root / "oversize.json"
        path.write_bytes(b" " * (POD_SNAPSHOT_MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "4 MiB"):
            self.collector(path).collect()
        real = self.write_snapshot(snapshot([]))
        link = self.root / "link.json"
        os.symlink(real, link)
        with self.assertRaises(OSError):
            self.collector(link).collect()

    def test_snapshot_observation_produces_pod_replaced_history(self) -> None:
        path = self.write_snapshot(snapshot([feed._reduce_pod(raw_pod("uid-1", "containerd://one"))]))
        registry = TargetRegistry(
            self.registration,
            data_dir=self.root / "data",
            observation_session_id="snapshot-test",
        )
        first, origin = self.collector(path).collect()
        registry.observe_kubernetes(first, origin)
        self.write_snapshot(snapshot([feed._reduce_pod(raw_pod("uid-2", "containerd://two"))]))
        second, origin = self.collector(path).collect()
        registry.observe_kubernetes(second, origin)
        records, corrupt = JsonlStore(self.root / "data" / "target_snapshots.jsonl").read_all()
        self.assertEqual(corrupt, [])
        self.assertEqual(records[-1]["event"], "instance_changed")
        self.assertEqual(records[-1]["reason"], "pod_replaced")
        self.assertNotEqual(records[0]["instance"]["instance_id"], records[-1]["instance"]["instance_id"])

    def test_snapshot_option_requires_registration_kubernetes_block(self) -> None:
        no_kubernetes = self.root / "no-kubernetes.json"
        no_kubernetes.write_text(json.dumps(target_document(kubernetes=False)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "kubernetes block"):
            GatewayApp(
                # Construction is enough; no socket is opened.
                MVPConfig(
                    data_dir=str(self.root / "gateway"),
                    target_path=str(no_kubernetes),
                    target_pod_snapshot=str(self.root / "target-pods.json"),
                ),
                load_policy(POLICY),
                ("127.0.0.1", 1),
            )

    def test_cli_target_sources_are_mutually_exclusive(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(
                [
                    "mvp",
                    "--target-pod-status",
                    "--target-pod-snapshot",
                    "target-pods.json",
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_default_snapshot_timing_stays_inside_gateway_stale_window(self) -> None:
        parsed = feed.build_parser().parse_args(["--output-dir", str(self.root)])
        self.assertEqual(parsed.target_interval, feed.DEFAULT_TARGET_INTERVAL_S)
        worst_case_age_s = (
            feed.DEFAULT_TARGET_INTERVAL_S
            + feed.TARGET_QUERY_TIMEOUT_S
            + DEFAULT_COLLECTOR_INTERVAL_S
        )
        self.assertLess(worst_case_age_s, DEFAULT_COLLECTOR_STALE_AFTER_S)

    def test_cli_rejects_target_interval_above_safe_limit(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            feed.build_parser().parse_args(
                ["--output-dir", str(self.root), "--target-interval", "5.1"]
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("at most 5 seconds", error.getvalue())


if __name__ == "__main__":
    unittest.main()
