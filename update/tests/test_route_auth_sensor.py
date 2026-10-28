"""EXAMPLE exact auth/lookup/serving bodies; no real router, credential or engine."""
import ast
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace,ModuleType

import pytest

from guard.adapters import vllm_lifecycle_sensor as sensor,vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_route_authentication as route_index
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory,RecordingObserver,CANARY,CONTEXT

AUTH="vllm.entrypoints.serve.middleware.authenticate"
MODELS="vllm.entrypoints.openai.models.serving"
ROUTER="vllm.entrypoints.openai.models.api_router"
FIXTURES=Path(__file__).parent/"fixtures"
def sha(value):return hashlib.sha256(value.encode()).hexdigest()


def native(name,module,namespace):
    row=json.loads((FIXTURES/"route-auth-native-methods.json").read_bytes())[name]
    assert row["full_source_sha256"]==sensor.SOURCE_SHA[module] and row["license"]=="Apache-2.0"
    if name=="show_available_models":assert row["owner_class"]=="OpenAIServingModels"
    node=ast.parse(row["method"]).body[0];node.decorator_list=[]
    tree=ast.Module(body=[ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0),node],type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree),"reviewed-native-route-body","exec"),namespace)
    return namespace[node.name]


class Headers:
    def __init__(self,*,scope):self.scope=scope
    def get(self,key):
        return next((v.decode() for k,v in self.scope.get("headers",[]) if k.lower()==key.lower().encode()),None)


class Response:
    def __init__(self,*,content,status_code=200):self.status=status_code
    async def __call__(self,scope,receive,send):
        await send({"type":"http.response.start","status":self.status})
        await send({"type":"http.response.body","body":b"EXAMPLE-response"})


class ModelList:
    def __init__(self):self.data=[];self.dumps=0
    def model_dump(self):self.dumps+=1;return {"data":[]}
    def __iter__(self):raise AssertionError("observer must not consume a response")


def application(observer,monkeypatch,*,registry=None,router_factory=None):
    module=ModuleType(AUTH)
    import secrets
    module.__dict__.update(hashlib=hashlib,secrets=secrets,Headers=Headers,JSONResponse=Response,
        GUARDED_PREFIX=("/v1","/v2","/inference","/cohere"))
    auth=native("AuthenticationMiddleware",AUTH,module.__dict__);observer.instrument(module)
    output=ModelList()
    async def read():return output
    method=native("show_available_models",MODELS,{})
    serving=ModuleType(MODELS);serving.OpenAIServingModels=type("OpenAIServingModels",(),{"show_available_models":method})
    observer.instrument(serving)
    handler=serving.OpenAIServingModels();handler.registry=SimpleNamespace(show_available_models=registry or read)
    handler.lora_requests={}
    router=ModuleType(ROUTER);router.JSONResponse=Response
    native("models",ROUTER,router.__dict__)
    endpoint=native("ModelsRouter.show_available_models",ROUTER,router.__dict__)
    if router_factory is not None:router.router=router_factory(endpoint)
    observer.instrument(router)  # The endpoint's global helper resolves the wrapped function.
    async def app(scope,receive,send):
        request=SimpleNamespace(scope=scope,app=SimpleNamespace(state=SimpleNamespace(openai_serving_models=handler)))
        response=await endpoint(request) if router_factory is None else await router.router.dispatch(request)
        await response(scope,receive,send)
    monkeypatch.setattr(sensor,"_OBSERVER",observer)
    return sensor.Middleware(auth(app,[CANARY+"-credential"])),handler,output


def begin(case,writer_factory,monkeypatch):
    observer=RecordingObserver()
    writer,fake=writer_factory(session=case.prepared["session"],settings_sha=case.prepared["assets"]["settings.json"]["sha256"])
    monkeypatch.setattr(observer,"emit",lambda event,payload:writer.write(event,payload))
    for module in (AUTH,MODELS,ROUTER):writer.write("MODULE",{"module":module,"verified":True,"source_sha256":sensor.SOURCE_SHA[module],"code_sha256":"c"*64})
    return observer,writer,fake


