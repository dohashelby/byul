"""REQ-11 Kubernetes transport; existing request cases/analyzer/judge remain shared."""
from pathlib import Path

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import model_request_conditions
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP, REQUEST_BINDING_PROFILE
from .content_common import content_request
from .engine_identity import _READ_TEMPLATE
from .http_common import Ctx
from .request_binding import RequestBindingExecutor, DEFAULTS


NORMAL_ONLY = "normal_request_binding_only"


def _ordinary_response(row, mode, model):
    if row.get("outcome") != 200 or row.get("capture_complete") is not True or row.get("eof") is not True:
        return None
    if mode == "json":
        if row.get("content_type") != "application/json":
            return None
        frames = [strict_json(row["body"].encode(), limit=8192)]
    else:
        events = row.get("events")
        if (row.get("content_type") != "text/event-stream" or row.get("terminal_seen") is not True
                or type(events) is not list or not 2 <= len(events) <= 16
                or [event["seq"] for event in events] != list(range(len(events)))
                or events[-1]["data"] != "[DONE]"):
            return None
        frames = [strict_json(event["data"].encode(), limit=8192) for event in events[:-1]]
    if any(type(frame) is not dict or frame.get("model") != model or type(frame.get("choices")) is not list
           or len(frame["choices"]) != 1 or frame["choices"][0].get("index") != 0 for frame in frames):
        return None
    expected_object = "chat.completion" if mode == "json" else "chat.completion.chunk"
    ids = {frame.get("id") for frame in frames}
    if (len(ids) != 1 or any(frame.get("object") != expected_object for frame in frames)
            or sum(frame["choices"][0].get("finish_reason") in {"stop", "length"} for frame in frames) != 1):
        return None
    identifier, = ids
    return identifier if type(identifier) is str and 9 <= len(identifier) <= 128 and identifier.startswith("chatcmpl-") else None


