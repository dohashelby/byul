"""EXAMPLE classification and tool-boundary contracts; real source runs separately."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from guard.executors.structured_limits import fixtures, describe, project
from guard.models import Verdict
from tests.test_structured_limits import check, evidence, judge_params, RuleTableJudge
from tools import req10_structured_limits as tool


def row(case_id, outcome='accepted'):
    case = next(case for case in fixtures() if case['id'] == case_id)
    return {**describe(case), 'outcome': outcome, 'events': [], 'after': {'outcome': 'execution_error'}}


def test_same_fifteen_input_identities_but_no_reference_depth_expectation_in_target():
    before = fixtures()
    specs = [tool.target031_spec(case) for case in before]
    assert len(specs) == 15
    assert [r['input_canonical_sha256'] for r in specs] == [describe(c)['input_canonical_sha256'] for c in before]
    for spec, case in zip(specs, before):
        assert spec['source_commit'] == tool.TARGET031_COMMIT
        if case['rule'] in {'reference_depth', 'reference_tag_depth'}:
            assert spec['expected'] is None and spec['rule'] == 'target031_depth_observation'
            assert spec['expectation_status'] == 'source_specific_expectation_unavailable'
    assert fixtures() == before
    assert next(c for c in fixtures() if c['id'] == 'depth-65-dict')['expected'] == 'rejected'


def test_accepted_depth65_is_reference_violation_but_target_observation():
    sample = row('depth-65-dict')
    assert project([sample])['structured_limits_confirmed_violations']['value'] == ['depth-65-dict']
    observation = tool.project_target031([sample])
    assert observation['structured_limits_confirmed_violations']['value'] == []
    assert not observation['structured_limits_cases_complete']['value']
    verdict = RuleTableJudge().judge(check(), evidence(observations=observation), judge_params(), {})
    assert verdict.verdicts['security'] == Verdict.UNKNOWN


def test_only_explicit_uniqueitems_rule_preserves_violation_with_missing_after():
    rows = [row('unsupported-unique-items')]
    cleaned = tool.target031_rows(rows)
    assert cleaned[0]['expected'] == 'rejected' and cleaned[0]['rule_source']['keyword'] == 'uniqueItems'
    observation = tool.project_target031(rows)
    assert observation['structured_limits_confirmed_violations']['value'] == ['unsupported-unique-items']
    verdict = RuleTableJudge().judge(check(), evidence(observations=observation), judge_params(), {})
    assert verdict.verdicts['security'] == Verdict.FAIL
    assert not observation['structured_limits_cases_complete']['value']


@pytest.mark.parametrize('invalid', ['duplicate', 'unknown', 'hash'])
def test_invalid_corpus_identity_stops_target_projection(invalid):
    sample = row('depth-65-dict')
    rows = [sample]
    if invalid == 'duplicate': rows.append(sample)
    elif invalid == 'unknown': sample['case_id'] = 'unknown'
    else: sample['input_canonical_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='STRUCTURED_LIMITS_TARGET031_FIXTURE_MISMATCH'):
        tool.project_target031(rows)


def test_caller_supplied_reference_expectation_is_replaced():
    sample = row('depth-65-dict')
    cleaned, = tool.target031_rows([sample])
    assert sample['expected'] == 'rejected' and cleaned['expected'] is None
    assert 'private' not in json.dumps(cleaned)


def test_main_default_preserves_reference_profile(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tool, 'capture', lambda root, **kw: calls.append(kw) or {'status': 'completed'})
    output = tmp_path / 'result.json'
    monkeypatch.setattr(sys, 'argv', ['limits', '--source-root', str(tmp_path), '--output', str(output)])
    assert tool.main() == 0 and calls == [{'source_profile': 'reference'}]


@pytest.mark.parametrize('optimization', ['-O', '-OO'])
@pytest.mark.parametrize('profile', ['reference', 'vllm-031'])
def test_optimized_tool_stops_before_source_or_output(tmp_path, profile, optimization):
    update = Path(tool.__file__).resolve().parents[1]
    output = tmp_path / 'result.json'
    result = subprocess.run([sys.executable, optimization, str(Path(tool.__file__).resolve()),
        '--source-profile', profile, '--source-root', str(tmp_path / 'missing'), '--output', str(output)],
        cwd=update, env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(update / 'src'), str(update)])},
        text=True, capture_output=True)
    assert result.returncode != 0 and 'STRUCTURED_LIMITS_CPU_REQUIRES_ASSERTIONS' in result.stderr
    assert not output.exists()
