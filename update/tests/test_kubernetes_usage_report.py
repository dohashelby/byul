"""OBS-04·05 Kubernetes 전송: 원 usage_report의 판정을 그대로 쓰고 읽기만 Kubernetes 고정 프로그램으로 바꾼다."""
import builtins
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.judge import judge_evidence
from guard.executors import kubernetes_usage_report as module
from guard.executors.kubernetes_lifecycle_capture import KubernetesCaptureError
from guard.executors.owned_capture import _FILE_READER, MAX_BYTES
from guard.executors.registry import EXECUTORS
from guard.executors.usage_report import SETTINGS, _READER, _snapshot_command
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP
from tests.builders import T0, evidence, policy
from tests.test_model_preparation_integration import FullWorld, full_inputs

PATHS = ['/root/.config/vllm/usage_stats.json']


def reader(*, sink=False, prefixes=None):
    value = module._UsageReader.__new__(module._UsageReader)
    value.binding = {'engine': {'container': 'sink' if sink else 'engine'}}
    value._paths, value._sink = ([] if sink else list(PATHS)), sink
    value._local = False
    program, arguments = _snapshot_command(value._paths, prefixes)
    value.snapshot_argv = ["python3", "-c", program, *arguments]
    return value


def test_engine_reader_runs_only_the_original_usage_snapshot_and_the_sink_reader_only_the_fixed_receipt_file():
    engine = reader()
    assert engine._program('engine', ['python3', '-c', _READER, ','.join(SETTINGS), *PATHS], 262144) == 'usage'
    for argv in (['python3', '-c', _READER, ','.join(SETTINGS), '/etc/shadow'],
                 ['python3', '-c', _FILE_READER, module.SINK_PATH, str(MAX_BYTES)]):
        with pytest.raises(KubernetesCaptureError):
            engine._program('engine', argv, 262144 if argv[2] == _READER else 2 * MAX_BYTES)
    with pytest.raises(KubernetesCaptureError):
        engine._program('other', ['python3', '-c', _READER, ','.join(SETTINGS), *PATHS], 262144)
    sink = reader(sink=True)
    assert sink._program('sink', ['python3', '-c', _FILE_READER, module.SINK_PATH, str(MAX_BYTES)], 2 * MAX_BYTES) == 'file'
    with pytest.raises(KubernetesCaptureError):
        sink._program('sink', ['python3', '-c', _FILE_READER, '/guard-usage/other.jsonl', str(MAX_BYTES)], 2 * MAX_BYTES)


def test_engine_reader_binds_the_exact_usage_append_prefix_to_the_existing_file_paths():
    prefixes = {PATHS[0]: [13, 'a' * 64, 'b' * 64]}
    engine = reader(prefixes=prefixes)
    assert engine._program('engine', engine.snapshot_argv, 262144) == 'usage'
    legacy = ['python3', '-c', _READER, ','.join(SETTINGS), *PATHS]
    with pytest.raises(KubernetesCaptureError):
        engine._program('engine', legacy, 262144)
    for alternative in ({}, {PATHS[0]: [13, 'c' * 64, 'b' * 64]}):
        program, args = _snapshot_command(PATHS, alternative)
        with pytest.raises(KubernetesCaptureError):
            engine._program('engine', ['python3', '-c', program, *args], 262144)


def test_transport_accepts_only_the_single_usage_control_request():
    good = {'id': 'usage-control', 'method': 'POST', 'url': 'http://x/v1/completions', 'headers': {},
            'body': {'model': 'm', 'prompt': 'ping', 'max_tokens': 1}}
    KubernetesHTTP._usage_control_request(None, good)
    for change in ({'id': 'func|POST /v1/completions'}, {'headers': {'Authorization': 'Bearer x'}},
                   {'body': {'model': 'm', 'prompt': 'free text', 'max_tokens': 1}},
                   {'body': {'model': 'm', 'prompt': 'ping', 'max_tokens': 1, 'stream': True}}, {'method': 'GET'}):
        with pytest.raises(KubernetesBindingError):
            KubernetesHTTP._usage_control_request(None, {**good, **change})


