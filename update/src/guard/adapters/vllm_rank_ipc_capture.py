"""동결 준비물·원 snapshot을 읽기만 검증한다. 인증·그룹 수락은 주장하지 않는다."""
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat

from . import vllm_rank_ipc_runtime as runtime


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()

def _read(path, bound):
    before = os.stat(path, follow_symlinks=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not 1 <= opened.st_size <= bound:
            raise ValueError('RANK_IPC_CAPTURE_BOUND')
        raw = os.read(fd, bound + 1)
        after, current = os.fstat(fd), os.stat(path, follow_symlinks=False)
        if (len(raw) != opened.st_size or len(raw) > bound
                or len({runtime.sources._identity(v) for v in (before, opened, after, current)}) != 1):
            raise ValueError('RANK_IPC_CAPTURE_BOUND')
        return raw
    finally:
        os.close(fd)

def verify_capture(prepared, capture):
    """준비 자산·비밀 키·고정 본문과 각 원 snapshot을 대조한다. 인증/완료는 반환하지 않는다."""
    prepared, capture = Path(prepared), Path(capture)
    manifest = json.loads(_read(prepared / 'manifest.json', 32768), object_pairs_hook=runtime._pairs)
    raw_settings = _read(prepared / 'settings.json', 32768)
    settings = json.loads(raw_settings, object_pairs_hook=runtime._pairs)
    assets_current = set(manifest['assets']) == runtime.ASSETS
    if not assets_current:
        # 실제 반출한 과거 준비물은 정확 소스 SHA/전체 자산 해시 행으로만 재검증한다.
        # 현재 준비물의 필수 자산을 누락시킨 것으로 대신할 수 없다.
        history = json.loads(runtime.sources.PROFILES.read_bytes()).get('prepared_history', [])
        if not any(manifest.get('source_manifest_sha256') == row['source_manifest_sha256']
                   and manifest['assets'] == row['assets'] for row in history):
            raise ValueError('RANK_IPC_PREPARED_MISMATCH')
    if (manifest['schema'] != 'guard-rank-ipc-prepared-candidate/1'
            or settings['schema'] != 'guard-rank-ipc-settings/1'
            or any(manifest.get(k) is not False for k in ('observer_installed', 'engine_group_verified', 'producer_registered'))
            or sha256(raw_settings).hexdigest() != manifest['settings_sha256']
            or settings['assets'] != manifest['assets'] or settings['image'] != manifest['image']
            or settings['session'] != manifest['session']):
        raise ValueError('RANK_IPC_PREPARED_MISMATCH')
    for name, expected in manifest['assets'].items():
        runtime._asset(prepared / name, expected)
    profiles = json.loads((prepared / 'guard/adapters/assets/vllm_rank_ipc_profiles.json').read_bytes())
    profile, = [p for p in profiles['profiles'] if p['image'] == manifest['image']]
    key = bytes.fromhex(settings['key'])
    rows, gaps, seen = [], [], set()
    paths = sorted(capture.glob('pid-*.json'))
    if not 1 <= len(paths) <= 512:
        raise ValueError('RANK_IPC_CAPTURE_BOUND')
    for path in paths:
        record = json.loads(_read(path, 131072), object_pairs_hook=runtime._pairs)
        if (type(record) is not dict or set(record) != {'snapshot', 'hmac_sha256'}
                or type(record['hmac_sha256']) is not str or re.fullmatch('[0-9a-f]{64}', record['hmac_sha256']) is None):
            raise ValueError('RANK_IPC_SNAPSHOT_INVALID')
        snapshot = record['snapshot']
        if (type(snapshot) is not dict or set(snapshot) != {'schema', 'session', 'image', 'settings_sha256',
                'observer_assets', 'context', 'ordinal', 'payload'}
                or not hmac.compare_digest(hmac.new(key, canonical(snapshot), sha256).hexdigest(), record['hmac_sha256'])
                or snapshot['schema'] != 'guard-rank-ipc-snapshot-candidate/1'
                or snapshot['session'] != manifest['session'] or snapshot['image'] != manifest['image']
                or snapshot['settings_sha256'] != manifest['settings_sha256']
                or snapshot['observer_assets'] != manifest['assets']):
            raise ValueError('RANK_IPC_SNAPSHOT_INVALID')
        context = snapshot['context']
        if (type(context) is not dict or set(context) != {'pid', 'start_ticks', 'uid', 'boot_sha256',
                'pid_namespace', 'time_namespace', 'time_offsets_sha256'}
                or any(type(context[k]) is not int or not 0 <= context[k] < 2**63
                       for k in ('pid', 'start_ticks', 'uid', 'pid_namespace', 'time_namespace'))
                or context['pid'] == 0 or type(snapshot['ordinal']) is not int or not 1 <= snapshot['ordinal'] <= 256
                or path.name != 'pid-{}-{}-{}.json'.format(context['pid'], context['start_ticks'], snapshot['ordinal'])
                or any(type(context[k]) is not str or re.fullmatch('[0-9a-f]{64}', context[k]) is None
                       for k in ('boot_sha256', 'time_offsets_sha256'))):
            raise ValueError('RANK_IPC_CONTEXT_INVALID')
        identity = (context['pid'], context['start_ticks'], snapshot['ordinal'])
        if identity in seen:
            raise ValueError('RANK_IPC_SNAPSHOT_REPLAY')
        seen.add(identity)
        payload = snapshot['payload']
        if payload.get('gaps'):
            gaps.extend(payload['gaps'])
            continue
        rpc = payload.get('schema') == 'guard-rank-rpc-hook-candidate/1'
        table = profile.get('rpc_sources', {}) if rpc else profile['sources']
        if payload.get('source_module') not in table:
            raise ValueError('RANK_IPC_SOURCE_RECORD_INVALID')
        expected = table[payload['source_module']]
        hook = expected['hooks'][payload['point']]
        graph = payload['source_graph']
        required = hook.get('source_modules', list(table))
        if (payload['schema'] != ('guard-rank-rpc-hook-candidate/1' if rpc else 'guard-rank-ipc-hook-candidate/1') or payload['context'] != context
                or payload['image'] != manifest['image'] or payload['profile'] != profile['id']
                or payload['source_sha256'] != expected['sha256']
                or payload['body_code_sha256'] != expected['body_code_sha256'][payload['point']]
                or payload['phase'] != hook['phase'] or payload['role'] != hook['role']
                or any(payload.get(k) is not False for k in ('source_authenticated', 'engine_group_verified', 'producer_registered'))
                or not set(required) <= set(graph) or not set(graph) <= set(table)
                or any(digest != table[name]['sha256'] for name, digest in graph.items())):
            raise ValueError('RANK_IPC_SOURCE_RECORD_INVALID')
        if rpc:
            fingerprint = lambda v: type(v) is str and re.fullmatch('[0-9a-f]{64}', v) is not None
            ids = payload.get('request_hmac_sha256')
            phases = {'CONTROL_READY', 'CONTROL_RELEASE', 'CONTROL_EXPIRED', 'CONTROL_EXCEPTION', 'CONTROL_CANCELLED', 'CONTROL_STALL'}
            idle_events = {'IDLE_CALL', 'IDLE_RETURN', 'IDLE_RAISE'}
            idle = payload.get('event') in idle_events
            if (payload.get('event') not in {'CALL', 'RETURN', 'RAISE', 'CONTROL_RAISE', *phases, *idle_events}
                    or not fingerprint(payload.get('call_hmac_sha256'))
                    or type(ids) is not list or len(ids) > 8 or any(not fingerprint(v) for v in ids)
                    or len(set(ids)) != len(ids)
                    or any(type(payload.get(k)) is not bool for k in
                           ('rpc_parent_frame_verified', 'rpc_parent_worker_verified'))
                    or payload['rpc_parent_frame_verified'] != payload['rpc_parent_worker_verified']
                    or type(payload.get('original_invoked')) is not bool):
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            parent = payload.get('rpc_parent_call_hmac_sha256')
            if idle:
                basis = payload.get('idle_basis')
                if (hook.get('no_scheduled_work') is None or hook['kind'] != 'rpc' or ids != []
                        or payload['rpc_parent_frame_verified'] or payload['rpc_parent_worker_verified']
                        or type(basis) is not dict or set(basis) != {'empty_scheduled_requests',
                            'total_scheduled_tokens', 'request_bound', 'fault_eligible'}
                        or basis['empty_scheduled_requests'] is not True
                        or type(basis['total_scheduled_tokens']) is not int or basis['total_scheduled_tokens'] != 0
                        or basis['request_bound'] is not False or basis['fault_eligible'] is not False):
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            elif 'idle_basis' in payload:
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if payload['rpc_parent_frame_verified']:
                if (not hook.get('parent') or not ids or not fingerprint(parent)
                        or not fingerprint(payload.get('rpc_parent_object_hmac_sha256'))
                        or parent == payload['call_hmac_sha256']):
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            elif parent is not None or payload.get('rpc_parent_object_hmac_sha256') is not None:
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if hook.get('parent') and not idle and not payload['rpc_parent_frame_verified']:
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if hook['kind'] in {'rpc', 'rpc-reply'} and not idle and not ids:
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if payload['original_invoked'] != (payload['event'] in {'RETURN', 'RAISE', 'IDLE_RETURN', 'IDLE_RAISE'}):
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if payload['event'] in phases or payload['event'] == 'CONTROL_RAISE':
                if hook.get('fault_eligible') is not True or not payload['rpc_parent_frame_verified']:
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if payload['event'] in phases:
                control = payload.get('control')
                if (type(control) is not dict or set(control) != {'nonce', 'fault_kind', 'arm_sha256',
                        'ready_sha256', 'action_sha256', 'target_sha256', 'control_code_sha256', 'barrier_only'}
                        or type(control['nonce']) is not str or re.fullmatch('[0-9a-f]{32}', control['nonce']) is None
                        or control['fault_kind'] not in {'delay', 'death', 'exception', 'communication-stall'}
                        or not all(fingerprint(control[k]) for k in ('arm_sha256', 'ready_sha256', 'target_sha256', 'control_code_sha256'))
                        or control['control_code_sha256'] != manifest['assets']['guard/adapters/vllm_rank_rpc_control.py']['sha256']
                        or control['barrier_only'] is not (payload['event'] not in {'CONTROL_EXCEPTION', 'CONTROL_STALL'})
                        or (control['action_sha256'] is not None and not fingerprint(control['action_sha256']))):
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
                if (control['fault_kind'] not in hook.get('fault_kinds', ['delay', 'death', 'exception'])
                        or payload['event'] == 'CONTROL_STALL' and (control['fault_kind'] != 'communication-stall'
                            or hook['kind'] != 'rpc-reply' or control['action_sha256'] is None)):
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if any(type(payload.get(k)) is not int or not 0 <= payload[k] < 2**63
                   for k in ('operation_started_ns', 'observation_ns')) or payload['operation_started_ns'] > payload['observation_ns']:
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if hook['kind'] == 'external-id' and payload['event'] == 'RETURN':
                if not fingerprint(payload.get('external_request_hmac_sha256')):
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if hook['kind'] == 'assignment' and payload['event'] == 'RETURN':
                assignment = payload.get('assignment', {})
                if (assignment.get('schema') != 'guard-rank-request-assignment-candidate/1'
                        or not all(fingerprint(assignment.get(k)) for k in ('external_hmac_sha256', 'internal_hmac_sha256'))
                        or any(assignment.get(k) is not False for k in
                               ('source_authenticated', 'rpc_active_verified', 'producer_registered'))):
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if hook['kind']=='deferred-result':
                deferred=payload.get('deferred_rpc');exception_kind=payload.get('exception_kind')
                fields={'schema','source_module','point','origin_call_hmac_sha256','origin_object_hmac_sha256',
                    'origin_started_ns','origin_returned_ns','future_object_hmac_sha256'}
                if (type(deferred)is not dict or set(deferred)!=fields
                        or deferred['schema']!='guard-rank-deferred-request-binding/1'
                        or deferred['source_module']!=payload['source_module']
                        or type(deferred['point'])is not str
                        or expected['hooks'].get(deferred['point'],{}).get('deferred_result')!=
                            {'class':payload['point'].split('.')[0],'point':payload['point']}
                        or not ids or any(not fingerprint(deferred[k])for k in
                            ('origin_call_hmac_sha256','origin_object_hmac_sha256','future_object_hmac_sha256'))
                        or deferred['future_object_hmac_sha256']!=payload.get('object_hmac_sha256')
                        or deferred['origin_call_hmac_sha256']==payload['call_hmac_sha256']
                        or any(type(deferred[k])is not int or not 0<=deferred[k]<2**63 for k in('origin_started_ns','origin_returned_ns'))
                        or not deferred['origin_started_ns']<=deferred['origin_returned_ns']<=payload['operation_started_ns']
                        or payload['event']not in{'CALL','RETURN','RAISE'}or 'exception_kind'not in payload
                        or ((type(exception_kind)is not str or exception_kind not in{'timeout','runtime-error','other'})
                            if payload['event']=='RAISE'else exception_kind is not None)):
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            elif 'deferred_rpc'in payload or 'exception_kind'in payload:raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if hook['kind']=='asgi-send':
                sent=payload.get('http_send')
                if (type(sent)is not dict or set(sent)!={'schema','type','method','path','status','more_body','external_request_hmac_sha256'}
                        or sent['schema']!='guard-rank-native-http-send/1'or sent['method']!='POST'
                        or type(sent['path'])is not str or sent['path']not in hook['paths']
                        or type(sent['type'])is not str or sent['type']not in{'http.response.start','http.response.body'}
                        or not fingerprint(sent['external_request_hmac_sha256'])or ids!=[sent['external_request_hmac_sha256']]
                        or payload['event']not in{'CALL','RETURN','RAISE'}):raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
                if sent['type']=='http.response.start':
                    if type(sent['status'])is not int or not 100<=sent['status']<=599 or sent['more_body']is not None:
                        raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
                elif sent['status']is not None or type(sent['more_body'])is not bool:raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            elif 'http_send'in payload:raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
        rows.append(payload)
    return {'schema': 'guard-rank-ipc-capture-review/1', 'session': manifest['session'],
            'image': manifest['image'], 'snapshots': len(paths), 'source_rows_verified': len(rows),
            'rows': rows, 'gaps': gaps, 'source_authenticated': False,
            'engine_group_verified': False, 'producer_registered': False}
