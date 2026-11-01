"""고정 native completion 본문 + EXAMPLE router. 실제 FastAPI/엔진 실증 아님."""
import ast
import asyncio
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from typer.testing import CliRunner

from guard.adapters import vllm_lifecycle_sensor as sensor, vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_route_authentication as route_index
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from tests.test_lifecycle_capture import case, revised, external
from tests.test_lifecycle_sensor import writer_factory, CANARY
from tests.test_registered_route_handler import finish, Router, Route
from tests.test_route_auth_sensor import AUTH, Headers, Response, native, begin, scope, invoke

MODULE="vllm.entrypoints.openai.completion.api_router"
POINT="api_router.create_completion"
FIXTURES=Path(__file__).parent/"fixtures"


def function(name,namespace):
    row=json.loads((FIXTURES/"completion-handler-native-methods.json").read_bytes())[name]
    assert row["full_source_sha256"]==sensor.SOURCE_SHA[MODULE] and row["public_commit"]==sensor.COMMIT
    assert row["license"]=="Apache-2.0"
    node=ast.parse(row["method"]).body[0];node.decorator_list=[]
    tree=ast.Module(body=[ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0),node],type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree),"reviewed-native-completion-body","exec"),namespace)
    return namespace[node.name]


class CompletionHeaders(Headers):
    def get(self,key,default=None):
        value=super().get(key)
        return default if value is None else value


class Output:
    def __init__(self):self.dumps=0
    def model_dump(self):self.dumps+=1;return {"private":CANARY}


class ErrorOutput(Output):
    error=SimpleNamespace(code=400)


class StreamOutput:
    def __aiter__(self):raise AssertionError("observer must not iterate the stream")


class JSONResponse(Response):
    def __init__(self,*,content,status_code=200,headers=None):super().__init__(content=content,status_code=status_code)


class StreamingResponse(Response):
    def __init__(self,*,content,media_type):
        assert media_type=="text/event-stream"
        self.content=content;self.status=200


def application(observer,monkeypatch,*,kind="json",failure=None,layout=None,keywords=False,rewrite=False):
    auth=ModuleType(AUTH)
    import secrets
    auth.__dict__.update(hashlib=hashlib,secrets=secrets,Headers=Headers,JSONResponse=Response,
        GUARDED_PREFIX=("/v1","/v2","/inference","/cohere"))
    middleware=native("AuthenticationMiddleware",AUTH,auth.__dict__);observer.instrument(auth)
    output=Output() if kind=="json" else ErrorOutput() if kind=="error" else StreamOutput()
    calls=[]
    async def serve(request,raw_request):
        calls.append((request,raw_request))
        if failure is not None:raise failure
        return output
    serving=SimpleNamespace(create_completion=serve)
    module=ModuleType(MODULE)
    module.__dict__.update(ErrorResponse=ErrorOutput,CompletionResponse=Output,JSONResponse=JSONResponse,
        StreamingResponse=StreamingResponse,metrics_header=lambda value:{},
        ENDPOINT_LOAD_METRICS_FORMAT_HEADER_LABEL="endpoint-load-metrics-format",
        with_sse_keep_alive=lambda generator,interval:generator)
    function("completion",module.__dict__)
    endpoint=function("create_completion",module.__dict__)
    route=Route(endpoint);route.path="/v1/completions";route.methods={"POST"}
    module.router=SimpleNamespace(routes=[route])
    if layout=="path":route.path="/v1/models"
    elif layout=="method":route.methods={"GET"}
    elif layout=="duplicate":module.router.routes.append(route)
    signature=inspect.signature(endpoint)
    observer.instrument(module)
    assert inspect.signature(module.create_completion)==signature
    protocol=SimpleNamespace(private_payload=CANARY)
    async def app(value,receive,send):
        if rewrite:value["path"]="/v1/completions"
        request=SimpleNamespace(scope=value,headers=CompletionHeaders(scope=value),
            app=SimpleNamespace(state=SimpleNamespace(openai_serving_completion=serving,args=None)))
        value.update(route=route,endpoint=route.endpoint)
        if layout=="runtime":route.dependant.call=lambda *a:None
        callback=module.create_completion if layout=="runtime" else route.dependant.call
        response=await callback(request=protocol,raw_request=request) if keywords else await callback(protocol,request)
        await response(value,receive,send)
    monkeypatch.setattr(sensor,"_OBSERVER",observer)
    return sensor.Middleware(middleware(app,[CANARY+"-credential"])),calls,output,protocol,module


