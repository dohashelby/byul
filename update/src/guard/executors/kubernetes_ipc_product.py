"""A1 registration boundary for A2's immutable Kubernetes Unix IPC consumer."""

from guard.collectors.file import load_bounded_json
from guard.adapters.engine_profiles import supports
import math
from guard.models import DataOrigin,ExecStatus,ORIGIN_STRENGTH,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.engine_http import KubernetesBindingError,KubernetesHTTP,REQUEST_BINDING_PROFILE
from .base import record_origin_ceiling
from .http_common import Ctx,HttpCheckExecutor
from . import kubernetes_ipc_metadata as path_metadata
from . import kubernetes_worker_trust as worker_trust
from .kubernetes_unix_ipc import collect_kubernetes_unix_ipc,validate_inputs,VERSION

CONFIG_KEYS={'target','kubectl_binary','max_duration_seconds','actor_specs','connect_timeout','total_timeout'}
NORMAL_TIMEOUT=3


from .lifecycle_common_input_consumption import LifecycleCommonInputConsumer


class KubernetesIPCProductExecutor(LifecycleCommonInputConsumer, HttpCheckExecutor):
    name='unix_ipc';version=VERSION;requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256
        self.worker_metadata='worker_trust_metadata_only' in self.cfg
        self.path_metadata='protected_path_metadata_only' in self.cfg
        if self.worker_metadata:
            self.version=worker_trust.VERSION
        elif self.path_metadata:
            self.version=path_metadata.VERSION

    def validate_policy(self,check,values):
        if self.worker_metadata:
            if (self.cfg['worker_trust_metadata_only'] is not True or set(self.cfg)-worker_trust.CONFIG_KEYS
                    or self.b_common_inputs is not None):
                raise ValueError('KUBERNETES_WORKER_TRUST_METADATA_CONFIGURATION_INVALID')
            return worker_trust.validate_policy(check,values)
        if self.path_metadata:
            if (self.cfg['protected_path_metadata_only'] is not True or set(self.cfg) - path_metadata.CONFIG_KEYS
                    or self.b_common_inputs is not None):
                raise ValueError('KUBERNETES_IPC_PATH_METADATA_CONFIGURATION_INVALID')
            return path_metadata.validate_policy(check, values)
        try:
            validate_inputs(check,values,actor_specs=self.cfg['actor_specs'],connect_timeout=self.cfg['connect_timeout'],
                total_timeout=self.cfg['total_timeout'])
        except (KeyError,TypeError):raise ValueError('KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID') from None

    def _duration(self, env, metadata_only):
        duration = self.cfg.get('max_duration_seconds', 120)
        minimum = 0 if metadata_only else self.cfg['total_timeout'] + 2 + NORMAL_TIMEOUT
        if ((not metadata_only and set(self.cfg) - CONFIG_KEYS)
                or type(duration) not in (int, float) or not math.isfinite(duration) or not minimum < duration <= 300
                or not supports(env.engine.name, env.engine.version, 'unix_ipc')
                or any(type(value) is not str or not value for value in (env.model.id, env.model.revision))):
            raise ValueError
        return duration

    def _validate_target(self, target, metadata_only, *, expected_target_sha256=None):
        pins = [pin for pin in (self.expected_target, expected_target_sha256) if pin is not None]
        if target.get('lab') is not (not metadata_only) or metadata_only and not pins:
            raise ValueError
        if pins:
            digest = sha256_hex(canonical_json(target))
            if any(pin != digest for pin in pins):
                raise ValueError

    def _transport(self, target, env, duration):
        return KubernetesHTTP(target, env, duration=duration, binary=self.cfg.get('kubectl_binary', 'kubectl'),
                              runner=self.runner, profile=REQUEST_BINDING_PROFILE)

    def prepared_worker_trust(self, check, env, values, *, expected_target_sha256=None):
        """Qualify only the saved PLT-06 marker metadata declaration, without a collection."""
        if not self.worker_metadata:
            raise ValueError('KUBERNETES_WORKER_TRUST_METADATA_CONFIGURATION_INVALID')
        self.validate_policy(check, values)
        self._prepared_metadata_target(env, expected_target_sha256=expected_target_sha256)

    def prepared_protected_paths(self, check, env, values, *, expected_target_sha256=None):
        """Qualify only the saved PLT-02 path metadata declaration."""
        if not self.path_metadata or self.worker_metadata:
            raise ValueError('KUBERNETES_IPC_PATH_METADATA_CONFIGURATION_INVALID')
        self.validate_policy(check, values)
        self._prepared_metadata_target(env, expected_target_sha256=expected_target_sha256)

    def _read_metadata_target(self):
        from guard.adapters.vllm_file_read_sensor import dependency

        target = strict_json(dependency(self.cfg['target'], None), limit=65536)
        if type(target) is not dict:
            raise ValueError
        return target

    def _prepared_metadata_target(self, env, *, expected_target_sha256):
        try:
            duration = self._duration(env, metadata_only=True)
            target = self._read_metadata_target()
            self._validate_target(target, metadata_only=True, expected_target_sha256=expected_target_sha256)
        except (KeyError, ValueError, TypeError, OverflowError, OSError, RuntimeError):
            raise ValueError('KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID') from None
        try:
            self._transport(target, env, duration)
        except KubernetesBindingError as error:
            raise ValueError(str(error)) from None

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        metadata_only = self.worker_metadata or self.path_metadata
        try:
            if adapter is None:raise ValueError
            duration = self._duration(env, metadata_only)
            if metadata_only:
                target = self._read_metadata_target()
            else:
                target = load_bounded_json(self.cfg['target'])
            self._validate_target(target, metadata_only)
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):raise ValueError('KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID') from None
        transport = self._transport(target, env, duration)
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=NORMAL_TIMEOUT,
            allow_mutating=False,keys={},secrets=[],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=REQUEST_BINDING_PROFILE)
        return self._finish(ctx)

    def collect(self,ctx):
        if self.worker_metadata:
            return worker_trust.collect(ctx)
        if self.path_metadata:
            return path_metadata.collect(ctx)
        observations,normal,challenges,summary=collect_kubernetes_unix_ipc(ctx,actor_specs=self.cfg['actor_specs'],
            connect_timeout=self.cfg['connect_timeout'],total_timeout=self.cfg['total_timeout'])
        report=summary['kubernetes'];cleanup=list(getattr(ctx,'_kubernetes_ipc_cleanup_errors',[]))
        if report is not None:cleanup.extend(report['cleanup_errors'])
        ctx._ipc_product_cleanup=sorted(set(cleanup))
        ctx._ipc_product_gap=bool(summary['collection_errors'] or cleanup or report is None or report['collection_gap'])
        return observations,normal,challenges,summary

    def _finish(self,ctx):
        draft=super()._finish(ctx)
        if self.worker_metadata or self.path_metadata:
            draft.cleanup_errors.extend(ctx._ipc_metadata_cleanup)
            if ctx._ipc_metadata_gap:
                draft.status=ExecStatus.ABORTED
                reason = ('KUBERNETES_WORKER_TRUST_METADATA_UNAVAILABLE' if self.worker_metadata
                          else 'KUBERNETES_IPC_PATH_METADATA_UNAVAILABLE')
                draft.abort_reason=draft.abort_reason or reason
            ceiling=getattr(ctx,'_kubernetes_process_data_origin',DataOrigin.DECLARED)
            if draft.status != ExecStatus.COMPLETED:
                ceiling=min(ceiling,DataOrigin.DECLARED,key=ORIGIN_STRENGTH.get)
            draft.data_origin=min(draft.data_origin,ceiling,key=ORIGIN_STRENGTH.get)
            return draft
        ceiling = record_origin_ceiling(ctx.env.data_origin, local_example=self.runner is not None or ctx.target['lab'])
        return draft.finish_collection(ceiling, ctx._ipc_product_gap, ctx._ipc_product_cleanup)
