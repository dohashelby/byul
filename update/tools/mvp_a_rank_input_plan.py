"""M-A3의 실제 저장된 미결합 준비물을 J01 계획에 연결한다. ENV·결과·M-B4는 만들지 않는다."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from guard.adapters import vllm_rank_ipc_runtime as runtime
from guard.models import canonical_json, load_pack
from tools.mvp_a_input_preflight import _read, _pairs
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/dev-1/ma7-session-input-plan.json'
FALSE_FIELDS = ('live_target_verified', 'input_preflight_passed', 'engine_executed', 'scope_complete',
    'producer_registered', 'source_authenticated', 'engine_group_verified')


def prepare(rank_preparation, output, *, plan_file=PLAN):
    try:
        return _prepare(rank_preparation, output, plan_file=plan_file)
    except (KeyError, TypeError, IndexError):
        raise ValueError('MA7_RANK_INPUT_INVALID') from None


def _prepare(rank_preparation, output, *, plan_file):
    tracked = {}
    def load(path):
        path = Path(path); raw, identity = _read(path); tracked[path] = (raw, identity)
        return json.loads(raw, object_pairs_hook=_pairs)
    original = load(plan_file); source = load(rank_preparation); pair = versions()
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    active = set(pack.checks) - {'SUP-01', 'SUP-02', 'SUP-14', 'LIF-15', 'OBS-07', 'OBS-08', 'OBS-10', 'OBS-12'}
    if (original['schema'] != 'guard-mvp-session-input-plan/1' or original['ready_for_execution']
            or original['input_preflight_passed'] or original['execution_authorized']
            or len(original['checks']) != 69 or {r['check_id'] for r in original['checks']} != active
            or any(r['actual_results'] is not None for r in original['checks'])
            or 'rank_fault_input_preparation' in original):
        raise ValueError('MA7_UNBOUND_SESSION_PLAN_REQUIRED')
    rank = next(r for r in original['checks'] if r['check_id'] == 'LIF-08')
    if any(s.get(k) is not None for s in rank['sides'].values() for k in ('environment_ref', 'target', 'prepared')):
        raise ValueError('MA7_BOUND_RANK_PLAN_PRESERVED')
    if (source['schema'] != 'guard-ma3-product-binding-preparation/1'
            or source['data_origin'] not in {'DECLARED', 'EXAMPLE'}
            or any(source.get(k) is not False for k in FALSE_FIELDS) or source.get('actual_results') is not None
            or type(source.get('prepared_candidates')) is not list or len(source['prepared_candidates']) != 6):
        raise ValueError('MA7_UNBOUND_RANK_PREPARATION_REQUIRED')
    rows, seen, sessions = [], set(), set()
    for row in source['prepared_candidates']:
        side, kind = row['side'], row['fault_kind']
        if (side not in pair or kind not in {'delay', 'exception', 'death'} or (side, kind) in seen
                or any(row.get(k) is not False for k in FALSE_FIELDS)
                or any(row.get(k) is not None for k in ('environment_ref', 'target_sha256', 'product_binding_file', 'actual_results'))):
            raise ValueError('MA7_UNBOUND_RANK_PREPARATION_REQUIRED')
        seen.add((side, kind)); directory = Path(row['prepared_directory'])
        manifest = load(directory / 'manifest.json'); settings = load(directory / 'settings.json')
        if (manifest['schema'] != 'guard-rank-ipc-prepared-candidate/1'
                or row['session'] != manifest['session'] or row['session'] in sessions
                or type(row['session']) is not str or re.fullmatch('[0-9a-f]{32}', row['session']) is None
                or manifest['image'] != pair[side]['image'] or settings['image'] != manifest['image']
                or manifest['source_manifest_sha256'] != source['reviewed_source_manifest_sha256']
                or any(manifest.get(k) is not False for k in ('observer_installed', 'engine_group_verified', 'producer_registered'))
                or sha256(tracked[directory / 'manifest.json'][0]).hexdigest() != row['prepared_manifest_sha256']
                or sha256(tracked[directory / 'settings.json'][0]).hexdigest() != manifest['settings_sha256']
                or settings['session'] != row['session'] or settings['assets'] != manifest['assets']
                or manifest['assets'] != row['assets'] or set(manifest['assets']) != runtime.ASSETS):
            raise ValueError('MA7_RANK_PREPARED_MISMATCH')
        sessions.add(row['session'])
        for name, pin in manifest['assets'].items():
            runtime._asset(directory / name, pin)
        data = directory / 'data' / row['session']
        if not data.is_dir() or data.is_symlink() or any(data.iterdir()):
            raise ValueError('MA7_OBSERVED_RANK_INPUT_NOT_PENDING')
        rows.append({'side': side, 'fault_kind': kind, 'session': row['session'],
            'prepared_manifest_file': str(directory / 'manifest.json'),
            'prepared_manifest_sha256': row['prepared_manifest_sha256'],
            'prepared_source_bytes_verified': True, 'prepared_runtime_qualified': False,
            'environment_ref': None, 'target': None, 'product_binding_file': None, 'common_input_file': None,
            'actual_results': None, 'ready_for_execution': False,
            'remaining': ['현재 ENV/target·기동 전 자격', 'ARM 전 제품 사전 결합',
                '실제 원 기록의 공통 입력 변환', '전체 TP2 그룹/기한/복구 수락·등록']})
    for path, snapshot in tracked.items():
        if _read(path) != snapshot:
            raise ValueError('MA7_RANK_INPUT_CHANGED')
    # 기존 69개 행·M-B 입력·정책값·18개 A 준비 세션은 그대로 보존한다.
    plan = deepcopy(original)
    link = {'schema': 'guard-ma7-rank-input-preparation/1', 'check_id': 'LIF-08', 'work_item': 'M-A3',
        'session': 'J01', 'data_origin': source['data_origin'],
        'source_plan_sha256': sha256(tracked[Path(plan_file)][0]).hexdigest(),
        'rank_preparation_sha256': sha256(tracked[Path(rank_preparation)][0]).hexdigest(),
        'reviewed_source_manifest_sha256': source['reviewed_source_manifest_sha256'], 'candidates': rows,
        'engine_executed': False, 'live_target_verified': False, 'input_preflight_passed': False,
        'ready_for_execution': False, 'actual_results': None}
    plan['rank_fault_input_preparation'] = link
    output = Path(output); output.mkdir(mode=0o700, exist_ok=False)
    for name, value in [('session-input-plan.json', plan), ('rank-input-link.json', link)]:
        with (output / name).open('xb') as stream:
            stream.write(canonical_json(value))
    return link, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rank-preparation', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=PLAN)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    link, _ = prepare(args.rank_preparation, args.output, plan_file=args.plan)
    print(json.dumps(link, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
