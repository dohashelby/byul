"""OBS-01 Gateway 경로(BYUL 구성). 필수 접근 기록의 생산자는 runtime Gateway다.

원 프로필(`kubernetes-required-event-record-observation-v1`)은 엔진에 직접 요청하고 엔진 쪽 기록을 읽는다.
BYUL에서는 엔진이 격리되고(REQ-01) 접근 판단과 감사 기록은 Gateway가 남긴다. 이 프로필은 그 구성에 맞춘 것이다.

- 시도: 같은 run의 probe Pod에서 Gateway로 허용 주체(전·후)와 추론 역할 없는 인증 주체(거부) 세 번.
  상관 키는 Gateway가 응답 `X-Request-ID`로 돌려준 요청 ID다(클라이언트가 정한 ID가 아니다).
- 기록: Gateway Pod의 고정 기록 파일(`gateway_records.jsonl`, `runtime-mvp-record/1.0`)을 소유 Pod 읽기로 읽는다.
  final 행만 원 기록 형식(`guard-required-event-records/1`)으로 옮긴다. 값은 만들지 않는다.
  Gateway가 남기지 않는 값(승인·출처)은 None이다.
- 대조·판정: 원 `observation_contract.correlate`와 같은 판정 규칙을 쓴다. 대상 지문만 Gateway의 target_id 기준이다.
- Gateway 기록은 끝 표식이 없는 추가 전용 파일이다. 그래서 `source_ended`는 False이고 이 프로필 결과는 ABORTED로 남는다.
  생산자 봉인(runtime 합의)이 생기기 전에는 PASS가 될 수 없다.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import time

from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import capture_pipes, strict_json
from guard.runtime.kubernetes_http import GATEWAY_EVENT_PROFILE, KubernetesHTTP
from guard.runtime.kubernetes_routes import KubernetesRoutesHTTP
from . import observation_contract as base
from .http_common import Ctx, HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesCaptureError, KubernetesLifecycleReader
from .owned_capture import _FILE_READER, MAX_BYTES

PROFILE = "kubernetes-gateway-required-event-record-v1"
POLICY_TYPES = {**base.POLICY_TYPES, "observation_gateway_target_id": "str"}
OBSERVATIONS = dict(base.OBSERVATIONS)
RECORD_PATH = "/srv/serving/runtime/data/gateway_records.jsonl"
RECORD_SCHEMA = "runtime-mvp-record/1.0"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values) != set(POLICY_TYPES):
            raise ValueError
        gateway_target = values["observation_gateway_target_id"]
        if type(gateway_target) is not str or _ID.fullmatch(gateway_target) is None:
            raise ValueError
        return base.normalize_policy({k: v for k, v in values.items() if k != "observation_gateway_target_id"})
    except (KeyError, TypeError, ValueError):
        raise ValueError("GATEWAY_OBSERVATION_POLICY_INVALID") from None


def principal_sha256(principal_id):
    return sha256_hex(("principal:" + principal_id).encode())


def gateway_target_sha256(target_id):
    return sha256_hex(("target:" + target_id).encode())


def record_events(lines, *, run_id):
    """Gateway final 행을 원 기록 형식의 사건으로 옮긴다. 형식이 다른 행은 버리지 않고 오류로 낸다."""
    events = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        if type(row) is not dict or row.get("schema_version") != RECORD_SCHEMA:
            raise ValueError("GATEWAY_RECORD_SCHEMA_UNSUPPORTED")
        if row.get("phase") != "final" or row.get("run_id") != run_id:
            continue
        decision = (row.get("decision") or {}).get("result")
        event = {"ALLOWED": "ACCESS_ALLOWED", "DENIED": "ACCESS_DENIED"}.get(decision)
        request_id = row.get("request_id")
        if event is None or type(request_id) is not str or _ID.fullmatch(request_id) is None:
            continue
        auth = row.get("auth") or {}
        principal = auth.get("principal_id") if auth.get("state") == "authenticated" else None
        policy = (row.get("policy") or {}).get("sha256")
        status = (row.get("decision") or {}).get("http_status")
        events.append({"seq": len(events), "event_id_sha256": sha256_hex(("event:" + request_id + ":final").encode()),
            "event": event,
            "actor_sha256": principal_sha256(principal) if type(principal) is str and principal else None,
            "target_sha256": gateway_target_sha256(row["target_id"]) if type(row.get("target_id")) is str else None,
            "run_sha256": sha256_hex(run_id.encode()), "request_sha256": sha256_hex(request_id.encode()),
            "policy_sha256": policy if type(policy) is str and re.fullmatch(r"[0-9a-f]{64}", policy) else None,
            "approval_sha256": None,
            "result_sha256": sha256_hex(canonical_json({"outcome": status})) if type(status) is int else None,
            "source_sha256": None})
        if len(events) > 64:
            raise ValueError("GATEWAY_RECORD_EVENT_BOUND")
    return events


class GatewayRecordReader(KubernetesLifecycleReader):
    def __init__(self, routes, target, session, *, binary, duration, runner):
        if target.get("lab") is not True:
            raise KubernetesCaptureError()
        binding = {"context": routes.binding["context"], "run_id": routes.binding["run_id"],
                   "engine": deepcopy(routes.binding["gateway"])}
        self._initialize(binding, target, {"session": session}, binary=binary, duration=duration, runner=runner)

    def _program(self, container, argv, max_bytes):
        if (container != self.binding["engine"]["container"]
                or argv != ["python3", "-c", _FILE_READER, RECORD_PATH, str(MAX_BYTES)] or max_bytes != 2 * MAX_BYTES):
            raise KubernetesCaptureError()
        return "file"


class KubernetesGatewayRequiredEventExecutor(HttpCheckExecutor):
    name = "required_event_record"; version = "0.1.0+gateway.1"
    requires_roles = True; requires_route_scope = False; functional_via = "gateway"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ); self.runner = runner; self.expected_target = expected_target_sha256

    def validate_policy(self, check, values):
        if check.id != "OBS-01" or getattr(check, "collection_profile", None) != PROFILE:
            raise ValueError("GATEWAY_OBSERVATION_PROFILE_UNSUPPORTED")
        normalize_policy(values)

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values)
        try:
            if (set(self.cfg) - {"target", "roles", "kubectl_binary", "max_duration_seconds"} or adapter is None
                    or set(self.roles()) != {"allowed", "denied"}):
                raise ValueError
            keys = self.keys()
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            digest = sha256_hex(canonical_json(target)); start, end = normalize_policy(policy_values)
            if (target.get("lab") is not True or digest != policy_values["observation_target_sha256"]
                    or env.ref().sha256 != policy_values["observation_environment_sha256"]
                    or self.expected_target is not None and self.expected_target != digest or not start <= utcnow() <= end):
                raise ValueError
        except (KeyError, ValueError, TypeError, OSError, RuntimeError):
            raise ValueError("GATEWAY_OBSERVATION_CONFIGURATION_INVALID") from None
        duration = self.cfg.get("max_duration_seconds", 120); binary = self.cfg.get("kubectl_binary", "kubectl")
        routes = KubernetesRoutesHTTP(target, env, duration=duration, binary=binary, runner=self.runner)
        engine_view = {k: v for k, v in target.items() if k not in {"kubernetes_routes", "gateway", "direct_targets"}}
        engine_view["origins"] = {"trusted": {"network": "kubernetes-probe"}, "external": {"network": "kubernetes-probe"}}
        transport = KubernetesHTTP(engine_view, env, duration=duration, binary=binary, runner=self.runner, profile=GATEWAY_EVENT_PROFILE)
        transport.declare_gateway(target["gateway"]["url"])
        ctx = Ctx(check=check, env=env, adapter=adapter, target=engine_view, policy_values=policy_values, timeout=10,
                  allow_mutating=False, keys=keys, secrets=[v for v in keys.values() if v], cprobe=transport,
                  workdir=workdir, started=utcnow())
        ctx._routes, ctx._full_target = routes, target
        return self._finish(ctx)

    def collect(self, ctx):
        values = ctx.policy_values; start, end = normalize_policy(values)
        routes, transport = ctx._routes, ctx.cprobe
        run_id = routes.binding["run_id"]
        expected_source = sha256_hex(routes.binding["gateway"]["image"].encode())
        summary = {"profile": PROFILE, "trials": [], "snapshots": [], "collection": [], "diagnostic_codes": [],
                   "fixture": {"profile": PROFILE}, "producer": "byul-runtime-gateway", "producer_authenticated": False,
                   "correlation": "gateway_issued_request_id", "producer_sealed": False}
        reader = None
        def read_records(stage):
            row = {"stage": stage, "collected": False, "file_bytes_sha256": None, "file_bytes": None,
                   "owner_binding_verified": False, "collected_monotonic_ns": time.monotonic_ns()}
            try:
                if not start <= utcnow() <= end:
                    raise ValueError
                role = reader.binding["engine"]
                raw, provenance = reader.read_file(reader, ctx._full_target, container=role["container"], image=role["image"], path=RECORD_PATH)
                if provenance.get("owner_binding_verified") is not True:
                    raise ValueError
                packet = {"schema": base.SCHEMA, "session": values["observation_session"],
                          "environment_ref": ctx.env.ref().model_dump(mode="json"), "target_sha256": values["observation_target_sha256"],
                          "producer_source_sha256": expected_source, "producer_authenticated": False,
                          "source_ended": False, "dropped_events": 0,
                          "events": record_events(raw.decode("utf-8").splitlines(), run_id=run_id)}
                packet = base.validate_record(packet, environment_ref=ctx.env.ref().model_dump(mode="json"),
                    target_sha256=values["observation_target_sha256"], session=values["observation_session"],
                    expected_source_sha256=values["observation_expected_source_sha256"], forbidden_values=ctx.secrets)
                row.update(collected=True, file_bytes_sha256=provenance["capture_sha256"], file_bytes=provenance["bytes"],
                           owner_binding_verified=True, collected_monotonic_ns=time.monotonic_ns())
                summary["snapshots"].append({"stage": stage, "packet": packet, "collected_monotonic_ns": row["collected_monotonic_ns"]})
            except Exception:
                summary["diagnostic_codes"].append("GATEWAY_RECORD_UNAVAILABLE")
            summary["collection"].append(row)
        try:
            reader = GatewayRecordReader(routes, ctx._full_target, values["observation_session"],
                                         binary=self.cfg.get("kubectl_binary", "kubectl"),
                                         duration=self.cfg.get("max_duration_seconds", 120), runner=self.runner or capture_pipes)
            reader.snapshot()
            gateway = ctx._full_target["gateway"]["url"]
            for slot, role in (("before", "allowed"), ("denied", "denied"), ("after", "allowed")):
                body = {"model": transport.api_model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "stream": False}
                row = {"case_id": "obs01-" + slot, "role": role, "request_id_sha256": None, "body_sha256": sha256_hex(canonical_json(body)),
                       "attempted": False, "outcome": None, "binding_verified": False, "expected_response_observed": False,
                       "normal_shape_verified": False, "started_at": None, "finished_at": None, "finished_monotonic_ns": time.monotonic_ns()}
                summary["trials"].append(row)
                if slot == "denied" and not summary["trials"][0]["normal_shape_verified"]:
                    continue
                try:
                    if not start <= utcnow() <= end:
                        raise ValueError
                    routes._verify()
                    row.update(attempted=True, started_at=utcnow().isoformat())
                    response, = transport("kubernetes-probe", [{"id": row["case_id"], "mode": "json", "method": "POST",
                        "url": gateway + "/v1/chat/completions", "headers": {"Authorization": "Bearer " + ctx.keys[role]}, "body": body}], 10)
                    routes._verify()
                    rid = response.get("gateway_request_id")
                    row.update(outcome=response.get("outcome"), binding_verified=True,
                               request_id_sha256=sha256_hex(rid.encode()) if rid else None)
                    complete = response.get("capture_complete") is True and response.get("eof") is True and response.get("utf8_valid") is True
                    if role == "allowed" and response.get("outcome") == 200 and complete:
                        value = strict_json(response.get("body", "").encode(), limit=128 * 1024)
                        choices = value.get("choices") if type(value) is dict else None
                        row["normal_shape_verified"] = (value.get("model") == transport.api_model and type(choices) is list and len(choices) == 1
                            and type(choices[0]) is dict and type(choices[0].get("message")) is dict
                            and type(choices[0]["message"].get("content")) is str)
                    row["expected_response_observed"] = bool(rid) and bool(row["normal_shape_verified"] if role == "allowed"
                                                                         else complete and response.get("outcome") in (401, 403))
                    if not row["expected_response_observed"]:
                        summary["diagnostic_codes"].append("GATEWAY_OBSERVATION_PROBE_CONTROL_UNVERIFIED")
                except Exception:
                    summary["diagnostic_codes"].append("GATEWAY_OBSERVATION_PROBE_UNAVAILABLE")
                finally:
                    row["finished_monotonic_ns"] = time.monotonic_ns()
                    if row["attempted"]:
                        row["finished_at"] = utcnow().isoformat()
                if slot == "before":
                    read_records("initial")
            read_records("final")
        except Exception:
            summary["diagnostic_codes"].append("GATEWAY_OBSERVATION_PREFLIGHT_UNAVAILABLE")
        correlated_values = {**values, "observation_target_sha256": gateway_target_sha256(values["observation_gateway_target_id"])}
        # Gateway 요청 ID를 받지 못한 시도는 어떤 기록과도 맞지 않으므로 원 대조에서 누락으로 남는다.
        projected = base.correlate(summary["snapshots"], summary["trials"], correlated_values, run_sha256=sha256_hex(run_id.encode()))
        summary["projection"] = projected; summary["transport"] = transport.report(); summary["routes"] = routes.report()
        if reader is not None:
            summary["record_transport"] = reader.report()
        hard = {"GATEWAY_OBSERVATION_PROBE_UNAVAILABLE", "GATEWAY_OBSERVATION_PROBE_CONTROL_UNVERIFIED", "GATEWAY_OBSERVATION_PREFLIGHT_UNAVAILABLE"}
        closed = bool(summary["snapshots"] and summary["snapshots"][-1]["packet"]["source_ended"])
        ctx._obs_gap = bool(projected["missing"] or not closed or hard.intersection(summary["diagnostic_codes"])
                            or summary["transport"].get("collection_gap") or summary["routes"].get("collection_gap"))
        ctx._obs_cleanup = list(summary["transport"].get("cleanup_errors", [])) + list(summary["routes"].get("cleanup_errors", []))
        observations = {"required_event_trials": summary["trials"], "required_event_records": projected["records"],
            "required_event_matches": projected["matches"], "required_event_missing": projected["missing"],
            "required_event_conflict_candidates": projected["conflict_candidates"], "required_event_collection": summary["collection"],
            "required_event_records_collected": bool(summary["snapshots"]), "required_event_producer_authenticated": False,
            "required_event_independent_trigger_verified": False, "required_event_scope_complete": False}
        normal_rows = [r for r in summary["trials"] if r["role"] == "allowed"]
        normal = {"attempted": any(r["attempted"] for r in normal_rows), "succeeded": None,
                  "detail": "Gateway 경로 전후 응답 구조는 trials에 있다. 신뢰 주체·필수 사건 범위·보존은 독립 확인되지 않았다."}
        summary["diagnostic_codes"] = sorted(set(summary["diagnostic_codes"]))
        challenges = [{"step": step, "attempted": any(r["attempted"] for r in summary["trials"]),
                       "observations": {"required_event_scope_complete": False}} for step in ctx.check.challenge_steps]
        return {k: {"value": v, "collected": True} for k, v in observations.items()}, normal, challenges, summary

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        ceiling = DataOrigin.EXAMPLE if self.runner is not None or ctx.target["lab"] or ctx.env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
        draft.data_origin = min(draft.data_origin, ceiling, key=lambda origin: ORIGIN_STRENGTH[origin])
        if ctx._obs_gap or ctx._obs_cleanup:
            draft.status = ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._obs_cleanup)
        return draft
