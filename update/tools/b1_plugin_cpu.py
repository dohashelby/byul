"""SUP-10 actual installed entry points in fresh isolated CPU subprocesses.

No monkeypatch, engine, daemon, network listener or model download is used.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as metadata
import json
import inspect
import tomllib
import os
from pathlib import Path
import subprocess
import sys
import time

from guard.executors.plugin_loading import CASES, GROUPS, NAMES, sanitize_events, project
from guard.executors.structured_tools_cpu import _verify_source, _validate_target031_dependencies
from tools import lif14_profiler_cpu as component_gate
SOURCE_COMMIT = '5d35ce3934a7804b298559d2d99eec33792e1eb9'
FIXTURE_DIST = 'byul-b-cpu-plugin-probe'
TARGET031_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
PRODUCT_COMPONENT_COMMIT = '0d0c116c4a1ed1ef1ba93a80b236bdedd2d73670'
FIXTURE_COMPONENT_COMMIT = 'ac8ffbadbb85dbbfe7c96fa36785ae9546e2c8c7'
GATE_SHA256 = '3ada5b08959d2935d8cf0d43d60e09eeaca4c0c560eda8accd22da9631f41d75'
FIXTURE_SCOPE = ('update/tests/fixtures/b_plugin_probe',)
SOURCE_PROFILES = {'reference5d': SOURCE_COMMIT, 'target031': TARGET031_COMMIT}

# Reviewed ac8 fixture bytes; runtime never regenerates approval from Git/HEAD.
FIXTURE_SHA256 = {
    'byul_b_cpu_probe/mismatch.py': '673f3b7b0b397cb2a5f387d6b2b7875b2e625e7641aeb74ebf619e53f4f4e641',
    'byul_b_cpu_probe/approved.py': '49a07cc131adb9da10ddaeea56ebb6354b1dc5becdc04c3162e7ef47c4057c82',
    'byul_b_cpu_probe/events.py': '178450a5b777c513e14d13da79632b8eb6361e82b7d1233ac951d90570021d30',
    'byul_b_cpu_probe/__init__.py': '318f36ffde07759b379e68e7a20c7750ce389513a15911abd38537f234a15dfd',
    'byul_b_cpu_probe/runtime.py': '5a027e82be3e323a3e151ac14358af9508677ec8f2a53326609bb7092b8567ad',
    'byul_b_cpu_probe/unapproved.py': '007d904c733064ba4e68015034461553d5fca14afbf8e1e6913c486b4a64336a',
}
FIXTURE_PROJECT_SHA256 = '7ebd4de428af0bf4349e0af912c2b016c057b3595265af3d7e3d8f528f1f2b19'
FIXTURE_ENTRYPOINTS = {
    GROUPS[0]: {'byul-approved': 'byul_b_cpu_probe.approved:general', 'byul-unapproved': 'byul_b_cpu_probe.unapproved:general'},
    GROUPS[1]: {'byul-approved': 'byul_b_cpu_probe.approved:endpoint', 'byul-unapproved': 'byul_b_cpu_probe.unapproved:endpoint',
                'byul-task-mismatch': 'byul_b_cpu_probe.mismatch:endpoint'},
    GROUPS[2]: {'byul-approved': 'byul_b_cpu_probe.approved:Logits', 'byul-unapproved': 'byul_b_cpu_probe.unapproved:Logits'},
    GROUPS[3]: {'byul-approved': 'byul_b_cpu_probe.approved:io_factory', 'byul-unapproved': 'byul_b_cpu_probe.unapproved:io_factory'},
}


def verify_checkout(repo):
    expected = repo / 'update/tools/lif14_profiler_cpu.py'
    try:
        if (expected.is_symlink() or not Path(component_gate.__file__).resolve().samefile(expected)
                or not Path(inspect.getsourcefile(component_gate.verify_component)).resolve().samefile(expected)
                or hashlib.sha256(expected.read_bytes()).hexdigest() != GATE_SHA256):
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('PLUGIN_CPU_COMPONENT_GATE_IDENTITY_MISMATCH') from None
    component_gate.verify_component(repo)  # Reuse helper own identity/hash/scope checks.
    component_gate.snapshot_helper.verify_snapshot(repo, PRODUCT_COMPONENT_COMMIT, component_gate.PINNED_PATHS)
    component_gate.snapshot_helper.verify_snapshot(repo, FIXTURE_COMPONENT_COMMIT, FIXTURE_SCOPE)


def verify_fixture_resolver(dist):
    """Inspect the real finder chain without importing the fixture package."""
    package = 'byul_b_cpu_probe'
    package_dir = Path(dist.locate_file(package)).resolve()
    def find(name, path):
        # Calling find_spec directly for submodules avoids util.find_spec's
        # implicit parent-package import. This uses the same finder order.
        for finder in sys.meta_path:
            method = getattr(finder, 'find_spec', None)
            if method is not None:
                spec = method(name, path, None)
                if spec is not None:
                    return spec
        return None
    top = find(package, None)
    if (top is None or top.origin is None or not Path(top.origin).resolve().samefile(package_dir / '__init__.py')
            or top.submodule_search_locations is None
            or {Path(path).resolve() for path in top.submodule_search_locations} != {package_dir}):
        raise ValueError('PLUGIN_CPU_FIXTURE_RESOLVER_MISMATCH')
    for relative in FIXTURE_SHA256:
        if relative.endswith('/__init__.py'):
            continue
        name = relative[:-3].replace('/', '.')
        spec = find(name, list(top.submodule_search_locations))
        if spec is None or spec.origin is None or not Path(spec.origin).resolve().samefile(dist.locate_file(relative)):
            raise ValueError('PLUGIN_CPU_FIXTURE_RESOLVER_MISMATCH')
    loaded = sys.modules.get(package)
    if loaded is not None and {Path(path).resolve() for path in getattr(loaded, '__path__', [])} != {package_dir}:
        raise ValueError('PLUGIN_CPU_FIXTURE_RESOLVER_MISMATCH')


def verify_fixture(repo):
    root = repo / FIXTURE_SCOPE[0]
    project_bytes = (root / 'pyproject.toml').read_bytes()
    if hashlib.sha256(project_bytes).hexdigest() != FIXTURE_PROJECT_SHA256:
        raise ValueError('PLUGIN_CPU_FIXTURE_PROJECT_MISMATCH')
    definition = tomllib.loads(project_bytes.decode())['project']
    dist = metadata.distribution(FIXTURE_DIST)
    if dist.version != definition['version'] or definition['name'] != FIXTURE_DIST:
        raise ValueError('PLUGIN_CPU_FIXTURE_VERSION_MISMATCH')
    expected = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*.py')}
    if {name: hashlib.sha256(data).hexdigest() for name, data in expected.items()} != FIXTURE_SHA256:
        raise ValueError('PLUGIN_CPU_FIXTURE_SOURCE_MISMATCH')
    actual = {str(path): Path(dist.locate_file(path)).read_bytes() for path in dist.files
              if str(path).startswith('byul_b_cpu_probe/') and str(path).endswith('.py')}
    if actual != expected:
        raise ValueError('PLUGIN_CPU_FIXTURE_BYTES_MISMATCH')
    verify_fixture_resolver(dist)
    for group in GROUPS:
        planned = FIXTURE_ENTRYPOINTS[group]
        if definition['entry-points'][group] != planned:
            raise ValueError('PLUGIN_CPU_FIXTURE_ENTRYPOINT_MISMATCH')
        installed = list(metadata.entry_points(group=group))
        if (len(installed) != len(planned) or {ep.name: ep.value for ep in installed} != planned
                or any(ep.dist.metadata['Name'] != FIXTURE_DIST for ep in installed)):
            raise ValueError('PLUGIN_CPU_FIXTURE_ENTRYPOINT_MISMATCH')
    for name, module in tuple(sys.modules.items()):
        if name != 'byul_b_cpu_probe' and not name.startswith('byul_b_cpu_probe.'):
            continue
        relative = name.replace('.', '/') + ('/__init__.py' if hasattr(module, '__path__') else '.py')
        if relative not in expected or not Path(module.__file__).resolve().samefile(dist.locate_file(relative)):
            raise ValueError('PLUGIN_CPU_FIXTURE_IMPORT_MISMATCH')
    return {path: hashlib.sha256(data).hexdigest() for path, data in actual.items()}


def verify_profile(root, source_profile):
    if source_profile not in SOURCE_PROFILES or sys.flags.optimize or sys.version_info[:2] != (3, 12):
        raise ValueError('PLUGIN_CPU_SOURCE_PROFILE_INVALID')
    repo = Path(__file__).resolve().parents[2]
    verify_checkout(repo)
    _verify_source(root, expected_commit=SOURCE_PROFILES[source_profile])
    if source_profile == 'target031':
        _validate_target031_dependencies()
    return verify_fixture(repo)


def verify_loaded_target(root):
    for name, module in tuple(sys.modules.items()):
        if name != 'vllm' and not name.startswith('vllm.'):
            continue
        filename = getattr(module, '__file__', None)
        if filename is None or not Path(filename).resolve().is_relative_to(root / 'vllm'):
            raise ValueError('PLUGIN_CPU_TARGET_IMPORT_MISMATCH')


def post_validation(root, source_profile, *, producer_sha256):
    try:
        verify_profile(root, source_profile)
        verify_loaded_target(root)
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != producer_sha256:
            raise ValueError('PLUGIN_CPU_COLLECTOR_BYTES_CHANGED')
        return {'status': 'verified'}
    except ValueError as error:
        return {'status': 'mismatch', 'error_type': type(error).__name__}
    except Exception as error:
        return {'status': 'unavailable', 'error_type': type(error).__name__}



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


def child(case, output, *, return_record=False, source_root=None):
    import asyncio
    from argparse import Namespace
    import vllm.plugins as plugins
    key, group, configured = next(row for row in CASES if row[0] == case)
    events_path = output.with_suffix('.events.jsonl')
    result = {'case_id': key, 'group': group, 'configured_names': configured,
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
        if source_root is not None:
            verify_loaded_target(source_root)
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
    if return_record:
        return result
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
    return 0


def capture(source_root: Path, workdir: Path, *, source_profile: str='reference5d') -> dict:
    root, workdir = Path(source_root).resolve(), Path(workdir).resolve()
    fixture_hashes = verify_profile(root, source_profile)
    import vllm
    import torch
    if torch.version.cuda is not None:
        raise ValueError('PLUGIN_CPU_REQUIRES_CPU_TORCH')
    verify_loaded_target(root)
    producer_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    workdir.mkdir(parents=True, exist_ok=False)
    start, results = time.perf_counter(), []
    for case, _, configured in CASES:
        env = dict(os.environ, BYUL_B_PLUGIN_EVENTS=str(workdir / (case + '.events.jsonl')),
            VLLM_TARGET_DEVICE='cpu', CUDA_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        if configured is None:
            env.pop('VLLM_PLUGINS', None)
        else:
            env['VLLM_PLUGINS'] = configured
        command = [sys.executable, str(Path(__file__).resolve()), '--source-root', str(root),
            '--source-profile', source_profile, '--workdir', str(workdir), '--case', case]
        began = time.perf_counter()
        try:
            completed = subprocess.run(command, env=env, capture_output=True)
            path = workdir / (case + '.json')
            row = json.loads(path.read_text()) if path.exists() else {'case_id': case, 'status': 'child_execution_error'}
            if row.get('case_id') != case:
                raise ValueError('PLUGIN_CPU_CASE_ATTRIBUTION_MISMATCH')
            row['exit_code'] = completed.returncode
            if completed.returncode != 0:
                row['status'] = 'execution_error'
        except Exception as error:
            row = {'case_id': case, 'status': 'child_execution_error', 'error_type': type(error).__name__}
        row['parent_wall_seconds'] = time.perf_counter() - began
        results.append(row)
        print(case, row['status'], flush=True)
    post = post_validation(root, source_profile, producer_sha256=producer_hash)
    complete = post['status'] == 'verified' and all(row.get('status') == 'completed'
        and row.get('after', {}).get('status') == 'completed' and
        {'plugin': 'byul-approved', 'phase': 'general_hook'} in row.get('after', {}).get('events', []) for row in results)
    record = {'source_commit': SOURCE_PROFILES[source_profile], 'source_profile': source_profile,
        'product_component_commit': PRODUCT_COMPONENT_COMMIT,
        'fixture_component_commit': FIXTURE_COMPONENT_COMMIT,
        'source_validation': {'pre': 'verified', 'post': post, 'boundary': 'source_fixture_component'},
        'capture_status': 'completed' if complete else 'incomplete',
        'scope': 'reference_installed_plugin_cpu_only' if source_profile == 'reference5d' else 'target031_source_installed_plugin_cpu_only',
        'fixture_origin': 'DECLARED controlled local distribution', 'installed_target': None,
        'runner_hook_verified': False, 'endpoint_route_collision_exercised': False,
        'endpoint_openapi_replacement_verified': False, 'results': results, 'observations': project(results),
        'fixture_file_bytes_sha256': fixture_hashes, 'own_producer_sha256': producer_hash,
        'parent_wall_seconds': time.perf_counter() - start}
    record['auxiliary_write'] = {'status': 'completed'}
    try:
        with (workdir / 'result.json').open('x') as stream:
            json.dump(record, stream, indent=2)
    except Exception as error:
        record['capture_status'] = 'incomplete'
        record['auxiliary_write'] = {'status': 'failed', 'error_type': type(error).__name__}
    return record


def main():
    if sys.flags.optimize:
        raise ValueError('PLUGIN_CPU_REQUIRES_ASSERTIONS')
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-profile', choices=tuple(SOURCE_PROFILES), default='reference5d')
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--case', choices=[row[0] for row in CASES])
    args = parser.parse_args()
    root = args.source_root.resolve()
    if args.case:
        verify_profile(root, args.source_profile)
        import vllm
        verify_loaded_target(root)
        output = args.workdir / (args.case + '.json')
        if output.exists():
            raise ValueError('PLUGIN_CPU_FRESH_OUTPUT_REQUIRED')
        expected = next(row[2] for row in CASES if row[0] == args.case)
        if os.environ.get('VLLM_PLUGINS') != expected:
            raise ValueError('PLUGIN_CPU_CASE_CONFIGURATION_MISMATCH')
        if os.environ.get('BYUL_B_PLUGIN_EVENTS') != str(output.with_suffix('.events.jsonl')):
            raise ValueError('PLUGIN_CPU_EVENT_BINDING_MISMATCH')
        producer_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result = child(args.case, output, return_record=True, source_root=root)
        post = post_validation(root, args.source_profile, producer_sha256=producer_hash)
        result.update(source_commit=SOURCE_PROFILES[args.source_profile], source_profile=args.source_profile,
            source_validation={'pre':'verified','post':post,'boundary':'source_fixture_component'})
        if post['status'] != 'verified':
            result['status'] = 'execution_error'
        with output.open('x') as stream:
            json.dump(result, stream, indent=2)
        return 0 if post['status'] == 'verified' else 2
    record = capture(root, args.workdir, source_profile=args.source_profile)
    return 0 if record['capture_status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
