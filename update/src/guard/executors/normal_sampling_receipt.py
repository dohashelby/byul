"""Read-only qualification of the existing metadata-only REQ-10 summary."""

import hmac
import re

from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json

_SCOPE_PINS = {'target_sha256', 'binding_sha256', 'probe_source_sha256', 'request_body_sha256'}
_FIELDS = _SCOPE_PINS | {
    'profile', 'supplied_sampling_options', 'http_dispatch_observed', 'ownership_verified_before_after',
    'outcome', 'response_capture', 'normal_completion_shape_verified', 'uncollected',
}
_CAPTURE_FIELDS = {'stored_body_representation', 'stored_body_is_verbatim', 'original_json_state'}
_EXTENT_FIELDS = {'original_body_sha256', 'original_body_bytes'}
_CAPTURE_STATES = {'valid', 'duplicate_keys', 'invalid_or_nonfinite', 'unavailable'}
_MISSING_NATIVE = (
    'REQ10_RESPONSE_ORIGINAL_BODY_UNAVAILABLE',
    'REQ10_RESPONSE_REQUEST_ID_UNAVAILABLE',
    'REQ10_RESPONSE_OBSERVATION_WINDOW_UNAVAILABLE',
)
_NOT_COLLECTED = ['sampling_limits', 'runner_consumption', 'rejections', 'structured_output', 'tool_selection']
_SUMMARY_LIMIT = 16 * 1024
_BODY_LIMIT = 128 * 1024
_INVALID = 'REQ10_NORMAL_RESPONSE_SUMMARY_INVALID'


