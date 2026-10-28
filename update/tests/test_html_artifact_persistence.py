"""EXAMPLE OBS-09 HTML storage faults; no engine, daemon, AWS or network."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import EvidenceDraft
from guard.executors.content_common import append_artifact, text_artifact
from guard.executors.log_integrity import LogIntegrityExecutor
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack
from tests.builders import PACK_DIR, T0, baseline_pack, environment, policy
from tests import test_log_integrity as source


HTML = "log_integrity_display.html"
ERROR = "EXAMPLE_PRIVATE_HTML_STORAGE_CANARY"
PARTIAL = b"EXAMPLE_PARTIAL_HTML_MUST_NOT_BE_REFERENCED"
CODE = "ARTIFACT_CAPTURE_INCOMPLETE"


@pytest.fixture
def scenario(tmp_path_factory, monkeypatch):
    def invoke(*, fault=None, violation=False):
        root = tmp_path_factory.mktemp("html-capture")
        target = root / "target.json"
        target.write_text(json.dumps(source.target()), encoding="utf-8")
        world = source.World(forged_metadata=violation)
        executor = LogIntegrityExecutor({"target": str(target), "request_timeout": 1},
                                       docker=world, container_probe=world.probe, environ={})
        pack = baseline_pack()
        env = environment(engine={"name":"vllm", "version":"0.31.0", "image_digest":source.IMAGE},
                          model={"id":"fixture-model"})
        pol = policy(pack.ref(), scope={"include":["OBS-09"],"exclude":[]}, values={"OBS-09":source.VALUES})
        store = Store(root / "store")
        put_pack(store, PACK_DIR)
        store.put_many([env, pol])
        plan = select(pack, [env], pol)
        store.put(plan)
        assert plan.item("OBS-09").selected
        work = store.root / "work" / plan.id / "OBS-09" / "single"
        original_write, original_mkdir, original_exists = Path.write_bytes, Path.mkdir, Path.exists
        original_put = store.put_artifact
        mkdir_failed = []
        put_calls = []
        def selected(path):
            return path.is_relative_to(work) and path.name == HTML
        def write(path, data):
            if selected(path):
                if fault in {"write", "partial", "combined"}:
                    if fault == "partial": original_write(path, PARTIAL)
                    raise PermissionError(13, ERROR, str(path))
                if fault == "missing":
                    result = original_write(path, data)
                    assert path.resolve().is_relative_to(work.resolve())
                    path.unlink()  # Only this fixture's just-created disposable HTML.
                    return result
            return original_write(path, data)
        def mkdir(path, *args, **kwargs):
            if path == work and fault == "mkdir" and len(world.requests) == 7 and not mkdir_failed:
                mkdir_failed.append(True)
                raise OSError(28, ERROR, str(path))
            return original_mkdir(path, *args, **kwargs)
        def exists(path):
            if selected(path) and fault == "metadata": raise OSError(5, ERROR, str(path))
            return original_exists(path)
        def put(data):
            put_calls.append(data)
            if fault == "combined" and len(put_calls) == 1:
                raise OSError(5, ERROR)
            return original_put(data)
        with monkeypatch.context() as patch:
            patch.setitem(registry.EXECUTORS, "log_integrity:html-test", lambda cfg: executor)
            patch.setattr(Path, "write_bytes", write)
            patch.setattr(Path, "mkdir", mkdir)
            patch.setattr(Path, "exists", exists)
            patch.setattr(store, "put_artifact", put)
            evd, = run_plan(store, pack, plan, RunConfig(executors={"log_integrity":"html-test"}, adapter="vllm"))
        res, = judge_plan(store, pack, plan, as_of=T0)
        return SimpleNamespace(store=store, pack=pack, plan=plan, evd=evd, res=res, world=world, work=work)
    return invoke


@pytest.mark.parametrize("fault", ["write", "partial", "mkdir", "missing", "metadata", "combined"])
@pytest.mark.parametrize("violation", [False, True], ids=["normal", "independent-fail"])
def test_html_storage_failure_preserves_collected_observations_and_other_successful_refs(scenario, fault, violation):
    baseline = scenario(violation=violation)
    case = scenario(fault=fault, violation=violation)
    evd = case.evd
    assert baseline.evd.execution.status == ExecStatus.COMPLETED
    assert evd.execution.status == ExecStatus.ABORTED and evd.data_origin == DataOrigin.EXAMPLE
    assert CODE in evd.execution.abort_reason
    assert evd.observations == baseline.evd.observations
    assert evd.normal_control == baseline.evd.normal_control and evd.challenges == baseline.evd.challenges
    assert evd.comparability.fixture_sha256 == baseline.evd.comparability.fixture_sha256
    assert len(case.world.requests) == 7 and all(c.attempted for c in evd.challenges)
    assert case.res.verdicts.security == (Verdict.FAIL if violation else Verdict.UNKNOWN)
    assert case.res.verdicts.functional == Verdict.UNKNOWN and case.res.verification_level is None
    names = {a.name for a in evd.artifacts}
    expected = {"probe_log.jsonl", "log_integrity_samples.json", "log_integrity_events.json"}
    if fault == "combined":
        expected.remove("probe_log.jsonl")
        assert evd.execution.abort_reason == CODE + "; ARTIFACT_PERSISTENCE_INCOMPLETE"
    else:
        assert evd.execution.abort_reason == CODE
    assert names == expected and HTML not in names
    for artifact in evd.artifacts:
        assert PARTIAL not in (case.store.root / artifact.path).read_bytes()
    assert ERROR not in canonical_json(evd.to_dict()).decode()
    assert case.store.verify() == []


@pytest.mark.parametrize("violation", [False, True])
def test_partial_html_failure_survives_report_bundle_and_exact_ref_replay(scenario, tmp_path, violation):
    case = scenario(fault="partial", violation=violation)
    rpt = build_report(case.store, case.pack, case.plan, as_of=T0)
    case.store.put(rpt)
    export_bundle(case.store.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    pack = get_pack(restored, case.pack.ref())
    replayed, = judge_plan(restored, pack, restored.resolve(case.plan.ref()), as_of=T0)
    assert replayed.ref() == case.res.ref()
    assert restored.resolve(case.evd.ref()).to_dict() == case.evd.to_dict()
    assert restored.resolve(rpt.ref()).to_dict() == rpt.to_dict() and restored.verify() == []
    assert ERROR not in canonical_json(rpt.to_dict()).decode()


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_html_write_cancellation_is_not_swallowed(tmp_path, monkeypatch, failure_type):
    original = Path.write_bytes
    error = failure_type(ERROR)
    def write(path, data):
        if path.name == HTML: raise error
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(failure_type) as caught:
        source.run(tmp_path)
    assert caught.value is error


@pytest.mark.parametrize("text", ["\ud800", "EXAMPLE"])
def test_text_encoding_failure_is_an_attempted_capture_gap(tmp_path, text):
    class BrokenText(str):
        def encode(self, *args, **kwargs): raise RuntimeError(ERROR)
    ctx = SimpleNamespace(workdir=tmp_path)
    text_artifact(ctx, HTML, text if text != "EXAMPLE" else BrokenText(text))
    draft = EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                          normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE control"})
    append_artifact(draft, ctx, HTML, "EXAMPLE-html/1")
    assert draft.status == ExecStatus.ABORTED and draft.abort_reason == CODE
    assert draft.data_origin == DataOrigin.EXAMPLE and draft.artifacts == []
    assert draft.normal_control.attempted and draft.normal_control.succeeded
    assert ERROR not in canonical_json(draft.model_dump(mode="json")).decode()


def test_text_artifact_keeps_exact_utf8_and_newline_bytes(tmp_path):
    text = "EXAMPLE 별\n<script>EXAMPLE</script>\n"
    ctx = SimpleNamespace(workdir=tmp_path)
    text_artifact(ctx, HTML, text)
    assert (tmp_path / HTML).read_bytes() == text.encode("utf-8")
    assert not getattr(ctx, "_failed_artifacts", ()) and HTML in ctx._sample_artifacts
