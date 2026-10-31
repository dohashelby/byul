"""Owned Unix IPC transport/packet boundary; A2 owns dedicated adjudication.

No path content, environment, argv, messages or exception text is evidence.
The existing REQUEST_BINDING_PROFILE supplies ENV/run and both Pod generations.
"""
from copy import deepcopy
import base64
import hashlib
from pathlib import Path
import re

from guard.models import canonical_json, sha256_hex, utcnow
from .kubernetes_ipc_contract import PROFILE, RESULT_SCHEMA, IPC_PROBE_SOURCE_SHA256, normalize_inputs

ACTOR_SCHEMA='guard-owned-unix-ipc-actor/1'
# Reviewed literal source pins are filled before freezing this bundle.
ACTOR_SOURCE_SHA256='5fe44e3599199324021f9691d9d048f75ead4ef340f8f24eebd190501cfaf4ea'
CONTRACT_SOURCE_SHA256='dcee148c16c9c97ec2031eb443daf8080c74dd481ab3621e8d3c655b2be31cec'
DIAGNOSTICS=frozenset({'IPC_ACTOR_DEADLINE','IPC_ACTOR_UNAVAILABLE','IPC_ACTOR_OUTPUT_BOUND',
    'IPC_ACTOR_OUTPUT_UNAVAILABLE','IPC_SNAPSHOT_DEADLINE','IPC_SNAPSHOT_UNAVAILABLE'})


def require(value):
    if not value:raise ValueError('KUBERNETES_IPC_PACKET_INVALID')


def integer(v,low=0,high=2**64-1):return type(v) is int and low<=v<=high
def digest(v):return type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None


def diagnostics(v):
    require(type(v) is list and len(v)<=len(DIAGNOSTICS) and len(v)==len(set(v))
        and all(type(x) is str and x in DIAGNOSTICS for x in v))


def namespaces(v,*,time_namespace=False):
    keys={'mnt','net','pid','user'}|({'time'} if time_namespace else set())
    require(type(v) is dict and set(v)==keys and all(type(x) is str
        and re.fullmatch(re.escape(k)+r':\[[0-9]{1,20}\]',x) is not None for k,x in v.items()))


def context(v):
    require(type(v) is dict and set(v)=={'pid','start_ticks','uids','gids','groups','cap_effective',
        'cap_permitted','cap_inheritable','cap_ambient','no_new_privs','namespaces','boot_sha256','time_offsets_sha256'})
    require(integer(v['pid'],1,2**31-1) and integer(v['start_ticks']))
    for k in ('uids','gids','groups'):
        require(type(v[k]) is list and len(v[k])==(4 if k!='groups' else len(v[k]))
            and len(v[k])<=128 and all(integer(x,0,2**32-1) for x in v[k]))
    require(v['groups']==sorted(set(v['groups'])))
    require(all(integer(v[k]) for k in ('cap_effective','cap_permitted','cap_inheritable','cap_ambient'))
        and integer(v['no_new_privs'],0,1) and digest(v['boot_sha256']) and digest(v['time_offsets_sha256']))
    namespaces(v['namespaces'],time_namespace=True)


def identity(v):
    require(type(v) is dict and set(v)=={'device','inode','uid','gid','mode','ctime_ns'}
        and all(integer(v[k]) for k in v) and v['mode']<=0o7777 and v['uid']<=2**32-1 and v['gid']<=2**32-1)


