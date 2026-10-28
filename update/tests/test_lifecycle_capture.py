"""EXAMPLE native asset/owned-capture contracts; no Docker or engine calls."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture_module, owned_capture
from guard.models import canonical_json, DataOrigin
from tests.builders import environment
from tests.test_lifecycle_sensor import writer_factory, CONTEXT, CANARY

IMAGE="example.invalid/engine@sha256:"+"a"*64
IMAGE_ID="sha256:"+"b"*64
REPOSITORY=Path(__file__).resolve().parents[2]
SOURCE_MODULE="vllm.v1.engine.input_processor"


@pytest.fixture
def external():
    directory=tempfile.TemporaryDirectory(prefix="byul-lifecycle-test-")
    root=Path(directory.name).resolve()
    assert root.is_relative_to(Path(tempfile.gettempdir()).resolve()) and not root.is_relative_to(REPOSITORY)
    try:
        yield root
    finally:
        assert root.name.startswith("byul-lifecycle-test-")
        directory.cleanup()


@pytest.fixture
def case(external,writer_factory):
    manifest=lifecycle.prepare_assets(external/"assets",REPOSITORY)
    prepared=lifecycle.load_prepared(external/"assets/manifest.json")
    assert manifest==prepared
    writer,fake=writer_factory(session=prepared["session"],settings_sha=prepared["assets"]["settings.json"]["sha256"])
    writer.write("MODULE",{"module":SOURCE_MODULE,"verified":True,
        "source_sha256":capture_module.sensor.SOURCE_SHA[SOURCE_MODULE],"code_sha256":"c"*64})
    writer.write("ASGI",{"request_sha256":"d"*64,"phase":"DISCONNECT_RECEIVED","more_body":None})
    writer.close("SEALED")
    env=environment(engine={"name":"vllm","version":"0.31.0","image_digest":IMAGE})
    target={"run_id":"EXAMPLE-lifecycle","environment_ref":env.ref().model_dump(mode="json"),
            "engine":{"container":"EXAMPLE-engine"}}
    source={"container":"EXAMPLE-engine","image":IMAGE,
        "path":"/guard-lifecycle/data/"+prepared["session"]+"/pid-42-99.jsonl"}
    (external/"env.json").write_bytes(canonical_json(env.to_dict()))
    (external/"target.json").write_text(json.dumps(target),encoding="utf-8")
    return SimpleNamespace(root=external,prepared=prepared,env=env,target=target,source=source,
        raw=bytes(fake.data),manifest_path=external/"assets/manifest.json")


class Docker:
    def __init__(self,case,mode=None):
        self.case,self.mode=case,mode
        self.inspections,self.files,self.contexts=0,0,0
        self.read_budgets=[]
    def inspect_json(self,container,*,timeout):
        self.inspections+=1
        started=CANARY if self.mode!="restart" or self.inspections<=2 else CANARY+"changed"
        return {"Id":"a"*64,"Image":IMAGE_ID,"Name":"/EXAMPLE-engine",
            "Config":{"Image":IMAGE,"Labels":{"guard.run":"EXAMPLE-lifecycle"}},
            "State":{"Running":True,"StartedAt":started}}
    def image_info(self,image,*,timeout):
        assert image==IMAGE_ID
        if self.mode=="image-exception":raise RuntimeError(CANARY)
        return {"digests":[IMAGE if self.mode!="wrong-image" else "other@sha256:"+"c"*64]}
    def exec_bounded(self,container,argv,*,timeout,max_bytes):
        pipe={"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False}
        if argv[2]==capture_module.CONTEXT_READER:
            self.contexts+=1
            if self.mode=="process-error":raise RuntimeError(CANARY)
            context=dict(CONTEXT)
            if self.mode=="wrong-context":context["start_ticks"]+=1
            value={"schema":"guard-lifecycle-process-context/1","context":context,
                   "stable":True,"same_executable":True}
        else:
            self.files+=1
            if self.mode=="late-read" and self.files==2:raise OSError(CANARY)
            bound=int(argv[-1]);self.read_budgets.append((bound,max_bytes))
            data=self.case.raw[:bound]
            full=len(self.case.raw)<=bound
            stat={"device":1,"inode":2,"size":len(self.case.raw),"mtime_ns":3,"ctime_ns":4}
            value={"schema":"guard-container-file/2","source_complete":full,"file_identity_stable":True,
                "truncated":not full,"nofollow_components":True,"regular_file":True,
                "raw_base64":base64.b64encode(data).decode(),"raw_retained_bytes":len(data),
                "raw_retained_sha256":hashlib.sha256(data).hexdigest(),
                "file_before":stat,"file_after":stat,"path_after":stat}
        return SimpleNamespace(returncode=0,stdout=json.dumps(value),capture=pipe)


def test_prepared_assets_are_exact_bytes_and_never_overwrite(case):
    for name,metadata in case.prepared["assets"].items():
        raw=(case.root/"assets"/name).read_bytes()
        assert len(raw)==metadata["bytes"] and hashlib.sha256(raw).hexdigest()==metadata["sha256"]
    assert case.prepared["engine_started"] is False and case.prepared["image_contents_verified"] is False
    with pytest.raises(ValueError):lifecycle.prepare_assets(case.root/"assets",REPOSITORY)


@pytest.mark.parametrize("kind",["normal","image-exception","wrong-image","late-read","process-error","wrong-context","restart"])
def test_actual_owned_transport_keeps_first_safe_recording_on_later_gaps(case,kind):
    docker=Docker(case,None if kind=="normal" else kind)
    value=lifecycle.collect(docker,case.env,case.target,case.source,case.prepared)
    assert value["records"]==capture_module.parse_recording(case.raw,case.prepared)[0]
    assert value["security"]=="UNKNOWN" and value["product_connected"] is False
    assert value["producer_authenticated"] is False and value["record_authenticity_verified"] is False
    assert value["data_origin"]=="EXAMPLE" and value["summary"]["whole_run_coverage_verified"] is False
    assert value["process_context_verified"] is (kind in {"normal","image-exception","wrong-image"})
    assert value["image_digest_verified"] is (kind not in {"image-exception","wrong-image"})
    assert CANARY not in json.dumps(value)
    assert docker.read_budgets and all(pair==(262144,524288) for pair in docker.read_budgets)


@pytest.mark.parametrize("origin",["DECLARED","MEASURED"])
def test_file_capture_never_upgrades_unverified_native_assertions_to_measured(case,origin):
    env=case.env.model_copy(update={"data_origin":DataOrigin(origin)}).seal()
    target={**case.target,"environment_ref":env.ref().model_dump(mode="json")}
    result=lifecycle.collect(Docker(case),env,target,case.source,case.prepared)
    assert result["data_origin"]=="DECLARED" and result["security"]=="UNKNOWN"


def test_partial_last_write_keeps_safe_prefix_and_only_hashes_discarded_values(case):
    partial=case.raw+('{"body":"'+CANARY).encode()
    rows,summary=capture_module.parse_recording(partial,case.prepared)
    assert rows==capture_module.parse_recording(case.raw,case.prepared)[0]
    assert summary["recording_closed"] is False and summary["incomplete_final_row"] is True
    assert summary["discarded_tail_sha256"]==hashlib.sha256(partial[len(case.raw):]).hexdigest()
    assert CANARY not in json.dumps({"records":rows,"summary":summary})


def revised(raw,change):
    rows=[json.loads(line) for line in raw.splitlines()]
    change(rows)
    previous="0"*64;lines=[]
    for seq,row in enumerate(rows):
        row["seq"]=seq;row["previous_sha256"]=previous
        line=capture_module.canonical_row(row)
        lines.append(line);previous=hashlib.sha256(line).hexdigest()
    return b"".join(lines)


@pytest.mark.parametrize("change",[
    lambda r:r[0]["payload"].update(body=CANARY),
    lambda r:r[0]["payload"].update(session="e"*32),
    lambda r:r[0]["payload"].update(producer_authenticated=True),
    lambda r:r[1]["payload"].update(source_sha256="e"*64),
    lambda r:r[2]["payload"].update(message=CANARY),
    lambda r:r[2]["payload"].update(request_sha256=CANARY),
    lambda r:r[2]["payload"].update(more_body=1),
    lambda r:r[-1]["payload"].update(reason=CANARY),
    lambda r:r.append(deepcopy(r[2])),
])
def test_resealed_unsafe_or_inconsistent_rows_are_rejected(case,change):
    with pytest.raises(ValueError):
        capture_module.parse_recording(revised(case.raw,change),case.prepared)


@pytest.mark.parametrize("operation",["whitespace","hash","duplicate"])
def test_exact_record_chain_and_strict_JSON_are_required(case,operation):
    if operation=="whitespace":raw=case.raw.replace(b'{"',b'{ "',1)
    elif operation=="hash":raw=case.raw.replace(b'"previous_sha256":"'+b'0'*64,b'"previous_sha256":"'+b'f'*64,1)
    else:raw=case.raw.replace(b'"seq":0',b'"seq":0,"seq":0',1)
    with pytest.raises(ValueError):capture_module.parse_recording(raw,case.prepared)


def test_lifecycle_budget_expansion_is_explicit_and_default_64KiB_stays_bounded(case,writer_factory):
    writer,fake=writer_factory(session=case.prepared["session"],settings_sha=case.prepared["assets"]["settings.json"]["sha256"])
    for _ in range(300):
        writer.write("ASGI",{"request_sha256":"d"*64,"phase":"BODY_SEND_RETURN","more_body":True})
    writer.close("SEALED")
    case.raw=bytes(fake.data)
    assert 65536<len(case.raw)<262144
    with pytest.raises(ValueError):owned_capture._read_owned_file(Docker(case),case.target,**case.source)
    value=lifecycle.collect(Docker(case),case.env,case.target,case.source,case.prepared)
    assert value["summary"]["recording_closed"] is True


@pytest.mark.parametrize("budget",[True,0,65535,262145])
def test_bad_read_budget_precedes_any_runtime_call(case,budget):
    class NoRuntime:
        def inspect_json(self,*a,**k):raise AssertionError("runtime touched")
    with pytest.raises(ValueError,match="CAPTURE_READ_BUDGET_INVALID"):
        owned_capture._read_owned_file(NoRuntime(),case.target,**case.source,max_bytes=budget)


def test_default_product_capture_validates_without_creating_client_or_output(case,monkeypatch):
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:(_ for _ in ()).throw(AssertionError("client created")))
    args=["capture-lifecycle-observations","--env",str(case.root/"env.json"),"--target",str(case.root/"target.json"),
        "--prepared",str(case.manifest_path),"--container",case.source["container"],"--image",IMAGE,
        "--path",case.source["path"],"--output",str(case.root/"capture.json")]
    result=CliRunner().invoke(app,args)
    assert result.exit_code==0,result.output
    assert json.loads(result.output)["capture_executed"] is False
    assert not (case.root/"capture.json").exists()


def test_product_capture_execute_writes_only_safe_metadata(case,monkeypatch):
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:Docker(case))
    result=CliRunner().invoke(app,["capture-lifecycle-observations","--env",str(case.root/"env.json"),
        "--target",str(case.root/"target.json"),"--prepared",str(case.manifest_path),"--container",case.source["container"],
        "--image",IMAGE,"--path",case.source["path"],"--output",str(case.root/"capture.json"),"--execute"])
    assert result.exit_code==0,result.output
    value=json.loads((case.root/"capture.json").read_text())
    assert value["process_context_verified"] and value["image_digest_verified"]
    assert value["security"]=="UNKNOWN" and CANARY not in json.dumps(value)


def test_asset_tamper_rejected_before_runtime_or_output(case,monkeypatch):
    (case.root/"assets/sitecustomize.py").write_text(CANARY)
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:(_ for _ in ()).throw(AssertionError("client created")))
    with pytest.raises(ValueError):lifecycle.load_prepared(case.manifest_path)


def test_source_path_and_environment_ref_are_bound_to_prepared_session(case):
    other={**case.source,"path":case.source["path"].replace(case.prepared["session"],"0"*32)}
    with pytest.raises(ValueError):
        lifecycle.load_inputs(case.root/"env.json",case.root/"target.json",other,case.manifest_path)
    case.target["environment_ref"]["sha256"]="0"*64
    (case.root/"target.json").write_text(json.dumps(case.target))
    with pytest.raises(ValueError):
        lifecycle.load_inputs(case.root/"env.json",case.root/"target.json",case.source,case.manifest_path)


def test_native_callback_writer_to_owned_capture_preserves_ID_binding_without_content(case,writer_factory):
    writer,fake=writer_factory(session=case.prepared["session"],settings_sha=case.prepared["assets"]["settings.json"]["sha256"])
    writer.write("MODULE",{"module":SOURCE_MODULE,"verified":True,
        "source_sha256":capture_module.sensor.SOURCE_SHA[SOURCE_MODULE],"code_sha256":"c"*64})
    observer=capture_module.sensor.Observer(writer.config,"b"*64)
    observer.writer=writer;observer.pid=42
    observer._writer=lambda:writer
    def original(request):
        request.external_req_id=request.request_id
        request.request_id += "-internal"
    request=SimpleNamespace(request_id=CANARY,external_req_id=None)
    wrapped=observer.wrap(SOURCE_MODULE,"InputProcessor.assign_request_id",original)
    assert wrapped(request) is None
    writer.close("SEALED");case.raw=bytes(fake.data)
    value=lifecycle.collect(Docker(case),case.env,case.target,case.source,case.prepared)
    native=[r["payload"] for r in value["records"] if r["event"]=="NATIVE"]
    assert [r["phase"] for r in native]==["CALL","RETURN"]
    assert native[-1]["metadata"]["external_request_sha256"]==hashlib.sha256(CANARY.encode()).hexdigest()
    assert CANARY not in json.dumps(value) and value["security"]=="UNKNOWN"


def test_prepare_product_command_creates_only_reviewable_assets(external):
    result=CliRunner().invoke(app,["prepare-lifecycle-sensor","--output",str(external/"assets")])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)["engine_started"] is False
    assert set(p.name for p in (external/"assets").iterdir())=={
        "guard_lifecycle_sensor.py","sitecustomize.py","settings.json","manifest.json"}


def test_malformed_input_reports_fixed_diagnostic_without_secret_or_client(case,monkeypatch):
    (case.root/"env.json").write_text('secret: ["'+CANARY+'"',encoding="utf-8")
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:(_ for _ in ()).throw(AssertionError("client created")))
    result=CliRunner().invoke(app,["capture-lifecycle-observations","--env",str(case.root/"env.json"),
        "--target",str(case.root/"target.json"),"--prepared",str(case.manifest_path),"--container",case.source["container"],
        "--image",IMAGE,"--path",case.source["path"],"--output",str(case.root/"capture.json")])
    assert result.exit_code==2 and result.output.strip()=="LIFECYCLE_CAPTURE_FAILED"
    assert CANARY not in result.output and not (case.root/"capture.json").exists()
