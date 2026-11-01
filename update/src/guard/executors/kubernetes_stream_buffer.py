"""LIF04 existing CLI, owned read controls and opt-in actual transport metadata."""
from pathlib import Path
import uuid
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_stream_buffer_http import KubernetesStreamBufferHTTP,HTTP_PROFILE,SLOTS
from guard.adapters import vllm_stream_buffer_sensor as sensor,vllm_stream_buffer_metadata as metadata
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session
from .stream_buffer_contract import PROFILE,normalize_policy,stamp


class KubernetesStreamBufferExecutor(HttpCheckExecutor):
    name='stream_buffer';version='0.1.0';requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='LIF-04' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('STREAM_BUFFER_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        from guard.collectors.lifecycle import load_prepared
        self.validate_policy(check,policy_values)
        try:
            if set(self.cfg)-{'target','prepared','kubectl_binary','max_duration_seconds'} or adapter is None:raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id,env.model.revision)):raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            digest=sha256_hex(canonical_json(target));prepared=load_prepared(self.cfg['prepared']);start,end,profiles=normalize_policy(policy_values)
            if (digest!=policy_values['buffer_target_sha256'] or env.ref().sha256!=policy_values['buffer_environment_sha256']
                    or self.expected_target is not None and digest!=self.expected_target or prepared['profile']!=sensor.PROFILE
                    or sha256_hex(canonical_json(prepared))!=policy_values['buffer_prepared_sha256'] or not start<=utcnow()<=end):raise ValueError
        except (ValueError,KeyError,TypeError,RuntimeError,OSError):raise ValueError('STREAM_BUFFER_CONFIGURATION_INVALID') from None
        transport=KubernetesStreamBufferHTTP(target,env,read_profiles=profiles,runner=self.runner,
            duration=self.cfg.get('max_duration_seconds',120),binary=self.cfg.get('kubectl_binary','kubectl'))
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys={'none':None},secrets=[],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        ctx._buffer_prepared=prepared;return self._finish(ctx)

    def collect(self,ctx):
        start,end,profiles=normalize_policy(ctx.policy_values);token=uuid.uuid4().hex
        trials=[];diagnostics=[];reader=None;packet=None;sealed=False
        native_target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        try:
            reader=KubernetesLifecycleReader(native_target,ctx.env,ctx._buffer_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120));reader.snapshot()
            for slot in SLOTS:
                if slot in {'normal','slow','slow-close'} and not trials[0].get('normal_shape_verified'):continue
                rid='byul-lif04-'+token+'-'+slot;streaming=slot not in {'normal-before','normal-after'}
                row={'case_id':'lif04-'+slot,'request_id_sha256':sha256_hex(rid.encode()),'attempted':False,'started_at':None,
                    'finished_at':None,'outcome':None,'capture_complete':False,'normal_shape_verified':False,'policy_window_verified':False}
                trials.append(row)
                try:
                    now=utcnow()
                    if not start<=now<=end:raise ValueError
                    request={'id':'lif04-'+slot,'method':'POST','mode':'sse' if streaming else 'json','url':ctx.engine_url+'/v1/completions',
                        'headers':{'X-Request-Id':rid},'body':{'model':ctx.api_model,'prompt':'hello','max_tokens':8 if streaming else 1,'temperature':0,'stream':streaming}}
                    if profiles.get(slot) is not None:request['read_profile']=profiles[slot]
                    row.update(attempted=True,started_at=now.isoformat());response,=ctx.cprobe('kubernetes-probe',[request],10)
                    row.update({k:v for k,v in response.items() if k not in {'body','id'}})
                    if not streaming and response['outcome']==200 and response['capture_complete']:
                        body=strict_json(response['body'].encode(),limit=131072);choices=body.get('choices') if type(body) is dict else None
                        row['normal_shape_verified']=(type(body) is dict and body.get('model')==ctx.api_model and type(choices) is list
                            and 1<=len(choices)<=8 and all(type(c) is dict and type(c.get('text')) is str for c in choices))
                    elif streaming:row['normal_shape_verified']=bool(response['outcome']==200 and response['event_stream'] and (response['capture_complete'] and response['terminal_seen'] or response['intentional_client_close']))
                    if not row['normal_shape_verified']:diagnostics.append('STREAM_BUFFER_NORMAL_OR_BOUNDED_STREAM_UNAVAILABLE')
                except Exception:diagnostics.append('STREAM_BUFFER_PROBE_UNAVAILABLE')
                finally:
                    if row['attempted']:row['finished_at']=utcnow().isoformat()
                    row['policy_window_verified']=bool(row['started_at'] and start<=stamp(row['started_at'])<=stamp(row['finished_at'])<=end)
                    if row['attempted'] and not row['policy_window_verified']:diagnostics.append('STREAM_BUFFER_POLICY_WINDOW_UNVERIFIED')
        except Exception:diagnostics.append('STREAM_BUFFER_PREFLIGHT_UNAVAILABLE')
        finally:
            if reader is not None:
                try:sealed=reader.seal_session().get('sealed') is True
                except Exception:pass
                if not sealed:diagnostics.append('STREAM_BUFFER_SESSION_SEAL_UNAVAILABLE')
                try:
                    b=reader.binding['engine'];packet=capture_session(reader,native_target,container=b['container'],image=b['image'],
                        prepared=ctx._buffer_prepared,owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                    if not packet.get('recorded_files_covered'):diagnostics.append('STREAM_BUFFER_NATIVE_COVERAGE_UNAVAILABLE')
                except Exception:diagnostics.append('STREAM_BUFFER_CAPTURE_UNAVAILABLE')
        projection=metadata.project(packet,trials,ctx.policy_values);http=ctx.cprobe.report();read_report=reader.report() if reader else None
        summary={'profile':PROFILE,'trials':trials,'native':packet,'projection':projection,'transport':http,'reader':read_report,
            'sealed_selected_session':sealed,'diagnostic_codes':diagnostics,'fixture':{'profile':PROFILE},'whole_scope_complete':False,
            'server_memory_is_client_read_timing':False,'worker_gpu_release_verified':False}
        ctx._buffer_cleanup=list(http['cleanup_errors'])
        if read_report and read_report.get('parent_cleanup_unverified'):ctx._buffer_cleanup.append('STREAM_BUFFER_PARENT_CLEANUP_UNVERIFIED')
        ctx._buffer_gap=bool(diagnostics or http['collection_gap'] or ctx._buffer_cleanup)
        data={'buffer_http_trials':trials,'buffer_native_samples':projection['samples'],'buffer_cases':projection['cases'],
            'buffer_missing':projection['missing'],'buffer_conflict_candidates':projection['conflict_candidates'],
            'buffer_producer_authenticated':False,'buffer_inventory_complete':False,'buffer_release_verified':False,'buffer_scope_complete':False}
        return {k:{'value':v,'collected':True} for k,v in data.items()}, {'attempted':bool(trials and trials[0]['attempted']),
            'succeeded':None,'detail':'Bounded client/actual selected transport samples; whole serving buffer inventory, debug and release policy unverified.'},[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx);draft.data_origin=DataOrigin.EXAMPLE
        if ctx._buffer_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._buffer_cleanup);return draft
