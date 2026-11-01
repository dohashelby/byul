"""B 자동 생성 객체의 기존 계약 확인. 실제 수집 전 EXAMPLE 경계에서 중단한다."""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
import re
import sys

import pytest

from guard.models import Environment, Policy, load_pack

ROOT = Path(__file__).resolve().parents[2]
COMMON = json.loads((ROOT / 'contracts/schemas/common.schema.json').read_text())['$defs']


class _BeforeCapture(Exception):
    """실제 수집·서버를 호출하지 않는 시험 경계."""


@pytest.mark.parametrize('producer', [
    'b1_input_integration_cpu',
    'b1_dynamic_code_integration_cpu',
    'b1_plugin_integration_cpu',
    'b_req18_slot',
    'lif14_profiler_cpu',
    'req10_tools_cpu_pipeline',
])
def test_generated_env_policy_refs_use_existing_contract_and_distinct_ids(tmp_path, monkeypatch, producer):
    module = importlib.import_module('tools.' + producer)
    pack = load_pack(ROOT / 'update/reference/pack-seeds/PACK-BASELINE-31')
    generated = []

    def stop_at_store(*args, **kwargs):
        # 생성된 실제 sealed 객체만 읽으며 Store·수집기로 진행하지 않는다.
        frame = inspect.currentframe().f_back
        try:
            generated.append((frame.f_locals['env'], frame.f_locals['policy'], frame.f_locals['pack'].ref()))
        finally:
            del frame
        raise _BeforeCapture

    def stop_at_pipeline(mode, env, policy, *args, **kwargs):
        assert mode == 'cpu'
        generated.append((env, policy, args[0].ref()))
        raise _BeforeCapture

    if producer == 'b_req18_slot':
        monkeypatch.setattr(module, 'proposal_pack', lambda *a, **kw: pack)
        monkeypatch.setattr(module, 'run_pipeline', stop_at_pipeline)
    else:
        monkeypatch.setattr(module, 'Store', stop_at_store)
    if hasattr(module, 'verify_runtime_components'):
        monkeypatch.setattr(module, 'verify_runtime_components', lambda *a: None)
        monkeypatch.setattr(module, 'proposal_pack', lambda *a, **kw: pack)
    elif producer == 'lif14_profiler_cpu':
        monkeypatch.setattr(module, 'verify_component', lambda *a: None)
    elif producer == 'req10_tools_cpu_pipeline':
        monkeypatch.setattr(module, 'verify_tool_checkout', lambda *a: None)

    for attempt in range(2):
        output = tmp_path / str(attempt)
        source = tmp_path / 'source-not-executed'
        with pytest.raises(_BeforeCapture):
            if producer == 'b_req18_slot':
                module.run_cpu_pipeline(source, output)
            elif producer in {'lif14_profiler_cpu', 'req10_tools_cpu_pipeline'}:
                monkeypatch.setattr(sys, 'argv', [producer, '--source-root', str(source), '--output-root', str(output)])
                module.main()
            else:
                module.run_pipeline(source, output, origin='EXAMPLE')

    assert len(generated) == 2
    for env, policy, expected_pack in generated:
        assert isinstance(env, Environment) and isinstance(policy, Policy)
        for obj, kind in ((env, 'ENV'), (policy, 'POL')):
            ref = obj.ref().model_dump(mode='json')
            schema = COMMON['object_ref']
            assert set(ref) == set(schema['required']) == set(schema['properties'])
            assert ref['kind'] == kind and ref['kind'] in schema['properties']['kind']['enum']
            assert re.fullmatch(schema['properties']['id']['pattern'], ref['id'])
            assert type(ref['revision']) is int and ref['revision'] >= schema['properties']['revision']['minimum']
            assert re.fullmatch(COMMON['sha256']['pattern'], ref['sha256'])
            assert ref['sha256'] == obj.content_sha256 == obj.compute_hash()
            assert obj.hash_ok()
        assert policy.pack == expected_pack
    assert len({obj.id for env, policy, _ in generated for obj in (env, policy)}) == 4
