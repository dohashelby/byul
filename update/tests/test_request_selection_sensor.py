"""EXAMPLE exact native selection bodies; no engine, credential or network use."""
import ast
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import ModuleType,SimpleNamespace

import pytest

from guard.adapters import vllm_lifecycle_sensor as sensor,vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_route_authentication as indexes
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory,RecordingObserver,CANARY

FIXTURES=Path(__file__).parent/'fixtures'
BASE='vllm.entrypoints.generate.base.serving'
ENGINE='vllm.entrypoints.serve.engine.serving'
INPUT='vllm.v1.engine.input_processor'


def sha(value):return hashlib.sha256(value.encode()).hexdigest()


def native_module(observer,module,owner,names):
    rows=json.loads((FIXTURES/'request-selection-native-methods.json').read_text())
    namespace=ModuleType(module)
    cls=type(owner,(),{})
    namespace.__dict__.update({owner:cls,'SESSION_ID_HEADER':'X-Session-ID','PRIORITY_HEADER':'X-Vllm-Priority',
        'random_uuid':lambda:'abcdef0123456789','envs':SimpleNamespace(VLLM_DISABLE_REQUEST_ID_RANDOMIZATION=False)})
    for name in names:
        row=rows[owner+'.'+name]
        assert row['module']==module and row['owner_class']==owner and row['license']=='Apache-2.0'
        assert row['full_source_sha256']==sensor.SOURCE_SHA[module]
        node=ast.parse(row['method']).body[0];node.decorator_list=[]
        tree=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
        exec(compile(ast.fix_missing_locations(tree),'reviewed-native-selection-body','exec'),namespace.__dict__)
        setattr(cls,name,staticmethod(namespace.__dict__[name]))
    observer.instrument(namespace)
    return cls


def setup(case,writer_factory,monkeypatch):
    observer=RecordingObserver()
    writer,fake=writer_factory(session=case.prepared['session'],settings_sha=case.prepared['assets']['settings.json']['sha256'])
    monkeypatch.setattr(observer,'emit',lambda event,payload:writer.write(event,payload))
    for module in (BASE,ENGINE,INPUT):
        writer.write('MODULE',{'module':module,'verified':True,'source_sha256':sensor.SOURCE_SHA[module],'code_sha256':'c'*64})
    base=native_module(observer,BASE,'GenerateBaseServing',sensor.POINTS[BASE]['GenerateBaseServing'])
    engine=native_module(observer,ENGINE,'BaseServing',('_base_request_id',))
    processor=native_module(observer,INPUT,'InputProcessor',('assign_request_id',))
    monkeypatch.setattr(sensor,'_OBSERVER',observer)
    return SimpleNamespace(observer=observer,writer=writer,fake=fake,base=base,engine=engine,processor=processor)


def packet(world,case):
    world.writer.close('SEALED')
    raw=bytes(world.fake.data)
    rows,summary=capture.parse_recording(raw,case.prepared)
    indexed=indexes.selection_index([{'capture':{'records':rows,'provenance':{'owner':{'container_id':'a'*64}}}}])
    return raw,rows,summary,indexed


def run_selection(world,*,headers=(),body_priority=3,body_session=None,xargs=None,path='/v1/completions'):
    reads=[];messages=[];output={}
    class Headers:
        def get(self,key):
            reads.append(key)
            assert key.lower()!='authorization'
            return next((v for k,v in headers if k.lower()==key.lower()),None)
    body=SimpleNamespace(priority=body_priority,session_id=body_session,vllm_xargs=xargs)
    raw=SimpleNamespace(headers=Headers())
    async def app(scope,receive,send):
        output.update(priority=world.base._get_priority(body,raw),session=world.base._get_session_id(body,raw),
                      rank=world.base._get_data_parallel_rank(raw),request_id=world.engine._base_request_id(raw,'EXAMPLE-default'))
        handoff=SimpleNamespace(priority=output['priority'],session_id=output['session'],data_parallel_rank=output['rank'],
                                request_id=output['request_id'],external_req_id=None)
        assert world.processor.assign_request_id(handoff) is None
        output['internal_request_id']=handoff.request_id
        await send({'type':'http.response.start','status':200})
        await send({'type':'http.response.body','body':b'EXAMPLE-not-observed'})
    async def receive():raise AssertionError('observer must not add receives')
    async def send(message):messages.append(message)
    scope={'type':'http','method':'POST','path':path,'root_path':'',
           'headers':[(k.lower().encode(),str(v).encode()) for k,v in headers]+[(b'authorization',CANARY.encode())]}
    asyncio.run(sensor.Middleware(app)(scope,receive,send))
    expected=['X-Vllm-Priority']+([] if body_session else ['X-Session-ID'])+['X-data-parallel-rank','X-Request-Id']
    assert len(messages)==2 and reads==expected
    return output


