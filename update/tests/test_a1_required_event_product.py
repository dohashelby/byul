"""OBS01 public transport/owned record/CLI: serving and kubectl are EXAMPLE."""
import ast
import base64
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import socket
import subprocess
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.adapters.vllm import VllmAdapter
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.executors import kubernetes_observation as observation
from guard.executors.observation_contract import EVENTS,FIELDS,SCHEMA,normalize_policy,validate_record
from guard.executors.owned_capture import _FILE_READER
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_kubernetes_request_binding import env,target
from tests.test_kubernetes_sampling import World as PodWorld
from tools.build_kubernetes_required_event_pack import derive

PRIVATE='EXAMPLE_OBS01_PRIVATE_NOT_PERSISTED'
ALLOWED='EXAMPLE_OBS01_ALLOWED_KEY_PRIVATE'
DENIED='EXAMPLE_OBS01_DENIED_KEY_PRIVATE'
SESSION='a1'*16


class World(PodWorld):
    def __init__(self,environment,mode='normal'):
        super().__init__(target(environment));self.environment=environment;self.mode=mode;self.sent=[];self.records=[];self.reads=0
        self.values=None

    def _packet(self):
        rows=deepcopy(self.records)
        if self.mode=='empty':rows=[]
        if self.mode=='wrong-actor' and rows:rows[0]['actor_sha256']='0'*64
        if self.mode=='wrong-record-target' and rows:rows[0]['target_sha256']='0'*64
        if self.mode=='missing-field' and rows:rows[0]['actor_sha256']=None
        if self.mode=='duplicate-record' and rows:rows.append(deepcopy(rows[0]));rows[-1]['seq']=len(rows)-1
        packet={'schema':SCHEMA,'session':SESSION,'environment_ref':self.environment.ref().model_dump(mode='json'),
            'target_sha256':self.values['observation_target_sha256'],'producer_source_sha256':self.values['observation_expected_source_sha256'],
            'producer_authenticated':False,'source_ended':self.mode!='unclosed','dropped_events':1 if self.mode=='dropped' else 0,'events':rows}
        if self.mode=='bad-source':packet['producer_source_sha256']='0'*64
        if self.mode=='producer-trust-claim':packet['producer_authenticated']=True
        if self.mode=='raw-field':packet['events'][0]['raw']=PRIVATE
        return packet

    def runner(self,argv,**kw):
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
        if 'exec' in argv and 'input_bytes' in kw:
            request,=json.loads(kw['input_bytes'])['requests'];self.sent.append(request['id']);self.after_exec=True
            slot=request['id'].removeprefix('obs01-');denied=slot=='denied'
            assert request['headers']['Authorization']=='Bearer '+(DENIED if denied else ALLOWED)
            assert request['body']=={'model':'fixture-model','messages':[{'role':'user','content':'hello'}],'max_tokens':1,'stream':False}
            outcome=403 if denied else 200
            if self.mode=='denied-allowed' and denied:outcome=200
            text=json.dumps({'error':PRIVATE} if denied else {'model':'fixture-model','choices':[{'message':{'content':PRIVATE}}]})
            if self.mode=='bad-control' and slot=='before':text='{}'
            if self.mode=='after-error' and slot=='after':raise OSError(PRIVATE)
            record={k:None for k in FIELDS};record.update(seq=len(self.records),event_id_sha256=sha256_hex(('EXAMPLE-event-'+str(len(self.records))).encode()),
                event='ACCESS_DENIED' if denied else 'ACCESS_ALLOWED',request_sha256=sha256_hex(request['headers']['X-Request-Id'].encode()),
                actor_sha256=self.values['observation_'+('denied' if denied else 'allowed')+'_actor_sha256'],
                target_sha256=self.values['observation_target_sha256'],run_sha256=sha256_hex(self.target['kubernetes']['run_id'].encode()),
                policy_sha256=self.values['observation_event_policy_sha256'],source_sha256=self.values['observation_expected_source_sha256'],
                result_sha256=sha256_hex(canonical_json({'outcome':outcome})))
            self.records.append(record)
            row={'id':request['id'],'mode':'json','method':'POST','outcome':outcome,'body':text,'body_bytes':len(text.encode()),
                'body_sha256':sha256_hex(text.encode()),'capture_complete':True,'eof':True,'utf8_valid':True,'body_truncated':False,'terminal_seen':False}
            if self.mode=='http-cleanup' and slot=='after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        if 'exec' in argv:
            self.reads+=1;command=argv[argv.index('--')+1:]
            assert command[:5]==['python3','-I','-S','-B','-c'] and command[5]==supervised_source(_FILE_READER)
            assert command[7:]==['/guard-observation/data/'+SESSION+'/required-events.json','65536']
            assert '--container=engine' in argv and '-i' not in argv and '-t' not in argv
            if self.mode=='missing' or self.mode=='recovered' and self.reads==1 or self.mode=='final-missing' and self.reads==2:raise OSError(PRIVATE)
            raw=canonical_json(self._packet());identity={'device':1,'inode':2,'size':len(raw),'mtime_ns':3,'ctime_ns':4}
            value={'schema':'guard-container-file/2','source_complete':True,'file_identity_stable':True,'truncated':False,
                'nofollow_components':True,'regular_file':True,'raw_base64':base64.b64encode(raw).decode(),
                'raw_retained_bytes':len(raw),'raw_retained_sha256':sha256_hex(raw),'file_before':identity,'file_after':identity,'path_after':identity}
            if self.mode=='record-cleanup':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json(value),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        if 'namespace' in argv:
            r=self.target['kubernetes']['engine'];value={'kind':'Namespace','metadata':{'name':r['namespace'],'uid':r['namespace_uid']}}
            return {'returncode':0,'stdout_bytes':canonical_json(value),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        result=super().runner(argv,**kw);value=json.loads(result['stdout_bytes'])
        if 'spec' in value:
            value['kind']='Pod'
            if self.after_exec and self.mode=='restart':value['status']['containerStatuses'][0]['restartCount']+=1
            if self.after_exec and self.mode=='pod-replaced':value['metadata']['uid']='00000000-0000-0000-0000-000000000099'
        result['stdout_bytes']=canonical_json(value);result['capture'].update(pipe);return result


def inputs(tmp_path,mode='normal'):
    environment=env();world=World(environment,mode);now=utcnow()
    values={'observation_environment_sha256':environment.ref().sha256,'observation_target_sha256':sha256_hex(canonical_json(world.target)),
        'observation_validity_start':(now-timedelta(minutes=1)).isoformat(),'observation_validity_end':(now+timedelta(minutes=1)).isoformat(),
        'observation_max_delay_ms':300000,'observation_required_events':['ACCESS_ALLOWED','ACCESS_DENIED'],
        'observation_required_fields':['actor_sha256','target_sha256','run_sha256','policy_sha256','request_sha256','result_sha256','source_sha256'],
        'observation_session':SESSION,'observation_expected_source_sha256':'b'*64,'observation_event_policy_sha256':'c'*64,
        'observation_allowed_actor_sha256':'d'*64,'observation_denied_actor_sha256':'e'*64}
    if mode=='required-unimplemented':values['observation_required_events']=list(sorted(EVENTS));values['observation_required_fields'].append('approval_sha256')
    if mode=='deadline':values['observation_max_delay_ms']=0
    world.values=values;receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    return environment,world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','wrong-actor','wrong-record-target','missing-field','duplicate-record','empty','missing','recovered',
    'final-missing','unclosed','dropped','bad-source','producer-trust-claim','raw-field','bad-control','denied-allowed','after-error',
    'http-cleanup','record-cleanup','restart','pod-replaced','attachment','required-unimplemented'])
