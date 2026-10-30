"""One explicitly declared startup stage; phase labels are not experiment proof."""
from .rank_failure_contract import stamp

PROFILE='kubernetes-mp-initialization-observation-v1'
POLICY_TYPES={'init_environment_sha256':'str','init_target_sha256':'str','init_prepared_sha256':'str',
 'init_validity_start':'str','init_validity_end':'str','init_executor_backend':'str','init_expected_ranks':'list[str]',
 'init_scenario_sha256':'str','init_phase':'str','init_required_stages':'list[str]','init_failure_rank':'int',
 'init_failure_stage':'str','init_preparation_deadline_ms':'int','init_recovery_deadline_ms':'int'}
OBSERVATIONS={'init_ready_endpoint_events':'list','init_ready_endpoint_candidates':'list','init_worker_generation_events':'list','init_worker_generation_candidates':'list','init_ready_send_observations':'list','init_worker_startup_events':'list','init_worker_startup_candidates':'list','init_worker_startup_missing':'list','init_observation_trials':'list','init_native_samples':'list','init_native_calls':'list','init_correspondence':'list',
 'init_missing':'list','init_producer_authenticated':'bool','init_all_preparation_verified':'bool','init_service_gate_verified':'bool',
 'init_cross_phase_lineage_verified':'bool','init_scope_complete':'bool'}


def normalize_policy(values):
    import re
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES) or values['init_executor_backend']!='mp':raise ValueError
        for key in ('init_environment_sha256','init_target_sha256','init_prepared_sha256','init_scenario_sha256'):
            if type(values[key]) is not str or re.fullmatch('[0-9a-f]{64}',values[key]) is None:raise ValueError
        start,end=stamp(values['init_validity_start']),stamp(values['init_validity_end'])
        if not 0<(end-start).total_seconds()<=600:raise ValueError
        ranks=values['init_expected_ranks'];stages=values['init_required_stages']
        if (type(ranks) is not list or not 1<=len(ranks)<=64 or any(type(v) is not str or re.fullmatch('0|[1-9][0-9]?',v) is None or int(v)>63 for v in ranks)
                or len(set(ranks))!=len(ranks) or type(values['init_failure_rank']) is not int or str(values['init_failure_rank']) not in ranks):raise ValueError
        if values['init_phase'] not in {'normal','partial-failure','recovered-normal'} or values['init_failure_stage'] not in {'worker-ready','model-load'}:raise ValueError
        if type(stages) is not list or len(stages)!=3 or set(stages)!={'worker-ready','model-load','admission'}:raise ValueError
        for key in ('init_preparation_deadline_ms','init_recovery_deadline_ms'):
            if type(values[key]) is not int or not 0<=values[key]<2**63:raise ValueError
        return start,end
    except (KeyError,TypeError,ValueError):raise ValueError('INITIALIZATION_POLICY_INVALID') from None
