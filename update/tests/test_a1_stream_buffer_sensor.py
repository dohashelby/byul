"""Real fixed Uvicorn method bodies; framework/transport I/O remain EXAMPLE."""
import ast,asyncio,copy,hashlib,json,logging,os,threading,time,types
from pathlib import Path
from types import SimpleNamespace
import h11,pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_stream_buffer_sensor as sensor,vllm_stream_buffer_metadata as metadata
from guard.collectors import lifecycle
from guard.executors.lifecycle_capture import parse_recording
from tests.test_lifecycle_sensor import CONTEXT

REPO=Path(__file__).resolve().parents[2]
REFERENCES=Path(__file__).parent/'fixtures/uvicorn-054'
PRIVATE='EXAMPLE_STREAM_BUFFER_PRIVATE'


class Transport:
    def __init__(self):self.raw=[];self.closed=False
    def write(self,value):self.raw.append(value)
    def close(self):self.closed=True
    def is_closing(self):return self.closed


class AnnNames(ast.NodeTransformer):
    def visit_arg(self,node):node.annotation=None;return node
    def visit_FunctionDef(self,node):node.returns=None;return self.generic_visit(node)
    def visit_AsyncFunctionDef(self,node):node.returns=None;return self.generic_visit(node)


def module_from_reference(name):
    path=REFERENCES/(name.rsplit('.',1)[-1]+'.py');raw=path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==sensor.SOURCE_ADDITIONS[name]
    wanted=set(sensor.TARGETS[name]);tree=ast.parse(raw,filename=str(path))
    classes=[copy.deepcopy(n) for n in tree.body if isinstance(n,ast.ClassDef) and n.name in wanted]
    for node in classes:
        if node.name!='RequestResponseCycle' and node.name!='FlowControl':node.body=[n for n in node.body if isinstance(n,ast.FunctionDef) and n.name=='connection_lost']
    future=copy.deepcopy(tree.body[0]);assert isinstance(future,ast.ImportFrom) and future.module=='__future__'
    tree=ast.Module(body=[future,*classes],type_ignores=[]);module=types.ModuleType(name);module.__file__=str(path)
    module.__dict__.update(asyncio=asyncio,h11=h11,logging=logging,CLOSE_HEADER=(b'connection',b'close'),TRACE_LOG_LEVEL=5,
        STATUS_PHRASES={200:b'OK',500:b'Internal Server Error'},cast=lambda _,v:v)
    exec(compile(ast.fix_missing_locations(tree),str(path),'exec',dont_inherit=True,optimize=0),module.__dict__)
    for cls in classes:
        if cls.name.endswith('Protocol'):setattr(vars(module)[cls.name],'_unset_keepalive_if_required',lambda self:None)
    return module


def make_observer(session='a'*32):
    base=types.ModuleType('_stream_private_base');base.__file__=base8.__file__
    exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__)
    sensor.configure(base)
    class Observer(base.Observer):
        def __init__(self):
            super().__init__({'session':session},'b'*64);self.recorded=[]
        def _writer(self):return SimpleNamespace(context={**CONTEXT,'pid':os.getpid()})
        def emit(self,event,payload):self.recorded.append({'event':event,'payload':copy.deepcopy(payload),'monotonic_ns':time.monotonic_ns()})
    observer=Observer();base._OBSERVER=observer;modules={}
    for name in sensor.SOURCE_ADDITIONS:
        module=module_from_reference(name);observer.instrument(module);modules[name]=module
        observer.modules[name]={'source_sha256':sensor.SOURCE_ADDITIONS[name],'code_sha256':'c'*64}
        observer.emit('MODULE',{'module':name,'verified':True,**observer.modules[name]})
    return SimpleNamespace(base=base,observer=observer,modules=modules)


