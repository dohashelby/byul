"""A1 registration boundary for A2's immutable Kubernetes Unix IPC consumer."""
from pathlib import Path
import math
from guard.models import DataOrigin,ExecStatus,ORIGIN_STRENGTH,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP,REQUEST_BINDING_PROFILE
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_unix_ipc import collect_kubernetes_unix_ipc,validate_inputs,VERSION

CONFIG_KEYS={'target','kubectl_binary','max_duration_seconds','actor_specs','connect_timeout','total_timeout'}
NORMAL_TIMEOUT=3


class KubernetesIPCProductExecutor(HttpCheckExecutor):
    name='unix_ipc';version=VERSION;requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        try:
            validate_inputs(check,values,actor_specs=self.cfg['actor_specs'],connect_timeout=self.cfg['connect_timeout'],
                total_timeout=self.cfg['total_timeout'])
        except (KeyError,TypeError):raise ValueError('KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID') from None

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        try:
            duration=self.cfg.get('max_duration_seconds',120)
            if (set(self.cfg)-CONFIG_KEYS or adapter is None or type(duration) not in (int,float) or not math.isfinite(duration)
                    or not self.cfg['total_timeout']+2+NORMAL_TIMEOUT<duration<=300
                    or env.engine.name!='vllm' or env.engine.version!='0.31.0'
                    or any(type(v) is not str or not v for v in (env.model.id,env.model.revision))):raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            if (target.get('lab') is not True or self.expected_target is not None
                    and sha256_hex(canonical_json(target))!=self.expected_target):raise ValueError
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):raise ValueError('KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID') from None
        transport=KubernetesHTTP(target,env,duration=duration,binary=self.cfg.get('kubectl_binary','kubectl'),
            runner=self.runner,profile=REQUEST_BINDING_PROFILE)
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=NORMAL_TIMEOUT,
            allow_mutating=False,keys={},secrets=[],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=REQUEST_BINDING_PROFILE)
        return self._finish(ctx)

    def collect(self,ctx):
        observations,normal,challenges,summary=collect_kubernetes_unix_ipc(ctx,actor_specs=self.cfg['actor_specs'],
            connect_timeout=self.cfg['connect_timeout'],total_timeout=self.cfg['total_timeout'])
        report=summary['kubernetes'];cleanup=list(getattr(ctx,'_kubernetes_ipc_cleanup_errors',[]))
        if report is not None:cleanup.extend(report['cleanup_errors'])
        ctx._ipc_product_cleanup=sorted(set(cleanup))
        ctx._ipc_product_gap=bool(summary['collection_errors'] or cleanup or report is None or report['collection_gap'])
        return observations,normal,challenges,summary

    def _finish(self,ctx):
        draft=super()._finish(ctx)
        ceiling=DataOrigin.EXAMPLE if self.runner is not None or ctx.target['lab'] or ctx.env.data_origin==DataOrigin.EXAMPLE else DataOrigin.DECLARED
        draft.data_origin=min(draft.data_origin,ceiling,key=lambda value:ORIGIN_STRENGTH[value])
        if ctx._ipc_product_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._ipc_product_cleanup)
        return draft
