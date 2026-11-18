"""No cluster: exercise the real command orchestration with a fake transport."""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import subprocess
import pytest

from tests.test_s2_exec import source
from tools.tetragon_exec_trial import ExecTrial
from tools.tetragon_exec_probe import PROBE, EXEMPT
from tools.tetragon_exec_demo import ExecDemo
from tools.tetragon_public import public_snapshot
from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.exec_verification import reassess_execution
from serving_runtime.enforcement.tetragon import render_manifests, comparable_spec
from tools.tetragon_exec_trial import digest
from serving_runtime.operations.safety import evaluate

IMAGE = "example/probe@sha256:" + "a" * 64


class Fake:
    def __init__(self, missing_event=False, fail=None):
        self.commands = []
        self.mode = "monitor"
        self.missing_event = missing_event
        self.fail = fail
        self.ns = "test"
        self.policy = None
        self.probe = {}
        self.pod = {
            "metadata": {"uid": "example-pod", "labels": source()["engine"]["labels"]},
            "spec": {"nodeName": "example-node"},
            "status": {
                "containerStatuses": [
                    {
                        "name": "engine",
                        "restartCount": 0,
                        "containerID": "example-container",
                        "state": {"running": {"startedAt": "example"}},
                    }
                ]
            },
        }

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        text = " ".join(command)

        def result(value="", code=0):
            return subprocess.CompletedProcess(
                command, code, json.dumps(value) if not isinstance(value, str) else value, ""
            )

        if self.fail and self.fail in text:
            return result("", 1)
        if "create" in command:
            resource = json.loads(Path(command[-1]).read_text())
            if resource["kind"] == "Namespace":
                self.ns = resource["metadata"]["name"]
            if resource["kind"] == "TracingPolicyNamespaced":
                self.policy = resource
            return result()
        if "set-mode" in command:
            self.mode = command[-1]
            return result()
        if "tracingpolicy" in command and "list" in command:
            return result(f"ID NAME STATE NAMESPACE MODE\n7 runtime-tg-exec-01 enabled {self.ns} {self.mode}\n")
        if "tracingpolicynamespaced" in command:
            return result(self.policy)
        if "get" in command and "pods" in command:
            return result({"items": [{"metadata": {"name": "agent"}, "spec": {"nodeName": "example-node"}}]})
        if "get" in command and "pod" in command:
            return result(self.pod)
        if "uname" in command:
            return result("test-kernel")
        if PROBE in command:
            denied = self.mode == "enforce"
            self.probe = {
                "alive": True,
                "pid": 42,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "cases": [
                    {
                        "name": n,
                        "errno": 13 if denied and n not in {"allowed", "os_system"} else None,
                        "returncode": None
                        if denied and n not in {"allowed", "os_system"}
                        else 127
                        if denied and n == "os_system"
                        else 0,
                    }
                    for n in ("absolute", "path", "bash", "allowed", "os_system")
                ],
            }
            return result(self.probe)
        if EXEMPT in command:
            return result({"returncode": 0})
        if "logs" in command:
            if self.missing_event:
                return result()
            return result(
                {
                    "time": self.probe["started_at"],
                    "process_lsm": {
                        "function_name": "bprm_check_security",
                        "policy_name": "runtime-tg-exec-01",
                        "action": "KPROBE_ACTION_OVERRIDE",
                        "process": {"pod": {"uid": self.pod["metadata"]["uid"]}},
                        "args": [{"linux_binprm_arg": {"path": "/bin/sh"}}],
                    },
                }
            )
        return result()


def trial(tmp_path, fake):
    return ExecTrial("test", tmp_path / "trial", source(), IMAGE, runner=fake)


