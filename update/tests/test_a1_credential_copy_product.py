"""PLT12 uses existing registry/reader/CLI; all Pod/producer inputs EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import base64,json,socket,subprocess
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.executors import credential_copy_contract as contract,kubernetes_credential_copy as copy_executor
from guard.executors.owned_capture import _FILE_READER,MAX_BYTES
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_credential_copy_contract import example
from tests.test_a1_required_event_product import World as OriginalWorld,PRIVATE
from tests.test_kubernetes_request_binding import env
from tools.build_kubernetes_credential_copy_pack import derive


class World(OriginalWorld):
    def _packet(self):
        packet=deepcopy(self.copy_packet);packet['recorded_at']=utcnow().isoformat()
        if self.mode=='missing-event':packet['events']=[r for r in packet['events'] if r['type']!='TREATMENT_CLAIM']
        if self.mode=='reuse-allowed':packet['events'][2]['outcome']='allowed'
        if self.mode=='treatment-failed':packet['events'][1]['outcome']='failed'
        if self.mode=='duplicate-event':packet['events'].append(deepcopy(packet['events'][1]))
        if self.mode=='event-conflict':
            row=deepcopy(packet['events'][1]);row['outcome']='failed';packet['events'].append(row)
        if self.mode=='wrong-recipient':packet['events'][1]['recipient_id']='f'*32
        if self.mode=='wrong-source':packet['source_sha256']='f'*64
        if self.mode=='raw-field':packet['events'][0]['credential_value']=PRIVATE
        if self.mode=='trust-claim':packet['producer_authenticated']=True
        if self.mode=='partial':packet['declared_complete']=False
        if self.mode=='empty':packet['events']=[]
        if self.mode=='after-invalid' and self.reads>1:packet['schema']='unsupported'
        if self.mode=='producer-changed' and self.reads>1:packet['producer_id']='f'*32
        if self.mode in {'restart','pod-replaced'} and self.reads>1:self.after_exec=True
        return packet

    def runner(self,argv,**kwargs):
        if 'input_bytes' in kwargs:raise AssertionError('PLT12 read-only consumer sent an HTTP/issuer/reuse request')
        if 'exec' in argv:
            self.reads+=1;command=argv[argv.index('--')+1:]
            assert command[:5]==['python3','-I','-S','-B','-c'] and command[5]==supervised_source(_FILE_READER)
            assert command[7:]==['/guard-observation/data/'+self.values['copy_record_session']+'/required-events.json',str(MAX_BYTES)]
            assert '--container=engine' in argv and '-i' not in argv and '-t' not in argv
            if self.mode=='missing' or self.mode=='recovered' and self.reads==1 or self.mode=='final-missing' and self.reads==2:raise OSError(PRIVATE)
            raw=canonical_json(self._packet());identity={'device':1,'inode':2,'size':len(raw),'mtime_ns':3,'ctime_ns':4}
            value={'schema':'guard-container-file/2','source_complete':True,'file_identity_stable':True,'truncated':False,
                'nofollow_components':True,'regular_file':True,'raw_base64':base64.b64encode(raw).decode(),
                'raw_retained_bytes':len(raw),'raw_retained_sha256':sha256_hex(raw),'file_before':identity,'file_after':identity,'path_after':identity}
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            if self.mode=='record-cleanup':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json(value),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        return super().runner(argv,**kwargs)


def inputs(tmp_path,mode='normal'):
    environment=env();world=World(environment,mode);inventory,values,packet,_=example()
    values['copy_environment_sha256']=environment.ref().sha256
    values['copy_target_sha256']=sha256_hex(canonical_json(world.target))
    packet.update(environment_ref=environment.ref().model_dump(mode='json'),target_sha256=values['copy_target_sha256'])
    if mode=='late':inventory['copies'][0]['deadline_at']=(utcnow()-timedelta(seconds=1)).isoformat()
    if mode=='retention':
        inventory['copies'][0]['retention_exception']={'exception_id':'f'*32,'expires_at':values['copy_validity_end'],'restrictions_required':['access-denied','audited']}
        packet['events'][1].update(outcome='retained',restriction_claims=['access-denied','audited'])
    path=tmp_path/'copy-inventory.json';path.write_bytes(canonical_json(inventory))
    values['copy_inventory_sha256']=sha256_hex(path.read_bytes());packet['inventory_sha256']=values['copy_inventory_sha256']
    world.copy_packet=packet;world.values=values
    receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    return environment,world,values,pack,receipt,path


@pytest.mark.parametrize('mode',['normal','reuse-allowed','treatment-failed','late','retention','missing-event','duplicate-event','event-conflict',
    'wrong-recipient','wrong-source','raw-field','trust-claim','partial','empty','after-invalid','producer-changed','missing','recovered',
    'final-missing','restart','pod-replaced','record-cleanup','owner-unbound','attachment'])
def test_existing_registry_cli_copy_records_and_exact_refs(tmp_path,monkeypatch,mode):
    environment,world,values,pack,receipt,inventory_path=inputs(tmp_path,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr(copy_executor,'capture_pipes',world.runner)
    if mode=='owner-unbound':
        original_read=copy_executor.RequiredEventReader.read_file
        def unbound(*args,**kwargs):
            raw,provenance=original_read(*args,**kwargs);return raw,{**provenance,'owner_binding_verified':False}
        monkeypatch.setattr(copy_executor.RequiredEventReader,'read_file',unbound)
    def forbidden(*args,**kwargs):raise AssertionError('unexpected network/subprocess outside existing selected reader')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*args,**kwargs):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['PLT-12']},values={'PLT-12':values})
    for name,value in [('environment',environment.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nrequired_event_record="kubernetes"\n[adapter]\nname="vllm"\n[required_event_record]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\ncopy_inventory='+json.dumps(str(inventory_path))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN');assert next(i for i in plan.items if i.check_id=='PLT-12').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common])
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD');result,=store.iter_kind('RES')
    assert evd.data_origin.value=='EXAMPLE' and evd.normal_control.attempted is False and evd.normal_control.succeeded is None
    assert result.verdicts.model_dump(mode='json')=={key:'UNKNOWN' for key in ('security','functional','performance')}
    assert all(evd.observations[key].value is False for key in ('copy_producer_authenticated','copy_membership_verified','copy_treatment_verified','copy_reuse_enforcer_verified','copy_issuer_verified','copy_scope_complete'))
    assert world.sent==[] and receipt['unchanged_definition_bytes']==76 and not receipt['full_PLT12_complete']
    rows=evd.observations['copy_event_declarations'].value
    if mode=='normal':assert len(rows)==4 and evd.execution.status.value=='COMPLETED'
    if mode=='duplicate-event':assert len(rows)==4
    if mode=='reuse-allowed':assert evd.observations['copy_reuse_candidates'].value[0]['declared_reuse_allowed']
    if mode=='treatment-failed':assert evd.observations['copy_treatment_candidates'].value[0]['declared_failure']
    if mode=='late':assert evd.observations['copy_treatment_candidates'].value[0]['declared_deadline_exceeded']
    if mode=='retention':assert evd.observations['copy_treatment_candidates'].value[0]['declared_restricted_retention_match']
    if mode in {'after-invalid','final-missing','restart','pod-replaced','record-cleanup','attachment'}:assert rows
    if mode in {'after-invalid','final-missing','restart','pod-replaced','record-cleanup','missing','partial','empty','event-conflict','wrong-recipient','wrong-source','raw-field','trust-claim','missing-event','producer-changed','recovered','owner-unbound','attachment'}:
        assert evd.execution.status.value=='ABORTED'
    assert bool(evd.execution.cleanup_errors)==(mode=='record-cleanup')
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    for artifact in evd.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg])
    assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[result.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_original_PLT12_question_criteria_and_other_definitions_preserved(tmp_path):
    _,_,_,pack,receipt,_=inputs(tmp_path)
    seed=baseline_pack().path
    for path in seed.rglob('*'):
        if path.is_file() and path.name!='pack.yaml' and path.relative_to(seed).as_posix()!='checks/PLT-12.yaml':
            assert (pack.path/path.relative_to(seed)).read_bytes()==path.read_bytes()
    import yaml
    old=yaml.safe_load((seed/'checks/PLT-12.yaml').read_text(encoding='utf-8'))
    new=yaml.safe_load((pack.path/'checks/PLT-12.yaml').read_text(encoding='utf-8'))
    for key in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','method_and_evidence'):
        assert new[key]==old[key]
    assert receipt['unchanged_definition_bytes']==76


@pytest.mark.parametrize('change',['env','target','inventory','window','source-type','session','delay-bool','unknown','actual-lab','profile'])
def test_configuration_binding_rejected_before_any_remote_io(tmp_path,monkeypatch,change):
    from guard.adapters.vllm import VllmAdapter
    from guard.executors.kubernetes_observation import KubernetesRequiredEventExecutor
    environment,world,values,pack,_,path=inputs(tmp_path)
    if change=='env':values['copy_environment_sha256']='f'*64
    elif change=='target':values['copy_target_sha256']='f'*64
    elif change=='inventory':values['copy_inventory_sha256']='f'*64
    elif change=='window':values['copy_validity_end']=values['copy_validity_start']
    elif change=='source-type':values['copy_record_source_sha256']=True
    elif change=='session':values['copy_record_session']='../not-supported'
    elif change=='delay-bool':values['copy_max_record_delay_ms']=True
    elif change=='unknown':values['new_policy']=None
    elif change=='actual-lab':world.target['lab']=False;values['copy_target_sha256']=sha256_hex(canonical_json(world.target))
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    def forbidden(*args,**kwargs):raise AssertionError('configuration caused remote IO')
    executor=KubernetesRequiredEventExecutor({'target':str(target),'copy_inventory':str(path)},runner=forbidden,environ={})
    check=pack.checks['PLT-12']
    if change=='profile':check=check.model_copy(update={'collection_profile':'kubernetes-required-event-record-observation-v1'})
    with pytest.raises(ValueError,match='COPY_'):
        executor.run(check,environment,values,VllmAdapter(),tmp_path/'work')


def test_read_only_copy_profile_never_loads_role_keys(tmp_path):
    from guard.adapters.vllm import VllmAdapter
    from guard.executors.kubernetes_observation import KubernetesRequiredEventExecutor
    environment,world,values,pack,_,path=inputs(tmp_path)
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    class NoCredentialReads(dict):
        def get(self,*args,**kwargs):raise AssertionError('copy consumer read a credential')
        def __getitem__(self,key):raise AssertionError('copy consumer read a credential')
    executor=KubernetesRequiredEventExecutor({'target':str(target),'copy_inventory':str(path),
        'roles':{'allowed':{'key_env':'UNUSED_ACTUAL_KEY'}}},runner=world.runner,environ=NoCredentialReads())
    draft=executor.run(pack.checks['PLT-12'],environment,values,VllmAdapter(),tmp_path/'work')
    assert draft.normal_control.attempted is False and world.sent==[]
    assert draft.observations['copy_producer_authenticated'].value is False
