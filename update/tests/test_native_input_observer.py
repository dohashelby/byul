"""EXAMPLE async lineage with content/property traps; no engine imports or network."""
import asyncio,hashlib,importlib.util,json,sys,types
from pathlib import Path
import pytest
from guard.adapters import vllm_rank_ipc_sources as sources

from guard.adapters import vllm_native_input_observer as prototype
RAW=b'''import asyncio
from abc import ABCMeta
calls=[]
pending=[]
class NativeError(ValueError):
    def __init__(self,parameter=None):
        self.parameter=parameter
    def __str__(self):
        raise AssertionError("exception text must not be read")
class Validator(metaclass=ABCMeta):
    def check(self,value):
        calls.append("validator")
        if self.error is not None:raise self.error
        return value
class Handler(metaclass=ABCMeta):
    async def handle(self,request,raw_request):
        calls.append("handler")
        await asyncio.sleep(0)
        if self.thread=="copy":return await asyncio.to_thread(validator.check,request)
        if self.thread=="missing":return await asyncio.get_running_loop().run_in_executor(None,validator.check,request)
        if self.thread=="late":
            async def later():
                await gate.wait()
                return validator.check(request)
            pending.append(asyncio.create_task(later()))
            return request
        return validator.check(request)
class Cycle:
    async def run_asgi(self,app):
        calls.append("cycle")
        return await app(self.scope)
'''


@pytest.fixture
def setup(tmp_path,monkeypatch):
    name='EXAMPLE_owned_native_input';path=tmp_path/'input_fixture.py';path.write_bytes(RAW)
    module=types.ModuleType(name);module.__file__=str(path);exec(compile(RAW,str(path),'exec',dont_inherit=True,optimize=0),vars(module))
    monkeypatch.setitem(sys.modules,name,module)
    points=['NativeError.__init__','Validator.check','Handler.handle','Cycle.run_asgi']
    codes=sources._codes(compile(RAW,'input_fixture.py','exec',dont_inherit=True,optimize=0))
    pin={'path':'input_fixture.py','bytes':len(RAW),'sha256':hashlib.sha256(RAW).hexdigest(),'functions':points,
        'body_code_sha256':{p:sources.code_digest(sources.relative_code(codes[p],'input_fixture.py'))for p in points},
        'hooks':{'Handler.handle':{'kind':'http-handler','raw_request_argument':'raw_request','paths':['/v1/completions','/v1/chat/completions']},
            'Validator.check':{'kind':'input-validator'},
            'Cycle.run_asgi':{'kind':'http-handler','scope_owner_argument':'self','paths':['/v1/completions','/v1/chat/completions']}}}
    profile={'runtime':sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3])),
        'sources':{name:pin},'validation_exception':{'module':name,'class':'NativeError'}}
    rows=[];observer=prototype.NativeInputObserver(profile,b'K'*32,rows.append);observer.attach(module)
    module.validator=module.Validator();module.validator.error=None
    handler=module.Handler();handler.thread=None
    return module,handler,observer,rows,path,profile


class ContentTrap:
    def __repr__(self):raise AssertionError('content repr forbidden')
    def __len__(self):raise AssertionError('content len forbidden')
    def __iter__(self):raise AssertionError('content iteration forbidden')


def request(identifier=b'EXAMPLE-request',path='/v1/completions'):
    class RawRequest:pass
    raw=RawRequest();raw.scope={'type':'http','method':'POST','path':path,
        'headers':[(b'x-request-id',identifier),(b'authorization',ContentTrap())]}
    return raw


@pytest.mark.parametrize('thread',[None,'copy'])
def test_original_result_and_native_validator_lineage_are_preserved_without_content_read(setup,thread):
    module,handler,observer,rows,path,profile=setup;handler.thread=thread;body=ContentTrap()
    assert asyncio.run(handler.handle(body,request()))is body
    assert module.calls==['handler','validator']and [r['event']for r in rows]==['CALL','CALL','RETURN','RETURN']
    assert len({r['root_call_hmac_sha256']for r in rows})==len({r['request_hmac_sha256']for r in rows})==1
    assert rows[0]['call_hmac_sha256']==rows[-1]['call_hmac_sha256']
    assert all(r['source_authenticated']is r['producer_registered']is r['scope_complete']is False for r in rows)
    assert 'EXAMPLE-request'not in json.dumps(rows)


def test_original_validation_exception_identity_and_text_are_preserved(setup):
    module,handler,observer,rows,path,profile=setup;error=module.NativeError('PRIVATE');module.validator.error=error
    with pytest.raises(module.NativeError)as caught:asyncio.run(handler.handle(ContentTrap(),request()))
    assert caught.value is error and module.calls==['handler','validator']
    assert [r['event']for r in rows]==['CALL','CALL','RAISE','RAISE']
    assert rows[2]['exception_kind']=='native-validation-error'and 'PRIVATE'not in json.dumps(rows)
    assert rows[2]['exception_site']['direct_raise']is True


@pytest.mark.parametrize('damage',['duplicate-header','invalid-id','scope-property','source-file','module-binding','class-binding'])
def test_failed_observation_does_not_replace_or_repeat_original(setup,monkeypatch,damage):
    module,handler,observer,rows,path,profile=setup;raw=request();body=ContentTrap();wrapped=handler.handle
    if damage=='duplicate-header':raw.scope['headers'].append((b'x-request-id',b'EXAMPLE-other'))
    elif damage=='invalid-id':raw.scope['headers'][0]=(b'x-request-id',b'\xff')
    elif damage=='scope-property':
        class Trap:
            @property
            def scope(self):raise AssertionError('property forbidden')
        raw=Trap()
    elif damage=='source-file':path.write_bytes(RAW+b'\n# changed source\n')
    elif damage=='module-binding':monkeypatch.setitem(sys.modules,module.__name__,types.ModuleType(module.__name__))
    elif damage=='class-binding':module.Handler=type('Handler',(),{'handle':module.Handler.handle})
    assert asyncio.run(wrapped(body,raw))is body and module.calls==['handler','validator']
    assert rows and all(r['schema']=='guard-native-input-hook-gap/1'for r in rows)


