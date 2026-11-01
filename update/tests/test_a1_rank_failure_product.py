"""Canonical LIF08 product/Ref tests; serving/kubectl/TP are EXAMPLE doubles."""
from copy import deepcopy
from datetime import timedelta
import json,socket,subprocess,sys
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.adapters import vllm_rank_failure_sensor as sensor,vllm_rank_failure_metadata as metadata
from guard.cli import app
from guard.collectors import lifecycle
from guard.executors.rank_failure_contract import normalize_policy
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.runtime.kubernetes_rank_failure import KubernetesRankFailureHTTP,SLOTS
from guard.runtime.bounded import capture_pipes
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_kubernetes_model_authorization_product import World as BaseWorld
from tests.test_a1_rank_failure_sensor import make_world,invoke,REPO
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_stream_probe import _server
from tools.build_kubernetes_rank_failure_pack import derive

PRIVATE='EXAMPLE_RANK_PRODUCT_PRIVATE'


class World(BaseWorld):
    def __init__(self,*args):
        super().__init__(*args);self.observed=make_world(self.group.prepared['session']);self.cursor=0

    def add_native(self,rid,slot):
        kind=slot.removeprefix('observe-')
        try:invoke(self.observed,rid,kind if kind in {'exception','delay','communication-stall'} else 'normal')
        except (RuntimeError,TimeoutError):pass
        additions=deepcopy(self.observed.observer.recorded[self.cursor:]);self.cursor=len(self.observed.observer.recorded)
        if self.mode=='mp-unobserved':additions=[r for r in additions if r['event']!='RANK_FAILURE']
        if self.mode=='invalid-native':
            next(r['payload'] for r in additions if r['event']=='RANK_FAILURE')['fault_activation_verified']=True
        def change(rows):
            if additions:
                rows[-1:-1]=additions;rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],additions[-1]['monotonic_ns'])
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],change)

    def runner(self,argv,**kwargs):
        if 'exec' not in argv or 'input_bytes' not in kwargs:
            result=super().runner(argv,**kwargs)
            if 'namespaces' in argv:
                value=json.loads(result['stdout_bytes'])
                for item in value['items']:item['kind']='Namespace'
                result['stdout_bytes']=canonical_json(value)
            return result
        request,=json.loads(kwargs['input_bytes'])['requests'];self.sent.append(request);slot=request['id'].removeprefix('lif08-')
        assert request['body']=={'model':'fixture-model','prompt':'hello','max_tokens':1,'temperature':0,'stream':False}
        self.add_native(request['headers']['X-Request-Id'],slot)
        if self.mode=='after-timeout' and slot=='normal-after':raise TimeoutError(PRIVATE)
        body='{}' if self.mode=='before-shape' and slot=='normal-before' else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]})
        row={'id':request['id'],'mode':'json','method':'POST','outcome':500 if slot=='observe-exception' else 200,'body':body,
            'body_bytes':len(body.encode()),'body_sha256':sha256_hex(body.encode()),'capture_complete':True,'eof':True,'utf8_valid':True,'body_truncated':False}
        if self.mode=='wrong-response-id' and slot=='observe-exception':row['id']='lif08-normal-before'
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
        if self.mode=='http-cleanup' and slot=='normal-after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
        return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}


def inputs(tmp_path,group,target,mode):
    environment=group.env.model_copy(deep=True);environment.model.id='EXAMPLE-model';environment.model.revision='EXAMPLE-revision'
    if mode=='version032':environment.engine.version='0.32.0'
    group.env=environment.seal();target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    prepared=group.root/'rank-prepared';group.prepared=lifecycle.prepare_assets(prepared,REPO,profile=sensor.PROFILE);group.manifest_path=prepared/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(schema=sensor.SCHEMA,profile=sensor.PROFILE,session=group.prepared['session'],
            settings_sha256=group.prepared['assets']['settings.json']['sha256'],sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'])
    group.raw_by_pid={pid:revised(raw,header) for pid,raw in group.raw_by_pid.items()}
    world=World(group,target,mode);receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'rank_environment_sha256':group.env.ref().sha256,'rank_target_sha256':sha256_hex(canonical_json(world.target)),
        'rank_prepared_sha256':sha256_hex(canonical_json(group.prepared)),
        'rank_validity_start':(now-timedelta(minutes=1)).isoformat(),'rank_validity_end':(now+timedelta(minutes=2)).isoformat(),
        'rank_executor_backend':'mp','rank_expected_ranks':['0','1'],'rank_output_rank':1}
    for kind in metadata.FAULT_KINDS:
        for step in ('detection','notification'):values['rank_'+kind.replace('-','_')+'_'+step+'_ms']=20
    return world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','mp-unobserved','invalid-native','native-one-missing','native-partial','native-census-gap',
    'before-shape','after-timeout','wrong-response-id','engine-replaced','seal-failure','http-cleanup','attachment','actual-lab-false','version032'])
