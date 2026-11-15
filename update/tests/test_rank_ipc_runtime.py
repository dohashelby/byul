"""비밀 준비 자산·독립 Python 프로세스 설치·원 snapshot 변조 거부를 확인한다."""
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from guard.adapters import vllm_rank_ipc_runtime as runtime
from guard.adapters import vllm_rank_ipc_sources as sources
from tools import rank_ipc_candidates as tool
from tools.mvp_versions import versions

RAW = b'''calls = []
class Worker:
    def bind(self, value):
        calls.append(value)
        return value + 1
'''


def _prepared(tmp_path):
    root = tmp_path / 'prepared'
    manifest = tool.prepare(root, image=versions()['candidate']['image'])
    settings = json.loads((root / 'settings.json').read_bytes())
    assert settings['key'] not in json.dumps(manifest)
    assert (root / 'settings.json').stat().st_mode & 0o777 == 0o600
    assert all(manifest[k] is False for k in ('observer_installed', 'engine_group_verified', 'producer_registered'))
    (root / 'fixture.py').write_bytes(RAW)
    codes = sources._codes(compile(RAW, 'fixture.py', 'exec', dont_inherit=True, optimize=0))
    source = {'path': 'fixture.py', 'bytes': len(RAW), 'sha256': sha256(RAW).hexdigest(),
        'functions': ['Worker.bind'], 'body_code_sha256': {'Worker.bind': sources.code_digest(codes['Worker.bind'])},
        'hooks': {'Worker.bind': {'phase': 'after-original', 'role': 'EXAMPLE',
            'scalars': {'rank': {'root': 'self', 'path': ['rank']}},
            'endpoints': {'task': {'root': 'self', 'path': ['connection'], 'kind': 'pipe'}}}}}
    profiles = {'schema': 'guard-rank-ipc-source-candidates/1', 'profiles': [{'id': 'EXAMPLE',
        'image': 'fixture-image', 'runtime': sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3])),
        'sources': {'fixture': source}}]}
    relative = 'guard/adapters/assets/vllm_rank_ipc_profiles.json'
    raw = tool.canonical(profiles)
    (root / relative).write_bytes(raw)
    settings['assets'][relative] = {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
    settings['image'] = manifest['image'] = 'fixture-image'
    settings['output_dir'] = str(root / 'data' / manifest['session'])
    raw = tool.canonical(settings)
    (root / 'settings.json').write_bytes(raw)
    manifest['settings_sha256'] = sha256(raw).hexdigest()
    manifest['assets'] = settings['assets']
    manifest['source_manifest_sha256'] = sha256(b'EXAMPLE fixture').hexdigest()
    (root / 'manifest.json').write_bytes(tool.canonical(manifest))
    return root, settings, manifest


def _run(root):
    code = '''import sys,importlib.util,importlib
from pathlib import Path
sys.path.insert(0, sys.argv[1])
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('_byul_rank_ipc', root/'guard/__init__.py',
    submodule_search_locations=[str(root/'guard')])
package = importlib.util.module_from_spec(spec)
sys.modules['_byul_rank_ipc'] = package
spec.loader.exec_module(package)
runtime = importlib.import_module('_byul_rank_ipc.adapters.vllm_rank_ipc_runtime')
runtime.ROOT = Path(sys.argv[1])
assert runtime.install()
import fixture
from multiprocessing.connection import Pipe
reader, writer = Pipe(duplex=False)
try:
    worker = fixture.Worker()
    worker.rank, worker.connection = 1, reader
    writer.send_bytes(b'untouched')
    assert worker.bind(4) == 5 and fixture.calls == [4]
    assert reader.recv_bytes() == b'untouched'
finally:
    reader.close()
    writer.close()
print('EXAMPLE 완료')
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(root)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'EXAMPLE 완료'


def test_isolated_installation_preserves_original_and_seals_snapshot(tmp_path):
    root, settings, _ = _prepared(tmp_path)
    _run(root)
    result = tool.verify_capture(root, root / 'data' / settings['session'])
    assert result['snapshots'] == result['source_rows_verified'] == 1 and result['gaps'] == []
    assert result['source_authenticated'] is result['engine_group_verified'] is result['producer_registered'] is False
    assert result['rows'][0]['scalars'] == {'rank': 1}
    assert settings['key'] not in json.dumps(result)


@pytest.mark.parametrize('change', ['hmac', 'body_pin', 'asset'])
def test_tampered_snapshot_or_prepared_asset_is_rejected(tmp_path, change):
    root, settings, _ = _prepared(tmp_path)
    _run(root)
    capture = root / 'data' / settings['session']
    path, = capture.glob('pid-*.json')
    record = json.loads(path.read_bytes())
    if change == 'asset':
        asset = root / 'guard/adapters/vllm_rank_ipc_identity.py'
        asset.write_bytes(asset.read_bytes() + b'\n')
    else:
        record['snapshot']['payload']['body_code_sha256'] = '0' * 64
        if change == 'body_pin':
            record['hmac_sha256'] = hmac.new(bytes.fromhex(settings['key']), tool.canonical(record['snapshot']), sha256).hexdigest()
        path.write_bytes(tool.canonical(record))
    with pytest.raises(ValueError):
        tool.verify_capture(root, capture)


def test_private_writer_never_overwrites_an_existing_snapshot(tmp_path):
    root, settings, manifest = _prepared(tmp_path)
    payload = {'gaps': ['EXAMPLE'], 'engine_group_verified': False, 'producer_registered': False}
    runtime.SnapshotWriter(settings, manifest['settings_sha256'])(payload)
    capture = root / 'data' / settings['session']
    path, = capture.glob('pid-*.json')
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        runtime.SnapshotWriter(settings, manifest['settings_sha256'])(payload)
    assert path.read_bytes() == original


def test_settings_permissions_and_duplicate_keys_are_rejected(tmp_path, monkeypatch):
    root, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(runtime, 'ROOT', root)
    path = root / 'settings.json'
    path.chmod(0o644)
    with pytest.raises(ValueError, match='RANK_IPC_SETTINGS_UNAVAILABLE'):
        runtime._settings()
    path.chmod(0o600)
    path.write_text('{"schema":1,"schema":2}')
    with pytest.raises(ValueError, match='RANK_IPC_SETTINGS_DUPLICATE'):
        runtime._settings()


def test_bootstrap_keeps_existing_sitecustomize_first(tmp_path):
    root = tmp_path / 'prepared'
    tool.prepare(root, image=versions()['candidate']['image'])
    original = tmp_path / 'original'
    original.mkdir()
    (original / 'guard').mkdir()
    (original / 'guard/__init__.py').write_text("EXAMPLE_ORIGINAL_PACKAGE = True\n")
    (original / 'sitecustomize.py').write_text("import os,guard\nassert guard.EXAMPLE_ORIGINAL_PACKAGE\nos.environ['BYUL_EXAMPLE_ORIGINAL_CUSTOMIZE']='1'\n")
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(root) + os.pathsep + str(original)
    code = "import os,sitecustomize,sys,guard;assert os.environ['BYUL_EXAMPLE_ORIGINAL_CUSTOMIZE']=='1';assert sitecustomize.__file__==sys.argv[1];assert guard.EXAMPLE_ORIGINAL_PACKAGE;assert '_byul_rank_ipc' in sys.modules;print('원래 초기화 유지')"
    result = subprocess.run([sys.executable, '-c', code, str(original / 'sitecustomize.py')],
                            env=environment, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '원래 초기화 유지'


def test_prepare_refuses_existing_directory_and_unknown_image(tmp_path):
    root = tmp_path / 'prepared'
    tool.prepare(root, image=versions()['candidate']['image'])
    with pytest.raises(ValueError, match='RANK_IPC_OUTPUT_EXISTS'):
        tool.prepare(root, image=versions()['candidate']['image'])
    with pytest.raises(ValueError, match='RANK_IPC_IMAGE_UNSUPPORTED'):
        tool.prepare(tmp_path / 'other', image='fixture-image')
