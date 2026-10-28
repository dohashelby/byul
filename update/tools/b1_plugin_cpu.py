"""SUP-10 actual installed entry points in fresh isolated CPU subprocesses.

No monkeypatch, engine, daemon, network listener or model download is used.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from guard.executors.plugin_loading import CASES, GROUPS, NAMES, sanitize_events, project
SOURCE_COMMIT = '5d35ce3934a7804b298559d2d99eec33792e1eb9'
FIXTURE_DIST = 'byul-b-cpu-plugin-probe'


def read_events(path):
    events, status = [], 'completed'
    if not path.exists():
        return {'events': events, 'status': status}
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeError):
        return {'events': [], 'status': 'read_error'}
    for line in lines:
        try:
            events.extend(sanitize_events([json.loads(line)]))
        except (ValueError, TypeError):
            status = 'partial'
    return {'events': events, 'status': status}


def child(case, output):
    import asyncio
    from argparse import Namespace
    import vllm.plugins as plugins
    key, group, _ = next(row for row in CASES if row[0] == case)
    events_path = output.with_suffix('.events.jsonl')
    result = {'case_id': key, 'group': group, 'configured_names': os.environ.get('VLLM_PLUGINS'),
              'runner_hook_verified': False, 'status': 'execution_error'}
    try:
        installed = list(metadata.entry_points(group=group))
        if not installed or any(ep.name not in NAMES or not ep.value.startswith('byul_b_cpu_probe.') for ep in installed):
            raise RuntimeError('Controlled fixture inventory required')
        result['installed_names'] = sorted(ep.name for ep in installed)
        result['fixture_version'] = metadata.version(FIXTURE_DIST)
        result['loader_source_bytes_sha256'] = hashlib.sha256(Path(plugins.__file__).read_bytes()).hexdigest()
        if group == GROUPS[1]:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from vllm.plugins.endpoint_plugins.interface import attach_endpoint_plugins, init_endpoint_plugins_state
        elif group == GROUPS[2]:
            from vllm.v1.sample.logits_processor import _load_logitsprocs_plugins
        elif group == GROUPS[3]:
            from vllm.plugins.io_processors import get_io_processor
        bootstrap = read_events(events_path)
        if bootstrap['events'] or bootstrap['status'] != 'completed':
            raise RuntimeError('Fixture imported during bootstrap; target attribution unavailable')
        result['target_started'] = True
        if group == GROUPS[0]:
            plugins.load_general_plugins()
        elif group == GROUPS[1]:
            app = FastAPI()
            attach_endpoint_plugins(app, ('generate',))
            asyncio.run(init_endpoint_plugins_state(None, app.state, Namespace()))
            result['selected_plugin_names'] = [p.name for p in app.state.endpoint_plugins]
            with TestClient(app) as client:
                result['asgi_control_status'] = client.get('/byul-cpu-byul-approved').status_code
        elif group == GROUPS[2]:
            classes = _load_logitsprocs_plugins()
            result['loaded_class_count'] = len(classes)
        else:
            # Explicit plugin-from-init path needs no engine config/renderer.
            obj = get_io_processor(None, None, plugin_from_init='byul-approved')
            result['selected_plugin_type'] = type(obj).__name__
        collected = read_events(events_path)
        result.update(status='completed', events=collected['events'], event_collection_status=collected['status'])
    except Exception as error:
        collected = read_events(events_path)
        result.update(error_type=type(error).__name__, missing_module=getattr(error, 'name', None),
                      events=collected['events'], event_collection_status=collected['status'])
    # Keep primary observations even if this independent normal collector fails.
    try:
        before = len(read_events(events_path)['events'])
        os.environ['VLLM_PLUGINS'] = 'byul-approved'
        normal = plugins.load_plugins_by_group(GROUPS[0])
        normal['byul-approved']()
        after = read_events(events_path)
        result['after'] = {'status': after['status'], 'events': after['events'][before:]}
    except Exception:
        result['after'] = {'status': 'execution_error'}
    output.write_text(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--case', choices=[row[0] for row in CASES])
    args = parser.parse_args()
    root = args.source_root.resolve()
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != SOURCE_COMMIT:
        raise SystemExit('Reference SHA mismatch')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--', 'vllm'], text=True):
        raise SystemExit('Reference source modified')
    import vllm
    if Path(vllm.__file__).resolve() != root / 'vllm/__init__.py':
        raise SystemExit('Reference import origin mismatch')
    from guard.executors import plugin_loading
    if not Path(plugin_loading.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1] / 'src'):
        raise SystemExit('B-1 import origin mismatch')
    if args.case:
        return child(args.case, args.workdir / (args.case + '.json'))
    args.workdir.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    results = []
    for case, _, configured in CASES:
        env = os.environ.copy()
        env['BYUL_B_PLUGIN_EVENTS'] = str(args.workdir / (case + '.events.jsonl'))
        if configured is None: env.pop('VLLM_PLUGINS', None)
        else: env['VLLM_PLUGINS'] = configured
        command = [sys.executable, str(Path(__file__).resolve()), '--source-root', str(root), '--workdir', str(args.workdir), '--case', case]
        began = time.perf_counter()
        completed = subprocess.run(command, env=env, capture_output=True)
        path = args.workdir / (case + '.json')
        row = json.loads(path.read_text()) if path.exists() else {'case_id': case, 'status': 'child_execution_error'}
        row['parent_wall_seconds'] = time.perf_counter() - began
        row['exit_code'] = completed.returncode
        results.append(row)
        print(case, row['status'], flush=True)
    dist = metadata.distribution(FIXTURE_DIST)
    files = [p for p in dist.files if str(p).startswith('byul_b_cpu_probe/') and str(p).endswith('.py')]
    record = {'source_commit': SOURCE_COMMIT, 'scope': 'reference_installed_plugin_cpu_only',
              'fixture_origin': 'DECLARED controlled local distribution', 'installed_target': None,
              'results': results, 'observations': project(results),
              'fixture_file_bytes_sha256': {str(p): hashlib.sha256(Path(dist.locate_file(p)).read_bytes()).hexdigest() for p in files},
              'parent_wall_seconds': time.perf_counter()-start}
    (args.workdir / 'result.json').write_text(json.dumps(record, indent=2))
    return 0 if all(row['status'] == 'completed' for row in results) else 2


if __name__ == '__main__':
    raise SystemExit(main())