def test_control_alias_restores_the_original_label_for_the_original_judge():
    seen = []
    def transport(network, requests, timeout):
        seen.extend(requests)
        return [{'id': r['id'], 'outcome': 200} for r in requests]
    alias = module._ControlAlias(SimpleNamespace(__call__=None))
    alias._transport = transport
    rows = alias('kubernetes-probe', [{'id': 'func|POST /v1/completions', 'method': 'POST'}], 10)
    assert seen[0]['id'] == 'usage-control' and rows == [{'id': 'func|POST /v1/completions', 'outcome': 200}]


def test_registry_sends_only_usage_profiles_to_the_new_executor_and_rejects_unknown_settings(tmp_path):
    from guard.executors.kubernetes_egress_product import KubernetesEgressProductExecutor
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    executor = EXECUTORS['boundary_egress:kubernetes']({'boundary_egress': {'target': str(tmp_path / 't.json'), 'docker_binary': 'docker'}})
    assert isinstance(executor, KubernetesEgressProductExecutor)
    values = {'usage_allowed': False, 'usage_report_paths': PATHS, 'usage_settings_fresh_process': True}
    with pytest.raises(ValueError, match='KUBERNETES_USAGE_CONFIGURATION_INVALID'):
        executor.validate_policy(pack.checks['OBS-04'], values)
    with pytest.raises(ValueError, match='KUBERNETES_EGRESS_PRODUCT_CONFIGURATION_INVALID'):
        executor.validate_policy(pack.checks['PLT-07'], {})


LOCAL_PATHS = ['/root/usage.jsonl', '/home/guard/.config/vllm/usage_stats.json']
LOCAL_VALUES = {'usage_allowed': False, 'usage_report_paths': LOCAL_PATHS, 'usage_settings_fresh_process': True}
PRIVATE = 'EXAMPLE-USAGE-RAW-MUST-NOT-PERSIST'
CACHE_PATH = '/cache/config/vllm/usage_stats.json'
CACHE_ENV = b'HOME=/cache/home\0XDG_CONFIG_HOME=/cache/config\0'
OPTOUT_ENV = b'VLLM_NO_USAGE_STATS=1\0DO_NOT_TRACK=1\0'
SOURCE = {'source_root': module._USAGE_SOURCE_ROOT, 'filename': 'usage_stats.json',
          'file_sha256': {'envs.py': '1' * 64, 'usage/usage_lib.py': '2' * 64}}
ENTRYPOINT = ['/usr/local/bin/vllm-nonroot-entrypoint.sh']
LAUNCH_ARGS = ['serve', '/models/fixture']
STARTUP_SOURCE = {'entrypoint': ENTRYPOINT, 'source_root': SOURCE['source_root'],
                  'required_env': {'VLLM_USE_V2_MODEL_RUNNER': '0'}, 'optional_env': {},
                  'forbidden_args': ['--config']}


def test_local_reader_projects_only_optouts_and_regular_file_metadata(tmp_path, monkeypatch, capsys):
    legacy = ['/root/usage.jsonl', '/root/.config/vllm/usage_stats.json', '/home/guard/.config/vllm/usage_stats.json']
    normal, missing, alias, raced = (tmp_path / path.lstrip('/') for path in [*legacy, CACHE_PATH])
    for path in (normal, alias, raced):
        path.parent.mkdir(parents=True)
    normal.write_text(PRIVATE, encoding='utf-8')
    alias.symlink_to(normal)
    raced.write_text(PRIVATE, encoding='utf-8')
    fake_proc = {'/proc/1/environ': CACHE_ENV + OPTOUT_ENV + b'VLLM_USAGE_STATS_SERVER=' + PRIVATE.encode(),
                 '/proc/1/stat': b'1 (engine) ' + b'0 ' * 19 + b'88'}
    original_open = builtins.open
    original_os_open = os.open

    def owned_proc(path, *args, **kwargs):
        if path in fake_proc:
            return io.BytesIO(fake_proc[path])
        return original_open(path, *args, **kwargs)

    def race_after_stat(path, *args, **kwargs):
        if path == '/':
            path = tmp_path
        if path == raced.name and kwargs.get('dir_fd') is not None:
            raced.unlink()
        return original_os_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', owned_proc)
        patch.setattr(os, 'open', race_after_stat)
        patch.setattr(sys, 'argv', ['reader', ','.join(module._OPTOUT_SETTINGS), '1', *legacy])
        exec(compile(module._LOCAL_READER, 'local usage reader', 'exec'), {})
    output = capsys.readouterr().out
    assert PRIVATE not in output
    rows = module._local_snapshot_value(json.loads(output), legacy, True)
    assert rows['report_root'] == 'cache-xdg'
    assert rows['settings'] == {'VLLM_NO_USAGE_STATS': True, 'DO_NOT_TRACK': True}
    assert rows['process_start_ticks'] == 88
    assert [row['exists'] for row in rows['files']] == [True, False, None, None]


