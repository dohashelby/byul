"""OBS-11: 수집 장애 기록을 기한·경보·의존 판정 기준과 대조한다.

수집기 중단과 복구는 준비 절차가 맡고, 실행기는 소유 위치의 닫힌 상태 기록만 읽는다. 생산자는
미인증이므로 기록 자체가 인정한 기한 위반만 FAIL 근거가 되고 전체 충족은 주장하지 않는다.
"""
from __future__ import annotations

import re

from guard.models import canonical_json, sha256_hex
from .owned_capture import read_owned_json

PROFILE = "collection-gap-visibility-v1"
VERSION = "0.4.0"
SCHEMA = "guard-collection-status/1"
_FIELDS = {"record", "event_at_ms", "arrived_at_ms", "gap_marked_at_ms", "alert_at_ms",
           "dependent_result", "clock_uncertainty_ms"}
_DICTS = ("collection_deadline_ms", "max_clock_uncertainty_ms", "detection_deadline_ms", "alert_deadline_ms")


def validate_policy(values):
    records = values.get("required_records") if type(values) is dict else None
    if type(records) is not list or not records or any(type(r) is not str or not r for r in records):
        raise ValueError("COLLECTION_GAP_POLICY_INVALID")
    for key in _DICTS:
        table = values.get(key)
        if (type(table) is not dict or set(table) != set(records)
                or any(type(v) is not int or isinstance(v, bool) or v < 0 for v in table.values())):
            raise ValueError("COLLECTION_GAP_POLICY_INVALID")
    if type(values.get("collection_session")) is not str or re.fullmatch("[0-9a-f]{32}", values["collection_session"]) is None:
        raise ValueError("COLLECTION_GAP_POLICY_INVALID")


def source(cfg):
    value = cfg.get("collection_status")
    if type(value) is not dict or set(value) != {"container", "image"} or not all(type(v) is str and v for v in value.values()):
        raise ValueError("COLLECTION_GAP_CONFIGURATION_INVALID")
    return value


def validate_record(packet, *, environment_ref, session):
    if (type(packet) is not dict or set(packet) != {"schema", "session", "environment_ref", "events"}
            or packet["schema"] != SCHEMA or packet["session"] != session or packet["environment_ref"] != environment_ref
            or type(packet["events"]) is not list or not 1 <= len(packet["events"]) <= 64):
        raise ValueError("COLLECTION_GAP_RECORD_INVALID")
    for row in packet["events"]:
        if (type(row) is not dict or set(row) != _FIELDS or type(row["record"]) is not str
                or type(row["event_at_ms"]) is not int or type(row["clock_uncertainty_ms"]) is not int
                or any(row[k] is not None and type(row[k]) is not int for k in ("arrived_at_ms", "gap_marked_at_ms", "alert_at_ms"))
                or row["dependent_result"] not in {"PASS", "FAIL", "UNKNOWN", None}):
            raise ValueError("COLLECTION_GAP_RECORD_INVALID")
    return packet


def evaluate(packet, values):
    """기록별 정상 도착과 누락 처리 위반을 나눈다. 시각 오차를 넘긴 행은 판단하지 않는다."""
    violations, unverified, missing, normal = [], [], [], []
    rows = {}
    for row in packet["events"]:
        rows.setdefault(row["record"], []).append(row)
    for record in values["required_records"]:
        selected = rows.get(record)
        if not selected:
            missing.append(record)
            continue
        for row in selected:
            start = row["event_at_ms"]
            if row["clock_uncertainty_ms"] > values["max_clock_uncertainty_ms"][record]:
                unverified.append(record)
                continue
            arrived = row["arrived_at_ms"]
            if arrived is not None and arrived - start <= values["collection_deadline_ms"][record]:
                normal.append(record)
                continue
            marked, alerted = row["gap_marked_at_ms"], row["alert_at_ms"]
            if marked is None or marked - start > values["detection_deadline_ms"][record]:
                violations.append({"record": record, "reason": "gap_not_marked_in_time"})
            if alerted is None or alerted - start > values["alert_deadline_ms"][record]:
                violations.append({"record": record, "reason": "alert_not_raised_in_time"})
            if row["dependent_result"] == "PASS":
                violations.append({"record": record, "reason": "dependent_test_passed_without_record"})
    return {"violations": violations, "unverified": sorted(set(unverified)), "missing": missing, "normal": sorted(set(normal))}


def collect(ctx, docker, cfg):
    values, status = ctx.policy_values, source(cfg)
    path = "/guard-collection/data/" + values["collection_session"] + "/collection-status.json"
    packet, provenance, diagnostics = None, None, []
    try:
        raw, provenance = read_owned_json(docker, ctx.target, container=status["container"], image=status["image"], path=path)
        packet = validate_record(raw, environment_ref=ctx.env.ref().model_dump(mode="json"), session=values["collection_session"])
        result = evaluate(packet, values)
    except Exception:
        diagnostics.append("COLLECTION_STATUS_UNAVAILABLE")
        result = None
    collected = result is not None
    obs = {"collection_gap_violations": {"value": result and result["violations"], "collected": collected},
           "collection_records_unverified": {"value": result and result["unverified"], "collected": collected},
           "collection_records_missing": {"value": result and result["missing"], "collected": collected},
           "collection_normal_arrivals": {"value": result and result["normal"], "collected": collected},
           "collection_producer_authenticated": {"value": False, "collected": True},
           "collection_independent_event_verified": {"value": False, "collected": True}}
    summary = {"profile": PROFILE, "diagnostic_codes": diagnostics,
               "record_sha256": sha256_hex(canonical_json(packet)) if packet is not None else None,
               "file_sha256": provenance["capture_sha256"] if provenance else None,
               "fixture": {"profile": PROFILE, "schema": SCHEMA}}
    normal = {"attempted": collected, "succeeded": None, "detail": "상태 기록의 정상 도착은 생산자 미인증 주장이다."}
    return obs, normal, [{"step": step, "attempted": False} for step in ctx.check.challenge_steps], summary
