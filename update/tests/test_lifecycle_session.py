"""EXAMPLE session census, actual owned-file protocol and CLI failure contracts."""
import ast
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from guard.adapters import vllm_lifecycle_links as links
from guard.cli import app
from guard.collectors import lifecycle
from guard.executors import lifecycle_session as session, lifecycle_capture as single, owned_capture
from guard.models import DataOrigin
from tests.test_lifecycle_capture import case, external, Docker as SingleDocker, IMAGE, revised
from tests.test_lifecycle_sensor import writer_factory, CONTEXT, CANARY


def native_payload(point="InputProcessor.assign_request_id",phase="RETURN",request="e"*64,external="d"*64):
    module="vllm.v1.engine.input_processor" if point.startswith("InputProcessor.") else "vllm.v1.worker.gpu_input_batch"
    return {"point":point,"phase":phase,"source_sha256":single.sensor.SOURCE_SHA[module],
        "requests":[{"request_sha256":request,"request_present":None,"output_state_present":None,
            "batch_row_present":None,"status_code":None}],"metadata":{"external_request_sha256":external},
        "selection_observed":True,"observation_started_ns":0,"observation_finished_ns":0}


@pytest.fixture
def group(case):
    def add(rows):
        rows.insert(-1,{"event":"NATIVE","monotonic_ns":rows[-1]["monotonic_ns"],"payload":native_payload()})
    first=revised(case.raw,add)
    def worker(rows):
        rows[0]["payload"]["context"]["pid"]=43
        rows[-2]["payload"]=native_payload("InputBatch.remove_request",external=None)
    second=revised(first,worker)
    case.raw_by_pid={42:first,43:second}
    return case


def inventory_packet(group):
    files=[];processes=[]
    for pid,raw in group.raw_by_pid.items():
        context=json.loads(raw.splitlines()[0])["payload"]["context"]
        files.append({"pid":pid,"start_ticks":context["start_ticks"],"bytes":len(raw),"uid":1000,"mode":0o600,
            "device":1,"inode":2,"mtime_ns":3,"ctime_ns":4,"regular":True})
        processes.append({**context,"parent_pid":1,"same_executable":True})
    processes.append({**CONTEXT,"pid":44,"parent_pid":1,"same_executable":True})
    directory={"device":1,"inode":7,"uid":1000,"mode":0o700,"mtime_ns":8}
    return {"schema":"guard-lifecycle-session-inventory/1","session":group.prepared["session"],
        "directory_before":directory,"directory_after":dict(directory),"directory_stable":True,"sealed":True,
        "files":files,"unknown_entries":0,"files_truncated":False,"processes":processes,"unreadable_pids":[],
        "processes_truncated":False}