def packet(v,mode,count,ctx):
    require(type(v) is dict and set(v)==({'schema','mode','uid','namespaces','rows'}|({'groups'} if mode=='connect' else set()))
        and v['schema']=='guard-unix-ipc-probe/1' and v['mode']==mode
        and integer(v['uid'],0,2**32-1) and v['uid']==ctx['uids'][1])
    namespaces(v['namespaces'])
    require(v['namespaces']=={k:ctx['namespaces'][k] for k in v['namespaces']})
    if mode=='connect':require(type(v['groups']) is list and all(integer(x,0,2**32-1) for x in v['groups'])
        and v['groups']==ctx['groups'])  # Preserve actual groups, never rewrite for A2's old parser.
    require(type(v['rows']) is list and len(v['rows'])<=count)
    for index,row in enumerate(v['rows']):
        require(type(row) is dict and type(row.get('index')) is int and row['index']==index)
        if mode=='inspect':
            require(type(row.get('complete')) is bool)
            if row['complete']:
                require(set(row)=={'index','complete','identity','socket_inode','owners'})
                identity(row['identity'])
                require(type(row['socket_inode']) is str and re.fullmatch('[1-9][0-9]{0,19}',row['socket_inode']) is not None)
                owners=row['owners'];require(type(owners) is list and 1<=len(owners)<=16)
                for owner in owners:
                    require(type(owner) is dict and set(owner)=={'pid','start','uid'} and integer(owner['pid'],1,2**31-1)
                        and integer(owner['uid'],0,2**32-1) and type(owner['start']) is str
                        and re.fullmatch('0|[1-9][0-9]{0,19}',owner['start']) is not None)
                require([x['pid'] for x in owners]==sorted({x['pid'] for x in owners}))
            else:require(set(row)=={'index','complete','error'} and row['error']=='IPC_METADATA_UNAVAILABLE')
        else:
            require(set(row)<={'index','outcome','peer','identity','path_stable'}
                and type(row.get('outcome')) is str and row['outcome'] in {'connected','denied','unknown'})
            if 'identity' in row:identity(row['identity'])
            if 'peer' in row:
                peer=row['peer'];require(type(peer) is dict and set(peer)=={'pid','uid','gid'}
                    and integer(peer['pid'],1,2**31-1) and all(integer(peer[k],0,2**32-1) for k in ('uid','gid')))
            if 'path_stable' in row:require(type(row['path_stable']) is bool)
            if row['outcome']=='connected':require(set(row)=={'index','outcome','peer','identity','path_stable'})
            if row['outcome']=='denied':require(set(row)=={'index','outcome'})


def _decode(value,config):
    require(type(value) is dict and set(value)=={'schema','probe_source_sha256','collector_before','collector_after',
        'inspect_before','inspect_after','actors','diagnostics','producer_authenticated','worker_identity_verified',
        'whole_scope_complete','started_monotonic_ns','finished_monotonic_ns','compile_provenance'}
        and value['schema']==ACTOR_SCHEMA and value['probe_source_sha256']==IPC_PROBE_SOURCE_SHA256)
    require(all(value[k] is False for k in ('producer_authenticated','worker_identity_verified','whole_scope_complete')))
    require(integer(value['started_monotonic_ns']) and integer(value['finished_monotonic_ns'])
        and value['started_monotonic_ns']<=value['finished_monotonic_ns'])
    diagnostics(value['diagnostics']);count=len(config['paths'])
    provenance=value['compile_provenance']
    require(type(provenance) is dict and set(provenance)=={'implementation','python','optimize','code_sha256'}
        and provenance['implementation']=='cpython' and type(provenance['python']) is str
        and re.fullmatch(r'3\.12\.[0-9]{1,3}',provenance['python']) is not None
        and type(provenance['optimize']) is int and provenance['optimize']==0)
    require(type(provenance['code_sha256']) is dict and set(provenance['code_sha256'])=={'actor','contract','probe'}
        and all(digest(x) for x in provenance['code_sha256'].values()))
    for key in ('collector_before','collector_after'):
        if value[key] is not None:context(value[key])
    for key in ('inspect_before','inspect_after'):
        observed=value[key]
        if observed is None:continue
        require(value['collector_before'] is not None and type(observed) is dict
            and set(observed)=={'packet','output_bound_reached'} and type(observed['output_bound_reached']) is bool)
        packet(observed['packet'],'inspect',count,value['collector_before'])
    require(type(value['actors']) is list and len(value['actors'])<=len(config['actors']))
    for index,row in enumerate(value['actors']):
        require(type(row) is dict and set(row)=={'requested','context_before','context_after','connect_packet','path_anchors',
            'credential_match_observed','context_stable','diagnostics','child_reaped','child_exit_ok'}
            and canonical_json(row['requested'])==canonical_json(config['actors'][index]))
        for key in ('credential_match_observed','context_stable','child_reaped','child_exit_ok'):require(type(row[key]) is bool)
        require(not row['child_exit_ok'] or row['child_reaped'])
        diagnostics(row['diagnostics'])
        for key in ('context_before','context_after'):
            if row[key] is not None:context(row[key])
        if row['credential_match_observed']:
            for ctx in (row['context_before'],row['context_after']):
                require(ctx is not None and ctx['uids']==[row['requested']['uid']]*4
                    and ctx['gids']==[row['requested']['gid']]*4 and ctx['groups']==row['requested']['groups'] and ctx['no_new_privs']==1)
                if row['requested']['uid']!=0:require(all(ctx[k]==0 for k in ('cap_effective','cap_permitted','cap_inheritable','cap_ambient')))
        if row['context_stable']:require(row['credential_match_observed'] and row['context_before']==row['context_after'])
        if row['connect_packet'] is not None:
            require(row['context_before'] is not None)
            packet(row['connect_packet'],'connect',count,row['context_before'])
        require(type(row['path_anchors']) is list and len(row['path_anchors'])<=count)
        indices=[]
        for anchor in row['path_anchors']:
            require(type(anchor) is dict and set(anchor)=={'index','identity','directory_chain_sha256','metadata_complete',
                'kernel_connect_attempted','path_binding_stable','started_monotonic_ns','finished_monotonic_ns'}
                and integer(anchor['index'],0,count-1))
            indices.append(anchor['index'])
            require(integer(anchor['started_monotonic_ns']) and (anchor['finished_monotonic_ns'] is None
                or integer(anchor['finished_monotonic_ns'],anchor['started_monotonic_ns'])))
            require(value['started_monotonic_ns']<=anchor['started_monotonic_ns']<=value['finished_monotonic_ns']
                and (anchor['finished_monotonic_ns'] is None or anchor['finished_monotonic_ns']<=value['finished_monotonic_ns']))
            for key in ('metadata_complete','kernel_connect_attempted','path_binding_stable'):require(type(anchor[key]) is bool)
            if anchor['identity'] is not None:identity(anchor['identity'])
            require(anchor['directory_chain_sha256'] is None or digest(anchor['directory_chain_sha256']))
            if anchor['metadata_complete']:require(anchor['identity'] is not None and digest(anchor['directory_chain_sha256']))
            if anchor['kernel_connect_attempted']:require(anchor['metadata_complete'])
            if anchor['path_binding_stable']:require(anchor['kernel_connect_attempted'] and anchor['finished_monotonic_ns'] is not None)
        require(indices==sorted(set(indices)))
    require(len(canonical_json(value))<=65536)
    return deepcopy(value)


