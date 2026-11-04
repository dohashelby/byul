"""LIF-09 실패 표식과 Ready의 새 Pod·현재 engine 결합 반례."""

import importlib.util
from pathlib import Path

import pytest


_source = Path(__file__).with_name('test_lab_activation.py')
_spec = importlib.util.spec_from_file_location('_lab_activation_contract_fixture', _source)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


@pytest.fixture(autouse=True)
def manifests(tmp_path, monkeypatch):
    for name in ('faulty', 'good', 'update'):
        (tmp_path / f'{name}.yaml').write_text(name)
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize('mode', ['old_container_marker', 'sidecar_marker', 'faulty_split_pods'])
def test_unbound_failure_marker_does_not_make_lif09_ready_a_violation(mode):
    facts, _ = _fixture.scenario('LIF-09', faulty_ready=True, **{mode: True})
    assert facts['activation_scope_complete'] is False
    assert 'FAULTY_REQUEST_GENERATION_UNATTRIBUTED' in facts['activation_missing']
    assert not any(v['code'] == 'PARTIAL_INITIALIZATION_REPORTED_READY'
        for v in facts['activation_violations'])


def test_old_pod_uid_reuse_does_not_make_lif09_ready_a_violation():
    facts, _ = _fixture.scenario('LIF-09', faulty_ready=True, apply_no_new=True)
    assert 'NEW_FAULTY_POD_UID_UNOBSERVED' in facts['activation_missing']
    assert not any(v['code'] == 'PARTIAL_INITIALIZATION_REPORTED_READY'
        for v in facts['activation_violations'])


def test_current_engine_marker_and_ready_on_same_new_pod_keeps_confirmed_failure():
    facts, _ = _fixture.scenario('LIF-09', faulty_ready=True)
    assert any(v['code'] == 'PARTIAL_INITIALIZATION_REPORTED_READY'
        for v in facts['activation_violations'])
    assert facts['activation_scope_complete'] is False
