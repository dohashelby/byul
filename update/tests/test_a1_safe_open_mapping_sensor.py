"""Exact vLLM generator + EXAMPLE safe_open class + owned stdlib mmap.

The native library gate is mocked only in explicitly EXAMPLE positive tests.
No safetensors/Torch installation, C execution or real serving/worker proof.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import json,mmap,os,sys,threading,types
import pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_weight_iteration_sensor as iteration
from guard.adapters import vllm_file_read_sensor as scope17
from guard.adapters import vllm_safe_open_mapping_sensor as sensor,vllm_safe_open_mapping_metadata as metadata
from guard.collectors.model_read_scope import deploy_scope,scope_summary
from guard.models import canonical_json,sha256_hex
from tests.test_a1_file_read_sensor import scope_value,MODEL_BYTES
from tests.test_a1_weight_iteration_sensor import make_world as iteration_world,packet,PRIVATE


def make_world(tmp_path,patch,*,mode='normal',session='b'*32,path=None,scope_path=None):
    path=path or tmp_path/'public-example.safetensors'
    if not path.exists():path.write_bytes(MODEL_BYTES)
    if scope_path is None:
        value=scope_value(path);scope_path=tmp_path/'scope.json';scope_path.write_bytes(deploy_scope(value,canonical_json(value),session))
    private=types.ModuleType('_mapping_test_iteration');private.__file__=iteration.__file__
    raw=Path(iteration.__file__).read_bytes();assert sha256_hex(raw)==sensor.ITERATION16_SHA256
    exec(compile(raw,iteration.__file__,'exec',dont_inherit=True,optimize=0),vars(private))
    with patch.context() as local:
        local.setattr(iteration,'configure',lambda base:sensor.configure(base,private,scope17,scope_path=str(scope_path)))
        world=iteration_world('late-callback' if mode=='late-callback' else 'normal',session=session)
    weights=world.modules[iteration.WEIGHTS];wrapped=vars(weights)['safe_open']
    cells=dict(zip(wrapped.__code__.co_freevars,(cell.cell_contents for cell in wrapped.__closure__)))
    original=cells['original_open'];world.mappings=[];world.closed=[]
    def enter(value):
        if mode=='enter-error':raise OSError(PRIVATE)
        fd=os.open(value.path,os.O_RDONLY)
        try:value.mapping=mmap.mmap(fd,0,access=mmap.ACCESS_READ)
        finally:os.close(fd)
        world.mappings.append(value.mapping);world.reads.append(value.path);return value
    def get(value,name):
        if mode=='tensor-error':raise OSError(PRIVATE)
        return world.tensor_values[name]
    def exit(value,*args):
        if mode!='retained':value.mapping.close();world.closed.append(True)
        if mode=='exit-error':raise OSError(PRIVATE)
    original.__enter__,original.get_tensor,original.__exit__=enter,get,exit
    if mode!='library-unavailable':patch.setattr(sensor,'library_identity',lambda original,cache:True)
    def iterator():return weights.safetensors_weights_iterator([str(path)],False,None,None,safetensors_prefetch_num_threads=1,safetensors_prefetch_block_size=1)
    world.load=lambda:world.auto._load_module('',world.model,iterator());world.loader._get_weights_iterator=lambda source:iterator()
    world.path=path;world.scope_path=scope_path
    world.cleanup=lambda:[mapping.close() for mapping in world.mappings if not mapping.closed]
    return world


def prepared_for(world):
    raw=world.scope_path.read_bytes();value=json.loads(raw)
    return {'profile':sensor.PROFILE,'session':value['session'],'model_read_scope':scope_summary(value),
        'assets':{'model-read-scope.json':{'sha256':sha256_hex(raw),'bytes':len(raw)}}}


def projection(world,*,bound=True):
    value=packet(world,bound=bound);value['recordings'][0]['capture']['records'][0]['payload']['profile']=sensor.PROFILE
    return metadata.project(value,prepared_for(world))


@pytest.mark.parametrize('mode',['normal','retained','enter-error','tensor-error','exit-error','late-callback'])
def test_same_generator_delegates_without_values_and_preserves_mapping_hints(tmp_path,monkeypatch,mode):
    world=make_world(tmp_path,monkeypatch,mode=mode)
    try:
        if mode.endswith('error') or mode=='late-callback':
            with pytest.raises(OSError,match=PRIVATE):world.run_loader()
        else:assert world.run_loader() is None
        result=projection(world);samples=result['samples'];assert samples
        assert all(row[key] is False for row in samples for key in sensor.TRUTH)
        assert any(row['iterator_pair_verified'] for row in samples)
        if mode!='enter-error':
            if sys.platform.startswith('linux'):
                assert any(row['after'] and row['after']['matches'] for row in samples)
            else:assert all(row['after']['status']=='unavailable' for row in samples if row['after'])
        if mode=='retained' and sys.platform.startswith('linux'):
            exited=next(row for row in samples if row['phase']=='EXIT_RETURN')
            assert exited['after']['matches'] and exited['mapping_release_verified'] is False
        assert PRIVATE not in json.dumps(result) and str(world.path) not in json.dumps(result) and MODEL_BYTES.decode() not in json.dumps(result)
        assert world.path.read_bytes()==MODEL_BYTES
    finally:world.cleanup()


def test_missing_library_gate_does_not_read_maps_or_proxy_unverified_class(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch,mode='library-unavailable')
    monkeypatch.setattr(sensor,'mapping_snapshot',lambda *a:pytest.fail('unqualified library must not collect maps'))
    try:
        world.run_loader();result=projection(world)
        assert [row['phase'] for row in result['samples']]==['LIBRARY_UNAVAILABLE']
        assert all(row['before'] is None and row['after'] is None for row in result['samples'])
    finally:world.cleanup()


def test_actual_library_default_gate_rejects_missing_or_replaced_aliases():
    assert sensor.library_identity(object,{}) is False


def test_missing_process_binding_retains_observations_without_worker_truth(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch)
    try:
        world.run_loader();result=projection(world,bound=False)
        assert result['samples'] and all(not row['process_binding_verified'] and not row['worker_assignment_verified'] for row in result['samples'])
    finally:world.cleanup()


def test_fork_generation_never_reads_maps(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);world.observer.mapping_pid=-1
    monkeypatch.setattr(sensor,'mapping_snapshot',lambda *a:pytest.fail('wrong PID must not read maps'))
    try:
        world.run_loader();assert projection(world)['samples']==[]
    finally:world.cleanup()


@pytest.mark.parametrize('mutation',['truth','scope','phase','context','clock','raw-address'])
def test_closed_event_contract_rejects_tampering(tmp_path,monkeypatch,mutation):
    world=make_world(tmp_path,monkeypatch)
    try:
        world.run_loader();rows=[{'seq':i+1,**row} for i,row in enumerate(world.observer.recorded) if row['event']=='SAFE_OPEN_MAPPING']
        value=deepcopy(rows[0]['payload'])
        if mutation=='truth':value['model_bytes_read_verified']=True
        elif mutation=='scope':value['scope_sha256']='bad'
        elif mutation=='phase':value['phase']='OTHER'
        elif mutation=='context':value['context_sha256']=False
        elif mutation=='clock':value['operation_finished_ns']=value['operation_started_ns']-1
        else:value['raw_address']=123
        with pytest.raises(ValueError,match='SAFE_OPEN_MAPPING_EVENT_INVALID'):metadata.validate_sample(value)
    finally:world.cleanup()


def test_mapping_replacement_and_unlink_never_mean_release(tmp_path):
    path=tmp_path/'public-map';path.write_bytes(b'EXAMPLE public mapping')
    observer=NS(mapping_pid=os.getpid(),mapping_regions={},weight_nonce=lambda:'a'*64,weight_lock=threading.RLock())
    fd=os.open(path,os.O_RDONLY)
    try:owned=mmap.mmap(fd,0,access=mmap.ACCESS_READ)
    finally:os.close(fd)
    identity=sensor.selected_identity(path)
    try:
        before=sensor.mapping_snapshot(observer,identity)
        if sys.platform.startswith('linux'):
            assert before['matches'];path.unlink();path.write_bytes(b'EXAMPLE replacement')
            assert sensor.selected_identity(path)!=identity
            after=sensor.mapping_snapshot(observer,identity);assert after['matches'] and all(row['deleted'] for row in after['matches'])
        else:assert before['status']=='unavailable'
    finally:owned.close()


def test_bounded_snapshot_retains_complete_prefix_without_raw_addresses(monkeypatch):
    import builtins,io
    observer=NS(mapping_pid=os.getpid(),mapping_regions={},weight_nonce=lambda:'a'*64,weight_lock=threading.RLock())
    raw=b'1000-2000 r--p 00000000 00:00 7 /EXAMPLE-public\nmalformed\n'
    original=builtins.open
    monkeypatch.setattr(builtins,'open',lambda path,*a,**kw:io.BytesIO(raw) if path=='/proc/self/maps' else original(path,*a,**kw))
    result=sensor.mapping_snapshot(observer,(os.makedev(0,0) if hasattr(os,'makedev') else 0,7))
    if sys.platform.startswith('linux'):
        assert result['status']=='bounded' and len(result['matches'])==1 and result['matches'][0]['mapped_bytes']==4096
        assert '1000' not in json.dumps(result) and '/EXAMPLE-public' not in json.dumps(result)
    else:assert result['status']=='unavailable'


def test_mapping_collection_failure_does_not_change_original_tensor_return_or_close(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch)
    def unavailable(*a):raise RuntimeError(PRIVATE)
    monkeypatch.setattr(sensor,'mapping_snapshot',unavailable)
    try:
        world.run_loader();result=projection(world)
        assert [name for name,value in world.applied]==['weight','other'] and world.closed==[True]
        assert result['samples'] and all(row['before']['status']=='unavailable' for row in result['samples'])
        assert PRIVATE not in json.dumps(result)
    finally:world.cleanup()


def test_reviewed_code_contract_matches_loaded_source_and_independent_compile():
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]))
    namespace=types.ModuleType('_mapping_independent_compile');namespace.__file__=sensor.__file__
    exec(compile(Path(sensor.__file__).read_bytes(),sensor.__file__,'exec',dont_inherit=True,optimize=0),vars(namespace))
    assert namespace.code_contract(base8)==sensor.code_contract(base8)==sensor.RUNTIME_CODE_CONTRACTS[runtime]
