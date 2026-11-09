"""New role preparation contracts; synthetic identities are never deployed."""
import base64
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors.lifecycle import load_prepared
from guard.executors import registry
from guard.models import canonical_json
from tools import gpu_b_sup16_operating_prepare as op
from tests.builders import environment


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))
    return path


def role_input(tmp_path, monkeypatch, phase='normal-before'):
    rows = [{'path': 'model.safetensors', 'sha256': 'a' * 64, 'bytes': 12}]
    env = environment(engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': op.static.GPU_IMAGE},
        model={'id': 'EXAMPLE-SUP16-no-model-load', 'revision': 'EXAMPLE-revision', 'inventory_sha256': op.sha(canonical_json(rows))})
    engine = {'namespace': 'example-owned', 'namespace_uid': '11111111-1111-1111-1111-111111111111',
        'pod': 'example-' + phase, 'pod_uid': '22222222-2222-2222-2222-222222222222',
        'container': 'engine', 'container_id': 'containerd://' + 'b' * 64, 'image': op.static.GPU_IMAGE,
        'image_id': 'sha256:' + 'c' * 64, 'restart_count': 0, 'node': 'example-node',
        'pod_ip': '10.0.0.10', 'started_at': '2026-11-09T01:00:00Z'}
    target = {'schema': 'guard-kubernetes-lifecycle-target/1', 'run_id': 'example-sup16',
        'environment_ref': env.ref().model_dump(mode='json'), 'lab': True,
        'kubernetes': {'context': 'EXAMPLE-contract', 'engine': engine}}
    root = tmp_path / 'inputs'
    manifest = write(root / 'reference.json', {'schema': 'guard.model-manifest/1', 'scope': 'all',
        'model_id': env.model.id, 'files': rows, 'inventory_sha256': env.model.inventory_sha256})
    sha = op.sha(manifest.read_bytes())
    args = SimpleNamespace(source_root=op.static.ROOT, source_commit='a' * 40, manifest_sha256='b' * 64,
        static_packet=root / 'static', static_sha256='c' * 64, phase=phase,
        environment=write(root / 'ENV.json', env.model_dump(mode='json')),
        target=write(root / 'target.json', target), model_manifest=manifest, model_manifest_sha256=sha,
        previous_binding=[], output=tmp_path / 'new-role')
    monkeypatch.setattr(op, 'packet', lambda a: ({'models': {'normal': {'reference_manifest_sha256': sha}}}, {},
        {'commit': a.source_commit, 'manifest_sha256': a.manifest_sha256, 'fixture_boundary': 'EXAMPLE source context only'}))
    return args, env, target


@pytest.mark.parametrize('phase', list(op.PHASES))
def test_supplied_role_inputs_to_real_prepared_loader_and_registered_factory_load(tmp_path, monkeypatch, phase):
    args, env, target = role_input(tmp_path, monkeypatch, phase)
    result = op.bind_role(args)
    role = op.PHASES[phase]
    prepared = load_prepared(args.output / 'native17/manifest.json')
    binding = json.loads((args.output / (role + '.binding.json')).read_bytes())
    payload = json.loads((args.output / 'installer-payload.json').read_bytes())
    assert base64.b64decode(payload['manifest']) == (args.output / 'native17/manifest.json').read_bytes()
    for name in prepared['assets']:
        assert base64.b64decode(payload['assets'][name]) == (args.output / 'native17' / name).read_bytes()
    assert binding['role'] == role and binding['session'] == prepared['session'] == result['session']
    assert binding['environment_ref'] == env.ref().model_dump(mode='json')
    assert binding['source_sha256'] == op.static.PINS['pair.py']
    values = {'supply_' + role + '_target_sha256': result['target_sha256'],
        'supply_' + role + '_prepared_sha256': result['prepared_sha256']}
    factory = registry.build_executor('supply_boot', 'kubernetes', {'supply_boot': {
        role: {'target': str(args.output / 'target.json'), 'prepared': str(args.output / 'native17/manifest.json')}}})
    loaded_target, loaded_prepared, digest = factory._load(env, values, role)
    assert loaded_target == target and loaded_prepared == prepared and digest == result['target_sha256']
    assert result['ready_for_execution'] is False and result['live_identity_checked'] is False
    assert result['engine_or_AWS_started'] is False
    assert not list(args.output.glob('ENV*')) and not list(args.output.glob('POL*'))


@pytest.mark.parametrize('field', ['container_id', 'started_at', 'pod_uid'])
def test_pending_target_cannot_be_filled_with_future_values(tmp_path, monkeypatch, field):
    args, _, target = role_input(tmp_path, monkeypatch)
    target['kubernetes']['engine'].pop(field)
    write(args.target, target)
    with pytest.raises(ValueError, match='KUBERNETES_CAPTURE_TARGET_INVALID'):
        op.bind_role(args)
    assert not args.output.exists()


def test_role_generation_reuse_rejected_without_modifying_previous(tmp_path, monkeypatch):
    args, _, target = role_input(tmp_path, monkeypatch, 'normal-recovered')
    previous = write(tmp_path / 'prior.json', {'schema': 'byul-sup16-role-preparation/1',
        'source': {'commit': args.source_commit}, 'phase': 'normal-before', 'engine': target['kubernetes']['engine']})
    original = previous.read_bytes()
    args.previous_binding = [previous]
    with pytest.raises(ValueError, match='FRESH_POD_GENERATION_REQUIRED'):
        op.bind_role(args)
    assert previous.read_bytes() == original and not args.output.exists()


