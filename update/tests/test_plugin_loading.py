"""SUP-10 collector/judge contracts; actual installed loaders run separately."""
import json
import pytest
from guard.executors.plugin_loading import CASES, sanitize_events, project, judge_params
from guard.judges.rule_table import RuleTableJudge
from tests.builders import evidence
from guard.models import Verdict, CheckDef
from pathlib import Path
import yaml


def definition():
    return CheckDef.model_validate(yaml.safe_load((Path(__file__).parents[1]/'reference/pack-seeds/PACK-BASELINE-31/checks/SUP-10.yaml').read_text()))


def test_case_matrix_keeps_unset_empty_and_group_specific_scope():
    assert len(CASES) == 10 and len({r[0] for r in CASES}) == 10
    assert next(r for r in CASES if r[0]=='endpoint-unset')[2] is None
    assert next(r for r in CASES if r[0]=='endpoint-disabled')[2] == ''


def test_source_allowlist_violation_survives_other_error_and_followup_failure():
    rows = [{'case_id':'general-allowlisted','status':'execution_error','target_started':True,
             'events':[{'plugin':'byul-unapproved','phase':'import'}],'after':{'status':'execution_error'}},
            {'case_id':'endpoint-disabled','status':'execution_error'}]
    obs = project(rows)
    assert obs['plugin_loading_confirmed_violations']['value'] == ['general-allowlisted']
    assert not obs['plugin_cpu_cases_complete']['value']
    assert RuleTableJudge().judge(definition(), evidence(check_id='SUP-10',observations=obs),judge_params(),{}).verdicts['security']==Verdict.FAIL


def test_logits_import_and_endpoint_factory_before_task_filter_are_observations_not_policy_fail():
    rows = [{'case_id':'logits-allowlisted','status':'completed','events':[{'plugin':'byul-unapproved','phase':'import'}]},
            {'case_id':'endpoint-task-mismatch','status':'completed','events':[{'plugin':'byul-task-mismatch','phase':'import'}, {'plugin':'byul-task-mismatch','phase':'endpoint_factory'}]}]
    obs = project(rows)
    assert obs['plugin_loading_confirmed_violations']['value']==[]
    assert obs['plugin_cpu_phases']['value']['endpoint-task-mismatch']==['byul-task-mismatch:import','byul-task-mismatch:endpoint_factory']
    assert RuleTableJudge().judge(definition(),evidence(observations=obs),judge_params(),{}).verdicts['security']==Verdict.UNKNOWN


def test_endpoint_task_mismatch_cannot_attach_route():
    row={'case_id':'endpoint-task-mismatch','status':'completed','events':[{'plugin':'byul-task-mismatch','phase':'attach_router'}]}
    assert project([row])['plugin_loading_confirmed_violations']['value']==['endpoint-task-mismatch']


def test_partial_parse_preserves_valid_violation_and_marks_collection_incomplete():
    row={'case_id':'endpoint-disabled','status':'completed','event_collection_status':'partial',
         'events':[{'plugin':'byul-unapproved','phase':'import'}]}
    obs=project([row]);assert obs['plugin_loading_confirmed_violations']['value']==['endpoint-disabled']
    assert not obs['plugin_cpu_cases_complete']['value']


def test_bad_or_missing_other_event_cannot_erase_known_violation():
    rows=[{'case_id':'general-disabled','status':'completed','events':[{'plugin':'byul-unapproved','phase':'import'}]},
          {'case_id':'io-allowlisted','status':'completed','events':[{'plugin':'private','phase':'raw exception'}]}]
    obs=project(rows);assert obs['plugin_loading_confirmed_violations']['value']==['general-disabled']
    assert obs['plugin_cpu_phases']['value']['io-allowlisted']=='unknown'
    assert 'private' not in json.dumps(obs)


def test_bootstrap_attribution_failure_is_unknown():
    obs=project([{'case_id':'general-disabled','status':'execution_error',
                  'events':[{'plugin':'byul-unapproved','phase':'import'}]}])
    assert obs['plugin_loading_confirmed_violations']['value']==[]


@pytest.mark.parametrize('event',[{'plugin':'secret','phase':'import'}, {'plugin':'byul-approved','phase':'body'},
                                 {'plugin':'byul-approved','phase':'import','exception':'private'}])
def test_event_allowlist_never_passes_arbitrary_names_or_raw_fields(event):
    with pytest.raises(ValueError):sanitize_events([event])


def test_event_file_keeps_valid_markers_after_malformed_lines(tmp_path):
    from tools.b1_plugin_cpu import read_events
    path=tmp_path/'events'
    path.write_text('{"plugin":"byul-unapproved","phase":"import"}\nprivate raw junk\n')
    result=read_events(path)
    assert result=={'status':'partial','events':[{'plugin':'byul-unapproved','phase':'import'}]}
    assert 'private' not in json.dumps(result)
