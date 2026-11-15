"""M-A3 IPC 관측 후보의 비밀 준비 자산과 원 snapshot 무결성을 검증한다.

엔진 기동·클라우드·장애 주입·공통 생산자 등록은 수행하지 않는다.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import secrets

from guard.adapters import vllm_rank_ipc_runtime as runtime
from guard.adapters.vllm_rank_ipc_capture import canonical, _read, verify_capture
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
