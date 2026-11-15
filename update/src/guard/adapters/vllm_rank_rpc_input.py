"""사전 결합된 RPC 원 기록의 부분 사실만 투영한다. 그룹/탐지/복구를 추정하지 않는다."""
from hashlib import sha256
import hmac
import json
from pathlib import Path
import re

from . import vllm_rank_rpc_control as control
from . import vllm_rank_ipc_capture as capture_code

FAULT_KINDS = frozenset({'delay', 'death', 'exception'})
CASE_FIELDS = {'schema', 'target', 'arm', 'ready', 'physical_action', 'physical_resume', 'client',
    'source_authenticated', 'engine_group_verified', 'producer_registered', 'scope_complete'}
CLIENT_FIELDS = {'context', 'header_hmac_sha256', 'request_started_ns', 'response_finished_ns',
    'response_id_hmac_sha256', 'response_sha256', 'status', 'complete'}


def require_case_binding(case, expected):
    """이 범위 검사만으로 HMAC·원 관측·판정 자격이 검증된 것은 아니다."""
    if (type(case) is not dict or type(case.get('arm')) is not dict
            or case['arm'].get('schema') != 'guard-rank-rpc-arm/2'
            or type(case.get('target')) is not dict
            or case['target'].get('schema') != 'guard-rank-rpc-lab-target/2'):
        raise ValueError('RANK_RPC_PRODUCT_BINDING_REQUIRED')
    control.product_binding(expected, expected.get('session') if type(expected) is dict else None)
    control.product_binding(case['arm'].get('product_binding'), expected['session'])
    if (control.canonical(case['arm']['product_binding']) != control.canonical(expected)
            or control.canonical(case['target'].get('product_binding')) != control.canonical(expected)
            or case['arm'].get('session') != expected['session']
            or case['target'].get('owner', {}).get('pod_uid') != expected['pod_uid']
            or case['arm'].get('target_sha256') != control.digest(case['target'])):
        raise ValueError('RANK_RPC_PRODUCT_BINDING_MISMATCH')


def _require(test, code='RANK_RPC_INPUT_PROOF_INVALID'):
    if not test:
        raise ValueError(code)


def _times(*values):
    _require(all(type(v) is int and 0 <= v < 2**63 for v in values))
    _require(all(a <= b for a, b in zip(values, values[1:])))


def _cohost(value, expected):
    return (control.clock(value) == control.clock(expected)
        and value['pid_namespace'] == expected['pid_namespace'] and value['uid'] == expected['uid'])


class _Files:
    """선택한 원 bytes를 두 번 대조해 검증 중 파일 변경을 거부한다."""
    def __init__(self):
        self.raw = {}

    def read(self, path, bound=32768):
        path = Path(path)
        raw = capture_code._read(path, bound)
        if path in self.raw:
            _require(self.raw[path] == raw, 'RANK_RPC_INPUT_CHANGED')
        self.raw[path] = raw
        return raw

    def json(self, path, bound=32768):
        return json.loads(self.read(path, bound), object_pairs_hook=control._pairs)

    def finish(self):
        for path, raw in self.raw.items():
            _require(capture_code._read(path, max(1, len(raw))) == raw, 'RANK_RPC_INPUT_CHANGED')


def _selected(records, *, event, call):
    matches = [p for p in records if p.get('event') == event and p.get('call_hmac_sha256') == call]
    _require(len(matches) == 1)
    return matches[0]


