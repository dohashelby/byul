import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from tools import selected_runtime_check as tool
from tools import selected_request_checks as checks


@contextmanager
def owned_stack(tmp_path, *, allowed_model='test-model'):
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.auth import token_digest
    from serving_runtime.mvp.config import MVPConfig
    from serving_runtime.mvp.mock_engine import MockEngine
    from serving_runtime.mvp.policy import load_policy
    inference, viewer = 'synthetic-inference-for-local-test', 'synthetic-viewer-for-local-test'
    auth = {'schema_version':'runtime-auth/1.0','auth_ref':'local-test-only','principals':[
        {'principal_id':'client-a','roles':['inference'],'token_sha256':token_digest(inference)},
        {'principal_id':'viewer','roles':['viewer'],'token_sha256':token_digest(viewer)}],
        'route_roles':{'POST /v1/chat/completions':['inference'],'GET /api/*':['viewer']}}
    policy = {'schema_version':'runtime-mvp/1.0','policy_id':'local-test-only','policy_version':'1','target_id':'local',
        'allowed_models':[allowed_model],'output_default_tokens':8,'output_max_tokens':16,'max_body_bytes':65536}
    auth_path, policy_path = tmp_path/'auth.json', tmp_path/'policy.json'
    auth_path.write_text(json.dumps(auth));policy_path.write_text(json.dumps(policy))
    engine = MockEngine(port=0).start(); gateway = None
    try:
        gateway = GatewayApp(MVPConfig(gateway_port=0,engine_port=0,auth_config_path=str(auth_path),
            policy_path=str(policy_path),data_dir=str(tmp_path/'records'),run_id='local-selected-request-test'),
            load_policy(policy_path),engine_address=engine.address).start()
        yield 'http://127.0.0.1:'+str(gateway.address[1]), inference, viewer
    finally:
        if gateway:gateway.stop()
        engine.stop()


def test_request_matrix_uses_real_owned_gateway_journals_and_platform(tmp_path):
    with owned_stack(tmp_path) as (url, inference, viewer):
        with tool.platform_server(url,viewer) as (platform,_):
            result = checks.request_matrix(url,platform,'test-model',inference,viewer,timeout=5)
    unknown = [c for c in result['cases'] if c['result'] == 'unknown']
    assert {c['id'] for c in unknown} <= {'REQ-ROUTE-GET-/v1/chat/completions'}
    for row in unknown:
        assert row['facts']['http_status'] == 405
        assert row['facts']['external_rejection_observed'] is True
        assert row['facts']['journal']['witness'] == 'external_http_only'
    assert not [c for c in result['cases'] if c['result']=='failed'], {c['id']:c['facts'] for c in result['cases'] if c['result']=='failed'}
    forgery = next(c for c in result['cases'] if c['id']=='REQ-HEADER-AND-BODY-FORGERY')
    assert forgery['facts']['journal']['principal_id']=='client-a'
    assert forgery['facts']['same_baseline_trusted_fields'] is True
    assert forgery['facts']['platform']['same_trusted_fields'] is True
    assert forgery['facts']['journal']['synthetic_marker_present'] is False
    assert inference not in json.dumps(result) and viewer not in json.dumps(result)
    assert result['gpu_release_claim'] is False and result['inference_posts_retried'] is False


def test_cross_record_id_never_binds_other_requests_context(monkeypatch):
    requested, other = 'a'*32, 'b'*32
    rows=[{'phase':p,'request_id':other,'auth':{'principal_id':'wrong-principal'}} for p in ('started','final')]
    monkeypatch.setattr(checks,'request',lambda *a,**kw:tool.HTTPResponse(200,{},json.dumps({'gateway_records':rows}).encode()))
    result=checks.record_for('http://127.0.0.1:1',requested,'synthetic-viewer',.1)
    assert result['state']=='mismatched_query_id'
    assert 'principal_id' not in result and other not in json.dumps(result)


def test_missing_viewer_precondition_does_not_send_post(monkeypatch):
    methods=[]
    def request(method,*a,**kw):
        methods.append(method);return tool.HTTPResponse(401,{},b'{}')
    monkeypatch.setattr(checks,'request',request)
    result=checks.request_matrix('http://127.0.0.1:1',None,'test-model','synthetic-inference',None)
    assert result['execution']=='not_run' and methods==['GET','GET']
    assert all(c['result']!='passed' for c in result['cases'])


def test_raw_journal_marker_is_detected_without_exporting_its_value(monkeypatch):
    rid='a'*32; marker='SYNTHETIC_PRIVATE_BODY_MARKER'
    rows=[{'phase':p,'request_id':rid,'request':{'forbidden_body':marker}} for p in ('started','final')]
    monkeypatch.setattr(checks,'request',lambda *a,**kw:tool.HTTPResponse(200,{},json.dumps({'gateway_records':rows}).encode()))
    result=checks.record_for('http://127.0.0.1:1',rid,'synthetic-viewer',.1,marker=marker)
    assert result['synthetic_marker_present'] is True and marker not in json.dumps(result)


def test_ambiguous_route_audit_is_unknown(monkeypatch):
    rid='a'*32
    monkeypatch.setattr(checks,'request',lambda *a,**kw:tool.HTTPResponse(200,{},json.dumps({'items':[{'request_id':rid}]*2,'window':{'truncated':True}}).encode()))
    result=checks.route_record_for('http://127.0.0.1:1',rid,'synthetic-viewer',.1)
    assert result['state']=='unknown' and result['window']['truncated'] is True


def test_cli_blocks_credentials_in_any_new_matrix_result(tmp_path,monkeypatch):
    secret='SYNTHETIC-CREDENTIAL-MUST-NOT-EXPORT'
    monkeypatch.setenv('BYUL_TEST_INFERENCE',secret);monkeypatch.setenv('BYUL_TEST_VIEWER','synthetic-viewer')
    monkeypatch.setattr(checks,'request_matrix',lambda *a,**kw:{'cases':[{'id':'bad','facts':{'code':secret}}]})
    output=tmp_path/'result.json'
    result=tool.main(['requests','--gateway-url','http://127.0.0.1:1','--model','test-model',
        '--inference-token-env','BYUL_TEST_INFERENCE','--viewer-token-env','BYUL_TEST_VIEWER','--output',str(output)])
    assert result==2 and secret not in output.read_text()
    assert json.loads(output.read_text())['cases'][0]['result']=='unknown'
