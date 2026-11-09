"""2단계 P1·P2 생산자 GPU 확인 수집(개발 담당 A). `v1-producers` 세대 엔진에서 한 세션의 기록을 모은다.

실행 대상(target)·환경·준비 자산을 받아 소유 Pod 바인딩을 확인한 뒤 콜백 행을 한 번 읽고,
켠 관측마다 #196 계약 기록을 만들어 계약 `validate()` 결과와 함께 쓴다. 판정을 하지 않는다.
지원 생산자 목록(`SUPPORTED_PRODUCERS`)은 바꾸지 않는다. 등록은 이 기록을 검토한 뒤 계약 모듈에서 따로 한다.

    python -m tools.gpu_a_producers_collect --env env.json --target target.json --prepared prepared --out producers.json

PLT-01·04(process-device-provider)는 `--node-view`로 노드 관측 위치를 고른다. GPU 서버에서는 `sudo`(노드 root),
k3d에서는 `pod:<namespace>/<name>`(hostPID·노드 /dev를 `/host/dev`로 마운트한 lab 관측 Pod)다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from guard.collectors.engine_callbacks import load_prepared
from guard.collectors.file import load_environment
from guard.collectors import gpu_input_producers as producers
from guard.collectors import process_device_observer as device_observer
from guard.collectors import process_device_producer as device_producer
from guard.collectors import scoped_consumption
from guard.collectors.gpu_common_inputs import GpuInputError
from guard.models import canonical_json, sha256_hex
from guard.runtime.engine_http import ENGINE_CALLBACK_PROFILE, KubernetesHTTP

SCHEMA = 'byul-gpu-a-producers-collect/1'
# 준비 설정 플래그 → (기록 kind, 생산 함수)
SIMPLE = (('request_attempts', 'request-attempt', producers.request_attempt_record),
          ('generation_transitions', 'generation-transition', producers.generation_transition_record),
          ('health_causal', 'health-worker-causal', producers.health_worker_record),
          ('profiler_control', 'profiler-control', producers.profiler_control_record))


def _summary(record, result):
    events = record['events']
    counts = {}
    for event in events:
        counts[event['event']] = counts.get(event['event'], 0) + 1
    return {'kind': record['kind'], 'producer': record['producer'], 'bindings': len(record['bindings']), 'events': len(events),
            'event_counts': dict(sorted(counts.items())), 'source_ended': record['completeness']['source_ended'],
            'diagnostic_codes': record['completeness']['diagnostic_codes'],
            **{k: result[k] for k in ('supported', 'complete', 'gaps', 'violation_candidates')}}


def node_view(mode, spec, *, context=None):
    """노드 관측기를 한 번 돌린다. 출력은 닫힌 JSON 하나다. 실패하면 None."""
    script = device_observer.NODE_OBSERVER.encode()
    if mode == 'sudo':
        argv = ['sudo', '-n', 'python3', '-I', '-S', '-', json.dumps(spec)]
    elif mode.startswith('pod:') and mode.count('/') == 1:
        namespace, name = mode[4:].split('/')
        argv = ['kubectl', *([f'--context={context}'] if context else []), f'--namespace={namespace}', 'exec', '-i', name, '--',
                'python3', '-I', '-S', '-', json.dumps(spec)]
    else:
        raise SystemExit('--node-view는 sudo 또는 pod:<namespace>/<name>입니다')
    try:
        result = subprocess.run(argv, input=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 8 * 1024**2:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def collect_process_device(transport, scope, *, mode, spec_overrides=None):
    """PLT-01·04: Pod 명세(앞) → 노드 관측 → 컨테이너 안 열기 시험 → Pod 명세(뒤). 바인딩이 바뀌면 전송 계층이 멈춘다."""
    engine = transport.binding['engine']
    before = transport.engine_pod_document()
    try:
        candidates = device_observer.provider_candidates(before, engine['container'])
    except device_observer.ObserverOutputError as error:
        return {'contract_error': str(error)}
    spec = device_observer.node_spec(pod_uid=engine['pod_uid'], container_id=engine['container_id'], candidates=candidates,
                                     **(spec_overrides or {}))
    node = node_view(mode, spec, context=transport.binding.get('context'))
    try:
        opened = transport.engine_device_open_test()
    except Exception:
        opened = None
    after = transport.engine_pod_document()
    stable = (before['metadata']['uid'] == after['metadata']['uid']
              and before.get('status', {}).get('containerStatuses') == after.get('status', {}).get('containerStatuses'))
    if node is None:
        return {'contract_error': 'PROCESS_DEVICE_NODE_VIEW_UNAVAILABLE', 'candidates': len(candidates)}
    try:
        record, result = device_producer.process_device_record(node, opened, candidates, container=engine['container'],
                                                         scope_stable=stable, **scope)
    except (GpuInputError, device_observer.ObserverOutputError) as error:
        return {'contract_error': str(error), 'candidates': len(candidates)}
    return {'record': record, 'validation': result, 'node_scanned': node['scanned'], 'open_test': opened}


def collect(*, env_path, target_path, prepared_dir, duration=240, node_view_mode=None, spec_overrides=None, runner_scope=None, pages_dir=None, expected_requests=None, topology=None, request_actors=None):
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
    value = transport.engine_attempt_events(prepared['session'], archive=pages_dir)
    processes, truncated = value['processes'], value['truncated']
    page_refs = value.get('page_refs', [])
    page_capture = {k: value[k] for k in ('diagnostic_codes', 'source_events', 'scope') if k in value}
    records, summaries = {}, []
    if settings.get('runner_consumption') or settings.get('scoped_runner_consumption'):
        # /1 writers remain available for historical reproduction only. New
        # captures use scoped coverage even when reading a legacy prepared set.
        try:
            from guard.collectors import actor_consumption
            record, result = actor_consumption.record(processes, topology=topology, request_actors=request_actors, truncated=truncated, expected_stage_ranks=runner_scope, expected_requests=expected_requests, qualification_scope={
                'engine': environment.engine.name, 'engine_version': environment.engine.version,
                'image_digest': environment.engine.image_digest, 'backend': 'gpu' if environment.runtime.gpu_count else 'cpu',
                'origin': transport.process_observation_origin}, **scope)
            records['scoped-runner-consumption'] = {'record': record, 'validation': result}
            summaries.append(_summary(record, result))
        except GpuInputError as error:
            records['scoped-runner-consumption'] = {'contract_error': str(error)}
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
    if node_view_mode:
        value = collect_process_device(transport, scope, mode=node_view_mode, spec_overrides=spec_overrides)
        records['process-device-provider'] = value
        summaries.append(_summary(value['record'], value['validation']) if 'record' in value
                         else {'kind': 'process-device-provider', 'contract_error': value['contract_error']})
    return {'schema': SCHEMA, 'session': prepared['session'], 'truncated': truncated,
            'processes': [{'pid': p['pid'], 'start_ticks': p['start_ticks'], 'rows': len(p['events'])} for p in processes],
            'summaries': summaries, 'records': records, 'raw_page_refs': page_refs, 'page_capture': page_capture}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--env', required=True); parser.add_argument('--target', required=True)
    parser.add_argument('--prepared', required=True); parser.add_argument('--out', required=True)
    parser.add_argument('--duration', type=int, default=240)
    parser.add_argument('--expected-requests', type=Path, help='실행 전에 고정한 요청 HMAC 목록 JSON; 미입력은 범위 미확인')
    parser.add_argument('--runner-scope', type=Path, help='단계별 기대 rank JSON; 미입력은 범위 미확인으로 남음')
    parser.add_argument('--request-actors', type=Path, help='bootstrap에 해시로 결합한 요청별 단계/actor 할당 JSON')
    parser.add_argument('--topology', type=Path, help='독립 bootstrap 관측으로 고정한 actor/group manifest; 미입력은 미완료')
    parser.add_argument('--node-view', help='PLT-01·04 노드 관측 위치: sudo 또는 pod:<namespace>/<name>')
    parser.add_argument('--node-dev-root', default='/dev', help='노드 관측에서 본 노드 /dev 경로(k3d 관측 Pod는 /host/dev)')
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit('출력 파일이 이미 있습니다')
    value = collect(env_path=args.env, target_path=args.target, prepared_dir=args.prepared, duration=args.duration,
                    node_view_mode=args.node_view, spec_overrides={'dev_root': args.node_dev_root},
                    runner_scope=json.loads(args.runner_scope.read_text()) if args.runner_scope else None,
                    pages_dir=out.with_suffix('.pages'), expected_requests=json.loads(args.expected_requests.read_text()) if args.expected_requests else None,
                    topology=json.loads(args.topology.read_text()) if args.topology else None,
                    request_actors=json.loads(args.request_actors.read_text()) if args.request_actors else None)
    out.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    for summary in value['summaries']:
        print(json.dumps({k: summary[k] for k in ('kind', 'contract_error', 'events', 'event_counts', 'source_ended', 'gaps',
                                                  'violation_candidates') if k in summary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
