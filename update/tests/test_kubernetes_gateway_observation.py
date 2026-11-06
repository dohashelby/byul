"""OBS-01 Gateway 경로: Gateway 기록 변환·정책·전송 검증·파생 팩."""
import json

import pytest

from guard.executors import kubernetes_gateway_observation as module
from guard.executors import observation_contract as base
from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_http import GATEWAY_EVENT_PROFILE, KubernetesBindingError, KubernetesHTTP


def record(request_id, result='ALLOWED', principal='dev1-general', status=200, phase='final', run='run-1'):
    return json.dumps({'schema_version': 'runtime-mvp-record/1.0', 'phase': phase, 'request_id': request_id, 'run_id': run,
                       'target_id': 'lab-target', 'policy': {'sha256': 'a' * 64},
                       'auth': {'state': 'authenticated' if principal else 'invalid', 'principal_id': principal},
                       'decision': {'result': result, 'http_status': status}})


def test_final_gateway_rows_become_closed_required_event_rows_without_invented_values():
    lines = [record('r1', phase='started'), record('r1'), record('r2', 'DENIED', 'dev1-observer', 403), record('r3', 'ERROR'),
             record('r4', run='other'), record('r5', 'DENIED', None, 401)]
    events = module.record_events(lines, run_id='run-1')
    assert [e['event'] for e in events] == ['ACCESS_ALLOWED', 'ACCESS_DENIED', 'ACCESS_DENIED']
    first, denied, anonymous = events
    assert first['actor_sha256'] == module.principal_sha256('dev1-general') and first['request_sha256'] == sha256_hex(b'r1')
    assert first['result_sha256'] == sha256_hex(canonical_json({'outcome': 200})) and first['approval_sha256'] is None and first['source_sha256'] is None
    assert denied['actor_sha256'] == module.principal_sha256('dev1-observer') and anonymous['actor_sha256'] is None
    packet = {'schema': base.SCHEMA, 'session': 'b' * 32, 'environment_ref': {'x': 1}, 'target_sha256': 'c' * 64,
              'producer_source_sha256': 'd' * 64, 'producer_authenticated': False, 'source_ended': False, 'dropped_events': 0, 'events': events}
    base.validate_record(packet, environment_ref={'x': 1}, target_sha256='c' * 64, session='b' * 32, expected_source_sha256='d' * 64)
    with pytest.raises(ValueError, match='GATEWAY_RECORD_SCHEMA_UNSUPPORTED'):
        module.record_events(['{"schema_version": "other"}'], run_id='run-1')


def test_policy_needs_the_gateway_target_id_on_top_of_the_original_keys():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    values = {'observation_environment_sha256': 'a' * 64, 'observation_target_sha256': 'b' * 64,
              'observation_validity_start': now.isoformat(), 'observation_validity_end': (now + timedelta(minutes=5)).isoformat(),
              'observation_max_delay_ms': 5000, 'observation_required_events': ['ACCESS_ALLOWED', 'ACCESS_DENIED'],
              'observation_required_fields': ['actor_sha256', 'target_sha256', 'run_sha256', 'policy_sha256'],
              'observation_session': 'c' * 32, 'observation_expected_source_sha256': 'd' * 64,
              'observation_event_policy_sha256': 'e' * 64, 'observation_allowed_actor_sha256': 'f' * 64,
              'observation_denied_actor_sha256': '1' * 64, 'observation_gateway_target_id': 'byul-lab-run-1'}
    module.normalize_policy(values)
    for change in ({'observation_gateway_target_id': 'bad id'}, {'observation_gateway_target_id': None}):
        with pytest.raises(ValueError, match='GATEWAY_OBSERVATION_POLICY_INVALID'):
            module.normalize_policy({**values, **change})
    with pytest.raises(ValueError, match='GATEWAY_OBSERVATION_POLICY_INVALID'):
        module.normalize_policy({k: v for k, v in values.items() if k != 'observation_gateway_target_id'})


def test_transport_accepts_only_the_three_gateway_trials_with_one_credential_header():
    stub = type('S', (), {'_gateway_netloc': '10.0.0.9:8080'})()
    good = {'id': 'obs01-denied', 'method': 'POST', 'url': 'http://10.0.0.9:8080/v1/chat/completions',
            'headers': {'Authorization': 'Bearer token-0123456789'},
            'body': {'model': 'm', 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 1, 'stream': False}}
    KubernetesHTTP._gateway_event_request(stub, good)
    for change in ({'id': 'other'}, {'headers': {}}, {'headers': {'Authorization': 'Bearer t-0123456789', 'X-User': 'a'}},
                   {'body': {**good['body'], 'temperature': 0}}, {'body': {**good['body'], 'messages': [{'role': 'user', 'content': 'free'}]}}):
        with pytest.raises(KubernetesBindingError):
            KubernetesHTTP._gateway_event_request(stub, {**good, **change})
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP._gateway_event_request(type('S', (), {'_gateway_netloc': None})(), good)
    assert GATEWAY_EVENT_PROFILE == 'kubernetes-owned-gateway-event-http-v1'


def test_derived_pack_changes_only_obs01_and_keeps_the_seed_pack_untouched(tmp_path):
    from pathlib import Path
    from guard.models import load_pack
    from tools import build_dev1_gateway_obs01_pack as tool
    result = tool.derive(tmp_path / 'pack')
    derived, original = load_pack(tmp_path / 'pack'), load_pack(Path(tool.__file__).parents[1] / 'packs/PACK-BASELINE')
    assert original.ref().model_dump(mode='json') == tool.SEED_REF and result['pack_ref']['id'] == 'PACK-DEV1-GATEWAY-OBS01'
    assert result['pack_ref']['revision'] == tool.DERIVED_REVISION == 2
    assert derived.checks['OBS-01'].collection_profile == module.PROFILE
    assert {cid for cid in original.checks if original.checks[cid] != derived.checks[cid]} == {'OBS-01'}
    with pytest.raises(ValueError, match='NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED'):
        tool.derive(tmp_path / 'pack')