class GroupDocker(SingleDocker):
    def __init__(self,group,mode=None):
        super().__init__(group)
        self.group,self.group_mode=group,mode
        self.inventories,self.owner_calls=0,0
    def inspect_json(self,container,*,timeout):
        self.owner_calls+=1
        if self.group_mode=="late-inventory-owner" and self.owner_calls==2:raise OSError(CANARY)
        value=super().inspect_json(container,timeout=timeout)
        if self.group_mode=="restart" and self.inventories>=2:value["State"]["StartedAt"]+=":new"
        return value
    def exec_bounded(self,container,argv,*,timeout,max_bytes):
        pipe={"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False}
        if argv[2]==session.INVENTORY_READER:
            self.inventories+=1
            assert argv[-1]==self.group.prepared["session"] and max_bytes==65536
            if ((self.group_mode=="initial-transport" and self.inventories==1)
                    or (self.group_mode=="final-transport" and self.inventories==2)):
                raise RuntimeError(CANARY)
            packet=inventory_packet(self.group)
            if self.group_mode=="new-file" and self.inventories==2:
                packet["files"].append({**packet["files"][0],"pid":45})
            if self.group_mode=="unsealed":packet["sealed"]=False
            if self.group_mode=="overflow":packet["processes_truncated"]=True
            if self.group_mode=="unreadable":
                packet["processes"]=[p for p in packet["processes"] if p["pid"]!=44]
                packet["unreadable_pids"]=[44]
            if self.group_mode=="file-replaced":packet["files"][0]["inode"]=99
            return SimpleNamespace(returncode=0,stdout=json.dumps(packet),capture=pipe)
        if argv[2]==single.CONTEXT_READER:
            pid=int(argv[-1])
            context=json.loads(self.group.raw_by_pid[pid].splitlines()[0])["payload"]["context"]
            if self.group_mode=="process-error":raise RuntimeError(CANARY)
            return SimpleNamespace(returncode=0,stdout=json.dumps({"schema":"guard-lifecycle-process-context/1",
                "context":context,"stable":True,"same_executable":True}),capture=pipe)
        pid=int(re.search(r'/pid-([0-9]+)-',argv[-2])[1])
        if self.group_mode==f"bad-{pid}":raise RuntimeError(CANARY)
        previous=self.case.raw;self.case.raw=self.group.raw_by_pid[pid]
        try:return super().exec_bounded(container,argv,timeout=timeout,max_bytes=max_bytes)
        finally:self.case.raw=previous


def collect(group,mode=None):
    return lifecycle.collect_session(GroupDocker(group,mode),group.env,group.target,
        group.source["container"],IMAGE,group.prepared)


def test_session_reads_each_owned_record_and_indexes_exact_refs_without_whole_run_claim(group):
    value=collect(group)
    assert value["live_census_stable"] and value["recorded_files_covered"]
    assert value["python_processes_without_recording"]==[{"pid":44,"start_ticks":99}]
    assert all(r["capture"]["process_context_verified"] and r["listed_file_identity_match"] for r in value["recordings"])
    domains=list(value["request_index"]["domains"].values())
    assert len(domains)==1
    assert domains[0]["assignments"]==[{"external_sha256":"d"*64,"internal_sha256":"e"*64,"event":[0,3]}]
    assert [1,3] in domains[0]["fingerprints"]["e"*64]
    assert value["security"]=="UNKNOWN" and value["data_origin"]=="EXAMPLE"
    assert value["producer_authenticated"] is False and value["whole_run_coverage_verified"] is False
    assert CANARY not in json.dumps(value)


@pytest.mark.parametrize("mode",["bad-42","bad-43","final-transport","restart","late-inventory-owner",
    "new-file","overflow","unreadable","file-replaced","process-error","unsealed"])
def test_independent_recordings_survive_transport_restart_census_and_context_gaps(group,mode):
    value=collect(group,mode)
    good=[r for r in value["recordings"] if r["capture"] is not None]
    assert len(good)==(1 if mode.startswith("bad-") else 2)
    for row in good:
        expected=single.parse_recording(group.raw_by_pid[row["pid"]],group.prepared)[0]
        assert row["capture"]["records"]==expected
    assert value["whole_run_coverage_verified"] is False and value["security"]=="UNKNOWN"
    if mode not in {"process-error","unsealed"}:assert value["recorded_files_covered"] is False
    if mode=="file-replaced":assert good[0]["listed_file_identity_match"] is False
    if mode=="process-error":assert all(r["capture"]["process_context_verified"] is False for r in good)
    assert CANARY not in json.dumps(value)


def test_initial_census_failure_is_explicit_even_when_later_census_succeeds(group):
    value=collect(group,"initial-transport")
    assert value["before"] is None and value["after"] is not None and value["recordings"]==[]
    assert not value["live_census_stable"] and not value["recorded_files_covered"]
    assert "LIFECYCLE_INITIAL_INVENTORY_UNAVAILABLE" in value["diagnostic_codes"]
    assert CANARY not in json.dumps(value)


@pytest.mark.parametrize("origin",[DataOrigin.DECLARED,DataOrigin.MEASURED])
def test_nonexample_session_never_authenticates_assertions_or_invents_measured_origin(group,origin):
    env=group.env.model_copy(update={"data_origin":origin}).seal()
    target={**group.target,"environment_ref":env.ref().model_dump(mode="json")}
    value=lifecycle.collect_session(GroupDocker(group),env,target,group.source["container"],IMAGE,group.prepared)
    assert value["data_origin"]=="DECLARED" and value["record_authenticity_verified"] is False


def test_reused_external_ID_remains_ambiguous_instead_of_collapsing_requests(group):
    def change(rows):rows[-2]["payload"]=native_payload(request="f"*64)
    group.raw_by_pid[43]=revised(group.raw_by_pid[43],change)
    domain=next(iter(collect(group)["request_index"]["domains"].values()))
    assert domain["ambiguous_external_sha256"]==["d"*64] and len(domain["assignments"])==2
    assert set(domain["fingerprints"])=={"d"*64,"e"*64,"f"*64}


def test_call_does_not_claim_internal_assignment_and_untyped_metadata_is_not_an_ID():
    row={"event":"NATIVE","payload":native_payload(phase="CALL",request="d"*64,external=None)}
    assert links.assignment(row) is None and links.fingerprints(row)==["d"*64]
    row["payload"]=native_payload(external="CALL")
    assert links.assignment(row) is None and links.fingerprints(row)==["e"*64]


@pytest.mark.parametrize("changed",["clock","owner"])
def test_clock_or_container_generation_changes_partition_declared_links(group,changed):
    value=collect(group)
    second=value["recordings"][1]["capture"]
    if changed=="clock":second["records"][0]["payload"]["context"]["time_namespace"]+=1
    else:second["provenance"]["owner"]["started_at_sha256"]="f"*64
    assert len(session.request_index(value["recordings"])["domains"])==2


@pytest.mark.parametrize("limit",["MAX_INDEX_IDS","MAX_INDEX_EVENTS"])
def test_index_limits_keep_all_original_records_and_report_incomplete_index(group,monkeypatch,limit):
    monkeypatch.setattr(session,limit,1)
    value=collect(group)
    assert not value["request_index"]["stored_record_index_complete"]
    assert all(r["capture"]["records"]==single.parse_recording(group.raw_by_pid[r["pid"]],group.prepared)[0]
        for r in value["recordings"])


@pytest.mark.parametrize("change",[
    lambda p:p.update(body=CANARY),lambda p:p.update(session="0"*32),
    lambda p:p.update(directory_stable=1),lambda p:p["directory_before"].update(mode=0o777),
    lambda p:p["files"].append(deepcopy(p["files"][0])),lambda p:p["files"][0].update(pid=True),
    lambda p:p["files"][0].update(path=CANARY),lambda p:p["processes"][0].update(command=CANARY),
    lambda p:p["processes"][0].update(boot_sha256=CANARY),lambda p:p["processes"][0].update(same_executable=1),
    lambda p:p["unreadable_pids"].append(42),lambda p:p.update(files=[p["files"][0]]*33),
])
def test_inventory_rejects_unsafe_unbounded_or_duplicate_metadata(group,change):
    packet=inventory_packet(group);change(packet)
    with pytest.raises(ValueError):session.parse_inventory(json.dumps(packet).encode(),group.prepared["session"])


def arguments(group,output):
    return ["capture-lifecycle-session","--env",str(group.root/"env.json"),"--target",str(group.root/"target.json"),
        "--prepared",str(group.manifest_path),"--container",group.source["container"],"--image",IMAGE,"--output",str(output)]


def test_product_default_validates_without_client_or_output(group,monkeypatch):
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:(_ for _ in ()).throw(AssertionError("runtime touched")))
    output=group.root/"session.json"
    result=CliRunner().invoke(app,arguments(group,output))
    assert result.exit_code==0,result.output
    assert json.loads(result.output)["capture_executed"] is False and not output.exists()