def test_registered_cli_selected_mp_facts_four_missing_faults_and_exact_refs(tmp_path,group,native_target,monkeypatch,mode):
    world,values,pack,_=inputs(tmp_path,group,native_target,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['LIF-08']},values={'LIF-08':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nrank_failure="kubernetes"\n[adapter]\nname="vllm"\n[rank_failure]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(group.manifest_path))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(i for i in plan.items if i.check_id=='LIF-08').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,a).value=='UNKNOWN' for a in ('security','functional','performance'))
    assert e.normal_control.succeeded is None
    flags=('rank_producer_authenticated','rank_normal_all_ranks_verified','rank_upper_client_receipt_verified','rank_scope_complete')
    if mode in {'actual-lab-false','version032'}:
        assert not world.sent and all(e.observations[k].value is None and not e.observations[k].collected for k in flags)
    else:
        assert all(e.observations[k].value is False for k in flags)
        assert len(world.sent)=={'before-shape':2,'wrong-response-id':2,'engine-replaced':1}.get(mode,6)
        assert world.native_ops.count('seal')==(0 if mode=='engine-replaced' else 1)
        cases=e.observations['rank_fault_cases'].value;assert [c['kind'] for c in cases]==list(metadata.FAULT_KINDS)
        assert all(not c['fault_activation_verified'] and not c['whole_kind_complete'] and not c['upper_notification_deadline_verified'] for c in cases)
    if mode in {'normal','after-timeout','seal-failure','http-cleanup','attachment','native-one-missing'}:
        assert e.observations['rank_native_samples'].value and e.observations['rank_native_calls'].value
        candidates=e.observations['rank_failure_candidates'].value;assert candidates
        assert all(not c['confirmed_violation'] and not c['four_fault_kind_inferred'] for c in candidates)
    if mode in {'mp-unobserved','invalid-native','native-partial','engine-replaced','actual-lab-false','version032'}:assert e.execution.status.value=='ABORTED'
    assert bool(e.execution.cleanup_errors)==(mode=='http-cleanup')
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for a in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/a.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_pack_preserves_all_four_faults_original_normal_criteria_and_other76(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_LIF08_complete'] and not receipt['four_faults_completed']
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['LIF-08'],name)==getattr(pack.checks['LIF-08'],name)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/LIF-08.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('damage',['ranks-bool','ranks-duplicate','output-not-selected','backend','deadline-bool','hash','window','extra-policy','raw-call','invalid-slot','wrong-id','after-first'])
def test_policy_and_public_probe_reject_unreviewed_inputs_before_io(tmp_path,group,native_target,damage):
    world,values,_,_=inputs(tmp_path,group,native_target,'normal')
    if damage=='ranks-bool':values['rank_expected_ranks']=[True]
    elif damage=='ranks-duplicate':values['rank_expected_ranks']=['0','0']
    elif damage=='output-not-selected':values['rank_output_rank']=3
    elif damage=='backend':values['rank_executor_backend']='ray'
    elif damage=='deadline-bool':values['rank_exception_detection_ms']=True
    elif damage=='hash':values['rank_target_sha256']='bad'
    elif damage=='window':values['rank_validity_end']=values['rank_validity_start']
    elif damage=='extra-policy':values['raw']=PRIVATE
    if damage not in {'raw-call','invalid-slot','wrong-id','after-first'}:
        with pytest.raises(ValueError):normalize_policy(values)
    else:
        transport=KubernetesRankFailureHTTP(world.target,group.env,runner=world.runner)
        with pytest.raises(http.KubernetesBindingError):
            if damage=='raw-call':transport('kubernetes-probe',[{'url':PRIVATE}],10)
            else:transport.perform('unreviewed' if damage=='invalid-slot' else 'normal-after' if damage=='after-first' else 'normal-before',
                PRIVATE if damage=='wrong-id' else 'byul-lif08-'+'ab'*16+'-normal-before')
    assert not world.sent and not world.native_ops and not world.pods.calls


def test_closed_public_profile_runs_six_same_source_owned_socket_probes_without_fault_control():
    from tests.test_kubernetes_request_binding import env,target
    from tests.test_kubernetes_sampling import World as PodWorld
    def handle(conn,raw,index,stop):
        head,_,body=raw.partition(b'\r\n\r\n');assert head.startswith(b'POST /v1/completions HTTP/1.1')
        assert json.loads(body)=={'model':'fixture-model','prompt':'hello','max_tokens':1,'temperature':0,'stream':False}
        data=json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]}).encode()
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(data)).encode()+b'\r\nConnection: close\r\n\r\n'+data)
    with _server(handle) as (url,accepted):
        selected=target();selected['engine']['trusted_url']=url;pods=PodWorld(selected)
        def runner(argv,**kwargs):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kwargs)
            result=pods.runner(argv,**kwargs);value=json.loads(result['stdout_bytes'])
            if 'namespaces' in argv:
                for item in value['items']:item['kind']='Namespace'
            else:value['kind']='Pod'
            result['stdout_bytes']=canonical_json(value);return result
        transport=KubernetesRankFailureHTTP(selected,env(),runner=runner,duration=60)
        replies=[transport.perform(slot,'byul-lif08-'+'ab'*16+'-'+slot) for slot in SLOTS]
        assert len(accepted)==6 and all(r['normal_shape_verified'] and r['capture_complete'] for r in replies)
    report=transport.report();assert not report['collection_gap'] and not report['faults_injected'] and not report['producer_authenticated']
    assert all(not r['fault_activation_verified'] and not r['all_required_ranks_completed'] for r in replies)
    assert PRIVATE not in json.dumps(report)
