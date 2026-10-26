"""EXAMPLE collector persistence faults; no engine, Docker or network execution.

The non-LAB origin case supplies synthetic MEASURED metadata only to verify the
DECLARED ceiling. It is not a measurement or real engine claim.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import EvidenceDraft
from guard.executors.content_common import append_artifact, sample_artifact
from guard.executors.http_common import Ctx, HttpCheckExecutor, incomplete_artifact_capture
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import put_pack, get_pack
from tests.builders import PACK_DIR, ROUTES, T0, baseline_pack, environment, policy, req01_obs

ERROR = "EXAMPLE_CAPTURE_EXCEPTION_CANARY"
PATH = "EXAMPLE_CAPTURE_PATH_CANARY"
PARTIAL = "EXAMPLE_PARTIAL_FILE_MUST_NOT_BE_REFERENCED"
CODE = "ARTIFACT_CAPTURE_INCOMPLETE"
FIRST, SECOND = "EXAMPLE-first.json", "EXAMPLE-second.json"


class Unserializable:
    def __str__(self):
        raise RuntimeError(ERROR)


def install_fault(monkeypatch, work, fault, first=FIRST):
    original_write, original_open = Path.write_text, Path.open
    original_mkdir, original_exists = Path.mkdir, Path.exists
    def selected(path, name=None):
        return path.is_relative_to(work) and (name is None or path.name == name)
    def write(path, text, *args, **kwargs):
        if selected(path, first) and fault in {"sample-write", "partial", "combined"}:
            if fault == "partial": original_write(path, PARTIAL, encoding="utf-8")
            raise PermissionError(13, ERROR, str(path))
        if selected(path, first) and fault == "missing":
            result = original_write(path, text, *args, **kwargs)
            path.unlink()  # Only this test's freshly created, disposable capture.
            return result
        return original_write(path, text, *args, **kwargs)
    def open_(path, mode="r", *args, **kwargs):
        if selected(path, "probe_log.jsonl") and mode == "w" and fault == "log-open":
            raise OSError(5, ERROR, str(path))
        return original_open(path, mode, *args, **kwargs)
    def mkdir(path, *args, **kwargs):
        if selected(path) and fault == "mkdir": raise OSError(28, ERROR, str(path))
        return original_mkdir(path, *args, **kwargs)
    def exists(path):
        if selected(path, first) and fault == "exists": raise OSError(5, ERROR, str(path))
        return original_exists(path)
    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(Path, "open", open_)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "exists", exists)


@pytest.fixture
def scenario(tmp_path_factory, monkeypatch):
    def run(*, fault=None, violation=True, lab=True):
        root = tmp_path_factory.mktemp("cap")
        store = Store(root / PATH)
        pack = baseline_pack()
        put_pack(store, PACK_DIR)
        check = pack.checks["REQ-01"]
        env = environment({"gateway": {"value":True,"origin":"EXAMPLE","source":"EXAMPLE-only"}})
        pol = policy(pack.ref(),scope={"include":[check.id],"exclude":[]})
        store.put_many([env,pol])
        plan = select(pack,[env],pol)
        store.put(plan)
        expected = req01_obs(direct={route:200 for route in ROUTES} if violation else None)
        target = {"lab":lab,"engine":{"trusted_url":"http://example.invalid:8000"},"comparison_config":{"profile":"EXAMPLE-capture"}}
        class Producer(HttpCheckExecutor):
            name, version = "EXAMPLE-capture-producer", "1"
            requires_roles = requires_route_scope = False
            def collect(self, ctx):
                sample_artifact(ctx,FIRST,Unserializable() if fault=="sample-serialize" else {"case":1,"origin":"EXAMPLE"})
                sample_artifact(ctx,SECOND,{"case":2,"origin":"EXAMPLE"})
                summary = {"profile":"EXAMPLE-capture","fixture":{"profile":"EXAMPLE-capture"}}
                if fault=="log-serialize": summary["diagnostic"] = Unserializable()
                return deepcopy(expected), {"attempted":True,"succeeded":True,"detail":"EXAMPLE normal control"}, [
                    {"step":step,"attempted":True} for step in ctx.check.challenge_steps], summary
            def _finish(self,ctx):
                draft = super()._finish(ctx)
                append_artifact(draft,ctx,FIRST,"EXAMPLE-capture/1")
                return append_artifact(draft,ctx,SECOND,"EXAMPLE-capture/1")
            def run(self, check, env, values, adapter, workdir):
                ctx = Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=values,timeout=1,
                          allow_mutating=False,keys={},secrets=[],cprobe=None,workdir=workdir,started=T0)
                return self._finish(ctx)
        # Use the real existing CheckDef executor name; only the implementation is an EXAMPLE fixture.
        monkeypatch.setitem(registry.EXECUTORS,check.executor+":capture-sim",lambda raw:Producer({}))
        cfg = RunConfig(executors={check.executor:"capture-sim"},adapter="fake")
        install_fault(monkeypatch,store.root/"work",fault)
        if fault=="combined":
            original_put = store.put_artifact
            def put(data):
                value = json.loads(data)
                if value.get("capture",{}).get("case")==2: raise OSError(ERROR)
                return original_put(data)
            monkeypatch.setattr(store,"put_artifact",put)
        (evd,) = run_plan(store,pack,plan,cfg)
        (result,) = judge_plan(store,pack,plan,as_of=T0)
        return SimpleNamespace(root=root,store=store,pack=pack,plan=plan,env=env,evd=evd,result=result,expected=expected)
    return run


@pytest.mark.parametrize("fault",["sample-write","partial","missing","sample-serialize","log-open","log-serialize","mkdir","exists"])
@pytest.mark.parametrize("violation",[False,True],ids=["no-violation","violation"])
@pytest.mark.parametrize("lab",[False,True],ids=["synthetic-measured","example"])
def test_capture_failure_preserves_valid_observations_and_successful_refs(scenario,fault,violation,lab):
    case = scenario(fault=fault,violation=violation,lab=lab)
    evd = case.evd
    assert evd.execution.status == ExecStatus.ABORTED and evd.execution.abort_reason == CODE
    assert evd.data_origin == (DataOrigin.EXAMPLE if lab else DataOrigin.DECLARED)
    assert evd.normal_control.attempted and evd.normal_control.succeeded
    assert all(step.attempted for step in evd.challenges)
    assert {key:value.model_dump() for key,value in evd.observations.items()} == case.expected
    names = {item.name for item in evd.artifacts}
    expected = set() if fault=="mkdir" else ({FIRST,SECOND} if fault.startswith("log-") else {"probe_log.jsonl",SECOND})
    assert names == expected
    assert case.result.verdicts.security == (Verdict.FAIL if violation else Verdict.UNKNOWN)
    assert case.result.verdicts.functional == case.result.verdicts.performance == Verdict.UNKNOWN
    assert case.result.verification_level is None
    assert evd.comparability.fixture_sha256 == sha256_hex(canonical_json({"profile":"EXAMPLE-capture"}))
    saved = canonical_json(evd.to_dict())+canonical_json(case.result.to_dict())
    for item in evd.artifacts:
        data = (case.store.root/item.path).read_bytes()
        assert item.sha256==sha256_hex(data) and item.bytes==len(data) and item.path=="artifacts/"+item.sha256
        saved += data
    for canary in (ERROR,PATH,PARTIAL): assert canary.encode() not in saved
    assert case.store.verify()==[]


def test_capture_and_store_failures_keep_both_fixed_diagnostics(scenario):
    case = scenario(fault="combined")
    assert case.evd.execution.abort_reason == CODE+"; ARTIFACT_PERSISTENCE_INCOMPLETE"
    assert [item.name for item in case.evd.artifacts]==["probe_log.jsonl"]
    assert case.result.verdicts.security==Verdict.FAIL and case.store.verify()==[]


def test_capture_failure_keeps_exact_refs_through_bundle_and_saved_pack_replay(scenario):
    case = scenario(fault="partial")
    before = case.evd.to_dict()
    archive = case.root/"evidence.zip"
    export_bundle(case.store.root,archive)
    with zipfile.ZipFile(archive) as bundle:
        assert not any(name.startswith("work/") for name in bundle.namelist())
        assert all(PARTIAL.encode() not in bundle.read(name) for name in bundle.namelist())
    restore_bundle(archive,case.root/"restored")
    restored = Store(case.root/"restored")
    saved_pack = get_pack(restored,case.pack.ref())
    (replayed,) = judge_plan(restored,saved_pack,restored.resolve(case.plan.ref()),as_of=T0)
    assert replayed.ref()==case.result.ref() and restored.resolve(case.evd.ref()).to_dict()==before
    assert restored.verify()==[] and case.store.resolve(case.evd.ref()).to_dict()==before


def test_repeated_capture_failure_preserves_prior_abort_and_cleanup():
    draft = EvidenceDraft(status=ExecStatus.ABORTED,data_origin=DataOrigin.DECLARED,
        normal_control={"attempted":False,"succeeded":False},abort_reason="EXAMPLE_PRIOR",
        cleanup_errors=["EXAMPLE_CLEANUP"])
    assert incomplete_artifact_capture(incomplete_artifact_capture(draft)) is draft
    assert draft.abort_reason=="EXAMPLE_PRIOR; "+CODE and draft.cleanup_errors==["EXAMPLE_CLEANUP"]


@pytest.mark.parametrize("invalid",["unfinished-measured","invalid-observation"])
def test_capture_fallback_does_not_repair_invalid_executor_output(invalid):
    from guard.models.objects import Observation
    draft = EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.MEASURED,
                          normal_control={"attempted":False,"succeeded":False})
    if invalid=="unfinished-measured":
        draft = draft.model_copy(update={"status":ExecStatus.ABORTED})
    else:
        draft = draft.model_copy(update={"observations":{"broken":Observation(value=True,collected=True).model_copy(update={"collected":False})}})
    before = draft.model_dump()
    with pytest.raises(ValueError): incomplete_artifact_capture(draft)
    assert draft.model_dump()==before


def test_unattempted_optional_artifact_keeps_original_absence_contract(tmp_path_factory):
    ctx = SimpleNamespace(workdir=tmp_path_factory.mktemp("optional"))
    draft = EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.EXAMPLE,
                          normal_control={"attempted":False,"succeeded":False})
    assert append_artifact(draft,ctx,"EXAMPLE-not-captured.json","EXAMPLE/1") is draft
    assert draft.status==ExecStatus.COMPLETED and draft.abort_reason is None and draft.artifacts==[]


@pytest.mark.parametrize("violation",[False,True])
def test_successful_capture_keeps_original_completion_and_origin(scenario,violation):
    case = scenario(violation=violation,lab=True)
    assert case.evd.execution.status==ExecStatus.COMPLETED and case.evd.execution.abort_reason is None
    assert case.evd.data_origin==DataOrigin.EXAMPLE and len(case.evd.artifacts)==3
    assert case.result.verdicts.security==(Verdict.FAIL if violation else Verdict.PASS)


@pytest.mark.parametrize("fault",["sample-write","log-open"])
def test_existing_template_profile_keeps_independent_fail_and_other_artifact(tmp_path_factory,monkeypatch,fault):
    from guard.executors.template_validation import TemplateValidationExecutor
    from tests import test_template_validation as template
    root = tmp_path_factory.mktemp("profile")
    world = template.World(context=64,after_error=True)
    target = root/"target.json"
    target.write_text(json.dumps(template.target(64)),encoding="utf-8")
    monkeypatch.setitem(registry.EXECUTORS,"template_validation:capture-fault",lambda raw:
        TemplateValidationExecutor({**template.settings(),"target":str(target)},docker=world,
                                   container_probe=world.probe,environ={}))
    store = Store(root/"store")
    pack = baseline_pack()
    put_pack(store,PACK_DIR)
    env = environment(engine={"name":"vllm","version":"0.31.0","image_digest":template.IMAGE},model={"id":"fixture-model"})
    pol = policy(pack.ref(),scope={"include":["REQ-07"],"exclude":[]},values={"REQ-07":deepcopy(template.VALUES)})
    store.put_many([env,pol])
    plan = select(pack,[env],pol)
    store.put(plan)
    install_fault(monkeypatch,store.root/"work",fault,first="template_samples.json")
    (evd,) = run_plan(store,pack,plan,RunConfig(executors={"template_validation":"capture-fault"},adapter="vllm"))
    (result,) = judge_plan(store,pack,plan,as_of=T0)
    assert evd.execution.status==ExecStatus.ABORTED and evd.execution.abort_reason==CODE
    assert evd.data_origin==DataOrigin.EXAMPLE and result.verdicts.security==Verdict.FAIL
    assert evd.observations["max_consumed_prompt_tokens"].collected
    assert {item.name for item in evd.artifacts}==({"probe_log.jsonl"} if fault=="sample-write" else {"template_samples.json"})
    assert store.verify()==[]


def test_existing_kubernetes_sampling_keeps_collected_facts_on_log_write_failure(tmp_path_factory,monkeypatch):
    from tests import test_kubernetes_sampling as kube
    root = tmp_path_factory.mktemp("kube")
    install_fault(monkeypatch,root/"work","log-open")
    draft, _ = kube.run(root)
    assert draft.status==ExecStatus.ABORTED and draft.abort_reason==CODE
    assert draft.data_origin==DataOrigin.EXAMPLE and not draft.artifacts
    assert draft.normal_control.succeeded and draft.observations["sampling_options_tested"].collected
    assert draft.observations["sampling_options_tested"].value


@pytest.mark.parametrize("fault",["sample-write","log-open"])
def test_existing_response_profile_keeps_observed_leak_on_capture_failure(tmp_path_factory,monkeypatch,fault):
    from tests import test_response_content as response
    root = tmp_path_factory.mktemp("resp")
    install_fault(monkeypatch,root/"store"/"work",fault,first="response_samples.json")
    store, evd, result = response.run_content(root,monkeypatch,response.response_probe(leak=True))
    assert evd.execution.status==ExecStatus.ABORTED and evd.execution.abort_reason==CODE
    assert evd.data_origin==DataOrigin.EXAMPLE and evd.observations["private_markers_returned"].value
    assert result.verdicts.security==Verdict.FAIL and result.verdicts.functional==Verdict.UNKNOWN
    assert {item.name for item in evd.artifacts}==({"probe_log.jsonl"} if fault=="sample-write" else {"response_samples.json"})
    assert store.verify()==[]
