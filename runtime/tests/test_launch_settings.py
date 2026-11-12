"""D7 parsing, safe transport and revalidation lifecycle."""
import json
from unittest.mock import Mock

import pytest

from serving_runtime.mvp.launch_settings import observe, comparison_rows
from serving_runtime.mvp.target import summarize_pods
from serving_runtime.mvp.revalidation import RevalidationRequests, evaluate_config_drift
from serving_runtime.mvp.app import GatewayApp


def pod(args=None, command=None):
    return {'metadata': {'name': 'engine', 'uid': 'uid'},
            'spec': {'containers': [{'name': 'engine', 'command': command or ['vllm', 'serve'], 'args': args or []}]},
            'status': {'phase': 'Running', 'containerStatuses': [
                {'name': 'engine', 'containerID': 'container', 'state': {'running': {}}}]}}


def collected(args=None, engine='vllm'):
    return {'state': 'ok', 'last_value': summarize_pods({'items': [pod(args)]}, 'engine', engine),
            'data_origin': 'kubernetes_api', 'last_success_at': '2026-11-12T00:00:00Z'}


@pytest.mark.parametrize('engine', ['vllm', 'sglang'])
def test_engine_defaults_and_applicability(engine):
    rows = {r['field'].split('.')[-1]: r for r in comparison_rows(
        {'trust_remote_code': False, 'revision': 'approved'}, collected(engine=engine))}
    assert rows['trust_remote_code']['state'] == 'match'
    assert rows['revision']['observed'] is None
    assert rows['revision']['state'] == 'drift'
    for key in (['enable_multimodal'] if engine == 'vllm' else ['code_revision', 'tokenizer_revision', 'trust_request_mm_kwargs']):
        assert rows[key]['state'] == 'not_applicable'


@pytest.mark.parametrize('args,key,value', [
    (['--trust-remote-code'], 'trust_remote_code', True),
    (['--no-trust-remote-code'], 'trust_remote_code', False),
    (['--trust_request_mm_kwargs'], 'trust_request_mm_kwargs', True),
    (['--no-trust-request-mm-kwargs'], 'trust_request_mm_kwargs', False),
    (['--trust-request-mm-kwargs=true'], 'trust_request_mm_kwargs', True),
    (['--revision=approved'], 'revision', 'approved'),
    (['--code-revision', 'refs/approved'], 'code_revision', 'refs/approved'),
    (['--tokenizer_revision=approved'], 'tokenizer_revision', 'approved'),
])
def test_vllm_flags(args, key, value):
    result = observe({'command': [], 'args': args}, 'vllm')['settings'][key]
    assert result == {'state': 'observed', 'value': value}


def test_sglang_command_only():
    settings = observe({'command': ['python', '-m', 'sglang.launch_server', '--enable-multimodal',
                                    '--trust-remote-code', '--revision=approved']}, 'sglang')['settings']
    assert settings['enable_multimodal']['value'] is True
    assert settings['trust_remote_code']['value'] is True
    assert settings['revision']['value'] == 'approved'
    # SGLang does not normalize underscores or support negative store_true flags.
    assert observe({'args': ['--no-trust-remote-code']}, 'sglang')['settings']['trust_remote_code']['state'] == 'unknown'


@pytest.mark.parametrize('spec', [None, {}, {'args': None}, {'args': ['--config', 'omitted']},
    {'command': ['sh', '-c', 'omitted']}, {'args': ['--config=omitted']}])
@pytest.mark.parametrize('engine', ['vllm', 'sglang'])
def test_unknown_never_becomes_false(spec, engine):
    result = observe(spec, engine)['settings']['trust_remote_code']
    assert result == {'state': 'unknown', 'value': None}


def test_invalid_revision_and_bool_values_are_not_retained():
    observation = observe({'args': ['--revision', '--api-key', 'omitted', '--trust-remote-code=invalid']}, 'vllm')
    assert observation['settings']['revision'] == {'value': None, 'state': 'unknown'}
    assert observation['settings']['trust_remote_code']['state'] == 'unknown'
    assert 'omitted' not in json.dumps(observation)


def test_null_and_absent_approvals_are_not_compared():
    rows = comparison_rows({'trust_remote_code': None}, collected(['--trust-remote-code']))
    assert rows[0]['state'] == 'unknown'
    assert rows[1]['state'] == 'unapproved'
    assert not evaluate_config_drift({'rows': rows}).conditions


