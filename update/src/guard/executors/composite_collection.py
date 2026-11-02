"""복합 프로파일: 하위 수집을 세션별로 따로 실행하고 결과만 한 EVD로 모은다.

시간이 가깝다는 이유로 하위 결과를 합치지 않는다. 먼저 수집한 사실은 뒤 수집이 실패해도 남는다.
"""
from __future__ import annotations

from guard.models import CheckDef, DataOrigin, ExecStatus, ORIGIN_STRENGTH, utcnow
from guard.models.objects import NormalControl, Observation

from .base import EvidenceDraft


def sub_check(check, profile):
    """하위 실행기가 자기 프로파일로 검증하도록 정의의 프로파일만 바꾼 사본."""
    return CheckDef.model_validate({**check.model_dump(mode="json"), "collection_profile": profile})


def select_policy(values, keys):
    return {key: values[key] for key in keys if key in values}


def merge_drafts(check, drafts, failures):
    """drafts: {이름: EvidenceDraft}. failures: {이름: 고정 진단 코드}. 예외 원문은 받지 않는다."""
    now = utcnow()
    completed = {name: draft for name, draft in drafts.items()}
    observations, artifacts, cleanup, config = {}, [], [], {}
    for name, draft in completed.items():
        observations.update(draft.observations)
        artifacts.extend(draft.artifacts)
        cleanup.extend(draft.cleanup_errors)
        config[name] = draft.config
    origins = [draft.data_origin for draft in completed.values()] or [DataOrigin.EXAMPLE]
    origin = min(origins, key=lambda value: ORIGIN_STRENGTH[value])
    gap = bool(failures or any(draft.status != ExecStatus.COMPLETED for draft in completed.values()))
    normals = [draft.normal_control for draft in completed.values()]
    normal = NormalControl(
        attempted=any(row.attempted for row in normals),
        succeeded=None if not normals or any(row.succeeded is None for row in normals) else all(row.succeeded for row in normals),
        detail=" / ".join(row.detail for row in normals if row.detail) or "하위 수집 없음")
    aborts = [f"{name}:{code}" for name, code in sorted(failures.items())]
    aborts += [f"{name}:{draft.abort_reason}" for name, draft in completed.items() if draft.abort_reason]
    observations["composite_subcollections"] = Observation(value={
        "completed": sorted(name for name, draft in completed.items() if draft.status == ExecStatus.COMPLETED),
        "incomplete": sorted(name for name, draft in completed.items() if draft.status != ExecStatus.COMPLETED),
        "failed": dict(sorted(failures.items()))}, collected=True)
    started = [draft.started_at for draft in completed.values() if draft.started_at]
    finished = [draft.finished_at for draft in completed.values() if draft.finished_at]
    return EvidenceDraft(
        status=ExecStatus.ABORTED if gap else ExecStatus.COMPLETED, data_origin=origin, normal_control=normal,
        challenges=[row for draft in completed.values() for row in draft.challenges], observations=observations,
        artifacts=artifacts, config=config, started_at=min(started) if started else now,
        finished_at=max(finished) if finished else now, abort_reason="; ".join(aborts) or None, cleanup_errors=cleanup)
