"""EXAMPLE pages use the same paired scoped producer collection entry."""
from copy import deepcopy
from functools import partial
import hmac
import json
from pathlib import Path

import pytest

from guard.adapters import vllm_runner_capture
from guard.adapters import vllm_consumption_callback as v1, vllm_consumption_v2_callback as v2
from guard.adapters.vllm import VllmAdapter
from guard.adapters.vllm_scoped_consumption_callback import ASSIGNMENT_SCHEMA, COMPLETION_SELECTION_SCHEMA
from guard.adapters.vllm_observer_pin_rows import ROWS
from guard.collectors import engine_callbacks
from guard.executors import common_consumption
from guard.executors.kubernetes_sampling import (
    NORMAL_FIXTURE, NORMAL_LABEL, NORMAL_OPTION, NORMAL_OPTIONS, KubernetesSamplingExecutor,
)
from guard.models import canonical_json, load_pack, sha256_hex
from guard.runtime import engine_http
from tests.test_gpu_input_producers import R1, api_rows
from tests.test_gpu_a_normal_tp2_callbacks import _stage, _owned_pods, _environment, _target
from tests.test_kubernetes_sampling import APPROVED_NORMAL, normal_sampling_inputs
from tests.test_runner_consumption import worker_rows
from tests.test_scoped_consumption import _reviewed_api_rows, native
from tools import gpu_a_normal_tp2_callbacks as passive
from tools import gpu_a_producers_collect as collect_tool
from tools.mvp_versions import versions


def _ordinary_rows(side, session, *, other_worker_source):
    # Inert closed callback rows, not results from a running engine.
    processes = native([
        {'pid': 1, 'start_ticks': 1, 'events': api_rows()},
        {'pid': 112, 'start_ticks': 2, 'rank': 0, 'events': worker_rows(logit_bias=0)},
        {'pid': 113, 'start_ticks': 3, 'rank': 1, 'events': worker_rows(logit_bias=0)},
    ])
    api_pins = {'baseline': ROWS[v1.INPUT]['sha256'], 'candidate': v1.cache.base.SOURCE_SHA[v1.INPUT]}
    _reviewed_api_rows(processes[0]['events'], api_pins[side])
    runner_pins = {'baseline': ROWS[v2.RUNNER]['sha256'], 'candidate': v2.SOURCES[v2.RUNNER]}
    opposite = 'candidate' if side == 'baseline' else 'baseline'
    points = {'GPUModelRunner._update_states': 'GPUModelRunner.add_requests',
              'GPUModelRunner._sample': 'GPUModelRunner.sample'}
    for process in processes:
        for row in process['events']:
            row['session_sha256'] = sha256_hex(session.encode())
            if row['point'] in points:
                row['point'] = points[row['point']]
                row['schema'] = v2.SCHEMA
                used = opposite if process['pid'] == 113 and other_worker_source else side
                row['source_file_sha256'] = runner_pins[used]
    return processes


def _native_request_link_rows(side, session, response_sha256):
    """Build inert source-pinned selection, assignment and worker rows."""
    processes = _ordinary_rows(side, session, other_worker_source=False)
    api = processes[0]
    sources = vllm_runner_capture.profile(versions()[side]['version']).sources
    outer = sha256_hex(b'EXAMPLE external request identity')
    shared = {
        'phase': 'RETURN', 'session_sha256': sha256_hex(session.encode()),
        'process_sha256': api['events'][0]['process_sha256'],
        'entry_code_sha256': '1' * 64, 'caller_code_sha256': '2' * 64,
        'consumption_scope': {'pid': api['pid'], 'start_ticks': api['start_ticks']},
        'entry_qualified_before': True, 'entry_qualified_after': True, 'runtime_context_injected': False,
    }
    selection = {**shared, 'schema': COMPLETION_SELECTION_SCHEMA, 'point': 'BaseServing._log_inputs',
        'call_sha256': '3' * 64, 'source_file_sha256': sources['entrypoints/serve/engine/serving.py'],
        'caller_source_file_sha256': sources['entrypoints/openai/completion/serving.py'],
        'operation_started_ns': 6, 'operation_finished_ns': 7,
        'fields': {'response_request_sha256': response_sha256, 'outer_request_sha256': outer}}
    assignment = {**shared, 'schema': ASSIGNMENT_SCHEMA, 'point': 'InputProcessor.assign_request_id',
        'call_sha256': '4' * 64, 'source_file_sha256': sources['v1/engine/input_processor.py'],
        'caller_source_file_sha256': sources['v1/engine/async_llm.py'],
        'operation_started_ns': 8, 'operation_finished_ns': 9,
        'fields': {'outer_request_sha256': outer, 'native_request_sha256': R1}}
    api['events'].extend((selection, assignment))
    return processes, selection, assignment