def scope(role="valid",header=CANARY,path="/v1/models",root=""):
    headers=[(b"x-request-id",header.encode())]
    if role!="missing":headers.append((b"authorization",("Bearer "+CANARY+("-credential" if role=="valid" else "-wrong")).encode()))
    return {"type":"http","method":"GET","path":path,"root_path":root,"headers":headers}


async def invoke(app,value):
    messages=[]
    async def receive():raise AssertionError("model-list request body must not be consumed")
    async def send(message):messages.append(message);return "EXAMPLE-send-result"
    assert await app(value,receive,send) is None
    return messages


def finish(case,writer,fake):
    writer.close("SEALED");raw=bytes(fake.data);rows,summary=capture.parse_recording(raw,case.prepared)
    result=route_index.index([{"capture":{"records":rows,"provenance":{"owner":{"container_id":"a"*64}}}}])
    assert summary["recording_closed"] and CANARY.encode() not in raw
    assert result["security"]=="UNKNOWN" and not result["authorization_verified"] and not result["registered_handler_verified"]
    assert not result["producer_authenticated"] and not result["whole_route_coverage_verified"]
    return result,rows,raw


@pytest.mark.parametrize("role",["missing","wrong","valid"])
@pytest.mark.parametrize("root",["","/EXAMPLE-private-prefix"])
def test_auth_and_serving_bodies_preserve_requests_and_do_not_read_credentials_or_responses(case,writer_factory,monkeypatch,role,root):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,handler,output=application(observer,monkeypatch)
    current=scope(role,path=root+"/v1/models",root=root)
    messages=asyncio.run(invoke(app,current));result,rows,raw=finish(case,writer,fake)
    assert messages[0]["status"]==(200 if role=="valid" else 401)
    auth=next(r for r in result["events"] if r["point"]=="AuthenticationMiddleware.verify_token" and r["phase"]=="RETURN")
    assert auth["metadata"]["token_verified"] is (role=="valid")
    assert auth["metadata"]["auth_call_sha256"] and auth["metadata"]["asgi_request_sha256"]==sha(CANARY)
    assert auth["metadata"]["observed_path_sha256"]==sha(current["path"])
    assert auth["metadata"]["auth_relative_path_sha256"]==sha("/v1/models")
    serving=[r for r in result["events"] if r["point"]=="OpenAIServingModels.show_available_models"]
    assert bool(serving) is (role=="valid") and output.dumps==(1 if role=="valid" else 0)
    if serving:
        returned=next(r for r in serving if r["phase"]=="RETURN")
        assert returned["metadata"]["serving_method_returned"] and returned["metadata"]["auth_call_sha256"] is None
        assert returned["metadata"]["asgi_invocation_sha256"]==auth["metadata"]["asgi_invocation_sha256"]
        assert any(r["point"]=="api_router.models" and r["metadata"]["route_lookup_returned"] for r in result["events"])
    assert not any(r["event"]=="ASGI" for r in rows)  # Existing completion callback observations retain their scope.
    assert b"EXAMPLE-private-prefix" not in raw and b"EXAMPLE-response" not in raw


