"""Actual CPU engine records -> exact request/worker/parameter/auxiliary links."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors import consumption_links as consumer, engine_callbacks
from guard.adapters.vllm_auxiliary_link_callback import AuxiliaryLinkObserver
from tools.consumption_links_capture import validate_pod

ROOT = Path(__file__).parent / 'fixtures'


def captured():
    return json.loads((ROOT / 'consumption_links_k3d_20261108.json').read_text())


def native(value, point, phase='RETURN'):
    return [r for r in value['rows'] if r.get('schema') == consumer.callback.SCHEMA and r.get('point') == point and r.get('phase') == phase]


def test_actual_engine_connections_include_real_worker_model_logits_and_grammar():
    result = consumer.project(captured())
    assert result['scenario_complete'] and result['selected_request_chains_complete'] and result['selected_auxiliary_links_complete']
    assert len(result['requests']) == 7 and sum(len(r['steps']) for r in result['requests']) == 16
    assert all(r['internal_sha256'] != r['external_sha256'] and not r['missing'] for r in result['requests'])
    assert result['capture_complete'] is False
    assert set(result['diagnostic_codes']) == {'CACHE_CALLBACK_WEAK_OBJECT_IDENTITY_UNAVAILABLE', 'DISTRIBUTED_NON_EXECUTE_RPC_UNSUPPORTED'}
    assert result['GPU_verified'] is False and result['producer_authenticated'] is False
    grammar = native(captured(), 'apply_grammar_bitmask')
    assert len(grammar) == 2 and all(r['fields']['execution_parent_verified'] for r in grammar)


def test_actual_failed_reload_changes_a_selected_parameter_and_source_restore_returns_it():
    result = consumer.project(captured())
    failed, restored = result['reloads']
    assert failed['phase'] == 'RAISE' and restored['phase'] == 'RETURN'
    assert failed['before']['bytes_sha256'] != failed['after']['bytes_sha256']
    assert restored['before'] == failed['after'] and restored['after'] == failed['before']
    transition = next(r for r in result['requests'] if r['case'] == 'transition')
    steps = sorted(transition['steps'], key=lambda s: s['num_computed_tokens'])
    assert [s['cpu_parameter']['bytes_sha256'] for s in steps] == [failed['before']['bytes_sha256'], failed['after']['bytes_sha256'], failed['before']['bytes_sha256'], failed['before']['bytes_sha256']]
    assert len({s['state_sha256'] for s in steps}) == 1 and len({s['worker_pid'] for s in steps}) == 1
    assert [v['reason'] for v in result['violation_candidates']] == ['cpu_parameter_changed_during_failed_reload']


def test_config_content_and_real_tokenizer_copy_lineage_link_file_reads_to_requests():
    result = consumer.project(captured())
    assert all(r['config_reads_linked'] and len(r['tokenizer_reads_linked']) == 2 for r in result['requests'])
    assert len({r['file_sha256'] for request in result['requests'] for r in request['config_reads_linked']}) == 1


def test_auxiliary_policy_missing_and_empty_list_keep_their_distinct_meanings():
    record = captured()
    baseline = consumer.project(record)
    assert baseline['auxiliary_policy_supplied'] is False
    result = consumer.project(record, allowed_auxiliary_sha256=[])
    assert result['auxiliary_policy_supplied'] is True
    excluded = [v for v in result['violation_candidates'] if v['reason'] == 'unapproved_auxiliary_read_linked_to_actual_request']
    assert len(excluded) == 21
    record['source_ended'] = False
    assert consumer.project(record, allowed_auxiliary_sha256=[])['violation_candidates'] == result['violation_candidates']


@pytest.mark.parametrize('point,damage', [
    ('InputProcessor.assign_request_id', 'external'), ('InputProcessor.assign_request_id', 'input'),
    ('Scheduler.schedule', 'wire'), ('Scheduler.schedule', 'attempt-object'),
    ('GPUModelRunner._update_states', 'worker-state'), ('GPUModelRunner._model_forward', 'parent'),
    ('Sampler.forward', 'model-logits'), ('Sampler.forward', 'metadata-pointer'), ('Sampler.forward', 'gpu-metadata'),
    ('apply_grammar_bitmask', 'parent'),
])
def test_exact_execution_links_do_not_fall_back_to_time_alias_or_synthetic_state(point, damage):
    value = captured()
    phase = 'CALL' if damage in ('model-logits', 'metadata-pointer', 'gpu-metadata') else 'RETURN'
    row = native(value, point, phase)[0]; f = row['fields']
    if damage == 'external': f['external_sha256'] = 'f' * 64
    elif damage == 'input': f['core_input_sha256'] = 'f' * 64
    elif damage == 'wire': f['schedule_sha256'] = 'f' * 64
    elif damage == 'attempt-object': f['attempts'][0]['request_object_sha256'] = 'f' * 64
    elif damage == 'worker-state': f['requests'][0]['state_sha256'] = 'f' * 64
    elif damage == 'parent': f['execution_parent_verified'] = False
    elif damage == 'model-logits': f['logits_same_as_model_output'] = False
    elif damage == 'metadata-pointer': f['metadata_same_as_batch'] = False
    else: f['metadata']['cpu_only'] = False
    result = consumer.project(value)
    assert not result['selected_request_chains_complete']
    assert any(r['missing'] for r in result['requests'])


def test_same_count_different_allowed_token_ids_are_a_candidate_after_later_capture_failure():
    value = captured()
    row = next(r for r in native(value, 'Sampler.forward', 'CALL') if r['fields']['metadata']['allowed_tokens'] == [1])
    row['fields']['metadata']['allowed_token_ids_sha256'] = ['f' * 64]
    value['source_ended'] = False
    assert any(v['reason'] == 'options_changed_before_actual_sampler' for v in consumer.project(value)['violation_candidates'])


@pytest.mark.parametrize('damage', ['metadata-missing', 'integer-bound', 'auxiliary-source-shape', 'loader-call-missing'])
def test_malformed_partial_capture_is_rejected_without_an_exception_or_lost_candidate(damage):
    value = captured()
    if damage == 'metadata-missing':
        native(value, 'Sampler.forward', 'CALL')[0]['fields'].pop('metadata')
    elif damage == 'integer-bound':
        native(value, 'Sampler.forward', 'CALL')[0]['fields']['pid'] = 10**1000
    else:
        for row in value['rows']:
            if row.get('schema') != consumer.auxiliary.SCHEMA:
                continue
            if damage == 'auxiliary-source-shape': row['source_sha256'] = []
            elif row.get('phase') == 'LOADER_RETURN': row['loader_call'] = None
    result = consumer.project(value)
    assert not result['capture_complete']
    assert any(v['reason'] == 'cpu_parameter_changed_during_failed_reload' for v in result['violation_candidates'])
    assert not result['selected_request_chains_complete'] or not result['selected_auxiliary_links_complete']


def test_missing_tokenizer_cache_identity_cannot_be_replaced_by_same_configuration_or_process():
    value = captured()
    for row in value['rows']:
        if row.get('schema') == consumer.auxiliary.SCHEMA and row.get('role') == 'tokenizer_cache':
            row['input_tokenizer_sha256'] = 'f' * 64
    result = consumer.project(value)
    assert result['selected_request_chains_complete'] and not result['selected_auxiliary_links_complete']
    assert all(not r['tokenizer_reads_linked'] for r in result['requests'])


@pytest.mark.parametrize('damage', ['duplicate', 'source', 'session', 'pid', 'injected', 'requests-shape', 'extra-field'])
def test_malformed_or_unqualified_rows_do_not_erase_the_original_failed_reload_candidate(damage):
    value = captured(); row = native(value, 'GPUModelRunner._model_forward')[0]
    if damage == 'duplicate': value['rows'].append(deepcopy(row))
    elif damage == 'source': row['source_file_sha256'] = 'f' * 64
    elif damage == 'session': row['session_sha256'] = 'f' * 64
    elif damage == 'pid': row['fields']['pid'] += 1
    elif damage == 'injected': row['runtime_context_injected'] = True
    elif damage == 'requests-shape': row['fields']['requests'] = 1
    else: row['fields']['payload'] = 'not accepted'
    result = consumer.project(value)
    assert not result['selected_request_chains_complete']
    assert any(v['reason'] == 'cpu_parameter_changed_during_failed_reload' for v in result['violation_candidates'])


def test_optional_prepared_profile_keeps_default_off_and_rejects_overlapping_observers(tmp_path):
    assert engine_callbacks.prepare_assets(tmp_path / 'default')['execution_links'] is False
    manifest = engine_callbacks.prepare_assets(tmp_path / 'on', execution_links=True, supply_roots=['/model'])
    assert manifest['execution_links'] is True
    for kwargs in ({'request_attempts': True}, {'transition_dispatches': True}, {'runner_consumption': True}):
        with pytest.raises(ValueError, match='EXECUTION_LINKS_REQUIRE_EXCLUSIVE_POINTS'):
            engine_callbacks.prepare_assets(tmp_path / 'invalid', execution_links=True, **kwargs)


def test_observation_sink_failure_preserves_original_loader_return_and_exception(monkeypatch):
    def unavailable(row):
        raise OSError('example sink unavailable')
    observer = AuxiliaryLinkObserver(['/tmp'], session='a' * 32,
                                     link=SimpleNamespace(process_sha256='b' * 64, token=lambda v: None), sink=unavailable)
    monkeypatch.setattr(observer, '_qualify', lambda module, code: True)
    module = SimpleNamespace(__name__='vllm.transformers_utils.config')
    sentinel = object()
    def successful():
        return sentinel
    def failed():
        raise original
    original = RuntimeError('original loader failure')
    assert observer._wrap(module, successful, 'tokenizer')() is sentinel
    with pytest.raises(RuntimeError) as caught:
        observer._wrap(module, failed, 'tokenizer')()
    assert caught.value is original
    assert 'AUXILIARY_LINK_SINK_UNAVAILABLE' in observer.gaps


@pytest.mark.parametrize('damage', ['serving-command', 'service-account', 'mutable-image', 'writable-model', 'shadowed-model', 'gpu-image'])
def test_capture_refuses_live_serving_pods_and_requires_the_pinned_cpu_image(damage):
    pod = {'metadata': {'uid': 'example'}, 'spec': {'automountServiceAccountToken': False,
           'containers': [{'name': 'producer', 'command': ['sleep', '3600'], 'imagePullPolicy': 'Never',
                           'image': 'reg/cpu@sha256:045fb648a61cfcd32d4b9ca5d0c391013afadae0178d2e23ac292597f2f64036',
                           'volumeMounts': [{'mountPath': '/models', 'readOnly': True}]}]},
           'status': {'phase': 'Running', 'containerStatuses': [{'name': 'producer', 'state': {'running': {}}, 'containerID': 'container', 'restartCount': 0}]}}
    container = pod['spec']['containers'][0]
    if damage == 'serving-command': container['command'] = ['vllm', 'serve']
    elif damage == 'service-account': pod['spec']['automountServiceAccountToken'] = True
    elif damage == 'mutable-image': container['image'] = 'reg/cpu:latest'
    elif damage == 'gpu-image': container['image'] = 'reg/gpu@sha256:' + 'a' * 64
    elif damage == 'shadowed-model': container['volumeMounts'].append({'mountPath': '/models/opt', 'readOnly': False})
    else: container['volumeMounts'][0]['readOnly'] = False
    with pytest.raises(ValueError, match='CONSUMPTION_LINK_REQUIRES_ISOLATED_POD'):
        validate_pod(pod, container='producer', model_dir='/models/opt')
