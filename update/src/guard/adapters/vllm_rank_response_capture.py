"""Private preparation and closed original trace records; no qualifications."""
from collections import defaultdict
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat

from . import vllm_rank_ipc_runtime as shared
from . import vllm_rank_response_runtime as runtime
from . import vllm_rank_response_trace as tracing
from .vllm_rank_response_bootstrap import BOOTSTRAP

FLAGS = {'observer_installed': False, 'producer_registered': False, 'fault_causality_verified': False,
         'upper_delivery_verified': False, 'recovery_verified': False, 'whole_resources_released': False}
CONTEXT = {'pid', 'start_ticks', 'uid', 'boot_sha256', 'pid_namespace', 'time_namespace', 'time_offsets_sha256'}
ROW = {'schema', 'profile', 'sequence', 'process', 'thread_hmac_sha256', 'operation_hmac_sha256', 'binding',
       'frame_call_hmac_sha256', 'parent_frame_call_hmac_sha256',
       'point', 'source_module', 'source_sha256', 'body_code_sha256', 'event', 'instruction_offset', 'line',
       'started_ns', 'observed_ns', 'reader_endpoint', 'reader_timeout_ns', 'exception', *set(FLAGS) - {'observer_installed'}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def document(raw):
    def invalid(_):
        raise ValueError('RANK_RESPONSE_JSON_NUMBER_INVALID')
    if type(raw) is not bytes or not 0 < len(raw) <= 131072:
        raise ValueError('RANK_RESPONSE_DOCUMENT_BOUND')
    quoted = escaped = False
    depth = 0
    for byte in raw:
        if quoted:
            if escaped: escaped = False
            elif byte == 92: escaped = True
            elif byte == 34: quoted = False
        elif byte == 34: quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 64: raise ValueError('RANK_RESPONSE_DOCUMENT_DEPTH')
        elif byte in (93, 125): depth -= 1
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=shared._pairs,
                          parse_constant=invalid, parse_float=invalid)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('RANK_RESPONSE_DOCUMENT_INVALID') from error


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _context(value):
    return (type(value) is dict and set(value) == CONTEXT
            and all(type(value[k]) is int and 0 <= value[k] < 2**63 for k in CONTEXT - {'boot_sha256', 'time_offsets_sha256'})
            and value['pid'] > 0 and value['start_ticks'] > 0
            and all(_sha(value[k]) for k in ('boot_sha256', 'time_offsets_sha256')))


def _files():
    root = Path(runtime.__file__).parents[2]
    return {name: BOOTSTRAP if name == 'sitecustomize.py' else (root / name).read_bytes() for name in runtime.ASSETS}