def start(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    writer.write("MODULE",{"module":MODULE,"verified":True,"source_sha256":sensor.SOURCE_SHA[MODULE],"code_sha256":"c"*64})
    return observer,writer,fake


def request(role="valid",prefix=""):
    value=scope(role,path=prefix+"/v1/completions",root=prefix)
    value["method"]="POST"
    return value


@pytest.mark.parametrize("kind",["json","stream"])
@pytest.mark.parametrize("role",["valid","missing","wrong"])
@pytest.mark.parametrize("prefix",["","/EXAMPLE-private-prefix"])
def test_native_completion_auth_and_registered_handler_flow(case,writer_factory,monkeypatch,kind,role,prefix):
    observer,writer,fake=start(case,writer_factory,monkeypatch)
    app,calls,output,protocol,module=application(observer,monkeypatch,kind=kind)
    messages=asyncio.run(invoke(app,request(role,prefix)))
    index,rows,raw,_=finish(case,writer,fake)
    assert messages[0]["status"]==(200 if role=="valid" else 401)
    entry=next(iter(index["invocations"].values()))
    assert entry["registered_handler_observed"] is (role=="valid")
    assert entry["accepted_token_before_handler_observed"] is (role=="valid")
    assert entry["handler_return_observed"] is (role=="valid")
    assert entry["source_modules_observed"] and index["stored_index_complete"]
    assert len(calls)==(1 if role=="valid" else 0)
    if calls:assert calls[0][0] is protocol
    if kind=="json":assert output.dumps==(1 if role=="valid" else 0)
    assert b"EXAMPLE-private-prefix" not in raw and b"private_payload" not in raw


@pytest.mark.parametrize("keywords",[False,True])
def test_request_argument_position_and_native_error_response_are_preserved(case,writer_factory,monkeypatch,keywords):
    observer,writer,fake=start(case,writer_factory,monkeypatch)
    app,calls,output,protocol,module=application(observer,monkeypatch,kind="error",keywords=keywords)
    assert asyncio.run(invoke(app,request()))[0]["status"]==400
    index,_,_,_=finish(case,writer,fake)
    assert next(iter(index["invocations"].values()))["registered_handler_observed"]
    assert output.dumps==1 and calls[0][0] is protocol


@pytest.mark.parametrize("layout",["path","method","duplicate","runtime"])
def test_unsupported_or_changed_registration_does_not_claim_execution(case,writer_factory,monkeypatch,layout):
    observer,writer,fake=start(case,writer_factory,monkeypatch)
    app,_,_,_,_=application(observer,monkeypatch,layout=layout)
    assert asyncio.run(invoke(app,request()))[0]["status"]==200
    index,_,_,_=finish(case,writer,fake)
    assert not next(iter(index["invocations"].values()))["registered_handler_observed"]


@pytest.mark.parametrize("failure_type",[RuntimeError,asyncio.CancelledError])
def test_native_exception_and_cancellation_object_are_preserved(case,writer_factory,monkeypatch,failure_type):
    error=failure_type(CANARY)
    observer,writer,fake=start(case,writer_factory,monkeypatch)
    app,_,_,_,_=application(observer,monkeypatch,failure=error)
    with pytest.raises(failure_type) as caught:asyncio.run(invoke(app,request()))
    assert caught.value is error and sensor._ROUTE_REQUEST.get() is None
    index,_,_,_=finish(case,writer,fake)
    entry=next(iter(index["invocations"].values()))
    assert entry["registered_handler_observed"] and not entry["handler_return_observed"]
    assert any(event["point"]==POINT and event["phase"]=="RAISE" for event in index["events"])


def test_post_auth_rewrite_and_missing_module_are_observations_not_authorization(case,writer_factory,monkeypatch):
    observer,writer,fake=start(case,writer_factory,monkeypatch)
    app,_,_,_,_=application(observer,monkeypatch,rewrite=True)
    value=request("missing");value["path"]="/health"
    assert asyncio.run(invoke(app,value))[0]["status"]==200
    index,_,_,recordings=finish(case,writer,fake)
    entry=next(iter(index["invocations"].values()))
    assert entry["path_change_observed"] is True and entry["token_results"]==[]
    assert entry["registered_handler_observed"] and entry["security"]=="UNKNOWN"
    rows=recordings[0]["capture"]["records"]
    rows[:]=[r for r in rows if not (r["event"]=="MODULE" and r["payload"]["module"]==MODULE)]
    incomplete=route_index.handler_index(recordings)
    assert incomplete["events"] and not incomplete["stored_index_complete"]


def test_same_header_does_not_merge_distinct_completion_invocations(case,writer_factory,monkeypatch):
    observer,writer,fake=start(case,writer_factory,monkeypatch)
    app,calls,_,_,_=application(observer,monkeypatch)
    async def twice():await asyncio.gather(invoke(app,request()),invoke(app,request()))
    asyncio.run(twice())
    index,_,_,_=finish(case,writer,fake)
    assert len(calls)==2 and len(index["invocations"])==2
    assert all(entry["registered_handler_observed"] and entry["handler_return_observed"] for entry in index["invocations"].values())
    assert len(index["ambiguous_asgi_request_sha256"])==1


def test_product_session_saves_completion_callback_with_raw_row_references(case,writer_factory,monkeypatch):
    from guard.cli import app as cli
    from guard.executors import lifecycle_session, owned_capture
    from tests.test_lifecycle_capture import Docker, IMAGE
    observer,writer,fake=start(case,writer_factory,monkeypatch)
    app,_,_,_,_=application(observer,monkeypatch)
    asyncio.run(invoke(app,request()))
    _,rows,raw,_=finish(case,writer,fake);case.raw=raw
    owner={"container_id":"a"*64}
    inventory={"owner":owner,"metadata":{"files":[{"pid":42,"start_ticks":99,"regular":True,"uid":1000,
        "mode":0o100600,"device":1,"inode":2,"bytes":len(raw),"mtime_ns":3,"ctime_ns":4}],
        "processes":[],"directory_before":{"uid":1000}}}
    monkeypatch.setattr(lifecycle_session,"inventory",lambda *a,**k:deepcopy(inventory))
    monkeypatch.setattr(lifecycle_session,"_complete",lambda *a:True)
    original=capture.capture
    def collected(*a,**k):
        value=original(*a,**k);value["provenance"]["owner"]=owner;return value
    monkeypatch.setattr(lifecycle_session,"capture",collected)
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:Docker(case))
    output=case.root/"completion-handler-session.json"
    args=["capture-lifecycle-session","--env",str(case.root/"env.json"),"--target",str(case.root/"target.json"),
        "--prepared",str(case.manifest_path),"--container",case.source["container"],"--image",IMAGE,"--output",str(output),"--execute"]
    result=CliRunner().invoke(cli,args)
    assert result.exit_code==0,result.output
    stored=json.loads(output.read_bytes())
    assert stored["recordings"][0]["capture"]["records"]==rows
    events=stored["route_handler_index"]["events"]
    assert any(event["point"]==POINT and event["phase"]=="CALL" for event in events)
    assert all(stored["recordings"][ref[0]]["capture"]["records"][ref[1]]["payload"]["point"]==event["point"]
        for event in events for ref in [event["event"]])
    assert stored["security"]=="UNKNOWN" and stored["data_origin"]=="EXAMPLE" and not stored["product_connected"]
    assert CANARY.encode() not in output.read_bytes()


