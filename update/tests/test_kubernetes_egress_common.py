"""Common public input only. No A2 consumer substitution or real network I/O."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import base64
import io
import json
import time
from types import SimpleNamespace
import pytest

from guard.models import canonical_json,sha256_hex
from guard.runtime import kubernetes_egress as e,kubernetes_egress_helper as helper
from guard.runtime.kubernetes_http import KubernetesHTTP,REQUEST_BINDING_PROFILE
from tests.test_model_preparation_integration import full_inputs
from tests.test_kubernetes_ipc_common import fixture

PRIVATE='EXAMPLE_EGRESS_PRIVATE_RAW'
NONCE='e'*32
ACTOR={'uid':2000,'gid':2000,'groups':[55,2000]}

def inputs():
    env,target=full_inputs();r=deepcopy(target['kubernetes']['probe'])
    r.update(pod='example-owned-receiver',pod_uid='12345678-1234-1234-1234-123456789012',container_id='containerd://'+'c'*64,pod_ip='10.42.0.30')
    receiver={'schema':'guard-kubernetes-egress-receiver-target/1','environment_ref':env.ref().model_dump(mode='json'),
        'source_target_sha256':sha256_hex(canonical_json(target)),'run_id':target['kubernetes']['run_id'],'lab':True,
        'context':target['kubernetes']['context'],'receiver':r,'receiver_pid':123}
    now=datetime.now(timezone.utc)
    p={'subject_scope':'all_processes_in_container_uid_netns','subject_container_id':target['kubernetes']['engine']['container_id'],
        'subject_uid':2000,'subject_net_namespace_inode':20,'forbidden_receiver_container_id':r['container_id'],
        'forbidden_receiver_ip':r['pod_ip'],'forbidden_receiver_port':28000,'forbidden_protocol':'tcp','forbidden_operation':'tcp_connect',
        'validity_start':(now-timedelta(minutes=1)).isoformat(),'validity_end':(now+timedelta(minutes=5)).isoformat(),
        'source_target_sha256':receiver['source_target_sha256'],'receiver_target_sha256':sha256_hex(canonical_json(receiver))}
    return env,target,receiver,p

def context():
    c=deepcopy(fixture()['collector_before']);c.update(pid=999,start_ticks=777,uids=[2000]*4,gids=[2000]*4,groups=[55,2000])
    c['namespaces']['net']='net:[20]';return c

def packet(mode,*,after=False,broken=None):
    c=context();now=datetime.now(timezone.utc).isoformat()
    if mode=='receiver':c['namespaces']['net']='net:[40]'
    identity={'pid':123,'start_time_ticks':30,'uid':2000,'net_namespace_inode':40}
    row={'schema':'guard-owned-egress-receiver/1','kind':'startup','identity':identity,'local':{'ip':'0.0.0.0','port':28000},
        'peer':None,'socket_inode':88,'observed_at':now,'correlation_sha256':None,'received_bytes':0,'ack_sent':False}
    v={'schema':e.ACTOR_SCHEMA,'mode':mode,'context_before':c,'context_after':deepcopy(c),'started_monotonic_ns':10,
        'finished_monotonic_ns':20,'sender':None,'file':None,'receiver_process':None,'diagnostic_codes':[]}
    if mode=='sender':
        actor={'pid':999,'start_time_ticks':777,'uid':2000,'net_namespace_inode':20}
        v['sender']={'schema':'guard-owned-egress-probe/1','identity_before':actor,'identity_after':deepcopy(actor),
            'local':{'ip':'10.42.0.10','port':45678},'peer':{'ip':'10.42.0.30','port':28000},'socket_inode':99,
            'correlation_sha256':sha256_hex(NONCE.encode()),'connected':True,'marker_sent':True,'ack_verified':False,
            'sent_bytes':32,'received_bytes':0,'outcome':'timeout','observed_at':now,'process_identity_stable':True,
            'deadline_guard_verified':False,'connected_at':now,'marker_sent_at':now,'connection_guard_verified':True}
    else:
        rows=[row]
        if after:rows.append({**deepcopy(row),'kind':'receipt','local':{'ip':'10.42.0.30','port':28000},
            'peer':{'ip':'10.42.0.10','port':45678},'socket_inode':89,'correlation_sha256':sha256_hex(NONCE.encode()),'received_bytes':32})
        raw=b''.join(canonical_json(r)+b'\n' for r in rows)
        f={'device':1,'inode':2,'size':len(raw),'mtime_ns':3,'ctime_ns':4}
        v['file']={'schema':'guard-container-file/2','source_complete':True,'file_identity_stable':True,'truncated':False,
            'nofollow_components':True,'regular_file':True,'raw_base64':base64.b64encode(raw).decode(),
            'raw_retained_bytes':len(raw),'raw_retained_sha256':sha256_hex(raw),'file_before':f,'file_after':deepcopy(f),'path_after':deepcopy(f)}
        _,receiver_source=e.source();command=['python3','-u','-c',receiver_source,'28000']
        v['receiver_process']={'schema':'guard-egress-receiver-process/1','complete':True,'identity':identity,
            'command_sha256':sha256_hex(canonical_json(command)),'socket_inodes':[88,89]}
    if broken=='raw':v['argv']=PRIVATE
    if broken=='groups':v['context_after']['groups']=[2000]
    return v

class World:
    def __init__(self,target,receiver,mode):self.target=target;self.receiver=receiver;self.mode=mode;self.execs=[]
    def runner(self,argv,**kw):
        roles=[*map(self.target['kubernetes'].__getitem__,('engine','probe')),self.receiver['receiver']]
        capture={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
        if 'exec' in argv:
            request=json.loads(kw['input_bytes']);mode=request['mode'];after=mode=='receiver' and bool(self.execs)
            # _call reduces timeout by its own elapsed time after this payload.
            assert request['budget']<=10 and kw['timeout']<=request['budget']
            assert argv[-6:-2]==['-I','-S','-B','-c'] and float(argv[-1])==request['budget']
            assert sha256_hex(argv[-2].encode())==sha256_hex(e.source()[0].encode())
            assert kw['max_stdout_bytes']==65536
            self.execs.append(mode);v=packet(mode,after=after,broken=self.mode if mode=='sender' else None)
            if self.mode=='later-transport' and after:capture['complete']=False
            if self.mode=='cleanup' and after:capture.update(pipes_closed=False,read_errors=['child_reap'])
            if self.mode=='missing-sender' and mode=='sender':v['sender']=None
            if self.mode=='receiver-command' and not after:v['receiver_process']['command_sha256']='0'*64
        elif 'namespace' in argv:
            name=argv[argv.index('namespace')+1];r=next(r for r in roles if r['namespace']==name)
            v={'kind':'Namespace','metadata':{'name':name,'uid':r['namespace_uid']}}
        else:
            r=next(r for r in roles if r['pod'] in argv);c={'name':r['container'],'image':r['image']}
            if r==roles[-1]:c.update(command=['python3','-u','-c',e.source()[1]],args=['28000'])
            v={'kind':'Pod','metadata':{'name':r['pod'],'namespace':r['namespace'],'uid':r['pod_uid'],'labels':{'guard.run':self.target['kubernetes']['run_id']}},
                'spec':{'nodeName':r['node'],'containers':[c]},'status':{'phase':'Running','podIP':r['pod_ip'],'conditions':[{'type':'Ready','status':'True'}],
                'containerStatuses':[{'name':r['container'],'containerID':r['container_id'],'imageID':r['image_id'],'restartCount':r['restart_count'],
                'ready':True,'state':{'running':{'startedAt':r['started_at']}}}]}}
            if self.mode=='restart' and len(self.execs)==3:v['status']['containerStatuses'][0]['restartCount']+=1
        return {'returncode':0,'stdout_bytes':canonical_json(v),'stderr_bytes':PRIVATE.encode(),'capture':capture}

@pytest.mark.parametrize('mode',['normal','later-transport','cleanup','raw','groups','restart','missing-sender','receiver-command'])
def test_public_closed_input_preserves_prior_facts_without_product_promotion(monkeypatch,mode):
    env,target,receiver,p=inputs();world=World(target,receiver,mode)
    monkeypatch.setattr(e.secrets,'token_hex',lambda n:NONCE)
    t=KubernetesHTTP(target,env,duration=30,runner=world.runner,profile=REQUEST_BINDING_PROFILE)
    result=t.egress_snapshot(p,receiver_target=receiver,actor_spec=ACTOR,connect_timeout=.1,total_timeout=10)
    assert result['data_origin']=='EXAMPLE' and result['security']=='UNKNOWN' and not result['whole_scope_complete']
    assert not result['producer_authenticated'] and not result['worker_identity_verified'] and not result['node_runtime_image_verified']
    assert result['normal_control']=={'attempted':False,'succeeded':None}
    assert result['requested_observations_complete'] is (mode=='normal')
    if mode in {'later-transport','cleanup','groups','restart','missing-sender'}:assert result['sender'] is not None
    if mode in {'later-transport','cleanup'}:
        assert result['binding_verified_before_after'] and result['sender']['sender']['connected']
        assert result['receiver_after']['file']['rows'][-1]['received_bytes']==32
    assert PRIVATE not in json.dumps(result) and NONCE not in json.dumps(result)
    assert 'raw_base64' not in json.dumps(result)
    assert world.execs==(['receiver'] if mode=='receiver-command' else ['receiver','sender','receiver'])

@pytest.mark.parametrize('bad',['docker-id','target','destination','scope','groups','uid','timeout','lab','version','expired','receiver-context'])
def test_bad_input_has_no_network_or_helper_execution(bad):
    env,target,receiver,p=inputs();actor=deepcopy(ACTOR);kwargs={'connect_timeout':.1,'total_timeout':10}
    if bad=='docker-id':p['subject_container_id']='a'*64
    if bad=='target':p['source_target_sha256']='0'*64
    if bad=='destination':p['forbidden_receiver_ip']='127.0.0.1'
    if bad=='scope':p['subject_scope']='worker-only'
    if bad=='groups':actor['groups']=[2000,55]
    if bad=='uid':actor['uid']=True
    if bad=='timeout':kwargs['total_timeout']=31
    if bad=='lab':target['lab']=False
    if bad=='version':
        # Keep every other exact Ref coherent so the selected capability's
        # native0.32 rejection, rather than an earlier mapping mismatch, is tested.
        env=env.model_copy(deep=True);env.engine.version='0.32.0';env=env.seal()
        target['environment_ref']=env.ref().model_dump(mode='json')
        target['model_mapping']['environment_ref']=env.ref().model_dump(mode='json')
        receiver['environment_ref']=env.ref().model_dump(mode='json')
        receiver['source_target_sha256']=sha256_hex(canonical_json(target))
        p['source_target_sha256']=receiver['source_target_sha256'];p['receiver_target_sha256']=sha256_hex(canonical_json(receiver))
    if bad=='expired':p['validity_end']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    if bad=='receiver-context':receiver['context']='other'
    calls=[]
    def forbidden(*a,**kw):calls.append(True);raise AssertionError('unexpected external operation')
    t=KubernetesHTTP(target,env,duration=30,runner=forbidden,profile=REQUEST_BINDING_PROFILE)
    try:result=t.egress_snapshot(p,receiver_target=receiver,actor_spec=actor,**kwargs)
    except ValueError:
        assert not calls
        return
    assert result['sender'] is None and not result['requested_observations_complete']
    assert not calls

def test_source_literals_and_no_implicit_formal_registration():
    program,_=e.source();compile(program,'EXAMPLE-egress','exec')
    assert len(program.encode())<128*1024
    from guard.executors.registry import resolve_executor
    assert resolve_executor('boundary_egress','kubernetes') is None

@pytest.mark.parametrize('field',['source','helper','contract','file-reader'])
def test_modified_source_is_rejected_without_approving_runtime_hash(monkeypatch,field):
    original=Path.read_bytes
    suffix={'source':'egress_probe.py','helper':'kubernetes_egress_helper.py','contract':'kubernetes_egress_contract.py'}.get(field)
    if suffix:
        def read(path):
            value=original(path);return value+b'\n# EXAMPLE unreviewed\n' if path.name==suffix else value
        monkeypatch.setattr(Path,'read_bytes',read)
    else:
        from guard.executors import owned_capture
        monkeypatch.setattr(owned_capture,'_FILE_READER',owned_capture._FILE_READER+'\n# EXAMPLE unreviewed\n')
    with pytest.raises(ValueError,match='KUBERNETES_EGRESS_INPUT_INVALID'):e.source()

@pytest.mark.parametrize('bad',['raw','groups-extra','duplicate-key','row-extra','nofollow','file-replaced','too-many-rows'])
def test_closed_packet_rejects_untrusted_or_partial_file_without_leaking_raw(bad):
    v=packet('receiver')
    if bad=='raw':v['argv']=PRIVATE
    if bad=='groups-extra':v['context_before']['groups'].append(55)
    if bad=='nofollow':v['file']['nofollow_components']=False
    if bad=='file-replaced':v['file']['path_after']['inode']+=1
    if bad in {'duplicate-key','row-extra','too-many-rows'}:
        raw=base64.b64decode(v['file']['raw_base64'])
        if bad=='duplicate-key':raw=raw.replace(b'"kind":',b'"kind":"startup","kind":',1)
        if bad=='row-extra':
            row=json.loads(raw);row['private']=PRIVATE;raw=canonical_json(row)+b'\n'
        if bad=='too-many-rows':raw*=10
        f=v['file'];f.update(raw_base64=base64.b64encode(raw).decode(),raw_retained_bytes=len(raw),raw_retained_sha256=sha256_hex(raw))
        for key in ('file_before','file_after','path_after'):f[key]['size']=len(raw)
    with pytest.raises(ValueError):e.decode(v,'receiver')

@pytest.mark.parametrize('matches',[False,True])
def test_helper_requires_matching_credentials_before_sender(monkeypatch,capsys,matches):
    env,target,receiver,p=inputs();request={'mode':'sender','budget':1,'policy_values':p,'actor_spec':ACTOR,'connect_timeout':.1,'correlation':NONCE}
    monkeypatch.setattr(helper.sys,'platform','linux');monkeypatch.setattr(helper.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(canonical_json(request))))
    monkeypatch.setattr(helper.signal,'signal',lambda *a:None);monkeypatch.setattr(helper.signal,'setitimer',lambda *a:None,raising=False)
    monkeypatch.setattr(helper.signal,'ITIMER_REAL',0,raising=False)
    monkeypatch.setattr(helper.signal,'SIGALRM',0,raising=False)
    monkeypatch.setattr(helper,'BOOT_DEADLINE',time.monotonic()+1,raising=False)
    c=context()
    if not matches:c['groups']=[2000]
    monkeypatch.setattr(helper,'context',lambda:deepcopy(c),raising=False)
    calls=[]
    def send(*a,**kw):
        calls.append((a,kw));return packet('sender')['sender']
    monkeypatch.setattr(helper,'sender',SimpleNamespace(send_probe=send),raising=False)
    assert helper.main()==0
    value=json.loads(capsys.readouterr().out)
    assert value['context_before']==value['context_after']==c
    assert len(calls)==int(matches)
    if matches:
        assert value['sender']['connected'] and value['diagnostic_codes']==[]
        assert calls[0][1]['expected_time_namespace_inode']>0 and calls[0][1]['deadline_monotonic_ns']>0
    else:assert value['sender'] is None and value['diagnostic_codes']==['EGRESS_HELPER_UNAVAILABLE']
