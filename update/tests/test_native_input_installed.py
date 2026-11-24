"""Installed source CodeTypes with owned stubs; no serving engine imports/run."""
import ast,asyncio,hashlib,importlib.util,json,sys,types,zipfile
from pathlib import Path
import pytest
from guard.adapters import vllm_rank_ipc_sources as source

from guard.adapters import vllm_native_input_observer as prototype

ROOT=Path(__file__).parent/'fixtures/vllm-0.30.0'
class FixtureArchive:
    def __init__(self,_path):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,path):
        raw=(ROOT/(path+'.txt')).read_bytes()
        row=json.loads((ROOT/'PROVENANCE.json').read_bytes())['files'][path+'.txt']
        assert hashlib.sha256(raw).hexdigest()==row['sha256']
        return raw

@pytest.mark.parametrize('kind,count',[('length',8),('length',9),('token',99),('token',100)])
def test_exact_installed_api_root_and_input_guard_keep_request_lineage(tmp_path,monkeypatch,kind,count):
    archive=ROOT
    selected=[('api','vllm/entrypoints/openai/completion/serving.py','OpenAIServingCompletion.create_completion'),
        ('params','vllm/renderers/params.py','TokenizeParams._token_len_check')if kind=='length'else
            ('params','vllm/v1/engine/input_processor.py','InputProcessor._validate_model_input'),
        ('errors','vllm/exceptions.py','VLLMValidationError.__init__')]
    modules={};pins={}
    with FixtureArchive(archive)as bundle:
        for role,path,point in selected:
            raw=bundle.read(path);filename=tmp_path/(role+'.py');filename.write_bytes(raw)
            name='EXAMPLE_installed_'+role;module=types.ModuleType(name);module.__file__=str(filename)
            compiled=compile(raw,path,'exec',dont_inherit=True,optimize=0);codes=source._codes(compiled)
            if role=='errors':
                tree=ast.parse(raw)
                assert all(not isinstance(n,ast.Import)and(not isinstance(n,ast.ImportFrom)or n.module in {'http','typing'})for n in tree.body)
                exec(compiled,vars(module))
            else:
                code=codes[point];assert not code.co_freevars
                cls,member=point.split('.');function=types.FunctionType(code,vars(module),member)
                setattr(module,cls,type(cls,(),{'__module__':name,member:function}))
            hook=({'kind':'http-handler','raw_request_argument':'raw_request','paths':['/v1/completions']}if role=='api'else
                {'kind':'input-validator'}if role=='params'else None)
            pins[name]={'path':path,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'functions':[point],
                'body_code_sha256':{point:source.code_digest(source.relative_code(codes[point],path))},'hooks':{point:hook}if hook else{}}
            modules[role]=module;monkeypatch.setitem(sys.modules,name,module)
    profile={'runtime':'cpython-3.12.3','sources':pins,
        'validation_exception':{'module':modules['errors'].__name__,'class':'VLLMValidationError'}}
    modules['params'].VLLMValidationError=modules['errors'].VLLMValidationError
    rows=[];observer=prototype.NativeInputObserver(profile,b'K'*32,rows.append)
    for module in modules.values():observer.attach(module)
    if kind=='length':
        params=modules['params'].TokenizeParams();params.max_input_tokens=8;params.max_output_tokens=1;params.max_total_tokens=9
        tokens=[0]*count;invoke=lambda:params._token_len_check(None,tokens);expected=tokens;valid=count==8
    else:
        modules['params'].length_from_prompt_token_ids_or_embeds=lambda ids,embeds:len(ids)
        params=modules['params'].InputProcessor();params.model_config=types.SimpleNamespace(get_vocab_size=lambda:100)
        params.tokenizer=types.SimpleNamespace(max_token_id=99);params._validate_prompt_len=lambda length,role:None
        invoke=lambda:params._validate_model_input({'type':'token','prompt_token_ids':[count]},'decoder');expected=None;valid=count==99
    api=modules['api'].OpenAIServingCompletion();calls=[]
    async def create(request,raw_request):calls.append('original-owned-stub');return invoke()
    async def cleanup(coroutine,request,raw_request):return await coroutine
    api._create_completion=create;api._with_kv_transfer_rejection_cleanup=cleanup
    class Request:pass
    raw=Request();raw.scope={'type':'http','method':'POST','path':'/v1/completions','headers':[(b'x-request-id',b'EXAMPLE-native-length')]}
    if valid:assert asyncio.run(api.create_completion(object(),raw))is expected
    else:
        with pytest.raises(modules['errors'].VLLMValidationError):asyncio.run(api.create_completion(object(),raw))
    assert calls==['original-owned-stub']and len(rows)==4
    assert [r['event']for r in rows]==(['CALL','CALL','RETURN','RETURN']if valid else['CALL','CALL','RAISE','RAISE'])
    assert len({r['root_call_hmac_sha256']for r in rows})==len({r['request_hmac_sha256']for r in rows})==1
    if not valid:
        assert rows[2]['exception_kind']=='native-validation-error'and rows[2]['exception_site']['direct_raise']is True
        with FixtureArchive(archive)as bundle:
            raw=bundle.read('vllm/renderers/params.py'if kind=='length'else'vllm/v1/engine/input_processor.py')
        assert rows[2]['exception_site']['source_line']in{n.lineno for n in ast.walk(ast.parse(raw))if isinstance(n,ast.Raise)}
    assert all(r['scope_complete']is r['producer_registered']is r['source_authenticated']is False for r in rows)