def selected(rows,point):
    return [r['payload']['metadata']['request_selection'] for r in rows if r['event']==('SELECTION' if point=='InputProcessor.assign_request_id' else 'NATIVE')
            and r['payload']['point']==point and r['payload']['phase']=='RETURN']


@pytest.mark.parametrize('header,expected',[('7',7),('-9',-9),('invalid',3),('',3),(None,3)])
def test_exact_priority_precedence_and_input_handoff(case,writer_factory,monkeypatch,header,expected):
    world=setup(case,writer_factory,monkeypatch)
    headers=[] if header is None else [('X-Vllm-Priority',header)]
    output=run_selection(world,headers=headers)
    raw,rows,_,index=packet(world,case)
    assert output['priority']==expected
    assert selected(rows,'GenerateBaseServing._get_priority')[0]['selected_priority']==expected
    assert selected(rows,'InputProcessor.assign_request_id')[0]['selected_priority']==expected
    assert len(index['invocations'])==1 and index['events'] and index['stored_index_complete']
    assert next(iter(index['invocations'].values()))['asgi_request_sha256'] is None
    assert CANARY not in raw.decode() and index['security']=='UNKNOWN' and not index['authorization_verified']


@pytest.mark.parametrize('body,header,xargs,expected',[
    ('EXAMPLE-body','EXAMPLE-header',{'session_id':'EXAMPLE-extra'},'EXAMPLE-body'),
    (None,'EXAMPLE-header',{'session_id':'EXAMPLE-extra'},'EXAMPLE-header'),
    (None,'',{'session_id':'EXAMPLE-extra'},'EXAMPLE-extra'),(None,None,None,None),
    (None,None,{'session_id':17},None)])
def test_exact_session_precedence_and_no_original_text(case,writer_factory,monkeypatch,body,header,xargs,expected):
    world=setup(case,writer_factory,monkeypatch)
    output=run_selection(world,headers=[] if header is None else [('X-Session-ID',header)],body_session=body,xargs=xargs)
    raw,rows,_,_=packet(world,case)
    assert output['session']==expected
    for point in ('GenerateBaseServing._get_session_id','InputProcessor.assign_request_id'):
        value=selected(rows,point)[0]
        assert value['session_observed'] and value['selected_session_sha256']==(sha(expected) if expected is not None else None)
    assert all(text not in raw.decode() for text in ('EXAMPLE-body','EXAMPLE-header','EXAMPLE-extra'))


@pytest.mark.parametrize('header,expected',[('2',2),('-1',-1),('invalid',None),(None,None)])
def test_rank_integer_or_none_is_observed_without_authorizing_it(case,writer_factory,monkeypatch,header,expected):
    world=setup(case,writer_factory,monkeypatch)
    output=run_selection(world,headers=[] if header is None else [('X-data-parallel-rank',header)])
    _,rows,_,index=packet(world,case)
    assert output['rank']==expected
    value=selected(rows,'GenerateBaseServing._get_data_parallel_rank')[0]
    assert value['rank_observed'] and value['selected_rank']==expected
    assert not index['principal_identity_verified'] and not index['worker_consumption_verified']


@pytest.mark.parametrize('header,expected',[('EXAMPLE-api-id','EXAMPLE-api-id'),('', ''),(None,'EXAMPLE-default')])
def test_request_id_precedence_and_native_assignment_mutation_are_preserved(case,writer_factory,monkeypatch,header,expected):
    world=setup(case,writer_factory,monkeypatch)
    output=run_selection(world,headers=[] if header is None else [('X-Request-Id',header)])
    raw,rows,_,_=packet(world,case)
    assert output['request_id']==expected and output['internal_request_id']==expected+'-abcdef01'
    assert selected(rows,'BaseServing._base_request_id')[0]['selected_request_id_sha256']==sha(expected)
    assert selected(rows,'InputProcessor.assign_request_id')[0]['selected_request_id_sha256']==sha(output['internal_request_id'])
    assert expected=='' or expected not in raw.decode()


