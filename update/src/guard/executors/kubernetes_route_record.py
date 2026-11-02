"""REQ02의 소유 기록 파일을 기존 제품 경로로 수집한다. 생산자 주장은 미인증이다."""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re

from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, sha256_hex, utcnow
from guard.models.objects import Observation
from guard.runtime.bounded import capture_pipes
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from .composite_collection import merge_drafts, select_policy, sub_check
from .kubernetes_container_config import KubernetesContainerConfigExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader, KubernetesCaptureError
from .native_observations import validate_capture
from .owned_capture import _FILE_READER, MAX_BYTES, strict_json

PROFILE = "kubernetes-owned-route-record-v1"
POLICY_TYPES = {
    "route_environment_sha256": "str", "route_target_sha256": "str",
    "route_session": "str", "route_file_sha256": "str",
    "route_validity_start": "str", "route_validity_end": "str",
}
OBSERVATIONS = {
    "route_events": "list", "route_record_collected": "bool",
    "route_producer_authenticated": "bool", "route_authorization_scope_complete": "bool",
}


COMPOSITE_PROFILE = "kubernetes-route-record-native-composite-v1"


def composite_contract():
    """REQ-02 복합 프로파일의 정책·관측. native_route 이름과 파생 팩 경로는 그대로 둔다."""
    from . import native_route_contract as native
    observations = {**OBSERVATIONS, **native.OBSERVATIONS, "route_bypass_claims": "list",
                    "composite_subcollections": "dict"}
    return {**POLICY_TYPES, **native.POLICY_TYPES}, observations


