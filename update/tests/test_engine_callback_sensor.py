"""Callback cohort projection and prepared sensor contracts. EXAMPLE only, no engine."""
from pathlib import Path
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.collectors import engine_callbacks
from guard.executors import engine_callback_projection as projection, engine_sensor
from guard.models import DataOrigin, Environment, canonical_json, sha256_hex
from tests.builders import baseline_pack
from tests.test_cache_callback import source_module

SECRET = b'k' * 32
TAGS = {'a': 'byul-cb-' + 'a' * 32, 'b': 'byul-cb-' + 'b' * 32, 'c': 'byul-cb-' + 'c' * 32, 'd': 'byul-cb-' + 'd' * 32}


def computed(tag, tokens, *, hashes=('h',), salt='s', qualified=True):
    return {'point': projection.COMPUTED, 'phase': 'RETURN', 'entry_qualified_before': True, 'entry_qualified_after': qualified,
        'fields': {'request_sha256': projection.tag_digest(SECRET, tag), 'num_computed_tokens': tokens,
            'request_block_hashes_sha256': list(hashes), 'request_cache_salt_sha256': salt}}


GROUP_CASES = [{'case_id': 'a', 'role': 'prime', 'group': 'A', 'prefix': 'p', 'prompt': 'x'},
    {'case_id': 'b', 'role': 'control', 'group': 'A', 'prefix': 'p', 'prompt': 'x'},
    {'case_id': 'c', 'role': 'probe', 'group': 'B', 'prefix': 'p', 'prompt': 'x'}]
GROUP_POLICY = {'isolated_cache_groups': ['A', 'B']}


def group(events, cases=GROUP_CASES, tags=None):
    return projection.project_cache_group(cases, projection.computed_rows(events, SECRET, tags or TAGS), GROUP_POLICY)


def test_cross_group_hit_after_working_control_is_a_violation_even_with_other_gaps():
    result = group([computed(TAGS['a'], 0, salt='1'), computed(TAGS['b'], 16, salt='1'), computed(TAGS['c'], 16, salt='2')])
    assert result['violation'] is True and result['pairs'][0]['salts_distinct'] and result['full_scope'] is False


def test_no_cross_hit_without_both_directions_stays_uncollected():
    result = group([computed(TAGS['a'], 0), computed(TAGS['b'], 16), computed(TAGS['c'], 0)])
    assert result['violation'] is None and 'ordered_group_pairs_incomplete' in result['missing']


def test_both_directions_clean_is_false_but_identity_keeps_scope_incomplete():
    mirrored = [{**case, 'case_id': case['case_id'] + '2', 'prefix': 'q',
        'group': {'A': 'B', 'B': 'A'}[case['group']]} for case in GROUP_CASES]
    tags = {**TAGS, 'a2': 'byul-cb-' + '1' * 32, 'b2': 'byul-cb-' + '2' * 32, 'c2': 'byul-cb-' + '3' * 32}
    events = [computed(tags['b'], 16), computed(tags['c'], 0), computed(tags['b2'], 16), computed(tags['c2'], 0)]
    result = group(events, GROUP_CASES + mirrored, tags)
    assert result['violation'] is False and result['full_scope'] is False


def test_control_without_a_hit_cannot_clear_or_condemn_a_pair():
    assert group([computed(TAGS['b'], 0), computed(TAGS['c'], 16)])['violation'] is None


def test_terminally_unqualified_row_is_not_evidence():
    assert group([computed(TAGS['b'], 16), computed(TAGS['c'], 16, qualified=False)])['violation'] is None


CONTEXT_CASES = [{'case_id': 'a', 'role': 'prime', 'context': 'salt', 'prefix': 'p', 'prompt': 'x'},
    {'case_id': 'b', 'role': 'control', 'context': 'salt', 'prefix': 'p', 'prompt': 'x'},
    {'case_id': 'c', 'role': 'probe', 'context': 'salt', 'prefix': 'p', 'prompt': 'x'},
    {'case_id': 'd', 'role': 'post', 'prefix': 'p', 'prompt': 'x'}]


def context(events):
    return projection.project_cache_context(CONTEXT_CASES, projection.computed_rows(events, SECRET, TAGS),
        {'required_cache_contexts': ['salt']})


def test_changed_context_reusing_the_same_key_is_a_violation():
    assert context([computed(TAGS['a'], 0), computed(TAGS['b'], 16), computed(TAGS['c'], 16), computed(TAGS['d'], 16)])['violation'] is True


