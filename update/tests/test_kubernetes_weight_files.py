"""EXAMPLE owned Pod generation and exact helper allowlist; no live cluster."""
import json

import pytest

from guard.executors.weight_file_capture import KubernetesWeightReader, HASH_READER, MAX_PACKET_BYTES
from guard.executors.kubernetes_lifecycle_capture import KubernetesCaptureError, supervised_source
from tests.test_kubernetes_lifecycle import target, World
from tests.test_lifecycle_session import group
from tests.test_lifecycle_capture import case, external, IMAGE
from tests.test_lifecycle_sensor import writer_factory
from tests.test_native_weight_files import packet, ROOT, sha


@pytest.mark.parametrize("mode",[None,"not-ready","late-owner","cleanup"])
def test_owned_kubernetes_hash_read_preserves_packet_after_generation_recheck_gap(group,target,mode):
    world=World(group,target,"not-ready" if mode=="not-ready" else None);commands=[]
    def runner(argv,**kwargs):
        if "exec" not in argv:return world.runner(argv,**kwargs)
        command=argv[argv.index("--")+1:];commands.append(command)
        assert command[:5]==["python3","-I","-S","-B","-c"] and command[5]==supervised_source(HASH_READER)
        assert command[7:]==[ROOT,"a.bin","1024"] and 0<float(command[6])<=5
        assert "-i" not in argv and "-t" not in argv and "--container=engine" in argv
        assert kwargs["max_stdout_bytes"]==MAX_PACKET_BYTES
        if mode=="late-owner":world.bad_owner=True
        return {"returncode":0,"stdout_bytes":json.dumps(packet(ROOT+"/a.bin")).encode(),"stderr_bytes":b"EXAMPLE-private",
            "capture":{"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False,
                "pipes_closed":mode!="cleanup","read_errors":["child_reap"] if mode=="cleanup" else []}}
    reader=KubernetesWeightReader(target,group.env,group.prepared,ROOT,["a.bin"],1024,runner=runner)
    value,owner,stable=reader.read("engine",IMAGE,ROOT,"a.bin",1024)
    assert value["content_sha256"]==sha(b"EXAMPLE-model-bytes") and len(commands)==1
    assert stable is (mode!="late-owner") and owner["pod_uid"]==target["kubernetes"]["engine"]["pod_uid"]
    report=reader.report()
    assert report["profile"]=="kubernetes-owned-weight-file-read-v1" and report["injected_runner"]
    assert not report["node_runtime_image_verified"] and "EXAMPLE-private" not in json.dumps(report)
    if mode=="cleanup":
        assert report["collection_gap"] and report["parent_cleanup_unverified"]
        assert stable and value["file_identity_stable"] and value["logical_binding_stable"]


@pytest.mark.parametrize("change",[lambda a:a.__setitem__(0,"sh"),lambda a:a.__setitem__(2,"print('private')"),
    lambda a:a.__setitem__(3,"/other"),lambda a:a.__setitem__(4,"../a.bin"),lambda a:a.__setitem__(4,[]),
    lambda a:a.__setitem__(5,"1025")])
def test_kubernetes_rejects_unlisted_command_before_any_api(group,target,change):
    reader=KubernetesWeightReader(target,group.env,group.prepared,ROOT,["a.bin"],1024,
        runner=lambda *a,**kw:pytest.fail("no API before command validation"))
    argv=["python3","-c",HASH_READER,ROOT,"a.bin","1024"];change(argv)
    with pytest.raises(KubernetesCaptureError):reader.exec_bounded("engine",argv,timeout=5,max_bytes=MAX_PACKET_BYTES)


def test_docker_lab_false_convention_does_not_relax_kubernetes_lab_declaration(group,target):
    with pytest.raises(ValueError):KubernetesWeightReader({**target,"lab":False},group.env,group.prepared,ROOT,["a.bin"],1024,
        runner=lambda *a,**kw:pytest.fail("no API for an invalid Kubernetes target"))