def bypass_claims(events):
    """인증 판단이 거부인데 handler 진입을 주장한 행. 생산자 미인증 주장이며 위반 확정이 아니다."""
    return [row for row in events or [] if row["auth_decision"] == "deny" and row["handler_outcome"] == "entered"]


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values) != set(POLICY_TYPES):
            raise ValueError
        for key in ("route_environment_sha256", "route_target_sha256", "route_file_sha256"):
            if type(values[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", values[key]):
                raise ValueError
        if type(values["route_session"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", values["route_session"]):
            raise ValueError
        times = []
        for key in ("route_validity_start", "route_validity_end"):
            if type(values[key]) is not str:
                raise ValueError
            value = datetime.fromisoformat(values[key])
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError
            times.append(value)
        if times[0] >= times[1]:
            raise ValueError
        return times
    except (KeyError, TypeError, ValueError):
        raise ValueError("KUBERNETES_ROUTE_POLICY_INVALID") from None


class KubernetesRouteReader(KubernetesLifecycleReader):
    """기존 native profile의 lab/source/image gate를 보존하는 제한 기록 reader."""
    def __init__(self, target, env, session, *, binary="kubectl", duration=30, runner=None):
        transport = KubernetesHTTP(target, env, duration=duration, binary=binary,
                                   runner=runner, profile=REQUEST_BINDING_PROFILE)
        self.cleanup_errors = []
        actual_runner = runner or capture_pipes
        def bounded_runner(*args, **kwargs):
            result = actual_runner(*args, **kwargs)
            pipe = result.get("capture", {})
            if pipe.get("pipes_closed") is False or "child_reap" in pipe.get("read_errors", []):
                if "KUBERNETES_ROUTE_PARENT_CLEANUP_UNVERIFIED" not in self.cleanup_errors:
                    self.cleanup_errors.append("KUBERNETES_ROUTE_PARENT_CLEANUP_UNVERIFIED")
                self.gap = True
            return result
        self._initialize(transport.binding, target, {"session": session},
                         binary=binary, duration=duration, runner=bounded_runner)
        self.injected = runner is not None

    def _program(self, container, argv, max_bytes):
        if (container != self.binding["engine"]["container"] or type(argv) is not list
                or argv != ["python3", "-c", _FILE_READER,
                            "/guard-route/data/" + self.session + "/route-observations.json", str(MAX_BYTES)]
                or max_bytes != 2 * MAX_BYTES):
            raise KubernetesCaptureError()
        return "file"

    def report(self):
        result = super().report()
        result.update(profile=PROFILE, cleanup_errors=list(self.cleanup_errors),
                      native_producer_verified=False, authorization_verified=False)
        # This profile can run only the fixed file reader, never native census/context.
        result["programs"] = {"file": result["programs"]["file"]}
        return result


def _events(packet):
    """Only hashes/enums survive; never publish producer supplied paths/identifiers."""
    hashed = {"event_id", "producer", "ingress_id", "upstream_id", "external_path", "auth_path",
              "auth_operation", "rewritten_path", "handler_id", "handler_operation"}
    return [{(key + "_sha256" if key in hashed else key):
             (hashlib.sha256(value.encode()).hexdigest() if key in hashed else value)
             for key, value in row.items()} for row in packet["events"]]


class KubernetesRouteRecordExecutor(KubernetesContainerConfigExecutor):
    name = "route_record"
    version = "0.1.0"

    def validate_policy(self, check, values):
        if check.id == "REQ-02" and getattr(check, "collection_profile", None) == COMPOSITE_PROFILE:
            from . import native_route_contract as native
            if type(values) is not dict or set(values) != set(composite_contract()[0]):
                raise ValueError("KUBERNETES_ROUTE_POLICY_INVALID")
            normalize_policy(select_policy(values, POLICY_TYPES))
            native.normalize_policy(select_policy(values, native.POLICY_TYPES))
            return
        if check.id != "REQ-02" or getattr(check, "collection_profile", None) != PROFILE:
            raise ValueError("KUBERNETES_ROUTE_PROFILE_UNSUPPORTED")
        normalize_policy(values)

    def run(self, check, env, policy_values, adapter, workdir):
        if getattr(check, "collection_profile", None) != COMPOSITE_PROFILE:
            return super().run(check, env, policy_values, adapter, workdir)
        return self._run_composite(check, env, policy_values, adapter, workdir)

    def _run_composite(self, check, env, values, adapter, workdir):
        """기록 파일 수집과 native 세션 수집을 각각 끝낸 뒤 모은다."""
        from . import native_route_contract as native
        from .kubernetes_native_route import KubernetesNativeRouteExecutor
        self.validate_policy(check, values)
        self.version = "0.2.0"
        cfg = dict(self.cfg)
        native_cfg = cfg.pop("native_route", None)
        if type(native_cfg) is not dict:
            raise ValueError("KUBERNETES_ROUTE_CONFIGURATION_INVALID")
        record = type(self)(cfg, runner=self._kubectl_runner, environ=self._environ,
                            expected_target_sha256=self._expected_target_sha256)
        session = KubernetesNativeRouteExecutor(native_cfg, runner=self._kubectl_runner, environ=self._environ,
                                                expected_target_sha256=self._expected_target_sha256)
        drafts, failures = {}, {}
        for name, executor, profile, keys in (("route_record", record, PROFILE, POLICY_TYPES),
                                              ("native_route", session, native.PROFILE, native.POLICY_TYPES)):
            part = Path(workdir) / name
            part.mkdir(parents=True, exist_ok=True)
            try:
                drafts[name] = executor.run(sub_check(check, profile), env, select_policy(values, keys), adapter, part)
            except Exception:
                failures[name] = "SUBCOLLECTION_UNAVAILABLE"
        draft = merge_drafts(check, drafts, failures)
        events = draft.observations.get("route_events")
        collected = events is not None and events.collected
        draft.observations["route_bypass_claims"] = Observation(
            value=bypass_claims(events.value) if collected else None, collected=collected)
        return draft

    def preview_reuse_inputs(self, check, env, values, adapter):
        return None

    def collect(self, ctx):
        values = ctx.policy_values
        start, end = normalize_policy(values)
        summary = {"profile": PROFILE, "record": None, "transport": None, "diagnostic_codes": [],
                   "producer_authenticated": False, "probe_ledger_verified": False,
                   "authorization_scope_complete": False, "native_worker_verified": False,
                   "fixture": {"profile": PROFILE}}
        observations = {key: {"value": False if kind == "bool" else None, "collected": kind == "bool"}
                        for key, kind in OBSERVATIONS.items()}
        reader = None
        try:
            if (values["route_environment_sha256"] != ctx.env.ref().sha256
                    or values["route_target_sha256"] != sha256_hex(canonical_json(ctx.target))
                    or not start <= utcnow() <= end):
                raise ValueError
            reader = KubernetesRouteReader(ctx.target, ctx.env, values["route_session"],
                binary=self.cfg.get("kubectl_binary", "kubectl"),
                duration=self.cfg.get("max_duration_seconds", 30), runner=self._kubectl_runner)
            role = reader.binding["engine"]
            raw, provenance = reader.read_file(reader, ctx.target, container=role["container"], image=role["image"],
                path="/guard-route/data/" + values["route_session"] + "/route-observations.json")
            if (provenance["capture_sha256"] != values["route_file_sha256"]
                    or provenance.get("owner_binding_verified") is not True or not start <= utcnow() <= end):
                raise ValueError
            packet = strict_json(raw)
            record = validate_capture(packet, kind="route", env_ref=ctx.env.ref().model_dump(mode="json"),
                                      run_id=reader.binding["run_id"], image=role["image"])
            summary["record"] = {**record, "file_bytes_sha256": provenance["capture_sha256"],
                                 "file_bytes": provenance["bytes"], "file_identity": provenance["file_identity"],
                                 "owner_binding_verified": True}
            observations["route_events"].update(value=_events(packet), collected=True)
            observations["route_record_collected"]["value"] = True
        except (ValueError, OSError, KeyError, TypeError):
            summary["diagnostic_codes"].append("KUBERNETES_ROUTE_RECORD_UNAVAILABLE")
        if reader is not None:
            summary["transport"] = reader.report()
        ctx._route_gap = bool(summary["diagnostic_codes"])
        ctx._route_cleanup = list(reader.cleanup_errors) if reader is not None else []
        ctx._route_origin = (DataOrigin.EXAMPLE if self._kubectl_runner is not None
            or ctx.target["lab"] or ctx.env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED)
        return observations, {"attempted": False, "succeeded": None,
            "detail": "Recorded claims only; independent normal/denied route probes unavailable."}, [], summary

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        draft.data_origin = min(draft.data_origin, ctx._route_origin,
                                key=lambda origin: ORIGIN_STRENGTH[origin])
        if ctx._route_gap:
            draft.status = ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._route_cleanup)
        return draft
