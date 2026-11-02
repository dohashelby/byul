"""기업 시스템이 서명한 기록으로 수집기가 스스로 확정할 수 없는 사실을 채운다.

발급자 이벤트·전파·승인처럼 대상 밖 시스템의 사실은 엔진 쪽 관측만으로 증명되지 않는다.
시스템별 차이는 서명 기록을 내보내는 커넥터가 흡수하고, 여기서는 서명·대상·신선도를
검증한다. 검증에 실패하면 관측을 바꾸지 않아 기존 UNKNOWN이 유지된다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path

from guard.models import ExecStatus, canonical_json, utcnow
from guard.models.objects import Challenge, Observation

SCHEMA = 'byul-signed-record/1'
SUPPLY = (('supply_boot_record_authenticated',), None)
# 점검별 (서명으로 받는 사실, 그 사실이 모두 참일 때만 켜는 범위 사실).
ATTESTED = {check: SUPPLY for check in ('LIF-01', 'LIF-02', 'PLT-03', 'PLT-05', 'SUP-07', 'SUP-09', 'SUP-10', 'SUP-11', 'SUP-12', 'SUP-15')}
ATTESTED.update({
    'OBS-01': (('required_event_producer_authenticated', 'required_event_independent_trigger_verified'), 'required_event_scope_complete'),
    'PLT-10': (('revocation_issuer_event_verified', 'revocation_provider_propagation_verified',
        'revocation_normal_before_issuer_verified', 'revocation_new_connection_receipt_verified'), 'revocation_scope_complete'),
    'PLT-11': (('session_issuer_event_verified', 'session_provider_propagation_verified',
        'session_application_session_or_handle_verified', 'session_serving_session_receipt_verified'), 'session_scope_complete'),
    'PLT-12': (('copy_producer_authenticated', 'copy_membership_verified', 'copy_treatment_verified',
        'copy_reuse_enforcer_verified', 'copy_issuer_verified'), 'copy_scope_complete'),
    'REQ-02': (('native_route_authorization_verified',), 'native_route_scope_complete'),
    'REQ-04': (('forwarded_producer_authenticated', 'forwarded_principal_identity_verified'), 'forwarded_scope_complete'),
    'REQ-05': (('final_model_identity_verified', 'model_retirement_scope_complete'), 'model_authorization_scope_complete'),
    'REQ-06': (('stored_producer_authenticated', 'stored_object_owner_verified', 'stored_worker_effect_verified'), 'stored_scope_complete'),
})


class RecordInvalid(ValueError):
    pass


def signature(document, key):
    body = {k: v for k, v in document.items() if k != 'signature'}
    return hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()


def verify_record(raw, key, *, check_id, environment_sha256, max_age_seconds, now=None):
    try:
        document = json.loads(raw)
        issued = datetime.fromisoformat(document['issued_at'])
    except (TypeError, ValueError, KeyError):
        raise RecordInvalid('RECORD_UNREADABLE') from None
    if (type(document) is not dict or document.get('schema') != SCHEMA or check_id not in ATTESTED
            or type(document.get('signature')) is not str
            or not hmac.compare_digest(document['signature'], signature(document, key))):
        raise RecordInvalid('RECORD_SIGNATURE_INVALID')
    facts = document.get('facts'); allowed, _ = ATTESTED[check_id]
    if (document.get('check_id') != check_id or document.get('environment_sha256') != environment_sha256
            or type(facts) is not dict or set(facts) != set(allowed) or any(type(v) is not bool for v in facts.values())
            or type(document.get('challenge_steps_completed')) is not bool or type(document.get('resolves', [])) is not list):
        raise RecordInvalid('RECORD_BINDING_INVALID')
    now = now or datetime.now(timezone.utc)
    if issued.tzinfo is None or not timedelta(0) <= now - issued <= timedelta(seconds=max_age_seconds):
        raise RecordInvalid('RECORD_NOT_FRESH')
    return document


class AttestedExecutor:
    def __init__(self, child, cfg, *, environ=None):
        self.child = child; self.cfg = cfg; self.environ = environ if environ is not None else os.environ

    def __getattr__(self, name):
        return getattr(self.child, name)

    def _record(self, check, env):
        path = (self.cfg.get('records') or {}).get(check.id)
        key = self.environ.get(self.cfg.get('key_env', ''))
        age = self.cfg.get('max_age_seconds')
        if path is None or not key or type(age) is not int or age <= 0:
            return None
        return verify_record(Path(path).read_bytes(), key.encode(), check_id=check.id,
            environment_sha256=env.ref().sha256, max_age_seconds=age)

    def run(self, check, env, policy_values, adapter, workdir):
        draft = self.child.run(check, env, policy_values, adapter, workdir)
        if check.id not in ATTESTED:
            return draft
        try:
            record = self._record(check, env)
        except (OSError, RecordInvalid) as error:
            draft.config = {**draft.config, 'record_attestation': {'accepted': False, 'reason': str(error)}}
            return draft
        if record is None:
            return draft
        facts, scope_key = record['facts'], ATTESTED[check.id][1]
        observations = dict(draft.observations)
        for key, value in facts.items():
            observations[key] = Observation(value=value, collected=True)
        for key, item in draft.observations.items():
            if key.endswith('_missing') and type(item.value) is list:
                observations[key] = Observation(value=[c for c in item.value if c not in record.get('resolves', [])], collected=True)
        verified = all(facts.values()) and record['challenge_steps_completed']
        left = any(key.endswith('_missing') and item.value for key, item in observations.items())
        if scope_key is not None:
            observations[scope_key] = Observation(value=verified and not left, collected=True)
        draft.observations = observations
        draft.config = {**draft.config, 'record_attestation': {'accepted': True, 'system': record.get('system'), 'issued_at': record['issued_at']}}
        if verified and not left and not draft.cleanup_errors:
            draft.status = ExecStatus.COMPLETED; draft.abort_reason = None
            draft.challenges = [Challenge(step=step, attempted=True) for step in check.challenge_steps]
            draft.finished_at = utcnow()
        return draft


def attested(factory):
    def build(cfg):
        child = factory(cfg)
        settings = cfg.get('record_attestation') if type(cfg) is dict else None
        return AttestedExecutor(child, settings, environ=cfg.get('_runtime_environ')) if settings else child
    return build
