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
from tests.test_kubernetes_record_access import context as record_access_context  # noqa: F401
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


@pytest.mark.parametrize(('unused_check', 'unused_executor'), [
    (None, None), ('LIF-04', 'stream_buffer'), ('REQ-10', 'sampling_validation'),
])
def test_example_file_binding_does_not_issue_live_readiness(tmp_path, sources, unused_check, unused_executor):  # noqa: F811
    value = blank();value['inputs'] = sources['inputs']
    if unused_check:
        import tomllib
        from tools.j02_run_config import write_config

        pointer = value['inputs'][0]['configuration']
        path = Path(pointer['path'])
        config = tomllib.loads(path.read_text(encoding='utf-8'))
        config[unused_executor] = 'EXAMPLE-unusable-section'
        config.setdefault('check_config', {})[unused_check] = {'max_duration_seconds': 120}
        write_config(config, path)
        pointer['sha256'] = sha256(path.read_bytes()).hexdigest()
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


@pytest.mark.parametrize(('check_id', 'candidate_input', 'gap'), [
    ('LIF-07', 'valid', None),
    ('LIF-09', 'valid', None),
    ('LIF-07', 'lab-false', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('LIF-09', 'wrong-model', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('LIF-07', 'scenario-changed', 'RUN_BINDING_INITIALIZATION_SCENARIO_CHANGED'),
    ('LIF-09', 'prepared-rewritten', 'INITIALIZATION_CONFIGURATION_INVALID'),
    ('LIF-07', 'prepared-pin', None),
    ('LIF-09', 'prepared-pin-replaced', 'MVP_INPUT_HASH_MISMATCH'),
])
def test_initialization_readiness_uses_the_declared_startup_inputs(
        tmp_path, external, monkeypatch, check_id, candidate_input, gap):
    from guard import application
    from guard.models import canonical_json, sha256_hex
    from tests.test_initialization_preparation import _paired_inputs
    from tools.j02_run_config import write_config

    def no_runtime(*args, **kwargs):
        pytest.fail('Initialization file readiness must not contact or run a target')

    monkeypatch.setattr(socket, 'socket', no_runtime)
    monkeypatch.setattr(subprocess, 'Popen', no_runtime)
    pack, envs, pol, config = _paired_inputs(external, check_id, None)
    policy_path = external / 'policy.json'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    config_path = external / 'guard.toml'
    entries = []
    for index, (side, env) in enumerate(zip(('baseline', 'candidate'), envs)):
        env = env.model_copy(update={'runtime': env.runtime.model_copy(update={
            'gpu_count': 1, 'gpu_model': 'EXAMPLE GPU', 'driver': 'EXAMPLE driver',
        })}).seal()
        envs[index] = env
        env_path = external / (side + '-env.json')
        env_path.write_bytes(canonical_json(env.to_dict()))
        path = Path(config['sides'][side]['initialization']['target'])
        target = json.loads(path.read_bytes())
        target['environment_ref'] = env.ref().model_dump(mode='json')
        if side == 'candidate':
            if candidate_input == 'lab-false':
                target['lab'] = False
            elif candidate_input == 'wrong-model':
                target['model_mapping']['revision'] = 'EXAMPLE other model revision'
        path.write_bytes(canonical_json(target))
        entries.append((side, env_path))

    ready = application.update_readiness(pack, envs, pol, config)
    inputs = next(row for row in ready['checks'] if row['check'] == 'inputs')
    static_failure = candidate_input in {'lab-false', 'wrong-model'}
    assert inputs['ok'] is (not static_failure), ready
    if static_failure:
        assert inputs['detail'] == gap
    assert all(not row['runtime_qualified'] for row in ready['check_support'])

    if candidate_input in {'scenario-changed', 'prepared-rewritten'}:
        bind = tool.run_binding.effective_values
        scenario = Path(config['run_binding'][check_id]['scenario'])
        manifest = Path(config['sides']['candidate']['initialization']['prepared'])

        def change_during_binding(*args, **kwargs):
            candidate = kwargs['side'] == 'candidate'
            if candidate and candidate_input == 'scenario-changed':
                scenario.write_bytes(canonical_json({'case': 'EXAMPLE substitute scenario'}))
            result = bind(*args, **kwargs)
            if candidate and candidate_input == 'prepared-rewritten':
                manifest.write_bytes(manifest.read_bytes() + b'\n')
            return result

        monkeypatch.setattr(tool.run_binding, 'effective_values', change_during_binding)
    write_config(config, config_path)
    index = tool.template(pack)
    index['inputs'] = [{'check_id': check_id, 'side': side, **{
        role: {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}
        for role, path in {'environment': env, 'policy': policy_path, 'configuration': config_path}.items()
    }} for side, env in entries]
    prepared_refs = {}
    if candidate_input in {'prepared-pin', 'prepared-pin-replaced'}:
        for row in index['inputs']:
            side = row['side']
            prepared = Path(config['sides'][side]['initialization']['prepared'])
            raw = prepared.read_bytes()
            prepared_refs[side] = row['initialization_prepared'] = {'path': str(prepared), 'sha256': sha256_hex(raw)}
            if side == 'candidate' and candidate_input == 'prepared-pin-replaced':
                prepared.write_bytes(raw + b'\n')
    report = run(tmp_path, index)
    assert report['sides'] == 138 and report['active_checks'] == 69
    assert report['file_binding_valid_sides'] == (2 if gap is None else 1)
    for row in report['rows']:
        if row['check_id'] != check_id:
            continue
        assert row['engine_executed'] is row['live_target_verified'] is row['ready_for_execution'] is False
        if row['side'] == 'candidate' and gap is not None:
            assert row['blocking_codes'] == [gap]
        else:
            assert row['binding_schema'] == 'guard-run-binding/2'
            assert set(row['blocking_codes']) == {'EXAMPLE_ENV_NOT_ACTUAL', 'LIVE_EXECUTOR_PREFLIGHT_REQUIRED'}
            if row['side'] in prepared_refs:
                assert row['files']['initialization_prepared'] == prepared_refs[row['side']]


@pytest.mark.parametrize('candidate_wrong_owner', [False, True])
def test_obs11_validates_the_declared_status_probe_in_both_preflights(tmp_path, monkeypatch, candidate_wrong_owner):
    from guard import application
    from guard.models import canonical_json, sha256_hex
    from tests.builders import environment, policy
    from tests.test_kubernetes_log_integrity import GAP_VALUES
    from tests.test_kubernetes_request_binding import target as owned_target
    from tests.test_model_mapping import declared_mapping
    from tools.j02_run_config import write_config
    from tools.mvp_versions import versions

    def forbidden(*args, **kwargs):
        pytest.fail('OBS-11 preparation must not access the declared status probe')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    check_id = 'OBS-11'
    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE')
    values = {key: value for key, value in GAP_VALUES.items() if key != 'collection_session'}
    pol = policy(pack.ref(), scope={'include': [check_id]}, values={check_id: values})
    cfg = {'adapter': {'name': 'vllm'}, 'executors': {'log_integrity': 'kubernetes'},
           'update': {'allowed_checks': [check_id]}, 'sides': {}}
    envs, env_paths = [], {}
    model_env, mapping = declared_mapping()
    for index, (side, row) in enumerate(versions().items(), start=1):
        env = environment(id=f'ENV-OBS11-{side.upper()}', model=model_env.model,
            engine={'name': row['engine'], 'version': row['version'], 'image_digest': row['image']},
            runtime={'gpu_count': 1, 'gpu_model': 'EXAMPLE', 'driver': 'EXAMPLE'})
        declaration = owned_target(env)
        declaration['model_mapping'] = {**mapping, 'environment_ref': env.ref().model_dump(mode='json')}
        declaration['observed_version'] = row['version']
        declaration['engine']['image_digest'] = row['image']
        declaration['kubernetes']['engine'].update(image=row['image'], image_id=row['image'])
        if side == 'candidate' and candidate_wrong_owner:
            declaration['kubernetes']['probe']['pod_uid'] = declaration['kubernetes']['engine']['pod_uid']
        target_path = tmp_path / f'{side}-target.json'
        target_path.write_bytes(canonical_json(declaration))
        cfg['sides'][side] = {'check_config': {check_id: {'target': str(target_path)}},
                             'run_binding': {check_id: {'record_session': f'{index:032x}'}}}
        env_path = tmp_path / f'{side}-env.json'
        env_path.write_bytes(canonical_json(env.to_dict()))
        envs.append(env)
        env_paths[side] = env_path

    ready = application.update_readiness(pack, envs, pol, cfg)
    inputs = next(row for row in ready['checks'] if row['check'] == 'inputs')
    assert inputs['ok'] is not candidate_wrong_owner, ready
    if candidate_wrong_owner:
        assert inputs['detail'] == 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'

    policy_path, config_path = tmp_path / 'policy.json', tmp_path / 'guard.toml'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    write_config(cfg, config_path)
    index = tool.template(pack)
    index['inputs'] = [{'check_id': check_id, 'side': side, **{
        key: {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}
        for key, path in {'environment': env, 'policy': policy_path, 'configuration': config_path}.items()
    }} for side, env in env_paths.items()]
    report = run(tmp_path, index)
    assert report['sides'] == 138 and report['file_binding_valid_sides'] == (1 if candidate_wrong_owner else 2)
    for row in report['rows']:
        if row['check_id'] != check_id:
            continue
        assert row['engine_executed'] is row['live_target_verified'] is row['ready_for_execution'] is False
        if row['side'] == 'candidate' and candidate_wrong_owner:
            assert row['blocking_codes'] == ['KUBERNETES_HTTP_BINDING_UNAVAILABLE']
        else:
            assert row['file_binding_valid'] and row['binding_schema'] == 'guard-run-binding/2'


@pytest.mark.parametrize(('check_id', 'executor_name', 'damage', 'reason'), [
    ('OBS-04', 'boundary_egress', None, None),
    ('OBS-04', 'boundary_egress', 'model', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('OBS-04', 'boundary_egress', 'duration', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('PLT-06', 'unix_ipc', None, None),
    ('PLT-06', 'unix_ipc', 'model', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('PLT-06', 'unix_ipc', 'duration', 'KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID'),
    ('PLT-02', 'unix_ipc', None, None),
    ('PLT-02', 'unix_ipc', 'model', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('PLT-02', 'unix_ipc', 'duration', 'KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID'),
    ('OBS-02', 'observation_state', None, None),
    ('OBS-02', 'observation_state', 'duration', 'OBS02_NORMAL_TARGET_UNVERIFIED'),
    ('REQ-09', 'input_validation', None, None),
    ('REQ-09', 'input_validation', 'duration', 'KUBERNETES_INPUT_NORMAL_TARGET_UNVERIFIED'),
    ('REQ-10', 'sampling_validation', None, None),
    ('REQ-10', 'sampling_validation', 'model', 'KUBERNETES_SAMPLING_NORMAL_TARGET_UNVERIFIED'),
    ('REQ-10', 'sampling_validation', 'missing-scope', 'NORMAL_SAMPLING_EXECUTION_SCOPE_INVALID'),
    ('LIF-03', 'stream_buffer', None, None),
    ('LIF-03', 'stream_buffer', 'duration', 'LIF03_NORMAL_TARGET_UNVERIFIED'),
    ('LIF-04', 'stream_buffer', None, None),
    ('LIF-04', 'stream_buffer', 'duration', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('LIF-04', 'stream_buffer', 'missing-scope', 'NORMAL_STREAM_EXECUTION_SCOPE_INVALID'),
    ('SUP-10', 'supply_boot', None, None),
    ('SUP-10', 'supply_boot', 'duration', 'SUP10_INVENTORY_TARGET_UNVERIFIED'),
])
def test_prebound_owned_preflights_share_the_declared_target_limits(
        tmp_path, monkeypatch, check_id, executor_name, damage, reason):
    from guard import application
    from guard.configuration import KUBERNETES_BOUND_CHECK_OPTIONS
    from guard.executors.http_common import HttpCheckExecutor
    from guard.models import canonical_json, sha256_hex
    from tests.builders import environment, policy
    from tests.test_input_matrix import values as input_values
    from tests.test_kubernetes_request_binding import target as owned_target
    from tests.test_kubernetes_usage_report import LOCAL_VALUES
    from tests.test_kubernetes_worker_trust_metadata import VALUES as WORKER_VALUES
    from tests.test_lif04_normal_stream import configuration as stream_configuration
    from tools.j02_run_config import write_config
    from tools.mvp_versions import versions

    def forbidden(*args, **kwargs):
        pytest.fail('Scoped preparation must not access credentials or the declared Pod')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE')
    policy_values = {'OBS-04': LOCAL_VALUES, 'PLT-06': WORKER_VALUES,
                     'SUP-10': {},
                     'PLT-02': {'protected_unix_paths': ['/run/fixture/engine.sock'], 'allowed_ipc_uids': ['1000']}}
    if check_id == 'REQ-09':
        policy_values[check_id] = input_values()
    elif check_id == 'REQ-10':
        policy_values[check_id] = {'allowed_sampling_options': ['max_tokens', 'temperature'],
                                   'rejection_codes': ['400', '422']}
    elif check_id == 'OBS-02':
        policy_values[check_id] = {'observation_state_required_components': ['http_listener'],
                                  'profiler_backend_kind': 'torch', 'profiler_required_ranks': ['0']}
    elif check_id in {'LIF-03', 'LIF-04'}:
        _, _, _, effective = stream_configuration(tmp_path)
        policy_values[check_id] = {field.key: effective[field.key] for field in pack.checks[check_id].policy_params}
    if check_id in {'REQ-10', 'LIF-04', 'PLT-02'}:
        monkeypatch.setattr(HttpCheckExecutor, 'keys', forbidden)
    pol = policy(pack.ref(), scope={'include': [check_id]}, values={check_id: policy_values[check_id]})
    option = {check_id: {KUBERNETES_BOUND_CHECK_OPTIONS[check_id]: True}}
    cfg = {'adapter': {'name': 'vllm'}, 'executors': {executor_name: 'kubernetes'},
           'update': {'allowed_checks': [check_id]}, 'sides': {}}
    if damage != 'missing-scope':
        cfg['check_config'] = option
    envs, env_paths = [], {}
    for side, row in versions().items():
        env = environment(id=f'ENV-OWNED-METADATA-{side.upper()}', model={'id': 'EXAMPLE', 'revision': 'r1'},
            engine={'name': row['engine'], 'version': row['version'], 'image_digest': row['image']},
            runtime={'gpu_count': 1, 'gpu_model': 'EXAMPLE', 'driver': 'EXAMPLE'})
        declaration = owned_target(env)
        declaration.update(lab=False, observed_version=row['version'])
        declaration['engine']['image_digest'] = row['image']
        declaration['kubernetes']['engine'].update(image=row['image'], image_id=row['image'])
        target_path = tmp_path / f'{side}-target.json'
        settings = {'target': str(target_path)}
        if side == 'candidate' and damage == 'model':
            declaration['model_mapping']['revision'] = 'EXAMPLE-other-revision'
        if side == 'candidate' and damage == 'duration':
            settings['max_duration_seconds'] = 0
        target_path.write_bytes(canonical_json(declaration))
        cfg['sides'][side] = {executor_name: settings}
        if side == 'baseline' and damage == 'missing-scope':
            cfg['sides'][side]['check_config'] = option
        env_path = tmp_path / f'{side}-env.json'
        env_path.write_bytes(canonical_json(env.to_dict()))
        envs.append(env)
        env_paths[side] = env_path

    ready = application.update_readiness(pack, envs, pol, cfg)
    inputs = next(row for row in ready['checks'] if row['check'] == 'inputs')
    assert inputs['ok'] is (reason is None), ready
    if reason:
        assert inputs['detail'] == reason

    policy_path, config_path = tmp_path / 'policy.json', tmp_path / 'guard.toml'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    write_config(cfg, config_path)
    index = tool.template(pack)
    index['inputs'] = [{'check_id': check_id, 'side': side, **{
        key: {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}
        for key, path in {'environment': env, 'policy': policy_path, 'configuration': config_path}.items()
    }} for side, env in env_paths.items()]
    report = run(tmp_path, index)
    invalid_sides = {'candidate'} if reason else set()
    if damage == 'missing-scope':
        invalid_sides.add('baseline')
    assert report['sides'] == 138 and report['file_binding_valid_sides'] == 2 - len(invalid_sides)
    for row in report['rows']:
        if row['check_id'] != check_id:
            continue
        assert row['engine_executed'] is row['live_target_verified'] is row['ready_for_execution'] is False
        assert row['actual_results'] is None
        if row['side'] in invalid_sides:
            expected_codes = [reason]
            if check_id == 'REQ-09' and damage == 'duration':
                expected_codes += [
                    'NATIVE_INPUT_PROFILE_UNAVAILABLE',
                    'NATIVE_REJECTION_PROFILE_UNAVAILABLE',
                    'NATIVE_INPUT_CAPTURE_GPU_UNQUALIFIED',
                    'NATIVE_REJECTION_PROJECTION_GPU_UNQUALIFIED',
                ]
            assert row['blocking_codes'] == expected_codes
        else:
            assert row['file_binding_valid'] and 'EXAMPLE_ENV_NOT_ACTUAL' in row['blocking_codes']


def _record_declarations(tmp_path, record_access_context, check_id, damage=None):
    from guard.configuration import RETRY_RECORD_SNAPSHOT_OPTION
    from guard.executors import credential_copy_contract as copy
    from guard.executors.observation_contract import MANDATORY_FIELDS
    from guard.executors.retry_record_contract import DECISION_KEY as RETRY_DECISION_KEY
    from guard.models import canonical_json, sha256_hex
    from tests.builders import environment, policy
    from tests.test_a1_credential_copy_contract import example as copy_example
    from tests.test_a1_retry_record_contract import example as retry_example
    from tests.test_kubernetes_request_binding import target as http_target
    from tools.mvp_versions import versions

    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE')
    check = pack.checks[check_id]
    record_access = check_id == 'OBS-06'
    retry_records = check_id == 'LIF-12'
    values = {}
    if check_id == 'PLT-12':
        inventory, copy_values, _, _ = copy_example()
        values = {copy.DECISION_KEY: copy.treatments(inventory),
                  'copy_max_record_delay_ms': copy_values['copy_max_record_delay_ms']}
        inventory['policy_sha256'] = sha256_hex(canonical_json(values))
    elif check_id == 'OBS-01':
        values = {'observation_required_events': ['ACCESS_ALLOWED'],
                  'observation_required_fields': sorted(MANDATORY_FIELDS), 'observation_max_delay_ms': 300000}
    elif retry_records:
        retry_scope, retry_values, _, _ = retry_example()
        values = {key: retry_values[key] for key in ('retry_policy_mode', 'retry_max_record_delay_ms')}
        values[RETRY_DECISION_KEY] = retry_scope['limits']
    pol = policy(pack.ref(), scope={'include': [check_id]}, values={check_id: values})
    cfg = {'adapter': {'name': 'vllm'}, 'executors': {check.executor: 'kubernetes'},
           'update': {'allowed_checks': [check_id]}, 'sides': {}}
    envs, env_paths = [], {}
    for number, (side, row) in enumerate(versions().items(), start=1):
        model = {'id': 'EXAMPLE', 'revision': 'r1'}
        if retry_records:
            model['inventory_sha256'] = retry_scope['model_inventory_sha256']
        env = environment(id=f'ENV-RECORD-DECLARATION-{side.upper()}', model=model,
            engine={'name': row['engine'], 'version': row['version'], 'image_digest': row['image']},
            runtime={'gpu_count': 1, 'gpu_model': 'EXAMPLE', 'driver': 'EXAMPLE'})
        ref = env.ref().model_dump(mode='json')
        target_path = tmp_path / f'{side}-target.json'
        settings = {'target': str(target_path)}
        producer = tmp_path / f'{side}-producer-identity.txt'
        producer.write_text('' if side == 'candidate' and damage == 'empty_producer'
                            else f'EXAMPLE producer identity for {side}\n', encoding='utf-8')
        run_cfg = {'record_session': f'{number:032x}', 'record_producer': str(producer)}
        if record_access:
            target = deepcopy(record_access_context.target)
            target['environment_ref'] = ref
            target['kubernetes']['engine'].update(image=row['image'], image_id=row['image'])
            subjects = deepcopy(record_access_context.subjects)
            subjects.update(environment_ref=ref, target_sha256=sha256_hex(canonical_json(target)),
                            policy_ref=pol.ref().model_dump(mode='json'))
            if side == 'candidate' and damage == 'policy':
                subjects['policy_ref'] = record_access_context.ref.model_dump(mode='json')
            subjects_path = tmp_path / f'{side}-subjects.json'
            subjects_path.write_bytes(canonical_json(subjects))
            settings['subjects'] = str(subjects_path)
        else:
            target = http_target(env)
            target.update(lab=retry_records and damage != 'normal-snapshot', observed_version=row['version'])
            target['engine']['image_digest'] = row['image']
            target['kubernetes']['engine'].update(image=row['image'], image_id=row['image'])
            if side == 'candidate' and damage == 'model':
                target['model_mapping']['revision'] = 'EXAMPLE-foreign-revision'
            if check_id == 'PLT-12':
                inventory_path = tmp_path / f'{side}-inventory.json'
                inventory_path.write_bytes(canonical_json(inventory))
                settings.update(copy_inventory=str(inventory_path), copy_record_snapshot_only=True)
            elif check_id == 'OBS-01':
                settings['record_snapshot_only'] = True
                run_cfg['test_actors'] = {'allowed': 'd'*64, 'denied': 'e'*64}
            elif retry_records:
                scope = {**retry_scope, 'run_id': target['kubernetes']['run_id'], 'model_revision': env.model.revision,
                         'image_digest': row['image'].rsplit('@', 1)[1], 'policy_sha256': sha256_hex(canonical_json(values)),
                         'producer_source_sha256': sha256_hex(producer.read_bytes())}
                if side == 'candidate' and damage == 'scope':
                    scope['run_id'] = 'example-other-run'
                scope_path = tmp_path / f'{side}-retry-scope.json'
                scope_path.write_bytes(canonical_json(scope))
                settings.update(retry_scope=str(scope_path), prepared=str(tmp_path / 'unused-legacy-prepared.json'))
                if damage == 'normal-snapshot':
                    settings[RETRY_RECORD_SNAPSHOT_OPTION] = True
        target_path.write_bytes(canonical_json(target))
        cfg['sides'][side] = {check.executor: settings, 'run_binding': {check_id: run_cfg}}
        env_path = tmp_path / f'{side}-env.json'
        env_path.write_bytes(canonical_json(env.to_dict()))
        envs.append(env)
        env_paths[side] = env_path
    return pack, envs, pol, cfg, env_paths


def _no_record_collection(*args, **kwargs):
    pytest.fail('Record declaration checks must not contact the target')


@pytest.mark.parametrize(('check_id', 'damage', 'reason'), [
    ('OBS-01', None, None),
    ('OBS-01', 'model', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('LIF-12', None, None),
    ('LIF-12', 'normal-snapshot', None),
    ('LIF-12', 'scope', 'RETRY_CONFIGURATION_INVALID'),
    ('LIF-12', 'scope_changed', 'MVP_INPUT_FILE_CHANGED'),
    ('OBS-06', None, None),
    ('OBS-06', 'policy', 'RECORD_ACCESS_CONFIGURATION_INVALID'),
    ('OBS-06', 'producer_changed', 'MVP_INPUT_FILE_CHANGED'),
    ('OBS-06', 'empty_producer', None),
    ('PLT-12', None, None),
    ('PLT-12', 'model', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('PLT-12', 'inventory_changed', 'MVP_INPUT_FILE_CHANGED'),
])
def test_record_declarations_match_runtime_input_contracts(
        tmp_path, monkeypatch, record_access_context, check_id, damage, reason):
    from guard import application
    from guard.executors.http_common import HttpCheckExecutor
    from guard.executors.kubernetes_observation import KubernetesRequiredEventExecutor
    from guard.executors.kubernetes_record_access_product import KubernetesRecordAccessExecutor
    from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor
    from guard.models import canonical_json, sha256_hex
    from tools.j02_run_config import write_config

    monkeypatch.setattr(socket, 'socket', _no_record_collection)
    monkeypatch.setattr(subprocess, 'Popen', _no_record_collection)
    monkeypatch.setattr(HttpCheckExecutor, 'keys', _no_record_collection)
    pack, envs, pol, cfg, env_paths = _record_declarations(tmp_path, record_access_context, check_id, damage)
    ready = application.update_readiness(pack, envs, pol, cfg)
    inputs = next(row for row in ready['checks'] if row['check'] == 'inputs')
    assert inputs['ok'] is (reason in {None, 'MVP_INPUT_FILE_CHANGED'}), ready
    if not inputs['ok']:
        assert inputs['detail'] == reason

    if reason == 'MVP_INPUT_FILE_CHANGED':
        if check_id == 'OBS-06':
            executor, method = KubernetesRecordAccessExecutor, 'prepared_record_access'
            candidate_file = tmp_path / 'candidate-producer-identity.txt'
        elif check_id == 'LIF-12':
            executor, method = KubernetesRequestTerminationExecutor, 'prepared_retry_records'
            candidate_file = tmp_path / 'candidate-retry-scope.json'
        else:
            executor, method = KubernetesRequiredEventExecutor, 'prepared_copy_record_snapshot'
            candidate_file = tmp_path / 'candidate-inventory.json'
        prepare = getattr(executor, method)

        def replace_after_validation(self, check, env, values, **kwargs):
            prepare(self, check, env, values, **kwargs)
            if env.ref() == envs[1].ref():
                candidate_file.write_bytes(candidate_file.read_bytes() + b'\n')

        monkeypatch.setattr(executor, method, replace_after_validation)
    policy_path, config_path = tmp_path / 'policy.json', tmp_path / 'guard.toml'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    write_config(cfg, config_path)
    index = tool.template(pack)
    index['inputs'] = [{'check_id': check_id, 'side': side, **{
        key: {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}
        for key, path in {'environment': env, 'policy': policy_path, 'configuration': config_path}.items()
    }} for side, env in env_paths.items()]
    report = run(tmp_path, index)
    assert report['sides'] == 138 and report['file_binding_valid_sides'] == (2 if reason is None else 1)
    for row in report['rows']:
        if row['check_id'] != check_id:
            continue
        assert row['engine_executed'] is row['live_target_verified'] is row['ready_for_execution'] is False
        assert row['actual_results'] is None
        if row['side'] == 'candidate' and reason:
            assert row['blocking_codes'] == [reason]
        else:
            assert row['file_binding_valid'] and row['binding_schema'] == 'guard-run-binding/2'
            assert 'EXAMPLE_ENV_NOT_ACTUAL' in row['blocking_codes']


@pytest.mark.parametrize("case", [
    "audit", "unsealed", "changed-under-lock", "exact",
    "scope-before-lock", "producer-before-lock", "scope-under-lock", "producer-under-lock",
])
def test_retry_snapshot_uses_only_verified_paired_recipe_bytes(tmp_path, monkeypatch, record_access_context, case):
    from guard import application
    from guard.core import run as core
    from guard.core.execution_recipe import bind_recipe
    from guard.core.select import select
    from guard.executors.base import EvidenceDraft
    from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor
    from guard.models import DataOrigin, ExecStatus, Mode, Side, canonical_json, sha256_hex, utcnow
    from guard.store import Store
    from guard.store.packs import put_pack

    pack, envs, pol, cfg, _ = _record_declarations(tmp_path, record_access_context, "LIF-12", "normal-snapshot")
    selected_envs = envs[:1] if case == "audit" else envs
    mode = Mode.audit if case == "audit" else Mode.update
    store = Store(tmp_path / "store")
    put_pack(store, pack.path)
    plan = select(pack, selected_envs, pol, mode=mode)
    if case not in {"audit", "unsealed"}:
        plan = bind_recipe(plan, cfg, selected_envs)
    store.put_many([*selected_envs, pol, plan])
    candidate = cfg["sides"]["candidate"]
    target_path = Path(candidate["request_termination"]["target"])
    scope_path = Path(candidate["request_termination"]["retry_scope"])
    producer_path = Path(candidate["run_binding"]["LIF-12"]["record_producer"])
    digest = sha256_hex(canonical_json(json.loads(target_path.read_bytes())))
    called = []

    def reader(executor, check, env, values, adapter, workdir):
        assert case == "exact"
        called.append(env.ref())
        assert executor.expected_target_sha256 == values["retry_target_sha256"] == digest
        assert values["retry_scope_sha256"] == sha256_hex(scope_path.read_bytes())
        assert values["retry_record_source_sha256"] == sha256_hex(producer_path.read_bytes())
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={"attempted": False})

    monkeypatch.setattr(KubernetesRequestTerminationExecutor, "run", reader)
    monkeypatch.setattr(socket, "socket", _no_record_collection)
    monkeypatch.setattr(subprocess, "Popen", _no_record_collection)
    rc = application.run_config(cfg)
    if case == "audit":
        managed = {'executors': {'request_termination': 'real'}, 'adapter': cfg['adapter'],
                   'check_config': {'LIF-12': {'retry_record_snapshot_only': True}}}
        monkeypatch.setattr(application, '_managed_config', _no_record_collection)
        with pytest.raises(ValueError, match="^RETRY_RECORD_SNAPSHOT_SCOPE_INVALID$"):
            application.execute_validation(store, pack, envs[0], pol, managed, tmp_path / 'managed', utcnow())
        assert not (tmp_path / 'managed').exists()
        with pytest.raises(ValueError, match="^RETRY_RECORD_SNAPSHOT_SCOPE_INVALID$"):
            core.run_plan(store, pack, plan, rc)
        assert not called and not list(store.iter_kind("EVD"))
        return

    if case.endswith("-before-lock"):
        path = scope_path if case.startswith("scope-") else producer_path
        path.write_bytes(path.read_bytes() + b'\n')
        with pytest.raises(ValueError, match="^EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED$"):
            core.run_plan(store, pack, plan, rc, only_side=Side.candidate)
        assert not called and not list(store.iter_kind("EVD"))
        return

    if case.endswith("-under-lock"):
        original = core._run_one

        def change_before_binding(*args, **kwargs):
            if case == "changed-under-lock":
                target = json.loads(target_path.read_bytes())
                target["model_mapping"]["api_model"] = "fixture-alternate-model"
                target_path.write_bytes(canonical_json(target))
            else:
                path = scope_path if case.startswith("scope-") else producer_path
                path.write_bytes(path.read_bytes() + b'\n')
            return original(*args, **kwargs)

        monkeypatch.setattr(core, "_run_one", change_before_binding)

    evd, = core.run_plan(store, pack, plan, rc, only_side=Side.candidate)
    if case == "exact":
        assert called == [envs[1].ref()]
        assert evd.execution.status is ExecStatus.COMPLETED and evd.data_origin is DataOrigin.EXAMPLE
        assert evd.execution.run_binding["files"]["retry_target_sha256"] == digest
    else:
        assert not called and evd.execution.status is ExecStatus.NOT_RUN
        assert evd.execution.abort_reason == {
            "unsealed": "RUN_BINDING_RETRY_SNAPSHOT_TARGET_UNAVAILABLE",
            "changed-under-lock": "RUN_BINDING_RETRY_SNAPSHOT_TARGET_CHANGED",
            "scope-under-lock": "RUN_BINDING_RETRY_SNAPSHOT_FILES_CHANGED",
            "producer-under-lock": "RUN_BINDING_RETRY_SNAPSHOT_FILES_CHANGED",
        }[case]
        assert evd.execution.run_binding is None


@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('source', ['target', 'subjects', 'record_producer'])
def test_obs06_rejects_changed_plan_inputs_before_collection(
        tmp_path, monkeypatch, record_access_context, late, source):
    from guard import application
    from guard.core import run as core_run
    from guard.core.execution_recipe import bind_recipe
    from guard.core.select import select
    from guard.executors.kubernetes_record_access_product import KubernetesRecordAccessExecutor
    from guard.models import ExecStatus, Mode, Side, canonical_json
    from guard.store import Store
    from guard.store.packs import put_pack

    pack, envs, pol, cfg, _ = _record_declarations(tmp_path, record_access_context, 'OBS-06')
    store = Store(tmp_path / 'store')
    put_pack(store, pack.path)
    plan = bind_recipe(select(pack, envs, pol, mode=Mode.update), cfg, envs)
    store.put_many([*envs, pol, plan])
    candidate = cfg['sides']['candidate']

    if source == 'target':
        path = Path(candidate['record_access']['target'])
        target = json.loads(path.read_bytes())
        target['kubernetes']['engine']['restart_count'] += 1
        replacement = canonical_json(target)
        reason = 'RUN_BINDING_RECORD_ACCESS_TARGET_CHANGED'
    elif source == 'subjects':
        path = Path(candidate['record_access']['subjects'])
        subjects = json.loads(path.read_bytes())
        subjects['subjects'][0]['username'] = 'example-replacement'
        replacement = canonical_json(subjects)
        reason = 'RUN_BINDING_RECORD_ACCESS_SUBJECTS_CHANGED'
    else:
        path = Path(candidate['run_binding']['OBS-06']['record_producer'])
        replacement = path.read_bytes() + b'\n'
        reason = 'RUN_BINDING_RECORD_ACCESS_PRODUCER_CHANGED'

    monkeypatch.setattr(KubernetesRecordAccessExecutor, 'run', _no_record_collection)
    monkeypatch.setattr(subprocess, 'Popen', _no_record_collection)
    rc = application.run_config(cfg)
    if late:
        original = core_run._run_one

        def replace_after_recipe(*args, **kwargs):
            path.write_bytes(replacement)
            return original(*args, **kwargs)

        monkeypatch.setattr(core_run, '_run_one', replace_after_recipe)
        evidence, = core_run.run_plan(store, pack, plan, rc, only_side=Side.candidate)
        assert evidence.execution.status == ExecStatus.NOT_RUN
        assert evidence.execution.abort_reason == reason
        assert evidence.execution.run_binding is None
        assert not evidence.artifacts and not evidence.normal_control.attempted
    else:
        path.write_bytes(replacement)
        with pytest.raises(ValueError, match='^EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED$'):
            core_run.run_plan(store, pack, plan, rc, only_side=Side.candidate)
        assert not list(store.iter_kind('EVD'))


@pytest.mark.parametrize('scope', ['selected', 'unselected', 'staged', 'legacy'])
def test_obs06_recipe_keeps_canonical_hash_and_read_scope(tmp_path, record_access_context, scope):
    from guard.core.execution_recipe import configuration_sha256
    from guard.core.select import select
    from guard.executors.run_binding import stages_for
    from guard.models import CheckDef, Mode, canonical_json
    from tests.builders import policy
    import yaml

    pack, envs, pol, cfg, _ = _record_declarations(tmp_path, record_access_context, 'OBS-06')
    if scope == 'unselected':
        pol = policy(pack.ref(), scope={'include': ['OBS-11']}, values={})
    if scope == 'staged':
        cfg['update']['kubernetes_candidate_binding'] = 'after_baseline'
    plan = select(pack, envs, pol, mode=Mode.update)
    if scope == 'legacy':
        legacy_path = tool.ROOT / 'tests/fixtures/run-binding-pack37/checks/OBS-06.yaml'
        legacy = CheckDef.model_validate(yaml.safe_load(legacy_path.read_bytes()))
        assert stages_for(legacy) == (1, 2, 3)
        plan = plan.model_copy(update={'items': [
            item.model_copy(update={'check_revision': legacy.revision}) if item.check_id == legacy.id else item
            for item in plan.items
        ]}).seal()
    original = configuration_sha256(cfg, envs, plan=plan)
    path = tmp_path / 'candidate-subjects.json'
    if scope == 'selected':
        replacement = path.read_bytes() + b'\n'
    else:
        subjects = json.loads(path.read_bytes())
        subjects['subjects'][0]['username'] = 'example-replacement'
        replacement = canonical_json(subjects)
    path.write_bytes(replacement)
    assert configuration_sha256(cfg, envs, plan=plan) == original