def test_product_execution_saves_exact_safe_bytes_and_never_overwrites(group,monkeypatch):
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:GroupDocker(group))
    output=group.root/"session.json"
    result=CliRunner().invoke(app,arguments(group,output)+["--execute"])
    assert result.exit_code==0,result.output
    raw=output.read_bytes();summary=json.loads(result.output)
    assert summary["artifact_saved"] and summary["sha256"]==hashlib.sha256(raw).hexdigest() and summary["bytes"]==len(raw)
    assert len(json.loads(raw)["recordings"])==2 and CANARY.encode() not in raw
    second=CliRunner().invoke(app,arguments(group,output)+["--execute"])
    assert second.exit_code==2 and output.read_bytes()==raw


@pytest.mark.parametrize("failure",["open","write","late-existing"])
def test_output_failure_returns_collected_safe_facts_without_raw_error_or_overwrite(group,monkeypatch,failure):
    output=group.root/"session.json";original=Path.open
    def client(*args):
        if failure=="late-existing":output.write_bytes(b"EXAMPLE-existing-artifact")
        return GroupDocker(group,"bad-43")
    monkeypatch.setattr(owned_capture,"create_capture_client",client)
    class Broken:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def write(self,data):raise OSError(CANARY)
    def opened(path,*args,**kwargs):
        if path==output and args and args[0]=="x":
            if failure=="open":raise OSError(CANARY)
            if failure=="write":return Broken()
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"open",opened)
    result=CliRunner().invoke(app,arguments(group,output)+["--execute"])
    assert result.exit_code==1,result.output
    value=json.loads(result.output)
    assert value["artifact_saved"] is False and value["capture_executed"] is True
    assert value["capture"]["recordings"][0]["capture"]["records"]==single.parse_recording(group.raw_by_pid[42],group.prepared)[0]
    assert value["capture"]["recordings"][1]["capture"] is None
    assert CANARY not in result.output
    if failure=="late-existing":assert output.read_bytes()==b"EXAMPLE-existing-artifact"


def test_bad_environment_ref_fails_before_runtime(group,monkeypatch):
    group.target["environment_ref"]["sha256"]="0"*64
    (group.root/"target.json").write_text(json.dumps(group.target))
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *a:(_ for _ in ()).throw(AssertionError("runtime touched")))
    result=CliRunner().invoke(app,arguments(group,group.root/"session.json")+["--execute"])
    assert result.exit_code==2 and result.output.strip()=="LIFECYCLE_SESSION_CAPTURE_FAILED"


