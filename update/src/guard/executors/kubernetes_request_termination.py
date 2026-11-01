"""LIF10 closed disconnect procedure and existing native session capture."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import re
import uuid
from guard.adapters.vllm_request_termination import SCOPES, project_native
from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, TERMINATION_PROFILE
from .http_common import Ctx, HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session

PROFILE="kubernetes-request-termination-observation-v1"
POLICY_TYPES={"termination_environment_sha256":"str","termination_target_sha256":"str",
    "termination_validity_start":"str","termination_validity_end":"str","termination_max_delay_ms":"int",
    "termination_required_scopes":"list[str]"}
OBSERVATIONS={"termination_attempts":"list","termination_native_events":"list",
    "termination_deadline_conflict_candidates":"list","termination_required_scope_status":"list",
    "termination_binding_observed":"bool","termination_scope_complete":"bool","worker_gpu_release_verified":"bool"}


def _time(value):
    if type(value) is not str or len(value)>40:raise ValueError
    stamp=datetime.fromisoformat(value.replace("Z","+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:raise ValueError
    return stamp


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        if any(type(values[k]) is not str or re.fullmatch(r"[0-9a-f]{64}",values[k]) is None
                for k in ("termination_environment_sha256","termination_target_sha256")):raise ValueError
        start,end=(_time(values[k]) for k in ("termination_validity_start","termination_validity_end"))
        scopes=values["termination_required_scopes"]
        if (not 0<(end-start).total_seconds()<=600 or type(values["termination_max_delay_ms"]) is not int
                or not 0<=values["termination_max_delay_ms"]<=300000 or type(scopes) is not list
                or any(type(s) is not str or s not in SCOPES for s in scopes) or len(set(scopes))!=len(scopes)):
            raise ValueError
        return start,end
    except (KeyError,TypeError,ValueError):raise ValueError("TERMINATION_POLICY_INVALID") from None


class KubernetesRequestTerminationExecutor(HttpCheckExecutor):
    name="request_termination";version="0.2.0"
    requires_roles=False;requires_route_scope=False;functional_via="engine"
    check_id="LIF-10";collection_profile=PROFILE;policy_prefix="termination"
    policy_normalizer=staticmethod(normalize_policy);transport_profile=TERMINATION_PROFILE
    supported_native_profiles=frozenset({"vllm-native-lifecycle-metadata/8","vllm-native-lifecycle-metadata/9"})
    configuration_keys=frozenset({'target','prepared','retry_scope','kubectl_binary','max_duration_seconds'})

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target_sha256=expected_target_sha256

    def validate_policy(self,check,values):
        consumer=self._consumer(check)
        if consumer is not self:return consumer.validate_policy(check,values)
        if check.id!=self.check_id or getattr(check,"collection_profile",None)!=self.collection_profile:
            raise ValueError("TERMINATION_PROFILE_UNSUPPORTED")
        self.policy_normalizer(values)

    def _consumer(self,check):
        # Only the public dispatcher selects consumers; subclasses never redispatch.
        if type(self) is not KubernetesRequestTerminationExecutor:return self
        from .kubernetes_retry_records import KubernetesRetryRecordExecutor
        from .retry_record_contract import PROFILE as RETRY_PROFILE
        from .kubernetes_request_attempts import KubernetesRequestAttemptsExecutor,PROFILE as ATTEMPT_PROFILE
        branches={('LIF-10',PROFILE):None,('LIF-12',RETRY_PROFILE):KubernetesRetryRecordExecutor,
            ('REQ-12',ATTEMPT_PROFILE):KubernetesRequestAttemptsExecutor}
        key=(check.id,getattr(check,'collection_profile',None))
        if key not in branches:
            # Preserve the established retry consumer's public rejection code.
            if check.id=='LIF-12':raise ValueError('RETRY_PROFILE_UNSUPPORTED')
            raise ValueError('TERMINATION_PROFILE_UNSUPPORTED')
        implementation=branches[key]
        if implementation is None:return self
        return implementation(self.cfg,runner=self.runner,environ=self._environ,
            expected_target_sha256=self.expected_target_sha256)

    def run(self,check,env,policy_values,adapter,workdir):
        consumer=self._consumer(check)
        if consumer is not self:return consumer.run(check,env,policy_values,adapter,workdir)
        self.validate_policy(check,policy_values)
        from guard.collectors.lifecycle import load_prepared
        try:
            if set(self.cfg)-self.configuration_keys or adapter is None:raise ValueError
            if 'retry_scope' in self.cfg and (type(self.cfg['retry_scope']) is not str or not self.cfg['retry_scope']):raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id,env.model.revision)):raise ValueError
            with Path(self.cfg["target"]).open("rb") as stream:target=strict_json(stream.read(65537),limit=65536)
            digest=sha256_hex(canonical_json(target))
            if (digest!=policy_values[self.policy_prefix+"_target_sha256"] or env.ref().sha256!=policy_values[self.policy_prefix+"_environment_sha256"]
                    or self.expected_target_sha256 is not None and self.expected_target_sha256!=digest):raise ValueError
            prepared=load_prepared(self.cfg["prepared"])
            if prepared["profile"] not in self.supported_native_profiles:raise ValueError
            start,end=self.policy_normalizer(policy_values)
            if not start<=utcnow()<=end:raise ValueError
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):raise ValueError("TERMINATION_CONFIGURATION_INVALID") from None
        transport=KubernetesHTTP(target,env,duration=self.cfg.get("max_duration_seconds",120),runner=self.runner,
            binary=self.cfg.get("kubectl_binary","kubectl"),profile=self.transport_profile)
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,
            allow_mutating=False,keys={"none":None},secrets=[],cprobe=transport,workdir=workdir,started=utcnow(),
            http_probe_profile=self.transport_profile)
        ctx._termination_prepared=prepared
        return self._finish(ctx)

    def collect(self,ctx):
        start,end=normalize_policy(ctx.policy_values)
        summary={"profile":PROFILE,"attempts":[],"native":None,"diagnostic_codes":[],
            "fixture":{"profile":PROFILE},"scope":"one selected client disconnect; controller/worker/ref claims remain separate",
            "worker_gpu_release_verified":False,"whole_scope_complete":False}
        native_target={"schema":"guard-kubernetes-lifecycle-target/1","lab":ctx.target["lab"],
            "run_id":ctx.cprobe.binding["run_id"],"environment_ref":ctx.env.ref().model_dump(mode="json"),
            "kubernetes":{"context":ctx.cprobe.binding["context"],"engine":ctx.cprobe.binding["engine"]}}
        reader=None;packet=None;replies={};token=uuid.uuid4().hex
        request_sha=None;sealed=False
        try:
            reader=KubernetesLifecycleReader(native_target,ctx.env,ctx._termination_prepared,
                runner=self.runner,binary=self.cfg.get("kubectl_binary","kubectl"),duration=self.cfg.get("max_duration_seconds",120))
            reader.snapshot()
            for slot in ("before","disconnect","after"):
                if slot=="disconnect" and not replies.get("before",{}).get("normal_shape_verified"):continue
                rid="byul-lif10-"+token+"-"+slot
                body={"model":ctx.api_model,"prompt":"hello","max_tokens":8 if slot=="disconnect" else 1,
                    "temperature":0,"stream":slot=="disconnect"}
                row={"case_id":"lif10-"+slot,"request_id_sha256":sha256_hex(rid.encode()),
                    "request_body_sha256":sha256_hex(canonical_json(body)),"attempted":False,"started_at":None,
                    "finished_at":None,"outcome":None,"normal_shape_verified":False,"expected_client_close":False,
                    "capture_complete":False,"policy_window_verified":False}
                summary["attempts"].append(row);replies[slot]=row
                try:
                    now=utcnow()
                    if not start<=now<=end:raise ValueError
                    request={"id":row["case_id"],"method":"POST","mode":"sse" if slot=="disconnect" else "json",
                        "url":ctx.engine_url+"/v1/completions","headers":{"X-Request-Id":rid},"body":body}
                    if slot=="disconnect":request["read_profile"]={"profile":"slow-reader-v1","chunk_bytes":1,"interval_ms":1,"close_after_reads":1}
                    row.update(attempted=True,started_at=now.isoformat())
                    if slot=="disconnect":request_sha=row["request_id_sha256"]
                    response,=ctx.cprobe("kubernetes-probe",[request],ctx.timeout)
                    row.update(outcome=response["outcome"],capture_complete=response.get("capture_complete") is True)
                    if slot=="disconnect":
                        request_sha=row["request_id_sha256"]
                        row["expected_client_close"]=any(r.get("id")==row["case_id"] and r.get("expected_client_close") is True
                            for r in ctx.cprobe.report()["requests"])
                        if not row["expected_client_close"]:summary["diagnostic_codes"].append("TERMINATION_CLIENT_CLOSE_UNVERIFIED")
                    elif (response["outcome"]==200 and response.get("capture_complete") is True and response.get("eof") is True
                            and response.get("utf8_valid") is True and type(response.get("body")) is str):
                        value=strict_json(response["body"].encode(),limit=128*1024)
                        row["normal_shape_verified"]=(type(value) is dict and value.get("model")==ctx.api_model
                            and type(value.get("choices")) is list and 1<=len(value["choices"])<=8
                            and all(type(c) is dict and type(c.get("text")) is str for c in value["choices"]))
                    if slot!="disconnect" and not row["normal_shape_verified"]:summary["diagnostic_codes"].append("TERMINATION_NORMAL_CONTROL_UNVERIFIED")
                except Exception:summary["diagnostic_codes"].append("TERMINATION_PROBE_UNAVAILABLE")
                finally:
                    if row["attempted"]:row["finished_at"]=utcnow().isoformat()
                    row["policy_window_verified"]=bool(row["started_at"] and start<=_time(row["started_at"])<=_time(row["finished_at"])<=end)
                    if row["attempted"] and not row["policy_window_verified"]:summary["diagnostic_codes"].append("TERMINATION_POLICY_WINDOW_UNVERIFIED")
        except Exception:summary["diagnostic_codes"].append("TERMINATION_NATIVE_PREFLIGHT_UNAVAILABLE")
        finally:
            if reader is not None:
                try:sealed=reader.seal_session()["sealed"]
                except Exception:sealed=False
                if not sealed:summary["diagnostic_codes"].append("TERMINATION_SESSION_SEAL_UNVERIFIED")
                try:
                    engine=reader.binding["engine"]
                    packet=capture_session(reader,native_target,container=engine["container"],image=engine["image"],
                        prepared=ctx._termination_prepared,owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                except Exception:summary["diagnostic_codes"].append("TERMINATION_NATIVE_CAPTURE_UNAVAILABLE")
                summary["native"]={"session_sha256":sha256_hex(ctx._termination_prepared["session"].encode()),"sealed":sealed,
                    "recorded_files_covered":bool(packet and packet["recorded_files_covered"]),
                    "live_census_stable":bool(packet and packet["live_census_stable"]),"transport":reader.report(),
                    "recordings":[{"pid":r["pid"],"start_ticks":r["start_ticks"],"captured":r["capture"] is not None,
                        "capture_sha256":r["capture"]["provenance"]["capture_sha256"] if r["capture"] else None,
                        "image_digest_verified":r["capture"]["image_digest_verified"] if r["capture"] else False}
                        for r in packet["recordings"]] if packet else []}
        projected=project_native(packet,request_sha,ctx.policy_values["termination_max_delay_ms"],ctx.policy_values["termination_required_scopes"])
        summary["native_projection"]=projected;summary["diagnostic_codes"].extend(projected["diagnostic_codes"])
        summary["http_transport"]=ctx.cprobe.report()
        summary["diagnostic_codes"]=list(dict.fromkeys(summary["diagnostic_codes"]))
        # Missing actual resource proof is UNKNOWN, not a transport execution failure.
        gaps={"TERMINATION_NATIVE_PREFLIGHT_UNAVAILABLE","TERMINATION_PROBE_UNAVAILABLE","TERMINATION_SESSION_SEAL_UNVERIFIED",
            "TERMINATION_NATIVE_CAPTURE_UNAVAILABLE","TERMINATION_POLICY_WINDOW_UNVERIFIED","TERMINATION_CLIENT_CLOSE_UNVERIFIED",
            "TERMINATION_NORMAL_CONTROL_UNVERIFIED","TERMINATION_NATIVE_COVERAGE_UNVERIFIED",
            "TERMINATION_NATIVE_PACKET_UNAVAILABLE","TERMINATION_EVENT_BOUND_REACHED",
            "TERMINATION_NATIVE_PROCESS_BINDING_UNVERIFIED"}
        ctx._termination_gap=bool(gaps.intersection(summary["diagnostic_codes"]) or summary["http_transport"]["collection_gap"])
        ctx._termination_cleanup=list(summary["http_transport"]["cleanup_errors"])
        if reader is not None and reader.report().get("parent_cleanup_unverified"):
            ctx._termination_cleanup.append("TERMINATION_NATIVE_PARENT_CLEANUP_UNVERIFIED");ctx._termination_gap=True
        values={"termination_attempts":summary["attempts"],"termination_native_events":projected["events"],
            "termination_deadline_conflict_candidates":projected["deadline_conflict_candidates"],
            "termination_required_scope_status":projected["scope_status"],"termination_binding_observed":projected["binding_observed"],
            "termination_scope_complete":False,"worker_gpu_release_verified":False}
        observations={k:{"value":v,"collected":True} for k,v in values.items()}
        normal={"attempted":bool(replies.get("before",{}).get("attempted")),
            "succeeded":bool(replies.get("before",{}).get("normal_shape_verified")) if "before" in replies else None,
            "detail":"Selected alias normal JSON shape only; native termination and reclaim remain separate."}
        return observations,normal,[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx)
        ceiling=DataOrigin.EXAMPLE if self.runner is not None or ctx.env.data_origin==DataOrigin.EXAMPLE or ctx.target["lab"] else DataOrigin.DECLARED
        draft.data_origin=min(draft.data_origin,ceiling,key=lambda value:ORIGIN_STRENGTH[value])
        if ctx._termination_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._termination_cleanup)
        return draft
