"""EXAMPLE request-context associations and immutable v1 read compatibility."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_lifecycle_sensor as sensor, vllm_lifecycle_profiles as profiles
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture, lifecycle_session as session
from tests.test_lifecycle_capture import case, external, revised
from tests.test_lifecycle_sensor import writer_factory, RecordingObserver, CANARY
from tests.test_lifecycle_session import group, collect, native_payload


MODULE = "vllm.v1.engine.input_processor"
POINT = "InputProcessor.assign_request_id"
LEGACY = Path(__file__).parent / "fixtures/lifecycle-sensor-v1.py.txt"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def scope(header=CANARY.encode()):
    return {"type":"http", "path":"/v1/completions", "headers":[] if header is None else [(b"X-Request-ID",header)]}


def assigned(observer, label=CANARY, suffix="fixture"):
    request=SimpleNamespace(request_id="cmpl-"+label+"-0",external_req_id=None)
    answer=object()
    def original(value):
        assert value is request
        value.external_req_id=value.request_id
        value.request_id+="-"+suffix
        return answer
    wrapped=observer.wrap(MODULE,POINT,original)
    assert wrapped(request) is answer
    return request


def returns(observer):
    return [r["payload"] for r in observer.recorded if r["event"]=="NATIVE" and r["payload"]["phase"]=="RETURN"]


def test_context_association_uses_observed_native_ID_without_reconstructing_prefix(monkeypatch):
    observer=RecordingObserver();monkeypatch.setattr(sensor,"_OBSERVER",observer)
    calls=[]
    async def receive():raise AssertionError("unexpected receive")
    async def send(value):calls.append(value)
    async def app(s,r,w):
        request=assigned(observer)
        assert request.external_req_id=="cmpl-"+CANARY+"-0"
        return request
    async def parent():
        result=await sensor.Middleware(app)(scope(),receive,send)
        assert sensor._ASGI_REQUEST.get() is None
        return result
    result=asyncio.run(parent());row=returns(observer)[0]
    assert row["metadata"]=={"external_request_sha256":digest(result.external_req_id),"asgi_request_sha256":digest(CANARY)}
    assert row["requests"][0]["request_sha256"]==digest(result.request_id)
    assert digest(CANARY)!=digest(result.external_req_id)
    assert not calls and CANARY not in json.dumps(observer.recorded)


def test_concurrent_request_contexts_do_not_cross_associate(monkeypatch):
    observer=RecordingObserver();monkeypatch.setattr(sensor,"_OBSERVER",observer)
    async def noop(*a):pass
    async def app(s,r,w):
        label=s["headers"][0][1].decode()
        await asyncio.sleep(0)
        assigned(observer,label)
        await asyncio.sleep(0)
    async def parent():
        middleware=sensor.Middleware(app)
        await asyncio.gather(*(middleware(scope(label.encode()),noop,noop) for label in (CANARY+"-a",CANARY+"-b")))
        assert sensor._ASGI_REQUEST.get() is None
    asyncio.run(parent())
    actual={r["metadata"]["external_request_sha256"]:r["metadata"]["asgi_request_sha256"] for r in returns(observer)}
    assert actual=={digest("cmpl-"+label+"-0"):digest(label) for label in (CANARY+"-a",CANARY+"-b")}
    assert CANARY not in json.dumps(observer.recorded)


@pytest.mark.parametrize("failure",[RuntimeError(CANARY),asyncio.CancelledError(CANARY)])
def test_native_exception_or_cancellation_preserves_identity_and_resets_context(monkeypatch,failure):
    observer=RecordingObserver();monkeypatch.setattr(sensor,"_OBSERVER",observer)
    async def noop(*a):pass
    async def app(s,r,w):
        assigned(observer)
        raise failure
    async def parent():
        with pytest.raises(type(failure)) as caught:await sensor.Middleware(app)(scope(),noop,noop)
        assert caught.value is failure and sensor._ASGI_REQUEST.get() is None
    asyncio.run(parent())
    assert observer.recorded[-1]["payload"]["phase"]=="APP_RAISE"
    assert CANARY not in json.dumps(observer.recorded)


@pytest.mark.parametrize("header",[None,b"",b"x"*513])
def test_nested_missing_or_unsupported_header_shadows_and_restores_outer_context(monkeypatch,header):
    observer=RecordingObserver();monkeypatch.setattr(sensor,"_OBSERVER",observer)
    async def noop(*a):pass
    async def inner(s,r,w):assigned(observer,"inner")
    async def outer(s,r,w):
        assigned(observer,"before")
        await sensor.Middleware(inner)(scope(header),noop,noop)
        assigned(observer,"after")
    asyncio.run(sensor.Middleware(outer)(scope(),noop,noop))
    rows=returns(observer)
    assert [r["metadata"].get("asgi_request_sha256") for r in rows]==[digest(CANARY),None,digest(CANARY)]


@pytest.mark.parametrize("context",[None,(-1,"a"*64)])
def test_absent_or_fork_inherited_context_does_not_supply_header(context):
    observer=RecordingObserver();token=sensor._ASGI_REQUEST.set(context)
    try:assigned(observer)
    finally:sensor._ASGI_REQUEST.reset(token)
    assert "asgi_request_sha256" not in returns(observer)[0]["metadata"]


def test_long_lived_output_handler_never_uses_inherited_header_as_assignment():
    observer=RecordingObserver();token=sensor._ASGI_REQUEST.set((sensor.os.getpid(),"a"*64))
    try:
        value=observer.snapshot("OutputProcessor.add_request",(SimpleNamespace(),SimpleNamespace(request_id=CANARY)),{})
        assert "asgi_request_sha256" not in value["metadata"]
    finally:sensor._ASGI_REQUEST.reset(token)


def test_observed_context_round_trips_through_writer_parser_and_exact_event_index(case,writer_factory,monkeypatch):
    observer=RecordingObserver();monkeypatch.setattr(sensor,"_OBSERVER",observer)
    writer,fake=writer_factory(session=case.prepared["session"],settings_sha=case.prepared["assets"]["settings.json"]["sha256"])
    writer.write("MODULE",{"module":MODULE,"verified":True,"source_sha256":sensor.SOURCE_SHA[MODULE],"code_sha256":"c"*64})
    monkeypatch.setattr(observer,"emit",lambda event,payload:writer.write(event,payload))
    async def noop(*a):pass
    async def app(s,r,w):assigned(observer)
    asyncio.run(sensor.Middleware(app)(scope(),noop,noop));writer.close("SEALED")
    raw=bytes(fake.data);rows,summary=capture.parse_recording(raw,case.prepared)
    index=session.request_index([{"capture":{"records":rows,"provenance":{"owner":{"container_id":"a"*64}}}}])
    domain=next(iter(index["domains"].values()));entry=domain["assignments"][0]
    assert entry["asgi_request_sha256"]==digest(CANARY)
    assert entry["external_sha256"]==digest("cmpl-"+CANARY+"-0")
    assert entry["internal_sha256"]==digest("cmpl-"+CANARY+"-0-fixture")
    assert rows[entry["event"][1]]["payload"]["phase"]=="RETURN"
    assert summary["recording_closed"] and not index["producer_authenticated"]
    assert CANARY.encode() not in raw


def legacy(case, crlf=False):
    manifest=deepcopy(case.prepared)
    raw=LEGACY.read_bytes()
    if crlf:raw=raw.replace(b"\n",b"\r\n")
    root=case.manifest_path.parent
    previous=manifest["assets"]["guard_lifecycle_sensor.py"]["sha256"]
    (root/"guard_lifecycle_sensor.py").write_bytes(raw)
    bootstrap=(root/"sitecustomize.py").read_bytes().replace(previous.encode(),hashlib.sha256(raw).hexdigest().encode())
    (root/"sitecustomize.py").write_bytes(bootstrap)
    manifest["profile"]=profiles.LEGACY_PROFILE
    for name in ("guard_lifecycle_sensor.py","sitecustomize.py"):
        data=(root/name).read_bytes()
        manifest["assets"][name]={"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
    case.manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
    def change(rows):
        rows[0]["payload"].update(profile=profiles.LEGACY_PROFILE,schema=profiles.LEGACY_SCHEMA,
            sensor_sha256=manifest["assets"]["guard_lifecycle_sensor.py"]["sha256"])
    recording=revised(case.raw,change)
    return manifest,recording


@pytest.mark.parametrize("crlf",[False,True])
def test_known_v1_source_assets_and_recording_remain_readable_without_import_or_upgrade(case,crlf):
    manifest,raw=legacy(case,crlf)
    assert lifecycle.load_prepared(case.manifest_path)==manifest
    rows,summary=capture.parse_recording(raw,manifest)
    assert rows[0]["payload"]["schema"]==profiles.LEGACY_SCHEMA
    assert "asgi_request_sha256" not in json.dumps(rows)
    assert summary["whole_run_coverage_verified"] is False
    assert raw==revised(case.raw,lambda values:values[0]["payload"].update(rows[0]["payload"]))


@pytest.mark.parametrize("kind",["changed-v1-source","v1-source-v2-profile","current-source-v1-profile","untyped-profile"])
def test_self_consistent_manifest_cannot_register_unknown_or_cross_version_source(case,kind):
    if kind in ("changed-v1-source","v1-source-v2-profile"):
        manifest,_=legacy(case)
        if kind=="changed-v1-source":
            source=case.manifest_path.parent/"guard_lifecycle_sensor.py"
            raw=source.read_bytes()+b"\n";source.write_bytes(raw)
            manifest["assets"][source.name]={"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)}
        else:manifest["profile"]=sensor.PROFILE
    else:
        manifest=deepcopy(case.prepared)
        manifest["profile"]=profiles.LEGACY_PROFILE if kind=="current-source-v1-profile" else []
    case.manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
    with pytest.raises(ValueError):lifecycle.load_prepared(case.manifest_path)


@pytest.mark.parametrize("value",[None,True,7,"CALL","a"*63,"a"*65,[]])
def test_resealed_invalid_header_assignment_metadata_is_rejected(case,value):
    def change(rows):
        data=native_payload();data["metadata"]["asgi_request_sha256"]=value
        rows.insert(-1,{**rows[-1],"event":"NATIVE","payload":data})
    with pytest.raises(ValueError):capture.parse_recording(revised(case.raw,change),case.prepared)


@pytest.mark.parametrize("legacy_profile",[False,True])
def test_header_association_is_versioned_and_only_valid_at_native_assignment(case,legacy_profile):
    prepared,raw=legacy(case) if legacy_profile else (case.prepared,case.raw)
    def change(rows):
        payload=native_payload() if legacy_profile else native_payload(point="OutputProcessor.add_request")
        if not legacy_profile:payload["source_sha256"]=sensor.SOURCE_SHA["vllm.v1.engine.output_processor"]
        payload["metadata"]["asgi_request_sha256"]="d"*64
        rows.insert(-1,{**rows[-1],"event":"NATIVE","payload":payload})
    with pytest.raises(ValueError):capture.parse_recording(revised(raw,change),prepared)


def test_v1_does_not_accept_new_context_key_hidden_in_nested_metadata(case):
    prepared,raw=legacy(case)
    def change(rows):
        payload=native_payload()
        payload["metadata"]["ownership"]={"asgi_request_sha256":"d"*64}
        rows.insert(-1,{**rows[-1],"event":"NATIVE","payload":payload})
    with pytest.raises(ValueError):capture.parse_recording(revised(raw,change),prepared)


def test_reused_header_keeps_distinct_native_IDs_and_explicit_ambiguity(group):
    for pid,external_id,internal_id in ((42,"6"*64,"e"*64),(43,"7"*64,"f"*64)):
        def change(rows):
            data=native_payload(external=external_id,request=internal_id)
            data["metadata"]["asgi_request_sha256"]="d"*64
            rows[-2]["payload"]=data
        group.raw_by_pid[pid]=revised(group.raw_by_pid[pid],change)
    result=collect(group);domain=next(iter(result["request_index"]["domains"].values()))
    assert domain["ambiguous_external_sha256"]==[]
    assert domain["ambiguous_asgi_request_sha256"]==["d"*64]
    assert len(domain["assignments"])==2
    assert {r["internal_sha256"] for r in domain["assignments"]}=={"e"*64,"f"*64}
    assert all(r["asgi_request_sha256"]=="d"*64 for r in domain["assignments"])
    assert len(domain["fingerprints"]["d"*64])>=4
    assert result["producer_authenticated"] is False and result["whole_run_coverage_verified"] is False


def test_missing_header_metadata_never_invents_link_from_ID_prefix_or_existing_ASGI_row(group):
    domain=next(iter(collect(group)["request_index"]["domains"].values()))
    assert domain["ambiguous_asgi_request_sha256"]==[]
    assert all("asgi_request_sha256" not in row for row in domain["assignments"])


@pytest.mark.parametrize("phase",["CALL","RAISE"])
def test_unfinished_native_assignment_does_not_establish_header_to_internal_link(group,phase):
    def change(rows):
        payload=native_payload(phase=phase)
        payload["metadata"]["asgi_request_sha256"]="9"*64
        rows[-2]["payload"]=payload
    group.raw_by_pid[42]=revised(group.raw_by_pid[42],change)
    domain=next(iter(collect(group)["request_index"]["domains"].values()))
    assert domain["assignments"]==[] and "9"*64 not in domain["fingerprints"]
