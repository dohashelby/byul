from copy import deepcopy
import asyncio
from pathlib import Path
import sys
import types
import pytest
from guard.adapters import vllm_profiler_sensor as sensor,vllm_profiler_metadata as metadata
from guard.models import load_pack
from tests.builders import baseline_pack
from tests.test_a1_profiler_sensor import make_world,invoke
from tools.build_kubernetes_profiler_control_pack import derive


def test_DRAFT_keeps_other76_bytes_and_original_question_judge_contract(tmp_path):
    seed=load_pack(Path(__file__).resolve().parents[1]/'packs/PACK-BASELINE');output=tmp_path/'derived'
    receipt=derive(seed.path,output);pack=load_pack(output)
    assert receipt['unchanged_definition_bytes']==76
    before=seed.checks['LIF-14'].model_dump(mode='json');after=pack.checks['LIF-14'].model_dump(mode='json')
    assert {k:v for k,v in before.items() if k!='required_observations'}=={k:v for k,v in after.items() if k!='required_observations'}
    for file in seed.path.rglob('*'):
        if file.is_file() and file.name!='pack.yaml' and file.relative_to(seed.path).as_posix()!='checks/LIF-14.yaml':
            assert file.read_bytes()==(output/file.relative_to(seed.path)).read_bytes()
    assert pack.manifest.review.status=='DRAFT'


def test_observer_function_replacement_rejected_without_approving_new_hash(monkeypatch):
    from guard.adapters import vllm_lifecycle_sensor as base
    monkeypatch.setattr(sensor.ProfilerObservation,'profiler_backend',lambda *a:None)
    with pytest.raises(ValueError,match='PROFILER_OBSERVER_CODE_UNAVAILABLE'):sensor.configure(base)


def test_actual_example_producer_unsupported_backend_stays_unqualified_and_legacy_reads(monkeypatch):
    world=make_world(patch=monkeypatch);loop=asyncio.new_event_loop()
    try:invoke(world,loop)
    finally:loop.close()
    samples=[r['payload'] for r in world.observer.recorded if r['event']=='PROFILER']
    assert samples and all(metadata.validate_sample(row) is None for row in samples)
    assert all(row['backend_probe_qualified'] is False for row in samples)
    for row in samples:
        legacy={key:deepcopy(value) for key,value in row.items() if key not in metadata.BACKEND_FIELDS}
        legacy['schema']=sensor.LEGACY_EVENT_SCHEMA
        legacy['observer_code_sha256']=sensor.LEGACY_RUNTIME_CODE_CONTRACTS[legacy['observer_runtime']]
        assert metadata.validate_sample(legacy) is None
    selected=next(deepcopy(row) for row in samples if row['point']=='TorchProfilerWrapper._stop' and row['phase']=='RETURN')
    selected['backend_probe_qualified']=True;selected['backend_generation_sha256']='a'*64
    with pytest.raises(ValueError,match='PROFILER_SAMPLE_INVALID'):metadata.validate_sample(selected)


def test_new_nested_payload_rejects_private_extra_field(monkeypatch):
    world=make_world(patch=monkeypatch);loop=asyncio.new_event_loop()
    try:invoke(world,loop)
    finally:loop.close()
    row=next(deepcopy(r['payload']) for r in world.observer.recorded
        if r['event']=='PROFILER' and r['payload']['point']=='TorchProfilerWrapper._stop' and r['payload']['phase']=='RETURN')
    row['trace_after']['private_path']='/PRIVATE'
    with pytest.raises(ValueError,match='PROFILER_SAMPLE_INVALID'):metadata.validate_sample(row)


def test_fresh_source_compilation_matches_runtime_and_rejects_changed_function():
    from guard.adapters import vllm_lifecycle_sensor as original_base
    modules=[]
    for name,path in (('base',Path(original_base.__file__)),('profiler',Path(sensor.__file__))):
        module=types.ModuleType('_fresh_'+name);module.__file__=str(path)
        exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True,optimize=0),module.__dict__)
        modules.append(module)
    base,fresh=modules;runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]))
    assert fresh.code_contract(base)==fresh.RUNTIME_CODE_CONTRACTS[runtime]
    fresh.configure(base)
    fresh.ProfilerObservation.profiler_backend=lambda *a:None
    with pytest.raises(ValueError,match='PROFILER_OBSERVER_CODE_UNAVAILABLE'):fresh.configure(base)
