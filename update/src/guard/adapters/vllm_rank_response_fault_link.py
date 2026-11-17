"""Selected reply-stall/source-timeout candidate, not a registered detector.

Both original capture readers run here. Public callers cannot supply a JSON
success flag in place of either sealed capture. Other fault kinds and the
frontend's causal error delivery/recovery remain outside this candidate scope.
"""
from hashlib import sha256
import hmac
from pathlib import Path

from . import vllm_rank_rpc_input as faults
from . import vllm_rank_response_capture as responses


def _require(value):
    if not value:
        raise ValueError('RANK_REPLY_STALL_RESPONSE_LINK_UNVERIFIED')


def _single(rows):
    _require(len(rows) == 1)
    return rows[0]


def _link_verified(fault, response, case, rpc_rows, response_profile):
    """Internal projection only; its input dictionaries are not public proofs."""
    arm, ready = case['arm'], case['ready']
    _require(arm['fault_kind'] == 'communication-stall' and arm['schema'] == 'guard-rank-rpc-arm/3')
    _require(response['gaps'] == [])
    worker_call = _single([row for row in rpc_rows if row.get('event') == 'CALL'
                          and row.get('call_hmac_sha256') == ready['call_hmac_sha256']])
    request = worker_call['request_hmac_sha256']
    _require(len(request) == 1)
    endpoint = worker_call['endpoints']['response']
    _require(endpoint['identity_hmac_sha256'] == arm['reply_binding']['response_hmac_sha256'])
    applied = _single([row for row in fault['events'] if row['event'] == 'fault_applied'])['at_ns']
    end = arm['expires_ns']
    if case['physical_resume'] is not None:
        end = min(end, case['physical_resume']['published_ns'])
    stops = [row['observation_ns'] for row in rpc_rows
             if row.get('call_hmac_sha256') == ready['call_hmac_sha256']
             and row.get('event') in {'CONTROL_RELEASE', 'CONTROL_CANCELLED', 'CONTROL_EXPIRED', 'RETURN', 'RAISE'}]
    if stops:
        end = min(end, *stops)
    selected = [op for op in response['operations'] if op['binding']['request_hmac_sha256'] == request]
    operation = _single(selected)
    _require(operation['source_four_point_graph_complete'] is True
             and operation['same_native_error_reached_future'] is True
             and operation['reader_error_is_direct_native_cause'] is True)
    context = operation['process']
    _require(all(context[k] == worker_call['context'][k] for k in
                 ('boot_sha256', 'time_namespace', 'time_offsets_sha256', 'pid_namespace', 'uid')))
    rows = [row for row in response['rows'] if row['operation_hmac_sha256'] == operation['operation_hmac_sha256']
            and row['process'] == context]
    def terminal(kind):
        return _single([row for row in rows if row['event'] in {'RETURN', 'UNWIND'}
            and response_profile['sources'][row['source_module']]['points'][row['point']]['kind'] == kind])
    reader, native, root = (terminal(kind) for kind in ('reader', 'native-response', 'root'))
    _require(all(row['event'] == 'UNWIND' and row['exception']['kind'] == 'timeout' for row in (reader, native, root)))
    _require(reader['reader_endpoint']['identity_hmac_sha256'] == endpoint['identity_hmac_sha256']
             and type(reader['reader_timeout_ns']) is int and reader['reader_timeout_ns'] > 0
             and applied < reader['observed_ns'] <= native['observed_ns'] <= root['observed_ns'] < end)
    deferred = _single([row for row in fault['native_deferred_results']
        if row['event'] == 'original-result-raised' and row['exception_kind'] == 'timeout'
        and row['context'] == context and row['deferred_rpc'] == operation['binding']['deferred_rpc']])
    _require(root['observed_ns'] <= deferred['at_ns'] < end)
    return {'schema': 'guard-selected-reply-stall-source-link-candidate/1',
        'fault_kind': arm['fault_kind'], 'request_hmac_sha256': request[0],
        'operation_hmac_sha256': operation['operation_hmac_sha256'],
        'reader_endpoint_hmac_sha256': endpoint['identity_hmac_sha256'],
        'fault_applied_ns': applied, 'reader_error_ns': reader['observed_ns'],
        'native_error_ns': native['observed_ns'], 'future_error_ns': root['observed_ns'],
        'deferred_wrapper_error_ns': deferred['at_ns'], 'fault_held_until_ns': end,
        'native_error_after_fault_ns': native['observed_ns'] - applied,
        'selected_stalled_channel_and_direct_timeout_link_observed': True,
        'producer_registered': False, 'fault_causality_verified': False, 'native_detector_qualified': False,
        'upper_delivery_verified': False, 'recovery_verified': False, 'whole_resources_released': False,
        'whole69_complete': False}


def link(rank_prepared, rank_capture, response_prepared, response_capture, expected, *, expected_uid=None):
    """Reverify original fault activation and closed response files end to end."""
    rank_prepared, rank_capture = Path(rank_prepared), Path(rank_capture)
    before = faults.project_stall_with_release_gap(rank_prepared, rank_capture, expected)
    response = responses.verify_capture(response_prepared, response_capture, expected_uid=expected_uid)
    manifest, cfg, profile = responses.load_prepared(response_prepared, expected_uid=expected_uid)
    files = faults._Files()
    settings_raw = files.read(rank_prepared / 'settings.json')
    settings = files.json(rank_prepared / 'settings.json')
    _require(cfg['rank_settings_sha256'] == sha256(settings_raw).hexdigest()
             and cfg['session'] == settings['session'] == expected['session']
             and cfg['image'] == settings['image'] == response['image']
             and hmac.compare_digest(cfg['key'], settings['key']))
    case = files.json(rank_capture / 'controls/control-case-result.json')['payload']
    rpc_review = faults.capture_code.verify_capture(rank_prepared, rank_capture / 'snapshots')
    result = _link_verified(before, response, case, rpc_review['rows'], profile)
    files.finish()
    _require(faults.project_stall_with_release_gap(rank_prepared, rank_capture, expected) == before
             and responses.verify_capture(response_prepared, response_capture, expected_uid=expected_uid) == response)
    return {**result, 'rank_source_set_sha256': before['source_set_sha256'],
            'response_capture_set_sha256': sha256(responses.canonical(response['records'])).hexdigest(),
            'response_settings_sha256': manifest['settings_sha256']}