@pytest.mark.parametrize('side', ['baseline', 'candidate'])
@pytest.mark.parametrize('other_worker_source', [False, True])
def test_collect_normal_scoped_runner_uses_each_release_pin(tmp_path, monkeypatch, side, other_worker_source):
    release = versions()[side]
    env, target, _, target_path = normal_sampling_inputs(tmp_path, release['version'])
    env = env.model_copy(update={'engine': env.engine.model_copy(update={'image_digest': release['image']}),
                                'runtime': env.runtime.model_copy(update={'gpu_count': 2, 'tp_size': 2})}).seal()
    target['environment_ref'] = env.ref().model_dump(mode='json')
    target['engine']['image_digest'] = release['image']
    target['kubernetes']['engine'].update(image=release['image'], image_id=release['image'])
    env_path = tmp_path / 'env.json'
    env_path.write_bytes(canonical_json(env.to_dict()))
    target_path.write_bytes(canonical_json(target))
    assets = tmp_path / 'assets'
    prepared = engine_callbacks.prepare_assets(assets, scoped_runner_consumption=True)
    processes = _ordinary_rows(side, prepared['session'], other_worker_source=other_worker_source)

    class RetainedPages:
        process_observation_origin = 'EXAMPLE'

        def __init__(self, declared_target, declared_env, *, duration, profile):
            assert declared_target == target and declared_env.ref() == env.ref()
            assert profile == collect_tool.ENGINE_CALLBACK_PROFILE
            self.binding = target['kubernetes']

        def engine_attempt_events(self, session, *, archive):
            assert session == prepared['session'] and archive is None
            return {'processes': processes, 'truncated': False}

    monkeypatch.setattr(collect_tool, 'KubernetesHTTP', RetainedPages)
    capture = collect_tool.collect(
        env_path=env_path, target_path=target_path, prepared_dir=assets, expected_requests=[R1],
        runner_scope={stage: [0, 1] for stage in ('runner_received', 'sampler_applied')})
    entry = capture['records']['scoped-runner-consumption']
    record, validation = entry['record'], entry['validation']
    received = [row for row in record['events'] if row['event'] == 'runner_received']
    applied = [row for row in record['events'] if row['event'] == 'sampler_applied']
    for rows in (received, applied):
        assert [record['bindings'][row['binding']]['rank'] for row in rows] == ([0] if other_worker_source else [0, 1])
    assert ('NATIVE_CALL_UNQUALIFIED' in validation['gaps']) is other_worker_source
    assert record['qualification_scope']['origin'] == 'EXAMPLE'
    scope = {key: record[key] for key in common_consumption.SCOPE_KEYS}
    projected = common_consumption.runner(record, scope, check_id='REQ-10', requests={'normal': R1},
                                          required_cases=['normal'])
    assert projected['cases']['normal']['api_state'] == 'api_validated'
    assert projected['cases']['normal']['sampler_events'] == len(applied)
    assert not projected['complete'] and not projected['producer_authenticated']