@pytest.mark.parametrize('env', [
    CACHE_ENV + b'HOME=/cache/home\0',
    CACHE_ENV + b'VLLM_CONFIG_ROOT=/cache/config/vllm\0',
    b'HOME=/cache/home\0XDG_CONFIG_HOME=/other/config\0',
])
def test_pid1_reader_rejects_unknown_active_location_before_any_file_read(monkeypatch, capsys, env):
    reads = []

    def proc(path, *_args):
        reads.append(path)
        return io.BytesIO(env + OPTOUT_ENV if path == '/proc/1/environ' else b'1 (engine) ' + b'0 ' * 19 + b'88')

    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', proc)
        patch.setattr(os, 'open', lambda *_args, **_kwargs: pytest.fail('unbound report read'))
        patch.setattr(sys, 'argv', ['reader', ','.join(module._OPTOUT_SETTINGS), '1', *LOCAL_PATHS])
        with pytest.raises(ValueError):
            exec(compile(module._LOCAL_READER, 'local usage reader', 'exec'), {})
    assert reads == ['/proc/1/stat', '/proc/1/environ']
    assert capsys.readouterr().out == ''


def test_pid1_reader_keeps_legacy_rows_without_opening_unqualified_cache_file(tmp_path, monkeypatch, capsys):
    (tmp_path / 'root').mkdir()
    (tmp_path / 'root/usage.jsonl').write_text(PRIVATE, encoding='utf-8')
    original_os_open = os.open

    def proc(path, *_args):
        return io.BytesIO(CACHE_ENV + OPTOUT_ENV if path == '/proc/1/environ'
                          else b'1 (engine) ' + b'0 ' * 19 + b'88')

    def rooted_open(path, *args, **kwargs):
        assert path != 'cache'
        return original_os_open(tmp_path if path == '/' else path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', proc)
        patch.setattr(os, 'open', rooted_open)
        patch.setattr(sys, 'argv', ['reader', ','.join(module._OPTOUT_SETTINGS), '0', *LOCAL_PATHS])
        exec(compile(module._LOCAL_READER, 'local usage reader', 'exec'), {})
    raw = capsys.readouterr().out
    assert PRIVATE not in raw
    snapshot = module._local_snapshot_value(json.loads(raw), LOCAL_PATHS, False)
    assert snapshot['report_root'] == 'cache-xdg'
    assert snapshot['files'][0]['exists'] is True
    assert snapshot['files'][-1] == {'path': CACHE_PATH, 'exists': None, 'readable': False}


@pytest.mark.parametrize(('home', 'root'), [
    ('/root', 'root-default'), ('/home/guard', 'guard-default'),
])
def test_pid1_reader_keeps_conventional_roots_without_native_source_contract(tmp_path, monkeypatch, capsys, home, root):
    original_os_open = os.open

    def proc(path, *_args):
        return io.BytesIO(f'HOME={home}\0'.encode() + OPTOUT_ENV if path == '/proc/1/environ'
                          else b'1 (engine) ' + b'0 ' * 19 + b'88')

    def rooted_open(path, *args, **kwargs):
        return original_os_open(tmp_path if path == '/' else path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', proc)
        patch.setattr(os, 'open', rooted_open)
        patch.setattr(sys, 'argv', ['reader', ','.join(module._OPTOUT_SETTINGS), '0', *LOCAL_PATHS])
        exec(compile(module._LOCAL_READER, 'local usage reader', 'exec'), {})
    snapshot = module._local_snapshot_value(json.loads(capsys.readouterr().out), LOCAL_PATHS, False)
    assert snapshot['report_root'] == root
    assert all(row['exists'] is False for row in snapshot['files'])


class LocalUsageWorld(FullWorld):
    def __init__(self, target, mode):
        super().__init__(target)
        self.mode, self.snapshots, self.controls, self.source_reads = mode, 0, 0, 0

    def runner(self, argv, **kwargs):
        if 'get' in argv and 'namespace' in argv:
            engine = self.target['kubernetes']['engine']
            assert engine['namespace'] in argv
            value = {'kind': 'Namespace', 'metadata': {'name': engine['namespace'], 'uid': engine['namespace_uid']}}
        elif 'exec' in argv and 'input_bytes' not in kwargs:
            engine = self.target['kubernetes']['engine']
            assert engine['pod'] in argv and '--container=' + engine['container'] in argv
            assert argv[-4] == ','.join(module._OPTOUT_SETTINGS) and argv[-2:] == LOCAL_PATHS
            assert module.supervised_source(module._LOCAL_READER) in argv
            assert 'VLLM_USAGE_STATS_SERVER' not in module._LOCAL_READER
            self.snapshots += 1
            if (self.mode in ('local-transport-failed', 'local-transport-timed-out')
                    or (self.mode == 'local-after-transport-timed-out' and self.snapshots == 2)):
                timed_out = self.mode.endswith('-timed-out')
                return {'returncode': 1, 'stdout_bytes': b'', 'stderr_bytes': PRIVATE.encode(),
                        'capture': {'complete': not timed_out, 'bounded_at_source': True,
                                    'timed_out': timed_out, 'truncated': False}}
            cache = self.mode.startswith('cache')
            paths = [*LOCAL_PATHS, CACHE_PATH] if cache else LOCAL_PATHS
            files = [{'path': path, 'exists': False, 'readable': True,
                      'directory_chain': ['f' * 64] * path.count('/')} for path in paths]
            if self.mode in ('one-unreadable', 'all-unreadable'):
                for row in files[:1 if self.mode == 'one-unreadable' else 2]:
                    row.update(exists=None, readable=False)
                    del row['directory_chain']
            if self.mode == 'present-unchanged' or (self.snapshots == 1 and self.mode in (
                    'effective-file-disappeared', 'optional-file-disappeared')):
                existing = files[0 if self.mode == 'optional-file-disappeared' else -1]
                existing.update(exists=True, bytes=42, lines=1, sha256='a' * 64)
            if self.mode in ('new', 'cache-new') and self.snapshots == 2:
                files[-1 if cache else 0].update(exists=True, bytes=42, lines=1, sha256='a' * 64)
            if self.mode == 'cache-source-change-legacy-new' and self.snapshots == 2:
                files[0].update(exists=True, bytes=42, lines=1, sha256='a' * 64)
            if self.mode == 'cache-folder-changed' and self.snapshots == 2:
                files[-1]['directory_chain'][-1] = '0' * 64
            if cache and argv[-3] == '0':
                files[-1] = {'path': CACHE_PATH, 'exists': None, 'readable': False}
            value = {'files': files, 'settings': {'VLLM_NO_USAGE_STATS': self.mode != 'optout-missing', 'DO_NOT_TRACK': True},
                     'process_start_ticks': 90 if self.mode == 'process-changed' and self.snapshots == 2 else 88,
                     'report_root': 'cache-xdg' if cache else 'guard-default'}
            if self.mode == 'local-projection-invalid':
                value['process_start_ticks'] = 0
        elif 'exec' in argv and 'input_bytes' in kwargs:
            payload = json.loads(kwargs['input_bytes'])
            if 'files' in payload:
                self.source_reads += 1
                assert self.mode.startswith('cache') and payload['roots'] == [SOURCE['source_root']]
                files = [{'root': 0, 'relative': row['relative'], 'state': 'hashed',
                          'sha256': SOURCE['file_sha256'][row['relative']]} for row in payload['files']]
                if self.mode.startswith('cache-source-change') and self.source_reads == 2:
                    files[0]['sha256'] = 'a' * 64
                return {'returncode': 0, 'stdout_bytes': canonical_json({'files': files}),
                        'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}
            self.controls += 1
            request, = payload['requests']
            assert request['id'] == 'usage-control' and request['headers'] == {}
            assert request['body'] == {'model': 'fixture-model', 'prompt': 'ping', 'max_tokens': 1}
            body = json.dumps({'model': 'fixture-model', 'choices': [{'text': PRIVATE, 'finish_reason': 'length'}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
            value = [{'id': 'usage-control', 'method': 'POST', 'mode': 'json', 'outcome': 200, 'body': body,
                      'body_bytes': len(body.encode()), 'body_sha256': sha256_hex(body.encode()),
                      'capture_complete': True, 'utf8_valid': True, 'eof': self.mode != 'partial-control', 'body_truncated': False}]
        else:
            result = super().runner(argv, **kwargs)
            if 'get' in argv and 'pod' in argv:
                pod = json.loads(result['stdout_bytes'])
                if self.mode in ('shared-pid', 'host-pid'):
                    pod['spec']['shareProcessNamespace' if self.mode == 'shared-pid' else 'hostPID'] = True
                if self.mode == 'proc-mount':
                    container, = pod['spec']['containers']
                    container['volumeMounts'].append({'name': 'proc-view', 'mountPath': '/proc/1'})
                if self.mode == 'local-owner-changed' and self.snapshots:
                    pod['metadata']['uid'] = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
                if self.mode.startswith('cache') and pod['metadata']['name'] == self.target['kubernetes']['engine']['pod']:
                    container, = pod['spec']['containers']
                    container.update(command=ENTRYPOINT, args=LAUNCH_ARGS,
                                     env=[{'name': 'VLLM_USE_V2_MODEL_RUNNER', 'value': '0'}])
                    if self.mode == 'cache-launch-override':
                        container['env'].append({'name': 'PYTHONPATH', 'value': '/example'})
                    if self.mode == 'cache-launch-mount-after' and self.controls:
                        container['volumeMounts'].append({'name': 'package', 'mountPath': SOURCE['source_root']})
                result['stdout_bytes'] = canonical_json(pod)
            result['capture'].update(bounded_at_source=True, timed_out=False, truncated=False)
            return result
        return {'returncode': 0, 'stdout_bytes': canonical_json(value), 'stderr_bytes': PRIVATE.encode(),
                'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}


@pytest.mark.parametrize('case,error', [
    ('ordinary', None),
    ('foreign-model-revision', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('over-budget', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('factory-target-changed', 'KUBERNETES_USAGE_CONFIGURATION_INVALID'),
    ('symlink-after-preparation', 'KUBERNETES_USAGE_CONFIGURATION_INVALID'),
    ('malformed-after-preparation', 'KUBERNETES_USAGE_CONFIGURATION_INVALID'),
])
def test_local_usage_preparation_checks_saved_target_without_collecting(tmp_path, monkeypatch, case, error):
    def forbidden(*args, **kwargs):
        pytest.fail('OBS-04 preparation must not contact the target or start a reader')

    monkeypatch.setattr(KubernetesHTTP, '_verify', forbidden)
    monkeypatch.setattr(KubernetesHTTP, '__call__', forbidden)
    monkeypatch.setattr(module.KubernetesUsageReportExecutor, '_reader', forbidden)
    env, target = full_inputs()
    target['lab'] = False
    if case == 'foreign-model-revision':
        target['model_mapping']['revision'] = ('1' if env.model.revision == '0' * 40 else '0') * 40
    raw = canonical_json(target)
    path = tmp_path / 'saved-target.json'
    path.write_bytes(raw)
    digest = sha256_hex(raw)
    cfg = {'target': str(path), 'local_observation_only': True,
           'max_duration_seconds': 301 if case == 'over-budget' else 30}
    executor = module.kubernetes_boundary_egress(
        cfg, expected_target_sha256='f' * 64 if case == 'factory-target-changed' else digest)
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['OBS-04']
    if case.endswith('-after-preparation'):
        executor.prepared_local_observation(check, env, LOCAL_VALUES, expected_target_sha256=digest)
        if case == 'symlink-after-preparation':
            moved = path.with_name('original-target.json')
            path.rename(moved)
            path.symlink_to(moved)
        else:
            path.write_bytes(b'[]')
        monkeypatch.setattr(module.KubernetesUsageReportExecutor, '_transport', forbidden)
        with pytest.raises(ValueError, match=f'^{error}$'):
            executor.run(check, env, LOCAL_VALUES, VllmAdapter(), tmp_path / 'work')
    if error is None:
        executor.prepared_local_observation(check, env, LOCAL_VALUES, expected_target_sha256=digest)
    else:
        with pytest.raises(ValueError, match=f'^{error}$'):
            executor.prepared_local_observation(check, env, LOCAL_VALUES, expected_target_sha256=digest)


@pytest.mark.parametrize('mode,completed,collected,verdict', [
    ('absent', True, True, 'UNKNOWN'), ('present-unchanged', True, True, 'UNKNOWN'),
    ('new', True, True, 'FAIL'), ('one-unreadable', True, False, 'UNKNOWN'),
    ('effective-file-disappeared', False, False, 'UNKNOWN'),
    ('optional-file-disappeared', True, False, 'UNKNOWN'),
    ('all-unreadable', False, False, 'UNKNOWN'), ('optout-missing', False, False, 'UNKNOWN'),
    ('process-changed', False, False, 'UNKNOWN'), ('partial-control', False, False, 'UNKNOWN'),
    ('shared-pid', False, False, 'UNKNOWN'), ('host-pid', False, False, 'UNKNOWN'), ('proc-mount', False, False, 'UNKNOWN'),
    ('cache-absent', True, True, 'UNKNOWN'), ('cache-new', True, True, 'FAIL'),
    ('cache-folder-changed', False, False, 'UNKNOWN'), ('cache-source-change', False, False, 'UNKNOWN'),
    ('cache-source-change-legacy-new', False, True, 'FAIL'),
    ('cache-launch-override', False, False, 'UNKNOWN'), ('cache-launch-mount-after', False, False, 'UNKNOWN'),
    ('cache-unqualified', False, False, 'UNKNOWN'),
    ('local-transport-timed-out', False, False, 'UNKNOWN'), ('local-after-transport-timed-out', False, False, 'UNKNOWN'),
    ('local-transport-failed', False, False, 'UNKNOWN'), ('local-owner-changed', False, False, 'UNKNOWN'),
    ('local-projection-invalid', False, False, 'UNKNOWN'),
])
def test_local_usage_runs_only_owned_reads_and_one_normal_control(tmp_path, monkeypatch, mode, completed, collected, verdict):
    env, target = full_inputs()
    target['lab'] = False
    world = LocalUsageWorld(target, mode)
    if mode.startswith('cache'):
        target['comparison_config']['launch']['argv'] = LAUNCH_ARGS
    with_source = mode.startswith('cache') and mode != 'cache-unqualified'

    def evidence_contract(name, bound_env):
        assert name == 'usage_report_source' and bound_env is env
        return SOURCE if with_source else None

    monkeypatch.setattr(module, 'get_provider', lambda _name: SimpleNamespace(evidence_contract=evidence_contract))
    monkeypatch.setattr(module, '_source_profile', lambda _adapter, _env: STARTUP_SOURCE)
    target_path = tmp_path / 'target.json'
    target_path.write_bytes(canonical_json(target))
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    check = pack.checks['OBS-04']
    executor = module.KubernetesUsageReportExecutor({'target': str(target_path), 'local_observation_only': True,
                                                    'usage_wait_seconds': 0}, runner=world.runner,
                                                   expected_target_sha256=sha256_hex(canonical_json(target)))
    draft = executor.run(check, env, LOCAL_VALUES, VllmAdapter(), tmp_path / 'work')
    pre_control_failures = {
        'shared-pid': 'USAGE_LOCAL_PRE_READ_UNVERIFIED',
        'host-pid': 'USAGE_LOCAL_PRE_READ_UNVERIFIED',
        'proc-mount': 'USAGE_LOCAL_PRE_READ_UNVERIFIED',
        'local-transport-timed-out': 'USAGE_LOCAL_TRANSPORT_UNAVAILABLE',
        'local-transport-failed': 'USAGE_LOCAL_TRANSPORT_UNAVAILABLE',
        'local-owner-changed': 'USAGE_LOCAL_OWNER_AFTER_UNVERIFIED',
        'local-projection-invalid': 'USAGE_LOCAL_PROJECTION_INVALID',
    }
    assert world.snapshots == {'optout-missing': 1, 'shared-pid': 0, 'host-pid': 0, 'proc-mount': 0,
                               'all-unreadable': 1, 'cache-unqualified': 1, 'cache-launch-override': 1,
                               'local-transport-timed-out': 1,
                               'local-transport-failed': 1, 'local-owner-changed': 1,
                               'local-projection-invalid': 1}.get(mode, 2)
    assert world.controls == (0 if mode in ({'optout-missing', 'all-unreadable', 'cache-unqualified',
                                           'cache-launch-override'} | pre_control_failures.keys()) else 1)
    expected_reads = {'cache-launch-override': 0, 'cache-launch-mount-after': 1}
    assert world.source_reads == expected_reads.get(mode, 2 if with_source else 0)
    assert draft.status == (ExecStatus.COMPLETED if completed else ExecStatus.ABORTED)
    assert draft.data_origin.value == 'EXAMPLE'
    assert draft.observations['usage_new_local_reports'].collected is collected
    assert draft.observations['usage_sink_receipts'].collected is False
    assert draft.observations['usage_send_sensor_available'].value is False
    assert all(not challenge.attempted for challenge in draft.challenges)
    observations = {key: value.model_dump() for key, value in draft.observations.items()}
    result = judge_evidence(check, evidence(status=draft.status, check_id='OBS-04', observations=observations,
                                          normal=draft.normal_control.model_dump()),
                            policy(pack.ref(), values={'OBS-04': LOCAL_VALUES}), T0)
    assert result.verdicts.security.value == verdict
    assert all(raw not in artifact.path.read_bytes() for artifact in draft.artifacts
               for raw in (PRIVATE.encode(), CACHE_PATH.encode(), b'/cache/home', SOURCE['source_root'].encode()))
    summary = next(row for row in map(json.loads, draft.artifacts[0].path.read_text().splitlines()) if row.get('summary'))
    if mode in pre_control_failures:
        assert summary['local_snapshot_status'] == {'before': pre_control_failures[mode]}
        assert summary['local_snapshot_presence'] == {}
        assert draft.normal_control.attempted is False
    elif mode in ('absent', 'present-unchanged'):
        assert summary['local_snapshot_status'] == dict.fromkeys(('before', 'after'), 'USAGE_LOCAL_SNAPSHOT_VERIFIED')
        assert draft.observations['usage_new_local_reports'].value == []
    elif mode == 'local-after-transport-timed-out':
        assert summary['local_snapshot_status'] == {
            'before': 'USAGE_LOCAL_SNAPSHOT_VERIFIED', 'after': 'USAGE_LOCAL_TRANSPORT_UNAVAILABLE',
        }
        assert set(summary['local_snapshot_presence']) == {'before'}
    failure_kinds = {
        'local-transport-failed': {'before': 'nonzero_process'},
        'local-transport-timed-out': {'before': 'timeout'},
        'local-after-transport-timed-out': {'after': 'timeout'},
    }
    if mode in failure_kinds:
        assert summary['local_snapshot_failure_kind'] == failure_kinds[mode]
    else:
        assert 'local_snapshot_failure_kind' not in summary
    if mode in ('effective-file-disappeared', 'optional-file-disappeared'):
        optional = mode == 'optional-file-disappeared'
        lost, retained = (LOCAL_PATHS if optional else LOCAL_PATHS[::-1])
        assert summary['unverified_paths'] == [sha256_hex(lost.encode())]
        assert summary['observed_paths'] == [sha256_hex(retained.encode())]
        assert summary['effective_report_path_observed'] is optional
        assert not summary['local_paths_complete']
        assert draft.normal_control.attempted and draft.normal_control.succeeded
    if mode == 'cache-new':
        reports = draft.observations['usage_new_local_reports'].value
        assert [report['path_sha256'] for report in reports] == [sha256_hex(CACHE_PATH.encode())]
    if mode == 'cache-source-change-legacy-new':
        reports = draft.observations['usage_new_local_reports'].value
        assert [report['path_sha256'] for report in reports] == [sha256_hex(LOCAL_PATHS[0].encode())]


@pytest.mark.parametrize('change', ['missing-hash', 'wrong-hash', 'lab', 'sink', 'path', 'allowed', 'sibling'])
def test_local_usage_rejects_non_admitted_configuration_before_kubectl(tmp_path, change):
    env, target = full_inputs()
    target['lab'] = change == 'lab'
    target_path = tmp_path / 'target.json'
    target_path.write_bytes(canonical_json(target))
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    check = pack.checks['OBS-05' if change == 'sibling' else 'OBS-04']
    cfg = {'target': str(target_path), 'local_observation_only': True}
    values = dict(LOCAL_VALUES)
    if change == 'sink': cfg['sink_target'] = str(tmp_path / 'sink.json')
    if change == 'path': values['usage_report_paths'] = ['/etc/passwd']
    if change == 'allowed': values['usage_allowed'] = True
    digest = None if change == 'missing-hash' else '0' * 64 if change == 'wrong-hash' else sha256_hex(canonical_json(target))
    calls = []
    executor = module.KubernetesUsageReportExecutor(cfg, runner=lambda *a, **kw: calls.append(a), expected_target_sha256=digest)
    with pytest.raises(ValueError, match='KUBERNETES_USAGE_CONFIGURATION_INVALID'):
        executor.run(check, env, values, VllmAdapter(), tmp_path / 'work')
    assert not calls


@pytest.mark.parametrize('mode,origin', [('absent', DataOrigin.MEASURED), ('all-unreadable', DataOrigin.DECLARED)])
def test_declared_environment_preserves_actual_bound_capture_origin(tmp_path, monkeypatch, mode, origin):
    from guard.executors import kubernetes_lifecycle_capture
    from guard.runtime import kubernetes_http

    env, target = full_inputs()
    env = env.model_copy(update={'data_origin': DataOrigin.DECLARED}).seal()
    target.update(lab=False, environment_ref=env.ref().model_dump(mode='json'))
    target['model_mapping']['environment_ref'] = target['environment_ref']
    world = LocalUsageWorld(target, mode)
    monkeypatch.setattr(module, 'get_provider', lambda _name: SimpleNamespace(evidence_contract=lambda *_: None))
    monkeypatch.setattr(kubernetes_lifecycle_capture, 'capture_pipes', world.runner)
    monkeypatch.setattr(kubernetes_http, 'capture_pipes', world.runner)
    target_path = tmp_path / 'target.json'
    target_path.write_bytes(canonical_json(target))
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    cfg = {'target': str(target_path), 'local_observation_only': True, 'usage_wait_seconds': 0}
    draft = module.KubernetesUsageReportExecutor(cfg, expected_target_sha256=sha256_hex(canonical_json(target))).run(
        pack.checks['OBS-04'], env, LOCAL_VALUES, VllmAdapter(), tmp_path / 'work')
    assert draft.data_origin == origin
