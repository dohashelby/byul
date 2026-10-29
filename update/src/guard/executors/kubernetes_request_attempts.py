"""REQ12 procedure through the existing request_termination Kubernetes plugin."""
from __future__ import annotations
import uuid
from guard.adapters.vllm_request_attempts import project_attempts
from guard.models import canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import REQUEST_ATTEMPTS_PROFILE
from .kubernetes_request_termination import KubernetesRequestTerminationExecutor,_time
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session

PROFILE="kubernetes-request-attempt-observation-v1"
POLICY_TYPES={"attempt_environment_sha256":"str","attempt_target_sha256":"str","attempt_validity_start":"str","attempt_validity_end":"str"}
OBSERVATIONS={"request_attempt_trials":"list","request_attempt_native_bindings":"list","request_attempt_native_events":"list",
    "request_attempt_conflict_candidates":"list","request_attempt_unbound_asgi_events":"list","native_attempt_binding_observed":"bool",
    "late_delivery_verified":"bool","automatic_retry_verified":"bool","request_attempt_scope_complete":"bool"}
CASES=("normal-first","normal-repeat","disconnect","replacement","after")

def normalize_policy(values):
    try:
        import re
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        if any(type(values[k]) is not str or re.fullmatch(r"[0-9a-f]{64}",values[k]) is None
            for k in ("attempt_environment_sha256","attempt_target_sha256")):raise ValueError
        start,end=(_time(values[k]) for k in ("attempt_validity_start","attempt_validity_end"))
        if not 0<(end-start).total_seconds()<=600:raise ValueError
        return start,end
    except (KeyError,TypeError,ValueError):raise ValueError("REQUEST_ATTEMPT_POLICY_INVALID") from None


