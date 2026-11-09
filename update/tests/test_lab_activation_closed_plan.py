"""Four native cases need four fresh scopes; replay delegates native truth."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from guard.executors import lab_activation as actor
from guard.models import Environment, Policy, canonical_json
from guard.runtime.kubernetes_binding import validate_capture_target
from tools import b_lif08_fault_control as tool


@pytest.fixture
def batch(tmp_path, monkeypatch):
    values={}
    for kind in tool.KINDS:
        values['rank_'+kind.replace('-','_')+'_detection_ms']=5000
        values['rank_'+kind.replace('-','_')+'_notification_ms']=1000
    policy=Policy(id='POL-test',data_origin='DECLARED',status='DRAFT',pack={
        'kind':'PACK','id':'PACK-test','revision':1,'sha256':'a'*64},values={'LIF-08':values}).seal()
    env=Environment(id='ENV-test',data_origin='DECLARED',engine={'name':'vllm','version':'0.31.0',
        'image_digest':'registry/vllm@sha256:'+'a'*64},model={'id':'model','revision':'b'*40,
        'inventory_sha256':'c'*64}).seal()
    projections={};calls=[]
    def validate(packet,client,injection,scope,deadlines):
        calls.append(scope['fault_kind'])
        assert packet['kind']==scope['fault_kind'] and client['fault_nonce']==scope['nonce']
        assert injection['pre_signal_packet']['kind']==scope['fault_kind']
        assert deadlines=={'detection_ms':5000,'notification_ms':1000,'response_ms':5000}
        return deepcopy(projections[scope['fault_kind']])
    module=SimpleNamespace(__file__=actor.__file__,PROFILE=tool.PROFILE,validate_fault_case=validate)
    monkeypatch.setattr(tool,'control_module',lambda:module)
    cases=[];refs=[]
    for i,kind in enumerate(sorted(tool.KINDS),1):
        engine={'namespace':'lab','namespace_uid':'00000000-0000-0000-0000-000000000001',
            'pod':'engine-'+str(i),'pod_uid':'00000000-0000-0000-0000-'+str(i+1).zfill(12),'container':'engine',
            'container_id':'containerd://'+format(i,'x')*64,'image':env.engine.image_digest,
            'image_id':'sha256:'+'d'*64,'restart_count':0,'node':'node','pod_ip':'10.0.0.'+str(i),
            'started_at':'2026-11-06T00:00:00Z'}
        target={'schema':'guard-kubernetes-lifecycle-target/1','run_id':'run','environment_ref':env.ref().model_dump(mode='json'),
            'lab':True,'kubernetes':{'context':'byul','engine':engine}}
        binding=validate_capture_target(target,env)
        ctx={'env':env,'binding':binding,'module':module}
        scope=tool._scope(ctx,{'model':'model','deadline_ms':5000},policy,values,'f'*40)
        token={'session':format(i,'x')*32,'nonce':format(i+4,'x')*32,'target_sha256':tool.digest(target),
            'external_request_sha256':format(i+8,'x')*64,'fault_kind':kind,'rank':0}
        expected={**token,'source_profile':tool.PROFILE,'pod_uid':engine['pod_uid'],'container_id':engine['container_id']}
        before={'packet':{'kind':kind},'transport':{'collection_gap':False},'sealed':False}
        projection={**dict.fromkeys(tool.FLAGS,True),'missing':[],'violations':[],'evidence_refs':['native:'+kind]}
        projections[kind]=projection
        trial={'schema':'byul-lab-native-fault-trial/1','fault_kind':kind,'nonce':token['nonce'],'control':token,
            'expected_scope':expected,'binding':binding,'environment':env.model_dump(mode='json'),'target':target,
            'prepared':{'profile':tool.PROFILE,'session':token['session']},
            'seed_capture':{'packet':{k:{'metadata':{'sealed':False}} for k in ('before','after')},
                'transport':{'collection_gap':False},'sealed':False},
            'pre_signal_capture':before,'post_capture':{'packet':{'kind':kind},'transport':{'collection_gap':False},'sealed':True},
            'injection':{'pre_signal_packet':before['packet']},'client':{'fault_nonce':token['nonce']},
            'deadlines':{'detection_ms':5000,'notification_ms':1000,'response_ms':5000},
            'response_deadline_ms':5000,'projection':deepcopy(projection),'issued':True,'released':True,
            'restore_confirmed':True,'error_kind':None}
        cases.append({'schema':tool.CASE_SCHEMA,'scope':scope,'policy':policy.model_dump(mode='json'),'trial':trial,
            'activation':{'activation_steps':[{'step':'fault-'+kind}], 'activation_violations':[],
                'activation_missing':[],'activation_scope_complete':False},'one_kind_complete':True})
        refs.append({'path':str(tmp_path/(kind+'.json')),'sha256':None})
    def write():
        for case,ref in zip(cases,refs):
            raw=canonical_json(case);tool.Path(ref['path']).write_bytes(raw);ref['sha256']=tool.sha(raw)
        return {'schema':tool.PLAN_SCHEMA,'cases':deepcopy(refs)}
    return SimpleNamespace(cases=cases,refs=refs,write=write,projections=projections,calls=calls,
        module=module,policy=policy,env=env)


def test_closed_plan_replays_four_raw_sources_and_only_then_sets_whole_scope(batch):
    result=tool.close_plan(batch.write())
    assert set(batch.calls)==tool.KINDS and len(batch.calls)==4
    assert result['activation_scope_complete'] and result['activation_missing']==[]
    assert all(c['activation']['activation_scope_complete'] is False for c in batch.cases)
    assert len(result['case_refs'])==4 and len(result['activation_steps'])==4


@pytest.mark.parametrize('field',['session','target_sha256','pod_uid','container_id','nonce','external_request_sha256'])
def test_duplicate_generation_or_session_never_closes_four(batch,field):
    # Even an internally self-consistent source replay must not reuse one trial
    # for a different kind. Most mutations are rejected before the uniqueness gate.
    first,second=batch.cases[:2]
    expected=second['trial']['expected_scope'];control=second['trial']['control']
    expected[field]=first['trial']['expected_scope'][field]
    if field in control:control[field]=expected[field]
    if field=='session':second['trial']['prepared']['session']=expected[field]
    if field=='nonce':
        second['trial']['nonce']=expected[field];second['trial']['client']['fault_nonce']=expected[field]
    if field in ('pod_uid','container_id'):
        second['trial']['binding']['engine'][field]=expected[field]
        second['trial']['target']['kubernetes']['engine'][field]=expected[field]
        expected['target_sha256']=control['target_sha256']=tool.digest(second['trial']['target'])
    with pytest.raises(ValueError):tool.close_plan(batch.write())


@pytest.mark.parametrize('field',['policy_sha256','image','model','context','namespace','source_profile','actor_sha256'])
def test_any_cross_case_logical_or_source_mismatch_is_rejected(batch,field):
    case=batch.cases[1]
    case['scope'][field]={'id':'other'} if field=='model' else 'different'
    with pytest.raises(ValueError):tool.close_plan(batch.write())


def test_original_fail_is_preserved_with_complete_negative_observation(batch):
    case=batch.cases[0];kind=case['trial']['fault_kind']
    projection=batch.projections[kind]
    projection['detection_deadline_verified']=False
    projection['violations']=[{'code':'RANK_DETECTION_DEADLINE_EXCEEDED','fault':kind}]
    case['trial']['projection']=deepcopy(projection)
    result=tool.close_plan(batch.write())
    assert result['activation_scope_complete'] is True
    assert result['activation_violations']==projection['violations']
    assert result['case_outcomes'][0]['native_projection']['detection_deadline_verified'] is False


def test_unknown_stays_incomplete_and_original_raw_reference_is_retained(batch):
    case=batch.cases[0];projection=batch.projections[case['trial']['fault_kind']]
    projection['whole_kind_complete']=False;projection['clock_verified']=False;projection['missing']=['CLOCK_CONTEXT_MISSING']
    case['trial']['projection']=deepcopy(projection);case['one_kind_complete']=False
    case['activation']['activation_missing']=['original-gap']
    result=tool.close_plan(batch.write())
    assert result['activation_scope_complete'] is False and 'original-gap' in result['activation_missing']
    assert result['case_refs'][0]==batch.refs[0]
    assert result['case_outcomes'][0]['native_projection']==projection


@pytest.mark.parametrize('failure',['old-seal','stored-projection-changed','missing-case','duplicate-kind','raw-hash-change','restore-missing','deadline-changed'])
def test_bad_or_incomplete_plan_cannot_claim_completion(batch,failure):
    case=batch.cases[0]
    if failure=='old-seal':case['trial']['seed_capture']['packet']['before']['metadata']['sealed']=True
    elif failure=='stored-projection-changed':case['trial']['projection']['clock_verified']=False
    elif failure=='restore-missing':case['trial']['restore_confirmed']=False
    elif failure=='deadline-changed':case['trial']['deadlines']['detection_ms']=999999
    elif failure=='duplicate-kind':case['trial']['fault_kind']=batch.cases[1]['trial']['fault_kind']
    plan=batch.write()
    if failure=='missing-case':plan['cases'].pop()
    elif failure=='raw-hash-change':tool.Path(plan['cases'][0]['path']).write_text('{}')
    with pytest.raises(ValueError):tool.close_plan(plan)


def test_generic_wrapper_cannot_seal_before_native_actor():
    child=SimpleNamespace(run=lambda *_:(_ for _ in ()).throw(AssertionError('passive must not run')))
    wrapper=actor.ActivatedExecutor(child,dict.fromkeys(actor.FAULT_CONTROL_KEYS,'configured'))
    result=wrapper.run(SimpleNamespace(id='LIF-08'),None,{},None,'unused')
    assert result.abort_reason=='FAULT_CONTROL_EXPLICIT_ONE_KIND_ENTRY_REQUIRED'


def test_generic_four_loop_cannot_reuse_first_case_session():
    lab=SimpleNamespace(cfg=dict.fromkeys(actor.FAULT_CONTROL_KEYS,'configured'),missing=[])
    lab.miss=lab.missing.append
    actor._rank_faults(lab,{})
    assert lab.missing==['FAULT_CONTROL_EXPLICIT_ONE_KIND_ENTRY_REQUIRED']


def test_one_kind_entry_requires_explicit_kind_before_any_process_action():
    with pytest.raises(ValueError,match='EXPLICIT_KIND'):tool.run_one({},None,'f'*40)


def test_cli_closure_writes_new_artifact_and_preserves_raw_files(batch,tmp_path):
    plan=batch.write();plan_path=tmp_path/'plan.json';plan_path.write_bytes(canonical_json(plan))
    output=tmp_path/'closed.json'
    assert tool.main(['close-plan','--plan',str(plan_path),'--output',str(output)])==0
    assert output.stat().st_mode & 0o777==0o600
    assert tool.read_json(output)['activation_scope_complete'] is True
    assert all(tool.sha(tool.Path(r['path']).read_bytes())==r['sha256'] for r in batch.refs)
    original=output.read_bytes()
    with pytest.raises(ValueError,match='OUTPUT_EXISTS'):
        tool.main(['close-plan','--plan',str(plan_path),'--output',str(output)])
    assert output.read_bytes()==original


@pytest.mark.parametrize('invalid',[None,False,True,0,1,[],{},'', 'a'*63,'a'*65,'A'*64,'g'*64])
def test_case_reference_requires_lower64hex_before_any_case_io(batch,monkeypatch,invalid):
    plan=batch.write();plan['cases'][0]['sha256']=invalid
    monkeypatch.setattr(tool,'read_json',lambda *_args,**_kwargs:(_ for _ in ()).throw(AssertionError('invalid reference must not read case')))
    with pytest.raises(ValueError,match='REFERENCE_INVALID'):tool.close_plan(plan)
