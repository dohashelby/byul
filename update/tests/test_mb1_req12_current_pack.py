"""현행 정본의 REQ-12에서 공통 입력·원 판정·정확 복원을 확인한다. 전송은 EXAMPLE이다."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors import gpu_common_inputs as contract
from guard.collectors import gpu_common_input_v4 as v4
from guard.executors import lab_activation
from guard.models import canonical_json, load_pack
from guard.runtime import kubernetes_http as http
from tests import test_b12_common_input_consumption as common
from tests.test_a1_kubernetes_request_attempts_product import inputs as native_inputs
from tests.test_kubernetes_lifecycle import group, target as native_target  # noqa: F401
from tests.test_lifecycle_capture import case, external, revised  # noqa: F401
from tests.test_lifecycle_sensor import writer_factory  # noqa: F401


@pytest.mark.parametrize('schema', ['guard-gpu-common-input/1', v4.SCHEMA])
@pytest.mark.parametrize('registered', [False, True])
@pytest.mark.parametrize('independent_fail', [False, True])
def test_req12_common_input_keeps_unknown_or_independent_fail_through_restore(
        tmp_path, group, native_target, monkeypatch, schema, registered, independent_fail):
    world, values, _, _ = native_inputs(tmp_path, group, native_target, 'normal')
    target_file = tmp_path / 'target.json'
    target_file.write_bytes(canonical_json(world.target))
    _, _, _, record, _, descriptor = common.inputs(
        tmp_path / 'common', env=group.env, target=world.target, prepared=group.prepared,
        pod=world.target['kubernetes']['engine'])
    if schema == v4.SCHEMA:
        record = v4.encode(record, wire_schema=schema)
    Path(descriptor['records']['request-attempt']).write_bytes(canonical_json(record))
    producer = record['producer']
    key = producer['name'], producer['version']
    registry = contract.SUPPORTED_V4_PRODUCERS if schema == v4.SCHEMA else contract.SUPPORTED_PRODUCERS
    assert registry.get(key) != producer['source_sha256']
    if registered:
        # 이 EXAMPLE 사례의 소비 분기만 모의한다. 실제 GPU 자격표는 바꾸지 않는다.
        monkeypatch.setitem(registry, key, producer['source_sha256'])
    monkeypatch.setattr(http, 'capture_pipes', world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes', world.runner)
    if independent_fail:
        active = {'activation_steps': [{'step': 'EXAMPLE-independent-fail'}],
                  'activation_violations': [{'code': 'EXAMPLE_CONFIRMED_INDEPENDENT_FAIL'}],
                  'activation_missing': [], 'activation_scope_complete': False}
        lab = SimpleNamespace(initialization_case=None, fault_evidence=[], normal=[True],
            cleanup_errors=[], diagnostic_write_errors=[], write_diagnostics=lambda _: [])
        monkeypatch.setattr(lab_activation, 'run_scenario', lambda *a, **kw: (deepcopy(active), lab))
    pack = load_pack(Path(__file__).parents[1] / 'packs/PACK-BASELINE')
    assert pack.manifest.revision == 36
    store, evd, result = common.pipeline(tmp_path / 'product', pack, group.env, 'REQ-12', values,
        {'target': str(target_file), 'prepared': str(group.manifest_path), 'common_inputs': descriptor},
        activation={'namespace': 'EXAMPLE'} if independent_fail else None)
    artifact = next(a for a in evd.artifacts if a.name == 'b-common-inputs.json')
    report = json.loads((store.root / artifact.path).read_bytes())
    consumed, = report['records']
    assert consumed['record'] == record
    assert consumed['validation']['supported'] is registered
    assert consumed['facts']['automatic_retry_and_final_output_binding_verified'] is False
    assert consumed['whole_question_complete'] is consumed['producer_authenticated'] is False
    assert report['gaps'] and not report['whole_question_complete']
    assert evd.observations['activation_scope_complete'].value is False
    assert result.verdicts.security.value == ('FAIL' if independent_fail else 'UNKNOWN')
    if independent_fail:
        assert {'code': 'EXAMPLE_CONFIRMED_INDEPENDENT_FAIL'} in evd.observations['activation_violations'].value
    if not registered:
        assert 'GPU_INPUT_PRODUCER_UNSUPPORTED' in report['gaps']
