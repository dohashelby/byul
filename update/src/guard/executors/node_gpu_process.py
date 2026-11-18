"""Consume local-node GPU facts only through the existing live task proof."""
from copy import deepcopy
import hashlib,sys,time
from guard.adapters import gpu_process_probe as probe
from guard.models import canonical_json,sha256_hex
from guard.runtime.bounded import capture_pipes,strict_json
from guard.runtime import gpu_process_source
from . import node_image_proof

_KEY=object()


class NodeGpuProcessSnapshot:
    __slots__=('_scope','_packet','_receipt')
    def __init__(self,scope,packet,receipt,*,_key=None):
        if _key is not _KEY:raise ValueError('GPU_PROCESS_COLLECTED_SNAPSHOT_REQUIRED')
        self._scope,self._packet,self._receipt=deepcopy(scope),deepcopy(packet),deepcopy(receipt)
    def packet(self):return deepcopy(self._packet)
    def public_receipt(self):return deepcopy(self._receipt)
    def matches(self,binding):return self._scope==binding.get('engine')


def collect(binding,task_proof,owners,*,runner=None,max_seconds=10):
    """The task proof is opaque, fresh and local; JSON success flags are refused."""
    if(type(binding)is not dict or type(binding.get('engine'))is not dict
            or type(task_proof)is not node_image_proof.NodeImageProof or not task_proof.matches(binding['engine'])):
        raise ValueError('GPU_PROCESS_LOCAL_TASK_PROOF_REQUIRED')
    if type(max_seconds)is not int or not 1<=max_seconds<=20:raise ValueError('GPU_PROCESS_BUDGET_INVALID')
    receipt=task_proof.public_receipt();observed=receipt.get('observed_monotonic_ns')
    now=time.monotonic_ns()
    if type(observed)is not int or not 0<=now-observed<=30_000_000_000:raise ValueError('GPU_PROCESS_TASK_PROOF_EXPIRED')
    task=receipt['task'];selection={'schema':probe.SELECTION_SCHEMA,
        'anchor':{'host_pid':task['pid'],'start_ticks':task['start_ticks'],'pid_namespace':task['pid_namespace']},
        'owners':deepcopy(owners)}
    probe.selection(selection)
    if any(row['context']['boot_sha256']!=receipt['node']['boot_id_sha256']for row in owners):
        raise ValueError('GPU_PROCESS_NODE_BOOT_MISMATCH')
    program=gpu_process_source.source();run=runner or capture_pipes
    began=time.monotonic_ns()
    result=run([sys.executable,'-I','-S','-B','-c',program],timeout=max_seconds+3,env_extra={},
        max_stdout_bytes=probe.MAX_OUTPUT,max_stderr_bytes=32768,
        input_bytes=canonical_json({'selection':selection,'max_seconds':max_seconds}))
    finished=time.monotonic_ns()
    if(result.get('returncode')!=0 or result.get('capture',{}).get('complete')is not True
            or type(result.get('stderr_bytes'))is not bytes or result['stderr_bytes']):
        raise ValueError('GPU_PROCESS_NODE_CAPTURE_INCOMPLETE')
    packet=strict_json(result['stdout_bytes'],limit=probe.MAX_OUTPUT);probe.validate_packet(packet,selection)
    if(packet['observer']['boot_sha256']!=receipt['node']['boot_id_sha256']
            or not began<=packet['started_ns']<=packet['finished_ns']<=finished):
        raise ValueError('GPU_PROCESS_NODE_INTERVAL_UNVERIFIED')
    origin='EXAMPLE'if runner is not None or binding.get('lab')is True else receipt['data_origin']
    public={'schema':'guard-local-node-gpu-process-capture/1','node':deepcopy(receipt['node']),
        'engine_binding_sha256':sha256_hex(canonical_json(binding['engine'])),
        'selection':selection,'selection_sha256':sha256_hex(canonical_json(selection)),
        'program_sha256':hashlib.sha256(program.encode()).hexdigest(),
        'packet_sha256':sha256_hex(canonical_json(packet)),'data_origin':origin,
        'actual_gpu_source_qualified':False,'producer_registered':False,'whole_group_recovery_verified':False,
        'whole_gpu_release_verified':False,'engine_launched_by_collector':False}
    return NodeGpuProcessSnapshot(binding['engine'],packet,public,_key=_KEY)


def collect_for_reader(reader,owners,*,containerd_address=None):
    """Preserve sampled facts if a later API/owner check fails, without coverage."""
    record={'schema':'guard-owned-node-gpu-process-read/1','collected':False,
        'owner_binding_verified':False,'clock_window_verified':False,
        'packet':None,'receipt':None,'diagnostic_codes':[]}
    try:
        if containerd_address not in(None,'/run/k3s/containerd/containerd.sock'):raise ValueError
        before=reader._owner(min(5,reader._remaining(reader.deadline)))
        engine=reader.binding['engine']
        def node():
            raw=reader._request(['get','node',engine['node'],'-o','json'],deadline=reader.deadline,cap=262144)
            return strict_json(raw,limit=262144)
        proof=node_image_proof.collect_local_node_image_proof({**reader.binding,'lab':reader.lab},node(),
            runner=reader.runner if reader.injected else None,timeout=min(30,reader._remaining(reader.deadline)),
            **({'containerd_address':containerd_address}if containerd_address is not None else{}))
        budget=min(10,int(reader._remaining(reader.deadline))-3)
        snapshot=collect({**reader.binding,'lab':reader.lab},proof,owners,
            runner=reader.runner if reader.injected else None,max_seconds=budget)
        record.update(packet=snapshot.packet(),receipt=snapshot.public_receipt())
        if(node_image_proof.validate_node(node(),engine['node'],node_image_proof.kernel_identity())!=proof.public_receipt()['node']
                or reader._owner(min(5,reader._remaining(reader.deadline)))!=before):raise ValueError
        record.update(collected=True,owner_binding_verified=True,clock_window_verified=True)
    except Exception:record['diagnostic_codes'].append('GPU_PROCESS_OWNED_NODE_READ_UNVERIFIED')
    return record
