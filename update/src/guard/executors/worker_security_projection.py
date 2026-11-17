"""Merge actual worker access facts without promoting partial or unregistered data."""
from guard.models import DataOrigin,ORIGIN_STRENGTH,canonical_json,sha256_hex


def _capture(ctx,process_record):
    path=getattr(ctx,'_worker_security_prepared',None)
    if path is None:return None,[],False,None
    from guard.collectors.worker_security import load_prepared
    prepared,cfg=load_prepared(path)
    snapshot=ctx.cprobe.worker_security_snapshot(path)
    if (type(snapshot) is not dict or snapshot.get('profile')!='kubernetes-owned-worker-security-v1'
            or snapshot.get('target_sha256')!=sha256_hex(canonical_json(ctx.target))
            or snapshot.get('prepared_sha256')!=sha256_hex(canonical_json(prepared))
            or snapshot.get('available') is not True or snapshot.get('qualified_registered') is not True):
        return snapshot,[],False,cfg
    packet=process_record.get('packet') or {}
    source=packet.get('capture_subject') or {}
    selected=[]
    for record in snapshot['records']:
        subject=record['context']
        matches=[p for p in packet.get('processes',[]) if p['pid_identity_stable'] is True
            and p['pid']==subject['pid'] and p['start_ticks']==subject['start_ticks']
            and p['uid_effective']==subject['uid_effective'] and p['cap_eff']==subject['cap_eff']
            and p['mount_namespace_inode']==subject['mount_namespace_inode']
            and source.get('pid_namespace_inode')==subject['pid_namespace_inode']]
        if len(matches)==1:selected.append(record)
    complete=(snapshot.get('whole_selected_tp_roster_verified') is True
        and len(selected)==len(cfg['expected_ranks']) and packet.get('pid_inventory_stable') is True
        and packet.get('process_metadata_complete') is True and process_record.get('binding_verified_before_after') is True)
    current=DataOrigin(getattr(ctx,'_kubernetes_process_data_origin',DataOrigin.EXAMPLE))
    ctx._kubernetes_process_data_origin=min(current,DataOrigin(snapshot['data_origin']),key=lambda v:ORIGIN_STRENGTH[v])
    return snapshot,selected,complete,cfg


def capture(ctx,process_record):
    try:return _capture(ctx,process_record)
    except Exception:
        return {'profile':'kubernetes-owned-worker-security-v1','available':False,
            'diagnostic_codes':['WORKER_SECURITY_AUXILIARY_UNAVAILABLE']},[],False,None


def permissions(ctx,process_record,observations):
    snapshot,records,complete,cfg=capture(ctx,process_record)
    if records:
        from .container_config import decode_caps
        caps=sorted({c for row in records for c in decode_caps(row['context']['cap_eff'])})
        existing=observations['capabilities_effective']
        observations['capabilities_effective']={'value':sorted(set(existing['value'] or [])|set(caps)),'collected':True}
        if any(r['context']['uid_effective']==0 for r in records):
            observations['root_uid_present']={'value':True,'collected':True}
        outcomes={}
        for path in cfg['sentinel_paths']:
            values=[r['sentinel_results'][path] for r in records]
            outcomes[path]=('succeeded' if 'succeeded' in values else 'error' if not complete or 'error' in values
                else 'denied' if 'denied' in values else 'absent')
        observations['sentinel_results']={'value':outcomes,'collected':bool(outcomes)}
        writable=set()
        for row in records:
            for path,state in row['write_results'].items():
                if state!='succeeded':continue
                containing=[m for m in row['mounts'] if path==m['path'] or path.startswith(m['path'].rstrip('/')+'/')]
                if containing:
                    mount=max(containing,key=lambda m:len(m['path']))
                    if not mount['read_only'] and not mount['noexec']:writable.add(mount['path'])
        # A positively observed writable mount without noexec is retained even
        # if another selected worker/path is missing. Empty samples prove no
        # whole-namespace absence and cannot satisfy the existing PASS rule.
        if writable:
            previous=observations['exec_writable_mounts']['value'] or []
            observations['exec_writable_mounts']={'value':sorted(writable|set(previous)),'collected':True}
    # Only declared owned access paths and held device FDs were probed.
    # This is not every accessible device/mount or all privilege enforcement.
    return snapshot,{'selected_tp_worker_roster_verified':complete,'worker_probe_count':len(records),
        'whole_permission_scope_verified':False,'device_access_scope':'held worker device FDs only'}


def credentials(ctx,process_record,observations):
    snapshot,records,complete,cfg=capture(ctx,process_record)
    access_verified=False
    if records:
        observations['worker_env_names']={'value':sorted({name for r in records for name in r['environment_names']}),'collected':True}
        observations['process_scan_complete']={'value':complete,'collected':True}
        declared=process_record.get('mount_declarations') or []
        actual={r['rank']:{m['path'] for m in r['mounts']} for r in records}
        if (complete and process_record.get('mount_declarations_stable') is True
                and all(m['mount_path'] in actual[r['rank']] for m in declared for r in records)):
            bans=ctx.policy_values['forbidden_mount_paths']
            found={p for p in bans for m in declared if p==m['mount_path']
                or p.startswith(m['mount_path'].rstrip('/')+'/') or m['mount_path'].startswith(p.rstrip('/')+'/')}
            existing=observations['forbidden_mount_violations']['value'] or []
            observations['forbidden_mount_violations']={'value':sorted(found|set(existing)),'collected':True}
        selected=ctx.policy_values.get('credential_candidate_paths')
        if selected is not None and selected==cfg['credential_paths']:
            outcomes={}
            for path in selected:
                values=[r['credential_results'][path] for r in records]
                outcomes[path]=('candidate_readable' if 'candidate_readable' in values else 'unknown' if not complete or 'unknown' in values
                    else 'denied' if 'denied' in values else 'absent')
            observations['credential_candidate_results']={'value':outcomes,'collected':True}
            observations['credential_candidates_complete']={'value':complete and all(v in {'denied','absent'} for v in outcomes.values()),'collected':True}
            access_verified=bool(complete and selected and all(v in {'candidate_readable','denied','absent'} for v in outcomes.values()))
    return snapshot,{'selected_tp_worker_roster_verified':complete,'worker_probe_count':len(records),
        'candidate_access_verified':access_verified,
        'provider_scope_verified':False,'credential_contents_read':False,
        'scope':'explicit candidate paths and names on selected source-bound TP workers'}
