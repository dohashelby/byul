"""Existing container_config dispatch for A-2's API-only SUP-04 consumer."""
from pathlib import Path

from guard.models import DataOrigin, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, KubernetesBindingError, REQUEST_BINDING_PROFILE
from .http_common import Ctx, HttpCheckExecutor
from .running_image_approval import PROFILE, validate_image_approval_policy, collect_kubernetes_running_image_approval


class KubernetesContainerConfigExecutor(HttpCheckExecutor):
    name="container_config"
    version="0.1.0"
    requires_roles=False
    requires_route_scope=False
    functional_via="engine"

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ)
        if expected_target_sha256 is not None and (type(expected_target_sha256) is not str
                or len(expected_target_sha256)!=64 or any(c not in "0123456789abcdef" for c in expected_target_sha256)):
            raise ValueError("KUBERNETES_CONTAINER_CONFIGURATION_INVALID")
        self._kubectl_runner=runner
        self._expected_target_sha256=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!="SUP-04" or getattr(check,"collection_profile",None)!=PROFILE:
            raise ValueError("KUBERNETES_CONTAINER_PROFILE_UNSUPPORTED")
        validate_image_approval_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        try:
            if set(self.cfg)-{"target","kubectl_binary","max_duration_seconds"} or adapter is None:
                raise ValueError
            with Path(self.cfg["target"]).open("rb") as stream:
                target=strict_json(stream.read(65537),limit=65536)
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target))!=self._expected_target_sha256:
                raise ValueError
        except (KeyError,TypeError,ValueError,OSError):
            raise ValueError("KUBERNETES_CONTAINER_CONFIGURATION_INVALID") from None
        transport=KubernetesHTTP(target,env,duration=self.cfg.get("max_duration_seconds",30),
            binary=self.cfg.get("kubectl_binary","kubectl"),runner=self._kubectl_runner,profile=REQUEST_BINDING_PROFILE)
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,
            timeout=3.0,allow_mutating=False,keys={},secrets=[],cprobe=transport,
            workdir=workdir,started=utcnow())
        draft=self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if transport.injected:draft.data_origin=DataOrigin.EXAMPLE
        return draft

    def collect(self,ctx):
        snapshots=[]
        for _ in range(2):
            try:snapshots.append(ctx.cprobe.snapshot())
            except KubernetesBindingError:snapshots.append({})
        observations,normal,challenges,summary=collect_kubernetes_running_image_approval(ctx,*snapshots)
        summary["kubernetes"]=ctx.cprobe.report()
        summary["dispatch"]={"connected":True,"scope":"api-metadata-only",
            "runtime_image_approval_connected":False,"whole_run_coverage_verified":False}
        summary["fixture"]["snapshot_profile"]="kubernetes-owned-pod-api-read-v1"
        summary["limitations"].append("The existing product dispatcher consumes API snapshots only; runtime image approval and inference remain unavailable.")
        return observations,normal,challenges,summary
