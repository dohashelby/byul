"""후속 /2의 닫힌 snapshot과 검증된 /28 원 기록을 읽기만 대조한다."""
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat

from . import vllm_lifecycle_catalog as catalog
from . import vllm_rank_v1_worker_runtime as runtime
from .vllm_rank_ipc_capture import canonical, _read
from ..collectors import lifecycle
from ..executors import lifecycle_capture

FALSE_FIELDS = runtime.binding.FALSE_FIELDS
MANIFEST_FIELDS = frozenset({'schema', 'session', 'image', 'settings_sha256', 'assets', 'source_manifest_sha256',
    'fresh_profile', 'fresh_prepared_sha256', 'binding_key_asset_sha256', 'observer_installed', *FALSE_FIELDS})
FACT_FIELDS = frozenset({'schema', 'session', 'image', 'profile', 'fresh_profile', 'fresh_prepared_sha256',
    'binding_key_asset_sha256', 'source_module', 'source_sha256', 'body_code_sha256', 'source_graph', 'point', 'event',
    'context', 'observation_ns', 'operation_started_ns', 'call_hmac_sha256', 'parent_receive_call_hmac_sha256',
    'worker_rank', 'owner_object_hmac_sha256', 'worker_object_hmac_sha256', 'queue_object_hmac_sha256',
    'request_hmac_sha256', 'request_binding_hmac_sha256', 'rpc_parent_frame_verified', 'rpc_parent_worker_verified',
    'response_status', 'gaps', *FALSE_FIELDS})
CONTEXT_FIELDS = frozenset({'pid', 'start_ticks', 'uid', 'boot_sha256', 'pid_namespace', 'time_namespace', 'time_offsets_sha256'})


def fresh_assets(folder):
    folder = Path(folder)
    raw = _read(folder / 'manifest.json', 16384)
    manifest = lifecycle.load_prepared(folder / 'manifest.json')
    if (manifest['profile'] != 'vllm-native-lifecycle-metadata/28' or json.loads(raw) != manifest
            or _read(folder / 'manifest.json', 16384) != raw):
        raise ValueError('RANK_V1_FRESH_PREPARED_INVALID')
    return manifest, sha256(raw).hexdigest()


def _prepared(folder, fresh_prepared, expected_source):
    folder = Path(folder)
    manifest = json.loads(_read(folder / 'manifest.json', 32768), object_pairs_hook=runtime.common._pairs)
    before = (folder / 'settings.json').lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600):
        raise ValueError('RANK_V1_SETTINGS_INVALID')
    raw = _read(folder / 'settings.json', 32768)
    if runtime.common.sources._identity(before) != runtime.common.sources._identity((folder / 'settings.json').lstat()):
        raise ValueError('RANK_V1_SETTINGS_INVALID')
    settings = runtime.validate_settings(json.loads(raw, object_pairs_hook=runtime.common._pairs))
    fresh, fresh_sha = fresh_assets(fresh_prepared)
    pin = fresh['assets']['request-binding-key.json']['sha256']
    if (type(manifest) is not dict or set(manifest) != MANIFEST_FIELDS
            or manifest['schema'] != 'guard-rank-ipc-prepared-candidate/2'
            or not runtime.binding._fingerprint(expected_source) or manifest['source_manifest_sha256'] != expected_source
            or any(manifest[k] is not False for k in ('observer_installed', *FALSE_FIELDS))
            or manifest['settings_sha256'] != sha256(raw).hexdigest()
            or any(manifest[k] != settings[k] for k in ('session', 'image', 'assets', 'fresh_profile',
                                                       'fresh_prepared_sha256', 'binding_key_asset_sha256'))
            or manifest['session'] != fresh['session'] or manifest['fresh_profile'] != fresh['profile']
            or manifest['fresh_prepared_sha256'] != fresh_sha or manifest['binding_key_asset_sha256'] != pin
            or settings['image'] not in catalog.row(fresh['profile'])['images']):
        raise ValueError('RANK_V1_PREPARED_MISMATCH')
    current = runtime.asset_bytes()
    expected = {name: {'bytes': len(value), 'sha256': sha256(value).hexdigest()} for name, value in current.items()}
    if manifest['assets'] != expected:
        raise ValueError('RANK_V1_OBSERVER_ASSET_MISMATCH')
    for name, meta in expected.items():
        runtime.common._asset(folder / name, meta)
    key = runtime.private_key(Path(fresh_prepared) / 'request-binding-key.json', expected_sha256=pin, session=fresh['session'])
    if not hmac.compare_digest(key, bytes.fromhex(settings['key'])):
        raise ValueError('RANK_V1_KEY_MISMATCH')
    table = json.loads((folder / 'guard/adapters/assets/vllm_rank_v1_source_reviews.json').read_bytes())
    rows = [row for row in table['profiles'] if row['image'] == manifest['image'] and 'worker_binding' in row]
    # 런타임별 원 code pin은 각 payload가 선택한 정확 행에서 따로 확인한다.
    if not rows or len({row['id'] for row in rows}) != 1:
        raise ValueError('RANK_V1_SOURCE_UNAVAILABLE')
    return manifest, settings, key, rows, fresh