@pytest.mark.parametrize('value',[True,2**80])
def test_unsupported_native_priority_is_unchanged_but_not_fabricated(case,writer_factory,monkeypatch,value):
    world=setup(case,writer_factory,monkeypatch)
    output=run_selection(world,body_priority=value)
    _,rows,_,_=packet(world,case)
    assert output['priority'] is value
    metadata=selected(rows,'GenerateBaseServing._get_priority')[0]
    assert not metadata['priority_observed'] and metadata['selected_priority'] is None


def test_outside_http_and_closed_inherited_context_do_not_gain_selection(case,writer_factory,monkeypatch):
    world=setup(case,writer_factory,monkeypatch)
    body=SimpleNamespace(priority=4)
    assert world.base._get_priority(body,None)==4
    async def exercise():
        gate=asyncio.Event()
        async def late():
            await gate.wait()
            assert world.base._get_priority(body,None)==4
        tasks=[]
        async def app(scope,receive,send):tasks.append(asyncio.create_task(late()))
        await sensor.Middleware(app)({'type':'http','method':'GET','path':'/health','headers':[]},None,None)
        gate.set();await tasks[0]
    asyncio.run(exercise())
    _,rows,_,index=packet(world,case)
    assert not any(r['event']=='NATIVE' for r in rows) and index['events']==[]


def test_native_exception_and_unrelated_body_properties_are_preserved(case,writer_factory,monkeypatch):
    world=setup(case,writer_factory,monkeypatch)
    error=RuntimeError(CANARY)
    class Request:
        @property
        def priority(self):raise error
        @property
        def messages(self):raise AssertionError('observer must not read body properties')
    async def app(scope,receive,send):
        with pytest.raises(RuntimeError) as caught:world.base._get_priority(Request(),None)
        assert caught.value is error
    asyncio.run(sensor.Middleware(app)({'type':'http','method':'GET','path':'/health','headers':[]},None,None))
    raw,rows,_,_=packet(world,case)
    assert CANARY not in raw.decode()
    assert [r['payload']['phase'] for r in rows if r['event']=='NATIVE']==['CALL','RAISE']


def test_return_after_context_closed_preserves_the_prior_call(case,writer_factory,monkeypatch):
    world=setup(case,writer_factory,monkeypatch)
    class Headers:
        def get(self,key):
            sensor._ROUTE_REQUEST.get()['closed']=True
            return '6'
    async def app(scope,receive,send):assert world.base._get_priority(None,SimpleNamespace(headers=Headers()))==6
    asyncio.run(sensor.Middleware(app)({'type':'http','method':'POST','path':'/v1/completions','headers':[]},None,None))
    _,rows,_,index=packet(world,case)
    native=[r for r in rows if r['event']=='NATIVE']
    assert len(native)==2 and 'request_selection' in native[0]['payload']['metadata']
    assert native[1]['payload']['metadata']=={} and native[1]['payload']['diagnostic']=='SAMPLE_UNAVAILABLE'
    assert len(index['events'])==1 and not index['stored_index_complete']


@pytest.mark.parametrize('damage',['bool','hash','unobserved','call','point','negative-clock'])
def test_resealed_invalid_metadata_does_not_pass_the_capture_boundary(case,writer_factory,monkeypatch,damage):
    world=setup(case,writer_factory,monkeypatch);run_selection(world)
    raw,_,_,_=packet(world,case)
    def change(rows):
        row=next(r for r in rows if r['event']=='NATIVE' and r['payload']['point']=='GenerateBaseServing._get_priority'
                 and r['payload']['phase']=='RETURN')
        value=row['payload']['metadata']['request_selection']
        if damage=='bool':value['selected_priority']=True
        elif damage=='hash':value['asgi_invocation_sha256']='bad'
        elif damage=='unobserved':value['priority_observed']=False
        elif damage=='call':row['payload']['phase']='CALL'
        elif damage=='point':row['payload']['point']='GenerateBaseServing._get_data_parallel_rank'
        else:row['monotonic_ns']=-1
    with pytest.raises(ValueError):capture.parse_recording(revised(raw,change),case.prepared)


