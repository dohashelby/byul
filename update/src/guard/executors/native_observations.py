"""실측 생산자 자료의 형식 경계. EXAMPLE 분석기를 실측 분석기로 승격하지 않는다.

실제 수집은 owned_capture의 별도 파일/전송/소유 신원을 보존한다. 이 검증은
생산자의 주장과 파일의 대응만 검사하며 인가나 실제 소비를 판정하지 않는다.
"""
from __future__ import annotations

import re

from guard.models import canonical_json, sha256_hex


_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("NATIVE_OBSERVATION_FIELDS_INVALID")


def _text(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def validate_capture(value, *, kind, env_ref, run_id, image):
    keys = {"schema", "run_id", "environment_ref", "image", "events"}
    if kind == "template" and type(value) is dict and "sensor_status" in value:
        keys.add("sensor_status")
    _keys(value, keys)
    if (value["schema"] != f"guard-{kind}-native-observations/1"
            or value["run_id"] != run_id or value["environment_ref"] != env_ref or value["image"] != image
            or type(value["events"]) is not list or not 1 <= len(value["events"]) <= 64):
        raise ValueError("NATIVE_OBSERVATION_BINDING_INVALID")
    if kind == "template":
        if "sensor_status" in value:
            status = value["sensor_status"]
            _keys(status, ("schema", "attempted_events", "failed_events", "emission_complete", "diagnostic_codes"))
            attempts, failures = status["attempted_events"], status["failed_events"]
            if (status["schema"] != "guard-template-sensor-emission/1"
                    or type(attempts) is not int or not 1 <= attempts <= 64
                    or type(failures) is not int or not 0 <= failures <= attempts
                    or not attempts - failures <= len(value["events"]) <= attempts
                    or type(status["emission_complete"]) is not bool
                    or status["emission_complete"] != (failures == 0)
                    or status["diagnostic_codes"] != (["TEMPLATE_SENSOR_EMIT_FAILED"] if failures else [])):
                raise ValueError("NATIVE_TEMPLATE_SENSOR_STATUS_INVALID")
        for event in value["events"]:
            stage = event.get("kind") if type(event) is dict else None
            if stage == "template_read":
                _keys(event, ("kind", "raw_sha256", "decoded_sha256", "raw_bytes", "decoded_bytes",
                             "loader_sha256", "process_id", "buffer_returned", "selection_observed",
                             "apply_observed", "generate_handoff_observed", "worker_consumption_verified"))
                if (any(not _sha(event[key]) for key in ("raw_sha256", "decoded_sha256", "loader_sha256"))
                        or any(type(event[k]) is not int or not 0 < event[k] <= 8192
                               for k in ("raw_bytes", "decoded_bytes"))
                        or type(event["process_id"]) is not int or not 0 < event["process_id"] <= 2**31 - 1
                        or event["buffer_returned"] is not True
                        or any(event[k] is not False for k in ("selection_observed", "apply_observed",
                                                             "generate_handoff_observed", "worker_consumption_verified"))):
                    raise ValueError("NATIVE_TEMPLATE_READ_INVALID")
            elif stage in {"template_selection", "template_apply"}:
                keys = {"kind", "argument_sha256", "source_sha256", "outcome"}
                if "returned_sha256" in event:
                    keys.add("returned_sha256")
                _keys(event, keys)
                if (not _sha(event["argument_sha256"]) or not _sha(event["source_sha256"])
                        or event["outcome"] not in ("error", "returned", "deferred")
                        or ("returned_sha256" in event and (
                            stage != "template_selection" or event["outcome"] != "returned"
                            or not _sha(event["returned_sha256"])))):
                    raise ValueError("NATIVE_TEMPLATE_CALL_INVALID")
            else:
                raise ValueError("NATIVE_TEMPLATE_STAGE_UNSUPPORTED")
    elif kind == "route":
        seen = set()
        for event in value["events"]:
            _keys(event, ("event_id", "producer", "ingress_id", "upstream_id", "request_sha256",
                         "principal_sha256", "external_method", "external_path", "auth_method", "auth_path",
                         "auth_operation", "auth_decision", "rewritten_method", "rewritten_path",
                         "handler_id", "handler_operation", "handler_outcome"))
            if (any(not _text(event[k]) for k in ("event_id", "producer", "ingress_id", "upstream_id",
                                                "auth_operation", "handler_id", "handler_operation"))
                    or event["event_id"] in seen or not _sha(event["request_sha256"]) or not _sha(event["principal_sha256"])
                    or event["external_method"] != "POST"
                    or event["auth_method"] not in ("GET", "POST")
                    or event["rewritten_method"] != "POST"
                    or any(type(event[k]) is not str or not re.fullmatch(r"/[A-Za-z0-9/_-]{1,255}", event[k])
                           for k in ("external_path", "auth_path", "rewritten_path"))
                    or event["auth_decision"] not in ("allow", "deny", "unknown")
                    or event["handler_outcome"] not in ("entered", "rejected", "unobserved")):
                raise ValueError("NATIVE_ROUTE_EVENT_INVALID")
            seen.add(event["event_id"])
    else:
        raise ValueError("NATIVE_OBSERVATION_KIND_UNSUPPORTED")
    result = {"schema": f"guard-{kind}-capture-summary/1", "events_seen": len(value["events"]),
            "record_sha256": sha256_hex(canonical_json(value)), "producer_authenticated": False,
            "independent_source_verified": False, "security": "UNKNOWN", "functional": "UNKNOWN",
            "actual_consumption_verified": False}
    if kind == "template" and "sensor_status" in value:
        # An unauthenticated producer's delivery claim, never evidence of full collection/consumption.
        status = value["sensor_status"]
        result["reported_sensor_status"] = {**status, "diagnostic_codes": list(status["diagnostic_codes"])}
        result["collection_errors"] = list(status["diagnostic_codes"])
    return result
