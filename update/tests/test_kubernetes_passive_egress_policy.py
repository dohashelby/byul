"""Exercise the bound API and status producer boundary without needing a cluster."""

import json

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.run import _normalize
from guard.executors.kubernetes_passive_egress_policy import (
    CILIUM_AGENT_IMAGE, CILIUM_AGENT_IMAGE_ID, KubernetesPassiveEgressPolicyExecutor, capture_cilium_agent_binding,
)
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from tests.builders import baseline_pack, evidence
from tests.test_kubernetes_sampling import World, normal_sampling_inputs

VALUES = {"forbidden_protocol": "tcp", "forbidden_operation": "tcp_connect"}
RESOURCE = "ciliumendpoints.cilium.io"
PRIVATE = "EXAMPLE-do-not-retain-policy-peer"
ATTACHMENT = "a8" * 32 + ":eth0"
AGENT_POD = "cilium-test-agent"
NS_UID = "11111111-1111-1111-1111-111111111111"
DS_UID = "22222222-2222-2222-2222-222222222222"
POD_UID = "33333333-3333-3333-3333-333333333333"
STARTED = "2026-11-01T00:00:00Z"


class Cluster(World):
    def __init__(self, target, fault=None, mode="egress"):
        super().__init__(target)
        self.fault = fault
        self.mode = mode
        self.cep_calls = []
        self.agent_reads = []
        self.policy_reads = []
        self.mutate = self.pod_readback

    def pod_readback(self, role, doc, after):
        if role == "namespaces":
            for item in doc["items"]:
                item["kind"] = "Namespace"
            return
        doc["kind"] = "Pod"
        if role == "engine" and (self.fault == "owner-before" or self.fault == "owner-after" and self.policy_reads):
            doc["metadata"]["uid"] = "other-generation"

    def runner(self, argv, **kwargs):
        assert argv[:2] == ["kubectl", "--context=fixture-context"]
        assert argv[2].startswith("--request-timeout=")
        assert "input_bytes" not in kwargs
        args = argv[3:]
        if args[:4] == ["--namespace=kube-system", "exec", AGENT_POD, "--container=cilium-agent"]:
            assert args[4:11] == ["--", "cilium-dbg", "--host=unix:///var/run/cilium/cilium.sock",
                                  "endpoint", "get", "88", "--output"]
            assert len(args) == 12 and args[11].startswith("jsonpath=")
            for field in ("external-identifiers.cni-attachment-id", "networking.addressing[0].ipv4",
                          "policy.realized.policy-enabled", "policy.realized.policy-revision"):
                assert "{[0].status." + field + "}" in args[11]
            assert all(field not in args[11] for field in ("allowed", "denied", "labels", "l4"))
            assert 0 < kwargs["timeout"] <= 5 and kwargs["max_stdout_bytes"] == 1024
            self.policy_reads.append(args)
            output = {"id": 88, "state": "ready", "connected": True, "attachment": ATTACHMENT,
                      "ipv4": self.target["kubernetes"]["engine"]["pod_ip"], "mode": self.mode, "revision": 27}
            if self.fault == "join-attachment":
                output["attachment"] = "bc" * 32 + ":eth0"
            elif self.fault == "join-ip":
                output["ipv4"] = "127.0.0.93"
            elif self.fault == "join-id":
                output["id"] = 89
            elif self.fault == "agent-not-ready":
                output["state"] = "regenerating"
            elif self.fault == "bad-revision":
                output["revision"] = True
            elif self.fault == "agent-raw-rules":
                output["rules"] = PRIVATE
            return self.reply(output, complete=self.fault != "truncated")
        if RESOURCE in args:
            engine = self.target["kubernetes"]["engine"]
            assert args == [f"--namespace={engine['namespace']}", "get", RESOURCE, engine["pod"], "-o", "json"]
            assert 0 < kwargs["timeout"] <= 5 and kwargs["max_stdout_bytes"] == 262144
            self.cep_calls.append(args)
            owner = {"apiVersion": "v1", "kind": "Pod", "uid": engine["pod_uid"], "name": engine["pod"]}
            metadata = {"name": engine["pod"], "namespace": engine["namespace"], "ownerReferences": [owner],
                        "uid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "resourceVersion": "67241"}
            # Stock Cilium v1.20.3 CEP publisher has no status.policy field.
            status = {"id": 88, "state": "ready", "identity": {"id": 23712, "labels": [PRIVATE]},
                      "external-identifiers": {"cni-attachment-id": ATTACHMENT},
                      "networking": {"addressing": [{"ipv4": engine["pod_ip"]}]}}
            document = {"apiVersion": "cilium.io/v2", "kind": "CiliumEndpoint", "metadata": metadata, "status": status}
            if self.fault == "wrong-cep-owner":
                owner["uid"] = "wrong-owner"
            elif self.fault == "wrong-cep-namespace":
                metadata["namespace"] = "wrong-namespace"
            elif self.fault == "wrong-cep-ip":
                status["networking"]["addressing"][0]["ipv4"] = "127.0.0.93"
            elif self.fault == "unsupported-api":
                document["apiVersion"] = "cilium.io/v2alpha1"
            elif self.fault == "missing-attachment":
                status.pop("external-identifiers")
            return self.reply(document, returncode=int(self.fault == "cep-unavailable"))
        if args[:3] == ["get", "namespace", "kube-system"]:
            assert args == ["get", "namespace", "kube-system", "-o", "json"]
            return self.reply({"kind": "Namespace", "apiVersion": "v1",
                               "metadata": {"name": "kube-system", "uid": NS_UID}})
        if args[0] == "--namespace=kube-system":
            labels = {"k8s-app": "cilium"}
            containers = [{"name": "cilium-agent", "image": CILIUM_AGENT_IMAGE}]
            if args == ["--namespace=kube-system", "get", "daemonset", "cilium", "-o", "json"]:
                return self.reply({"kind": "DaemonSet", "apiVersion": "apps/v1",
                                   "metadata": {"namespace": "kube-system", "name": "cilium", "uid": DS_UID},
                                   "spec": {"selector": {"matchLabels": labels},
                                            "template": {"spec": {"containers": containers}}}})
            assert args == ["--namespace=kube-system", "get", "pod", AGENT_POD, "-o", "json"]
            self.agent_reads.append(args)
            pin = self.target["cilium_agent"]
            owner = {"apiVersion": "apps/v1", "kind": "DaemonSet", "name": "cilium", "uid": DS_UID, "controller": True}
            if self.fault == "wrong-ds-owner":
                owner["uid"] = NS_UID
            metadata = {"namespace": "kube-system", "name": AGENT_POD, "uid": POD_UID,
                        "labels": labels, "ownerReferences": [owner]}
            state = {"name": "cilium-agent", "imageID": CILIUM_AGENT_IMAGE_ID, "containerID": pin["container_id"],
                     "ready": True, "restartCount": int(self.fault == "agent-restarted" and bool(self.policy_reads)),
                     "state": {"running": {"startedAt": STARTED}}}
            return self.reply({"kind": "Pod", "apiVersion": "v1", "metadata": metadata,
                               "spec": {"nodeName": pin["node"], "hostNetwork": True, "containers": containers},
                               "status": {"phase": "Running", "containerStatuses": [state],
                                          "conditions": [{"type": "Ready", "status": "True"}]}})
        assert "get" in argv and "exec" not in argv
        return super().runner(argv, **kwargs)

    @staticmethod
    def reply(document, *, returncode=0, complete=True):
        return {"returncode": returncode, "stdout_bytes": canonical_json(document),
                "capture": {"complete": complete}}


def _inputs(tmp_path, version):
    env, target, _, path = normal_sampling_inputs(tmp_path, version)
    target["comparison_config"] = {"engine_args": ["stock-launch-settings"]}
    target["cilium_agent"] = {"namespace_uid": NS_UID, "daemonset_uid": DS_UID, "pod": AGENT_POD,
        "pod_uid": POD_UID, "container_id": "containerd://" + "3" * 64, "image": CILIUM_AGENT_IMAGE,
        "image_id": CILIUM_AGENT_IMAGE_ID, "restart_count": 0, "started_at": STARTED,
        "node": target["kubernetes"]["engine"]["node"]}
    return env, target, path


def _run(tmp_path, version, fault=None, mode="egress"):
    env, target, path = _inputs(tmp_path, version)
    cluster = Cluster(target, fault, mode)
    if fault == "agent-pin-missing":
        del target["cilium_agent"]
    path.write_bytes(canonical_json(target))
    cfg = {"cilium_egress_status_only": fault != "option-disabled", "target": str(path)}
    values = {} if fault == "wrong-policy" else VALUES
    pin = "0" * 64 if fault == "wrong-pin" else sha256_hex(path.read_bytes())
    executor = KubernetesPassiveEgressPolicyExecutor(cfg, runner=cluster.runner, expected_target_sha256=pin)
    check = baseline_pack().checks["PLT-07"]
    if fault in {"wrong-pin", "option-disabled", "wrong-policy", "agent-pin-missing"}:
        with pytest.raises(ValueError, match=r"^PLT07_CILIUM_(TARGET_UNVERIFIED|SCOPE_INVALID|POLICY_INVALID)$"):
            executor.run(check, env, values, VllmAdapter(), tmp_path / "work")
        assert not cluster.calls and not cluster.agent_reads and not cluster.cep_calls and not cluster.policy_reads
        return None, None, None, None
    draft = executor.run(check, env, values, VllmAdapter(), tmp_path / "work")
    blocked_before_cep = fault in {"owner-before", "wrong-ds-owner"}
    blocked_before_debugger = blocked_before_cep or fault in {
        "wrong-cep-owner", "wrong-cep-namespace", "wrong-cep-ip", "unsupported-api", "missing-attachment", "cep-unavailable"}
    assert len(cluster.cep_calls) == int(not blocked_before_cep)
    assert len(cluster.policy_reads) == int(not blocked_before_debugger)
    assert not cluster.execs
    raw = draft.artifacts[0].path.read_text()
    engine = target["kubernetes"]["engine"]
    for value in (PRIVATE, engine["pod"], engine["pod_uid"], engine["pod_ip"], ATTACHMENT, '"67241"', AGENT_POD, POD_UID):
        assert value not in raw
    return draft, json.loads(raw), check, env


@pytest.mark.parametrize(("version", "mode"), [("0.30.0", "egress"), ("0.31.0", "audit-both"), ("0.31.0", "none")])
def test_reports_only_agent_policy_state_with_unknown_egress_result(tmp_path, version, mode):
    draft, artifact, check, env = _run(tmp_path, version, mode=mode)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert not draft.normal_control.attempted
    assert all(not step.attempted for step in draft.challenges)
    state = artifact["observation"]
    assert state["reported_policy_mode"] == mode
    assert state["reported_egress_enforcing"] is (mode == "egress")
    assert state["endpoint_state"] == "ready" and state["reported_policy_revision"] == 27
    assert state["read_started_at"] <= state["read_finished_at"]
    for field in ("endpoint_binding_sha256", "agent_binding_sha256", "owned_snapshot_sha256", "ownership_source_sha256"):
        assert len(state[field]) == 64
    assert artifact["environment_sha256"] == env.ref().sha256
    assert artifact["conditions_sha256"] == draft.comparability.config_sha256
    assert len(artifact["conditions_sha256"]) == 64
    assert {"traffic_trials", "direct_agent_version_readback"} <= set(artifact["missing"])
    assert artifact["gap"] is None
    observations = _normalize(check, draft)["observations"]
    assert observations == {
        "egress_trials": {"value": None, "collected": False},
        "forbidden_egress_successes": {"value": None, "collected": False},
        "egress_scope_complete": {"value": False, "collected": True},
    }
    evd = evidence(check_id=check.id, observations=observations, normal=draft.normal_control.model_dump())
    assert RuleTableJudge().judge(check, evd, check.judge.params, VALUES).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("fault", ["owner-before", "owner-after", "wrong-ds-owner", "agent-restarted", "wrong-cep-owner",
                                   "wrong-cep-namespace", "wrong-cep-ip", "unsupported-api", "missing-attachment",
                                   "cep-unavailable", "join-attachment", "join-ip", "join-id", "agent-not-ready",
                                   "bad-revision", "agent-raw-rules", "truncated"])
def test_requires_exact_owners_and_a_complete_same_endpoint_status(tmp_path, fault):
    draft, artifact, _, _ = _run(tmp_path, "0.31.0", fault)
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["observation"] is None and artifact["gap"] == draft.abort_reason
    assert not draft.observations["forbidden_egress_successes"].collected


@pytest.mark.parametrize("fault", ["wrong-pin", "option-disabled", "wrong-policy", "agent-pin-missing"])
def test_fails_closed_without_dispatch_on_wrong_opt_in_context(tmp_path, fault):
    _run(tmp_path, "0.31.0", fault)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("settings,accepted", [
    ({"max_duration_seconds": 30.5, "kubectl_binary": "example-kubectl"}, True),
    ({"max_duration_seconds": 0}, False),
    ({"kubectl_binary": " "}, False),
])
def test_prepares_saved_cilium_target_without_reading_status(tmp_path, version, settings, accepted):
    env, target, path = _inputs(tmp_path, version)
    raw = canonical_json(target)
    path.write_bytes(raw)
    check = baseline_pack().checks["PLT-07"]

    def no_collection(*args, **kwargs):
        pytest.fail("PLT-07 preparation must not collect agent status")

    executor = KubernetesPassiveEgressPolicyExecutor(
        {"cilium_egress_status_only": True, "target": str(path), **settings}, runner=no_collection)
    if accepted:
        executor.prepared_passive_egress_policy(check, env, VALUES, expected_target_sha256=sha256_hex(raw))
    else:
        with pytest.raises(ValueError, match="^PLT07_CILIUM_TARGET_UNVERIFIED$"):
            executor.prepared_passive_egress_policy(check, env, VALUES, expected_target_sha256=sha256_hex(raw))


def test_captures_named_agent_binding_with_only_kubernetes_api_reads(tmp_path):
    env, target, _ = _inputs(tmp_path, "0.30.0")
    cluster = Cluster(target)
    expected = target["cilium_agent"]
    captured = capture_cilium_agent_binding({key: value for key, value in target.items() if key != "cilium_agent"},
        env, pod=AGENT_POD, duration=12, runner=cluster.runner)
    assert captured == expected
    assert len(cluster.agent_reads) == 2
    assert not cluster.cep_calls and not cluster.policy_reads and not cluster.execs
