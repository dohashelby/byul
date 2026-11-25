"""Saved paired inputs through the existing Runtime bundle and target loaders."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
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
    owner_suffix = 'a' if side == 'baseline' else 'b'
    pod['metadata']['uid'] = '12345678-1234-1234-1234-0123456789a' + owner_suffix
    pod['spec']['nodeName'] = engine['node']
    pod['status'] = {'phase': 'Running', 'podIP': '10.1.4.77', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                     'containerStatuses': [{'name': 'gateway', 'imageID': tool.GATEWAY_IMAGE,
                                            'containerID': 'containerd://' + owner_suffix * 64, 'restartCount': 0,
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


def _ordinary_receipts(directory, env, bound, prepared, owner, *, recorded_at=None):
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
    final['recorded_at'] = recorded_at or (NOW + timedelta(seconds=1)).isoformat()
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


@pytest.mark.parametrize('changed', [None, 'missing-side', 'journal', 'policy', 'owner'])
def test_retained_pair_reads_original_runtime_sources_without_new_authority(tmp_path, gateway_pair, monkeypatch, changed):
    def forbidden(*args, **kwargs):
        pytest.fail('Historical readback cannot start processes or make network requests')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    archived, revoked, revoked_sha256 = gateway_pair
    sources = {'schema': tool.RETAINED_INPUT_SCHEMA, 'revocations': {'path': str(revoked), 'sha256': revoked_sha256},
               'sides': {}}
    captured, originals = {}, {}
    source_recorded_at = '2026-11-19T00:00:00.000000650Z'

    for side, settings in archived.items():
        env, engine_target, _, prepared, owner = _gateway_owner(gateway_pair, side)
        bound = tool.bind_gateway(engine_target, env, prepared, **owner)
        saved = tmp_path / (side + '-saved')
        saved.mkdir()
        response, audit, journal = _ordinary_receipts(saved, env, bound, prepared, owner, recorded_at=source_recorded_at)
        response['observed_started_ns'] += 100
        response['observed_finished_ns'] = response['observed_started_ns'] + 400
        if changed == 'owner' and side == 'candidate':
            # Keep all raw pins valid but omit the actual captured generation.
            bound['kubernetes_gateway']['container_id'] = 'containerd://' + '7' * 64
            audit['gateway_target_sha256'] = sha256(canonical_json(bound)).hexdigest()
        selection = {key: {'path': str(saved / (key + '.json')), 'sha256': _write(saved / (key + '.json'), value)}
                     for key, value in [('response_receipt', response), ('audit_receipt', audit)]}
        selection['journal'] = {'path': str(journal), 'sha256': audit['journal_sha256']}
        observation, target = saved / 'selection.json', saved / 'target.json'
        env_path = Path(settings['environment'])
        entry = {'environment': {'path': str(env_path), 'sha256': sha256(env_path.read_bytes()).hexdigest()},
                 'target': {'path': str(target), 'sha256': _write(target, bound)},
                 'observation': {'path': str(observation), 'sha256': _write(observation, selection)},
                 'bundle': {'path': settings['bundle'], 'sha256': settings['bundle_sha256']}}
        sources['sides'][side] = entry
        originals.update({path: path.read_bytes() for path in (env_path, target, observation, journal)})
        captured[side] = (response, audit, selection)
        if changed == 'journal' and side == 'candidate':
            journal.write_bytes(originals[journal] + b'\n')
        if changed == 'policy' and side == 'candidate':
            policy = Path(settings['bundle']) / 'gateway_policy.json'
            policy.write_bytes(policy.read_bytes() + b'\n')
    if changed == 'missing-side':
        sources['sides'].pop('candidate')

    inputs = tmp_path / 'inputs.json'
    output = tmp_path / 'historical'
    inputs_sha256 = _write(inputs, sources)
    monkeypatch.setattr(sys, 'argv', ['mvp_gateway_preparation', 'audit-retained', '--inputs', str(inputs),
                                     '--inputs-sha256', inputs_sha256, '--output', str(output)])
    if changed is not None:
        with pytest.raises(SystemExit, match='2'):
            tool.main()
        assert not output.exists()
        return

    tool.main()
    evidence, = output.iterdir()
    assert evidence.name == 'obs01-retained-audit.json' and evidence.stat().st_mode & 0o777 == 0o600
    result = json.loads(evidence.read_bytes())
    assert result['schema'] == tool.RETAINED_AUDIT_SCHEMA
    assert result['inputs_sha256'] == inputs_sha256 and result['revocations_sha256'] == revoked_sha256
    assert result['official_evidence'] is False and result['current_authority'] is False
    for side, verified in result['sides'].items():
        response, audit, selection = captured[side]
        assert verified['input_sha256'] == {key: row['sha256'] for key, row in selection.items()}
        assert verified['observed_actor_sha256'] == audit['actor_sha256']
        assert verified['observed_session_sha256'] == sha256(audit['session'].encode()).hexdigest()
        assert verified['request_sha256'] == response['request_sha256']
        assert verified['selected_row_sha256'] == audit['selected_row_sha256']
        assert verified['source_recorded_at'] == source_recorded_at
        assert verified['response_status'] == 200 and verified['environment_ref'] == audit['environment_ref']
        assert evidence.read_bytes().find(response['request_id'].encode()) == -1
    assert {path: path.read_bytes() for path in originals} == originals


def test_observation_time_checks_use_full_owner_and_policy_precision():
    epoch = int(NOW.timestamp()) * 10**9
    policy = {'valid_from': '2026-11-19T00:00:00.000000100Z', 'valid_until': '2026-11-19T00:00:00.000000900Z'}
    target = {'kubernetes': {'engine': {'started_at': START}},
              'kubernetes_gateway': {'started_at': '2026-11-19T00:00:00.000000123Z'}}
    response = {'observed_started_ns': epoch + 123, 'observed_finished_ns': epoch + 345}
    tool._check_observation_times(response, '2026-11-19T00:00:00.000000899Z', target, policy)
    with pytest.raises(ValueError, match='GATEWAY_ORDINARY_OBSERVATION_INVALID'):
        tool._check_observation_times(response, policy['valid_until'], target, policy)
    response['observed_started_ns'] -= 1
    with pytest.raises(ValueError, match='GATEWAY_ORDINARY_OBSERVATION_INVALID'):
        tool._check_observation_times(response, '2026-11-19T00:00:00.000000899Z', target, policy)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("fault", [None, "namespace", "other-run", "late-owner", "incomplete"])
@pytest.mark.parametrize("loopback", [False, True], ids=["probe-pod", "gateway-loopback"])
def test_owned_normal_response_capture_keeps_the_gateway_interval(gateway_pair, monkeypatch, tmp_path, side, fault, loopback):
    from guard.models import load_pack, sha256_hex
    from guard.runtime import kubernetes_http as http
    from tests.builders import policy
    from tests.test_kubernetes_response_snapshot import VALUES
    from tests.test_model_preparation_integration import FullWorld
    from tools import mvp_req13_gateway_inputs as intake

    env, target_path, target, prepared, owned = _gateway_owner(gateway_pair, side)
    gateway = tool.bind_gateway(target_path, env, prepared, **owned)["kubernetes_gateway"]
    env = env.model_copy(update={"data_origin": DataOrigin.EXAMPLE}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["model_mapping"]["environment_ref"] = target["environment_ref"]
    world = FullWorld(target)
    auth = "Bearer EXAMPLE-private-auth"
    request_id = "fixture-request-" + side
    body = canonical_json({"id": request_id, "model": MODEL, "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "fixture-ordinary-response"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}).decode("utf-8")
    reply = {
        "id": "obs01-before", "mode": "json", "method": "POST", "outcome": 200,
        "body": body, "body_bytes": len(body), "body_sha256": sha256_hex(body.encode()),
        "body_truncated": False, "utf8_valid": True, "capture_complete": fault != "incomplete",
        "eof": fault != "incomplete", "content_type": "application/json",
        "response_headers": {"x-request-id": request_id},
    }
    calls = []

    def runner(argv, **kwargs):
        if "get" in argv and "namespace" in argv:
            value = {"kind": "Namespace", **deepcopy(owned["namespace"])}
            if fault == "namespace":
                value["metadata"]["uid"] = "00000000-0000-0000-0000-000000000000"
            calls.append("namespace")
        elif "get" in argv and gateway["pod"] in argv:
            value = {"kind": "Pod", **deepcopy(owned["pod"])}
            if fault == "other-run":
                value["metadata"]["labels"]["guard.run"] = "fixture-unselected-run"
            if fault == "late-owner" and "response" in calls:
                value["status"]["containerStatuses"][0]["containerID"] = "containerd://" + "f" * 64
            calls.append("gateway")
        elif "exec" in argv:
            request, = json.loads(kwargs["input_bytes"])["requests"]
            sender = gateway if loopback else target["kubernetes"]["probe"]
            assert argv[3:9] == [f"--namespace={sender['namespace']}", "exec", "-i", sender["pod"],
                                 f"--container={sender['container']}", "--"]
            python = ["python3", "-I", "-S", "-B", "-c"] if loopback else ["python3", "-c"]
            assert argv[9:9 + len(python)] == python
            host = "127.0.0.1" if loopback else gateway["pod_ip"]
            assert request["url"] == f"http://{host}:8080/v1/chat/completions"
            assert request["method"] == "POST" and request["headers"] == {"Authorization": auth}
            assert request["body"] == {"model": MODEL, "messages": [{"role": "user", "content": "hello"}],
                                       "max_tokens": 1, "stream": False}
            calls.append("response")
            value = [reply]
        elif "get" in argv and "namespaces" in argv:
            value = {"kind": "Namespace", **deepcopy(owned["namespace"])}
        else:
            assert "get" in argv
            reviewed = world.runner(argv, **kwargs)
            value = json.loads(reviewed["stdout_bytes"])
            value["metadata"]["labels"]["guard.run"] = target["kubernetes"]["run_id"]
        return {"returncode": 0, "stdout_bytes": canonical_json(value), "capture": {"complete": True}}

    def no_external_io(*args, **kwargs):
        pytest.fail("Only the test runner may interact with the Gateway capture")

    monkeypatch.setattr(socket, "socket", no_external_io)
    monkeypatch.setattr(subprocess, "Popen", no_external_io)
    base = int(NOW.timestamp()) * 10**9
    before, start, finish, after = range(base, base + 4)
    monkeypatch.setattr(http.time, "time_ns", iter((before, start, finish, after)).__next__)
    collector = http.KubernetesHTTP(target, env, duration=10, runner=runner,
                                    profile=http.GATEWAY_EVENT_PROFILE, retain_gateway_response=True)
    capture_options = {"loopback": True} if loopback else {}
    if fault is not None:
        with pytest.raises(http.KubernetesBindingError):
            collector.capture_retained_gateway_response(gateway, auth, timeout=1, **capture_options)
        assert collector.report()["collection_gap"]
        assert calls.count("response") == (0 if fault in {"namespace", "other-run"} else 1)
        return

    if loopback:
        wrong_image = "docker.io/library/byul-gateway-codex@sha256:" + "f" * 64
        with pytest.raises(http.KubernetesBindingError):
            collector.capture_retained_gateway_response({**gateway, "image": wrong_image}, auth,
                                                        timeout=1, loopback=True)
        assert not calls
        collector = http.KubernetesHTTP(target, env, duration=10, runner=runner,
                                        profile=http.GATEWAY_EVENT_PROFILE, retain_gateway_response=True)
    captured = collector.capture_retained_gateway_response(gateway, auth, timeout=1, **capture_options)
    assert calls == ["namespace", "gateway", "response", "namespace", "gateway"]
    projection = verify_owned_pod(owned["pod"], owned["namespace"]["metadata"], gateway,
                                  target["kubernetes"]["run_id"])
    assert captured["gateway_owners"] == {
        "before": {"observed_at_ns": before, "owner": projection},
        "after": {"observed_at_ns": after, "owner": projection},
    }
    assert set(captured) == {"response", "transport_report", "gateway_owners"}
    row, = captured["transport_report"]["requests"]
    receipt = row["retained_normal_response"]
    assert receipt["observed_started_ns"] == start and receipt["observed_finished_ns"] == finish
    assert receipt["response_sha256"] == sha256_hex(canonical_json(captured["response"]))
    assert receipt["data_origin"] == "EXAMPLE"
    if loopback:
        assert receipt["schema"] == http.GATEWAY_LOOPBACK_RESPONSE_RECEIPT
        assert receipt["transport_origin"] == http.GATEWAY_LOOPBACK_RESPONSE_ORIGIN
        assert receipt["source_owner_sha256"] == sha256_hex(canonical_json(projection))
    else:
        assert receipt["schema"] == http.GATEWAY_NORMAL_RESPONSE_RECEIPT
        assert "transport_origin" not in receipt
    assert auth.encode() not in canonical_json(captured)
    assert body.encode() not in canonical_json(captured["transport_report"])
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    pol = policy(pack.ref(), scope={"include": ["REQ-13"]}, values={"REQ-13": {**VALUES,
                    "allowed_response_fields": [*VALUES["allowed_response_fields"], "object"]}},
                 status="CONFIRMED", confirmed_by="EXAMPLE approver", confirmed_at=NOW - timedelta(minutes=2),
                 valid_from=NOW - timedelta(minutes=1), valid_until=NOW + timedelta(minutes=1))

    def save(name, value):
        path = tmp_path / name
        return {"path": str(path), "sha256": _write(path, value)}

    prep = {**prepared, "environment_ref": target["environment_ref"], "engine_target": save("engine.json", target)}
    inputs = {"environment": env.to_dict(), "target": {**target, "kubernetes_gateway": gateway},
              "guard_policy": pol.to_dict(), "preparation": {"image": tool.GATEWAY_IMAGE,
              "archive_sha256": tool.ARCHIVE_SHA256, "producer_sha256": tool.PRODUCER_SHA256,
              "gateway_environment": tool.GATEWAY_ENV, "sides": {side: prep}}, **captured}
    refs = {name: save(name + ".json", value) for name, value in inputs.items()}
    source = save("source.json", {
        "schema": intake.RESPONSE_ONLY_SOURCE_SCHEMA, "files": refs, "side": side,
        "request_sha256": sha256_hex(request_id.encode()),
    })
    output = tmp_path / "captured-response-only"
    selected = intake.prepare(source["path"], source["sha256"], output, pack=pack)
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["response"] == refs["response"] and manifest["gateway_owners"] == refs["gateway_owners"]
    selection = selected["gateway_response_inputs"]
    assert selection["request_sha256"] == sha256_hex(request_id.encode())
    assert selection.get("transport_origin") == (http.GATEWAY_LOOPBACK_RESPONSE_ORIGIN if loopback else None)
    if loopback:
        drifted_report = deepcopy(captured["transport_report"])
        drifted_report["requests"][0]["retained_normal_response"]["source_owner_sha256"] = "0" * 64
        changed = save("changed-source.json", {
            "schema": intake.RESPONSE_ONLY_SOURCE_SCHEMA, "side": side, "request_sha256": selection["request_sha256"],
            "files": {**refs, "transport_report": save("changed-transport.json", drifted_report)},
        })
        rejected = tmp_path / "rejected-loopback"
        with pytest.raises(ValueError, match="^REQ13_GATEWAY_CAPTURE_SOURCE_INVALID$"):
            intake.prepare(changed["path"], changed["sha256"], rejected, pack=pack)
        assert not rejected.exists()
