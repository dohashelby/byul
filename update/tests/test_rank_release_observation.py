"""Execute pinned .30 method bodies with EXAMPLE queues; never a GPU qualification."""
import copy
import hashlib
import sys
import time
import types
from pathlib import Path

import pytest

from guard.adapters import vllm_release_observation_sensor as variant
from guard.adapters import vllm_rank_failure_sensor as rank
from guard.adapters import vllm_rank_failure_metadata as metadata
from guard.collectors import lifecycle
from guard.executors.lifecycle_capture import parse_recording
from tests.test_lifecycle_capture import external, case, revised  # noqa: F401
from tests.test_lifecycle_sensor import writer_factory  # noqa: F401
from tests.test_a1_rank_failure_sensor import reference_module, make_world, invoke

REPO=Path(__file__).resolve().parents[2]
FIXTURES=Path(__file__).parent/'fixtures/vllm-0.30.0'
PATHS={name:FIXTURES/(name.replace('.','/')+'.py.txt') for name in variant.RANK_ROWS}


def composed(external,monkeypatch):
    prepared=lifecycle.prepare_assets(external/'assets',REPO,profile=variant.RANK_PROFILE)
    module=types.ModuleType('_rank_release_test')
    module.__file__=str(external/'assets/guard_lifecycle_sensor.py')
    exec(compile(Path(module.__file__).read_bytes(),module.__file__,'exec',dont_inherit=True,optimize=0),vars(module))
    def read(name,digest):
        raw=(external/'assets'/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==digest
        return raw
    monkeypatch.setattr(module,'_read',read)
    monkeypatch.setattr(module,'_request_key',lambda binding:(prepared['session'],b'k'*32))
    return prepared,module


def original(name):
    return reference_module(name,source_paths=PATHS,source_rows=variant.RANK_ROWS)


@pytest.mark.parametrize('kind',['normal','exception','delay','communication-stall'])
def test_release_exact_rank_methods_keep_results_private_and_do_not_certify_faults(external,monkeypatch,kind):
    prepared,module=composed(external,monkeypatch)
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]))
    if runtime not in variant.RANK_RUNTIME_CODE_CONTRACTS:
        with pytest.raises(ValueError):module._private_base()
        return
    base=module._private_base()
    assert base.PROFILE==variant.RANK_PROFILE and base.SOURCE_SHA[rank.MP]==variant.RANK_ROWS[rank.MP]
    world=make_world(prepared['session'],private_base=base,module_factory=original)
    ancestor=next(cls for cls in base.Observer.__mro__ if cls.__name__=='Observer')
    monkeypatch.setattr(ancestor,'emit',lambda self,event,payload:self.recorded.append({
        'event':event,'payload':copy.deepcopy(payload),'monotonic_ns':time.monotonic_ns()}))
    monkeypatch.setattr(base.Observer,'selection_context',lambda self:{'request':'a'*64})
    world.observer.emit=types.MethodType(base.Observer.emit,world.observer)
    if kind=='normal':assert invoke(world,'owned-release').req_ids==['native-owned-release']
    else:
        with pytest.raises(RuntimeError if kind=='exception' else TimeoutError):invoke(world,'owned-release',kind)
    rows=[r['payload'] for r in world.observer.recorded if r['event']=='RANK_FAILURE']
    assert rows and {r['rank'] for r in rows if r['role']=='worker'}=={0,1}
    raw=hashlib.sha256(b'native-owned-release').hexdigest()
    for row in rows:
        metadata.validate_sample(row,source_contracts=variant.RANK_ROWS,
            runtime_contracts=variant.RANK_RUNTIME_CODE_CONTRACTS)
        assert row['source_sha256']==variant.RANK_ROWS[row['source_module']]
        assert raw not in row['native_request_sha256']
        assert row['fault_activation_verified'] is row['cross_process_group_verified'] is row['upper_client_receipt_verified'] is False
    with pytest.raises(ValueError,match='RANK_FAILURE_SAMPLE_INVALID'):
        metadata.validate_sample(rows[0])  # Old /13 must reject .30 source claims.


