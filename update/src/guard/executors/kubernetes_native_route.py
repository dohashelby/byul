"""REQ02 native8 auth/registered-handler facts through the existing CLI/session."""
from guard.models import canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import ROUTE_AUTHENTICATION_PROFILE
from .kubernetes_request_termination import KubernetesRequestTerminationExecutor,_time
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session
from . import native_route_contract as contract
import re

PROFILE=contract.PROFILE;POLICY_TYPES=contract.POLICY_TYPES;OBSERVATIONS=contract.OBSERVATIONS

class KubernetesNativeRouteExecutor(KubernetesRequestTerminationExecutor):
    name='native_route';version='0.1.0';check_id='REQ-02';collection_profile=PROFILE;policy_prefix='route_native'
    policy_normalizer=staticmethod(contract.normalize_policy);transport_profile=ROUTE_AUTHENTICATION_PROFILE
    supported_native_profiles=frozenset({'vllm-native-lifecycle-metadata/8'})
    configuration_keys=KubernetesRequestTerminationExecutor.configuration_keys|{'probe_key_env'}

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        from guard.collectors.lifecycle import load_prepared
        try:
            if env.engine.name!='vllm' or env.engine.version!='0.31.0':raise ValueError
            self._route_probe_key=None
            if 'probe_key_env' in self.cfg:
                name=self.cfg['probe_key_env']
                if type(name) is not str or re.fullmatch('[A-Z_][A-Z0-9_]{0,63}',name) is None:raise ValueError
                key=self._environ.get(name)
                if type(key) is not str or re.fullmatch('[!-~]{1,512}',key) is None:raise ValueError
                self._route_probe_key=key
            prepared=load_prepared(self.cfg['prepared'])
            if sha256_hex(canonical_json(prepared))!=policy_values['route_native_prepared_sha256']:raise ValueError
        except Exception:raise ValueError('NATIVE_ROUTE_PREPARED_UNAVAILABLE') from None
        return super().run(check,env,policy_values,adapter,workdir)

    def collect(self,ctx):
        import uuid
        start,end=self.policy_normalizer(ctx.policy_values);token=uuid.uuid4().hex;reader=None;packet=None;sealed=False
        if self._route_probe_key is not None:ctx.secrets.append(self._route_probe_key)
        summary={'profile':PROFILE,'trials':[],'native':None,'diagnostic_codes':[],'fixture':{'profile':PROFILE}}
        target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        try:
            reader=KubernetesLifecycleReader(target,ctx.env,ctx._termination_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120));reader.snapshot()
            for slot in ('models-before','completion','models-after'):
                completion=slot=='completion';rid='byul-req02-'+token+'-'+slot
                body={'model':ctx.api_model,'prompt':'hello','max_tokens':1,'temperature':0,'stream':False} if completion else None
                row={'case_id':'req02-'+slot,'request_id_sha256':sha256_hex(rid.encode()),'request_body_sha256':sha256_hex(canonical_json(body)),
                    'method_sha256':sha256_hex(('POST' if completion else 'GET').encode()),
                    'path_sha256':sha256_hex(('/v1/completions' if completion else '/v1/models').encode()),
                    'attempted':False,'response_binding_verified':False,'capture_complete':False,'outcome':None,'shape_observed':None,'started_at':None,'finished_at':None,'policy_window_verified':False}
                summary['trials'].append(row)
                try:
                    if not start<=utcnow()<=end:raise ValueError
                    headers={'X-Request-Id':rid}
                    if self._route_probe_key is not None:headers['Authorization']='Bearer '+self._route_probe_key
                    request={'id':row['case_id'],'method':'POST' if completion else 'GET','mode':'json',
                        'url':ctx.engine_url+('/v1/completions' if completion else '/v1/models'),'headers':headers,'body':body}
                    row.update(attempted=True,started_at=utcnow().isoformat());reply,=ctx.cprobe('kubernetes-probe',[request],ctx.timeout)
                    row['response_binding_verified']=True
                    text=reply.get('body');row['outcome']=reply.get('outcome')
                    row['capture_complete']=(reply.get('capture_complete') is True and reply.get('eof') is True and reply.get('utf8_valid') is True
                        and reply.get('body_truncated') is False and type(text) is str and type(reply.get('body_bytes')) is int
                        and reply['body_bytes']==len(text.encode()) and reply.get('body_sha256')==sha256_hex(text.encode()))
                    if row['capture_complete'] and type(reply.get('outcome')) is int and reply['outcome']==200:
                        value=strict_json(text.encode(),limit=128*1024)
                        row['shape_observed']=bool(type(value) is dict and (value.get('model')==ctx.api_model and type(value.get('choices')) is list if completion else type(value.get('data')) is list))
                    if not row['capture_complete']:summary['diagnostic_codes'].append('NATIVE_ROUTE_HTTP_CAPTURE_UNAVAILABLE')
                except Exception:summary['diagnostic_codes'].append('NATIVE_ROUTE_HTTP_UNAVAILABLE')
                finally:
                    if row['attempted']:row['finished_at']=utcnow().isoformat()
                    row['policy_window_verified']=bool(row['started_at'] and start<=_time(row['started_at'])<=_time(row['finished_at'])<=end)
                    if row['attempted'] and not row['policy_window_verified']:summary['diagnostic_codes'].append('NATIVE_ROUTE_POLICY_WINDOW_UNVERIFIED')
        except Exception:summary['diagnostic_codes'].append('NATIVE_ROUTE_PREFLIGHT_UNAVAILABLE')
        finally:
            if reader is not None:
                try:sealed=reader.seal_session()['sealed']
                except Exception:pass
                if not sealed:summary['diagnostic_codes'].append('NATIVE_ROUTE_SEAL_UNVERIFIED')
                try:
                    role=reader.binding['engine'];packet=capture_session(reader,target,container=role['container'],image=role['image'],
                        prepared=ctx._termination_prepared,owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                except Exception:summary['diagnostic_codes'].append('NATIVE_ROUTE_CAPTURE_UNAVAILABLE')
                try:summary['native']={'sealed':sealed,'transport':reader.report()}
                except Exception:summary['diagnostic_codes'].append('NATIVE_ROUTE_REPORT_UNAVAILABLE')
        projection=contract.project(packet,summary['trials']);summary['projection']=projection;summary['http_transport']=ctx.cprobe.report()
        summary['diagnostic_codes']=list(dict.fromkeys(summary['diagnostic_codes']))
        ctx._termination_gap=bool(summary['diagnostic_codes'] or summary['http_transport']['collection_gap']
            or packet is None or not packet['recorded_files_covered'] or 'NATIVE_ROUTE_INDEX_INCOMPLETE' in projection['missing'])
        ctx._termination_cleanup=list(summary['http_transport']['cleanup_errors'])
        if reader is not None and summary['native'] is not None and summary['native']['transport'].get('parent_cleanup_unverified'):
            ctx._termination_cleanup.append('NATIVE_ROUTE_PARENT_CLEANUP_UNVERIFIED');ctx._termination_gap=True
        values={'native_route_trials':summary['trials'],'native_route_events':projection['events'],'native_route_associations':projection['associations'],
            'native_route_missing':projection['missing'],'native_route_authorization_verified':False,'native_route_scope_complete':False}
        return {k:{'value':v,'collected':True} for k,v in values.items()}, {'attempted':any(r['attempted'] for r in summary['trials']),
            'succeeded':None,'detail':'Selected HTTP shape/native callback association only; authorized principal and Gateway rewrite remain unverified.'},[],summary