def _sha256(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _reported_capture(capture):
    if (type(capture) is not dict or not _CAPTURE_FIELDS <= capture.keys()
            or capture['stored_body_representation'] != 'omitted_hash_and_parse_state_only'
            or capture['stored_body_is_verbatim'] is not False
            or type(capture['original_json_state']) is not str or capture['original_json_state'] not in _CAPTURE_STATES):
        raise ValueError(_INVALID)
    if capture['original_json_state'] == 'unavailable':
        if (capture.keys() != _CAPTURE_FIELDS and (capture.keys() != _CAPTURE_FIELDS | {'diagnostic'}
                or capture['diagnostic'] != 'INPUT_CAPTURE_METADATA_UNAVAILABLE')):
            raise ValueError(_INVALID)
        return {}
    if (capture.keys() != _CAPTURE_FIELDS | _EXTENT_FIELDS
            or not _sha256(capture['original_body_sha256'])
            or type(capture['original_body_bytes']) is not int
            or not 0 <= capture['original_body_bytes'] <= _BODY_LIMIT):
        raise ValueError(_INVALID)
    return {'reported_body_sha256': capture['original_body_sha256'],
            'reported_body_bytes': capture['original_body_bytes']}


def qualify_summary(raw, *, expected_file_sha256, expected_pins):
    """Check the producer's exact summary against independent file and scope pins.

    A valid summary reports an observation. It contains no original response
    bytes, response request identity or clock window for native correlation.
    """
    if (not _sha256(expected_file_sha256) or type(expected_pins) is not dict
            or expected_pins.keys() != _SCOPE_PINS or not all(_sha256(value) for value in expected_pins.values())):
        raise ValueError('REQ10_NORMAL_RESPONSE_PINS_INVALID')
    if type(raw) is not bytes or not 0 < len(raw) <= _SUMMARY_LIMIT:
        raise ValueError(_INVALID)
    if sha256_hex(raw) != expected_file_sha256:
        raise ValueError('REQ10_NORMAL_RESPONSE_SUMMARY_CHANGED')
    try:
        summary = strict_json(raw, limit=_SUMMARY_LIMIT)
        normalized = canonical_json(summary)
    except (TypeError, ValueError, RecursionError):
        raise ValueError(_INVALID) from None
    options = summary.get('supplied_sampling_options') if type(summary) is dict else None
    if (type(summary) is not dict or summary.keys() != _FIELDS
            or summary['profile'] != 'kubernetes-owned-normal-sampling/1'
            or type(options) is not list or any(type(name) is not str for name in options)
            or len(set(options)) != len(options) or 'max_tokens' not in options
            or not set(options) <= {'temperature', 'top_p', 'max_tokens', 'min_tokens'}
            or summary['uncollected'] != _NOT_COLLECTED or normalized != raw):
        raise ValueError(_INVALID)
    if any(summary[key] != expected for key, expected in expected_pins.items()):
        raise ValueError('REQ10_NORMAL_RESPONSE_SCOPE_MISMATCH')

    bound = summary['ownership_verified_before_after']
    success = summary['normal_completion_shape_verified']
    dispatch = summary['http_dispatch_observed']
    outcome, capture = summary['outcome'], summary['response_capture']
    if type(bound) is not bool or type(success) is not bool:
        raise ValueError(_INVALID)
    if bound:
        valid_status = (type(outcome) is int and 100 <= outcome <= 599
                        or type(outcome) is str and outcome in
                        {'refused', 'timeout', 'unreachable', 'dns_error', 'error', 'probe_error'})
        if not valid_status or dispatch is not (True if type(outcome) is int else None):
            raise ValueError(_INVALID)
        reported_capture = _reported_capture(capture)
        if success and (outcome != 200 or capture['original_json_state'] != 'valid'
                        or not reported_capture.get('reported_body_bytes')):
            raise ValueError(_INVALID)
    else:
        if dispatch is not None or outcome is not None or capture is not None or success:
            raise ValueError(_INVALID)
        reported_capture = {}

    gaps = list(_MISSING_NATIVE)
    if not bound:
        gaps.append('REQ10_NORMAL_RESPONSE_OWNER_UNVERIFIED')
    elif not success:
        gaps.append('REQ10_NORMAL_RESPONSE_INCOMPLETE')
    return {'summary_sha256': expected_file_sha256, 'reported_outcome': outcome,
            'reported_owner_verified_before_after': bound, 'reported_completion_shape_verified': success,
            **reported_capture, 'native_request_binding_verified': False, 'diagnostic_codes': sorted(gaps)}



_CUT_PINS = {
    'target_sha256', 'target_file_sha256', 'plan_sha256', 'source_sha256', 'owned_generation_sha256',
    'session_sha256', 'cursor_sha256', 'writer_scope_sha256', 'source_observation_sha256',
}
_CUT_FIELDS = _CUT_PINS | {'schema', 'data_origin', 'environment_ref', 'capture_id', 'start_page'}
_OBSERVER_FIELDS = {'body_bytes', 'summary_sha256', 'environment_ref', 'data_origin', 'attempt_sha256'}
_CALLBACK_FIELDS = {'normal_callback_cut', 'normal_callback_cut_verified', 'truncated', 'native_request_links'}
_REQUIRED_RESPONSE = {'id', 'object', 'created', 'model', 'choices', 'usage'}
_OPTIONAL_RESPONSE = {'service_tier', 'system_fingerprint', 'kv_transfer_params', 'ec_transfer_params', 'metrics'}
_LINK_PINS = {
    'response_request_sha256', 'outer_request_sha256', 'native_request_sha256',
    'selection_call_sha256', 'assignment_call_sha256',
}
_WORKER_FIELDS = {'binding', 'admission_sha256', 'runner_event_seq', 'sampler_event_seqs'}
_ATTEMPT_UNVERIFIED = 'REQ10_RESPONSE_ATTEMPT_UNVERIFIED'
_LINK_INVALID = 'REQ10_RESPONSE_NATIVE_LINK_INVALID'


def _session_id(value):
    return type(value) is str and len(value) == 32 and all(c in '0123456789abcdef' for c in value)


def _valid_cut(cut, expected_pins, session):
    if (type(cut) is not dict or cut.keys() != _CUT_FIELDS
            or cut['schema'] != 'guard-normal-tp2-callback-cut/1' or cut['data_origin'] not in ('MEASURED', 'EXAMPLE')
            or not all(_sha256(cut[name]) for name in _CUT_PINS) or not _session_id(cut['capture_id'])
            or not _session_id(session) or cut['session_sha256'] != sha256_hex(session.encode('ascii'))
            or cut['owned_generation_sha256'] != expected_pins['binding_sha256']
            or cut['target_sha256'] != expected_pins['target_sha256']):
        return False
    env, page = cut['environment_ref'], cut['start_page']
    return (type(env) is dict and env.keys() == {'kind', 'id', 'revision', 'sha256'} and env['kind'] == 'ENV'
            and type(env['id']) is str and 0 < len(env['id']) <= 128 and _sha256(env['sha256'])
            and type(env['revision']) is int and env['revision'] > 0
            and type(page) is dict and page.keys() == {'path', 'sha256'} and _sha256(page['sha256'])
            and page['path'] == 'start-' + cut['capture_id'] + '.json')


def _response_id(raw_body, summary, api_model):
    if type(raw_body) is not bytes or not 0 < len(raw_body) <= _BODY_LIMIT:
        raise ValueError('REQ10_RESPONSE_ORIGINAL_BODY_UNAVAILABLE')
    if len(raw_body) != summary['reported_body_bytes'] or sha256_hex(raw_body) != summary['reported_body_sha256']:
        raise ValueError('REQ10_RESPONSE_ORIGINAL_BODY_MISMATCH')
    try:
        payload = strict_json(raw_body, limit=_BODY_LIMIT)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('REQ10_RESPONSE_BODY_SHAPE_UNVERIFIED') from None
    if type(payload) is dict and 'id' not in payload:
        raise ValueError('REQ10_RESPONSE_REQUEST_ID_UNAVAILABLE')
    if (type(payload) is not dict or not _REQUIRED_RESPONSE <= payload.keys()
            or payload.keys() - _REQUIRED_RESPONSE - _OPTIONAL_RESPONSE
            or payload['object'] != 'text_completion' or payload['model'] != api_model
            or type(payload['created']) is not int or not 0 <= payload['created'] < 2**63
            or type(payload['choices']) is not list or len(payload['choices']) != 1
            or type(payload['choices'][0]) is not dict or type(payload['usage']) is not dict
            or type(payload['usage'].get('completion_tokens')) is not int or payload['usage']['completion_tokens'] != 1):
        raise ValueError('REQ10_RESPONSE_BODY_SHAPE_UNVERIFIED')
    identifier = payload['id']
    if type(identifier) is not str or re.fullmatch(r'[A-Za-z0-9_.:-]{1,512}', identifier) is None:
        raise ValueError('REQ10_RESPONSE_REQUEST_ID_UNAVAILABLE')
    return identifier


def _worker_locators(values):
    if type(values) is not list or not 0 < len(values) <= 128:
        raise ValueError(_LINK_INVALID)
    workers = []
    seen = set()
    for worker in values:
        if type(worker) is not dict or worker.keys() != _WORKER_FIELDS:
            raise ValueError(_LINK_INVALID)
        binding = worker['binding']
        received, sampled = worker['runner_event_seq'], worker['sampler_event_seqs']
        if (type(binding) is not int or not 0 <= binding < 128 or not _sha256(worker['admission_sha256'])
                or type(received) is not int or not 0 <= received < 4096
                or type(sampled) is not list or not 0 < len(sampled) <= 4096
                or any(type(item) is not int or not received < item < 4096 for item in sampled)
                or len(set(sampled)) != len(sampled)):
            raise ValueError(_LINK_INVALID)
        identity = (binding, worker['admission_sha256'])
        if identity in seen:
            raise ValueError(_LINK_INVALID)
        seen.add(identity)
        workers.append({**worker, 'sampler_event_seqs': sampled.copy()})
    return workers


def _native_link(links, response_sha256):
    if (type(links) is not list or len(links) > 4096
            or any(type(link) is not dict or link.keys() != _LINK_PINS | {'workers'}
                   or not all(_sha256(link[name]) for name in _LINK_PINS) for link in links)):
        raise ValueError(_LINK_INVALID)
    selected = [link for link in links if link['response_request_sha256'] == response_sha256]
    if not selected:
        raise ValueError('REQ10_RESPONSE_NATIVE_LINK_UNAVAILABLE')
    if len(selected) != 1:
        raise ValueError('REQ10_RESPONSE_NATIVE_LINK_AMBIGUOUS')
    link, = selected
    if (link['selection_call_sha256'] == link['assignment_call_sha256']
            or any(row is not link and (row['outer_request_sha256'] == link['outer_request_sha256']
                    or row['native_request_sha256'] == link['native_request_sha256']) for row in links)):
        raise ValueError('REQ10_RESPONSE_NATIVE_LINK_AMBIGUOUS')
    return {**link, 'workers': _worker_locators(link['workers'])}


def associate_response(raw, *, expected_file_sha256, expected_pins, observer, callback_capture,
                       expected_cut, session, request_key, api_model):
    """Join observations passed by the same live trial coordinator, in memory.

    The caller verifies the mounted preparation, single operation and start/end
    cut custody, then recomputes the native projection from that closed range.
    Archived boolean flags alone do not establish these prerequisites. This
    helper only reports that one HMAC matched the observed native locators.
    """
    summary = qualify_summary(raw, expected_file_sha256=expected_file_sha256, expected_pins=expected_pins)
    if not summary['reported_completion_shape_verified']:
        raise ValueError('REQ10_NORMAL_RESPONSE_INCOMPLETE')
    if (not _valid_cut(expected_cut, expected_pins, session)
            or type(callback_capture) is not dict or callback_capture.keys() != _CALLBACK_FIELDS
            or callback_capture['normal_callback_cut_verified'] is not True or callback_capture['truncated'] is not False
            or callback_capture['normal_callback_cut'] != expected_cut
            or type(request_key) is not bytes or len(request_key) != 32
            or type(api_model) is not str or not 0 < len(api_model) <= 200):
        raise ValueError(_ATTEMPT_UNVERIFIED)
    if observer is None:
        raise ValueError('REQ10_RESPONSE_ORIGINAL_BODY_UNAVAILABLE')
    attempt_sha256 = sha256_hex(canonical_json(expected_cut))
    if (type(observer) is not dict or observer.keys() != _OBSERVER_FIELDS
            or observer['attempt_sha256'] != attempt_sha256 or observer['summary_sha256'] != expected_file_sha256
            or observer['environment_ref'] != expected_cut['environment_ref']
            or observer['data_origin'] != expected_cut['data_origin']):
        raise ValueError(_ATTEMPT_UNVERIFIED)
    identifier = _response_id(observer['body_bytes'], summary, api_model)
    # Same exact opaque string domain and prepared-session key as the scoped sensor.
    response_sha256 = hmac.digest(request_key, b's:' + identifier.encode('ascii'), 'sha256').hex()
    link = _native_link(callback_capture['native_request_links'], response_sha256)
    return {'attempt_sha256': attempt_sha256, 'summary_sha256': expected_file_sha256,
            'session_sha256': expected_cut['session_sha256'], 'data_origin': expected_cut['data_origin'], **link}
