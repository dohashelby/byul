import copy
import json

from tools import selected_observation_checks as checks
from tools import selected_runtime_check as tool
from byul_platform.sources.checklist_data import CHECKS,FIXED_SHA,PACK_REVISION


def feed(*,platform=False,count=10):
    detail={k:None for k in checks.FIELDS};detail.update(redaction_dropped_lines_total=count,storage_errors_total=2,
        last_storage_error_class='OSError',last_storage_error_at='2026-11-06T00:00:00Z',checkpoint_pending=True,
        writer={'configured':True,'state':'stopped','updated_at':'2026-11-06T00:00:00Z','reconnect_count':1,
                'open_gap':{'gap_id':'synthetic-gap-id','opened_at':'2026-11-06T00:00:00Z','closed_at':None,'attempts':1,'backfill':'unknown'},'last_gap':None})
    return {'source':'observation_feed_tetragon','state':'error','detail' if platform else 'last_value':detail}


def test_stable_loss_fields_are_compared_and_error_text_not_exported():
    raw=feed();api=feed(platform=True)
    raw['last_value']['last_redaction_error_class']='SYNTHETIC_PRIVATE_ERROR_VALUE'
    api['detail']['last_redaction_error_class']='SYNTHETIC_PRIVATE_ERROR_VALUE'
    passed,detail=checks.compare_feed(raw,api,copy.deepcopy(raw))
    assert passed is True and detail['after']['numeric_fields']['redaction_dropped_lines_total']==10
    assert detail['after']['diagnostics']['last_storage_error_class']=='OSError'
    assert detail['after']['gaps']['open_gap']['opened_at']=='2026-11-06T00:00:00Z'
    assert 'SYNTHETIC_PRIVATE_ERROR_VALUE' not in json.dumps(detail)


def test_source_changed_during_snapshot_is_unknown_but_stable_loss_is_failure():
    result,_=checks.compare_feed(feed(count=0),feed(platform=True,count=0),feed(count=10))
    assert result is None
    result,_=checks.compare_feed(feed(count=10),feed(platform=True,count=0),feed(count=10))
    assert result is False


def run_observe(monkeypatch,*,wrong_scope=False,wrong_ids=False,invalid=False):
    window={'source_windows':{'gateway':{'returned':0,'total':None,'limit':200,'truncated':False},
            'hubble':{'returned':0,'total':0,'limit':50,'truncated':False},'tetragon':{'returned':50,'total':100,'limit':50,'truncated':True}}}
    raw={'auth':{'protected_routes':['GET /api/*']},'policy':{'sha256':'policy'},'target':{'target_id':'target'},
         'run_id':'run','observation_session_id':'session','collectors':[feed()]}
    item={'policy':{'policy_ref':{'sha256':'other' if wrong_scope else 'policy'}},'target':{'target_id':'target'},
          'run_id':'run','observation_session_id':'session','collectors':[feed(platform=True)]}
    coverage={**window,'selected_count':37,'excluded_count':40,'pack_revision':PACK_REVISION,'fixed_sha':FIXED_SHA,
              'items':[{'framework_id':str(index) if wrong_ids else c['framework_id']} for index,c in enumerate(CHECKS)]}
    def request(method,url,**kw):
        assert method=='GET'
        value=({'item':item,'invalid':{'code':'invalid'} if invalid else None} if '/api/runtime/status' in url else
               coverage if url.endswith('/checks') else window if url.endswith('/events') else raw)
        return tool.HTTPResponse(200,{},json.dumps(value).encode())
    monkeypatch.setattr(checks,'request',request)
    return checks.observe('http://gateway','http://platform','synthetic-viewer','tetragon',expected_state='error',wait_seconds=.2)


def test_viewer_only_snapshot_preserves_windows_and_exact_selected_ids(monkeypatch):
    result=run_observe(monkeypatch)
    assert all(c['result']=='passed' for c in result['cases'])
    assert result['inference_posts']==0 and result['past_gap_backfill_independently_verified'] is False


def test_wrong_platform_generation_and_contract_invalid_never_pass(monkeypatch):
    for options in ({'wrong_scope':True},{'invalid':True}):
        result=run_observe(monkeypatch,**options)
        assert result['cases'][0]['result']=='failed'


def test_same_count_of_unrelated_checks_is_not_selected37(monkeypatch):
    result=run_observe(monkeypatch,wrong_ids=True)
    row=next(c for c in result['cases'] if c['id']=='OBS-SELECTED37')
    assert row['result']=='failed' and row['facts']['unexpected_id_count']==37


def test_string_truncation_flag_is_not_a_valid_window():
    data={'source_windows':{name:{'returned':0,'total':None if name=='gateway' else 0,'limit':200 if name=='gateway' else 50,'truncated':'false' if name=='gateway' else False} for name in ('gateway','hubble','tetragon')}}
    assert tool.validate_windows(data) is False


def test_observe_cli_only_uses_viewer_reads_and_never_exports_the_token(tmp_path,monkeypatch):
    run_observe(monkeypatch)
    secret='SYNTHETIC_VIEWER_PRIVATE_VALUE';monkeypatch.setenv('BYUL_TEST_VIEWER',secret)
    output=tmp_path/'observe.json'
    code=tool.main(['observe','--gateway-url','http://gateway','--platform-url','http://platform',
        '--viewer-token-env','BYUL_TEST_VIEWER','--source','tetragon','--expected-state','error',
        '--wait-seconds','.2','--output',str(output)])
    assert code==0 and secret not in output.read_text()
    assert json.loads(output.read_text())['inference_posts']==0