class KubernetesRequestBindingExecutor(RequestBindingExecutor):
    version="0.2.0"
    _uses_docker_identity=False

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ)
        if expected_target_sha256 is not None and (type(expected_target_sha256) is not str or len(expected_target_sha256)!=64
                or any(c not in "0123456789abcdef" for c in expected_target_sha256)):
            raise ValueError("KUBERNETES_REQUEST_BINDING_CONFIGURATION_INVALID")
        self._kubectl_runner=runner
        self._expected_target_sha256=expected_target_sha256

    def _api_model(self,ctx):
        return ctx.api_model

    def _template_snapshot(self,ctx,identity,argv,deadline):
        return ctx.cprobe.template_snapshot(identity,argv,ctx.target["engine"].get("command"),_READ_TEMPLATE)

    def run(self,check,env,policy_values,adapter,workdir):
        generated_only = NORMAL_ONLY in self.cfg
        try:
            allowed={"target","endpoint","tokenize_endpoint","request_timeout","kubectl_binary","roles",
                     "wrong_key_env","denied_key_env","allow_mutating_routes",NORMAL_ONLY,*DEFAULTS}
            if set(self.cfg)-allowed or self.cfg.get("allow_mutating_routes",False) is not False or adapter is None:
                raise ValueError
            self.validate_policy(check,policy_values)
            target=strict_json(Path(self.cfg["target"]).read_bytes())
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target))!=self._expected_target_sha256:
                raise ValueError
            if generated_only and (check.id != "REQ-11" or self.cfg[NORMAL_ONLY] is not True
                    or self._expected_target_sha256 is None or target["lab"] is not False
                    or set(self.cfg) != {NORMAL_ONLY, "target", "request_timeout"}
                    or self.cfg["request_timeout"] != 120.0
                    or policy_values != {"result_binding": "originating_connection", "client_request_id_trust": "untrusted_correlation"}):
                raise ValueError
        except (KeyError,TypeError,ValueError,OSError):
            raise ValueError("KUBERNETES_REQUEST_BINDING_CONFIGURATION_INVALID") from None
        transport=KubernetesHTTP(target,env,duration=self.cfg.get("max_duration_seconds",DEFAULTS["max_duration_seconds"]),
            binary=self.cfg.get("kubectl_binary","kubectl"),runner=self._kubectl_runner,profile=REQUEST_BINDING_PROFILE)
        keys={} if generated_only else self.keys()
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,
            timeout=8.0 if generated_only else float(self.cfg["request_timeout"]),allow_mutating=False,keys=keys,secrets=[v for v in keys.values() if v],
            cprobe=transport,sprobe=transport,workdir=workdir,started=utcnow())
        if generated_only:
            self.version += "+normal-generated"
        draft=self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if transport.injected:draft.data_origin=DataOrigin.EXAMPLE
        if generated_only and not draft.normal_control.succeeded:
            draft.status = ExecStatus.ABORTED
            draft.abort_reason = "REQUEST_BINDING_ORDINARY_GENERATED_SCOPE_INCOMPLETE"
        return draft

    def collect(self,ctx):
        if self.cfg.get(NORMAL_ONLY) is True:
            observations,normal,challenges,summary=self._generated_pair(ctx)
        else:
            observations,normal,challenges,summary=super().collect(ctx)
        report=ctx.cprobe.report()
        summary["kubernetes"]=report
        if ctx.cprobe.model_mapping.get("schema")=="guard.model-request-mapping/1":
            from .model_mounts import analyze_model_mount
            snapshots=ctx.cprobe.model_snapshots
            target={"schema":"guard-kubernetes-lifecycle-target/1","lab":ctx.target["lab"],
                "run_id":ctx.cprobe.binding["run_id"],"environment_ref":ctx.env.ref().model_dump(mode="json"),
                "kubernetes":{"context":ctx.cprobe.binding["context"],"engine":ctx.cprobe.binding["engine"]}}
            before=snapshots[0] if snapshots else {}
            after=snapshots[-1] if len(snapshots)==2 and not report["collection_gap"] else {}
            summary["model_mount"]=analyze_model_mount(ctx.env,target,ctx.cprobe.model_mapping,before,after)
            summary["model_mount"]["scope"]="first_to_last_verified_ready_snapshot"
            summary["model_mount"]["whole_run_coverage_verified"]=False
        summary["model_mapping_sha256"]=sha256_hex(canonical_json(ctx.cprobe.model_mapping))
        summary["fixture"].update(transport_profile=REQUEST_BINDING_PROFILE,
            probe_source_sha256=report["probe_source_sha256"],model_mapping_sha256=sha256_hex(canonical_json(model_request_conditions(ctx.cprobe.model_mapping))))
        summary["limitations"].append("Kubernetes Pod identity is not actual GPU/tensor/model-consumption or GPU-release proof.")
        return observations,normal,challenges,summary

    def _generated_pair(self, ctx):
        cases = []
        model = self._api_model(ctx)
        functional, = ctx.check.functional_probes
        for mode in ("json", "sse"):
            labels = [f"{mode}-generated-{slot}" for slot in ("A", "B")]
            requests = []
            for label, text in zip(labels, (functional.body["messages"][0]["content"], "guard normal response sample")):
                body = {"model": model, "messages": [{"role": "user", "content": text}],
                        "max_tokens": 1, "stream": mode == "sse"}
                request = content_request(ctx, "engine", label, functional.path, body)
                requests.append({**request, "mode": mode, "headers": {}})
            try:
                results = ctx.cprobe(ctx.trusted_net, requests, ctx.timeout)
                by_id = {row["id"]: row for row in results}
                if len(results) != 2 or set(by_id) != set(labels):
                    raise ValueError
                ids = [_ordinary_response(by_id[label], mode, model) for label in labels]
            except (KubernetesBindingError, OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
                cases.append({"mode": mode, "received": None, "complete_pair": False,
                              "diagnostic": "ORDINARY_GENERATED_RESPONSE_UNAVAILABLE"})
                continue
            cases.append({"mode": mode, "received": [by_id[label].get("outcome") for label in labels],
                          "complete_pair": all(ids) and len(set(ids)) == 2})

        succeeded = all(case["complete_pair"] for case in cases)
        verified = [f"{case['mode']}:generated" for case in cases if case["complete_pair"]]
        observations = {obs.key: {"value": None, "collected": False} for obs in ctx.check.required_observations}
        for key, value in {"cases_planned": ["json:generated", "sse:generated"],
                           "cases_attempted": ["json:generated", "sse:generated"],
                           "normal_controls": succeeded, "collection_complete": False,
                           "output_binding_gap": "owner-bound normal response shapes only",
                           "functional_probes_planned": [functional.id],
                           "functional_probe_results": {functional.id: "pass" if succeeded else "no_response"}}.items():
            observations[key] = {"value": value, "collected": True}
        return observations, {"attempted": True, "succeeded": succeeded, "detail": "two ordinary generated request pairs"}, [
            {"step": step, "attempted": False} for step in ctx.check.challenge_steps], {
            "fixture": {"profile": "owned-normal-generated-response-pairs/1", "max_output_tokens": 1},
            "observed_response_shapes": verified, "pairs": cases,
            "limitations": ["Consumer input and worker-to-client output attribution remain unverified."]}
