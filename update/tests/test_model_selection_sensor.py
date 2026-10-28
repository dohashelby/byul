"""EXAMPLE loaded-object/call observations; no engine, tensor or model identity proof."""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import weakref

import pytest

from guard.adapters import vllm_lifecycle_sensor as sensor, vllm_lifecycle_profiles as profiles
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture, lifecycle_session as session
from tests.test_lifecycle_capture import case, external, revised
from tests.test_lifecycle_sensor import writer_factory, RecordingObserver, CONTEXT, CANARY

BASE="vllm.model_executor.model_loader.base_loader"
WEIGHTS="vllm.model_executor.model_loader.default_loader"
CPU="vllm.v1.worker.cpu_model_runner"
GPU="vllm.v1.worker.gpu_model_runner"
FIXTURES=Path(__file__).parent/"fixtures"


def native_method(name,namespace):
    value=json.loads((FIXTURES/"model-selection-native-methods.json").read_text(encoding="utf-8"))[name]
    module=BASE if name.startswith("Base") else CPU if name.startswith("CPU") else GPU
    assert value["full_source_sha256"]==sensor.SOURCE_SHA[module]
    function=ast.parse(value["method"]).body[0]
    function.decorator_list=[]  # Full module, tracing, constructors and Torch are not executed.
    tree=ast.Module(body=[ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0),function],type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree),"reviewed-native-method","exec"),namespace)
    return namespace[function.name]


class Model:
    def __init__(self):self.calls=[];self.answer=object()
    def eval(self):return self
    def __call__(self,**kwargs):self.calls.append(kwargs);return self.answer
    def __hash__(self):raise AssertionError("model hash must not execute")
    def __iter__(self):raise AssertionError("model/tensor iteration must not execute")


def metadata():
    return {"owner_model_sha256":None,"argument_model_sha256":None,"returned_model_sha256":None,
        "configured_model_sha256":None,"configured_revision_sha256":None,"load_format_sha256":None,
        "load_dummy_weights":None,"lora_config_present":None,"speculative_config_present":None}


def model_payload(point="GPUModelRunner._model_forward",phase="RETURN",model=None):
    module=BASE if point.startswith("Base") else WEIGHTS if point.startswith("Default") else CPU if point.startswith("CPU") else GPU
    return {"point":point,"phase":phase,"source_sha256":sensor.SOURCE_SHA[module],"requests":[],
        "metadata":{"model_selection":metadata() if model is None else model},"selection_observed":False,
        "observation_started_ns":0,"observation_finished_ns":0}


def append_payload(raw,payload):
    def change(rows):rows.insert(-1,{**rows[-1],"event":"NATIVE","payload":payload})
    return revised(raw,change)


