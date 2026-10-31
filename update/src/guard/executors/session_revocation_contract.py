"""PLT11 selected existing probe connection vs declared issuer event."""
from datetime import timedelta
import re
from .credential_revocation_contract import auth_bindings
from .rank_failure_contract import stamp

PROFILE='kubernetes-existing-http-session-observation-v1'
POLICY_TYPES={'session_environment_sha256':'str','session_target_sha256':'str','session_prepared_sha256':'str',
 'session_validity_start':'str','session_validity_end':'str','session_issuer_id':'str','session_event_id':'str','session_event_at':'str',
 'session_open_before':'str','session_deny_after':'str','session_deadline_basis':'str','session_propagation_limit_ms':'int',
 'session_max_clock_skew_ms':'int','session_max_wait_seconds':'int','session_residual_policy':'str'}
OBSERVATIONS={'session_trials':'list','session_native_bindings':'list','session_conflict_candidates':'list','session_missing':'list',
 'session_existing_connection_constraint_violated':'bool','session_selected_probe_connection_reuse_observed':'bool',
 'session_distinct_new_probe_connection_observed':'bool','session_issuer_event_verified':'bool','session_provider_propagation_verified':'bool',
 'session_application_session_or_handle_verified':'bool','session_serving_session_receipt_verified':'bool','session_scope_complete':'bool'}


def epoch_ns(value):
    value=stamp(value);result=int(value.timestamp())*10**9+value.microsecond*1000
    if not 0<result<2**63:raise ValueError('SESSION_POLICY_TIME_UNSUPPORTED')
    return result


def normalize_policy(v):
    try:
        if type(v) is not dict or set(v)!=set(POLICY_TYPES):raise ValueError
        for key in ('session_environment_sha256','session_target_sha256','session_prepared_sha256'):
            if type(v[key]) is not str or re.fullmatch('[0-9a-f]{64}',v[key]) is None:raise ValueError
        for key in ('session_issuer_id','session_event_id'):
            if type(v[key]) is not str or re.fullmatch('[A-Za-z][A-Za-z0-9_.-]{0,63}',v[key]) is None:raise ValueError
        start,end,event,opening,deadline=(stamp(v[key]) for key in ('session_validity_start','session_validity_end','session_event_at','session_open_before','session_deny_after'))
        if not 0<(end-start).total_seconds()<=600 or not start<opening<=event<=deadline<=end or opening>=deadline:raise ValueError
        for key,maximum in (('session_propagation_limit_ms',600000),('session_max_clock_skew_ms',60000)):
            if type(v[key]) is not int or not 0<=v[key]<=maximum:raise ValueError
        if type(v['session_max_wait_seconds']) is not int or not 1<=v['session_max_wait_seconds']<=10:raise ValueError
        if v['session_deadline_basis'] not in {'absolute-unconditional','after-verified-issuer-event'} or v['session_residual_policy']!='deny-existing-after-deadline':raise ValueError
        if v['session_deadline_basis']=='after-verified-issuer-event' and event+timedelta(milliseconds=v['session_propagation_limit_ms'])!=deadline:raise ValueError
        for key in ('session_validity_start','session_validity_end','session_event_at','session_open_before','session_deny_after'):epoch_ns(v[key])
        return start,end,event,opening,deadline
    except (KeyError,ValueError,TypeError,OverflowError,OSError):raise ValueError('SESSION_REVOCATION_POLICY_INVALID') from None


def project(native,trials,values,transport):
    normalize_policy(values);bindings=auth_bindings(native,trials);bycase={r['case_id']:r for r in bindings};rows={r['case_id']:r for r in trials}
    def accepted(case):
        row=rows.get(case,{});binding=bycase.get(case,{})
        return bool(transport.get('binding_before_verified') and transport.get('binding_after_verified') and row.get('normal_shape_verified')
            and row.get('capture_complete') and row.get('clock_consistent') and row.get('policy_window_verified')
            and binding.get('unique_source_process_invocation_observed') and binding.get('accepted_token_before_handler_observed')
            and binding.get('registered_handler_observed') and binding.get('handler_return_observed'))
    opened=rows.get('plt11-retired-session-open',{});reused=rows.get('plt11-retired-session-reuse',{});new=rows.get('plt11-retired-new',{})
    same=bool(opened.get('selected_existing_socket_verified') and reused.get('selected_existing_socket_verified')
        and opened.get('probe_socket_before') is not None and opened['probe_socket_before']==opened.get('probe_socket_after')
        ==reused.get('probe_socket_before')==reused.get('probe_socket_after'))
    earlier=bool(opened.get('finished_wall_ns') and opened['finished_wall_ns']<epoch_ns(values['session_open_before']))
    late=bool(reused.get('started_wall_ns') and reused['started_wall_ns']>epoch_ns(values['session_deny_after'])+values['session_max_clock_skew_ms']*10**6)
    control=accepted('plt11-control-before') and accepted('plt11-retired-session-open') and earlier
    candidate=bool(control and same and accepted('plt11-retired-session-reuse') and late)
    violation=bool(candidate and values['session_deadline_basis']=='absolute-unconditional')
    missing=['issuer_event_and_effective_policy_cache_producer_unverified','probe_TCP_connection_is_not_application_session_or_handle',
        'credential_label_is_not_trusted_principal','serving_session_receipt_unverified','whole_session_and_handle_scope_unverified']
    if not control:missing.append('selected_source_bound_predeclared_event_normal_unavailable')
    if not same:missing.append('same_selected_probe_socket_unavailable')
    if not new.get('new_distinct_probe_connection_verified'):missing.append('distinct_new_probe_socket_unavailable')
    if any(not r['unique_source_process_invocation_observed'] for r in bindings):missing.append('selected_native_auth_binding_unavailable')
    return {'bindings':bindings,'candidates':[{'case_id':'plt11-retired-session-reuse','same_selected_probe_connection':same,
        'late_declared_deadline_observed':late,'source_bound_existing_connection_acceptance_observed':candidate,
        'explicit_unconditional_constraint_violated':violation,'confirmed_security_bypass':False}],
        'existing_connection_constraint_violated':violation,'selected_probe_connection_reuse_observed':same,
        'distinct_new_probe_connection_observed':new.get('new_distinct_probe_connection_verified') is True,'missing':sorted(set(missing)),
        'issuer_event_verified':False,'provider_propagation_verified':False,'application_session_or_handle_verified':False,
        'serving_session_receipt_verified':False,'scope_complete':False,'producer_authenticated':False}
