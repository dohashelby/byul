"""REQ-02의 EXAMPLE 전용 경로 변환 증거 계약. 실제 실행기에는 연결하지 않는다.

현재 Gateway 로그는 주소·요청·상태만 가지며, 인가 입력과 최종 handler를
연결하는 신뢰 수집원이 없다. 이 모듈은 그 공백을 HTTP 성공이나 신고값으로
채우지 않는다. bytes 파싱과 합성 사건의 연결 검증만 제공하고 EVD/RES를 만들지
않는다. EXAMPLE의 source identity/서버 ID도 실제 출처 인증의 구현이 아니다.

첫 기술 범위는 단일 proxy에서 POST /v1/completions 한 작업으로 재작성하는
작은 무해 표본이다. 기업 허용값(policy_values)과 기술 표본(fixture/trials)을
분리한다. inputs의 정확한 팩·정의·POL/ENV/PLAN Ref와 정책·표본 해시를 함께
고정하므로 저장한 합성 캡처를 다른 팩/정책/계획으로 재해석할 수 없다.
이 해시는 두 합성 파일의 대응만 확인하며 Ref 원본·출처의 진위를 인증하지 않는다.
Guard 제품 모듈/실행기/판정기에 import하지 않으며 wheel에 포함하지 않는다.
사용: python -m tools.analyze_route_rewrite --inputs EXAMPLE-inputs.json
      --capture EXAMPLE-capture.json [--output NEW-diagnostic.json]
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


SCHEMA = "guard-route-rewrite-example/1"
MAX_CAPTURE_BYTES = 131072
MAX_EVENTS = 64
MAX_TRIALS = 8
ENDPOINT = {"method": "POST", "path": "/v1/completions", "operation": "text_completion"}
SOURCE_NAMES = ("proxy_auth", "engine_handler")
REQUIRED_REAL_COLLECTORS = (
    "proxy-owned principal and authorization method/path/operation/decision events, not forwarded client headers",
    "proxy-owned external method/path, rewritten method/path and explicit blocked/forwarded dispatch events",
    "engine-owned handler identity, operation, path and entered/rejected events for the reviewed safe endpoint",
    "server-generated ingress and private upstream identifiers bound to the probe ledger; reject client supplied IDs",
    "independently checked source ownership, instance/start identity, capture bounds and complete event framing",
    "exact stored pack/check bytes, PLAN/ENV/POL Refs and before/after normal generation controls",
)
_SHA = re.compile(r"[0-9a-f]{64}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_PATH = re.compile(r"/[A-Za-z0-9/_-]{0,255}")


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _name(value):
    return type(value) is str and _NAME.fullmatch(value) is not None


def _path(value):
    return (type(value) is str and _PATH.fullmatch(value) is not None
            and "//" not in value and value != "/")


def _origin(value):
    if type(value) is not str or len(value) > 512:
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username
                and not parts.password and not parts.path and not parts.query and not parts.fragment
                and (parts.port is None or 1 <= parts.port <= 65535))
    except ValueError:
        return False


def _list(value, validate, label, *, empty=False):
    if (type(value) is not list or len(value) > 32 or (not value and not empty)
            or any(not validate(item) for item in value) or len(value) != len(set(value))):
        raise ValueError(f"invalid explicit {label}")


def validate_example_inputs(inputs: dict) -> str:
    """명시적인 EXAMPLE 입력만 검증하고 정규 문서 해시를 반환한다. I/O 없음."""
    if type(inputs) is not dict or inputs.get("schema") != SCHEMA or inputs.get("data_origin") != "EXAMPLE":
        raise ValueError("route rewrite contract accepts EXAMPLE inputs only")
    if not _name(inputs.get("run_id")):
        raise ValueError("explicit run_id required")
    refs = inputs.get("refs")
    if type(refs) is not dict or set(refs) != {"pack", "plan", "environment", "policy"}:
        raise ValueError("exact pack/plan/environment/policy Refs required")
    for key, kind in (("pack", "PACK"), ("plan", "PLAN"), ("environment", "ENV"), ("policy", "POL")):
        ref = refs[key]
        if (type(ref) is not dict or set(ref) != {"kind", "id", "revision", "sha256"}
                or ref.get("kind") != kind or not _name(ref.get("id"))
                or type(ref.get("revision")) is not int or ref["revision"] < 1 or not _sha(ref.get("sha256"))):
            raise ValueError(f"invalid exact {key} Ref")
    check = inputs.get("check")
    if (type(check) is not dict or set(check) != {"id", "revision", "sha256"}
            or check.get("id") != "REQ-02" or type(check.get("revision")) is not int
            or check["revision"] < 1 or not _sha(check.get("sha256"))):
        raise ValueError("exact REQ-02 definition revision and bytes hash required")
    policy = inputs.get("policy_values")
    if type(policy) is not dict or set(policy) != {"operation", "allowed_principals", "allowed_external_paths"}:
        raise ValueError("explicit single-operation policy required; no policy defaults")
    if policy["operation"] != ENDPOINT["operation"]:
        raise ValueError("policy must explicitly name the supported single operation")
    _list(policy["allowed_principals"], _name, "allowed_principals", empty=True)
    _list(policy["allowed_external_paths"], _path, "allowed_external_paths", empty=True)
    fixture = inputs.get("fixture")
    if (type(fixture) is not dict or set(fixture) != {"external_origin", "endpoint", "handler_id", "source_identities"}
            or not _origin(fixture.get("external_origin")) or fixture.get("endpoint") != ENDPOINT
            or not _name(fixture.get("handler_id"))):
        raise ValueError("only the single safe completions fixture is supported")
    identities = fixture["source_identities"]
    if (type(identities) is not dict or set(identities) != set(SOURCE_NAMES)
            or any(not _name(value) for value in identities.values())
            or len(set(identities.values())) != len(SOURCE_NAMES)):
        raise ValueError("distinct explicit proxy and engine source identities required")
    trials = inputs.get("trials")
    if type(trials) is not list or not 3 <= len(trials) <= MAX_TRIALS:
        raise ValueError("bounded base/prefix/challenge trial inventory required")
    for trial in trials:
        if (type(trial) is not dict or not _name(trial.get("id"))
                or trial.get("case") not in ("normal_base", "normal_prefix", "challenge")
                or not _name(trial.get("principal")) or not _path(trial.get("external_path"))
                or trial.get("external_url") != fixture["external_origin"] + trial["external_path"]
                or not _sha(trial.get("request_sha256"))
                or not _name(trial.get("proxy_request_id")) or not _name(trial.get("upstream_request_id"))):
            raise ValueError("invalid explicit probe ledger")
    cases = Counter(trial["case"] for trial in trials)
    if cases["normal_base"] != 1 or cases["normal_prefix"] != 1 or not cases["challenge"]:
        raise ValueError("one base and prefix normal control plus challenge required")
    for key in ("id", "proxy_request_id", "upstream_request_id", "request_sha256"):
        if len({trial[key] for trial in trials}) != len(trials):
            raise ValueError(f"ambiguous probe ledger {key}")
    for trial in trials:
        if trial["case"] == "normal_base" and trial["external_path"] != ENDPOINT["path"]:
            raise ValueError("base control must use the original endpoint")
        if trial["case"] == "normal_prefix" and (trial["external_path"] == ENDPOINT["path"]
                or not trial["external_path"].endswith(ENDPOINT["path"])):
            raise ValueError("prefix control must use a distinct prefixed endpoint")
    return hashlib.sha256(_canonical(inputs)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode(payload):
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_CAPTURE_BYTES:
        raise ValueError("bounded capture bytes required")
    try:
        result = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid complete UTF-8 JSON capture") from exc
    return result


def _parse(payload):
    result = _decode(payload)
    if type(result) is not dict or result.get("schema") != SCHEMA or result.get("data_origin") != "EXAMPLE":
        raise ValueError("route rewrite capture accepts EXAMPLE only; real collector unavailable")
    events = result.get("events")
    if type(events) is not list or len(events) > MAX_EVENTS or any(type(event) is not dict for event in events):
        raise ValueError("bounded event inventory required")
    if any(event.get("data_origin") != "EXAMPLE" for event in events):
        raise ValueError("mixed/undeclared event origins cannot be promoted to EXAMPLE")
    return result


def analyze_example_capture(payload: bytes, *, inputs: dict) -> dict:
    """합성 연결·우회 기대값만 계산한다. 결과는 제품 보안 판정이 아니다.

    입력/캡처 자체가 유효하지 않으면 거부한다. 사건별 관측 누락은 gaps로
    남기며, 별개의 완전한 합성 우회 사건은 뒤의 누락 때문에 지우지 않는다.
    클라이언트 ID·응답 상태·설정 문자열은 어떤 연결 근거로도 읽지 않는다.
    """
    inputs_sha = validate_example_inputs(inputs)
    capture = _parse(payload)
    gaps, linked, bypass, normal, details = [], [], [], [], []
    result = {"schema": "guard-route-rewrite-example-analysis/1", "data_origin": "EXAMPLE",
              "product_connected": False, "trusted_collection_implemented": False,
              "guard_check_status": {"check_id": "REQ-02", "connected": False, "security": "UNKNOWN"},
              "inputs_sha256": inputs_sha, "capture_bytes_sha256": hashlib.sha256(payload).hexdigest(),
              "refs": inputs["refs"], "check": inputs["check"],
              "required_real_collectors": list(REQUIRED_REAL_COLLECTORS)}

    def finish():
        complete = not gaps and len(linked) == len(inputs["trials"])
        controls = len(normal) == 2
        return {**result, "example_linked_trials": sorted(linked), "example_bypass_trials": sorted(bypass),
                "example_normal_controls_verified": controls, "example_coverage_complete": complete,
                "example_chain_state": "BYPASS_OBSERVED" if bypass else "COMPLETE_WITHOUT_BYPASS" if complete and controls else "INCONCLUSIVE",
                "example_trial_details": details, "gaps": sorted(set(gaps))}

    if capture.get("inputs_sha256") != inputs_sha or capture.get("run_id") != inputs["run_id"]:
        gaps.append("CAPTURE_INPUT_BINDING_MISMATCH")
        return finish()
    sources = capture.get("sources")
    if type(sources) is not dict:
        gaps.append("SOURCE_INVENTORY_MISSING")
        return finish()
    stable = {}
    for source in SOURCE_NAMES:
        row = sources.get(source)
        if type(row) is dict and row.get("data_origin") != "EXAMPLE":
            raise ValueError("mixed/undeclared source origins cannot be promoted to EXAMPLE")
        expected = inputs["fixture"]["source_identities"][source]
        stable[source] = (type(row) is dict and row.get("identity_before") == expected
                          and row.get("identity_after") == expected)
        if not stable[source]:
            gaps.append(f"{source}:SOURCE_IDENTITY_UNVERIFIED")
        if type(row) is not dict or row.get("complete") is not True:
            gaps.append(f"{source}:SOURCE_INCOMPLETE")
    valid = []
    for event in capture["events"]:
        source = event.get("source")
        if source not in SOURCE_NAMES or not stable[source]:
            gaps.append("EVENT_SOURCE_UNVERIFIED")
            continue
        if (event.get("run_id") != inputs["run_id"]
                or event.get("source_identity") != inputs["fixture"]["source_identities"][source]
                or not _name(event.get("event_id"))):
            gaps.append("EVENT_RUN_OR_IDENTITY_UNVERIFIED")
            continue
        valid.append(event)
    duplicate_ids = {key for key, count in Counter(event["event_id"] for event in valid).items() if count > 1}
    if duplicate_ids:
        gaps.append("DUPLICATE_EVENT_ID")
    valid = [event for event in valid if event["event_id"] not in duplicate_ids]
    planned_ids = {"proxy_auth": {trial["proxy_request_id"] for trial in inputs["trials"]},
                   "engine_handler": {trial["upstream_request_id"] for trial in inputs["trials"]}}
    for event in valid:
        key = "proxy_request_id" if event["source"] == "proxy_auth" else "upstream_request_id"
        identifier = event.get(key)
        if not _name(identifier) or identifier not in planned_ids[event["source"]]:
            gaps.append("UNPLANNED_SOURCE_EVENT")
    policy, fixture = inputs["policy_values"], inputs["fixture"]
    for trial in inputs["trials"]:
        label = trial["id"]
        proxy = [event for event in valid if event["source"] == "proxy_auth"
                 and event.get("proxy_request_id") == trial["proxy_request_id"]]
        engine = [event for event in valid if event["source"] == "engine_handler"
                  and event.get("upstream_request_id") == trial["upstream_request_id"]]
        if len(proxy) != 1:
            gaps.append(f"{label}:EXACT_PROXY_EVENT_MISSING_OR_AMBIGUOUS")
            continue
        proxy = proxy[0]
        if (proxy.get("upstream_request_id") != trial["upstream_request_id"]
                or proxy.get("request_sha256") != trial["request_sha256"]
                or proxy.get("principal") != trial["principal"]
                or proxy.get("external_method") != ENDPOINT["method"]
                or proxy.get("external_url") != trial["external_url"]
                or proxy.get("external_path") != trial["external_path"]
                or not _path(proxy.get("auth_path")) or proxy.get("auth_method") != ENDPOINT["method"]
                or not _name(proxy.get("auth_operation")) or proxy.get("auth_decision") not in ("allow", "deny")
                or proxy.get("rewritten_method") != ENDPOINT["method"]
                or proxy.get("rewritten_path") != ENDPOINT["path"]):
            gaps.append(f"{label}:PROXY_AUTH_REWRITE_CHAIN_UNVERIFIED")
            continue
        entered, generation_ok = False, False
        if proxy.get("dispatch") == "blocked" and proxy["auth_decision"] == "deny" and not engine:
            pass  # Explicit source-owned block, not absence of an engine log alone.
        elif proxy.get("dispatch") == "forwarded" and len(engine) == 1:
            event = engine[0]
            if (event.get("proxy_request_id") != trial["proxy_request_id"]
                    or event.get("request_sha256") != trial["request_sha256"]
                    or event.get("method") != ENDPOINT["method"] or event.get("path") != ENDPOINT["path"]
                    or event.get("operation") != ENDPOINT["operation"]
                    or event.get("handler_id") != fixture["handler_id"]
                    or event.get("execution") not in ("entered", "rejected")):
                gaps.append(f"{label}:FINAL_HANDLER_UNVERIFIED_OR_UNSUPPORTED")
                continue
            entered = event["execution"] == "entered"
            generation_ok = (event.get("normal_generation") is True
                             and event.get("normal_generation_basis") == "structured_probe_response")
        else:
            gaps.append(f"{label}:DISPATCH_HANDLER_CHAIN_MISSING_OR_AMBIGUOUS")
            continue
        allowed = (trial["principal"] in policy["allowed_principals"]
                   and trial["external_path"] in policy["allowed_external_paths"])
        linked.append(label)
        details.append({"trial": label, "auth_path": proxy["auth_path"], "auth_operation": proxy["auth_operation"],
                        "auth_decision": proxy["auth_decision"], "final_operation": ENDPOINT["operation"],
                        "handler_entered": entered, "policy_allows": allowed})
        if not allowed and entered:
            bypass.append(label)
        if trial["case"].startswith("normal_"):
            if allowed and entered and proxy["auth_decision"] == "allow" and generation_ok:
                normal.append(label)
            else:
                gaps.append(f"{label}:NORMAL_CONTROL_UNVERIFIED")
    return finish()


def main(argv=None):
    """두 명시적 EXAMPLE 파일만 읽는다. 네트워크/엔진/제품 판정 호출 없음."""
    parser = argparse.ArgumentParser(description="REQ-02 EXAMPLE 증거 연결 진단; Guard 판정은 UNKNOWN/미연결")
    parser.add_argument("--inputs", type=Path, required=True, help="EXAMPLE 정책·기술표본·정확한 Ref 입력 JSON")
    parser.add_argument("--capture", type=Path, required=True, help="EXAMPLE source/event 캡처 JSON")
    parser.add_argument("--output", type=Path, help="새 진단 JSON 경로; 기존 파일은 덮어쓰지 않음")
    args = parser.parse_args(argv)

    def read(path):
        with path.open("rb") as stream:
            return stream.read(MAX_CAPTURE_BYTES + 1)

    try:
        result = analyze_example_capture(read(args.capture), inputs=_decode(read(args.inputs)))
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(rendered)
        else:
            print(rendered, end="")
    except (OSError, ValueError):
        # OS exceptions include private host paths. Do not echo them or input.
        print("REQ-02 EXAMPLE 진단 거부: invalid_example_input_or_output", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
