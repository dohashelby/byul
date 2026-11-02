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


def timing(present_hit, *, fast=True, power=True):
    cases, tags, events, elapsed = [], {}, [], {}
    for role, value, hit in (('control-present', 1 if power else 5, 1), ('control-absent', 5, 0),
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


def test_too_few_samples_stays_uncollected():
    result = projection.project_cache_timing([], {}, {}, {'cache_timing_max_auc_percent': 80, 'cache_timing_min_samples': 4})
    assert result['violation'] is None


def batch(scheduled, returned, point='MultiprocExecutor.execute_model'):
    digest = lambda key: projection.tag_digest(SECRET, TAGS[key])
    return {'point': point, 'phase': 'RETURN', 'entry_qualified_before': True, 'entry_qualified_after': True,
        'fields': {'native_request_sha256': [digest(k) for k in scheduled], 'output_request_sha256': [digest(k) for k in returned], 'rank': 0}}


def test_output_for_a_request_outside_the_batch_is_a_violation():
    tags = {'a': TAGS['a']}
    assert projection.project_distributed([batch(['a'], ['a', 'b'])], SECRET, tags)['violation'] is True


def test_clean_batches_stay_partial_scope_because_buffers_are_unobserved():
    result = projection.project_distributed([batch(['a'], ['a'])], SECRET, {'a': TAGS['a']})
    assert result['violation'] is False and result['full_scope'] is False


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
        self.events.append({'point': projection.COMPUTED, 'phase': 'RETURN', 'entry_qualified_before': True,
            'entry_qualified_after': True, 'fields': {'request_sha256': projection.tag_digest(self.secret, tag),
                'num_computed_tokens': self.tokens[self.count], 'request_block_hashes_sha256': ['h'],
                'request_cache_salt_sha256': request['body'].get('cache_salt')}})
        self.count += 1
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
    assert draft.observations['engine_sensor_violation'] == {'value': True, 'collected': True}
    assert draft.observations['engine_sensor_full_scope']['value'] is False and draft.data_origin == DataOrigin.EXAMPLE


def test_executor_keeps_an_incomplete_clean_run_uncollected(tmp_path):
    draft = run_check(tmp_path, [0, 16, 0])
    assert draft.observations['engine_sensor_violation'] == {'value': None, 'collected': False}
    assert draft.abort_reason == 'ENGINE_SENSOR_CALLBACK_SCOPE_INCOMPLETE'
