"""GPU async producer metadata must be consumed only after a qualified return.

CPU/EXAMPLE contracts, not GPU measurements. Native source/entry qualification
is exercised by the existing observer contracts and the actual GPU replay.
"""
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.executors import engine_callback_projection as projection

SECRET = b'e' * 32
TAG = 'byul-cb-' + 'a' * 32
OTHER = 'byul-cb-' + 'b' * 32


def produce(returned=True, ids=None):
    observer = sensor.DistributedObserver('a' * 32, context_provider=lambda: {'origin': 'EXAMPLE'})
    observer.secret = SECRET
    owner = SimpleNamespace()
    result = SimpleNamespace(req_ids=[TAG] if ids is None else ids)
    return observer._snapshot(sensor.GPU_OUTPUT_POINT, (owner,), {}, result, returned)


def project(fields, phase='RETURN', after=True):
    events = [{'point': 'WorkerProc._execute_worker_rpc', 'phase': 'CALL', 'entry_qualified_before': True,
        'process_sha256': 'example-worker', 'fields': {'rpc_method': 'execute_model',
        'native_request_sha256': [projection.tag_digest(SECRET, TAG)]}},
        {'point': sensor.GPU_OUTPUT_POINT, 'phase': phase, 'entry_qualified_before': True,
        'entry_qualified_after': after, 'process_sha256': 'example-worker', 'fields': fields}]
    return projection.project_distributed(events, SECRET, {'case': TAG})


def test_async_producer_return_reaches_existing_partial_scope_projection():
    result = project(produce())
    assert result['violation'] is False and result['batches_checked'] == 1
    assert result['full_scope'] is False
    assert 'intermediate_tensor_buffer_lineage_unobserved' in result['missing']


def test_producer_before_return_does_not_claim_output_or_consume_it():
    fields = produce(returned=False)
    assert fields['output_request_sha256'] is None
    assert project(fields, phase='CALL')['violation'] is None


@pytest.mark.parametrize('phase,after', [('CALL', True), ('RAISE', True), ('RETURN', False)])
def test_unfinished_or_replaced_native_entry_cannot_qualify_output(phase, after):
    assert project(produce(), phase=phase, after=after)['violation'] is None


def test_confirmed_wrong_request_is_preserved_despite_missing_tensor_lineage():
    result = project(produce(ids=[TAG, OTHER]))
    assert result['violation'] is True and result['full_scope'] is False
    assert result['violations'][0]['unscheduled_outputs'] == 1


@pytest.mark.parametrize('ids', [None, 'not-a-list', [object()], ['x' * 513], ['x'] * 65])
def test_missing_or_unsupported_producer_output_is_not_a_clean_batch(ids):
    fields = produce(ids=[]) if ids is None else produce(ids=ids)
    assert project(fields)['violation'] is None
