"""M-A3 IPC 관측 후보의 비밀 준비 자산과 원 snapshot 무결성을 검증한다.

엔진 기동·클라우드·장애 주입·공통 생산자 등록은 수행하지 않는다.
"""
import argparse
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat

from guard.adapters import vllm_rank_ipc_runtime as runtime
from tools.verify_req10_consumption import verify as verify_source

ROOT = Path(__file__).resolve().parents[1]

BOOTSTRAP = '''"""기존 sitecustomize를 먼저 실행한 뒤 IPC 후보만 설치한다."""
def _byul_rank_ipc_start():
    import importlib.machinery
    import importlib.util
    from pathlib import Path
    import sys
    current = sys.modules[__name__]
    current.__dict__.pop('_byul_rank_ipc_start', None)
    own = Path(__file__).resolve().parent
    search = []
    for item in sys.path:
        if type(item) is not str:
            search.append(item)
            continue
        try:
            same = Path(item or '.').resolve() == own
        except OSError:
            same = False
        if not same:
            search.append(item)
    sys.path[:] = search
    original = importlib.machinery.PathFinder.find_spec('sitecustomize', search)
    if original is not None and original.loader is not None:
        current.__file__, current.__loader__, current.__spec__ = original.origin, original.loader, original
        current.__package__, current.__cached__ = original.parent, original.cached
        original.loader.exec_module(current)
    try:
        name = '_byul_rank_ipc'
        if name in sys.modules:
            raise ValueError('RANK_IPC_NAMESPACE_IN_USE')
        spec = importlib.util.spec_from_file_location(name, own / 'guard' / '__init__.py',
                                                     submodule_search_locations=[str(own / 'guard')])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
        module = importlib.import_module(name + '.adapters.vllm_rank_ipc_runtime')
        module.install()
    except Exception:
        sys.stderr.write('RANK_IPC_OBSERVER_UNAVAILABLE\\n')
_byul_rank_ipc_start()
'''


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


def prepare(output, *, image):
    """현재 검토 소스를 확인한 뒤 새 디렉터리에만 만든다. 키는 반환/출력하지 않는다."""
    source = verify_source(ROOT.parent)
    profiles = json.loads((ROOT / 'src/guard/adapters/assets/vllm_rank_ipc_profiles.json').read_bytes())
    if len([p for p in profiles['profiles'] if p['image'] == image]) != 1:
        raise ValueError('RANK_IPC_IMAGE_UNSUPPORTED')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('RANK_IPC_OUTPUT_EXISTS')
    files = {}
    for relative in runtime.ASSETS:
        files[relative] = BOOTSTRAP.encode() if relative == 'sitecustomize.py' else (ROOT / 'src' / relative).read_bytes()
    assets = {name: {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()} for name, raw in sorted(files.items())}
    session = secrets.token_hex(16)
    settings = {'schema': 'guard-rank-ipc-settings/1', 'session': session, 'image': image,
                'key': secrets.token_hex(32), 'output_dir': '/guard-rank-ipc/data/' + session, 'assets': assets}
    raw_settings = canonical(settings)
    manifest = {'schema': 'guard-rank-ipc-prepared-candidate/1', 'session': session, 'image': image,
                'settings_sha256': sha256(raw_settings).hexdigest(), 'assets': assets,
                'source_manifest_sha256': source['reviewed_common_commit'],
                'observer_installed': False, 'engine_group_verified': False, 'producer_registered': False}
    files.update({'settings.json': raw_settings, 'manifest.json': canonical(manifest)})
    output.mkdir(mode=0o700)
    for name, raw in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o600)
    data = output / 'data' / session
    data.mkdir(parents=True, mode=0o700)
    return manifest


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
            if (payload.get('event') not in {'CALL', 'RETURN', 'RAISE'}
                    or not fingerprint(payload.get('call_hmac_sha256'))
                    or type(ids) is not list or len(ids) > 8 or any(not fingerprint(v) for v in ids)
                    or len(set(ids)) != len(ids)
                    or any(type(payload.get(k)) is not bool for k in
                           ('rpc_parent_frame_verified', 'rpc_parent_worker_verified'))
                    or payload['rpc_parent_frame_verified'] != payload['rpc_parent_worker_verified']):
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            parent = payload.get('rpc_parent_call_hmac_sha256')
            if payload['rpc_parent_frame_verified']:
                if (not hook.get('parent') or not ids or not fingerprint(parent)
                        or parent == payload['call_hmac_sha256']):
                    raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            elif parent is not None:
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if hook.get('parent') and not payload['rpc_parent_frame_verified']:
                raise ValueError('RANK_RPC_SOURCE_RECORD_INVALID')
            if hook['kind'] == 'rpc' and not ids:
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
        rows.append(payload)
    return {'schema': 'guard-rank-ipc-capture-review/1', 'session': manifest['session'],
            'image': manifest['image'], 'snapshots': len(paths), 'source_rows_verified': len(rows),
            'rows': rows, 'gaps': gaps, 'source_authenticated': False,
            'engine_group_verified': False, 'producer_registered': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare'); p.add_argument('--image', required=True); p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('verify'); p.add_argument('--prepared', type=Path, required=True); p.add_argument('--capture', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output, image=args.image)
    else:
        result = verify_capture(args.prepared, args.capture)
        result.pop('rows')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
