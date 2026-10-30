"""REQ13 selected error-field capture via the approved public Kubernetes profile."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import math
from pathlib import Path
import re
import sys
from guard.models import DataOrigin,ExecStatus,canonical_json
from guard.automation_policy import string_list
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP
from .base import DraftArtifact,EvidenceDraft
from .http_common import incomplete_artifact_capture
from .response_content import OPTIONS,ResponseContentExecutor

PROFILE='req13-kubernetes-error-fields/1'
CAPTURE_PROFILE='kubernetes-owned-validation-error-http-v1'
_SCOPE_GAPS=['REQ13_SUCCESS_FIELDS_NOT_COLLECTED','REQ13_PRIVATE_AND_OPTIONS_NOT_COLLECTED','REQ13_NATIVE_HANDLER_ROUTING_UNVERIFIED']
SUPPORTED_FAIL_RULE={'obs':'response_violations','op':'nonempty','when':{'obs':'response_coverage_complete','op':'eq','value':False}}
_POLICY_KEYS=('allowed_response_fields','allowed_error_fields','forbidden_return_options','required_targets')


def _digest(value):return hashlib.sha256(canonical_json(value)).hexdigest()
def _sha(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None

def _adapter():
    from guard.adapters import kubernetes_response_error
    return kubernetes_response_error


def _date(value):
    try:
        parsed=datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError,TypeError):return None


def _rule_supported(check):
    params={p.key:p for p in check.policy_params}
    return (check.id=='REQ-13' and check.executor=='response_content'
        and all(key in params and params[key].type=='list[str]' and params[key].required for key in _POLICY_KEYS)
        and check.judge is not None and check.judge.plugin=='rule_table'
        and type(check.judge.params.get('fail_when_any')) is list
        and any(type(rule) is dict and rule==SUPPORTED_FAIL_RULE and rule.get('when',{}).get('value') is False for rule in check.judge.params['fail_when_any']))


def _normal_control(controls):
    """Actual tokenize failure is false; missing or unbound observations stay unknown."""
    states=[]
    for case in ('before','after'):
        value=controls.get(case,{})
        if (value.get('collected') is True and value.get('binding_verified') is True
                and value.get('capture_complete') is True and type(value.get('succeeded')) is bool):states.append(value['succeeded'])
        else:states.append(None)
    return False if False in states else True if states==[True,True] else None


class KubernetesResponseErrorExecutor(ResponseContentExecutor):
    name='response_content'
    version='0.1.0'

    def __init__(self,cfg,*,environ=None,expected_target_sha256=None,_transport_factory=None):
        super().__init__(cfg,environ=environ)
        if expected_target_sha256 is not None and not _sha(expected_target_sha256):raise ValueError('REQ13_TARGET_HASH_INVALID')
        self._expected_target_sha256=expected_target_sha256
        self._transport_factory=_transport_factory

    def validate_policy(self,check,values):
        """An explicit empty error allowlist forbids every collected error field."""
        self.validate_configuration(check)
        for key in _POLICY_KEYS:
            string_list(values,key,empty=key in {'allowed_error_fields','forbidden_return_options'})
        if set(values['forbidden_return_options'])-OPTIONS.keys():
            raise ValueError('unsupported forbidden return option; cannot silently omit it')
        if set(values['required_targets'])-{'engine','gateway'}:
            raise ValueError('required_targets supports engine and gateway only')

    def validate_configuration(self,check):
        allowed={'target','kubectl_binary','request_timeout','max_duration_seconds','allow_mutating_routes'}
        if (type(self.cfg) is not dict or set(self.cfg)-allowed or self.cfg.get('allow_mutating_routes',False) is not False
                or type(self.cfg.get('target')) is not str or not self.cfg['target']
                or type(self.cfg.get('kubectl_binary','kubectl')) is not str or not self.cfg.get('kubectl_binary','kubectl')):
            raise ValueError('REQ13_KUBERNETES_CONFIGURATION_INVALID')
        for key,default,maximum in (('max_duration_seconds',60,300),('request_timeout',10,120)):
            value=self.cfg.get(key,default)
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=maximum:raise ValueError('REQ13_KUBERNETES_BUDGET_INVALID')

    def run(self,check,env,policy_values,adapter,workdir):
        if sys.flags.optimize:raise ValueError('REQ13_NORMAL_PYTHON_REQUIRED')
        self.validate_policy(check,policy_values)
        if not env.hash_ok() or env.engine.name!='vllm':raise ValueError('REQ13_ENVIRONMENT_INVALID')
        if self._transport_factory is not None and env.data_origin!=DataOrigin.EXAMPLE:raise ValueError('REQ13_INJECTION_REQUIRES_EXAMPLE')
        record=None;target=None;api_model=None;diagnostics=[];capture_calls=0
        began=datetime.now(timezone.utc)
        if not _rule_supported(check):diagnostics.append('REQ13_RULE_UNSUPPORTED')
        elif 'engine' not in policy_values['required_targets']:diagnostics.append('REQ13_ENGINE_NOT_SELECTED')
        elif env.engine.version!='0.31.0':diagnostics.append('REQ13_UNSUPPORTED_ENGINE_VERSION')
        else:
            file=Path(self.cfg['target'])
            if file.is_symlink() or not file.is_file():raise ValueError('REQ13_TARGET_FILE_INVALID')
            target=strict_json(file.read_bytes())
            if type(target) is not dict or target.get('environment_ref')!=env.ref().model_dump(mode='json'):raise ValueError('REQ13_ENV_TARGET_MISMATCH')
            if self._expected_target_sha256 is not None and _digest(target)!=self._expected_target_sha256:raise ValueError('REQ13_TARGET_HASH_MISMATCH')
            duration=self.cfg.get('max_duration_seconds',60);timeout=self.cfg.get('request_timeout',10);binary=self.cfg.get('kubectl_binary','kubectl')
            module=_adapter()
            try:
                if self._transport_factory is None:module.verify_a_capability()
                factory=self._transport_factory or KubernetesHTTP
                # This constructor validates ownership/image/model mapping without I/O.
                transport=factory(target,env,duration=duration,binary=binary,profile=CAPTURE_PROFILE)
                api_model=transport.api_model
                if type(api_model) is not str or not api_model:raise ValueError('REQ13_API_MODEL_UNAVAILABLE')
                capture_calls+=1
                kwargs={'duration':duration,'timeout':timeout,'binary':binary,'allowed_error_fields':policy_values['allowed_error_fields'],'forbidden_contexts':None}
                if self._transport_factory is not None:kwargs['_transport_factory']=self._transport_factory
                actual=module.capture_validation_error(env,target,**kwargs)
                record=module.project_error_capture(actual,env,target,api_model,policy_values['allowed_error_fields'])
                if record is None:diagnostics.append('REQ13_OBSERVATION_UNAVAILABLE')
            except Exception:diagnostics.append('REQ13_CAPTURE_UNAVAILABLE')
        observations={o.key:{'value':None,'collected':False} for o in check.required_observations}
        observations['response_coverage_complete']={'value':False,'collected':True}
        observations['response_coverage_gaps']={'value':[*_SCOPE_GAPS,*diagnostics],'collected':True}
        error=record.get('error_observation') if record else None
        if error is not None:
            observations['response_cases_tested']={'value':['engine:safe_error'],'collected':True}
            if error.get('field_path_sha256'):
                observations['response_violations']={'value':error['violations'],'collected':True}
        # Original inference observations remain null; tokenize has a separate control scope.
        controls=record.get('normal_controls',{}) if record else {}
        attempted=any(v.get('attempted') is True for v in controls.values())
        cleanup=record.get('cleanup',{}) if record else {}
        cleanup_errors=['REQ13_OWNED_PROBE_CLEANUP_UNVERIFIED'] if record and (cleanup.get('collected') is not True or cleanup.get('complete') is not True or cleanup.get('collection_gap') is True) else []
        draft=EvidenceDraft(status=ExecStatus.ABORTED,
            data_origin=DataOrigin.EXAMPLE if env.data_origin==DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control={'attempted':attempted,'succeeded':_normal_control(controls) if attempted else None},
            observations=observations,challenges=[{'step':step,'attempted':i==2 and error is not None} for i,step in enumerate(check.challenge_steps)],
            config={'profile':PROFILE,'capture_calls':capture_calls,'full_req13_coverage':False,'normal_control_scope':'tokenize_api_shape_only',
                'handler_consumption_verified':False,'native_handler_consumption_verified':False,'model_inference_verified':False,
                'source_profile':'vllm-031-validation-error','target_sha256':_digest(target) if target else None,
                'api_model_sha256':hashlib.sha256(api_model.encode()).hexdigest() if api_model else None,
                'allowed_error_fields_sha256':_digest(policy_values['allowed_error_fields']),
                'collection_id':record.get('collection_id') if record else None,'diagnostic_codes':diagnostics},
            started_at=_date(record.get('capture_started_at')) if record else began,
            finished_at=_date(record.get('capture_finished_at')) if record else datetime.now(timezone.utc),
            abort_reason='REQ13_RULE_UNSUPPORTED' if 'REQ13_RULE_UNSUPPORTED' in diagnostics else 'REQ13_ERROR_ONLY_PARTIAL_SCOPE',cleanup_errors=cleanup_errors)
        if record is not None:
            try:
                path=Path(workdir)/'req13-error.json'
                with path.open('xb') as stream:stream.write(canonical_json({**record,'producer_diagnostics':diagnostics}))
                draft.artifacts=[DraftArtifact(name=path.name,path=path,collector=PROFILE)]
            except Exception:draft=incomplete_artifact_capture(draft)
        return draft
