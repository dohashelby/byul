"""Actor delegates source judgment and restores every issued fault, even on collection errors."""
import hashlib
import threading
from types import SimpleNamespace

import pytest

from guard.executors import lab_activation as actor


BASE={'namespace':'lab','selector':'app=engine','container':'engine','probe_pod':'probe','probe_container':'probe',
    'service_url':'http://engine.lab.svc:8000','model':'model','deployment':'engine','process_pattern':'VLLM',
    'deadline_ms':5000,'good_manifest':'approved.json','max_duration_seconds':600,
    'fault_control_directory':'/guard-lifecycle/fault-control','fault_control_session':'a'*32,
    'fault_control_target_sha256':'b'*64,'fault_control_rank':0,'fault_control_environment':'/scope/ENV.json',
    'fault_control_target':'/scope/target.json','fault_control_prepared':'/scope/manifest.json'}
FLAGS=('occurrence_verified','request_fault_overlap_verified','detection_deadline_verified',
    'upper_notification_deadline_verified','affected_request_error_verified','clock_verified','whole_kind_complete')


@pytest.fixture
def workflow(monkeypatch):
    time=[0.];lab=actor.Lab(dict(BASE),clock=lambda:time[0],wait=lambda n:time.__setitem__(0,time[0]+n))
    operations=[];snapshots=[];verifications=[];restores=[]
    client_started=threading.Event();client_release=threading.Event()
    projection={**dict.fromkeys(FLAGS,True),'missing':[],'violations':[],'evidence_refs':['source-record-ref']}
    def verify(packet,client,injection,scope,deadlines):
        assert injection['pre_signal_packet']=={'marker':'live-active-call'}
        assert packet=={'marker':'postmortem-source'}
        assert client['external_request_sha256']==scope['external_request_sha256']
        assert client['fault_nonce']==scope['nonce']
        verifications.append((scope,deadlines))
        return projection
    module=SimpleNamespace(PROFILE='vllm-native-lifecycle-metadata/24',validate_fault_case=verify)
    ctx={'module':module,'env':SimpleNamespace(model_dump=lambda **_:{'id':'ENV-test'}),'target':{},'prepared':{},'binding':{'engine':{'pod_uid':'engine-uid','container_id':'containerd://'+'c'*64}}}
    monkeypatch.setattr(actor,'_control_context',lambda _:ctx)
    def capture(_lab,_ctx,*,seal=False):
        assert _lab is lab and _ctx is ctx
        index=len(snapshots);snapshots.append(seal)
        return {'packet':{'marker':('seed','live-active-call','postmortem-source')[index]},
            'transport':{'collection_gap':False},'sealed':seal}
    monkeypatch.setattr(actor,'_control_capture',capture)
    target={'pid':107,'start_ticks':123,'pod':'engine','pod_uid':'engine-uid'}
    monkeypatch.setattr(actor,'_control_seed',lambda *_:target)
    def rpc(_lab,_ctx,action,token,_target):
        operations.append(action)
        if action=='ready':
            assert client_started.wait(2)
            return {'receipt':{'active':True},'selection_only':True,'native_fault_verified':False}
        if action in ('signal','activate','release'):client_release.set()
        return {'action':action,'nonce':token['nonce']}
    monkeypatch.setattr(actor,'_control_rpc',rpc)
    def normal(_):lab.normal.append(True);return {'outcome':200,'complete':True}
    monkeypatch.setattr(lab,'normal_request',normal)
    def requests(items,**_):
        item,=items
        client_started.set();client_release.wait(2)
        return [{'outcome':503,'complete':True,'elapsed_ms':999999999,
            'external_request_sha256':hashlib.sha256(item['headers']['X-Request-Id'].encode()).hexdigest(),
            'fault_nonce':item['fault_nonce']}]
    monkeypatch.setattr(lab,'requests',requests)
    def restore():restores.append('restore');return True
    monkeypatch.setattr(lab,'restore',restore)
    return SimpleNamespace(lab=lab,operations=operations,snapshots=snapshots,verifications=verifications,
        restores=restores,projection=projection,ctx=ctx,rpc=rpc,capture=capture,client_release=client_release)


