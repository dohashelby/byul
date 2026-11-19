"""LIF04 existing CLI, owned read controls and opt-in actual transport metadata."""
from pathlib import Path
import uuid
from guard.models import DataOrigin,ExecStatus,ORIGIN_STRENGTH,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.engine_http import KubernetesBindingError
from guard.runtime.kubernetes_stream_buffer_http import KubernetesStreamBufferHTTP,HTTP_PROFILE,SLOTS
from guard.adapters import vllm_stream_buffer_sensor as sensor,vllm_stream_buffer_metadata as metadata
from guard.adapters import vllm_release_observation_sensor as release_observation
from guard.adapters import vllm_lifecycle_catalog as configured
from guard.adapters import vllm_baseline_stream_composition as baseline_stream
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session
from .stream_buffer_contract import PROFILE,normalize_policy,stamp
from . import normal_stream_buffer


BUDGET_PROFILE='kubernetes-stream-buffer-budget-observation-v1'
CHECK_PROFILES={'LIF-04':PROFILE,'LIF-03':BUDGET_PROFILE}


from .lifecycle_common_input_consumption import LifecycleCommonInputConsumer


class KubernetesStreamBufferExecutor(LifecycleCommonInputConsumer, HttpCheckExecutor):
    name='stream_buffer';version='0.1.0';requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256
        self.normal_only=self.cfg.get('normal_stream_only') is True
        if self.normal_only:self.version='0.1.0+normal-stream'

    def validate_policy(self,check,values):
        if CHECK_PROFILES.get(check.id)!=getattr(check,'collection_profile',None) or check.id not in CHECK_PROFILES:raise ValueError('STREAM_BUFFER_PROFILE_UNSUPPORTED')
        if 'normal_stream_only' in self.cfg and (not self.normal_only or check.id!='LIF-04'
                or set(self.cfg)-normal_stream_buffer.NORMAL_CONFIG_KEYS or self.b_common_inputs is not None):
            raise ValueError('STREAM_BUFFER_NORMAL_CONFIGURATION_INVALID')
        normalize_policy(values,normal_only=self.normal_only)

    def _validate_target(self, target, env, values, *, expected_target_sha256=None):
        digest = sha256_hex(canonical_json(target))
        pins = [pin for pin in (self.expected_target, expected_target_sha256) if pin is not None]
        if (digest != values['buffer_target_sha256'] or env.ref().sha256 != values['buffer_environment_sha256']
                or self.normal_only and not pins or any(digest != pin for pin in pins)):
            raise ValueError

    def _transport(self, target, env, profiles):
        return KubernetesStreamBufferHTTP(target, env, read_profiles=profiles, runner=self.runner,
            duration=self.cfg.get('max_duration_seconds', 120), binary=self.cfg.get('kubectl_binary', 'kubectl'),
            normal_only=self.normal_only)

    def prepared_normal_stream(self, check, env, values, *, expected_target_sha256=None):
        """Validate only the saved, bounded LIF-04 normal-stream declaration."""
        from guard.adapters.vllm_file_read_sensor import dependency
        from .run_binding import stages_for

        if not self.normal_only or check.id != 'LIF-04' or check.executor != self.name or stages_for(check) != (1,):
            raise ValueError('STREAM_BUFFER_NORMAL_CONFIGURATION_INVALID')
        self.validate_policy(check, values)
        try:
            if any(type(value) is not str or not value for value in (env.model.id, env.model.revision)):
                raise ValueError
            target = strict_json(dependency(self.cfg['target'], None), limit=65536)
            if type(target) is not dict:
                raise ValueError
            self._validate_target(target, env, values, expected_target_sha256=expected_target_sha256)
            _, _, profiles = normalize_policy(values, normal_only=True)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            raise ValueError('STREAM_BUFFER_CONFIGURATION_INVALID') from None
        try:
            self._transport(target, env, profiles)
        except KubernetesBindingError as error:
            raise ValueError(str(error)) from None
        except OverflowError:
            raise ValueError('STREAM_BUFFER_CONFIGURATION_INVALID') from None

    def run(self,check,env,policy_values,adapter,workdir):
        from guard.collectors.lifecycle import load_prepared
        self.validate_policy(check,policy_values)
        try:
            if (adapter is None or not self.normal_only and set(self.cfg)-{'target','prepared','kubectl_binary','max_duration_seconds'}):raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id,env.model.revision)):raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            start,end,profiles=normalize_policy(policy_values,normal_only=self.normal_only)
            self._validate_target(target, env, policy_values)
            if not start<=utcnow()<=end:raise ValueError
            prepared=None if self.normal_only else load_prepared(self.cfg['prepared'])
            if not self.normal_only and (prepared['profile'] not in {sensor.PROFILE,release_observation.STREAM_PROFILE,baseline_stream.PROFILE}
                    or sha256_hex(canonical_json(prepared))!=policy_values['buffer_prepared_sha256']):raise ValueError
        except (ValueError,KeyError,TypeError,RuntimeError,OSError):raise ValueError('STREAM_BUFFER_CONFIGURATION_INVALID') from None
        transport = self._transport(target, env, profiles)
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys={'none':None},secrets=[],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        ctx._buffer_prepared=prepared
        ctx._stream_window=(start,end)
        return self._finish(ctx)

    def collect(self,ctx):
        if self.normal_only:
            return normal_stream_buffer.collect(ctx,*ctx._stream_window)
        from guard.collectors.lifecycle import request_header_fingerprint
        start,end,profiles=normalize_policy(ctx.policy_values);token=uuid.uuid4().hex
        trials=[];diagnostics=[];reader=None;packet=None;sealed=False
        budget_profile=ctx.check.collection_profile==BUDGET_PROFILE;rss_samples=[]
        native_target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        try:
            reader_type=KubernetesLifecycleReader
            if budget_profile:
                from .kubernetes_process_rss import KubernetesProcessRSSReader
                reader_type=KubernetesProcessRSSReader
            reader=reader_type(native_target,ctx.env,ctx._buffer_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120));reader.snapshot()
            if budget_profile:self._collect_rss(reader,native_target,ctx._buffer_prepared,'before',rss_samples,diagnostics)
            for slot in SLOTS:
                if slot in {'normal','slow','slow-close'} and not trials[0].get('normal_shape_verified'):continue
                rid='byul-lif04-'+token+'-'+slot;streaming=slot not in {'normal-before','normal-after'}
                private=(ctx._buffer_prepared['profile']==release_observation.STREAM_PROFILE
                    or configured.stream_observation(ctx._buffer_prepared['profile']))
                try:
                    identity=(request_header_fingerprint(self.cfg['prepared'],ctx._buffer_prepared,rid)
                        if private else sha256_hex(rid.encode()))
                except ValueError:
                    identity=None
                    diagnostics.append('STREAM_BUFFER_REQUEST_KEY_UNAVAILABLE')
                row={'case_id':'lif04-'+slot,'request_id_sha256':identity,'attempted':False,'started_at':None,
                    'finished_at':None,'outcome':None,'capture_complete':False,'normal_shape_verified':False,'policy_window_verified':False}
                trials.append(row)
                try:
                    now=utcnow()
                    if identity is None or not start<=now<=end:raise ValueError
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
                    if budget_profile and slot in {'normal','slow','slow-close'}:
                        self._collect_rss(reader,native_target,ctx._buffer_prepared,slot,rss_samples,diagnostics)
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
                    if budget_profile:rss_samples.extend(reader.sample_packet(packet,'after'))
                except Exception:diagnostics.append('STREAM_BUFFER_CAPTURE_UNAVAILABLE')
        sources=baseline_stream.SOURCE_ROWS if configured.stream_observation(ctx._buffer_prepared['profile']) else None
        runtime_codes=baseline_stream.RUNTIME_CODES if sources else None
        projection=metadata.project(packet,trials,ctx.policy_values,sources=sources,runtime_codes=runtime_codes,
            require_live_context=bool(sources))
        try:http=ctx.cprobe.report()
        except Exception:
            diagnostics.append('STREAM_BUFFER_HTTP_REPORT_UNAVAILABLE')
            http={'collection_gap':True,'cleanup_errors':['STREAM_BUFFER_HTTP_REPORT_UNAVAILABLE']}
        read_report=None
        if reader is not None:
            try:read_report=reader.report()
            except Exception:
                diagnostics.append('STREAM_BUFFER_READER_REPORT_UNAVAILABLE')
                read_report={'collection_gap':True,'parent_cleanup_unverified':True}
        summary={'profile':ctx.check.collection_profile,'trials':trials,'native':packet,'projection':projection,'transport':http,'reader':read_report,
            'sealed_selected_session':sealed,'diagnostic_codes':diagnostics,'fixture':{'profile':PROFILE},'whole_scope_complete':False,
            'server_memory_is_client_read_timing':False,'worker_gpu_release_verified':False}
        ctx._buffer_cleanup=list(http['cleanup_errors'])
        if read_report and read_report.get('parent_cleanup_unverified'):ctx._buffer_cleanup.append('STREAM_BUFFER_PARENT_CLEANUP_UNVERIFIED')
        if budget_profile:
            if read_report and read_report.get('collection_gap'):diagnostics.append('BUDGET_RSS_COLLECTION_GAP')
            summary['budget_process_rss_samples']=rss_samples
            summary['budget_unimplemented_scopes']=['concurrency','queue','KV','media_cumulative_budget','fairness']
            if any(not row['process_context_verified'] or not row['owner_binding_verified'] for row in rss_samples):
                diagnostics.append('BUDGET_RSS_SAMPLING_INCOMPLETE')
        ctx._buffer_gap=bool(diagnostics or http['collection_gap'] or ctx._buffer_cleanup)
        ctx._buffer_diagnostic_codes=tuple(diagnostics)
        data={'buffer_http_trials':trials,'buffer_native_samples':projection['samples'],'buffer_cases':projection['cases'],
            'buffer_missing':projection['missing'],'buffer_conflict_candidates':projection['conflict_candidates'],
            'buffer_producer_authenticated':False,'buffer_inventory_complete':False,'buffer_release_verified':False,'buffer_scope_complete':False}
        observations={k:{'value':v,'collected':True} for k,v in data.items()}
        if budget_profile:
            from .kubernetes_process_rss import project_samples
            observations.update(project_samples(rss_samples,ctx.policy_values['buffer_max_process_rss_bytes']))
        return observations, {'attempted':bool(trials and trials[0]['attempted']),
            'succeeded':None,'detail':'Bounded client/actual selected transport samples; whole serving buffer inventory, debug and release policy unverified.'},[],summary

    @staticmethod
    def _collect_rss(reader,target,prepared,stage,samples,diagnostics):
        try:
            engine=reader.binding['engine']
            packet=capture_session(reader,target,container=engine['container'],image=engine['image'],prepared=prepared,
                owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
            samples.extend(reader.sample_packet(packet,stage))
        except Exception:diagnostics.append('BUDGET_RSS_CAPTURE_UNAVAILABLE')

    def _finish(self,ctx):
        draft=super()._finish(ctx)
        origin=DataOrigin(ctx.cprobe.base.process_observation_origin) if self.normal_only else DataOrigin.EXAMPLE
        if self.normal_only and (ctx._buffer_gap or draft.status!=ExecStatus.COMPLETED):
            origin=min(origin,DataOrigin.DECLARED,key=ORIGIN_STRENGTH.get)
        draft.data_origin=min(draft.data_origin,origin,key=ORIGIN_STRENGTH.get)
        if ctx._buffer_gap:
            draft.status=ExecStatus.ABORTED
            if draft.abort_reason is None:
                codes=sorted(set(tuple(getattr(ctx,'_buffer_diagnostic_codes',()))+tuple(ctx._buffer_cleanup)))
                draft.abort_reason='; '.join(codes) or 'STREAM_BUFFER_COLLECTION_INCOMPLETE'
        draft.cleanup_errors.extend(ctx._buffer_cleanup);return draft
