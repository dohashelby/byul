"""OBS-04·05 Kubernetes 전송: 원 usage_report의 판정을 그대로 쓰고 읽기만 Kubernetes 고정 프로그램으로 바꾼다."""
import builtins
import io
import json
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
from guard.executors.usage_report import SETTINGS, _READER
from guard.models import ExecStatus, canonical_json, load_pack, sha256_hex
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP
from tests.builders import T0, evidence, policy
from tests.test_model_preparation_integration import FullWorld, full_inputs

PATHS = ['/root/.config/vllm/usage_stats.json']


def reader(*, sink=False):
    value = module._UsageReader.__new__(module._UsageReader)
    value.binding = {'engine': {'container': 'sink' if sink else 'engine'}}
    value._paths, value._sink = ([] if sink else list(PATHS)), sink
    value._local = False
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


def test_local_reader_projects_only_optouts_and_regular_file_metadata(tmp_path, monkeypatch, capsys):
    normal = tmp_path / 'report.json'
    normal.write_text(PRIVATE, encoding='utf-8')
    missing = tmp_path / 'missing.json'
    alias = tmp_path / 'link.json'
    alias.symlink_to(normal)
    fake_proc = {'/proc/1/environ': b'VLLM_NO_USAGE_STATS=1\0DO_NOT_TRACK=1\0VLLM_USAGE_STATS_SERVER=' + PRIVATE.encode(),
                 '/proc/1/stat': b'1 (engine) ' + b'0 ' * 19 + b'88'}
    original_open = builtins.open

    def owned_proc(path, *args, **kwargs):
        if path in fake_proc:
            return io.BytesIO(fake_proc[path])
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', owned_proc)
        patch.setattr(sys, 'argv', ['reader', ','.join(module._OPTOUT_SETTINGS), *map(str, (normal, missing, alias))])
        exec(compile(module._LOCAL_READER, 'local usage reader', 'exec'), {})
    output = capsys.readouterr().out
    assert PRIVATE not in output
    rows = module._local_snapshot_value(json.loads(output), list(map(str, (normal, missing, alias))))
    assert rows['settings'] == {'VLLM_NO_USAGE_STATS': True, 'DO_NOT_TRACK': True}
    assert rows['process_start_ticks'] == 88
    assert [row['exists'] for row in rows['files']] == [True, False, None]


class LocalUsageWorld(FullWorld):
    def __init__(self, target, mode):
        super().__init__(target)
        self.mode, self.snapshots, self.controls = mode, 0, 0

    def runner(self, argv, **kwargs):
        if 'get' in argv and 'namespace' in argv:
            engine = self.target['kubernetes']['engine']
            assert engine['namespace'] in argv
            value = {'kind': 'Namespace', 'metadata': {'name': engine['namespace'], 'uid': engine['namespace_uid']}}
        elif 'exec' in argv and 'input_bytes' not in kwargs:
            engine = self.target['kubernetes']['engine']
            assert engine['pod'] in argv and '--container=' + engine['container'] in argv
            assert argv[-3:] == [','.join(module._OPTOUT_SETTINGS), *LOCAL_PATHS]
            assert module.supervised_source(module._LOCAL_READER) in argv
            assert 'VLLM_USAGE_STATS_SERVER' not in module._LOCAL_READER
            self.snapshots += 1
            files = [{'path': path, 'exists': False, 'readable': True} for path in LOCAL_PATHS]
            if self.mode in ('one-unreadable', 'all-unreadable'):
                for row in files[:1 if self.mode == 'one-unreadable' else 2]:
                    row.update(exists=None, readable=False)
            if self.mode == 'new' and self.snapshots == 2:
                files[0].update(exists=True, bytes=42, lines=1, sha256='a' * 64)
            value = {'files': files, 'settings': {'VLLM_NO_USAGE_STATS': self.mode != 'optout-missing', 'DO_NOT_TRACK': True},
                     'process_start_ticks': 90 if self.mode == 'process-changed' and self.snapshots == 2 else 88}
        elif 'exec' in argv and 'input_bytes' in kwargs:
            self.controls += 1
            request, = json.loads(kwargs['input_bytes'])['requests']
            assert request['id'] == 'usage-control' and request['headers'] == {}
            assert request['body'] == {'model': 'fixture-model', 'prompt': 'ping', 'max_tokens': 1}
            body = json.dumps({'model': 'fixture-model', 'choices': [{'text': PRIVATE, 'finish_reason': 'length'}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
            value = [{'id': 'usage-control', 'method': 'POST', 'mode': 'json', 'outcome': 200, 'body': body,
                      'body_bytes': len(body.encode()), 'body_sha256': sha256_hex(body.encode()),
                      'capture_complete': True, 'utf8_valid': True, 'eof': self.mode != 'partial-control', 'body_truncated': False}]
        else:
            result = super().runner(argv, **kwargs)
            result['capture'].update(bounded_at_source=True, timed_out=False, truncated=False)
            return result
        return {'returncode': 0, 'stdout_bytes': canonical_json(value), 'stderr_bytes': PRIVATE.encode(),
                'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}


@pytest.mark.parametrize('mode,completed,collected,verdict', [
    ('absent', True, True, 'UNKNOWN'), ('new', True, True, 'FAIL'), ('one-unreadable', True, False, 'UNKNOWN'),
    ('all-unreadable', False, False, 'UNKNOWN'), ('optout-missing', False, False, 'UNKNOWN'),
    ('process-changed', False, False, 'UNKNOWN'), ('partial-control', False, False, 'UNKNOWN'),
])
def test_local_usage_runs_only_owned_reads_and_one_normal_control(tmp_path, mode, completed, collected, verdict):
    env, target = full_inputs()
    target['lab'] = False
    world = LocalUsageWorld(target, mode)
    target_path = tmp_path / 'target.json'
    target_path.write_bytes(canonical_json(target))
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    check = pack.checks['OBS-04']
    executor = module.KubernetesUsageReportExecutor({'target': str(target_path), 'local_observation_only': True,
                                                    'usage_wait_seconds': 0}, runner=world.runner,
                                                   expected_target_sha256=sha256_hex(canonical_json(target)))
    draft = executor.run(check, env, LOCAL_VALUES, VllmAdapter(), tmp_path / 'work')
    assert world.snapshots == (1 if mode == 'optout-missing' else 2)
    assert world.controls == (0 if mode == 'optout-missing' else 1)
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
    assert all(PRIVATE.encode() not in artifact.path.read_bytes() for artifact in draft.artifacts)


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