@pytest.mark.parametrize('kind,number',[('death',9),('delay',19),('exception',None),('communication-stall',None)])
def test_each_controlled_kind_uses_its_source_protocol_and_shared_time_validator(workflow,kind,number):
    f=workflow
    actor._one_fault(f.lab,kind,lambda _target:(_ for _ in ()).throw(AssertionError('legacy callback not allowed')),
        5000,number,notification_ms=1000)
    assert f.operations==['arm','ready','signal' if number else 'activate','release','clear']
    assert f.snapshots==[False,False,True] and f.restores==['restore'] and f.lab.mutated is False
    assert f.lab.missing==[] and f.lab.violations==[]
    assert f.verifications[0][1]=={'detection_ms':5000,'notification_ms':1000,'response_ms':5000}
    assert f.lab.fault_evidence[0]['document']['client']['elapsed_ms']==999999999
    # The actor never compares that remote duration to a controller timestamp.
    assert f.lab.steps[-1]['fault_detection_latency_verified'] is True


def test_pending_or_spoofed_ready_never_authorizes_an_engine_signal(workflow,monkeypatch):
    f=workflow
    def rpc(lab,ctx,action,token,target):
        if action=='ready':
            f.client_release.set()
            raise ValueError('missing receipt')
        return f.rpc(lab,ctx,action,token,target)
    monkeypatch.setattr(actor,'_control_rpc',rpc)
    actor._one_fault(f.lab,'death',None,5000,9,notification_ms=1000)
    assert 'signal' not in f.operations and 'activate' not in f.operations
    assert f.restores==[] and f.lab.mutated is False
    assert any(v.startswith('FAULT_CONTROL_UNVERIFIED:death:') for v in f.lab.missing)
    assert f.operations[-2:]==['release','clear']


@pytest.mark.parametrize('failure',['signal-return-lost','post-capture','verifier'])
def test_issued_fault_always_releases_cleans_and_restores(workflow,monkeypatch,failure):
    f=workflow
    if failure=='signal-return-lost':
        def rpc(lab,ctx,action,token,target):
            value=f.rpc(lab,ctx,action,token,target)
            if action=='signal':raise OSError('transport failed after possible signal')
            return value
        monkeypatch.setattr(actor,'_control_rpc',rpc)
    elif failure=='post-capture':
        def capture(lab,ctx,*,seal=False):
            if seal:raise ValueError('capture unavailable')
            return f.capture(lab,ctx,seal=seal)
        monkeypatch.setattr(actor,'_control_capture',capture)
    else:f.ctx['module'].validate_fault_case=lambda *_:(_ for _ in ()).throw(ValueError('source proof rejected'))
    actor._one_fault(f.lab,'delay',None,5000,19,notification_ms=1000)
    assert f.operations[-2:]==['release','clear'] and f.restores==['restore']
    assert f.lab.missing and f.lab.violations==[] and f.lab.mutated is False


def test_inconsistent_completeness_flags_cannot_create_pass(workflow):
    f=workflow;f.projection['clock_verified']=False
    actor._one_fault(f.lab,'death',None,5000,9,notification_ms=1000)
    assert 'FAULT_CONTROL_KIND_INCOMPLETE:death' in f.lab.missing


def test_exact_zero_detection_deadline_is_not_replaced_by_a_longer_budget(workflow):
    f=workflow;f.projection['detection_deadline_verified']=False
    f.projection['violations']=[{'code':'RANK_DETECTION_DEADLINE_EXCEEDED'}]
    actor._one_fault(f.lab,'death',None,0,9,notification_ms=0)
    assert f.verifications[0][1]=={'detection_ms':0,'notification_ms':0,'response_ms':5000}
    assert f.lab.missing==[]
    assert f.lab.violations[0]['code']=='RANK_DETECTION_DEADLINE_EXCEEDED'


def test_unknown_notification_deadline_is_not_inferred_from_detection(workflow):
    f=workflow;f.projection['upper_notification_deadline_verified']=False;f.projection['whole_kind_complete']=False
    actor._one_fault(f.lab,'death',None,5000,9)
    assert f.verifications[0][1]['notification_ms'] is None
    assert 'FAULT_CONTROL_KIND_INCOMPLETE:death' in f.lab.missing


def test_restoration_failure_blocks_later_fault(workflow,monkeypatch):
    f=workflow;monkeypatch.setattr(f.lab,'restore',lambda:False)
    actor._one_fault(f.lab,'death',None,5000,9,notification_ms=1000)
    before=list(f.operations)
    actor._one_fault(f.lab,'delay',None,5000,19,notification_ms=1000)
    assert f.operations==before and f.lab.fault_restore_failed
    assert 'SUBSEQUENT_FAULTS_BLOCKED_BY_RESTORE_FAILURE' in f.lab.missing


def test_wrong_physical_action_for_kind_refuses_before_any_control_write(workflow):
    with pytest.raises(actor.ActivationRefused,match='ACTION_KIND'):
        actor._one_fault(workflow.lab,'communication-stall',None,5000,19,notification_ms=1000)
    assert workflow.operations==[] and workflow.restores==[]


