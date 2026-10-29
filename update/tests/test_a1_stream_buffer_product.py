"""LIF04 real product/pack/Ref boundaries; kubectl/serving are EXAMPLE I/O."""
import asyncio,json,socket,subprocess,sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.adapters import vllm_stream_buffer_sensor as sensor
from guard.cli import app
from guard.collectors import lifecycle
from guard.executors.stream_buffer_contract import normalize_policy
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.runtime.kubernetes_stream_buffer_http import KubernetesStreamBufferHTTP
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_kubernetes_model_authorization_product import World as BaseWorld
from tests.test_a1_stream_buffer_sensor import make_observer,invoke,REPO
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_stream_probe import _server
from guard.runtime.bounded import capture_pipes
from tools.build_kubernetes_stream_buffer_pack import derive

PRIVATE='EXAMPLE_STREAM_PRODUCT_PRIVATE'


class World(BaseWorld):
    def __init__(self,*args):
        super().__init__(*args);self.observed=make_observer(self.group.prepared['session']);self.cursor=0

    def add_native(self,rid):
        if self.mode=='native-unlinked':rid='byul-lif04-'+'00'*16+'-normal'
        asyncio.run(invoke(self.observed,rid));added=deepcopy(self.observed.observer.recorded[self.cursor:]);self.cursor=len(self.observed.observer.recorded)
        if self.mode=='budget-candidate':
            for row in added:
                if row['event']=='STREAM_BUFFER':row['payload'].update(transport_kind='asyncio-selector-tcp',write_buffer_bytes=2,transport_closing=False)
        if self.mode=='invalid-native':
            next(row['payload'] for row in added if row['event']=='STREAM_BUFFER')['observer_code_sha256']='0'*64
        def change(rows):
            rows[-1:-1]=added;rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],added[-1]['monotonic_ns'])
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],change)

    def runner(self,argv,**kwargs):
        if 'exec' not in argv or 'input_bytes' not in kwargs:return super().runner(argv,**kwargs)
        spec=json.loads(kwargs['input_bytes']);request,=spec['requests'];self.sent.append(request);slot=request['id'].removeprefix('lif04-')
        self.add_native(request['headers']['X-Request-Id'])
        if self.mode=='after-timeout' and slot=='normal-after':raise TimeoutError(PRIVATE)
        streaming=request['mode']=='sse'
        body='data: [DONE]\n\n' if streaming else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE,'finish_reason':'length'}]})
        if self.mode=='before-shape' and slot=='normal-before':body='{}'
        row={'id':request['id'],'method':'POST','mode':request['mode'],'outcome':200,'body':body,'body_bytes':len(body.encode()),
            'body_sha256':sha256_hex(body.encode()),'capture_complete':True,'eof':True,'utf8_valid':True,'body_truncated':False,
            'terminal_seen':streaming,'content_type':'text/event-stream' if streaming else 'application/json'}
        if slot in {'slow','slow-close'}:
            reads=request['read_profile']['close_after_reads'];row.update(body='d'*reads,body_bytes=reads,body_sha256=sha256_hex(('d'*reads).encode()),
                capture_complete=False,eof=False,terminal_seen=False,client_closed=True,client_close_ms=reads*2+1,
                read_count=reads,read_pause_count=reads-1,read_observations=[{'seq':i,'started_ms':i*2+1,'finished_ms':i*2+2,'bytes':1} for i in range(reads)],stop_reason='client_close')
        if self.mode=='wrong-response-id' and slot=='normal':row['id']='lif04-slow'
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
        if self.mode=='http-cleanup' and slot=='normal-after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
        return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}


