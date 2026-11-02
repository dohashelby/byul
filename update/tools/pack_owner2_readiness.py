"""담당 2의 확정 23개 매핑을 읽기 전용으로 검사한다. 연결과 실측은 별개다."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from guard.executors import registry
from guard.executors.engine_sensor import PROFILES as SENSOR_PROFILES
from guard.executors.kubernetes_stream_buffer import PROFILE as STREAM_PROFILE, BUDGET_PROFILE
from guard.executors.kubernetes_initialization import PROFILE as INIT_PROFILE, READINESS_PROFILE
from guard.executors.kubernetes_rank_failure import PROFILE as RANK_PROFILE
from guard.executors.kubernetes_request_termination import PROFILE as TERMINATION_PROFILE
from guard.executors.kubernetes_resource_recovery import PROFILE as RESOURCE_PROFILE
from guard.executors.retry_record_contract import PROFILE as RETRY_PROFILE
from guard.executors.kubernetes_request_attempts import PROFILE as ATTEMPT_PROFILE
from guard.executors.kubernetes_observation_composite import PROFILE as OBS_PROFILE
from guard.executors.kubernetes_profiler_control import PROFILE as PROFILER_CONTROL_PROFILE
from guard.models import load_pack

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {
    **{key: ('engine_sensor', 'kubernetes', (profile,)) for key, profile in SENSOR_PROFILES.items()},
    'LIF-05': ('lifecycle_transition', 'kubernetes', ('lif05-stopped-redeploy-v1',)),
    'LIF-06': ('lifecycle_transition', 'kubernetes', ('lif06-live-weights-v1', 'lif06-lora-v1', 'lif06-rolling-v1', 'lif06-multi-replica-v1')),
    'LIF-16': ('lifecycle_transition', 'kubernetes', ('lif16-restore-redeploy-v1',)),
    'LIF-04': ('stream_buffer', 'kubernetes', (STREAM_PROFILE,)),
    'LIF-08': ('rank_failure', 'kubernetes', (RANK_PROFILE,)),
    'LIF-09': ('initialization', 'kubernetes', (INIT_PROFILE,)),
    'LIF-10': ('request_termination', 'kubernetes', (TERMINATION_PROFILE,)),
    'LIF-11': ('resource_recovery', 'kubernetes', (RESOURCE_PROFILE,)),
    'LIF-12': ('request_termination', 'kubernetes', (RETRY_PROFILE,)),
    'REQ-12': ('request_termination', 'kubernetes', (ATTEMPT_PROFILE,)),
    'OBS-02': ('observation_state', 'kubernetes', (OBS_PROFILE,)),
    'LIF-03': ('stream_buffer', 'kubernetes', (BUDGET_PROFILE,)),
    'LIF-07': ('initialization', 'kubernetes', (READINESS_PROFILE,)),
    'LIF-14': ('profiler_observation', 'kubernetes', (PROFILER_CONTROL_PROFILE,)),
}
EXCLUDED = ('SUP-01', 'SUP-02', 'SUP-14', 'LIF-15', 'OBS-07', 'OBS-08', 'OBS-10', 'OBS-12')


def rows(pack):
    result = []
    for check_id, (name, implementation, profiles) in sorted(MAPPING.items()):
        check = pack.checks[check_id]
        factory = registry.resolve_executor(name, implementation)
        instance = factory({}) if factory is not None else None
        available = instance is not None and instance.name == name and instance.name != 'fake'
        supported = available
        reason = None
        if available and name == 'engine_sensor':
            state = instance.support(check_id, profiles[0])
            supported, reason = state['implemented'], state['reason']
        selected = check.executor == name and check.collection_profile in profiles
        judge = check.judge
        rules = bool(judge and judge.plugin == 'rule_table' and any(judge.params.get(k) for k in ('pass_when_all', 'fail_when_any')))
        result.append({'check_id': check_id, 'executor': name, 'implementation': implementation,
            'collection_profiles': profiles, 'registered_factory_available': bool(available),
            'selected_implementation_supported': bool(supported), 'unimplemented_reason': reason,
            'baseline_mapping_matches': selected, 'baseline_judge_rules_present': rules,
            'baseline_connected': bool(selected and rules and supported),
            'whole_check_experiment_complete': False, 'kubernetes_measured': False, 'gpu_measured': False})
    return result


def inspect(pack):
    items = rows(pack)
    preserved = all(pack.checks[key].executor is None for key in EXCLUDED)
    return {'pack': pack.ref().model_dump(mode='json'), 'assigned': len(items),
        'baseline_connected': sum(row['baseline_connected'] for row in items),
        'registered_factory_available': sum(row['registered_factory_available'] for row in items),
        'excluded_manual_paths_preserved': preserved,
        'note': 'factory 존재·CPU/EXAMPLE 계약 검증·실제 환경 시험 완료를 구분하며, 미구현 capability는 연결 수에서 제외한다.',
        'checks': items}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', type=Path, default=ROOT/'packs/PACK-BASELINE')
    parser.add_argument('--check', action='store_true', help='23개 실제 팩 연결과 제외 수동 경로가 모두 성립해야 성공')
    args = parser.parse_args(argv)
    summary = inspect(load_pack(args.pack))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(args.check and (summary['baseline_connected'] != 23 or not summary['excluded_manual_paths_preserved']))


if __name__ == '__main__':
    raise SystemExit(main())