def test_wrong_reference_stops_before_preparing_assets(tmp_path, monkeypatch):
    args, _, _ = role_input(tmp_path, monkeypatch)
    args.model_manifest_sha256 = '0' * 64
    with pytest.raises(ValueError, match='FILE_READ_DEPENDENCY_UNAVAILABLE'):
        op.bind_role(args)
    assert not args.output.exists()


def test_existing_output_and_original_input_survive_collision(tmp_path, monkeypatch):
    args, _, _ = role_input(tmp_path, monkeypatch)
    old = write(args.output / 'old.json', {'original': True})
    raw = old.read_bytes()
    with pytest.raises(ValueError, match='existing evidence is never overwritten'):
        op.bind_role(args)
    assert old.read_bytes() == raw


def template(tmp_path):
    config = {'kind': 'ConfigMap', 'metadata': {'name': 'sup16-pair-producer'}, 'data': {
        'write_pair_boot.py': 'old', 'sup16_stage_artifact.py': 'old-stage', 'native17_install.py': '# unchanged installer',
        'launch_native_engine.py': '# unchanged bounded launcher', 'runtime_probe.py': '# unchanged probe'}}
    pods = [{'kind': 'Pod', 'metadata': {'name': 'sup16-' + role}, 'spec': {'containers': [{'name': 'engine',
        'resources': {'limits': {'nvidia.com/gpu': '1'}}, 'command': ['original-launcher']}], 'original_role': role}} for role in ('normal', 'challenge')]
    return write(tmp_path / 'template.json', {'items': [{'kind': 'Namespace', 'metadata': {'name': '@NAMESPACE@'}}, config, *pods]})


def test_plan_has_one_pod_per_phase_and_exact_reused_pair_assets(tmp_path, monkeypatch):
    args, _, _ = role_input(tmp_path, monkeypatch)
    args.pair_template = template(tmp_path)
    args.capture_helper = tmp_path / 'capture.py'
    args.capture_helper.write_bytes(b'# EXAMPLE capture source, not executed\n')
    monkeypatch.setattr(op, 'CAPTURE_SHA', op.sha(args.capture_helper.read_bytes()))
    monkeypatch.setattr(op, 'TEMPLATE_SHA', op.sha(args.pair_template.read_bytes()))
    assets = {n: (op.static.ROOT / ('update/tools/gpu_b_sup16_pair_writer.py' if n == 'pair.py' else
        op.static.SNAPSHOTS + '/sup16-write_normal_boot.py.txt')).read_bytes() for n in ('pair.py', 'ordering.py')}
    assets['stage.py'] = b'# EXAMPLE stage bytes, not executed'
    assets['source_owner.py'] = b'# EXAMPLE owner helper, not executed'
    assets['original-procedure.json'] = canonical_json({'sequence': [{'step': 'publish_actual_binding',
        'inside_preparation_argv': ['python3', 'normal.binding.json'], 'stdin': 'OLD'}]})
    monkeypatch.setattr(op, 'packet', lambda a: ({'models': {}}, assets, {'commit': args.source_commit, 'manifest_sha256': args.manifest_sha256}))
    result = op.plan(args)
    assert result['phase_order'] == list(op.PHASES)
    for phase in op.PHASES:
        emitted = json.loads((args.output / (phase + '.template.json')).read_bytes())
        pod, = [x for x in emitted['items'] if x['kind'] == 'Pod']
        config, = [x for x in emitted['items'] if x['kind'] == 'ConfigMap']
        assert pod['metadata']['name'] == 'sup16-' + phase
        assert config['data']['pair.py'].encode() == assets['pair.py']
        assert config['data']['ordering.py'].encode() == assets['ordering.py']
        assert 'write_pair_boot.py' not in config['data']
        assert pod['spec']['containers'][0]['command'] == ['original-launcher']
        assert result['phases'][phase]['capture'][-1] == '--execute'
        role = op.PHASES[phase]
        assert result['phases'][phase]['binding_publication']['inside_preparation_argv'][-1] == role + '.binding.json'
        assert result['phases'][phase]['capture'][result['phases'][phase]['capture'].index('--source-commit') + 1] == args.source_commit
        bind = result['phases'][phase]['bind']
        expected = list(op.PHASES)[:list(op.PHASES).index(phase)]
        prior = [bind[i + 1] for i, value in enumerate(bind) if value == '--previous-binding']
        assert prior == ['<new-' + p + '-inputs>/role-preparation.json' for p in expected]
    assert result['ready_for_execution'] is False and result['executed'] is False
    assert result['product_pair_blocker'] == op.PAIR_BLOCKER
    assert result['deadlines_seconds'] == {'asset_wait': 90, 'collection': 120, 'Pod': 600}


def test_template_sha_error_prevents_output_creation(tmp_path, monkeypatch):
    args, _, _ = role_input(tmp_path, monkeypatch)
    args.pair_template = template(tmp_path)
    args.capture_helper = tmp_path / 'capture.py'
    args.capture_helper.write_bytes(b'EXAMPLE wrong source')
    monkeypatch.setattr(op, 'CAPTURE_SHA', op.sha(args.capture_helper.read_bytes()))
    with pytest.raises(ValueError, match='FILE_READ_DEPENDENCY_UNAVAILABLE'):
        op.plan(args)
    assert not args.output.exists()
