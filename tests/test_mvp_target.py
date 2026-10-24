"""Tests for the F1 target registration frame (runtime-target/1.0)."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.target import (
    KubernetesTargetCollector,
    TargetRegistry,
    instance_id,
    load_target,
    pods_command,
    summarize_pods,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy.json"
EXAMPLE_TARGET = ROOT / "examples/mvp/target.example.json"
KIND_TARGET = ROOT / "examples/mvp/target-kind-lab.example.json"


def target_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-target/1.0",
        "target_id": "mock-engine-001",
        "engine": {"kind": "mock", "version": "mock-1.0"},
        "model": {"id": "test-model"},
    }
    document.update(updates)
    return document


def kubernetes_block(**updates: object) -> dict[str, object]:
    block: dict[str, object] = {
        "context": "kind-runtime-lab",
        "namespace": "serving-lab",
        "selector": {"app": "serving-engine"},
        "container": "engine",
    }
    block.update(updates)
    return block


def pod(name: str, uid: str, container_id: str, *, restarts: int = 0, running: bool = True, deleting: bool = False) -> dict[str, object]:
    metadata: dict[str, object] = {"name": name, "uid": uid}
    if deleting:
        metadata["deletionTimestamp"] = "2026-10-24T00:00:00Z"
    return {
        "metadata": metadata,
        "spec": {"nodeName": "node-1", "containers": [{"name": "engine"}]},
        "status": {
            "phase": "Running",
            "startTime": "2026-10-24T00:00:00Z",
            "containerStatuses": [
                {
                    "name": "engine",
                    "image": "serving-runtime-lab:dev",
                    "imageID": "docker.io/library/x@sha256:" + "a" * 64,
                    "containerID": container_id,
                    "restartCount": restarts,
                    "ready": running,
                    "state": {"running": {}} if running else {"waiting": {}},
                    "env": [{"name": "SECRET", "value": "must-not-be-read"}],
                }
            ],
        },
    }


class FakeRunner:
    def __init__(self, *documents: object) -> None:
        self.documents = list(documents)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        document = self.documents.pop(0) if len(self.documents) > 1 else self.documents[0]
        if isinstance(document, int):
            return subprocess.CompletedProcess(argv, document, "", "error")
        return subprocess.CompletedProcess(argv, 0, json.dumps(document), "")


class TempMixin:
    def write(self, name: str, document: object) -> Path:
        path = Path(self.temporary.name) / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path


class TargetRegistrationTests(TempMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_examples_load(self) -> None:
        for path in (EXAMPLE_TARGET, KIND_TARGET):
            with self.subTest(path=path.name):
                state = load_target(path)
                self.assertEqual(state.state, "ok", state.error_message)
        self.assertEqual(load_target(None).state, "not_configured")

    def test_invalid_registrations_are_errors(self) -> None:
        cases = {
            "schema": target_document(schema_version="runtime-target/2.0"),
            "unknown": target_document(extra=1),
            "empty-id": target_document(target_id=" "),
            "bad-kind": target_document(engine={"kind": "tgi"}),
            "engine-unknown": target_document(engine={"kind": "mock", "port": 1}),
            "bad-digest": target_document(engine={"kind": "mock", "image_digest": "sha256:abc"}),
            "no-model": target_document(model={}),
            "other-context": target_document(kubernetes=kubernetes_block(context="prod")),
            "bad-namespace": target_document(kubernetes=kubernetes_block(namespace="Bad_NS")),
            "empty-selector": target_document(kubernetes=kubernetes_block(selector={})),
            "bad-label": target_document(kubernetes=kubernetes_block(selector={"app": "a b"})),
            "k8s-unknown": target_document(kubernetes=dict(kubernetes_block(), token="x")),
            "not-object": [],
        }
        for label, document in cases.items():
            with self.subTest(case=label):
                state = load_target(self.write("target.json", document))
                self.assertEqual(state.state, "error")
                self.assertEqual(state.error_code, "INVALID_TARGET")
        missing = load_target(Path(self.temporary.name) / "absent.json")
        self.assertEqual(missing.error_code, "TARGET_NOT_FOUND")

    def test_gateway_decision_holds_only_declared_conflicts(self) -> None:
        data_dir = Path(self.temporary.name) / "data"
        ok = TargetRegistry(load_target(EXAMPLE_TARGET), data_dir=data_dir, observation_session_id="s")
        self.assertIsNone(ok.gateway_decision("mock-engine-001"))
        self.assertEqual(ok.gateway_decision("other-target")[0], "TARGET_MISMATCH")
        none = TargetRegistry(load_target(None), data_dir=data_dir, observation_session_id="s")
        self.assertIsNone(none.gateway_decision("anything"))
        broken = TargetRegistry(
            load_target(self.write("bad.json", {"schema_version": "x"})),
            data_dir=data_dir,
            observation_session_id="s",
        )
        self.assertEqual(broken.gateway_decision("mock-engine-001")[0], "TARGET_UNAVAILABLE")


class PodSnapshotTests(TempMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_summary_states_never_pick_among_conflicts(self) -> None:
        one = summarize_pods({"items": [pod("p1", "u1", "containerd://c1")]}, "engine")
        self.assertEqual(one["state"], "ok")
        self.assertEqual(one["instance"]["instance_id"], instance_id("u1", "containerd://c1"))
        self.assertEqual(one["instance"]["image_digest"], "sha256:" + "a" * 64)
        self.assertNotIn("must-not-be-read", json.dumps(one))
        two = summarize_pods({"items": [pod("p1", "u1", "c1"), pod("p2", "u2", "c2")]}, "engine")
        self.assertEqual((two["state"], two["instance"]), ("multiple_targets", None))
        terminating = summarize_pods(
            {"items": [pod("p1", "u1", "c1", deleting=True), pod("p2", "u2", "c2")]}, "engine"
        )
        self.assertEqual(terminating["instance"]["pod"], "p2")
        self.assertEqual(summarize_pods({"items": []}, "engine")["state"], "not_found")
        self.assertEqual(
            summarize_pods({"items": [pod("p1", "u1", "c1")]}, "other")["state"], "container_missing"
        )
        self.assertEqual(
            summarize_pods({"items": [pod("p1", "u1", "c1", running=False)]}, "engine")["state"],
            "not_running",
        )
        with self.assertRaises(ValueError):
            summarize_pods({"kind": "List"}, "engine")

    def test_collector_uses_fixed_read_only_argv(self) -> None:
        registration = load_target(self.write("t.json", target_document(kubernetes=kubernetes_block())))
        runner = FakeRunner({"items": [pod("p1", "u1", "c1")]})
        collector = KubernetesTargetCollector(registration.kubernetes, timeout_s=3, runner=runner)
        value, origin = collector.collect()
        self.assertEqual((value["state"], origin), ("ok", "kubernetes_api"))
        argv, kwargs = runner.calls[0]
        self.assertEqual(argv, pods_command(registration.kubernetes))
        self.assertEqual(argv[:5], ["kubectl", "--context", "kind-runtime-lab", "--namespace", "serving-lab"])
        self.assertIn("get", argv)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["timeout"], 3)
        failing = KubernetesTargetCollector(registration.kubernetes, timeout_s=3, runner=FakeRunner(1))
        with self.assertRaises(OSError):
            failing.collect()

    def test_restart_and_replacement_become_new_instances_across_sessions(self) -> None:
        data_dir = Path(self.temporary.name) / "data"
        registration = load_target(self.write("t.json", target_document(kubernetes=kubernetes_block())))
        first = TargetRegistry(registration, data_dir=data_dir, observation_session_id="s1")
        snap = lambda *pods: summarize_pods({"items": list(pods)}, "engine")
        first.observe_kubernetes(snap(pod("p1", "u1", "c1")), "kubernetes_api")
        first.observe_kubernetes(snap(pod("p1", "u1", "c1")), "kubernetes_api")
        first.observe_kubernetes(snap(pod("p1", "u1", "c2", restarts=1)), "kubernetes_api")
        first.observe_kubernetes(snap(pod("p1", "u1", "c2"), pod("p9", "u9", "c9")), "kubernetes_api")
        self.assertIsNone(first.record_context()["instance_id"])
        self.assertEqual(first.record_context()["instance_state"], "multiple_targets")
        first.observe_kubernetes(snap(pod("p2", "u2", "c3")), "kubernetes_api")

        second = TargetRegistry(registration, data_dir=data_dir, observation_session_id="s2")
        second.observe_kubernetes(snap(pod("p2", "u2", "c3")), "kubernetes_api")
        second.observe_kubernetes(snap(pod("p2", "u2", "c3")), "kubernetes_api")

        records, corrupt = JsonlStore(data_dir / "target_snapshots.jsonl").read_all()
        self.assertEqual(corrupt, [])
        self.assertEqual(
            [(r["event"], r["reason"], r["observation_session_id"]) for r in records],
            [
                ("instance_started", None, "s1"),
                ("instance_changed", "container_restarted", "s1"),
                ("instance_changed", "pod_replaced", "s1"),
                ("instance_confirmed", None, "s2"),
            ],
        )
        self.assertEqual(records[1]["previous_instance_id"], instance_id("u1", "c1"))
        self.assertEqual(second.record_context()["instance_id"], instance_id("u2", "c3"))

    def test_status_compares_declared_and_observed_values(self) -> None:
        registration = load_target(
            self.write(
                "t.json",
                target_document(
                    engine={"kind": "mock", "version": "mock-2.0", "image_digest": "sha256:" + "b" * 64},
                    kubernetes=kubernetes_block(),
                ),
            )
        )
        registry = TargetRegistry(registration, data_dir=Path(self.temporary.name) / "d", observation_session_id="s")
        registry.observe_kubernetes(summarize_pods({"items": [pod("p1", "u1", "c1")]}, "engine"), None)
        status = registry.status(
            policy_target_id="mock-engine-001",
            backend_kind="mock",
            engine_info={"state": "ok", "last_value": {"engine.version": "mock-1.0", "model.id": "test-model"}},
            kubernetes={"state": "ok"},
        )
        rows = {row["field"]: row["state"] for row in status["comparison"]}
        self.assertEqual(rows["policy.target_id"], "match")
        self.assertEqual(rows["engine.kind"], "match")
        self.assertEqual(rows["engine.version"], "mismatch")
        self.assertEqual(rows["model.id"], "match")
        self.assertEqual(rows["model.revision"], "not_declared")
        self.assertEqual(rows["engine.image_digest"], "mismatch")
        self.assertIsNone(status["gateway_hold"])
        stale = registry.status(
            policy_target_id="mock-engine-001",
            backend_kind="mock",
            engine_info={"state": "stale", "last_value": {"model.id": "test-model"}},
            kubernetes=None,
        )
        self.assertEqual({r["field"]: r["state"] for r in stale["comparison"]}["model.id"], "unknown")


class TargetGatewayHttpTests(TempMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "data"
        try:
            self.engine = MockEngine(port=0, receipts_path=self.data_dir / "engine_receipts.jsonl").start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)

    def start(self, target_path: Path | None) -> None:
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(POLICY),
            data_dir=str(self.data_dir),
            target_path=str(target_path) if target_path else None,
            collector_interval_s=0.05,
        )
        try:
            self.gateway = GatewayApp(config, load_policy(POLICY), self.engine.address).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.gateway.stop)

    def url(self, path: str) -> str:
        return f"http://{self.gateway.address[0]}:{self.gateway.address[1]}{path}"

    def post(self):
        body = json.dumps({"model": "test-model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}).encode()
        request = Request(self.url("/v1/chat/completions"), data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            with closing(error):
                return error.code, dict(error.headers), error.read()

    def get(self, path: str) -> dict[str, object]:
        with urlopen(self.url(path), timeout=3) as response:
            return json.loads(response.read())

    def final(self, request_id: str) -> dict[str, object]:
        records, _ = JsonlStore(self.data_dir / "gateway_records.jsonl").read_all()
        return [r for r in records if r["request_id"] == request_id and r["phase"] == "final"][-1]

    def receipts(self) -> int:
        records, _ = JsonlStore(self.data_dir / "engine_receipts.jsonl").read_all()
        return len(records)

    def test_matching_target_relays_and_records_target(self) -> None:
        self.start(EXAMPLE_TARGET)
        status, headers, _ = self.post()
        self.assertEqual(status, 200)
        record = self.final(headers["X-Request-ID"])
        self.assertEqual(record["target"]["registration"], "ok")
        self.assertEqual(record["target"]["target_id"], "mock-engine-001")
        self.assertEqual(record["target"]["instance_state"], "not_configured")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            target = self.get("/api/target")
            rows = {row["field"]: row["state"] for row in target["comparison"]}
            if rows.get("model.id") == "match":
                break
            time.sleep(0.05)
        self.assertEqual(rows["model.id"], "match")
        self.assertEqual(rows["engine.version"], "match")
        self.assertEqual(self.get("/api/status")["target"]["registration"], "ok")

    def test_target_conflicts_hold_requests_without_relay(self) -> None:
        for label, document, code in (
            ("mismatch", target_document(target_id="other-target"), "TARGET_MISMATCH"),
            ("broken", {"schema_version": "runtime-target/1.0"}, "TARGET_UNAVAILABLE"),
        ):
            with self.subTest(case=label):
                self.start(self.write(f"{label}.json", document))
                before = self.receipts()
                status, headers, body = self.post()
                self.assertEqual(status, 503)
                self.assertEqual(json.loads(body)["error"]["code"], code)
                self.assertEqual(self.receipts(), before)
                record = self.final(headers["X-Request-ID"])
                self.assertEqual(record["decision"]["result"], "ERROR")
                self.assertEqual(self.get("/api/status")["status"], "degraded")
                self.gateway.stop()

    def test_without_target_behaves_as_before(self) -> None:
        self.start(None)
        status, headers, _ = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.final(headers["X-Request-ID"])["target"]["registration"], "not_configured")


if __name__ == "__main__":
    unittest.main()