def test_concurrent_copied_contexts_cannot_mix_requests(setup):
    module,handler,observer,rows,path,profile=setup;other=module.Handler();handler.thread=other.thread='copy'
    first,second=ContentTrap(),ContentTrap()
    async def both():return await asyncio.gather(handler.handle(first,request(b'EXAMPLE-one')),other.handle(second,request(b'EXAMPLE-two','/v1/chat/completions')))
    result=asyncio.run(both());assert result[0]is first and result[1]is second
    groups={}
    for row in rows:groups.setdefault(row['root_call_hmac_sha256'],[]).append(row)
    assert len(groups)==2 and len(rows)==8
    for values in groups.values():
        assert len(values)==4 and len({v['request_hmac_sha256']for v in values})==len({v['path']for v in values})==1


def test_missing_thread_context_supplies_no_validator_proof(setup):
    module,handler,observer,rows,path,profile=setup;handler.thread='missing';body=ContentTrap()
    assert asyncio.run(handler.handle(body,request()))is body and module.calls==['handler','validator']
    assert [r['point']for r in rows]==['Handler.handle','Handler.handle']


def test_inherited_context_after_closed_http_root_cannot_supply_validator_proof(setup):
    module,handler,observer,rows,path,profile=setup;handler.thread='late';body=ContentTrap()
    async def run():
        module.gate=asyncio.Event();assert await handler.handle(body,request())is body
        module.gate.set();assert await module.pending[0]is body
    asyncio.run(run())
    assert module.calls==['handler','validator']and [r['point']for r in rows if 'point'in r]==['Handler.handle','Handler.handle']
    assert rows[-1]['schema']=='guard-native-input-hook-gap/1'


def test_failed_emitter_cannot_change_original_exception(setup):
    module,handler,observer,rows,path,profile=setup;error=module.NativeError('PRIVATE');module.validator.error=error
    def fail(value):raise RuntimeError('EXAMPLE emitter unavailable')
    observer.emit=fail
    with pytest.raises(module.NativeError)as caught:asyncio.run(handler.handle(ContentTrap(),request()))
    assert caught.value is error and module.calls==['handler','validator']


@pytest.mark.parametrize('reach_api',[False,True])
def test_asgi_root_covers_validation_before_api_entry_and_binds_nested_api_root(setup,reach_api):
    module,handler,observer,rows,path,profile=setup;raw=request();cycle=module.Cycle();cycle.scope=raw.scope;body=ContentTrap()
    async def app(scope):
        assert scope is raw.scope
        if reach_api:return await handler.handle(body,raw)
        return module.validator.check(body)
    assert asyncio.run(cycle.run_asgi(app))is body
    assert module.calls==(['cycle','handler','validator']if reach_api else['cycle','validator'])
    assert len(rows)==(6 if reach_api else 4)
    if reach_api:
        api=[r for r in rows if r['point']=='Handler.handle'];guard=[r for r in rows if r['point']=='Validator.check']
        assert all(r['parent_root_call_hmac_sha256']==rows[0]['call_hmac_sha256']for r in [*api,*guard])
        assert all(r['root_call_hmac_sha256']==api[0]['call_hmac_sha256']for r in guard)
    else:assert all(r['root_call_hmac_sha256']==rows[0]['call_hmac_sha256']for r in rows)


def test_inconsistent_nested_request_is_not_given_parent_lineage(setup):
    module,handler,observer,rows,path,profile=setup;outer=request();inner=request(b'EXAMPLE-different');cycle=module.Cycle();cycle.scope=outer.scope
    body=ContentTrap()
    async def app(scope):return await handler.handle(body,inner)
    assert asyncio.run(cycle.run_asgi(app))is body and module.calls==['cycle','handler','validator']
    # An invalid nested handler must not allow its validator to borrow the outer root.
    assert not any(r.get('point')=='Handler.handle'for r in rows)
    assert not any(r.get('point')=='Validator.check'for r in rows)


@pytest.mark.parametrize('method,path',[('GET','/health'),('GET','/v1/models'),('POST','/not-selected')])
def test_unselected_health_or_other_routes_leave_no_input_proof_or_gap(setup,method,path):
    module,handler,observer,rows,source,profile=setup;raw=request(path=path);raw.scope['method']=method
    body=ContentTrap();cycle=module.Cycle();cycle.scope=raw.scope
    async def app(scope):return await handler.handle(body,raw)
    assert asyncio.run(cycle.run_asgi(app))is body
    assert module.calls==['cycle','handler','validator']and rows==[]


def test_traceback_property_is_not_executed_by_rejection_site_observation(setup):
    module,handler,observer,rows,path,profile=setup
    def forbidden(error):raise AssertionError('engine traceback getter forbidden')
    module.NativeError.__traceback__=property(forbidden)
    error=module.NativeError('PRIVATE');module.validator.error=error
    try:asyncio.run(handler.handle(ContentTrap(),request()))
    except module.NativeError as caught:assert caught is error
    else:raise AssertionError('original exception missing')
    assert rows[2]['exception_site']['direct_raise']is True and 'PRIVATE'not in json.dumps(rows)