def test_sync_auth_return_records_coroutine_creation_before_serving_completion(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,_,_=application(observer,monkeypatch)
    asyncio.run(invoke(app,scope()));result,_,_=finish(case,writer,fake)
    auth=next(i for i,r in enumerate(result["events"]) if r["point"]=="AuthenticationMiddleware.__call__" and r["phase"]=="RETURN")
    serving=next(i for i,r in enumerate(result["events"]) if r["point"]=="OpenAIServingModels.show_available_models" and r["phase"]=="CALL")
    assert auth<serving and result["events"][auth]["metadata"]["returned_native_coroutine"] is True
    assert result["events"][auth]["metadata"]["serving_method_returned"] is None


@pytest.mark.parametrize("header",[[],[(b"x-request-id",b"")],[(b"x-request-id",b"a"*513)],
    [(b"x-request-id",b"a"),(b"X-Request-ID",b"b")],[(b"unused",b"x")]*65])
def test_unusable_or_duplicate_header_does_not_create_route_association(case,writer_factory,monkeypatch,header):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,_,_=application(observer,monkeypatch)
    value=scope();value["headers"]=header+[(b"authorization",("Bearer "+CANARY+"-credential").encode())]
    assert asyncio.run(invoke(app,value))[0]["status"]==200
    result,_,_=finish(case,writer,fake)
    assert result["events"]==[] and result["invocations"]=={}


def test_reused_header_retains_both_invocations_and_marks_external_ambiguity(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,_,_=application(observer,monkeypatch)
    async def run():await asyncio.gather(invoke(app,scope()),invoke(app,scope()))
    asyncio.run(run());result,_,_=finish(case,writer,fake)
    assert len(result["invocations"])==2 and result["ambiguous_asgi_request_sha256"]==[sha(CANARY)]


def test_late_child_context_is_closed_and_cannot_attribute_later_serving_call(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,handler,_=application(observer,monkeypatch)
    async def run():
        ready=asyncio.Event();children=[]
        original=app.app.app
        async def outer(scope,receive,send):
            async def later():await ready.wait();return await handler.show_available_models()
            children.append(asyncio.create_task(later()));await original(scope,receive,send)
        app.app.app=outer
        await invoke(app,scope());before=len(fake.data);ready.set();await children[0]
        assert len(fake.data)==before
    asyncio.run(run());result,_,_=finish(case,writer,fake)
    assert len([r for r in result["events"] if r["point"]=="OpenAIServingModels.show_available_models" and r["phase"]=="RETURN"])==1


def test_original_registry_exception_is_preserved_and_scope_is_reset(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);error=RuntimeError(CANARY)
    async def failure():raise error
    app,_,_=application(observer,monkeypatch,registry=failure)
    with pytest.raises(RuntimeError) as caught:asyncio.run(invoke(app,scope()))
    assert caught.value is error and sensor._ROUTE_REQUEST.get() is None
    result,_,_=finish(case,writer,fake)
    assert any(r["point"]=="OpenAIServingModels.show_available_models" and r["phase"]=="RAISE" for r in result["events"])
    assert not any(r["metadata"]["serving_method_returned"] for r in result["events"])


def test_serving_call_started_live_but_returning_after_request_keeps_prior_metadata(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    async def run():
        entered=asyncio.Event();release=asyncio.Event();children=[];output=ModelList()
        async def registry():entered.set();await release.wait();return output
        app,handler,_=application(observer,monkeypatch,registry=registry)
        async def outer(scope,receive,send):
            children.append(asyncio.create_task(handler.show_available_models()));await entered.wait()
        app.app.app=outer
        await invoke(app,scope());release.set()
        assert await children[0] is output
    asyncio.run(run());result,rows,_=finish(case,writer,fake)
    assert any(r["point"]=="OpenAIServingModels.show_available_models" and r["phase"]=="CALL" for r in result["events"])
    assert not any(r["metadata"]["serving_method_returned"] for r in result["events"])
    returned=next(r["payload"] for r in rows if r["event"]=="NATIVE" and r["payload"]["point"]=="OpenAIServingModels.show_available_models" and r["payload"]["phase"]=="RETURN")
    assert returned["metadata"]=={} and returned["diagnostic"]=="SAMPLE_UNAVAILABLE" and not result["stored_index_complete"]


def test_nested_non_http_scope_cannot_inherit_a_live_http_route_context(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);_,handler,_=application(observer,monkeypatch)
    async def child(scope,receive,send):return await handler.show_available_models()
    app=sensor.Middleware(child)
    token=sensor._ROUTE_REQUEST.set({"pid":fake.getpid(),"closed":False,"request":"a"*64,"invocation":"b"*64})
    try:
        async def no_io(*args):raise AssertionError("no additional callbacks")
        assert asyncio.run(app({"type":"lifespan"},no_io,no_io)) is not None
    finally:sensor._ROUTE_REQUEST.reset(token)
    result,_,_=finish(case,writer,fake)
    assert result["events"]==[]


def test_route_index_bound_preserves_first_events_and_marks_incomplete(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,_,_=application(observer,monkeypatch)
    asyncio.run(invoke(app,scope()));monkeypatch.setattr(route_index,"MAX_EVENTS",1)
    result,_,_=finish(case,writer,fake)
    assert len(result["events"])==1 and result["events"][0]["phase"]=="CALL" and not result["stored_index_complete"]


def test_auth_instrumentation_does_not_partially_replace_methods_when_a_target_is_missing():
    observer=RecordingObserver();module=ModuleType(AUTH)
    def original(*args):return object()
    module.AuthenticationMiddleware=type("AuthenticationMiddleware",(),{"__call__":original})
    with pytest.raises(ValueError):observer.instrument(module)
    assert module.AuthenticationMiddleware.__call__ is original


def test_observed_auth_and_route_scopes_remain_distinct_after_an_owned_rewrite(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,_,_=application(observer,monkeypatch)
    native_app=app.app.app
    async def rewritten(scope,receive,send):
        scope["path"]="/v1/models";scope["root_path"]=""
        return await native_app(scope,receive,send)
    app.app.app=rewritten
    value=scope(path="/private/v1/models",root="/private")
    asyncio.run(invoke(app,value));result,_,_=finish(case,writer,fake)
    auth=next(r["metadata"] for r in result["events"] if r["point"]=="AuthenticationMiddleware.verify_token" and r["phase"]=="RETURN")
    lookup=next(r["metadata"] for r in result["events"] if r["point"]=="api_router.models" and r["phase"]=="RETURN")
    assert auth["entry_path_sha256"]==auth["observed_path_sha256"]==sha("/private/v1/models")
    assert lookup["entry_path_sha256"]==sha("/private/v1/models") and lookup["observed_path_sha256"]==sha("/v1/models")


def test_generator_or_background_calls_without_live_route_context_do_not_create_associations(monkeypatch):
    observer=RecordingObserver();called=[]
    async def original(owner):called.append(owner);return owner
    wrapped=observer.wrap(MODELS,"OpenAIServingModels.show_available_models",original)
    owner=object()
    assert asyncio.run(wrapped(owner)) is owner and called==[owner] and observer.recorded==[]


def test_observer_never_calls_header_get_or_stringifies_opaque_native_objects(writer_factory):
    _,fake=writer_factory();observer=RecordingObserver()
    context={"pid":fake.getpid(),"closed":False,"request":"a"*64,"invocation":"b"*64}
    token=sensor._ROUTE_REQUEST.set(context)
    class Trap:
        def get(self,*args):raise AssertionError("no header reads")
        def __str__(self):raise AssertionError("no stringify")
        def __iter__(self):raise AssertionError("no consume")
    try:
        value=observer.route_metadata("AuthenticationMiddleware.verify_token",(Trap(),Trap()),{},True,True)
        assert value["token_verified"] is True
        context["pid"]+=1
        assert observer.route_context() is None
    finally:sensor._ROUTE_REQUEST.reset(token)


@pytest.mark.parametrize("key,value",[("token_verified",1),("asgi_request_sha256",None),
    ("entry_path_sha256",CANARY),("auth_call_sha256",True),("serving_method_returned",False),("unexpected",None)])
def test_resealed_invalid_route_metadata_is_rejected(case,writer_factory,monkeypatch,key,value):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,_,_=application(observer,monkeypatch)
    asyncio.run(invoke(app,scope()));writer.close("SEALED")
    def change(rows):
        row=next(r for r in rows if r["event"]=="NATIVE")
        row["payload"]["metadata"]["route_authentication"][key]=value
    with pytest.raises(ValueError):capture.parse_recording(revised(bytes(fake.data),change),case.prepared)


def profile4(case,raw_rows,crlf=False):
    root=case.manifest_path.parent;manifest=deepcopy(case.prepared);raw=(FIXTURES/"lifecycle-sensor-v4.py.txt").read_bytes()
    if crlf:raw=raw.replace(b"\n",b"\r\n")
    previous=manifest["assets"]["guard_lifecycle_sensor.py"]["sha256"]
    (root/"guard_lifecycle_sensor.py").write_bytes(raw)
    (root/"sitecustomize.py").write_bytes((root/"sitecustomize.py").read_bytes().replace(previous.encode(),hashlib.sha256(raw).hexdigest().encode()))
    manifest.update(profile=profiles.V4_PROFILE,source_sha256=profiles.V4_SOURCE_SHA)
    for name in ("guard_lifecycle_sensor.py","sitecustomize.py"):
        data=(root/name).read_bytes();manifest["assets"][name]={"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
    case.manifest_path.write_text(json.dumps(manifest))
    def change(rows):rows[0]["payload"].update(profile=profiles.V4_PROFILE,schema=profiles.V4_SCHEMA,sensor_sha256=hashlib.sha256(raw).hexdigest())
    return manifest,revised(raw_rows,change)


@pytest.mark.parametrize("crlf",[False,True])
def test_frozen_profile4_asset_and_weight_call_frames_remain_readable(case,crlf):
    manifest,raw=profile4(case,case.raw,crlf)
    assert lifecycle.load_prepared(case.manifest_path)==manifest and len(profiles.source_hashes(profiles.V4_PROFILE))==20
    rows,_=capture.parse_recording(raw,manifest)
    assert rows[0]["payload"]["profile"]==profiles.V4_PROFILE
    assert profiles.valid_call_frame({"point":"DefaultModelLoader.load_weights","request_sha256":[],"weight_call_sha256":"a"*64,"weight_model_sha256":"b"*64},profiles.V4_PROFILE)
    assert not any(p.startswith("AuthenticationMiddleware") for p in profiles.point_sources(profiles.V4_PROFILE))


def test_frozen_profile4_actual_weight_selection_recording_keeps_its_original_meaning(case,writer_factory,monkeypatch):
    from tests.test_native_weight_files import recording
    _,_,raw=recording(case,writer_factory,monkeypatch,["/models/EXAMPLE-private/a.bin"])
    manifest,raw=profile4(case,raw)
    rows,_=capture.parse_recording(raw,manifest)
    selected=next(value for row in rows if (value:=profiles.returned_weight_selection(row)) is not None)
    assert selected["load_context_linked"] and selected["paths_complete"]
    assert selected["paths"]==[sha("/models/EXAMPLE-private/a.bin")]
    assert route_index.index([{"capture":{"records":rows,"provenance":{"owner":{"container_id":"a"*64}}}}])["events"]==[]


@pytest.mark.parametrize("change",["source","module","point","metadata"])
def test_frozen_profile4_cannot_acquire_new_auth_source_points_or_metadata(case,writer_factory,monkeypatch,change):
    observer,writer,fake=begin(case,writer_factory,monkeypatch);app,_,_=application(observer,monkeypatch)
    asyncio.run(invoke(app,scope()));writer.close("SEALED")
    if change=="source":
        manifest,_=profile4(case,case.raw);manifest["source_sha256"]=sensor.SOURCE_SHA
        case.manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(ValueError):lifecycle.load_prepared(case.manifest_path)
        return
    manifest,old=profile4(case,case.raw)
    current_rows=[json.loads(line) for line in bytes(fake.data).splitlines()]
    def modify(rows):
        if change=="module":rows[1]["payload"]=deepcopy(current_rows[1]["payload"])
        elif change=="point":
            payload=deepcopy(next(r["payload"] for r in current_rows if r["event"]=="NATIVE"));payload["metadata"]={}
            payload.update(observation_started_ns=0,observation_finished_ns=0)
            rows.insert(-1,{**rows[-1],"event":"NATIVE","payload":payload})
        else:
            payload=deepcopy(next(r["payload"] for r in current_rows if r["event"]=="NATIVE"))
            payload.update(point="InputProcessor.assign_request_id",source_sha256=sensor.SOURCE_SHA["vllm.v1.engine.input_processor"],
                observation_started_ns=0,observation_finished_ns=0)
            rows.insert(-1,{**rows[-1],"event":"NATIVE","payload":payload})
    with pytest.raises(ValueError):capture.parse_recording(revised(old,modify),manifest)
