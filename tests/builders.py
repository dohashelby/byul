"""테스트용 객체 생성기. 모든 객체는 EXAMPLE 자료다."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from guard.models import (
    DataOrigin,
    Environment,
    Evidence,
    ExecStatus,
    Policy,
    Ref,
    load_pack,
)

ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "packs/PACK-BASELINE"
T0 = datetime(2026, 10, 19, 0, 0, 0, tzinfo=timezone.utc)

REQ01_STEPS = ["실제 route 목록 확보", "키 없음·오류·허용키로 Gateway와 직통 경로 비교", "handler 실행과 정책 거부를 대조"]


def dummy_ref(kind: str, id_: str | None = None) -> Ref:
    return Ref(kind=kind, id=id_ or f"{kind}-TEST", revision=1, sha256="0" * 64)


def baseline_pack():
    return load_pack(PACK_DIR)


def environment(facts: dict[str, dict] | None = None, origin: DataOrigin = DataOrigin.EXAMPLE, **kw) -> Environment:
    return Environment(
        id=kw.pop("id", "ENV-TEST"),
        created_at=T0,
        data_origin=origin,
        engine={"name": "engine-x", "version": "1.0.0"},
        facts=facts or {},
        **kw,
    ).seal()


def policy(pack_ref: Ref | None = None, **kw) -> Policy:
    data: dict[str, Any] = dict(
        id="POL-TEST",
        created_at=T0,
        data_origin=DataOrigin.EXAMPLE,
        status="DRAFT",
        pack=pack_ref or dummy_ref("PACK", "PACK-BASELINE"),
        values={"REQ-01": {"allowed_callers": ["gateway-sa"]}},
    )
    data.update(kw)
    return Policy(**data).seal()


def evidence(
    status: ExecStatus = ExecStatus.COMPLETED,
    data_origin: DataOrigin = DataOrigin.EXAMPLE,
    normal: dict | None = None,
    attempted: bool | list[bool] = True,
    observations: dict[str, dict] | None = None,
    check_id: str = "REQ-01",
    **kw,
) -> Evidence:
    att = attempted if isinstance(attempted, list) else [attempted] * len(REQ01_STEPS)
    return Evidence(
        id=kw.pop("id", "EVD-TEST"),
        created_at=T0,
        data_origin=data_origin,
        plan=dummy_ref("PLAN"),
        check_id=check_id,
        side=kw.pop("side", "single"),
        environment=dummy_ref("ENV"),
        execution={"status": status},
        normal_control=normal if normal is not None else {"attempted": True, "succeeded": True, "detail": ""},
        challenges=[{"step": s, "attempted": a} for s, a in zip(REQ01_STEPS, att)],
        observations=observations
        if observations is not None
        else {
            "direct_route_status": {"value": 401, "collected": True},
            "gateway_route_status": {"value": 200, "collected": True},
        },
        **kw,
    ).seal()
