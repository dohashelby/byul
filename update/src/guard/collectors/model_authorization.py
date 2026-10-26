"""REQ-05의 실제 runtime 기록 수집 경계. 최종 엔진 모델 생산자 없이는 판정하지 않는다.

원문 JSONL·주체 이름·모델 표시명·메시지·경로는 산출물에 넣지 않는다.
주체/요청 모델/정책/실행 구간의 자료 대응은 생산자 인증이나 실제 모델 소비 증명이 아니다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from guard.executors.owned_capture import read_owned_jsonl, strict_json, validate_owned_source
from guard.models import Environment, Policy, canonical_json, load_pack, sha256_hex

_SHA = re.compile(r"[0-9a-f]{64}")
_REQUEST = re.compile(r"[0-9a-f]{32}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def load_inputs(pack_path, env_path, policy_path, context_path, target_path, source):
    def read(path):
        with Path(path).open("rb") as stream:
            return strict_json(stream.read(65537))
    pack = load_pack(pack_path)
    env, policy = Environment.model_validate(read(env_path)), Policy.model_validate(read(policy_path))
    context, target = read(context_path), read(target_path)
    if policy.pack != pack.ref() or "REQ-05" not in pack.checks:
        raise ValueError("MODEL_AUTHORIZATION_PACK_MISMATCH")
    validate_inputs(env, policy, context, target, source)
    return env, policy, context, target


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("MODEL_AUTHORIZATION_FIELDS_INVALID")


def _id(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _time(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError("MODEL_AUTHORIZATION_TIME_INVALID")
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if time.tzinfo is None:
        raise ValueError("MODEL_AUTHORIZATION_TIME_INVALID")
    return time


def _text_sha(value):
    if type(value) is not str or not 1 <= len(value) <= 256:
        raise ValueError("MODEL_AUTHORIZATION_TEXT_INVALID")
    return sha256_hex(value.encode("utf-8"))


def validate_inputs(env, policy, context, target, source):
    """봉인된 Guard Ref와 별도의 runtime 정책 파일 bytes Ref를 명시적으로 대조한다."""
    _keys(context, ("schema", "run_id", "environment_ref", "policy_ref", "runtime_policy",
                    "observation_session_id", "target_id", "instance_id", "window", "cases"))
    if (context["schema"] != "guard-fixed-model-authorization-context/1"
            or not env.hash_ok() or not policy.hash_ok()
            or context["environment_ref"] != env.ref().model_dump(mode="json")
            or context["policy_ref"] != policy.ref().model_dump(mode="json")
            or not all(_id(context[k]) for k in ("run_id", "observation_session_id", "target_id", "instance_id"))
            or type(target) is not dict or target.get("run_id") != context["run_id"]
            or type(target.get("engine")) is not dict
            or target["engine"].get("image_digest") != env.engine.image_digest
            or type(env.engine.image_digest) is not str
            or not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", env.engine.image_digest)):
        raise ValueError("MODEL_AUTHORIZATION_BINDING_INVALID")
    validate_owned_source(**source)
    runtime = context["runtime_policy"]
    _keys(runtime, ("policy_id", "policy_version", "sha256"))
    if not _id(runtime["policy_id"]) or not _id(runtime["policy_version"]) or not _sha(runtime["sha256"]):
        raise ValueError("MODEL_AUTHORIZATION_RUNTIME_POLICY_INVALID")
    _keys(context["window"], ("start", "end"))
    start, end = (_time(context["window"][k]) for k in ("start", "end"))
    if not 0 < (end - start).total_seconds() <= 600:
        raise ValueError("MODEL_AUTHORIZATION_WINDOW_INVALID")
    model_sha = _text_sha(env.model.id)
    values = policy.values.get("REQ-05")
    _keys(values, ("principal_model_grants",))
    grants = values["principal_model_grants"]
    if type(grants) is not list or not 1 <= len(grants) <= 64:
        raise ValueError("MODEL_AUTHORIZATION_GRANTS_REQUIRED")
    pairs = set()
    for grant in grants:
        _keys(grant, ("principal_sha256", "model_sha256"))
        if not _sha(grant["principal_sha256"]) or not _sha(grant["model_sha256"]):
            raise ValueError("MODEL_AUTHORIZATION_GRANT_INVALID")
        pair = (grant["principal_sha256"], grant["model_sha256"])
        if pair in pairs:
            raise ValueError("MODEL_AUTHORIZATION_GRANT_DUPLICATE")
        pairs.add(pair)
    cases = context["cases"]
    if type(cases) is not list or not 2 <= len(cases) <= 8:
        raise ValueError("MODEL_AUTHORIZATION_CASE_BOUND")
    ids, requests, membership = set(), set(), set()
    for case in cases:
        _keys(case, ("case_id", "request_id", "principal_sha256", "requested_model_sha256"))
        if (not _id(case["case_id"]) or type(case["request_id"]) is not str or not _REQUEST.fullmatch(case["request_id"])
                or not _sha(case["principal_sha256"]) or case["requested_model_sha256"] != model_sha
                or case["case_id"] in ids or case["request_id"] in requests):
            raise ValueError("MODEL_AUTHORIZATION_CASE_INVALID")
        ids.add(case["case_id"])
        requests.add(case["request_id"])
        membership.add((case["principal_sha256"], model_sha) in pairs)
    if membership != {False, True}:
        raise ValueError("MODEL_AUTHORIZATION_NORMAL_AND_DENIED_CASE_REQUIRED")
    return pairs


def _project(record, case, context):
    """현재 runtime-mvp-record의 필요한 필드만 추린다. 원문 extra 필드는 버린다."""
    if (record.get("schema_version") != "runtime-mvp-record/1.0"
            or record.get("phase") != "final" or record.get("request_id") != case["request_id"]
            or record.get("run_id") != context["run_id"]
            or record.get("observation_session_id") != context["observation_session_id"]
            or record.get("target_id") != context["target_id"]
            or type(record.get("target")) is not dict
            or record["target"].get("instance_id") != context["instance_id"]
            or not _time(context["window"]["start"]) <= _time(record.get("recorded_at")) <= _time(context["window"]["end"])):
        raise ValueError("MODEL_AUTHORIZATION_RECORD_BINDING_INVALID")
    policy = record.get("policy")
    if type(policy) is not dict or any(policy.get(k) != v for k, v in context["runtime_policy"].items()):
        raise ValueError("MODEL_AUTHORIZATION_RUNTIME_POLICY_MISMATCH")
    auth, request, decision, delivery = (record.get(k) for k in ("auth", "request", "decision", "delivery"))
    if (any(type(v) is not dict for v in (auth, request, decision, delivery))
            or auth.get("state") != "authenticated"
            or _text_sha(auth.get("principal_id")) != case["principal_sha256"]
            or _text_sha(request.get("model")) != case["requested_model_sha256"]
            or decision.get("result") not in {"ALLOWED", "DENIED", "INVALID", "ENGINE_REJECTED", "ERROR"}
            or decision.get("source") not in {"gateway", "engine"}
            or type(decision.get("http_status")) is not int or not 100 <= decision["http_status"] <= 599
            or type(delivery.get("attempted")) is not bool
            or record.get("data_origin") not in {"external_backend", "actual_test"}):
        raise ValueError("MODEL_AUTHORIZATION_RECORD_FIELDS_INVALID")
    return {"request_id": case["request_id"], "principal_sha256": case["principal_sha256"],
            "requested_model_sha256": case["requested_model_sha256"], "gateway_decision": decision["result"],
            "decision_source": decision["source"], "http_status": decision["http_status"],
            "delivery_attempted": delivery["attempted"], "recorded_at": record["recorded_at"],
            "origin_detail": record["data_origin"]}


def capture(docker, env, policy, context, target, source):
    pairs = validate_inputs(env, policy, context, target, source)
    records, provenance = read_owned_jsonl(docker, target, **source)
    rows = []
    for case in context["cases"]:
        finals = [r for r in records if r.get("request_id") == case["request_id"] and r.get("phase") == "final"]
        row = {"case_id": case["case_id"], "request_id": case["request_id"],
               "requested_model_authorized_by_declared_policy":
                   (case["principal_sha256"], case["requested_model_sha256"]) in pairs,
               "metadata_linked": False, "gateway": None, "diagnostic_codes": []}
        if len(finals) != 1:
            row["diagnostic_codes"].append("GATEWAY_FINAL_MISSING" if not finals else "GATEWAY_FINAL_DUPLICATE")
        else:
            try:
                projection = _project(finals[0], case, context)
                row["gateway"] = projection
                row["metadata_linked"] = True
                if projection["gateway_decision"] == "ALLOWED" and not row["requested_model_authorized_by_declared_policy"]:
                    row["diagnostic_codes"].append("DECLARED_MODEL_GRANT_CONFLICT")
                if projection["gateway_decision"] == "DENIED" and projection["delivery_attempted"]:
                    row["diagnostic_codes"].append("DECLARED_DENIAL_WITH_RELAY_ATTEMPT")
            except (ValueError, TypeError, KeyError, UnicodeError):
                row["diagnostic_codes"].append("GATEWAY_RECORD_UNVERIFIED")
        rows.append(row)
    example = (env.data_origin.value == "EXAMPLE" or policy.data_origin.value == "EXAMPLE"
               or any(r.get("data_origin") == "actual_test" or r.get("backend_kind") == "mock" for r in records))
    return {"schema": "guard-model-authorization-capture/1", "check_id": "REQ-05",
            "environment_ref": env.ref().model_dump(mode="json"), "policy_ref": policy.ref().model_dump(mode="json"),
            "pack_ref": policy.pack.model_dump(mode="json"), "runtime_policy": context["runtime_policy"],
            "run_id": context["run_id"], "observation_session_id": context["observation_session_id"],
            "target_id": context["target_id"], "instance_id": context["instance_id"], "window": context["window"],
            "context_sha256": sha256_hex(canonical_json(context)), "capture": provenance, "cases": rows,
            "data_origin": "EXAMPLE" if example else "DECLARED", "official": False,
            "capture_executed": True, "engine_started": False, "product_connected": False,
            "producer_authenticated": False, "final_model_verified": False, "actual_consumption_verified": False,
            "security": "UNKNOWN", "functional": "UNKNOWN", "performance": "UNKNOWN",
            "pending": ["FINAL_ENGINE_MODEL_PRODUCER_UNAVAILABLE", "PRODUCER_TRUST_UNVERIFIED"],
            "not_exercised": ["alias_resolution", "lora", "retirement", "alternative_endpoints"]}
