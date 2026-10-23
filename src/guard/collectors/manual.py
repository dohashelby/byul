"""수동 증거 입력: 빈 양식 만들기(build_template)와 채운 양식 한 번에 넣기(import_evidence).

사람의 판정은 신고된 관측이다(SPEC §4.5): EVD의 `declared_verdicts`·`declared_basis`·`declared_observation_notes`로
들어가고, 판정기(실행기 메타데이터의 기본 판정기 `declared`)가 PASS 관문을 적용해 RES를 만든다.
- data_origin은 항상 DECLARED(INV-02). 입력자·시각은 execution.operator(인증되지 않은 자기 신고).
- 선정되지 않은 점검은 거부(INV-10). 자동 실행기가 있는 점검은 override_reason이 있을 때만 받는다.
- 양식·첨부 읽기·초안 생성은 전부 먼저, EVD 객체는 한 SQLite 트랜잭션으로 저장한다.
- I/O 실패 시 참조되지 않은 새 내용 주소 blob은 남을 수 있다. 기존 증거는 삭제·재작성하지 않는다.
양식 필드는 reference/framework/data/result_template.json을 참고했다(verdict 3축, normal_control_result,
evidence, limitations, started_at/finished_at).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from guard.executors import registry
from guard.manual_policy import OBSERVATION, blank_evidence, normalize, parameter
from guard.models import (
    DataOrigin,
    Evidence,
    ExecStatus,
    Mode,
    Pack,
    Plan,
    Ref,
    Side,
    canonical_json,
    new_id,
    sha256_hex,
    utcnow,
)
from guard.models.objects import Artifact, Execution, ExecutorInfo, Operator
from guard.store import Store

TEMPLATE_VERSION = "guard-manual-evidence/2"
LEGACY_TEMPLATE_VERSION = "guard-manual-evidence/1"
EXECUTOR_NAME = "manual-entry"
EXECUTOR_VERSION = "0.3.0"
NORMAL_RESULTS = ("succeeded", "failed", "not_attempted")
VERDICTS = ("PASS", "FAIL", "UNKNOWN")
AXES = ("security", "functional", "performance")

HEADER = """\
# guard 수동 증거 양식 ({version})
# 채울 항목: verdicts(PASS|FAIL|UNKNOWN), normal_control.result(succeeded|failed|not_attempted),
#   challenges[].attempted(true|false), observation_notes[].answer(관측 설명), basis(근거 설명),
#   attachments(이 파일 기준 상대 경로, PASS는 1개 이상 필요), entered_by(입력자).
# 자동 실행기가 있는 점검(automated: true)은 override_reason(사유)이 있어야 받는다.
# verdicts를 모두 비워 둔 항목은 건너뛴다. 사람이 입력한 값은 DECLARED(자기 신고)로 기록된다.
# v2 policy/pack은 PLAN의 정확한 참조다. policy_evidence는 항목·case별 관측과
# evidence: [{{attachment: <현재 attachments의 경로>, locator: <위치>, basis: <근거>}}]를 연결한다.
# 관측 null/빈 evidence는 미확인이다. 정책 항목·case를 성공 결과로 해석하지 않는다.
"""


class ManualEvidenceError(ValueError):
    pass


def _sides(plan: Plan) -> list[Side]:
    return [Side.single] if plan.mode == Mode.audit else [Side.baseline, Side.candidate]


def build_template(store: Store, pack: Pack, plan: Plan, include_automated: bool = False) -> dict:
    """선정된 점검 중 수동 입력이 필요한 것(자동 실행기 없음, include_automated면 전부)의 빈 양식."""
    if plan.inputs.pack != pack.ref():
        raise ManualEvidenceError("pack does not match the one the plan used")
    policy = store.resolve(plan.inputs.policy)
    entries = []
    for item in plan.items:
        if not item.selected:
            continue
        check = pack.checks[item.check_id]
        automated = check.executor is not None
        if automated and not include_automated:
            continue
        for side in _sides(plan):
            entries.append({
                "check_id": check.id,
                "side": side.value,
                "question": check.question,
                "purpose": check.purpose,
                "automated": automated,
                "override_reason": None,
                "normal_control": {"description": check.normal_control, "result": None},
                "challenges": [{"step": s, "attempted": None} for s in check.challenge_steps],
                "required_observations": [{"key": o.key, "type": o.type, "value": None, "collected": None}
                                          for o in check.required_observations],
                "observation_notes": [{"item": o, "answer": None} for o in check.required_observations_prose],
                "satisfied_if": check.satisfied_if,
                "fail_criteria": check.fail_criteria,
                "unknown_criteria": check.unknown_criteria,
                "verdicts": {a: None for a in AXES},
                "basis": None,
                "limitations": [],
                "attachments": [],
                "entered_by": None,
                "entered_at": None,
                "started_at": None,
                "finished_at": None,
            })
            if parameter(check):
                entries[-1]["policy_evidence"] = blank_evidence(check, policy.values.get(check.id, {}))
    return {"template": TEMPLATE_VERSION,
            "plan": {"id": plan.id, "revision": plan.revision, "sha256": plan.content_sha256},
            "policy": plan.inputs.policy.model_dump(mode="json"),
            "pack": plan.inputs.pack.model_dump(mode="json"),
            "checks": entries}


def dump_template(doc: dict) -> str:
    return HEADER.format(version=TEMPLATE_VERSION) + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=1000)


def _dt(value: Any, field: str, where: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ManualEvidenceError(f"{where}: {field} is not RFC3339: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _filled(e: dict) -> bool:
    return any((e.get("verdicts") or {}).get(a) not in (None, "") for a in AXES)


def read_document(path: Path) -> dict:
    """Read one immutable-in-memory form and validate its outer structure."""
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManualEvidenceError(f"cannot read manual evidence form ({type(exc).__name__})") from exc
    return validate_document(doc)


def validate_document(doc: Any) -> dict:
    if not isinstance(doc, dict) or doc.get("template") not in (TEMPLATE_VERSION, LEGACY_TEMPLATE_VERSION):
        raise ManualEvidenceError("unsupported or missing manual evidence template version")
    p = doc.get("plan")
    if (not isinstance(p, dict) or not isinstance(p.get("id"), str)
            or type(p.get("revision")) is not int or p["revision"] < 1 or not isinstance(p.get("sha256"), str)):
        raise ManualEvidenceError("plan must contain id, positive integer revision and sha256")
    if doc["template"] == TEMPLATE_VERSION:
        for name, kind in (("policy", "POL"), ("pack", "PACK")):
            try:
                ref = Ref.model_validate(doc.get(name))
                if ref.kind != kind:
                    raise ValueError("wrong reference kind")
            except (TypeError, ValueError) as exc:
                raise ManualEvidenceError(f"v2 {name} requires a complete {kind} Ref") from exc
    entries = doc.get("checks")
    if not isinstance(entries, list):
        raise ManualEvidenceError("checks must be a list")
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("check_id"), str)
                or not isinstance(entry.get("side"), str) or not isinstance(entry.get("verdicts"), dict)):
            raise ManualEvidenceError("each check needs check_id, side and a verdicts mapping")
        key = (entry["check_id"], entry["side"])
        if key in seen:
            raise ManualEvidenceError(f"duplicate check/side: {key[0]}/{key[1]}")
        seen.add(key)
    return doc


def _validate(entry: dict, pack: Pack, plan: Plan, base: Path, *, values: dict, v2: bool) -> dict:
    cid = entry.get("check_id")
    where = f"{cid}/{entry.get('side')}"
    try:
        item = plan.item(cid)
    except KeyError:
        raise ManualEvidenceError(f"{where}: check is not in the plan") from None
    if not item.selected:
        raise ManualEvidenceError(f"{where}: check is not selected in the plan (INV-10)")
    if entry.get("side") not in [s.value for s in _sides(plan)]:
        raise ManualEvidenceError(f"{where}: side must be one of {[s.value for s in _sides(plan)]}")
    check = pack.checks[cid]
    reason = entry.get("override_reason")
    if reason is not None and not isinstance(reason, str):
        raise ManualEvidenceError(f"{where}: override_reason must be text")
    if check.executor is not None and not (reason or "").strip():
        raise ManualEvidenceError(f"{where}: check has an automated executor; override_reason is required")
    by = entry.get("entered_by")
    if not isinstance(by, str) or not by.strip():
        raise ManualEvidenceError(f"{where}: entered_by is required")
    verdicts = {}
    for a in AXES:
        v = (entry.get("verdicts") or {}).get(a)
        if v in (None, ""):
            v = "UNKNOWN"
        if v not in VERDICTS:
            raise ManualEvidenceError(f"{where}: verdict {a}={v!r} must be one of {VERDICTS}")
        verdicts[a] = v
    if not isinstance(entry.get("normal_control"), dict):
        raise ManualEvidenceError(f"{where}: normal_control must be a mapping")
    nc = entry["normal_control"].get("result")
    if nc not in NORMAL_RESULTS:
        raise ManualEvidenceError(f"{where}: normal_control.result must be one of {NORMAL_RESULTS}")
    steps = entry.get("challenges") if entry.get("challenges") is not None else []
    if not isinstance(steps, list) or any(not isinstance(c, dict) for c in steps):
        raise ManualEvidenceError(f"{where}: challenges must be a list of mappings")
    if [c.get("step") for c in steps] != list(check.challenge_steps):
        raise ManualEvidenceError(f"{where}: challenge steps must be exactly the CheckDef steps (no additions or removals)")
    for c in steps:
        if not isinstance(c.get("attempted"), bool):
            raise ManualEvidenceError(f"{where}: challenge attempted must be true/false: {c.get('step')!r}")
    observations = entry.get("required_observations") if entry.get("required_observations") is not None else []
    if not isinstance(observations, list) or any(not isinstance(o, dict) for o in observations):
        raise ManualEvidenceError(f"{where}: required_observations must be a list of mappings")
    if any(not isinstance(o.get("key"), str) for o in observations):
        raise ManualEvidenceError(f"{where}: observation keys must be text")
    obs_in = {o.get("key"): o for o in observations}
    if len(obs_in) != len(observations):
        raise ManualEvidenceError(f"{where}: duplicate required_observations key")
    if set(obs_in) != {o.key for o in check.required_observations}:
        raise ManualEvidenceError(f"{where}: required_observations keys must match the CheckDef")
    for observation in observations:
        collected = observation.get("collected")
        if collected is not None and type(collected) is not bool:
            raise ManualEvidenceError(f"{where}: collected must be true, false or null (not quoted text or a number)")
    notes = entry.get("observation_notes") if entry.get("observation_notes") is not None else []
    if not isinstance(notes, list) or any(not isinstance(o, dict) for o in notes):
        raise ManualEvidenceError(f"{where}: observation_notes must be a list of mappings")
    if any(not isinstance(o.get("item"), str) for o in notes):
        raise ManualEvidenceError(f"{where}: observation note items must be text")
    notes_in = {o.get("item"): o.get("answer") for o in notes}
    if len(notes_in) != len(notes):
        raise ManualEvidenceError(f"{where}: duplicate observation_notes item")
    if any(answer is not None and not isinstance(answer, str) for answer in notes_in.values()):
        raise ManualEvidenceError(f"{where}: observation note answers must be text or null")
    if set(notes_in) != set(check.required_observations_prose):
        raise ManualEvidenceError(f"{where}: observation_notes items must match the CheckDef")
    files = []
    attachments = entry.get("attachments") if entry.get("attachments") is not None else []
    if not isinstance(attachments, list) or any(not isinstance(s, str) for s in attachments):
        raise ManualEvidenceError(f"{where}: attachments must be a list of paths")
    if entry.get("basis") is not None and not isinstance(entry["basis"], str):
        raise ManualEvidenceError(f"{where}: basis must be text or null")
    limitations = entry.get("limitations") if entry.get("limitations") is not None else []
    if not isinstance(limitations, list) or any(not isinstance(s, str) for s in limitations):
        raise ManualEvidenceError(f"{where}: limitations must be a list of text")
    for rel in attachments:
        path = (base / str(rel)).resolve()
        if not path.is_file():
            raise ManualEvidenceError(f"{where}: attachment not found: {rel}")
        files.append((str(rel), path))
    try:
        if not v2 and "policy_evidence" in entry:
            raise ValueError("policy_evidence requires a v2 form")
        normalize(check, values, entry.get("policy_evidence"), dict.fromkeys(attachments))
    except ValueError as exc:
        raise ManualEvidenceError(f"{where}: {exc}") from exc
    return {"entry": entry, "check": check, "verdicts": verdicts, "normal": nc, "obs": obs_in, "notes": notes_in,
            "files": files, "by": by.strip(), "at": _dt(entry.get("entered_at"), "entered_at", where),
            "started": _dt(entry.get("started_at"), "started_at", where),
            "finished": _dt(entry.get("finished_at"), "finished_at", where)}


def import_evidence(store: Store, pack: Pack, path: Path, now: datetime | None = None, *, document: dict | None = None) -> dict:
    """채운 양식을 검증하고(전부), 통과하면 점검마다 DECLARED EVD를 새 revision으로 넣는다."""
    path = Path(path)
    doc = read_document(path) if document is None else validate_document(document)
    p = doc["plan"]
    plan: Plan = store.resolve(Ref(kind="PLAN", id=p["id"], revision=p["revision"], sha256=p["sha256"]))
    if plan.kind != "PLAN":
        raise ManualEvidenceError("form must reference a PLAN")
    if plan.inputs.pack != pack.ref():
        raise ManualEvidenceError("pack does not match the one the plan used")
    v2 = doc["template"] == TEMPLATE_VERSION
    if v2 and (Ref.model_validate(doc["policy"]) != plan.inputs.policy
               or Ref.model_validate(doc["pack"]) != plan.inputs.pack):
        raise ManualEvidenceError("v2 policy/pack Ref does not match the pinned PLAN inputs")
    policy = store.resolve(plan.inputs.policy)
    now = now or utcnow()
    entries = doc.get("checks") or []
    skipped = [e.get("check_id") for e in entries if not _filled(e)]
    valid = [_validate(e, pack, plan, path.parent, values=policy.values.get(e["check_id"], {}), v2=v2)
             for e in entries if _filled(e)]  # 전부 검증 먼저

    plan_ref = plan.ref()
    env_refs = plan.inputs.environments
    existing = {(e.check_id, e.side): e for e in store.find_latest("EVD", lambda e: e.plan == plan_ref)}
    default_judge = registry.EXECUTOR_DEFAULT_JUDGES.get(EXECUTOR_NAME)
    imported = []
    prepared = []
    payloads: dict[str, bytes] = {}
    for v in valid:
        e, check = v["entry"], v["check"]
        side = Side(e["side"])
        at = v["at"] or now
        artifacts = []
        attachment_hashes = {}
        for rel, fpath in v["files"]:
            try:
                data = fpath.read_bytes()  # Read every attachment before any write.
            except OSError as exc:
                raise ManualEvidenceError(f"cannot read attachment ({type(exc).__name__})") from exc
            digest = sha256_hex(data)
            attachment_hashes[rel] = digest
            payloads[digest] = data
            artifacts.append(Artifact(name=Path(rel).name, path=f"artifacts/{digest}", sha256=digest, bytes=len(data),
                                      collected_at=at, collector=f"{EXECUTOR_NAME}/{v['by']}"))
        observations: dict[str, dict] = {}
        for o in check.required_observations:
            given = v["obs"][o.key]
            collected = given.get("collected")
            collected = (given.get("value") is not None) if collected is None else collected
            observations[o.key] = {"value": given.get("value") if collected else None, "collected": collected}
        observations["declared_verdicts"] = {"value": v["verdicts"], "collected": True}
        observations["declared_basis"] = {"value": e.get("basis"), "collected": e.get("basis") is not None}
        observations["declared_observation_notes"] = {"value": v["notes"], "collected": True}
        observations["declared_limitations"] = {"value": list(e.get("limitations") or []), "collected": True}
        if v2:
            normalized = normalize(check, policy.values.get(check.id, {}), e.get("policy_evidence"), attachment_hashes)
            if normalized is not None:
                observations[OBSERVATION] = {"value": normalized, "collected": True}
        config = {k: e.get(k) for k in ("check_id", "side", "verdicts", "basis", "override_reason")}
        config["attachments"] = [a.sha256 for a in artifacts]
        if OBSERVATION in observations:
            config[OBSERVATION] = observations[OBSERVATION]["value"]
        prev = existing.get((check.id, side))
        env_ref = env_refs[0] if side in (Side.single, Side.baseline) else env_refs[1]
        attempted_nc = v["normal"] != "not_attempted"
        evd = Evidence(
            id=prev.id if prev else new_id("EVD"),
            revision=prev.revision + 1 if prev else 1,
            data_origin=DataOrigin.DECLARED,
            refs=[plan_ref, env_ref],
            plan=plan_ref,
            check_id=check.id,
            side=side,
            environment=env_ref,
            execution=Execution(
                status=ExecStatus.COMPLETED,
                executor=ExecutorInfo(name=EXECUTOR_NAME, version=EXECUTOR_VERSION,
                                      config_sha256=sha256_hex(canonical_json(config)), default_judge=default_judge),
                operator=Operator(by=v["by"], at=at),
                override_reason=(e.get("override_reason") or None) if check.executor is not None else None,
                started_at=v["started"], finished_at=v["finished"],
            ),
            normal_control={"attempted": attempted_nc,
                            "succeeded": (v["normal"] == "succeeded") if attempted_nc else None,
                            "detail": f"declared: {v['normal']} — {(e.get('normal_control') or {}).get('description', '')}"},
            challenges=[{"step": c["step"], "attempted": c["attempted"]} for c in e["challenges"]],
            observations=observations,
            artifacts=artifacts,
        ).seal()
        prepared.append(evd)
        imported.append(check.id)
    try:
        for data in payloads.values():
            store.put_artifact(data)
        store.put_many(prepared)
    except Exception as exc:
        # Content-addressed blobs may remain after an I/O failure. No EVD batch
        # is partially committed, and existing blobs are never deleted/rewritten.
        raise ManualEvidenceError(f"no evidence objects imported ({type(exc).__name__})") from exc
    return {"imported": imported, "skipped": skipped, "plan": plan.id}