def test_official_one_kind_entry_runs_actor_before_consumption_and_never_claims_four(workflow,monkeypatch):
    from tools import b_lif08_fault_control as tool
    from guard.models import Environment,Policy
    f=workflow
    env=Environment(id='ENV-test',data_origin='DECLARED',engine={'name':'vllm','image_digest':'registry/vllm@sha256:'+'a'*64},
        model={'id':'model','revision':'b'*40,'inventory_sha256':'c'*64}).seal()
    f.ctx.update(env=env,target={},prepared={})
    f.ctx['module'].__file__=actor.__file__
    f.ctx['binding'].update(context='byul',run_id='run')
    f.ctx['binding']['engine'].update(namespace='lab',namespace_uid='ns',image=env.engine.image_digest)
    values={'rank_death_detection_ms':5000,'rank_death_notification_ms':1000}
    policy=Policy(id='POL-test',data_origin='DECLARED',status='DRAFT',pack={
        'kind':'PACK','id':'PACK-test','revision':1,'sha256':'a'*64},values={'LIF-08':values}).seal()
    f.lab.cfg['fault_control_kind']='death'
    f.lab.cfg['fault_control_node_image_verification']='local-containerd-v1'
    monkeypatch.setattr(actor,'Lab',lambda *_args,**_kwargs:f.lab)
    monkeypatch.setattr(f.lab,'verify_owned',lambda:None)
    case=tool.run_one(f.lab.cfg,policy,'f'*40)
    assert f.operations==['arm','ready','signal','release','clear']
    assert case['one_kind_complete'] is True and case['activation']['activation_scope_complete'] is False
    assert case['trial']['restore_confirmed'] is True and f.restores==['restore']
    assert case['trial']['environment']==env.model_dump(mode='json')
    assert case['scope']['policy_values_sha256']==tool.digest(values)


def test_explicit_kind_mismatch_is_refused_before_control_writes(workflow):
    workflow.lab.cfg['fault_control_kind']='death'
    with pytest.raises(actor.ActivationRefused,match='CONFIG_KIND'):
        actor._one_fault(workflow.lab,'delay',None,5000,19)
    assert workflow.operations==[]


@pytest.mark.parametrize('kind,number',[('death',9),('delay',19),('exception',None),('communication-stall',None)])
def test_client_finishing_during_presignal_capture_prevents_physical_actuation(workflow,monkeypatch,kind,number):
    f=workflow;original_pool=actor.ThreadPoolExecutor;futures=[]
    class TrackedPool(original_pool):
        def submit(self,*args,**kwargs):
            future=super().submit(*args,**kwargs);futures.append(future);return future
    monkeypatch.setattr(actor,'ThreadPoolExecutor',TrackedPool)
    def capture(lab,ctx,*,seal=False):
        packet=f.capture(lab,ctx,seal=seal)
        if len(f.snapshots)==2:
            # READY was obtained while the client remained active, but costly
            # actual node/source capture allowed it to finish before injection.
            f.client_release.set();futures[0].result(timeout=2)
        return packet
    monkeypatch.setattr(actor,'_control_capture',capture)
    actor._one_fault(f.lab,kind,None,5000,number,notification_ms=1000)
    assert f.operations==['arm','ready','release','clear']
    assert f.restores==[] and f.lab.mutated is False and f.verifications==[]
    trial=f.lab.fault_evidence[0]['document']
    assert trial['issued'] is False and trial['injection'] is None and trial['client']['complete'] is True
    assert trial['pre_signal_capture'] is not None and trial['post_capture'] is None
    assert trial['error_kind']=='ActivationRefused'
    assert 'FAULT_CONTROL_REQUEST_ENDED_BEFORE_INJECTION:'+kind in f.lab.missing


def test_transport_stays_open_for_native_barrier_without_extending_fault_deadlines(workflow,monkeypatch):
    f=workflow;original=f.lab.requests;observed=[]
    def requests(items,**kwargs):
        item,=items;observed.append((item['timeout'],kwargs['timeout']))
        # Twenty seconds of selected source/node verification occurs before
        # injection. A ten-second socket would already have ended the request.
        if item['timeout']<=20:f.client_release.set()
        return original(items,**kwargs)
    monkeypatch.setattr(f.lab,'requests',requests)
    actor._one_fault(f.lab,'death',None,5000,9,notification_ms=1000)
    assert f.operations==['arm','ready','signal','release','clear']
    assert observed==[(40,45)]
    assert f.verifications[0][1]=={'detection_ms':5000,'notification_ms':1000,'response_ms':5000}
    assert f.lab.fault_evidence[0]['document']['control']['hold_timeout_ms']==30000
