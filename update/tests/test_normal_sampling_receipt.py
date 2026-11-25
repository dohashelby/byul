"""An ordinary owned response summary never fabricates the later native join."""

import json

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors.kubernetes_sampling import KubernetesSamplingExecutor, NORMAL_LABEL, NORMAL_OPTIONS
from guard.executors.normal_sampling_receipt import associate_response, qualify_summary
from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesHTTP
from tests.test_kubernetes_sampling import APPROVED_NORMAL, World, normal_sampling_inputs


def _owned_normal_summary(tmp_path, release, owner_changed=False, *, normal_response_observer=None, include_id=True):
    env, target, check, path = normal_sampling_inputs(tmp_path, release)
    path.write_bytes(canonical_json(target))
    world = World(target)
    payload = {'id': 'cmpl-example', 'object': 'text_completion', 'created': 1, 'model': 'fixture-model',
               'choices': [{'text': 'fixture-only', 'finish_reason': 'length'}],
               'usage': {'prompt_tokens': 4, 'completion_tokens': 1, 'total_tokens': 5}}
    if not include_id:
        del payload['id']
    response_raw = canonical_json(payload)

    def runner(argv, **kwargs):
        out = world.runner(argv, **kwargs)
        if owner_changed and 'get' in argv and 'engine-pod' in argv and world.after_exec:
            pod = json.loads(out['stdout_bytes'])
            pod['metadata']['uid'] = 'fixture-replacement'
            out['stdout_bytes'] = canonical_json(pod)
        if 'exec' in argv:
            row, = json.loads(out['stdout_bytes'])
            assert row['id'] == NORMAL_LABEL
            row.update(body=response_raw.decode(), mode='json', body_bytes=len(response_raw), body_sha256=sha256_hex(response_raw),
                       capture_complete=True, eof=True, utf8_valid=True, body_truncated=False)
            out['stdout_bytes'] = canonical_json([row])
        return out

    transport = KubernetesHTTP(target, env, duration=3, runner=runner, profile=INPUT_VALIDATION_PROFILE)
    expected = {'target_sha256': sha256_hex(path.read_bytes()),
                'binding_sha256': sha256_hex(canonical_json(transport.binding)),
                'probe_source_sha256': transport.source_sha256,
                'request_body_sha256': sha256_hex(canonical_json(
                    {'model': 'fixture-model', 'prompt': 'guard sampling control', **NORMAL_OPTIONS}))}
    executor = KubernetesSamplingExecutor({'target': str(path), 'normal_sampling_only': True},
                                          runner=runner, expected_target_sha256=expected['target_sha256'])
    draft = executor.run(check, env, APPROVED_NORMAL, VllmAdapter(), tmp_path / 'work',
                         normal_response_observer=normal_response_observer)
    return draft.artifacts[0].path.read_bytes(), expected, response_raw


@pytest.mark.parametrize('release', ['0.30.0', '0.31.0'])
@pytest.mark.parametrize('owner_changed', [False, True])
def test_req10_owned_normal_writer_summary_has_no_native_receipt(tmp_path, release, owner_changed):
    raw, expected, response_raw = _owned_normal_summary(tmp_path, release, owner_changed)
    pin = sha256_hex(raw)
    qualified = qualify_summary(raw, expected_file_sha256=pin, expected_pins=expected)
    if owner_changed:
        assert qualified['reported_outcome'] is None and not qualified['reported_owner_verified_before_after']
        assert 'REQ10_NORMAL_RESPONSE_OWNER_UNVERIFIED' in qualified['diagnostic_codes']
        assert 'reported_body_sha256' not in qualified and not qualified['native_request_binding_verified']
        return
    assert qualified['reported_outcome'] == 200 and qualified['reported_owner_verified_before_after']
    assert qualified['reported_completion_shape_verified'] is True
    assert qualified['reported_body_sha256'] == sha256_hex(response_raw)
    assert qualified['reported_body_bytes'] == len(response_raw)
    assert qualified['native_request_binding_verified'] is False
    assert qualified['diagnostic_codes'] == [
        'REQ10_RESPONSE_OBSERVATION_WINDOW_UNAVAILABLE',
        'REQ10_RESPONSE_ORIGINAL_BODY_UNAVAILABLE',
        'REQ10_RESPONSE_REQUEST_ID_UNAVAILABLE',
    ]
    assert 'cmpl-example' not in str(qualified) and 'fixture-only' not in str(qualified)
    with pytest.raises(ValueError, match='^REQ10_NORMAL_RESPONSE_SUMMARY_CHANGED$'):
        qualify_summary(raw + b' ', expected_file_sha256=pin, expected_pins=expected)
    for key in expected:
        mismatched = {**expected, key: '0' * 64}
        with pytest.raises(ValueError, match='^REQ10_NORMAL_RESPONSE_SCOPE_MISMATCH$'):
            qualify_summary(raw, expected_file_sha256=pin, expected_pins=mismatched)
    summary = json.loads(raw)
    summary['response_capture']['stored_body_is_verbatim'] = True
    altered = canonical_json(summary)
    with pytest.raises(ValueError, match='^REQ10_NORMAL_RESPONSE_SUMMARY_INVALID$'):
        qualify_summary(altered, expected_file_sha256=sha256_hex(altered), expected_pins=expected)



