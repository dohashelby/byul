"""Ordinary passive callback preparation from exact handoff and saved Pod fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import stat

import pytest
import yaml

from guard.models import canonical_json, sha256_hex
from tests.test_gpu_a_aws_lab import HANDOFF, REVISION
from tests.test_gpu_a_run_inputs import NS_UID
from tools import gpu_a_aws_lab as lab
from tools import gpu_a_normal_tp2_callbacks as passive
from tools import gpu_a_producers_collect as collector
from tools import gpu_a_run_inputs as inputs
from tools.mvp_versions import versions


def _stage(tmp_path, side, mode, *, generation='v2-default'):
    release = versions()[side]
    arguments = dict(generation=generation, profile='dev', run_id='test-' + side,
        engine_image=release['image'], engine_version=release['version'], model_revision=REVISION,
        host_root='/srv/byul-a', release_launch=True, normal_tp2=True)
    output = tmp_path / 'prepared'
    record = lab.prepare_normal_tp2_render(HANDOFF, output, callback_mode=mode, **arguments)
    return output, record, arguments


def _owned_pods(output):
    deployment, _ = yaml.safe_load_all((output / 'engine.yaml').read_bytes())
    template = deployment['spec']['template']
    engine = {'kind': 'Pod', 'metadata': {**template['metadata'], 'namespace': 'serving-lab', 'name': 'engine-a'},
              'spec': template['spec']}
    probe = yaml.safe_load((output / 'probe.yaml').read_bytes())
    for index, pod in enumerate((engine, probe), 1):
        container = pod['spec']['containers'][0]
        pod['metadata']['uid'] = f'00000000-0000-0000-0000-00000000000{index}'
        pod['spec']['nodeName'] = 'test-node'
        pod['status'] = {'phase': 'Running', 'podIP': f'10.0.4.{index}',
            'conditions': [{'type': 'Ready', 'status': 'True'}],
            'containerStatuses': [{'name': container['name'], 'containerID': 'containerd://' + str(index) * 64,
                'imageID': container['image'], 'restartCount': 0, 'ready': True,
                'state': {'running': {'startedAt': '2026-11-04T01:00:00Z'}}}]}
    return {'kind': 'Namespace', 'metadata': {'name': 'serving-lab', 'uid': NS_UID}}, engine, probe


def _environment(record, namespace, engine):
    return inputs.environment(record, inventory='b' * 64, gpu_model='NVIDIA L4', driver='EXAMPLE-driver',
        observed_engine_version=record['engine_version'], external_platform='Linux x86_64', engine_pod=engine,
        namespace=namespace, observed_gpu_count=2, data_origin='EXAMPLE')


def _target(env, record, namespace, engine, probe):
    return inputs.target(env, record, namespace, engine, probe, context='offline-fixture',
                         observed_engine_version=record['engine_version'])


@pytest.mark.parametrize('side', ('baseline', 'candidate'))
@pytest.mark.parametrize('mode', passive.MODES)
def test_normal_tp2_passive_stage_preserves_boot_and_binds_public_saved_owner(tmp_path, side, mode):
    generation = 'v2-template' if mode == 'worker-shards-and-scoped-runner-consumption' else 'v2-default'
    output, record, arguments = _stage(tmp_path, side, mode, generation=generation)
    engine_yaml, probe_yaml, stock = lab.render(HANDOFF, **arguments)
    deployment, service = yaml.safe_load_all((output / 'engine.yaml').read_bytes())
    stock_deployment, stock_service = yaml.safe_load_all(engine_yaml)
    container = deployment['spec']['template']['spec']['containers'][0]
    stock_container = stock_deployment['spec']['template']['spec']['containers'][0]
    assert record['launch'] == stock['launch']
    assert container['command'] == stock_container['command']
    assert container['args'] == stock_container['args']
    assert container['securityContext'] == stock_container['securityContext']
    assert container['env'] == [*stock_container['env'], {'name': 'PYTHONPATH', 'value': lab.CALLBACKS}]
    assert service == stock_service and (output / 'probe.yaml').read_bytes() == probe_yaml
    assert passive.KEY not in stock
    if generation == 'v2-template':
        assert sha256_hex((output / Path(lab.CHAT_TEMPLATE_PATH).name).read_bytes()) == lab.CHAT_TEMPLATE_SHA256

    path = output / 'engine-callbacks'
    plan, files, manifest, settings = passive.read_preparation(path, mode)
    assert plan == {key: record[passive.KEY][key] for key in plan}
    assert all((path / name).read_bytes() == contents for name, contents in files.items())
    assert {key for key in passive._FLAGS if manifest[key]} == passive.MODES[mode]
    assert all(settings[key] is manifest[key] for key in passive._FLAGS)
    assert stat.S_IMODE(path.stat().st_mode) == 0o500
    assert stat.S_IMODE((path / 'settings.json').stat().st_mode) == 0o400
    assert not list((output / 'lifecycle-data').iterdir())
    assert stat.S_IMODE((output / 'lifecycle-data').stat().st_mode) == 0o700
    assert set(record[passive.KEY]) == set(plan) | {'mounts_sha256'}
    assert deployment['spec']['template']['metadata']['annotations'][passive.ANNOTATION] == passive.plan_sha256(record[passive.KEY])
    assert json.loads((output / 'render.json').read_bytes()) == record

    namespace, engine, probe = _owned_pods(output)
    env = _environment(record, namespace, engine)
    target = _target(env, record, namespace, engine, probe)
    assert passive.environment_field(record[passive.KEY]) in env.runtime.platform.split('; ')
    assert env.data_origin.value == 'EXAMPLE'
    assert target[passive.KEY] == record[passive.KEY]
    assert target['environment_ref'] == env.ref().model_dump(mode='json')
    assert target['comparison_config']['launch'][passive.KEY] == {
        key: plan[key] for key in ('schema', 'mode', 'assets_sha256')}
    assert not {'manifest_sha256', 'settings_sha256', 'session_sha256'} & set(target['comparison_config']['launch'][passive.KEY])


@pytest.mark.parametrize('damage', ('drop-plan', 'drop-code', 'subpath', 'read-write-code', 'shared-source',
                                    'change-pythonpath', 'runner-override', 'different-reader', 'drop-annotation', 'changed-seal'))
def test_normal_tp2_passive_saved_pod_requires_the_exact_separate_mounts(tmp_path, damage):
    output, record, _ = _stage(tmp_path, 'baseline', 'worker-shards')
    namespace, engine, probe = _owned_pods(output)
    env = _environment(record, namespace, engine)
    candidate, spec = deepcopy(record), engine['spec']
    container = spec['containers'][0]
    code = next(item for item in container['volumeMounts'] if item['name'] == 'guard-callbacks')
    data = next(item for item in spec['volumes'] if item['name'] == 'guard-data')
    if damage == 'drop-plan':
        del candidate[passive.KEY]
    elif damage == 'drop-code':
        container['volumeMounts'].remove(code)
    elif damage == 'subpath':
        code['subPath'] = 'another-directory'
    elif damage == 'read-write-code':
        code['readOnly'] = False
    elif damage == 'shared-source':
        data['hostPath']['path'] = candidate['host_directories']['engine_callbacks']
    elif damage == 'change-pythonpath':
        next(item for item in container['env'] if item['name'] == 'PYTHONPATH')['value'] = lab.DATA
    elif damage == 'runner-override':
        container['env'].append({'name': lab.V2_MODEL_RUNNER_ENV, 'value': '0'})
    elif damage == 'different-reader':
        container['securityContext']['runAsUser'] = 2001
    elif damage == 'drop-annotation':
        del engine['metadata']['annotations'][passive.ANNOTATION]
    else:
        candidate[passive.KEY]['manifest_sha256'] = 'c' * 64
    with pytest.raises(ValueError, match='^(RUN_INPUTS_NORMAL_TP2_POD_MISMATCH|AWS_NORMAL_TP2_CALLBACKS_UNVERIFIED)$'):
        _target(env, candidate, namespace, engine, probe)


@pytest.mark.parametrize('damage', ('changed-settings-bytes', 'enabled-extra-option', 'extra-import-file', 'other-session'))
def test_normal_tp2_passive_collector_blocks_replaced_stage_before_transport(tmp_path, monkeypatch, damage):
    output, record, _ = _stage(tmp_path, 'candidate', 'scoped-runner-consumption')
    namespace, engine, probe = _owned_pods(output)
    env = _environment(record, namespace, engine)
    target = _target(env, record, namespace, engine, probe)
    env_path, target_path = tmp_path / 'env.json', tmp_path / 'target.json'
    env_path.write_bytes(canonical_json(env.to_dict()))
    target_raw = canonical_json(target)
    target_path.write_bytes(target_raw)

    root = output / 'engine-callbacks'
    if damage == 'changed-settings-bytes':
        settings = root / 'settings.json'
        settings.chmod(0o600)
        settings.write_bytes(settings.read_bytes() + b'\n')
    elif damage == 'enabled-extra-option':
        settings = root / 'settings.json'
        saved = json.loads(settings.read_bytes())
        saved['profiler_control'] = True
        settings.chmod(0o600)
        settings.write_bytes(canonical_json(saved))
    elif damage == 'extra-import-file':
        root.chmod(0o700)
        (root / 'unreviewed.py').write_bytes(b'# EXAMPLE non-shipped source')
    else:
        root = tmp_path / 'different-assets'
        passive.prepare(root, 'scoped-runner-consumption')

    def unexpected_transport(*_args, **_kwargs):
        raise AssertionError('Offline EXAMPLE must fail before even constructing a target transport')

    monkeypatch.setattr(collector, 'KubernetesHTTP', unexpected_transport)
    with pytest.raises(ValueError, match='^AWS_NORMAL_TP2_CALLBACKS_UNVERIFIED$'):
        collector.collect(env_path=env_path, target_path=target_path, prepared_dir=root,
                          target_file_sha256=sha256_hex(target_raw))


def test_passive_mode_requires_the_exact_tp2_v2_launch(tmp_path):
    _, _, arguments = _stage(tmp_path, 'baseline', 'worker-shards')
    for extra in (
        {'generation': lab.NORMAL_TP2_V1_EAGER_GENERATION},
        {'generation': 'v2-producers'},
        {'normal_tp2': False},
        {'release_launch': False},
        {'extra_engine_args': (lab.EAGER_WEIGHT_READ_ARG,)},
    ):
        refused = tmp_path / 'refused'
        with pytest.raises(ValueError, match='^AWS_NORMAL_TP2_CALLBACKS_UNVERIFIED$'):
            lab.prepare_normal_tp2_render(HANDOFF, refused, callback_mode='worker-shards', **{**arguments, **extra})
        assert not refused.exists()


def test_normal_tp2_pair_has_equal_runtime_and_distinct_source_pins(tmp_path):
    modes = 'worker-shards-and-scoped-runner-consumption'
    results = []
    for side in ('baseline', 'candidate'):
        output, record, _ = _stage(tmp_path / side, side, modes)
        namespace, engine, probe = _owned_pods(output)
        env = _environment(record, namespace, engine)
        target = _target(env, record, namespace, engine, probe)
        results.append((env, target, record[passive.KEY]))
    baseline, candidate = results
    assert baseline[0].runtime == candidate[0].runtime
    assert baseline[1]['comparison_config']['launch'] == candidate[1]['comparison_config']['launch']
    for key in ('session_sha256', 'settings_sha256', 'manifest_sha256', 'mounts_sha256'):
        assert baseline[2][key] != candidate[2][key]


def test_normal_tp2_collector_requires_original_target_bytes_even_if_replacement_stage_is_consistent(tmp_path, monkeypatch):
    original, record, _ = _stage(tmp_path / 'original', 'baseline', 'worker-shards')
    namespace, engine, probe = _owned_pods(original)
    env = _environment(record, namespace, engine)
    target = _target(env, record, namespace, engine, probe)
    env_path, target_path = tmp_path / 'env.json', tmp_path / 'target.json'
    env_path.write_bytes(canonical_json(env.to_dict()))
    original_sha = sha256_hex(canonical_json(target))

    replacement, replacement_record, _ = _stage(tmp_path / 'replacement', 'baseline', 'worker-shards')
    replacement_target = _target(env, replacement_record, *_owned_pods(replacement))
    assert replacement_target['environment_ref'] == target['environment_ref']
    assert replacement_target['comparison_config'] == target['comparison_config']
    replacement_raw = canonical_json(replacement_target)
    assert sha256_hex(replacement_raw) != original_sha
    target_path.write_bytes(replacement_raw)

    def unexpected_transport(*_args, **_kwargs):
        raise AssertionError('Offline EXAMPLE must reject the mismatched independent raw target pin')

    monkeypatch.setattr(collector, 'KubernetesHTTP', unexpected_transport)
    for pin in (None, original_sha):
        with pytest.raises(ValueError, match='^AWS_NORMAL_TP2_CALLBACKS_UNVERIFIED$'):
            collector.collect(env_path=env_path, target_path=target_path, prepared_dir=replacement / 'engine-callbacks',
                              target_file_sha256=pin)