def prepare(output, *, rank_prepared, repository):
    """Add a separate extension; never edit the rank SDK or emit its key."""
    from tools.verify_req10_consumption import verify
    from tools.rank_ipc_candidates import BOOTSTRAP as rank_bootstrap
    from guard.update_validation import reserve_output
    reviewed = verify(Path(repository))
    rank_prepared = Path(rank_prepared)
    owner = (rank_prepared / 'settings.json').lstat()
    if owner.st_uid != os.geteuid() or stat.S_IMODE(owner.st_mode) not in {0o400, 0o600}:
        raise ValueError('RANK_RESPONSE_ORIGINAL_SETTINGS_PRIVATE')
    raw = runtime.read(rank_prepared / 'settings.json', 32768)
    rank = document(raw)
    manifest = document(runtime.read(rank_prepared / 'manifest.json', 32768))
    if (type(rank) is not dict or rank.get('schema') != 'guard-rank-ipc-settings/1'
            or type(rank.get('assets')) is not dict or set(rank['assets']) != shared.ASSETS
            or manifest.get('schema') != 'guard-rank-ipc-prepared-candidate/1'
            or manifest.get('settings_sha256') != sha256(raw).hexdigest()
            or manifest.get('assets') != rank['assets']
            or any(manifest.get(k) is not False for k in ('observer_installed', 'producer_registered', 'engine_group_verified'))
            or any(manifest.get(k) != rank[k] for k in ('image', 'session'))):
        raise ValueError('RANK_RESPONSE_ORIGINAL_PREPARATION_INVALID')
    root = Path(shared.__file__).parents[2]
    for name, pin in rank['assets'].items():
        value = shared._asset(rank_prepared / name, pin)
        expected = rank_bootstrap.encode() if name == 'sitecustomize.py' else (root / name).read_bytes()
        if value != expected:
            raise ValueError('RANK_RESPONSE_ORIGINAL_ASSETS_CHANGED')
    tracing.profile(rank['image'])
    files = _files()
    assets = {name: {'bytes': len(value), 'sha256': sha256(value).hexdigest()} for name, value in sorted(files.items())}
    cfg = {'schema': 'guard-rank-response-trace-settings/1', 'session': rank['session'], 'image': rank['image'],
           'key': rank['key'], 'output_dir': str(runtime.ROOT / 'data' / rank['session']), 'assets': assets,
           'rank_settings_sha256': sha256(raw).hexdigest()}
    cfg_raw = canonical(cfg)
    result = {'schema': 'guard-rank-response-trace-prepared/1', 'session': cfg['session'], 'image': cfg['image'],
              'settings_sha256': sha256(cfg_raw).hexdigest(), 'assets': assets,
              'rank_settings_sha256': cfg['rank_settings_sha256'],
              'source_manifest_sha256': reviewed['reviewed_common_commit'], **FLAGS}
    files.update({'settings.json': cfg_raw, 'manifest.json': canonical(result)})
    output = reserve_output(Path(output).absolute(), protected=[Path(repository), rank_prepared])
    for name, value in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
    (output / 'data' / cfg['session']).mkdir(parents=True, mode=0o700)
    return result


def load_prepared(prepared, *, expected_uid=None):
    prepared = Path(prepared)
    cfg, digest = runtime.settings(prepared, expected_uid=expected_uid)
    manifest = document(runtime.read(prepared / 'manifest.json', 32768))
    if (type(manifest) is not dict or set(manifest) != {'schema', 'session', 'image', 'settings_sha256', 'assets',
            'rank_settings_sha256', 'source_manifest_sha256', *FLAGS}
            or manifest['schema'] != 'guard-rank-response-trace-prepared/1'
            or not _sha(manifest['source_manifest_sha256'])
            or manifest['settings_sha256'] != digest or manifest['assets'] != cfg['assets']
            or any(manifest[k] != cfg[k] for k in ('session', 'image', 'rank_settings_sha256'))
            or any(manifest[k] is not v for k, v in FLAGS.items())):
        raise ValueError('RANK_RESPONSE_PREPARED_INVALID')
    for name, expected in _files().items():
        if shared._asset(prepared / name, cfg['assets'][name]) != expected:
            raise ValueError('RANK_RESPONSE_PREPARED_SOURCE_CHANGED')
    return manifest, cfg, tracing.profile(cfg['image'])