@pytest.mark.parametrize('backend',['h11','httptools'])
@pytest.mark.parametrize('count',[8,9])
def test_exact_installed_asgi_root_covers_pre_api_validation_with_owned_stubs(tmp_path,monkeypatch,backend,count):
    archive=ROOT
    selected=[('http','uvicorn/protocols/http/'+backend+'_impl.py','RequestResponseCycle.run_asgi'),
        ('params','vllm/renderers/params.py','TokenizeParams._token_len_check'),
        ('errors','vllm/exceptions.py','VLLMValidationError.__init__')]
    modules={};pins={}
    with FixtureArchive(archive)as bundle:
        for role,path,point in selected:
            raw=bundle.read(path);filename=tmp_path/(role+'.py');filename.write_bytes(raw)
            name='EXAMPLE_pre_api_'+role;module=types.ModuleType(name);module.__file__=str(filename)
            compiled=compile(raw,path,'exec',dont_inherit=True,optimize=0);codes=source._codes(compiled)
            if role=='errors':
                tree=ast.parse(raw)
                assert all(not isinstance(n,ast.Import)and(not isinstance(n,ast.ImportFrom)or n.module in {'http','typing'})for n in tree.body)
                exec(compiled,vars(module))
            else:
                code=codes[point];assert not code.co_freevars
                cls,member=point.split('.');function=types.FunctionType(code,vars(module),member)
                setattr(module,cls,type(cls,(),{'__module__':name,member:function}))
            hook=({'kind':'http-handler','scope_owner_argument':'self','paths':['/v1/completions']}if role=='http'else
                {'kind':'input-validator'}if role=='params'else None)
            pins[name]={'path':path,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'functions':[point],
                'body_code_sha256':{point:source.code_digest(source.relative_code(codes[point],path))},'hooks':{point:hook}if hook else{}}
            modules[role]=module;monkeypatch.setitem(sys.modules,name,module)
    profile={'runtime':'cpython-3.12.3','sources':pins,
        'validation_exception':{'module':modules['errors'].__name__,'class':'VLLMValidationError'}}
    modules['params'].VLLMValidationError=modules['errors'].VLLMValidationError
    rows=[];observer=prototype.NativeInputObserver(profile,b'K'*32,rows.append)
    for module in modules.values():observer.attach(module)
    params=modules['params'].TokenizeParams();params.max_input_tokens=8;params.max_output_tokens=1;params.max_total_tokens=9
    cycle=modules['http'].RequestResponseCycle();cycle.response_started=False;cycle.response_complete=False;cycle.disconnected=False
    cycle.scope={'type':'http','method':'POST','path':'/v1/completions','headers':[(b'x-request-id',b'EXAMPLE-pre-api-validation')]}
    calls=[]
    cycle.receive=object();cycle.send=object()
    cycle.logger=types.SimpleNamespace(error=lambda *args,**kwargs:calls.append('native-logger-called'))
    cycle.transport=types.SimpleNamespace(close=lambda:calls.append('native-transport-close'))
    async def send_500():calls.append('native-send-500');cycle.response_started=cycle.response_complete=True
    cycle.send_500_response=send_500
    async def app(scope,receive,send):
        assert scope is cycle.scope and receive is cycle.receive and send is cycle.send
        params._token_len_check(None,[0]*count)
        cycle.response_started=cycle.response_complete=True
        return None
    assert asyncio.run(cycle.run_asgi(app))is None
    assert len(rows)==4 and [r['event']for r in rows]==(['CALL','CALL','RETURN','RETURN']if count==8 else['CALL','CALL','RAISE','RETURN'])
    assert calls==([]if count==8 else['native-logger-called','native-send-500'])
    assert cycle.on_response()is None
    assert len({r['root_call_hmac_sha256']for r in rows})==1
    if count==9:assert rows[2]['exception_kind']=='native-validation-error'and rows[2]['exception_site']['direct_raise']is True
    assert all(r['scope_complete']is r['producer_registered']is r['source_authenticated']is False for r in rows)
