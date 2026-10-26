"""EXAMPLE producer contracts; no native engine, daemon or GPU."""
import asyncio
import gc
import hashlib
import importlib.machinery
import importlib.util
import json
import marshal
import os
from pathlib import Path
from types import SimpleNamespace
import types
import threading
import weakref

import pytest

from guard.adapters import vllm_lifecycle_sensor as sensor

CANARY="EXAMPLE_LIFECYCLE_PRIVATE_6FB53A"
CONTEXT={"pid":42,"start_ticks":99,"uid":1000,"boot_sha256":"1"*64,
         "pid_namespace":23,"time_namespace":24,"time_offsets_sha256":"2"*64}


class FakeOS:
    O_WRONLY=1;O_CREAT=2;O_EXCL=4;O_NOFOLLOW=8
    def __init__(self):
        self.data=bytearray();self.writes=[];self.closed=[];self.failure=False;self.names=set();self.next_fd=11
    def getpid(self):return 42
    def geteuid(self):return 1000
    def fstat(self,fd):return SimpleNamespace(st_uid=1000,st_mode=0o40700)
    def open(self,name,flags,mode,*,dir_fd):
        assert flags==self.O_WRONLY|self.O_CREAT|self.O_EXCL|self.O_NOFOLLOW
        assert mode==0o600 and dir_fd==10
        if name in self.names:raise FileExistsError(CANARY)
        self.names.add(name);self.name=name
        descriptor=self.next_fd;self.next_fd+=1
        return descriptor
    def close(self,fd):self.closed.append(fd)
    def write(self,fd,data):
        if self.failure:raise OSError(CANARY)
        self.writes.append(bytes(data));self.data.extend(data);return len(data)


class SilentThread:
    def __init__(self,**kwargs):pass
    def start(self):pass


@pytest.fixture
def writer_factory(monkeypatch):
    def build(*,session="a"*32,settings_sha="b"*64):
        fake=FakeOS()
        monkeypatch.setattr(sensor,"os",fake)
        monkeypatch.setattr(sensor,"_open_directory",lambda p:10)
        monkeypatch.setattr(sensor,"threading",SimpleNamespace(RLock=threading.RLock,Thread=SilentThread,Event=threading.Event))
        config={"session":session,"output_dir":"/guard-lifecycle/data/"+session,
                "control_path":"/guard-lifecycle/data/"+session+"/SEALED"}
        writer=sensor.Writer(config,settings_sha,dict(CONTEXT))
        return writer,fake
    return build


class RecordingObserver(sensor.Observer):
    def __init__(self):
        super().__init__({"session":"a"*32},"b"*64)
        self.recorded=[]
    def emit(self,event,payload):
        self.recorded.append({"event":event,"payload":payload})
    def _writer(self):
        return SimpleNamespace(context=dict(CONTEXT))


def test_slot_metadata_and_properties_never_execute_caller_getters():
    class Slot:
        __slots__=("request_id",)
    item=Slot();item.request_id=CANARY
    assert sensor.attrs(item).get("request_id")==CANARY
    class Property:
        @property
        def request_id(self):raise AssertionError(CANARY)
    assert sensor.attrs(Property()).get("request_id") is None
    assert sensor.id_sha(CANARY)==hashlib.sha256(CANARY.encode()).hexdigest()


@pytest.mark.parametrize("failure",[None,RuntimeError,KeyboardInterrupt,SystemExit])
def test_wrapper_preserves_native_return_or_same_exception_without_raw_values(failure):
    observer=RecordingObserver()
    returned=object()
    error=failure(CANARY) if failure else None
    def original(owner,request_id):
        if error:raise error
        return returned
    wrapped=observer.wrap("vllm.v1.engine.async_llm","AsyncLLM.abort",original)
    if failure:
        with pytest.raises(failure) as caught:wrapped(object(),CANARY)
        assert caught.value is error
    else:
        assert wrapped(object(),CANARY) is returned
    assert sensor._CHAIN.get()==()
    assert CANARY not in json.dumps(observer.recorded)
    assert observer.recorded[-1]["payload"]["phase"]==("RAISE" if failure else "RETURN")