def _row(value, profile, context):
    if (type(value) is not dict or set(value) != ROW or value['schema'] != tracing.SCHEMA
            or value['profile'] != profile['id'] or value['process'] != context
            or value['event'] not in {'CALL', 'EXCEPTION', 'RETURN', 'UNWIND'}
            or any(value[k] is not False for k in set(FLAGS) - {'observer_installed'})
            or any(not _sha(value[k]) for k in ('thread_hmac_sha256', 'operation_hmac_sha256', 'frame_call_hmac_sha256', 'source_sha256', 'body_code_sha256'))
            or value['parent_frame_call_hmac_sha256'] is not None and not _sha(value['parent_frame_call_hmac_sha256'])
            or any(type(value[k]) is not int or not 0 <= value[k] < 2**63 for k in ('sequence', 'line', 'instruction_offset', 'started_ns', 'observed_ns'))
            or not 1 <= value['sequence'] <= tracing.MAX_ROWS or value['started_ns'] > value['observed_ns']):
        raise ValueError('RANK_RESPONSE_ROW_INVALID')
    source = profile['sources'][value['source_module']]
    point = source['points'][value['point']]
    if (value['source_sha256'] != source['sha256'] or value['body_code_sha256'] != source['body_code_sha256'][value['point']]
            or source['instruction_lines'][value['point']].get(str(value['instruction_offset'])) != value['line']
            or value['event'] == 'RETURN' and value['instruction_offset'] not in source['return_offsets'][value['point']]
            or value['event'] == 'UNWIND' and value['instruction_offset'] in source['return_offsets'][value['point']]):
        raise ValueError('RANK_RESPONSE_ROW_SOURCE_INVALID')
    binding = value['binding']
    if (type(binding) is not dict or set(binding) != {'context', 'request_hmac_sha256', 'deferred_rpc', 'deferred_call_hmac_sha256'}
            or binding['context'] != context or not _sha(binding['deferred_call_hmac_sha256'])
            or type(binding['request_hmac_sha256']) is not list or len(binding['request_hmac_sha256']) != 1
            or not _sha(binding['request_hmac_sha256'][0])):
        raise ValueError('RANK_RESPONSE_ROW_BINDING_INVALID')
    deferred = binding['deferred_rpc']
    if (type(deferred) is not dict or set(deferred) != {'schema', 'source_module', 'point', 'origin_call_hmac_sha256',
            'origin_object_hmac_sha256', 'origin_started_ns', 'origin_returned_ns', 'future_object_hmac_sha256'}
            or deferred['schema'] != 'guard-rank-deferred-request-binding/1'
            or any(not _sha(deferred[k]) for k in ('origin_call_hmac_sha256', 'origin_object_hmac_sha256', 'future_object_hmac_sha256'))
            or any(type(deferred[k]) is not int or not 0 <= deferred[k] <= value['started_ns'] for k in ('origin_started_ns', 'origin_returned_ns'))
            or deferred['origin_started_ns'] > deferred['origin_returned_ns']):
        raise ValueError('RANK_RESPONSE_DEFERRED_INVALID')
    original, = [row for row in json.loads(shared.sources.PROFILES.read_bytes())['profiles'] if row['image'] == profile['image']]
    origin = original['rpc_sources'][deferred['source_module']]['hooks'][deferred['point']]
    target = origin.get('deferred_result', {}).get('point')
    if (origin.get('kind') != 'rpc' or target is None
            or profile['sources'][deferred['source_module']]['points'].get(target, {}).get('kind') != 'root'):
        raise ValueError('RANK_RESPONSE_DEFERRED_INVALID')
    error = value['exception']
    if ((value['event'] in {'CALL', 'RETURN'}) != (error is None)
            or error is not None and (type(error) is not dict or set(error) != {'object_hmac_sha256', 'cause_hmac_sha256', 'kind'}
                or not _sha(error['object_hmac_sha256']) or error['cause_hmac_sha256'] is not None and not _sha(error['cause_hmac_sha256'])
                or error['kind'] not in {'timeout', 'runtime-error', 'other'})):
        raise ValueError('RANK_RESPONSE_EXCEPTION_INVALID')
    endpoint = value['reader_endpoint']
    timeout = value['reader_timeout_ns']
    if timeout is not None and (point['kind'] != 'reader' or type(timeout) is not int or not 0 <= timeout <= (2**31) * 1_000_000_000):
        raise ValueError('RANK_RESPONSE_TIMEOUT_INVALID')
    if (point['kind'] == 'reader') != (endpoint is not None):
        raise ValueError('RANK_RESPONSE_READER_INVALID')
    if endpoint is not None and (type(endpoint) is not dict or set(endpoint) != {'schema', 'kind', 'identity_hmac_sha256',
            'observation_started_ns', 'observation_finished_ns', 'source_authenticated', 'engine_group_verified'}
            or endpoint['schema'] != tracing.identity.SCHEMA or endpoint['kind'] != 'shared-memory'
            or not _sha(endpoint['identity_hmac_sha256']) or endpoint['source_authenticated'] is not False
            or endpoint['engine_group_verified'] is not False
            or any(type(endpoint[k]) is not int or not 0 <= endpoint[k] <= value['observed_ns'] for k in ('observation_started_ns', 'observation_finished_ns'))
            or endpoint['observation_started_ns'] > endpoint['observation_finished_ns']):
        raise ValueError('RANK_RESPONSE_READER_INVALID')


