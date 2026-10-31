"""Explicit ABI admission and cross-host decoding, without a serving/GPU claim."""
from copy import deepcopy
import importlib
import inspect
import sys
import types

import pytest
from guard.adapters import vllm_lifecycle_sensor as base8
from guard.adapters import vllm_online_sensor as online
from guard.adapters import vllm_weight_iteration_sensor as iteration
from guard.adapters import vllm_weight_iteration_metadata as metadata
from tests.test_a1_weight_iteration_sensor import make_world,packet

SENSORS=('attempt','file_read','initialization','profiler','rank_failure','ready_endpoint',
         'request_output','safe_open_mapping','stream_buffer','weight_iteration',
         'worker_generation','worker_initialization','worker_startup')


@pytest.mark.parametrize('name',SENSORS)
def test_native31215_nonlinux_producer_rejected_before_mutation(monkeypatch,name):
    module=importlib.import_module('guard.adapters.vllm_'+name+'_sensor')
    base=types.ModuleType('_abi_rejection_only')
    base.code_digest=base8.code_digest
    base.PROFILE='vllm-native-lifecycle-metadata/21'
    base.SCHEMA='guard-native-lifecycle-events/21'
    contract_name={'worker_startup':'worker_startup','worker_generation':'worker_generation','ready_endpoint':'ready_endpoint'}.get(name)
    contract=importlib.import_module('guard.adapters.vllm_'+contract_name+'_contract') if contract_name else None
    arguments=[base]+[contract if parameter=='contract' else None for parameter in list(inspect.signature(module.configure).parameters)[1:] if parameter!='scope_path']
    before=dict(vars(base))
    with monkeypatch.context() as patch:
        patch.setattr(sys,'version_info',(3,12,15));patch.setattr(sys,'platform','darwin')
        with pytest.raises(ValueError):module.configure(*arguments)
    assert vars(base)==before


def test_online_explicit_incoming_contract_is_host_independent(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(sys,'platform','win32')
        patch.setattr(online,'runtime_name',lambda:'cpython-3.12.15')
        with pytest.raises(ValueError,match='ONLINE_CODE_RUNTIME_UNSUPPORTED'):online.runtime_contract()
        assert online.runtime_contract('cpython-3.12.15')==online.RUNTIME_CONTRACTS['cpython-3.12.14']


@pytest.mark.parametrize('machine',['aarch64','AMD64','unknown'])
def test_native31215_unreviewed_machine_stays_closed(monkeypatch,machine):
    import os
    with monkeypatch.context() as patch:
        patch.setattr(sys,'version_info',(3,12,15));patch.setattr(sys,'platform','linux')
        patch.setattr(os,'name','posix');patch.setattr(os,'uname',lambda:types.SimpleNamespace(machine=machine),raising=False)
        with pytest.raises(ValueError,match='WEIGHT_ITERATION_OBSERVER_CODE_UNAVAILABLE'):iteration.configure(base8)


def test_reviewed_compiler_contracts_and_unknown_runtime_remain_distinct():
    assert set(iteration.RUNTIME_CODE_CONTRACTS)=={'cpython-3.12.3','cpython-3.12.14','cpython-3.12.15'}
    assert iteration.RUNTIME_CODE_CONTRACTS['cpython-3.12.15']==iteration.RUNTIME_CODE_CONTRACTS['cpython-3.12.14']
    assert iteration.RUNTIME_POINT_CODES['cpython-3.12.15']==iteration.RUNTIME_POINT_CODES['cpython-3.12.14']
    with pytest.raises(ValueError,match='ONLINE_CODE_RUNTIME_UNSUPPORTED'):online.runtime_contract('cpython-3.12.16')


def test_actual_original_loader_iteration_and_parser_preserve_truth_limits():
    world=make_world();assert list(world.load())==['weight','other']
    result=metadata.project(packet(world))
    assert result['model_candidates']
    assert all(candidate['scope_complete'] is False for candidate in result['model_candidates'])
    assert all(candidate['tensor_storage_applied_verified'] is False for candidate in result['model_candidates'])
    assert all(candidate['producer_authenticated'] is False for candidate in result['model_candidates'])


def test_resealed_unknown_runtime_does_not_become_an_approval():
    world=make_world();list(world.load());value=deepcopy(packet(world))
    for row in value['recordings']:
        for record in row['capture']['records']:
            if record['event']=='WEIGHT_ITERATION':record['payload']['observer_runtime']='cpython-3.12.16'
    with pytest.raises(ValueError):metadata.project(value)