def _context(value):
    if (type(value) is not dict or set(value) != CONTEXT_FIELDS
            or any(type(value[k]) is not int or not 0 <= value[k] < 2**63
                   for k in ('pid', 'start_ticks', 'uid', 'pid_namespace', 'time_namespace'))
            or value['pid'] == 0 or any(not runtime.binding._fingerprint(value[k]) for k in ('boot_sha256', 'time_offsets_sha256'))):
        raise ValueError('RANK_V1_CONTEXT_INVALID')


def _fact(payload, context, manifest, rows):
    if (type(payload) is not dict or set(payload) != FACT_FIELDS or payload['schema'] != runtime.binding.SCHEMA
            or any(payload[k] is not False for k in FALSE_FIELDS) or payload['gaps'] != []
            or payload['context'] != context or payload['rpc_parent_frame_verified'] is not True
            or payload['rpc_parent_worker_verified'] is not True
            or any(payload[k] != manifest[k] for k in ('session', 'image', 'fresh_profile', 'fresh_prepared_sha256',
                                                       'binding_key_asset_sha256'))
            or type(payload['worker_rank']) is not int or not 0 <= payload['worker_rank'] < 4096
            or any(not runtime.binding._fingerprint(payload[k]) for k in ('source_sha256', 'body_code_sha256',
                   'call_hmac_sha256', 'owner_object_hmac_sha256', 'worker_object_hmac_sha256', 'queue_object_hmac_sha256'))
            or any(type(payload[k]) is not int or not 0 <= payload[k] < 2**63 for k in ('operation_started_ns', 'observation_ns'))
            or payload['operation_started_ns'] > payload['observation_ns']):
        raise ValueError('RANK_V1_SOURCE_RECORD_INVALID')
    for name in ('request_hmac_sha256', 'request_binding_hmac_sha256'):
        values = payload[name]
        if (type(values) is not list or not 1 <= len(values) <= 8
                or any(not runtime.binding._fingerprint(v) for v in values) or len(values) != len(set(values))):
            raise ValueError('RANK_V1_REQUEST_RECORD_INVALID')
    if len(payload['request_hmac_sha256']) != len(payload['request_binding_hmac_sha256']):
        raise ValueError('RANK_V1_REQUEST_RECORD_INVALID')
    matches = []
    for row in rows:
        spec = row['worker_binding']
        if payload['profile'] != row['id'] or payload['source_module'] != spec['queue_module']:
            continue
        source = row['sources'][spec['queue_module']]
        if (type(payload['point']) is not str or payload['point'] not in {spec['receive_point'], spec['publish_point']}
                or payload['source_sha256'] != source['sha256']
                or payload['body_code_sha256'] != source['body_code_sha256'].get(payload['point'])
                or payload['source_graph'] != {name: p['sha256'] for name, p in row['sources'].items()}):
            continue
        receive = payload['point'] == spec['receive_point']
        if (receive and (payload['event'] != 'worker-rpc-received' or payload['parent_receive_call_hmac_sha256'] is not None
                         or payload['response_status'] is not None)
                or not receive and (payload['event'] != 'worker-response-published'
                    or not runtime.binding._fingerprint(payload['parent_receive_call_hmac_sha256'])
                    or payload['response_status'] not in ('success', 'failure'))):
            continue
        matches.append(row)
    if not matches:
        raise ValueError('RANK_V1_SOURCE_RECORD_INVALID')