def test_pinned_native_load_and_forward_bodies_preserve_object_and_argument_identity(case,writer_factory,monkeypatch):
    observer=RecordingObserver();writer,fake=writer_factory(session=case.prepared["session"],settings_sha=case.prepared["assets"]["settings.json"]["sha256"])
    monkeypatch.setattr(observer,"emit",lambda event,payload:writer.write(event,payload))
    for module in (BASE,WEIGHTS,CPU,GPU):
        writer.write("MODULE",{"module":module,"verified":True,"source_sha256":sensor.SOURCE_SHA[module],"code_sha256":"c"*64})
    model=Model();loaded=[]
    cfg=SimpleNamespace(model=CANARY,revision=CANARY+"-revision",dtype=object())
    load_cfg=SimpleNamespace(device=None,load_format="fixture-format")
    vllm_cfg=SimpleNamespace(model_config=cfg,load_config=load_cfg,device_config=SimpleNamespace(device="fixture"))
    loader=SimpleNamespace(load_config=load_cfg,create_model=lambda **kw:model)
    def weights(owner,value,config):
        assert owner is loader and value is model and config is cfg
        loaded.append(value)
    loader.load_weights=observer.wrap(WEIGHTS,"DefaultModelLoader.load_weights",weights).__get__(loader)
    base=native_method("BaseModelLoader.load_model",{"torch":SimpleNamespace(device=lambda x:nullcontext()),
        "set_default_torch_dtype":lambda x:nullcontext(),"logger":SimpleNamespace(debug=lambda *a:None),
        "current_platform":SimpleNamespace(is_cuda_alike=lambda:False,is_xpu=lambda:False),
        "_has_online_quant":lambda m:False,"process_weights_after_loading":lambda *a:None})
    base=observer.wrap(BASE,"BaseModelLoader.load_model",base)
    cpu=native_method("CPUModelRunner.load_model",{"logger":SimpleNamespace(info=lambda *a:None),
        "get_model":lambda **kw:base(loader,vllm_cfg,cfg)})
    worker=SimpleNamespace(model_config=cfg,vllm_config=vllm_cfg,lora_config=None,speculative_config=None,
        _setup_eagle3_aux_hidden_state_outputs=lambda:None)
    assert observer.wrap(CPU,"CPUModelRunner.load_model",cpu)(worker) is None
    assert loaded==[model] and worker.model is model
    getter=observer.wrap(CPU,"CPUModelRunner.get_model",native_method("CPUModelRunner.get_model",{}))
    assert getter(worker) is model
    forward=observer.wrap(GPU,"GPUModelRunner._model_forward",native_method("GPUModelRunner._model_forward",{}))
    opaque=object();rid="cmpl-"+CANARY+"-0-random"
    schedule=SimpleNamespace(num_scheduled_tokens={rid:1},finished_req_ids=set(),sched_step_seq=1)
    def execute(owner,selected):
        assert selected is schedule
        return forward(owner,input_ids=opaque,positions=opaque,intermediate_tensors=opaque,inputs_embeds=opaque,extra=opaque)
    assert observer.wrap(GPU,"GPUModelRunner.execute_model",execute)(worker,schedule) is model.answer
    assert all(value is opaque for value in model.calls[0].values())
    writer.close("SEALED");raw=bytes(fake.data);rows,summary=capture.parse_recording(raw,case.prepared)
    index=session.request_index([{"capture":{"records":rows,"provenance":{"owner":{"container_id":"a"*64}}}}])
    records=next(iter(index["domains"].values()))["model_observations"]
    def row(point):return next(r for r in records if r["point"]==point and r["phase"]=="RETURN")
    reference=row("BaseModelLoader.load_model")["metadata"]["returned_model_sha256"]
    assert reference is not None
    assert row("DefaultModelLoader.load_weights")["metadata"]["argument_model_sha256"]==reference
    assert row("CPUModelRunner.load_model")["metadata"]["owner_model_sha256"]==reference
    assert row("CPUModelRunner.get_model")["metadata"]["returned_model_sha256"]==reference
    called=row("GPUModelRunner._model_forward")
    assert called["metadata"]["owner_model_sha256"]==reference
    assert called["request_sha256"]==[hashlib.sha256(rid.encode()).hexdigest()]
    assert row("BaseModelLoader.load_model")["metadata"]["configured_model_sha256"]==hashlib.sha256(CANARY.encode()).hexdigest()
    assert summary["recording_closed"] and CANARY.encode() not in raw
    assert index["final_model_verified"] is index["actual_consumption_verified"] is index["weights_revision_verified"] is False


def test_weak_references_do_not_retain_models_and_new_generation_does_not_reuse_digest():
    observer=RecordingObserver();model=Model();ref=weakref.ref(model)
    first=observer.model_reference(model);assert first==observer.model_reference(model)
    del model
    assert ref() is None
    assert observer.model_reference(Model())!=first


def test_same_model_reference_is_stable_across_threads():
    observer=RecordingObserver();model=Model()
    with ThreadPoolExecutor(max_workers=4) as pool:values=list(pool.map(lambda _:observer.model_reference(model),range(32)))
    assert len(set(values))==1 and observer.model_serial==1


def test_model_reference_bound_retains_prior_models_and_leaves_new_reference_missing():
    observer=RecordingObserver();models=[Model() for _ in range(sensor.MAX_MODEL_OBJECTS+1)]
    refs=[observer.model_reference(m) for m in models]
    assert all(refs[:-1]) and refs[-1] is None
    assert observer.model_reference(models[0])==refs[0] and len(observer.model_objects)==sensor.MAX_MODEL_OBJECTS


def test_fork_resets_reference_generation_without_reusing_parent_identity(monkeypatch):
    pid=[42]
    monkeypatch.setattr(sensor,"os",SimpleNamespace(getpid=lambda:pid[0]))
    monkeypatch.setattr(sensor,"_context",lambda:{**CONTEXT,"pid":pid[0]})
    monkeypatch.setattr(sensor,"Writer",lambda config,sha,context:SimpleNamespace(context=context,fd=None,closed=False))
    observer=sensor.Observer({"session":"a"*32},"b"*64);model=Model()
    first=observer.model_reference(model);pid[0]=43;second=observer.model_reference(model)
    assert first!=second and observer.model_serial==1 and len(observer.model_objects)==1