@pytest.mark.parametrize('damage',['class-cell','callable','namespace','source','runtime-contract'])
def test_release_rank_rejects_drift_before_instrumentation(external,monkeypatch,damage):
    _,module=composed(external,monkeypatch)
    if damage=='runtime-contract':
        monkeypatch.setattr(module,'RANK_RUNTIME_CODE_CONTRACTS',{
            'cpython-'+'.'.join(map(str,sys.version_info[:3])):'0'*64})
        with pytest.raises(ValueError):module._private_base()
        return
    base=module._private_base()
    observer=base.Observer.__new__(base.Observer)
    target=original(rank.MP)
    originals={point:vars(vars(target)[cls])[method] for cls,methods in rank.TARGETS[rank.MP].items()
        for method in methods for point in [cls+'.'+method]}
    if damage=='class-cell':
        fn=target.FutureWrapper.__init__
        target.FutureWrapper.__init__=types.FunctionType(fn.__code__,fn.__globals__,fn.__name__,fn.__defaults__,
            (types.CellType(type('WrongOwner',(),{})),))
    elif damage=='callable':target.WorkerProc.enqueue_output=staticmethod(lambda:None)
    elif damage=='namespace':
        fn=target.WorkerProc.enqueue_output
        target.WorkerProc.enqueue_output=types.FunctionType(fn.__code__,{},fn.__name__,fn.__defaults__)
    else:
        path=external/'changed-mp.py';path.write_bytes(PATHS[rank.MP].read_bytes()+b'\n')
        target.__file__=str(path)
    with pytest.raises(ValueError):observer.instrument(target)
    assert target.MultiprocExecutor.execute_model is originals['MultiprocExecutor.execute_model']


def test_original_rank_dependency_remains_byte_exact():
    assert hashlib.sha256(Path(rank.__file__).read_bytes()).hexdigest()==variant.RANK_SHA256
    assert 'cpython-3.12.8' not in rank.RUNTIME_CODE_CONTRACTS
    assert rank.SOURCES[rank.MP]!=variant.RANK_ROWS[rank.MP]


@pytest.mark.parametrize('damage',[None,'source','runtime','verified-fault'])
def test_release_rank_records_cross_closed_parser_and_refuse_unreviewed_claims(external,case,monkeypatch,damage):
    directory=external/'rank';directory.mkdir()
    prepared,module=composed(directory,monkeypatch)
    base=module._private_base()
    world=make_world(prepared['session'],private_base=base,module_factory=original)
    assert invoke(world,'EXAMPLE-parse').req_ids==['native-EXAMPLE-parse']
    additions=copy.deepcopy(world.observer.recorded)
    def change(rows):
        rows[0]['payload'].update(schema=base.SCHEMA,profile=prepared['profile'],commit=base.COMMIT,
            session=prepared['session'],settings_sha256=prepared['assets']['settings.json']['sha256'],
            sensor_sha256=prepared['assets']['guard_lifecycle_sensor.py']['sha256'])
        rows[1]['payload']['source_sha256']=base.SOURCE_SHA[rows[1]['payload']['module']]
        rows[-1:-1]=additions
        rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],additions[-1]['monotonic_ns'])
        selected=next(r['payload'] for r in rows if r['event']=='RANK_FAILURE')
        if damage=='source':selected['source_sha256']=rank.SOURCES[selected['source_module']]
        elif damage=='runtime':selected['observer_runtime']='cpython-unreviewed'
        elif damage=='verified-fault':selected['fault_activation_verified']=True
    raw=revised(case.raw,change)
    if damage:
        with pytest.raises(ValueError):parse_recording(raw,prepared)
    else:
        records,summary=parse_recording(raw,prepared)
        samples=[r['payload'] for r in records if r['event']=='RANK_FAILURE']
        assert len(samples)>0 and all(not r['fault_activation_verified'] for r in samples)
        assert summary['recording_closed'] is True
