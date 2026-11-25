"""Versioned worker coverage fixes; legacy /1 remains byte-compatible."""
from copy import deepcopy
import hashlib

import pytest

from guard.adapters import vllm_consumption_callback as callback
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.collectors import scoped_consumption as scoped, gpu_common_inputs as legacy, engine_callbacks
from tests.test_gpu_input_producers import api_rows, POD, R1, SCOPE
from tests.test_runner_consumption import worker_rows


def native(processes):
    """Explicit EXAMPLE qualification metadata; never an engine measurement."""
    for process in processes:
        rows = []
        for index, raw in enumerate(process['events']):
            row = deepcopy(raw)
            module = callback.INPUT if row['point'].startswith('InputProcessor.') else callback.RUNNER
            row.update(process_sha256=hashlib.sha256(str(process['pid']).encode()).hexdigest(),
                       call_sha256=hashlib.sha256(str(index).encode()).hexdigest(),
                       entry_code_sha256='a' * 64, source_file_sha256=callback.cache.base.SOURCE_SHA[module],
                       session_sha256=hashlib.sha256(SCOPE['session'].encode()).hexdigest(), runtime_context_injected=False)
            if 'rank' in process:
                row['consumption_scope'] = {'rank': process['rank'], 'admissions': {R1: process.get('admission', 'b' * 64)}, 'pid': process['pid'], 'start_ticks': process['start_ticks']}
            else:
                row['consumption_scope'] = {'pid': process['pid'], 'start_ticks': process['start_ticks']}
            rows.append(row)
            # Existing API fixture pairs calls. Worker fixture explicitly holds
            # CALL-only rows; add its EXAMPLE successful terminal separately.
            if module == callback.RUNNER:
                rows.append(dict(row, phase='RETURN', fields={}, operation_finished_ns=row['operation_started_ns'] + 1))
        # Match the original API CALL to its terminal (fixture keys are cN).
        if process['pid'] == 1:
            calls = {}
            for row in rows:
                key = row['point']
                if row['phase'] == 'CALL': calls[key] = row['call_sha256']
                else: row['call_sha256'] = calls[key]
        process['events'] = rows
    return processes


EXAMPLE_SCOPE = {'engine': 'vllm', 'engine_version': '0.31.0', 'image_digest': 'EXAMPLE',
                 'backend': 'EXAMPLE', 'tp_size': 2, 'origin': 'EXAMPLE'}


def capture(**kwargs):
    processes = native([{'pid': 1, 'start_ticks': 1, 'events': api_rows()},
                        {'pid': 112, 'start_ticks': 2, 'rank': 0, 'events': worker_rows(logit_bias=0)},
                        {'pid': 113, 'start_ticks': 3, 'rank': 1, 'events': worker_rows(logit_bias=0)}])
    return scoped.record(processes, pod=POD, qualification_scope=EXAMPLE_SCOPE, expected_requests=[R1], expected_stage_ranks={'runner_received': [0, 1], 'sampler_applied': [0, 1]}, **SCOPE, **kwargs)


def register(monkeypatch):
    p = scoped.producer()
    monkeypatch.setitem(scoped.SUPPORTED_PRODUCERS, (p['name'], p['version']), p['source_sha256'])
    monkeypatch.setattr(scoped, 'QUALIFICATIONS', (scoped.Qualification('EXAMPLE', scoped.SCHEMA, p['source_sha256'], **EXAMPLE_SCOPE,
        stage_ranks=(('runner_received', (0, 1)), ('sampler_applied', (0, 1)))),))


def test_two_identical_workers_are_preserved_and_each_rank_has_closed_calls(monkeypatch):
    register(monkeypatch)
    record, result = capture()
    workers = [r for r in record['events'] if r['event'] in ('runner_received', 'sampler_applied')]
    assert len(workers) == 4
    assert {record['bindings'][r['binding']]['rank'] for r in workers} == {0, 1}
    assert result['complete'] and result['violation_candidates'] == []
    assert legacy.validate(record, kind='runner-consumption', **SCOPE) == result


