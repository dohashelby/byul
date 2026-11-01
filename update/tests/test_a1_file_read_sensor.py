"""Real owned EXAMPLE binary FD reads through the exact eager source body."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import builtins,hashlib,json,os,types
import pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_file_read_sensor as sensor,vllm_file_read_metadata as metadata
from guard.adapters import vllm_weight_iteration_sensor as iteration
from guard.collectors.model_read_scope import deploy_scope,scope_summary
from guard.models import canonical_json,sha256_hex
from tests.test_a1_weight_iteration_sensor import make_world as iteration_world,packet,PRIVATE

MODEL_BYTES=b'EXAMPLE PUBLIC SYNTHETIC MODEL BYTES; not a safetensors decoder fixture'


def scope_value(path,*,env=None,target=None,hash_model_bytes=True,max_hash_bytes=sensor.MAX_HASH_BYTES):
    return {'schema':sensor.SCOPE_SCHEMA,'session':None,'input_scope_sha256':None,
        'environment_ref':env.ref().model_dump(mode='json') if env else {'kind':'ENV','id':'ENV-EXAMPLE-FD','revision':1,'sha256':'a'*64},
        'target_sha256':sha256_hex(canonical_json(target)) if target else 'b'*64,
        'model_sha256':sha256_hex(env.model.id.encode()) if env else 'c'*64,
        'revision_sha256':sha256_hex(env.model.revision.encode()) if env else 'd'*64,
        'inventory_sha256':env.model.inventory_sha256 if env else 'e'*64,'manifest_bytes_sha256':'f'*64,
        'data_origin':'EXAMPLE','nonsecret_model_weights':True,'hash_model_bytes':hash_model_bytes,'max_hash_bytes':max_hash_bytes,
        'files':[{'normalized_path_sha256':sha256_hex(str(path.absolute()).encode()),'reference_sha256':sha256_hex(MODEL_BYTES),'bytes':len(MODEL_BYTES)}]}


def make_world(tmp_path,patch,*,session='b'*32,path=None,scope_path=None,mode='normal'):
    path=path or tmp_path/'public-example.safetensors'
    if not path.exists():path.write_bytes(MODEL_BYTES)
    if scope_path is None:
        value=scope_value(path);scope_path=tmp_path/'scope.json';scope_path.write_bytes(deploy_scope(value,canonical_json(value),session))
    private=types.ModuleType('_file_read_test_iteration');private.__file__=iteration.__file__
    raw=Path(iteration.__file__).read_bytes();assert sha256_hex(raw)==sensor.ITERATION16_SHA256
    exec(compile(raw,iteration.__file__,'exec',dont_inherit=True,optimize=0),private.__dict__)
    with patch.context() as local:
        local.setattr(iteration,'configure',lambda base:sensor.configure(base,private,scope_path=str(scope_path)))
        world=iteration_world('late-callback' if mode=='late-callback' else 'normal',session=session)
    weights=world.modules[iteration.WEIGHTS];world.decoded=[]
    def decode(raw):
        world.decoded.append(raw)
        if mode=='decoder-error':raise OSError(PRIVATE)
        if mode=='replace':
            if os.name!='nt':path.unlink()
            path.write_bytes(b'EXAMPLE replacement bytes')
        return dict(world.tensor_values)
    weights.load=decode
    def iterator():
        return weights.safetensors_weights_iterator([str(path)],False,None if mode=='normal-C' else 'eager',None,
            safetensors_prefetch_num_threads=1,safetensors_prefetch_block_size=1)
    world.iterator=iterator;world.load=lambda:world.auto._load_module('',world.model,iterator())
    world.loader._get_weights_iterator=lambda source:iterator()
    world.path=path;world.scope_path=scope_path
    return world


def prepared_for(world):
    raw=world.scope_path.read_bytes();value=json.loads(raw)
    return {'profile':sensor.PROFILE,'session':value['session'],'model_read_scope':scope_summary(value),
        'assets':{'model-read-scope.json':{'sha256':sha256_hex(raw),'bytes':len(raw)}}}


def projection(world):
    value=packet(world);value['recordings'][0]['capture']['records'][0]['payload']['profile']=sensor.PROFILE
    return metadata.project(value,prepared_for(world))


@pytest.mark.parametrize('mode',['normal','replace','decoder-error','normal-C'])
def test_exact_eager_source_read_return_close_and_decoder_semantics(tmp_path,monkeypatch,mode):
    original_open=builtins.open;world=make_world(tmp_path,monkeypatch,mode=mode);iterator=world.load()
    assert not world.decoded and not [r for r in world.observer.recorded if r['event']=='FILE_READ']
    if mode=='decoder-error':
        with pytest.raises(OSError,match=PRIVATE):next(iterator)
    else:assert list(iterator)==['weight','other']
    assert builtins.open is original_open
    result=projection(world)
    if mode=='normal-C':assert result['samples']==[] and not world.decoded;return
    assert world.decoded==[MODEL_BYTES]
    sample,=result['samples'];assert sample['source_fd_read_return_observed'] and sample['iterator_step_link_observed']
    assert sample['full_fd_read_return_observed']==(mode!='replace')
    assert sample['declared_reference_bytes_match_observed']==(mode!='replace') and sample['close_observed']
    assert sample['fd_before']['ctime_basis']=='change'
    assert sample['path_before']['ctime_basis']==('creation' if os.name=='nt' else 'change')
    assert sample['path_fd_metadata_match_observed']==(mode!='replace' and os.name!='nt')
    assert all(sample[k] is False for k in (*metadata.TRUTH,'reference_authority_verified','scope_complete'))
    text=json.dumps(world.observer.recorded)
    assert PRIVATE not in text and str(world.path) not in text and MODEL_BYTES.decode() not in text


@pytest.mark.parametrize('hashing,cap,status',[(False,sensor.MAX_HASH_BYTES,'disabled'),(True,1,'too-large'),(True,sensor.MAX_HASH_BYTES,'complete')])
def test_hash_requires_explicit_nonsecret_scope_and_bound(tmp_path,monkeypatch,hashing,cap,status):
    path=tmp_path/'public.safetensors';path.write_bytes(MODEL_BYTES);value=scope_value(path,hash_model_bytes=hashing,max_hash_bytes=cap)
    scope=tmp_path/'scope.json';scope.write_bytes(deploy_scope(value,canonical_json(value),'b'*32))
    world=make_world(tmp_path,monkeypatch,path=path,scope_path=scope);list(world.load());sample,=projection(world)['samples']
    assert sample['hash_status']==status
    assert (sample['returned_bytes_sha256'] is not None)==(status=='complete')


def test_nonallowlisted_original_read_is_unmodified_and_unhashed(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);value=json.loads(world.scope_path.read_bytes());value['files'][0]['normalized_path_sha256']='0'*64
    world.observer.read_scope=value
    assert list(world.load())==['weight','other'] and world.decoded==[MODEL_BYTES]
    assert not [row for row in world.observer.recorded if row['event']=='FILE_READ']


def test_open_failure_preserves_exception_and_attempt_metadata(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);world.path.unlink()
    with pytest.raises(FileNotFoundError):list(world.load())
    result=projection(world);assert result['samples']==[]
    failure,=result['open_failures'];assert failure['phase']=='OPEN_RAISE' and failure['exception_kind']=='exception'
    assert failure['source_fd_read_return_observed'] is False and failure['returned_bytes_sha256'] is None


@pytest.mark.parametrize('changed',['duplicate','scope','source','clock','authority','generation'])
def test_fd_parser_rejects_substitution_and_claim_promotion(tmp_path,monkeypatch,changed):
    world=make_world(tmp_path,monkeypatch);list(world.load());rows=packet(world)['recordings'][0]['capture']['records']
    reads=[row for row in rows if row['event']=='FILE_READ'];weights=[row for row in rows if row['event']=='WEIGHT_ITERATION']
    if changed=='duplicate':reads.insert(1,deepcopy(reads[0]))
    elif changed=='scope':reads[1]['payload']['scope_sha256']='0'*64
    elif changed=='source':reads[1]['payload']['source_sha256']='0'*64
    elif changed=='clock':reads[1]['payload']['operation_finished_ns']=reads[1]['payload']['operation_started_ns']-1
    elif changed=='authority':reads[1]['payload']['approved_model_file_verified']=True
    else:reads[1]['payload']['call_sha256']='0'*64
    with pytest.raises(ValueError):metadata.journal(reads,prepared_for(world),weights)


def test_missing_close_and_missing_step_do_not_erase_read(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);list(world.load());rows=packet(world)['recordings'][0]['capture']['records']
    reads=[row for row in rows if row['event']=='FILE_READ' and row['payload']['phase']!='CLOSE_RETURN']
    result=metadata.journal(reads,prepared_for(world),[]);sample,=result['facts']
    assert sample['source_fd_read_return_observed'] and not sample['close_observed'] and not sample['iterator_step_link_observed']
    assert 'file_close_unavailable' in result['missing'] and 'file_read_iterator_step_link_unavailable' in result['missing']


def test_path_ctime_disagreement_remains_visible_without_discarding_fd_bytes(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);list(world.load());rows=packet(world)['recordings'][0]['capture']['records']
    reads=[row for row in rows if row['event']=='FILE_READ'];weights=[row for row in rows if row['event']=='WEIGHT_ITERATION']
    for row in reads:
        for key in ('path_before','path_after'):
            if row['payload'][key]:row['payload'][key]['ctime_ns']-=1000
    sample,=metadata.journal(reads,prepared_for(world),weights)['facts']
    assert sample['fd_metadata_stable_observed'] and sample['declared_reference_bytes_match_observed']
    assert sample['path_fd_metadata_match_observed'] is False


@pytest.mark.parametrize('phase',['read','close'])
def test_real_file_errors_propagate_without_raw_error_text(tmp_path,monkeypatch,phase):
    world=make_world(tmp_path,monkeypatch);list(world.load())
    binding=NS(**{});row=next(row['payload'] for row in world.observer.recorded if row['event']=='FILE_READ')
    binding={'pid':os.getpid(),'root':None,'call':row['call_sha256'],'loader_call':None,'loader_model':None,
        'operations':1,'method_code':row['source_method_code_sha256']}
    stream=builtins.open(world.path,'rb');handle={'nonce':'9'*64,'path':str(world.path),'path_sha256':row['normalized_path_sha256'],'cwd_sha256':row['cwd_sha256']}
    proxy=sensor.ReadProxy(world.observer,binding,handle,stream)
    if phase=='read':
        stream.close()
        with pytest.raises(ValueError):proxy.read()
    else:
        os.close(stream.fileno())
        with pytest.raises(OSError):proxy.__exit__(None,None,None)
    event=world.observer.recorded[-1]['payload'];assert event['phase']==('READ_RAISE' if phase=='read' else 'CLOSE_RAISE')
    assert event['exception_kind']=='exception' and event['returned_bytes_sha256'] is None


def test_observer_code_and_instrumented_source_gates_remain_closed(tmp_path,monkeypatch):
    private=types.ModuleType('base');exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec'),private.__dict__)
    monkeypatch.setitem(sensor.RUNTIME_CODE_CONTRACTS,'cpython-3.12.14','0'*64)
    monkeypatch.setitem(sensor.RUNTIME_CODE_CONTRACTS,'cpython-3.12.3','0'*64)
    monkeypatch.setitem(sensor.RUNTIME_CODE_CONTRACTS,'cpython-3.12.15','0'*64)
    with pytest.raises(ValueError,match='FILE_READ_OBSERVER_CODE'):sensor.configure(private,iteration)


def test_unreviewed_runtime_platform_pair_fails_before_scope_or_instrumentation(monkeypatch):
    private=types.ModuleType('base');exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec'),private.__dict__)
    monkeypatch.setattr(sensor,'os',NS(name='unreviewed'))
    with pytest.raises(ValueError,match='FILE_READ_OBSERVER_CODE'):sensor.configure(private,iteration)