@pytest.mark.parametrize('state', ['error', 'stale', 'disconnected'])
def test_collection_failure_ignores_last_value(state):
    pods = collected(['--trust-remote-code'])
    pods['state'] = state
    rows = comparison_rows({'trust_remote_code': False}, pods)
    assert rows[0]['state'] == 'unknown'
    assert rows[0]['observed'] is None


@pytest.mark.parametrize('items', [[], [pod(), pod()]])
def test_missing_or_ambiguous_pods_are_unknown(items):
    snapshot = summarize_pods({'items': items}, 'engine', 'vllm')
    assert snapshot['launch_settings']['settings']['trust_remote_code']['state'] == 'unknown'


def test_missing_container_is_unknown():
    assert summarize_pods({'items': [pod()]}, 'other', 'vllm')['launch_settings']['settings']['trust_remote_code']['state'] == 'unknown'


def test_revalidation_deduplicates_and_unknown_does_not_clear(tmp_path):
    requests = RevalidationRequests(tmp_path)
    context = dict(observation_session_id='session', target_id='target', policy={})
    drift = {'rows': comparison_rows({'trust_remote_code': False}, collected(['--trust-remote-code']))}
    requests.observe_baseline(drift, **context)
    requests.observe_baseline(drift, **context)
    assert len(requests.store.read_all()[0]) == 1
    # Reload to exercise persisted source-aware scope reconstruction.
    requests = RevalidationRequests(tmp_path)
    requests.observe_baseline({'rows': comparison_rows({'trust_remote_code': False}, None)}, **context)
    assert requests.summary()['current_condition_count'] == 1
    requests.observe_baseline({'rows': comparison_rows({'trust_remote_code': False}, collected())}, **context)
    assert requests.summary()['current_condition_count'] == 0
    assert requests.store.read_all()[0][-1]['event'] == 'condition_cleared'


def test_same_settings_do_not_request_revalidation(tmp_path):
    requests = RevalidationRequests(tmp_path)
    requests.observe_baseline({'rows': comparison_rows({'trust_remote_code': False}, collected())},
                              observation_session_id='s', target_id='t', policy={})
    assert requests.summary()['requests'] == []


def test_host_transport_and_stores_only_allowlisted_values(tmp_path):
    from tools.k8s_observation_feed import _reduce_pod
    marker = 'D7_UNRELATED_VALUE_MUST_NOT_SURVIVE'
    original = pod(['--api-key', marker, '--revision=approved', '--trust-remote-code'])
    reduced = _reduce_pod(original, "engine")
    assert marker not in json.dumps(reduced)
    assert 'args' not in reduced['spec']['containers'][0]
    assert summarize_pods({'items': [reduced]}, 'engine', 'vllm')['launch_settings'] == summarize_pods({'items': [original]}, 'engine', 'vllm')['launch_settings']
    requests = RevalidationRequests(tmp_path)
    rows = comparison_rows({'trust_remote_code': False}, collected(original['spec']['containers'][0]['args']))
    requests.observe_baseline({'rows': rows}, observation_session_id='s', target_id='t', policy={})
    assert marker not in json.dumps(requests.summary())
    assert all(marker not in p.read_text() for p in tmp_path.glob('*.jsonl'))


def test_reduced_snapshot_cannot_smuggle_extra_fields():
    marker = 'D7_UNRELATED_FIELD_MUST_NOT_SURVIVE'
    reduced = observe({'args': []}, 'vllm')
    reduced['extra'] = marker
    result = observe({'launch_settings': {'vllm': reduced}}, 'vllm')
    assert marker not in json.dumps(result)
    assert result['settings']['trust_remote_code']['state'] == 'unknown'


def test_host_transport_excludes_non_engine_containers():
    from tools.k8s_observation_feed import _reduce_pod
    raw = pod()
    raw['spec']['containers'].append({'name': 'sidecar', 'args': ['--revision=unrelated']})
    reduced = _reduce_pod(raw, 'engine')
    assert 'unrelated' not in json.dumps(reduced)
    assert len(reduced['spec']['containers']) == 1