@pytest.mark.parametrize('damage', ['missing-worker', 'missing-sampler', 'rank-unknown', 'attempt-mismatch', 'missing-plan', 'later-error'])
def test_missing_worker_rank_admission_and_source_end_cannot_be_complete(monkeypatch, damage):
    register(monkeypatch); value, _ = capture()
    if damage in ('missing-worker', 'missing-sampler'):
        value['events'] = [r for r in value['events'] if not (value['bindings'][r['binding']]['worker_pid'] == 113
                           and (damage == 'missing-worker' or r['event'] == 'sampler_applied'))]
        for index, row in enumerate(value['events']): row['seq'] = index
    elif damage == 'rank-unknown': value['bindings'][-1]['rank'] = None
    elif damage == 'attempt-mismatch': value['events'][-1]['admission_sha256'] = 'f' * 64
    elif damage == 'missing-plan': value['expected_stage_ranks'] = None
    else: value['completeness']['source_ended'] = False
    assert not scoped.validate(value, **SCOPE)['complete']


def test_changed_worker_option_survives_missing_another_rank_and_later_error(monkeypatch):
    register(monkeypatch); value, _ = capture()
    row = next(r for r in value['events'] if r['event'] == 'sampler_applied')
    row['top_k'] += 1
    value['completeness']['source_ended'] = False
    value['expected_stage_ranks']['sampler_applied'].append(2)
    result = scoped.validate(value, **SCOPE)
    assert not result['complete']
    assert any(v['field'] == 'top_k' for v in result['violation_candidates'])


def test_schema_v2_writer_does_not_use_mutable_legacy_schema(monkeypatch):
    identity = scoped.producer()
    monkeypatch.setattr(legacy, 'SCHEMA', 'guard-gpu-common-input/999')
    value, _ = capture()
    assert value['schema'] == 'guard-gpu-common-input/2' and value['producer'] == identity


def test_new_captures_are_unregistered_and_do_not_change_legacy_registration():
    before = deepcopy(legacy.SUPPORTED_PRODUCERS)
    _, result = capture()
    assert not result['supported'] and not result['complete']
    assert legacy.SUPPORTED_PRODUCERS == before and len(before) == 8


def test_prepared_sensor_selects_scoped_observers_without_overwriting_legacy_flag(tmp_path):
    prepared = engine_callbacks.prepare_assets(tmp_path / 'assets', scoped_runner_consumption=True)
    assert prepared['scoped_runner_consumption'] and not prepared['runner_consumption']
    callbacks = sensor.EngineCallbacks('a' * 32, b'k' * 32, lambda row: None, scoped_runner_consumption=True,
                                       context_provider=lambda: {'pid': 1, 'fixture': True})
    assert isinstance(callbacks.consumption, sensor.ScopedConsumptionObserver)
    with pytest.raises(ValueError, match='SCOPED_CONSUMPTION_EXCLUSIVE'):
        engine_callbacks.prepare_assets(tmp_path / 'bad', scoped_runner_consumption=True, runner_consumption=True)


@pytest.mark.parametrize('module_name', [callback.INPUT, sensor.consumption_v2.RUNNER])
def test_mounted_scoped_observers_accept_reviewed_baseline_methods_only_in_scoped_mode(module_name):
    from pathlib import Path
    from types import CodeType, FunctionType, ModuleType

    fixture = Path(__file__).parent / 'fixtures' / 'vllm-0.30.0'
    source = fixture / (module_name.replace('.', '/') + '.py.txt')
    raw = source.read_bytes()
    expected = SCOPED_RELEASES['0.30.0'][1 if module_name == callback.INPUT else 2]
    assert hashlib.sha256(raw).hexdigest() == expected
    points = (callback.TARGETS if module_name == callback.INPUT else sensor.consumption_v2.TARGETS)[module_name]

    # Build only the exact code objects needed by the installer. No engine
    # module initialization or method body runs for this fixture.
    module = ModuleType(module_name)
    module.__file__ = str(source)
    code = compile(raw, str(source), 'exec', dont_inherit=True, optimize=0)
    owner_name = points[0].split('.')[0]
    owner_code = next(child for child in code.co_consts
                      if type(child) is CodeType and child.co_qualname == owner_name)
    codes = {child.co_qualname: child for child in owner_code.co_consts if type(child) is CodeType}
    methods = {point.split('.')[1]: FunctionType(codes[point], vars(module)) for point in points}
    owner = type(owner_name, (), methods)
    setattr(module, owner_name, owner)

    def offline_callbacks(**options):
        return sensor.EngineCallbacks('a' * 32, b'k' * 32, lambda row: None,
                                      context_provider=lambda: {'EXAMPLE': True}, **options)

    legacy = offline_callbacks(runner_consumption=True)
    assert legacy.instrument(module) is False
    assert all(vars(owner)[method] is original for method, original in methods.items())

    mounted = offline_callbacks(scoped_runner_consumption=True)
    assert mounted.instrument(module) is True
    observer = mounted.consumption if module_name == callback.INPUT else mounted.consumption_v2
    assert observer._source_pins[module_name] == expected
    assert all(vars(owner)[method].__wrapped__ is original for method, original in methods.items())


