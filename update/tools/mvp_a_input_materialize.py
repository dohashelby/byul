"""M-A7의 미결합 입력 자산을 만든다. 실제 ENV/target·실행 결과를 생성하지 않는다."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import yaml

from guard.collectors import engine_callbacks
from guard.executors import engine_callback_projection as projection
from guard.models import canonical_json, load_pack, sha256_hex
from tools import gpu_a_run_inputs

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/dev-1/ma7-session-input-plan.json'
CONFIRMED = ROOT / 'examples/mvp-confirmed-values.yaml'
POLICY_DRAFT = ROOT / 'docs/gpu-a/policy-a-draft.yaml'
SUP08_AUDIT = ROOT / 'docs/dev-1/ma8-local-preflight-20261113.json'
NATIVE_CHECKS = ('REQ-14', 'REQ-16', 'REQ-17', 'REQ-18', 'REQ-19', 'REQ-20', 'REQ-21', 'LIF-13')
PRESETS = {'REQ-14': 'req14-normal', 'REQ-16': 'req16-full', 'REQ-18': 'req18-slot'}
SIDES = ('baseline', 'candidate')


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical_json(value))


def _timing_requirements(values):
    minimum = values['REQ-17']['cache_timing_min_samples']
    if type(minimum) is not int or minimum <= 0:
        raise ValueError('REQ17_CONFIRMED_MINIMUM_INVALID')
    # 현행 판정은 네 역할 각각의 표본 수에 같은 최소값을 적용한다.
    return {'minimum_per_scored_role': minimum, 'scored_roles': 4,
        'minimum_scored_cases': 4 * minimum, 'single_manifest_max_cases': projection.MAX_CASES,
        'fits_single_manifest': 4 * minimum <= projection.MAX_CASES,
        'collection_or_judgment_changed': False}


def materialize(output, *, plan_file=PLAN, audit_file=SUP08_AUDIT):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('새 입력 준비 디렉터리가 필요합니다.')
    source_raw = Path(plan_file).read_bytes()
    source = json.loads(source_raw)
    plan = deepcopy(source)
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    selected = {row['check_id'] for row in source['checks']}
    active = set(pack.checks) - {'SUP-01', 'SUP-02', 'SUP-14', 'LIF-15', 'OBS-07', 'OBS-08', 'OBS-10', 'OBS-12'}
    a_rows = [row for row in plan['checks'] if row['work_group'] == 'M-A']
    if (selected != active or len(source['checks']) != len(selected) or len(a_rows) != 27
            or source['planned_pack_ref'] != pack.ref().model_dump(mode='json')
            or source['ready_for_execution'] or source['input_preflight_passed'] or source['execution_authorized']
            or any(row['actual_results'] is not None for row in source['checks'])
            or any(side['environment_ref'] is not None or side['target'] is not None
                   for row in a_rows for side in row['sides'].values())):
        raise ValueError('UNBOUND_A_PLAN_REQUIRED')
    audit_raw = Path(audit_file).read_bytes()
    audit = json.loads(audit_raw)
    if (audit['schema'] != 'guard-mvp-sup08-local-audit/1' or not audit['local_assets_valid']
            or audit['input_preflight_passed'] or audit['engine_executed'] or audit['cluster_observed']
            or audit['actual_comparison'] is not None or audit['pack_ref'] != source['planned_pack_ref']):
        raise ValueError('SUP08_LOCAL_AUDIT_REQUIRED')
    # 준비 보고서 뒤에 기동 파일이 바뀌었으면 현재 파일과 일치한다고 표시하지 않는다.
    for row in audit['launch_files']:
        if not row['verified'] or sha256_hex(Path(row['path']).read_bytes()) != row['sha256']:
            raise ValueError('SUP08_LAUNCH_FILE_CHANGED')
    launch_path = Path(audit['launch_manifest_file'])
    fixture_path = Path(audit['fixture_file'])
    if sha256_hex(fixture_path.read_bytes()) != audit['fixture_manifest_sha256']:
        raise ValueError('SUP08_FIXTURE_MANIFEST_CHANGED')
    if sha256_hex(launch_path.read_bytes()) != audit['launch_manifest_sha256']:
        raise ValueError('SUP08_LAUNCH_MANIFEST_CHANGED')
    launch = json.loads(launch_path.read_bytes())
    draft = yaml.safe_load(POLICY_DRAFT.read_bytes())
    values = yaml.safe_load(CONFIRMED.read_bytes())['verification']['values']
    for check, row in values.items():
        draft['values'].setdefault(check, {}).update(deepcopy(row))
    draft['pack'] = {'id': pack.ref().id, 'revision': pack.ref().revision}
    draft['status'] = 'DRAFT'
    draft['data_origin'] = 'EXAMPLE'
    draft['confirmed_by'] = draft['confirmed_at'] = None
    timing = _timing_requirements(values)
    output.mkdir(parents=True)
    draft_path = output / 'policy-input-draft.yaml'
    draft_path.write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False))
    prepared_rows, case_rows = [], []
    for row in a_rows:
        check = row['check_id']
        for side in SIDES:
            item = row['sides'][side]
            folder = output / side / check
            if check in NATIVE_CHECKS:
                prepared = folder / 'prepared'
                manifest = engine_callbacks.prepare_assets(prepared,
                    lab_faults=check == 'REQ-18', lab_kernel_checks=check == 'REQ-19')
                if engine_callbacks.load_prepared(prepared) != manifest:
                    raise ValueError('PREPARED_BYTES_MISMATCH')
                item.update(prepared=str(prepared / 'manifest.json'),
                    prepared_file_sha256=sha256_hex((prepared / 'manifest.json').read_bytes()),
                    prepared_settings_file_sha256=sha256_hex((prepared / 'settings.json').read_bytes()),
                    prepared_source_bytes_verified=True, prepared_node_installed=False,
                    prepared_runtime_qualified=False, prepared_session=manifest['session'])
                prepared_rows.append({'check_id': check, 'side': side, 'path': item['prepared'],
                    'sha256': item['prepared_file_sha256'], 'session': manifest['session'],
                    'settings_file_sha256': item['prepared_settings_file_sha256']})
                if check in PRESETS:
                    cases = gpu_a_run_inputs.cases(PRESETS[check])
                    projection.validate_cases(check, cases)
                    if check == 'REQ-16':
                        projection.validate_lab_contexts(gpu_a_run_inputs.LAB, cases)
                    path = folder / 'cases.json'
                    _write(path, cases)
                    item.update(cases_file=str(path), cases_file_sha256=sha256_hex(path.read_bytes()),
                                cases_preset=PRESETS[check])
                    case_rows.append({'check_id': check, 'side': side, 'count': len(cases),
                        'path': str(path), 'sha256': item['cases_file_sha256']})
                item['reason'] += ' 로컬 prepared bytes를 준비했으며 노드 설치·관측 지점 자격·ENV/target 결합은 미완료다.'
            if check == 'SUP-08':
                base = launch_path.parent / side
                for key, name in [('policy_file', 'policy.yaml'), ('render_file', 'render.json'),
                                 ('configuration_pending_target_file', 'guard.pending-target.toml')]:
                    item[key] = str(base / name)
                    item[key + '_sha256'] = launch['files_sha256'][side + '/' + name]
                item.update(local_audit_file=str(Path(audit_file).resolve()),
                    local_audit_sha256=sha256_hex(audit_raw), local_assets_verified=True,
                    fixture_manifest_path=str(fixture_path), fixture_manifest_sha256=audit['fixture_manifest_sha256'],
                    prepared_required=False)
                row['input_state'] = '실제 로컬 파일 SHA 재확인·최종 ENV/target 미입력'
    plan['input_materialization']['SUP08_local_audit'] = {
        'path': str(Path(audit_file).resolve()), 'sha256': sha256_hex(audit_raw),
        'model_bytes_checked_at': audit.get('checked_at'), 'launch_manifest_sha256': audit['launch_manifest_sha256'],
        'input_preflight_passed': False}
    plan['input_materialization']['REQ17_timing_requirements'] = timing
    for row in a_rows:
        if row['check_id'] == 'REQ-17' and not timing['fits_single_manifest']:
            row['case_generation_deferred'] = True
            row['collection_plan_blockers'] = ['REQ17_MINIMUM_PER_ROLE_EXCEEDS_SINGLE_MANIFEST_BOUND']
    manifest = {'schema': 'guard-ma7-local-materialization/1', 'scope': 'M-A 미결합 로컬 입력 준비',
        'source_plan_sha256': sha256_hex(source_raw), 'pack_ref': pack.ref().model_dump(mode='json'),
        'generator_source_sha256': sha256_hex(Path(__file__).read_bytes()),
        'prepared': prepared_rows, 'cases': case_rows, 'confirmed_policy_source_sha256': sha256_hex(CONFIRMED.read_bytes()),
        'policy_input_draft': str(draft_path), 'policy_input_draft_sha256': sha256_hex(draft_path.read_bytes()),
        'req17_timing_requirements': timing, 'ready_for_execution': False,
        'input_preflight_passed': False, 'engine_executed': False, 'actual_results': None}
    _write(output / 'session-input-plan.json', plan)
    _write(output / 'manifest.json', manifest)
    return manifest, plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=PLAN)
    parser.add_argument('--sup08-audit', type=Path, default=SUP08_AUDIT)
    args = parser.parse_args(argv)
    manifest, _ = materialize(args.output, plan_file=args.plan, audit_file=args.sup08_audit)
    print(json.dumps({'prepared': len(manifest['prepared']), 'cases': len(manifest['cases']),
        'ready_for_execution': False, 'req17_timing_requirements': manifest['req17_timing_requirements']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