@pytest.mark.parametrize(('side', 'damage'), [
    ('baseline', None), ('candidate', None),
    ('baseline', 'other-serving-source'), ('candidate', 'duplicate-selection'),
    ('baseline', 'duplicate-assignment'), ('candidate', 'other-epoch'),
    ('baseline', 'late-assignment'), ('candidate', 'other-admission'),
    ('baseline', 'truncated'),
])
def test_owned_scoped_capture_keeps_exact_native_selection_assignment_and_worker_link(tmp_path, monkeypatch, side, damage):
    mode = 'worker-shards-and-scoped-runner-consumption'
    folder, render, _ = _stage(tmp_path, side, mode)
    namespace, engine, probe = _owned_pods(folder)
    env = _environment(render, namespace, engine)
    target = _target(env, render, namespace, engine, probe)
    env_path, target_path = tmp_path / 'env.json', tmp_path / 'target.json'
    env_path.write_bytes(canonical_json(env.to_dict()))
    target_path.write_bytes(canonical_json(target) + b'\n')
    target_pin = sha256_hex(target_path.read_bytes())
    assets = folder / 'engine-callbacks'
    manifest = engine_callbacks.load_prepared(assets)
    session = manifest['session']
    response = sha256_hex(b'EXAMPLE HTTP response identity')
    processes, selection, assignment = _native_request_link_rows(side, session, response)
    api, *workers = processes
    outer = selection['fields']['outer_request_sha256']
    if damage == 'other-serving-source':
        other_sources = vllm_runner_capture.profile(versions()['candidate']['version']).sources
        selection['caller_source_file_sha256'] = other_sources['entrypoints/openai/completion/serving.py']
    elif damage == 'duplicate-selection':
        api['events'].append({**selection, 'call_sha256': '5' * 64})
    elif damage == 'duplicate-assignment':
        api['events'].append({**assignment, 'call_sha256': '6' * 64})
    elif damage == 'other-epoch':
        assignment['consumption_scope'] = {**selection['consumption_scope'], 'start_ticks': 99}
    elif damage == 'late-assignment':
        assignment.update(operation_started_ns=12, operation_finished_ns=13)
    elif damage == 'other-admission':
        for process in workers:
            for row in process['events']:
                if row['point'] == 'GPUModelRunner.sample':
                    row['consumption_scope']['admissions'][R1] = '7' * 64

    class OwnedPages:
        process_observation_origin = 'EXAMPLE'

        def __init__(self, declared_target, declared_env, *, duration, profile):
            assert declared_target == target and declared_env.ref() == env.ref()
            assert profile == collect_tool.ENGINE_CALLBACK_PROFILE
            self.binding = target['kubernetes']

        def engine_attempt_events(self, wanted, *, archive, normal_callback_assets, normal_pod_check):
            assert wanted == session and archive is None and callable(normal_pod_check)
            assert normal_callback_assets['target_file_sha256'] == target_pin
            return {'processes': processes, 'truncated': damage == 'truncated',
                    'mounted_source_attestation': {'data_origin': 'EXAMPLE'}}

    monkeypatch.setattr(collect_tool, 'KubernetesHTTP', OwnedPages)
    capture = collect_tool.collect(
        env_path=env_path, target_path=target_path, prepared_dir=assets, target_file_sha256=target_pin,
        expected_requests=[R1], runner_scope={stage: [0, 1] for stage in ('runner_received', 'sampler_applied')})
    evidence = capture['records']['scoped-runner-consumption']
    assert evidence['record']['qualification_scope']['origin'] == 'EXAMPLE'
    assert not evidence['validation']['complete'] and not evidence['validation']['producer_authenticated']
    if damage is not None:
        assert 'native_request_links' not in capture
        return

    link, = capture['native_request_links']
    assert {key: link[key] for key in (
        'response_request_sha256', 'outer_request_sha256', 'native_request_sha256')} == {
            'response_request_sha256': response, 'outer_request_sha256': outer, 'native_request_sha256': R1}
    bindings, events = (evidence['record'][key] for key in ('bindings', 'events'))
    assert {bindings[row['binding']]['rank'] for row in link['workers']} == {0, 1}
    for row in link['workers']:
        received = events[row['runner_event_seq']]
        sampled = [events[index] for index in row['sampler_event_seqs']]
        assert received['event'] == 'runner_received' and sampled
        assert all(event['event'] == 'sampler_applied' and event['request_sha256'] == R1
                   and event['admission_sha256'] == row['admission_sha256'] for event in sampled)


