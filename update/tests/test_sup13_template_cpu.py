"""Collector failure-preservation contracts using EXAMPLE-only local replacements."""
import json
from pathlib import Path
import pytest
from tools import b_sup13_template_cpu as c

@pytest.fixture
def boundary(monkeypatch):
    monkeypatch.setattr(c,'verify_component',lambda:None)
    monkeypatch.setattr(c,'_verify_source',lambda root:None)
    monkeypatch.setattr(c,'_post',lambda *a,**k:{'status':'verified'})


def rows(record):
    record['data_origin']='EXAMPLE'
    for case in ('before','challenge','after'):
        record['results'].append({'case_id':case,'outcome':'returned','snapshots':[{'metadata':{'attribute':'__class__','attribute_allowed':True}}],
            'source_validation':{'pre':'verified','post':{'status':'verified'},'boundary':'source_component'}})


def test_formal_component_is_closed_without_root_pin(monkeypatch):
    monkeypatch.setattr(c,'PRODUCT_COMPONENT_COMMIT',None)
    with pytest.raises(ValueError,match='SUP13_COMPONENT_PIN_UNCONFIGURED'):c.verify_component()

@pytest.mark.parametrize('profile',['reference5d','target032',''])
def test_wrong_profile_before_capture(tmp_path,profile):
    with pytest.raises(ValueError,match='SUP13_PROFILE_INVALID'):c.capture_template_cpu(tmp_path,tmp_path/'work',source_profile=profile)
    assert not (tmp_path/'work').exists()


def test_completed_example_contract(tmp_path,boundary,monkeypatch):
    monkeypatch.setattr(c,'_run_actual',lambda root,workdir,record:rows(record))
    r=c.capture_template_cpu(tmp_path,tmp_path/'work')
    assert r['capture_status']=='completed' and r['data_origin']=='EXAMPLE'
    assert r['installed_target'] is None and not r['full_scope_coverage']
    assert r['auxiliary_write']=={'status':'completed'}


def test_late_failure_keeps_known_attribute_true(tmp_path,boundary,monkeypatch):
    def run(root,workdir,record):rows(record);raise OSError('PRIVATE')
    monkeypatch.setattr(c,'_run_actual',run)
    r=c.capture_template_cpu(tmp_path,tmp_path/'work')
    assert r['capture_status']=='incomplete' and r['results'][1]['snapshots'][0]['metadata']['attribute_allowed'] is True
    assert r['error_type']=='OSError' and 'PRIVATE' not in json.dumps(r)


def test_final_guard_failure_keeps_independent_facts(tmp_path,boundary,monkeypatch):
    monkeypatch.setattr(c,'_run_actual',lambda root,workdir,record:rows(record))
    monkeypatch.setattr(c,'_post',lambda *a,**k:{'status':'unavailable','error_type':'OSError'})
    r=c.capture_template_cpu(tmp_path,tmp_path/'work')
    assert r['capture_status']=='incomplete' and len(r['results'])==3
    assert r['results'][1]['snapshots'][0]['metadata']['attribute_allowed'] is True


def test_auxiliary_failure_keeps_facts(tmp_path,boundary,monkeypatch):
    def run(root,workdir,record):rows(record);(workdir/'result.json').mkdir()
    monkeypatch.setattr(c,'_run_actual',run)
    r=c.capture_template_cpu(tmp_path,tmp_path/'work')
    assert r['capture_status']=='incomplete' and r['auxiliary_write']['status']=='failed'
    assert len(r['results'])==3


def test_event_overflow_is_incomplete_and_preserves_known_observations(tmp_path,boundary,monkeypatch):
    def run(root,workdir,record):
        rows(record)
        challenge=record['results'][1]
        for _ in range(c.MAX_EVENTS):c._retain_event(challenge,{'status':'collected'})
    monkeypatch.setattr(c,'_run_actual',run)
    r=c.capture_template_cpu(tmp_path,tmp_path/'work')
    challenge=r['results'][1]
    assert r['capture_status']=='incomplete' and len(challenge['snapshots'])==c.MAX_EVENTS
    assert challenge['observation_gaps']==['event_limit_exceeded'] and challenge['dropped_event_count']==1
    assert challenge['snapshots'][0]['metadata']['attribute_allowed'] is True
    assert len(r['results'])==3


@pytest.mark.parametrize('phase',['CALL','RETURN'])
@pytest.mark.parametrize('positional',[False,True])
def test_actual_wrapper_arguments_reach_snapshot(phase,positional):
    messages=c.native.CHALLENGE;template='PRIVATE_TEMPLATE'
    values={'self':object(),'args':(messages,) if positional else (),
        'kwargs':{'chat_template':template,**({} if positional else {'conversation':messages})}}
    args,kwargs=c._snapshot_arguments('tokenizer.apply_chat_template',values)
    s=c.native.snapshot_template_boundary('tokenizer.apply_chat_template',args,kwargs,phase=phase,result='PRIVATE_RENDER')
    assert s['metadata']['messages_sha256']==c.native.digest(messages)
    assert s['metadata']['messages_case']=='challenge' and s['metadata']['template']==c.native._text(template)
    assert 'PRIVATE' not in json.dumps(s)


@pytest.mark.parametrize('bad',[object(),iter(())])
def test_wrapper_unknown_containers_are_not_inspected(bad):
    args,kwargs=c._snapshot_arguments('tokenizer.apply_chat_template',{'args':bad,'kwargs':{}})
    assert args==() and kwargs=={}


def test_oversized_wrapper_mapping_is_unavailable():
    assert c._snapshot_arguments('tokenizer.apply_chat_template',{'args':(),'kwargs':dict.fromkeys(range(33))})==((),{})


def test_cli_output_alias_reuses_same_capture_api(monkeypatch,tmp_path):
    called=[]
    def capture(source,work):called.append((source,work));return {'capture_status':'completed'}
    monkeypatch.setattr(c,'capture_template_cpu',capture)
    monkeypatch.setattr(c.sys,'argv',['collector','--source-root',str(tmp_path/'source'),'--output',str(tmp_path/'owned')])
    assert c.main()==0 and called==[(tmp_path/'source',tmp_path/'owned')]
