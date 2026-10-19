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


ROUTES = ["/v1/models", "/v1/completions", "/invocations"]


FUNCTIONAL_PROBE = "POST /v1/completions"


def req01_obs(direct=None, gateway=None, registered=None, tested=None, trusted=True, missing=(),
              functional=None, planned=None) -> dict:
    """REQ-01 관측(경로별). 기본값은 정상 통과 형태(직통 401, Gateway 200, 전 경로 시험, 기능 표본 통과)."""
    functional = {FUNCTIONAL_PROBE: "pass"} if functional is None else functional
    planned = list(functional) if planned is None else planned
    registered = ROUTES if registered is None else registered
    tested = registered if tested is None else tested
    direct = {r: 401 for r in tested} if direct is None else direct
    gateway = {r: 200 for r in tested} if gateway is None else gateway
    obs = {
        "routes_registered": {"value": registered, "collected": True},
        "routes_tested": {"value": tested, "collected": True},
        "direct_outcomes": {"value": direct, "collected": True},
        "gateway_outcomes": {"value": gateway, "collected": True},
        "trusted_origin_reachable": {"value": trusted, "collected": True},
        "functional_probes_planned": {"value": planned, "collected": True},
        "functional_probe_results": {"value": functional, "collected": True},
    }
    for k in missing:
        obs[k] = {"value": None, "collected": False}
    return obs


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
        else req01_obs(),
        **kw,
    ).seal()