async def invoke(world,rid,*,protocol='h11_impl',failure=None,paused=True):
    module=world.modules['uvicorn.protocols.http.'+protocol];flow=world.modules['uvicorn.protocols.http.flow_control'].FlowControl(Transport())
    transport=flow._transport;conn=h11.Connection(h11.SERVER);conn.receive_data(b'POST /v1/completions HTTP/1.1\r\nHost: example\r\nContent-Length: 0\r\n\r\n')
    conn.next_event();conn.next_event()
    scope={'type':'http','path':'/v1/completions','method':'POST','headers':[(b'x-request-id',rid.encode()),(b'authorization',PRIVATE.encode())],
        'query_string':b'','client':('127.0.0.1',1),'http_version':'1.1'}
    # httptools framing has a different constructor and is covered by code/shape
    # admission separately; this invocation exercises the real h11 bodies.
    cycle=module.RequestResponseCycle(scope,conn,transport,flow,logging.getLogger('owned-example'),logging.getLogger('owned-example'),False,[],asyncio.Event(),lambda:None)
    async def app(s,receive,send):
        await send({'type':'http.response.start','status':200,'headers':[]})
        if paused:
            flow.pause_writing();asyncio.get_running_loop().call_later(.001,flow.resume_writing)
        if failure is not None:
            flow.drain=lambda:raise_async(failure)
        await send({'type':'http.response.body','body':PRIVATE.encode(),'more_body':False})
    async def raise_async(error):raise error
    before=len(world.observer.recorded);await cycle.run_asgi(world.base.Middleware(app))
    cls=module.H11Protocol;parent=object.__new__(cls);parent.__dict__.update(connections=set(),logger=logging.getLogger('owned-example'),cycle=cycle,
        conn=conn,flow=flow,transport=transport,timeout_keep_alive_task=None)
    parent.connection_lost(None)
    return world.observer.recorded[before:]


def test_real_source_checked_send_drain_and_close_have_parent_binding_and_no_content():
    world=make_observer();rows=asyncio.run(invoke(world,'byul-lif04-'+'ab'*16+'-slow'))
    samples=[r['payload'] for r in rows if r['event']=='STREAM_BUFFER']
    assert {'REQUEST_ENTER','SEND_ENTER','SEND_RETURN','DRAIN_ENTER','DRAIN_RETURN','RUN_RETURN','CONNECTION_LOST_RETURN'}<=set(r['phase'] for r in samples)
    for row in samples:metadata.validate_sample(row)
    assert all(r['write_buffer_bytes'] is None and r['transport_kind']=='unsupported' for r in samples)
    drain=next(r for r in samples if r['phase']=='DRAIN_RETURN')
    assert any(r['call_sha256']==drain['parent_call_sha256'] and r['phase']=='SEND_RETURN' for r in samples)
    assert drain['operation_started_ns']<=drain['operation_finished_ns']
    assert PRIVATE not in json.dumps(rows) and all(r['producer_authenticated'] is False for r in [metadata.project(None,[],{'buffer_release_deadline_ms':0})])


@pytest.mark.parametrize('damage',['changed-code','unknown-source','runtime','optimize'])
def test_unapproved_code_source_or_runtime_never_installs_hooks(monkeypatch,damage):
    if damage in {'runtime','optimize'}:
        if damage=='runtime':monkeypatch.setattr(sensor,'RUNTIME_CODE_CONTRACTS',{})
        else:monkeypatch.setattr(sensor,'sys',SimpleNamespace(version_info=sensor.sys.version_info,implementation=sensor.sys.implementation,flags=SimpleNamespace(optimize=1)))
        with pytest.raises(ValueError):make_observer()
        return
    world=make_observer();module=module_from_reference('uvicorn.protocols.http.h11_impl')
    original=module.RequestResponseCycle.run_asgi
    if damage=='changed-code':module.RequestResponseCycle.send=lambda *a:PRIVATE
    else:module.__file__=str(REFERENCES/'flow_control.py')
    with pytest.raises((ValueError,KeyError)):world.observer.instrument(module)
    assert module.RequestResponseCycle.run_asgi is original


def test_late_code_mutation_retains_earlier_records_and_marks_source_unavailable():
    world=make_observer();asyncio.run(invoke(world,'byul-lif04-'+'ab'*16+'-normal'))
    module=world.modules['uvicorn.protocols.http.h11_impl'];wrapped=module.RequestResponseCycle.send;original=wrapped.__wrapped__
    async def changed(*args,**kwargs):return None
    original.__code__=changed.__code__
    async def call():
        owner=object.__new__(module.RequestResponseCycle);await wrapped(owner,{'type':'http.response.body','body':PRIVATE.encode()})
    asyncio.run(call())
    assert any(r['event']=='STREAM_BUFFER' for r in world.observer.recorded)
    assert world.observer.recorded[-1]['payload']=={'module':'uvicorn.protocols.http.h11_impl','verified':False}


