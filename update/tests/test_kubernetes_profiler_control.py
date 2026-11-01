"""Prepared15/native callbacks and actual kubectl protocol; all I/O is EXAMPLE."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_profiler_sensor as sensor
from guard.collectors.lifecycle import prepare_assets, load_prepared
from guard.executors import kubernetes_profiler_control as product
from guard.models import canonical_json, sha256_hex, utcnow
from guard.runtime.kubernetes_http import KubernetesBindingError
from guard.runtime.kubernetes_profiler_control import KubernetesProfilerControlHTTP, SLOTS, CONTROL_PATHS
from tests.test_kubernetes_request_binding import target as http_target, env as http_env
from tests.test_kubernetes_sampling import World as PodWorld
from tests.test_lifecycle_capture import external
from tests.test_lifecycle_sensor import CONTEXT
from tests.test_a1_profiler_sensor import make_world, PRIVATE


class World(PodWorld):
    def __init__(self, target, prepared, monkeypatch, mode):
        super().__init__(target)
        self.mode = mode; self.prepared = prepared; self.replaced = False; self.stages = []
        self.native = make_world(session=prepared['session'], patch=monkeypatch,
            mode='backend-stop-failure' if mode in {'conflict', 'followup-error', 'capture-error', 'replacement'}
            else 'start-failure' if mode == 'source-start-error' else 'normal')
        self.mutate = self.mutation

    def mutation(self, role, value, after):
        if role == 'engine' and self.replaced: value['metadata']['uid'] = 'replacement-is-not-selected-owner'

    def runner(self, argv, **kwargs):
        if not ('exec' in argv and 'input_bytes' in kwargs): return super().runner(argv, **kwargs)
        request, = json.loads(kwargs['input_bytes'])['requests']; slot = request['id']
        self.execs.append(deepcopy(request)); self.stages.append(slot)
        assert request['method'] == 'POST' and request['mode'] == 'json'
        assert request['url'].endswith(CONTROL_PATHS.get(slot, '/v1/completions'))
        assert request['body'] == (None if slot in CONTROL_PATHS else {
            'model': 'fixture-model', 'prompt': 'hello', 'max_tokens': 1, 'temperature': 0, 'stream': False})
        outcome = 200
        if slot in CONTROL_PATHS:
            rid = request['headers']['X-Request-Id']
            token = self.native.base._ROUTE_REQUEST.set({'pid': os.getpid(), 'closed': False,
                'request': sha256_hex(rid.encode()), 'invocation': sha256_hex(('invocation-' + rid).encode()),
                'path': None, 'root': None, 'method': None})
            try:
                response = asyncio.run(self.native.router.router.dispatch(CONTROL_PATHS[slot], SimpleNamespace()))
                outcome = response.status_code
            finally: self.native.base._ROUTE_REQUEST.reset(token)
            text = ''
        else: text = json.dumps({'model': 'fixture-model', 'choices': [{'text': PRIVATE}]})
        if self.mode == 'followup-error' and slot == 'normal-after': raise OSError(PRIVATE)
        if self.mode == 'baseline-error' and slot == 'normal-before': outcome = 503
        if self.mode == 'http-start-error' and slot == 'start': outcome = 503
        if self.mode == 'replacement' and slot == 'normal-next': self.replaced = True
        row = {'id': slot, 'method': 'POST', 'mode': 'json', 'outcome': outcome, 'body': text,
            'body_bytes': len(text.encode()), 'body_sha256': sha256_hex(text.encode()), 'eof': True,
            'capture_complete': True, 'utf8_valid': True, 'body_truncated': False}
        if self.mode == 'wrong-response-id' and slot == 'start': row['id'] = 'foreign'
        return {'returncode': 0, 'stdout_bytes': canonical_json([row]), 'stderr_bytes': PRIVATE.encode(),
            'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}

    def packet(self):
        recorded = [] if self.mode == 'missing-native' else self.native.observer.recorded
        rows = [{'seq': 0, 'event': 'START', 'payload': {'context': {**CONTEXT, 'pid': os.getpid()}}}]
        rows += [{'seq': i + 1, **deepcopy(row)} for i, row in enumerate(recorded)]
        return {'session': self.prepared['session'], 'recorded_files_covered': not self.replaced,
            'recordings': [{'capture': {'records': rows, 'summary': {'recording_closed': True},
                'process_context_verified': not self.replaced, 'image_digest_verified': False},
                'session_owner_match': not self.replaced, 'listed_file_identity_match': not self.replaced}]}


def inputs(tmp_path, external, monkeypatch, mode='normal'):
    env = http_env(); target = http_target(env)
    folder = external / 'prepared15'
    prepare_assets(folder, Path(__file__).resolve().parents[2], profile=sensor.PROFILE)
    prepared = load_prepared(folder / 'manifest.json')
    world = World(target, prepared, monkeypatch, mode)
    target_path = tmp_path / 'target.json'; target_path.write_bytes(canonical_json(target))
    cfg = {'target': str(target_path), 'prepared': str(folder / 'manifest.json')}
    now = utcnow()
    values = {'profiler_environment_sha256': env.ref().sha256, 'profiler_target_sha256': sha256_hex(canonical_json(target)),
        'profiler_prepared_sha256': sha256_hex(canonical_json(prepared)),
        'profiler_validity_start': (now - timedelta(seconds=30)).isoformat(),
        'profiler_validity_end': (now + timedelta(seconds=90)).isoformat(),
        'profiler_backend_kind': 'torch', 'profiler_required_ranks': ['0'],
        'profiler_control_scope': 'dedicated-owned-profiler', 'profiler_control_owner_ref': 'approval:EXAMPLE-owned-profiler-case',
        'profiler_case': 'prepared-stop-failure' if mode != 'normal' else 'normal'}
    return env, world, cfg, values


def run(tmp_path, external, monkeypatch, mode):
    env, world, cfg, values = inputs(tmp_path, external, monkeypatch, mode)
    if mode == 'preexisting':
        asyncio.run(world.native.router.router.dispatch('/start_profile', SimpleNamespace()))
        assert world.native.backend.running is True and world.native.backend.starts == 1 and world.native.backend.stops == 0
    class Reader:
        def __init__(self, target, env, prepared, **kwargs): self.binding = {'engine': world.target['kubernetes']['engine']}
        def snapshot(self): return {}
        def seal_session(self): return {'sealed': mode != 'seal-error' and not world.replaced}
        def report(self): return {}
    monkeypatch.setattr(product, 'KubernetesLifecycleReader', Reader)
    executor = product.KubernetesProfilerControlExecutor(cfg, runner=world.runner)
    captures = []
    def capture(*args):
        captures.append(len(captures))
        if mode == 'capture-error' and len(captures) > 1: raise OSError(PRIVATE)
        return world.packet()
    monkeypatch.setattr(executor, '_capture', capture)
    check = SimpleNamespace(id='LIF-14', collection_profile=product.PROFILE)
    return executor.run(check, env, values, None, tmp_path / 'work'), world, values


@pytest.mark.parametrize('mode', ['normal', 'conflict', 'followup-error', 'capture-error', 'replacement', 'missing-native', 'seal-error', 'baseline-error'])
def test_closed_control_sequence_real_native_producer_and_strict_truth_limits(tmp_path, external, monkeypatch, mode):
    draft, world, values = run(tmp_path, external, monkeypatch, mode)
    trials = draft.observations['profiler_control_trials'].value
    if mode == 'baseline-error':
        assert world.stages == ['normal-before', 'normal-after']
        assert all(not t['attempted'] for t in trials if t['case_id'] in CONTROL_PATHS)
    elif mode == 'replacement':
        assert world.stages == list(SLOTS[:6])
    else: assert world.stages == list(SLOTS)
    assert len(trials) == len(SLOTS)
    assert draft.data_origin.value == 'EXAMPLE'
    assert all(draft.observations[key].value is False for key in (
        'profiler_backend_collection_stopped_verified', 'profiler_trace_output_completed_verified',
        'profiler_cross_process_rpc_verified', 'profiler_scope_complete'))
    assert world.native.backend.starts == (0 if mode == 'baseline-error' else 2)
    conflicts = draft.observations['profiler_control_flag_adapter_conflicts'].value
    expected_conflict = mode in {'conflict', 'followup-error', 'capture-error', 'replacement'}
    assert any(c['flag_adapter_conflict_observed'] for c in conflicts) is expected_conflict
    assert all(not c['actual_collection_continued_verified'] and not c['confirmed_full_state_violation'] for c in conflicts)
    if mode in {'normal', 'conflict', 'followup-error', 'seal-error'}:
        assert draft.observations['profiler_next_start_api_linked'].value is True
    if mode in {'followup-error', 'capture-error', 'replacement', 'seal-error', 'baseline-error'}:
        assert draft.status.value == 'ABORTED'
    if mode in {'normal', 'conflict'}: assert draft.status.value == 'COMPLETED'
    artifact = json.loads(draft.artifacts[0].path.read_bytes())
    assert artifact['policy_values'] == values
    assert not artifact['faults_injected'] and not artifact['engine_started']
    assert PRIVATE not in draft.artifacts[0].path.read_text(encoding='utf-8')
    assert all(not any(word in argv for word in ('apply', 'patch', 'delete', 'create', 'rollout')) for argv in world.calls)


@pytest.mark.parametrize('slot', ['start', 'delete', 'normal-active'])
def test_transport_rejects_out_of_order_or_unknown_actions_before_api(tmp_path, external, monkeypatch, slot):
    env, world, cfg, values = inputs(tmp_path, external, monkeypatch)
    transport = KubernetesProfilerControlHTTP(world.target, env, duration=60, runner=world.runner)
    with pytest.raises(KubernetesBindingError): transport.perform(slot, 'byul-lif14-' + 'a' * 32 + '-' + slot)
    assert not world.execs


def test_wrong_response_identity_does_not_claim_control_success_but_next_stop_can_run(tmp_path, external, monkeypatch):
    env, world, cfg, values = inputs(tmp_path, external, monkeypatch, 'wrong-response-id')
    transport = KubernetesProfilerControlHTTP(world.target, env, duration=60, runner=world.runner)
    token = 'b' * 32
    assert transport.perform('normal-before', 'byul-lif14-' + token + '-normal-before')['normal_shape_verified']
    with pytest.raises(KubernetesBindingError): transport.perform('start', 'byul-lif14-' + token + '-start')
    assert transport.perform('stop', 'byul-lif14-' + token + '-stop')['profiler_control_response_success']
    assert not transport.report()['requests'][1]['binding_verified_before_after']


@pytest.mark.parametrize('change', ['target-pin', 'prepared-pin', 'unsupported-backend', 'required-rank', 'external-pin'])
def test_policy_and_target_preconditions_reject_before_start(tmp_path, external, monkeypatch, change):
    env, world, cfg, values = inputs(tmp_path, external, monkeypatch)
    expected = None
    if change == 'target-pin': values['profiler_target_sha256'] = '0' * 64
    if change == 'prepared-pin': values['profiler_prepared_sha256'] = '0' * 64
    if change == 'unsupported-backend': values['profiler_backend_kind'] = 'infer-from-http-200'
    if change == 'required-rank': values['profiler_required_ranks'] = ['0', '0']
    if change == 'external-pin': expected = '0' * 64
    executor = product.KubernetesProfilerControlExecutor(cfg, runner=world.runner, expected_target_sha256=expected)
    with pytest.raises(ValueError): executor.run(SimpleNamespace(id='LIF-14', collection_profile=product.PROFILE), env, values, None, tmp_path)
    assert world.stages == []


def test_same_time_or_different_request_identity_cannot_join_api_calls(tmp_path, external, monkeypatch):
    draft, world, values = run(tmp_path, external, monkeypatch, 'normal')
    artifact = json.loads(draft.artifacts[0].path.read_bytes())
    trials = deepcopy(artifact['trials'])
    for trial in trials: trial['request_id_sha256'] = '0' * 64
    projection = product.project_captures(artifact['captures'], trials)
    assert projection['api_links'] == []
    assert any('unique_binding_unavailable' in item for item in projection['missing'])


@pytest.mark.parametrize('change', ['missing-scope', 'missing-owner', 'unknown-scope', 'false-scope', 'empty-owner',
    'whitespace-owner', 'false-owner', 'unqualified-owner', 'control-character-owner'])
def test_preexisting_active_profiler_is_untouched_without_explicit_dedicated_fixture_declaration(
        tmp_path, external, monkeypatch, change):
    env, world, cfg, values = inputs(tmp_path, external, monkeypatch)
    asyncio.run(world.native.router.router.dispatch('/start_profile', SimpleNamespace()))
    assert world.native.backend.running is True
    assert world.native.backend.starts == 1 and world.native.backend.stops == 0
    if change == 'missing-scope': values.pop('profiler_control_scope')
    if change == 'missing-owner': values.pop('profiler_control_owner_ref')
    if change == 'unknown-scope': values['profiler_control_scope'] = 'shared-existing-profiler'
    if change == 'false-scope': values['profiler_control_scope'] = False
    if change == 'empty-owner': values['profiler_control_owner_ref'] = ''
    if change == 'whitespace-owner': values['profiler_control_owner_ref'] = '   '
    if change == 'false-owner': values['profiler_control_owner_ref'] = False
    if change == 'unqualified-owner': values['profiler_control_owner_ref'] = 'someone'
    if change == 'control-character-owner': values['profiler_control_owner_ref'] = 'approval:owner\x00'
    executor = product.KubernetesProfilerControlExecutor(cfg, runner=world.runner)
    with pytest.raises(ValueError, match='PROFILER_CONTROL_POLICY_INVALID'):
        executor.run(SimpleNamespace(id='LIF-14', collection_profile=product.PROFILE), env, values, None, tmp_path)
    assert world.stages == [] and world.execs == [] and world.calls == []
    assert world.native.backend.running is True
    assert world.native.backend.starts == 1 and world.native.backend.stops == 0


def test_explicit_dedicated_fixture_declaration_allows_preexisting_stop_without_backend_truth_claim(tmp_path, external, monkeypatch):
    draft, world, values = run(tmp_path, external, monkeypatch, 'preexisting')
    assert world.stages == list(SLOTS)
    assert world.native.backend.starts == 2 and world.native.backend.stops == 2
    assert world.native.backend.running is False
    artifact = json.loads(draft.artifacts[0].path.read_bytes())
    expected = {key: values[key] for key in ('profiler_control_scope', 'profiler_control_owner_ref',
        'profiler_environment_sha256', 'profiler_target_sha256', 'profiler_prepared_sha256')}
    assert artifact['control_scope_declaration'] == draft.config['control_scope_declaration'] == expected
    assert artifact['policy_values'] == values
    assert artifact['preexisting_backend_inactive_verified'] is False
    assert artifact['profiler_ownership_acquired_by_start_verified'] is False
    assert draft.observations['profiler_backend_collection_stopped_verified'].value is False
    assert draft.observations['profiler_scope_complete'].value is False


@pytest.mark.parametrize('mode', ['source-start-error', 'http-start-error'])
def test_failed_start_never_acquires_ownership_stop_scope_comes_only_from_explicit_fixture_policy(tmp_path, external, monkeypatch, mode):
    draft, world, values = run(tmp_path, external, monkeypatch, mode)
    artifact = json.loads(draft.artifacts[0].path.read_bytes())
    assert world.stages == list(SLOTS)
    assert artifact['profiler_ownership_acquired_by_start_verified'] is False
    assert artifact['control_scope_declaration']['profiler_control_owner_ref'] == values['profiler_control_owner_ref']
    assert artifact['control_scope_declaration']['profiler_control_scope'] == 'dedicated-owned-profiler'
    if mode == 'source-start-error':
        assert any(s['point'] == 'TorchProfilerWrapper._start' and s['phase'] == 'RAISE'
            for projection in draft.observations['profiler_control_projections'].value for s in projection['samples'])
    else:
        assert next(t for t in draft.observations['profiler_control_trials'].value if t['case_id'] == 'start')['profiler_control_response_success'] is False
    assert draft.observations['profiler_backend_collection_stopped_verified'].value is False
    assert draft.observations['profiler_scope_complete'].value is False
