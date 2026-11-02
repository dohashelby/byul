"""서명 기록 검증과 관측 덧씌우기 계약. 기업 시스템 없이 EXAMPLE 기록만 쓴다."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from guard.executors import record_attestation as tool
from guard.executors import registry
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin, ExecStatus
from guard.models.objects import Observation

KEY = b'EXAMPLE-shared-record-key'
ENV = SimpleNamespace(ref=lambda: SimpleNamespace(sha256='e' * 64))
FACTS = {'revocation_issuer_event_verified': True, 'revocation_provider_propagation_verified': True,
    'revocation_normal_before_issuer_verified': True, 'revocation_new_connection_receipt_verified': True}


def record(*, facts=FACTS, check_id='PLT-10', env='e' * 64, issued=None, steps=True, resolves=(), key=KEY):
    document = {'schema': tool.SCHEMA, 'system': 'EXAMPLE-idp', 'check_id': check_id, 'environment_sha256': env,
        'issued_at': (issued or datetime.now(timezone.utc)).isoformat(), 'facts': facts,
        'challenge_steps_completed': steps, 'resolves': list(resolves)}
    document['signature'] = tool.signature(document, key)
    return json.dumps(document).encode()


def verify(raw, check_id='PLT-10'):
    return tool.verify_record(raw, KEY, check_id=check_id, environment_sha256='e' * 64, max_age_seconds=3600)


def test_valid_record_returns_its_facts():
    assert verify(record())['facts'] == FACTS


@pytest.mark.parametrize('raw,code', [
    (record(key=b'other'), 'RECORD_SIGNATURE_INVALID'),
    (record(check_id='PLT-11'), 'RECORD_BINDING_INVALID'),
    (record(env='f' * 64), 'RECORD_BINDING_INVALID'),
    (record(facts={**FACTS, 'extra': True}), 'RECORD_BINDING_INVALID'),
    (record(issued=datetime.now(timezone.utc) - timedelta(days=2)), 'RECORD_NOT_FRESH'),
    (record(issued=datetime.now(timezone.utc) + timedelta(days=2)), 'RECORD_NOT_FRESH'),
    (b'not json', 'RECORD_UNREADABLE'),
], ids=['other-key', 'other-check', 'other-env', 'extra-fact', 'stale', 'future', 'not-json'])
def test_invalid_record_is_rejected(raw, code):
    with pytest.raises(tool.RecordInvalid, match=code):
        verify(raw)


def test_changed_fact_breaks_the_signature():
    document = json.loads(record(facts={**FACTS, 'revocation_issuer_event_verified': False}))
    document['facts']['revocation_issuer_event_verified'] = True
    with pytest.raises(tool.RecordInvalid, match='SIGNATURE'):
        verify(json.dumps(document).encode())


class Child:
    name = 'credential_revocation'; version = '1'

    def run(self, check, env, values, adapter, workdir):
        return EvidenceDraft(status=ExecStatus.ABORTED, data_origin=DataOrigin.DECLARED, abort_reason='scope',
            normal_control={'attempted': True, 'succeeded': True},
            observations={'revocation_scope_complete': Observation(value=False, collected=True),
                'revocation_missing': Observation(value=['issuer_event_unverified'], collected=True)})


CHECK = SimpleNamespace(id='PLT-10', challenge_steps=['one'])


def run(tmp_path, raw, cfg=None):
    path = tmp_path / 'record.json'; path.write_bytes(raw)
    settings = {'records': {'PLT-10': str(path)}, 'key_env': 'KEY', 'max_age_seconds': 3600, **(cfg or {})}
    return tool.AttestedExecutor(Child(), settings, environ={'KEY': KEY.decode()}).run(CHECK, ENV, {}, None, tmp_path)


def test_complete_record_clears_scope_and_gate(tmp_path):
    draft = run(tmp_path, record(resolves=['issuer_event_unverified']))
    assert draft.status == ExecStatus.COMPLETED and draft.observations['revocation_scope_complete'].value is True
    assert draft.observations['revocation_missing'].value == [] and draft.challenges[0].attempted


def test_unresolved_missing_keeps_scope_open(tmp_path):
    draft = run(tmp_path, record())
    assert draft.status == ExecStatus.ABORTED and draft.observations['revocation_scope_complete'].value is False


def test_false_fact_is_kept_and_scope_stays_open(tmp_path):
    draft = run(tmp_path, record(facts={**FACTS, 'revocation_issuer_event_verified': False}, resolves=['issuer_event_unverified']))
    assert draft.observations['revocation_issuer_event_verified'].value is False
    assert draft.observations['revocation_scope_complete'].value is False and draft.status == ExecStatus.ABORTED


def test_incomplete_challenges_keep_the_gate_closed(tmp_path):
    draft = run(tmp_path, record(steps=False, resolves=['issuer_event_unverified']))
    assert draft.status == ExecStatus.ABORTED


def test_bad_record_changes_no_observation(tmp_path):
    draft = run(tmp_path, record(key=b'other'))
    assert 'revocation_issuer_event_verified' not in draft.observations
    assert draft.config['record_attestation'] == {'accepted': False, 'reason': 'RECORD_SIGNATURE_INVALID'}


def test_missing_key_leaves_the_child_result(tmp_path):
    draft = run(tmp_path, record(), {'key_env': 'ABSENT'})
    assert draft.status == ExecStatus.ABORTED and 'record_attestation' not in draft.config


def test_every_attested_fact_is_required_by_a_connected_check():
    from guard.models import load_pack
    from pathlib import Path
    pack = load_pack(Path(__file__).parents[1] / 'packs/PACK-BASELINE')
    for check_id, (facts, scope) in tool.ATTESTED.items():
        rules = {rule['obs'] for rule in pack.checks[check_id].judge.params['pass_when_all']}
        assert set(facts) | ({scope} if scope else set()) <= rules, check_id


def test_registry_wraps_only_with_explicit_settings():
    assert not isinstance(registry.build_executor('supply_boot', 'kubernetes', {}), tool.AttestedExecutor)
    assert isinstance(registry.build_executor('supply_boot', 'kubernetes', {'record_attestation': {'key_env': 'KEY'}}), tool.AttestedExecutor)
