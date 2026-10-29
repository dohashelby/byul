"""Owned CPU profiler lifecycle observations; torch is imported only in the child."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from guard.models import DataOrigin, ExecStatus
from .base import DraftArtifact, EvidenceDraft
from .http_common import incomplete_artifact_capture
from .structured_tools_cpu import _verify_source

SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
PROFILE = 'torch-profiler-lifecycle-reference-cpu-v1'
TAG = 'byul_lif14_cpu_work'
PREFIX = 'BYUL_LIF14_SAFE='
CASE_IDS = ('normal', 'trace_directory_failure')
COLLISION_BYTES = b'owned directory collision fixture'


def native_state(torch):
    probe = getattr(torch.autograd, '_profiler_enabled', None)
    if not callable(probe):
        return {'value': None, 'collected': False}
    try:
        value = probe()
        return {'value': value if type(value) is bool else None, 'collected': type(value) is bool}
    except Exception:
        return {'value': None, 'collected': False}



def qualify_native_after(before, during, after):
    qualified = before == {'value': False, 'collected': True} and during == {'value': True, 'collected': True}
    return qualified, after if qualified else {'value': None, 'collected': False}


def restore_owned_directory(path, owner):
    if (path.parent.resolve() != owner.resolve() or path.is_symlink() or not path.is_file()
            or path.read_bytes() != COLLISION_BYTES):
        raise ValueError('PROFILER_CPU_RECOVERY_OWNERSHIP_UNVERIFIED')
    path.unlink()
    path.mkdir()

def output_summary(path):
    result = {'trace_files': 0, 'table_files': 0, 'tag_events': 0, 'files': []}
    if not path.is_dir():
        return result
    for file in sorted(path.rglob('*')):
        if not file.is_file():
            continue
        data = file.read_bytes()
        kind = 'trace' if file.name.endswith('.pt.trace.json') else 'table' if file.name.startswith('profiler_out_') else 'other'
        result['files'].append({'kind': kind, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        if kind == 'trace':
            parsed = json.loads(data)
            result['trace_files'] += 1
            result['tag_events'] += sum(event.get('name') == TAG for event in parsed.get('traceEvents', []))
        if kind == 'table':
            result['table_files'] += 1
    return result


def _phase(wrapper, torch, log_events):
    row = {'native_before': native_state(torch)}
    if row['native_before'] == {'value': True, 'collected': True}:
        row['status'] = 'preexisting_native_collection'
        return row
    begin = time.perf_counter()
    try:
        wrapper.start()
        row.update(start_returned=True, wrapper_running_during=wrapper.is_running,
                   native_during=native_state(torch))
        with wrapper.annotate_context_manager(TAG):
            (torch.ones((4, 4)) + 1).sum().item()
        codes = {wrapper._stop.__code__: 'wrapper_backend_stop',
                 wrapper.profiler.stop.__code__: 'torch_stop',
                 wrapper._profiler_kwargs['on_trace_ready'].__code__: 'trace_handler',
                 wrapper.profiler.export_chrome_trace.__code__: 'trace_export'}
        entries = set()
        old_profile = sys.getprofile()
        def profile(frame, event, arg):
            if event == 'call' and frame.f_code in codes:
                entries.add(codes[frame.f_code])
        stop_begin = time.perf_counter()
        count_before = len(log_events)
        try:
            if old_profile is None:
                sys.setprofile(profile)
            returned = wrapper.stop()
            row['stop_return'] = 'none' if returned is None else 'other'
        finally:
            if old_profile is None:
                sys.setprofile(None)
            row['stop_elapsed_seconds'] = time.perf_counter() - stop_begin
            row['stop_entries'] = sorted(entries) if old_profile is None else None
            row['stop_entry_collection_status'] = 'collected' if old_profile is None else 'unavailable_existing_profile'
            row['stop_warning_types'] = log_events[count_before:]
            probe = native_state(torch)
            qualified, effective = qualify_native_after(row['native_before'], row.get('native_during'), probe)
            row.update(wrapper_running_after=wrapper.is_running, native_after_probe=probe,
                       native_probe_qualified=qualified, native_after=effective)
        try:
            row['recorded_tag_events'] = sum(event.name == TAG for event in wrapper.profiler.events())
            row['event_collection_status'] = 'collected'
        except Exception as error:
            row.update(recorded_tag_events=None, event_collection_status='unavailable', event_error_type=type(error).__name__)
        row['status'] = 'completed'
    except Exception as error:
        row.update(status='execution_error', error_type=type(error).__name__)
    row['elapsed_seconds'] = time.perf_counter() - begin
    return row


def child_case(source_root, owned_parent, case_id):
    """Invoke real source methods; no injected methods, decoded values or raw logs."""
    if sys.flags.optimize or case_id not in CASE_IDS:
        raise ValueError('PROFILER_CPU_EXECUTION_PROFILE_INVALID')
    source_root = Path(source_root).resolve()
    _verify_source(source_root, expected_commit=SOURCE_COMMIT)
    import importlib.metadata
    import torch
    from vllm.config import ProfilerConfig
    from vllm.profiler import wrapper as module
    if torch.version.cuda is not None or sys.version_info[:2] != (3, 12):
        raise ValueError('PROFILER_CPU_ENVIRONMENT_INVALID')
    objects = (module.WorkerProfiler, module.TorchProfilerWrapper, ProfilerConfig)
    files = {Path(inspect.getfile(obj)).resolve() for obj in objects}
    if any(not path.is_relative_to(source_root / 'vllm') for path in files):
        raise ValueError('PROFILER_SOURCE_IMPORT_MISMATCH')
    metadata = {'source_commit': SOURCE_COMMIT, 'profile': PROFILE,
        'dependencies': {'torch': importlib.metadata.version('torch')},
        'source_file_bytes_sha256': {path.relative_to(source_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}
    logs = []
    class Warnings(logging.Handler):
        def emit(self, record):
            if record.msg == 'Failed to stop profiler: %s':
                logs.append(type(record.args[0]).__name__)
    handler = Warnings()
    module.logger.addHandler(handler)
    wrappers, cleanup = [], []
    directory = None
    result = {'case_id': case_id, **metadata, 'next_instance': 'same_wrapper', 'constructor_returned': False}
    def cleanup_owned():
        for instance in reversed(wrappers):
            entry = {'shutdown_returned': False}
            try:
                instance.shutdown()
                entry['shutdown_returned'] = True
                state = native_state(torch)
                if state == {'value': True, 'collected': True} and hasattr(instance, 'profiler'):
                    entry['owned_native_stop_attempted'] = True
                    instance.profiler.stop()
            except Exception as error:
                entry['error_type'] = type(error).__name__
            entry['native_after'] = native_state(torch)
            entry['wrapper_running_after'] = instance.is_running
            cleanup.append(entry)
    try:
        with tempfile.TemporaryDirectory(prefix='lif14-owned-', dir=owned_parent) as directory:
            directory = Path(directory)
            try:
                def make(path):
                    config = ProfilerConfig(profiler='torch', torch_profiler_dir=str(path),
                        torch_profiler_activities=['CPU'], delay_iterations=0, max_iterations=0,
                        warmup_iterations=0, wait_iterations=0, torch_profiler_with_stack=False,
                        torch_profiler_record_shapes=False, torch_profiler_with_memory=False,
                        torch_profiler_with_flops=False, torch_profiler_use_gzip=False)
                    instance = module.TorchProfilerWrapper(config, worker_name='byul-lif14', local_rank=1, activities=['CPU'])
                    wrappers.append(instance)
                    return instance
                output = directory / 'primary'
                if case_id == 'trace_directory_failure':
                    output.write_bytes(COLLISION_BYTES)
                result['injection_stage'] = 'default_trace_handler_directory_creation' if case_id != 'normal' else None
                primary = make(output)
                result['constructor_returned'] = True
                result['primary'] = _phase(primary, torch, logs)
                result['primary']['output'] = output_summary(output)
                if case_id == 'trace_directory_failure':
                    restore_owned_directory(output, directory)
                    result['recovery_action'] = 'owned_regular_file_replaced_with_directory'
                result['next'] = _phase(primary, torch, logs)
                result['next']['output'] = output_summary(output)
                result['capture_status'] = 'completed'
            finally:
                cleanup_owned()
    except Exception as error:
        result.update(capture_status='execution_error', error_type=type(error).__name__)
    finally:
        module.logger.removeHandler(handler)
        qualified = any(result.get(phase, {}).get('native_probe_qualified') is True for phase in ('primary', 'next'))
        for entry in cleanup:
            entry['native_after_probe'] = entry['native_after']
            if not qualified:
                entry['native_after'] = {'value': None, 'collected': False}
        result['cleanup'] = cleanup
        result['final_native_probe'] = native_state(torch)
        result['final_native'] = result['final_native_probe'] if qualified else {'value': None, 'collected': False}
        result['owned_directory_removed'] = directory is not None and not Path(directory).exists()
    _verify_source(source_root, expected_commit=SOURCE_COMMIT)
    return result


def capture(source_root, owned_parent):
    """Use one child per actual case; raw stdout/stderr never become artifacts."""
    if sys.flags.optimize:
        raise ValueError('PROFILER_CPU_REQUIRES_ASSERTIONS')
    source_root = Path(source_root).resolve()
    _verify_source(source_root, expected_commit=SOURCE_COMMIT)
    owned_parent = Path(owned_parent).resolve()
    owned_parent.mkdir(parents=True, exist_ok=True)
    repo_update = Path(__file__).resolve().parents[3]
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(repo_update / 'src'), str(repo_update), str(source_root)]),
        'VLLM_TARGET_DEVICE': 'cpu', 'CUDA_VISIBLE_DEVICES': '', 'VLLM_PLUGINS': '',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    rows = []
    for case_id in CASE_IDS:
        process = subprocess.run([sys.executable, '-m', __name__, '--child-case', case_id,
            '--source-root', str(source_root), '--owned-parent', str(owned_parent)], env=env, capture_output=True, text=True)
        values = [line[len(PREFIX):] for line in process.stdout.splitlines() if line.startswith(PREFIX)]
        try:
            row = json.loads(values[-1])
            if row['case_id'] != case_id or row['source_commit'] != SOURCE_COMMIT or process.returncode != 0:
                raise ValueError
        except (IndexError, KeyError, TypeError, ValueError):
            row = {'case_id': case_id, 'capture_status': 'child_capture_unavailable', 'child_returncode': process.returncode}
        rows.append(row)
    _verify_source(source_root, expected_commit=SOURCE_COMMIT)
    return {'profile': PROFILE, 'source_commit': SOURCE_COMMIT, 'scope': 'reference_cpu_profiler_lifecycle',
        'installed_target': None, 'enterprise_deadline_policy': None, 'rows': rows,
        'own_producer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def project(record):
    rows = record['rows']
    phases = [row.get(phase, {}) for row in rows for phase in ('primary', 'next')]
    known = [phase for phase in phases if phase.get('native_probe_qualified') is True and type(phase.get('wrapper_running_after')) is bool
             and phase.get('native_after', {}).get('collected') is True]
    mismatch = any(phase['wrapper_running_after'] is False and phase['native_after']['value'] is True for phase in known)
    return {'profiler_cpu_stop_mismatch': {'value': mismatch if known else None, 'collected': bool(known)},
        'profiler_cpu_trials': {'value': rows or None, 'collected': bool(rows)},
        'profiler_cpu_deadline_policy': {'value': None, 'collected': False}}


def judge_params():
    return {'axes': {'functional': {'fail_when_any': [{'obs': 'profiler_cpu_stop_mismatch', 'op': 'eq', 'value': True}]}}}


class ProfilerLifecycleCPUExecutor:
    name, version = 'profiler_lifecycle_cpu', '0.1.0'

    def __init__(self, cfg):
        if type(cfg) is not dict or set(cfg) != {'source_root', 'owned_parent'}:
            raise ValueError('PROFILER_CPU_CONFIGURATION_INVALID')
        self.cfg = dict(cfg)

    def run(self, check, env, policy_values, adapter, workdir):
        if (check.id != 'LIF-14' or policy_values or not env.hash_ok() or env.engine.name != 'vllm'
                or env.engine.version != 'reference-' + SOURCE_COMMIT):
            raise ValueError('PROFILER_CPU_ENVIRONMENT_BINDING_INVALID')
        start = datetime.now(timezone.utc)
        record = capture(**self.cfg)
        obs = project(record)
        complete = all(row.get('capture_status') == 'completed' for row in record['rows'])
        normal = next((row for row in record['rows'] if row['case_id'] == 'normal'), {})
        normal_ok = (normal.get('primary', {}).get('recorded_tag_events', 0) or 0) > 0 and normal.get('primary', {}).get('output', {}).get('tag_events', 0) > 0
        draft = EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED,
            data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control={'attempted': bool(normal), 'succeeded': normal_ok}, observations=obs,
            challenges=[{'step': step, 'attempted': complete} for step in check.challenge_steps],
            config={'profile': PROFILE, 'source_commit': SOURCE_COMMIT}, started_at=start,
            finished_at=datetime.now(timezone.utc), abort_reason=None if complete else 'PROFILER_CPU_CAPTURE_INCOMPLETE',
            cleanup_errors=['PROFILER_CPU_NATIVE_SHUTDOWN_UNVERIFIED'] if any(
                row.get('final_native') != {'value': False, 'collected': True} for row in record['rows']) else [])
        if any(row.get('owned_directory_removed') is not True for row in record['rows']):
            draft.cleanup_errors.append('PROFILER_CPU_OWNED_DIRECTORY_CLEANUP_UNVERIFIED')
        path = Path(workdir) / 'profiler-lifecycle-cpu.json'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x') as stream:
                json.dump(record, stream, indent=2)
            draft.artifacts = [DraftArtifact(name=path.name, path=path, collector=PROFILE)]
        except Exception:
            return incomplete_artifact_capture(draft)
        return draft


def _child_main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--child-case', choices=CASE_IDS, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--owned-parent', type=Path, required=True)
    args = parser.parse_args()
    print(PREFIX + json.dumps(child_case(args.source_root, args.owned_parent, args.child_case)))


if __name__ == '__main__':
    _child_main()