def project(prepared, capture, expected):
    """호출자가 독립적으로 공급한 예상 제품 범위와 대조하며 비밀/원문을 반환하지 않는다."""
    control.product_binding(expected, expected.get('session') if type(expected) is dict else None)
    prepared, capture = Path(prepared), Path(capture)
    files = _Files()
    manifest = files.json(prepared / 'manifest.json')
    settings = files.json(prepared / 'settings.json')
    _require(sha256(files.raw[prepared / 'manifest.json']).hexdigest() == expected['prepared_sha256']
        and manifest['session'] == expected['session'], 'RANK_RPC_INPUT_PREPARED_MISMATCH')
    controls, hashes = {}, {}
    def read_control(name):
        path = capture / 'controls' / name
        envelope = files.json(path)
        _require(type(envelope) is dict and set(envelope) == {'payload', 'hmac_sha256'}
            and control.fingerprint(envelope['hmac_sha256'])
            and hmac.compare_digest(envelope['hmac_sha256'], hmac.new(bytes.fromhex(settings['key']),
                control.DOMAIN + control.canonical(envelope['payload']), sha256).hexdigest()), 'RANK_RPC_INPUT_CONTROL_HMAC')
        controls[name], hashes[name] = envelope['payload'], sha256(files.raw[path]).hexdigest()
        return envelope['payload']
    case = read_control('control-case-result.json')
    require_case_binding(case, expected)
    _require(set(case) == CASE_FIELDS and case['schema'] == 'guard-rank-rpc-lab-case/1'
        and all(case[k] is False for k in ('source_authenticated', 'engine_group_verified', 'producer_registered', 'scope_complete')))
    binding = read_control('control-product-binding.json')
    _require(control.canonical(binding) == control.canonical(expected), 'RANK_RPC_PRODUCT_BINDING_MISMATCH')
    arm = read_control('control-arm.json'); ready = read_control('control-ready.json')
    physical = read_control('control-physical-action.json')
    _require(case['arm'] == arm and case['ready'] == ready and case['physical_action'] == physical)
    _require(set(arm) == control.ARM_FIELDS | {'product_binding'} and arm['fault_kind'] in FAULT_KINDS
        and arm['settings_sha256'] == sha256(files.raw[prepared / 'settings.json']).hexdigest()
        and arm['assets_sha256'] == control.digest(manifest['assets']) and arm['image'] == manifest['image']
        and type(arm['nonce']) is str and re.fullmatch('[0-9a-f]{32}', arm['nonce']) is not None)
    _times(arm['issued_ns'], arm['expires_ns'])
    _require(0 < arm['expires_ns'] - arm['issued_ns'] <= 120_000_000_000)
    context = control.context(arm['target_context'])
    target = case['target']
    _require(set(target) == {'schema', 'owner', 'image', 'model', 'context', 'parent_object_hmac_sha256',
        'initial_capture_sha256', 'product_binding'} and target['image'] == manifest['image']
        and target['context'] == context and target['parent_object_hmac_sha256'] == arm['parent_object_hmac_sha256']
        and target['initial_capture_sha256'] == arm['initial_capture_sha256']
        and type(target['model']) is str and 1 <= len(target['model']) <= 1024
        and set(target['owner']) == {'pod_uid', 'model_receipt_sha256'}
        and control.fingerprint(target['owner']['model_receipt_sha256']))
    _require(set(ready) == {'schema', 'nonce', 'fault_kind', 'arm_sha256', 'context', 'call_hmac_sha256',
        'parent_call_hmac_sha256', 'target_sha256', 'source_point', 'prepared_assets_sha256',
        'source_refs', 'ready_ns', 'expires_ns', 'rpc_active'}
        and ready['schema'] == 'guard-rank-rpc-ready/1' and ready['rpc_active'] is True
        and ready['arm_sha256'] == hashes['control-arm.json'] and ready['nonce'] == arm['nonce']
        and ready['fault_kind'] == arm['fault_kind'] and ready['context'] == context
        and ready['source_point'] == arm['target_point'] and ready['target_sha256'] == arm['target_sha256']
        and ready['prepared_assets_sha256'] == arm['assets_sha256'] and ready['expires_ns'] == arm['expires_ns'])
    _times(arm['issued_ns'], ready['ready_ns'], arm['expires_ns'])
    _require(set(ready['source_refs']) == {'call_capture_sha256', 'header_capture_sha256', 'assignment_capture_sha256'})
    paths = sorted((capture / 'snapshots').glob('pid-*.json'))
    _require(1 <= len(paths) <= 512)
    snapshots = {sha256(files.read(p, 131072)).hexdigest(): files.json(p, 131072)['snapshot']['payload'] for p in paths}
    _require(len(snapshots) == len(paths), 'RANK_RPC_INPUT_DUPLICATE_CAPTURE')
    review = capture_code.verify_capture(prepared, capture / 'snapshots')
    rows = review['rows']
    profiles = files.json(prepared / 'guard/adapters/assets/vllm_rank_ipc_profiles.json', 262144)
    profile, = [p for p in profiles['profiles'] if p['image'] == manifest['image']]
    call = snapshots.get(ready['source_refs']['call_capture_sha256'], {})
    header = snapshots.get(ready['source_refs']['header_capture_sha256'], {})
    _require(call in rows and header in rows)
    _require(call.get('event') == 'CALL' and call.get('context') == context
        and call.get('point') == arm['target_point'] and call.get('call_hmac_sha256') == ready['call_hmac_sha256']
        and call.get('rpc_parent_call_hmac_sha256') == ready['parent_call_hmac_sha256']
        and call.get('rpc_parent_object_hmac_sha256') == arm['parent_object_hmac_sha256']
        and call.get('source_sha256') == arm['source_sha256'] and call.get('body_code_sha256') == arm['body_code_sha256']
        and call.get('rpc_parent_frame_verified') is True and call.get('rpc_parent_worker_verified') is True
        and call.get('original_invoked') is False and type(call.get('scalars', {}).get('gpu_rank')) is int
        and call['scalars']['gpu_rank'] == 0 and type(call['scalars'].get('configured_tp_size')) is int
        and call['scalars']['configured_tp_size'] == 2 and len(call.get('request_hmac_sha256', [])) == 1)
    hook = profile['rpc_sources'][call['source_module']]['hooks'][call['point']]
    _require(hook.get('fault_eligible') is True)
    parent = _selected(rows, event='CALL', call=ready['parent_call_hmac_sha256'])
    _require(parent['context'] == context and parent['request_hmac_sha256'] == call['request_hmac_sha256'])
    _require(header.get('event') == 'RETURN' and header.get('context') == arm['header_context']
        and header.get('external_request_hmac_sha256') == arm['header_hmac_sha256']
        and profile['rpc_sources'][header['source_module']]['hooks'][header['point']]['kind'] == 'external-id')
    _require(_cohost(header['context'], context))
    _times(arm['issued_ns'], header['observation_ns'], call['observation_ns'], ready['ready_ns'])
    request = call['request_hmac_sha256'][0]
    if profile['request_binding']['kind'] == 'assigned':
        mapping = snapshots.get(ready['source_refs']['assignment_capture_sha256'], {})
        _require(mapping in rows)
        _require(mapping.get('event') == 'RETURN' and mapping.get('context') == arm['assignment_context']
            and mapping.get('assignment', {}).get('external_hmac_sha256') == arm['external_hmac_sha256']
            and mapping['assignment']['internal_hmac_sha256'] == request
            and profile['rpc_sources'][mapping['source_module']]['hooks'][mapping['point']]['kind'] == 'assignment'
            and _cohost(mapping['context'], context))
        _times(header['observation_ns'], mapping['observation_ns'], call['observation_ns'])
    else:
        _require(profile['request_binding']['kind'] == 'direct' and arm['assignment_context'] is None
            and ready['source_refs']['assignment_capture_sha256'] is None and request == arm['external_hmac_sha256'])
    claim = read_control('control-claimed-' + arm['nonce'] + '.json')
    _require(claim == {'schema': 'guard-rank-rpc-claim/1', 'nonce': arm['nonce'], 'arm_sha256': hashes['control-arm.json'],
        'context': context, 'parent_object_hmac_sha256': arm['parent_object_hmac_sha256'], 'call_hmac_sha256': ready['call_hmac_sha256']})
    source_ready = _selected(rows, event='CONTROL_READY', call=ready['call_hmac_sha256'])
    _phase(source_ready, call, arm, hashes)
    _times(ready['ready_ns'], source_ready['observation_ns'], physical['before_started_ns'], physical['before_finished_ns'])
    _require(physical.get('schema') == 'guard-rank-rpc-action-receipt/1' and physical['context'] == context
        and physical['fault_kind'] == arm['fault_kind'] and physical['arm_sha256'] == hashes['control-arm.json']
        and physical['ready_sha256'] == hashes['control-ready.json'] and physical['source_refs'] == ready['source_refs']
        and physical['before_state'] in {'R', 'S', 'D', 'I'})
    physical_fields = {'schema', 'arm_sha256', 'ready_sha256', 'context', 'fault_kind', 'source_refs',
        'before_started_ns', 'before_finished_ns', 'before_state', 'action', 'signal'}
    kind = arm['fault_kind']; events = []; codes = ['RANK_RPC_GROUP_UNVERIFIED', 'RANK_RPC_DETECTION_UNOBSERVED',
        'RANK_RPC_UPPER_DELIVERY_UNVERIFIED', 'RANK_RPC_RECOVERY_UNOBSERVED']
    if review['gaps']:
        codes.append('RANK_RPC_SOURCE_GAP')
    if kind == 'exception':
        _require(set(physical) == physical_fields | {'published_ns'})
        action = read_control('control-action.json')
        _action(action, 'raise', arm, hashes, 'control-physical-action.json')
        raised = _selected(rows, event='CONTROL_RAISE', call=ready['call_hmac_sha256'])
        _require(raised['context'] == context and raised['request_hmac_sha256'] == [request]
            and raised['rpc_parent_call_hmac_sha256'] == ready['parent_call_hmac_sha256']
            and raised['original_invoked'] is False and physical['signal'] is None and physical['action'] == 'source-exception')
        _times(physical['before_finished_ns'], physical['published_ns'], raised['observation_ns'], arm['expires_ns'])
        applied = raised['observation_ns']
    else:
        _require(set(physical) == physical_fields | {'signal_started_ns', 'signal_finished_ns', 'after_state', 'after_observed_ns'})
        _require(physical['action'] == 'pidfd-signal' and type(physical['signal']) is int
            and physical['signal'] == (19 if kind == 'delay' else 9)
            and physical['after_state'] == ('T' if kind == 'delay' else 'EXITED'))
        _times(physical['before_finished_ns'], physical['signal_started_ns'], physical['signal_finished_ns'],
            physical['after_observed_ns'], arm['expires_ns'])
        applied = physical['after_observed_ns']
    events.append({'event': 'fault_applied', 'at_ns': applied, 'ack': True})
    _require(not any(p.get('call_hmac_sha256') == ready['call_hmac_sha256']
        and p.get('event') in {'RETURN', 'RAISE', 'CONTROL_RELEASE', 'CONTROL_CANCELLED', 'CONTROL_EXPIRED'}
        and p['observation_ns'] <= applied for p in rows), 'RANK_RPC_INPUT_INACTIVE_FAULT')
    if kind == 'death':
        _require(not any(p.get('call_hmac_sha256') == ready['call_hmac_sha256']
            and p.get('event') == 'CONTROL_RELEASE' for p in rows), 'RANK_RPC_INPUT_DEATH_RELEASE')
    if kind == 'delay' and case['physical_resume'] is not None:
        resume = read_control('control-physical-resume.json')
        _require(set(resume) == {'schema', 'context', 'arm_sha256', 'ready_sha256', 'signal', 'before_started_ns',
            'before_state', 'before_finished_ns', 'signal_started_ns', 'signal_finished_ns', 'after_state', 'after_observed_ns'}
            and case['physical_resume'] == resume and resume.get('schema') == 'guard-rank-rpc-resume-receipt/1'
            and resume['context'] == context and resume['arm_sha256'] == hashes['control-arm.json']
            and resume['ready_sha256'] == hashes['control-ready.json'] and type(resume['signal']) is int and resume['signal'] == 18
            and resume['before_state'] == 'T' and resume['after_state'] in {'R', 'S', 'D', 'I'})
        _times(applied, resume['before_started_ns'], resume['before_finished_ns'], resume['signal_started_ns'],
            resume['signal_finished_ns'], resume['after_observed_ns'], arm['expires_ns'])
        action = read_control('control-action.json'); _action(action, 'release', arm, hashes, 'control-physical-resume.json')
        releases = [p for p in rows if p.get('event') == 'CONTROL_RELEASE'
            and p.get('call_hmac_sha256') == ready['call_hmac_sha256']]
        _require(len(releases) <= 1)
        if releases:
            released, = releases
            _phase(released, call, arm, hashes, hashes['control-action.json'])
            _times(resume['after_observed_ns'], released['observation_ns'])
            events.append({'event': 'fault_released', 'at_ns': resume['after_observed_ns'], 'ack': True})
        else:
            codes.append('RANK_RPC_RELEASE_UNOBSERVED')
    else:
        _require(case['physical_resume'] is None)
        codes.append('RANK_RPC_RELEASE_UNOBSERVED')
    client = case['client']
    _require(type(client) is dict and CLIENT_FIELDS <= set(client) <= CLIENT_FIELDS | {'transport_error'}
        and type(client['complete']) is bool and (client['status'] is None or type(client['status']) is int and 100 <= client['status'] <= 599)
        and client['header_hmac_sha256'] == arm['header_hmac_sha256']
        and _cohost(client['context'], context)
        and ('transport_error' not in client or type(client['transport_error']) is bool)
        and all(v is None or control.fingerprint(v) for v in (client['response_sha256'], client['response_id_hmac_sha256'])))
    _times(arm['issued_ns'], client['request_started_ns'], header['observation_ns'], client['response_finished_ns'])
    # 클라이언트 EOF는 actor의 EXITED 관측보다 먼저 올 수 있다.
    # 현재 이 응답을 탐지/상위 전달 사건으로 쓰지 않으므로 관측 순서를 인과 순서로 바꾸지 않는다.
    for name, pin in manifest['assets'].items():
        capture_code.runtime._asset(prepared / name, pin)
    files.finish()
    source_set = {str(path.relative_to(capture)): sha256(raw).hexdigest()
        for path, raw in files.raw.items() if path.is_relative_to(capture)}
    return {'schema': 'guard-rank-rpc-partial-projection/1', 'events': [{**e, 'context': context,
        'rank': 0, 'fault_kind': kind, 'request_sha256': arm['header_hmac_sha256']} for e in events],
        'diagnostic_codes': codes, 'scope_complete': False, 'source_authenticated': False,
        'engine_group_verified': False, 'producer_registered': False,
        'source_snapshots_verified': review['snapshots'], 'source_set_sha256': control.digest(source_set),
        'client_observation': {'status': client['status'],
            'complete': client['complete'], 'upper_delivery_verified': False}}


def _phase(value, call, arm, hashes, action=None):
    _require(value['context'] == call['context'] and value['request_hmac_sha256'] == call['request_hmac_sha256']
        and value['rpc_parent_call_hmac_sha256'] == call['rpc_parent_call_hmac_sha256']
        and value['original_invoked'] is False and value['control']['nonce'] == arm['nonce']
        and value['control']['fault_kind'] == arm['fault_kind'] and value['control']['target_sha256'] == arm['target_sha256']
        and value['control']['arm_sha256'] == hashes['control-arm.json']
        and value['control']['ready_sha256'] == hashes['control-ready.json']
        and value['control']['action_sha256'] == action and value['control']['barrier_only'] is True)


def _action(value, kind, arm, hashes, receipt):
    _require(value == {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
        'arm_sha256': hashes['control-arm.json'], 'ready_sha256': hashes['control-ready.json'],
        'action': kind, 'receipt_sha256': hashes[receipt]})
