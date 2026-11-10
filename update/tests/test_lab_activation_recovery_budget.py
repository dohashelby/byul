"""Operational cold-start recovery does not change fault deadlines or generations."""
from copy import deepcopy
import hashlib

import pytest

from guard.executors import lab_activation as actor


BASE={'namespace':'lab','selector':'app=engine','container':'engine','probe_pod':'probe','probe_container':'probe',
    'service_url':'http://engine.lab.svc:8000','model':'model','deployment':'engine','process_pattern':'VLLM',
    'deadline_ms':5000,'good_manifest':'approved.json','max_duration_seconds':600}


def pod(uid,cid,ready):
    return {'metadata':{'name':'engine-'+uid,'uid':uid},'status':{
        'conditions':[{'type':'Ready','status':'True' if ready else 'False'}],
        'containerStatuses':[{'name':'engine','ready':ready,'containerID':'containerd://'+cid*64,'restartCount':0,
            'state':{'running':{'startedAt':'2026-11-06T00:00:00Z'}}}]}}


class ColdStart(actor.Lab):
    def __init__(self,cfg,*,old_ready=True,same_uid=False):
        self.now=0.;self.applied=False;self.old_ready=old_ready;self.same_uid=same_uid;self.direct=[]
        super().__init__(cfg,clock=lambda:self.now,wait=self.advance)
        self.mutated=True
    def advance(self,seconds):self.now+=seconds
    def apply(self,name):assert name=='good_manifest';self.applied=True
    def _pods(self):
        if self.applied and self.now>=120:return [pod('old' if self.same_uid else 'new','b',True)]
        return [pod('old','a',self.old_ready)]
    def normal_request(self,*_args,**_kwargs):raise AssertionError('new recovery must use its new Pod address')


def test_long_recovery_waits_for_fresh_pod_then_uses_bound_normal_request(monkeypatch):
    lab=ColdStart({**BASE,'restore_timeout_ms':240000})
    monkeypatch.setattr(actor,'_deployment_lineage',lambda *_:'owned-deployment')
    def direct(instance,selected,item,**options):
        assert instance is lab and selected['metadata']['uid']=='new'
        assert options['require_ready'] and options['scope_bound'] and item['timeout']==5
        lab.direct.append(selected)
        return {'outcome':200,'complete':True,'external_request_sha256':hashlib.sha256(item['headers']['X-Request-Id'].encode()).hexdigest()}
    monkeypatch.setattr(actor,'_bound_pod_request',direct)
    assert lab.restore() is True and lab.now==120 and len(lab.direct)==1
    assert lab.cfg['deadline_ms']==5000 and lab.cfg['restore_timeout_ms']==240000
    assert lab.mutated is False and lab.normal==[True]


def test_same_pod_container_restart_is_a_fresh_incarnation(monkeypatch):
    lab=ColdStart({**BASE,'restore_timeout_ms':240000},same_uid=True)
    monkeypatch.setattr(actor,'_deployment_lineage',lambda *_:'owned-deployment')
    monkeypatch.setattr(actor,'_bound_pod_request',lambda _lab,_pod,item,**_:{'outcome':200,'complete':True,
        'external_request_sha256':hashlib.sha256(item['headers']['X-Request-Id'].encode()).hexdigest()})
    assert lab.restore() is True and lab.now==120


def test_old_ready_pod_cannot_satisfy_recovery_or_relax_fault_deadline(monkeypatch):
    lab=ColdStart({**BASE,'restore_timeout_ms':10000})
    monkeypatch.setattr(actor,'_bound_pod_request',lambda *_args,**_kwargs:(_ for _ in ()).throw(AssertionError()))
    assert lab.restore() is False and lab.now==10 and lab.cfg['deadline_ms']==5000
    assert lab.mutated is True and 'RECOVERY_NOT_READY_WITHIN_DEADLINE' in lab.missing


def test_unconfigured_original_five_second_recovery_failure_is_preserved():
    lab=ColdStart(BASE,old_ready=False)
    assert lab.restore() is False and lab.now==5
    assert 'RECOVERY_NOT_READY_WITHIN_DEADLINE' in lab.missing


@pytest.mark.parametrize('value',[True,0,-1,600001,'240000'])
def test_invalid_operational_budget_refuses_before_any_work(value):
    with pytest.raises(actor.ActivationRefused,match='RESTORE_TIMEOUT'):
        actor.Lab({**BASE,'restore_timeout_ms':value})


def test_recovery_response_must_match_the_new_request(monkeypatch):
    lab=ColdStart({**BASE,'restore_timeout_ms':240000})
    monkeypatch.setattr(actor,'_deployment_lineage',lambda *_:'owned-deployment')
    monkeypatch.setattr(actor,'_bound_pod_request',lambda *_args,**_kwargs:{'outcome':200,'complete':True,
        'external_request_sha256':'0'*64})
    assert lab.restore() is False and lab.normal==[False] and lab.mutated is True
