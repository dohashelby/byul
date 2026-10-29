"""Draft explicit selected mp inputs; no fault activation or policy inference."""
from datetime import datetime
import re
from guard.adapters.vllm_rank_failure_metadata import FAULT_KINDS

PROFILE='kubernetes-mp-rank-failure-observation-v1'
POLICY_TYPES={'rank_environment_sha256':'str','rank_target_sha256':'str','rank_prepared_sha256':'str',
 'rank_validity_start':'str','rank_validity_end':'str','rank_executor_backend':'str','rank_expected_ranks':'list[str]','rank_output_rank':'int'}
for kind in FAULT_KINDS:
 for step in ('detection','notification'):POLICY_TYPES['rank_'+kind.replace('-','_')+'_'+step+'_ms']='int'
OBSERVATIONS={'rank_observation_trials':'list','rank_native_samples':'list','rank_native_calls':'list','rank_failure_candidates':'list',
 'rank_fault_cases':'list','rank_missing':'list','rank_producer_authenticated':'bool','rank_normal_all_ranks_verified':'bool',
 'rank_upper_client_receipt_verified':'bool','rank_scope_complete':'bool'}


def stamp(value):
    if type(value) is not str or len(value)>40:raise ValueError
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError
    return result


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES) or values['rank_executor_backend']!='mp':raise ValueError
        for key in ('rank_environment_sha256','rank_target_sha256','rank_prepared_sha256'):
            if type(values[key]) is not str or re.fullmatch('[0-9a-f]{64}',values[key]) is None:raise ValueError
        start,end=stamp(values['rank_validity_start']),stamp(values['rank_validity_end'])
        if not 0<(end-start).total_seconds()<=600:raise ValueError
        ranks=values['rank_expected_ranks'];output=values['rank_output_rank']
        if (type(ranks) is not list or not 1<=len(ranks)<=64 or any(type(v) is not str or re.fullmatch('0|[1-9][0-9]?',v) is None or int(v)>63 for v in ranks)
                or len(set(ranks))!=len(ranks) or type(output) is not int or str(output) not in ranks):raise ValueError
        for key,kind in POLICY_TYPES.items():
            if kind=='int' and key!='rank_output_rank' and (type(values[key]) is not int or not 0<=values[key]<2**63):raise ValueError
        return start,end
    except (KeyError,TypeError,ValueError):raise ValueError('RANK_FAILURE_POLICY_INVALID') from None
