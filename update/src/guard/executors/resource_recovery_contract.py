"""Explicit LIF11 ownership and deadline inputs; no inferred reclaim approval."""
from .rank_failure_contract import stamp
from guard.adapters.resource_recovery_probe import fingerprint,validate_selection,NAMESPACE_FIELDS

PROFILE='kubernetes-selected-resource-recovery-v1'
POLICY_TYPES={'recovery_environment_sha256':'str','recovery_target_sha256':'str','recovery_prepared_sha256':'str',
 'recovery_selection_sha256':'str','recovery_scenario_sha256':'str','recovery_validity_start':'str','recovery_validity_end':'str',
 'recovery_branch':'str','recovery_executor_backend':'str','recovery_expected_ranks':'list[str]','recovery_phase':'str',
 'recovery_deadline_basis':'str','recovery_required_absent_after':'str','recovery_max_clock_skew_ms':'int','recovery_required_resource_kinds':'list[str]'}
OBSERVATIONS={'recovery_trials':'list','recovery_resource_censuses':'list','recovery_selected_resource_status':'list','recovery_missing':'list',
 'recovery_absolute_deadline_violated':'bool','recovery_selected_absence_observed':'bool','recovery_global_resources_released':'bool',
 'recovery_next_normal_launch_verified':'bool','recovery_scope_complete':'bool'}


def normalize_policy(values):
    import re
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        for key in ('recovery_environment_sha256','recovery_target_sha256','recovery_prepared_sha256','recovery_selection_sha256','recovery_scenario_sha256'):
            if not fingerprint(values[key]):raise ValueError
        start,end=stamp(values['recovery_validity_start']),stamp(values['recovery_validity_end']);deadline=stamp(values['recovery_required_absent_after'])
        if not 0<(end-start).total_seconds()<=600 or not start<=deadline<=end:raise ValueError
        ranks=values['recovery_expected_ranks']
        if (values['recovery_executor_backend']!='mp' or type(ranks) is not list or not 1<=len(ranks)<=64
                or any(type(v) is not str or re.fullmatch('0|[1-9][0-9]?',v) is None or int(v)>63 for v in ranks) or len(set(ranks))!=len(ranks)):raise ValueError
        if values['recovery_branch'] not in {'runtime-failure','partial-initialization'} or values['recovery_phase'] not in {'before-failure','after-cleanup','restarted-normal'}:raise ValueError
        if values['recovery_deadline_basis'] not in {'absolute-unconditional','after-verified-failure'}:raise ValueError
        if type(values['recovery_max_clock_skew_ms']) is not int or not 0<=values['recovery_max_clock_skew_ms']<=60000:raise ValueError
        kinds=values['recovery_required_resource_kinds']
        if (type(kinds) is not list or not 1<=len(kinds)<=6 or len(set(kinds))!=len(kinds)
                or not set(kinds)<={'process','unix-socket','posix-shm-fd','gpu-memory','queue','shared-reference'}):raise ValueError
        return start,end,deadline
    except (KeyError,ValueError,TypeError):raise ValueError('RESOURCE_RECOVERY_POLICY_INVALID') from None


def validate_selected_policy(selection,values):
    validate_selection(selection);normalize_policy(values)
    if {o['rank'] for o in selection['owners']}!={int(v) for v in values['recovery_expected_ranks']}:raise ValueError('RESOURCE_SELECTED_RANKS_MISMATCH')
    if any(r['kind'] not in values['recovery_required_resource_kinds'] for r in selection['resources']):raise ValueError('RESOURCE_SELECTED_KIND_UNDECLARED')


def project(censuses,selection,values):
    from guard.adapters.resource_recovery_probe import validate_result
    start,end,deadline=normalize_policy(values);rows=[];missing=[];violation=False;absence=False
    maps=[]
    for snapshot in censuses:
        if snapshot.get('collected') is not True:maps.append(None);continue
        value=validate_result(snapshot['packet'],selection)
        clock=(snapshot['clock_window_verified'] is True and start<=stamp(snapshot['started_at'])<=stamp(snapshot['finished_at'])<=end)
        bound=snapshot['owner_binding_verified'] is True and clock
        maps.append((value,bound,snapshot))
    first=maps[0] if len(maps)>0 else None;last=maps[1] if len(maps)>1 else None
    baseline_owners={r['id']:r for r in first[0]['owners']} if first and first[1] else {}
    baseline_resources={r['id']:r for r in first[0]['resources']} if first and first[1] else {}
    selected_rows=[('process',r) for r in selection['owners']]+[(r['kind'],r) for r in selection['resources']]
    after_rows={r['id']:r for r in last[0]['owners']+last[0]['resources']} if last and last[1] else {}
    skew=values['recovery_max_clock_skew_ms']/1000
    late=bool(last and last[1] and (stamp(last[2]['started_at'])-deadline).total_seconds()>skew
        and last[0]['started_wall_ns']>int(deadline.timestamp()*10**9))
    for kind,selected in selected_rows:
        before=(baseline_owners if kind=='process' else baseline_resources).get(selected['id']);after=after_rows.get(selected['id'])
        bound=bool(before and before['status']=='match' and after)
        retained=bool(bound and after['status']=='match')
        absent=bool(bound and after['status']=='absent')
        confirmed=(retained and selected['require_absent'] and late and values['recovery_deadline_basis']=='absolute-unconditional')
        violation=violation or confirmed
        rows.append({'id':selected['id'],'kind':kind,'before_status':before['status'] if before else None,'after_status':after['status'] if after else None,
            'same_selected_baseline_observed':bound,'selected_retained_observed':retained,'selected_absent_observed':absent,
            'unconditional_absolute_deadline_verified':late and values['recovery_deadline_basis']=='absolute-unconditional',
            'required_absence_deadline_violated':confirmed,'global_resource_release_verified':False})
    required=[r for r,s in zip(rows,[v for k,v in selected_rows]) if s['require_absent']]
    absence=bool(required and all(r['selected_absent_observed'] for r in required))
    if not first or not first[1] or not last or not last[1]:missing.append('complete_selected_before_after_owner_clock_binding_unavailable')
    if values['recovery_deadline_basis']=='after-verified-failure':missing.append('independent_failure_trigger_and_conditional_deadline_binding_unavailable')
    missing.extend(['selected_fd_absence_is_not_global_ipc_shared_memory_or_gpu_release','all_resource_owners_refs_and_return_times_unverified',
        'normal_and_failure_repaired_launch_lineage_inflight_and_inference_unverified','native_source_claim_is_not_selected_kernel_owner_authentication'])
    return {'rows':rows,'absolute_deadline_violated':violation,'selected_absence_observed':absence,'missing':missing,
        'global_resources_released':False,'next_normal_launch_verified':False,'scope_complete':False}