def test_trial_all_cases_cleanup_and_reassessment(tmp_path):
    fake = Fake()
    report = trial(tmp_path, fake).execute(allow_isolated_trials=True)
    assert report["verification"]["status"] == "passed"
    assert report["data_origin"] == "synthetic"
    assert report["probe"]["cases"][0]["errno"] == 13
    assert report["monitor_probe"]["cases"][0]["returncode"] == 0
    assert report["exempt"]["returncode"] == 0
    assert report["pod_before"] == report["pod_after"]
    assert report["cleanup"] == {"monitor_restored": True, "namespace_deleted": True}
    assert fake.mode == "monitor" and any("delete" in c and "namespace" in c for c in fake.commands)
    assert all(c[1:3] == ["--context", "test"] for c in fake.commands)
    assert not any(c[-2:] == ["--", "sh"] for c in fake.commands)
    safe = public_snapshot({"verifications": [report]})["verifications"][0]
    assert reassess_execution(safe)["verification"]["status"] == "passed"
    assert "stdout" not in json.dumps(safe)


@pytest.mark.parametrize("change", ["errno", "event", "uid", "baseline", "exempt", "restart", "step", "cleanup"])
def test_proof_mutations_fail(tmp_path, change):
    report = trial(tmp_path, Fake()).execute(allow_isolated_trials=True)
    assert report["verification"]["status"] == "passed"
    if change == "errno":
        report["probe"]["cases"][0]["errno"] = 2
    if change == "event":
        report["events"] = []
    if change == "uid":
        report["events"][0]["pod_uid"] = "other"
    if change == "baseline":
        report["monitor_probe"]["cases"][0]["returncode"] = 1
    if change == "exempt":
        report["exempt"]["returncode"] = 1
    if change == "restart":
        report["pod_after"]["containers"][0]["restarts"] = 1
    if change == "step":
        report["steps"][0]["after"]["id"] = "8"
    if change == "cleanup":
        report["cleanup"]["monitor_restored"] = False
    assert reassess_execution(report)["verification"]["status"] == "failed"


@pytest.mark.parametrize("failure", ["wait", "exec probe", "logs", "set-mode"])
def test_trial_failure_still_restores_and_deletes(tmp_path, failure):
    fake = Fake(fail=failure)
    report = trial(tmp_path, fake).execute(allow_isolated_trials=True)
    assert report["verification"]["status"] == "failed"
    assert any("delete" in c and "namespace" in c for c in fake.commands)
    assert (tmp_path / "trial/result.verification.json").exists()


def test_no_confirmation_no_commands(tmp_path):
    fake = Fake()
    with pytest.raises(ValueError):
        trial(tmp_path, fake).execute()
    assert fake.commands == []


def ready_inputs(tmp_path):
    fake = Fake()
    report = trial(tmp_path, fake).execute(allow_isolated_trials=True)
    # Trusted real-proof shape is simulated here; this test does not perform a live trial.
    report["data_origin"] = "real"
    intent = parse_intent(source())
    fake.ns = intent.namespace
    fake.policy = render_manifests(intent)["tetragon-tg-exec-01.json"]
    sha = digest(comparable_spec(fake.policy["spec"]))
    report["policy_spec_sha256"] = sha
    at = datetime.now(timezone.utc)
    status = {
        "context": "test",
        "fresh": True,
        "state": "ok",
        "generated_at": at.isoformat(),
        "policies": [
            {
                "name": "runtime-tg-exec-01",
                "namespace": intent.namespace,
                "node": "example-node",
                "state": "enabled",
                "mode": "monitor",
                "id": "7",
                "method": "lsm_bprm_check",
                "spec_sha256": sha,
            }
        ],
        "capabilities": [{"node": "example-node", "kernel": "test-kernel", "unknown": ["lsm_bprm_check"]}],
        "verifications": [report],
        "monitoring": {
            "state": "healthy",
            **dict.fromkeys(("policies_verified", "writer_verified", "identities_verified", "events_verified"), True),
        },
    }
    window = {
        "policy": "runtime-tg-exec-01",
        "namespace": intent.namespace,
        "node": "example-node",
        "policy_id": "7",
        "method": "lsm_bprm_check",
        "spec_sha256": sha,
        "kernel": "test-kernel",
        "mode": "monitor",
        "since": (at - timedelta(seconds=310)).isoformat(),
        "until": at.isoformat(),
        "continuous": True,
        "event_count": 0,
        "false_positive_count": 0,
        "events_sha256": "b" * 64,
    }
    return fake, intent, status, {"windows": [window]}