def test_input_iterator_is_consumed_only_by_the_native_function():
    observer=RecordingObserver();seen=[]
    def values():
        seen.append("native-read");yield CANARY
    def original(owner,request_ids):
        return [next(request_ids)]
    wrapped=observer.wrap("vllm.v1.engine.output_processor","OutputProcessor.abort_requests",original)
    assert wrapped(SimpleNamespace(),values())==[CANARY]
    assert seen==["native-read"] and CANARY not in json.dumps(observer.recorded)


def test_async_context_isolated_across_interleaved_native_calls():
    observer=RecordingObserver();seen={}
    async def original(owner,request_id):
        await asyncio.sleep(0)
        seen[request_id]=sensor._CHAIN.get()[-1]["request_sha256"]
        return request_id
    wrapped=observer.wrap("vllm.v1.engine.async_llm","AsyncLLM.abort",original)
    async def run():
        return await asyncio.gather(wrapped(object(),"EXAMPLE_A"),wrapped(object(),"EXAMPLE_B"))
    assert asyncio.run(run())==["EXAMPLE_A","EXAMPLE_B"]
    assert seen=={v:[sensor.id_sha(v)] for v in ("EXAMPLE_A","EXAMPLE_B")}
    assert sensor._CHAIN.get()==()


def test_observer_never_keeps_block_or_pool_alive():
    class Block:
        __slots__=("block_id","ref_cnt","__weakref__")
    class Pool:pass
    block=Block();block.block_id=0;block.ref_cnt=1
    pool=Pool();pool.blocks=[block]
    child=SimpleNamespace(block_pool=pool,req_to_blocks={CANARY:[block]})
    coordinator=SimpleNamespace(single_type_managers=(child,))
    owner=SimpleNamespace(coordinator=coordinator)
    observer=RecordingObserver()
    facts=observer.blocks(owner,(CANARY,))
    assert facts["complete"] and CANARY not in json.dumps(facts)
    assert observer.pool_snapshot(pool)["blocks"][0]["ref_count"]==1
    block_ref,pool_ref=weakref.ref(block),weakref.ref(pool)
    del owner,coordinator,child,pool,block
    gc.collect()  # Test-only lifetime assertion; the producer never collects native objects.
    assert block_ref() is None and pool_ref() is None


