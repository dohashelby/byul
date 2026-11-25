"""엔진 센서 두 판본 입력 결합(tools/engine_sensor_pair_inputs). 엔진·kubectl 없이 정적 결합만 본다."""
import json
from pathlib import Path

import pytest
import yaml

from guard.collectors import engine_callbacks
from guard.collectors.file import load_environment
from guard.executors import engine_callback_projection as projection
from guard.executors import engine_sensor, run_binding
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from tests.builders import baseline_pack
from tests.test_gpu_a_run_inputs import deployed
from tests.test_req16_lab_contexts import CohortTransport
from tools import engine_sensor_pair_inputs as pair
from tools import gpu_a_run_inputs as inputs
from tools import j02_run_config

UPDATE = Path(__file__).resolve().parents[1]
POLICY = {'values': {'REQ-16': {'required_cache_contexts': ['cache_salt']}}}


def side(tmp_path, name, generation='v1-req16-lab', *, kernel_checks=True):
    root = tmp_path / name
    root.mkdir()
    deployed(root, generation)
    inputs.main(['env', '--render', str(root / 'render.json'), '--gpu-model', 'NVIDIA L4', '--driver', 'EXAMPLE-driver',
        '--out', str(root / 'env.json')])
    inputs.main(['target', '--env', str(root / 'env.json'), '--render', str(root / 'render.json'),
        '--namespace', str(root / 'namespace.json'), '--engine-pod', str(root / 'engine-pod.json'),
        '--probe-pod', str(root / 'probe-pod.json'), '--context', 'byul-aws-lab', '--out', str(root / 'target.json')])
    engine_callbacks.prepare_assets(root / 'prepared', lab_kernel_checks=kernel_checks)
    return {'env': root / 'env.json', 'target': root / 'target.json', 'prepared': root / 'prepared'}


def policy_file(tmp_path):
    path = tmp_path / 'policy.yaml'
    path.write_text(yaml.safe_dump(POLICY))
    return path


def test_presets_pass_the_projection_contract():
    for check_id in pair.CHECKS:
        if check_id == 'REQ-15':
            with pytest.raises(ValueError, match='ENGINE_SENSOR_PAIR_CASES_UNAVAILABLE'):
                pair.case_list(check_id)
            continue
        cases = pair.case_list(check_id)
        projection.validate_cases(check_id, cases)
        if pair.lab_contexts(check_id) is not None:
            projection.validate_lab_contexts(pair.lab_contexts(check_id), cases)
    long_case, = (c for c in pair.case_list('REQ-19') if c['case_id'] == 'long')
    assert len(long_case['prompt']) <= 4096 - 33 and long_case['prompt'].count(' a') == 1500


def test_lab_pair_binds_each_side_and_writes_a_j02_fragment(tmp_path):
    sides = {'baseline': side(tmp_path, 'b'), 'candidate': side(tmp_path, 'c')}
    report = pair.pair(sides, tmp_path / 'out', policy_file=policy_file(tmp_path))
    assert report['ready_static_both_sides'] == ['REQ-14', 'REQ-16', 'REQ-18', 'REQ-19', 'REQ-21']
    assert report['ready_for_execution'] is False and report['engine_executed'] is False
    assert report['session_shared_across_sides'] is False and report['engine_restarting_checks'] == ['REQ-21']
    for name in pair.SIDES:
        assert report['rows'][name]['REQ-15']['codes'] == ['REQ15_GATEWAY_INPUTS_REQUIRED']
    pack = load_pack(UPDATE / 'packs/PACK-BASELINE')
    for check_id in report['ready_static_both_sides']:
        rows = [report['rows'][name][check_id] for name in pair.SIDES]
        # 두 판본은 같은 사례 bytes를 쓰고, manifest는 쪽마다 자기 세션·target·ENV에 묶인다.
        assert rows[0]['cases_sha256'] == rows[1]['cases_sha256'] and rows[0]['manifest_sha256'] != rows[1]['manifest_sha256']
        for name, row in zip(pair.SIDES, rows):
            manifest = json.loads(Path(row['executor']['manifest']).read_text())
            prepared = engine_callbacks.load_prepared(sides[name]['prepared'])
            target = json.loads(sides[name]['target'].read_text())
            assert manifest['session'] == prepared['session'] and manifest['target_sha256'] == sha256_hex(canonical_json(target))
            assert ('lab_contexts' in manifest) is (check_id == 'REQ-21')
            assert row['executor']['max_duration_seconds'] == (900 if check_id == 'REQ-21' else 300)
            env = load_environment(sides[name]['env'])
            binding, _ = run_binding.effective_values(pack.checks[check_id], row['executor'], env,
                POLICY['values'].get(check_id, {}), side=name, attempt_id=None, issued_at=utcnow())
            assert binding['files']['sensor_manifest_sha256'] == sha256_hex(canonical_json(manifest))
    spec = yaml.safe_load((tmp_path / 'out/j02-engine-sensor.yaml').read_text())
    config = j02_run_config.build_config(spec)
    assert config['executors'] == {'engine_sensor': 'kubernetes'}
    for name in pair.SIDES:
        assert sorted(config['sides'][name]['check_config']) == report['ready_static_both_sides']
        assert config['sides'][name]['check_config']['REQ-14'] == report['rows'][name]['REQ-14']['executor']