def decode(value,config):
    try:return _decode(value,config)
    except Exception:raise ValueError('KUBERNETES_IPC_PACKET_INVALID') from None


def source():
    """Embed reviewed bytes in isolated modules; never load target-supplied code."""
    directory=Path(__file__).parent
    paths={'actor':directory/'kubernetes_ipc_actor.py','contract':directory/'kubernetes_ipc_contract.py',
        'probe':directory.parent/'executors/ipc_probe.py'}
    pins={'actor':ACTOR_SOURCE_SHA256,'contract':CONTRACT_SOURCE_SHA256,'probe':IPC_PROBE_SOURCE_SHA256}
    content={key:path.read_bytes() for key,path in paths.items()}
    if any(sha256_hex(raw)!=pins[key] for key,raw in content.items()):raise ValueError('KUBERNETES_IPC_SOURCE_UNAVAILABLE')
    # No timer threads before fork. The actor supervisor bounds itself with an
    # isolated SIGALRM and sets PDEATHSIG only on its own children.
    program="import base64,types,hashlib,marshal,sys\ncode_sha={}\n"
    for key in ('contract','actor'):
        program+=f"{key}=types.ModuleType('_guard_ipc_{key}')\n{key}.__file__='guard-ipc-{key}.py'\n"
        program+=f"code=compile(base64.b64decode({base64.b64encode(content[key]).decode()!r}),'guard-ipc-{key}.py','exec',dont_inherit=True,optimize=0)\ncode_sha[{key!r}]=hashlib.sha256(marshal.dumps(code)).hexdigest()\nexec(code,{key}.__dict__)\n"
    program+=f"actor.normalize_inputs=contract.normalize_inputs\nactor.PROBE_SOURCE_B64={base64.b64encode(content['probe']).decode()!r}\nactor.COMPILE_PROVENANCE={{'implementation':sys.implementation.name,'python':'.'.join(map(str,sys.version_info[:3])),'optimize':0,'code_sha256':code_sha}}\nraise SystemExit(actor.main())\n"
    return program


