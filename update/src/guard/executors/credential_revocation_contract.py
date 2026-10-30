"""PLT10 declared issuer event vs explicit unconditional access constraint."""
from datetime import timedelta
import re
from .rank_failure_contract import stamp

PROFILE='kubernetes-credential-revocation-observation-v1'
POLICY_TYPES={'revocation_environment_sha256':'str','revocation_target_sha256':'str','revocation_prepared_sha256':'str',
 'revocation_validity_start':'str','revocation_validity_end':'str','revocation_issuer_id':'str','revocation_event_id':'str',
 'revocation_event_at':'str','revocation_deny_after':'str','revocation_deadline_basis':'str','revocation_propagation_limit_ms':'int',
 'revocation_max_clock_skew_ms':'int','revocation_subject_label':'str','revocation_max_new_access_samples':'int'}
OBSERVATIONS={'revocation_trials':'list','revocation_auth_bindings':'list','revocation_conflict_candidates':'list','revocation_missing':'list',
 'revocation_absolute_denial_constraint_violated':'bool','revocation_issuer_event_verified':'bool','revocation_provider_propagation_verified':'bool',
 'revocation_normal_before_issuer_verified':'bool','revocation_new_connection_receipt_verified':'bool','revocation_scope_complete':'bool'}
SLOTS=('control-before','retired-first','retired-second','retired-third','control-after')


def normalize_policy(v):
    try:
        if type(v) is not dict or set(v)!=set(POLICY_TYPES):raise ValueError
        for key in ('revocation_environment_sha256','revocation_target_sha256','revocation_prepared_sha256'):
            if type(v[key]) is not str or re.fullmatch('[0-9a-f]{64}',v[key]) is None:raise ValueError
        for key in ('revocation_issuer_id','revocation_event_id'):
            if type(v[key]) is not str or re.fullmatch('[A-Za-z][A-Za-z0-9_.-]{0,63}',v[key]) is None:raise ValueError
        start,end=stamp(v['revocation_validity_start']),stamp(v['revocation_validity_end'])
        event,deadline=stamp(v['revocation_event_at']),stamp(v['revocation_deny_after'])
        if not 0<(end-start).total_seconds()<=600 or not start<=deadline<=end or event>deadline:raise ValueError
        if v['revocation_deadline_basis'] not in {'absolute-unconditional','after-verified-issuer-event'}:raise ValueError
        if type(v['revocation_propagation_limit_ms']) is not int or not 0<=v['revocation_propagation_limit_ms']<=600000:raise ValueError
        if type(v['revocation_max_clock_skew_ms']) is not int or not 0<=v['revocation_max_clock_skew_ms']<=60000:raise ValueError
        if v['revocation_deadline_basis']=='after-verified-issuer-event' and event+timedelta(milliseconds=v['revocation_propagation_limit_ms'])!=deadline:raise ValueError
        if v['revocation_subject_label']!='retired' or type(v['revocation_max_new_access_samples']) is not int or v['revocation_max_new_access_samples']!=3:raise ValueError
        return start,end,event,deadline
    except (KeyError,ValueError,TypeError,OverflowError):raise ValueError('CREDENTIAL_REVOCATION_POLICY_INVALID') from None


def auth_bindings(packet,trials):
    result=[];index={} if not packet else packet.get('route_handler_index',{})
    entries=index.get('invocations',{});ambiguous=set(index.get('ambiguous_asgi_request_sha256',[]))
    for trial in trials:
        matches=[v for v in entries.values() if v.get('asgi_request_sha256')==trial['request_id_sha256']]
        bound=False;accepted=False;rejected=False;handler=False;returned=False;references=[]
        if len(matches)==1 and trial['request_id_sha256'] not in ambiguous:
            entry=matches[0];tokens=entry.get('token_results',[]);references=entry.get('events',[])
            positions={r[0] for r in references if type(r) is list and len(r)==2}
            if len(positions)==1:
                number=next(iter(positions));recordings=[] if not packet else packet.get('recordings',[])
                recording=recordings[number] if type(number) is int and 0<=number<len(recordings) else {}
                capture=recording.get('capture') or {}
                bound=bool(entry.get('metadata_consistent') and entry.get('recording_closed') and entry.get('source_modules_observed')
                    and capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match') and len(tokens)==1)
                accepted=bool(bound and tokens[0].get('token_verified') is True and entry.get('accepted_token_before_handler_observed'))
                rejected=bool(bound and tokens[0].get('token_verified') is False)
                handler=entry.get('registered_handler_observed') is True;returned=entry.get('handler_return_observed') is True
        result.append({'case_id':trial['case_id'],'request_id_sha256':trial['request_id_sha256'],'credential_label':trial['credential_label'],
            'unique_source_process_invocation_observed':bound,'accepted_token_before_handler_observed':accepted,
            'rejected_token_observed':rejected,'registered_handler_observed':handler,'handler_return_observed':returned,
            'event_refs':references,'producer_authenticated':False,'principal_identity_verified':False})
    return result


def project(packet,trials,values):
    start,end,event,deadline=normalize_policy(values);bindings=auth_bindings(packet,trials);violation=False;candidates=[];missing=[]
    bycase={v['case_id']:v for v in bindings};normal=next((r for r in trials if r['case_id']=='plt10-control-before'),None)
    normal_binding=bycase.get('plt10-control-before',{})
    control=bool(normal and normal.get('binding_verified') and normal.get('normal_shape_verified') and normal.get('policy_window_verified')
        and normal_binding.get('accepted_token_before_handler_observed') and normal_binding.get('handler_return_observed'))
    for trial in trials:
        binding=bycase[trial['case_id']]
        if not binding['unique_source_process_invocation_observed']:missing.append('unique_selected_native_auth_invocation_unavailable')
        if trial['credential_label']!='retired':continue
        late=bool(trial.get('started_at') and (stamp(trial['started_at'])-deadline).total_seconds()>values['revocation_max_clock_skew_ms']/1000)
        accepted=bool(control and trial.get('binding_verified') and trial.get('normal_shape_verified') and trial.get('policy_window_verified')
            and binding['accepted_token_before_handler_observed'] and binding['registered_handler_observed'] and binding['handler_return_observed'])
        confirmed=bool(accepted and late and values['revocation_deadline_basis']=='absolute-unconditional')
        violation=violation or confirmed
        candidates.append({'case_id':trial['case_id'],'credential_label':'retired','late_declared_deadline_observed':late,
            'selected_credential_functional_acceptance_observed':accepted,'absolute_denial_constraint_violated':confirmed,
            'issuer_revocation_verified':False,'provider_propagation_verified':False,'confirmed_security_bypass':False})
    if not control:missing.append('active_credential_source_bound_normal_control_unavailable')
    missing.extend(['issuer_event_and_effective_policy_cache_producer_unverified','credential_label_is_not_trusted_principal_identity',
        'pre_issuer_normal_and_provider_propagation_clock_unverified','fixed_fresh_connection_source_design_without_independent_connection_receipt',
        'existing_session_and_copy_cleanup_are_PLT11_PLT12_separate_scopes'])
    return {'bindings':bindings,'candidates':candidates,'absolute_denial_constraint_violated':violation,'missing':sorted(set(missing)),
        'issuer_event_verified':False,'provider_propagation_verified':False,'normal_before_issuer_verified':False,'scope_complete':False}
