"""EXAMPLE native selections and owned file packets; no engine/model execution."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace, ModuleType

import pytest
from typer.testing import CliRunner

from guard.adapters import vllm_lifecycle_sensor as sensor, vllm_lifecycle_profiles as profiles
from guard.cli import app
from guard.collectors import native_weight_files as collector, lifecycle, model_manifest
from guard.executors import lifecycle_capture as capture, weight_file_capture as files, owned_capture
from guard.models import canonical_json
from tests.test_lifecycle_capture import case, external, revised, Docker, IMAGE
from tests.test_lifecycle_sensor import writer_factory, RecordingObserver, CANARY
from tests.test_model_selection_sensor import Model

WEIGHTS="vllm.model_executor.model_loader.default_loader"
UTILS="vllm.model_executor.model_loader.weight_utils"
POINT="weight_utils.pt_weights_iterator"
ROOT="/models/EXAMPLE-private"
FIXTURES=Path(__file__).parent/"fixtures"
def sha(value):return hashlib.sha256(value if type(value) is bytes else value.encode()).hexdigest()


def method(name,module,namespace):
    row=json.loads((FIXTURES/"weight-selection-native-methods.json").read_text())[name]
    assert row["full_source_sha256"]==sensor.SOURCE_SHA[module] and row["license"]=="Apache-2.0"
    node=ast.parse(row["method"]).body[0];node.decorator_list=[]
    tree=ast.Module(body=[ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0),node],type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree),"reviewed-native-weight-method","exec"),namespace)
    return namespace[name]


def recording(case,writer_factory,monkeypatch,paths):
    observer=RecordingObserver()
    writer,fake=writer_factory(session=case.prepared["session"],settings_sha=case.prepared["assets"]["settings.json"]["sha256"])
    monkeypatch.setattr(fake,"getcwd",lambda:ROOT,raising=False)
    monkeypatch.setattr(observer,"emit",lambda event,payload:writer.write(event,payload))
    for module in (WEIGHTS,UTILS):
        writer.write("MODULE",{"module":module,"verified":True,"source_sha256":sensor.SOURCE_SHA[module],"code_sha256":"c"*64})
    factory=observer.wrap(UTILS,POINT,lambda rows,*args:(v for v in rows))
    model=Model()
    load=observer.wrap(WEIGHTS,"DefaultModelLoader.load_weights",lambda owner,value,config:factory(paths,False))
    result=load(SimpleNamespace(),model,SimpleNamespace())
    writer.close("SEALED")
    return capture.parse_recording(bytes(fake.data),case.prepared)[0],result,bytes(fake.data)


def test_native_generator_is_not_consumed_and_selected_paths_link_to_load_object(case,writer_factory,monkeypatch):
    observer=RecordingObserver();calls=[];tensor=object()
    _,fake=writer_factory();monkeypatch.setattr(fake,"getcwd",lambda:ROOT,raising=False)
    original=method("pt_weights_iterator",UTILS,{"torch":SimpleNamespace(load=lambda *a,**kw:calls.append((a,kw)) or {"opaque":tensor}),
        "tqdm":lambda rows,**kw:rows,"enable_tqdm":lambda flag:False,"_BAR_FORMAT":""})
    created=[]
    def factory(*args,**kwargs):
        value=original(*args,**kwargs);created.append(value);return value
    wrapped=observer.wrap(UTILS,POINT,factory)
    value=wrapped([ROOT+"/a.bin"],False)
    assert value is created[0] and calls==[]
    assert next(value)==("opaque",tensor) and len(calls)==1
    value.close()  # The caller alone consumes/closes the native generator.
    rows,returned,raw=recording(case,writer_factory,monkeypatch,[ROOT+"/a.bin"])
    selected=[profiles.returned_weight_selection(r) for r in rows]
    event=next(v for v in selected if v)
    assert event["load_context_linked"] and event["factory_returned_generator"] and event["paths_complete"]
    assert event["paths"]==[sha(ROOT+"/a.bin")] and event["weight_model_sha256"]
    assert ROOT.encode() not in raw and CANARY.encode() not in raw and "_weight_pid" not in raw.decode()
    assert next(returned)==ROOT+"/a.bin"


@pytest.mark.parametrize("safe",[False,True])
def test_native_iterator_selection_uses_final_factory_arguments_after_filter(case,writer_factory,monkeypatch,safe):
    observer=RecordingObserver();_,fake=writer_factory();monkeypatch.setattr(fake,"getcwd",lambda:ROOT,raising=False)
    raw_files=[ROOT+"/a.bin",ROOT+"/b.bin"];selected=raw_files[:1] if safe else raw_files
    native=(v for v in [("opaque",object())]);factories=[]
    def original(rows,*args,**kwargs):factories.append(rows);return native
    factory=observer.wrap(UTILS,"weight_utils.safetensors_weights_iterator" if safe else POINT,original)
    loader=SimpleNamespace(load_config=SimpleNamespace(load_format="pt",model_loader_extra_config={},use_tqdm_on_load=False,
        pt_load_map_location="cpu",safetensors_load_strategy=None,safetensors_prefetch_num_threads=1,safetensors_prefetch_block_size=1),
        _encoder_only_lm_prefixes=["opaque"] if safe else None,_encoder_only_weights_mapper=None,
        local_expert_ids=None,counter_before_loading_weights=0.)
    loader._prepare_weights=observer.wrap(WEIGHTS,"DefaultModelLoader._prepare_weights",lambda *args:(ROOT,raw_files,safe,"index.json"))
    get=method("_get_weights_iterator",WEIGHTS,{"pt_weights_iterator":factory,"safetensors_weights_iterator":factory,
        "filter_mm_encoder_only_safetensors_files":lambda *a,**kw:selected,"time":SimpleNamespace(perf_counter=lambda:1)})
    source=SimpleNamespace(model_or_path=CANARY,subfolder=None,revision="private",fall_back_to_pt=True,
        allow_patterns_overrides=None,prefix="")
    getter=observer.wrap(WEIGHTS,"DefaultModelLoader._get_weights_iterator",get)
    load=observer.wrap(WEIGHTS,"DefaultModelLoader.load_weights",lambda owner,model,config:getter(loader,source))
    result=load(loader,Model(),SimpleNamespace())
    assert factories==[selected] and native.gi_frame is not None and result is not native
    returns=[r["payload"] for r in observer.recorded if r["event"]=="NATIVE" and r["payload"]["phase"]=="RETURN"]
    observed=next(r["metadata"]["weight_selection"] for r in returns if r["point"].startswith("weight_utils."))
    assert observed["normalized_path_sha256"]==[sha(p) for p in selected]
    assert observed["weight_model_sha256"] and observed["weight_call_sha256"]


def test_function_instrumentation_is_atomic_and_returns_original_generator():
    observer=RecordingObserver();module=ModuleType(UTILS);native=(v for v in ())
    def original(*args):return native
    for name in sensor.FUNCTION_POINTS[UTILS][:-1]:setattr(module,name,original)
    with pytest.raises(ValueError):observer.instrument(module)
    assert module.pt_weights_iterator is original
    setattr(module,sensor.FUNCTION_POINTS[UTILS][-1],original);observer.instrument(module)
    assert module.pt_weights_iterator([],False) is native


@pytest.mark.parametrize("paths,complete,count",[([],True,0),(["a.bin"],True,1),(["x\x00"],False,1),
    (["a.bin"]*65,False,65),([1],False,1)])
def test_path_observation_is_bounded_and_never_serializes_paths(writer_factory,monkeypatch,paths,complete,count):
    _,fake=writer_factory();monkeypatch.setattr(fake,"getcwd",lambda:ROOT,raising=False)
    value=RecordingObserver().weight_metadata(POINT,(paths,),{},None,False)
    assert value["path_count"]==count and value["paths_complete"] is complete and len(value["normalized_path_sha256"])<=64
    assert ROOT not in json.dumps(value)


def test_iterator_and_property_inputs_are_never_consumed(writer_factory,monkeypatch):
    _,fake=writer_factory();monkeypatch.setattr(fake,"getcwd",lambda:ROOT,raising=False)
    class Trap:
        def __iter__(self):raise AssertionError("must not consume")
        def __str__(self):raise AssertionError("must not stringify")
    value=RecordingObserver().weight_metadata(POINT,(Trap(),),{},None,False)
    assert value["path_count"] is None and not value["paths_complete"]


def test_cwd_change_and_inherited_parent_context_are_not_verified(writer_factory,monkeypatch):
    _,fake=writer_factory();values=iter([ROOT,"/other"])
    monkeypatch.setattr(fake,"getcwd",lambda:next(values),raising=False)
    observer=RecordingObserver()
    value=observer.weight_metadata(POINT,(["a.bin"],),{},None,False)
    assert value["normalized_path_sha256"]==[sha(ROOT+"/a.bin")] and not value["paths_complete"]
    token=sensor._CHAIN.set(({"point":"DefaultModelLoader.load_weights","request_sha256":[],
        "weight_call_sha256":"a"*64,"weight_model_sha256":"b"*64,"_weight_pid":fake.getpid()+1},))
    try:assert observer.chain_rows()==[{"point":"DefaultModelLoader.load_weights","request_sha256":[]}]
    finally:sensor._CHAIN.reset(token)


@pytest.mark.parametrize("key,value",[("path_count",True),("path_count",0),("paths_complete",1),
    ("normalized_path_sha256",[CANARY]),("weight_call_sha256",True),("unexpected",None)])
def test_resealed_invalid_native_weight_metadata_is_rejected(case,writer_factory,monkeypatch,key,value):
    _,_,raw=recording(case,writer_factory,monkeypatch,[ROOT+"/a.bin"])
    def change(rows):
        row=next(r for r in rows if r["event"]=="NATIVE" and r["payload"]["point"]==POINT)
        row["payload"]["metadata"]["weight_selection"][key]=value
    with pytest.raises(ValueError):capture.parse_recording(revised(raw,change),case.prepared)


@pytest.mark.parametrize("crlf",[False,True])
def test_v3_exact_source_and_model_records_remain_readable(case,crlf):
    prepared=deepcopy(case.prepared);root=case.manifest_path.parent
    raw=(FIXTURES/"lifecycle-sensor-v3.py.txt").read_bytes()
    if crlf:raw=raw.replace(b"\n",b"\r\n")
    previous=prepared["assets"]["guard_lifecycle_sensor.py"]["sha256"]
    (root/"guard_lifecycle_sensor.py").write_bytes(raw)
    (root/"sitecustomize.py").write_bytes((root/"sitecustomize.py").read_bytes().replace(previous.encode(),sha(raw).encode()))
    prepared.update(profile=profiles.V3_PROFILE,source_sha256=profiles.V3_SOURCE_SHA)
    for name in ("guard_lifecycle_sensor.py","sitecustomize.py"):
        data=(root/name).read_bytes();prepared["assets"][name]={"bytes":len(data),"sha256":sha(data)}
    case.manifest_path.write_text(json.dumps(prepared))
    assert lifecycle.load_prepared(case.manifest_path)==prepared
    def change(rows):rows[0]["payload"].update(profile=profiles.V3_PROFILE,schema=profiles.V3_SCHEMA,sensor_sha256=sha(raw))
    before=revised(case.raw,change);rows,_=capture.parse_recording(before,prepared)
    assert rows[0]["payload"]["profile"]==profiles.V3_PROFILE and len(profiles.source_hashes(profiles.V3_PROFILE))==19
    assert not any(p.startswith("weight_utils") for p in profiles.point_sources(profiles.V3_PROFILE))
    assert not profiles.valid_call_frame({"point":"DefaultModelLoader.load_weights","request_sha256":[],
        "weight_call_sha256":"a"*64,"weight_model_sha256":"b"*64},profiles.V3_PROFILE)


def packet(path,raw=b"EXAMPLE-model-bytes"):
    stat={"device":1,"inode":2,"size":len(raw),"mtime_ns":3,"ctime_ns":4}
    return {"schema":"guard-weight-file-hash/1","path_sha256":sha(path),"resolved_path_sha256":sha(path),
        "content_sha256":sha(raw),"bytes_read":len(raw),"file_before":stat,"file_after":dict(stat),
        "regular_file":True,"nofollow_resolved_components":True,"source_complete":True,
        "file_identity_stable":True,"logical_binding_stable":True}


@pytest.mark.parametrize("change",[lambda p:p.update(raw=CANARY),lambda p:p.update(bytes_read=True),
    lambda p:p.update(file_after=None),lambda p:p.update(source_complete=False),lambda p:p.update(content_sha256=CANARY),
    lambda p:p["file_before"].update(size=1025),lambda p:p.update(logical_binding_stable=1)])
def test_invalid_or_raw_file_packet_is_rejected(change):
    value=packet(ROOT+"/a.bin");change(value)
    with pytest.raises(ValueError):files.validate_packet_body(value,sha(ROOT+"/a.bin"),1024)


@pytest.fixture
def inputs(case,writer_factory,monkeypatch):
    snapshot=case.root/"snapshot";snapshot.mkdir()
    for name in ("a.bin","b.bin"):(snapshot/name).write_bytes(b"EXAMPLE-model-bytes")
    manifest=model_manifest.build_manifest(snapshot,model_id=case.env.model.id)
    env=case.env.model_copy(deep=True);env.model.inventory_sha256=manifest["inventory_sha256"];env=env.seal()
    target={**case.target,"lab":True,"environment_ref":env.ref().model_dump(mode="json")}
    rows,_,_=recording(case,writer_factory,monkeypatch,[ROOT+"/a.bin",ROOT+"/b.bin"])
    owner=owned_capture.identity(Docker(case),case.target,case.source["container"],IMAGE,5)
    native={"schema":"guard-lifecycle-session-capture/1","environment_ref":env.ref().model_dump(mode="json"),
        "session":case.prepared["session"],"data_origin":"EXAMPLE","recordings":[{"capture":{"records":rows,"provenance":{"owner":owner}}}]}
    for name,value in (("env",env.to_dict()),("target",target),("manifest",manifest),("native",native)):
        (case.root/(name+".json")).write_bytes(canonical_json(value))
    context={"schema":"guard-native-weight-files-context/1","environment_ref":env.ref().model_dump(mode="json"),
        "session":case.prepared["session"],"transport":"docker","model_root":ROOT,
        "native_session_sha256":sha((case.root/"native.json").read_bytes()),"model_manifest_sha256":sha((case.root/"manifest.json").read_bytes())}
    (case.root/"context.json").write_bytes(canonical_json(context))
    paths=[case.root/"env.json",case.root/"target.json",case.manifest_path,case.root/"context.json",case.root/"manifest.json",case.root/"native.json"]
    return SimpleNamespace(value=collector.load_inputs(*paths),paths=paths,owner=owner,case=case)


class Reader:
    def __init__(self,owner,mode=None):self.owner,self.mode,self.calls=owner,mode,[]
    def read(self,container,image,root,relative,limit):
        self.calls.append(relative)
        if self.mode=="late-read" and len(self.calls)==2:raise OSError(CANARY)
        value=packet(root+"/"+relative,b"different" if self.mode=="mismatch" else b"EXAMPLE-model-bytes")
        if self.mode=="late-file":value.update(logical_binding_stable=False)
        if self.mode=="raw":value["raw"]=CANARY
        return value,self.owner,self.mode!="late-owner"
    def report(self):raise AssertionError("injected report must not be serialized")


@pytest.mark.parametrize("mode",[None,"mismatch","late-read","late-owner","late-file","raw"])
def test_file_capture_retains_successes_and_negative_observations_without_promoting_verdict(inputs,mode):
    reader=Reader(inputs.owner,mode);value=collector.collect(inputs.value,reader=reader,max_file_bytes=1024)
    assert reader.calls==["a.bin","b.bin"] and len(value["files"])==2
    assert value["security"]=="UNKNOWN" and value["data_origin"]=="EXAMPLE"
    assert all(value[k] is False for k in ("producer_authenticated","final_model_verified","actual_consumption_verified","weights_revision_verified","whole_run_coverage_verified"))
    first,last=value["files"]
    if mode=="late-read":assert first["capture_verified"] and last["capture"] is None and value["collection_gap"]
    elif mode=="raw":assert first["capture"] is None and CANARY not in json.dumps(value)
    else:
        assert first["capture"] and first["native_owner_match"]
        assert first["content_matches_reference"] is (mode!="mismatch")
        assert first["capture_verified"] is (mode not in {"late-owner","late-file"})
    assert ROOT not in json.dumps(value) and CANARY not in json.dumps(value)


@pytest.mark.parametrize("field,value",[("session","0"*32),("transport",[]),("native_session_sha256","0"*64),
    ("model_manifest_sha256","0"*64),("model_root","/models/../private"),("unexpected",True)])
def test_context_rejects_mismatched_or_unsafe_input_before_runtime(inputs,field,value):
    path=inputs.paths[3];context=json.loads(path.read_bytes());context[field]=value;path.write_bytes(canonical_json(context))
    with pytest.raises(ValueError):collector.load_inputs(*inputs.paths)


def test_missing_or_unmapped_selection_never_constructs_runtime(inputs,monkeypatch):
    value=deepcopy(inputs.value);value["selections"]=[]
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *args:pytest.fail("no runtime without selected files"))
    result=collector.collect(value)
    assert result["files"]==[] and result["collection_gap"]


def test_default_docker_path_uses_existing_capture_factory_and_owned_reader(inputs,monkeypatch):
    constructed=[];client=object()
    def create(run_id):constructed.append(run_id);return client
    def reader(value,target,**kwargs):
        assert value is client and target==inputs.value["target"]
        return SimpleNamespace(read=Reader(inputs.owner).read)  # Real DockerWeightReader has no report method.
    monkeypatch.setattr(owned_capture,"create_capture_client",create)
    monkeypatch.setattr(files,"DockerWeightReader",reader)
    result=collector.collect(inputs.value,max_file_bytes=1024)
    assert constructed==[inputs.value["target"]["run_id"]]
    assert len(result["files"])==2 and all(r["capture_verified"] for r in result["files"])


def native_target(inputs):
    target=json.loads(inputs.paths[1].read_bytes())
    target.update(lab=False,engine_kind="vllm")  # sandbox._up's actual-engine target convention.
    inputs.paths[1].write_bytes(canonical_json(target))
    return collector.load_inputs(*inputs.paths)


def test_actual_engine_target_convention_reaches_owned_reader_without_promoting_origin(inputs,monkeypatch):
    value=native_target(inputs);constructed=[]
    def client(run_id):constructed.append(run_id);return object()
    monkeypatch.setattr(owned_capture,"create_capture_client",client)
    monkeypatch.setattr(files,"DockerWeightReader",lambda *a,**kw:SimpleNamespace(read=Reader(inputs.owner).read))
    result=collector.collect(value,max_file_bytes=1024)
    assert constructed==[value["target"]["run_id"]] and value["target"]["lab"] is False
    assert len(result["files"])==2 and all(r["capture_verified"] for r in result["files"])
    assert result["data_origin"]=="EXAMPLE" and result["security"]=="UNKNOWN" and result["engine_started"] is False


@pytest.mark.parametrize("grade",["DECLARED","MEASURED"])
def test_non_stub_target_is_still_declared_at_most_for_non_example_inputs(inputs,monkeypatch,grade):
    from guard.models import DataOrigin
    value=native_target(inputs)
    env=value["env"].model_copy(deep=True);env.data_origin=DataOrigin(grade);env=env.seal()
    ref=env.ref().model_dump(mode="json")
    inputs.paths[0].write_bytes(canonical_json(env.to_dict()))
    target=json.loads(inputs.paths[1].read_bytes());target["environment_ref"]=ref
    inputs.paths[1].write_bytes(canonical_json(target))
    native=json.loads(inputs.paths[5].read_bytes());native.update(environment_ref=ref,data_origin=grade)
    native_raw=canonical_json(native);inputs.paths[5].write_bytes(native_raw)
    context=json.loads(inputs.paths[3].read_bytes());context.update(environment_ref=ref,native_session_sha256=sha(native_raw))
    inputs.paths[3].write_bytes(canonical_json(context))
    value=collector.load_inputs(*inputs.paths)
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:object())
    monkeypatch.setattr(files,"DockerWeightReader",lambda *a,**kw:SimpleNamespace(read=Reader(inputs.owner).read))
    result=collector.collect(value,max_file_bytes=1024)
    assert result["data_origin"]=="DECLARED" and result["security"]=="UNKNOWN"
    assert not result["final_model_verified"] and not result["actual_consumption_verified"]


@pytest.mark.parametrize("lab",[None,0,1,"false",[],{}])
def test_target_requires_explicit_boolean_lab_flag(inputs,lab):
    target=json.loads(inputs.paths[1].read_bytes());target["lab"]=lab
    inputs.paths[1].write_bytes(canonical_json(target))
    with pytest.raises(ValueError):collector.load_inputs(*inputs.paths)


@pytest.mark.parametrize("field",["label","image","name"])
def test_non_stub_target_still_requires_owned_run_image_and_container(inputs,monkeypatch,field):
    value=native_target(inputs);docker=Docker(inputs.case);original=docker.inspect_json
    def inspect(container,**kwargs):
        row=original(container,**kwargs)
        if field=="label":row["Config"]["Labels"]["guard.run"]="other-run"
        elif field=="image":row["Config"]["Image"]="other@sha256:"+"c"*64
        else:row["Name"]="/other-container"
        return row
    monkeypatch.setattr(docker,"inspect_json",inspect)
    monkeypatch.setattr(docker,"exec_bounded",lambda *a,**kw:pytest.fail("no helper before ownership verification"))
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:docker)
    result=collector.collect(value,max_file_bytes=1024)
    assert result["collection_gap"] and all(r["capture"] is None for r in result["files"])
    assert result["security"]=="UNKNOWN" and not result["actual_consumption_verified"]


def test_non_stub_target_cli_dry_run_uses_no_runtime(inputs,monkeypatch):
    native_target(inputs);output=inputs.case.root/"non-stub.json"
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:pytest.fail("dry run must not construct runtime"))
    result=CliRunner().invoke(app,cli_args(inputs,output))
    assert result.exit_code==0 and not output.exists() and not json.loads(result.stdout)["capture_executed"]


def cli_args(inputs,output):
    flags=("--env","--target","--prepared","--context","--model-manifest","--native-session")
    return ["capture-native-weight-files",*[v for pair in zip(flags,map(str,inputs.paths)) for v in pair],"--output",str(output)]


def test_cli_default_is_no_runtime_or_output_and_execute_is_exclusive(inputs,monkeypatch):
    output=inputs.case.root/"capture.json";called=[]
    def collect(value,**kwargs):called.append(True);return collector_original(value,reader=Reader(inputs.owner),max_file_bytes=1024)
    collector_original=collector.collect;monkeypatch.setattr(collector,"collect",collect)
    result=CliRunner().invoke(app,cli_args(inputs,output))
    assert result.exit_code==0 and not called and not output.exists()
    result=CliRunner().invoke(app,[*cli_args(inputs,output),"--execute"])
    assert result.exit_code==0 and json.loads(output.read_bytes())["files"][0]["capture"]
    before=output.read_bytes();result=CliRunner().invoke(app,[*cli_args(inputs,output),"--execute"])
    assert result.exit_code==2 and output.read_bytes()==before and len(called)==1


def test_cli_late_output_failure_retains_complete_safe_capture_on_stdout(inputs,monkeypatch):
    output=inputs.case.root/"late.json";original=collector.collect
    def collect(value,**kwargs):
        result=original(value,reader=Reader(inputs.owner),max_file_bytes=1024)
        output.write_bytes(b"EXAMPLE-existing-owner");return result
    monkeypatch.setattr(collector,"collect",collect)
    result=CliRunner().invoke(app,[*cli_args(inputs,output),"--execute"])
    assert result.exit_code==1 and output.read_bytes()==b"EXAMPLE-existing-owner"
    value=json.loads(result.stdout)
    assert not value["artifact_saved"] and len(value["capture"]["files"])==2
    assert value["capture"]["files"][0]["capture_verified"] and CANARY not in result.stdout


def test_docker_owner_recheck_failure_preserves_file_packet(inputs,monkeypatch):
    client=SimpleNamespace(exec_bounded=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(packet(ROOT+"/a.bin")),
        capture={"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False}))
    calls=[]
    def identity(*args):
        calls.append(True)
        if len(calls)>1:raise OSError(CANARY)
        return inputs.owner
    monkeypatch.setattr(files,"identity",identity)
    result,owner,stable=files.DockerWeightReader(client,inputs.value["target"]).read("EXAMPLE-engine",IMAGE,ROOT,"a.bin",1024)
    assert result["content_sha256"]==sha(b"EXAMPLE-model-bytes") and owner==inputs.owner and stable is False