def inputs(tmp_path,group,target,mode):
    environment=group.env.model_copy(deep=True);environment.model.id='EXAMPLE-model';environment.model.revision='EXAMPLE-revision'
    group.env=environment.seal();target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    prepared_dir=group.root/'stream-prepared';group.prepared=lifecycle.prepare_assets(prepared_dir,REPO,profile=sensor.PROFILE)
    group.manifest_path=prepared_dir/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(schema=sensor.SCHEMA,profile=sensor.PROFILE,session=group.prepared['session'],
            settings_sha256=group.prepared['assets']['settings.json']['sha256'],sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'])
    group.raw_by_pid={pid:revised(raw,header) for pid,raw in group.raw_by_pid.items()}
    world=World(group,target,mode);receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'buffer_environment_sha256':group.env.ref().sha256,'buffer_target_sha256':sha256_hex(canonical_json(world.target)),
        'buffer_prepared_sha256':sha256_hex(canonical_json(group.prepared)),
        'buffer_validity_start':(now-timedelta(minutes=1)).isoformat(),'buffer_validity_end':(now+timedelta(minutes=2)).isoformat(),
        'buffer_max_write_bytes':1,'buffer_max_process_rss_bytes':2**62,'buffer_max_send_wait_ms':1000,'buffer_release_deadline_ms':50,
        'buffer_read_chunk_bytes':1,'buffer_read_interval_ms':1,'buffer_read_max_reads':2,
        'buffer_close_chunk_bytes':1,'buffer_close_interval_ms':1,'buffer_close_max_reads':1,'buffer_debug_mode':'unverified'}
    return world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','budget-candidate','native-unlinked','invalid-native','native-one-missing','native-partial',
    'native-census-gap','before-shape','after-timeout','wrong-response-id','engine-replaced','seal-failure','http-cleanup','attachment','actual-lab-false'])
def test_registered_cli_native_samples_and_exact_ref_roundtrip(tmp_path,group,native_target,monkeypatch,mode):
    world,values,pack,_=inputs(tmp_path,group,native_target,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected external runtime')
    # asyncio's owned event loop uses its local self-pipe socket pair while the
    # real fixed method bodies run; remote kubectl/HTTP remain intercepted.
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['LIF-04']},values={'LIF-04':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nstream_buffer="kubernetes"\n[adapter]\nname="vllm"\n[stream_buffer]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(group.manifest_path))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN');assert next(x for x in plan.items if x.check_id=='LIF-04').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,a).value=='UNKNOWN' for a in ('security','functional','performance'))
    assert e.normal_control.succeeded is None
    assert all(e.observations[key].value is False for key in ('buffer_producer_authenticated','buffer_inventory_complete','buffer_release_verified','buffer_scope_complete'))
    if mode=='actual-lab-false':assert not world.sent and e.execution.status.value=='ABORTED'
    else:
        assert len(world.sent)=={'before-shape':2,'engine-replaced':1}.get(mode,5)
        assert world.native_ops.count('seal')==(0 if mode=='engine-replaced' else 1)
    samples=e.observations['buffer_native_samples'].value;cases=e.observations['buffer_cases'].value
    if mode in {'normal','budget-candidate','after-timeout','seal-failure','attachment','http-cleanup','wrong-response-id','native-one-missing'}:assert samples and cases
    if mode=='normal':
        assert any(c['drain_intervals_ns'] for c in cases)
        assert all(c['sampled_write_buffer_peak_bytes'] is None and c['process_rss_is_connection_cost'] is False for c in cases)
    if mode=='budget-candidate':assert e.observations['buffer_conflict_candidates'].value and all(not c['confirmed_violation'] for c in e.observations['buffer_conflict_candidates'].value)
    if mode=='native-unlinked':assert not any(c['unique_connection_observed'] for c in cases)
    if mode in {'invalid-native','native-partial','actual-lab-false','engine-replaced'}:assert e.execution.status.value=='ABORTED'
    assert bool(e.execution.cleanup_errors)==(mode=='http-cleanup')
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for a in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/a.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_full_lif04_pack_preserves_both_branches_and_original_criteria(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_LIF04_complete']
    for field in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['LIF-04'],field)==getattr(pack.checks['LIF-04'],field)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/LIF-04.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('damage',['raw-path','header','body','before-first','bool-read','zero-interval','close-order','extra-policy','hash','window','debug'])
