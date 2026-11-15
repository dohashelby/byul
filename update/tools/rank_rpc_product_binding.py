"""M-A3의 장애 적용 전 제품 입력 결합을 만든다. 기동·신호·등록은 수행하지 않는다."""
import argparse
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat

from guard.adapters import vllm_rank_rpc_control as control
from guard.models import Environment, canonical_json
from tools import rank_ipc_candidates as ipc
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]


def _json(path, bound=32768):
    raw = ipc._read(Path(path), bound)
    return json.loads(raw, object_pairs_hook=control._pairs), raw


def _prepared(path):
    """현재 검토 소스와 정확히 같은 새 준비물만 받는다. 과거 실측 자산은 수정하지 않는다."""
    path = Path(path)
    source = ipc.verify_source(ROOT.parent)['reviewed_common_commit']
    manifest, raw = _json(path / 'manifest.json')
    settings, settings_raw = _json(path / 'settings.json')
    private = (path / 'settings.json').stat(follow_symlinks=False)
    if private.st_uid != os.geteuid() or stat.S_IMODE(private.st_mode) != 0o600:
        raise ValueError('RANK_RPC_BINDING_PRIVATE_SETTINGS')
    assets = {}
    for name in ipc.runtime.ASSETS:
        expected = ipc.BOOTSTRAP.encode() if name == 'sitecustomize.py' else (ROOT / 'src' / name).read_bytes()
        pin = {'bytes': len(expected), 'sha256': sha256(expected).hexdigest()}
        if ipc.runtime._asset(path / name, pin) != expected:
            raise ValueError('RANK_RPC_BINDING_ASSET_MISMATCH')
        assets[name] = pin
    if (type(settings) is not dict
            or set(settings) != {'schema', 'session', 'image', 'key', 'output_dir', 'assets'}
            or settings['schema'] != 'guard-rank-ipc-settings/1'
            or not control.fingerprint(settings['key'])
            or type(settings['session']) is not str or re.fullmatch('[0-9a-f]{32}', settings['session']) is None
            or settings['output_dir'] != '/guard-rank-ipc/data/' + settings['session']
            or control.canonical(settings['assets']) != control.canonical(assets)
            or control.canonical(manifest) != control.canonical({'schema': 'guard-rank-ipc-prepared-candidate/1',
                'session': settings['session'], 'image': settings['image'],
                'settings_sha256': sha256(settings_raw).hexdigest(), 'assets': assets,
                'source_manifest_sha256': source, 'observer_installed': False,
                'engine_group_verified': False, 'producer_registered': False})):
        raise ValueError('RANK_RPC_BINDING_PREPARED_MISMATCH')
    return manifest, settings, sha256(raw).hexdigest()


def binding_for(prepared, environment, target):
    manifest, settings, prepared_sha = _prepared(prepared)
    document, _ = _json(environment, 1048576)
    env = Environment.model_validate(document)
    if not env.hash_ok() or env.kind.value != 'ENV':
        raise ValueError('RANK_RPC_BINDING_ENVIRONMENT_INVALID')
    pair = [p for p in versions().values() if p['image'] == manifest['image']]
    if (len(pair) != 1 or (env.engine.name, env.engine.version, env.engine.image_digest) !=
            (pair[0]['engine'], pair[0]['version'], pair[0]['image'])
            or env.runtime.tp_size != 2 or env.runtime.pp_size != 1 or env.runtime.dp_size != 1
            or type(env.runtime.gpu_count) is not int or env.runtime.gpu_count < 2):
        raise ValueError('RANK_RPC_BINDING_ENVIRONMENT_MISMATCH')
    target_value, _ = _json(target, 1048576)
    ref = env.ref().model_dump(mode='json')
    if (type(target_value) is not dict
            or control.canonical(target_value.get('environment_ref')) != control.canonical(ref)):
        raise ValueError('RANK_RPC_BINDING_TARGET_ENVIRONMENT')
    pod = target_value.get('kubernetes', {}).get('engine', {})
    if (not isinstance(pod, dict) or pod.get('image') != manifest['image']
            or pod.get('image_id') != manifest['image']):
        raise ValueError('RANK_RPC_BINDING_TARGET_IMAGE')
    container = pod.get('container_id')
    if type(container) is not str:
        raise ValueError('RANK_RPC_BINDING_TARGET_OWNER')
    value = {'schema': 'guard-rank-rpc-product-binding/1', 'session': settings['session'],
        'environment_ref': ref, 'target_sha256': sha256(canonical_json(target_value)).hexdigest(),
        'prepared_sha256': prepared_sha, 'pod_uid': pod.get('pod_uid'),
        'container_id': container.removeprefix('containerd://'), 'restart_count': pod.get('restart_count')}
    origin = 'EXAMPLE' if env.data_origin.value == 'EXAMPLE' else 'DECLARED'
    return control.product_binding(value, settings['session']), settings, origin


def prepare(prepared, environment, target, output):
    value, settings, origin = binding_for(prepared, environment, target)
    envelope = {'payload': value, 'hmac_sha256': hmac.new(bytes.fromhex(settings['key']),
        control.DOMAIN + control.canonical(value), sha256).hexdigest()}
    output = Path(output)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    raw = control.canonical(envelope)
    path = output / 'control-product-binding.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, raw) != len(raw):
            raise OSError('RANK_RPC_BINDING_PARTIAL')
    finally:
        os.close(fd)
    return {'schema': 'guard-rank-rpc-product-binding-preparation/1',
        'binding': value, 'file_sha256': sha256(raw).hexdigest(),
        'data_origin': origin,
        'live_target_verified': False, 'input_preflight_passed': False,
        'engine_executed': False, 'scope_complete': False, 'producer_registered': False}


def require_case_binding(case, expected):
    """변환 전 필요한 범위 검사다. HMAC·원 관측·판정 자격 확인을 대신하지 않는다."""
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
    # 형식이 맞아도 공통 입력을 만들거나 인증/완료를 반환하지 않는다.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'environment', 'target', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.prepared, args.environment, args.target, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
