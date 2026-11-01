"""Fixture/projection contracts; actual library/mode execution runs separately."""
import json
import pytest
from guard.executors.dynamic_code_cpu import fixtures,identity,clean_phases,project,judge_params
from guard.judges.rule_table import RuleTableJudge
from guard.models import Verdict,CheckDef
from tests.builders import evidence
from pathlib import Path
import yaml


def row(entry='autoconfig',trust=False,mode='normal',events=None,outcome='rejected_at_trust_gate'):
    case=next(c for c in fixtures() if c['entry']==entry and c['trust']==trust and c['mode']==mode)
    return {**case,'case_identity':identity(case),'events':events or [],'outcome':outcome,'event_collection_status':'completed',
            'before':{'outcome':'accepted'},'after':{'outcome':'accepted'}}


def definition(check_id):
    return CheckDef.model_validate(yaml.safe_load((Path(__file__).parents[1]/'reference/pack-seeds/PACK-BASELINE-31/checks'/f'{check_id}.yaml').read_text()))


def test_two_loader_paths_reuse_one_case_matrix_for_three_python_modes():
    rows=fixtures();assert len(rows)==12 and len({c['case_id'] for c in rows})==12
    assert {c['mode'] for c in rows}=={'normal','O','OO'}
    assert {c['entry'] for c in rows}=={'autoconfig','vllm-config'}


def test_forbidden_import_is_preserved_with_unexpected_error_and_failed_after_control():
    item=row(events=[{'phase':'import'}],outcome='execution_error');item['after']={'outcome':'execution_error'}
    obs=project([item])['SUP-09']
    assert RuleTableJudge().judge(definition('SUP-09'),evidence(observations=obs),judge_params('SUP-09'),{}).verdicts['security']==Verdict.FAIL


def test_mode_violation_uses_the_matching_normal_baseline_and_preserves_missing_other_rows():
    base=row();optimized=row(mode='O',events=[{'phase':'import'}],outcome='accepted')
    facts=project([base,optimized])
    assert facts['SUP-11']['dynamic_mode_confirmed_violations']['value']==[optimized['case_id']]
    assert not facts['complete']


def test_missing_normal_or_different_entry_is_not_mode_equivalence_proof():
    optimized=row(mode='OO',events=[{'phase':'import'}],outcome='accepted')
    facts=project([row(entry='vllm-config'),optimized])
    assert facts['SUP-09']['dynamic_code_confirmed_violations']['value']==[optimized['case_id']]
    assert facts['SUP-11']['dynamic_mode_confirmed_violations']['value']==[]


def test_partial_normal_log_cannot_prove_absence_for_mode_comparison():
    base=row();base['event_collection_status']='partial'
    optimized=row(mode='O',events=[{'phase':'import'}],outcome='accepted')
    assert not project([base,optimized])['SUP-11']['dynamic_mode_confirmed_violations']['value']


def test_allowed_import_is_not_an_enterprise_policy_violation():
    facts=project([row(trust=True,events=[{'phase':'import'},{'phase':'constructor'}],outcome='accepted')])
    assert facts['SUP-09']['dynamic_code_confirmed_violations']['value']==[]


@pytest.mark.parametrize('outcome',['execution_error','rejected_at_trust_gate'])
def test_error_or_rejection_is_not_full_security_pass(outcome):
    obs=project([row(outcome=outcome)])['SUP-09']
    assert RuleTableJudge().judge(definition('SUP-09'),evidence(observations=obs),judge_params('SUP-09'),{}).verdicts['security']==Verdict.UNKNOWN


def test_bad_other_identity_or_event_never_erases_valid_violation():
    good=row(events=[{'phase':'import'}],outcome='accepted')
    bad=row(mode='O');bad['case_identity']='0'*64
    bad_event=row(mode='OO');bad_event['events']=[{'phase':'private raw data'}]
    facts=project([good,bad,bad_event])
    assert facts['SUP-09']['dynamic_code_confirmed_violations']['value']==[good['case_id']]
    assert 'private' not in json.dumps(facts)


def test_error_text_alone_is_not_an_actual_trust_gate_exception():
    from tools.b1_dynamic_code_cpu import trust_rejection
    assert not trust_rejection(ValueError('resolve_trust_remote_code private text'))


def test_marker_parser_preserves_valid_prefix_without_saving_bad_line(tmp_path):
    from tools.b1_dynamic_code_cpu import read_events
    p=tmp_path/'events';p.write_text('{"phase":"import"}\nprivate malformed text\n')
    events,status=read_events(p);assert status=='partial' and events==[{'phase':'import'}]
    assert 'private' not in json.dumps(events)


@pytest.mark.parametrize('change',[
    {'outcome':'not_executed'}, {'before':None}, {'after':None},
    {'selected_fixture_source_matches':False}, {'events':[]},
])
def test_incomplete_positive_witness_cannot_close_cpu_batch(change):
    rows=[]
    for case in fixtures():
        item=row(case['entry'],case['trust'],case['mode'],
                 [{'phase':'import'},{'phase':'constructor'}] if case['trust'] else [],
                 'accepted' if case['trust'] else 'rejected_at_trust_gate')
        item['selected_fixture_source_matches']=True
        rows.append(item)
    assert project(rows)['complete']
    next(x for x in rows if x['trust']).update(change)
    assert not project(rows)['complete']