def old7(case,raw,crlf):
    root=case.manifest_path.parent
    source=(FIXTURES/"lifecycle-sensor-v7.py.txt").read_bytes().replace(b"\r\n",b"\n")
    if crlf:source=source.replace(b"\n",b"\r\n")
    (root/"guard_lifecycle_sensor.py").write_bytes(source)
    manifest=deepcopy(case.prepared);previous=manifest["assets"]["guard_lifecycle_sensor.py"]["sha256"]
    (root/"sitecustomize.py").write_bytes((root/"sitecustomize.py").read_bytes().replace(previous.encode(),hashlib.sha256(source).hexdigest().encode()))
    manifest.update(profile=profiles.V7_PROFILE,source_sha256=profiles.V7_SOURCE_SHA)
    for name in manifest["assets"]:
        data=(root/name).read_bytes();manifest["assets"][name]={"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
    case.manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
    def update(rows):
        rows[0]["payload"].update(profile=profiles.V7_PROFILE,schema=profiles.V7_SCHEMA,sensor_sha256=hashlib.sha256(source).hexdigest())
    return manifest,revised(raw,update)


@pytest.mark.parametrize("crlf",[False,True])
def test_profile7_keeps_models_callback_and_rejects_new_completion_point(case,writer_factory,monkeypatch,crlf):
    from tests.test_route_auth_sensor import application as models_app, scope as models_scope
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=models_app(observer,monkeypatch,router_factory=Router)
    asyncio.run(invoke(app,models_scope()));writer.close("SEALED")
    manifest,raw=old7(case,bytes(fake.data),crlf)
    assert lifecycle.load_prepared(case.manifest_path)==manifest
    rows,_=capture.parse_recording(raw,manifest)
    index=route_index.handler_index([{"capture":{"records":rows,"provenance":{"owner":{}}}}])
    assert next(iter(index["invocations"].values()))["registered_handler_observed"]
    assert POINT not in profiles.point_sources(profiles.V7_PROFILE)
    assert len(profiles.V7_SOURCE_SHA)==25 and len(sensor.SOURCE_SHA)==26
    def mutate(rows):
        row=next(r for r in rows if r["event"]=="NATIVE" and r["payload"]["point"]==sensor.HANDLER_POINT)
        row["payload"].update(point=POINT,source_sha256=sensor.SOURCE_SHA[MODULE])
    with pytest.raises(ValueError):capture.parse_recording(revised(raw,mutate),manifest)
