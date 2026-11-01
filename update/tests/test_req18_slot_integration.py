"""EXAMPLE diagnostic contracts. Actual CPU capture is run once after root freeze."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from guard.executors import registry
from guard.models import DataOrigin, Environment, Policy
from guard.store import Store
from tests.test_kubernetes_request_binding import env as live_env, target
from tools import b_req18_slot as producer

PRIVATE = 'PRIVATE_SLOT_RAW_TOKEN_OR_EXCEPTION'


@pytest.fixture(autouse=True)
def example_pins(monkeypatch):
    monkeypatch.setattr(producer, 'COLLECTOR_SHA256', 'c' * 64)
    monkeypatch.setattr(producer, 'CALLBACK_SHA256', 'd' * 64)


@pytest.fixture
def inputs(tmp_path):
    pack = producer.proposal_pack(tmp_path / 'pack')
    environment = Environment(id='ENV-REQ18-CPU-EXAMPLE', data_origin=DataOrigin.EXAMPLE,
        engine={'name': 'vllm', 'version': 'source-' + producer.SOURCE_COMMIT}).seal()
    policy = Policy(id='POL-REQ18-EXAMPLE', data_origin=DataOrigin.EXAMPLE, status='DRAFT',
        pack=pack.ref(), scope={'include': ['REQ-18']}).seal()
    return environment, policy, pack


def cpu_record():
    validation = {'pre': 'verified', 'post': {'status': 'verified'}, 'boundary': 'source_component'}
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for key in producer._CASES:
        standalone = key == 'standalone-b'
        rows.append({'case_id': key, 'collected': True, 'current_b_unrestricted': True, 'slot_reused': not standalone,
            'constraint_sha256': producer.native_digest(None), 'logits_sha256': '1' * 64,
            'host_mask_status': 'absent' if standalone else 'collected',
            'host_mask_sha256': None if standalone else '2' * 64, 'host_masked_count': None if standalone else 0,
            'metadata_mask_status': 'absent' if standalone else 'collected', 'metadata_mask_present': not standalone,
            'metadata_mask_sha256': None if standalone else '2' * 64, 'metadata_masked_count': None if standalone else 0,
            'sampling_metadata_token': '3' * 64, 'sampling_mask_token': None if standalone else '4' * 64,
            'sampled_count': 1, 'sampled_ids_sha256': '5' * 64,
            'standalone_output_sha256': '5' * 64, 'standalone_mask_sha256': None,
            'mask_matches_standalone': True, 'mask_comparison_basis': 'effective_unrestricted_branches',
            'effective_unrestricted': True, 'output_matches_standalone': True, 'sampler_returned': True,
            'metadata_argument_matches_refresh': True, 'restricted_request_preserved': True,
            'source_validation': deepcopy(validation), 'private': PRIVATE})
    return {'schema': 'byul-req18-slot-cpu-capture/1', 'source_profile': 'target031',
        'source_commit': producer.SOURCE_COMMIT, 'own_producer_sha256': producer.COLLECTOR_SHA256,
        'native_callback_sha256': producer.CALLBACK_SHA256, 'source_validation': validation,
        'collection_id': 'req18-slot-cpu-' + 'a' * 32, 'capture_started_at': now, 'capture_finished_at': now,
        'results': rows, 'capture_status': 'completed', 'private': PRIVATE}


def mismatch(record):
    row = record['results'][1]
    row.update(host_mask_sha256='6' * 64, host_masked_count=31, metadata_mask_sha256='6' * 64,
        metadata_masked_count=31, sampled_ids_sha256='7' * 64, mask_matches_standalone=False,
        output_matches_standalone=False, effective_unrestricted=False)
    return record


def run(tmp_path, inputs, record):
    calls = []
    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return deepcopy(record)
    summary = producer.run_pipeline('cpu', *inputs, tmp_path / 'run', source_root=tmp_path / 'source',
        execute=True, _example_capture=capture)
    store = Store(tmp_path / 'run/store')
    return summary, calls, store, next(store.iter_kind('EVD')), next(store.iter_kind('RES'))


def test_absent_standalone_and_real_zero_row_are_semantic_controls_not_fabricated_hashes(tmp_path, inputs):
    before = dict(registry.EXECUTORS)
    summary, calls, store, evidence, result = run(tmp_path, inputs, cpu_record())
    assert len(calls) == 1 and calls[0][1] == {'source_profile': 'target031'}
    assert summary['execution_status'] == 'COMPLETED' and result.verdicts.security.value == 'UNKNOWN'
    assert summary['installed_target'] is None and not summary['gpu_execution_verified']
    assert not summary['full_live_coverage'] and evidence.data_origin == DataOrigin.EXAMPLE
    artifact = json.loads((store.root / evidence.artifacts[0].path).read_text())
    assert artifact['rows'][0]['host_mask_sha256'] is None
    assert artifact['rows'][1]['host_mask_sha256'] == '2' * 64
    assert PRIVATE not in json.dumps(artifact) and PRIVATE not in json.dumps(summary)
    assert summary['restore_verified'] and summary['exact_ref_rejudgment_unchanged'] and store.verify() == []
    assert registry.EXECUTORS == before


def test_only_mask_consumption_and_identical_logits_output_difference_produce_fail(tmp_path, inputs):
    summary, _, _, evidence, result = run(tmp_path, inputs, mismatch(cpu_record()))
    assert result.verdicts.security.value == 'FAIL'
    assert evidence.observations['req18_mask_inheritance_mismatch'].value is True
    assert summary['restore_verified']


@pytest.mark.parametrize('change', ['logits', 'sampler', 'metadata', 'same_output', 'unused_host', 'no_reuse', 'unqualified_source'])
def test_host_state_alone_or_unqualified_trial_is_not_consumption_fail(tmp_path, inputs, change):
    record = mismatch(cpu_record())
    row = record['results'][1]
    if change == 'logits': row['logits_sha256'] = '9' * 64
    elif change == 'sampler': row['sampler_returned'] = False
    elif change == 'metadata': row['metadata_argument_matches_refresh'] = False
    elif change == 'same_output': row.update(sampled_ids_sha256='5' * 64, output_matches_standalone=True)
    elif change == 'unused_host': row.update(metadata_mask_sha256=None, metadata_masked_count=None, metadata_mask_present=False)
    elif change == 'no_reuse': row['slot_reused'] = False
    else: row['source_validation']['post']['status'] = 'mismatch'
    _, _, _, _, result = run(tmp_path, inputs, record)
    assert result.verdicts.security.value == 'UNKNOWN'


def test_earlier_verified_failure_survives_late_case_and_global_post_failure(tmp_path, inputs):
    record = mismatch(cpu_record())
    record['results'].pop()
    record['capture_status'] = 'incomplete'
    record['source_validation']['post']['status'] = 'unavailable'
    summary, _, _, evidence, result = run(tmp_path, inputs, record)
    assert evidence.execution.status.value == 'ABORTED' and result.verdicts.security.value == 'FAIL'
    assert summary['restore_verified']


def test_artifact_error_preserves_independent_fail(tmp_path, inputs, monkeypatch):
    write = producer._write
    def fail(path, value):
        if Path(path).name == 'slot-diagnostic.json':
            raise OSError(PRIVATE)
        return write(path, value)
    monkeypatch.setattr(producer, '_write', fail)
    _, _, _, evidence, result = run(tmp_path, inputs, mismatch(cpu_record()))
    assert not evidence.artifacts and result.verdicts.security.value == 'FAIL'


def test_cpu_and_kubernetes_environments_cannot_be_interchanged(tmp_path, inputs):
    with pytest.raises(ValueError, match='KUBERNETES_ENV_MUST_BE_SEPARATE'):
        producer.run_pipeline('kubernetes', *inputs, tmp_path / 'bad', source_root=tmp_path / 'source')
    environment = live_env()
    with pytest.raises(ValueError, match='CPU_ENV_MUST_BE_SEPARATE'):
        producer.run_pipeline('cpu', environment, inputs[1], inputs[2], tmp_path / 'bad', source_root=tmp_path / 'source')


def test_declared_environment_rejects_private_example_capture(tmp_path, inputs):
    environment = inputs[0].model_copy(update={'data_origin': DataOrigin.DECLARED}).seal()
    with pytest.raises(ValueError, match='INJECTION_REQUIRES_EXAMPLE'):
        producer.run_pipeline('cpu', environment, inputs[1], inputs[2], tmp_path / 'bad',
            source_root=tmp_path / 'source', execute=True, _example_capture=lambda *a, **k: cpu_record())


def test_unconfigured_component_gate_closes_before_actual_cpu_capture(tmp_path, inputs, monkeypatch):
    monkeypatch.setattr(producer, 'GATE_SHA256', None)
    summary = producer.run_pipeline('cpu', *inputs, tmp_path / 'closed',
        source_root=tmp_path / 'source', execute=True)
    assert summary['capture_calls'] == 0 and summary['security'] == 'UNKNOWN' and summary['restore_verified']


def test_original_req18_remains_unregistered_and_private_diagnostic_has_no_pass_rule(inputs):
    original = producer.yaml.safe_load((Path(producer.__file__).resolve().parents[1]
        / 'packs/PACK-BASELINE/checks/REQ-18.yaml').read_text())
    assert original['executor'] is None and original['judge'] is None and original['policy_params'] == []
    assert inputs[2].checks['REQ-18'].policy_params == []
    assert 'pass_when_all' not in inputs[2].checks['REQ-18'].judge.params


def native_events():
    """EXAMPLE parser-boundary events, including a complete relevant writer epoch."""
    dimensions = {'vocab_size': 8, 'max_model_len': 16, 'max_num_reqs': 2,
                  'device_type': 'cpu', 'is_pooling_model': False}
    batch, standalone, prior, current = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64
    domain = (123, 456)
    events = []
    last_state = {}
    def event(point, phase, metadata, call=None):
        call = call or producer.native_digest([point, len(events)])
        def append(kind, value):
            events.append({'seq': len(events) + 1, 'domain': domain,
                'caller_provenance': {'point': 'GPUModelRunner.sample_tokens', 'module': 'EXAMPLE-v1-runner',
                    'source_sha256': '8' * 64, 'code_sha256': '9' * 64},
                'snapshot': {'point': point, 'phase': kind, 'binding': {'call_id_sha256': call}, 'metadata': deepcopy(value)}})
        if point.startswith('InputBatch.') and phase == 'RETURN':
            matching = [e for e in events if e['snapshot']['point'] == point and e['snapshot']['phase'] == 'CALL'
                        and e['snapshot']['binding']['call_id_sha256'] == call]
            if not matching:
                before = {**metadata, **last_state.get(metadata['batch_token'], {})}
                before.pop('assigned_slot_index', None)
                append('CALL', before)
        append(phase, metadata)
        if point.startswith('InputBatch.') and phase == 'RETURN':
            last_state[metadata['batch_token']] = deepcopy(metadata)
    def batch_state(rows):
        return {'batch_token': batch, 'dimensions': dimensions, 'rows': rows, 'slot_map_complete': True}
    first = {'slot_index': 0, 'request_sha256': standalone, 'restricted': False,
             'cpu_mask': {'status': 'absent', 'cells': None, 'masked_count': None, 'mask_sha256': None}}
    event('InputBatch.add_request', 'RETURN', {**batch_state([first]), 'assigned_slot_index': 0,
        'argument_request_sha256': standalone, 'argument_constraint': {'status': 'collected', 'restricted': False, 'count': 0, 'sha256': producer.native_digest(None)}})
    metadata = {**batch_state([first]), 'sampling_metadata_token': '1' * 64,
        'sampling_mask_token': None, 'sampling_mask': {'status': 'absent'}}
    event('InputBatch.refresh_metadata', 'RETURN', metadata)
    event('Sampler.forward', 'CALL', metadata, call='7' * 64)
    event('Sampler.forward', 'RETURN', metadata, call='7' * 64)
    mask = {'status': 'collected', 'cells': 8, 'masked_count': 7, 'mask_sha256': '2' * 64}
    event('InputBatch.add_request', 'RETURN', {**batch_state([
        {'slot_index': 0, 'request_sha256': prior, 'restricted': True, 'cpu_mask': mask}]), 'assigned_slot_index': 0,
        'argument_request_sha256': prior, 'argument_constraint': {'status': 'collected', 'restricted': True, 'count': 1, 'sha256': producer.native_digest([1])}})
    event('InputBatch.remove_request', 'RETURN', batch_state([]))
    row = {'slot_index': 0, 'request_sha256': current, 'restricted': False, 'cpu_mask': mask}
    event('InputBatch.add_request', 'RETURN', {**batch_state([row]), 'assigned_slot_index': 0,
        'argument_request_sha256': current, 'argument_constraint': {'status': 'collected', 'restricted': False, 'count': 0, 'sha256': producer.native_digest(None)}})
    descriptor = {'status': 'collected', 'device_type': 'cpu', 'dtype': 'torch.bool', 'shape': [1, 8],
        'values_collected': True, 'cpu_rows': [{'slot_index': 0, 'cpu_mask': mask}]}
    metadata = {**batch_state([row]), 'has_allowed_token_ids': True, 'sampling_metadata_token': '3' * 64,
        'sampling_mask_token': '4' * 64, 'sampling_mask': descriptor}
    event('InputBatch.refresh_metadata', 'CALL', {**metadata, 'sampling_metadata_token': '0' * 64}, call='6' * 64)
    event('InputBatch.refresh_metadata', 'RETURN', metadata, call='6' * 64)
    event('Sampler.forward', 'CALL', metadata, call='5' * 64)
    event('Sampler.forward', 'RETURN', metadata, call='5' * 64)
    return events, (domain, batch, dimensions), standalone, prior, current


def analyze(events, bootstrap, standalone, prior, current, *, interval=True):
    return producer._native_result(events, bootstrap, current, standalone_b_sha=standalone,
        prior_request_sha=prior, prior_constraint_sha=producer.native_digest([1]), interval_complete=interval)


def test_real_host_row_and_sampler_argument_identity_support_conditional_source_diagnostic(monkeypatch):
    monkeypatch.setattr(producer, 'POINT_CONTRACTS', {'Sampler.forward': {'branch': 'apply_nonnull_allowed_mask'},
        'GPUModelRunner.sample_tokens': {'module': 'EXAMPLE-v1-runner', 'source_sha256': '8' * 64, 'code_sha256': '9' * 64}})
    result = analyze(*native_events())
    assert result['collected'] and result['mismatch']
    assert not result['gpu_execution_verified'] and not result['full_live_coverage']
    assert result['rows'][0]['prior_request_sha256'] == 'c' * 64


@pytest.mark.parametrize('change', ['writer_interval', 'no_standalone', 'no_return', 'different_mask',
    'different_cpu_values', 'intervening_update', 'no_source_branch', 'no_reuse', 'no_v1_caller'])
def test_missing_native_consumption_epoch_or_control_never_produces_fail(monkeypatch, change):
    monkeypatch.setattr(producer, 'POINT_CONTRACTS', {'Sampler.forward': {'branch': 'apply_nonnull_allowed_mask'},
        'GPUModelRunner.sample_tokens': {'module': 'EXAMPLE-v1-runner', 'source_sha256': '8' * 64, 'code_sha256': '9' * 64}})
    events, bootstrap, standalone, prior, current = native_events()
    interval = True
    if change == 'writer_interval': interval = False
    elif change == 'no_standalone': standalone = '0' * 64
    elif change == 'no_return': events.pop()
    elif change == 'different_mask': events[-2]['snapshot']['metadata']['sampling_mask_token'] = '0' * 64
    elif change == 'different_cpu_values':
        for event in events[-3:]:
            row = event['snapshot']['metadata']['sampling_mask']['cpu_rows'][0]
            row['cpu_mask'] = {**row['cpu_mask'], 'mask_sha256': '0' * 64}
    elif change == 'intervening_update':
        writer = deepcopy(next(e for e in events if e['snapshot']['point'] == 'InputBatch.remove_request'))
        writer['snapshot']['binding']['call_id_sha256'] = '0' * 64
        writer['snapshot']['phase'] = 'CALL'; writer['seq'] = events[-2]['seq'] + 0.1
        returned = deepcopy(writer); returned['snapshot']['phase'] = 'RETURN'; returned['seq'] += 0.1
        events[-1:-1] = [writer, returned]
    elif change == 'no_v1_caller': events[-1]['caller_provenance'] = None
    elif change == 'no_source_branch': monkeypatch.setattr(producer, 'POINT_CONTRACTS', {})
    else: prior = '0' * 64
    result = analyze(events, bootstrap, standalone, prior, current, interval=interval)
    assert not result['mismatch']


def test_gpu_descriptor_only_requires_reviewed_source_copy_path_without_readback(monkeypatch):
    events, bootstrap, standalone, prior, current = native_events()
    dimensions = {**bootstrap[2], 'device_type': 'cuda'}
    bootstrap = (bootstrap[0], bootstrap[1], dimensions)
    for event in events:
        metadata = event['snapshot']['metadata']
        metadata['dimensions'] = dimensions
        if metadata.get('sampling_mask_token'):
            metadata['sampling_mask'].update(device_type='cuda', values_collected=False, cpu_rows=None)
    monkeypatch.setattr(producer, 'POINT_CONTRACTS', {'Sampler.forward': {'branch': 'apply_nonnull_allowed_mask'},
        'GPUModelRunner.sample_tokens': {'module': 'EXAMPLE-v1-runner', 'source_sha256': '8' * 64, 'code_sha256': '9' * 64}})
    assert not analyze(events, bootstrap, standalone, prior, current)['collected']
    monkeypatch.setattr(producer, 'POINT_CONTRACTS', {
        'Sampler.forward': {'branch': 'apply_nonnull_allowed_mask'},
        'GPUModelRunner.sample_tokens': {'module': 'EXAMPLE-v1-runner', 'source_sha256': '8' * 64, 'code_sha256': '9' * 64},
        'InputBatch.refresh_metadata': {'branch': 'copy_active_host_mask_rows', 'copy_helper': {'module': 'EXAMPLE-reviewed'}}})
    result = analyze(events, bootstrap, standalone, prior, current)
    assert not result['collected'] and not result['mismatch'] and result['gpu_execution_verified'] is False
    assert result['gaps'] == ['GPU_ORDERING_CONTEXT_UNAVAILABLE']
    assert result['rows'] and result['rows'][0]['host_masked_count'] == 7
    assert result['rows'][0]['sampler_argument_identity_matched'] is True
    assert result['rows'][0]['qualified'] is False and result['rows'][0]['mismatch'] is None


def test_writer_coverage_is_not_inferred_from_empty_or_truncated_metadata(monkeypatch):
    monkeypatch.setattr(producer, 'POINT_CONTRACTS', {})
    assert not producer._interval_verified({'recordings': []}, (123, 456))
    assert producer._native_events({'recordings': []}, {'session': 'a' * 32}) == []


def live_inputs(inputs):
    environment = live_env()
    environment = environment.model_copy(update={'model': environment.model.model_copy(
        update={'inventory_sha256': 'e' * 64})}).seal()
    destination = target(environment)
    prepared = {'session': 'a' * 32}
    body = {'model': 'fixture-model', 'prompt': 'public prompt', 'max_tokens': 1, 'temperature': 0}
    manifest = {'schema': 'byul-req18-cohort/1', 'environment_ref': environment.ref().model_dump(mode='json'),
        'target_sha256': producer.digest(destination), 'session': prepared['session'],
        'run_id': destination['kubernetes']['run_id'], 'owner': 'EXAMPLE-owner', 'recovery_owner': 'EXAMPLE-recovery',
        'model_inventory_sha256': 'e' * 64, 'vocab_size': 8,
        'cohort': [[{'role': 'standalone-b', 'body': deepcopy(body)}],
                   [{'role': 'request-a', 'body': {**body, 'allowed_token_ids': [1]}},
                    {'role': 'request-b', 'body': deepcopy(body)}],
                   [{'role': 'followup-b', 'body': deepcopy(body)}]]}
    return environment, inputs[1], inputs[2], destination, prepared, manifest


def test_live_capability_missing_closes_before_http_and_reader(tmp_path, inputs, monkeypatch):
    environment, policy, pack, destination, prepared, manifest = live_inputs(inputs)
    monkeypatch.setattr(producer, 'verify_components', lambda repo: None)
    monkeypatch.setattr(producer, 'A_SOURCE_PINS', None)
    def forbidden(*args, **kwargs):
        raise AssertionError('network must not run')
    monkeypatch.setattr(producer.kubernetes_http.KubernetesHTTP, '__call__', forbidden)
    monkeypatch.setattr(producer.readers.KubernetesLifecycleReader, 'snapshot', forbidden)
    summary = producer.run_pipeline('kubernetes', environment, policy, pack, tmp_path / 'live',
        target=destination, prepared=prepared, manifest=manifest, execute=True)
    assert summary['mode'] == 'kubernetes' and summary['security'] == 'UNKNOWN'
    assert summary['execution_status'] == 'ABORTED' and summary['restore_verified']


@pytest.mark.parametrize('change', ['oov', 'restricted_b', 'different_b', 'model', 'inventory'])
def test_unsafe_or_unbound_live_cohort_is_rejected_before_output(tmp_path, inputs, change):
    environment, policy, pack, destination, prepared, manifest = live_inputs(inputs)
    if change == 'oov': manifest['cohort'][1][0]['body']['allowed_token_ids'] = [8]
    elif change == 'restricted_b': manifest['cohort'][1][1]['body']['allowed_token_ids'] = [1]
    elif change == 'different_b': manifest['cohort'][1][1]['body']['prompt'] = 'different'
    elif change == 'model': manifest['cohort'][1][1]['body']['model'] = 'wrong-alias'
    else: manifest['model_inventory_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        producer.run_pipeline('kubernetes', environment, policy, pack, tmp_path / 'bad',
            target=destination, prepared=prepared, manifest=manifest, execute=True)
    assert not (tmp_path / 'bad').exists()


def test_late_component_guard_error_aborts_but_keeps_verified_cpu_fail(tmp_path, inputs, monkeypatch):
    calls = []
    def verify(repo):
        calls.append('gate')
        if len(calls) > 1:
            raise ValueError(PRIVATE)
        return lambda *args, **kwargs: mismatch(cpu_record())
    monkeypatch.setattr(producer, 'verify_components', verify)
    summary = producer.run_pipeline('cpu', *inputs, tmp_path / 'post',
        source_root=tmp_path / 'source', execute=True)
    assert summary['execution_status'] == 'ABORTED' and summary['security'] == 'FAIL'
    assert summary['diagnostics'] == ['REQ18_COMPONENT_POSTCHECK_UNAVAILABLE']


@pytest.mark.parametrize('kind', ['crossing', 'open', 'orphan', 'duplicate', 'raise'])
def test_entire_writer_call_lifetime_is_required_for_cpu_native_reducer(monkeypatch, kind):
    monkeypatch.setattr(producer, 'POINT_CONTRACTS', {'Sampler.forward': {'branch': 'apply_nonnull_allowed_mask'},
        'GPUModelRunner.sample_tokens': {'module': 'EXAMPLE-v1-runner', 'source_sha256': '8' * 64, 'code_sha256': '9' * 64}})
    events, bootstrap, standalone, prior, current = native_events()
    call = deepcopy(next(e for e in events if e['snapshot']['point'] == 'InputBatch.remove_request'))
    call['snapshot']['binding']['call_id_sha256'] = '0' * 64
    call['snapshot']['phase'] = 'CALL'
    refresh = next(i for i, e in enumerate(events) if e['snapshot']['point'] == 'InputBatch.refresh_metadata'
                   and e['snapshot']['phase'] == 'RETURN' and e['snapshot']['metadata'].get('sampling_mask_token'))
    call['seq'] = events[refresh]['seq'] - 0.1
    returned = deepcopy(call)
    returned['snapshot']['phase'] = 'RETURN'
    returned['seq'] = events[-1]['seq'] + 1
    if kind == 'orphan':
        events.append(returned)
    else:
        events.insert(refresh, call)
        if kind != 'open':
            if kind == 'raise': returned['snapshot']['phase'] = 'RAISE'
            events.append(returned)
        if kind == 'duplicate': events.append(deepcopy(returned))
    result = analyze(events, bootstrap, standalone, prior, current)
    assert not result['collected'] and not result['mismatch']
    # The independent formal source-CPU criterion remains intact.
    assert producer._cpu_result(mismatch(cpu_record()))['mismatch'] is True


def test_actual_capture_shape_request_selection_reaches_native_analysis(tmp_path, inputs, monkeypatch):
    """EXAMPLE public-interface doubles; real raw request-map join is exercised."""
    from types import SimpleNamespace
    environment, _, _, destination, prepared, manifest = live_inputs(inputs)
    events, bootstrap, standalone, prior, current = native_events()
    contract = {'module': 'EXAMPLE-source', 'source_sha256': '8' * 64, 'code_sha256': '9' * 64}
    monkeypatch.setattr(producer, 'A_SOURCE_PINS', {'guard.runtime.kubernetes_http': '8' * 64})
    monkeypatch.setattr(producer, 'CALLBACK_CODE_SHA256', '9' * 64)
    monkeypatch.setattr(producer, 'POINT_CONTRACTS', {
        'InputProcessor.assign_request_id': contract,
        'Sampler.forward': {'branch': 'apply_nonnull_allowed_mask'},
        'GPUModelRunner.sample_tokens': {'module': 'EXAMPLE-v1-runner', 'source_sha256': '8' * 64, 'code_sha256': '9' * 64}})
    prepared['source_sha256'] = {'EXAMPLE-source': '8' * 64}
    monkeypatch.setattr(producer, '_identity', lambda *args: None)
    monkeypatch.setattr(producer.kubernetes_http, 'SLOT_MASK_PROFILE', 'EXAMPLE-slot-profile', raising=False)
    sent, observed, reads = {}, [], []
    class HTTP:
        def __init__(self, *args, **kwargs): pass
        def __call__(self, network, requests, timeout):
            actors = {'standalone-b': standalone, 'request-a': prior, 'request-b': current, 'followup-b': 'e' * 64}
            for request in requests:
                asgi = producer.hashlib.sha256(request['headers']['X-Request-Id'].encode()).hexdigest()
                sent[asgi] = actors[request['id'].removeprefix('req18-slot-')]
            return [{'outcome': 200}]
        def report(self): return {'cleanup_errors': []}
    class Reader:
        def __init__(self, target, env, prepared, **kwargs): self.binding = target['kubernetes']
        def snapshot(self): return {'EXAMPLE-generation': 1}
        def seal_session(self): return {'sealed': True}
        def owner_identity(self, *args): pass
        def read_file(self, *args): pass
        def image_check(self, *args): return True
    def capture(*args, **kwargs):
        reads.append('capture')
        records = [{'event': 'MODULE', 'payload': {**contract, 'verified': True}}]
        records.extend({'event': 'SELECTION', 'payload': {'point': 'InputProcessor.assign_request_id',
            'source_sha256': '8' * 64, 'metadata': {'request_selection': {
                'asgi_request_sha256': asgi, 'selected_request_id_sha256': native, 'request_id_observed': True}}}}
            for asgi, native in sent.items())
        return {'recordings': [{'session_owner_match': True, 'listed_file_identity_match': True,
            'capture': {'image_digest_verified': True, 'process_context_verified': True, 'records': records}}],
            'slot_request_index': {key: '0' * 64 for key in sent}}  # Must be ignored.
    def snapshots(packet, prepared):
        if len(reads) == 1:
            return [{'domain': bootstrap[0], 'snapshot': {'point': 'InputBatch.__init__', 'phase': 'RETURN',
                'metadata': {'batch_token': bootstrap[1], 'dimensions': bootstrap[2]}}}]
        return events
    analyze_original = producer._native_result
    def analyze_spy(events, bootstrap, request, **kwargs):
        observed.append((request, kwargs))
        return analyze_original(events, bootstrap, request, **kwargs)
    monkeypatch.setattr(producer.kubernetes_http, 'KubernetesHTTP', HTTP)
    monkeypatch.setattr(producer.readers, 'KubernetesLifecycleReader', Reader)
    monkeypatch.setattr(producer.lifecycle_session, 'capture_session', capture)
    monkeypatch.setattr(producer, '_native_events', snapshots)
    monkeypatch.setattr(producer, '_interval_verified', lambda *args: True)
    monkeypatch.setattr(producer, '_native_result', analyze_spy)
    result = producer._capture_kubernetes(environment, destination, prepared, manifest, duration=60, binary='kubectl')
    assert len(reads) == 2 and len(observed) == 1
    assert observed[0][0] == current and observed[0][1]['standalone_b_sha'] == standalone
    assert result['collected'] and result['mismatch']
