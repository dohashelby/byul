"""EXAMPLE kubectl protocol plus product run/store/judge/RPT; no cluster startup."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.core.select import select
from guard.executors.kubernetes_sampling import KubernetesSamplingExecutor
from guard.models import DataOrigin, ExecStatus, Verdict
from guard.runtime import kubernetes_http as kube
from guard.runtime.bounded import capture_pipes
from guard.store import Store
from tests.builders import baseline_pack, environment, policy
from tests.test_sampling_validation import cfg, check, judge, summary, VALUES


IMAGE = "example/engine@sha256:" + "a" * 64
PROBE = "example/python@sha256:" + "b" * 64
PRIVATE = "EXAMPLE-private-response-and-exception"


def inputs():
    roles = {}
    for index, (role, ip, image) in enumerate((("engine", "127.0.0.1", IMAGE), ("probe", "127.0.0.2", PROBE)), 1):
        roles[role] = {"namespace": role, "namespace_uid": f"00000000-0000-0000-0000-{index:012d}",
            "pod": role + "-pod", "pod_uid": f"10000000-0000-0000-0000-{index:012d}",
            "container": role, "container_id": "containerd://" + str(index) * 64,
            "image": image, "image_id": image, "restart_count": 0, "node": "fixture-node", "pod_ip": ip,
            "started_at": "2026-10-26T00:00:00Z"}
    return {"lab": True, "topology": "kubernetes-owned-direct", "engine_kind": "vllm",
        "origins": {"trusted": {"network": "kubernetes-probe"}},
        "engine": {"trusted_url": "http://127.0.0.1:8000", "image_digest": IMAGE},
        "kubernetes": {"context": "fixture-context", "run_id": "guard-fixture", **roles}}


def env():
    return environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE}, model={"id": "fixture-model"})


class World:
    def __init__(self, target=None):
        self.target = target or inputs()
        self.calls = []
        self.execs = []
        self.mutate = None
        self.outcome = None
        self.gap = None
        self.after_exec = False
        self.actual_probe = False
        self.lock = threading.Lock()

    def runner(self, argv, **kwargs):
        assert argv[0] == "kubectl" and argv[1] == "--context=fixture-context"
        assert argv[2].startswith("--request-timeout=") and kwargs["timeout"] > 0
        assert PRIVATE not in " ".join(argv)
        with self.lock:
            self.calls.append(argv)
        if self.gap == "exception":
            raise RuntimeError(PRIVATE)
        if "namespaces" in argv:
            items = [{"metadata": {"name": r["namespace"], "uid": r["namespace_uid"]}}
                     for r in self.target["kubernetes"].values() if type(r) is dict]
            value = {"kind": "List", "items": items}
            if self.mutate:
                self.mutate("namespaces", value, self.after_exec)
        elif "get" in argv:
            role = "engine" if "engine-pod" in argv else "probe"
            r = self.target["kubernetes"][role]
            value = {"metadata": {"name": r["pod"], "namespace": r["namespace"], "uid": r["pod_uid"],
                                   "labels": {"guard.run": "guard-fixture"}, "annotations": {"private": PRIVATE}},
                     "spec": {"nodeName": r["node"], "containers": [{"name": r["container"], "image": r["image"],
                              "env": [{"name": "PRIVATE", "value": PRIVATE}]}]},
                     "status": {"phase": "Running", "podIP": r["pod_ip"], "conditions": [{"type": "Ready", "status": "True"}],
                         "containerStatuses": [{"name": r["container"], "containerID": r["container_id"],
                         "imageID": r["image_id"], "restartCount": 0, "ready": True,
                         "state": {"running": {"startedAt": r["started_at"]}}}]}}
            if self.mutate:
                self.mutate(role, value, self.after_exec)
        else:
            assert "exec" in argv and "-i" in argv and "--container=probe" in argv
            spec = json.loads(kwargs["input_bytes"])
            request, = spec["requests"]
            with self.lock:
                self.execs.append(request)
                self.after_exec = True
            if self.actual_probe:
                return capture_pipes([sys.executable, "-c", argv[-2], argv[-1]], **kwargs)
            outcome = self.outcome if self.outcome is not None and request["id"].endswith("-challenge") else (
                400 if request["id"].endswith("-challenge") else 200)
            value = [{"id": request["id"], "method": request["method"], "outcome": outcome,
                "body": json.dumps({"model": "fixture-model", "choices": [{"text": PRIVATE, "finish_reason": "length"}],
                                    "usage": {"completion_tokens": 1}}),
                "detail": PRIVATE, "body_preview": PRIVATE, "headers": {"Authorization": PRIVATE}, "body_bytes": 123}]
        return {"returncode": 1 if self.gap == "returncode" else 0, "stdout_bytes": json.dumps(value).encode(),
                "stderr_bytes": PRIVATE.encode(), "capture": {"complete": self.gap != "truncated"}}


def run(tmp_path, world=None, target=None):
    world = world or World(target)
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target or world.target), encoding="utf-8")
    draft = KubernetesSamplingExecutor({**cfg(), "target": str(path)}, runner=world.runner).run(
        check(), env(), deepcopy(VALUES), VllmAdapter(), tmp_path / "work")
    return draft, world


def test_reuses_all_sampling_cases_preserves_unknown_security_and_no_raw_data(tmp_path):
    draft, world = run(tmp_path)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert judge(draft).verdicts == {"security": Verdict.UNKNOWN, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    assert len(world.execs) == 16
    log = draft.artifacts[0].path.read_text(encoding="utf-8")
    assert PRIVATE not in log and "guard sampling control" not in log
    data = summary(draft)
    assert data["request_dispatch_attempts"] == 16
    assert "control" not in data["fixture"] and len(data["fixture"]["control_sha256"]) == 64
    assert data["kubernetes"]["binding"] == world.target["kubernetes"]
    assert all(r["binding_verified_before_after"] for r in data["kubernetes"]["requests"])
    assert draft.config["probe_image"] == PROBE


@pytest.mark.parametrize("field", ["pod_uid", "container_id", "image_id", "restart_count", "started_at", "node", "pod_ip", "image"])
@pytest.mark.parametrize("role", ["engine", "probe"])
def test_same_name_label_cannot_substitute_a_different_generation(tmp_path, field, role):
    world = World()
    def mutate(which, value, after):
        if which != role or not after:
            return
        if field == "pod_uid": value["metadata"]["uid"] = "replaced"
        elif field == "node": value["spec"]["nodeName"] = "replacement"
        elif field == "pod_ip": value["status"]["podIP"] = "127.0.0.3"
        elif field == "image": value["spec"]["containers"][0]["image"] = PROBE if role == "engine" else IMAGE
        else:
            state = value["status"]["containerStatuses"][0]
            if field == "started_at": state["state"]["running"]["startedAt"] = "2026-10-26T01:00:00Z"
            else: state[{"container_id": "containerID", "image_id": "imageID", "restart_count": "restartCount"}[field]] = 1
    world.mutate = mutate
    draft, _ = run(tmp_path, world)
    assert len(world.execs) == 1
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN
    assert draft.observations["sampling_options_tested"].value == []
    assert summary(draft)["kubernetes"]["collection_gap"] is True


@pytest.mark.parametrize("mode", ["namespace", "not_ready", "terminating", "host_network", "label", "missing_container"])
def test_binding_gap_stops_before_http_and_does_not_persist_api_objects(tmp_path, mode):
    world = World()
    def mutate(role, value, after):
        if mode == "namespace" and role == "namespaces": value["items"][0]["metadata"]["uid"] = "other-cluster"
        if role != "engine": return
        if mode == "not_ready": value["status"]["conditions"] = []
        elif mode == "terminating": value["metadata"]["deletionTimestamp"] = "now"
        elif mode == "host_network": value["spec"]["hostNetwork"] = True
        elif mode == "label": value["metadata"]["labels"] = {}
        elif mode == "missing_container": value["status"]["containerStatuses"] = []
    world.mutate = mutate
    draft, _ = run(tmp_path, world)
    assert world.execs == []
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN
    assert PRIVATE not in draft.artifacts[0].path.read_text(encoding="utf-8")


@pytest.mark.parametrize("gap", ["exception", "returncode", "truncated"])
def test_kubectl_failure_is_unknown_with_fixed_diagnostic(tmp_path, gap):
    world = World(); world.gap = gap
    draft, _ = run(tmp_path, world)
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN
    assert PRIVATE not in draft.artifacts[0].path.read_text(encoding="utf-8")


@pytest.mark.parametrize("field,bad", [("container_id", "docker://" + "1" * 64), ("restart_count", True),
    ("image", "example/engine:latest"), ("pod_uid", ""), ("pod_ip", "engine.service"), ("started_at", "unknown")])
def test_invalid_selection_never_runs_kubectl(tmp_path, field, bad):
    target = inputs(); target["kubernetes"]["engine"][field] = bad
    world = World(target)
    with pytest.raises(kube.KubernetesBindingError, match="^KUBERNETES_HTTP_BINDING_UNAVAILABLE$"):
        run(tmp_path, world)
    assert world.calls == []


def test_api_acceptance_is_recorded_without_creating_a_security_fail_or_pass(tmp_path):
    world = World(); world.outcome = 200
    draft, _ = run(tmp_path, world)
    assert draft.observations["sampling_accepted_options"].value == VALUES["allowed_sampling_options"]
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_later_generation_gap_preserves_earlier_verified_observations(tmp_path):
    world = World(); world.outcome = 200
    def mutate(role, value, after):
        if role == "engine" and len(world.execs) >= 7:
            value["metadata"]["uid"] = "replacement"
    world.mutate = mutate
    draft, _ = run(tmp_path, world)
    assert draft.observations["sampling_options_tested"].value == ["temperature"]
    assert draft.observations["sampling_accepted_options"].value == ["temperature"]
    assert draft.observations["sampling_challenge_outcomes"].value == {"temperature": "200"}
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN


def test_credentials_travel_in_stdin_and_never_in_binding_report():
    world = World()
    transport = kube.KubernetesHTTP(world.target, env(), duration=30, runner=world.runner)
    result = transport("kubernetes-probe", [{"id": "sampling-before", "method": "POST", "url": "http://127.0.0.1:8000/v1/completions",
        "headers": {"Authorization": PRIVATE}, "body": {"model": "fixture-model", "prompt": "EXAMPLE", "max_tokens": 1}}], 1)
    assert result[0]["outcome"] == 200
    assert world.execs[0]["headers"]["Authorization"] == PRIVATE
    assert PRIVATE not in json.dumps(transport.report())


def test_exhausted_whole_run_budget_never_dispatches_kubectl():
    world = World()
    transport = kube.KubernetesHTTP(world.target, env(), duration=30, runner=world.runner)
    transport.deadline = 0
    with pytest.raises(kube.KubernetesBindingError):
        transport("kubernetes-probe", [{"id": "sampling-before", "method": "GET", "url": "http://127.0.0.1:8000/health"}], 1)
    assert world.calls == []


@pytest.mark.parametrize("mode", ["unknown_setting", "mutating_setting", "malformed_target"])
def test_configuration_errors_do_not_echo_raw_values_or_dispatch(tmp_path, mode):
    world = World()
    path = tmp_path / "target.json"
    path.write_text(PRIVATE if mode == "malformed_target" else json.dumps(world.target), encoding="utf-8")
    settings = {**cfg(), "target": str(path)}
    if mode == "unknown_setting": settings["private_request"] = PRIVATE
    if mode == "mutating_setting": settings["allow_mutating_routes"] = True
    with pytest.raises(ValueError, match="^KUBERNETES_SAMPLING_CONFIGURATION_INVALID$"):
        KubernetesSamplingExecutor(settings, runner=world.runner).run(check(), env(), VALUES, VllmAdapter(), tmp_path / "work")
    assert world.calls == []


def test_product_cli_stores_exact_binding_judges_reports_and_verifies(tmp_path, monkeypatch):
    world = World()
    monkeypatch.setattr(kube, "capture_pipes", world.runner)
    pack, selected_env = baseline_pack(), env()
    pol = policy(pack.ref(), scope={"include": ["REQ-10"]}, values={"REQ-10": VALUES})
    store_path = tmp_path / "store"
    store = Store(store_path); store.put_many([selected_env, pol])
    plan = select(pack, [selected_env], pol); store.put(plan)
    path = tmp_path / "target.json"; path.write_text(json.dumps(world.target), encoding="utf-8")
    config = tmp_path / "guard.toml"
    config.write_text('[executors]\nsampling_validation = "kubernetes"\n[adapter]\nname = "vllm"\n'
        '[sampling_validation]\n' + '\n'.join(f'{k} = {json.dumps(v)}' for k, v in {**cfg(), "target": str(path)}.items()), encoding="utf-8")
    cli = CliRunner()
    pack_path = Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE"
    for command in (["run", "--plan", plan.id, "--pack", str(pack_path)],
                    ["judge", "--plan", plan.id, "--pack", str(pack_path)],
                    ["report", "--plan", plan.id, "--pack", str(pack_path), "--json", str(tmp_path / "report.json")], ["verify"]):
        result = cli.invoke(app, [*command, "--config", str(config), "--store", str(store_path)])
        assert result.exit_code == 0, result.output + repr(result.exception)
    row, = [r for r in json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["by_check"] if r["check_id"] == "REQ-10"]
    assert {axis: row["single"][axis] for axis in ("security", "functional", "performance")} == {
        "security": "UNKNOWN", "functional": "PASS", "performance": "UNKNOWN"}
    assert row["single"]["verification_level"] == "EXAMPLE"
    stored_result, = store.find_latest("RES", lambda r: True)
    assert row["single"]["result"] == stored_result.ref().model_dump(mode="json")
    evd, = store.find_latest("EVD", lambda e: True)
    assert evd.data_origin == DataOrigin.EXAMPLE and evd.execution.executor.version == "0.2.1"
    assert store.verify() == []


def test_existing_probe_source_runs_through_real_stdin_and_owned_http_socket(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            invalid = body.get("temperature", 0) < 0 or body.get("top_p", 1) <= 0 or body.get("min_tokens", 0) > body["max_tokens"]
            self.send_response(400 if invalid else 200); self.end_headers()
            self.wfile.write(json.dumps({"model": "fixture-model", "choices": [{"text": PRIVATE, "finish_reason": "length"}],
                                       "usage": {"completion_tokens": 1}}).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        world = World(); world.actual_probe = True
        world.target["engine"]["trusted_url"] = f"http://127.0.0.1:{server.server_port}"
        draft, _ = run(tmp_path, world)
        assert len(world.execs) == 16 and judge(draft).verdicts["functional"] == Verdict.PASS
        assert PRIVATE not in draft.artifacts[0].path.read_text(encoding="utf-8")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_remote_probe_terminates_itself_while_http_body_is_still_open():
    release = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"partial"); self.wfile.flush()
            release.wait(5)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        transport = kube.KubernetesHTTP(inputs(), env(), duration=30)
        packet = json.dumps({"timeout": 5, "requests": [{"id": "sampling-before", "method": "GET",
                            "url": f"http://127.0.0.1:{server.server_port}/health", "want_body": True}]}).encode()
        result = capture_pipes([sys.executable, "-c", transport.source, "0.2"], input_bytes=packet,
                               timeout=3, env_extra={}, max_stdout_bytes=4096, max_stderr_bytes=4096)
        assert result["returncode"] == 124 and result["capture"]["complete"]
        assert result["stdout_bytes"] == b""
    finally:
        release.set(); server.shutdown(); server.server_close(); thread.join(timeout=2)
