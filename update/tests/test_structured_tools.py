"""EXAMPLE transport/validator contracts, never installed vLLM measurements."""
from copy import deepcopy
import json

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors.structured_tools import (SOURCE_COMMIT, CASE_IDS, StructuredToolsExecutor,
    cases, judge_params, project_validator, validate_request_class)
from guard.judges.rule_table import RuleTableJudge
from guard.models import Verdict
from tests.builders import environment, evidence
from tests.test_sampling_validation import check


class ValidationRejected(Exception):
    pass


def test_fixtures_share_schema_tool_and_only_named_json_is_forbidden():
    inputs = cases("fixture-model")
    assert tuple(inputs) == CASE_IDS
    for key, body in inputs.items():
        assert body['tools'] == inputs['auto']['tools']
        if key != 'named-only':
            assert body['structured_outputs'] == inputs['auto']['structured_outputs']
    inputs['auto']['tools'].clear()
    assert len(cases('fixture-model')['auto']['tools']) == 1


def fake_class(overrides):
    class Request:
        @staticmethod
        def model_validate(body):
            key = body['tool_choice'] if isinstance(body['tool_choice'], str) else (
                'named-json' if 'structured_outputs' in body else 'named-only')
            action = overrides.get(key, 'rejected' if key == 'named-json' else 'accepted')
            if action == 'rejected':
                raise ValidationRejected('private request')
            if action == 'error':
                raise RuntimeError('private exception')
            obj = Request()
            obj.tool_choice = body['tool_choice']
            return obj
    return Request


@pytest.mark.parametrize('action,expected,collected', [('accepted', True, True), ('rejected', False, True), ('error', None, False)])
def test_request_class_projection(action, expected, collected):
    result = validate_request_class(fake_class({'named-json': action}), ValidationRejected, model='fixture-model')
    assert all(result[k] == 'accepted' for k in CASE_IDS if k != 'named-json')
    assert project_validator(result)['structured_tools_validator_violation'] == {'value': expected, 'collected': collected}
    assert 'private' not in json.dumps(result)


def judge(obs):
    return RuleTableJudge().judge(check(), evidence(check_id='REQ-10', observations=obs), judge_params(), {})


def test_independent_violation_survives_missing_or_failed_controls():
    obs = project_validator({'named-json': 'accepted'})
    assert judge(obs).verdicts['security'] == Verdict.FAIL
    obs['structured_tools_controls'] = {'value': {'after': 'unknown'}, 'collected': True}
    assert judge(obs).verdicts['security'] == Verdict.FAIL
    assert judge(obs).verdicts['functional'] == Verdict.UNKNOWN


@pytest.mark.parametrize('outcomes', [{}, {'named-json': 'execution_error'}, {'named-json': 'rejected'}])
def test_no_security_pass_from_boundary_rejection_or_missing_data(outcomes):
    assert judge(project_validator(outcomes)).verdicts['security'] == Verdict.UNKNOWN


def run(tmp_path, *, override=None, supported=True, malformed=False):
    calls = []
    def probe(network, requests, timeout):
        request, = requests
        calls.append(deepcopy(request))
        key = request['id'].removeprefix('structured-tools-')
        action = (override or {}).get(key, 400 if key == 'named-json' else 200)
        if action == 'raise':
            raise RuntimeError('private exception')
        if action == 'missing':
            return []
        body = {'model': 'fixture-model', 'choices': [{'message': {'role': 'assistant', 'content': 'private response'},
                'finish_reason': 'length'}], 'usage': {'completion_tokens': 1}}
        return [{'id': request['id'], 'outcome': action, 'body': 'bad' if malformed else json.dumps(body),
                 'body_preview': 'private response'}]
    target = tmp_path / 'target.json'
    target.write_text(json.dumps({'lab': True, 'origins': {'trusted': {'network': 'trusted-net'}},
                                'engine': {'trusted_url': 'http://engine:8000'}}))
    executor = StructuredToolsExecutor({'target': str(target), 'source_commit': SOURCE_COMMIT,
                                      'backend': None, 'supported': supported}, container_probe=probe)
    draft = executor.run(check(), environment(model={'id': 'fixture-model'}), {}, VllmAdapter(), tmp_path / 'work')
    return draft, calls


def test_http_reuses_existing_draft_and_rule_table_without_promoting_400(tmp_path):
    draft, calls = run(tmp_path)
    assert len(calls) == 7
    assert draft.data_origin == 'EXAMPLE'
    assert draft.normal_control.succeeded
    obs = {k: v.model_dump() for k, v in draft.observations.items()}
    assert judge(obs).verdicts['functional'] == Verdict.PASS
    assert judge(obs).verdicts['security'] == Verdict.UNKNOWN
    assert not draft.observations['runner_consumption_verified'].value
    log = draft.artifacts[0].path.read_text()
    for raw in ['private response', 'Return a location.', 'private exception', 'body_preview']:
        assert raw not in log


@pytest.mark.parametrize('action', [200, 500, 'raise', 'missing', True])
def test_http_challenge_status_cannot_prove_validator_violation(tmp_path, action):
    draft, _ = run(tmp_path, override={'named-json': action})
    assert not draft.observations['structured_tools_validator_violation'].collected


@pytest.mark.parametrize('action', [500, 'raise', 'missing', True])
def test_after_failure_is_preserved(tmp_path, action):
    draft, _ = run(tmp_path, override={'after': action})
    assert not draft.normal_control.succeeded
    assert draft.observations['structured_tools_controls'].value['after'] == 'unknown'


def test_unsupported_target_sends_nothing_and_remains_unknown(tmp_path):
    draft, calls = run(tmp_path, supported=None)
    assert not calls and not draft.normal_control.attempted
    assert not draft.observations['structured_tools_http_outcomes'].collected
    assert all(not c.attempted for c in draft.challenges)


def test_malformed_control_stops_before_challenge(tmp_path):
    draft, calls = run(tmp_path, malformed=True)
    assert len(calls) == 1 and not draft.normal_control.succeeded
    assert all(not c.attempted for c in draft.challenges)


def test_api_alias_is_used_for_request_and_response_without_changing_repository_identity(tmp_path):
    from tests.test_model_mapping import declared_mapping
    env, mapping = declared_mapping()
    calls = []
    def probe(network, requests, timeout):
        request, = requests
        calls.append(request)
        return [{'id': request['id'], 'outcome': 400 if request['id'].endswith('named-json') else 200,
            'body': json.dumps({'model': mapping['served_model_name'], 'choices': [{'message': {'role': 'assistant',
                'content': 'ok'}, 'finish_reason': 'stop'}], 'usage': {'completion_tokens': 1}})}]
    target = tmp_path / 'target.json'
    target.write_text(json.dumps({'lab': True, 'origins': {'trusted': {'network': 'trusted-net'}},
        'engine': {'trusted_url': 'http://engine:8000'}, 'model_mapping': mapping}))
    executor = StructuredToolsExecutor({'target': str(target), 'source_commit': SOURCE_COMMIT,
        'backend': None, 'supported': True}, container_probe=probe)
    before = env.ref()
    draft = executor.run(check(), env, {}, VllmAdapter(), tmp_path / 'work')
    assert len(calls) == 7 and all(c['body']['model'] == mapping['served_model_name'] for c in calls)
    assert draft.normal_control.succeeded
    assert env.ref() == before and env.model.id == mapping['repository_id']
