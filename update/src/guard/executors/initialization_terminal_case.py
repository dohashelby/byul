"""LIF09 single-container pre-init failure from Kubernetes terminal evidence.

Pure projection for an owned LAB collector. Inputs are metadata receipts, not a
native stage acknowledgment. No reader, executor, transport, or policy is changed.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

_PREFIX = 'LIF09_TERMINAL_CASE_'


class _Invalid(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise _Invalid(code)


def _object(value, code):
    _require(type(value) is dict, code)
    return value


def _text(value, code):
    _require(type(value) is str and 0 < len(value) <= 512, code)
    return value


def _hash(value, code):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None, code)
    return value


def _integer(value, code, *, minimum=0):
    _require(type(value) is int and minimum <= value < 2**63, code)
    return value


def _stamp(value, code):
    _text(value, code)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise _Invalid(code) from None
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, code)
    return parsed


def _one(rows, name, code):
    _require(type(rows) is list and 0 < len(rows) <= 64, code)
    selected = [row for row in rows if type(row) is dict and row.get('name') == name]
    _require(len(selected) == 1, code)
    return selected[0]


def _cid(value):
    _text(value, 'CONTAINER_ID_UNAVAILABLE')
    _require(re.fullmatch(r'[a-z0-9._-]+://[A-Za-z0-9][A-Za-z0-9._:-]{0,255}', value) is not None,
             'CONTAINER_ID_INVALID')
    return value


def _pod(document, expected, container, *, terminal=False, ready=None):
    pod = _object(document, 'POD_UNAVAILABLE')
    meta = _object(pod.get('metadata'), 'POD_METADATA_UNAVAILABLE')
    spec = _object(pod.get('spec'), 'POD_SPEC_UNAVAILABLE')
    status = _object(pod.get('status'), 'POD_STATUS_UNAVAILABLE')
    _require(meta.get('namespace') == expected['namespace'], 'POD_NAMESPACE_MISMATCH')
    _require(spec.get('hostNetwork', False) is False, 'HOST_NETWORK_POD_UNSUPPORTED')
    name, uid = _text(meta.get('name'), 'POD_NAME_UNAVAILABLE'), _text(meta.get('uid'), 'POD_UID_UNAVAILABLE')
    node = _text(spec.get('nodeName'), 'POD_NODE_UNAVAILABLE')
    try:
        address = str(ipaddress.ip_address(status.get('podIP')))
    except (ValueError, TypeError):
        raise _Invalid('POD_ADDRESS_INVALID') from None
    declared = _one(spec.get('containers'), container, 'CONTAINER_SPEC_UNAVAILABLE')
    cs = _one(status.get('containerStatuses'), container, 'CONTAINER_STATUS_UNAVAILABLE')
    conditions = status.get('conditions')
    _require(type(conditions) is list, 'POD_READY_UNAVAILABLE')
    flags = [c.get('status') for c in conditions if type(c) is dict and c.get('type') == 'Ready']
    _require(len(flags) == 1 and flags[0] in ('True', 'False'), 'POD_READY_UNAVAILABLE')
    if ready is not None:
        _require(flags[0] == ('True' if ready else 'False'), 'POD_READY_MISMATCH')
    restart = _integer(cs.get('restartCount'), 'RESTART_COUNT_INVALID')
    state = _object(cs.get('state'), 'CONTAINER_STATE_UNAVAILABLE')
    if terminal:
        if type(state.get('terminated')) is dict and set(state) == {'terminated'}:
            attempt = state['terminated']; previous = False
        elif type(state.get('waiting')) is dict and set(state) == {'waiting'}:
            attempt = _object(_object(cs.get('lastState'), 'TERMINAL_ATTEMPT_UNAVAILABLE').get('terminated'),
                              'TERMINAL_ATTEMPT_UNAVAILABLE'); previous = True
        else:
            raise _Invalid('TERMINAL_ATTEMPT_UNAVAILABLE')
        cid = _cid(attempt.get('containerID'))
        if cs.get('containerID'):
            _require(cs['containerID'] == cid, 'TERMINAL_CONTAINER_ID_MISMATCH')
        exit_code = _integer(attempt.get('exitCode'), 'TERMINAL_EXIT_CODE_INVALID')
        _require(exit_code != 0, 'TERMINAL_EXIT_WAS_SUCCESS')
        started, finished = attempt.get('startedAt'), attempt.get('finishedAt')
        _require(_stamp(started, 'TERMINAL_STARTED_AT_INVALID') <= _stamp(finished, 'TERMINAL_FINISHED_AT_INVALID'),
                 'TERMINAL_TIME_REVERSED')
        _require(cs.get('ready') is False, 'TERMINAL_CONTAINER_READY')
        identity = {'container_id': cid, 'restart_count': restart, 'started_at': started,
                    'finished_at': finished, 'exit_code': exit_code, 'previous': previous}
    else:
        running = _object(state.get('running'), 'RUNNING_CONTAINER_UNAVAILABLE')
        _require(set(state) == {'running'} and cs.get('ready') is True, 'RUNNING_CONTAINER_NOT_READY')
        started = running.get('startedAt'); _stamp(started, 'RUNNING_STARTED_AT_INVALID')
        identity = {'container_id': _cid(cs.get('containerID')), 'restart_count': restart,
                    'started_at': started}
    ports = declared.get('ports',[])
    _require(type(ports) is list and len(ports)<=64, 'CONTAINER_PORTS_INVALID')
    ports = [{'containerPort':p.get('containerPort'),'protocol':p.get('protocol','TCP')} for p in ports if type(p) is dict]
    return {'pod': name, 'pod_uid': uid, 'node': node, 'pod_ip': address,
            'container': container, 'attempt': identity, 'ready': flags[0] == 'True',
            'declared_ports': ports}


def _same(a, b, code):
    _require(a == b, code)
    return a


def _namespace(value, expected):
    value = _object(value, 'NAMESPACE_UNAVAILABLE')
    _require(value.get('namespace') == expected['namespace']
             and value.get('namespace_uid') == expected['namespace_uid'], 'NAMESPACE_IDENTITY_MISMATCH')


def _sequence(section, before, middle, after):
    seq = _object(section.get('sequence'), 'OBSERVATION_SEQUENCE_UNAVAILABLE')
    required = (*before, middle, *after)
    values = {key: _integer(seq.get(key), 'OBSERVATION_SEQUENCE_INVALID') for key in required}
    _require(len(set(values.values())) == len(values), 'OBSERVATION_SEQUENCE_DUPLICATE')
    _require(all(values[key] < values[middle] for key in before)
             and all(values[middle] < values[key] for key in after), 'OBSERVATION_ORDER_INVALID')
    return min(values.values()), max(values.values()), set(values.values())


def _direct(section, pod, expected, *, normal):
    for key in ('namespace_before', 'namespace_after'):
        _namespace(section.get(key), expected)
    probe = _same(_pod(section.get('probe_before_request'), expected, expected['probe_container'], ready=True),
                  _pod(section.get('probe_after_request'), expected, expected['probe_container'], ready=True),
                  'PROBE_GENERATION_CHANGED')
    request = _object(section.get('direct_request'), 'DIRECT_REQUEST_UNAVAILABLE')
    request_id = _hash(request.get('request_id_sha256'), 'REQUEST_ID_UNAVAILABLE')
    _require(request.get('transport') == 'http.client.HTTPConnection'
             and request.get('used_proxy') is False and request.get('followed_redirects') is False,
             'DIRECT_TRANSPORT_UNVERIFIED')
    _require(request.get('method') == 'POST' and request.get('path') == '/v1/chat/completions',
             'DIRECT_ROUTE_MISMATCH')
    try:
        url = urlsplit(request.get('url'))
        _require(url.scheme == 'http' and not url.username and not url.password and not url.query
                 and not url.fragment and url.path in ('', '/') and url.port == expected['port']
                 and str(ipaddress.ip_address(url.hostname)) == pod['pod_ip'], 'DIRECT_TARGET_MISMATCH')
    except (ValueError, TypeError, AttributeError):
        raise _Invalid('DIRECT_TARGET_MISMATCH') from None
    ports = pod['declared_ports']
    _require(type(ports) is list and any(type(p) is dict and p.get('containerPort') == expected['port']
                                       and type(p.get('containerPort')) is int and p.get('protocol', 'TCP') == 'TCP' for p in ports), 'ENGINE_PORT_UNAVAILABLE')
    http = _object(request.get('http'), 'HTTP_RESULT_UNAVAILABLE')
    outcome = http.get('outcome')
    _hash(http.get('sha256'), 'HTTP_HASH_UNAVAILABLE')
    size = _integer(http.get('bytes'), 'HTTP_BYTE_COUNT_INVALID')
    if normal:
        _require(type(outcome) is int and outcome == 200 and http.get('complete') is True
                 and http.get('normal_shape_verified') is True and http.get('model') == expected['model']
                 and size > 0 and http.get('sha256') != hashlib.sha256(b'').hexdigest()
                 and http.get('connection_established') is True and http.get('request_sent') is True, 'RECOVERY_NORMAL_UNVERIFIED')
    else:
        server_error = (type(outcome) is int and 500 <= outcome < 600 and http.get('complete') is True
                        and http.get('connection_established') is True and http.get('request_sent') is True)
        refused = (outcome == 'error' and http.get('error_stage') == 'connect'
                   and http.get('error_kind') == 'connection-refused'
                   and http.get('connect_refused') is True and http.get('connection_established') is False
                   and http.get('request_sent') is False and type(http.get('errno')) is int
                   and http['errno'] in (61, 111) and size == 0 and http.get('complete') is False
                   and http['sha256'] == hashlib.sha256(b'').hexdigest())
        _require(server_error or refused, 'DIRECT_REJECTION_UNVERIFIED')
    safe_http = {'outcome':outcome,'sha256':http['sha256'],'bytes':size,'complete':http.get('complete'),
                 'normal_shape_verified':True if normal else False,
                 'model':expected['model'] if normal else None,
                 'connection_established':http.get('connection_established'),'request_sent':http.get('request_sent')}
    if not normal and outcome == 'error':
        safe_http.update(error_stage='connect',error_kind='connection-refused',
                         errno=http['errno'],connect_refused=True)
    return {'request_id_sha256': request_id, 'probe': probe, 'http': safe_http,
            'body_sha256': request.get('body_sha256')}


def project_terminal_case(receipt, expected):
    """Return existing activation4 observations; unbound input remains missing.

    The owned producer must supply immutable metadata snapshots and a complete
    log marker receipt. Hashes preserve that receipt; they do not independently
    reconstruct or interpret the omitted original log text.
    """
    facts = {'activation_steps': [], 'activation_violations': [], 'activation_missing': [],
             'activation_scope_complete': False}
    try:
        expected = _object(expected, 'EXPECTED_SCOPE_UNAVAILABLE')
        for key in ('context', 'namespace', 'namespace_uid', 'container', 'probe_container', 'model'):
            _text(expected.get(key), 'EXPECTED_SCOPE_UNAVAILABLE')
        _integer(expected.get('port'), 'EXPECTED_PORT_INVALID', minimum=1)
        _require(expected['port'] <= 65535, 'EXPECTED_PORT_INVALID')
        _hash(expected.get('failure_marker_sha256'), 'EXPECTED_MARKER_INVALID')
        _require(expected.get('failure_kind') in ('config', 'model'), 'EXPECTED_FAILURE_KIND_INVALID')
        receipt = _object(receipt, 'RECEIPT_UNAVAILABLE')
        _require(receipt.get('context') == expected['context'], 'CONTEXT_MISMATCH')
        _text(receipt.get('sequence_scope'), 'SEQUENCE_SCOPE_UNAVAILABLE')
        normal_before = _object(receipt.get('normal_before'), 'NORMAL_BEFORE_UNAVAILABLE')
        baseline = _same(_pod(normal_before.get('pod_before_request'), expected, expected['container'], ready=True),
                         _pod(normal_before.get('pod_after_request'), expected, expected['container'], ready=True),
                         'NORMAL_BEFORE_GENERATION_CHANGED')
        prior_normal = _direct(normal_before, baseline, expected, normal=True)
        failure = _object(receipt.get('failure'), 'FAILURE_UNAVAILABLE')
        admission = _object(receipt.get('admission'), 'ADMISSION_UNAVAILABLE')
        recovery = _object(receipt.get('recovery'), 'RECOVERY_UNAVAILABLE')
        failed = _same(_pod(failure.get('pod_before_log'), expected, expected['container'], terminal=True, ready=False),
                       _pod(failure.get('pod_after_log'), expected, expected['container'], terminal=True, ready=False),
                       'LOG_CONTAINER_ATTEMPT_CHANGED')
        log = _object(failure.get('log_receipt'), 'LOG_RECEIPT_UNAVAILABLE')
        endpoint = _object(log.get('endpoint'), 'LOG_ENDPOINT_UNAVAILABLE')
        endpoint_expected = {'context':expected['context'],'namespace':expected['namespace'],
                             'pod':failed['pod'],'container':expected['container'],'previous':failed['attempt']['previous']}
        _require(all(endpoint.get(key) == value and type(endpoint.get(key)) is type(value)
                     for key,value in endpoint_expected.items()), 'LOG_ENDPOINT_MISMATCH')
        for key, value in {'namespace': expected['namespace'], 'pod': failed['pod'], 'pod_uid': failed['pod_uid'],
                           'container': expected['container'], **failed['attempt']}.items():
            _require(log.get(key) == value and type(log.get(key)) is type(value), 'LOG_ATTEMPT_MISMATCH')
        _require(type(log.get('returncode')) is int and log['returncode'] == 0
                 and log.get('marker_observed') is True and log.get('capture_complete') is True,
                 'LOG_CAPTURE_UNVERIFIED')
        _hash(log.get('stdout_sha256'), 'LOG_HASH_INVALID')
        _require(log['stdout_sha256'] != hashlib.sha256(b'').hexdigest(),'LOG_EMPTY')
        _integer(log.get('stdout_bytes'), 'LOG_EMPTY', minimum=1)
        _require(log.get('marker_sha256') == expected['failure_marker_sha256'], 'LOG_MARKER_MISMATCH')
        bad = _same(_pod(admission.get('pod_before_request'), expected, expected['container'], terminal=True, ready=False),
                    _pod(admission.get('pod_after_request'), expected, expected['container'], terminal=True, ready=False),
                    'ADMISSION_CONTAINER_ATTEMPT_CHANGED')
        _same(failed, bad, 'LOG_ADMISSION_ATTEMPT_MISMATCH')
        _require(bad['pod_uid'] != baseline['pod_uid']
                 and bad['attempt']['container_id'] != baseline['attempt']['container_id'], 'FAULTY_NEW_GENERATION_UNVERIFIED')
        rejected = _direct(admission, bad, expected, normal=False)
        good = _same(_pod(recovery.get('pod_before_request'), expected, expected['container'], ready=True),
                     _pod(recovery.get('pod_after_request'), expected, expected['container'], ready=True),
                     'RECOVERY_GENERATION_CHANGED')
        _require(good['pod_uid'] not in (bad['pod_uid'],baseline['pod_uid'])
                 and good['attempt']['container_id'] not in (bad['attempt']['container_id'],baseline['attempt']['container_id']),
                 'RECOVERY_NEW_GENERATION_UNVERIFIED')
        normal = _direct(recovery, good, expected, normal=True)
        _require(len({normal['request_id_sha256'],rejected['request_id_sha256'],prior_normal['request_id_sha256']}) == 3, 'REQUEST_ID_REUSED')
        # Optional until the existing producer emits this metadata on both paths.
        if any(row['body_sha256'] is not None for row in (normal,rejected,prior_normal)):
            _hash(normal['body_sha256'], 'REQUEST_BODY_HASH_UNAVAILABLE')
            _require(normal['body_sha256'] == rejected['body_sha256'] == prior_normal['body_sha256'], 'REQUEST_BODY_MISMATCH')
        fseq = _sequence(failure, ('pod_before_log',), 'log_receipt', ('pod_after_log',))
        before = ('namespace_before', 'probe_before_request', 'pod_before_request')
        after = ('pod_after_request', 'probe_after_request', 'namespace_after')
        aseq = _sequence(admission, before, 'direct_request', after)
        rseq = _sequence(recovery, before, 'direct_request', after)
        nseq = _sequence(normal_before, before, 'direct_request', after)
        _require(nseq[1] < fseq[0] and fseq[1] < aseq[0] and aseq[1] < rseq[0], 'PHASE_ORDER_INVALID')
        facts['activation_steps'] = [
            {'step':'normal-before-startup-failure','engine':baseline,**prior_normal,
             'normal_control_verified':True,'native_stage_ack_observed':False},
            {'step': 'startup-failure-admission', 'proof_source': 'kubelet-terminal-container-attempt',
             'declared_failure_kind': expected['failure_kind'], 'engine': bad, **rejected,
             'request_rejected': True, 'request_accepted': False, 'readiness_closed': True,
             'attribution': 'same-failed-Pod-address-and-terminal-attempt', 'service_routing_attribution': False,
             'original_error_sha256': log['stdout_sha256'], 'original_error_bytes': log['stdout_bytes'],
             'marker_sha256': log['marker_sha256'], 'native_stage_ack_observed': False},
            {'step': 'startup-recovery-normal', 'engine': good, **normal,
             'new_generation_normal': True, 'native_stage_ack_observed': False}]
        for step in facts['activation_steps']:
            step['context'] = expected['context']
            step['namespace'] = {'namespace':expected['namespace'],'namespace_uid':expected['namespace_uid']}
            step['sequence_scope'] = receipt['sequence_scope']
        facts['activation_steps'][1]['failure'] = {
            'pod_uid':bad['pod_uid'],'attempt':bad['attempt'],
            'original_error_sha256':log['stdout_sha256'],'original_error_bytes':log['stdout_bytes'],
            'marker_sha256':log['marker_sha256'],'source':'kubelet-terminal-container-attempt',
            'upper_boot_failure':True}
        facts['activation_scope_complete'] = True
    except _Invalid as error:
        facts['activation_missing'].append(_PREFIX + str(error))
    return facts