def test_existing_cli_records_and_exact_refs_preserve_unknown_and_followup_facts(tmp_path,monkeypatch,mode):
    environment,world,values,pack,receipt=inputs(tmp_path,mode)
    monkeypatch.setenv('OBS_ALLOWED',ALLOWED);monkeypatch.setenv('OBS_DENIED',DENIED)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr(observation,'capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected external runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['OBS-01']},values={'OBS-01':values})
    for name,value in [('environment',environment.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nrequired_event_record="kubernetes"\n[adapter]\nname="vllm"\n[required_event_record]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nroles={allowed="OBS_ALLOWED",denied="OBS_DENIED"}\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,expected=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==expected,(args,result.output,result.exception)
        assert all(secret not in result.output for secret in (PRIVATE,ALLOWED,DENIED))
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode in {'http-cleanup','record-cleanup'} else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,k).value=='UNKNOWN' for k in ('security','functional','performance'))
    for key in ('required_event_producer_authenticated','required_event_independent_trigger_verified','required_event_scope_complete'):assert e.observations[key].value is False
    assert e.execution.status.value==('COMPLETED' if mode=='normal' else 'ABORTED')
    if mode=='normal':
        assert world.sent==['obs01-before','obs01-denied','obs01-after'] and world.reads==2
        assert all(m['declared_record_match_observed'] and not m['producer_authenticated'] for m in e.observations['required_event_matches'].value)
        assert len(e.observations['required_event_records'].value)==3 and not e.observations['required_event_missing'].value
    if mode in {'wrong-actor','wrong-record-target','duplicate-record'}:
        assert e.observations['required_event_conflict_candidates'].value
        assert all(not r['confirmed_violation'] for r in e.observations['required_event_conflict_candidates'].value)
    if mode=='final-missing':assert e.observations['required_event_records'].value
    if mode=='required-unimplemented':
        assert {'ADMIN_ACTION','APPROVAL','ARTIFACT_CONSUMED','DEPLOYMENT'}<={m['event'] for m in e.observations['required_event_missing'].value}
    assert receipt['checks']==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_OBS01_complete']
    assert all(secret not in canonical_json(e.to_dict()).decode() for secret in (PRIVATE,ALLOWED,DENIED))
    for artifact in e.artifacts:assert all(secret.encode() not in (tmp_path/'store'/artifact.path).read_bytes() for secret in (PRIVATE,ALLOWED,DENIED))
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);cli(['verify','--store',tmp_path/'restored','--config',cfg])
    assert [o.ref() for o in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]


