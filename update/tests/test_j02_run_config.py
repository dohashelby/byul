"""tools/j02_run_config.py: one guard update config for the J02 checks (static, never a verdict)."""
from __future__ import annotations

import json
import tomllib

import pytest
import yaml

from guard.configuration import for_check, merge_config
from guard.executors import run_binding
from tests.builders import environment
from tools import j02_run_config as tool

SESSION = '0123456789abcdef0123456789abcdef'


def test_j02_scope_is_the_63_checks_of_the_plan():
    checks = tool.j02_checks()
    assert len(checks) == 63 == len(set(checks))
    # Every stage-2 check of J02 needs a run_binding entry the tool can carry.
    assert {c for c in checks if c in run_binding.CONFIG_KEYS} == {
        'LIF-05', 'LIF-06', 'LIF-07', 'LIF-09', 'LIF-12', 'LIF-16', 'OBS-01', 'OBS-02', 'OBS-06', 'OBS-11',
        'PLT-07', 'PLT-08', 'PLT-12', 'REQ-02', 'REQ-04', 'REQ-06'}


def spec():
    return {'executors': {'lifecycle_transition': 'kubernetes', 'required_event_record': 'kubernetes'},
            'shared': {'lifecycle_transition': {'kubectl_binary': 'kubectl', 'max_duration_seconds': 300}},
            'sides': {side: {'env': side + '-env.json',
                             'checks': {'LIF-05': {'executor': {'generations': [{'id': 'old', 'target': side + '-t.json',
                                                                                 'prepared': 'p.json', 'environment': side + '-env.json'}]}},
                                        'OBS-01': {'run_binding': {'record_session': SESSION}}}}
                      for side in tool.SIDES}}


def test_config_round_trips_and_each_side_keeps_its_own_check_tables():
    config = tool.build_config(spec())
    text = tool.dumps_toml(config)
    assert tomllib.loads(text) == config
    for side in tool.SIDES:
        effective = merge_config(config, config['sides'][side])
        lif05 = for_check(effective, 'lifecycle_transition', 'LIF-05')['lifecycle_transition']
        assert lif05['generations'][0]['target'] == side + '-t.json' and lif05['max_duration_seconds'] == 300
        assert run_binding.run_config(effective, 'OBS-01') == {'record_session': SESSION}
        assert for_check(effective, 'lifecycle_transition', 'LIF-06')['lifecycle_transition'] == config['lifecycle_transition']


def test_report_states_what_is_missing_and_never_claims_more(tmp_path):
    for side in tool.SIDES:
        env = environment(engine={'name': 'vllm', 'version': '0.31.0'}, model={'id': 'm', 'revision': 'r'})
        (tmp_path / (side + '-env.json')).write_text(json.dumps(env.to_dict()))
    (tmp_path / 'policy.yaml').write_text(yaml.safe_dump({'pack': {'id': 'PACK-BASELINE', 'revision': 36}, 'values': {}}))
    declared = {**spec(), 'policy': 'policy.yaml'}
    (tmp_path / 'spec.yaml').write_text(yaml.safe_dump(declared))
    tool.main([str(tmp_path / 'spec.yaml'), '--out', str(tmp_path / 'out')])
    result = json.loads((tmp_path / 'out/report.json').read_text())
    assert result['checks'] == 63 and len(result['rows']) == 126
    assert 'READY_STATIC' not in result['counts'] or result['counts']['READY_STATIC'] < 126
    rows = {(r['side'], r['check_id']): r for r in result['rows']}
    assert rows['baseline', 'REQ-01']['reasons'] == ['EXECUTOR_IMPLEMENTATION_NOT_SELECTED']
    # LIF-05: implementation chosen, but its transition table and generation files are not there.
    lif05 = rows['baseline', 'LIF-05']
    assert lif05['status'] == 'MISSING_INPUT' and lif05['run_binding_entries'] == ['phase_generation', 'sample_offsets_ms']
    assert any(r.startswith('RUN_BINDING_') for r in lif05['reasons'])
    # OBS-01: enterprise policy values are reported missing, never filled.
    assert 'POLICY_VALUES_MISSING' in rows['candidate', 'OBS-01']['reasons']
    assert rows['candidate', 'OBS-01']['policy_keys_missing']
    assert tomllib.loads((tmp_path / 'out/guard.toml').read_text()) == tool.build_config(declared)
    with pytest.raises(SystemExit):
        tool.main([str(tmp_path / 'spec.yaml'), '--out', str(tmp_path / 'out')])


def test_policy_for_another_pack_is_refused(tmp_path):
    (tmp_path / 'policy.yaml').write_text(yaml.safe_dump({'pack': {'id': 'PACK-BASELINE', 'revision': 35}, 'values': {}}))
    with pytest.raises(ValueError, match='POLICY_PACK_REVISION_MISMATCH'):
        tool.report({'policy': 'policy.yaml', 'sides': {}}, tool.build_config({}), base=tmp_path)
