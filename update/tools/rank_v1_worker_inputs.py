"""승인된 /2의 파일 준비/검토만 수행한다. 엔진/GPU·장애 제어는 실행하지 않는다."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.adapters import vllm_rank_v1_worker_runtime as runtime
from guard.adapters.vllm_rank_ipc_capture import canonical, _read
from guard.adapters.vllm_rank_v1_worker_capture import fresh_assets
from tools.verify_req10_consumption import verify as verify_source

ROOT = Path(__file__).resolve().parents[1]


def prepare(output, *, image, fresh_prepared):
    source = verify_source(ROOT.parent)
    fresh, fresh_sha = fresh_assets(fresh_prepared)
    if image not in catalog.row(fresh['profile'])['images']:
        raise ValueError('RANK_V1_FRESH_IMAGE_INVALID')
    session, pin = fresh['session'], fresh['assets']['request-binding-key.json']['sha256']
    key = runtime.private_key(Path(fresh_prepared) / 'request-binding-key.json', expected_sha256=pin, session=session)
    files = runtime.asset_bytes(ROOT)
    assets = {name: {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()} for name, raw in files.items()}
    value = runtime.validate_settings({'schema': runtime.SCHEMA, 'session': session, 'image': image, 'key': key.hex(),
        'output_dir': '/guard-rank-ipc/data/' + session, 'assets': assets, 'fresh_profile': fresh['profile'],
        'fresh_prepared_sha256': fresh_sha, 'binding_key_asset_sha256': pin,
        'binding_key_path': '/guard-lifecycle/request-binding-key.json'})
    settings = canonical(value)
    manifest = {'schema': 'guard-rank-ipc-prepared-candidate/2', 'session': session, 'image': image,
        'settings_sha256': sha256(settings).hexdigest(), 'assets': assets,
        'source_manifest_sha256': source['reviewed_common_commit'], 'fresh_profile': fresh['profile'],
        'fresh_prepared_sha256': fresh_sha, 'binding_key_asset_sha256': pin,
        'observer_installed': False, 'source_authenticated': False, 'scope_complete': False,
        'engine_group_verified': False, 'producer_registered': False}
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('RANK_V1_OUTPUT_EXISTS')
    output.mkdir(mode=0o700)
    files.update({'settings.json': settings, 'manifest.json': canonical(manifest)})
    for name, raw in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    (output / 'data' / session).mkdir(parents=True, mode=0o700)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--image', required=True)
    p.add_argument('--fresh-prepared', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('verify')
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--fresh-prepared', type=Path, required=True)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--fresh-recording', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output, image=args.image, fresh_prepared=args.fresh_prepared)
    else:
        from guard.adapters.vllm_rank_v1_worker_capture import verify_capture
        source = verify_source(ROOT.parent)
        result = verify_capture(args.prepared, args.capture, fresh_prepared=args.fresh_prepared,
                                fresh_recording=args.fresh_recording,
                                expected_source_manifest_sha256=source['reviewed_common_commit'])
        result.pop('rows', None)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