@pytest.mark.parametrize('damage',['missing','unknown','bool-delay','too-long','actor-same','bad-source','no-actor-field','unknown-event','no-approval-field'])
def test_bad_policy_refused(tmp_path,damage):
    _,_,values,_,_=inputs(tmp_path)
    if damage=='missing':values.pop('observation_session')
    elif damage=='unknown':values['raw_key']=PRIVATE
    elif damage=='bool-delay':values['observation_max_delay_ms']=True
    elif damage=='too-long':values['observation_validity_end']=(utcnow()+timedelta(days=1)).isoformat()
    elif damage=='actor-same':values['observation_denied_actor_sha256']=values['observation_allowed_actor_sha256']
    elif damage=='bad-source':values['observation_expected_source_sha256']='not-sha'
    elif damage=='no-actor-field':values['observation_required_fields'].remove('actor_sha256')
    elif damage=='unknown-event':values['observation_required_events']=['HTTP200_IS_APPROVED']
    else:values['observation_required_events'].append('APPROVAL')
    with pytest.raises(ValueError,match='^OBSERVATION_POLICY_INVALID$'):normalize_policy(values)


@pytest.mark.parametrize('damage',['lab','native032','target','environment','expired'])
def test_configuration_refuses_before_any_io(tmp_path,damage):
    environment,world,values,pack,_=inputs(tmp_path)
    if damage=='lab':world.target['lab']=False;values['observation_target_sha256']=sha256_hex(canonical_json(world.target))
    elif damage=='native032':
        environment=environment.model_copy(deep=True);environment.engine.version='0.32.0';environment=environment.seal()
        world.target['environment_ref']=environment.ref().model_dump(mode='json');world.target['observed_version']='0.32.0'
        values['observation_environment_sha256']=environment.ref().sha256;values['observation_target_sha256']=sha256_hex(canonical_json(world.target))
    elif damage=='target':values['observation_target_sha256']='0'*64
    elif damage=='environment':values['observation_environment_sha256']='0'*64
    else:values['observation_validity_end']=(utcnow()-timedelta(seconds=1)).isoformat()
    path=tmp_path/'target.json';path.write_bytes(canonical_json(world.target))
    def forbidden(*a,**kw):raise AssertionError('configuration must refuse before IO')
    executor=observation.KubernetesRequiredEventExecutor({'target':str(path),'roles':{'allowed':'OBS_ALLOWED','denied':'OBS_DENIED'}},
        runner=forbidden,environ={'OBS_ALLOWED':ALLOWED,'OBS_DENIED':DENIED})
    with pytest.raises((ValueError,RuntimeError)):executor.run(pack.checks['OBS-01'],environment,values,VllmAdapter(),tmp_path/'work')


def test_known_secret_cannot_be_relabelled_as_a_hash_field(tmp_path):
    environment,world,values,_,_=inputs(tmp_path)
    row={k:None for k in FIELDS};row.update(seq=0,event_id_sha256='f'*64,event='ACCESS_ALLOWED')
    world.records=[row];packet=world._packet()
    with pytest.raises(ValueError,match='^OBSERVATION_SENSITIVE_RECORD_REFUSED$'):
        validate_record(packet,environment_ref=environment.ref().model_dump(mode='json'),target_sha256=values['observation_target_sha256'],
            session=SESSION,expected_source_sha256=values['observation_expected_source_sha256'],forbidden_values=['f'*64])
