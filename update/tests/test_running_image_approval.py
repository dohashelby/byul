"""SUP-04 실행 이미지 부분계약. Docker·HTTP는 모두 대역이며 EXAMPLE이다."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.container_config import ContainerConfigExecutor
from guard.executors.running_image_approval import PROFILE, validate_image_approval_policy
from guard.models import ExecStatus, Verdict
from guard.store import Store
from tests.builders import PACK_DIR, baseline_pack, completion_response, environment, policy


PIN = "example.invalid/vllm-cpu@sha256:" + "a" * 64
OTHER = "example.invalid/vllm-cpu@sha256:" + "c" * 64
IMAGE_ID = "sha256:" + "b" * 64
CONTAINER = "guard-example-engine"
RUN = "example-image-approval"
_DEFAULT_ENV_IMAGE = object()


def _inspect(pin=PIN):
    return {"Id": "d" * 64, "Name": "/" + CONTAINER, "Image": IMAGE_ID,
            "State": {"Running": True, "StartedAt": "2026-10-26T00:00:00Z"},
            "Config": {"Image": pin, "Labels": {"guard.run": RUN},
                       "Env": ["SECRET_TOKEN=NEVER_PERSIST_ENV"], "Cmd": ["NEVER_PERSIST_CMD"]},
            "Mounts": [{"Source": "NEVER_PERSIST_HOST_PATH", "Destination": "/models"}]}


class FakeDocker:
    def __init__(self, *, before=None, after=None, digests=None, inspect_error=False, image_error=False):
        self.before = deepcopy(_inspect() if before is None else before)
        self.after = deepcopy(self.before if after is None else after)
        self.digests = [PIN] if digests is None else digests
        self.inspect_error, self.image_error = inspect_error, image_error
        self.calls = []
        self.inspections = 0

    def inspect_json(self, container, *, timeout):
        self.calls.append(("inspect", container, timeout))
        if self.inspect_error:
            raise OSError("NEVER_PERSIST_DAEMON_ERROR")
        self.inspections += 1
        return deepcopy(self.before if self.inspections == 1 else self.after)

    def image_info(self, image_id, *, timeout):
        self.calls.append(("image_info", image_id, timeout))
        assert image_id == IMAGE_ID  # RepoDigest 문자열 대신 실제 image ID로 조회한다.
        if self.image_error:
            raise OSError("NEVER_PERSIST_IMAGE_ERROR")
        return {"os": "linux", "architecture": "amd64", "digests": self.digests,
                "entrypoint": ["NEVER_PERSIST_IMAGE_ENTRYPOINT"]}

    def exec(self, *args, **kwargs):
        raise AssertionError("supply-image profile must not execute sentinel or package commands")


def _run(tmp_path, monkeypatch, *, docker=None, values=None, pin=PIN,
         cfg_container=None, target_extra=None, functional_error=False, status=200,
         environment_image=_DEFAULT_ENV_IMAGE):
    docker = docker or FakeDocker()
    target = {"lab": True, "run_id": RUN, "probe_image": "unused",
              "origins": {"trusted": {"network": "trusted"}},
              "gateway": {"url": "http://gateway:8080"},
              "engine": {"container": CONTAINER, "image_digest": pin,
                         "trusted_url": "http://engine:8000", "ready_timeout": 1}}
    if target_extra:
        target.update(target_extra)
    source = tmp_path / "target.json"
    source.write_text(json.dumps(target), encoding="utf-8")
    cfg = {"target": str(source)}
    if cfg_container is not None:
        cfg["container"] = cfg_container
    probes = []

    def probe(network, requests, timeout):
        probes.extend(deepcopy(requests))
        if any(request.get("method") == "POST" for request in requests) and functional_error:
            raise OSError("NEVER_PERSIST_FUNCTIONAL_ERROR")
        return [{"id": request["id"], "method": request["method"], "url": request["url"],
                 **completion_response(request, status if request.get("method") == "POST" else 200)}
                for request in requests]

    monkeypatch.setitem(registry.EXECUTORS, "container_config:image-sim",
                        lambda raw: ContainerConfigExecutor(cfg, container_probe=probe, docker=docker))
    store, pack = Store(tmp_path / "store"), baseline_pack()
    check = pack.checks["SUP-04"]
    assert check.executor == "container_config" and check.collection_profile == PROFILE
    declared = pin if environment_image is _DEFAULT_ENV_IMAGE else environment_image
    env = environment(engine={"name": "vllm", "version": "0.32.0", "image_digest": declared}, model={"id": "m"})
    values = {"approved_image_digests": [PIN]} if values is None else values
    pol = policy(pack.ref(), scope={"include": ["SUP-04"], "exclude": []}, values={"SUP-04": values})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    (evd,) = run_plan(store, pack, plan, RunConfig(executors={"container_config": "image-sim"}, adapter="vllm"))
    (result,) = judge_plan(store, pack, plan, as_of=env.created_at)
    return store, pack, plan, evd, result, docker, probes


def _summary(store, evd):
    raw = (store.root / evd.artifacts[0].path).read_text(encoding="utf-8")
    return json.loads(raw.splitlines()[-1]), raw


def test_approved_running_image_is_partial_unknown_not_full_supply_pass(tmp_path, monkeypatch):
    store, _, _, evd, result, docker, probes = _run(tmp_path, monkeypatch)
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.data_origin.value == "EXAMPLE"
    assert evd.execution.executor.name == "container_config" and evd.execution.executor.version == "0.6.1"
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["running_image_identity_verified"].value is True
    assert evd.observations["running_image_repo_digest"].value == PIN
    assert evd.observations["running_image_approved"].value is True
    assert evd.observations["supply_execution_scope_complete"].value is False
    assert evd.observations["functional_probe_results"].value == {"POST /v1/completions": "pass"}
    assert evd.normal_control.succeeded is True
    assert any(not challenge.attempted for challenge in evd.challenges)
    assert result.verdicts.functional == Verdict.UNKNOWN  # 원문 전체 challenge의 INV-11 관문 보존.
    assert any(request.get("method") == "POST" for request in probes)
    assert [call[1] for call in docker.calls if call[0] == "image_info"] == [IMAGE_ID]
    summary, raw = _summary(store, evd)
    assert summary["identity_before"] == summary["identity_after"]
    assert summary["not_exercised"]
    for secret in ("NEVER_PERSIST_ENV", "NEVER_PERSIST_CMD", "NEVER_PERSIST_HOST_PATH", "NEVER_PERSIST_IMAGE_ENTRYPOINT"):
        assert secret not in raw


@pytest.mark.parametrize("approved", [[OTHER], []])
def test_verified_running_image_outside_explicit_policy_is_fail(tmp_path, monkeypatch, approved):
    _, _, _, evd, result, _, _ = _run(tmp_path, monkeypatch, values={"approved_image_digests": approved})
    assert evd.observations["running_image_unapproved"].value is True
    assert result.verdicts.security == Verdict.FAIL
    assert result.verdicts.performance == Verdict.UNKNOWN


def test_later_functional_collection_error_does_not_erase_confirmed_image_fail(tmp_path, monkeypatch):
    store, _, _, evd, result, _, _ = _run(tmp_path, monkeypatch,
        values={"approved_image_digests": [OTHER]}, functional_error=True)
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.normal_control.succeeded is False
    assert result.verdicts.security == Verdict.FAIL
    summary, raw = _summary(store, evd)
    assert "RUNNING_IMAGE_FUNCTIONAL_UNAVAILABLE" in summary["collection_errors"]
    assert "NEVER_PERSIST_FUNCTIONAL_ERROR" not in raw


@pytest.mark.parametrize("field", ["Id", "Image", "StartedAt", "Running", "Name", "label"])
def test_identity_change_during_digest_collection_is_unknown(tmp_path, monkeypatch, field):
    before, after = _inspect(), _inspect()
    if field in {"StartedAt", "Running"}:
        after["State"][field] = False if field == "Running" else "2026-10-26T00:00:01Z"
    elif field == "label":
        after["Config"]["Labels"]["guard.run"] = "other-run"
    else:
        after[field] = {"Id": "f" * 64, "Image": "sha256:" + "e" * 64, "Name": "/other-engine"}[field]
    _, _, _, evd, result, _, probes = _run(tmp_path, monkeypatch,
        docker=FakeDocker(before=before, after=after), values={"approved_image_digests": [OTHER]})
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["running_image_identity_verified"].value is False
    assert evd.observations["running_image_unapproved"].collected is False
    assert not any(request.get("method") == "POST" for request in probes)


@pytest.mark.parametrize("digests", [[], [OTHER], [IMAGE_ID], [None]])
def test_missing_or_unlinked_repo_digest_does_not_prove_approval_or_violation(tmp_path, monkeypatch, digests):
    _, _, _, evd, result, _, _ = _run(tmp_path, monkeypatch, docker=FakeDocker(digests=digests))
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["running_image_repo_digest"].collected is False


@pytest.mark.parametrize("pin", ["example.invalid/vllm-cpu:latest", IMAGE_ID, None])
def test_tag_or_local_image_id_is_not_a_repo_digest(tmp_path, monkeypatch, pin):
    _, _, _, evd, result, docker, probes = _run(tmp_path, monkeypatch, pin=pin)
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["running_image_identity_verified"].value is False
    assert not any(call[0] == "image_info" for call in docker.calls)
    assert not any(request.get("method") == "POST" for request in probes)


@pytest.mark.parametrize("fault", ["inspect", "image"])
def test_collection_error_is_unknown_and_exception_text_is_not_persisted(tmp_path, monkeypatch, fault):
    store, _, _, evd, result, _, _ = _run(tmp_path, monkeypatch,
        docker=FakeDocker(inspect_error=fault == "inspect", image_error=fault == "image"))
    assert result.verdicts.security == Verdict.UNKNOWN
    summary, raw = _summary(store, evd)
    assert "RUNNING_IMAGE_COLLECTION_UNAVAILABLE" in summary["collection_errors"]
    assert "NEVER_PERSIST_DAEMON_ERROR" not in raw and "NEVER_PERSIST_IMAGE_ERROR" not in raw


def test_explicit_container_must_match_target_engine(tmp_path, monkeypatch):
    _, _, _, evd, result, docker, probes = _run(tmp_path, monkeypatch, cfg_container="other-engine")
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["running_image_identity_verified"].value is False
    assert not docker.calls and not any(request.get("method") == "POST" for request in probes)


def test_byul_bare_manifest_digest_is_bound_to_explicit_verified_target(tmp_path, monkeypatch):
    _, _, _, evd, result, _, _ = _run(tmp_path, monkeypatch,
        environment_image=PIN.partition("@")[2], values={"approved_image_digests": [OTHER]})
    assert evd.observations["running_image_repo_digest"].value == PIN
    assert result.verdicts.security == Verdict.FAIL


@pytest.mark.parametrize("declared", [IMAGE_ID, "sha256:" + "c" * 64, None])
def test_unlinked_environment_digest_cannot_borrow_target_identity(tmp_path, monkeypatch, declared):
    _, _, _, evd, result, docker, _ = _run(tmp_path, monkeypatch, environment_image=declared)
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["running_image_identity_verified"].value is False
    assert not docker.calls


@pytest.mark.parametrize("field,value", [("Id", "malformed"), ("StartedAt", "not-a-time"),
                                        ("StartedAt", "2026-10-26T00:00:00")])
def test_stable_but_malformed_identity_is_not_verified(tmp_path, monkeypatch, field, value):
    before = _inspect()
    (before["State"] if field == "StartedAt" else before)[field] = value
    _, _, _, evd, result, _, _ = _run(tmp_path, monkeypatch,
        docker=FakeDocker(before=before), values={"approved_image_digests": [OTHER]})
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["running_image_identity_verified"].value is False


def test_unowned_target_is_not_queried_as_a_verified_running_engine(tmp_path, monkeypatch):
    _, _, _, evd, result, _, probes = _run(tmp_path, monkeypatch, target_extra={"run_id": None})
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["running_image_identity_verified"].value is False
    assert not any(request.get("method") == "POST" for request in probes)


def test_missing_approval_policy_is_not_run_and_never_filled_from_observation(tmp_path, monkeypatch):
    _, _, _, evd, result, docker, probes = _run(tmp_path, monkeypatch, values={})
    assert evd.execution.status == ExecStatus.NOT_RUN
    assert result.verdicts.security == Verdict.UNKNOWN
    assert not docker.calls and not probes


@pytest.mark.parametrize("values", [{}, {"approved_image_digests": None},
    {"approved_image_digests": PIN}, {"approved_image_digests": [IMAGE_ID]},
    {"approved_image_digests": [PIN, PIN]}, {"approved_image_digests": ["example:latest"]}])
def test_policy_shape_and_manifest_identity_are_explicit(values):
    with pytest.raises(ValueError):
        validate_image_approval_policy(values)


def test_rejudging_saved_evidence_preserves_exact_verdict_and_identity(tmp_path, monkeypatch):
    from guard.store.bundle import export_bundle, restore_bundle
    from guard.store.packs import get_pack, put_pack

    store, pack, plan, evd, result, _, _ = _run(tmp_path, monkeypatch, values={"approved_image_digests": [OTHER]})
    before = evd.to_dict()
    (again,) = judge_plan(store, pack, plan, as_of=evd.created_at)
    assert again.ref() == result.ref() and again.verdicts.security == Verdict.FAIL
    assert store.resolve(evd.ref()).to_dict() == before
    assert put_pack(store, PACK_DIR) == pack.ref()
    export_bundle(store.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.verify() == []
    saved_pack = get_pack(restored, pack.ref())
    (replayed,) = judge_plan(restored, saved_pack, restored.resolve(plan.ref()), as_of=evd.created_at)
    assert replayed.ref() == result.ref()
    assert restored.resolve(evd.ref()).to_dict() == before


def test_aborted_supply_collection_records_same_static_executor_version(tmp_path, monkeypatch):
    def interrupted(self, ctx):
        raise RuntimeError("EXAMPLE interrupted collection")

    assert ContainerConfigExecutor.version == "0.6.1"
    monkeypatch.setattr(ContainerConfigExecutor, "collect", interrupted)
    _, _, _, evd, result, _, _ = _run(tmp_path, monkeypatch)
    assert evd.execution.status == ExecStatus.ABORTED
    assert evd.execution.executor.name == "container_config"
    assert evd.execution.executor.version == ContainerConfigExecutor.version == "0.6.1"
    assert result.verdicts.security == Verdict.UNKNOWN


def test_manual_pass_cannot_bypass_partial_supply_security_ceiling(tmp_path, monkeypatch):
    from guard.judges.declared import DeclaredJudge
    from guard.models import Evidence

    _, pack, _, evd, _, _, _ = _run(tmp_path, monkeypatch)
    raw = evd.to_dict()
    raw["observations"]["declared_verdicts"] = {"collected": True, "value": {
        "security": "PASS", "functional": "UNKNOWN", "performance": "UNKNOWN"}}
    raw["observations"]["declared_observation_notes"] = {"collected": True, "value": {
        item: "EXAMPLE operator claim" for item in pack.checks["SUP-04"].required_observations_prose}}
    # 완전한 도전 수행이라고 신고해도 새 자동 계약의 빈 보안 PASS 규칙을 우회하지 못한다.
    for challenge in raw["challenges"]:
        challenge["attempted"] = True
    claimed = Evidence.model_validate(raw).seal()
    out = DeclaredJudge().judge(pack.checks["SUP-04"], claimed, {}, {"approved_image_digests": [PIN]})
    assert out.verdicts["security"] == Verdict.UNKNOWN


def test_static_guidance_names_partial_scope_without_claiming_runtime_identity():
    from guard.readiness_support import assess_check_support

    pack = baseline_pack()
    env = environment(engine={"name": "vllm", "version": "0.32.0", "image_digest": PIN})
    values = {"SUP-04": {"approved_image_digests": [PIN]}}
    pol = policy(pack.ref(), values=values, scope={"include": ["SUP-04"], "exclude": []})
    cfg = {"adapter": {"name": "vllm"}, "executors": {"container_config": "real"}}
    row, = assess_check_support(pack, env, pol, cfg, selected_check_ids=["SUP-04"])
    assert row["status"] == "SUPPORTED" and row["policy_valid"] is True
    assert "no sentinel" in row["scope"]
    assert any("remain unobserved" in limitation for limitation in row["limitations"])
    pol = pol.model_copy(update={"values": {"SUP-04": {"approved_image_digests": [IMAGE_ID]}}})
    bad, = assess_check_support(pack, env, pol, cfg, selected_check_ids=["SUP-04"])
    assert bad["status"] == "UNKNOWN" and bad["policy_valid"] is False
