"""EXAMPLE-only parent contracts. No cluster, HTTP dispatch or source CPU run."""
from copy import deepcopy
from types import SimpleNamespace
import json
from pathlib import Path
import pytest
from guard.adapters.vllm import VllmAdapter
from guard.executors import kubernetes_response_error as parent
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin,CheckDef,ExecStatus
from guard.models.objects import Challenge
from guard.core.judge import judge_evidence,pass_gate
from tests.builders import baseline_pack,policy,evidence
from tests.test_kubernetes_request_binding import env,target
from tests.test_response_kubernetes import error_record,FIELDS,PRIVATE
from guard.adapters.kubernetes_response_error import project_error_capture as _project

VALUES={'allowed_response_fields':['id'],'allowed_error_fields':FIELDS,'forbidden_return_options':[],'required_targets':['engine']}

@pytest.fixture
def context(tmp_path,monkeypatch):
    environment=env();destination=target(environment);pack=baseline_pack()
    pol=policy(pack.ref(),scope={'include':['REQ-13']},values={'REQ-13':deepcopy(VALUES)})
    raw=pack.checks['REQ-13'].model_dump(mode='json')
    raw['judge']['params']['fail_when_any'].append(deepcopy(parent.SUPPORTED_FAIL_RULE))
    check=CheckDef.model_validate(raw)
    path=tmp_path/'target.json';path.write_text(json.dumps(destination));work=tmp_path/'work';work.mkdir()
    calls=[];captured=error_record((environment,pol,pack,destination),violation=True)
    def capture(e,t,**kwargs):calls.append((e,t,kwargs));return deepcopy(captured)
    def factory(t,e,**kwargs):
        assert kwargs['profile']==parent.CAPTURE_PROFILE
        assert e.model.id!='fixture-model'
        return SimpleNamespace(api_model='fixture-model')
    def denied():raise ValueError(PRIVATE)
    module=SimpleNamespace(verify_a_capability=denied,capture_validation_error=capture,project_error_capture=_project)
    monkeypatch.setattr(parent,'_adapter',lambda:module)
    cfg={'target':str(path)}
    executor=parent.KubernetesResponseErrorExecutor(cfg,expected_target_sha256=parent._digest(destination),_transport_factory=factory)
    return SimpleNamespace(environment=environment,target=destination,pack=pack,policy=pol,check=check,path=path,work=work,calls=calls,raw=captured,module=module,cfg=cfg,executor=executor,factory=factory)


def run(c,**changes):return c.executor.run(changes.get('check',c.check),changes.get('env',c.environment),changes.get('values',deepcopy(VALUES)),VllmAdapter(),c.work)

def verdict(c,draft):
    evd=evidence(check_id='REQ-13',status=draft.status,normal=draft.normal_control.model_dump(),observations={k:v.model_dump() for k,v in draft.observations.items()})
    return RuleTableJudge().judge(c.check,evd,c.check.judge.params,VALUES)


def test_exact_public_alias_policy_and_optional_violation(context):
    c=context;draft=run(c)
    assert len(c.calls)==1 and c.calls[0][2]['allowed_error_fields']==FIELDS and c.calls[0][2]['forbidden_contexts'] is None
    assert draft.status.value=='ABORTED' and draft.data_origin==DataOrigin.EXAMPLE
    assert draft.normal_control.succeeded is True
    assert draft.config['api_model_sha256']==parent.hashlib.sha256(b'fixture-model').hexdigest()
    assert 'response_violations' not in {o.key for o in c.check.required_observations}
    assert draft.observations['response_violations'].collected and draft.observations['response_violations'].value
    assert verdict(c,draft).verdicts['security'].value=='FAIL'
    assert verdict(c,draft).verdicts['functional'].value=='UNKNOWN'
    for key in ('error_fields_seen','response_fields_seen','response_options_returned','private_markers_returned','functional_probe_results'):
        assert draft.observations[key].value is None and not draft.observations[key].collected
    raw=draft.artifacts[0].path.read_text();assert PRIVATE not in raw
    assert c.environment.model.id not in json.dumps(draft.config)


@pytest.mark.parametrize('mutation',['after_unknown','after_fail','cleanup','artifact','all_unknown'])
def test_independent_fail_survives_late_missing_and_artifact_errors(context,mutation):
    c=context
    if mutation=='after_unknown':c.raw['normal_controls']['after'].update(collected=False,succeeded=None)
    elif mutation=='after_fail':c.raw['normal_controls']['after']['succeeded']=False
    elif mutation=='all_unknown':
        for v in c.raw['normal_controls'].values():v.update(collected=False,succeeded=None)
    elif mutation=='cleanup':c.raw['cleanup'].update(complete=False,collection_gap=True)
    else:(c.work/'req13-error.json').mkdir()
    draft=run(c)
    assert verdict(c,draft).verdicts['security'].value=='FAIL'
    evd=evidence(check_id='REQ-13',status=draft.status,normal=draft.normal_control.model_dump(),observations={k:v.model_dump() for k,v in draft.observations.items()})
    assert judge_evidence(c.check,evd,c.policy,c.environment.created_at).verdicts.security.value=='FAIL'
    if mutation in ('after_unknown','all_unknown'):assert draft.normal_control.succeeded is None
    elif mutation=='after_fail':assert draft.normal_control.succeeded is False
    else:assert draft.normal_control.succeeded is True
    if mutation=='cleanup':assert draft.cleanup_errors==['REQ13_OWNED_PROBE_CLEANUP_UNVERIFIED']
    if mutation=='artifact':assert draft.artifacts==[]