def test_same_config_different_objects_and_inplace_config_change_do_not_claim_weights_identity():
    observer=RecordingObserver();cfg=SimpleNamespace(model=CANARY,revision="one")
    left=SimpleNamespace(model=Model(),model_config=cfg);right=SimpleNamespace(model=Model(),model_config=cfg)
    a=observer.model_metadata("GPUModelRunner._model_forward",(left,),{},None,False)
    b=observer.model_metadata("GPUModelRunner._model_forward",(right,),{},None,False)
    assert a["configured_model_sha256"]==b["configured_model_sha256"] and a["owner_model_sha256"]!=b["owner_model_sha256"]
    cfg.revision="two"
    after=observer.model_metadata("GPUModelRunner._model_forward",(left,),{},None,False)
    assert after["owner_model_sha256"]==a["owner_model_sha256"]
    assert after["configured_revision_sha256"]!=a["configured_revision_sha256"]


def test_getter_records_distinct_wrapper_and_return_objects_without_implicit_merge():
    observer=RecordingObserver();wrapper=Model();inner=Model();owner=SimpleNamespace(model=wrapper)
    calls=[]
    def getter(value):calls.append(value);return inner
    assert observer.wrap(GPU,"GPUModelRunner.get_model",getter)(owner) is inner
    row=observer.recorded[-1]["payload"]["metadata"]["model_selection"]
    assert row["owner_model_sha256"]!=row["returned_model_sha256"] and calls==[owner]


@pytest.mark.parametrize("value",[None,1,[],{}])
def test_unreferenceable_values_are_missing_instead_of_fabricated(value):
    assert RecordingObserver().model_reference(value) is None


def test_model_metadata_never_executes_properties_or_stringifies_config():
    class Trap:
        @property
        def model(self):raise AssertionError(CANARY)
        @property
        def model_config(self):raise AssertionError(CANARY)
        def __str__(self):raise AssertionError(CANARY)
    observer=RecordingObserver()
    assert observer.model_metadata("GPUModelRunner._model_forward",(Trap(),),{},None,False)==metadata()


def test_warmup_call_has_no_request_association_or_native_work_added():
    observer=RecordingObserver();model=Model();worker=SimpleNamespace(model=model)
    forward=observer.wrap(GPU,"GPUModelRunner._model_forward",native_method("GPUModelRunner._model_forward",{}))
    assert forward(worker) is model.answer and len(model.calls)==1 and observer.recorded==[]


def test_model_call_exception_identity_and_chain_cleanup_are_preserved():
    observer=RecordingObserver();error=RuntimeError(CANARY)
    def failed(owner,*args,**kw):raise error
    worker=SimpleNamespace(model=Model())
    wrapped=observer.wrap(GPU,"GPUModelRunner._model_forward",failed)
    with pytest.raises(RuntimeError) as caught:wrapped(worker)
    assert caught.value is error and sensor._CHAIN.get()==()
    assert CANARY not in json.dumps(observer.recorded)


def test_native_load_failure_keeps_observed_attempt_without_claiming_returned_model():
    observer=RecordingObserver();error=RuntimeError(CANARY)
    def failed(owner,**kwargs):raise error
    wrapped=observer.wrap(BASE,"BaseModelLoader.load_model",failed)
    with pytest.raises(RuntimeError) as caught:wrapped(SimpleNamespace())
    assert caught.value is error and observer.recorded[-1]["payload"]["phase"]=="RAISE"
    assert observer.recorded[-1]["payload"]["metadata"]["model_selection"]["returned_model_sha256"] is None
    assert CANARY not in json.dumps(observer.recorded)


def test_sampler_failure_does_not_replace_native_model_return(monkeypatch):
    observer=RecordingObserver();answer=object()
    monkeypatch.setattr(observer,"model_reference",lambda value:(_ for _ in ()).throw(OSError(CANARY)))
    wrapped=observer.wrap(CPU,"CPUModelRunner.load_model",lambda owner:answer)
    assert wrapped(SimpleNamespace()) is answer
    assert observer.recorded[-1]["payload"]["diagnostic"]=="SAMPLE_UNAVAILABLE"
    assert CANARY not in json.dumps(observer.recorded)