@pytest.mark.parametrize("missing", [False, True])
def test_demo_complete_loop_and_missing_event_remains_attempt(tmp_path, missing):
    fake, intent, status, review = ready_inputs(tmp_path)
    fake.missing_event = missing
    checks = evaluate(status, "runtime-tg-exec-01", intent, review, 300)
    assert all(checks["checks"].values())
    demo = ExecDemo(
        {"context": "test", "pod": "engine", "output": tmp_path / "demo"},
        intent,
        status,
        review,
        runner=fake,
        infer=lambda: 200,
    )
    report = demo.execute(allow_isolated_trials=True, allow_engine_pod=True, monitor_seconds=300)
    assert report["result"] == ("시도 기록만" if missing else "거부 확인")
    assert fake.mode == "monitor"
    assert "실제 취약점 공격이 아니라" in (tmp_path / "demo/result.md").read_text()


@pytest.mark.parametrize("check", ["policy", "evidence", "capability", "monitor_period", "collection", "allowed_mode"])
def test_each_gate_blocks_demo_before_mutation(tmp_path, check):
    fake, intent, status, review = ready_inputs(tmp_path)
    if check == "policy":
        status["policies"][0]["state"] = "disabled"
    if check == "evidence":
        status["verifications"] = []
    if check == "capability":
        status["capabilities"][0]["unavailable"] = ["lsm_bprm_check"]
    if check == "monitor_period":
        review["windows"][0]["false_positive_count"] = 1
    if check == "collection":
        status["monitoring"]["state"] = "error"
    if check == "allowed_mode":
        intent = parse_intent(
            {k: v for k, v in source().items() if k not in {"tetragon_allowed_modes", "tetragon_enforce_methods"}}
        )
    fake.commands.clear()
    demo = ExecDemo(
        {"context": "test", "pod": "engine", "output": tmp_path / "demo"},
        intent,
        status,
        review,
        runner=fake,
        infer=lambda: 200,
    )
    with pytest.raises(ValueError):
        demo.execute(allow_isolated_trials=True, allow_engine_pod=True, monitor_seconds=300)
    assert fake.commands == []


def test_no_exemptions_trial_keeps_exact_candidate_and_public_proof(tmp_path):
    doc = source()
    doc["exec_deny_exempt_callers"] = []
    fake = Fake()
    report = ExecTrial("test", tmp_path / "empty-trial", doc, IMAGE, runner=fake).execute(allow_isolated_trials=True)
    assert report["verification"]["status"] == "passed"
    assert not any(EXEMPT in command for command in fake.commands)
    expected = render_manifests(parse_intent(doc))["tetragon-tg-exec-01.json"]["spec"]
    assert report["policy_spec_sha256"] == digest(comparable_spec(expected))
    assert report["exemption_test"]["status"] == "not_configured"
    assert "exempt_allowed" not in report["facts"]
    safe = public_snapshot({"verifications": [report]})["verifications"][0]
    assert reassess_execution(safe)["verification"]["status"] == "passed"
    safe["empty_exemption_candidate"]["deny_paths"][0]["path"] = "/other"
    assert reassess_execution(safe)["verification"]["status"] == "failed"


def test_trial_waits_for_delayed_export_without_widening_probe_time(tmp_path, monkeypatch):
    fake = Fake()
    calls = []

    def delayed(command, **kwargs):
        if "logs" in command:
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 0, "", "")
        return fake(command, **kwargs)

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    report = ExecTrial("test", tmp_path / "delayed-trial", source(), IMAGE, runner=delayed).execute(
        allow_isolated_trials=True
    )
    assert report["verification"]["status"] == "passed"
    assert len(calls) == 2
    assert calls[0] == calls[1]


@pytest.mark.parametrize("stale", [False, True])
def test_demo_reads_collector_timestamp_instead_of_expect_fresh_field(tmp_path, stale):
    fake, intent, snapshot, review = ready_inputs(tmp_path)
    raw = dict(snapshot)
    raw.pop("fresh")
    raw.update(schema_version="runtime-tetragon-state/1.0", transitions=[])
    if stale:
        raw["generated_at"] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    path = tmp_path / "collector.json"
    path.write_text(json.dumps(raw))
    fake.commands.clear()
    demo = ExecDemo(
        {"context": "test", "pod": "probe", "output": str(tmp_path / "real-format-demo"), "status_path": str(path)},
        intent,
        snapshot,
        review,
        runner=fake,
        infer=lambda: 200,
    )
    report = demo.execute(allow_isolated_trials=True, allow_engine_pod=True, monitor_seconds=300)
    assert report["denial_confirmed"] is not stale
    if stale:
        assert not any("set-mode" in c for c in fake.commands)


