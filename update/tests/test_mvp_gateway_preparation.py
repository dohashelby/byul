"""Saved paired inputs through the existing Runtime bundle and target loaders."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.executors.kubernetes_gateway_observation import principal_sha256, snapshot_record
from guard.models import DataOrigin, Environment, canonical_json
from guard.models.objects import FactValue
from guard.runtime.kubernetes_binding import verify_owned_pod
from guard.store import Store
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
        binding = engine_target['kubernetes']
        binding['probe'].update(namespace=binding['engine']['namespace'], namespace_uid=binding['engine']['namespace_uid'])
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


def _gateway_owner(gateway_pair, side):
    inputs, _, _ = gateway_pair
    prepared = tool.prepare_pair(*gateway_pair, issued_at=NOW)[side]
    env = Environment.model_validate(tool._source(inputs[side]['environment'])[0])
    target_path = inputs[side]['target']
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
    return env, target_path, target, prepared, {'namespace': namespace, 'pod': pod, 'configuration': config}


def test_only_verified_saved_gateway_configuration_binds_the_target(gateway_pair):
    env, target_path, target, prepared, owner = _gateway_owner(gateway_pair, 'baseline')

    def bind():
        return tool.bind_gateway(target_path, env, prepared, **owner)

    result = bind()
    assert result['kubernetes_gateway']['pod_uid'] == owner['pod']['metadata']['uid']
    assert {key: value for key, value in result.items() if key != 'kubernetes_gateway'} == target
    owner['configuration']['data']['input-0'] = '{}'
    with pytest.raises(ValueError, match='GATEWAY_OBSERVED_CONFIGURATION_MISMATCH'):
        bind()


def _ordinary_receipts(directory, env, bound, prepared, owner):
    from serving_runtime.gateway.request_records import RequestRecords
    from serving_runtime.gateway.validator import Decision

    target_id, run = prepared['runtime_target_id'], bound['kubernetes']['run_id']
    session, request_id, principal = 'b' * 32, 'c' * 32, 'fixture-approved-actor'
    route = 'POST /v1/chat/completions'
    policy_sha = prepared['runtime_policy_sha256']
    policy = SimpleNamespace(target_id=target_id, schema_version='runtime-mvp/1.1', profile=None,
                             policy_id='fixture-policy', policy_version='1', cache_isolation=None)
    state = SimpleNamespace(policy=policy, sha256=policy_sha, path='fixture-policy.json')
    writer = RequestRecords(directory, session, lambda: state, 'vllm', environment=tool.GATEWAY_ENV, run_id=run,
                            target_context=lambda: {'target_id': target_id, 'registration': 'ok'})
    writer.clear_context(route)
    writer.bind_context(state)
    writer.set_auth({'state': 'authenticated', 'principal_id': principal})
    decision = Decision('ALLOWED', 200, 'ALLOWED', None, 'allowed', MODEL, 1)
    final = writer.build('final', request_id, decision, attempted=True, backend_receipt='not_available',
                         response='completed', error_code=None, final_outcome='completed')
    final['recorded_at'] = (NOW + timedelta(seconds=1)).isoformat()
    assert writer.append(final)
    journal = directory / 'gateway_records.jsonl'
    journal_raw = journal.read_bytes()
    request_sha = sha256(request_id.encode()).hexdigest()
    actor_sha = principal_sha256(principal)
    source = {'environment': tool.GATEWAY_ENV, 'target_id': target_id, 'runtime_policy_sha256': policy_sha,
              'request_sha256': request_sha}
    _, row_sha, recorded_at = snapshot_record(journal_raw, source=source, run_id=run,
        values={'observation_session': session, 'observation_allowed_actor_sha256': actor_sha})
    gateway_owner = verify_owned_pod(owner['pod'], owner['namespace']['metadata'], bound['kubernetes_gateway'], run)
    common = {'environment_ref': env.ref().model_dump(mode='json'), 'request_sha256': request_sha,
              'engine_owner_sha256': tool._owner_sha256(bound['kubernetes']['engine']),
              'gateway_owner_sha256': sha256(canonical_json(gateway_owner)).hexdigest()}
    response = {**common, 'body_sha256': sha256(b'ordinary fixture response').hexdigest(), 'body_bytes': 25,
                'status': 200, 'request_id': request_id, 'route': route, 'run_sha256': sha256(run.encode()).hexdigest(),
                'observed_started_ns': int(NOW.timestamp()) * 10**9,
                'observed_finished_ns': int(NOW.timestamp()) * 10**9 + 100_000_000}
    audit = {**common, 'event': 'ACCESS_ALLOWED', 'gateway_target_sha256': sha256(canonical_json(bound)).hexdigest(),
             'owner_before_after_verified': True, 'producer_sha256': tool.PRODUCER_SHA256,
             'runtime_policy_sha256': policy_sha, 'journal_bytes': len(journal_raw),
             'journal_sha256': sha256(journal_raw).hexdigest(), 'selected_row_sha256': row_sha,
             'recorded_at': recorded_at, 'actor_sha256': actor_sha, 'session': session,
             'independent_denied_event_observed': False}
    return response, audit, journal


@pytest.mark.parametrize(('side', 'changed'), [('baseline', None), ('candidate', None),
    ('candidate', 'gateway_owner_sha256'), ('candidate', 'observed_finished_ns'), ('candidate', 'journal')])
def test_bind_publishes_only_same_owner_normal_gateway_fact(tmp_path, gateway_pair, monkeypatch, side, changed):
    def forbidden(*args, **kwargs):
        pytest.fail('Saved observation binding must not send requests or invoke subprocesses')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    env, target_path, _, prepared, owner = _gateway_owner(gateway_pair, side)
    bound = tool.bind_gateway(target_path, env, prepared, **owner)
    response, audit, journal = _ordinary_receipts(tmp_path, env, bound, prepared, owner)
    if changed == 'gateway_owner_sha256':
        response[changed] = audit[changed] = '0' * 64
    if changed == 'observed_finished_ns':
        response[changed] = response['observed_started_ns'] - 1
    response_path, audit_path = tmp_path / 'response.json', tmp_path / 'audit.json'
    selection = {'response_receipt': {'path': str(response_path), 'sha256': _write(response_path, response)},
                 'audit_receipt': {'path': str(audit_path), 'sha256': _write(audit_path, audit)},
                 'journal': {'path': str(journal), 'sha256': sha256(journal.read_bytes()).hexdigest()}}
    selection_path = tmp_path / 'selection.json'
    selection_sha = _write(selection_path, selection)
    if changed == 'journal':
        journal.write_bytes(journal.read_bytes() + b'\n')

    prep_dir = tmp_path / 'prepared'
    prep_dir.mkdir()
    preparation = prep_dir / 'preparation.json'
    preparation_sha = _write(preparation, {'image': tool.GATEWAY_IMAGE, 'producer_sha256': tool.PRODUCER_SHA256,
        'gateway_environment': tool.GATEWAY_ENV, 'sides': {side: prepared}})
    output = tmp_path / 'output'
    environment = gateway_pair[0][side]['environment']
    sources = []
    for key, obj in owner.items():
        path = tmp_path / (key + '.json')
        _write(path, obj)
        sources.extend(['--' + key, str(path)])
    argv = ['mvp_gateway_preparation', 'bind', '--side', side, '--preparation', str(preparation),
            '--preparation-sha256', preparation_sha, '--environment', environment, '--output', str(output), *sources]
    original_env = tool._source(environment)[0]
    original_target = tool._source(target_path)[0]
    monkeypatch.setattr(sys, 'argv', [*argv, '--observation', str(selection_path), '--observation-sha256', selection_sha])
    if changed is not None:
        with pytest.raises(SystemExit, match='2'):
            tool.main()
        assert not output.exists()
        return
    tool.main()
    derived = Environment.model_validate_json((output / 'environment.json').read_bytes())
    target = json.loads((output / 'target.json').read_bytes())
    provenance_raw = (output / 'gateway-observation.json').read_bytes()
    assert derived.hash_ok() and derived.id != env.id and derived.revision == 1
    assert derived.refs == env.refs
    assert Store(tmp_path / 'store').put(derived) == derived.ref()
    assert derived.facts['gateway'] == FactValue(value=True, origin=DataOrigin.DECLARED,
        source='gateway-observation.json#sha256=' + sha256(provenance_raw).hexdigest())
    assert json.loads(provenance_raw)['environment_ref'] == env.ref().model_dump(mode='json')
    source = json.loads((output / 'gateway-source.json').read_bytes())
    assert source['request_sha256'] == response['request_sha256']
    assert target['environment_ref'] == target['model_mapping']['environment_ref'] == derived.ref().model_dump(mode='json')
    assert target['kubernetes_gateway'] == bound['kubernetes_gateway']
    assert tool._source(environment)[0] == original_env and tool._source(target_path)[0] == original_target

    for fact in (FactValue(value=False), FactValue(conflict=[True, False])):
        existing = env.model_copy(update={'facts': {**env.facts, 'gateway': fact}}).seal()
        with pytest.raises(ValueError, match='GATEWAY_ENVIRONMENT_FACT_ALREADY_BOUND'):
            tool._observation_environment(existing, bound, prepared, selection_path, selection_sha)
    legacy = tmp_path / 'legacy'
    argv[argv.index('--output') + 1] = str(legacy)
    monkeypatch.setattr(sys, 'argv', argv)
    tool.main()
    assert sorted(path.name for path in legacy.iterdir()) == ['gateway-source.json', 'target.json']
    assert json.loads((legacy / 'target.json').read_bytes()) == bound
    assert json.loads((legacy / 'gateway-source.json').read_bytes()) == {
        key: value for key, value in source.items() if key != 'request_sha256'}
