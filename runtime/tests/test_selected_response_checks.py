import json

from tools import selected_response_checks as checks
from tools import selected_runtime_check as tool


def row(marker,rid,*,closed=False):
    return {'mode':'sse','outcome':200,'capture_complete':not closed,'terminal_seen':not closed,
        'client_closed':closed,'stop_reason':'client_close' if closed else 'eof',
        'response_headers':{'x-request-id':rid},
        'events':[{'data':json.dumps({'choices':[{'delta':{'content':marker}}]})}]}


def raw(rid):
    return {'state':'observed','request_id':rid,'record_request_ids_match_query':True,'context_stable':True,
            'principal_id':'client-a','policy_sha256':'policy','target_id':'target','target_instance_id':'generation','run_id':'run',
            'decision_result':'ALLOWED','started_decision_result':'ALLOWED','auth_state':'authenticated','delivery_attempted':True,'final_outcome':'completed'}


def test_first_generated_marker_close_and_new_correct_recipient():
    a,b='a'*32,'b'*32
    result=checks.assess_separation(row('ALPHA001',a,closed=True),row('BETA002',b),'ALPHA001','BETA002',raw(a),raw(b))
    assert result['result']=='passed'
    assert result['facts']['alpha_generated_output_observed'] is True
    assert result['facts']['engine_termination_verified'] is False


def test_header_only_close_preserves_content_unknown():
    a,b='a'*32,'b'*32
    result=checks.assess_separation(row('',a,closed=True),row('BETA002',b),'ALPHA001','BETA002',raw(a),raw(b))
    assert result['result']=='unknown' and result['facts']['beta_completed'] is True
    assert result['facts']['alpha_generated_output_observed'] is False


def test_late_alpha_content_in_beta_is_failure():
    a,b='a'*32,'b'*32
    result=checks.assess_separation(row('ALPHA001',a,closed=True),row('BETA002 ALPHA001',b),'ALPHA001','BETA002',raw(a),raw(b))
    assert result['result']=='failed' and result['facts']['cross_marker_observed'] is True


def test_consistent_other_record_cannot_bind_to_current_response():
    a,b='a'*32,'b'*32
    result=checks.assess_separation(row('ALPHA001',a,closed=True),row('BETA002',b),'ALPHA001','BETA002',raw('c'*32),raw(b))
    assert result['result']=='unknown' and result['facts']['same_trusted_context'] is False


def test_beta_failure_is_not_hidden_by_a_matching_echo():
    a,b='a'*32,'b'*32
    failed={**raw(b),'decision_result':'ERROR','final_outcome':'CLIENT_DISCONNECTED'}
    result=checks.assess_separation(row('ALPHA001',a,closed=True),row('BETA002',b),'ALPHA001','BETA002',raw(a),failed)
    assert result['result']=='failed'


def test_auth_denial_cannot_be_a_normal_alpha_generation():
    a,b='a'*32,'b'*32
    alpha={**row('ALPHA001',a,closed=True),'outcome':401}
    denied={**raw(a),'decision_result':'DENIED','started_decision_result':'DENIED','auth_state':'invalid','delivery_attempted':False}
    result=checks.assess_separation(alpha,row('BETA002',b),'ALPHA001','BETA002',denied,raw(b))
    assert result['result']=='failed'


def test_absent_verified_principal_policy_target_stays_unknown():
    a,b='a'*32,'b'*32
    empty=lambda rid:{**raw(rid),**dict.fromkeys(('principal_id','policy_sha256','target_id','target_instance_id','run_id')),'auth_state':'anonymous'}
    result=checks.assess_separation(row('ALPHA001',a,closed=True),row('BETA002',b),'ALPHA001','BETA002',empty(a),empty(b))
    assert result['result']=='unknown' and result['facts']['same_trusted_context'] is False


def test_configured_generation_cannot_be_omitted():
    a,b='a'*32,'b'*32
    missing={**raw(a),'run_id':None}
    result=checks.assess_separation(row('ALPHA001',a,closed=True),row('BETA002',b),'ALPHA001','BETA002',missing,raw(b),expected_scope={'run_id':'run'})
    assert result['result']=='unknown'


def test_unprotected_status_never_dispatches_separation(monkeypatch):
    monkeypatch.setattr(checks,'request',lambda *a,**kw:tool.HTTPResponse(200,{},b'{"current":{"output_max_tokens":128,"sha256":"policy","target_id":"target"}}'))
    monkeypatch.setattr(checks,'collect',lambda *a:(_ for _ in ()).throw(AssertionError('must not dispatch')))
    result=checks.separation('http://127.0.0.1:1','test-model','synthetic-inference',None)
    assert result['execution']=='not_run' and result['cases'][0]['result']=='unknown'


def test_separation_dispatches_b_only_after_a_collection(monkeypatch):
    order=[];records={};index=[0]
    monkeypatch.setattr(checks,'request',lambda *a,**kw:tool.HTTPResponse(200,{},json.dumps(
        {'auth':{'protected_routes':['GET /api/*']},'run_id':'run'} if a[1].endswith('/api/status') else
        {'current':{'output_max_tokens':128,'sha256':'policy','target_id':'target'}}).encode()))
    monkeypatch.setattr(checks,'concurrent_precondition',lambda *a,**kw:(True,{}))
    monkeypatch.setattr(checks,'record_for',lambda base,rid,*args:raw(rid))
    def collect(specs,timeout):
        assert len(specs)==1
        spec=specs[0];index[0]+=1;rid=f'{index[0]:032x}';order.append(spec)
        return [row('',rid,closed=spec['id']=='alpha')]
    def buffered(spec,timeout):
        index[0]+=1;order.append(spec);r=row('',f'{index[0]:032x}',closed=True);r['mode']='json';r['body']='{';return r
    monkeypatch.setattr(checks,'collect',collect);monkeypatch.setattr(checks,'buffered_close',buffered)
    result=checks.separation('http://127.0.0.1:1','test-model','synthetic-only','viewer',timeout=1)
    assert [s['id'] for s in order]==['alpha','beta']*3
    assert {s['headers']['X-Request-ID'] for s in order}=={'same-synthetic-client-id'}
    assert all(c['result']=='unknown' for c in result['cases'])
    assert all('synthetic-only' not in json.dumps(c) for c in result['cases'])


def test_buffered_close_uses_real_http_and_current_source(tmp_path):
    from test_selected_request_checks import owned_stack
    with owned_stack(tmp_path) as (url, inference, viewer):
        spec={'url':url+'/v1/chat/completions','headers':{'Authorization':'Bearer '+inference},
            'body':tool.json_body('test-model')}
        result=checks.buffered_close(spec,2)
    assert result['mode']=='json' and result['outcome']==200
    assert result['client_closed'] is True and len(result['body'].encode())==1
    assert result['capture_complete'] is False
