"""lab 활성화 시나리오 계약. kubectl 대역만 쓰며 실제 클러스터는 없다."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from guard.executors import lab_activation as lab
from guard.executors import registry
from guard.models import DataOrigin, ExecStatus
from guard.models.objects import Observation

BASE = {'namespace': 'lab', 'selector': 'app=vllm', 'container': 'engine', 'probe_pod': 'probe', 'probe_container': 'probe',
    'service_url': 'http://vllm.lab.svc:8000', 'model': 'm', 'deployment': 'vllm', 'process_pattern': 'VLLM', 'deadline_ms': 10000,
    'burst_requests': 4, 'allowed_models': ['m'], 'faulty_manifest': 'faulty.yaml', 'good_manifest': 'good.yaml',
    'update_manifest': 'update.yaml', 'trace_directory': '/trace', 'fault_directory': '/data'}
POLICY = {'buffer_max_process_rss_bytes': 10 ** 9, 'buffer_max_send_wait_ms': 5000, 'buffer_release_deadline_ms': 2000,
    'termination_max_delay_ms': 2000, 'retry_policy_mode': 'forbidden'}


class Cluster:
    def __init__(self, **modes):
        self.now = 0.0; self.modes = modes; self.calls = []; self.running = 0; self.counter = 0; self.traces = [1, 1]
        self.manifest = 'good'

    def clock(self):
        self.now += 0.01
        return self.now

    def wait(self, seconds):
        self.now += seconds

    def result(self, value):
        raw = value if type(value) is bytes else json.dumps(value).encode()
        return {'returncode': 0, 'stdout_bytes': raw, 'stderr_bytes': b''}

    def pods(self):
        return [{'metadata': {'name': 'engine-0', 'labels': {'byul.lab/generation': self.manifest}},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True' if self.ready() else 'False'}]}}]

    def ready(self):
        return self.manifest != 'faulty' or self.modes.get('faulty_ready', False)

    def http(self, item):
        row = {'outcome': 200, 'elapsed_ms': 10, 'sha256': 'a' * 64, 'model': 'm', 'closed_early': False, 'complete': True, 'bytes': 10, 'answer': 'b' * 64}
        if item['path'] == '/metrics':
            running = 1 if self.modes.get('stuck') else 0
            return {**row, 'metrics': f"vllm:num_requests_running {running}\nvllm:prompt_tokens_total {self.counter}\n"}
        if item['path'] == '/health':
            return row
        if item['path'] in ('/start_profile', '/stop_profile'):
            return row
        if item['body'] and item['body']['messages'][0]['content'].startswith('retry') and not item.get('close_after_bytes'):
            self.counter += 10
        if item.get('close_after_bytes'):
            self.counter += 30 if self.modes.get('retry') else 10
            return {**row, 'closed_early': True, 'complete': False}
        if item['body']['messages'][0]['content'].startswith('fault'):
            return {**row, 'outcome': 'timeout', 'complete': False} if self.modes.get('hang') else {**row, 'elapsed_ms': 2000}
        if self.modes.get('model'):
            return {**row, 'model': self.modes['model']}
        if self.modes.get('not_serving') and self.modes['not_serving'] > 0:
            self.modes['not_serving'] -= 1
            return {**row, 'outcome': 503}
        return row

    def __call__(self, argv, *, timeout, env_extra, max_stdout_bytes, max_stderr_bytes, input_bytes=None):
        self.calls.append(argv)
        text = ' '.join(argv)
        if 'get namespace' in text:
            labels = {lab.OWNED_LABEL: 'true'} if not self.modes.get('foreign') else {}
            return self.result({'metadata': {'labels': labels}})
        if 'get pods' in text:
            return self.result({'items': self.pods()})
        if 'rollout restart' in text:
            return self.result({})
        if ' apply ' in text:
            self.manifest = 'faulty' if b'faulty' in input_bytes else 'good'
            return self.result({})
        if lab.CLIENT in argv:
            spec = json.loads(input_bytes)
            return self.result([self.http(item) for item in spec['requests']])
        if lab.CENSUS in argv:
            count = 0 if self.modes.get('gone') else 2
            return self.result({'processes': [{'pid': 7, 'state': 'S', 'rss_pages': self.modes.get('rss', 100)}] * count,
                'shm_entries': 1, 'page_bytes': 4096})
        if lab.FILE_COUNT in argv:
            return self.result({'count': self.traces.pop(0)})
        if lab.SIGNAL in argv and self.modes.get('die_on_signal'):
            self.modes['gone'] = True
        return self.result({})


@pytest.fixture(autouse=True)
def manifests(tmp_path, monkeypatch):
    for name in ('faulty', 'good', 'update'):
        (tmp_path / f'{name}.yaml').write_text(name)
    monkeypatch.chdir(tmp_path)


def scenario(check_id, *, config=None, policy=None, **modes):
    world = Cluster(**modes)
    facts, _ = lab.run_scenario(check_id, {**BASE, **(config or {})}, {**POLICY, **(policy or {})},
        runner=world, clock=world.clock, wait=world.wait)
    return facts, world


@pytest.mark.parametrize('check_id', sorted(lab.SCENARIOS))
def test_clean_target_completes_scope_with_no_violation(check_id):
    facts, _ = scenario(check_id)
    assert facts['activation_scope_complete'] is True and facts['activation_violations'] == [], facts


@pytest.mark.parametrize('check_id,modes,code', [
    ('LIF-03', {'rss': 10 ** 9}, 'PROCESS_RSS_OVER_BUDGET'),
    ('LIF-04', {'stuck': True}, 'SLOW_READER_NOT_RELEASED_BEFORE_DEADLINE'),
    ('LIF-05', {'model': 'other'}, 'DISALLOWED_MODEL_SERVED_DURING_TRANSITION'),
    ('LIF-06', {'faulty_ready': True}, 'PARTIAL_CHANGE_ACCEPTED'),
    ('LIF-08', {'hang': True}, 'FAULT_NOT_DETECTED_BEFORE_DEADLINE'),
    ('LIF-09', {'faulty_ready': True}, 'PARTIAL_INITIALIZATION_REPORTED_READY'),
    ('LIF-10', {'stuck': True}, 'WORK_CONTINUED_PAST_TERMINATION_DEADLINE'),
    ('LIF-12', {'retry': True}, 'FORBIDDEN_AUTOMATIC_RETRY_OBSERVED'),
    ('LIF-16', {'faulty_ready': True}, 'TAMPERED_RESTORE_ACCEPTED'),
])
def test_activated_fault_produces_the_named_violation(check_id, modes, code):
    facts, _ = scenario(check_id, **modes)
    assert code in {row['code'] for row in facts['activation_violations']}, facts


def test_request_that_ended_before_the_fault_is_not_evidence(monkeypatch):
    world = Cluster()
    original = world.http
    monkeypatch.setattr(world, 'http', lambda item: {**original(item), 'elapsed_ms': 1})
    facts, _ = lab.run_scenario('LIF-08', BASE, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert facts['activation_scope_complete'] is False and 'FAULT_AFTER_REQUEST_FINISHED:death' in facts['activation_missing']


def test_ready_before_service_is_a_violation():
    facts, _ = scenario('LIF-07', not_serving=2)
    assert 'READY_BEFORE_INFERENCE_AVAILABLE' in {row['code'] for row in facts['activation_violations']}


def test_trace_growth_after_stop_is_a_violation():
    world = Cluster(); world.traces = [1, 2]
    facts, _ = lab.run_scenario('LIF-14', BASE, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert 'COLLECTION_CONTINUED_AFTER_STOP' in {row['code'] for row in facts['activation_violations']}


def test_health_after_workers_gone_is_a_violation():
    facts, _ = scenario('OBS-02', die_on_signal=True)
    assert 'HEALTH_REPORTED_AFTER_WORKERS_GONE' in {row['code'] for row in facts['activation_violations']}


def test_unowned_namespace_refuses_before_any_change():
    world = Cluster(foreign=True)
    with pytest.raises(lab.ActivationRefused, match='NOT_OWNED'):
        lab.run_scenario('LIF-11', BASE, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert len(world.calls) == 1 and 'namespace' in world.calls[0]


@pytest.mark.parametrize('check_id,key', [('LIF-09', 'faulty_manifest'), ('LIF-14', 'trace_directory'), ('LIF-06', 'update_manifest')])
def test_missing_operator_input_keeps_scope_incomplete(check_id, key):
    config = {k: v for k, v in BASE.items() if k != key}
    world = Cluster()
    facts, _ = lab.run_scenario(check_id, config, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert facts['activation_scope_complete'] is False and 'CONFIG_REQUIRED:' + key in facts['activation_missing']


def test_missing_fault_hook_keeps_rank_scope_incomplete():
    config = {k: v for k, v in BASE.items() if k != 'fault_directory'}
    world = Cluster()
    facts, _ = lab.run_scenario('LIF-08', config, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert 'CONFIG_REQUIRED:fault_directory' in facts['activation_missing']


def test_missing_metric_is_not_a_clean_result(monkeypatch):
    world = Cluster()
    monkeypatch.setattr(lab.Lab, 'metric', lambda self, name: None)
    facts, _ = lab.run_scenario('LIF-10', BASE, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert facts['activation_scope_complete'] is False and 'RUNNING_REQUEST_METRIC_UNAVAILABLE' in facts['activation_missing']


def test_config_without_required_keys_is_refused():
    with pytest.raises(lab.ActivationRefused, match='CONFIG_INVALID'):
        lab.Lab({'namespace': 'lab'})


class Passive:
    name = 'stream_buffer'; version = '1'

    def __init__(self, failure=None):
        self.failure = failure

    def run(self, check, env, values, adapter, workdir):
        if self.failure:
            raise self.failure
        return SimpleNamespace(observations={'passive': Observation(value=True, collected=True)}, artifacts=[], config={}, cleanup_errors=[])


CHECK = SimpleNamespace(id='LIF-10', challenge_steps=['one', 'two'])


def test_passive_and_active_facts_merge_into_a_completed_evidence(tmp_path):
    world = Cluster()
    executor = lab.ActivatedExecutor(Passive(), BASE, runner=world, clock=world.clock, wait=world.wait)
    draft = executor.run(CHECK, None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert draft.observations['activation_scope_complete'].value is True and draft.observations['passive'].value is True
    assert all(step.attempted for step in draft.challenges) and draft.normal_control.succeeded is True


def test_passive_failure_does_not_block_active_measurement(tmp_path):
    world = Cluster()
    executor = lab.ActivatedExecutor(Passive(RuntimeError('x')), BASE, runner=world, clock=world.clock, wait=world.wait)
    draft = executor.run(CHECK, None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.COMPLETED
    assert {'step': 'passive-collection', 'unavailable': 'RuntimeError'} in draft.observations['activation_steps'].value


def test_incomplete_scope_cannot_clear_the_pass_gate(tmp_path):
    world = Cluster()
    config = {k: v for k, v in BASE.items() if k != 'faulty_manifest'}
    executor = lab.ActivatedExecutor(Passive(), config, runner=world, clock=world.clock, wait=world.wait)
    draft = executor.run(SimpleNamespace(id='LIF-09', challenge_steps=['one']), None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.ABORTED and not draft.challenges[0].attempted


def test_unfinished_real_run_is_never_measured(tmp_path, monkeypatch):
    world = Cluster()
    config = {k: v for k, v in BASE.items() if k != 'faulty_manifest'}
    executor = lab.ActivatedExecutor(Passive(), config)
    monkeypatch.setattr(lab, 'capture_pipes', world)
    executor.clock = world.clock; executor.wait = world.wait
    draft = executor.run(SimpleNamespace(id='LIF-09', challenge_steps=['one']), None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.DECLARED


def test_registry_wraps_only_with_explicit_activation_settings():
    assert not isinstance(registry.build_executor('stream_buffer', 'kubernetes', {}), lab.ActivatedExecutor)
    wrapped = registry.build_executor('stream_buffer', 'kubernetes', {'lab_activation': deepcopy(BASE)})
    assert isinstance(wrapped, lab.ActivatedExecutor) and wrapped.name == 'stream_buffer'