def capture(transport,policy_values,*,actor_specs,connect_timeout,total_timeout):
    from .kubernetes_http import REQUEST_BINDING_PROFILE
    config=normalize_inputs(policy_values,actor_specs=actor_specs,connect_timeout=connect_timeout,total_timeout=total_timeout)
    # This is a separate explicit lab capability. Native0.32 and production
    # worker/enterprise trust are not enabled by the HTTP sampling profile.
    require(transport.profile==REQUEST_BINDING_PROFILE and transport.lab is True
        and transport.engine_name=='vllm' and transport.engine_version=='0.31.0')
    program=source()
    record={'schema':RESULT_SCHEMA,'profile':PROFILE,'started_at':utcnow().isoformat(),'finished_at':None,
        'environment_ref':deepcopy(transport.environment_ref),'target_sha256':transport.target_sha256,
        'binding':deepcopy(transport.binding),'binding_verified_before_after':False,
        'program_sha256':sha256_hex(program.encode()),'actor_source_sha256':ACTOR_SOURCE_SHA256,
        'contract_source_sha256':CONTRACT_SOURCE_SHA256,'original_probe_sha256':IPC_PROBE_SOURCE_SHA256,
        'policy_input_sha256':sha256_hex(canonical_json(policy_values)),
        'path_sha256':[sha256_hex(p.encode()) for p in config['paths']],
        'data_origin':'EXAMPLE','packet':None,'available':False,'requested_observations_complete':False,'diagnostic_codes':[],
        'cleanup_errors':[],'producer_authenticated':False,'worker_identity_verified':False,
        'whole_scope_complete':False,'security':'UNKNOWN'}
    try:
        with transport.lock:require(not transport.failed)
        pods=transport._verify(capture_snapshot=True)
        require(pods['engine']['spec'].get('shareProcessNamespace',False) is False)
        engine=transport.binding['engine'];remaining=transport.deadline-transport.clock()
        require(total_timeout+2<=remaining)  # Reserve owned-child cleanup and both post-generation reads.
        def remember(document):record['packet']=decode(document,config)
        transport._call(['--namespace='+engine['namespace'],'exec','-i',engine['pod'],
            '--container='+engine['container'],'--','python3','-c',program],
            payload=canonical_json({'policy_values':policy_values,'actor_specs':actor_specs,
                'connect_timeout':connect_timeout,'total_timeout':total_timeout}),cap=65536,budget=total_timeout+2,on_document=remember)
        transport._verify(capture_snapshot=True)
        record.update(binding_verified_before_after=True,available=True)
    except Exception:record['diagnostic_codes'].append('KUBERNETES_IPC_CAPTURE_UNAVAILABLE')
    record['cleanup_errors']=list(transport.cleanup_errors)
    p=record['packet']
    if p is not None and record['binding_verified_before_after'] and not record['cleanup_errors']:
        record['requested_observations_complete']=(not p['diagnostics'] and p['collector_before']==p['collector_after']
            and all(p[k] is not None and not p[k]['output_bound_reached'] and len(p[k]['packet']['rows'])==len(config['paths'])
                and all(row['complete'] for row in p[k]['packet']['rows']) for k in ('inspect_before','inspect_after'))
            and len(p['actors'])==len(config['actors']) and all(a['context_stable'] and a['child_reaped'] and a['child_exit_ok']
                and not a['diagnostics'] and a['connect_packet'] is not None and len(a['connect_packet']['rows'])==len(config['paths'])
                for a in p['actors']))
    record['finished_at']=utcnow().isoformat()
    with transport.lock:
        transport.snapshot_reads.append({'profile':PROFILE,'started_at':record['started_at'],'finished_at':record['finished_at'],
            'target_sha256':record['target_sha256'],'program_sha256':record['program_sha256'],
            'collected':record['available'],'packet_sha256':sha256_hex(canonical_json(p)) if p is not None else None,
            'diagnostic_codes':list(record['diagnostic_codes'])})
    return record