def v2(case,crlf=False):
    manifest=deepcopy(case.prepared);root=case.manifest_path.parent
    raw=(FIXTURES/"lifecycle-sensor-v2.py.txt").read_bytes()
    if crlf:raw=raw.replace(b"\n",b"\r\n")
    previous=manifest["assets"]["guard_lifecycle_sensor.py"]["sha256"]
    (root/"guard_lifecycle_sensor.py").write_bytes(raw)
    bootstrap=(root/"sitecustomize.py").read_bytes().replace(previous.encode(),hashlib.sha256(raw).hexdigest().encode())
    (root/"sitecustomize.py").write_bytes(bootstrap)
    manifest.update(profile=profiles.V2_PROFILE,source_sha256=profiles.LEGACY_SOURCE_SHA)
    for name in ("guard_lifecycle_sensor.py","sitecustomize.py"):
        data=(root/name).read_bytes();manifest["assets"][name]={"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
    case.manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
    def change(rows):rows[0]["payload"].update(profile=profiles.V2_PROFILE,schema=profiles.V2_SCHEMA,
        sensor_sha256=manifest["assets"]["guard_lifecycle_sensor.py"]["sha256"])
    return manifest,revised(case.raw,change)


@pytest.mark.parametrize("crlf",[False,True])
def test_v2_source_and_records_remain_readable_without_new_model_meaning(case,crlf):
    prepared,raw=v2(case,crlf)
    assert lifecycle.load_prepared(case.manifest_path)==prepared
    before=hashlib.sha256(raw).hexdigest();rows,summary=capture.parse_recording(raw,prepared)
    assert rows[0]["payload"]["profile"]==profiles.V2_PROFILE and len(profiles.source_hashes(prepared["profile"]))==17
    assert "model_selection" not in json.dumps(rows) and not summary["whole_run_coverage_verified"]
    assert hashlib.sha256(raw).hexdigest()==before


@pytest.mark.parametrize("change",["source","module","point","metadata"])
def test_v2_cannot_import_new_module_point_or_model_metadata_contract(case,change):
    prepared,raw=v2(case)
    if change=="source":
        value=deepcopy(prepared);value["source_sha256"]=sensor.SOURCE_SHA
        case.manifest_path.write_text(json.dumps(value),encoding="utf-8")
        with pytest.raises(ValueError):lifecycle.load_prepared(case.manifest_path)
        return
    if change=="module":
        def alter(rows):rows[1]["payload"].update(module=BASE,source_sha256=sensor.SOURCE_SHA[BASE])
        raw=revised(raw,alter)
    elif change=="point":
        value=model_payload();value["metadata"]={};raw=append_payload(raw,value)
    else:
        value=model_payload(point="InputProcessor.assign_request_id")
        value["source_sha256"]=sensor.SOURCE_SHA["vllm.v1.engine.input_processor"];raw=append_payload(raw,value)
    with pytest.raises(ValueError):capture.parse_recording(raw,prepared)


@pytest.mark.parametrize("key,value",[("owner_model_sha256",True),("argument_model_sha256","a"*63),
    ("configured_revision_sha256",1),("load_dummy_weights",1),("lora_config_present",[]),
    ("speculative_config_present","RETURN"),("unexpected",None)])
def test_resealed_invalid_model_metadata_is_rejected(case,key,value):
    data=metadata();data[key]=value
    with pytest.raises(ValueError):capture.parse_recording(append_payload(case.raw,model_payload(model=data)),case.prepared)


@pytest.mark.parametrize("point,phase",[("GPUModelRunner._model_forward","RETURN"),("BaseModelLoader.load_model","CALL"),
    ("BaseModelLoader.load_model","RAISE"),("DefaultModelLoader.load_weights","RETURN")])
def test_returned_model_reference_requires_actual_model_return_point(case,point,phase):
    data=metadata();data["returned_model_sha256"]="a"*64
    with pytest.raises(ValueError):capture.parse_recording(append_payload(case.raw,model_payload(point,phase,data)),case.prepared)


def test_model_index_bound_preserves_first_observations_and_marks_incomplete(case,monkeypatch):
    first=append_payload(case.raw,model_payload())
    rows,_=capture.parse_recording(first,case.prepared)
    recording={"capture":{"records":rows,"provenance":{"owner":{"container_id":"a"*64}}}}
    monkeypatch.setattr(session,"MAX_MODEL_EVENTS",1)
    result=session.request_index([recording,deepcopy(recording)])
    domain=next(iter(result["domains"].values()))
    assert len(domain["model_observations"])==1 and not result["stored_record_index_complete"]
    assert domain["model_observations"][0]["event"]==[0,len(rows)-2]
    assert not result["final_model_verified"] and not result["actual_consumption_verified"]