def verify_capture(prepared, capture, *, fresh_prepared, fresh_recording, expected_source_manifest_sha256):
    manifest, settings, key, profiles, fresh = _prepared(prepared, fresh_prepared, expected_source_manifest_sha256)
    paths = sorted(Path(capture).glob('pid-*.json'))
    if not 1 <= len(paths) <= 512:
        raise ValueError('RANK_V1_CAPTURE_BOUND')
    rows, gaps, seen, calls = [], [], set(), set()
    for path in paths:
        raw = _read(path, 131072)
        record = json.loads(raw, object_pairs_hook=runtime.common._pairs)
        if (type(record) is not dict or set(record) != {'snapshot', 'hmac_sha256'}
                or not runtime.binding._fingerprint(record['hmac_sha256'])
                or not hmac.compare_digest(record['hmac_sha256'], hmac.new(key, canonical(record['snapshot']), sha256).hexdigest())):
            raise ValueError('RANK_V1_SNAPSHOT_HMAC_INVALID')
        snapshot = record['snapshot']
        if (type(snapshot) is not dict or set(snapshot) != {'schema', 'session', 'image', 'settings_sha256',
                'observer_assets', 'context', 'ordinal', 'payload'}
                or snapshot['schema'] != 'guard-rank-ipc-snapshot-candidate/2'
                or any(snapshot[k] != manifest[k] for k in ('session', 'image', 'settings_sha256'))
                or snapshot['observer_assets'] != manifest['assets']
                or type(snapshot['ordinal']) is not int or not 1 <= snapshot['ordinal'] <= 256):
            raise ValueError('RANK_V1_SNAPSHOT_INVALID')
        context = snapshot['context']; _context(context)
        identity = context['pid'], context['start_ticks'], snapshot['ordinal']
        if path.name != 'pid-{}-{}-{}.json'.format(*identity) or identity in seen:
            raise ValueError('RANK_V1_SNAPSHOT_REPLAY')
        seen.add(identity)
        payload = snapshot['payload']
        if type(payload) is dict and payload.get('schema') == 'guard-rank-rpc-diagnostic/2':
            if (set(payload) != {'schema', 'session', 'point', 'gaps', *FALSE_FIELDS}
                    or payload['session'] != manifest['session'] or any(payload[k] is not False for k in FALSE_FIELDS)
                    or type(payload['point']) is not str
                    or payload['point'] not in {r['worker_binding'][p] for r in profiles for p in ('receive_point', 'publish_point')}
                    or payload['gaps'] not in (['RANK_V1_BINDING_UNAVAILABLE'], ['RANK_V1_IDLE_UNBOUND'])):
                raise ValueError('RANK_V1_DIAGNOSTIC_INVALID')
            gaps.extend(payload['gaps'])
            continue
        _fact(payload, context, manifest, profiles)
        if payload['call_hmac_sha256'] in calls:
            raise ValueError('RANK_V1_CALL_REPLAY')
        calls.add(payload['call_hmac_sha256'])
        rows.append({'payload': payload, 'context': context, 'ordinal': snapshot['ordinal'],
                     'source_ref': {'file': path.name, 'sha256': sha256(raw).hexdigest()}})
    rows.sort(key=lambda r: (r['context']['pid'], r['context']['start_ticks'], r['ordinal']))
    receives, responses = {}, {}
    for row in rows:
        payload = row['payload']
        if payload['event'] == 'worker-rpc-received':
            receives[payload['call_hmac_sha256']] = row
        else:
            parent = payload['parent_receive_call_hmac_sha256']
            if parent not in receives:
                gaps.append('RANK_V1_RECEIVE_UNOBSERVED'); continue
            if parent in responses:
                raise ValueError('RANK_V1_RESPONSE_REPLAY')
            received = receives[parent]['payload']
            if (any(payload[k] != received[k] for k in ('context', 'worker_rank', 'owner_object_hmac_sha256',
                    'worker_object_hmac_sha256', 'request_hmac_sha256', 'request_binding_hmac_sha256'))
                    or received['observation_ns'] > payload['operation_started_ns']):
                raise ValueError('RANK_V1_RESPONSE_SCOPE_INVALID')
            responses[parent] = row
    ordinals = {}
    for pid, ticks, ordinal in seen:
        ordinals.setdefault((pid, ticks), set()).add(ordinal)
    if any(values != set(range(1, max(values) + 1)) for values in ordinals.values()):
        gaps.append('RANK_V1_SNAPSHOT_SEQUENCE_GAP')
    fresh_raw = _read(fresh_recording, lifecycle_capture.sensor.MAX_BYTES)
    fresh_rows, summary = lifecycle_capture.parse_recording(fresh_raw, fresh)
    clock = fresh_rows[0]['payload']['context']
    journal = summary['fresh_request_binding']
    if not summary['recording_closed'] or journal['missing']:
        gaps.append('RANK_V1_FRESH_SCOPE_INCOMPLETE')
    spec = catalog.row(fresh['profile'])['request_binding']
    inputs, ended = {}, {}
    for row in fresh_rows:
        payload = row['payload']
        sample = payload.get('metadata', {}).get('request_binding') if row['event'] == 'NATIVE' else None
        if sample is not None and payload['point'] == spec['input_point'] and payload['phase'] == 'RETURN':
            inputs.setdefault(sample['native_request_sha256'], []).append((row, sample))
        if row['event'] == 'ASGI' and payload['phase'] in {'APP_RETURN', 'APP_RAISE'}:
            ended.setdefault(payload['asgi_invocation_sha256'], []).append(row['monotonic_ns'])
    links = []
    for call, received in receives.items():
        payload = received['payload']
        for native_id in payload['request_binding_hmac_sha256']:
            witnesses = inputs.get(native_id, [])
            if len(witnesses) != 1 or not summary['recording_closed'] or journal['missing']:
                gaps.append('RANK_V1_FRESH_INPUT_UNVERIFIED'); continue
            native, sample = witnesses[0]; ends = ended.get(sample['asgi_invocation_sha256'], [])
            if (len(ends) != 1 or not native['monotonic_ns'] <= payload['observation_ns'] <= ends[0]
                    or any(clock[k] != payload['context'][k] for k in ('boot_sha256', 'time_namespace', 'time_offsets_sha256'))):
                gaps.append('RANK_V1_FRESH_CLOCK_OR_LIFETIME_UNVERIFIED'); continue
            links.append({'request_binding_hmac_sha256': native_id, 'asgi_invocation_sha256': sample['asgi_invocation_sha256'],
                          'native_call_sha256': sample['native_call_sha256'], 'worker_rank': payload['worker_rank'],
                          'receive_ref': received['source_ref'], 'response_ref': responses[call]['source_ref'] if call in responses else None})
    if set(receives) - set(responses): gaps.append('RANK_V1_RESPONSE_UNOBSERVED')
    if not receives: gaps.append('RANK_V1_RECEIVE_UNOBSERVED')
    return {'schema': 'guard-rank-v1-worker-binding-review/2', 'session': manifest['session'], 'rows': rows,
            'fresh_recording_sha256': sha256(fresh_raw).hexdigest(), 'fresh_prepared_sha256': manifest['fresh_prepared_sha256'],
            'source_manifest_sha256': manifest['source_manifest_sha256'],
            'worker_receives': len(receives), 'worker_responses': len(responses), 'fresh_worker_links': links,
            'gaps': sorted(set(gaps)), 'observer_installed': False, **dict.fromkeys(FALSE_FIELDS, False)}
