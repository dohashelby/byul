"""EXAMPLE contracts for served aliases reproduced on the owned L4 engine."""
from copy import deepcopy
import json

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors.input_validation import InputValidationExecutor
from guard.executors.log_analysis import LogAnalysisExecutor
from guard.models import canonical_json
from tests.builders import baseline_pack, completion_response, environment
from tests.test_log_analysis import Source, VALUES as LOG_VALUES


@pytest.mark.parametrize('logical', ['input_validation', 'log_analysis'])
@pytest.mark.parametrize('reported', ['served-alias', 'repository/model', 'unrelated', None])
def test_content_collectors_bind_requests_and_controls_to_the_validated_alias(tmp_path, logical, reported):
    env = environment(engine={'name': 'vllm', 'version': '0.31.0'},
                      model={'id': 'repository/model', 'revision': 'a' * 40, 'inventory_sha256': 'b' * 64})
    before = canonical_json(env.to_dict())
    mapping = {'schema': 'guard.model-request-mapping/1', 'environment_ref': env.ref().model_dump(mode='json'),
               'repository_id': env.model.id, 'revision': env.model.revision, 'served_model_name': 'served-alias',
               'host_snapshot': '/srv/models/' + env.model.revision, 'container_mount': '/models',
               'inventory_sha256': env.model.inventory_sha256}
    target = {'lab': True, 'engine': {'trusted_url': 'http://engine:8000', 'container': 'engine'},
              'origins': {'trusted': {'network': 'inside'}}, 'model_mapping': mapping}
    path = tmp_path / 'target.json'
    path.write_text(json.dumps(target), encoding='utf-8')
    sent = []
    source = Source()

    def probe(network, requests, timeout):
        sent.extend(deepcopy(requests))
        if logical == 'log_analysis':
            replies = source.probe(network, requests, timeout)
        else:
            replies = [completion_response(request) if request['body']['prompt'] == [0] else
                       {'outcome': 400, 'body': json.dumps({'error': {'message': 'EXAMPLE rejected input'}})}
                       for request in requests]
        for reply in replies:
            if reply['outcome'] == 200:
                body = json.loads(reply['body'])
                if reported is None:
                    body.pop('model', None)
                else:
                    body['model'] = reported
                reply['body'] = json.dumps(body)
                reply.pop('body_bytes', None)
        return replies

    pack = baseline_pack()
    if logical == 'input_validation':
        check = pack.checks['REQ-09']
        values = {'max_input_tokens': 8, 'rejection_codes': ['400'],
                  'allowed_endpoints': ['POST /v1/completions'], 'allowed_input_formats': ['token_ids']}
        executor = InputValidationExecutor({'target': str(path), 'vocab_size': 100,
            'vocab_source': 'EXAMPLE fixture', 'max_probe_tokens': 16}, container_probe=probe)
        expected_calls = 5
    else:
        check = pack.checks['OBS-03']
        values = deepcopy(LOG_VALUES)
        executor = LogAnalysisExecutor({'target': str(path), 'log_sources': [
            {'id': 'engine', 'kind': 'docker_logs', 'container': '@engine', 'role': 'engine'}]},
            container_probe=probe, docker=source)
        expected_calls = 3
    draft = executor.run(check, env, values, VllmAdapter(), tmp_path / 'work')
    matched = reported == 'served-alias'
    assert draft.normal_control.succeeded is matched
    assert len(sent) == (expected_calls if matched else 1)
    assert all(request['body']['model'] == 'served-alias' for request in sent)
    assert draft.data_origin.value == 'EXAMPLE'
    assert canonical_json(env.to_dict()) == before