def test_filesystem_identifiers_preserve_unsigned_kernel_values(group):
    packet=inventory_packet(group)
    packet["files"][0]["inode"]=2**64-1
    assert session.parse_inventory(json.dumps(packet).encode(),group.prepared["session"])["files"][0]["inode"]==2**64-1
    packet["files"][0]["inode"]=2**64
    with pytest.raises(ValueError):session.parse_inventory(json.dumps(packet).encode(),group.prepared["session"])


@pytest.mark.parametrize("condition",["normal","unknown-name","symlink","unreadable","file-bound","process-bound","bad-seal"])
def test_actual_inventory_script_reads_only_bounded_metadata_without_native_or_secret_access(condition):
    output=[];reads=[]
    def metadata(**extra):
        return SimpleNamespace(st_dev=1,st_ino=2,st_uid=1000,st_mode=0o100600,st_size=20,st_mtime_ns=3,st_ctime_ns=4,**extra)
    class Entry:
        def __init__(self,name):self.name=name
        def stat(self,*,follow_symlinks):
            assert follow_symlinks is False
            value=metadata()
            if condition=="symlink" and self.name.endswith('.jsonl') or condition=="bad-seal" and self.name=='SEALED':value.st_mode=0o120777
            return value
    class Scan:
        def __init__(self,entries):self.entries=entries
        def __enter__(self):return iter(self.entries)
        def __exit__(self,*a):pass
    files=[Entry('pid-42-99.jsonl'),Entry('SEALED')]
    if condition=='unknown-name':files.append(Entry(CANARY))
    if condition=='file-bound':files=[Entry(f'pid-{pid}-99.jsonl') for pid in range(1,35)]
    pids=[42,999] if condition!='process-bound' else list(range(1,131))+[999]
    class FakePath:
        def __init__(self,value):self.path=str(value)
        def __truediv__(self,value):return FakePath(self.path+'/'+str(value))
        def open(self,mode):
            reads.append(self.path)
            if condition=='unreadable' and self.path=='/proc/42/stat':raise OSError(CANARY)
            if self.path.endswith('/boot_id'):raw=b'EXAMPLE-boot'
            elif self.path.endswith('/stat'):
                pid=int(self.path.split('/')[2]);values=['R','1']+['0']*17+['99']
                raw=(str(pid)+' ('+CANARY+') '+' '.join(values)).encode()
            elif self.path.endswith('/status'):raw=b'Uid:\t1000\t1000\t1000\t1000\n'
            elif self.path.endswith('/timens_offsets'):raw=b'monotonic 0 0\n'
            else:raise AssertionError('unexpected read: '+self.path)
            return io.BytesIO(raw)
    class FakeOS:
        O_RDONLY=1;O_DIRECTORY=2;O_NOFOLLOW=4
        path=SimpleNamespace(samefile=lambda *a:True)
        def open(self,path,flags,*,dir_fd=None):
            if path!='/':assert flags & self.O_NOFOLLOW
            return 10
        def close(self,fd):pass
        def getpid(self):return 999
        def geteuid(self):return 1000
        def fstat(self,fd):
            value=metadata();value.st_mode=0o40700;return value
        def scandir(self,path):return Scan(files if path==10 else [Entry(str(pid)) for pid in pids])
        def stat(self,path):return metadata()
    tree=ast.parse(session.INVENTORY_READER)
    tree.body=[node for node in tree.body if not isinstance(node,(ast.Import,ast.ImportFrom))]
    namespace={"os":FakeOS(),"json":json,"hashlib":hashlib,"re":re,"stat":stat,"Path":FakePath,
        "PurePosixPath":PurePosixPath,"sys":SimpleNamespace(argv=['EXAMPLE','a'*32]),"print":output.append}
    exec(compile(tree,'EXAMPLE-owned-inventory','exec'),namespace)
    assert len(output)==1 and CANARY not in output[0]
    value=session.parse_inventory(output[0].encode(),'a'*32)
    assert not any(path.endswith(('/cmdline','/environ')) for path in reads)
    if condition=='normal':assert value['sealed'] and len(value['files'])==len(value['processes'])==1
    if condition=='unknown-name':assert value['unknown_entries']==1
    if condition=='symlink':assert value['files'][0]['regular'] is False
    if condition=='unreadable':assert value['unreadable_pids']==[42]
    if condition=='file-bound':assert value['files_truncated'] and len(value['files'])==32
    if condition=='process-bound':assert value['processes_truncated'] and len(value['processes'])==128
    if condition=='bad-seal':assert not value['sealed'] and value['unknown_entries']==1
