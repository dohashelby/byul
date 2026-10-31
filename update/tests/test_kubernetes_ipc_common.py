"""Owned transport contract regressions; all injected observations are EXAMPLE."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from guard.models import canonical_json
from guard.runtime import kubernetes_http as kube
from guard.runtime import kubernetes_ipc as ipc
from guard.runtime.kubernetes_ipc_contract import normalize_inputs
from tests.test_model_preparation_integration import full_inputs, FullWorld

PRIVATE='EXAMPLE_IPC_PRIVATE_d4e7'
VALUES={'protected_unix_paths':['/run/guard-owned.sock'],'allowed_ipc_uids':['1000']}
ACTORS=[{'uid':1000,'gid':1000,'groups':[44,1000]},{'uid':2000,'gid':2000,'groups':[]}]


def config():return normalize_inputs(deepcopy(VALUES),actor_specs=deepcopy(ACTORS),connect_timeout=.1,total_timeout=3)


def ctx(uid=1000,gid=1000,groups=None,pid=91):
    return {'pid':pid,'start_ticks':123,'uids':[uid]*4,'gids':[gid]*4,'groups':[44,1000] if groups is None else groups,
        'cap_effective':0,'cap_permitted':0,'cap_inheritable':0,'cap_ambient':0,'no_new_privs':1,
        'namespaces':{k:k+':[11]' for k in ('mnt','net','pid','user','time')},
        'boot_sha256':'a'*64,'time_offsets_sha256':'b'*64}


def identity():return {'device':1,'inode':20,'uid':1000,'gid':1000,'mode':0o660,'ctime_ns':1234}


def original_packet(context,mode):
    row=({'index':0,'complete':True,'identity':identity(),'socket_inode':'41',
        'owners':[{'pid':8,'start':'12','uid':1000}]} if mode=='inspect' else
        {'index':0,'outcome':'connected','identity':identity(),'path_stable':True,'peer':{'pid':8,'uid':1000,'gid':1000}})
    result={'schema':'guard-unix-ipc-probe/1','mode':mode,'uid':context['uids'][1],
        'namespaces':{k:context['namespaces'][k] for k in ('mnt','net','pid','user')},'rows':[row]}
    if mode=='connect':result['groups']=list(context['groups'])
    return result


def fixture():
    collector=ctx();actors=[]
    for a in ACTORS:
        c=ctx(a['uid'],a['gid'],a['groups'],pid=90+a['uid'])
        actors.append({'requested':deepcopy(a),'context_before':c,'context_after':deepcopy(c),
            'connect_packet':original_packet(c,'connect'),'path_anchors':[{'index':0,'identity':identity(),
                'directory_chain_sha256':'c'*64,'metadata_complete':True,'kernel_connect_attempted':True,
                'path_binding_stable':True,'started_monotonic_ns':20,'finished_monotonic_ns':30}],
            'credential_match_observed':True,'context_stable':True,'diagnostics':[],'child_reaped':True,'child_exit_ok':True})
    inspection={'packet':original_packet(collector,'inspect'),'output_bound_reached':False}
    return {'schema':ipc.ACTOR_SCHEMA,'probe_source_sha256':ipc.IPC_PROBE_SOURCE_SHA256,'collector_before':collector,
        'compile_provenance':{'implementation':'cpython','python':'3.12.3','optimize':0,
            'code_sha256':{k:'d'*64 for k in ('actor','contract','probe')}},
        'collector_after':deepcopy(collector),'inspect_before':inspection,'inspect_after':deepcopy(inspection),
        'actors':actors,'diagnostics':[],'producer_authenticated':False,'worker_identity_verified':False,
        'whole_scope_complete':False,'started_monotonic_ns':10,'finished_monotonic_ns':40}


class World(FullWorld):
    def __init__(self,target,mode=None):super().__init__(target);self.mode=mode;self.ipc_calls=0
    def runner(self,argv,**kwargs):
        if 'exec' not in argv:return super().runner(argv,**kwargs)
        self.calls.append(argv);self.ipc_calls+=1;self.after_exec=True
        assert argv[-1]==ipc.source() and argv[-2]=='-c' and 'python3' in argv
        assert '--container=engine' in argv and '-i' in argv and PRIVATE not in ' '.join(argv)
        assert json.loads(kwargs['input_bytes'])=={'policy_values':VALUES,'actor_specs':ACTORS,'connect_timeout':.1,'total_timeout':3}
        p=fixture()
        if self.mode=='private':p['argv']=PRIVATE
        elif self.mode=='worker':p['worker_identity_verified']=True
        elif self.mode=='partial':
            p['actors'][1].update(connect_packet=None,context_after=None,credential_match_observed=False,context_stable=False,
                path_anchors=[],diagnostics=['IPC_ACTOR_UNAVAILABLE'])
            p['inspect_after']['packet']['rows']=[];p['diagnostics']=['IPC_SNAPSHOT_DEADLINE']
        result={'returncode':0,'stdout_bytes':canonical_json(p),'stderr_bytes':PRIVATE.encode(),'capture':{'complete':True}}
        if self.mode in {'cleanup','cleanup-incomplete'}:
            result['capture'].update(pipes_closed=False,read_errors=['child_reap'])
            if self.mode=='cleanup-incomplete':result['capture']['complete']=False
        return result


def transport(mode=None):
    env,target=full_inputs();world=World(target,mode)
    return kube.KubernetesHTTP(target,env,duration=30,profile=kube.REQUEST_BINDING_PROFILE,runner=world.runner),world


def capture(t):return t.ipc_snapshot(VALUES,actor_specs=ACTORS,connect_timeout=.1,total_timeout=3)


def test_explicit_policy_and_actual_groups_survive_without_legacy_rewriting():
    before=deepcopy((VALUES,ACTORS));cfg=config();p=fixture();result=ipc.decode(p,cfg)
    assert result==p and result is not p and result['actors'][0]['connect_packet']['groups']==[44,1000]
    assert result['actors'][1]['requested']['uid']==2000 and VALUES['allowed_ipc_uids']==['1000']
    assert (VALUES,ACTORS)==before and not result['whole_scope_complete']


@pytest.mark.parametrize('damage',['uid-int','uid-leading-zero','uid-bool','path-relative','path-dot','path-symlink-spelling',
    'path-control','path-duplicate','no-denied','no-allowed','actor-bool','actor-extra','groups-duplicate','groups-bool',
    'groups-bound','connect-nan','connect-bool','connect-bound','total-bound','policy-extra'])
def test_invalid_closed_inputs_fail_before_any_execution(damage):
    values=deepcopy(VALUES);actors=deepcopy(ACTORS);connect=.1;total=3
    if damage=='uid-int':values['allowed_ipc_uids']=[1000]
    elif damage=='uid-leading-zero':values['allowed_ipc_uids']=['01000']
    elif damage=='uid-bool':values['allowed_ipc_uids']=[True]
    elif damage=='path-relative':values['protected_unix_paths']=['relative']
    elif damage=='path-dot':values['protected_unix_paths']=['/run/../guard.sock']
    elif damage=='path-symlink-spelling':values['protected_unix_paths']=['//run/guard.sock']
    elif damage=='path-control':values['protected_unix_paths']=['/run/guard\n.sock']
    elif damage=='path-duplicate':values['protected_unix_paths']*=2
    elif damage=='no-denied':values['allowed_ipc_uids']=['1000','2000']
    elif damage=='no-allowed':values['allowed_ipc_uids']=['3000']
    elif damage=='actor-bool':actors[0]['uid']=True
    elif damage=='actor-extra':actors[0]['argv']=PRIVATE
    elif damage=='groups-duplicate':actors[0]['groups']=[44,44]
    elif damage=='groups-bool':actors[0]['groups']=[True]
    elif damage=='groups-bound':actors[0]['groups']=list(range(17))
    elif damage=='connect-nan':connect=float('nan')
    elif damage=='connect-bool':connect=True
    elif damage=='connect-bound':connect=2.1
    elif damage=='total-bound':total=301
    elif damage=='policy-extra':values['command']=PRIVATE
    t,w=transport()
    with pytest.raises(ValueError,match='KUBERNETES_IPC_INPUT_INVALID'):
        t.ipc_snapshot(values,actor_specs=actors,connect_timeout=connect,total_timeout=total)
    assert not w.calls and w.ipc_calls==0


@pytest.mark.parametrize('damage',['extra','worker','auth','scope','source','bool-pid','bool-uid','requested-bool','requested-extra',
    'groups-rewritten','groups-bool','namespace','capability','context-claim','child-claim','peer-extra','metadata-extra',
    'owner-duplicate','anchor-extra','anchor-clock','anchor-claim','row-index','unknown-raw','denied-peer','diagnostic','time'])
def test_untrusted_packet_cannot_inject_raw_fields_or_promote_subject(damage):
    p=fixture();a=p['actors'][0];row=a['connect_packet']['rows'][0]
    if damage=='extra':p['environment']=PRIVATE
    elif damage=='worker':p['worker_identity_verified']=True
    elif damage=='auth':p['producer_authenticated']=True
    elif damage=='scope':p['whole_scope_complete']=True
    elif damage=='source':p['probe_source_sha256']='0'*64
    elif damage=='bool-pid':a['context_before']['pid']=True
    elif damage=='bool-uid':a['connect_packet']['uid']=True
    elif damage=='requested-bool':a['requested']['gid']=True
    elif damage=='requested-extra':a['requested']['raw']=PRIVATE
    elif damage=='groups-rewritten':a['connect_packet']['groups']=[1000]
    elif damage=='groups-bool':a['connect_packet']['groups']=[True,1000]
    elif damage=='namespace':a['connect_packet']['namespaces']['pid']='net:[11]'
    elif damage=='capability':a['context_after']['cap_effective']=1
    elif damage=='context-claim':a['context_after']['pid']=999
    elif damage=='child-claim':a['child_reaped']=False
    elif damage=='peer-extra':row['peer']['argv']=PRIVATE
    elif damage=='metadata-extra':row['identity']['path']=PRIVATE
    elif damage=='owner-duplicate':p['inspect_before']['packet']['rows'][0]['owners']*=2
    elif damage=='anchor-extra':a['path_anchors'][0]['path']=PRIVATE
    elif damage=='anchor-clock':a['path_anchors'][0]['finished_monotonic_ns']=41
    elif damage=='anchor-claim':a['path_anchors'][0]['metadata_complete']=False
    elif damage=='row-index':row['index']=1
    elif damage=='unknown-raw':row.update(outcome='unknown',exception=PRIVATE)
    elif damage=='denied-peer':row['outcome']='denied'
    elif damage=='diagnostic':p['diagnostics']=[PRIVATE]
    elif damage=='time':p['finished_monotonic_ns']=9
    with pytest.raises(ValueError,match='^KUBERNETES_IPC_PACKET_INVALID$'):ipc.decode(p,config())


def test_public_transport_reuses_both_generations_and_never_claims_whole_check():
    t,w=transport();r=capture(t)
    assert r['available'] and r['binding_verified_before_after'] and w.ipc_calls==1
    assert r['environment_ref']==t.environment_ref and r['binding']==t.binding
    assert r['policy_input_sha256']==hashlib.sha256(canonical_json(VALUES)).hexdigest()
    assert r['path_sha256']==[hashlib.sha256(VALUES['protected_unix_paths'][0].encode()).hexdigest()]
    assert r['data_origin']=='EXAMPLE' and r['security']=='UNKNOWN'
    assert r['requested_observations_complete']
    assert all(r[k] is False for k in ('producer_authenticated','worker_identity_verified','whole_scope_complete'))
    assert PRIVATE not in json.dumps(r) and VALUES['protected_unix_paths'][0] not in json.dumps(r)
    assert not any(any(op in argv for op in ('create','patch','delete','apply','kill')) for argv in w.calls)


@pytest.mark.parametrize('mode',['private','worker'])
def test_invalid_output_is_quarantined_without_raw_exception(mode):
    t,w=transport(mode);r=capture(t)
    assert not r['available'] and r['packet'] is None and PRIVATE not in json.dumps(r)
    assert r['diagnostic_codes']==['KUBERNETES_IPC_CAPTURE_UNAVAILABLE']


def test_later_partial_collection_keeps_earlier_connect_and_metadata():
    t,w=transport('partial');r=capture(t)
    assert r['available'] and r['binding_verified_before_after']
    assert r['packet']['actors'][0]['connect_packet']['rows'][0]['outcome']=='connected'
    assert r['packet']['inspect_before']['packet']['rows'][0]['complete']
    assert r['packet']['inspect_after']['packet']['rows']==[] and r['security']=='UNKNOWN'
    assert not r['requested_observations_complete']


@pytest.mark.parametrize('role',['engine','probe','namespaces'])
@pytest.mark.parametrize('damage',['uid','restart','container','image','start'])
def test_later_generation_gap_retains_packet_but_withholds_binding(role,damage):
    t,w=transport()
    def mutate(which,value,after):
        if which!=role or not after:return
        if role=='namespaces':value['items'][0]['metadata']['uid']='replacement'
        elif damage=='uid':value['metadata']['uid']='replacement'
        else:
            state=value['status']['containerStatuses'][0]
            if damage=='restart':state['restartCount']=1
            elif damage=='container':state['containerID']='containerd://'+'0'*64
            elif damage=='image':state['imageID']='fixture@sha256:'+'0'*64
            else:state['state']['running']['startedAt']='2026-01-01T00:00:00Z'
    w.mutate=mutate;r=capture(t)
    assert not r['available'] and not r['binding_verified_before_after'] and r['packet'] is not None
    assert r['packet']['actors'][0]['connect_packet']['rows'][0]['outcome']=='connected'
    assert r['security']=='UNKNOWN' and not r['worker_identity_verified']


def test_parent_cleanup_is_distinct_from_packet_availability():
    t,w=transport('cleanup');r=capture(t)
    assert r['available'] and r['packet'] is not None
    assert r['cleanup_errors']==['KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED'] and r['security']=='UNKNOWN'


def test_incomplete_parent_cleanup_retains_valid_document_without_relaxing_success_gate():
    t,w=transport('cleanup-incomplete');r=capture(t)
    assert not r['available'] and not r['binding_verified_before_after'] and not r['requested_observations_complete']
    assert r['packet']['actors'][0]['connect_packet']['rows'][0]['outcome']=='connected'
    assert r['cleanup_errors']==['KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED']
    assert r['diagnostic_codes']==['KUBERNETES_IPC_CAPTURE_UNAVAILABLE']
    assert t.report()['snapshot_reads'][-1]['packet_sha256']==hashlib.sha256(canonical_json(r['packet'])).hexdigest()


@pytest.mark.parametrize('damage',['profile','lab','version','engine','budget','shared-pid'])
def test_unsupported_source_privilege_or_budget_has_no_actor_exec(damage):
    t,w=transport()
    if damage=='profile':t.profile=kube.VALIDATION_ERROR_PROFILE
    elif damage=='lab':t.lab=False
    elif damage=='version':t.engine_version='0.32.0'
    elif damage=='engine':t.engine_name='other'
    elif damage=='budget':t.deadline=t.clock()+4
    else:
        def mutate(which,value,after):
            if which=='engine':value['spec']['shareProcessNamespace']=True
        w.mutate=mutate
    if damage in {'profile','lab','version','engine'}:
        with pytest.raises(ValueError):capture(t)
    else:assert not capture(t)['available']
    assert w.ipc_calls==0


def test_builder_checks_reviewed_original_bytes_and_uses_no_prefork_timer_threads(monkeypatch):
    program=ipc.source();compile(program,'reviewed-ipc-program','exec')
    assert 'threading' not in program and 'setuid' not in program and len(program.encode())<100000
    original=Path.read_bytes
    def changed(path):
        raw=original(path)
        return raw+b'\n# unreviewed\n' if path.name=='ipc_probe.py' else raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    with pytest.raises(ValueError,match='KUBERNETES_IPC_SOURCE_UNAVAILABLE'):ipc.source()