def test_changed_key_without_hit_passes_every_required_context():
    result = context([computed(TAGS['a'], 0), computed(TAGS['b'], 16), computed(TAGS['c'], 0, hashes=('other',)), computed(TAGS['d'], 16)])
    assert result['violation'] is False and result['full_scope'] is True


def test_hit_on_a_different_key_is_not_assumed_to_be_a_defect():
    result = context([computed(TAGS['a'], 0), computed(TAGS['b'], 16), computed(TAGS['c'], 16, hashes=('other',)), computed(TAGS['d'], 16)])
    assert result['violation'] is False


def test_unmeasured_required_context_stays_uncollected():
    assert context([computed(TAGS['a'], 0), computed(TAGS['b'], 16)])['violation'] is None


def timing(present_hit, *, fast=True, power=True, control=None):
    cases, tags, events, elapsed = [], {}, [], {}
    for role, value, hit in (('control-present', control or (1 if power else 5), 1), ('control-absent', 5, 0),
            ('present', 1 if fast else 5, present_hit), ('absent', 5, 0)):
        for index in range(4):
            case_id = f'{role}-{index}'; tag = 'byul-cb-' + f'{len(tags):032x}'
            cases.append({'case_id': case_id, 'role': role, 'prompt': 'x'}); tags[case_id] = tag
            elapsed[case_id] = value + index / 100
            if role == 'present':
                events.append(computed(tag, 16 if hit else 0))
    return projection.project_cache_timing(cases, projection.computed_rows(events, SECRET, tags), elapsed,
        {'cache_timing_max_auc_percent': 80, 'cache_timing_min_samples': 4})


def test_timing_gap_needs_a_native_cross_group_hit():
    assert timing(True)['violation'] is True
    result = timing(False)
    assert result['violation'] is None and 'timing_gap_without_native_hit' in result['missing']


def test_clean_timing_needs_a_control_with_detection_power():
    assert timing(False, fast=False)['violation'] is False and timing(False, fast=False)['full_scope'] is True
    assert timing(False, fast=False, power=False)['violation'] is None


def test_control_slower_with_the_cache_is_reported_as_reversed_not_as_no_power():
    result = timing(False, fast=False, control=9)
    assert result['control_auc_percent'] == 0 and result['violation'] is None
    assert 'timing_control_direction_reversed' in result['missing'] and 'timing_control_has_no_power' not in result['missing']


def test_too_few_samples_stays_uncollected():
    result = projection.project_cache_timing([], {}, {}, {'cache_timing_max_auc_percent': 80, 'cache_timing_min_samples': 4})
    assert result['violation'] is None


def batch(scheduled, returned):
    digest = lambda key: projection.tag_digest(SECRET, TAGS[key])
    common = {'phase': 'CALL', 'entry_qualified_before': True, 'process_sha256': 'p'}
    return [{**common, 'point': 'WorkerProc._execute_worker_rpc',
            'fields': {'rpc_method': 'execute_model', 'native_request_sha256': [digest(k) for k in scheduled]}},
        {**common, 'point': 'WorkerProc.handle_output', 'fields': {'output_request_sha256': [digest(k) for k in returned]}}]


def test_output_for_a_request_outside_the_batch_is_a_violation():
    assert projection.project_distributed(batch(['a'], ['a', 'b']), SECRET, {'a': TAGS['a']})['violation'] is True


def test_clean_batches_stay_partial_scope_because_buffers_are_unobserved():
    result = projection.project_distributed(batch(['a'], ['a']), SECRET, {'a': TAGS['a']})
    assert result['violation'] is False and result['full_scope'] is False


def test_output_from_the_previous_batch_is_allowed_in_pipelined_execution():
    events = [*batch(['a'], []), *batch(['b'], ['a'])]
    assert projection.project_distributed(events, SECRET, {'a': TAGS['a'], 'b': TAGS['b']})['violation'] is False


