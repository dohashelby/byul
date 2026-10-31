"""REQ05의 소유 credential probe와 기존 native 모델 관측을 같은 제품 증거로 묶는다.

현재 native image/최종 모델 lineage 미확인은 그대로 UNKNOWN이다.
runtime 주장이나 mount/API 이름을 최종 모델 승인 증명으로 승격하지 않는다.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re
import uuid

from guard.adapters.vllm_model_authorization import project_native
from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, MODEL_AUTHORIZATION_PROFILE
from .http_common import Ctx, HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session

PROFILE = "kubernetes-model-authorization-observation-v1"
POLICY_TYPES = {
    "model_environment_sha256": "str", "model_target_sha256": "str",
    "allowed_principal_sha256": "str", "denied_principal_sha256": "str",
    "selected_model_grants": "list[str]", "retired_model_revisions": "list[str]",
    "model_validity_start": "str", "model_validity_end": "str",
}
OBSERVATIONS = {
    "model_use_trials": "list", "configured_model_policy_conflicts": "list",
    "configured_retired_model_uses": "list", "model_authorization_scope_complete": "bool",
    "model_retirement_scope_complete": "bool", "final_model_identity_verified": "bool",
}


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _time(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError
    return stamp


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values) != set(POLICY_TYPES):
            raise ValueError
        for key in ("model_environment_sha256", "model_target_sha256",
                    "allowed_principal_sha256", "denied_principal_sha256"):
            if not _sha(values[key]):
                raise ValueError
        if values["allowed_principal_sha256"] == values["denied_principal_sha256"]:
            raise ValueError
        start, end = (_time(values[k]) for k in ("model_validity_start", "model_validity_end"))
        if not 0 < (end - start).total_seconds() <= 600:
            raise ValueError
        grants = values["selected_model_grants"]
        retired = values["retired_model_revisions"]
        if (type(grants) is not list or not 1 <= len(grants) <= 64 or len(set(grants)) != len(grants)
                or type(retired) is not list or len(retired) > 64 or len(set(retired)) != len(retired)):
            raise ValueError
        parsed_grants, parsed_retired = set(), []
        for item in grants:
            if type(item) is not str or re.fullmatch(r"[0-9a-f]{64}:[0-9a-f]{64}:[0-9a-f]{64}", item) is None:
                raise ValueError
            parsed_grants.add(tuple(item.split(":")))
        seen = set()
        for item in retired:
            if type(item) is not str or len(item) > 170:
                raise ValueError
            model, revision, when = item.split(":", 2)
            if not _sha(model) or not _sha(revision) or (model, revision) in seen:
                raise ValueError
            seen.add((model, revision)); parsed_retired.append((model, revision, _time(when)))
        return start, end, parsed_grants, parsed_retired
    except (ValueError, TypeError, KeyError):
        raise ValueError("MODEL_AUTHORIZATION_POLICY_INVALID") from None


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class KubernetesModelAuthorizationExecutor(HttpCheckExecutor):
    name = "model_authorization"
    version = "0.1.0"
    requires_roles = True
    requires_route_scope = False
    functional_via = "engine"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ)
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def validate_policy(self, check, values):
        if check.id != "REQ-05" or getattr(check, "collection_profile", None) != PROFILE:
            raise ValueError("MODEL_AUTHORIZATION_PROFILE_UNSUPPORTED")
        normalize_policy(values)

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values)
        # Asset preparation imports application helpers; defer until registry loading is complete.
        from guard.collectors.lifecycle import load_prepared
        try:
            if set(self.cfg) - {"target", "prepared", "roles", "kubectl_binary", "max_duration_seconds"} or adapter is None:
                raise ValueError
            if set(self.roles()) != {"allowed", "denied"}:
                raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id, env.model.revision)):
                raise ValueError
            keys = self.keys()
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_hash = sha256_hex(canonical_json(target))
            if (target_hash != policy_values["model_target_sha256"]
                    or policy_values["model_environment_sha256"] != env.ref().sha256
                    or self._expected_target_sha256 is not None and self._expected_target_sha256 != target_hash):
                raise ValueError
            prepared = load_prepared(self.cfg["prepared"])
            validity_start, validity_end, grants, _ = normalize_policy(policy_values)
            if not validity_start <= utcnow() <= validity_end:
                raise ValueError
            pair = (_digest(env.model.id), _digest(env.model.revision))
            if ((policy_values["allowed_principal_sha256"], *pair) not in grants
                    or (policy_values["denied_principal_sha256"], *pair) in grants):
                raise ValueError
        except (ValueError, TypeError, KeyError, OSError, RuntimeError):
            raise ValueError("MODEL_AUTHORIZATION_CONFIGURATION_INVALID") from None
        transport = KubernetesHTTP(target, env, duration=self.cfg.get("max_duration_seconds", 120),
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner,
            profile=MODEL_AUTHORIZATION_PROFILE)
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
            timeout=10, allow_mutating=False, keys=keys, secrets=[v for v in keys.values() if v],
            cprobe=transport, workdir=workdir, started=utcnow())
        ctx._model_prepared = prepared
        return self._finish(ctx)

    def collect(self, ctx):
        values = ctx.policy_values
        start, end, grants, retired_models = normalize_policy(values)
        summary = {"profile": PROFILE, "attempts": [], "native": None, "diagnostic_codes": [],
            "fixture": {"profile": PROFILE}, "scope": "one selected base-model alias / declared probe roles",
            "final_model_identity_verified": False, "principal_identity_verified": False,
            "retirement_scope_complete": False, "alternative_selectors_scope_complete": False}
        reader = None; packet = None; replies = {}
        native_target = {"schema": "guard-kubernetes-lifecycle-target/1", "lab": ctx.target["lab"],
            "run_id": ctx.cprobe.binding["run_id"], "environment_ref": ctx.env.ref().model_dump(mode="json"),
            "kubernetes": {"context": ctx.cprobe.binding["context"], "engine": ctx.cprobe.binding["engine"]}}
        token = uuid.uuid4().hex
        try:
            reader = KubernetesLifecycleReader(native_target, ctx.env, ctx._model_prepared,
                binary=self.cfg.get("kubectl_binary", "kubectl"), duration=self.cfg.get("max_duration_seconds", 120),
                runner=self._kubectl_runner)
            reader.snapshot()
            for slot, role in (("before", "allowed"), ("challenge", "denied"), ("after", "allowed")):
                if slot == "challenge" and not replies.get("before", {}).get("normal_shape_verified", False):
                    continue
                rid = "byul-req05-model-" + token + "-" + slot
                body = {"model": ctx.api_model, "messages": [{"role": "user", "content": "hello"}],
                        "max_tokens": 1, "stream": False}
                row = {"case_id": "req05-model-" + slot, "role": role,
                       "request_id_sha256": _digest(rid), "body_sha256": sha256_hex(canonical_json(body)),
                       "attempted": False, "started_at": None, "finished_at": None,
                       "outcome": None, "binding_verified": False, "policy_window_verified": False,
                       "normal_shape_verified": False}
                summary["attempts"].append(row)
                try:
                    now = utcnow()
                    if not start <= now <= end:
                        raise ValueError
                    row.update(attempted=True, started_at=now.isoformat())
                    request = {"id": row["case_id"], "method": "POST", "mode": "json",
                        "url": ctx.engine_url + "/v1/chat/completions",
                        "headers": {"X-Request-Id": rid, "Authorization": "Bearer " + ctx.keys[role]}, "body": body}
                    response, = ctx.cprobe("kubernetes-probe", [request], ctx.timeout)
                    row.update(outcome=response.get("outcome"), binding_verified=True)
                    if (response.get("outcome") == 200 and response.get("capture_complete") is True
                            and response.get("eof") is True and response.get("utf8_valid") is True):
                        value = strict_json(response["body"].encode(), limit=128*1024)
                        row["normal_shape_verified"] = (value.get("model") == ctx.api_model
                            and type(value.get("choices")) is list and bool(value["choices"])
                            and all(type(c) is dict and type(c.get("message")) is dict
                                    and type(c["message"].get("content")) is str for c in value["choices"]))
                except Exception:
                    summary["diagnostic_codes"].append("MODEL_PROBE_UNAVAILABLE")
                finally:
                    row["finished_at"] = utcnow().isoformat() if row["attempted"] else None
                    row["policy_window_verified"] = bool(row["started_at"] and start <= _time(row["started_at"])
                        <= _time(row["finished_at"]) <= end)
                    if row["attempted"] and not row["policy_window_verified"]:
                        summary["diagnostic_codes"].append("MODEL_POLICY_WINDOW_UNVERIFIED")
                replies[slot] = row
        except Exception:
            summary["diagnostic_codes"].append("MODEL_NATIVE_PREFLIGHT_UNAVAILABLE")
        finally:
            if reader is not None:
                # Even seal/follow-up failures must not erase already available native metadata.
                sealed = reader.seal_session()["sealed"]
                if not sealed: summary["diagnostic_codes"].append("MODEL_SESSION_SEAL_UNVERIFIED")
                engine = reader.binding["engine"]
                packet = capture_session(reader, native_target, container=engine["container"], image=engine["image"],
                    prepared=ctx._model_prepared, owner_identity=reader.owner_identity,
                    read_file=reader.read_file, image_check=reader.image_check)
                summary["native"] = {"session_sha256": _digest(ctx._model_prepared["session"]),
                    "sealed": sealed, "recorded_files_covered": packet["recorded_files_covered"],
                    "live_census_stable": packet["live_census_stable"],
                    "recordings": [{"pid": r["pid"], "start_ticks": r["start_ticks"],
                        "captured": r["capture"] is not None,
                        "capture_sha256": r["capture"]["provenance"]["capture_sha256"] if r["capture"] else None,
                        "image_digest_verified": r["capture"]["image_digest_verified"] if r["capture"] else False,
                        "process_context_verified": r["capture"]["process_context_verified"] if r["capture"] else False}
                        for r in packet["recordings"]],
                    "diagnostic_codes": packet["diagnostic_codes"], "transport": reader.report()}
                if not packet["recorded_files_covered"]:
                    summary["diagnostic_codes"].append("MODEL_NATIVE_RECORDING_GAP")
        trials, conflicts, retired = project_native(packet, summary["attempts"], grants=grants, retired=retired_models,
            principals={"allowed":values["allowed_principal_sha256"], "denied":values["denied_principal_sha256"]})
        observations = {key: {"value": value, "collected": True} for key, value in {
            "model_use_trials": trials, "configured_model_policy_conflicts": conflicts,
            "configured_retired_model_uses": retired, "model_authorization_scope_complete": False,
            "model_retirement_scope_complete": False, "final_model_identity_verified": False}.items()}
        summary["http_transport"] = ctx.cprobe.report()
        ctx._model_gap = bool(summary["diagnostic_codes"])
        ctx._model_cleanup = summary["http_transport"]["cleanup_errors"]
        if reader is not None and reader.report().get("parent_cleanup_unverified"):
            ctx._model_cleanup.append("MODEL_NATIVE_PARENT_CLEANUP_UNVERIFIED")
        normal = {"attempted": bool(replies.get("before", {}).get("attempted")),
            "succeeded": bool(replies.get("before", {}).get("normal_shape_verified")) if "before" in replies else None,
            "detail": "Selected alias HTTP shape only; final model authorization/retirement remains unverified."}
        return observations, normal, [], summary

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        ceiling = DataOrigin.EXAMPLE if self._kubectl_runner is not None or ctx.env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
        draft.data_origin = min(draft.data_origin, ceiling, key=lambda x: ORIGIN_STRENGTH[x])
        if ctx._model_gap: draft.status = ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._model_cleanup)
        return draft
