"""독립 Python의 실제 원 프레임 연결·닫힌 RPC 기록과 과거 준비물 거부 경계를 확인한다."""
from hashlib import sha256
import hmac
import json
import subprocess
import sys

import pytest

from guard.adapters import vllm_rank_ipc_sources as sources
from tools import rank_ipc_candidates as tool
from tests.test_rank_ipc_runtime import _prepared
from tests.test_rank_rpc_hooks import RAW


def _rpc_prepared(tmp_path):
    root, settings, manifest = _prepared(tmp_path)
    (root / 'rank_rpc_fixture.py').write_bytes(RAW)
    profile_path = root / 'guard/adapters/assets/vllm_rank_ipc_profiles.json'
    profiles = json.loads(profile_path.read_bytes()); profile = profiles['profiles'][0]
    name = 'rank_rpc_fixture'; points = ['Outer.execute', 'Inner.execute', 'External.header', 'Assignment.assign']
    codes = sources._codes(compile(RAW, 'rank_rpc_fixture.py', 'exec'))
    common = {'kind': 'rpc', 'phase': 'around-original', 'role': 'EXAMPLE', 'source_modules': [name]}
    request = {'root': 'scheduler_output', 'layout': 'scheduled'}
    source = {'path': 'rank_rpc_fixture.py', 'bytes': len(RAW), 'sha256': sha256(RAW).hexdigest(),
        'functions': points, 'body_code_sha256': {p: sources.code_digest(codes[p]) for p in points},
        'hooks': {'Outer.execute': {**common, 'requests': request},
                  'Inner.execute': {**common, 'requests': request,
                      'parent': {'point': 'Outer.execute', 'worker_path': ['worker']}},
                  'External.header': {**common, 'kind': 'external-id'},
                  'Assignment.assign': {**common, 'kind': 'assignment', 'assignment': {'root': 'request'}}}}
    profile['rpc_sources'] = {name: source}
    profile['request_layouts'] = {'scheduled': {'kind': 'positive-dict-keys',
        'type': {'module': name, 'name': 'Scheduled'}, 'field': 'num_scheduled_tokens', 'item_types': []}}
    profile['assignment_type'] = {'module': name, 'name': 'Request'}
    raw = tool.canonical(profiles); profile_path.write_bytes(raw)
    settings['assets']['guard/adapters/assets/vllm_rank_ipc_profiles.json'] = {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
    raw = tool.canonical(settings); (root / 'settings.json').write_bytes(raw)
    manifest['assets'], manifest['settings_sha256'] = settings['assets'], sha256(raw).hexdigest()
    (root / 'manifest.json').write_bytes(tool.canonical(manifest))
    return root, settings


def _run_rpc(root):
    code = '''import sys,importlib.util,importlib
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location('_byul_rank_ipc',root/'guard/__init__.py',submodule_search_locations=[str(root/'guard')])
package=importlib.util.module_from_spec(spec);sys.modules['_byul_rank_ipc']=package;spec.loader.exec_module(package)
runtime=importlib.import_module('_byul_rank_ipc.adapters.vllm_rank_ipc_runtime');runtime.ROOT=root
assert runtime.install()
import rank_rpc_fixture as fixture
outer,inner,scheduler=fixture.Outer(),fixture.Inner(),fixture.Scheduled()
outer.worker,outer.replace_ids,inner.error,inner.result=inner,False,None,object()
scheduler.num_scheduled_tokens={'EXAMPLE-private-request':1}
assert outer.execute(scheduler)is inner.result
assert fixture.calls==[('outer',scheduler),('inner',scheduler)]
assert fixture.External.header('EXAMPLE-header')=='EXAMPLE-header'
request=fixture.Request();request.request_id='EXAMPLE-header';request.external_req_id=None
assert fixture.Assignment.assign(request)is None
assert request.request_id=='EXAMPLE-header-internal'
print('EXAMPLE RPC 완료')
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(root)], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'EXAMPLE RPC 완료'


def test_isolated_rpc_parent_frame_and_assignment_are_sealed(tmp_path):
    root, settings = _rpc_prepared(tmp_path); _run_rpc(root)
    result = tool.verify_capture(root, root / 'data' / settings['session'])
    assert result['source_rows_verified'] == result['snapshots'] == 6 and result['gaps'] == []
    rows = result['rows']; entered = [r for r in rows if r['point'] == 'Inner.execute']
    assert len(entered) == 2 and all(r['rpc_parent_frame_verified'] and r['rpc_parent_worker_verified'] for r in entered)
    header, = [r for r in rows if r['point'] == 'External.header']
    assignment, = [r for r in rows if r['point'] == 'Assignment.assign']
    assert header['external_request_hmac_sha256'] == assignment['assignment']['external_hmac_sha256']
    assert 'EXAMPLE-private-request' not in json.dumps(result) and settings['key'] not in json.dumps(result)
    assert result['engine_group_verified'] is result['producer_registered'] is False


@pytest.mark.parametrize('change', ['frame', 'event', 'request', 'body', 'time'])
def test_even_resigned_inconsistent_rpc_metadata_is_rejected(tmp_path, change):
    root, settings = _rpc_prepared(tmp_path); _run_rpc(root)
    capture = root / 'data' / settings['session']
    for path in capture.glob('pid-*.json'):
        record = json.loads(path.read_bytes()); payload = record['snapshot']['payload']
        if payload['point'] == 'Inner.execute' and payload['event'] == 'CALL':
            break
    else:
        pytest.fail('실제 내부 호출 기록이 없다')
    if change == 'frame':
        payload['rpc_parent_frame_verified'] = payload['rpc_parent_worker_verified'] = False
        payload['rpc_parent_call_hmac_sha256'] = None
    elif change == 'event':
        payload['event'] = 'READY'
    elif change == 'request':
        payload['request_hmac_sha256'] = []
    elif change == 'body':
        payload['body_code_sha256'] = '0' * 64
    else:
        payload['operation_started_ns'] = payload['observation_ns'] + 1
    record['hmac_sha256'] = hmac.new(bytes.fromhex(settings['key']), tool.canonical(record['snapshot']), sha256).hexdigest()
    path.write_bytes(tool.canonical(record))
    with pytest.raises(ValueError):
        tool.verify_capture(root, capture)


def test_missing_current_assets_cannot_impersonate_a_legacy_prepared_set(tmp_path):
    root, settings = _rpc_prepared(tmp_path); _run_rpc(root)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    del manifest['assets']['guard/adapters/vllm_rank_rpc_hooks.py']
    settings['assets'] = manifest['assets']; raw = tool.canonical(settings)
    (root / 'settings.json').write_bytes(raw); manifest['settings_sha256'] = sha256(raw).hexdigest()
    manifest['source_manifest_sha256'] = '7c225d4eb755d80f554938159056b5a038e1a0a7b48a1fc6eeac06ca0a1f2232'
    (root / 'manifest.json').write_bytes(tool.canonical(manifest))
    with pytest.raises(ValueError, match='RANK_IPC_PREPARED_MISMATCH'):
        tool.verify_capture(root, root / 'data' / settings['session'])