def test_bound_manifest_reaches_the_executor_without_binding_mismatch(tmp_path):
    sides = {'baseline': side(tmp_path, 'b'), 'candidate': side(tmp_path, 'c')}
    report = pair.pair(sides, tmp_path / 'out', checks=('REQ-14',))
    cfg = report['rows']['baseline']['REQ-14']['executor']
    env = load_environment(sides['baseline']['env'])
    check = load_pack(UPDATE / 'packs/PACK-BASELINE').checks['REQ-14']
    _, values = run_binding.effective_values(check, cfg, env, {}, side='baseline', attempt_id=None, issued_at=utcnow())
    executor = engine_sensor.EngineSensorExecutor(cfg)
    manifest = json.loads(Path(cfg['manifest']).read_text())
    executor.transport = CohortTransport(engine_callbacks.load_secret(sides['baseline']['prepared']), [0] * len(manifest['cases']))
    check_def = baseline_pack().checks['REQ-14'].model_copy(deep=True)
    check_def.executor = 'engine_sensor'; check_def.collection_profile = engine_sensor.PROFILES['REQ-14']
    draft = executor.run(check_def, env, values, None, tmp_path / 'work')
    assert draft.abort_reason != 'ENGINE_SENSOR_NATIVE_BINDING_MISMATCH'
    assert len(executor.transport.order) >= len(manifest['cases'])


def test_non_lab_target_and_missing_kernel_checks_stay_missing(tmp_path):
    sides = {'baseline': side(tmp_path, 'b', 'v1-callback', kernel_checks=False),
             'candidate': side(tmp_path, 'c', kernel_checks=False)}
    report = pair.pair(sides, tmp_path / 'out', policy_file=policy_file(tmp_path))
    base, cand = report['rows']['baseline'], report['rows']['candidate']
    assert base['REQ-19']['codes'] == ['LAB_TARGET_REQUIRED', 'LAB_KERNEL_CHECKS_REQUIRED']
    assert base['REQ-21']['codes'] == ['LAB_TARGET_REQUIRED']
    assert cand['REQ-19']['codes'] == ['LAB_KERNEL_CHECKS_REQUIRED'] and cand['REQ-21']['status'] == 'READY_STATIC'
    assert base['REQ-19']['executor'] is None and not (tmp_path / 'out/baseline/REQ-19').exists()
    assert report['ready_static_both_sides'] == ['REQ-14', 'REQ-16', 'REQ-18']
    spec = yaml.safe_load((tmp_path / 'out/j02-engine-sensor.yaml').read_text())
    assert set(spec['sides']['baseline']['checks']) == {'REQ-14', 'REQ-16', 'REQ-18'}


def test_enterprise_policy_value_is_not_filled(tmp_path):
    sides = {'baseline': side(tmp_path, 'b'), 'candidate': side(tmp_path, 'c')}
    report = pair.pair(sides, tmp_path / 'out', checks=('REQ-16',))
    for name in pair.SIDES:
        row = report['rows'][name]['REQ-16']
        assert row['status'] == 'MISSING_INPUT' and row['codes'] == ['POLICY_VALUES_MISSING']
        assert row['missing_policy_keys'] == ['required_cache_contexts'] and row['run_binding_computed'] is True
    assert report['ready_static_both_sides'] == []


def test_one_prepared_session_for_both_versions_is_refused(tmp_path):
    baseline = side(tmp_path, 'b')
    candidate = side(tmp_path, 'c')
    candidate['prepared'] = baseline['prepared']
    report = pair.pair({'baseline': baseline, 'candidate': candidate}, tmp_path / 'out', checks=('REQ-14',))
    assert report['session_shared_across_sides'] is True and report['ready_static_both_sides'] == []
    assert 'PREPARED_SESSION_SHARED_ACROSS_SIDES' in report['rows']['candidate']['REQ-14']['codes']


def test_target_for_another_environment_and_bad_inputs_are_reported(tmp_path):
    baseline = side(tmp_path, 'b')
    candidate = side(tmp_path, 'c')
    candidate['target'] = baseline['target']
    (tmp_path / 'broken').mkdir()
    report = pair.pair({'baseline': baseline, 'candidate': candidate}, tmp_path / 'out', checks=('REQ-14',))
    assert report['rows']['candidate']['REQ-14']['codes'] == ['TARGET_ENVIRONMENT_MISMATCH']
    bad = {'env': tmp_path / 'missing.json', 'target': tmp_path / 'missing-target.json', 'prepared': tmp_path / 'broken'}
    report = pair.pair({'baseline': bad, 'candidate': side(tmp_path, 'd')}, tmp_path / 'out2', checks=('REQ-14',))
    assert report['rows']['baseline']['REQ-14']['codes'] == ['ENV_UNAVAILABLE', 'TARGET_UNAVAILABLE', 'PREPARED_INVALID']
    with pytest.raises(ValueError, match='ENGINE_SENSOR_PAIR_OUTPUT_EXISTS'):
        pair.pair({'baseline': baseline, 'candidate': candidate}, tmp_path / 'out', checks=('REQ-14',))
    with pytest.raises(ValueError, match='ENGINE_SENSOR_PAIR_CHECKS_INVALID'):
        pair.pair({'baseline': baseline, 'candidate': candidate}, tmp_path / 'out3', checks=('REQ-20',))
