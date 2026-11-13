"""M-A 로컬 자산 준비는 실제 ENV·결과와 M-B 입력을 만들거나 바꾸지 않는다."""
import json
from pathlib import Path

import pytest
import yaml

from guard.collectors import engine_callbacks
from guard.executors import engine_callback_projection as projection
from guard.models import canonical_json, sha256_hex
from tests.test_mvp_sup08_local_audit import local_assets
from tools import mvp_a_input_materialize as tool, mvp_sup08_local_audit
from tools.gpu_a_req17_cohort import build as timing_cohort


def example_audit(tmp_path, monkeypatch):
    fixture, launch, _, _ = local_assets(tmp_path, monkeypatch)
    audit = mvp_sup08_local_audit.audit(fixture, launch)
    audit.update(fixture_file=str(fixture), launch_manifest_file=str(launch),
        launch_manifest_sha256=sha256_hex(launch.read_bytes()), checked_at='EXAMPLE-not-a-runtime-observation')
    path = tmp_path / 'EXAMPLE-audit.json'
    path.write_bytes(canonical_json(audit))
    return path


def test_materialized_files_are_valid_without_invented_binding_or_results(tmp_path, monkeypatch):
    audit = example_audit(tmp_path, monkeypatch)
    original = json.loads(tool.PLAN.read_bytes())
    out = tmp_path / 'materialized'
    manifest, plan = tool.materialize(out, audit_file=audit)
    assert len(manifest['prepared']) == 16 and len(manifest['cases']) == 6
    assert len({row['session'] for row in manifest['prepared']}) == 16
    assert not manifest['ready_for_execution'] and not manifest['input_preflight_passed']
    assert not manifest['engine_executed'] and manifest['actual_results'] is None
    assert [row for row in original['checks'] if row['work_group'] == 'M-B'] == [
        row for row in plan['checks'] if row['work_group'] == 'M-B']
    assert len(plan['checks']) == 69 and all(row['actual_results'] is None for row in plan['checks'])
    assert all(side['environment_ref'] is None and side['target'] is None
        for row in plan['checks'] for side in row['sides'].values())
    for row in manifest['prepared']:
        assert sha256_hex(Path(row['path']).read_bytes()) == row['sha256']
        assert engine_callbacks.load_prepared(row['path'])['session'] == row['session']
        assert sha256_hex((Path(row['path']).parent / 'settings.json').read_bytes()) == row['settings_file_sha256']
    for row in manifest['cases']:
        assert sha256_hex(Path(row['path']).read_bytes()) == row['sha256']
        projection.validate_cases(row['check_id'], json.loads(Path(row['path']).read_bytes()))
    values = yaml.safe_load(tool.CONFIRMED.read_bytes())['verification']['values']
    draft = yaml.safe_load(Path(manifest['policy_input_draft']).read_bytes())
    for check, fields in values.items():
        assert {key: draft['values'][check][key] for key in fields} == fields
    assert draft['status'] == 'DRAFT' and draft['confirmed_by'] is None and draft['data_origin'] == 'EXAMPLE'
    for fields in draft['values'].values():
        if type(fields) is dict:
            assert not set(fields) & {'sensor_environment_sha256', 'sensor_target_sha256',
                                      'sensor_prepared_sha256', 'sensor_manifest_sha256'}
    assert not plan['ready_for_execution'] and not plan['input_preflight_passed']
    assert not any(path.name in {'environment.json', 'target.json'} for path in out.rglob('*'))
    for check in tool.NATIVE_CHECKS:
        row = next(r for r in plan['checks'] if r['check_id'] == check)
        assert all(s['prepared_source_bytes_verified'] and not s['prepared_runtime_qualified']
            and not s['prepared_node_installed'] for s in row['sides'].values())


def test_req17_conflict_keeps_confirmed_minimum_and_existing_case_bound():
    values = yaml.safe_load(tool.CONFIRMED.read_bytes())['verification']['values']
    result = tool._timing_requirements(values)
    assert result['minimum_per_scored_role'] == 200
    assert result['minimum_scored_cases'] == 800 and result['single_manifest_max_cases'] == 512
    assert not result['fits_single_manifest'] and not result['collection_or_judgment_changed']
    with pytest.raises(ValueError):
        timing_cohort(20261113, quartets=200)


@pytest.mark.parametrize('damage', ['observed_environment', 'result', 'ready'])
def test_bound_or_completed_source_is_not_overwritten_as_pending(tmp_path, monkeypatch, damage):
    audit = example_audit(tmp_path, monkeypatch)
    plan = json.loads(tool.PLAN.read_bytes())
    row = next(r for r in plan['checks'] if r['work_group'] == 'M-A')
    if damage == 'observed_environment': row['sides']['baseline']['environment_ref'] = {'EXAMPLE': 'existing'}
    elif damage == 'result': row['actual_results'] = {'EXAMPLE': 'existing'}
    else: plan['ready_for_execution'] = True
    source = tmp_path / 'source-plan.json'
    source.write_bytes(canonical_json(plan))
    out = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='UNBOUND_A_PLAN_REQUIRED'):
        tool.materialize(out, plan_file=source, audit_file=audit)
    assert not out.exists()


def test_changed_launch_after_audit_is_rejected_before_assets_are_written(tmp_path, monkeypatch):
    audit = example_audit(tmp_path, monkeypatch)
    report = json.loads(audit.read_bytes())
    Path(report['launch_files'][0]['path']).write_bytes(b'EXAMPLE-changed-after-audit')
    out = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='SUP08_LAUNCH_FILE_CHANGED'):
        tool.materialize(out, audit_file=audit)
    assert not out.exists()


def test_existing_materialization_directory_is_preserved(tmp_path):
    out = tmp_path / 'existing'
    out.mkdir()
    (out / 'keep').write_bytes(b'EXAMPLE-original')
    with pytest.raises(ValueError, match='새 입력 준비'):
        tool.materialize(out)
    assert (out / 'keep').read_bytes() == b'EXAMPLE-original'