def frozen5(case,recording,crlf=False):
    raw=(FIXTURES/'lifecycle-sensor-v5.py.txt').read_bytes()
    if crlf:raw=raw.replace(b'\n',b'\r\n')
    root=case.root/'assets';manifest=deepcopy(case.prepared)
    previous=manifest['assets']['guard_lifecycle_sensor.py']['sha256']
    (root/'guard_lifecycle_sensor.py').write_bytes(raw)
    (root/'sitecustomize.py').write_bytes((root/'sitecustomize.py').read_bytes().replace(previous.encode(),hashlib.sha256(raw).hexdigest().encode()))
    manifest.update(profile=profiles.V5_PROFILE,source_sha256=profiles.V5_SOURCE_SHA)
    for name in ('guard_lifecycle_sensor.py','sitecustomize.py'):
        data=(root/name).read_bytes();manifest['assets'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    case.manifest_path.write_text(json.dumps(manifest))
    def change(rows):rows[0]['payload'].update(profile=profiles.V5_PROFILE,schema=profiles.V5_SCHEMA,sensor_sha256=hashlib.sha256(raw).hexdigest())
    return manifest,revised(recording,change)


@pytest.mark.parametrize('crlf',[False,True])
def test_frozen_profile5_asset_keeps_its_old_sources_points_and_route_meaning(case,writer_factory,monkeypatch,crlf):
    from tests.test_route_auth_sensor import begin,application,invoke,scope
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch)
    asyncio.run(invoke(app,scope()));writer.close('SEALED')
    manifest,old=frozen5(case,bytes(fake.data),crlf)
    assert lifecycle.load_prepared(case.manifest_path)==manifest
    rows,_=capture.parse_recording(old,manifest)
    index=indexes.index([{'capture':{'records':rows,'provenance':{'owner':{'container_id':'a'*64}}}}])
    assert index['events'] and any(row['metadata']['token_verified'] is True for row in index['events'])
    assert len(profiles.V5_SOURCE_SHA)==23 and not sensor.REQUEST_SELECTION_POINTS.intersection(profiles.point_sources(profiles.V5_PROFILE))
    assert profiles.valid_call_frame({'point':'DefaultModelLoader.load_weights','request_sha256':[],
        'weight_call_sha256':'a'*64,'weight_model_sha256':None},profiles.V5_PROFILE)


def test_frozen_profile5_rejects_new_selection_fields_but_keeps_old_assignment(case,writer_factory,monkeypatch):
    world=setup(case,writer_factory,monkeypatch);run_selection(world)
    _,rows,_,_=packet(world,case)
    payload=deepcopy(next(r['payload'] for r in rows if r['event']=='SELECTION'))
    payload.update(observation_started_ns=0,observation_finished_ns=0,call_chain=[])
    manifest,old=frozen5(case,case.raw)
    def inject(rows):rows.insert(-1,{**rows[-1],'event':'NATIVE','payload':payload})
    with pytest.raises(ValueError):capture.parse_recording(revised(old,inject),manifest)
    payload['metadata']={}
    capture.parse_recording(revised(old,inject),manifest)


@pytest.mark.parametrize('with_header',[False,True])
def test_separate_invocations_and_header_reuse_ambiguity_are_preserved(case,writer_factory,monkeypatch,with_header):
    world=setup(case,writer_factory,monkeypatch)
    headers=[('X-Request-Id','EXAMPLE-reused')] if with_header else []
    run_selection(world,headers=headers);run_selection(world,headers=headers)
    _,_,_,index=packet(world,case)
    assert len(index['invocations'])==2
    assert index['ambiguous_asgi_request_sha256']==([sha('EXAMPLE-reused')] if with_header else [])


@pytest.mark.parametrize('boundary',['non-http','pid'])
def test_nested_context_never_crosses_http_or_process_boundary(case,writer_factory,monkeypatch,boundary):
    world=setup(case,writer_factory,monkeypatch)
    body=SimpleNamespace(priority=4)
    async def nested(scope,receive,send):assert world.base._get_priority(body,None)==4
    async def outer(scope,receive,send):
        assert world.base._get_priority(body,None)==4
        if boundary=='non-http':await sensor.Middleware(nested)({'type':'websocket'},None,None)
        else:
            with monkeypatch.context() as patch:
                patch.setattr(sensor.os,'getpid',lambda:43)
                await nested(None,None,None)
        assert world.base._get_priority(body,None)==4
    asyncio.run(sensor.Middleware(outer)({'type':'http','method':'GET','path':'/health','headers':[]},None,None))
    _,rows,_,index=packet(world,case)
    assert len([r for r in rows if r['event']=='NATIVE'])==4 and len(index['invocations'])==1


