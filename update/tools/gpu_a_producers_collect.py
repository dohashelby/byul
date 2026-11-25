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
from threading import get_ident

from guard.collectors.engine_callbacks import load_prepared
from guard.collectors.file import load_environment
from guard.collectors import gpu_input_producers as producers
from guard.collectors import process_device_observer as device_observer
from guard.collectors import process_device_producer as device_producer
from guard.collectors import scoped_consumption
from guard.collectors import worker_shard_producer
from guard.collectors.gpu_common_inputs import GpuInputError
from guard.collectors.gpu_common_input_v4 import event_count
from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, sha256_hex, utcnow
from guard.runtime.engine_http import (
    ENGINE_CALLBACK_PROFILE, NORMAL_CALLBACK_ATTESTATION_SCHEMA, NORMAL_CALLBACK_CUT_GAP,
    KubernetesBindingError, KubernetesHTTP,
)

SCHEMA = 'byul-gpu-a-producers-collect/1'
NORMAL_SAMPLING_TRIAL_SCHEMA = 'byul-normal-sampling-trial/1'
_NORMAL_SAMPLING_TRIAL_DIRECTORY = 'normal-sampling-trial'
_NORMAL_SAMPLING_SCOPE_ERROR = 'NORMAL_SAMPLING_TRIAL_SCOPE_INVALID'
# 준비 설정 플래그 → (기록 kind, 생산 함수)
SIMPLE = (('request_attempts', 'request-attempt', producers.request_attempt_record),
          ('generation_transitions', 'generation-transition', producers.generation_transition_record),
          ('health_causal', 'health-worker-causal', producers.health_worker_record),
          ('profiler_control', 'profiler-control', producers.profiler_control_record))


def _summary(record, result):
    events = record['events']
    counts = {}
    for event in events:
        counts[event['event']] = counts.get(event['event'], 0) + event_count(event)
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


def _archive_normal_sampling_receipt(draft, archive, pins, *, started, finished, transport_origin):
    from guard.executors.base import EvidenceDraft
    from guard.executors.kubernetes_sampling import NORMAL_OPTION, KubernetesSamplingExecutor
    from guard.executors.normal_sampling_receipt import qualify_summary
    from guard.update_validation import _plain_path
    from tools.mvp_a_input_preflight import _read

    if (not isinstance(draft, EvidenceDraft) or draft.status not in {ExecStatus.COMPLETED, ExecStatus.ABORTED}
            or draft.config != {NORMAL_OPTION: True, 'target_sha256': pins['target_sha256']}
            or draft.normal_control.attempted is not True or len(draft.artifacts) != 1
            or draft.started_at is None or draft.started_at.utcoffset() is None
            or draft.finished_at is None or draft.finished_at.utcoffset() is None
            or not started <= draft.started_at <= draft.finished_at <= finished):
        raise ValueError
    artifact, = draft.artifacts
    if (artifact.name != 'normal_sampling.json' or artifact.path.name != artifact.name
            or artifact.collector != f'{KubernetesSamplingExecutor.name}/{KubernetesSamplingExecutor.version}'):
        raise ValueError
    raw, _ = _read(_plain_path(artifact.path), max_bytes=16 * 1024)
    digest = sha256_hex(raw)
    qualified = qualify_summary(raw, expected_file_sha256=digest, expected_pins=pins)
    if draft.status == ExecStatus.COMPLETED and (
            draft.abort_reason is not None or draft.cleanup_errors or draft.normal_control.succeeded is not True
            or qualified['reported_completion_shape_verified'] is not True):
        raise ValueError
    with (archive / artifact.name).open('xb') as stream:
        stream.write(raw)
    origin = min(draft.data_origin, DataOrigin(transport_origin), key=ORIGIN_STRENGTH.__getitem__)
    return {'path': artifact.name, 'sha256': digest, 'bytes': len(raw), 'data_origin': origin.value,
            'execution_status': draft.status.value, 'started_at': draft.started_at.isoformat(),
            'finished_at': draft.finished_at.isoformat()}