@pytest.mark.parametrize('damage',['raw','bool','phase','source','runtime','parent','unsupported-bytes','close-request','clock'])
def test_closed_metadata_rejects_forged_fields_and_relabelled_raw_values(damage):
    world=make_observer();rows=asyncio.run(invoke(world,'byul-lif04-'+'ab'*16+'-slow'));value=copy.deepcopy(next(r['payload'] for r in rows if r['event']=='STREAM_BUFFER' and r['payload']['phase']=='DRAIN_RETURN'))
    if damage=='raw':value['raw']=PRIVATE
    elif damage=='bool':value['body_bytes']=True
    elif damage=='phase':value['phase']='unreviewed'
    elif damage=='source':value['source_sha256']=PRIVATE
    elif damage=='runtime':value['observer_code_sha256']='0'*64
    elif damage=='parent':value['parent_call_sha256']=None
    elif damage=='unsupported-bytes':value['write_buffer_bytes']=1
    elif damage=='close-request':value['request_sha256']=None
    else:value['operation_finished_ns']=value['operation_started_ns']-1
    with pytest.raises(ValueError,match='STREAM_BUFFER_SAMPLE_INVALID'):metadata.validate_sample(value)


def test_native8_and_old_profiles_stay_default_and_source_bytes_unchanged():
    assert base8.PROFILE=='vllm-native-lifecycle-metadata/8'
    assert hashlib.sha256(Path(base8.__file__).read_bytes()).hexdigest()==sensor.BASE8_SHA256
    assert sensor.PROFILE=='vllm-native-lifecycle-metadata/12'


def test_actual_send_exception_and_observer_failure_preserve_original_semantics():
    world=make_observer();module=world.modules['uvicorn.protocols.http.h11_impl']
    async def attempt(disable):
        flow=world.modules['uvicorn.protocols.http.flow_control'].FlowControl(Transport())
        owner=object.__new__(module.RequestResponseCycle)
        owner.__dict__.update(scope={'type':'http','path':'/v1/completions','headers':[(b'x-request-id',('byul-lif04-'+'ab'*16+'-normal').encode())]},
            transport=flow._transport,flow=flow,disconnected=False,response_started=False)
        if disable:
            def broken(*a):raise KeyboardInterrupt(PRIVATE)
            world.observer.emit=broken
        with pytest.raises(RuntimeError,match='Expected ASGI message'):
            await owner.send({'type':'http.response.body','body':PRIVATE.encode()})
    asyncio.run(attempt(False))
    assert any(r['event']=='STREAM_BUFFER' and r['payload']['phase']=='SEND_RAISE' for r in world.observer.recorded)
    asyncio.run(attempt(True));assert world.observer.disabled_pid==os.getpid()


def test_owned_local_selector_socket_samples_buffer_and_kernel_queue_without_privileges():
    if sensor.sys.platform!='linux':
        # Existing unsupported platform contract: no synthetic Linux zeroes.
        assert sensor.transport_metrics(Transport())['kernel_outq_bytes'] is None
        return
    import socket
    async def run():
        selected=[];ready=asyncio.Event();lost=asyncio.Event()
        class Owned(asyncio.Protocol):
            def connection_made(self,t):selected.append(t);ready.set()
            def connection_lost(self,exc):lost.set()
        server=await asyncio.get_running_loop().create_server(Owned,'127.0.0.1',0)
        peer=socket.socket();peer.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096);peer.setblocking(False)
        try:
            await asyncio.wait_for(asyncio.get_running_loop().sock_connect(peer,server.sockets[0].getsockname()),5)
            await asyncio.wait_for(ready.wait(),5);transport,=selected
            transport.write(b'x'*(4*1024**2));before=sensor.transport_metrics(transport)
            assert before['transport_kind']=='asyncio-selector-tcp' and before['write_buffer_bytes']>0
            assert before['kernel_outq_bytes']>=0 and before['socket_inode']>0 and before['socket_identity_stable']
            assert before['process_rss_bytes']>0
            transport.abort();await asyncio.wait_for(lost.wait(),5);after=sensor.transport_metrics(transport)
            assert after['write_buffer_bytes']==0 and after['transport_closing'] is True
            assert after['kernel_outq_bytes'] is None and after['socket_inode'] is None
        finally:
            peer.close()
            for transport in selected:transport.abort()
            server.close();await server.wait_closed()
    asyncio.run(run())