def test_source_loader_verifies_cache_code_and_preserves_original_behavior(tmp_path,monkeypatch):
    path=tmp_path/"example_native.py";path.write_text("class Example:\n    def work(self,value): return value+1\n")
    name="EXAMPLE_native_module"
    monkeypatch.setitem(sensor.SOURCE_SHA,name,hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setitem(sensor.POINTS,name,{"Example":("work",)})
    original=importlib.machinery.SourceFileLoader(name,str(path))
    original.get_code(name)  # Exercise an existing pyc, not only direct compilation.
    module=types.ModuleType(name)
    observer=RecordingObserver()
    sensor.CheckedLoader(name,original,observer).exec_module(module)
    assert module.Example().work(2)==3
    assert observer.recorded[0]["payload"]["verified"] is True
    assert observer.recorded[-1]["payload"]["phase"]=="RETURN"


@pytest.mark.parametrize("kind",["source","cache"])
def test_unreviewed_source_or_executed_code_never_gets_verified_hooks(tmp_path,monkeypatch,kind):
    path=tmp_path/"example_native.py"
    text="class Example:\n    def work(self,value): return value+1\n"
    path.write_text(text);name="EXAMPLE_native_module"
    monkeypatch.setitem(sensor.SOURCE_SHA,name,hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setitem(sensor.POINTS,name,{"Example":("work",)})
    loader=importlib.machinery.SourceFileLoader(name,str(path))
    loader.get_code(name)
    if kind=="source":
        monkeypatch.setitem(sensor.SOURCE_SHA,name,"0"*64)
    else:
        cache=Path(importlib.util.cache_from_source(str(path)))
        raw=cache.read_bytes()
        cache.write_bytes(raw[:16]+marshal.dumps(compile(text.replace("value+1","value+9"),str(path),"exec")))
    module=types.ModuleType(name);observer=RecordingObserver()
    sensor.CheckedLoader(name,loader,observer).exec_module(module)
    assert module.Example().work(2)==(3 if kind=="source" else 11)
    assert observer.recorded==[{"event":"MODULE","payload":{"module":name,"verified":False}}]


def test_module_instrumentation_is_atomic_when_a_required_method_is_missing(monkeypatch):
    class Example:
        @staticmethod
        def work(value):return value
    original=vars(Example)["work"]
    module=SimpleNamespace(__name__="EXAMPLE_native_module",Example=Example)
    monkeypatch.setitem(sensor.POINTS,module.__name__,{"Example":("work","missing")})
    monkeypatch.setitem(sensor.SOURCE_SHA,module.__name__,"0"*64)
    with pytest.raises(ValueError):RecordingObserver().instrument(module)
    assert vars(Example)["work"] is original


def test_staticmethod_descriptor_and_native_ID_mapping_survive_instrumentation(monkeypatch):
    class InputProcessor:
        @staticmethod
        def assign_request_id(request):
            request.external_req_id=request.request_id
            request.request_id += "-internal"
    module=SimpleNamespace(__name__="vllm.v1.engine.input_processor",InputProcessor=InputProcessor)
    observer=RecordingObserver();observer.instrument(module)
    request=SimpleNamespace(request_id=CANARY,external_req_id=None)
    module.InputProcessor.assign_request_id(request)
    assert isinstance(vars(InputProcessor)["assign_request_id"],staticmethod)
    result=observer.recorded[-1]["payload"]
    assert result["metadata"]["external_request_sha256"]==sensor.id_sha(CANARY)
    assert result["requests"][0]["request_sha256"]==sensor.id_sha(CANARY+"-internal")
    assert CANARY not in json.dumps(observer.recorded)


@pytest.mark.parametrize("raises",[False,True])
@pytest.mark.parametrize("more_body",[None,False,True,CANARY])
def test_ASGI_observation_never_adds_receive_send_or_cleanup(monkeypatch,raises,more_body):
    observer=RecordingObserver();monkeypatch.setattr(sensor,"_OBSERVER",observer)
    incoming={"type":"http.disconnect"};outgoing={"type":"http.response.body","body":CANARY.encode()}
    if more_body is not None:outgoing["more_body"]=more_body
    calls=[];answer=object();error=RuntimeError(CANARY)
    async def receive():calls.append("receive");return incoming
    async def send(value):assert value is outgoing;calls.append("send");return answer
    async def app(scope,received,sent):
        assert await received() is incoming
        assert await sent(outgoing) is answer
        if raises:raise error
        return answer
    scope={"type":"http","path":"/v1/completions","headers":[(b"x-request-id",CANARY.encode())]}
    if raises:
        with pytest.raises(RuntimeError) as caught:asyncio.run(sensor.Middleware(app)(scope,receive,send))
        assert caught.value is error
    else:
        assert asyncio.run(sensor.Middleware(app)(scope,receive,send)) is answer
    assert calls==["receive","send"] and CANARY not in json.dumps(observer.recorded)
    body_event=next(r["payload"] for r in observer.recorded if r["payload"]["phase"]=="BODY_SEND_RETURN")
    assert body_event["more_body"] is (False if more_body is None else more_body if type(more_body) is bool else None)
    assert observer.recorded[-1]["payload"]["phase"]==("APP_RAISE" if raises else "APP_RETURN")


def test_recording_is_append_only_bounded_and_closed_without_native_cleanup(writer_factory):
    writer,fake=writer_factory()
    for _ in range(sensor.MAX_ROWS+3):
        writer.write("ASGI",{"request_sha256":"c"*64,"phase":"BODY_SEND_RETURN","more_body":True})
    rows=[json.loads(line) for line in bytes(fake.data).splitlines()]
    assert rows[0]["event"]=="START" and rows[-1]["payload"]["reason"]=="BOUND_REACHED"
    assert len(rows)<=sensor.MAX_ROWS and len(fake.data)<=sensor.MAX_BYTES and writer.closed
    assert fake.name=="pid-42-99.jsonl" and fake.closed.count(11)==1
    assert all(rows[i]["previous_sha256"]==hashlib.sha256(fake.writes[i-1]).hexdigest() for i in range(1,len(rows)))


def test_recording_IO_failure_never_replaces_a_native_return(writer_factory,monkeypatch):
    writer,fake=writer_factory();observer=RecordingObserver()
    observer.writer=writer;observer.pid=42
    monkeypatch.setattr(observer,"_writer",lambda:writer)
    monkeypatch.setattr(observer,"emit",lambda e,p:writer.write(e,p))
    fake.failure=True
    value=object()
    wrapped=observer.wrap("vllm.v1.engine.async_llm","AsyncLLM.abort",lambda *a:value)
    assert wrapped(object(),CANARY) is value
    assert writer.closed and CANARY.encode() not in fake.data


def test_default_install_has_no_import_hook_or_filesystem_side_effect(monkeypatch):
    monkeypatch.setattr(sensor,"_OBSERVER",None)
    monkeypatch.delenv(sensor.CONFIG_ENV,raising=False)
    before=list(sensor.sys.meta_path)
    assert sensor.install() is False and sensor.sys.meta_path==before


def test_repeated_empty_warmup_calls_do_not_exhaust_recording_window():
    observer=RecordingObserver();calls=[]
    def original(owner,scheduler_output):calls.append(True)
    wrapped=observer.wrap("vllm.v1.worker.gpu_model_runner","GPUModelRunner.execute_model",original)
    output=SimpleNamespace(num_scheduled_tokens={},finished_req_ids=set())
    for _ in range(1000):wrapped(SimpleNamespace(),output)
    assert len(calls)==1000 and len(observer.recorded)==2


def test_structural_code_digest_survives_cache_deserialization_and_detects_changes():
    code=compile("def value(): return (b'x',1.5,2j,...)\n","EXAMPLE-native.py","exec")
    assert sensor.code_digest(code)==sensor.code_digest(marshal.loads(marshal.dumps(code)))
    changed=compile("def value(): return (b'y',1.5,2j,...)\n","EXAMPLE-native.py","exec")
    assert sensor.code_digest(code)!=sensor.code_digest(changed)


def test_failed_control_thread_closes_only_its_owned_recording(writer_factory,monkeypatch):
    writer,fake=writer_factory()
    writer.close("SEALED")
    class BrokenThread(SilentThread):
        def start(self):raise RuntimeError(CANARY)
    monkeypatch.setattr(sensor.threading,"Thread",BrokenThread)
    second=sensor.Writer(writer.config,"b"*64,{**CONTEXT,"start_ticks":100})
    assert second.closed
    assert json.loads(fake.writes[-1])["payload"]["reason"]=="CONTROL_UNAVAILABLE"
    assert CANARY.encode() not in fake.data


def test_seal_uses_a_fresh_owned_control_file_and_stops_only_recording(writer_factory,monkeypatch):
    writer,fake=writer_factory()
    monkeypatch.setattr(sensor,"load_settings",lambda p:(writer.config,"b"*64))
    sensor.seal()
    assert "SEALED" in fake.names and writer.closed is False
    monkeypatch.setattr(sensor,"Path",lambda p:SimpleNamespace(exists=lambda:"SEALED" in fake.names))
    writer._watch()
    assert writer.closed and json.loads(fake.writes[-1])["payload"]["reason"]=="SEALED"


def test_existing_recording_is_never_reopened_or_truncated(writer_factory):
    writer,fake=writer_factory()
    writer.close("SEALED")
    before=bytes(fake.data)
    with pytest.raises(FileExistsError):sensor.Writer(writer.config,"b"*64,dict(CONTEXT))
    assert bytes(fake.data)==before