def _collect_normal_sampling_trial(transport, session, pages_dir, operation, request_sha256, capture_options,
                                   *, observe_response):
    from guard.update_validation import _plain_path

    pages_dir = _plain_path(Path(pages_dir))
    pages_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    archive = pages_dir / _NORMAL_SAMPLING_TRIAL_DIRECTORY
    archive.mkdir(mode=0o700)
    pins = {'target_sha256': transport.target_sha256, 'binding_sha256': sha256_hex(canonical_json(transport.binding)),
            'probe_source_sha256': transport.source_sha256, 'request_body_sha256': request_sha256}
    cut = transport.begin_callback_trial(session, archive=archive, **capture_options)
    thread = get_ident()
    accepting = seen = False
    pending = observation = None

    def remember_response(*, body_bytes, summary_sha256, environment_ref, data_origin):
        nonlocal pending, seen
        if not accepting or get_ident() != thread:
            return
        if seen:
            pending = None
            return
        seen = True
        if (type(body_bytes) is not bytes or not 0 < len(body_bytes) <= 128 * 1024
                or type(summary_sha256) is not str or len(summary_sha256) != 64
                or any(char not in '0123456789abcdef' for char in summary_sha256)
                or type(environment_ref) is not dict or environment_ref != cut['environment_ref']
                or type(data_origin) is not str or data_origin != cut['data_origin']):
            return
        pending = {'body_bytes': body_bytes, 'summary_sha256': summary_sha256,
                   'environment_ref': cut['environment_ref'], 'data_origin': data_origin,
                   'attempt_sha256': sha256_hex(canonical_json(cut))}

    diagnostics = []
    returned = False
    draft = None
    try:
        transport.verify_normal_callback_cut(session, cut, **capture_options)
        started = utcnow()
        accepting = observe_response
        try:
            draft = operation(normal_response_observer=remember_response) if observe_response else operation()
        except Exception:
            diagnostics.append('NORMAL_SAMPLING_OPERATION_FAILED')
        else:
            returned = True
            observation = pending
        finally:
            accepting = False
            pending = None
        finished = utcnow()
    finally:
        try:
            captured = transport.engine_attempt_events(session, archive=pages_dir,
                                                       normal_callback_cut=cut, **capture_options)
        except (KubernetesBindingError, OSError, ValueError):
            captured = {'processes': [], 'truncated': True, 'diagnostic_codes': [NORMAL_CALLBACK_CUT_GAP]}
    receipt = None
    if returned:
        try:
            receipt = _archive_normal_sampling_receipt(draft, archive, pins, started=started, finished=finished,
                                                       transport_origin=transport.process_observation_origin)
        except (KeyError, OSError, TypeError, ValueError):
            diagnostics.append('NORMAL_SAMPLING_RECEIPT_UNVERIFIED')
    return captured, {'schema': NORMAL_SAMPLING_TRIAL_SCHEMA, 'archive_dir': _NORMAL_SAMPLING_TRIAL_DIRECTORY,
                      'cut': cut, 'cut_verified': captured.get('normal_callback_cut_verified') is True,
                      'operation_returned': returned, 'diagnostic_codes': diagnostics,
                      'expected_pins': pins, 'receipt': receipt}, observation


def _associate_normal_response(captured, trial, observer, links, *, pages_dir, scope, settings, api_model):
    from guard.executors.normal_sampling_receipt import associate_response
    from guard.update_validation import _plain_path
    from tools.mvp_a_input_preflight import _read

    cut, receipt, pins = (trial[key] for key in ('cut', 'receipt', 'expected_pins'))
    session = scope['session']
    mounted = captured.get('mounted_source_attestation') or {}
    if (observer is None or receipt is None or receipt['execution_status'] != ExecStatus.COMPLETED
            or not trial['cut_verified'] or captured.get('normal_callback_cut') != cut
            or captured.get('truncated') is not False or mounted.get('collection_truncated') is not False
            or mounted.get('schema') != NORMAL_CALLBACK_ATTESTATION_SCHEMA or not captured.get('page_refs')
            or mounted.get('observed_pages') != len(captured['page_refs'])
            or cut['environment_ref'] != scope['environment_ref'] or cut['target_sha256'] != pins['target_sha256']
            or cut['owned_generation_sha256'] != pins['binding_sha256']
            or cut['session_sha256'] != sha256_hex(session.encode()) or settings['session'] != session
            or observer['data_origin'] != receipt['data_origin']
            or observer['summary_sha256'] != receipt['sha256']
            or any(mounted.get(key) != cut[key] for key in (
                'data_origin', 'environment_ref', 'target_sha256', 'target_file_sha256',
                'plan_sha256', 'source_sha256', 'owned_generation_sha256'))
            or sha256_hex(canonical_json(mounted.get('observation'))) != cut['source_observation_sha256']):
        raise ValueError
    archive = Path(pages_dir) / trial['archive_dir']
    start = cut['start_page']
    raw_start, _ = _read(_plain_path(archive / start['path']), max_bytes=1024**2)
    if sha256_hex(raw_start) != start['sha256']:
        raise ValueError
    raw_summary, _ = _read(_plain_path(archive / receipt['path']), max_bytes=16 * 1024)
    if len(raw_summary) != receipt['bytes']:
        raise ValueError
    callback_capture = {key: captured[key] for key in (
        'normal_callback_cut', 'normal_callback_cut_verified', 'truncated')}
    return associate_response(raw_summary, expected_file_sha256=receipt['sha256'], expected_pins=pins,
        observer=observer, callback_capture={**callback_capture, 'native_request_links': links},
        expected_cut=cut, session=session, request_key=bytes.fromhex(settings['secret']), api_model=api_model)


