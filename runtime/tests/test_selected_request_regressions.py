"""Counterexamples from independent R2 review; synthetic protocol fixture only."""
import json

from tools import selected_request_checks as checks
from tools import selected_runtime_check as tool


def run_matrix(monkeypatch, *, drift_after=False, invalid_platform=False, anonymous=False):
    state = {"n": 0, "normal": 0}

    def request(method, url, **kw):
        if url.endswith('/api/status'):
            return tool.HTTPResponse(200, {}, json.dumps({'auth': {'protected_routes': ['GET /api/*']}}).encode())
        if url.endswith('/api/policy'):
            return tool.HTTPResponse(200, {}, json.dumps({'current': {'output_max_tokens': 16, 'sha256': 'policy'}}).encode())
        state['n'] += 1
        rid = f"{state['n']:032x}"
        payload = json.loads(kw['body']) if kw.get('body') else {}
        normal = (method == 'POST' and url.endswith('/v1/chat/completions')
                  and kw.get('token') == 'inference'
                  and not (set(payload) - {'model', 'messages', 'max_tokens', 'stream'})
                  and all('code_revision' not in message for message in payload.get('messages', []))
                  and payload.get('max_tokens', 0) <= 16
                  and not (kw.get('extra_headers') or {}).get('authorization'))
        if normal:
            state['normal'] += 1
        state.update(rid=rid, normal_current=normal)
        # Every rejection is a request-validation error, never authentication.
        return tool.HTTPResponse(200 if normal else 400, {'X-Request-ID': rid},
                                 b'{}' if normal else b'{"error":{"code":"INVALID_MESSAGES"}}')

    def record(*args, **kw):
        normal = state['normal_current']
        return {'state': 'observed', 'record_request_ids_match_query': True,
                'request_id': state['rid'], 'context_stable': True,
                'principal_id': None if anonymous else 'wrong-after' if drift_after and state['normal'] == 3 else 'client-a',
                'auth_state':'not_configured' if anonymous else 'authenticated',
                'policy_sha256': 'policy', 'target_id': 'target', 'target_instance_id': 'generation',
                'run_id': 'run', 'cache_scope_sha256': 'cache', 'synthetic_marker_present': False,
                'decision_result': 'ALLOWED' if normal else 'INVALID',
                'record_http_status': 200 if normal else 400, 'reason_code': None if normal else 'INVALID_MESSAGES',
                'http_status': 400, 'delivery_attempted': normal,
                'final_outcome': 'completed' if normal else 'INVALID_MESSAGES'}

    monkeypatch.setattr(checks, 'request', request)
    monkeypatch.setattr(checks, 'concurrent_precondition', lambda *a, **kw: (True, {}))
    monkeypatch.setattr(checks, 'record_for', record)
    monkeypatch.setattr(checks, 'route_record_for', record)
    monkeypatch.setattr(checks, 'platform_comparison', lambda *a, **kw: {
        'state': 'observed', 'same_trusted_fields': True, 'invalid': invalid_platform})
    return checks.request_matrix('http://local', 'http://platform', 'test-model', 'inference', 'viewer')


def test_auth_cases_reject_wrong_validation_reason(monkeypatch):
    result = run_matrix(monkeypatch)
    cases = [c for c in result['cases'] if c['id'].startswith('REQ-AUTH-')]
    assert len(cases) == 3
    assert all(c['result'] == 'failed' for c in cases)
    assert all(c['facts']['error_code'] == 'INVALID_MESSAGES' for c in cases)


def test_matrix_rejects_platform_contract_errors(monkeypatch):
    result = run_matrix(monkeypatch, invalid_platform=True)
    row = next(c for c in result['cases'] if c['id'] == 'REQ-CONTROL-BEFORE')
    assert row['facts']['platform']['invalid'] is True
    assert row['result'] == 'failed'


def test_after_control_rejects_changed_principal(monkeypatch):
    result = run_matrix(monkeypatch, drift_after=True)
    row = next(c for c in result['cases'] if c['id'] == 'REQ-CONTROL-AFTER')
    assert row['facts']['journal']['principal_id'] == 'wrong-after'
    assert row['result'] == 'failed'


def test_fields_cannot_pass_an_unrelated_request_validation_error(monkeypatch):
    result=run_matrix(monkeypatch)
    fields=[c for c in result['cases'] if c['id'].startswith('REQ-FIELD-')]
    assert fields and all(c['result']=='failed' for c in fields)
    assert all(c['facts']['error_code']=='INVALID_MESSAGES' for c in fields)
    assert all(c['facts']['expected_rejection'] is not None for c in fields)


def test_unverified_normal_identity_cannot_be_a_trusted_forgery_baseline(monkeypatch):
    result=run_matrix(monkeypatch,anonymous=True)
    controls=[c for c in result['cases'] if c['id'] in ('REQ-CONTROL-BEFORE','REQ-HEADER-AND-BODY-FORGERY','REQ-CONTROL-AFTER')]
    assert len(controls)==3 and all(c['result']=='unknown' for c in controls)


def test_known_api_contract_failure_survives_missing_identity(monkeypatch):
    result=run_matrix(monkeypatch,anonymous=True,invalid_platform=True)
    controls=[c for c in result['cases'] if c['id'] in ('REQ-CONTROL-BEFORE','REQ-HEADER-AND-BODY-FORGERY','REQ-CONTROL-AFTER')]
    assert all(c['result']=='failed' for c in controls)