@pytest.mark.parametrize('size',[512,513])
def test_session_text_bound_does_not_truncate_native_selection(case,writer_factory,monkeypatch,size):
    world=setup(case,writer_factory,monkeypatch)
    value='s'*size
    assert run_selection(world,body_session=value)['session'] is value
    raw,rows,_,_=packet(world,case)
    selected_value=selected(rows,'GenerateBaseServing._get_session_id')[0]
    assert selected_value['session_observed'] is (size<=512)
    assert selected_value['selected_session_sha256']==(sha(value) if size<=512 else None)
    assert value not in raw.decode()


def test_index_bound_retains_previous_events(case,writer_factory,monkeypatch):
    world=setup(case,writer_factory,monkeypatch);run_selection(world)
    _,rows,_,full=packet(world,case)
    monkeypatch.setattr(indexes,'MAX_EVENTS',2)
    partial=indexes.selection_index([{'capture':{'records':rows,'provenance':{'owner':{'container_id':'a'*64}}}}])
    assert partial['events']==full['events'][:2] and not partial['stored_index_complete']


def test_optional_selection_failure_preserves_existing_assignment(case,writer_factory,monkeypatch):
    from guard.executors.lifecycle_session import request_index
    world=setup(case,writer_factory,monkeypatch)
    def failed(*args,**kwargs):raise RuntimeError(CANARY)
    monkeypatch.setattr(world.observer,'request_selection',failed)
    value=SimpleNamespace(request_id='EXAMPLE-original',external_req_id=None)
    async def app(scope,receive,send):world.processor.assign_request_id(value)
    asyncio.run(sensor.Middleware(app)({'type':'http','method':'POST','path':'/v1/completions',
        'headers':[(b'x-request-id',b'EXAMPLE-header')]},None,None))
    raw,rows,_,index=packet(world,case)
    recording=[{'capture':{'records':rows,'provenance':{'owner':{'container_id':'a'*64}}}}]
    domain=next(iter(request_index(recording)['domains'].values()))
    assert domain['assignments'] and value.request_id=='EXAMPLE-original-abcdef01'
    assert domain['assignments'][0]['asgi_request_sha256']==sha('EXAMPLE-header')
    assert len([r for r in rows if r['event']=='SELECTION' and r['payload'].get('diagnostic')=='SAMPLE_UNAVAILABLE'])==2
    assert all('request_selection' not in r['payload']['metadata'] and 'diagnostic' not in r['payload']
               for r in rows if r['event']=='NATIVE')
    assert index['events']==[] and not index['stored_index_complete'] and CANARY not in raw.decode()


@pytest.mark.parametrize('damage',['native','wrong-point','requests'])
def test_input_selection_is_a_separate_typed_event(case,writer_factory,monkeypatch,damage):
    world=setup(case,writer_factory,monkeypatch);run_selection(world)
    raw,_,_,_=packet(world,case)
    def change(rows):
        row=next(r for r in rows if r['event']=='SELECTION')
        if damage=='native':row['event']='NATIVE'
        elif damage=='wrong-point':row['payload']['point']='GenerateBaseServing._get_priority'
        else:row['payload']['requests']=[{'request_sha256':'a'*64,'request_present':True,
            'output_state_present':None,'batch_row_present':None,'status_code':None}]
    with pytest.raises(ValueError):capture.parse_recording(revised(raw,change),case.prepared)


def test_frozen_profile5_cannot_acquire_a_selection_event(case):
    manifest,old=frozen5(case,case.raw)
    payload={'point':'InputProcessor.assign_request_id','phase':'RETURN','source_sha256':sensor.SOURCE_SHA[INPUT],
        'requests':[],'metadata':{},'selection_observed':False,'diagnostic':'SAMPLE_UNAVAILABLE',
        'observation_started_ns':0,'observation_finished_ns':0}
    def change(rows):rows.insert(-1,{**rows[-1],'event':'SELECTION','payload':payload})
    with pytest.raises(ValueError):capture.parse_recording(revised(old,change),manifest)