def collect(*, env_path, target_path, prepared_dir, duration=240, node_view_mode=None, spec_overrides=None,
            runner_scope=None, pages_dir=None, expected_requests=None, topology=None, request_actors=None,
            target_file_sha256=None, normal_sampling_operation=None, normal_sampling_request_sha256=None,
            observe_normal_sampling_response=False):
    """The caller supplies the approved operation, optionally accepting a one-shot response observer."""
    normal_trial = normal_sampling_operation is not None or normal_sampling_request_sha256 is not None
    if type(observe_normal_sampling_response) is not bool or observe_normal_sampling_response and not normal_trial:
        raise ValueError(_NORMAL_SAMPLING_SCOPE_ERROR)
    if normal_trial and (not callable(normal_sampling_operation) or type(normal_sampling_request_sha256) is not str
            or len(normal_sampling_request_sha256) != 64
            or any(char not in '0123456789abcdef' for char in normal_sampling_request_sha256)
            or pages_dir is None or node_view_mode is not None or spec_overrides is not None):
        raise ValueError(_NORMAL_SAMPLING_SCOPE_ERROR)
    environment = load_environment(env_path)
    callback_fields = [field for field in (environment.runtime.platform or '').split('; ')
                       if field.startswith('normal_tp2_callbacks_sha256=')]
    if target_file_sha256 is not None or callback_fields:
        from tools.gpu_a_normal_tp2_callbacks import read_pinned_target

        target = read_pinned_target(target_path, target_file_sha256)
    else:
        target = json.loads(Path(target_path).read_text())
    capture_options = {}
    if 'normal_tp2_callbacks' in target:
        from tools.gpu_a_normal_tp2_callbacks import ERROR, environment_field, plan_sha256, read_preparation, verify_mounted_pod

        expected = target.get('normal_tp2_callbacks')
        if (target_file_sha256 is None or node_view_mode is not None
                or callback_fields != [environment_field(expected)] or target.get('lab') is not False
                or target.get('synthetic') is True):
            raise ValueError(ERROR)
        actual, files, prepared, settings = read_preparation(prepared_dir, expected['mode'])
        if any(expected[key] != value for key, value in actual.items()):
            raise ValueError(ERROR)
        capture_options = {
            'normal_callback_assets': {
                'target_file_sha256': target_file_sha256, 'plan_sha256': plan_sha256(expected),
                'files': {name: {'bytes': len(raw), 'sha256': sha256_hex(raw)} for name, raw in files.items()},
            },
            'normal_pod_check': lambda pod: verify_mounted_pod(target, environment, pod),
        }
    else:
        prepared = load_prepared(prepared_dir)
        settings = json.loads((Path(prepared_dir) / 'settings.json').read_text())
    if normal_trial and (not capture_options or prepared.get('scoped_runner_consumption') is not True
            or settings.get('scoped_runner_consumption') is not True):
        raise ValueError(_NORMAL_SAMPLING_SCOPE_ERROR)
    transport = KubernetesHTTP(target, environment, duration=duration, profile=ENGINE_CALLBACK_PROFILE)
    scope = {'pod': transport.binding['engine'], 'environment_ref': environment.ref().model_dump(mode='json'),
             'target_sha256': sha256_hex(canonical_json(target)), 'prepared_sha256': sha256_hex(canonical_json(prepared)),
             'session': prepared['session']}
    roots = settings.get('supply_roots') or []
    mount_before = transport.supply_mount_exclusivity(roots) if roots else None
    if normal_trial:
        value, trial, response_observation = _collect_normal_sampling_trial(
            transport, prepared['session'], pages_dir, normal_sampling_operation,
            normal_sampling_request_sha256, capture_options, observe_response=observe_normal_sampling_response)
    else:
        value = transport.engine_attempt_events(prepared['session'], archive=pages_dir, **capture_options)
    processes, truncated = value['processes'], value['truncated']
    page_refs = value.get('page_refs', [])
    page_capture = {k: value[k] for k in ('diagnostic_codes', 'source_events', 'scope') if k in value}
    records, summaries = {}, []
    native_request_links = []
    scoped_requested = bool(settings.get('scoped_runner_consumption') or prepared.get('scoped_runner_consumption'))
    if settings.get('runner_consumption') or scoped_requested:
        # /1 writers remain available for historical reproduction only.
        try:
            from guard.collectors import actor_consumption

            if scoped_requested and (settings.get('session') != prepared['session'] or any(
                    source.get('scoped_runner_consumption') is not True or source.get('runner_consumption') is not False
                    for source in (settings, prepared))):
                raise GpuInputError('GPU_INPUT_SCOPED_PREPARATION_MISMATCH')
            reviewed = (scoped_requested and target.get('lab') is False and target.get('synthetic') is not True and all(
                source.get('lab_faults') is False and source.get('lab_kernel_checks') is False
                for source in (settings, prepared)))
            record, result = actor_consumption.record(processes, topology=topology, request_actors=request_actors,
                truncated=truncated, expected_stage_ranks=runner_scope, expected_requests=expected_requests,
                reviewed_scoped_sources=reviewed, retain_request_links=bool(capture_options), qualification_scope={
                'engine': environment.engine.name, 'engine_version': environment.engine.version,
                'image_digest': environment.engine.image_digest, 'backend': 'gpu' if environment.runtime.gpu_count else 'cpu',
                'origin': transport.process_observation_origin}, **scope)
            native_request_links = result.pop('native_request_links', [])
            records['scoped-runner-consumption'] = {'record': record, 'validation': result}
            summaries.append(_summary(record, result))
        except GpuInputError as error:
            records['scoped-runner-consumption'] = {'contract_error': str(error)}
    if observe_normal_sampling_response:
        try:
            trial['response_association'] = _associate_normal_response(
                value, trial, response_observation, native_request_links, pages_dir=pages_dir, scope=scope,
                settings=settings, api_model=transport.api_model)
        except (KeyError, OSError, TypeError, ValueError):
            trial['diagnostic_codes'].append('NORMAL_SAMPLING_RESPONSE_ASSOCIATION_UNVERIFIED')
        finally:
            response_observation = None
    for flag, kind, make in SIMPLE:
        if not settings.get(flag):
            continue
        try:
            record, result = make(processes, truncated=truncated, wire_schema='guard-gpu-common-input/4', **scope)
        except GpuInputError as error:
            # 이 세션에서 그 경로를 일으키지 않았으면(예: 바인딩 0개) 계약이 형식 오류로 거부한다. 결과로만 남긴다.
            records[kind] = {'contract_error': str(error)}
            summaries.append({'kind': kind, 'contract_error': str(error)})
            continue
        records[kind] = {'record': record, 'validation': result}
        summaries.append(_summary(record, result))
    if prepared.get('worker_shards') is True or settings.get('worker_shards') is True:
        kind = 'worker-shard-load'
        if (prepared.get('worker_shards') is not True or settings.get('worker_shards') is not True
                or settings.get('session') != prepared['session']):
            records[kind] = {'contract_error': 'WORKER_SHARD_PREPARED_SCOPE_MISMATCH'}
        else:
            try:
                record, result = worker_shard_producer.record_from_processes(processes, **scope)
            except (KeyError, TypeError, ValueError):
                records[kind] = {'contract_error': 'WORKER_SHARD_CAPTURE_CONTRACT_ERROR'}
            else:
                records[kind] = {'record': record, 'validation': result}
        captured = records[kind]
        summaries.append(_summary(captured['record'], captured['validation']) if 'record' in captured
                         else {'kind': kind, 'contract_error': captured['contract_error']})
    if roots:
        files = producers.supply_open_files(processes)
        hashes = transport.engine_supply_hashes(roots, files)
        mount_after = transport.supply_mount_exclusivity(roots)
        record, result = producers.supply_read_record(processes, hashes, truncated=truncated, mount_before=mount_before,
                                                      mount_after=mount_after, wire_schema='guard-gpu-common-input/4', **scope)
        records['supply-reads'] = {'record': record, 'validation': result, 'mount_before': mount_before, 'mount_after': mount_after}
        summaries.append(_summary(record, result))
    if node_view_mode:
        value = collect_process_device(transport, scope, mode=node_view_mode, spec_overrides=spec_overrides)
        records['process-device-provider'] = value
        summaries.append(_summary(value['record'], value['validation']) if 'record' in value
                         else {'kind': 'process-device-provider', 'contract_error': value['contract_error']})
    return {'schema': SCHEMA, 'session': prepared['session'], 'truncated': truncated,
            'processes': [{'pid': p['pid'], 'start_ticks': p['start_ticks'], 'rows': len(p['events'])} for p in processes],
            'summaries': summaries, 'records': records, 'raw_page_refs': page_refs, 'page_capture': page_capture,
            **({'mounted_source_attestation': value['mounted_source_attestation']}
               if 'mounted_source_attestation' in value else {}),
            **({'normal_sampling_trial': trial} if normal_trial else {}),
            **({'native_request_links': native_request_links} if native_request_links else {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--env', required=True); parser.add_argument('--target', required=True)
    parser.add_argument('--prepared', required=True); parser.add_argument('--out', required=True)
    parser.add_argument('--target-file-sha256', help='Independent raw target pin for prepared normal TP2 callbacks')
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
                    target_file_sha256=args.target_file_sha256,
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