def test_scoped_observer_preserves_actual_rank_and_changes_admission_only_after_native_return(monkeypatch):
    from guard.adapters import vllm_scoped_consumption_callback as native
    rows = []
    observer = native.ScopedConsumptionObserver('a' * 32, context_provider=lambda: {'EXAMPLE': True}, sink=rows.append)
    monkeypatch.setattr(native, '_tp_rank', lambda: 1)
    monkeypatch.setattr(native, '_start_ticks', lambda: 123)
    call = {'schema': callback.SCHEMA, 'point': 'GPUModelRunner._update_states', 'phase': 'CALL',
            'call_sha256': 'b' * 64, 'fields': {'received': [{'request_sha256': R1}]}}
    observer._emit(call)
    assert rows[-1]['consumption_scope']['admissions'] == {}
    observer._emit({**call, 'phase': 'RETURN', 'fields': {}})
    first = rows[-1]['consumption_scope']
    assert first['rank'] == 1 and first['start_ticks'] == 123 and first['admissions'][R1]
    observer._emit({**call, 'call_sha256': 'c' * 64})
    observer._emit({**call, 'call_sha256': 'c' * 64, 'phase': 'RAISE'})
    assert rows[-1]['consumption_scope']['admissions'][R1] == first['admissions'][R1]
    observer._emit({**call, 'call_sha256': 'd' * 64})
    observer._emit({**call, 'call_sha256': 'd' * 64, 'phase': 'RETURN'})
    assert rows[-1]['consumption_scope']['admissions'][R1] != first['admissions'][R1]


@pytest.mark.parametrize('damage', ['missing-request', 'narrowed-ranks', 'unknown-generation'])
def test_planned_scope_cannot_be_narrowed_to_the_rows_that_survived(monkeypatch, damage):
    register(monkeypatch)
    value, _ = capture()
    if damage == 'missing-request':
        value['expected_requests'].append('f' * 64)
    elif damage == 'narrowed-ranks':
        value['expected_stage_ranks']['runner_received'] = [0]
    else:
        value['bindings'][-1]['start_ticks'] = None
    assert not scoped.validate(value, **SCOPE)['complete']


# Independently reviewed source identities, with synthetic calls and values.
SCOPED_RELEASES = {
    '0.30.0': (
        'vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40',
        'c8abd8e4e14d99929d45a7ff3b2a74898269c5e2a7fa30b826623ee85e4ce813',
        '174c93db921c23cf0396eee4764be25b2bd2d4b6a06e9fa41ce3598b884ce8ce',
    ),
    '0.31.0': (
        'docker.io/vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b',
        'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
        '725c47114e148ade3a26b4cc9c5f7295588fdd387afc3cb5fdb2d16513a1bb5c',
    ),
}


def _reviewed_api_rows(rows, api_pin, *, label='input'):
    calls = {row['point']: row for row in rows if row['phase'] == 'CALL'}
    ids = {point: hashlib.sha256((label + point).encode()).hexdigest() for point in calls}
    parent = ids['InputProcessor.process_inputs']
    for row in rows:
        point = row['point']
        row.update(source_file_sha256=api_pin, call_sha256=ids[point],
                   parent_call_sha256=None if point == 'InputProcessor.process_inputs' else parent,
                   operation_started_ns=calls[point]['operation_started_ns'])
    return rows


