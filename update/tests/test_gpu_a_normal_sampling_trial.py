"""Bind the existing normal sampling receipt to one inert, owned capture range."""
from copy import deepcopy
from datetime import timedelta
from functools import partial
import json
from pathlib import Path
from threading import Thread

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.kubernetes_sampling import (
    NORMAL_FIXTURE, NORMAL_LABEL, NORMAL_OPTION, NORMAL_OPTIONS, NORMAL_PROFILE, KubernetesSamplingExecutor,
)
from guard.executors import normal_sampling_receipt
from guard.executors.normal_sampling_receipt import qualify_summary
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from guard.runtime import engine_http
from tests.test_gpu_a_normal_tp2_callbacks import _environment, _owned_pods, _stage, _target
from tests.test_kubernetes_sampling import APPROVED_NORMAL
from tools import gpu_a_normal_tp2_callbacks as passive
from tools import gpu_a_producers_collect as collector


@pytest.mark.parametrize(('side', 'damage'), [
    ('baseline', None),
    ('candidate', None),
    ('baseline', 'ordinary-writer'),
    ('candidate', 'ordinary-writer'),
    ('baseline', 'operation-fails'),
    ('baseline', 'missing-start-page'),
    ('candidate', 'end-owner-changed'),
    ('candidate', 'stale-receipt'),
    ('baseline', 'wrong-pin'),
    ('baseline', 'aborted-receipt'),
    ('baseline', 'observe-once'),
    ('candidate', 'observe-duplicate'),
    ('baseline', 'observe-late'),
    ('candidate', 'observe-cross-thread'),
    ('baseline', 'observe-wrong-summary'),
    ('candidate', 'observe-wrong-origin'),
    ('candidate', 'observe-end-owner-changed'),
])
def test_collector_brackets_one_approved_operation_with_its_original_receipt(tmp_path, monkeypatch, side, damage):
    stage, render, _ = _stage(tmp_path, side, 'worker-shards-and-scoped-runner-consumption')
    namespace, engine, probe = _owned_pods(stage)
    env = _environment(render, namespace, engine)
    target = _target(env, render, namespace, engine, probe)
    env_file, target_file = tmp_path / 'env.json', tmp_path / 'target.json'
    env_file.write_bytes(canonical_json(env.to_dict()))
    target_file.write_bytes(canonical_json(target))
    target_sha = sha256_hex(canonical_json(target))
    prepared = stage / 'engine-callbacks'
    _, files, manifest, _ = passive.read_preparation(prepared, target[passive.KEY]['mode'])
    mounted = {'schema': engine_http.NORMAL_CALLBACK_SOURCE_SCHEMA,
               'files_sha256': sha256_hex(canonical_json({name: {'bytes': len(raw), 'sha256': sha256_hex(raw)}
                                                         for name, raw in files.items()})),
               'filesystem_sha256': 'f' * 64, 'file_count': len(files), 'uid': 2000, 'gid': 0}
    original = engine_http.KubernetesHTTP(target, env, duration=15, profile=engine_http.ENGINE_CALLBACK_PROFILE)
    binding_sha = sha256_hex(canonical_json(original.binding))
    pages, archive = tmp_path / 'pages', tmp_path / 'pages/normal-sampling-trial'
    calls, engine_reads = [], []
    pods = {pod['metadata']['name']: pod for pod in (engine, probe)}
    use_writer = damage == 'ordinary-writer'
    request_body = {'model': original.api_model, 'prompt': NORMAL_FIXTURE['prompt'], **NORMAL_OPTIONS}
    response_body = canonical_json({'id': 'cmpl-in-memory-fixture', 'model': original.api_model,
        'choices': [{'text': 'EXAMPLE completion', 'finish_reason': 'length'}],
        'usage': {'prompt_tokens': 4, 'completion_tokens': 1, 'total_tokens': 5}})
    observations, late, associations = [], [], []
    observe = damage is not None and damage.startswith('observe-')

    def associate(raw, *, expected_file_sha256, expected_pins, observer, callback_capture, expected_cut,
                  session, request_key, api_model):
        assert observe and damage == 'observe-once' and calls == ['start', 'operation', 'finish']
        assert observer == {'body_bytes': response_body, 'summary_sha256': expected_file_sha256,
                            'environment_ref': env.ref().model_dump(mode='json'), 'data_origin': 'EXAMPLE',
                            'attempt_sha256': sha256_hex(canonical_json(expected_cut))}
        assert callback_capture == {'normal_callback_cut': expected_cut, 'normal_callback_cut_verified': True,
                                    'truncated': False, 'native_request_links': []}
        assert session == manifest['session'] and len(request_key) == 32 and api_model == original.api_model
        assert request_key == bytes.fromhex(json.loads(files['settings.json'])['secret'])
        assert qualify_summary(raw, expected_file_sha256=expected_file_sha256, expected_pins=expected_pins)[
            'reported_body_sha256'] == sha256_hex(response_body)
        associations.append(expected_file_sha256)
        return {'data_origin': 'EXAMPLE', 'response_request_sha256': 'a' * 64}

    monkeypatch.setattr(normal_sampling_receipt, 'associate_response', associate)

    def runner(argv, *, max_stdout_bytes, input_bytes=None, **_kwargs):
        assert argv[:2] == ['kubectl', '--context=offline-fixture']
        args = argv[3:]
        if args == ['get', 'namespaces', 'serving-lab', '-o', 'json']:
            value = namespace
        elif args[:3] == ['--namespace=serving-lab', 'get', 'pod']:
            value = deepcopy(pods[args[3]])
            if args[3] == engine['metadata']['name']:
                engine_reads.append(list(calls))
                if damage == 'missing-start-page' and len(engine_reads) == 3:
                    start, = archive.glob('start-*.json')
                    start.unlink()
                if damage in {'end-owner-changed', 'observe-end-owner-changed'} and 'operation' in calls:
                    value['status']['containerStatuses'][0]['restartCount'] += 1
        elif input_bytes is not None:
            assert use_writer and calls == ['start', 'operation']
            assert args[:4] == ['--namespace=serving-lab', 'exec', '-i', probe['metadata']['name']]
            request, = json.loads(input_bytes)['requests']
            assert request == {'id': NORMAL_LABEL, 'mode': 'json', 'method': 'POST',
                               'url': target['engine']['trusted_url'] + '/v1/completions',
                               'headers': {}, 'body': request_body}
            calls.append('http')
            value = [{'id': NORMAL_LABEL, 'mode': 'json', 'method': 'POST', 'outcome': 200,
                      'body': response_body.decode(), 'body_bytes': len(response_body),
                      'body_sha256': sha256_hex(response_body), 'capture_complete': True,
                      'eof': True, 'utf8_valid': True, 'body_truncated': False}]
        else:
            assert args[:4] == ['--namespace=serving-lab', 'exec', engine['metadata']['name'], '--container=engine']
            assert args[4:9] == ['--', 'python3', '-I', '-S', '-c'] and max_stdout_bytes == 1024**2
            assert args[9] == engine_http._callback_page_source(attest=True, trial=True)
            spec = json.loads(args[-1])
            assert spec['session'] == manifest['session'] and spec['snapshot'] is None
            calls.append('start' if spec['seek_end'] else 'finish')
            if not spec['seek_end'] and late:
                observer, response = late.pop()
                observer(**response)
            value = {'schema': 'guard-callback-page/2', 'capture_id': spec['capture_id'], 'done': True,
                     'snapshot': {}, 'cursor': {}, 'start_cursor': {}, 'rows': [], 'gaps': [],
                     'normal_callback_sources': mounted, 'container_start_monotonic_ns': 100,
                     'normal_callback_writers': {'session_directory_sha256': 'e' * 64, 'writers': {'7': 9}}}
        return {'returncode': 0, 'stdout_bytes': canonical_json(value), 'capture': {'complete': True}}

    monkeypatch.setattr(collector, 'KubernetesHTTP', partial(engine_http.KubernetesHTTP, runner=runner))
    request_sha = sha256_hex(canonical_json(request_body)) if use_writer else '8' * 64
    receipt_path = tmp_path / 'normal_sampling.json'

    def operation(*, normal_response_observer=None):
        assert calls == ['start'] and len(engine_reads) == 3
        start_path, = archive.glob('start-*.json')
        assert json.loads(start_path.read_bytes())['normal_callback_sources'] == mounted
        calls.append('operation')
        if use_writer:
            check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['REQ-10']
            executor = KubernetesSamplingExecutor({'target': str(target_file), NORMAL_OPTION: True},
                                                  runner=runner, expected_target_sha256=target_sha)
            return executor.run(check, env, APPROVED_NORMAL, VllmAdapter(), tmp_path,
                                normal_response_observer=lambda **observation: observations.append(observation))
        if damage == 'operation-fails':
            raise RuntimeError('EXAMPLE private diagnostic')
        started = utcnow() - timedelta(days=1) if damage == 'stale-receipt' else utcnow()
        aborted = damage == 'aborted-receipt'
        summary = {'profile': NORMAL_PROFILE, 'target_sha256': target_sha, 'binding_sha256': binding_sha,
                   'probe_source_sha256': original.source_sha256,
                   'request_body_sha256': '9' * 64 if damage == 'wrong-pin' else request_sha,
                   'supplied_sampling_options': ['max_tokens'], 'ownership_verified_before_after': not aborted,
                   'http_dispatch_observed': None if aborted else True, 'outcome': None if aborted else 200,
                   'normal_completion_shape_verified': not aborted,
                   'response_capture': None if aborted else {'stored_body_representation': 'omitted_hash_and_parse_state_only',
                       'stored_body_is_verbatim': False, 'original_json_state': 'valid',
                       'original_body_sha256': sha256_hex(response_body), 'original_body_bytes': len(response_body)},
                   'uncollected': ['sampling_limits', 'runner_consumption', 'rejections', 'structured_output', 'tool_selection']}
        raw_summary = canonical_json(summary)
        receipt_path.write_bytes(raw_summary)
        if observe:
            response = {'body_bytes': response_body, 'summary_sha256': sha256_hex(raw_summary),
                        'environment_ref': env.ref().model_dump(mode='json'), 'data_origin': 'EXAMPLE'}
            if damage == 'observe-wrong-summary':
                response['summary_sha256'] = '2' * 64
            if damage == 'observe-wrong-origin':
                response['data_origin'] = 'MEASURED'
            if damage == 'observe-late':
                late.append((normal_response_observer, response))
            elif damage == 'observe-cross-thread':
                worker = Thread(target=normal_response_observer, kwargs=response)
                worker.start()
                worker.join()
            else:
                normal_response_observer(**response)
                if damage == 'observe-duplicate':
                    normal_response_observer(**response)
        return EvidenceDraft(status='ABORTED' if aborted else 'COMPLETED', data_origin='EXAMPLE',
            normal_control={'attempted': True, 'succeeded': None if aborted else True},
            config={NORMAL_OPTION: True, 'target_sha256': target_sha}, started_at=started, finished_at=utcnow(),
            artifacts=[DraftArtifact(name=receipt_path.name, path=receipt_path,
               collector=f'{KubernetesSamplingExecutor.name}/{KubernetesSamplingExecutor.version}')])

    arguments = dict(env_path=env_file, target_path=target_file, prepared_dir=prepared, pages_dir=pages,
                     target_file_sha256=sha256_hex(target_file.read_bytes()), normal_sampling_request_sha256=request_sha,
                     normal_sampling_operation=operation if observe else lambda: operation(),
                     observe_normal_sampling_response=observe)
    if damage == 'missing-start-page':
        with pytest.raises(engine_http.KubernetesBindingError):
            collector.collect(**arguments)
        assert calls == ['start'] and not receipt_path.exists()
        return
    value = collector.collect(**arguments)
    trial = value['normal_sampling_trial']
    expected_calls = ['start', 'operation']
    if use_writer:
        expected_calls.append('http')
    owner_changed = damage in {'end-owner-changed', 'observe-end-owner-changed'}
    if not owner_changed:
        expected_calls.append('finish')
    assert calls == expected_calls
    assert trial['schema'] == collector.NORMAL_SAMPLING_TRIAL_SCHEMA and trial['archive_dir'] == archive.name
    assert trial['cut']['target_sha256'] == target_sha and trial['cut']['data_origin'] == 'EXAMPLE'
    start_ref = trial['cut']['start_page']
    assert start_ref['sha256'] == sha256_hex((archive / start_ref['path']).read_bytes())
    assert trial['cut_verified'] is not owner_changed
    assert trial['operation_returned'] is (damage != 'operation-fails')
    if damage in {'operation-fails', 'stale-receipt', 'wrong-pin'}:
        code = 'NORMAL_SAMPLING_OPERATION_FAILED' if damage == 'operation-fails' else 'NORMAL_SAMPLING_RECEIPT_UNVERIFIED'
        assert trial['diagnostic_codes'] == [code] and trial['receipt'] is None
        assert not (archive / receipt_path.name).exists()
    else:
        expected_codes = ['NORMAL_SAMPLING_RESPONSE_ASSOCIATION_UNVERIFIED'] if observe and damage != 'observe-once' else []
        assert trial['diagnostic_codes'] == expected_codes
        receipt = trial['receipt']
        raw = receipt_path.read_bytes()
        assert raw == (archive / receipt['path']).read_bytes()
        assert receipt['data_origin'] == 'EXAMPLE'
        assert receipt['execution_status'] == ('ABORTED' if damage == 'aborted-receipt' else 'COMPLETED')
        assert receipt['sha256'] == sha256_hex(raw) and receipt['bytes'] == len(raw)
        assert trial['expected_pins'] == {'target_sha256': target_sha, 'binding_sha256': binding_sha,
                                         'probe_source_sha256': original.source_sha256, 'request_body_sha256': request_sha}
        if use_writer:
            assert observations == [{'body_bytes': response_body, 'summary_sha256': receipt['sha256'],
                                     'environment_ref': env.ref().model_dump(mode='json'), 'data_origin': 'EXAMPLE'}]
            qualified = qualify_summary(raw, expected_file_sha256=observations[0]['summary_sha256'],
                                        expected_pins=trial['expected_pins'])
            assert qualified['reported_body_sha256'] == sha256_hex(response_body)
            assert qualified['native_request_binding_verified'] is False
            assert b'cmpl-in-memory-fixture' not in raw and b'EXAMPLE completion' not in raw
            assert not value.get('native_request_links')
    if damage == 'observe-once':
        assert associations == [trial['receipt']['sha256']]
        assert trial['response_association'] == {'data_origin': 'EXAMPLE', 'response_request_sha256': 'a' * 64}
    else:
        assert not associations and 'response_association' not in trial
    retained = json.dumps(value)
    assert 'EXAMPLE private diagnostic' not in retained
    assert 'cmpl-in-memory-fixture' not in retained and 'EXAMPLE completion' not in retained