def _association_inputs(tmp_path, release, *, include_id=True):
    from types import SimpleNamespace

    from guard.adapters.vllm_cache_callback import CacheCallbackObserver
    from tools.gpu_a_normal_tp2_callbacks import prepare

    calls = []
    raw, expected, body = _owned_normal_summary(
        tmp_path, release, normal_response_observer=lambda **kwargs: calls.append(kwargs), include_id=include_id)
    observer, = calls
    assert observer['body_bytes'] == body and observer['summary_sha256'] == sha256_hex(raw)
    plan, _, settings_manifest, settings = prepare(tmp_path / 'assets', 'scoped-runner-consumption')
    session = settings_manifest['session']
    request_key = bytes.fromhex(settings['secret'])
    cut = {'schema': 'guard-normal-tp2-callback-cut/1', 'data_origin': observer['data_origin'],
           'environment_ref': observer['environment_ref'], 'capture_id': 'd' * 32,
           'target_sha256': expected['target_sha256'], 'target_file_sha256': '2' * 64,
           'plan_sha256': sha256_hex(canonical_json(plan)), 'source_sha256': '3' * 64,
           'owned_generation_sha256': expected['binding_sha256'], 'session_sha256': plan['session_sha256'],
           'cursor_sha256': '4' * 64, 'writer_scope_sha256': '5' * 64, 'source_observation_sha256': '6' * 64,
           'start_page': {'path': 'start-' + 'd' * 32 + '.json', 'sha256': '7' * 64}}
    observer['attempt_sha256'] = sha256_hex(canonical_json(cut))
    response_sha256 = CacheCallbackObserver.fingerprint(SimpleNamespace(secret=request_key), 'cmpl-example')
    link = {'response_request_sha256': response_sha256, 'outer_request_sha256': '8' * 64,
            'native_request_sha256': '9' * 64, 'selection_call_sha256': 'a' * 64, 'assignment_call_sha256': 'b' * 64,
            'workers': [{'binding': 0, 'admission_sha256': 'c' * 64, 'runner_event_seq': 1, 'sampler_event_seqs': [2]}]}
    capture = {'normal_callback_cut': cut.copy(), 'normal_callback_cut_verified': True,
               'truncated': False, 'native_request_links': [link]}
    return raw, {'expected_file_sha256': sha256_hex(raw), 'expected_pins': expected, 'observer': observer,
                 'callback_capture': capture, 'expected_cut': cut, 'session': session, 'request_key': request_key,
                 'api_model': 'fixture-model'}


@pytest.mark.parametrize('release', ['0.30.0', '0.31.0'])
def test_req10_joins_original_response_to_same_cut_native_link_without_raw_output(tmp_path, release):
    raw, proof = _association_inputs(tmp_path, release)
    accepted = associate_response(raw, **proof)
    assert accepted['data_origin'] == 'EXAMPLE' and accepted['attempt_sha256'] == proof['observer']['attempt_sha256']
    assert accepted['response_request_sha256'] == proof['callback_capture']['native_request_links'][0]['response_request_sha256']
    assert accepted['session_sha256'] == proof['expected_cut']['session_sha256']
    assert accepted['workers'] == proof['callback_capture']['native_request_links'][0]['workers']
    assert 'cmpl-example' not in str(accepted) and 'fixture-only' not in str(accepted)
    assert qualify_summary(raw, expected_file_sha256=proof['expected_file_sha256'],
                           expected_pins=proof['expected_pins'])['native_request_binding_verified'] is False


@pytest.mark.parametrize(('case', 'code'), [
    ('prior-attempt', 'ATTEMPT_UNVERIFIED'), ('unverified-cut', 'ATTEMPT_UNVERIFIED'),
    ('different-cut', 'ATTEMPT_UNVERIFIED'), ('different-owner', 'ATTEMPT_UNVERIFIED'),
    ('different-env', 'ATTEMPT_UNVERIFIED'), ('different-origin', 'ATTEMPT_UNVERIFIED'),
    ('body-drift', 'ORIGINAL_BODY_MISMATCH'), ('missing-id', 'REQUEST_ID_UNAVAILABLE'),
    ('different-key', 'NATIVE_LINK_UNAVAILABLE'), ('duplicate-native', 'NATIVE_LINK_AMBIGUOUS'),
])
def test_req10_will_not_join_unproven_response_or_callback_capture(tmp_path, case, code):
    raw, proof = _association_inputs(tmp_path, '0.31.0', include_id=case != 'missing-id')
    observer, capture = proof['observer'], proof['callback_capture']
    if case == 'prior-attempt':
        observer['attempt_sha256'] = 'e' * 64
    elif case == 'unverified-cut':
        capture['normal_callback_cut_verified'] = False
    elif case == 'different-cut':
        capture['normal_callback_cut']['capture_id'] = 'a' * 32
    elif case == 'different-owner':
        proof['expected_cut']['owned_generation_sha256'] = 'a' * 64
    elif case == 'different-env':
        observer['environment_ref'] = {**observer['environment_ref'], 'sha256': 'a' * 64}
    elif case == 'different-origin':
        observer['data_origin'] = 'MEASURED'
    elif case == 'body-drift':
        observer['body_bytes'] += b' '
    elif case == 'different-key':
        proof['request_key'] = b'0' * 32
    elif case == 'duplicate-native':
        capture['native_request_links'].append(capture['native_request_links'][0].copy())
    with pytest.raises(ValueError, match=f'^REQ10_RESPONSE_{code}$'):
        associate_response(raw, **proof)
