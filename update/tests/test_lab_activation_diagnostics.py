"""B-only diagnostic receipts: never substitute for native or verdict facts."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from guard.executors import lab_activation as lab
from guard.models import DataOrigin, ExecStatus

_spec = importlib.util.spec_from_file_location('_lab_diagnostic_fixture', Path(__file__).with_name('test_lab_activation.py'))
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)


def pod(name, container):
    return {'metadata': {'name': name, 'namespace': 'lab', 'uid': name + '-uid',
            'labels': {'app': 'vllm', 'byul.lab/generation': 'faulty'}},
        'spec': {'nodeName': 'node-1', 'containers': [{'name': container,
            'args': ['--port', '8000'], 'ports': [{'containerPort': 8000, 'protocol': 'TCP'}]}]},
        'status': {'podIP': '10.0.0.2' if name == 'engine' else '10.0.0.3',
            'containerStatuses': [{'name': container, 'containerID': 'containerd://' + hashlib.sha256(name.encode()).hexdigest(),
                'restartCount': 0, 'ready': False, 'state': {'running': {'startedAt': '2026-11-03T00:00:00Z'}}}]}}


def captured(value):
    raw = value if type(value) is bytes else json.dumps(value).encode()
    streams = {name: {'limit': 2 * 1024 ** 2 if name == 'stdout' else 4096,
        'retained_bytes': len(data), 'observed_bytes': len(data), 'truncated': False,
        'retained_sha256': hashlib.sha256(data).hexdigest()} for name, data in (('stdout', raw), ('stderr', b''))}
    return {'returncode': 0, 'stdout_bytes': raw, 'stderr_bytes': b'', 'capture': {
        'schema': 'guard-pipe-capture/1', 'bounded_at_source': True, 'complete': True, 'timed_out': False,
        'truncated': False, 'pipes_closed': True, 'read_errors': [], 'streams': streams}}


class DiagnosticCluster:
    def __init__(self):
        self.now = 0.; self.calls = []; self.request = None; self.after = False
        self.engine = pod('engine', 'engine'); self.probe = pod('probe', 'probe')
        self.service = {'metadata': {'namespace': 'lab'}, 'spec': {'selector': {'app': 'vllm'},
            'ports': [{'port': 8000, 'targetPort': 8000, 'protocol': 'TCP'}]}}
        self.namespace = {'metadata': {'name': 'lab', 'uid': 'namespace-generation-1', 'labels': {lab.OWNED_LABEL: 'true'}}}
        self.after_change = None; self.after_error = False
        self.outcome = 'timeout'

    def runner(self, argv, **options):
        self.calls.append((argv, options)); self.now += .01
        if 'get' in argv and 'namespace' in argv:
            value = deepcopy(self.namespace)
            if self.after and self.after_change:
                self.after_change(value)
        elif 'get' in argv and 'pod' in argv:
            if self.after_error and self.after:
                raise OSError('after fetch unavailable')
            name = argv[argv.index('pod') + 1]
            value = deepcopy(self.engine if name == 'engine' else self.probe)
            if self.after and self.after_change:
                self.after_change(value)
        elif 'service' in argv:
            value = self.service
        elif lab.CLIENT in argv:
            self.request = json.loads(options['input_bytes']); self.after = True
            value = [{'outcome': self.outcome, 'complete': self.outcome == 200,
                'elapsed_ms': 10, 'sha256': 'a' * 64, 'bytes': 8, 'model': 'm'}]
        else:
            raise AssertionError(argv)
        return captured(value)

    def activation(self):
        cfg = {**fixture.BASE, 'probe_pod': 'probe', 'probe_container': 'probe',
            'selector': 'app=vllm', 'service_url': 'http://vllm.lab.svc:8000', 'deadline_ms': 20000}
        value = lab.Lab(cfg, runner=self.runner, clock=lambda: self.now)
        value.diagnostic_namespace = lab._diagnostic_namespace_identity(self.namespace, 'lab')
        value.diagnostic_poll = 1
        return value


@pytest.mark.parametrize('outcome', [200, 503, 'timeout', 'error'])
def test_direct_request_is_identity_bound_diagnostic_only(outcome):
    world = DiagnosticCluster(); world.outcome = outcome; activation = world.activation()
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    start = next(row for row in activation.diagnostics if row['event'] == 'direct-request-start')
    result = next(row for row in activation.diagnostics if row['event'] == 'direct-request-result')
    binding = next(row for row in activation.diagnostics if row['event'] == 'direct-request-binding')
    assert binding['bound'] is True and result['result']['outcome'] == outcome
    assert start['attribution'] == 'POD_ADDRESS_DIAGNOSTIC_ONLY'
    assert world.request['url'] == 'http://10.0.0.2:8000'
    item, = world.request['requests']
    assert hashlib.sha256(item['headers']['X-Request-Id'].encode()).hexdigest() == start['request_id_sha256']
    assert item['timeout'] <= .5
    assert all(options['timeout'] <= .5 for _, options in world.calls)
    assert activation.violations == activation.missing == activation.normal == []
    assert not any('authenticated' in key for row in activation.diagnostics for key in row)


@pytest.mark.parametrize('change', [
    lambda p: p['metadata'].update(uid='replaced'),
    lambda p: p['status']['containerStatuses'][0].update(containerID='containerd://new'),
    lambda p: p['status']['containerStatuses'][0].update(restartCount=1),
    lambda p: p['status']['containerStatuses'][0]['state']['running'].update(startedAt='later'),
    lambda p: p['status'].update(podIP='10.0.0.99'),
    lambda p: p['spec'].update(nodeName='other-node'),
])
def test_engine_or_probe_identity_change_unbinds_without_erasing_response(change):
    for changed_name in ('engine', 'probe'):
        world = DiagnosticCluster()
        world.after_change = lambda p: change(p) if p['metadata']['name'] == changed_name else None
        activation = world.activation()
        lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
        assert any(row['event'] == 'direct-request-result' for row in activation.diagnostics)
        assert next(row for row in activation.diagnostics if row['event'] == 'direct-request-binding')['bound'] is False
        assert activation.violations == activation.missing == []


def test_after_identity_fetch_error_preserves_response_and_raw_receipt():
    world = DiagnosticCluster(); world.after_error = True; activation = world.activation()
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert any(row['event'] == 'direct-request-result' for row in activation.diagnostics)
    assert any(row['event'] == 'direct-request-error' for row in activation.diagnostics)
    assert not any(row.get('bound') is True for row in activation.diagnostics)
    assert any(row['event'] == 'api-command' for row in activation.diagnostics)


@pytest.mark.parametrize('bad', [
    lambda w: w.service['spec']['ports'][0].update(port=80),
    lambda w: w.service['spec']['ports'][0].update(targetPort=80),
    lambda w: w.service['spec']['ports'][0].update(targetPort='8000'),
    lambda w: w.service['spec']['ports'][0].update(protocol='UDP'),
    lambda w: w.service['spec'].update(selector={'app': 'other'}),
    lambda w: w.service['metadata'].update(namespace='foreign'),
    lambda w: w.engine['metadata'].update(namespace='foreign'),
    lambda w: w.engine['metadata'].update(name='wrong'),
    lambda w: w.engine['status']['containerStatuses'][0].update(restartCount=False),
    lambda w: w.probe['status']['containerStatuses'].append(deepcopy(w.probe['status']['containerStatuses'][0])),
    lambda w: w.probe['status']['containerStatuses'][0].update(state={'waiting': {'reason': 'ContainerCreating'}}),
])
def test_invalid_binding_never_sends_direct_request(bad):
    world = DiagnosticCluster(); bad(world); activation = world.activation()
    lab._diagnostic_fault_pod_request(activation, [pod('engine', 'engine')], 20, 'm', idle_end=.5)
    assert world.request is None
    assert any(row['event'] == 'direct-request-unbound' for row in activation.diagnostics)
    assert activation.violations == activation.missing == []


@pytest.mark.parametrize('remaining', [0, .1, 5, 5.5])
def test_no_diagnostic_calls_when_service_budget_is_not_available(remaining):
    world = DiagnosticCluster(); activation = world.activation()
    lab._diagnostic_fault_pod_request(activation, [world.engine], remaining, 'm', idle_end=.5)
    assert world.calls == [] and world.request is None
    assert activation.diagnostics[-1]['reason'] == 'SERVICE_BUDGET_RESERVED'


def test_budget_expiration_during_snapshot_cannot_start_http():
    world = DiagnosticCluster(); activation = world.activation()
    original = activation.runner
    def runner(argv, **options):
        value = original(argv, **options); world.now += 1
        return value
    activation.runner = runner
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert len(world.calls) == 1 and world.request is None
    assert any(row['event'] == 'direct-request-error' for row in activation.diagnostics)
    assert activation.diagnostic_deadline is None
    assert activation.diagnostic_idle_overrun is True
    count = len(world.calls)
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=world.now + .5)
    assert len(world.calls) == count
    assert activation.diagnostics[-1]['reason'] == 'PRIOR_DIAGNOSTIC_IDLE_OVERRUN'


@pytest.mark.parametrize('change', [
    lambda c: c.update(complete=False), lambda c: c.update(bounded_at_source=False),
    lambda c: c.update(timed_out=True), lambda c: c.update(truncated=True),
    lambda c: c.update(pipes_closed=False), lambda c: c.update(read_errors=['stderr']),
    lambda c: c.pop('pipes_closed'), lambda c: c.update(read_errors=None),
    lambda c: c['streams']['stdout'].update(retained_bytes=False),
    lambda c: c['streams']['stdout'].update(retained_sha256='0' * 64),
    lambda c: c['streams']['stdout'].update(observed_bytes=100000),
])
def test_conflicting_or_missing_capture_fields_cannot_claim_binding(change):
    world = DiagnosticCluster(); activation = world.activation(); original = activation.runner
    def runner(argv, **options):
        result = original(argv, **options); change(result['capture']); return result
    activation.runner = runner
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert world.request is None and not any(row.get('bound') is True for row in activation.diagnostics)


@pytest.mark.parametrize('change', [
    lambda p: p['metadata'].update(uid='namespace-replaced'),
    lambda p: p['metadata'].update(deletionTimestamp='2026-11-03T00:00:00Z'),
    lambda p: p['metadata']['labels'].update({lab.OWNED_LABEL: 'false'}),
])
def test_namespace_generation_change_or_deletion_never_binds(change):
    world = DiagnosticCluster(); activation = world.activation()
    world.after_change = lambda p: change(p) if p['metadata']['name'] == 'lab' else None
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert next(row for row in activation.diagnostics if row['event'] == 'direct-request-binding')['bound'] is False


def test_namespace_baseline_is_recorded_by_existing_ownership_check():
    world = DiagnosticCluster(); activation = world.activation(); activation.diagnostic_namespace = None
    activation.verify_owned()
    assert activation.diagnostic_namespace == {'namespace': 'lab', 'namespace_uid': 'namespace-generation-1'}
    world.namespace['metadata']['uid'] = 'namespace-replaced'
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert world.request is None
    assert any(row['event'] == 'direct-request-unbound' for row in activation.diagnostics)


@pytest.mark.parametrize('ip', ['::ffff:127.0.0.1', '::ffff:169.254.169.254', '::1', 'fe80::1'])
def test_local_or_mapped_local_addresses_are_not_direct_targets(ip):
    world = DiagnosticCluster(); world.engine['status']['podIP'] = ip; activation = world.activation()
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert world.request is None


def test_ipv6_uses_brackets_and_same_generation_binding():
    world = DiagnosticCluster(); world.engine['status']['podIP'] = '2001:db8::2'; activation = world.activation()
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert world.request['url'] == 'http://[2001:db8::2]:8000'
    assert next(row for row in activation.diagnostics if row['event'] == 'direct-request-binding')['bound'] is True


@pytest.mark.parametrize('value', [True, 1, 'false'])
def test_host_network_or_untyped_host_network_never_claims_pod_address_scope(value):
    world = DiagnosticCluster(); world.engine['spec']['hostNetwork'] = value; activation = world.activation()
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert world.request is None and world.calls == []


def test_incomplete_pipe_capture_cannot_bind_a_diagnostic_request():
    world = DiagnosticCluster(); activation = world.activation(); original = activation.runner
    def runner(argv, **options):
        value = original(argv, **options)
        value['capture']['complete'] = False; value['capture']['truncated'] = True
        return value
    activation.runner = runner
    lab._diagnostic_fault_pod_request(activation, [world.engine], 20, 'm', idle_end=.5)
    assert world.request is None
    assert any(row['event'] == 'object-api-receipt' and row['capture']['truncated'] is True
        for row in activation.diagnostics)
    assert not any(row.get('bound') is True for row in activation.diagnostics)


def test_diagnostic_metadata_hashes_raw_without_copying_canary_or_substrings(tmp_path):
    activation = DiagnosticCluster().activation()
    secret = b'PRIVATE_CANARY_SECRET_82bf6b9a_DO_NOT_EXPORT'
    receipt = activation.diagnostic_stream_metadata(secret)
    activation.diagnostic('marker-command', stdout=receipt, stderr=activation.diagnostic_stream_metadata(secret))
    activation.diagnostic('api-command', result=lab._diagnostic_http_metadata({
        'outcome': 200, 'complete': True, 'sha256': hashlib.sha256(secret).hexdigest(),
        'body': secret.decode(), 'metrics': secret.decode(), 'model': secret.decode(), 'answer': secret.decode()}))
    artifacts = activation.write_diagnostics(tmp_path)
    assert receipt == {'sha256': hashlib.sha256(secret).hexdigest(), 'bytes': len(secret)}
    assert len(artifacts) == 1 and artifacts[0].name == 'lab-diagnostics.json'
    raw = (tmp_path / 'lab-diagnostics.json').read_bytes()
    assert all(secret[index:index + 8] not in raw for index in range(len(secret) - 7))
    assert not list(tmp_path.glob('*.bin'))
    assert json.loads(raw)['metadata_only'] is True


def test_diagnostic_write_failure_preserves_independent_fail_facts(tmp_path, monkeypatch):
    activation = DiagnosticCluster().activation()
    activation.diagnostic('record', stdout=activation.diagnostic_stream_metadata(b'raw'))
    activation.violations.append({'code': 'PARTIAL_INITIALIZATION_REPORTED_READY'})
    activation.missing.append('FAULTY_REQUEST_GENERATION_UNATTRIBUTED')
    facts = {'activation_steps': activation.steps, 'activation_missing': activation.missing,
        'activation_violations': activation.violations, 'activation_scope_complete': False}
    monkeypatch.setattr(lab, 'run_scenario', lambda *a, **kw: (facts, activation))
    original = Path.write_bytes
    def write(path, data):
        if path.name.startswith('lab-diagnostic'):
            raise OSError('diagnostic storage unavailable')
        return original(path, data)
    monkeypatch.setattr(Path, 'write_bytes', write)
    draft = lab.ActivatedExecutor(fixture.Passive(), fixture.BASE, runner=object()).run(
        SimpleNamespace(id='LIF-09', challenge_steps=['fault']), None, {}, None, tmp_path)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert draft.observations['activation_violations'].value == activation.violations
    assert draft.observations['activation_missing'].value == ['FAULTY_REQUEST_GENERATION_UNATTRIBUTED']
    primary = json.loads((tmp_path / 'lab-activation.json').read_bytes())
    assert primary['activation_violations'] == activation.violations
    assert 'lab_diagnostics' not in draft.config
    assert draft.cleanup_errors == ['LAB_ACTIVATION_DIAGNOSTIC_ARTIFACT_UNAVAILABLE']
    assert primary['activation_steps'] == draft.observations['activation_steps'].value
    assert activation.steps == []


def test_real_factory_core_run_serializes_write_failure_with_same_config_hash_and_fail(tmp_path, monkeypatch):
    from datetime import timedelta
    from guard.core.judge import judge_plan
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.executors import registry
    from guard.executors.kubernetes_initialization import KubernetesInitializationExecutor
    from guard.models import Verdict, canonical_json, load_pack, sha256_hex
    from guard.store import Store
    from tests.builders import T0, environment, policy

    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    check = pack.checks['LIF-09']
    env = environment()
    values = {'init_environment_sha256': env.ref().sha256, 'init_target_sha256': 'a' * 64,
        'init_prepared_sha256': 'b' * 64, 'init_validity_start': (T0 - timedelta(minutes=1)).isoformat(),
        'init_validity_end': (T0 + timedelta(minutes=1)).isoformat(), 'init_executor_backend': 'mp',
        'init_expected_ranks': ['0'], 'init_scenario_sha256': 'c' * 64, 'init_phase': 'partial-failure',
        'init_required_stages': ['model-load'], 'init_failure_rank': 0, 'init_failure_stage': 'model-load',
        'init_preparation_deadline_ms': 10000, 'init_recovery_deadline_ms': 10000}
    pol = policy(pack.ref(), scope={'include': [check.id], 'exclude': []}, values={check.id: values})
    # Real registered factory/core/store/judge; only native transport/scenario are EXAMPLE.
    original_build = registry.build_executor
    built = []
    def build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        assert isinstance(result, lab.ActivatedExecutor) and isinstance(result.child, KubernetesInitializationExecutor)
        result.runner = object()  # explicit EXAMPLE injection, never MEASURED
        built.append(result)
        return result
    monkeypatch.setattr(registry, 'build_executor', build)
    monkeypatch.setattr(KubernetesInitializationExecutor, 'run',
        lambda self, *args: fixture.Passive().run(*args))
    original_write = Path.write_bytes
    cases = []
    for fails in (False, True):
        activation = DiagnosticCluster().activation(); activation.normal.append(True)
        activation.diagnostic('poll-start')
        activation.violations.append({'code': 'PARTIAL_INITIALIZATION_REPORTED_READY'})
        activation.missing.append('FAULTY_REQUEST_GENERATION_UNATTRIBUTED')
        facts = {'activation_steps': activation.steps, 'activation_violations': activation.violations,
            'activation_missing': activation.missing, 'activation_scope_complete': False}
        monkeypatch.setattr(lab, 'run_scenario', lambda *args, **kwargs: (facts, activation))
        def write(path, data):
            if fails and path.name == 'lab-diagnostics.json':
                raise OSError('PRIVATE_WRITE_ERROR_CANARY_NOT_FOR_EVD')
            return original_write(path, data)
        monkeypatch.setattr(Path, 'write_bytes', write)
        store = Store(tmp_path / ('failed' if fails else 'normal'))
        store.put_many([env, pol]); plan = select(pack, [env], pol); store.put(plan)
        assert plan.item(check.id).selected is True
        evd, = run_plan(store, pack, plan, RunConfig(executors={'initialization': 'kubernetes'},
            raw={'lab_activation': deepcopy(fixture.BASE)}))
        restored = store.resolve(evd.ref())
        result, = judge_plan(store, pack, plan, as_of=T0)
        assert restored.execution.status == ExecStatus.COMPLETED and restored.data_origin == DataOrigin.EXAMPLE
        assert restored.observations['activation_missing'].value == ['FAULTY_REQUEST_GENERATION_UNATTRIBUTED']
        assert result.verdicts.security == Verdict.FAIL
        expected = ['LAB_ACTIVATION_DIAGNOSTIC_ARTIFACT_UNAVAILABLE'] if fails else []
        assert restored.execution.cleanup_errors == expected
        assert 'PRIVATE_WRITE_ERROR_CANARY' not in canonical_json(restored.to_dict()).decode()
        primary, = [a for a in evd.artifacts if a.name == 'lab-activation.json']
        primary_path = store.root / primary.path
        document = json.loads(primary_path.read_bytes())
        assert document['activation_steps'] == restored.observations['activation_steps'].value
        expected_config = {'lab_activation': deepcopy(fixture.BASE)}
        assert restored.execution.executor.config_sha256 == sha256_hex(canonical_json(expected_config))
        cases.append(restored)
    assert len(built) == 2
    assert cases[0].execution.executor.config_sha256 == cases[1].execution.executor.config_sha256


@pytest.mark.parametrize('mode', ['current', 'previous', 'absent', 'log-error'])
def test_poll_marker_raw_and_error_receipts_do_not_change_existing_verdict(mode, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); (tmp_path / 'faulty.yaml').write_text('faulty')
    world = fixture.Cluster(faulty_ready=mode == 'current', old_container_marker=mode == 'previous',
        no_marker=mode == 'absent')
    def runner(argv, **options):
        if mode == 'log-error' and 'logs' in argv:
            return {'returncode': 1, 'stdout_bytes': b'partial', 'stderr_bytes': b'log unavailable'}
        return world(argv, **options)
    activation = lab.Lab(fixture.BASE, runner=runner, clock=world.clock, wait=world.wait)
    lab._rejected(activation, 'PARTIAL_INITIALIZATION_REPORTED_READY', fixture.BASE['failure_marker'],
        require_new_faulty_uid=True)
    assert any(row['event'] == 'poll-start' for row in activation.diagnostics)
    assert any(row['event'] == 'marker-before' for row in activation.diagnostics)
    raw = next(row for row in activation.diagnostics if row['event'] == 'marker-command')
    if mode == 'log-error':
        assert raw['returncode'] == 1 and raw['stderr']['sha256'] == hashlib.sha256(b'log unavailable').hexdigest()
    assert bool(activation.violations) is (mode == 'current')
    assert 'FAULTY_REQUEST_GENERATION_UNATTRIBUTED' in activation.missing
    assert not any(row.get('bound') is True for row in activation.diagnostics)


def test_service_success_and_same_fault_pod_timeout_remain_separate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); (tmp_path / 'faulty.yaml').write_text('faulty')
    world = fixture.Cluster(); original_pods = world.pods
    def pods():
        values = deepcopy(original_pods())
        for value in values:
            value['metadata']['namespace'] = 'lab'; value['metadata']['labels']['app'] = 'vllm'
            value['spec']['nodeName'] = 'node-1'
            status = value['status']['containerStatuses'][0]
            status['containerID'] = 'containerd://' + hashlib.sha256(status['containerID'].encode()).hexdigest()
        return values
    world.pods = pods
    def runner(argv, **options):
        if 'get' in argv and 'namespace' in argv:
            result = captured(DiagnosticCluster().namespace)
        elif 'get' in argv and 'pod' in argv:
            name = argv[argv.index('pod') + 1]
            result = world.result(pod('probe', 'probe') if name == 'probe' else pods()[0])
        elif 'get' in argv and 'service' in argv:
            result = world.result(DiagnosticCluster().service)
        elif lab.CLIENT in argv and json.loads(options['input_bytes'])['url'].startswith('http://10.'):
            result = world.result([{'outcome': 'timeout', 'complete': False, 'elapsed_ms': 100}])
        else:
            result = world(argv, **options)
        return captured(result['stdout_bytes'])
    activation = lab.Lab(fixture.BASE, runner=runner, clock=world.clock, wait=world.wait)
    activation.verify_owned()
    lab._rejected(activation, 'PARTIAL_INITIALIZATION_REPORTED_READY', fixture.BASE['failure_marker'],
        require_new_faulty_uid=True)
    assert any(row['event'] == 'service-request' and row['result']['outcome'] == 200 for row in activation.diagnostics)
    assert any(row['event'] == 'direct-request-result' and row['result']['outcome'] == 'timeout' for row in activation.diagnostics)
    assert next(row for row in activation.steps if row['step'] == 'faulty-generation')['served_models'] == ['m']
    assert activation.violations == [] and 'FAULTY_REQUEST_GENERATION_UNATTRIBUTED' in activation.missing


@pytest.mark.parametrize('narrow_ready', [True, False])
def test_optional_work_uses_original_idle_and_keeps_service_cadence_and_narrow_fail(narrow_ready, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); (tmp_path / 'faulty.yaml').write_text('faulty')
    def run(optional_duration):
        world = fixture.Cluster(); world.clock = lambda: world.now
        world.ready = lambda: world.manifest != 'faulty' or narrow_ready and .4 <= world.now < .6
        services = []; original = world.__call__
        def runner(argv, **options):
            if lab.CLIENT in argv:
                services.append(world.now)
            return original(argv, **options)
        def optional(*args, **kwargs):
            assert kwargs['idle_end'] == world.now + .5
            world.now += optional_duration
        monkeypatch.setattr(lab, '_diagnostic_fault_pod_request', optional)
        activation = lab.Lab({**fixture.BASE, 'deadline_ms': 1200}, runner=runner,
            clock=world.clock, wait=world.wait)
        lab._rejected(activation, 'PARTIAL_INITIALIZATION_REPORTED_READY', fixture.BASE['failure_marker'],
            require_new_faulty_uid=True)
        return services, activation.violations, activation.missing
    baseline = run(0)
    with_optional = run(.5)
    assert with_optional == baseline
    if narrow_ready:
        assert baseline[0] == [0.0]
        assert baseline[1] == [{'code': 'PARTIAL_INITIALIZATION_REPORTED_READY'}]
    else:
        assert baseline[0] == [0.0, .5, 1.0]


def test_log_stderr_and_http_canaries_never_enter_draft_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ('faulty', 'good'):
        (tmp_path / (name + '.yaml')).write_text(name)
    secret = b'PRIVATE_RANDOM_CANARY_c1cd84d9f024_DO_NOT_EXPORT'
    world = fixture.Cluster()
    def runner(argv, **options):
        result = world(argv, **options)
        if 'logs' in argv:
            result['stdout_bytes'] += secret
            result['stderr_bytes'] = secret
        elif lab.CLIENT in argv:
            rows = json.loads(result['stdout_bytes'])
            for row in rows:
                row['body'] = secret.decode(); row['metrics'] = secret.decode()
            result['stdout_bytes'] = json.dumps(rows).encode()
        return result
    activation = lab.ActivatedExecutor(fixture.Passive(), fixture.BASE, runner=runner,
        clock=world.clock, wait=world.wait)
    draft = activation.run(SimpleNamespace(id='LIF-09', challenge_steps=['fault']), None, {}, None, tmp_path / 'work')
    assert any(item.name == 'lab-diagnostics.json' for item in draft.artifacts)
    assert not any(item.name.endswith('.bin') for item in draft.artifacts)
    for item in draft.artifacts:
        raw = item.path.read_bytes()
        assert all(secret[index:index + 8] not in raw for index in range(len(secret) - 7))
    assert secret.decode() not in str(draft.config)


def test_existing_client_producer_keeps_unique_header_and_response_hash_with_url_override():
    received = {}
    secret = 'PRIVATE_HTTP_BODY_CANARY_729a53ac_DO_NOT_EXPORT'
    body = json.dumps({'model': 'm', 'choices': [{'message': {'content': secret}}]}).encode()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received['id'] = self.headers.get('X-Request-Id')
            received['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        server.timeout = 3
        thread = threading.Thread(target=server.handle_request, daemon=True); thread.start()
        activation = DiagnosticCluster().activation()
        item = activation.chat(headers={'X-Request-Id': 'diagnostic-contract'}, timeout=2)
        try:
            result = lab.capture_pipes([sys.executable, '-c', lab.CLIENT],
                input_bytes=json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'requests': [item]}).encode(),
                env_extra={}, timeout=5, max_stdout_bytes=1024 ** 2, max_stderr_bytes=4096)
        finally:
            thread.join(timeout=3)
    assert lab._diagnostic_capture_complete(result) is True
    row, = json.loads(result['stdout_bytes'])
    assert received['id'] == 'diagnostic-contract' and received['body']['max_tokens'] == 1
    assert row['outcome'] == 200 and row['complete'] is True
    assert row['sha256'] == hashlib.sha256(body).hexdigest()
    metadata = json.dumps(lab._diagnostic_http_metadata(row))
    assert all(secret[index:index + 8] not in metadata for index in range(len(secret) - 7))
