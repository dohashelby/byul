"""Direct CPU tests of the B request observer's allowed normal path."""

import asyncio
from datetime import datetime, timezone
import functools
from hashlib import sha256
import importlib
from importlib.machinery import ModuleSpec
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from guard.executors.lif05_stages import combine_dispatch


ENGINE_SOURCE = """
class InputProcessor:
    @staticmethod
    def assign_request_id(request):
        request.external_req_id = request.request_id
        if request.fail:
            raise ValueError('native fixture failure')
        request.request_id = request.external_req_id + '-internal'
"""


@pytest.fixture
def context(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(Path(__file__).resolve().parents[2]/'.github/scripts/gpu63')
    trace = importlib.import_module('owned_lif05_request_trace')
    probe = importlib.import_module('owned_lif05_staged_probe')
    image = next(iter(trace.PINS))
    root = tmp_path/'package'
    pins = {}
    for name in trace.PATHS:
        source = root/name
        source.parent.mkdir(parents=True,exist_ok=True)
        raw = ENGINE_SOURCE.encode() if name == trace.INPUT else b'# fixed source\n'
        source.write_bytes(raw)
        pins[name] = sha256(raw).hexdigest()
    monkeypatch.setitem(trace.PINS, image, tuple(pins[name] for name in trace.PATHS))
    source = root/trace.INPUT
    module = ModuleType('vllm.v1.engine.input_processor')
    module.__spec__ = ModuleSpec(module.__name__,None,origin=str(source))
    exec(compile(ENGINE_SOURCE,str(source),'exec',dont_inherit=True,optimize=0),vars(module))
    monkeypatch.setitem(sys.modules,module.__name__,module)
    engine = ModuleType('vllm.v1.engine')

    class EngineCoreRequest:
        __slots__ = ('request_id','external_req_id','fail')

        def __init__(self, identifier, fail):
            self.request_id = identifier
            self.external_req_id = None
            self.fail = fail

    engine.EngineCoreRequest = EngineCoreRequest
    monkeypatch.setitem(sys.modules,engine.__name__,engine)
    meta = {'pod_uid':'engine','image':image,'image_id':image,'container_id':'cid','namespace_uid':'namespace'}
    target = {'kubernetes':{'engine':meta,'probe':{'pod_uid':'probe','pod_ip':'10.0.0.1'}}}
    gen = SimpleNamespace(key='old',prepared={'session':'a'*32},target=target)
    reg = probe.TraceRegistration('ATTEMPT-FIXTURE',gen,tmp_path)
    reg.directory.mkdir()
    sensor = trace.Trace({'scope':reg.scope,'slots':list(reg.slots.values()),'key':reg.key.hex()},
                        reg.directory/trace.JOURNAL)
    yield trace, module, EngineCoreRequest, reg, sensor
    for number in (3,4):
        mon = sys.monitoring
        if mon.get_tool(number) == 'owned-lif05-request-assignment':
            code = importlib.import_module('inspect').unwrap(module.InputProcessor.assign_request_id).__code__
            mon.set_local_events(number,code,0)
            mon.register_callback(number,mon.events.PY_START,None)
            mon.register_callback(number,mon.events.PY_RETURN,None)
            mon.free_tool_id(number)


def measure(context, *, wrong_peer=False, wrong_external=False, failing_call=False, duplicate=False, wrapped=False,
            client_dispatch=True):
    trace, module, engine, reg, sensor = context
    original = module.InputProcessor.assign_request_id
    if wrapped:
        @functools.wraps(original)
        def wrapper(item):
            return original(item)
        module.InputProcessor.assign_request_id = staticmethod(wrapper)
    token = reg.tokens['before']
    scope = {'type':'http','method':'POST','path':'/v1/chat/completions',
             'headers':[(b'x-request-id',token.encode())],
             'client':('10.0.0.2' if wrong_peer else '10.0.0.1',12345)}

    async def app(scope, receive, send):
        request = engine('chatcmpl-' + (token if not wrong_external else 'different-identifier'), failing_call)
        try:
            module.InputProcessor.assign_request_id(request)
        except ValueError:
            assert failing_call
        await send({'type':'http.response.start','status':200})
        await send({'type':'http.response.body','body':b'PRIVATE FIXTURE BODY'})

    async def send(message):
        return message['type']

    before = datetime.now(timezone.utc).isoformat()
    asyncio.run(sensor.observe(app,scope,None,send))
    if duplicate:
        asyncio.run(sensor.observe(app,scope,None,send))
    after = datetime.now(timezone.utc).isoformat()
    trial = {'case_id':'before','generation':'old','transport_dispatched':client_dispatch,
             'dispatch_evidence':[], 'normal_shape_verified':False, 'started_at':before,'finished_at':after}
    receipt = reg.read([trial],{trace.ASSET:{'sha256':sha256(Path(trace.__file__).read_bytes()).hexdigest()}},
                       owner=os.getuid())
    return trial, receipt


@pytest.mark.parametrize('mode', ['exact','wrapped','wrong_peer','wrong_external','exception','duplicate','wrong_source'])
def test_trace_retains_only_source_qualified_owned_request_metadata(context, mode):
    trace, module, _, reg, sensor = context
    if mode == 'wrong_source':
        source = Path(module.__spec__.origin)
        source.write_bytes(source.read_bytes()+b'# not the installed pin\n')
    _, output = measure(context, wrong_peer=mode=='wrong_peer', wrong_external=mode=='wrong_external',
                        failing_call=mode=='exception', duplicate=mode=='duplicate', wrapped=mode=='wrapped')
    assert output['diagnostic'] is None and not sensor.calls
    row, = output['cases']
    assert row['request_observer_invoked'] is (mode not in ('wrong_peer','duplicate'))
    assert row['assignment_bound'] is (mode in ('exact','wrapped'))
    assert row['asgi_terminal_send_observed'] is row['request_observer_invoked']
    if mode == 'exception':
        assert row['assignment_invocation_count'] == 1
    if mode == 'wrong_source':
        assert not output['source_verified']
    assert reg.tokens['before'] not in json.dumps(output)
    assert sha256(reg.tokens['before'].encode()).hexdigest() not in json.dumps(output)
    assert reg.key.hex() not in json.dumps(output)
    assert 'PRIVATE FIXTURE BODY' not in json.dumps(output)


@pytest.mark.parametrize('same_generation_receive', [True,False])
def test_unknown_client_dispatch_uses_only_scoped_asgi_receipt(context, same_generation_receive):
    trial, receipt = measure(context, wrong_peer=not same_generation_receive, client_dispatch=None)
    case, = receipt['cases']
    projection = combine_dispatch(trial, case)
    assert projection['client_transport_dispatched'] is None
    assert projection['transport_dispatched'] is (True if same_generation_receive else None)
    assert projection['same_generation_asgi_received'] is same_generation_receive
    assert projection['dispatch_evidence'] == (['same_generation_asgi_entered'] if same_generation_receive else [])
    assert not projection['stock_generation_attribution_verified'] and not projection['normal_shape_verified']