@pytest.mark.parametrize('version', SCOPED_RELEASES)
@pytest.mark.parametrize('change', [None, 'other-worker', 'other-return', 'other-image'])
def test_reviewed_v2_scoped_capture_projects_each_release_and_its_own_rank_calls(version, change):
    from guard.adapters import vllm_consumption_v2_callback as v2

    processes = native([{'pid': 1, 'start_ticks': 1, 'events': api_rows()},
                        {'pid': 112, 'start_ticks': 2, 'rank': 0, 'events': worker_rows(logit_bias=0)},
                        {'pid': 113, 'start_ticks': 3, 'rank': 1, 'events': worker_rows(logit_bias=0)}])
    image, api_pin, worker_pin = SCOPED_RELEASES[version]
    other_image, _, other_worker_pin = next(value for release, value in SCOPED_RELEASES.items() if release != version)
    _reviewed_api_rows(processes[0]['events'], api_pin)
    points = {'GPUModelRunner._update_states': 'GPUModelRunner.add_requests',
              'GPUModelRunner._sample': 'GPUModelRunner.sample'}
    for worker in processes[1:]:
        for row in worker['events']:
            pin = worker_pin
            if worker['rank'] == 1 and (change == 'other-worker' or change == 'other-return' and row['phase'] == 'RETURN'):
                pin = other_worker_pin
            row.update(schema=v2.SCHEMA, point=points[row['point']], source_file_sha256=pin)
    scope = {**EXAMPLE_SCOPE, 'engine_version': version,
             'image_digest': other_image if change == 'other-image' else image}
    options = dict(pod=POD, qualification_scope=scope, expected_requests=[R1], reviewed_scoped_sources=True,
                   expected_stage_ranks={'runner_received': [0, 1], 'sampler_applied': [0, 1]}, **SCOPE)
    if change == 'other-image':
        with pytest.raises(legacy.GpuInputError, match='^GPU_INPUT_SCOPED_SOURCE_SCOPE_UNREVIEWED$'):
            scoped.record(processes, **options)
        return
    record, validation = scoped.record(processes, **options)
    assert [row['event'] for row in record['events'] if record['bindings'][row['binding']]['role'] == 'api'] == ['api_validated']
    ranks = [0, 1] if change is None else [0]
    for stage in ('runner_received', 'sampler_applied'):
        assert [record['bindings'][row['binding']]['rank'] for row in record['events'] if row['event'] == stage] == ranks
    assert record['completeness']['diagnostic_codes'] == (['NATIVE_CALL_UNQUALIFIED'] if change else [])
    assert record['completeness']['source_ended'] is False
    assert not validation['supported'] and not validation['complete']


@pytest.mark.parametrize('version', SCOPED_RELEASES)
@pytest.mark.parametrize('validated_parent', [True, False])
def test_reviewed_api_validation_stays_with_its_recorded_parent(version, validated_parent):
    image, api_pin, _ = SCOPED_RELEASES[version]
    first, second = native([{'pid': 1, 'start_ticks': 1, 'events': api_rows()} for _ in range(2)])
    first_rows = _reviewed_api_rows(first['events'], api_pin, label='first')
    second_rows = _reviewed_api_rows(second['events'], api_pin, label='second')
    # The first API invocation is still in flight when a second normal call
    # reuses the outer HMAC. Each has distinct closed validation calls.
    first_rows[-1]['operation_finished_ns'] = 20
    for row in second_rows:
        row['operation_started_ns'] += 6
        row['operation_finished_ns'] += 6
    for rows, temperature in ((first_rows, 125), (second_rows, 875)):
        params = next(row for row in rows if row['point'] == 'InputProcessor._validate_params' and row['phase'] == 'CALL')
        params['fields']['params']['temperature_milli'] = temperature
    if not validated_parent:
        for row in second_rows[1:-1]:
            row['parent_call_sha256'] = None

    process = {**first, 'events': first_rows[:-1] + second_rows + first_rows[-1:]}
    scope = {**EXAMPLE_SCOPE, 'engine_version': version, 'image_digest': image}
    record, validation = scoped.record([process], pod=POD, expected_requests=[R1],
                                       reviewed_scoped_sources=True, qualification_scope=scope, **SCOPE)
    actual = [(row['source_call']['call_sha256'], row['temperature_milli']) for row in record['events']]
    first_result = (first_rows[0]['call_sha256'], 125)
    second_result = (second_rows[0]['call_sha256'], 875)
    assert actual == ([second_result, first_result] if validated_parent else [first_result])
    assert record['completeness']['diagnostic_codes'] == (
        [] if validated_parent else ['API_VALIDATION_UNOBSERVED', 'NATIVE_API_VALIDATION_NOT_LINKED'])
    assert record['completeness']['source_ended'] is False
    assert validation['complete'] is validation['producer_authenticated'] is False
