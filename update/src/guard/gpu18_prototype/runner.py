"""격리된 합성 관측을 기존 select/run/judge/report 및 저장 팩으로 처리한다.

원본 팩의 판정 공백은 그대로 별도 보고한다. 모의 계약 전용 규칙은 이름이
다른 DRAFT 팩에만 넣으며, EXAMPLE 자료는 제품 실증을 대체하지 않는다.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
from threading import RLock

import yaml

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.models import DataOrigin, Environment, ExecStatus, Policy, canonical_json, load_pack, new_id, sha256_hex
from guard.models.base import utcnow
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle, verify_complete
from guard.store.packs import get_pack, put_pack

from . import CHECK_IDS, SCENARIOS

_MODULE_NAMES = ("common_cases", "cpu_cases", "history_cases", "native_case")
_REGISTRY_KEY = "gpu18-prototype-example-only"
_REGISTRY_LOCK = RLock()
_SOURCE_PACK_SHA256 = "74219b48b241775e4c4fa153a950b2a94029147f512c731f055e91cf945cd5b6"


def _modules():
    modules = [importlib.import_module(f"{__package__}.{name}") for name in _MODULE_NAMES]
    owners = {}
    for module in modules:
        for check_id in module.CHECK_IDS:
            if check_id in owners:
                raise ValueError(f"duplicate prototype owner: {check_id}")
            owners[check_id] = module
    if set(owners) != set(CHECK_IDS):
        raise ValueError("prototype scope must be exactly the assigned 18 checks")
    return owners


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _prototype_pack(source, owners, destination):
    """원팩을 바꾸지 않고 모의 계약 규칙 차이를 해시 고정하여 보존한다."""
    destination.mkdir()
    (destination / "checks").mkdir()
    files = []
    changes = {}
    for cid in CHECK_IDS:
        original = source.checks[cid]
        data = original.model_dump(mode="json")
        params = getattr(owners[cid], "PROTOTYPE_JUDGE_PARAMS", {}).get(cid)
        if params is not None:
            changes[cid] = {"original_judge": data["judge"], "prototype_judge": {
                "plugin": "rule_table", "params": params},
                "scope": "EXAMPLE mock contract only; not an approved production rule"}
            data["judge"] = changes[cid]["prototype_judge"]
        name = f"checks/{cid}.yaml"
        raw = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")
        (destination / name).write_bytes(raw)
        files.append({"path": name, "sha256": sha256_hex(raw)})
    derivation = {"data_origin": "EXAMPLE", "official": False,
                  "source_pack": source.ref().model_dump(mode="json"), "judge_differences": changes}
    raw = canonical_json(derivation)
    (destination / "derivation.json").write_bytes(raw)
    files.append({"path": "derivation.json", "sha256": sha256_hex(raw)})
    manifest = {"pack_id": "PACK-GPU18-PROTOTYPE", "revision": 1,
                "engines": ["gpu18-simulation"], "checks": list(CHECK_IDS),
                "files_sha256": files, "review": {"status": "DRAFT"}}
    (destination / "pack.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return load_pack(destination), derivation


class _PrototypeExecutor:
    name = _REGISTRY_KEY
    version = "0.1.0"

    def __init__(self, scenario, owners):
        self.scenario, self.owners = scenario, owners

    def run(self, check, env, policy_values, adapter, workdir):
        if check.id not in self.owners or self.scenario not in SCENARIOS:
            raise ValueError("unsupported prototype case")
        workdir.mkdir(parents=True, exist_ok=True)
        started = utcnow()
        module = self.owners[check.id]
        if policy_values != module.POLICY_VALUES.get(check.id, {}):
            raise ValueError("prototype policies must match the simulated producer contract")
        observed = module.simulate(check.id, self.scenario, workdir)
        normal = dict(observed["normal_control"])
        if "note" in normal:
            normal["detail"] = normal.pop("note")
        events = observed["events"]
        if not events:
            raise ValueError("simulation must produce traceable events")
        artifact = workdir / "simulation.json"
        _write_json(artifact, {"simulation": True, "data_origin": "EXAMPLE", "official": False,
                              "check_id": check.id, "scenario": self.scenario,
                              "events": events, "observations": observed["observations"]})
        return EvidenceDraft(
            status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
            normal_control=normal,
            challenges=[{"step": step, "attempted": True,
                         "observations": {"simulation": True, "trace": "simulation.json"}}
                        for step in check.challenge_steps],
            observations=observed["observations"],
            artifacts=[DraftArtifact(name="EXAMPLE simulation trace", path=artifact, collector=self.name)],
            config={"simulation": True, "scenario": self.scenario, "check_id": check.id,
                    "policy_values": policy_values}, started_at=started, finished_at=utcnow())


@contextmanager
def _registered_executor(scenario, owners):
    # 전용 실행 중에만 사용한다. 기존 전역 registry 소스와 값은 보존한다.
    with _REGISTRY_LOCK:
        if _REGISTRY_KEY in registry.EXECUTORS:
            raise ValueError("prototype executor key is already registered")
        registry.EXECUTORS[_REGISTRY_KEY] = lambda cfg: _PrototypeExecutor(scenario, owners)
        try:
            yield
        finally:
            registry.EXECUTORS.pop(_REGISTRY_KEY, None)


def _environment(pack):
    facts = {}
    for cid in CHECK_IDS:
        for condition in pack.checks[cid].applies_when.all:
            value = {"value": condition.eq, "origin": "EXAMPLE", "source": "gpu18 in-memory simulation"}
            if condition.eq is False:
                value["absence_evidence"] = "EXAMPLE simulated feature disabled"
            if condition.fact in facts and facts[condition.fact] != value:
                raise ValueError(f"incompatible simulated applicability: {condition.fact}")
            facts[condition.fact] = value
    return Environment(id=new_id("ENV"), data_origin=DataOrigin.EXAMPLE,
                       engine={"name": "gpu18-simulation", "version": "0.1.0"}, facts=facts).seal()


def _execute(store, pack, env, owners, scenario, as_of, reports_dir, label):
    # 최초 보고서부터 저장 팩으로 만든다. 복원 시에만 추가되는 보존본 안내를
    # 제거하거나 비교에서 제외하지 않고, 같은 경로로 정확히 대조한다.
    pack = get_pack(store, pack.ref())
    policy = Policy(id=new_id("POL"), data_origin=DataOrigin.EXAMPLE, status="DRAFT",
                    pack=pack.ref(), refs=[pack.ref()], scope={"include": list(CHECK_IDS)},
                    values={cid: owners[cid].POLICY_VALUES.get(cid, {}) for cid in CHECK_IDS}).seal()
    store.put(policy)
    plan = select(pack, [env], policy)
    selected = {item.check_id for item in plan.items if item.selected}
    if selected != set(CHECK_IDS):
        raise ValueError(f"prototype selection differs: {sorted(selected)}")
    store.put(plan)
    cfg = RunConfig(executors={pack.checks[cid].executor: _REGISTRY_KEY for cid in CHECK_IDS},
                    adapter="fake", raw={})
    with _registered_executor(scenario, owners):
        evidence = run_plan(store, pack, plan, cfg)
    if len(evidence) != len(CHECK_IDS) or any(e.execution.status != ExecStatus.COMPLETED for e in evidence):
        failures = {e.check_id: e.execution.abort_reason for e in evidence
                    if e.execution.status != ExecStatus.COMPLETED}
        raise ValueError(f"simulation evidence incomplete: {failures}")
    results = judge_plan(store, pack, plan, as_of)
    report = build_report(store, pack, plan, as_of)
    store.put(report)
    _write_json(reports_dir / f"{label}-{scenario}.json", report.to_dict())
    return {"scenario": scenario, "plan_id": plan.id, "plan_ref": plan.ref().model_dump(mode="json"),
            "report_id": report.id, "report_ref": report.ref().model_dump(mode="json"),
            "verdicts": {r.check_id: r.verdicts.security.value for r in results},
            "result_refs": [r.ref().model_dump(mode="json") for r in results]}


def run_demo(output: Path) -> dict:
    """18×3 모의 사례와 원팩 대조, 실제 저장·반출·복원·재판정을 한 번 수행한다."""
    output = Path(output)
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError("output must be an empty or new directory")
    output.mkdir(parents=True, exist_ok=True)
    owners = _modules()
    source_dir = Path(__file__).resolve().parents[3] / "packs" / "PACK-BASELINE"
    source = load_pack(source_dir)
    if source.ref().revision != 33 or source.ref().sha256 != _SOURCE_PACK_SHA256:
        raise ValueError("prototype cases require the pinned PACK-BASELINE33 snapshot")
    pack, derivation = _prototype_pack(source, owners, output / "prototype-pack")
    store = Store(output / "store")
    put_pack(store, source_dir)
    put_pack(store, pack.path)
    env = _environment(pack)
    store.put(env)
    reports = output / "reports"
    reports.mkdir()
    as_of = datetime.now(timezone.utc).replace(microsecond=0)
    runs, baseline_runs = [], []
    for scenario in SCENARIOS:
        runs.append(_execute(store, pack, env, owners, scenario, as_of, reports, "prototype"))
        baseline_runs.append(_execute(store, source, env, owners, scenario, as_of, reports, "source-pack-example"))
    expected = {"normal": "PASS", "violation": "FAIL", "missing": "UNKNOWN"}
    for run in runs:
        if set(run["verdicts"]) != set(CHECK_IDS) or set(run["verdicts"].values()) != {expected[run["scenario"]]}:
            raise ValueError(f"prototype contract mismatch: {run['scenario']}: {run['verdicts']}")
    verify_complete(store)
    manifest = export_bundle(store.root, output / "evidence.zip")
    restore_bundle(output / "evidence.zip", output / "restored")
    restored = Store(output / "restored")
    verify_complete(restored)
    for run in [*runs, *baseline_runs]:
        plan = restored.get(run["plan_id"], run["plan_ref"]["revision"])
        restored_pack = get_pack(restored, plan.inputs.pack)
        before = run["result_refs"]
        after = [r.ref().model_dump(mode="json") for r in judge_plan(restored, restored_pack, plan, as_of)]
        if before != after:
            raise ValueError("restored rejudgment changed pinned results")
        report = build_report(restored, restored_pack, plan, as_of)
        original = restored.get(run["report_id"], run["report_ref"]["revision"])
        volatile = {"id", "created_at", "content_sha256"}
        if ({k: v for k, v in report.to_dict().items() if k not in volatile}
                != {k: v for k, v in original.to_dict().items() if k not in volatile}):
            raise ValueError("restored report differs")
    summary = {"format": "gpu18-prototype/1", "data_origin": "EXAMPLE", "official": False,
               "check_ids": list(CHECK_IDS), "scenarios": list(SCENARIOS), "as_of": as_of.isoformat(),
               "prototype_pack": pack.ref().model_dump(mode="json"), "derivation": derivation,
               "runs": runs, "baseline_runs": baseline_runs, "bundle_verified": True,
               "bundle_files": len(manifest["files"]), "restored_rejudge_stable": True,
               "gpu_started": False, "real_integration_completed": False,
               "integration_required": {cid: owners[cid].INTEGRATION_REQUIRED[cid] for cid in CHECK_IDS}}
    _write_json(output / "summary.json", summary)
    return summary
