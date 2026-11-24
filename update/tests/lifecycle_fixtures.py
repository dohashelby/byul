"""Function-scoped lifecycle capture fixtures and sealed recording helpers."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

import pytest


CANARY="EXAMPLE_LIFECYCLE_PRIVATE_6FB53A"
CONTEXT={"pid":42,"start_ticks":99,"uid":1000,"boot_sha256":"1"*64,
         "pid_namespace":23,"time_namespace":24,"time_offsets_sha256":"2"*64}
IMAGE="example.invalid/engine@sha256:"+"a"*64
REPOSITORY=Path(__file__).resolve().parents[2]
SOURCE_MODULE="vllm.v1.engine.input_processor"


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
def lifecycle_external():
    directory=tempfile.TemporaryDirectory(prefix="byul-lifecycle-test-")
    root=Path(directory.name).resolve()
    assert root.is_relative_to(Path(tempfile.gettempdir()).resolve()) and not root.is_relative_to(REPOSITORY)
    try:
        yield root
    finally:
        assert root.name.startswith("byul-lifecycle-test-")
        directory.cleanup()


@pytest.fixture
def lifecycle_writer_factory(monkeypatch):
    from guard.adapters import vllm_lifecycle_sensor as sensor

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


@pytest.fixture
def lifecycle_capture_case(lifecycle_external, lifecycle_writer_factory):
    from guard.collectors import lifecycle
    from guard.executors import lifecycle_capture as capture_module
    from guard.models import canonical_json
    from tests.builders import environment

    root=lifecycle_external
    manifest=lifecycle.prepare_assets(root/"assets",REPOSITORY)
    prepared=lifecycle.load_prepared(root/"assets/manifest.json")
    assert manifest==prepared
    writer,fake=lifecycle_writer_factory(session=prepared["session"],settings_sha=prepared["assets"]["settings.json"]["sha256"])
    writer.write("MODULE",{"module":SOURCE_MODULE,"verified":True,
        "source_sha256":capture_module.sensor.SOURCE_SHA[SOURCE_MODULE],"code_sha256":"c"*64})
    writer.write("ASGI",{"request_sha256":"d"*64,"phase":"DISCONNECT_RECEIVED","more_body":None})
    writer.close("SEALED")
    env=environment(engine={"name":"vllm","version":"0.31.0","image_digest":IMAGE})
    target={"run_id":"EXAMPLE-lifecycle","environment_ref":env.ref().model_dump(mode="json"),
            "engine":{"container":"EXAMPLE-engine"}}
    source={"container":"EXAMPLE-engine","image":IMAGE,
        "path":"/guard-lifecycle/data/"+prepared["session"]+"/pid-42-99.jsonl"}
    (root/"env.json").write_bytes(canonical_json(env.to_dict()))
    (root/"target.json").write_text(json.dumps(target),encoding="utf-8")
    return SimpleNamespace(root=root,prepared=prepared,env=env,target=target,source=source,
        raw=bytes(fake.data),manifest_path=root/"assets/manifest.json")


# Legacy imports share the canonical fixture instances within each test.
@pytest.fixture
def external(lifecycle_external):
    return lifecycle_external


@pytest.fixture
def writer_factory(lifecycle_writer_factory):
    return lifecycle_writer_factory


@pytest.fixture
def case(lifecycle_capture_case):
    return lifecycle_capture_case


def revised(raw,change):
    from guard.executors import lifecycle_capture as capture_module

    rows=[json.loads(line) for line in raw.splitlines()]
    change(rows)
    previous="0"*64;lines=[]
    for seq,row in enumerate(rows):
        row["seq"]=seq;row["previous_sha256"]=previous
        line=capture_module.canonical_row(row)
        lines.append(line);previous=hashlib.sha256(line).hexdigest()
    return b"".join(lines)