def test_real_sources_and_shared_key_bind_engine_ids_back_to_the_parent_tag(tmp_path):
    from guard.adapters import vllm_cache_callback as callback
    manager_module = source_module('vllm.v1.core.kv_cache_manager', tmp_path)
    pool_module = source_module('vllm.v1.core.block_pool', tmp_path)
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'fixture': True})
    assert callbacks.instrument(manager_module) and callbacks.instrument(pool_module)
    manager = manager_module.KVCacheManager()
    manager.prefix_cache_lookup_enabled = lambda request: True
    manager.enable_kv_cache_events = False
    manager.create_kv_cache_blocks = lambda blocks: SimpleNamespace(blocks=tuple(blocks))
    manager.block_pool = pool_module.BlockPool()
    manager.coordinator = SimpleNamespace(find_longest_cache_hit=lambda keys, maximum: ([[]], 4, 0))
    request = SimpleNamespace(request_id=f"chatcmpl-{TAGS['a']}-1234", cache_salt='private-salt-value', block_hashes=[b'x'], lora_request=None, num_tokens=5)
    manager.get_computed_blocks(request)
    found = projection.computed_rows(rows, SECRET, {'a': TAGS['a']})
    assert found['a'][0]['tokens'] == 4 and 'private-salt-value' not in json.dumps(rows) and TAGS['a'] not in json.dumps(rows)


def test_prepared_assets_detect_changed_bytes(tmp_path):
    root = tmp_path / 'assets'
    manifest = engine_callbacks.prepare_assets(root)
    assert engine_callbacks.load_prepared(root / 'manifest.json') == manifest
    assert 'secret' not in json.dumps(manifest) and len(engine_callbacks.load_secret(root)) == 32
    with pytest.raises(ValueError, match='ENGINE_CALLBACK_OUTPUT_EXISTS'):
        engine_callbacks.prepare_assets(root)
    (root / 'sitecustomize.py').write_text('import os\n')
    with pytest.raises(ValueError, match='ENGINE_CALLBACK_PREPARED_INVALID'):
        engine_callbacks.load_prepared(root)


def test_cli_prepares_assets_without_exposing_the_key(tmp_path):
    from typer.testing import CliRunner
    from guard.cli import app
    result = CliRunner().invoke(app, ['prepare-engine-callbacks', '--output', str(tmp_path / 'assets')])
    assert result.exit_code == 0 and 'secret' not in result.output
    assert json.loads(result.output)['profile'] == sensor.PROFILE


