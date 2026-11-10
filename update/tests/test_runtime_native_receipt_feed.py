"""EXAMPLE packet -> original projector -> real host feed -> request API wiring.

Root Linux tests use actual file owners/modes. No engine/GPU execution is claimed.
"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from guard.collectors.lifecycle import prepare_assets
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
from guard.models import canonical_json,sha256_hex
from serving_runtime.mvp.native_receipts import NativeReceiptFeed,canonical,read_host_feed
from serving_runtime.mvp.collectors import CollectionFailure
from serving_runtime.mvp.update_engine_link import project_termination
from serving_runtime.mvp.query import QueryService
from serving_runtime.mvp.store import JsonlStore
from tests.test_runtime_termination_native23 import inputs,RID
from tests.test_kubernetes_request_binding import env
from tests.test_kubernetes_sampling import inputs as pod_inputs

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('native_product_cli',ROOT/'runtime/tools/update_engine_link.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
pytestmark=pytest.mark.skipif(os.geteuid()!=0,reason='actual root-owned feed OS boundary runs in Linux container')


def files(tmp_path,backend='vllm'):
    environment=env()
    if backend=='sglang':
        values=environment.model_dump(mode='python');values['engine'].update(name='sglang',version='0.5.22');values['content_sha256']=''
        environment=type(environment).model_validate(values).seal()
    prepared=prepare_assets(tmp_path/'prepared',ROOT,profile='sglang-native-lifecycle-metadata/1'if backend=='sglang'else'vllm-native-lifecycle-metadata/23')
    role=deepcopy(pod_inputs()['kubernetes']['engine']);role['image']=environment.engine.image_digest;role['image_id']=role['image']
    target={'schema':'guard-kubernetes-lifecycle-target/1','lab':True,'run_id':'run-one','environment_ref':environment.ref().model_dump(mode='json'),'kubernetes':{'context':'fixture-context','engine':role}}
    reader=KubernetesLifecycleReader(target,environment,prepared)
    records,packet,_=inputs();binding=reader.binding
    if backend=='sglang':
        from tests.test_sglang_request_termination import packet as sglang_packet,G
        from tests.test_runtime_termination_native23 import replace
        packet=replace(sglang_packet(),G,hashlib.sha256(RID.encode()).hexdigest())
        for row in records:row.update(backend_kind='sglang',upstream_request_id=RID)
    owner={'namespace_uid':role['namespace_uid'],'pod_uid':role['pod_uid'],'container_id':role['container_id'],'reported_image_id':role['image_id'],'restart_count':role['restart_count']}
    packet['session']=prepared['session']
    for recording in packet['recordings']:
        recording['capture']['records'][0]['payload']['session']=prepared['session']
        recording['capture'].setdefault('provenance',{})['owner']=deepcopy(owner)
    packet['before']['owner']=deepcopy(owner);packet['after']['owner']=deepcopy(owner);packet['recordings'][0]['capture']['provenance']['owner']=deepcopy(owner)
    for row in records:
        row['target'].update(pod_uid=role['pod_uid'],container_id=role['container_id'],image_digest=role['image_id'],restart_count=role['restart_count'])
        row['decision']={'result':'ALLOWED'};row['delivery']={'attempted':True};row['request']={}
    paths={name:tmp_path/(name+'.json')for name in ('environment','target','packet','records')}
    paths['environment'].write_bytes(canonical_json(environment.model_dump(mode='json')))
    paths['target'].write_bytes(canonical_json(target))
    source={'schema':'byul-runtime-native-capture/1','packet':packet,'data_origin':'EXAMPLE','environment_ref':environment.ref().model_dump(mode='json'),'target_sha256':sha256_hex(canonical_json(target)),'session_sealed':True,'transport':{'binding':binding,'collection_gap':False}}
    paths['packet'].write_bytes(canonical(source));paths['packet'].chmod(0o600)
    paths['records'].write_text(''.join(json.dumps(r)+'\n'for r in records));paths['records'].chmod(0o600)
    archive=tmp_path/'captures';archive.mkdir(mode=0o700)
    output=tmp_path/'feed.json'
    argv=['watch',*[v for name in ('environment','target','records')for v in ('--'+name,str(paths[name]))],'--prepared',str(tmp_path/'prepared/manifest.json'),'--capture-dir',str(archive),'--output',str(output),'--reader-gid','10010','--gateway-writer-uid','0','--max-delay-ms','1','--required-scopes','asgi_transport','--packet',str(paths['packet']),'--packet-sha256',hashlib.sha256(paths['packet'].read_bytes()).hexdigest(),'--once']
    return paths,output,argv,records,source


def produce(tmp_path):
    paths,output,argv,records,source=files(tmp_path);original=paths['packet'].read_bytes()
    assert cli.main(argv)==0 and paths['packet'].read_bytes()==original
    pin=hashlib.sha256(Path(cli.__file__).read_bytes()).hexdigest()
    feed=NativeReceiptFeed(output,reader_gid=10010,producer_source_sha256=pin)
    with pytest.raises(CollectionFailure)as error:feed.collect()
    assert error.value.last_value['capture_state']=='closed'
    return paths,output,argv,records,source,feed


def test_original_projection_is_delivered_to_existing_nonmock_request_detail(tmp_path):
    paths,output,argv,records,source,feed=produce(tmp_path)
    entry=feed.lookup(records,RID,'runtime-session');assert entry is not None
    expected=project_termination(records,RID,source['packet'],1,['asgi_transport'],source_binding=source['transport']['binding'],source_data_origin='EXAMPLE')
    assert entry['projection']==expected and not entry['projection']['actual_engine_execution_verified']
    gateway=JsonlStore(tmp_path/'gateway.jsonl');engine=JsonlStore(tmp_path/'engine.jsonl')
    for row in records:gateway.append(row)
    query=QueryService(gateway,engine,lambda:{},'runtime-session',native_receipt_lookup=feed.lookup)
    detail=query.detail(RID,lambda _:pytest.fail('nonmock must not call mock engine'),'runtime-session')
    assert detail['engine_receipts']==[entry] and detail['evidence_state']=='unknown'
    assert detail['engine_live']['evidence_kind']=='source_qualified_native_history' and detail['engine_live']['status']=='not_available'
    assert output.stat().st_uid==0 and output.stat().st_gid==10010 and output.stat().st_mode&0o777==0o640


@pytest.mark.parametrize('change',['wrong-session','wrong-run','wrong-policy','wrong-bundle','wrong-pod','wrong-cid','wrong-request'])
def test_context_replay_cannot_attach_to_another_request(tmp_path,change):
    _,_,_,records,_,feed=produce(tmp_path);rows=deepcopy(records)
    if change=='wrong-session':
        assert feed.lookup(rows,RID,'another-session')['capture_state']=='closed';return
    for row in rows:
        if change=='wrong-run':row['run_id']='other'
        elif change=='wrong-policy':row['policy']['sha256']='9'*64
        elif change=='wrong-bundle':row['policy_bundle']['sha256']='9'*64
        elif change=='wrong-pod':row['target']['pod_uid']='other'
        elif change=='wrong-cid':row['target']['container_id']='other'
        elif change=='wrong-request':row['request_id']='e'*32
    assert feed.lookup(rows,RID,'runtime-session')is None


@pytest.mark.parametrize('change',['stale','future','bad-hash','bad-source','bad-uuid','producer-error','nonfinite','duplicate-key','mode','symlink'])
def test_corrupt_or_stale_producer_loses_receipts(tmp_path,change):
    _,output,_,records,_,feed=produce(tmp_path);doc=json.loads(output.read_bytes())
    if change=='stale':doc['generated_at']=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
    elif change=='future':doc['generated_at']=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()
    elif change=='bad-hash':doc['entries'][0]['projection']['native_termination_verified']=False
    elif change=='bad-source':doc['producer_source_sha256']='0'*64
    elif change=='bad-uuid':doc['entries'][0]['request_id']='fake'
    elif change=='producer-error':doc['state']='unavailable'
    output.write_bytes(canonical(doc))
    if change=='nonfinite':output.write_text('{"value":NaN}')
    elif change=='duplicate-key':output.write_text('{"state":"ok","state":"ok"}')
    elif change=='mode':output.chmod(0o666)
    elif change=='symlink':other=tmp_path/'other';other.write_bytes(output.read_bytes());output.unlink();output.symlink_to(other)
    with pytest.raises(CollectionFailure)as rejected:feed.collect()
    assert rejected.value.last_value['capture_fresh']is False
    assert rejected.value.last_value['future_observation_available']is False
    assert feed.lookup(records,RID,'runtime-session')['capture_state']=='closed'
    fresh=NativeReceiptFeed(output,reader_gid=10010,producer_source_sha256=feed.source_sha)
    with pytest.raises(CollectionFailure):fresh.collect()
    recovered=fresh.lookup(records,RID,'runtime-session')
    assert (recovered is not None and recovered['capture_state']=='closed')if change=='stale'else recovered is None


def test_hash_mismatched_original_capture_does_not_generate_receipt(tmp_path):
    paths,output,argv,records,_=files(tmp_path);paths['packet'].write_text('{}')
    assert cli.main(argv)==0
    doc=json.loads(output.read_bytes());assert doc['state']=='unavailable' and doc['entries']==[]


def test_unsealed_original_observations_keep_all_completion_flags_unknown(tmp_path):
    paths,output,argv,records,source=files(tmp_path)
    source['packet']['before']['metadata']['sealed']=False;source['packet']['after']['metadata']['sealed']=False
    for recording in source['packet']['recordings']:
        capture=recording['capture'];capture['summary']['recording_closed']=False;capture['summary']['recording_end']=None;capture['records']=capture['records'][:-1]
    # Invoke the original public projector on the live packet, not a summary stub.
    proof=project_termination(records,RID,source['packet'],1,['asgi_transport'],source_binding=source['transport']['binding'],source_data_origin='EXAMPLE')
    assert proof['native_projection']['binding_observed']
    assert not proof['native_termination_verified'] and not proof['native_projection']['whole_scope_complete']
    assert all(not r['termination_verified']for r in proof['native_projection']['scope_status'])
    source['session_sealed']=False
    paths['packet'].write_bytes(canonical(source))
    # Continuous route must not accept an imported unsealed artifact as a sealed one.
    argv[argv.index('--packet-sha256')+1]=hashlib.sha256(paths['packet'].read_bytes()).hexdigest()
    cli.main(argv);assert json.loads(output.read_bytes())['state']=='unavailable'


def test_real_background_collector_and_platform_request_endpoint(tmp_path):
    from urllib.request import urlopen,Request
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.fake_backend import FakeBackend
    from serving_runtime.mvp.config import MVPConfig
    from serving_runtime.mvp.policy import load_policy
    from fastapi.testclient import TestClient
    from byul_platform.main import create_app
    from byul_platform.config import Settings
    paths,output,argv,records,source=files(tmp_path)
    backend=FakeBackend(port=0).start();gateway=None
    try:
        backend_path=tmp_path/'backend.json'
        backend_doc=json.loads((ROOT/'runtime/examples/mvp/backend-vllm.example.json').read_text());backend_doc['base_url']=backend.base_url;backend_doc['metrics_url']=backend.base_url+'/metrics';backend_doc['model_map']={'test-model':'fake-vllm-model'}
        backend_path.write_text(json.dumps(backend_doc));policy=ROOT/'runtime/examples/mvp/policy.json'
        pin=hashlib.sha256(Path(cli.__file__).read_bytes()).hexdigest()
        config=MVPConfig(gateway_port=0,backend='vllm',backend_config_path=str(backend_path),policy_path=str(policy),data_dir=str(tmp_path/'gateway'),collector_interval_s=.05,native_receipt_feed_path=str(output),native_receipt_reader_gid=10010,native_receipt_producer_sha256=pin)
        gateway=GatewayApp(config,load_policy(policy)).start();base='http://%s:%s'%gateway.address
        # Unavailable native observation does not introduce an inference gate.
        with urlopen(Request(base+'/v1/chat/completions',data=json.dumps({'model':'test-model','messages':[{'role':'user','content':'EXAMPLE'}],'max_tokens':1}).encode(),headers={'Content-Type':'application/json'}),timeout=3)as reply:
            assert reply.status==200
            normal_id=reply.headers['X-Request-ID']
        for row in records:row['observation_session_id']=gateway.observation_session_id;gateway._records.store.append(row)
        argv[argv.index('--records')+1]=str(gateway._records.store.path)
        cli.main(argv)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            with urlopen(base+'/api/requests/'+RID)as reply:detail=json.load(reply)
            if detail['engine_receipts']:break
            time.sleep(.05)
        assert len(detail['engine_receipts'])==1 and detail['evidence_state']=='unknown'
        with TestClient(create_app(Settings(runtime_base_url=base,web_dist=tmp_path/'none',platform_data_dir=tmp_path/'platform')))as platform:
            response=platform.get('/api/runtime/requests/'+RID)
            assert response.status_code==200
            body=response.json();assert body['detail_invalid']is None
            assert body['detail']['engine_receipts']['count']==1
            assert body['detail']['native']['engine_receipts'][0]['projection']['source_data_origin']=='EXAMPLE'
            status_response=platform.get('/api/runtime/status').json()
            assert status_response['invalid']is None
            observer=next(c for c in status_response['item']['collectors']if c['source']=='native_receipt_observer')
            assert observer['detail']['capture_state']=='closed'and observer['detail']['capture_fresh']is False
        with urlopen(base+'/api/requests/'+normal_id)as reply:normal_detail=json.load(reply)
        assert normal_detail['engine_receipts']==[]
        doc=json.loads(output.read_bytes());doc['state']='unavailable';output.write_bytes(canonical(doc))
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            with urlopen(base+'/api/requests/'+RID)as reply:detail=json.load(reply)
            if detail['engine_live']['evidence_kind']=='source_qualified_native_history':break
            time.sleep(.05)
        assert len(detail['engine_receipts'])==1 and detail['engine_live']['status']=='not_available'
        native_status=next(c for c in gateway.collectors_status()['collectors']if c['source']=='native_receipt_observer')
        assert native_status['state']=='error'
    finally:
        if gateway:gateway.stop()
        backend.stop()


def test_os_reader_can_read_explicit_group_but_cannot_write(tmp_path):
    import shutil,uuid
    _,output,_,_,_,_=produce(tmp_path)
    directory=Path('/tmp')/('native-reader-'+uuid.uuid4().hex);directory.mkdir(mode=0o750);os.chown(directory,0,10010)
    path=directory/'feed.json';shutil.copyfile(output,path);os.chown(path,0,10010);path.chmod(0o640)
    try:
        code="from serving_runtime.mvp.native_receipts import read_host_feed; import sys; assert read_host_feed(sys.argv[1],10010)['state']=='ok'; exec(\"try:\\n open(sys.argv[1],'a')\\nexcept PermissionError: pass\\nelse: raise AssertionError('reader wrote root proof')\")"
        result=subprocess.run([sys.executable,'-B','-c',code,str(path)],user=2000,group=10010,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        path.chmod(0o660)
        with pytest.raises(PermissionError):read_host_feed(path,10010)
    finally:shutil.rmtree(directory)


def test_continuous_capture_route_never_seals_and_bounds_live_snapshots(tmp_path,monkeypatch):
    paths,output,argv,records,source=files(tmp_path)
    source['packet']['before']['metadata']['sealed']=False;source['packet']['after']['metadata']['sealed']=False
    for r in source['packet']['recordings']:
        r['capture']['summary']['recording_closed']=False;r['capture']['summary']['recording_end']=None;r['capture']['records']=r['capture']['records'][:-1]
    for flag in ('--packet-sha256','--packet'):
        i=argv.index(flag);del argv[i:i+2]
    original_main=cli.main;calls=[]
    def capture_boundary(command):
        # EXAMPLE replaces only the remote capture boundary; watch/projector,
        # owned output, consumer, and exact request join remain production code.
        assert command[0]=='capture' and '--seal' not in command
        calls.append(command);source['safe_cycle']=len(calls)
        Path(command[command.index('--output')+1]).write_bytes(canonical(source))
        return 0
    monkeypatch.setattr(cli,'main',capture_boundary)
    for _ in range(5):assert original_main(argv)==0
    doc=json.loads(output.read_bytes());assert doc['state']=='ok' and len(doc['entries'])==1
    assert not doc['entries'][0]['session_sealed']
    assert not doc['entries'][0]['projection']['native_termination_verified']
    assert len(list((tmp_path/'captures').rglob('live-*.json')))==2
    pin=hashlib.sha256(Path(cli.__file__).read_bytes()).hexdigest()
    feed=NativeReceiptFeed(output,reader_gid=10010,producer_source_sha256=pin);feed.collect()
    assert feed.lookup(records,RID,'runtime-session') is not None
    assert feed.lookup(records,RID,'another-session') is None
    from serving_runtime.mvp.collectors import Collector
    collector=Collector('native_receipt_observer',feed.collect);collector.collect()
    assert collector.snapshot()['state']=='partial'
    assert collector.snapshot()['last_value']['future_observation_available']is None


def test_example_capture_cannot_be_relabelled_measured(tmp_path):
    paths,output,argv,_,source=files(tmp_path);source['data_origin']='MEASURED';paths['packet'].write_bytes(canonical(source))
    argv[argv.index('--packet-sha256')+1]=hashlib.sha256(paths['packet'].read_bytes()).hexdigest()
    cli.main(argv);assert json.loads(output.read_bytes())['state']=='unavailable'


def test_gateway_template_pins_existing_host_producer_and_reads_observation_mount():
    import yaml
    spec=yaml.safe_load((ROOT/'runtime/deploy/gpu-lab/gateway.yaml').read_text())['spec']['template']['spec']
    gateway=next(c for c in spec['containers']if c['name']=='gateway');args=gateway['args']
    assert args[args.index('--native-receipt-producer-sha256')+1]==hashlib.sha256(Path(cli.__file__).read_bytes()).hexdigest()
    assert args[args.index('--native-receipt-feed')+1]=='/srv/serving/observation/native/receipts.json'
    assert next(m for m in gateway['volumeMounts']if m['name']=='observation')['readOnly']is True
    assert gateway['securityContext']['readOnlyRootFilesystem']is True


def test_sglang_original_anchor_shape_connects_without_inventing_vllm_fields(tmp_path):
    paths,output,argv,records,source=files(tmp_path,backend='sglang')
    argv[argv.index('--max-delay-ms')+1]='1000'
    cli.main(argv);pin=hashlib.sha256(Path(cli.__file__).read_bytes()).hexdigest()
    feed=NativeReceiptFeed(output,reader_gid=10010,producer_source_sha256=pin)
    with pytest.raises(CollectionFailure):feed.collect()
    entry=feed.lookup(records,RID,'runtime-session');assert entry is not None
    native=entry['projection']['native_projection']
    assert native['binding_observed'] and 'physical_process_binding_verified'not in native['anchor']
    assert 'asgi_request_sha256'not in native['anchor']
    original=project_termination(records,RID,source['packet'],1000,['asgi_transport'],source_binding=source['transport']['binding'],source_data_origin='EXAMPLE')
    assert entry['projection']==original
    assert original['source_data_origin']=='EXAMPLE'and original['actual_engine_execution_verified']is False


def test_gateway_module_remains_stdlib_only():
    code="import sys;sys.path.insert(0,sys.argv[1]);import serving_runtime.mvp.app;assert not any(n=='guard'or n.startswith('guard.')for n in sys.modules)"
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT/'runtime/src')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_protected_journal_tail_keeps_complete_rows_only(tmp_path):
    from serving_runtime.mvp.native_receipts import read_protected
    path=tmp_path/'rows.jsonl';path.write_bytes(b'{"old":"'+b'a'*100+b'"}\n{"new":1}\n');path.chmod(0o600)
    raw=read_protected(path,10010,owner_uid=0,limit=30,tail=True)
    assert raw==b'{"new":1}\n'


def test_closed_republication_preserves_history_but_never_refreshes_native_monitoring(tmp_path):
    _,output,argv,records,_,feed=produce(tmp_path)
    first=json.loads(output.read_bytes());entry=feed.lookup(records,RID,'runtime-session')
    time.sleep(.01);cli.main(argv)
    second=json.loads(output.read_bytes())
    assert second['generated_at']!=first['generated_at']
    assert second['capture_observed_at']==first['capture_observed_at']==None
    assert second['source_event_times']==first['source_event_times']
    from serving_runtime.mvp.collectors import Collector
    collector=Collector('native_receipt_observer',feed.collect);collector.collect();status=collector.snapshot()
    assert status['state']=='error' and status['data_origin']=='synthetic'
    detail=status['last_value'];assert detail['producer_fresh']is True and detail['capture_fresh']is False
    assert detail['future_observation_available']is False and detail['capture_state']=='closed'
    assert feed.lookup(records,RID,'new-gateway-session')==entry
    # The existing Platform status adapter preserves the split under its contract.
    from byul_platform.sources.runtime import _collector_detail
    from byul_platform.contracts import Contracts
    api=_collector_detail(status)
    assert api['capture_state']=='closed'and api['source_event_freshness']=='unknown'
    # Resolve timestamp refs via the full normal status route in the HTTP test;
    # this assertion verifies every raw producer/API field is preserved.
    assert api['source_event_times']==detail['source_event_times']


@pytest.mark.parametrize('ending,state',[('BOUND_REACHED','bound_reached'),('ATEXIT','stopped')])
def test_ended_writer_is_not_a_current_monitoring_source(tmp_path,monkeypatch,ending,state):
    _,output,argv,records,source=files(tmp_path)
    source['packet']['before']['metadata']['sealed']=False;source['packet']['after']['metadata']['sealed']=False
    capture=source['packet']['recordings'][0]['capture'];capture['summary']['recording_end']=ending;capture['records'][-1]['payload']={'reason':ending}
    for flag in ('--packet-sha256','--packet'):
        i=argv.index(flag);del argv[i:i+2]
    original=cli.main
    def boundary(command):
        assert '--seal'not in command
        Path(command[command.index('--output')+1]).write_bytes(canonical(source));return 0
    monkeypatch.setattr(cli,'main',boundary);original(argv)
    doc=json.loads(output.read_bytes());assert doc['capture_state']==state
    feed=NativeReceiptFeed(output,reader_gid=10010,producer_source_sha256=hashlib.sha256(Path(cli.__file__).read_bytes()).hexdigest())
    with pytest.raises(CollectionFailure)as error:feed.collect()
    assert error.value.last_value['future_observation_available']is False
    assert feed.lookup(records,RID,'runtime-session')is None


@pytest.mark.parametrize('kind',['symlink','fifo'])
def test_sealed_import_is_nonblocking_and_never_follows_symlink(tmp_path,kind):
    paths,output,argv,_,_=files(tmp_path)
    if kind=='symlink':
        other=tmp_path/'original.json';paths['packet'].rename(other);paths['packet'].symlink_to(other)
    else:
        paths['packet'].unlink();os.mkfifo(paths['packet'],0o600)
    started=time.monotonic();cli.main(argv)
    assert time.monotonic()-started<2
    assert json.loads(output.read_bytes())['state']=='unavailable'
