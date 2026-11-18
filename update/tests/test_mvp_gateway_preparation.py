"""Saved paired inputs through the existing Runtime bundle and target loaders."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import socket
import subprocess

import pytest

from guard.models import Environment, canonical_json
from tests.test_mvp_metadata_wiring import sources
from tools import mvp_gateway_preparation as tool

NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)
START, END = '2026-11-18T00:00:00Z', '2026-11-20T00:00:00Z'
TARGET_ID, MODEL = 'fixture-owned-target', 'fixture-model'


def _write(path, value):
    raw = canonical_json(value)
    path.write_bytes(raw)
    return sha256(raw).hexdigest()


@pytest.fixture
def gateway_pair(tmp_path, sources):
    _, sides, _ = sources
    policy = {'schema_version': 'runtime-mvp/1.1', 'policy_id': 'fixture-policy',
              'policy_version': '1', 'target_id': TARGET_ID, 'allowed_models': [MODEL],
              'output_default_tokens': 1, 'output_max_tokens': 1, 'max_body_bytes': 8192,
              'max_context_tokens': 256, 'valid_from': START, 'valid_until': END, 'approval_ref': 'fixture-approval'}
    policy_sha = sha256(canonical_json(policy)).hexdigest()
    approval = {'policy_id': policy['policy_id'], 'policy_version': policy['policy_version'],
                'sha256': policy_sha, 'approval_ref': policy['approval_ref']}
    inputs = {}
    for side, paths in sides.items():
        environment = Environment.model_validate_json(paths['environment'].read_bytes())
        engine_target = json.loads(paths['target'].read_bytes())
        engine_target['kubernetes']['run_id'] = 'fixture-' + side
        _write(paths['target'], engine_target)
        directory = tmp_path / (side + '-bundle')
        directory.mkdir()
        documents = {
            'gateway_policy': policy,
            'policy_approvals': {'schema_version': 'runtime-policy-approvals/1.0',
                                 'entries': [{**approval, 'approved_at': START, 'valid_until': END}]},
            'target': {'schema_version': 'runtime-target/1.0', 'target_id': TARGET_ID,
                       'engine': {'kind': 'vllm', 'version': environment.engine.version,
                                  'image_digest': environment.engine.image_digest.rsplit('@', 1)[1]},
                       'model': {'id': MODEL, 'revision': environment.model.revision}},
        }
        files = []
        for role, value in documents.items():
            member = directory / (role + '.json')
            digest = _write(member, value)
            files.append({'role': role, 'path': member.name, 'schema_version': value['schema_version'],
                          'sha256': digest, 'bytes': member.stat().st_size})
        bundle_sha = _write(directory / 'bundle.json', {
            'schema_version': 'byul-policy-bundle/0.1', 'bundle_id': side + '-bundle', 'created_at': START,
            'policy_ref': {key: approval[key] for key in ('policy_id', 'policy_version', 'sha256')},
            'target_ref': TARGET_ID, 'approval': {'approved_by': 'Fixture owner', 'approved_at': START,
                                                  'approval_ref': approval['approval_ref']},
            'validity': {'valid_from': START, 'valid_until': END}, 'files': files,
        })
        inputs[side] = {**{key: str(path) for key, path in paths.items()},
                        'bundle': str(directory), 'bundle_sha256': bundle_sha, 'auth_secret': 'fixture-auth-' + side}
    revoked = tmp_path / 'revocations.json'
    digest = _write(revoked, {'schema_version': 'runtime-mvp-revocations/1.0', 'revoked': []})
    return inputs, revoked, digest


def test_paired_preparation_only_reads_and_keeps_sources_and_policy_exact(gateway_pair, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Gateway preparation must not run any network or subprocess operations')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    prepared = tool.prepare_pair(*gateway_pair, issued_at=NOW)
    assert prepared['baseline']['bundle_sha256'] != prepared['candidate']['bundle_sha256']
    assert prepared['baseline']['runtime_policy_sha256'] == prepared['candidate']['runtime_policy_sha256']
    for side, entry in prepared.items():
        pod, config, isolation = entry['deployment'].values()
        assert pod['kind'] == 'Pod' and config['kind'] == 'ConfigMap' and config['immutable'] is True
        container, = pod['spec']['containers']
        assert container['image'] == tool.GATEWAY_IMAGE and container['imagePullPolicy'] == 'Never'
        assert pod['spec']['automountServiceAccountToken'] is False
        assert {list(volume)[1] for volume in pod['spec']['volumes']} == {'configMap', 'secret', 'emptyDir'}
        assert isolation['spec']['ingress'] == []
        peer, = isolation['spec']['egress'][0]['to']
        assert peer == {'podSelector': {'matchLabels': {'app': 'serving-engine', 'guard.run': 'fixture-' + side}}}
        assert {json.loads(raw).get('schema_version') for raw in config['data'].values()} == {
            'byul-policy-bundle/0.1', 'runtime-target/1.0', 'runtime-backend/1.0', 'runtime-mvp/1.1',
            'runtime-policy-approvals/1.0', 'runtime-mvp-revocations/1.0',
        }
        assert 'auth.json' not in str(config) and 'cache-salt-key' not in str(config)


def test_both_engines_need_their_own_published_target_and_revocation_bytes(gateway_pair):
    inputs, revoked, digest = gateway_pair
    with pytest.raises(ValueError, match='GATEWAY_PUBLISHED_FILE_IDENTITY_MISMATCH'):
        tool.prepare_pair(inputs, revoked, '0' * 64, issued_at=NOW)
    inputs['candidate'].update({key: inputs['baseline'][key] for key in ('bundle', 'bundle_sha256')})
    with pytest.raises(ValueError, match='GATEWAY_PUBLISHED_TARGET_MISMATCH'):
        tool.prepare_pair(inputs, revoked, digest, issued_at=NOW)


def test_only_verified_saved_gateway_configuration_binds_the_target(gateway_pair):
    inputs, _, _ = gateway_pair
    prepared = tool.prepare_pair(*gateway_pair, issued_at=NOW)['baseline']
    env = Environment.model_validate(tool._source(inputs['baseline']['environment'])[0])
    target_path = inputs['baseline']['target']
    target, _ = tool._source(target_path)
    engine = target['kubernetes']['engine']
    namespace = {'metadata': {'name': engine['namespace'], 'uid': engine['namespace_uid'],
                              'labels': {'byul.lab/owned': 'true'}}}
    pod = deepcopy(prepared['deployment']['pod'])
    pod['metadata']['uid'] = '12345678-1234-1234-1234-0123456789ab'
    pod['spec']['nodeName'] = engine['node']
    pod['status'] = {'phase': 'Running', 'podIP': '10.1.4.77', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                     'containerStatuses': [{'name': 'gateway', 'imageID': tool.GATEWAY_IMAGE,
                                            'containerID': 'containerd://' + '9' * 64, 'restartCount': 0,
                                            'ready': True, 'state': {'running': {'startedAt': START}}}]}
    config = deepcopy(prepared['deployment']['configuration'])

    def bind():
        return tool.bind_gateway(target_path, env, prepared, namespace=namespace, pod=pod, configuration=config)

    result = bind()
    assert result['kubernetes_gateway']['pod_uid'] == pod['metadata']['uid']
    assert {key: value for key, value in result.items() if key != 'kubernetes_gateway'} == target
    config['data']['input-0'] = '{}'
    with pytest.raises(ValueError, match='GATEWAY_OBSERVED_CONFIGURATION_MISMATCH'):
        bind()
