"""현재 source 결합과 저장 Ref 재생의 경계. 실측 없는 DECLARED 계약 자료."""
from copy import deepcopy
import json
import socket
import subprocess

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.change_selection import build_context
from guard.core.judge import judge_evidence, judge_plan
from guard.core.report import build_report
from guard.core.select import select
from guard.executors.credential_exposure import CredentialExposureExecutor
from guard.executors.http_common import Ctx, http_probe
from guard.executors.reuse import preview_inputs
from guard.models import Evidence, canonical_json, sha256_hex
from guard.models.objects import ExecutorInfo, PluginInfo
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack
from tests.builders import T0, baseline_pack, completion_response, environment, policy
from tests.test_credential_mount_semantics import FakeDocker, IMAGE, RUN


def forbidden(*args, **kwargs):
    raise AssertionError("static selection must not collect runtime identity or send requests")


def _archive(tmp_path, *, violation=False, origin="DECLARED"):
    """Real PACK31/rule_table; DECLARED inputs are synthetic, never MEASURED."""
    pack = baseline_pack()
    check = pack.checks["PLT-04"]
    env = environment(origin=origin, engine={"name": "engine-x", "version": "1.0.0", "image_digest": IMAGE},
                      model={"id": "test"})
    values = {"forbidden_env_names": ["EXAMPLE_FORBIDDEN"], "forbidden_mount_paths": ["/credentials"],
              "credential_candidate_paths": ["/credentials/key"]}
    pol = policy(pack.ref(), data_origin=origin, scope={"include": [check.id]}, values={check.id: values})
    store = Store(tmp_path / "store")
    assert put_pack(store, pack.path) == pack.ref()
    store.put_many([env, pol])
    prior = select(pack, [env], pol)
    store.put(prior)
    target = {"run_id": RUN, "environment_ref": env.ref().model_dump(mode="json"),
              "engine": {"container": "engine", "trusted_url": "http://engine:8000"},
              "origins": {"trusted": {"network": "EXAMPLE-trusted"}},
              "comparison_config": {"profile": "EXAMPLE-source-reuse-contract"}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    # Explicitly match the real HTTP transport profile; an unrelated missing
    # setting must not hide the source-identity reuse boundary under test.
    settings = {"target": str(path), "http_probe_profile": http_probe.TRANSPORT_PROFILE}
    cfg = {"executors": {check.executor: "real"}, "adapter": {"name": "vllm"}, check.executor: settings}
    adapter = VllmAdapter()
    ctx = Ctx(check=check, env=env, adapter=adapter, target={**deepcopy(target), "lab": True},
              policy_values=values, timeout=3.0, allow_mutating=False, keys={}, secrets=[],
              cprobe=lambda net, requests, timeout: [completion_response(r) for r in requests],
              workdir=tmp_path / "EXAMPLE-collector", started=T0)
    draft = CredentialExposureExecutor(settings, docker=FakeDocker(
        process_names=["EXAMPLE_FORBIDDEN"] if violation else []))._finish(ctx)
    assert draft.data_origin.value == "EXAMPLE"
    fixture = json.loads(draft.artifacts[0].path.read_text(encoding="utf-8").splitlines()[-1])["fixture"]
    assert sha256_hex(canonical_json(fixture)) == draft.comparability.fixture_sha256
    executor = ExecutorInfo(name="credential_exposure", version=CredentialExposureExecutor.version,
                            config_sha256=sha256_hex(canonical_json(draft.config)))
    assert executor.config_sha256 == draft.comparability.config_sha256
    # Store explicit DECLARED contract facts. This is not a live target PASS.
    evd = Evidence(id="EVD-DECLARED-SOURCE-REUSE", created_at=T0, data_origin=origin,
        refs=[prior.ref(), env.ref()], plan=prior.ref(), check_id=check.id, side="single", environment=env.ref(),
        execution={"status": "COMPLETED", "executor": executor,
                   "adapter": PluginInfo(name=adapter.name, version=adapter.version),
                   "started_at": T0, "finished_at": T0},
        normal_control=draft.normal_control, challenges=draft.challenges,
        observations=draft.observations, comparability=draft.comparability).seal()
    store.put(evd)
    res = judge_evidence(check, evd, pol, T0, plan=prior)
    store.put(res)
    assert evd.data_origin.value == res.data_origin.value == origin
    assert res.verdicts.security.value == ("FAIL" if violation else "PASS")
    return pack, env, pol, store, prior, evd, res, cfg, target, path


@pytest.mark.parametrize("change", ["same-declaration", "run", "container"])
def test_current_source_is_reselected_without_runtime_io_or_store_writes(tmp_path, monkeypatch, change):
    pack, env, pol, store, prior, evd, res, cfg, target, path = _archive(tmp_path)
    if change == "run":
        target["run_id"] = "EXAMPLE-next-owner"
    elif change == "container":
        target["engine"]["container"] = "EXAMPLE-replacement"
    # A restart can also keep all declarations unchanged. Static selection has
    # no independently observed StartedAt/container ID to rule it out.
    path.write_text(json.dumps(target), encoding="utf-8")
    before = {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    monkeypatch.setattr(CredentialExposureExecutor, "collect", forbidden)
    monkeypatch.setattr(CredentialExposureExecutor, "_docker_obj", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert preview_inputs(pack.checks["PLT-04"], env, pol.values["PLT-04"], cfg) is None
    context = build_context(store, prior.ref(), pack, [env], pol, cfg, as_of=T0, read_only=True)
    assert not context.full_scope and not context.carried_over
    assert context.triggers["PLT-04"][0].refs == ["PROCEDURE_OR_FIXTURE_UNCONFIRMED"]
    plan = select(pack, [env], pol, context=context)
    assert plan.item("PLT-04").selected and plan.item("PLT-04").carried_over is None
    assert store.resolve(evd.ref()).to_dict() == evd.to_dict()
    assert store.resolve(res.ref()).to_dict() == res.to_dict()
    after = {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("violation", [False, True])
def test_saved_exact_refs_rejudge_report_export_and_restore_are_preserved(tmp_path, violation):
    pack, env, pol, store, prior, evd, res, cfg, target, path = _archive(tmp_path, violation=violation)
    expected = "FAIL" if violation else "PASS"
    assert judge_plan(store, pack, prior, T0)[0].ref() == res.ref()
    report = build_report(store, pack, prior, T0)
    store.put(report)
    assert evd.ref() in report.inputs_manifest and res.ref() in report.inputs_manifest
    assert not report.official
    export_bundle(store.root, tmp_path / "bundle.zip")
    restore_bundle(tmp_path / "bundle.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.resolve(report.ref()).to_dict() == report.to_dict()
    assert restored.resolve(evd.ref()).to_dict() == evd.to_dict()
    assert judge_plan(restored, get_pack(restored, prior.inputs.pack), prior, T0)[0].ref() == res.ref()
    assert restored.resolve(res.ref()).verdicts.security.value == expected
    assert not build_context(restored, prior.ref(), pack, [env], pol, cfg, as_of=T0).carried_over


def test_example_prior_evidence_is_still_ineligible(tmp_path):
    pack, env, pol, store, prior, evd, res, cfg, target, path = _archive(tmp_path, origin="EXAMPLE")
    context = build_context(store, prior.ref(), pack, [env], pol, cfg, as_of=T0)
    assert not context.carried_over
    assert context.triggers["PLT-04"][0].refs == ["PRIOR_EVIDENCE_INELIGIBLE"]
