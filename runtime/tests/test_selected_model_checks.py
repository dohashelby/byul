import json
import hashlib
import copy

import pytest

from tools import selected_model_checks as checks
from tools import selected_runtime_check as tool
from test_selected_request_checks import owned_stack


def test_actual_owned_policy_transition_and_same_actor(tmp_path,monkeypatch):
    before_dir=tmp_path/'before';after_dir=tmp_path/'after';before_dir.mkdir();after_dir.mkdir()
    before_path=tmp_path/'before-report.json';after_path=tmp_path/'after-report.json'
    backend=tmp_path/'backend.json';backend.write_text(json.dumps({'model_map':{'replacement-model':'replacement-model'}}))
    for phase,directory,allowed,output in [('before',before_dir,'test-model',before_path),('after',after_dir,'replacement-model',after_path)]:
        with owned_stack(directory,allowed_model=allowed) as (url,inference,viewer):
            monkeypatch.setenv('BYUL_TEST_INFERENCE',inference);monkeypatch.setenv('BYUL_TEST_VIEWER',viewer)
            args=['lifecycle','--gateway-url',url,'--model','test-model','--replacement-model','replacement-model',
                '--phase',phase,'--inference-token-env','BYUL_TEST_INFERENCE','--viewer-token-env','BYUL_TEST_VIEWER','--output',str(output)]
            if phase=='after':args+=['--before-report',str(before_path),'--backend-config',str(backend)]
            assert tool.main(args)==0
        value=json.loads(output.read_text())
        assert all(c['result']=='passed' for c in value['cases']), {c['id']:c['facts'] for c in value['cases'] if c['result']!='passed'}
        assert inference not in output.read_text() and viewer not in output.read_text()
    after=json.loads(after_path.read_text())
    transition=next(c for c in after['cases'] if c['id']=='MODEL-RETIREMENT-TRANSITION')
    assert transition['facts']['policy_changed'] is True
    assert transition['facts']['before_report_sha256']==hashlib.sha256(before_path.read_bytes()).hexdigest()
    assert transition['facts']['before_report_sha256']!=hashlib.sha256(backend.read_bytes()).hexdigest()
    assert after['model_weight_change_claim'] is False and after['engine_log_nonarrival_verified'] is False


def test_unchanged_allowed_model_is_not_retirement(tmp_path):
    directory=tmp_path/'stack';directory.mkdir();before_path=tmp_path/'before.json'
    with owned_stack(directory) as (url,inference,viewer):
        before=checks.model_phase(url,None,'test-model','replacement-model',inference,viewer,'before')
        before['mode']='lifecycle';before_path.write_text(json.dumps(before))
        after=checks.model_phase(url,None,'test-model','replacement-model',inference,viewer,'after',before_report=before_path)
    assert next(c for c in after['cases'] if c['id']=='MODEL-RETIREMENT-TRANSITION')['result']=='failed'


def test_fake_or_partial_before_report_never_dispatches_after(tmp_path,monkeypatch):
    path=tmp_path/'fake-before.json';path.write_text(json.dumps({'mode':'lifecycle','lifecycle':{
        'phase':'before','old_model':'old','new_model':'new','principal_id':'actor','policy_sha256':'hash'},
        'cases':[{'id':'fake','result':'passed'}]}))
    monkeypatch.setattr(checks,'request',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must not dispatch')))
    result=checks.model_phase('http://127.0.0.1:1',None,'old','new','synthetic-inference','viewer','after',before_report=path)
    assert result['execution']=='not_run' and result['cases'][0]['result']=='unknown'


def test_same_selector_and_oversize_evidence_rejected(tmp_path):
    with pytest.raises(ValueError):checks.model_phase('http://127.0.0.1:1',None,'same','same','inference','viewer','before')
    path=tmp_path/'oversize.json';path.write_bytes(b' '* (1024*1024+1))
    with pytest.raises(ValueError):checks.bounded_object(path)


def valid_before():
    scope={'policy_sha256':'before-policy','target_id':'target','run_id':None,'target_instance_id':None}
    rows=[]
    for index,(label,model,allowed) in enumerate([('normal-before','old',True),('retired-or-not-yet-allowed','new',False),('normal-after','old',True)],1):
        rid=f'{index:032x}'
        raw={**scope,'state':'observed','request_id':rid,'record_request_ids_match_query':True,'auth_state':'authenticated',
             'principal_id':'actor','context_stable':True,'started_decision_result':'ALLOWED' if allowed else 'DENIED',
             'decision_result':'ALLOWED' if allowed else 'DENIED','delivery_attempted':allowed,'record_http_status':200 if allowed else 403,
             'final_outcome':'completed' if allowed else 'MODEL_NOT_ALLOWED','reason_code':None if allowed else 'MODEL_NOT_ALLOWED'}
        rows.append({'id':'MODEL-'+label,'result':'passed','facts':{'request_id':rid,'model_selector':model,
            'response_shape_valid':True if allowed else None,'http_status':200 if allowed else 403,'journal':raw}})
    rows += [{'id':'MODEL-PHASE-ACTOR','result':'passed','facts':{'same_verified_principal':True}},
             {'id':'MODEL-PHASE-STABLE','result':'passed','facts':{'scope_before':scope,'scope_after':copy.deepcopy(scope)}}]
    return {'mode':'lifecycle','lifecycle':{'phase':'before','old_model':'old','new_model':'new','principal_id':'actor',
            'policy_sha256':'before-policy','target_scope':scope},'cases':rows}


@pytest.mark.parametrize('change',['record-target','record-instance','stable-scope','missing-scope'])
def test_before_scope_must_match_all_records_and_stability_facts(change):
    before=valid_before();assert checks.prior_phase_valid(before,'old','new') is True
    if change=='record-target':before['cases'][0]['facts']['journal']['target_id']='other'
    elif change=='record-instance':before['cases'][1]['facts']['journal']['target_instance_id']='other'
    elif change=='stable-scope':before['cases'][-1]['facts']['scope_after']['target_id']='other'
    else:del before['lifecycle']['target_scope']
    assert checks.prior_phase_valid(before,'old','new') is False
