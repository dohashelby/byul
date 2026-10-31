"""LIF12 existing registry/owned file/CLI/exact Ref; fake Pod and producer only."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.executors import kubernetes_retry_records as retry_executor
from guard.executors.owned_capture import _FILE_READER, MAX_BYTES
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_a1_required_event_product import World as OriginalWorld, PRIVATE
from tests.test_a1_retry_record_contract import example, bind, automatic, rows_of, append_row
from tests.test_kubernetes_request_binding import env
from tools.build_kubernetes_retry_record_pack import derive


class World(OriginalWorld):
    def _packet(self):
        packet=deepcopy(self.retry_packet);packet['recorded_at']=utcnow().isoformat()
        if self.mode=='declared-forbidden':
            pass  # fixed EXAMPLE policy/config was selected before collection
        elif self.mode=='declared-cost':rows_of(packet,'COST_SAMPLE_CLAIM')[-1]['data']['cumulative_amount']=101
        elif self.mode=='manual-only':
            for row in packet['events']:
                if row['initiator']=='AUTOMATIC':row['initiator']='MANUAL'
        elif self.mode=='missing-event':packet['events']=[r for r in packet['events'] if r['type']!='ATTEMPT_END_CLAIM']
        elif self.mode=='duplicate-event':packet['events'].append(deepcopy(automatic(packet)))
        elif self.mode=='event-conflict':
            row=deepcopy(automatic(packet));row['initiator']='MANUAL';packet['events'].append(row)
        elif self.mode=='seq-gap':packet['events'][-1]['seq']+=1
        elif self.mode=='wrong-actor':automatic(packet)['actor_id']='f'*32
        elif self.mode=='wrong-generation':automatic(packet)['worker_generation']='f'*32
        elif self.mode=='wrong-source':packet['source_sha256']='f'*64
        elif self.mode=='raw-field':automatic(packet)['data']['prompt']=PRIVATE
        elif self.mode=='trust-claim':packet['producer_authenticated']=True
        elif self.mode=='partial':packet['declared_complete']=False
        elif self.mode=='empty':packet['events']=[]
        elif self.mode=='after-invalid' and self.reads>1:packet['schema']='unsupported'
        elif self.mode=='producer-changed' and self.reads>1:packet['producer_id']='f'*32
        elif self.mode=='snapshot-changed' and self.reads>1:packet['snapshot_generation']='f'*32
        if self.mode in {'restart','pod-replaced'} and self.reads>1:self.after_exec=True
        return packet

    def runner(self,argv,**kwargs):
        if 'input_bytes' in kwargs:raise AssertionError('read-only retry consumer sent HTTP, retry or fault injection')
        if 'exec' in argv:
            self.reads+=1;command=argv[argv.index('--')+1:]
            assert command[:5]==['python3','-I','-S','-B','-c'] and command[5]==supervised_source(_FILE_READER)
            assert command[7:]==['/guard-observation/data/'+self.values['retry_session']+'/required-events.json',str(MAX_BYTES)]
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
    environment=env();environment=environment.model_copy(update={'model':environment.model.model_copy(update={'inventory_sha256':'c'*64})}).seal()
    world=World(environment,mode);scope,values,packet,_=example()
    values.update(retry_environment_sha256=environment.ref().sha256,retry_target_sha256=sha256_hex(canonical_json(world.target)))
    scope.update(run_id=world.target['kubernetes']['run_id'],image_digest=world.target['kubernetes']['engine']['image'].rsplit('@',1)[1])
    if mode=='declared-forbidden':
        values['retry_policy_mode']='FORBID'
        for row in rows_of(packet,'CONFIGURATION_CLAIM'):row['data']['mode']='FORBID'
    elif mode=='unresolved':
        values['retry_policy_mode']='UNRESOLVED'
        for row in rows_of(packet,'CONFIGURATION_CLAIM'):row['data']['mode']='UNRESOLVED'
    packet.update(environment_ref=environment.ref().model_dump(mode='json'),target_sha256=values['retry_target_sha256'],run_id=scope['run_id'])
    bind(scope,values,packet)
    path=tmp_path/'retry-scope.json';path.write_bytes(canonical_json(scope))
    assert sha256_hex(path.read_bytes())==values['retry_scope_sha256']
    world.values=values;world.retry_packet=packet
    receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    return environment,world,scope,values,pack,receipt,path


@pytest.mark.parametrize('mode',['normal','unresolved','declared-forbidden','declared-cost','manual-only','missing-event',
    'duplicate-event','event-conflict','seq-gap','wrong-actor','wrong-generation','wrong-source','raw-field','trust-claim',
    'partial','empty','after-invalid','producer-changed','snapshot-changed','missing','recovered','final-missing',
    'restart','pod-replaced','record-cleanup','owner-unbound','attachment'])
def test_existing_request_termination_retry_records_cli_exact_refs(tmp_path,monkeypatch,mode):
    environment,world,scope,values,pack,receipt,scope_path=inputs(tmp_path,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr(retry_executor,'capture_pipes',world.runner)
    if mode=='owner-unbound':
        original=retry_executor.RequiredEventReader.read_file
        def unbound(*args,**kwargs):
            raw,provenance=original(*args,**kwargs);return raw,{**provenance,'owner_binding_verified':False}
        monkeypatch.setattr(retry_executor.RequiredEventReader,'read_file',unbound)
    def forbidden(*args,**kwargs):raise AssertionError('unexpected subprocess or network outside selected owned reader')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def opened(path,*args,**kwargs):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',opened)
    pol=policy(pack.ref(),scope={'include':['LIF-12']},values={'LIF-12':values})
    for name,value in [('environment',environment.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nrequest_termination="kubernetes"\n[adapter]\nname="vllm"\n[request_termination]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nretry_scope='+json.dumps(str(scope_path))+'\nprepared="EXAMPLE_NOT_READ.json"\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN');assert next(i for i in plan.items if i.check_id=='LIF-12').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='record-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD');result,=store.iter_kind('RES')
    assert evd.data_origin.value=='EXAMPLE' and evd.normal_control.attempted is False and evd.normal_control.succeeded is None
    assert result.verdicts.model_dump(mode='json')=={k:'UNKNOWN' for k in ('security','functional','performance')}
    assert all(evd.observations[k].value is False for k in ('retry_producer_authenticated','automatic_retry_verified','retry_nonoccurrence_verified',
        'retry_previous_work_termination_verified','retry_cost_enforcement_verified','retry_scope_complete'))
    assert world.sent==[] and receipt['unchanged_definition_bytes']==76 and not receipt['full_LIF12_complete']
    rows=evd.observations['retry_event_declarations'].value
    if mode in {'normal','unresolved','declared-forbidden','declared-cost','duplicate-event'}:
        assert len(rows)==len(world.retry_packet['events']) and evd.execution.status.value=='COMPLETED'
    if mode=='unresolved':assert 'enterprise_retry_policy_unresolved' in evd.observations['retry_unverified'].value
    if mode=='declared-forbidden':assert any(r['declared_forbidden_automatic_attempt'] for r in evd.observations['retry_family_candidates'].value)
    if mode=='declared-cost':assert any(r['declared_limit_exceeded'] for r in evd.observations['retry_cost_candidates'].value)
    if mode=='manual-only':assert not evd.observations['retry_attempt_candidates'].value
    if mode in {'after-invalid','producer-changed','snapshot-changed','final-missing','restart','pod-replaced','record-cleanup','attachment'}:assert rows
    if mode not in {'normal','unresolved','declared-forbidden','declared-cost','duplicate-event'}:assert evd.execution.status.value=='ABORTED'
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


def test_original_LIF12_criteria_and_other76_definitions_preserved(tmp_path):
    _,_,_,_,pack,receipt,_=inputs(tmp_path)
    seed=baseline_pack().path
    for path in seed.rglob('*'):
        if path.is_file() and path.name!='pack.yaml' and path.relative_to(seed).as_posix()!='checks/LIF-12.yaml':
            assert (pack.path/path.relative_to(seed)).read_bytes()==path.read_bytes()
    import yaml
    old=yaml.safe_load((seed/'checks/LIF-12.yaml').read_text(encoding='utf-8'))
    new=yaml.safe_load((pack.path/'checks/LIF-12.yaml').read_text(encoding='utf-8'))
    for key in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','method_and_evidence'):
        assert new[key]==old[key]
    assert receipt['unchanged_definition_bytes']==76


@pytest.mark.parametrize('change',['env','target','scope','scope-raw','model','inventory','image','run','window','session',
    'source','policy-mode','delay-bool','unknown','actual-lab','version032','profile'])
def test_retry_configuration_closed_before_any_remote_IO(tmp_path,change):
    from guard.adapters.vllm import VllmAdapter
    from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor
    environment,world,scope,values,pack,_,path=inputs(tmp_path)
    if change=='env':values['retry_environment_sha256']='f'*64
    elif change=='target':values['retry_target_sha256']='f'*64
    elif change=='scope':values['retry_scope_sha256']='f'*64
    elif change in {'scope-raw','model','inventory','image','run'}:
        if change=='scope-raw':scope['argv']=['EXAMPLE_PRIVATE']
        elif change=='model':scope['model_revision']='other-revision'
        elif change=='inventory':scope['model_inventory_sha256']='f'*64
        elif change=='image':scope['image_digest']='sha256:'+'f'*64
        elif change=='run':scope['run_id']='other-run'
        path.write_bytes(canonical_json(scope));values['retry_scope_sha256']=sha256_hex(path.read_bytes())
    elif change=='window':values['retry_validity_end']=values['retry_validity_start']
    elif change=='session':values['retry_session']='../unsupported'
    elif change=='source':values['retry_record_source_sha256']=True
    elif change=='policy-mode':values['retry_policy_mode']='implicit-allow'
    elif change=='delay-bool':values['retry_max_record_delay_ms']=True
    elif change=='unknown':values['new_policy']=None
    elif change=='actual-lab':world.target['lab']=False;values['retry_target_sha256']=sha256_hex(canonical_json(world.target))
    elif change=='version032':environment=environment.model_copy(update={'engine':environment.engine.model_copy(update={'version':'0.32.0'})}).seal();values['retry_environment_sha256']=environment.ref().sha256
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    def forbidden(*args,**kwargs):raise AssertionError('retry configuration caused remote IO')
    executor=KubernetesRequestTerminationExecutor({'target':str(target),'retry_scope':str(path)},runner=forbidden,environ={})
    check=pack.checks['LIF-12']
    if change=='profile':check=check.model_copy(update={'collection_profile':'kubernetes-request-attempt-observation-v1'})
    with pytest.raises(ValueError,match='RETRY_'):executor.run(check,environment,values,VllmAdapter(),tmp_path/'work')


def test_retry_read_only_consumer_does_not_read_credentials_or_old_prepared_path(tmp_path,monkeypatch):
    from guard.adapters.vllm import VllmAdapter
    from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor
    environment,world,_,values,pack,_,scope_path=inputs(tmp_path)
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    class NoCredentials(dict):
        def get(self,*args,**kwargs):raise AssertionError('retry consumer accessed a credential')
        def __getitem__(self,key):raise AssertionError('retry consumer accessed a credential')
    original=Path.open
    def opened(path,*args,**kwargs):
        if path.name=='EXAMPLE_NOT_READ.json':raise AssertionError('retry consumer opened LIF10/REQ12 prepared file')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    executor=KubernetesRequestTerminationExecutor({'target':str(target),'retry_scope':str(scope_path),
        'prepared':'EXAMPLE_NOT_READ.json'},runner=world.runner,environ=NoCredentials())
    draft=executor.run(pack.checks['LIF-12'],environment,values,VllmAdapter(),tmp_path/'work')
    assert draft.normal_control.attempted is False and world.sent==[]
    assert draft.observations['automatic_retry_verified'].value is False
