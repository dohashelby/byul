"""SUP-04 API 소비 제안의 EXAMPLE 회귀. 실제 cluster/노드 관계는 검증하지 않는다."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.executors import registry
from guard.executors.container_config import ContainerConfigExecutor
from guard.executors.running_image_approval import (
    analyze_kubernetes_running_image, collect_kubernetes_running_image_approval,
)
from guard.models import canonical_json
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.store import Store
from tests.builders import baseline_pack, environment, policy, T0


PIN = "example.invalid/engine@sha256:" + "a" * 64
OTHER = "example.invalid/engine@sha256:" + "b" * 64
PRIVATE = "NEVER_PERSIST_PRIVATE_SOURCE"


def inputs():
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": PIN}, model={"id": "m"})
    roles = {}
    for index, role in enumerate(("engine", "probe"), 1):
        roles[role] = {
            "namespace": role, "namespace_uid": f"00000000-0000-0000-0000-{index:012d}",
            "pod": role + "-pod", "pod_uid": f"10000000-0000-0000-0000-{index:012d}",
            "container": role, "container_id": "containerd://" + str(index) * 64,
            "image": PIN if role == "engine" else OTHER, "image_id": "sha256:" + str(index) * 64,
            "restart_count": 0, "node": "example-node", "pod_ip": f"127.0.0.{index}",
            "started_at": "2026-10-29T00:00:00Z",
        }
    target = {
        "schema": "guard-kubernetes-request-binding-target/1", "lab": True,
        "topology": "kubernetes-owned-direct", "environment_ref": env.ref().model_dump(mode="json"),
        "engine": {"image_digest": PIN, "trusted_url": "http://127.0.0.1:8000"},
        "origins": {"trusted": {"network": "kubernetes-probe"}},
        "kubernetes": {"context": "example-context", "run_id": "example-owned-run", **roles},
    }
    return env, target


def snapshot(target, role="engine"):
    value = target["kubernetes"][role]
    return {
        "namespace": {"kind": "Namespace", "metadata": {
            "name": value["namespace"], "uid": value["namespace_uid"], "annotations": {"secret": PRIVATE}}},
        "pod": {"kind": "Pod", "metadata": {"name": value["pod"], "namespace": value["namespace"],
            "uid": value["pod_uid"], "labels": {"guard.run": target["kubernetes"]["run_id"]},
            "annotations": {"secret": PRIVATE}},
            "spec": {"nodeName": value["node"], "containers": [{"name": value["container"],
                "image": value["image"], "env": [{"name": "TOKEN", "value": PRIVATE}],
                "command": [PRIVATE], "args": [PRIVATE]}]},
            "status": {"phase": "Running", "podIP": value["pod_ip"],
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{"name": value["container"], "containerID": value["container_id"],
                    "imageID": value["image_id"], "restartCount": value["restart_count"], "ready": True,
                    "state": {"running": {"startedAt": value["started_at"]}}}]}},
    }


def collect(env, target, before, after, approved):
    ctx = SimpleNamespace(env=env, target=target, check=baseline_pack().checks["SUP-04"],
                          policy_values={"approved_image_digests": approved})
    return collect_kubernetes_running_image_approval(ctx, before, after)


@pytest.mark.parametrize("image_id", ["sha256:" + "1" * 64, PIN, "docker-pullable://" + PIN])
@pytest.mark.parametrize("approved", [[PIN], [OTHER], []])
def test_api_image_id_never_creates_runtime_approval_or_confirmed_violation(image_id, approved):
    env, target = inputs()
    target["kubernetes"]["engine"]["image_id"] = image_id
    before = snapshot(target)
    raw = deepcopy(before)
    observations, normal, challenges, summary = collect(env, target, before, deepcopy(before), approved)
    analysis = summary["kubernetes_image"]
    assert analysis["api_identity_stable"] is True
    assert analysis["identity_before"] == analysis["identity_after"]
    assert analysis["runtime_image_relation_verified"] is False
    assert analysis["diagnostic_codes"] == ["RUNNING_IMAGE_RUNTIME_RELATION_UNAVAILABLE"]
    assert observations["running_image_identity_verified"] == {"value": False, "collected": True}
    for key in ("running_image_repo_digest", "running_image_approved", "running_image_unapproved"):
        assert observations[key] == {"value": None, "collected": False}
    assert not normal["attempted"] and normal["succeeded"] is None
    assert all(not c["attempted"] for c in challenges)
    assert PRIVATE not in json.dumps(summary) and before == raw


@pytest.mark.parametrize("field,value", [
    ("namespace_uid", "90000000-0000-0000-0000-000000000001"),
    ("pod_uid", "90000000-0000-0000-0000-000000000002"),
    ("container_id", "containerd://" + "f" * 64), ("image_id", OTHER),
    ("restart_count", 1), ("started_at", "2026-10-29T00:00:01Z"),
    ("node", "other-node"), ("pod_ip", "127.0.0.9"), ("image", OTHER),
])
def test_generation_replacement_or_restart_does_not_bind_two_sources(field, value):
    env, target = inputs()
    other = deepcopy(target)
    other["kubernetes"]["engine"][field] = value
    analysis = analyze_kubernetes_running_image(env, target, snapshot(target), snapshot(other))
    assert not analysis["api_identity_stable"]
    assert analysis["identity_before"] is not None and analysis["identity_after"] is None
    assert analysis["diagnostic_codes"] == ["RUNNING_IMAGE_SOURCE_CHANGED"]
    assert PRIVATE not in json.dumps(analysis)


@pytest.mark.parametrize("damage", ["missing-ref", "foreign-ref", "extra-ref", "unsealed", "context", "run", "schema", "image"])
def test_invalid_environment_or_target_never_borrows_another_binding(damage):
    env, target = inputs()
    before = snapshot(target)
    if damage == "missing-ref":
        target.pop("environment_ref")
    elif damage == "foreign-ref":
        target["environment_ref"]["revision"] += 1
    elif damage == "extra-ref":
        target["environment_ref"]["extra"] = PRIVATE
    elif damage == "unsealed":
        env = env.model_copy(update={"content_sha256": "0" * 64})
    elif damage in {"context", "run"}:
        target["kubernetes"]["context" if damage == "context" else "run_id"] = PRIVATE + "\n"
    elif damage == "schema":
        target["schema"] = "guard-kubernetes-lifecycle-target/1"
    else:
        target["engine"]["image_digest"] = OTHER
    analysis = analyze_kubernetes_running_image(env, target, before, before)
    assert analysis["diagnostic_codes"] == ["RUNNING_IMAGE_TARGET_UNVERIFIED"]
    assert analysis["identity_before"] is None and not analysis["api_identity_stable"]


@pytest.mark.parametrize("damage", ["owner", "namespace-name", "namespace-kind", "pod-kind", "ready", "duplicate", "missing", "untrusted-relation"])
def test_missing_or_malformed_metadata_remains_unconfirmed(damage):
    env, target = inputs()
    before = snapshot(target)
    if damage == "owner":
        before["pod"]["metadata"]["labels"]["guard.run"] = "another-run"
    elif damage == "namespace-name":
        before["namespace"]["metadata"]["name"] = "another-namespace"
    elif damage == "namespace-kind":
        before["namespace"]["kind"] = "Secret"
    elif damage == "pod-kind":
        before["pod"]["kind"] = "Secret"
    elif damage == "ready":
        before["pod"]["status"]["conditions"] = []
    elif damage == "duplicate":
        before["pod"]["status"]["containerStatuses"] *= 2
    elif damage == "missing":
        before = {}
    else:
        before["image_relation"] = {"verified": True, "repo_digests": [PIN], "source": PRIVATE}
    analysis = analyze_kubernetes_running_image(env, target, before, before)
    assert not analysis["runtime_image_relation_verified"] and not analysis["producer_authenticated"]
    assert analysis["diagnostic_codes"] == ["RUNNING_IMAGE_RUNTIME_RELATION_UNAVAILABLE" if damage == "untrusted-relation"
                                           else "RUNNING_IMAGE_IDENTITY_UNVERIFIED"]
    assert PRIVATE not in json.dumps(analysis)


@pytest.mark.parametrize("gap", [None, "timeout", "incomplete"])
def test_existing_common_kubectl_response_parser_feeds_the_dedicated_consumer(gap):
    env, target = inputs()
    target["model_mapping"] = {"repository_id": env.model.id, "revision": env.model.revision, "api_model": "alias"}
    calls = []
    def runner(argv, **kwargs):
        assert "exec" not in argv and argv[1] == "--context=example-context"
        assert kwargs["max_stdout_bytes"] == 262144 and kwargs["max_stderr_bytes"] == 4096
        calls.append(argv)
        if gap == "timeout":
            raise TimeoutError(PRIVATE)
        if "namespaces" in argv:
            value = {"kind": "List", "items": [snapshot(target, r)["namespace"] for r in ("engine", "probe")]}
        else:
            role = "engine" if "engine-pod" in argv else "probe"
            value = snapshot(target, role)["pod"]
        return {"returncode": 0, "stdout_bytes": json.dumps(value).encode(), "stderr_bytes": PRIVATE.encode(),
                "capture": {"complete": gap != "incomplete"}}
    transport = KubernetesHTTP(target, env, duration=10, runner=runner, profile=REQUEST_BINDING_PROFILE)
    pairs = []
    for _ in range(2):
        try:
            pods = transport._verify()
            pairs.append({"pod": pods["engine"], "namespace": snapshot(target)["namespace"]})
        except RuntimeError:
            pairs.append({})
    analysis = analyze_kubernetes_running_image(env, target, *pairs)
    assert analysis["api_identity_stable"] is (gap is None)
    assert len(calls) == (6 if gap is None else 2)
    assert PRIVATE not in json.dumps(analysis)


@pytest.mark.parametrize("approved", [[PIN], [OTHER], [], None])
def test_proposed_consumption_through_existing_product_cli_and_exact_saved_refs(tmp_path, monkeypatch, approved):
    """수집 응답만 주입한다. production registry 연결 완료나 실제 실행으로 집계하지 않는다."""
    env, target = inputs()
    pack = baseline_pack()
    values = {} if approved is None else {"approved_image_digests": approved}
    pol = policy(pack.ref(), scope={"include": ["SUP-04"], "exclude": []}, values={"SUP-04": values})
    env_path, pol_path, target_path = [tmp_path / p for p in ("env.json", "policy.json", "target.json")]
    env_path.write_bytes(canonical_json(env.to_dict()))
    pol_path.write_bytes(canonical_json(pol.to_dict()))
    target_path.write_text(json.dumps(target), encoding="utf-8")
    config = tmp_path / "guard.toml"
    config.write_text('[executors]\ncontainer_config="real"\n[adapter]\nname="vllm"\n'
                      '[container_config]\ntarget=' + json.dumps(target_path.as_posix()) + '\n', encoding="utf-8")
    calls = []
    def forbidden(*args, **kwargs):
        pytest.fail("no network, inference, Docker or replay collection")
    def consume(self, ctx):
        calls.append(ctx.check.id)
        return collect_kubernetes_running_image_approval(ctx, snapshot(target), snapshot(target))
    monkeypatch.setattr(ContainerConfigExecutor, "collect", consume)
    monkeypatch.setitem(registry.EXECUTORS, "container_config:real", lambda raw:
                        ContainerConfigExecutor(raw["container_config"], container_probe=forbidden))
    runner = CliRunner()
    store_path = tmp_path / "store"
    common = ["--config", str(config), "--store", str(store_path)]
    def cli(args):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, (args, result.stdout, repr(result.exception))
        assert PRIVATE not in result.stdout
        return result.stdout
    plan_ref = cli(["select", "--pack", str(pack.path), "--env", str(env_path), "--policy", str(pol_path), *common]).strip().splitlines()[-1]
    cli(["run", "--plan", plan_ref, "--pack", str(pack.path), *common])
    cli(["judge", "--plan", plan_ref, "--pack", str(pack.path), "--as-of", T0.isoformat(), *common])
    cli(["report", "--plan", plan_ref, "--pack", str(pack.path), "--as-of", T0.isoformat(), "--json", str(tmp_path / "report.json"), *common])
    cli(["verify", *common])
    store = Store(store_path)
    evd, = store.find_latest("EVD", lambda r: True)
    res, = store.find_latest("RES", lambda r: True)
    rpt, = store.find_latest("RPT", lambda r: True)
    assert res.verdicts.security.value == "UNKNOWN" and evd.data_origin.value == "EXAMPLE"
    assert evd.environment == env.ref() and evd.ref() in rpt.inputs_manifest and res.ref() in rpt.inputs_manifest
    assert len(calls) == (0 if approved is None else 1)
    assert evd.execution.status.value == ("NOT_RUN" if approved is None else "COMPLETED")
    if approved is not None:
        assert evd.observations["running_image_unapproved"].collected is False
        for artifact in evd.artifacts:
            assert PRIVATE.encode() not in (store.root / artifact.path).read_bytes()
    bundle, restored_path = tmp_path / "bundle.zip", tmp_path / "restored"
    cli(["evidence", "export", "--store", str(store_path), "--output", str(bundle)])
    cli(["evidence", "restore", "--bundle", str(bundle), "--destination", str(restored_path)])
    target_path.unlink()
    monkeypatch.setattr(ContainerConfigExecutor, "collect", forbidden)
    cli(["judge", "--plan", plan_ref, "--pack", str(pack.path), "--as-of", T0.isoformat(), "--config", str(config), "--store", str(restored_path)])
    cli(["verify", "--config", str(config), "--store", str(restored_path)])
    restored = Store(restored_path)
    replayed, = restored.find_latest("RES", lambda r: True)
    assert replayed.ref() == res.ref() and restored.resolve(evd.ref()).to_dict() == evd.to_dict()
    assert restored.resolve(rpt.ref()).to_dict() == rpt.to_dict()