def test_three_exercises_share_one_checked_activation(tmp_path):
    fake, intent, status, review = ready_inputs(tmp_path)
    demo = ExecDemo(
        {"context": "test", "pod": "engine", "output": tmp_path / "session"},
        intent,
        status,
        review,
        runner=fake,
        infer=lambda: 200,
    )
    demo.monitor_seconds = 300
    transition = {}
    demo.activate(transition)
    for _ in range(3):
        report = {"transition": transition["transition"]}
        demo.exercise(report, already_enforced=True)
        assert report["denial_confirmed"]
    fake.mode = "monitor"
    with pytest.raises(ValueError, match="identity or mode"):
        demo.exercise({"transition": transition["transition"]}, already_enforced=True)


def test_actual_error_message_uses_probe_eperm_without_changing_action_claim(tmp_path):
    class EpermFake(Fake):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if PROBE in command and self.mode == "enforce":
                value = json.loads(result.stdout)
                for row in value["cases"]:
                    if row.get("errno") == 13:
                        row["errno"] = 1
                result.stdout = json.dumps(value)
            return result

    fake, intent, status, review = ready_inputs(tmp_path)
    live = EpermFake()
    live.ns, live.policy = fake.ns, fake.policy
    demo = ExecDemo(
        {"context": "test", "pod": "engine", "output": tmp_path / "eperm"},
        intent,
        status,
        review,
        runner=live,
        infer=lambda: 200,
    )
    report = demo.execute(allow_isolated_trials=True, allow_engine_pod=True, monitor_seconds=300)
    assert report["denial_confirmed"]
    assert report["actual_errno"] == [1]
    assert report["event_summary"] == "실행 거부 확인: EPERM (errno 1)"
    assert "EPERM (errno 1)" in (tmp_path / "eperm/result.md").read_text()


def test_strict_demo_requires_eperm_and_reports_pod_scope(tmp_path):
    fake, intent, status, review = ready_inputs(tmp_path)
    demo = ExecDemo(
        {"context": "test", "pod": "probe", "output": tmp_path / "strict", "required_errno": 1},
        intent,
        status,
        review,
        runner=fake,
        infer=lambda: 200,
    )
    report = demo.execute(allow_isolated_trials=True, allow_engine_pod=True, monitor_seconds=300)
    assert report["actual_errno"] == [13] and not report["denial_confirmed"]
    assert report["result"] == "시도 기록만" and fake.mode == "monitor"
    text = (tmp_path / "strict/result.md").read_text()
    assert "엔진 Pod 전체" in text and "/tmp" in text and "기동 중에는 monitor" in text


def test_normal_inference_response_requires_model_and_generated_answer(monkeypatch, tmp_path):
    from tools import tetragon_exec_demo as tool

    token = tmp_path / "token"
    token.write_text("local-fixture-token")
    token.chmod(0o600)

    class Response:
        status = 200
        body = {
            "object": "chat.completion",
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "ready"}}],
        }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return json.dumps(self.body).encode()

    response = Response()
    monkeypatch.setattr(tool, "urlopen", lambda *a, **kw: response)
    body = json.dumps({"model": "test-model"}).encode()
    assert tool.normal_request("http://127.0.0.1:8080/v1/chat/completions", body, token) == 200
    response.body["model"] = "wrong-model"
    with pytest.raises(ValueError, match="body/model/generated"):
        tool.normal_request("http://127.0.0.1:8080/v1/chat/completions", body, token)
    response.body["model"] = "test-model"
    response.body["choices"][0]["message"]["content"] = ""
    with pytest.raises(ValueError, match="body/model/generated"):
        tool.normal_request("http://127.0.0.1:8080/v1/chat/completions", body, token)
