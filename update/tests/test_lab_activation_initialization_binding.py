"""Original boot error, terminal container, closed Ready and refused request stay together."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard.executors import lab_activation as actor


BASE={'namespace':'lab','selector':'app=engine,guard.run=run','container':'engine','probe_pod':'probe','probe_container':'probe',
    'service_url':'http://engine.lab.svc:8000','model':'model','deployment':'engine','process_pattern':'VLLM',
    'deadline_ms':5000,'good_manifest':'good.json','faulty_manifest':'faulty.json','failure_marker':'model shard missing'}


def engine():
    return {'metadata':{'namespace':'lab','name':'engine','uid':'new','labels':{'app':'engine','guard.run':'run',
        'byul.lab/generation':'faulty'},'ownerReferences':[{'kind':'ReplicaSet','name':'rs','uid':'rs-id','controller':True}]},
        'spec':{'nodeName':'node','containers':[{'name':'engine','ports':[{'containerPort':8000}]}]},
        'status':{'podIP':'10.0.0.10','conditions':[{'type':'Ready','status':'False'}],
            'containerStatuses':[{'name':'engine','ready':False,'restartCount':1,'containerID':'containerd://'+'a'*64,
                'state':{'waiting':{'reason':'CrashLoopBackOff'}},'lastState':{'terminated':{
                    'containerID':'containerd://'+'a'*64,'startedAt':'2026-11-06T00:00:00Z',
                    'finishedAt':'2026-11-06T00:00:01Z','exitCode':1}}}]}}


def probe():
    return {'metadata':{'namespace':'lab','name':'probe','uid':'probe-id','labels':{}},
        'spec':{'nodeName':'node','containers':[{'name':'probe'}]},
        'status':{'podIP':'10.0.0.20','conditions':[{'type':'Ready','status':'True'}],
            'containerStatuses':[{'name':'probe','containerID':'containerd://'+'b'*64,'restartCount':0,
                'state':{'running':{'startedAt':'2026-11-06T00:00:00Z'}},'ready':True}]}}


class Trial(actor.Lab):
    def __init__(self):
        self.now=0.;self.pod=engine();self.client=probe();self.requests_sent=[];self.commands=[];self.mode=None
        self.namespace={'metadata':{'name':'lab','uid':'namespace-id','labels':{actor.OWNED_LABEL:'true'}}}
        super().__init__(BASE,clock=lambda:self.now,wait=lambda n:setattr(self,'now',self.now+n))
    def _pods(self):return [deepcopy(self.pod)]
    def _kubectl(self,*args,**_kwargs):
        self.commands.append(args)
        if args[:2]==('get','namespace'):value=self.namespace
        elif args[:2]==('get','pod'):value=self.pod if args[2]=='engine' else self.client
        elif args[:2]==('get','deployment'):value={'metadata':{'namespace':'lab','uid':'deployment-id'}}
        elif args[:2]==('get','replicaset'):value={'metadata':{'namespace':'lab','uid':'rs-id',
            'ownerReferences':[{'kind':'Deployment','name':'engine','uid':'deployment-id','controller':True}]}}
        elif args[:2]==('get','service'):value={'metadata':{'namespace':'lab'},'spec':{'selector':{'app':'engine'},
            'ports':[{'port':8000,'targetPort':8000,'protocol':'TCP'}]}}
        elif args[0]=='logs':
            if '--container=engine' not in args:return b''
            if self.mode=='restart-during-log':
                self.pod['status']['containerStatuses'][0]['state']={'running':{'startedAt':'2026-11-06T00:00:02Z'}}
            return b'FileNotFoundError: model shard missing\n'
        else:raise AssertionError(args)
        return json.dumps(value).encode()
    def requests(self,items,**options):
        self.requests_sent.append((items,options))
        if self.mode=='pod-changed':self.pod['metadata']['uid']='other'
        if self.mode=='probe-changed':self.client['metadata']['uid']='other-probe'
        if self.mode=='namespace-changed':self.namespace['metadata']['uid']='other-namespace'
        if self.mode=='container-changed':self.pod['status']['containerStatuses'][0]['lastState']['terminated']['containerID']='containerd://'+'c'*64
        token=items[0].get('headers',{}).get('X-Request-Id')
        if token is None:return [{'outcome':503,'complete':True,'model':None}]
        return [{'outcome':200 if self.mode=='accepted' else 'error','complete':self.mode=='accepted',
            'external_request_sha256':hashlib.sha256(token.encode()).hexdigest(),
            'connection_established':self.mode=='accepted','request_sent':self.mode=='accepted',
            'error_stage':'connect','error_kind':'connection-refused'}]


def test_exact_previous_terminal_attempt_is_admissible_only_while_still_waiting():
    lab=Trial();proof=actor._initialization_failure_marker(lab,lab.pod,BASE['failure_marker'],5)
    assert proof['attempt']['previous'] and proof['attempt']['exit_code']==1 and proof['upper_boot_failure']
    assert any('--previous' in c for c in lab.commands)
    receipt=actor._initialization_admission(lab,lab.pod,proof,5,'model')
    assert receipt['request_rejected'] and not receipt['request_accepted'] and receipt['readiness_closed']
    assert lab.requests_sent[0][1]['url']=='http://10.0.0.10:8000'
    assert receipt['service_routing_attribution'] is False


@pytest.mark.parametrize('mode',['pod-changed','probe-changed','namespace-changed','container-changed'])
def test_response_does_not_inherit_another_epoch_or_peer(mode):
    lab=Trial();proof=actor._initialization_failure_marker(lab,lab.pod,BASE['failure_marker'],5)
    lab.mode=mode
    assert actor._initialization_admission(lab,lab.pod,proof,5,'model') is None


def test_old_error_does_not_bind_to_running_replacement():
    lab=Trial();lab.mode='restart-during-log'
    assert actor._initialization_failure_marker(lab,lab.pod,BASE['failure_marker'],5) is None


@pytest.mark.parametrize('change',['success-exit','missing-cid','ready','no-readiness','wrong-container'])
def test_failure_receipt_requires_real_nonzero_exit_of_selected_container(change):
    pod=engine();status=pod['status']['containerStatuses'][0]
    if change=='success-exit':status['lastState']['terminated']['exitCode']=0
    if change=='missing-cid':status['lastState']['terminated'].pop('containerID')
    if change=='ready':pod['status']['conditions'][0]['status']='True'
    if change=='no-readiness':pod['status']['conditions']=[]
    if change=='wrong-container':status['name']='sidecar'
    assert actor._failed_start_attempt(pod,'engine') is None


def test_closed_readiness_does_not_hide_actual_acceptance():
    lab=Trial();proof=actor._initialization_failure_marker(lab,lab.pod,BASE['failure_marker'],5)
    lab.mode='accepted';receipt=actor._initialization_admission(lab,lab.pod,proof,5,'model')
    assert receipt['request_accepted'] is True and receipt['request_rejected'] is False


def test_single_initial_failure_uses_bound_rejection_without_legacy_unknown(monkeypatch):
    class Boot(Trial):
        applied=False
        def _pods(self):
            value=super()._pods()
            if not self.applied:
                value[0]['metadata']['uid']='old';value[0]['metadata']['labels']['byul.lab/generation']='good'
            return value
        def apply(self,name):
            assert name=='faulty_manifest';self.applied=True;self.mutated=True
    # This case isolates failure/admission; a separate roundtrip test covers
    # the newly required pre-failure normal generation.
    monkeypatch.setattr(actor,'_primary_normal',lambda lab,_label:{'normal_control_fixture':True})
    lab=Boot();actor._partial_start(lab,{'init_preparation_deadline_ms':10000})
    assert lab.missing==[] and lab.violations==[]
    trial=next(v for v in lab.steps if v['step']=='startup-failure-admission')
    assert trial['request_rejected'] and trial['failure']['upper_boot_failure']