def test_prepared_package_leaves_an_unpinned_engine_running_and_records_the_gap(tmp_path):
    root = tmp_path / 'assets'; data = tmp_path / 'data'
    session = engine_callbacks.prepare_assets(root)['session']
    package = tmp_path / 'engine/vllm/v1/core'; package.mkdir(parents=True)
    for folder in (tmp_path / 'engine/vllm', tmp_path / 'engine/vllm/v1', package):
        (folder / '__init__.py').write_text('')
    (package / 'block_pool.py').write_text('class BlockPool:\n    def get_cached_block(self):\n        return 7\n')
    code = ('import sys\nsys.path[:0]=[sys.argv[1],sys.argv[2]]\n'
        'from guard_engine_callbacks import vllm_engine_callback_sensor as s\n'
        "assert s.install(sys.argv[1],sys.argv[3],context_provider=lambda:{'fixture':True})\n"
        'import vllm.v1.core.block_pool as b\nprint(b.BlockPool().get_cached_block())\n')
    done = subprocess.run([sys.executable, '-I', '-c', code, str(root), str(tmp_path / 'engine'), str(data)],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == '7', done.stderr
    row = json.loads(next((data / session).glob('callbacks-*.jsonl')).read_text().splitlines()[0])
    assert row['phase'] == 'GAP' and row['code'] == 'ENGINE_CALLBACK_INSTALL_UNAVAILABLE'


class Transport:
    api_model = 'EXAMPLE-model'; process_observation_origin = 'EXAMPLE'
    endpoint = SimpleNamespace(scheme='http', netloc='engine:8000')

    def __init__(self, secret, tokens):
        self.secret = secret; self.tokens = tokens; self.events = []; self.count = 0

    def __call__(self, network, requests, timeout):
        request, = requests
        tag = request['headers']['X-Request-Id']
        barrier = request['id'] == 'cb-9999'
        self.events.append({'point': projection.COMPUTED, 'phase': 'RETURN', 'entry_qualified_before': True,
            'entry_qualified_after': True, 'fields': {'request_sha256': projection.tag_digest(self.secret, tag),
                'num_computed_tokens': 0 if barrier else self.tokens[self.count], 'request_block_hashes_sha256': ['h'],
                'request_cache_salt_sha256': request['body'].get('cache_salt')}})
        self.count += 0 if barrier else 1
        return [{'outcome': 200, 'capture_complete': True, 'elapsed_ms': 5}]

    def engine_callback_events(self, session):
        return {'events': self.events, 'truncated': False}


def run_check(tmp_path, tokens):
    env = Environment(id='ENV-CALLBACK-EXAMPLE', data_origin=DataOrigin.EXAMPLE,
        engine={'name': 'vllm', 'version': '0.31.0'}).seal()
    prepared = engine_callbacks.prepare_assets(tmp_path / 'assets')
    target = {'kubernetes': {}, 'lab': True}
    manifest = {'schema': projection.SCHEMA, 'session': prepared['session'], 'target_sha256': sha256_hex(canonical_json(target)),
        'environment_ref': env.ref().model_dump(mode='json'),
        'cases': [{**case, 'cache_salt': case['group']} for case in GROUP_CASES]}
    (tmp_path / 'target.json').write_bytes(canonical_json(target))
    (tmp_path / 'manifest.json').write_bytes(canonical_json(manifest))
    executor = engine_sensor.EngineSensorExecutor({'target': str(tmp_path / 'target.json'),
        'prepared': str(tmp_path / 'assets/manifest.json'), 'manifest': str(tmp_path / 'manifest.json')}, implementation='kubernetes')
    executor.transport = Transport(engine_callbacks.load_secret(tmp_path / 'assets'), tokens)
    values = {'sensor_environment_sha256': env.ref().sha256, 'sensor_target_sha256': sha256_hex(canonical_json(target)),
        'sensor_prepared_sha256': sha256_hex(canonical_json(prepared)), 'sensor_manifest_sha256': sha256_hex(canonical_json(manifest)),
        **GROUP_POLICY, 'public_prefix_sharing_allowed': False, 'cache_identity_policy_ref': 'EXAMPLE'}
    check = baseline_pack().checks['REQ-15'].model_copy(deep=True)
    check.executor = 'engine_sensor'; check.collection_profile = engine_sensor.PROFILES['REQ-15']
    return executor.run(check, env, values, None, tmp_path / 'work')


def test_executor_reports_an_independent_cross_group_failure(tmp_path):
    draft = run_check(tmp_path, [0, 16, 16])
    violation = draft.observations['engine_sensor_violation']
    assert violation.value is True and violation.collected is True
    assert draft.observations['engine_sensor_full_scope'].value is False and draft.data_origin == DataOrigin.EXAMPLE


def test_executor_keeps_an_incomplete_clean_run_uncollected(tmp_path):
    draft = run_check(tmp_path, [0, 16, 0])
    assert draft.observations['engine_sensor_violation'].collected is False
    assert draft.abort_reason == 'ENGINE_SENSOR_CALLBACK_SCOPE_INCOMPLETE'


def test_lab_fault_raises_once_in_the_worker_and_only_when_enabled(tmp_path):
    fault = tmp_path / 'fault'
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, lambda row: None, context_provider=lambda: {}, fault_file=str(fault))
    faulted = callbacks._fault(lambda: 'done')
    assert faulted() == 'done'
    fault.write_text('1')
    with pytest.raises(RuntimeError, match='GUARD_LAB_FAULT'):
        faulted()
    assert faulted() == 'done' and not fault.exists()


IDENTITY_CASES = [{'case_id': f'{p}{i}', 'role': 'probe', 'prompt': 'x', 'principal': p, **({'forged_salt': 'z'} if i else {})}
    for p in 'AB' for i in (0, 1)]


def identity(salts):
    rows = {case['case_id']: [{'salt': salt}] for case, salt in zip(IDENTITY_CASES, salts)}
    return projection.project_identity(IDENTITY_CASES, rows)


def test_principal_decides_the_salt_even_when_the_client_forges_one():
    assert identity(['a', 'a', 'b', 'b'])['bound'] is True


def test_forged_salt_reaching_the_engine_is_a_group_change():
    result = identity(['a', 'z', 'b', 'b'])
    assert result['client_changed_group'] == ['A'] and not result['bound']


def test_two_principals_sharing_a_salt_is_not_isolated():
    assert identity(['a', 'a', 'a', 'a'])['principals_sharing_salt'] == ['A']


def test_group_scope_completes_only_with_a_bound_identity():
    mirrored = [{**case, 'case_id': case['case_id'] + '2', 'prefix': 'q',
        'group': {'A': 'B', 'B': 'A'}[case['group']]} for case in GROUP_CASES]
    tags = {**TAGS, 'a2': 'byul-cb-' + '1' * 32, 'b2': 'byul-cb-' + '2' * 32, 'c2': 'byul-cb-' + '3' * 32}
    rows = projection.computed_rows([computed(tags['b'], 16), computed(tags['c'], 0), computed(tags['b2'], 16), computed(tags['c2'], 0)], SECRET, tags)
    cases = GROUP_CASES + mirrored
    result = projection.project_cache_group(cases, rows, GROUP_POLICY, identity(['a', 'a', 'b', 'b']))
    assert result['violation'] is False and result['full_scope'] is True
    changed = projection.project_cache_group(cases, rows, GROUP_POLICY, identity(['a', 'z', 'b', 'b']))
    assert changed['violation'] is True and changed['full_scope'] is False


