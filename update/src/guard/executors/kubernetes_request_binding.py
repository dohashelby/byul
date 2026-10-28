"""REQ-11 Kubernetes transport; existing request cases/analyzer/judge remain shared."""
from pathlib import Path

from guard.models import DataOrigin, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import model_request_conditions
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from .engine_identity import _READ_TEMPLATE
from .http_common import Ctx
from .request_binding import RequestBindingExecutor, DEFAULTS


class KubernetesRequestBindingExecutor(RequestBindingExecutor):
    version="0.1.3"
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
        try:
            allowed={"target","endpoint","tokenize_endpoint","request_timeout","kubectl_binary","roles",
                     "wrong_key_env","denied_key_env","allow_mutating_routes",*DEFAULTS}
            if set(self.cfg)-allowed or self.cfg.get("allow_mutating_routes",False) is not False or adapter is None:
                raise ValueError
            self.validate_policy(check,policy_values)
            target=strict_json(Path(self.cfg["target"]).read_bytes())
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target))!=self._expected_target_sha256:
                raise ValueError
        except (KeyError,TypeError,ValueError,OSError):
            raise ValueError("KUBERNETES_REQUEST_BINDING_CONFIGURATION_INVALID") from None
        transport=KubernetesHTTP(target,env,duration=self.cfg.get("max_duration_seconds",DEFAULTS["max_duration_seconds"]),
            binary=self.cfg.get("kubectl_binary","kubectl"),runner=self._kubectl_runner,profile=REQUEST_BINDING_PROFILE)
        keys=self.keys()
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,
            timeout=float(self.cfg["request_timeout"]),allow_mutating=False,keys=keys,secrets=[v for v in keys.values() if v],
            cprobe=transport,sprobe=transport,workdir=workdir,started=utcnow())
        draft=self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if transport.injected:draft.data_origin=DataOrigin.EXAMPLE
        return draft

    def collect(self,ctx):
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
