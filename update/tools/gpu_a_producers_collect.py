"""2단계 P1·P2 생산자 GPU 확인 수집(개발 담당 A). `v1-producers` 세대 엔진에서 한 세션의 기록을 모은다.

실행 대상(target)·환경·준비 자산을 받아 소유 Pod 바인딩을 확인한 뒤 콜백 행을 한 번 읽고,
켠 관측마다 #196 계약 기록을 만들어 계약 `validate()` 결과와 함께 쓴다. 판정을 하지 않는다.
지원 생산자 목록(`SUPPORTED_PRODUCERS`)은 바꾸지 않는다. 등록은 이 기록을 검토한 뒤 계약 모듈에서 따로 한다.

    python -m tools.gpu_a_producers_collect --env env.json --target target.json --prepared prepared --out producers.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from guard.collectors.engine_callbacks import load_prepared
from guard.collectors.file import load_environment
from guard.collectors import gpu_input_producers as producers
from guard.collectors.gpu_common_inputs import GpuInputError
from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_http import ENGINE_CALLBACK_PROFILE, KubernetesHTTP

SCHEMA = 'byul-gpu-a-producers-collect/1'
# 준비 설정 플래그 → (기록 kind, 생산 함수)
SIMPLE = (('request_attempts', 'request-attempt', producers.request_attempt_record),
          ('generation_transitions', 'generation-transition', producers.generation_transition_record),
          ('health_causal', 'health-worker-causal', producers.health_worker_record),
          ('profiler_control', 'profiler-control', producers.profiler_control_record),
          ('runner_consumption', 'runner-consumption', producers.runner_consumption_record))


def _summary(record, result):
    events = record['events']
    counts = {}
    for event in events:
        counts[event['event']] = counts.get(event['event'], 0) + 1
    return {'kind': record['kind'], 'producer': record['producer'], 'bindings': len(record['bindings']), 'events': len(events),
            'event_counts': dict(sorted(counts.items())), 'source_ended': record['completeness']['source_ended'],
            'diagnostic_codes': record['completeness']['diagnostic_codes'],
            **{k: result[k] for k in ('supported', 'complete', 'gaps', 'violation_candidates')}}


def collect(*, env_path, target_path, prepared_dir, duration=240):
    environment = load_environment(env_path)
    target = json.loads(Path(target_path).read_text())
    prepared = load_prepared(prepared_dir)
    settings = json.loads((Path(prepared_dir) / 'settings.json').read_text())
    transport = KubernetesHTTP(target, environment, duration=duration, profile=ENGINE_CALLBACK_PROFILE)
    scope = {'pod': transport.binding['engine'], 'environment_ref': environment.ref().model_dump(mode='json'),
             'target_sha256': sha256_hex(canonical_json(target)), 'prepared_sha256': sha256_hex(canonical_json(prepared)),
             'session': prepared['session']}
    roots = settings.get('supply_roots') or []
    mount_before = transport.supply_mount_exclusivity(roots) if roots else None
    value = transport.engine_attempt_events(prepared['session'])
    processes, truncated = value['processes'], value['truncated']
    records, summaries = {}, []
    for flag, kind, make in SIMPLE:
        if not settings.get(flag):
            continue
        try:
            record, result = make(processes, truncated=truncated, **scope)
        except GpuInputError as error:
            # 이 세션에서 그 경로를 일으키지 않았으면(예: 바인딩 0개) 계약이 형식 오류로 거부한다. 결과로만 남긴다.
            records[kind] = {'contract_error': str(error)}
            summaries.append({'kind': kind, 'contract_error': str(error)})
            continue
        records[kind] = {'record': record, 'validation': result}
        summaries.append(_summary(record, result))
    if roots:
        files = producers.supply_open_files(processes)
        hashes = transport.engine_supply_hashes(roots, files)
        mount_after = transport.supply_mount_exclusivity(roots)
        record, result = producers.supply_read_record(processes, hashes, truncated=truncated, mount_before=mount_before,
                                                      mount_after=mount_after, **scope)
        records['supply-reads'] = {'record': record, 'validation': result, 'mount_before': mount_before, 'mount_after': mount_after}
        summaries.append(_summary(record, result))
    return {'schema': SCHEMA, 'session': prepared['session'], 'truncated': truncated,
            'processes': [{'pid': p['pid'], 'start_ticks': p['start_ticks'], 'rows': len(p['events'])} for p in processes],
            'summaries': summaries, 'records': records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--env', required=True); parser.add_argument('--target', required=True)
    parser.add_argument('--prepared', required=True); parser.add_argument('--out', required=True)
    parser.add_argument('--duration', type=int, default=240)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit('출력 파일이 이미 있습니다')
    value = collect(env_path=args.env, target_path=args.target, prepared_dir=args.prepared, duration=args.duration)
    out.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    for summary in value['summaries']:
        print(json.dumps({k: summary[k] for k in ('kind', 'contract_error', 'events', 'event_counts', 'source_ended', 'gaps',
                                                  'violation_candidates') if k in summary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
