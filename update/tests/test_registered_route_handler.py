"""EXAMPLE registered endpoint wiring with exact reviewed vLLM bodies.

The small router below exercises captured endpoint and dependant references.
It is not FastAPI, a real Gateway or an authenticated producer.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from guard.adapters import vllm_lifecycle_sensor as sensor, vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_route_authentication as route_index
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture, lifecycle_session, owned_capture
from guard.cli import app as cli
from tests.test_lifecycle_capture import case, external, revised, Docker, IMAGE
from tests.test_lifecycle_sensor import writer_factory, RecordingObserver, CANARY
from tests.test_route_auth_sensor import application, begin, scope, invoke, Response, FIXTURES


class Route:
    def __init__(self,endpoint):
        self.endpoint=endpoint
        self.dependant=SimpleNamespace(call=endpoint)
        self.path="/v1/models"
        self.methods={"GET"}

    async def dispatch(self,request):
        request.scope.update(route=self,endpoint=self.endpoint)
        return await self.dependant.call(request)


class Router:
    def __init__(self,endpoint):self.routes=[Route(endpoint)]
    async def dispatch(self,request):
        route=self.routes[0]
        if request.scope["path"].removeprefix(request.scope.get("root_path",""))!="/v1/models":
            return Response(content={},status_code=404)
        return await route.dispatch(request)


def finish(case,writer,fake):
    writer.close("SEALED")
    raw=bytes(fake.data);rows,summary=capture.parse_recording(raw,case.prepared)
    recordings=[{"capture":{"records":rows,"provenance":{"owner":{"container_id":"a"*64}}}}]
    value=route_index.handler_index(recordings)
    assert summary["recording_closed"] and CANARY.encode() not in raw
    assert value["security"]=="UNKNOWN" and value["producer_authenticated"] is False
    assert value["registered_handler_verified"] is False and value["authorization_verified"] is False
    assert value["whole_route_coverage_verified"] is False
    return value,rows,raw,recordings


@pytest.mark.parametrize("role",["valid","missing","wrong"])
@pytest.mark.parametrize("prefix",["","/EXAMPLE-private-prefix"])
def test_exact_auth_to_registered_callback_to_serving_return(case,writer_factory,monkeypatch,role,prefix):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,output=application(observer,monkeypatch,router_factory=Router)
    response=asyncio.run(invoke(app,scope(role,path=prefix+"/v1/models",root=prefix)))
    index,rows,raw,_=finish(case,writer,fake)
    assert response[0]["status"]==(200 if role=="valid" else 401)
    entry=next(iter(index["invocations"].values()))
    assert [item["token_verified"] for item in entry["token_results"]]==[role=="valid"]
    assert entry["registered_handler_observed"] is (role=="valid")
    assert entry["handler_return_observed"] is (role=="valid")
    assert entry["accepted_token_before_handler_observed"] is (role=="valid")
    assert entry["path_change_observed"] is (False if role=="valid" else None)
    assert output.dumps==(1 if role=="valid" else 0)
    assert index["stored_index_complete"] and entry["source_modules_observed"]
    assert b"EXAMPLE-response" not in raw and b"EXAMPLE-private-prefix" not in raw


def test_auth_before_inner_rewrite_and_actual_registered_handler_are_distinct_facts(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=Router)
    original=app.app.app
    async def rewrite(value,receive,send):
        value["path"]="/v1/models"
        return await original(value,receive,send)
    app.app.app=rewrite
    response=asyncio.run(invoke(app,scope("missing",path="/health")))
    assert response[0]["status"]==200
    index,_,_,_=finish(case,writer,fake);entry=next(iter(index["invocations"].values()))
    assert entry["registered_handler_observed"] and entry["handler_return_observed"]
    assert entry["path_change_observed"] is True
    assert entry["token_results"]==[] and not entry["accepted_token_before_handler_observed"]
    assert entry["security"]=="UNKNOWN" and not entry["handler_coverage_verified"]


@pytest.mark.parametrize("layout",["missing","duplicate","wrong-path","wrong-method","wrong-call","no-dict"])
def test_unknown_registration_layout_does_not_modify_native_captured_callback(case,writer_factory,monkeypatch,layout):
    observers=[];originals=[]
    def factory(endpoint):
        router=Router(endpoint);route=router.routes[0]
        originals.append(endpoint);observers.append(router)
        if layout=="missing":router.routes=[]
        elif layout=="duplicate":router.routes.append(Route(endpoint))
        elif layout=="wrong-path":route.path="/other"
        elif layout=="wrong-method":route.methods={"POST"}
        elif layout=="wrong-call":route.dependant.call=lambda *a:None
        else:
            class Slotted:
                __slots__=("call",)
            route.dependant=Slotted();route.dependant.call=endpoint
        return router
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    application(observer,monkeypatch,router_factory=factory)
    router=observers[0]
    for route in router.routes:
        assert route.endpoint is originals[0]
    # No request was sent; absence is not a rejection or a PASS.
    index,_,_,_=finish(case,writer,fake)
    assert index["events"]==[] and index["invocations"]=={}


def test_both_original_dependant_and_later_included_route_use_same_wrapper(case,writer_factory,monkeypatch):
    routers=[]
    def factory(endpoint):
        router=Router(endpoint);routers.append(router);return router
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=factory)
    registered=routers[0].routes[0]
    assert registered.endpoint is registered.dependant.call
    included=Route(registered.endpoint)
    routers[0].routes=[included]
    assert asyncio.run(invoke(app,scope()))[0]["status"]==200
    index,_,_,_=finish(case,writer,fake)
    assert next(iter(index["invocations"].values()))["registered_handler_observed"]


def test_direct_call_without_registered_scope_does_not_claim_router_execution(case,writer_factory,monkeypatch):
    # The old EXAMPLE app calls the captured native function directly.
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch)
    assert asyncio.run(invoke(app,scope()))[0]["status"]==200
    index,_,_,_=finish(case,writer,fake)
    assert not next(iter(index["invocations"].values()))["registered_handler_observed"]


def test_callback_exception_object_and_asgi_callbacks_are_preserved(case,writer_factory,monkeypatch):
    error=RuntimeError(CANARY)
    async def failure():raise error
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,registry=failure,router_factory=Router)
    with pytest.raises(RuntimeError) as caught:asyncio.run(invoke(app,scope()))
    assert caught.value is error and sensor._ROUTE_REQUEST.get() is None
    index,_,_,_=finish(case,writer,fake)
    entry=next(iter(index["invocations"].values()))
    assert entry["registered_handler_observed"] and not entry["handler_return_observed"]
    assert any(event["point"]==sensor.HANDLER_POINT and event["phase"]=="RAISE" for event in index["events"])


@pytest.mark.parametrize("damage",["endpoint","dependant","scope-endpoint"])
def test_runtime_registration_mismatch_cannot_claim_registered_execution(case,writer_factory,monkeypatch,damage):
    class Changed(Route):
        async def dispatch(self,request):
            wrapped=self.dependant.call
            request.scope.update(route=self,endpoint=self.endpoint)
            if damage=="endpoint":self.endpoint=lambda:None
            elif damage=="dependant":self.dependant.call=lambda:None
            else:request.scope["endpoint"]=lambda:None
            return await wrapped(request)
    class ChangedRouter(Router):
        def __init__(self,endpoint):self.routes=[Changed(endpoint)]
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=ChangedRouter)
    assert asyncio.run(invoke(app,scope()))[0]["status"]==200
    index,_,_,_=finish(case,writer,fake)
    entry=next(iter(index["invocations"].values()))
    assert not entry["registered_handler_observed"] and entry["handler_return_observed"]


def test_duplicate_request_header_keeps_invocations_ambiguous(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=Router)
    async def two():await asyncio.gather(invoke(app,scope()),invoke(app,scope()))
    asyncio.run(two());index,_,_,_=finish(case,writer,fake)
    assert len(index["invocations"])==2 and len(index["ambiguous_asgi_request_sha256"])==1


@pytest.mark.parametrize("headers",[[],[(b"x-request-id",b"")],[(b"x-request-id",b"a"),(b"X-Request-ID",b"b")]])
def test_unusable_header_preserves_native_auth_without_handler_link(case,writer_factory,monkeypatch,headers):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=Router)
    value=scope();value["headers"]=[pair for pair in value["headers"] if pair[0]!=b"x-request-id"]+headers
    assert asyncio.run(invoke(app,value))[0]["status"]==200
    index,_,_,_=finish(case,writer,fake)
    assert index["events"]==[] and index["invocations"]=={}


@pytest.mark.parametrize("change",["flag","return-false","return-call","unknown-key","wrong-point","bad-hash"])
def test_handler_metadata_is_strict_and_phase_specific(case,writer_factory,monkeypatch,change):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=Router)
    asyncio.run(invoke(app,scope()));_,_,raw,_=finish(case,writer,fake)
    def mutate(rows):
        row=next(row for row in rows if row["event"]=="NATIVE" and row["payload"]["point"]==sensor.HANDLER_POINT)
        payload=row["payload"];value=payload["metadata"]["route_handler"]
        if change=="flag":value["registered_route_observed"]=1
        elif change=="return-false":value["handler_returned"]=False
        elif change=="return-call":value["handler_returned"]=True
        elif change=="unknown-key":value["raw_request"]=CANARY
        elif change=="wrong-point":payload["point"]="api_router.models"
        else:value["asgi_invocation_sha256"]="bad"
    with pytest.raises(ValueError):capture.parse_recording(revised(raw,mutate),case.prepared)


def old6(case,raw,crlf):
    root=case.manifest_path.parent
    frozen=(FIXTURES/"lifecycle-sensor-v6.py.txt").read_bytes().replace(b"\r\n",b"\n")
    if crlf:frozen=frozen.replace(b"\n",b"\r\n")
    (root/"guard_lifecycle_sensor.py").write_bytes(frozen)
    manifest=deepcopy(case.prepared);previous=manifest["assets"]["guard_lifecycle_sensor.py"]["sha256"]
    (root/"sitecustomize.py").write_bytes((root/"sitecustomize.py").read_bytes().replace(previous.encode(),hashlib.sha256(frozen).hexdigest().encode()))
    manifest.update(profile=profiles.V6_PROFILE,source_sha256=profiles.V6_SOURCE_SHA)
    for name in manifest["assets"]:
        data=(root/name).read_bytes();manifest["assets"][name]={"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
    case.manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
    def update(rows):
        rows[0]["payload"].update(profile=profiles.V6_PROFILE,schema=profiles.V6_SCHEMA,
            sensor_sha256=hashlib.sha256(frozen).hexdigest())
    return manifest,revised(raw,update)


@pytest.mark.parametrize("crlf",[False,True])
def test_profile6_frozen_assets_and_existing_auth_observations_are_unchanged(case,writer_factory,monkeypatch,crlf):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch)
    asyncio.run(invoke(app,scope()));writer.close("SEALED")
    manifest,raw=old6(case,bytes(fake.data),crlf)
    assert lifecycle.load_prepared(case.manifest_path)==manifest
    rows,_=capture.parse_recording(raw,manifest)
    assert len(profiles.source_hashes(profiles.V6_PROFILE))==25
    assert sensor.HANDLER_POINT not in profiles.point_sources(profiles.V6_PROFILE)
    assert route_index.index([{"capture":{"records":rows,"provenance":{"owner":{}}}}])["events"]


def test_profile6_selection_events_and_typed_weight_frames_still_parse(case):
    from tests.test_lifecycle_session import native_payload
    payload=native_payload();payload.update(requests=[],selection_observed=False,metadata={"request_selection":{
        "asgi_request_sha256":None,"asgi_invocation_sha256":"a"*64,
        "entry_path_sha256":None,"entry_root_path_sha256":None,"entry_method_sha256":None,
        "priority_observed":False,"session_observed":False,"rank_observed":False,"request_id_observed":False,
        "selected_priority":None,"selected_session_sha256":None,"selected_rank":None,"selected_request_id_sha256":None}})
    def add(rows):
        rows.insert(-1,{**rows[-1],"event":"SELECTION","payload":payload})
    manifest,raw=old6(case,revised(case.raw,add),False)
    rows,_=capture.parse_recording(raw,manifest)
    assert route_index.selection_index([{"capture":{"records":rows,"provenance":{"owner":{}}}}])["events"]
    assert profiles.valid_call_frame({"point":"DefaultModelLoader.load_weights","request_sha256":[],
        "weight_call_sha256":"a"*64,"weight_model_sha256":None},profiles.V6_PROFILE)


def test_profile6_does_not_acquire_new_handler_point_or_metadata(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=Router)
    asyncio.run(invoke(app,scope()));_,_,raw,_=finish(case,writer,fake)
    manifest,raw=old6(case,raw,False)
    with pytest.raises(ValueError):capture.parse_recording(raw,manifest)


@pytest.mark.parametrize("failure",["unsealed","bounded","module","recording","limit"])
def test_partial_capture_retains_handler_facts_without_coverage_claim(case,writer_factory,monkeypatch,failure):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=Router)
    asyncio.run(invoke(app,scope()));_,_,_,recordings=finish(case,writer,fake)
    rows=recordings[0]["capture"]["records"]
    if failure=="unsealed":rows.pop()
    elif failure=="bounded":rows[-1]["payload"]["reason"]="BOUND_REACHED"
    elif failure=="module":rows[:]=[row for row in rows if row["event"]!="MODULE"]
    elif failure=="recording":recordings.append({"capture":None})
    else:monkeypatch.setattr(route_index,"MAX_EVENTS",7)
    index=route_index.handler_index(recordings)
    assert not index["stored_index_complete"] and index["events"] and index["security"]=="UNKNOWN"
    assert not index["whole_route_coverage_verified"]


def test_product_session_collection_stores_new_index_with_raw_records_and_unknown_verdict(case,writer_factory,monkeypatch):
    observer,writer,fake=begin(case,writer_factory,monkeypatch)
    app,_,_=application(observer,monkeypatch,router_factory=Router)
    asyncio.run(invoke(app,scope()));_,rows,raw,_=finish(case,writer,fake);case.raw=raw
    owner={"container_id":"a"*64}
    inventory={"owner":owner,"metadata":{"files":[{"pid":42,"start_ticks":99,"regular":True,"uid":1000,
        "mode":0o100600,"device":1,"inode":2,"bytes":len(raw),"mtime_ns":3,"ctime_ns":4}],
        "processes":[],"directory_before":{"uid":1000}}}
    monkeypatch.setattr(lifecycle_session,"inventory",lambda *a,**k:deepcopy(inventory))
    monkeypatch.setattr(lifecycle_session,"_complete",lambda *a:True)
    from guard.executors import lifecycle_capture
    original=lifecycle_capture.capture
    def collected(*a,**k):
        value=original(*a,**k);value["provenance"]["owner"]=owner
        return value
    monkeypatch.setattr(lifecycle_session,"capture",collected)
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:Docker(case))
    output=case.root/"registered-handler-session.json"
    args=["capture-lifecycle-session","--env",str(case.root/"env.json"),"--target",str(case.root/"target.json"),
        "--prepared",str(case.manifest_path),"--container",case.source["container"],"--image",IMAGE,"--output",str(output),"--execute"]
    result=CliRunner().invoke(cli,args)
    assert result.exit_code==0,result.output
    stored=json.loads(output.read_bytes())
    assert stored["recordings"][0]["capture"]["records"]==rows
    assert next(iter(stored["route_handler_index"]["invocations"].values()))["registered_handler_observed"]
    assert stored["security"]=="UNKNOWN" and stored["data_origin"]=="EXAMPLE" and not stored["product_connected"]
    assert CANARY.encode() not in output.read_bytes()

