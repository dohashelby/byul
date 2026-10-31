"""Owned same-cluster egress transport. A2 owns receipt/operation adjudication."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import ast
import base64
import secrets

from guard.models import canonical_json,sha256_hex,utcnow
from .bounded import strict_json
from .kubernetes_binding import validate_container_binding,verify_owned_pod
from .kubernetes_egress_contract import PROFILE,SCHEMA,ACTOR_SCHEMA,normalize,require,integer,digest,when
from .kubernetes_ipc import context as validate_context

# Reviewed literal bytes. Runtime or target reads never approve a new hash.
PINS={
 'executors/egress_probe.py':'cd5b395f08dc141074964b0804b37f5f9cd3aafbe3db4271b0128dae57d23687',
 'executors/egress_receiver.py':'f16f8544908e038ddd90d66516eafc3a52cc3bc8b673cccdf4b8e05254aae599',
 'executors/boundary_egress.py':'21b135d6c80be03c99a3df540fb035d991e21985557be7aec63a5a5a60caae79',
 'runtime/kubernetes_ipc_actor.py':'5fe44e3599199324021f9691d9d048f75ead4ef340f8f24eebd190501cfaf4ea',
}
HELPER_SHA256='9e1d71ade479e3b6ac9f51e968f47996ddda05026c7fea8a15ed081754f3539c'
CONTRACT_SHA256='09b21321d0d0af87163492118a4407ad69e3beeff6fe188bb2f6d1334fc93975'
FILE_READER_SHA256='f8de10f8a6eaaeeb21a31e177e2db617e94dc6a31bdccb8e2c47cc739d38de13'

def source():
    root=Path(__file__).parent.parent;sources={n:(root/n).read_bytes() for n in PINS}
    require(all(sha256_hex(sources[n])==v for n,v in PINS.items()))
    helper=(root/'runtime/kubernetes_egress_helper.py').read_bytes();contract=(root/'runtime/kubernetes_egress_contract.py').read_bytes()
    require(sha256_hex(helper)==HELPER_SHA256 and sha256_hex(contract)==CONTRACT_SHA256)
    from guard.executors.owned_capture import _FILE_READER
    require(sha256_hex(_FILE_READER.encode())==FILE_READER_SHA256)
    process,=[ast.literal_eval(n.value) for n in ast.parse(sources['executors/boundary_egress.py']).body
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_RECEIVER_PROCESS' for t in n.targets)]
    program=('import base64,types,math,signal,sys,time\n'
        'budget=float(sys.argv[1])\n'
        'if not math.isfinite(budget) or not 0<budget<=30:raise ValueError()\n'
        'deadline=time.monotonic()+budget\n'
        'signal.signal(signal.SIGALRM,signal.SIG_DFL)\nsignal.setitimer(signal.ITIMER_REAL,budget)\n')
    for name,raw in [('ipc',sources['runtime/kubernetes_ipc_actor.py']),('sender',sources['executors/egress_probe.py']),('helper',helper)]:
        program+=f"{name}=types.ModuleType('_guard_egress_{name}')\nexec(compile(base64.b64decode({base64.b64encode(raw).decode()!r}),'guard-egress-{name}.py','exec',dont_inherit=True,optimize=0),{name}.__dict__)\n"
    program+=f'helper.BOOT_DEADLINE=deadline\nhelper.context=ipc.context\nhelper.sender=sender\nhelper.FILE_READER={_FILE_READER!r}\nhelper.RECEIVER_PROCESS={process!r}\nraise SystemExit(helper.main())\n'
    return program,sources['executors/egress_receiver.py'].decode()

def _endpoint(v,*,listening=False):
    import ipaddress
    require(type(v) is dict and set(v)=={'ip','port'} and type(v['ip']) is str and integer(v['port'],1,65535))
    address=ipaddress.IPv4Address(v['ip']);require(str(address)==v['ip'] and (listening or not address.is_unspecified))

def _identity(v):
    require(type(v) is dict and set(v)=={'pid','start_time_ticks','uid','net_namespace_inode'}
        and integer(v['pid'],1,2**31-1) and integer(v['start_time_ticks'],1)
        and integer(v['uid'],0,2**32-1) and integer(v['net_namespace_inode'],1))

def _sender(v):
    keys={'schema','identity_before','identity_after','local','peer','socket_inode','correlation_sha256',
        'connected','marker_sent','ack_verified','sent_bytes','received_bytes','outcome','observed_at',
        'process_identity_stable','deadline_guard_verified','connected_at','marker_sent_at','connection_guard_verified'}
    require(type(v) is dict and set(v)==keys and v['schema']=='guard-owned-egress-probe/1')
    require(all(type(v[k]) is bool for k in ('connected','marker_sent','ack_verified','process_identity_stable','deadline_guard_verified','connection_guard_verified'))
        and integer(v['sent_bytes'],0,32) and integer(v['received_bytes'],0,66) and digest(v['correlation_sha256'])
        and (v['socket_inode'] is None or integer(v['socket_inode'],1)) and type(v['outcome']) is str
        and v['outcome'] in {'identity_unavailable','identity_mismatch','connect_refused','timeout','transport_error','ack_mismatch','completed','source_deadline','source_clock_unavailable','source_clock_mismatch'})
    for k in ('identity_before','identity_after'):
        if v[k] is not None:_identity(v[k])
    for k in ('local','peer'):
        if v[k] is not None:_endpoint(v[k])
    for k in ('connected_at','marker_sent_at','observed_at'):
        if v[k] is not None:when(v[k])

def _file(v):
    from guard.executors.owned_capture import validate_file_result
    # This parses the fixed helper's nested file document, independently of
    # whether its outer transport later succeeds. Outer success is gated below.
    raw,identity=validate_file_result(SimpleNamespace(returncode=0,stdout=canonical_json(v).decode(),
        capture={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}))
    require(len(raw)<=16384)
    lines=raw.splitlines();require(1<=len(lines)<=9 and all(0<len(row)<=2048 for row in lines))
    rows=[strict_json(row,limit=2048) for row in lines]
    keys={'schema','kind','identity','local','peer','socket_inode','observed_at','correlation_sha256','received_bytes','ack_sent'}
    for i,row in enumerate(rows):
        require(type(row) is dict and set(row)==keys and row['schema']=='guard-owned-egress-receiver/1'
            and row['kind']==('startup' if i==0 else 'receipt') and integer(row['socket_inode'],1)
            and integer(row['received_bytes'],0,32) and type(row['ack_sent']) is bool)
        _identity(row['identity']);_endpoint(row['local'],listening=i==0);when(row['observed_at'])
        if i==0:require(row['peer'] is None and row['correlation_sha256'] is None and row['received_bytes']==0 and not row['ack_sent'])
        else:_endpoint(row['peer']);require(digest(row['correlation_sha256']))
    return {'rows':rows,'file_identity':identity,'capture_sha256':sha256_hex(raw),'bytes':len(raw)}

def decode(v,mode):
    require(type(v) is dict and set(v)=={'schema','mode','context_before','context_after','started_monotonic_ns',
        'finished_monotonic_ns','sender','file','receiver_process','diagnostic_codes'} and v['schema']==ACTOR_SCHEMA and v['mode']==mode
        and integer(v['started_monotonic_ns']) and integer(v['finished_monotonic_ns']) and v['started_monotonic_ns']<=v['finished_monotonic_ns'])
    codes=v['diagnostic_codes'];require(type(codes) is list and len(codes)<=3 and len(codes)==len(set(codes))
        and all(c in {'EGRESS_HELPER_DEADLINE','EGRESS_HELPER_UNAVAILABLE','EGRESS_HELPER_CONTEXT_UNAVAILABLE'} for c in codes))
    for k in ('context_before','context_after'):
        if v[k] is not None:validate_context(v[k])
    out=deepcopy(v)
    if mode=='sender':
        require(v['file'] is v['receiver_process'] is None)
        if v['sender'] is not None:_sender(v['sender'])
    else:
        require(v['sender'] is None)
        if v['file'] is not None:out['file']=_file(v['file'])
        p=v['receiver_process']
        if p is not None:
            require(type(p) is dict and p.get('schema')=='guard-egress-receiver-process/1' and type(p.get('complete')) is bool)
            require(set(p)==({'schema','complete','identity','command_sha256','socket_inodes'} if p['complete'] else {'schema','complete'}))
            if p['complete']:
                _identity(p['identity']);require(digest(p['command_sha256']) and type(p['socket_inodes']) is list and len(p['socket_inodes'])<=128
                    and all(integer(n,1) for n in p['socket_inodes']) and p['socket_inodes']==sorted(set(p['socket_inodes'])))
    require(len(canonical_json(out))<=65536)
    return out

def _receiver(t,target,p):
    require(type(target) is dict and set(target)=={'schema','environment_ref','source_target_sha256','run_id','lab','context','receiver','receiver_pid'}
        and target['schema']=='guard-kubernetes-egress-receiver-target/1' and target['lab'] is True
        and target['environment_ref']==t.environment_ref and target['source_target_sha256']==t.target_sha256
        and target['context']==t.binding['context'] and target['run_id']==t.binding['run_id'] and integer(target['receiver_pid'],1,2**31-1))
    r=target['receiver'];validate_container_binding(r)
    require(r['pod_uid'] not in {t.binding[k]['pod_uid'] for k in ('engine','probe')}
        and r['container_id']==p['forbidden_receiver_container_id'] and r['pod_ip']==p['forbidden_receiver_ip']
        and sha256_hex(canonical_json(target))==p['receiver_target_sha256'])
    return deepcopy(r)

def capture(t,policy_values,*,receiver_target,actor_spec,connect_timeout,total_timeout):
    from .kubernetes_http import REQUEST_BINDING_PROFILE
    cfg=normalize(policy_values,actor_spec=actor_spec,connect_timeout=connect_timeout,total_timeout=total_timeout)
    p=cfg['policy_values'];require(t.profile==REQUEST_BINDING_PROFILE and t.lab is True and t.engine_name=='vllm' and t.engine_version=='0.31.0')
    require(p['source_target_sha256']==t.target_sha256 and p['subject_container_id']==t.binding['engine']['container_id'])
    receiver=_receiver(t,receiver_target,p);program,receiver_source=source();nonce=secrets.token_hex(16)
    deadline=min(t.deadline,t.clock()+total_timeout)
    def remaining():
        seconds=deadline-t.clock();require(seconds>0);return seconds
    out={'schema':SCHEMA,'profile':PROFILE,'environment_ref':deepcopy(t.environment_ref),'target_sha256':t.target_sha256,
        'receiver_target_sha256':sha256_hex(canonical_json(receiver_target)),'policy_input_sha256':sha256_hex(canonical_json(p)),
        'actor_requested':deepcopy(actor_spec),'binding':deepcopy(t.binding),'model_mapping':deepcopy(t.model_mapping),
        'receiver_binding':receiver,'program_sha256':sha256_hex(program.encode()),
        'source_pins':deepcopy(PINS),'helper_source_sha256':HELPER_SHA256,'contract_source_sha256':CONTRACT_SHA256,'file_reader_sha256':FILE_READER_SHA256,
        'correlation_sha256':sha256_hex(nonce.encode()),'started_at':utcnow().isoformat(),'finished_at':None,
        'receiver_before':None,'sender':None,'receiver_after':None,'binding_verified_before_after':False,'requested_observations_complete':False,
        'diagnostic_codes':[],'cleanup_errors':[],'data_origin':'EXAMPLE','producer_authenticated':False,'worker_identity_verified':False,
        'node_runtime_image_verified':False,'whole_scope_complete':False,'security':'UNKNOWN','normal_control':{'attempted':False,'succeeded':None}}
    def verify():
        # Reuse the common exact-generation validator. Every API read receives
        # this operation's remaining budget, including the source and probe.
        for role in (t.binding['engine'],t.binding['probe'],receiver):
            ns=t._call(['get','namespace',role['namespace'],'-o','json'],cap=262144,budget=remaining())
            require(ns.get('kind')=='Namespace' and ns['metadata'].get('name')==role['namespace'])
            pod=t._call(['--namespace='+role['namespace'],'get','pod',role['pod'],'-o','json'],cap=262144,budget=remaining())
            require(pod.get('kind')=='Pod' and pod['spec'].get('shareProcessNamespace',False) is False)
            verify_owned_pod(pod,ns['metadata'],role,t.binding['run_id'])
        c,=[c for c in pod['spec']['containers'] if c['name']==receiver['container']]
        require(c.get('command')==['python3','-u','-c',receiver_source] and c.get('args')==[str(p['forbidden_receiver_port'])])
    def collect(key,role,mode):
        budget=min(5 if mode=='receiver' else total_timeout,remaining())
        if mode=='sender':require(when(p['validity_start'])<=datetime.now(timezone.utc)<when(p['validity_end']))
        request={'mode':mode,'budget':budget,'receiver_pid':receiver_target['receiver_pid']} if mode=='receiver' else {
            'mode':mode,'budget':budget,'policy_values':p,'actor_spec':actor_spec,'connect_timeout':connect_timeout,'correlation':nonce}
        def remember(v):out[key]=decode(v,mode)
        t._call(['--namespace='+role['namespace'],'exec','-i',role['pod'],'--container='+role['container'],'--','python3','-I','-S','-B','-c',program,str(budget)],
            payload=canonical_json(request),cap=65536,budget=budget,on_document=remember)
    try:
        require(not t.failed and total_timeout<t.deadline-t.clock())
        require(when(p['validity_start'])<=datetime.now(timezone.utc)<when(p['validity_end']))
        verify();collect('receiver_before',receiver,'receiver')
        prior=out['receiver_before'];require(prior is not None and not prior['diagnostic_codes'] and prior['file'] is not None and prior['receiver_process'] is not None)
        startup=prior['file']['rows'][0];process=prior['receiver_process'];command=['python3','-u','-c',receiver_source,str(p['forbidden_receiver_port'])]
        require(process['complete'] and process['identity']==startup['identity'] and startup['identity']['pid']==receiver_target['receiver_pid']
            and process['command_sha256']==sha256_hex(canonical_json(command)) and startup['socket_inode'] in process['socket_inodes']
            and startup['local']['port']==p['forbidden_receiver_port'] and startup['local']['ip'] in {'0.0.0.0',p['forbidden_receiver_ip']})
        c=prior['context_before']
        require(c is not None and c==prior['context_after'] and c['pid']!=receiver_target['receiver_pid']
            and c['namespaces']['net']=='net:['+str(process['identity']['net_namespace_inode'])+']')
        verify()
        for key,role,mode in [('sender',t.binding['engine'],'sender'),('receiver_after',receiver,'receiver')]:
            try:collect(key,role,mode)
            except Exception:out['diagnostic_codes'].append('KUBERNETES_EGRESS_'+key.upper()+'_INCOMPLETE')
        verify()
        out['binding_verified_before_after']=True
        out['requested_observations_complete']=not out['diagnostic_codes'] and all(out[k] is not None and not out[k]['diagnostic_codes']
            and out[k]['context_before'] is not None and out[k]['context_before']==out[k]['context_after'] for k in ('receiver_before','sender','receiver_after'))
        out['requested_observations_complete'] &= out['sender'] is not None and out['sender']['sender'] is not None and all(out[k] is not None and out[k]['file'] is not None
            and out[k]['receiver_process'] is not None and out[k]['receiver_process']['complete'] for k in ('receiver_before','receiver_after'))
        c=out['sender']['context_before'] if out['sender'] is not None else None
        out['requested_observations_complete'] &= (c is not None and c['uids']==[actor_spec['uid']]*4
            and c['gids']==[actor_spec['gid']]*4 and c['groups']==actor_spec['groups']
            and c['namespaces']['net']=='net:['+str(p['subject_net_namespace_inode'])+']')
    except Exception:out['diagnostic_codes'].append('KUBERNETES_EGRESS_CAPTURE_UNAVAILABLE')
    out['cleanup_errors']=list(t.cleanup_errors)
    if out['cleanup_errors']:out['requested_observations_complete']=False
    out['finished_at']=utcnow().isoformat()
    with t.lock:t.snapshot_reads.append({'profile':PROFILE,'started_at':out['started_at'],'finished_at':out['finished_at'],
        'target_sha256':t.target_sha256,'program_sha256':out['program_sha256'],'collected':out['requested_observations_complete'],'diagnostic_codes':list(out['diagnostic_codes'])})
    return out