def test_sequence_binding_needs_one_request_per_case():
    events = [computed('byul-cb-' + str(i) * 32, 0) for i in range(4)]
    assert set(projection.sequence_rows(events, IDENTITY_CASES)) == {c['case_id'] for c in IDENTITY_CASES}
    assert projection.sequence_rows(events[:3], IDENTITY_CASES) == {}
    earlier = [computed('byul-cb-' + 'e' * 32, 0)]
    assert projection.sequence_rows(earlier + events, IDENTITY_CASES)['A0'] == projection.sequence_rows(events, IDENTITY_CASES)['A0']


SLOT_CASES = [{'case_id': 'a', 'role': 'standalone', 'prompt': 'x'}, {'case_id': 'b', 'role': 'standalone', 'prompt': 'x'},
    {'case_id': 'c', 'role': 'restricted', 'prompt': 'y', 'allowed_token_ids': [1]}, {'case_id': 'd', 'role': 'reused', 'prompt': 'x'}]


def slot_event(tag, slot, restricted):
    return {'point': 'InputBatch.add_request', 'phase': 'RETURN', 'entry_qualified_before': True, 'entry_qualified_after': True,
        'fields': {'request_sha256': projection.tag_digest(SECRET, tag), 'slot': slot, 'restricted': restricted}}


def slot(outputs, *, b_slot=0):
    events = [slot_event(TAGS['c'], 0, True), slot_event(TAGS['d'], b_slot, False)]
    return projection.project_slot(SLOT_CASES, outputs, events, SECRET, TAGS)


def test_reused_slot_with_changed_output_is_a_violation():
    assert slot({'a': 'o', 'b': 'o', 'd': 'other'})['violation'] is True


def test_reused_slot_with_the_standalone_output_completes_scope():
    result = slot({'a': 'o', 'b': 'o', 'd': 'o'})
    assert result['violation'] is False and result['full_scope'] is True


def test_different_slot_proves_nothing():
    assert slot({'a': 'o', 'b': 'o', 'd': 'other'}, b_slot=1)['violation'] is None


def test_unstable_controls_cannot_condemn_the_engine():
    result = slot({'a': 'o', 'b': 'p', 'd': 'other'})
    assert result['violation'] is None and 'standalone_control_not_deterministic' in result['missing']


def test_slot_observer_records_slot_and_restriction_without_values():
    rows = []
    observer = sensor.SlotObserver('a' * 32, sink=rows.append, context_provider=lambda: {})
    request = SimpleNamespace(req_id=TAGS['a'], sampling_params=SimpleNamespace(allowed_token_ids=[5]))
    fields = observer._snapshot('InputBatch.add_request', (None, request), {}, 3, True)
    assert fields['slot'] == 3 and fields['restricted'] is True and '5' not in str(fields['request_sha256'][:0])


def test_recording_continues_past_the_in_memory_event_limit():
    from guard.adapters import vllm_cache_callback as callback
    rows = []
    observer = sensor.SlotObserver('a' * 32, sink=rows.append, context_provider=lambda: {})
    for _ in range(callback.MAX_EVENTS + 5):
        observer._emit({'phase': 'CALL'})
    assert len(rows) == callback.MAX_EVENTS + 5 and not observer.gaps and not observer.events


def test_gateway_rejecting_the_forged_salt_counts_as_bound():
    rows = {'A0': [{'salt': 'a'}], 'B0': [{'salt': 'b'}]}
    assert projection.project_identity(IDENTITY_CASES, rows, {'A1', 'B1'})['bound'] is True


def test_window_drops_earlier_runs_and_processes_without_the_barrier():
    barrier = 'byul-cb-' + 'f' * 32
    old = {**computed(TAGS['a'], 16), 'process_sha256': 'p1'}
    mark = {**computed(barrier, 0), 'process_sha256': 'p1'}
    new = {**computed(TAGS['b'], 16), 'process_sha256': 'p1'}
    gone = {**computed(TAGS['c'], 16), 'process_sha256': 'p0'}
    assert projection.window([old, gone, mark, new], SECRET, barrier) == [mark, new]
