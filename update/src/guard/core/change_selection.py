"""U1 입력 차이·변경 선정·이월 자격. 실행·판정 규칙과 엔진 종류를 모른다."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import re

from guard.executors.reuse import ExecutionPreview, preview_inputs
from guard.judges import registry as judges
from guard.models import DataOrigin, ExecStatus, Mode, Side, Verdict, sha256_hex, utcnow
from guard.models.objects import CarriedOver, SelectionTrigger
from guard.store.packs import get_pack

from .select import _in_scope as _scope


@dataclass(frozen=True)
class InputDiff:
    facts: list[str] = field(default_factory=list)
    runtime: list[str] = field(default_factory=list)
    policy_values: list[str] = field(default_factory=list)
    scope_added: list[str] = field(default_factory=list)
    model_changed: bool = False
    model_id_changed: bool = False
    policy_metadata_changed: bool = False


@dataclass
class SelectionContext:
    change_type: str
    previous_plan: object | None = None
    triggers: dict[str, list[SelectionTrigger]] = field(default_factory=dict)
    carried_over: dict[str, CarriedOver] = field(default_factory=dict)
    full_scope: bool = False
    reason: str = ""


def input_diff(previous_env, current_env, previous_policy, current_policy, check_ids=()) -> InputDiff:
    """입력 의미를 비교한다. revision·시각·출처 해시 자체를 변경 종류로 쓰지 않는다."""
    oldfacts = {key: value.model_dump(mode="json") for key, value in previous_env.facts.items()}
    newfacts = {key: value.model_dump(mode="json") for key, value in current_env.facts.items()}
    oldruntime = previous_env.runtime.model_dump(mode="json")
    newruntime = current_env.runtime.model_dump(mode="json")
    keys = set(previous_policy.values) | set(current_policy.values)
    return InputDiff(
        facts=sorted(key for key in oldfacts.keys() | newfacts.keys() if oldfacts.get(key) != newfacts.get(key)),
        runtime=sorted(f"runtime.{key}" for key in oldruntime.keys() | newruntime.keys()
                       if oldruntime.get(key) != newruntime.get(key)),
        policy_values=sorted(key for key in keys if previous_policy.values.get(key) != current_policy.values.get(key)),
        scope_added=sorted(cid for cid in check_ids if not _scope(cid, previous_policy) and _scope(cid, current_policy)),
        model_changed=any(getattr(previous_env.model, key) != getattr(current_env.model, key)
                          for key in ("id", "revision", "inventory_sha256")),
        model_id_changed=previous_env.model.id != current_env.model.id,
        policy_metadata_changed=any(getattr(previous_policy, key) != getattr(current_policy, key)
                                    for key in ("exceptions", "status", "confirmed_by", "confirmed_at")),
    )


def classify_engine_change(before: str | None, after: str | None) -> str:
    """숫자 major·minor가 같고 patch 꼬리가 앞으로 갈 때만 패치다."""
    if not all(isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)*", value)
               for value in (before, after)):
        return "ENGINE_RELEASE"
    left, right = [tuple(int(part) for part in value.split(".")) for value in (before, after)]
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return "ENGINE_PATCH" if left[:2] == right[:2] and right[2:] > left[2:] else "ENGINE_RELEASE"


def selection_triggers(pack, change_type: str, change, diff: InputDiff, *, model_change_verified: bool = True):
    """팩 데이터와 적용 조건의 참조만 읽는다. 미분류 입력은 보수적으로 넓힌다."""
    rules = pack.change_selection or {}
    triggers: dict[str, list[SelectionTrigger]] = {}

    def add(cids, type_, refs):
        for cid in cids:
            if cid in pack.checks:
                trigger = SelectionTrigger(type=type_, refs=list(refs))
                if trigger not in triggers.setdefault(cid, []):
                    triggers[cid].append(trigger)

    full = change_type in {"INITIAL", "ENGINE_RELEASE"}
    facts = change.facts if change else []
    if change_type == "ENGINE_PATCH":
        add(rules.get("patch_always", []), "PATCH_ALWAYS", [])
        if any(getattr(fact, "unmapped", False) for fact in facts):
            add({cid for ids in rules.get("engine_categories", {}).values() for cid in ids},
                "UNMAPPED_ENGINE", [fact.id for fact in facts if getattr(fact, "unmapped", False)])
        for fact in facts:
            add(rules.get("engine_categories", {}).get(fact.category, []), "ENGINE_DIFF", [fact.id])
            add(fact.related_checks, "CHANGE_FACT", [fact.id])
    if diff.model_changed:
        model_categories = rules.get("model_categories", {})
        categories = {fact.category for fact in facts if fact.category in model_categories}
        if (diff.model_id_changed or not model_change_verified or not categories
                or any(getattr(fact, "unmapped", False) for fact in facts)):
            categories = set(model_categories)
        add(rules.get("model_common", []), "MODEL_COMMON", [])
        for category in sorted(categories):
            add(model_categories[category], "MODEL_DIFF", [category])
        if not model_change_verified:
            add({cid for ids in model_categories.values() for cid in ids},
                "MODEL_MANIFEST_UNCONFIRMED", ["inventory target relation not confirmed"])
    add(diff.policy_values, "POLICY_VALUE", ["values"])
    add(diff.scope_added, "POLICY_SCOPE", ["scope"])
    for key in [*diff.facts, *diff.runtime]:
        related = set(rules.get("fact_triggers", {}).get(key, []))
        related.update(cid for cid, check in pack.checks.items()
                       if any(condition.fact == key for condition in check.applies_when.all))
        if not related:
            full = True
        else:
            add(sorted(related), "ENV_INPUT", [key])
    if full:
        add(pack.manifest.checks, "FULL_SCOPE", [change_type])
    return triggers, full


def _merged(base, overlay):
    result = deepcopy(base)
    for key, value in overlay.items():
        result[key] = _merged(result.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return result


def _effective_config(cfg, mode):
    if hasattr(cfg, "raw"):
        if mode == Mode.update and getattr(cfg, "side_configs", None):
            return cfg.side_configs.get("baseline", cfg).raw
        return cfg.raw
    if mode == Mode.update:
        return _merged(cfg, cfg.get("sides", {}).get("baseline", {}))
    return cfg


def source_image_relation(change, envs) -> str:
    """CHG와 ENV 입력 메타데이터의 대조 상태. 이미지 내부 소스 측정을 뜻하지 않는다."""
    if change is None or len(envs) != 2 or change.extractor.name == "model_manifest":
        return "UNVERIFIED"
    statuses = {env.engine.source_commit_status for env in envs}
    if "MISMATCH" in statuses:
        return "MISMATCH"
    if "UNVERIFIED" in statuses:
        return "UNVERIFIED"
    complete = True
    for point, env in zip((change.baseline, change.candidate), envs):
        if env.engine.version and point.version != env.engine.version:
            return "MISMATCH"
        if point.image_digest is not None and env.engine.image_digest and point.image_digest != env.engine.image_digest:
            return "MISMATCH"
        source = env.engine.source_commit
        source_full = bool(source and re.fullmatch(r"[0-9a-f]{40}", source))
        point_full = bool(re.fullmatch(r"[0-9a-f]{40}", point.source_commit))
        if source_full and point_full and source != point.source_commit:
            return "MISMATCH"
        if (not env.engine.version or not env.engine.image_digest or not source_full or not point_full
                or source != point.source_commit):
            complete = False
    return "CONFIRMED" if complete else "UNVERIFIED"


def _source_matches(change, envs):
    return source_image_relation(change, envs) == "CONFIRMED"


def _model_manifest_matches(change, previous_env, current_env) -> bool:
    if change is None or change.extractor.name != "model_manifest":
        return False
    analysis = change.analysis or {}
    before = previous_env.model.inventory_sha256
    after = current_env.model.inventory_sha256
    return (isinstance(before, str) and re.fullmatch(r"[0-9a-f]{64}", before) is not None
            and isinstance(after, str) and re.fullmatch(r"[0-9a-f]{64}", after) is not None
            and analysis.get("baseline_inventory_sha256") == before
            and analysis.get("candidate_inventory_sha256") == after)


def _definition_hash(pack, cid):
    return next((entry.sha256 for entry in pack.manifest.files_sha256 if entry.path == f"checks/{cid}.yaml"), None)


def _prior_pair(store, previous, cid):
    item = next((item for item in previous.items if item.check_id == cid), None)
    if item is None:
        return None
    if item.carried_over is not None:
        result = store.resolve(item.carried_over.result)
        evidence = store.resolve(item.carried_over.evidence)
        return evidence, result
    side = Side.candidate if previous.mode == Mode.update else Side.single
    evidence = [evd for evd in store.find_latest("EVD", lambda value: value.plan == previous.ref())
                if evd.check_id == cid and evd.side == side]
    if not evidence:
        return None
    # 여러 EVD id가 있으면 최신 출처를 택하되 같은 시각의 모호함은 이월하지 않는다.
    evidence.sort(key=lambda evd: (evd.created_at, evd.revision), reverse=True)
    if len(evidence) > 1 and (evidence[0].created_at, evidence[0].revision) == (evidence[1].created_at, evidence[1].revision):
        return None
    evd = evidence[0]
    results = store.find_latest("RES", lambda result: result.evidence == evd.ref())
    if not results:
        return None
    results.sort(key=lambda result: (result.created_at, result.revision), reverse=True)
    return store.resolve(evd.ref()), store.resolve(results[0].ref())


def _judge_signature(check, evidence):
    executor = evidence.execution.executor
    if executor is not None and executor.default_judge:
        name, params = executor.default_judge, {}
    elif check.judge is not None:
        name, params = check.judge.plugin, check.judge.params
    else:
        return None
    plugin = judges.JUDGES.get(name)
    if plugin is None:
        return None
    from guard.models import canonical_json, sha256_hex
    return plugin.name, plugin.version, sha256_hex(canonical_json(params))


def _eligible(store, previous, oldpack, pack, cid, env, policy, cfg, as_of, read_only):
    olditem = next((item for item in previous.items if item.check_id == cid), None)
    check = pack.checks[cid]
    oldhash, newhash = _definition_hash(oldpack, cid), _definition_hash(pack, cid)
    if olditem is None or olditem.check_revision != check.revision or not oldhash or oldhash != newhash:
        return None, "CHECK_DEFINITION_CHANGED"
    pair = _prior_pair(store, previous, cid)
    if pair is None:
        return None, "NO_PRIOR_RESULT"
    evidence, result = pair
    if (result.evidence != evidence.ref() or result.check_id != cid or evidence.check_id != cid
            or result.side != evidence.side
            or evidence.data_origin == DataOrigin.EXAMPLE or result.data_origin == DataOrigin.EXAMPLE
            or evidence.execution.status != ExecStatus.COMPLETED):
        return None, "PRIOR_EVIDENCE_INELIGIBLE"
    for artifact in evidence.artifacts:
        if artifact.path != f"artifacts/{artifact.sha256}":
            return None, "PRIOR_ARTIFACT_UNVERIFIED"
        path = store.root / artifact.path
        if (not path.is_file() or path.stat().st_size != artifact.bytes
                or sha256_hex(path.read_bytes()) != artifact.sha256):
            return None, "PRIOR_ARTIFACT_MISSING_OR_CHANGED"
    oldenv = store.resolve(evidence.environment)
    if not env.engine.image_digest or oldenv.engine.image_digest != env.engine.image_digest:
        return None, "IMAGE_DIGEST_CHANGED_OR_UNKNOWN"
    if oldenv.engine.name != env.engine.name or oldenv.adapter != env.adapter:
        return None, "ENGINE_OR_ENV_ADAPTER_CHANGED"
    preview: ExecutionPreview | None = preview_inputs(check, env, policy.values.get(cid, {}), cfg)
    if preview is None:
        return None, "PROCEDURE_OR_FIXTURE_UNCONFIRMED"
    if evidence.execution.executor != preview.executor or evidence.execution.adapter != preview.adapter:
        return None, "EXECUTOR_OR_ADAPTER_CHANGED"
    if not preview.fixture_sha256 or evidence.comparability.fixture_sha256 != preview.fixture_sha256:
        return None, "FIXTURE_CHANGED_OR_UNKNOWN"
    if not preview.config_sha256 or evidence.comparability.config_sha256 != preview.config_sha256:
        return None, "EXECUTION_CONFIG_CHANGED_OR_UNKNOWN"
    signature = _judge_signature(check, evidence)
    if signature is None:
        return None, "JUDGE_UNAVAILABLE"
    if (result.judge.name, result.judge.params_sha256) != (signature[0], signature[2]):
        return None, "JUDGE_CONTRACT_CHANGED"
    if result.judge.version != signature[1]:
        if read_only:
            return None, "READ_ONLY_REJUDGMENT_PENDING"
        from .judge import judge_evidence
        result = judge_evidence(check, evidence, policy, as_of,
                                condition_origins=[condition.origin for condition in olditem.conditions], plan=previous)
        store.put(result)
    if result.verdicts.security != Verdict.PASS:
        return None, "PRIOR_SECURITY_NOT_PASS"
    return CarriedOver(result=result.ref(), evidence=evidence.ref(), image_digest=env.engine.image_digest,
                       judged_at=result.created_at, reason="unchanged inputs and verified prior security PASS"), ""


def build_context(store, previous_plan, pack, envs, policy, cfg, change=None, mode=Mode.audit,
                  as_of: datetime | None = None, read_only: bool = False) -> SelectionContext:
    """정확한 이전 PLAN·보존 팩·EVD/RES로 이월을 사전 확인한다.

    read_only는 준비 명령용이다. 판정기 변경 시 쓰기 없이 재판정 필요를 남긴다.
    이 함수의 사전 재판정만 RES를 만들 수 있고 select/이월 단계는 만들지 않는다.
    """
    mode = Mode(mode)
    if previous_plan is None:
        return SelectionContext("INITIAL", full_scope=True, reason="NO_PREVIOUS_PLAN")
    previous = store.resolve(previous_plan) if hasattr(previous_plan, "kind") and not hasattr(previous_plan, "inputs") else previous_plan
    if previous.kind != "PLAN" or not previous.hash_ok():
        raise ValueError("previous_plan must resolve to a sealed PLAN")
    oldenvs = [store.resolve(ref) for ref in previous.inputs.environments]
    oldenv = oldenvs[-1]
    oldpolicy = store.resolve(previous.inputs.policy)
    current = envs[-1]
    diff = input_diff(oldenv, current, oldpolicy, policy, pack.manifest.checks)
    if mode == Mode.update:
        change_type = classify_engine_change(envs[0].engine.version, envs[-1].engine.version)
        if not envs[0].engine.image_digest or envs[0].engine.image_digest == envs[-1].engine.image_digest:
            change_type = "ENGINE_RELEASE"
    elif (not current.engine.image_digest or oldenv.engine.image_digest != current.engine.image_digest
          or oldenv.engine.name != current.engine.name):
        change_type = "ENGINE_RELEASE"
    else:
        change_type = "MODEL" if diff.model_changed else "INPUT"
    triggers, full = selection_triggers(pack, change_type, change, diff,
                                      model_change_verified=_model_manifest_matches(change, oldenv, current))
    context = SelectionContext(change_type, previous.ref(), triggers, full_scope=full)
    if change_type == "ENGINE_PATCH" and not _source_matches(change, envs):
        context.full_scope = True
        context.reason = "SOURCE_IMAGE_RELATION_UNVERIFIED_OR_MISMATCH"
    if not pack.change_selection:
        context.full_scope = True
        context.reason = "NO_SELECTION_RULES"
    if context.full_scope:
        return context
    oldpack = get_pack(store, previous.inputs.pack)
    effective = _effective_config(cfg, mode)
    target = envs[0] if mode == Mode.update else current
    now = as_of or utcnow()
    from .select import evaluate_check
    for cid in pack.manifest.checks:
        check = pack.checks[cid]
        if check.retired or cid in triggers or not _scope(cid, policy):
            continue
        if evaluate_check(check, envs)[0].value == "NOT_APPLICABLE":
            continue
        carried, reason = _eligible(store, previous, oldpack, pack, cid, target, policy, effective, now, read_only)
        if carried is not None:
            context.carried_over[cid] = carried
        else:
            context.triggers.setdefault(cid, []).append(SelectionTrigger(type="REUSE_REJECTED", refs=[reason]))
    return context