def response_graphs(rows, profile):
    """Pair source calls; never infer the injected fault or upper HTTP delivery."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['process']['pid'], row['process']['start_ticks'], row['operation_hmac_sha256'])].append(row)
    result = []
    for key, values in grouped.items():
        values = sorted(values, key=lambda row: row['sequence'])
        binding, context, thread = values[0]['binding'], values[0]['process'], values[0]['thread_hmac_sha256']
        calls = defaultdict(list)
        for row in values:
            if row['binding'] != binding or row['process'] != context or row['thread_hmac_sha256'] != thread:
                raise ValueError('RANK_RESPONSE_OPERATION_MISMATCH')
            calls[row['frame_call_hmac_sha256']].append(row)
        terminal = {}
        kinds = defaultdict(list)
        gaps = []
        for call, records in calls.items():
            first, last = records[0], records[-1]
            fields = ('point', 'source_module', 'started_ns', 'reader_endpoint', 'reader_timeout_ns', 'parent_frame_call_hmac_sha256')
            if (first['event'] != 'CALL' or any(any(row[k] != first[k] for k in fields) for row in records)
                    or any(a['observed_ns'] > b['observed_ns'] for a, b in zip(records, records[1:]))
                    or any(row['event'] == 'CALL' for row in records[1:])
                    or any(row['event'] in {'RETURN', 'UNWIND'} for row in records[:-1])):
                raise ValueError('RANK_RESPONSE_FRAME_PAIR_INVALID')
            if last['event'] not in {'RETURN', 'UNWIND'}:
                gaps.append('RANK_RESPONSE_FRAME_NOT_CLOSED')
                continue
            if last['event'] == 'UNWIND':
                errors = [r['exception'] for r in records if r['event'] == 'EXCEPTION']
                if not errors or last['exception'] != errors[-1]:
                    raise ValueError('RANK_RESPONSE_UNWIND_PAIR_INVALID')
            terminal[call] = (first, last)
            spec = profile['sources'][first['source_module']]['points'][first['point']]
            kinds[spec['kind']].append((first, last))
        for call, (first, last) in terminal.items():
            spec = profile['sources'][first['source_module']]['points'][first['point']]
            parent = first['parent_frame_call_hmac_sha256']
            if spec['kind'] == 'root':
                if parent is not None:
                    raise ValueError('RANK_RESPONSE_PARENT_INVALID')
            elif parent not in terminal:
                gaps.append('RANK_RESPONSE_PARENT_NOT_CLOSED')
            else:
                before, after = terminal[parent]
                parent_spec = profile['sources'][before['source_module']]['points'][before['point']]
                if (parent_spec['kind'] != spec['parent_kind']
                        or not before['sequence'] < first['sequence'] < last['sequence'] < after['sequence']
                        or not before['observed_ns'] <= first['observed_ns'] <= last['observed_ns'] <= after['observed_ns']):
                    raise ValueError('RANK_RESPONSE_PARENT_INVALID')
        complete = not gaps and set(kinds) == {'root', 'response-wait', 'native-response', 'reader'} and all(len(v) == 1 for v in kinds.values())
        if not complete:
            gaps.append('RANK_RESPONSE_FOUR_POINT_GRAPH_MISSING_OR_AMBIGUOUS')
        same_error = direct_cause = False
        if complete:
            native, root, reader = (kinds[k][0][1] for k in ('native-response', 'root', 'reader'))
            same_error = (native['event'] == root['event'] == 'UNWIND'
                          and native['exception']['object_hmac_sha256'] == root['exception']['object_hmac_sha256'])
            direct_cause = (same_error and reader['event'] == 'UNWIND'
                            and native['exception']['cause_hmac_sha256'] == reader['exception']['object_hmac_sha256'])
        result.append({'operation_hmac_sha256': key[2], 'process': context, 'binding': binding,
            'source_four_point_graph_complete': complete, 'same_native_error_reached_future': same_error,
            'reader_error_is_direct_native_cause': direct_cause, 'gaps': sorted(set(gaps)), **FLAGS})
    return result


def verify_capture(prepared, capture, *, expected_uid=None):
    manifest, cfg, profile = load_prepared(prepared, expected_uid=expected_uid)
    paths = list(Path(capture).glob('pid-*.json'))
    if not 1 <= len(paths) <= 4096:
        raise ValueError('RANK_RESPONSE_CAPTURE_BOUND')
    records, rows, gaps, seen = [], [], [], set()
    sequences = defaultdict(list)
    ordinals = defaultdict(list)
    for path in paths:
        raw = runtime.read(path, 131072)
        record = document(raw)
        if type(record) is not dict or set(record) != {'snapshot', 'hmac_sha256'} or not _sha(record['hmac_sha256']):
            raise ValueError('RANK_RESPONSE_SNAPSHOT_INVALID')
        value = record['snapshot']
        if (type(value) is not dict or set(value) != {'schema', 'session', 'image', 'settings_sha256', 'observer_assets', 'context', 'ordinal', 'payload'}
                or not hmac.compare_digest(record['hmac_sha256'], hmac.new(bytes.fromhex(cfg['key']), canonical(value), sha256).hexdigest())
                or value['schema'] != runtime.SnapshotWriter.schema or value['session'] != cfg['session'] or value['image'] != cfg['image']
                or value['settings_sha256'] != manifest['settings_sha256'] or value['observer_assets'] != cfg['assets']
                or not _context(value['context']) or type(value['ordinal']) is not int or not 1 <= value['ordinal'] <= 256):
            raise ValueError('RANK_RESPONSE_SNAPSHOT_INVALID')
        context = value['context']
        token = (context['pid'], context['start_ticks'], value['ordinal'])
        if token in seen or path.name != 'pid-{}-{}-{}.json'.format(*token):
            raise ValueError('RANK_RESPONSE_SNAPSHOT_REPLAY')
        seen.add(token)
        ordinals[token[:2]].append(value['ordinal'])
        payload = value['payload']
        if type(payload) is dict and payload.get('schema') == 'guard-rank-response-trace-gap/1':
            if (set(payload) != {'schema', 'diagnostic', *set(FLAGS) - {'observer_installed'}}
                    or payload['diagnostic'] != 'RANK_RESPONSE_OBSERVATION_INCOMPLETE'
                    or any(payload[k] is not False for k in set(FLAGS) - {'observer_installed'})):
                raise ValueError('RANK_RESPONSE_GAP_INVALID')
            gaps.append(payload['diagnostic'])
        else:
            _row(payload, profile, context)
            rows.append(payload)
            sequences[token[:2]].append((value['ordinal'], payload['sequence']))
        records.append({'name': path.name, 'bytes': len(raw), 'sha256': sha256(raw).hexdigest()})
    for values in sequences.values():
        ordered = sorted(values)
        if [b for a, b in ordered] != list(range(1, len(ordered) + 1)):
            raise ValueError('RANK_RESPONSE_SEQUENCE_INCOMPLETE')
    if any(sorted(values) != list(range(1, len(values) + 1)) for values in ordinals.values()):
        raise ValueError('RANK_RESPONSE_ORDINAL_INCOMPLETE')
    return {'schema': 'guard-rank-response-capture-review/1', 'session': cfg['session'], 'image': cfg['image'],
            'settings_sha256': manifest['settings_sha256'], 'source_manifest_sha256': manifest['source_manifest_sha256'],
            'records': records, 'rows': sorted(rows, key=lambda r: (r['process']['pid'], r['sequence'])),
            'gaps': sorted(set(gaps)), 'operations': response_graphs(rows, profile), **FLAGS}
