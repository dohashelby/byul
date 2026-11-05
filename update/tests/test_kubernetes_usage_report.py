"""OBS-04·05 Kubernetes 전송: 원 usage_report의 판정을 그대로 쓰고 읽기만 Kubernetes 고정 프로그램으로 바꾼다."""
from types import SimpleNamespace

import pytest

from guard.executors import kubernetes_usage_report as module
from guard.executors.kubernetes_lifecycle_capture import KubernetesCaptureError
from guard.executors.owned_capture import _FILE_READER, MAX_BYTES
from guard.executors.registry import EXECUTORS
from guard.executors.usage_report import SETTINGS, _READER
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP

PATHS = ['/root/.config/vllm/usage_stats.json']


def reader(*, sink=False):
    value = module._UsageReader.__new__(module._UsageReader)
    value.binding = {'engine': {'container': 'sink' if sink else 'engine'}}
    value._paths, value._sink = ([] if sink else list(PATHS)), sink
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
    from guard.models import load_pack
    from pathlib import Path
    from guard.executors.kubernetes_egress_product import KubernetesEgressProductExecutor
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    executor = EXECUTORS['boundary_egress:kubernetes']({'boundary_egress': {'target': str(tmp_path / 't.json'), 'docker_binary': 'docker'}})
    assert isinstance(executor, KubernetesEgressProductExecutor)
    values = {'usage_allowed': False, 'usage_report_paths': PATHS, 'usage_settings_fresh_process': True}
    with pytest.raises(ValueError, match='KUBERNETES_USAGE_CONFIGURATION_INVALID'):
        executor.validate_policy(pack.checks['OBS-04'], values)
    with pytest.raises(ValueError, match='KUBERNETES_EGRESS_PRODUCT_CONFIGURATION_INVALID'):
        executor.validate_policy(pack.checks['PLT-07'], {})
