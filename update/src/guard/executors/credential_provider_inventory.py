"""Bind safe API declarations around the existing qualified worker collection."""
from pathlib import Path
from guard.adapters import credential_provider_inventory as inventory
from guard.models import canonical_json,sha256_hex


def snapshot(transport):
    value={'available':False,'declarations':None,'diagnostic_codes':[]}
    try:
        pods=transport._verify(capture_snapshot=True)
        value.update(available=True,declarations=inventory.project(pods['engine'],transport.binding['engine']))
    except Exception:value['diagnostic_codes'].append('CREDENTIAL_PROVIDER_OWNED_SNAPSHOT_UNVERIFIED')
    return value


def finish(ctx,before,after,summary):
    """Never borrow a registered worker producer's whole-provider qualification."""
    result={'schema':'guard-owned-credential-provider-inventory/1','before':before,'after':after,
        'declarations_stable':False,'worker_associations':None,'diagnostic_codes':[],
        'provider_scope_verified':False,'gpu_qualified':False,'qualified_registered':False,
        'source_sha256':sha256_hex(Path(inventory.__file__).read_bytes()+Path(__file__).read_bytes())}
    if before['available']and after['available']:
        result['declarations_stable']=before['declarations']==after['declarations']
        if not result['declarations_stable']:result['diagnostic_codes'].append('CREDENTIAL_PROVIDER_DECLARATIONS_CHANGED')
    elif not before['available']:return result
    worker=summary.get('worker_security_probe')or{}
    scope=summary.get('worker_security_scope')or{}
    process=summary['process_observation'];packet=process.get('packet')or{}
    source=packet.get('capture_subject')or{}
    try:
        from guard.collectors.worker_security import load_prepared
        prepared,cfg=load_prepared(ctx._worker_security_prepared)
        if(worker.get('available')is not True or worker.get('qualified_registered')is not True
                or worker.get('target_sha256')!=sha256_hex(canonical_json(ctx.target))
                or worker.get('prepared_sha256')!=sha256_hex(canonical_json(prepared))):raise ValueError
        records=[]
        for record in worker['records']:
            subject=record['context']
            matches=[p for p in packet.get('processes',[])if p['pid_identity_stable']is True
                and p['pid']==subject['pid']and p['start_ticks']==subject['start_ticks']
                and p['uid_effective']==subject['uid_effective']and p['cap_eff']==subject['cap_eff']
                and p['mount_namespace_inode']==subject['mount_namespace_inode']
                and source.get('pid_namespace_inode')==subject['pid_namespace_inode']]
            if len(matches)==1:records.append(record)
        complete=(scope.get('selected_tp_worker_roster_verified')is True
            and sorted(r['rank']for r in records)==cfg['expected_ranks']
            and process.get('binding_verified_before_after')is True
            and result['declarations_stable']and before['declarations']['declarations_complete'])
        result['worker_associations']=inventory.associate(before['declarations'],records,roster_complete=complete)
    except Exception:result['diagnostic_codes'].append('CREDENTIAL_PROVIDER_WORKER_ASSOCIATION_UNVERIFIED')
    return result
