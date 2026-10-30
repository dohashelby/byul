"""OBS02 existing CLI and source gates; serving/records/native are EXAMPLE."""
import ast
from copy import deepcopy
from datetime import timedelta
import json,socket,subprocess
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.models import canonical_json,sha256_hex,load_pack,utcnow
from guard.runtime import kubernetes_http as http
from guard.executors.owned_capture import _FILE_READER
from guard.executors import observation_identity_contract as contract
from guard.executors.observation_contract import SCHEMA,FIELDS
from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external,revised
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_a1_observation_state_product import World as StateWorld,inputs as state_inputs
from tests.test_a1_kubernetes_model_authorization_product import World as ModelWorld,GPU
from tools.build_kubernetes_observation_identity_pack import derive

PRIVATE='EXAMPLE_OBS02_IDENTITY_PRIVATE_RECORD_AND_ERROR'
SESSION='ed'*16;SOURCE='f'*64;APPROVAL=sha256_hex(b'EXAMPLE-nonsecret-approval-record')


class World(StateWorld):
    def __init__(self,base,mode):
        self.__dict__.update(base.__dict__);self.mode=mode;self.record_requests=[];self.record_reads=0

    def add_native(self,rid):
        self.record_requests.append(rid);ModelWorld.add_native(self,rid)
        if self.mode in {'cross-process','cross-clock'}:
            moved=[]
            def remove(rows):
                for row in rows[:]:
                    if row['event']=='NATIVE' and row['payload']['point']=='GPUModelRunner._model_forward':
                        moved.append(deepcopy(row));rows.remove(row)
            self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],remove)
            def insert(rows):
                if not any(r['event']=='MODULE' and r['payload']['module']==GPU for r in rows):
                    rows.insert(-1,{'event':'MODULE','monotonic_ns':rows[-1]['monotonic_ns'],
                        'payload':{'module':GPU,'verified':True,'source_sha256':sensor.SOURCE_SHA[GPU],'code_sha256':'c'*64}})
                for row in moved:row['monotonic_ns']=rows[-1]['monotonic_ns'];rows.insert(-1,row)
                if self.mode=='cross-clock':rows[0]['payload']['context']['time_namespace']+=1
            self.group.raw_by_pid[43]=revised(self.group.raw_by_pid[43],insert)
        if self.mode=='unclosed':
            self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],lambda rows:rows.pop() if rows[-1]['event']=='END' else None)

    def record_packet(self):
        run=sha256_hex(self.target['kubernetes']['run_id'].encode()) if 'run_id' in self.target['kubernetes'] else sha256_hex(self.target['run_id'].encode())
        target=sha256_hex(canonical_json(self.target));events=[]
        def event(kind,request=None):
            row={'seq':len(events),'event_id_sha256':sha256_hex((kind+str(request)+str(len(events))).encode()),'event':kind,
                **{key:None for key in FIELDS}}
            row.update(actor_sha256=sha256_hex(('EXAMPLE-'+kind+'-actor').encode()),target_sha256=target,run_sha256=run,
                policy_sha256='a'*64,approval_sha256=APPROVAL,source_sha256=SOURCE,request_sha256=sha256_hex(request.encode()) if request else None)
            events.append(row)
        if self.mode!='missing-approval':event('APPROVAL')
        if self.mode=='duplicate-approval':event('APPROVAL')
        for rid in self.record_requests:
            if self.mode=='missing-consumption':continue
            event('ARTIFACT_CONSUMED',rid)
            if self.mode=='duplicate-consumption':event('ARTIFACT_CONSUMED',rid)
            if self.mode=='wrong-declared-target':events[-1]['target_sha256']='0'*64
            if self.mode=='wrong-declared-policy':events[-1]['policy_sha256']='0'*64
        packet={'schema':SCHEMA,'session':SESSION,'environment_ref':self.group.env.ref().model_dump(mode='json'),
            'target_sha256':target,'producer_source_sha256':SOURCE,'producer_authenticated':False,
            'source_ended':self.mode!='record-unended','dropped_events':1 if self.mode=='record-dropped' else 0,'events':events}
        if self.mode=='wrong-record-source':packet['producer_source_sha256']='0'*64
        if self.mode=='wrong-record-target':packet['target_sha256']='0'*64
        if self.mode=='record-extra':packet['raw']=PRIVATE
        if self.mode=='forged-authentication':packet['producer_authenticated']=True
        return packet

    def runner(self,argv,**kwargs):
        if 'exec' in argv and 'input_bytes' not in kwargs:
            command=argv[argv.index('--')+1:]
            if command[-2].startswith('/guard-observation/'):
                self.record_reads+=1
                assert command[:5]==['python3','-I','-S','-B','-c'] and command[-2:]==['/guard-observation/data/'+SESSION+'/required-events.json','65536']
                call=next(n for n in ast.walk(ast.parse(command[5])) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='compile')
                assert ast.literal_eval(call.args[0])==_FILE_READER
                if self.mode=='record-missing' or self.mode=='late-record-error' and self.record_reads>=3:raise OSError(PRIVATE)
                raw=canonical_json(self.record_packet());identity={'device':1,'inode':12,'size':len(raw),'mtime_ns':3,'ctime_ns':4}
                value={'schema':'guard-container-file/2','source_complete':True,'file_identity_stable':True,'truncated':False,
                    'nofollow_components':True,'regular_file':True,'raw_base64':__import__('base64').b64encode(raw).decode(),
                    'raw_retained_bytes':len(raw),'raw_retained_sha256':sha256_hex(raw),'file_before':identity,'file_after':identity,'path_after':identity}
                pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
                if self.mode=='record-cleanup' and self.record_reads==3:pipe.update(pipes_closed=False,read_errors=['child_reap'])
                return {'returncode':0,'stdout_bytes':canonical_json(value),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        result=super().runner(argv,**kwargs)
        if self.mode=='restart' and 'exec' not in argv and self.record_reads>=2:
            value=json.loads(result['stdout_bytes'])
            if value.get('kind')=='Pod' and value['metadata']['name']==self.target['kubernetes']['engine']['pod']:
                value['status']['containerStatuses'][0]['restartCount']+=1;result['stdout_bytes']=canonical_json(value)
        return result


def inputs(tmp_path,group,native_target,monkeypatch,mode):
    if mode=='version032':
        env=group.env.model_copy(deep=True);env.engine.version='0.32.0';group.env=env.seal()
        native_target=deepcopy(native_target);native_target['environment_ref']=group.env.ref().model_dump(mode='json')
    world,values,_,_=state_inputs(tmp_path,group,native_target,monkeypatch,mode)
    if mode=='actual-lab-false':world.target['lab']=False
    world=World(world,mode)
    values.update(observation_state_target_sha256=sha256_hex(canonical_json(world.target)),
        identity_prepared_sha256='0'*64 if mode=='prepared-mismatch' else sha256_hex(canonical_json(group.prepared)),
        identity_record_session=SESSION,identity_record_source_sha256=SOURCE)
    derive(baseline_pack().path,tmp_path/'identity-pack');pack=load_pack(tmp_path/'identity-pack')
    return world,values,pack


@pytest.mark.parametrize('mode',['normal','missing-approval','missing-consumption','duplicate-approval','duplicate-consumption',
 'wrong-declared-target','wrong-declared-policy','record-missing','late-record-error','record-extra','wrong-record-source',
 'wrong-record-target','forged-authentication','record-dropped','record-unended','cross-process','cross-clock','unclosed',
 'native-one-missing','native-partial','seal-failure','engine-replaced','restart','attachment','record-cleanup','version032','actual-lab-false','prepared-mismatch'])
def test_registered_identity_records_native_candidates_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    world,values,pack=inputs(tmp_path,group,native_target,monkeypatch,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected external runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['OBS-02']},values={'OBS-02':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nobservation_identity="kubernetes"\n[adapter]\nname="vllm"\n[observation_identity]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(argv,expected=0):
        result=runner.invoke(app,list(map(str,argv)));assert result.exit_code==expected,(result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='record-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,k).value=='UNKNOWN' for k in ('security','functional','performance'))
    assert e.normal_control.succeeded is None
    preflight=mode in {'version032','prepared-mismatch'}
    if preflight:assert not world.sent and world.record_reads==0
    else:
        for key in contract.TRUTH_FIELDS:assert e.observations[key].value is False
        candidates=e.observations['identity_binding_candidates'].value
        if mode in {'normal','attachment','late-record-error','record-cleanup'}:
            assert any(c['same_process_native_model_link_observed'] and c['declared_record_pair_consistent'] for c in candidates)
            for c in candidates:
                assert not c['actual_inventory_verified'] and not c['actual_consumption_verified'] and not c['approval_authority_verified'] and not c['confirmed_violation']
                assert c['native']['native_locator']['capture_sha256']
        if mode=='normal':
            assert len(world.sent)==5 and world.record_reads==3 and e.execution.status.value=='COMPLETED'
            assert all(c['declared_record_pair_consistent'] and c['configured_ENV_model_match_observed'] for c in candidates)
        if mode in {'cross-process','cross-clock','unclosed'}:assert not any(c['same_process_native_model_link_observed'] for c in candidates)
        if mode in {'duplicate-approval','duplicate-consumption','wrong-declared-target','wrong-declared-policy'}:
            assert e.observations['identity_conflict_candidates'].value and not any(c['declared_record_pair_consistent'] for c in candidates)
        if mode in {'record-extra','wrong-record-source','wrong-record-target','forged-authentication'}:
            assert not e.observations['identity_declared_records'].value and e.execution.status.value=='ABORTED'
        if mode in {'attachment','late-record-error','record-cleanup','record-missing','record-dropped','record-unended'}:assert e.execution.status.value=='ABORTED'
        assert set(contract.SEMANTIC_GAPS).issubset(e.observations['identity_missing'].value)
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for artifact in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg])
    assert [o.ref() for o in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


@pytest.mark.parametrize('damage',['missing','extra','uppercase-session','boolean-source','empty-prepared'])
def test_closed_technical_policy_no_enterprise_values_created(tmp_path,group,native_target,monkeypatch,damage):
    _,values,_=inputs(tmp_path,group,native_target,monkeypatch,'normal')
    if damage=='missing':values.pop('identity_record_session')
    elif damage=='extra':values['approval_authority_verified']=True
    elif damage=='uppercase-session':values['identity_record_session']='EF'*16
    elif damage=='boolean-source':values['identity_record_source_sha256']=True
    else:values['identity_prepared_sha256']=''
    with pytest.raises(ValueError,match='OBSERVATION_IDENTITY_POLICY_INVALID'):contract.normalize_policy(values)


def test_derived_pack_preserves_original_three_branches_and_76_bytes(tmp_path):
    seed=baseline_pack();derive(seed.path,tmp_path/'pack');new=load_pack(tmp_path/'pack')
    for name in ('question','normal_control','challenge_steps','fail_criteria','unknown_criteria','satisfied_if','legacy','method_and_evidence'):
        assert getattr(new.checks['OBS-02'],name)==getattr(seed.checks['OBS-02'],name)
    for key in seed.checks:
        if key!='OBS-02':assert (new.path/'checks'/f'{key}.yaml').read_bytes()==(seed.path/'checks'/f'{key}.yaml').read_bytes()