@pytest.mark.parametrize(('side', 'matches'), [('baseline', True), ('candidate', True), ('candidate', False)])
def test_owned_ordinary_response_joins_only_matching_native_link_from_archived_cut(tmp_path, monkeypatch, side, matches):
    mode = 'worker-shards-and-scoped-runner-consumption'
    prepared_root, render, _ = _stage(tmp_path, side, mode)
    namespace, engine, probe = _owned_pods(prepared_root)
    env = _environment(render, namespace, engine)
    target = _target(env, render, namespace, engine, probe)
    env_path, target_path = tmp_path / 'env.json', tmp_path / 'target.json'
    env_path.write_bytes(canonical_json(env.to_dict()))
    target_raw = canonical_json(target)
    target_path.write_bytes(target_raw)
    target_sha = sha256_hex(target_raw)
    prepared = prepared_root / 'engine-callbacks'
    _, files, manifest, settings = passive.read_preparation(prepared, mode)
    session = manifest['session']
    mounted = {'schema': engine_http.NORMAL_CALLBACK_SOURCE_SCHEMA,
               'files_sha256': sha256_hex(canonical_json({name: {'sha256': sha256_hex(raw), 'bytes': len(raw)}
                                                         for name, raw in files.items()})),
               'filesystem_sha256': 'e' * 64, 'file_count': len(files), 'uid': 2000, 'gid': 0}
    model = target['model_mapping']['served_model_name']
    request_body = {'model': model, 'prompt': NORMAL_FIXTURE['prompt'], **NORMAL_OPTIONS}
    response_id = 'cmpl-' + 'a' * 32
    response_body = canonical_json({'id': response_id, 'object': 'text_completion', 'created': 700,
        'model': model, 'choices': [{'text': 'EXAMPLE private completion', 'finish_reason': 'length'}],
        'usage': {'prompt_tokens': 4, 'completion_tokens': 1, 'total_tokens': 5}})
    native_id = response_id if matches else 'cmpl-' + 'b' * 32
    response_sha = hmac.digest(bytes.fromhex(settings['secret']), b's:' + native_id.encode(), 'sha256').hex()
    processes, _, _ = _native_request_link_rows(side, session, response_sha)
    writers = {str(process['pid']): process['start_ticks'] for process in processes}
    rows, snapshot = [], {}
    for process in processes:
        name = f"callbacks-{process['pid']}.jsonl"
        offset = 0
        for event in process['events']:
            end = offset + len(canonical_json(event)) + 1
            rows.append({'file': name, 'offset': offset, 'end': end, 'event': event})
            offset = end
        snapshot[name] = {'pid': process['pid'], 'start_ticks': process['start_ticks'],
                          'size': offset, 'device': 1, 'inode': process['pid']}
    cursor = {name: metadata['size'] for name, metadata in snapshot.items()}
    pod_by_name = {pod['metadata']['name']: pod for pod in (engine, probe)}
    calls = []

    def runner(argv, *, input_bytes=None, **_kwargs):
        assert argv[:2] == ['kubectl', '--context=offline-fixture']
        args = argv[3:]
        if args == ['get', 'namespaces', 'serving-lab', '-o', 'json']:
            value = namespace
        elif args[:3] == ['--namespace=serving-lab', 'get', 'pod']:
            value = deepcopy(pod_by_name[args[3]])
        elif input_bytes is not None:
            assert calls == ['start']
            request, = json.loads(input_bytes)['requests']
            assert request == {'id': NORMAL_LABEL, 'mode': 'json', 'method': 'POST',
                               'url': target['engine']['trusted_url'] + '/v1/completions',
                               'headers': {}, 'body': request_body}
            calls.append('ordinary')
            value = [{'id': NORMAL_LABEL, 'method': 'POST', 'mode': 'json', 'outcome': 200,
                      'body': response_body.decode(), 'body_bytes': len(response_body),
                      'body_sha256': sha256_hex(response_body), 'body_truncated': False,
                      'capture_complete': True, 'eof': True, 'utf8_valid': True}]
        else:
            assert args[:4] == ['--namespace=serving-lab', 'exec', engine['metadata']['name'], '--container=engine']
            assert args[-2] == engine_http._callback_page_source(attest=True, trial=True)
            spec = json.loads(args[-1])
            assert spec['session'] == session and spec['snapshot'] is None
            start = spec['seek_end']
            assert spec['cursor'] == (None if start else {})
            calls.append('start' if start else 'end')
            value = {'schema': 'guard-callback-page/2', 'capture_id': spec['capture_id'], 'done': True, 'gaps': [],
                     'snapshot': {} if start else snapshot, 'cursor': {} if start else cursor, 'start_cursor': {},
                     'rows': [] if start else rows, 'container_start_monotonic_ns': 1,
                     'normal_callback_sources': mounted,
                     'normal_callback_writers': {'session_directory_sha256': 'd' * 64, 'writers': writers}}
        return {'returncode': 0, 'stdout_bytes': canonical_json(value), 'capture': {'complete': True}}

    monkeypatch.setattr(collect_tool, 'KubernetesHTTP', partial(engine_http.KubernetesHTTP, runner=runner))
    executor = KubernetesSamplingExecutor({'target': str(target_path), NORMAL_OPTION: True},
                                          runner=runner, expected_target_sha256=target_sha)
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['REQ-10']
    archive = tmp_path / 'pages'
    result = collect_tool.collect(
        env_path=env_path, target_path=target_path, prepared_dir=prepared, target_file_sha256=target_sha,
        pages_dir=archive, expected_requests=[R1], runner_scope={'runner_received': [0, 1], 'sampler_applied': [0, 1]},
        normal_sampling_request_sha256=sha256_hex(canonical_json(request_body)), observe_normal_sampling_response=True,
        normal_sampling_operation=partial(executor.run, check, env, APPROVED_NORMAL, VllmAdapter(), tmp_path / 'normal'))
    assert calls == ['start', 'ordinary', 'end']
    trial = result['normal_sampling_trial']
    assert trial['cut_verified'] is True and trial['receipt']['data_origin'] == 'EXAMPLE'
    page, = result['raw_page_refs']
    assert page['sha256'] == sha256_hex((archive / page['path']).read_bytes())
    assert json.loads((archive / page['path']).read_bytes())['rows'] == rows
    receipt_bytes = (archive / trial['archive_dir'] / trial['receipt']['path']).read_bytes()
    assert trial['receipt']['sha256'] == sha256_hex(receipt_bytes)
    assert json.loads(receipt_bytes)['response_capture']['original_body_sha256'] == sha256_hex(response_body)
    link, = result['native_request_links']
    native = result['records']['scoped-runner-consumption']
    assert not native['validation']['producer_authenticated'] and not native['validation']['complete']
    if matches:
        association = trial['response_association']
        assert association == {'attempt_sha256': sha256_hex(canonical_json(trial['cut'])),
                               'summary_sha256': trial['receipt']['sha256'], 'session_sha256': sha256_hex(session.encode()),
                               'data_origin': 'EXAMPLE', **link}
        assert association['response_request_sha256'] == response_sha and not trial['diagnostic_codes']
        assert {native['record']['bindings'][worker['binding']]['rank'] for worker in association['workers']} == {0, 1}
    else:
        assert not trial.get('response_association')
        assert trial['diagnostic_codes'] == ['NORMAL_SAMPLING_RESPONSE_ASSOCIATION_UNVERIFIED']
    assert response_id.encode() not in receipt_bytes and response_id not in json.dumps(result)
    assert b'EXAMPLE private completion' not in receipt_bytes and settings['secret'] not in json.dumps(result)
