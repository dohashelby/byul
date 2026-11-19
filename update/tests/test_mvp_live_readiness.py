"""All active sides stay visible; offline and synthetic inputs never become live proof."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess

import pytest

from guard.models import load_pack
from tools import mvp_live_readiness as tool
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_mvp_a_input_preflight import sources  # noqa: F401


def run(tmp_path, value):
    index = tmp_path / 'index.json'
    index.write_text(json.dumps(value), encoding='utf-8')
    return tool.audit(index)


def blank():
    return tool.template(load_pack(tool.ROOT / 'packs/PACK-BASELINE'))


def test_all_69_checks_and_138_sides_remain_visible_without_network_or_execution(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('offline readiness must not access a target or execute commands')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    report = run(tmp_path, blank())
    assert report['active_checks'] == 69 and len(report['rows']) == report['sides'] == 138
    assert len({(r['check_id'], r['side']) for r in report['rows']}) == 138
    assert all('ACTUAL_INPUT_FILES_MISSING' in r['blocking_codes'] for r in report['rows'])
    assert report['blocking_counts']['SECURITY_RULES_EMPTY'] == 2
    assert {r['check_id'] for r in report['rows'] if not r['security_rules_present']} == {'SUP-03'}
    assert all(r['engine_executed'] is r['ready_for_execution'] is r['live_target_verified'] is False
               and r['actual_results'] is None for r in report['rows'])
    assert report['engine_executed'] is report['ready_for_execution'] is False


@pytest.mark.parametrize('damage', ['pack', 'retired', 'side', 'duplicate', 'extra'])
def test_invalid_or_ambiguous_sources_are_rejected(tmp_path, damage):
    value = blank()
    item = dict(check_id='SUP-08', side='baseline', environment=None, policy=None, configuration=None)
    value['inputs'] = [item]
    if damage == 'pack': value['pack_ref']['revision'] -= 1
    if damage == 'retired': item['check_id'] = 'SUP-01'
    if damage == 'side': item['side'] = 'single'
    if damage == 'duplicate': value['inputs'].append(deepcopy(item))
    if damage == 'extra': item['ready_for_execution'] = True
    with pytest.raises(ValueError, match='MVP_SOURCE_'):
        run(tmp_path, value)


def test_supplied_file_hash_mismatch_is_recorded_without_hiding_other_137_sides(tmp_path):
    value = blank();path = tmp_path / 'env.json';path.write_text('{}')
    pointer = {'path': str(path), 'sha256': '0' * 64}
    value['inputs'] = [dict(check_id='SUP-08', side='baseline', environment=pointer,
                            policy=pointer, configuration=pointer)]
    report = run(tmp_path, value)
    damaged = next(r for r in report['rows'] if (r['check_id'], r['side']) == ('SUP-08', 'baseline'))
    assert damaged['blocking_codes'] == ['MVP_INPUT_HASH_MISMATCH']
    assert report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING'] == 137


def test_example_file_binding_does_not_issue_live_readiness(tmp_path, sources):  # noqa: F811
    value = blank();value['inputs'] = sources['inputs']
    report = run(tmp_path, value)
    bound = [r for r in report['rows'] if r['file_binding_valid']]
    assert report['file_binding_valid_sides'] == len(bound) == 2
    assert all('EXAMPLE_ENV_NOT_ACTUAL' in r['blocking_codes'] for r in bound)
    assert all('LIVE_EXECUTOR_PREFLIGHT_REQUIRED' in r['blocking_codes'] for r in bound)
    assert all(not r['ready_for_execution'] and r['actual_results'] is None for r in bound)
    assert report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING'] == 136


def test_fake_executor_cannot_be_an_active_execution_input(tmp_path, sources):  # noqa: F811
    value = blank();value['inputs'] = sources['inputs'][:1]
    pointer = value['inputs'][0]['configuration'];path = Path(pointer['path'])
    path.write_bytes(path.read_bytes().replace(b'kubernetes', b'fake'))
    pointer['sha256'] = sha256(path.read_bytes()).hexdigest()
    report = run(tmp_path, value)
    row = next(r for r in report['rows'] if (r['check_id'], r['side']) == ('SUP-08', 'baseline'))
    assert row['file_binding_valid'] is False
    assert row['blocking_codes'] == ['MVP_ACTIVE_EXECUTOR_REQUIRED']


def test_typed_but_unsupported_policy_is_refused_by_the_actual_executor_before_network(tmp_path,sources,monkeypatch):
    import yaml
    def forbidden(*args,**kwargs):raise AssertionError('offline admission must not execute or contact a target')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    value=blank();value['inputs']=sources['inputs'][:1]
    entry=value['inputs'][0];path=Path(entry['policy']['path']);policy=yaml.safe_load(path.read_bytes())
    policy['values']['SUP-08']['code_selection_fields']=['invalid..field']
    path.write_text(yaml.safe_dump(policy));entry['policy']['sha256']=sha256(path.read_bytes()).hexdigest()
    report=run(tmp_path,value)
    row=next(r for r in report['rows']if(r['check_id'],r['side'])==('SUP-08','baseline'))
    assert row['file_binding_valid']is False and row['blocking_codes']==['MVP_EXECUTOR_POLICY_INVALID']
    assert len(report['rows'])==138 and report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING']==137
    assert not report['engine_executed']and not report['ready_for_execution']


@pytest.mark.parametrize('check_id,projection', [
    ('REQ-08', 'REUSED_INPUT_PROJECTION'), ('REQ-09', 'NATIVE_REJECTION_PROJECTION'),
])
@pytest.mark.parametrize('capture,reason', [
    ('valid', None),
    ('absent', 'MVP_EXECUTOR_POLICY_INVALID'),
    ('missing', 'NATIVE_INPUT_PREPARED_MISSING'),
    ('digest', 'NATIVE_INPUT_CAPTURE_BINDING_CHANGED'),
    ('target-image', 'NATIVE_INPUT_CAPTURE_TARGET_MISMATCH'),
    ('pod-image', 'NATIVE_INPUT_CAPTURE_TARGET_MISMATCH'),
    ('owner', 'NATIVE_INPUT_PREPARED_INVALID'),
    ('asset', 'NATIVE_INPUT_PREPARED_INVALID'),
    ('real', 'NATIVE_INPUT_KUBERNETES_REQUIRED'),
])
def test_current_matrix_binds_actual_prepared_producer_output_offline(
        tmp_path, monkeypatch, check_id, projection, capture, reason):
    import os

    from guard.collectors import native_input
    from guard.models import canonical_json
    from tools import verify_req10_consumption
    from tests.builders import policy
    from tests.test_input_matrix import values as input_values
    from tests.test_model_preparation_integration import full_inputs
    from tests.test_reused_input_matrix import values as reused_values

    def forbidden(*args, **kwargs):
        raise AssertionError('no target or engine during file admission')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE')
    release = next(row for row in json.loads(tool.vllm_native_input_rejection.PROFILES.read_bytes())['profiles']
                   if row['version'] == '0.30.0')
    identity = {key: release[key] for key in ('engine', 'version', 'image')}
    versions = {**tool.versions(), 'candidate': identity}
    monkeypatch.setattr(tool, 'versions', lambda: versions)
    env, target = full_inputs()
    env = env.model_copy(update={
        'engine': env.engine.model_copy(update={'name': identity['engine'], 'version': identity['version'],
                                                'image_digest': identity['image']}),
        'runtime': env.runtime.model_copy(update={'gpu_count': 2, 'gpu_model': 'EXAMPLE-GPU',
                                                 'driver': 'EXAMPLE-driver'}),
    }).seal()
    target['environment_ref'] = env.ref().model_dump(mode='json')
    target['lab'] = True
    other_image = 'example.invalid/engine@sha256:' + '0' * 64
    target['engine']['image_digest'] = other_image if capture == 'target-image' else identity['image']
    target['kubernetes']['engine']['image'] = other_image if capture == 'pod-image' else identity['image']
    values = input_values() if check_id == 'REQ-09' else reused_values()
    pol = policy(pack.ref(), scope={'include': [check_id]}, values={check_id: values})
    files = {}
    for role, obj in [('environment', env.to_dict()), ('policy', pol.to_dict()), ('target', target)]:
        file = tmp_path / (role + '.json')
        file.write_bytes(canonical_json(obj))
        files[role] = file

    cfg = tmp_path / 'guard.toml'
    implementation = 'real' if capture == 'real' else 'kubernetes'
    config = (f'[executors]\ninput_validation="{implementation}"\n[adapter]\nname="vllm"\n'
              f'[input_validation]\ntarget={json.dumps(str(files["target"]))}\n'
              'vocab_size=100\nvocab_source="EXAMPLE"\n')
    if check_id == 'REQ-08':
        config += 'reused_token_ids=[5,7]\n'
    prepared = tmp_path / 'prepared'
    if capture != 'absent':
        digest = 'a' * 64
        if capture not in {'missing', 'real'}:
            monkeypatch.setattr(verify_req10_consumption, 'verify', lambda _: {'reviewed_common_commit': 'a' * 64})
            manifest = native_input.prepare(prepared, image=identity['image'], repository=tool.ROOT.parent)
            if capture != 'digest':
                digest = sha256(native_input.runtime.canonical(manifest)).hexdigest()
            if capture == 'asset':
                (prepared / 'sitecustomize.py').write_bytes(b'# EXAMPLE-changed-observer')
        config += (f'[check_config.{check_id}.native_input_capture]\n'
                   f'prepared_dir={json.dumps(str(prepared))}\nprepared_sha256="{digest}"\n')
        if capture == 'owner':
            config += f'prepared_uid={os.geteuid() + 1}\n'
    cfg.write_text(config)
    files['configuration'] = cfg
    entry = {'check_id': check_id, 'side': 'candidate', **{
        role: {'path': str(files[role]), 'sha256': sha256(files[role].read_bytes()).hexdigest()}
        for role in ('environment', 'policy', 'configuration')}}
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({**blank(), 'inputs': [entry]}))
    result = tool.audit(index, side='candidate')
    row = next(r for r in result['rows'] if r['check_id'] == check_id)
    assert len(result['rows']) == 69
    assert row['file_binding_valid'] is row['executor_policy_valid'] is (reason is None)
    if reason is None:
        assert {'EXAMPLE_ENV_NOT_ACTUAL', 'LIVE_EXECUTOR_PREFLIGHT_REQUIRED'} <= set(row['blocking_codes'])
        private_key = json.loads((prepared / 'settings.json').read_bytes())['key']
        assert private_key not in json.dumps(result)
    else:
        assert reason in row['blocking_codes']
    assert projection + '_GPU_UNQUALIFIED' in row['blocking_codes']
    assert row['available_implementations'] == ['kubernetes']
    assert row['ready_for_execution'] is row['engine_executed'] is False and row['actual_results'] is None


def test_unpinned_image_reports_separate_missing_profiles_and_qualifications(tmp_path, monkeypatch):
    versions = tool.versions()
    versions['candidate'] = {**versions['candidate'], 'image': 'example.invalid/engine@sha256:' + '0' * 64}
    monkeypatch.setattr(tool, 'versions', lambda: versions)
    report = run(tmp_path, blank())
    candidates = {r['check_id']: r for r in report['rows'] if r['side'] == 'candidate'}
    for check_id, projection in [('REQ-08', 'REUSED_INPUT_PROJECTION'), ('REQ-09', 'NATIVE_REJECTION_PROJECTION')]:
        assert {
            'ACTUAL_INPUT_FILES_MISSING', 'NATIVE_INPUT_PROFILE_UNAVAILABLE', 'NATIVE_REJECTION_PROFILE_UNAVAILABLE',
            'NATIVE_INPUT_CAPTURE_GPU_UNQUALIFIED', projection + '_GPU_UNQUALIFIED',
        } <= set(candidates[check_id]['blocking_codes'])
    assert 'NATIVE_INPUT_PROFILE_UNAVAILABLE' not in candidates['REQ-10']['blocking_codes']


@pytest.mark.parametrize(('candidate_input', 'gap'), [
    ('exact', None),
    ('foreign-target', 'MVP_TARGET_ENVIRONMENT_MISMATCH'),
    ('foreign-release', 'SUPPLY_BOOT_NORMAL_BINDING_INVALID'),
    ('reference-changed', 'SUPPLY_BOOT_NORMAL_REFERENCE_MISMATCH'),
])
def test_sup05_normal_bundle_reaches_offline_input_audit(tmp_path, external, monkeypatch, candidate_input, gap):
    from guard.adapters.vllm_approved_file_read_composition import PROFILES
    from guard.collectors.lifecycle import prepare_assets
    from guard.collectors.model_read_scope import build_scope
    from guard.configuration import NORMAL_FILE_READS_OPTION
    from guard.models import canonical_json, sha256_hex
    from tests.builders import environment, policy
    from tests.test_kubernetes_sampling import inputs as sampling_inputs
    from tools.j02_run_config import write_config

    def forbid_runtime(*args, **kwargs):
        raise AssertionError('offline file binding must not contact or run a target')

    monkeypatch.setattr(socket, 'socket', forbid_runtime)
    monkeypatch.setattr(subprocess, 'Popen', forbid_runtime)
    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE')
    pol = policy(pack.ref(), scope={'include': ['SUP-05']}, values={})
    policy_path = external / 'policy.json'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    config = {'executors': {'supply_boot': 'kubernetes'},
              'update': {'mode': 'external', 'allowed_checks': ['SUP-05']},
              'check_config': {'SUP-05': {NORMAL_FILE_READS_OPTION: True}}, 'sides': {}}
    config_path = external / 'guard.toml'
    entries = []
    model = 'example/approved'
    files = [{'path': 'model.safetensors', 'bytes': 7, 'sha256': sha256_hex(b'EXAMPLE')}]
    inventory = sha256_hex(canonical_json(files))
    reference = {'schema': 'guard.model-manifest/1', 'scope': 'all', 'model_id': model,
                 'files': files, 'inventory_sha256': inventory}

    for index, (side, release) in enumerate(tool.versions().items(), 1):
        root = external / side
        root.mkdir()
        ref_path = root / 'reference.json'
        ref_path.write_bytes(canonical_json(reference))
        env = environment(id=f'ENV-EXAMPLE-SUP05-{side.upper()}',
            engine={'name': release['engine'], 'version': release['version'], 'image_digest': release['image']},
            runtime={'platform': 'fixture; engine_requested=V1', 'gpu_count': 1,
                     'gpu_model': 'EXAMPLE GPU', 'driver': 'EXAMPLE driver'},
            model={'id': model, 'revision': 'approved-example', 'inventory_sha256': inventory})
        env_path = root / 'env.json'
        env_path.write_bytes(canonical_json(env.to_dict()))
        supplied = sampling_inputs()['kubernetes']
        engine = {**supplied['engine'], 'image': release['image'], 'image_id': release['image'],
                  'pod_uid': f'10000000-0000-0000-0000-{index:012d}', 'container_id': 'containerd://' + str(index) * 64}
        target = {'schema': 'guard-kubernetes-lifecycle-target/1', 'lab': True, 'run_id': supplied['run_id'],
                  'environment_ref': env.ref().model_dump(mode='json'),
                  'kubernetes': {'context': supplied['context'], 'engine': engine}}
        target_path = root / 'target.json'
        target_path.write_bytes(canonical_json(target))
        scope = build_scope(env, target, ref_path, '/models', nonsecret_model_weights=True, hash_model_bytes=True)
        scope_path = root / 'scope.json'
        scope_path.write_bytes(canonical_json(scope))
        profile = PROFILES[release['version']]
        if side == 'candidate' and candidate_input == 'foreign-release':
            profile = PROFILES[tool.versions()['baseline']['version']]
        assets = root / 'assets'
        prepare_assets(assets, tool.ROOT.parent, profile=profile, model_read_scope=scope_path)
        if side == 'candidate' and candidate_input == 'foreign-target':
            target_path = external / 'baseline' / 'target.json'
        if side == 'candidate' and candidate_input == 'reference-changed':
            ref_path.write_bytes(ref_path.read_bytes() + b'\n')
        config['sides'][side] = {'supply_boot': {'normal': {'target': str(target_path),
            'prepared': str(assets / 'manifest.json'), 'reference_manifest': str(ref_path)}}}
        entries.append((side, env_path))

    write_config(config, config_path)
    inputs = [{'check_id': 'SUP-05', 'side': side, **{
        role: {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}
        for role, path in {'environment': env, 'policy': policy_path, 'configuration': config_path}.items()}}
        for side, env in entries]
    result = run(tmp_path, {**blank(), 'inputs': inputs})
    assert len(result['rows']) == 138
    rows = {row['side']: row for row in result['rows'] if row['check_id'] == 'SUP-05'}
    for side, row in rows.items():
        assert row['ready_for_execution'] is row['live_target_verified'] is row['engine_executed'] is False
        if side == 'candidate' and gap is not None:
            assert not row['file_binding_valid'] and row['blocking_codes'] == [gap]
        else:
            assert row['file_binding_valid'] and row[NORMAL_FILE_READS_OPTION]
            assert row['binding_schema'] == 'guard-run-binding/1'
            assert set(row['blocking_codes']) == {'EXAMPLE_ENV_NOT_ACTUAL', 'LIVE_EXECUTOR_PREFLIGHT_REQUIRED'}