@pytest.mark.parametrize('mutation',['original','unconditional','wrong_when','wrong_type','wrong_policy'])
def test_rule_unsupported_stops_before_collection(context,mutation):
    c=context;raw=c.check.model_dump(mode='json')
    if mutation=='original':raw=c.pack.checks['REQ-13'].model_dump(mode='json')
    elif mutation=='unconditional':raw['judge']['params']['fail_when_any'][-1]={'obs':'response_violations','op':'nonempty'}
    elif mutation=='wrong_when':raw['judge']['params']['fail_when_any'][-1]['when']['value']=True
    elif mutation=='wrong_type':raw['judge']['params']['fail_when_any'][-1]['when']['value']=0
    else:raw['policy_params'][0]['required']=False
    draft=run(c,check=CheckDef.model_validate(raw))
    assert c.calls==[] and draft.abort_reason=='REQ13_RULE_UNSUPPORTED'
    assert draft.config['capture_calls']==0 and draft.normal_control.succeeded is None
    assert 'REQ13_RULE_UNSUPPORTED' in draft.observations['response_coverage_gaps'].value


@pytest.mark.parametrize('missing',list(VALUES))
def test_original_policy_params_still_required(context,missing):
    values=deepcopy(VALUES);values.pop(missing)
    with pytest.raises((ValueError,KeyError)):run(context,values=values)
    assert context.calls==[]


def test_unsupported_a_source_is_zero_requests(context):
    c=context;c.executor=parent.KubernetesResponseErrorExecutor(c.cfg)
    draft=run(c)
    assert c.calls==[] and draft.config['capture_calls']==0 and draft.normal_control.succeeded is None
    assert draft.observations.get('response_violations') is None
    assert 'REQ13_CAPTURE_UNAVAILABLE' in draft.config['diagnostic_codes']


@pytest.mark.parametrize('version',['0.32.0','source-db9527a',''])
def test_selected_source_version_only(context,version):
    c=context;e=c.environment.model_copy(update={'engine':c.environment.engine.model_copy(update={'version':version})}).seal()
    draft=run(c,env=e)
    assert c.calls==[] and draft.config['capture_calls']==0
    assert 'REQ13_UNSUPPORTED_ENGINE_VERSION' in draft.config['diagnostic_codes']


def test_target_hash_and_environment_ref_mismatch_before_call(context):
    c=context;c.target['run_id']='EXAMPLE-other';c.path.write_text(json.dumps(c.target))
    with pytest.raises(ValueError,match='REQ13_TARGET_HASH_MISMATCH'):run(c)
    assert c.calls==[]


def test_declared_private_injection_is_rejected(context):
    c=context;e=c.environment.model_copy(update={'data_origin':DataOrigin.DECLARED}).seal()
    with pytest.raises(ValueError,match='REQ13_INJECTION_REQUIRES_EXAMPLE'):run(c,env=e)
    assert c.calls==[]


def test_required_engine_scope_not_silently_widened(context):
    values=deepcopy(VALUES);values['required_targets']=['gateway'];draft=run(context,values=values)
    assert context.calls==[] and draft.config['capture_calls']==0
    assert 'REQ13_ENGINE_NOT_SELECTED' in draft.config['diagnostic_codes']


def test_projection_policy_or_alias_mismatch_cannot_supply_violation(context):
    c=context;c.raw['allowed_error_fields_sha256']='f'*64
    draft=run(c)
    assert 'response_violations' not in draft.observations
    assert draft.normal_control.succeeded is None


def test_original_whole_pass_has_no_new_required_observation(context):
    c=context;original=c.pack.checks['REQ-13']
    assert c.check.required_observations==original.required_observations
    values={o.key:{'value':[],'collected':True} for o in c.check.required_observations}
    values.update(response_coverage_complete={'value':True,'collected':True},
        response_fields_seen={'value':['id'],'collected':True},
        error_fields_seen={'value':['error.message'],'collected':True},
        response_targets_tested={'value':['engine'],'collected':True},
        functional_probes_planned={'value':['POST /v1/chat/completions'],'collected':True},
        functional_probe_results={'value':{'POST /v1/chat/completions':'pass'},'collected':True})
    evd=evidence(check_id='REQ-13',observations=values).model_copy(update={'challenges':[Challenge(step=s,attempted=True) for s in c.check.challenge_steps]}).seal()
    assert 'response_violations' not in evd.observations and pass_gate(c.check,evd)==[]
    old=judge_evidence(original,evd,c.policy,c.environment.created_at)
    new=judge_evidence(c.check,evd,c.policy,c.environment.created_at)
    assert old.verdicts==new.verdicts and new.verdicts.security.value=='PASS'
    assert 'response_violations' not in new.missing_observations


@pytest.mark.parametrize('missing_extra',[True,False])
def test_partial_without_violation_is_unknown(context,missing_extra):
    c=context
    if missing_extra:c.raw['error_observation']=None
    else:
        c.raw=error_record((c.environment,c.policy,c.pack,c.target),violation=False)
        # capture closure reads the current context attribute via a fresh module binding.
        c.module.capture_validation_error=lambda *args,**kwargs:deepcopy(c.raw)
    draft=run(c)
    assert draft.observations['response_coverage_complete'].value is False
    assert verdict(c,draft).verdicts['security'].value=='UNKNOWN'