def test_gateway_pod_callback_runs_existing_revalidation_flow(tmp_path):
    app = object.__new__(GatewayApp)
    app._target = Mock()
    app._target.registration.state = 'ok'
    app._target.registration.engine_kind = 'vllm'
    app._target.registration.launch_settings = {'trust_remote_code': False}
    app._collectors = Mock()
    app._collectors.get.side_effect = lambda source: collected(['--trust-remote-code']) if source == 'target_pods' else None
    from serving_runtime.mvp.collectors import load_declaration
    app._baseline = load_declaration(None, kind='baseline')
    app._manifest = load_declaration(None, kind='manifest')
    app._revalidation = RevalidationRequests(tmp_path)
    app._revalidation_context = lambda: dict(observation_session_id='s', target_id='t', policy={})
    app._observe_target_pods({}, 'kubernetes_api')
    assert app._revalidation.summary()['current_condition_count'] == 1
    # D7 rows are kept out of target admission comparisons.
    assert all(r['source'] == 'kubernetes_spec' for r in app.baseline_status()['rows'])


@pytest.mark.parametrize('engine,args,keys', [
    ('vllm', ['--trust-remote'], ['trust_remote_code']),
    ('vllm', ['--trust_remote'], ['trust_remote_code']),
    ('vllm', ['--trust-request-mm'], ['trust_request_mm_kwargs']),
    ('vllm', ['--no-trust-req'], ['trust_request_mm_kwargs']),
    ('vllm', ['--code-rev=x'], ['code_revision']),
    ('vllm', ['--code-rev', 'main'], ['code_revision']),
    ('vllm', ['--trust'], ['trust_remote_code', 'trust_request_mm_kwargs']),
    ('vllm', ['--no-trust'], ['trust_remote_code', 'trust_request_mm_kwargs']),
    ('vllm', ['--tokenizer-rev=main'], ['tokenizer_revision']),
    ('sglang', ['--trust-remote'], ['trust_remote_code']),
    ('sglang', ['--enable-multi'], ['enable_multimodal']),
    ('sglang', ['--rev=main'], ['revision']),
])
def test_argparse_abbreviations_are_unknown_in_observation_and_comparison(engine, args, keys):
    snapshot = collected(args, engine)
    rows = {row['field'].split('.')[-1]: row for row in comparison_rows(
        {key: False if key in {'trust_remote_code', 'trust_request_mm_kwargs', 'enable_multimodal'}
         else 'approved' for key in keys}, snapshot)}
    for key in keys:
        assert snapshot['last_value']['launch_settings']['settings'][key] == {'state': 'unknown', 'value': None}
        assert rows[key]['state'] == 'unknown'
        assert rows[key]['observed'] is None
    assert not evaluate_config_drift({'rows': list(rows.values())}).observed_origins


@pytest.mark.parametrize('engine', ['vllm', 'sglang'])
def test_abbreviated_config_is_unknown(engine):
    result = observe({'args': ['--conf=omitted']}, engine)
    assert result['settings']['trust_remote_code'] == {'state': 'unknown', 'value': None}


def test_abbreviation_cannot_misparse_later_tokens_or_clear_previous_drift(tmp_path):
    from tools.k8s_observation_feed import _reduce_pod
    marker = 'D7_UNRELATED_ABBREVIATION_VALUE'
    args = ['--code-rev', marker, '--trust-remote-code']
    raw = pod(args)
    transported = summarize_pods({'items': [_reduce_pod(raw, 'engine')]}, 'engine', 'vllm')
    settings = transported['launch_settings']['settings']
    assert settings['code_revision'] == {'state': 'unknown', 'value': None}
    assert settings['trust_remote_code'] == {'state': 'unknown', 'value': None}
    assert marker not in json.dumps(transported)
    requests = RevalidationRequests(tmp_path)
    context = dict(observation_session_id='session', target_id='target', policy={})
    requests.observe_baseline({'rows': comparison_rows({'trust_remote_code': False}, collected(['--trust-remote-code']))}, **context)
    requests.observe_baseline({'rows': comparison_rows({'trust_remote_code': False}, collected(args))}, **context)
    assert requests.summary()['current_condition_count'] == 1
    assert len(requests.store.read_all()[0]) == 1
    assert marker not in json.dumps(requests.summary())