class KubernetesRequestAttemptsExecutor(KubernetesRequestTerminationExecutor):
    version="0.2.0";check_id="REQ-12";collection_profile=PROFILE;policy_prefix="attempt"
    policy_normalizer=staticmethod(normalize_policy);transport_profile=REQUEST_ATTEMPTS_PROFILE

    def collect(self,ctx):
        start,end=normalize_policy(ctx.policy_values);token=uuid.uuid4().hex
        summary={"profile":PROFILE,"trials":[],"native":None,"diagnostic_codes":[],
            "fixture":{"profile":PROFILE},"whole_scope_complete":False,"late_delivery_verified":False,
            "scope":"explicit repeated external IDs; auxiliary session tags separate native selections, not native attempt IDs or automatic retry"}
        target={"schema":"guard-kubernetes-lifecycle-target/1","lab":ctx.target["lab"],"run_id":ctx.cprobe.binding["run_id"],
            "environment_ref":ctx.env.ref().model_dump(mode="json"),
            "kubernetes":{"context":ctx.cprobe.binding["context"],"engine":ctx.cprobe.binding["engine"]}}
        reader=None;packet=None;bycase={};sealed=False
        try:
            reader=KubernetesLifecycleReader(target,ctx.env,ctx._termination_prepared,runner=self.runner,
                binary=self.cfg.get("kubectl_binary","kubectl"),duration=self.cfg.get("max_duration_seconds",120))
            reader.snapshot()
            for case in CASES:
                if case in {"disconnect","replacement"} and not all(bycase.get(c,{}).get("normal_shape_verified") for c in CASES[:2]):continue
                if case=="replacement" and not bycase.get("disconnect",{}).get("expected_client_close"):continue
                group="normal" if case.startswith("normal-") else "shared" if case in {"disconnect","replacement"} else "after"
                rid="byul-req12-"+token+"-"+group;session="byul-req12-"+token+"-"+case
                body={"model":ctx.api_model,"prompt":"hello","max_tokens":8 if case=="disconnect" else 1,"temperature":0,
                    "stream":case=="disconnect","session_id":session}
                row={"case_id":"req12-"+case,"external_request_sha256":sha256_hex(rid.encode()),"auxiliary_session_sha256":sha256_hex(session.encode()),
                    "request_body_sha256":sha256_hex(canonical_json(body)),"attempted":False,"normal_shape_verified":False,
                    "expected_client_close":False,"outcome":None,"response_body_sha256":None,"capture_complete":False,
                    "started_at":None,"finished_at":None,"policy_window_verified":False}
                summary["trials"].append(row);bycase[case]=row
                try:
                    now=utcnow()
                    if not start<=now<=end:raise ValueError
                    request={"id":row["case_id"],"method":"POST","mode":"sse" if case=="disconnect" else "json",
                        "url":ctx.engine_url+"/v1/completions","headers":{"X-Request-Id":rid},"body":body}
                    if case=="disconnect":request["read_profile"]={"profile":"slow-reader-v1","chunk_bytes":1,"interval_ms":1,"close_after_reads":1}
                    row.update(attempted=True,started_at=now.isoformat())
                    response,=ctx.cprobe("kubernetes-probe",[request],ctx.timeout)
                    row.update(outcome=response["outcome"],response_body_sha256=response.get("body_sha256"),capture_complete=response.get("capture_complete") is True)
                    if case=="disconnect":
                        row["expected_client_close"]=any(r.get("id")==row["case_id"] and r.get("expected_client_close") is True for r in ctx.cprobe.report()["requests"])
                        if not row["expected_client_close"]:summary["diagnostic_codes"].append("REQUEST_ATTEMPT_CLIENT_CLOSE_UNVERIFIED")
                    elif (response["outcome"]==200 and response.get("capture_complete") is True and response.get("eof") is True
                            and response.get("utf8_valid") is True and type(response.get("body")) is str):
                        value=strict_json(response["body"].encode(),limit=128*1024)
                        row["normal_shape_verified"]=(type(value) is dict and value.get("model")==ctx.api_model and type(value.get("choices")) is list
                            and 1<=len(value["choices"])<=8 and all(type(c) is dict and type(c.get("text")) is str for c in value["choices"]))
                    if case!="disconnect" and not row["normal_shape_verified"]:summary["diagnostic_codes"].append("REQUEST_ATTEMPT_NORMAL_CONTROL_UNVERIFIED")
                except Exception:summary["diagnostic_codes"].append("REQUEST_ATTEMPT_PROBE_UNAVAILABLE")
                finally:
                    if row["attempted"]:row["finished_at"]=utcnow().isoformat()
                    row["policy_window_verified"]=bool(row["started_at"] and start<=_time(row["started_at"])<=_time(row["finished_at"])<=end)
                    if row["attempted"] and not row["policy_window_verified"]:summary["diagnostic_codes"].append("REQUEST_ATTEMPT_POLICY_WINDOW_UNVERIFIED")
        except Exception:summary["diagnostic_codes"].append("REQUEST_ATTEMPT_NATIVE_PREFLIGHT_UNAVAILABLE")
        finally:
            if reader is not None:
                try:sealed=reader.seal_session()["sealed"]
                except Exception:sealed=False
                if not sealed:summary["diagnostic_codes"].append("REQUEST_ATTEMPT_SEAL_UNVERIFIED")
                try:
                    engine=reader.binding["engine"]
                    packet=capture_session(reader,target,container=engine["container"],image=engine["image"],prepared=ctx._termination_prepared,
                        owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                except Exception:summary["diagnostic_codes"].append("REQUEST_ATTEMPT_CAPTURE_UNAVAILABLE")
                summary["native"]={"sealed":sealed,"session_sha256":sha256_hex(ctx._termination_prepared["session"].encode()),
                    "recorded_files_covered":bool(packet and packet["recorded_files_covered"]),"transport":reader.report(),
                    "capture_sha256":[r["capture"]["provenance"]["capture_sha256"] for r in packet["recordings"] if r["capture"]] if packet else []}
        projected=project_attempts(packet,summary["trials"]);summary["native_projection"]=projected
        summary["diagnostic_codes"].extend(projected["diagnostic_codes"]);summary["http_transport"]=ctx.cprobe.report()
        summary["diagnostic_codes"]=sorted(set(summary["diagnostic_codes"]))
        incomplete=bool(packet is None or not packet["recorded_files_covered"])
        gaps={"REQUEST_ATTEMPT_CLIENT_CLOSE_UNVERIFIED","REQUEST_ATTEMPT_NORMAL_CONTROL_UNVERIFIED","REQUEST_ATTEMPT_PROBE_UNAVAILABLE",
            "REQUEST_ATTEMPT_POLICY_WINDOW_UNVERIFIED","REQUEST_ATTEMPT_NATIVE_PREFLIGHT_UNAVAILABLE","REQUEST_ATTEMPT_SEAL_UNVERIFIED","REQUEST_ATTEMPT_CAPTURE_UNAVAILABLE",
            "REQUEST_ATTEMPT_EVENT_BOUND_REACHED"}
        ctx._termination_gap=bool(incomplete or gaps.intersection(summary["diagnostic_codes"]) or summary["http_transport"]["collection_gap"])
        ctx._termination_cleanup=list(summary["http_transport"]["cleanup_errors"])
        if reader is not None and reader.report().get("parent_cleanup_unverified"):
            ctx._termination_cleanup.append("REQUEST_ATTEMPT_PARENT_CLEANUP_UNVERIFIED");ctx._termination_gap=True
        values={"request_attempt_trials":summary["trials"],"request_attempt_native_bindings":projected["bindings"],
            "request_attempt_native_events":projected["events"],"request_attempt_conflict_candidates":projected["conflict_candidates"],
            "request_attempt_unbound_asgi_events":projected["unbound_asgi_events"],"native_attempt_binding_observed":projected["native_attempt_binding_observed"],
            "late_delivery_verified":False,"automatic_retry_verified":False,"request_attempt_scope_complete":False}
        normal={"attempted":bool(bycase.get("normal-first",{}).get("attempted")),
            "succeeded":all(bycase.get(c,{}).get("normal_shape_verified") for c in CASES[:2]) if bycase else None,
            "detail":"Two explicit noncancelled requests share an external ID; no late delivery or worker reclaim proof."}
        return {k:{"value":v,"collected":True} for k,v in values.items()},normal,[],summary
