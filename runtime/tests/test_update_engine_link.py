"""Exact synthetic EVD/runtime joins; no native execution is claimed."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from guard.models.objects import Evidence
from serving_runtime.mvp.update_engine_link import link_evidence
from serving_runtime.mvp.update_engine_link import project_termination

RID = 'a' * 32
AT = '2026-11-06T01:00:00+00:00'
REF = {'kind': 'ENV', 'id': 'ENV-example', 'revision': 1, 'sha256': '1' * 64}


def case(check='LIF-10'):
    context = {'request_id': RID, 'upstream_request_id': 'byul-cb-' + RID,
               'observation_session_id': 'session', 'run_id': 'run-one', 'environment': 'lab',
               'backend_kind': 'vllm', 'target_id': 'target-one',
               'target': {'pod_uid': 'pod-one', 'container_id': 'container-one',
                          'image_digest': 'sha256:' + '2' * 64, 'instance_id': 'instance-one'},
               'policy': {'sha256': '3' * 64}, 'policy_bundle': {'manifest_sha256': '4' * 64},
               'route': 'POST /v1/completions'}
    records = [{**deepcopy(context), 'phase': phase, 'recorded_at': AT,
                'final_outcome': 'client_disconnected', 'backend_cancel': 'not_observed'}
               for phase in ('started', 'final')]
    binding = {'context': 'cluster-one', 'run_id': 'run-one', 'engine': {
        'pod_uid': 'pod-one', 'container_id': 'container-one', 'image_id': 'sha256:' + '2' * 64}}
    payload = {'native': {'transport': {'binding': binding, 'collection_gap': False}},
               'native_projection': {'anchor': {'asgi_request_sha256': hashlib.sha256(('byul-cb-' + RID).encode()).hexdigest()},
                                     'worker_gpu_release_verified': False}}
    return records, payload, check


def evd_for(payload, check):
    raw = json.dumps(payload).encode()
    evd = Evidence(id='EVD-example', data_origin='EXAMPLE',
        plan={**REF, 'kind': 'PLAN', 'id': 'PLAN-example'}, environment=REF,
        check_id=check, side='single', execution={'status': 'COMPLETED', 'started_at': AT, 'finished_at': AT},
        normal_control={'attempted': True, 'succeeded': True},
        observations={'worker_gpu_release_verified': {'value': False, 'collected': True}},
        artifacts=[{'name': 'native.json', 'path': 'native.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                    'bytes': len(raw), 'collected_at': AT, 'collector': 'native'}]).seal()
    return evd, raw


def test_exact_refs_and_original_observations_survive_correlation():
    records, payload, check = case()
    evd, raw = evd_for(payload, check)
    linked = link_evidence(records, RID, evd, 'native.json', raw)
    assert linked['binding']['state'] == 'bound'
    assert linked['evidence_ref'] == evd.ref().model_dump(mode='json')
    assert linked['observations']['worker_gpu_release_verified']['value'] is False
    assert linked['data_origin'] == 'EXAMPLE' and linked['actual_engine_execution_verified'] is False
    assert linked['new_security_verdict'] is None


def test_actual_kubernetes_repository_imageid_matches_runtime_digest():
    records, payload, check = case()
    payload['native']['transport']['binding']['engine']['image_id'] = 'docker.io/vllm/vllm-openai@sha256:' + '2' * 64
    evd, raw = evd_for(payload, check)
    linked = link_evidence(records, RID, evd, 'native.json', raw)
    assert linked['binding']['target_generation_observed'] is True
    assert linked['binding']['state'] == 'bound'
    assert linked['artifact']['sha256'] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('key,value,expected', [
    ('container_id', 'other-container', 'UPDATE_TARGET_GENERATION_UNBOUND'),
    ('image_id', 'other-image', 'UPDATE_TARGET_GENERATION_UNBOUND'),
    ('pod_uid', 'other-pod', 'UPDATE_TARGET_GENERATION_UNBOUND')])
def test_other_generations_cannot_supply_this_requests_evidence(key, value, expected):
    records, payload, check = case()
    payload['native']['transport']['binding']['engine'][key] = value
    evd, raw = evd_for(payload, check)
    linked = link_evidence(records, RID, evd, 'native.json', raw)
    assert expected in linked['binding']['gaps']


def test_client_close_alone_never_proves_native_termination():
    records, payload, check = case()
    payload['native_projection'] = {}
    payload['unrelated'] = {'asgi_request_sha256': hashlib.sha256(('byul-cb-' + RID).encode()).hexdigest()}
    evd, raw = evd_for(payload, check)
    linked = link_evidence(records, RID, evd, 'native.json', raw)
    assert 'UPDATE_NATIVE_REQUEST_UNBOUND' in linked['binding']['gaps']


def test_tampered_artifact_and_unsealed_evidence_are_rejected():
    records, payload, check = case()
    evd, raw = evd_for(payload, check)
    with pytest.raises(ValueError, match='BYTES_MISMATCH'):
        link_evidence(records, RID, evd, 'native.json', raw + b' ')
    evd.check_id = 'REQ-12'
    with pytest.raises(ValueError, match='SEALED'):
        link_evidence(records, RID, evd, 'native.json', raw)


def test_changed_policy_or_duplicate_final_does_not_merge_attempts():
    records, payload, check = case()
    evd, raw = evd_for(payload, check)
    records[-1]['policy']['sha256'] = 'changed'
    with pytest.raises(ValueError, match='CONTEXT_CHANGED'):
        link_evidence(records, RID, evd, 'native.json', raw)
    records, payload, check = case()
    with pytest.raises(ValueError, match='UNIQUE_REQUEST'):
        link_evidence(records + [records[-1]], RID, evd, 'native.json', raw)


@pytest.mark.parametrize('check', ['LIF-07', 'LIF-08', 'LIF-09', 'LIF-03', 'LIF-04'])
def test_worker_and_pressure_join_generation_and_interval_without_new_verdict(check):
    records, payload, _ = case(check)
    payload.pop('native_projection')
    evd, raw = evd_for(payload, check)
    result = link_evidence(records, RID, evd, 'native.json', raw)
    assert result['binding']['state'] == 'bound'
    assert result['binding']['native_request_observed'] is False
    assert result['new_security_verdict'] is None


def test_termination_projector_receives_exact_upstream_id_and_keeps_native_gaps(monkeypatch):
    from guard.adapters import vllm_request_termination as producer
    records, _payload, _check = case()
    calls = []
    real = producer.project_native
    def observe(packet, selected, maximum, scopes):
        calls.append((packet, selected, maximum, scopes))
        return real(packet, selected, maximum, scopes)
    monkeypatch.setattr(producer, 'project_native', observe)
    result = project_termination(records, RID, None, 100, ['scheduler_state', 'kv_references'])
    assert calls[0][1] == hashlib.sha256(('byul-cb-' + RID).encode()).hexdigest()
    assert result['gateway_terminated'] is True
    assert result['native_projection']['binding_observed'] is False
    assert result['worker_gpu_release_verified'] is False
    assert 'TERMINATION_NATIVE_PACKET_UNAVAILABLE' in result['native_projection']['diagnostic_codes']


def test_unsupported_chat_native_contract_remains_explicit():
    records, _payload, _check = case()
    for row in records:
        row['route'] = 'POST /v1/chat/completions'
    result = project_termination(records, RID, {'recordings': []}, 100, ['scheduler_state'])
    assert result['support'] == 'unsupported_native_route_or_engine'
    assert result['native_projection']['binding_observed'] is False


def test_cli_writes_exact_ref_link_without_altering_evidence(tmp_path):
    from tools.update_engine_link import main
    records, payload, check = case()
    evd, raw = evd_for(payload, check)
    (tmp_path / 'records.jsonl').write_text('\n'.join(json.dumps(row) for row in records))
    (tmp_path / 'evd.json').write_text(evd.model_dump_json())
    (tmp_path / 'native.json').write_bytes(raw)
    argv = ['link', '--records', str(tmp_path / 'records.jsonl'), '--request-id', RID,
            '--evidence', str(tmp_path / 'evd.json'), '--artifact', str(tmp_path / 'native.json'),
            '--artifact-name', 'native.json', '--output', str(tmp_path / 'linked.json')]
    assert main(argv) == 0
    linked = json.loads((tmp_path / 'linked.json').read_text())
    assert linked['evidence_ref'] == evd.ref().model_dump(mode='json')
    assert Evidence.model_validate_json((tmp_path / 'evd.json').read_text()).hash_ok()
    with pytest.raises(FileExistsError):
        main(argv)


def test_cli_probe_reuses_existing_collector_and_minimizes_saved_content(tmp_path, monkeypatch):
    from tools.update_engine_link import main
    from guard.executors import stream_probe
    secret = 'fixture-credential-secret'
    fixture = [{'id': 'normal', 'mode': 'json', 'method': 'POST', 'url': 'http://127.0.0.1:8080/v1/completions',
                'headers': {'Authorization': 'Bearer ' + secret}, 'body': {'prompt': 'private-fixture'}}]
    (tmp_path / 'requests.json').write_text(json.dumps(fixture))
    calls = []
    def collect(requests, timeout):
        calls.append((requests, timeout))
        return [{'id': 'normal', 'outcome': 200, 'capture_complete': True, 'body': 'private-result',
                 'body_sha256': 'a' * 64, 'response_headers': {'x-request-id': RID},
                 'dispatch_ms': 0, 'end_ms': 200}]
    monkeypatch.setattr(stream_probe, 'collect', collect)
    assert main(['probe', '--requests', str(tmp_path / 'requests.json'), '--timeout', '3',
                 '--normal-id', 'normal', '--max-normal-ms', '100', '--output', str(tmp_path / 'probe.json')]) == 0
    result = (tmp_path / 'probe.json').read_text()
    assert secret not in result and 'private-fixture' not in result and 'private-result' not in result
    document = json.loads(result)
    assert document['rows'][0]['runtime_request_id'] == RID
    assert document['normal_http_slo']['http_completed'] is True
    assert document['normal_http_slo']['within_budget'] is False
    assert calls == [(fixture, 3)]


def test_termination_cli_never_binds_two_unavailable_image_digests(tmp_path):
    from tools.update_engine_link import main
    records, payload, check = case()
    for row in records:
        row['target']['image_digest'] = None
    (tmp_path / 'records.jsonl').write_text('\n'.join(json.dumps(row) for row in records))
    transport = payload['native']['transport']
    transport['binding']['engine']['image_id'] = 'unparseable'
    (tmp_path / 'packet.json').write_text(json.dumps({'packet': None, 'transport': transport,
        'environment_ref': REF, 'target_sha256': 'f' * 64}))
    assert main(['termination', '--records', str(tmp_path / 'records.jsonl'), '--request-id', RID,
        '--packet', str(tmp_path / 'packet.json'), '--max-delay-ms', '100',
        '--required-scopes', 'scheduler_state', '--output', str(tmp_path / 'result.json')]) == 0
    assert json.loads((tmp_path / 'result.json').read_text())['runtime_target_generation_observed'] is False