def test_closed_profile_and_policy_reject_unapproved_inputs_before_io(tmp_path,group,native_target,damage):
    world,values,_,_=inputs(tmp_path,group,native_target,'normal')
    request={'id':'lif04-normal-before','method':'POST','mode':'json','url':world.target['engine']['trusted_url']+'/v1/completions',
        'headers':{'X-Request-Id':'byul-lif04-'+'ab'*16+'-normal-before'},'body':{'model':'fixture-model','prompt':'hello','max_tokens':1,'temperature':0,'stream':False}}
    if damage=='raw-path':request['url']+='/other'
    elif damage=='header':request['headers']['Authorization']=PRIVATE
    elif damage=='body':request['body']['prompt']=PRIVATE
    elif damage=='before-first':request['id']='lif04-normal';request['mode']='sse'
    elif damage=='bool-read':values['buffer_read_chunk_bytes']=True
    elif damage=='zero-interval':values['buffer_read_interval_ms']=0
    elif damage=='close-order':values['buffer_close_max_reads']=values['buffer_read_max_reads']
    elif damage=='extra-policy':values['raw']=PRIVATE
    elif damage=='hash':values['buffer_target_sha256']='bad'
    elif damage=='window':values['buffer_validity_end']=values['buffer_validity_start']
    elif damage=='debug':values['buffer_debug_mode']='true'
    if damage in {'raw-path','header','body','before-first'}:
        _,_,profiles=normalize_policy(values);transport=KubernetesStreamBufferHTTP(world.target,group.env,read_profiles=profiles,runner=world.runner)
        with pytest.raises(http.KubernetesBindingError):transport('kubernetes-probe',[request],10)
    else:
        with pytest.raises(ValueError):normalize_policy(values)
    assert not world.sent and not world.native_ops and not world.pods.calls


def test_same_pinned_stdlib_source_executes_five_bounded_owned_socket_requests():
    from tests.test_kubernetes_request_binding import env,target
    from tests.test_kubernetes_sampling import World as PodWorld
    def handle(conn,raw,index,stop):
        head,_,body=raw.partition(b'\r\n\r\n');value=json.loads(body);streaming=index in (1,2,3)
        assert head.startswith(b'POST /v1/completions HTTP/1.1')
        assert value=={'model':'fixture-model','prompt':'hello','max_tokens':8 if streaming else 1,'temperature':0,'stream':streaming}
        data=b'data: '+PRIVATE.encode()+b'\n\ndata: [DONE]\n\n' if streaming else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]}).encode()
        content=b'text/event-stream' if streaming else b'application/json'
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: '+content+b'\r\nContent-Length: '+str(len(data)).encode()+b'\r\nConnection: close\r\n\r\n'+data)
    profiles={'normal':None,'slow':{'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':2},
        'slow-close':{'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':1}}
    with _server(handle) as (url,accepted):
        selected=target();selected['engine']['trusted_url']=url;pods=PodWorld(selected)
        def runner(argv,**kwargs):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kwargs)
            return pods.runner(argv,**kwargs)
        transport=KubernetesStreamBufferHTTP(selected,env(),read_profiles=profiles,runner=runner,duration=60);replies=[]
        for slot in ('normal-before','normal','slow','slow-close','normal-after'):
            streaming=slot in profiles
            request={'id':'lif04-'+slot,'method':'POST','mode':'sse' if streaming else 'json','url':url+'/v1/completions',
                'headers':{'X-Request-Id':'byul-lif04-'+'ab'*16+'-'+slot},
                'body':{'model':'fixture-model','prompt':'hello','max_tokens':8 if streaming else 1,'temperature':0,'stream':streaming}}
            if profiles.get(slot) is not None:request['read_profile']=profiles[slot]
            reply,=transport('kubernetes-probe',[request],10);replies.append(reply)
        assert len(accepted)==5
    assert replies[0]['capture_complete'] and replies[-1]['capture_complete'] and replies[1]['terminal_seen']
    assert replies[2]['intentional_client_close'] and replies[3]['intentional_client_close']
    assert all(reply['body'] is None for reply in replies[1:4])
    report=transport.report();assert not report['collection_gap'] and not report['server_memory_verified']
    assert PRIVATE not in json.dumps(report)
