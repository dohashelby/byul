"""Kubernetes terminal pre-init cases need no fabricated native stage ACK."""
from copy import deepcopy
import hashlib
import pytest
from guard.executors.initialization_terminal_case import project_terminal_case

EMPTY=hashlib.sha256(b'').hexdigest()
EXPECTED={'context':'owned-lab','namespace':'serving-lab','namespace_uid':'namespace-uid',
          'container':'engine','probe_container':'probe','port':8000,'model':'test-model',
          'failure_kind':'config','failure_marker_sha256':'c'*64}


def pod(name,uid,cid,*,terminal=False,previous=False,probe=False):
    container='probe' if probe else 'engine'
    attempt={'containerID':cid,'exitCode':1,'startedAt':'2026-11-06T13:00:00Z','finishedAt':'2026-11-06T13:00:01Z'}
    status={'name':container,'containerID':cid,'restartCount':1 if previous else 0,'ready':not terminal,
            'state':({'waiting':{'reason':'CrashLoopBackOff'}} if previous else {'terminated':attempt}) if terminal
                    else {'running':{'startedAt':'2026-11-06T13:00:02Z'}}}
    if previous:status['lastState']={'terminated':attempt}
    return {'metadata':{'name':name,'uid':uid,'namespace':'serving-lab'},
      'spec':{'nodeName':'owned-node','hostNetwork':False,'containers':[{'name':container,'ports':[] if probe else [{'containerPort':8000}]}]},
      'status':{'podIP':'10.244.0.20' if terminal else '10.244.0.30' if probe else '10.244.0.21',
                'conditions':[{'type':'Ready','status':'False' if terminal else 'True'}], 'containerStatuses':[status]}}


def receipt(*,previous=False,server_error=False):
    bad=pod('bad','bad-uid','containerd://bad',terminal=True,previous=previous)
    good=pod('good','good-uid','containerd://good');probe=pod('probe','probe-uid','containerd://probe',probe=True)
    attempt={'container_id':'containerd://bad','restart_count':1 if previous else 0,
             'started_at':'2026-11-06T13:00:00Z','finished_at':'2026-11-06T13:00:01Z','exit_code':1,'previous':previous}
    log={'namespace':'serving-lab','pod':'bad','pod_uid':'bad-uid','container':'engine',**attempt,
         'returncode':0,'stdout_sha256':'d'*64,'stdout_bytes':128,'marker_sha256':'c'*64,
         'marker_observed':True,'capture_complete':True,'endpoint':{'context':'owned-lab','namespace':'serving-lab',
         'pod':'bad','container':'engine','previous':previous}}
    failure={'pod_before_log':deepcopy(bad),'pod_after_log':deepcopy(bad),'log_receipt':log,
             'sequence':{'pod_before_log':1,'log_receipt':2,'pod_after_log':3}}
    def phase(engine,start,normal):
        seq={k:start+i for i,k in enumerate(('namespace_before','probe_before_request','pod_before_request',
                  'direct_request','pod_after_request','probe_after_request','namespace_after'))}
        http=({'outcome':200,'sha256':'a'*64,'bytes':42,'complete':True,'normal_shape_verified':True,'model':'test-model',
               'connection_established':True,'request_sent':True} if normal else
              {'outcome':'error','sha256':EMPTY,'bytes':0,'complete':False,'normal_shape_verified':False,
               'error_stage':'connect','error_kind':'connection-refused','errno':111,'connect_refused':True,
               'connection_established':False,'request_sent':False})
        if server_error and not normal:
            http={'outcome':503,'sha256':'e'*64,'bytes':12,'complete':True,'connection_established':True,'request_sent':True}
        return {'namespace_before':{'namespace':'serving-lab','namespace_uid':'namespace-uid'},
          'namespace_after':{'namespace':'serving-lab','namespace_uid':'namespace-uid'},
          'pod_before_request':deepcopy(engine),'pod_after_request':deepcopy(engine),
          'probe_before_request':deepcopy(probe),'probe_after_request':deepcopy(probe), 'sequence':seq,
          'direct_request':{'url':'http://'+engine['status']['podIP']+':8000','method':'POST','path':'/v1/chat/completions',
            'request_id_sha256':('1' if normal else '2')*64,'body_sha256':'b'*64,'transport':'http.client.HTTPConnection',
            'used_proxy':False,'followed_redirects':False,'http':http}}
    prior=pod('normal-before','prior-uid','containerd://prior')
    initial=phase(prior,1,True);initial['direct_request']['request_id_sha256']='3'*64
    failure['sequence']={key:value+7 for key,value in failure['sequence'].items()}
    return {'context':'owned-lab','sequence_scope':'controller-unique-clock','normal_before':initial,'failure':failure,
            'admission':phase(bad,11,False),'recovery':phase(good,18,True)}


@pytest.mark.parametrize('previous',[False,True])
@pytest.mark.parametrize('server_error',[False,True])
def test_preinit_terminal_config_model_with_no_native_ack(previous,server_error):
    doc=receipt(previous=previous,server_error=server_error);original=deepcopy(doc)
    result=project_terminal_case(doc,EXPECTED)
    assert result['activation_scope_complete'] is True and result['activation_missing']==[]
    assert result['activation_violations']==[] and doc==original
    prior,bad,good=result['activation_steps']
    assert bad['proof_source']=='kubelet-terminal-container-attempt'
    assert bad['engine']['attempt']['previous'] is previous and bad['request_rejected'] is True
    assert good['new_generation_normal'] is True
    assert bad['native_stage_ack_observed'] is good['native_stage_ack_observed'] is False
    assert not any(key.startswith('init_native') for key in result)
    model_expected={**EXPECTED,'failure_kind':'model'}
    assert project_terminal_case(doc,model_expected)['activation_steps'][1]['declared_failure_kind']=='model'


def change(doc,path,value):
    current=doc
    for key in path[:-1]:current=current[key]
    current[path[-1]]=value


@pytest.mark.parametrize('path,value',[
    (('context',),'other-context'),
    (('failure','pod_after_log','metadata','uid'),'other-uid'),
    (('failure','log_receipt','container_id'),'containerd://sidecar'),
    (('failure','log_receipt','previous'),True),
    (('failure','log_receipt','endpoint','previous'),0),
    (('failure','log_receipt','stdout_sha256'),EMPTY),
    (('failure','log_receipt','capture_complete'),False),
    (('failure','log_receipt','marker_observed'),False),
    (('failure','log_receipt','marker_sha256'),'0'*64),
    (('failure','log_receipt','returncode'),True),
    (('failure','log_receipt','stdout_bytes'),0),
    (('failure','log_receipt','endpoint','pod'),'other-pod'),
    (('admission','namespace_after','namespace_uid'),'recreated-namespace'),
    (('admission','pod_after_request','status','containerStatuses',0,'containerID'),'containerd://restarted'),
    (('admission','pod_before_request','status','conditions',0,'status'),'True'),
    (('admission','pod_after_request','status','conditions',0,'status'),'True'),
    (('admission','pod_after_request','status','podIP'),'10.244.0.99'),
    (('admission','probe_after_request','metadata','uid'),'other-probe'),
    (('admission','probe_before_request','spec','hostNetwork'),True),
    (('admission','direct_request','url'),'http://service.serving-lab:8000'),
    (('admission','direct_request','url'),'http://10.244.0.99:8000'),
    (('admission','direct_request','used_proxy'),True),
    (('admission','direct_request','followed_redirects'),True),
    (('admission','direct_request','method'),'GET'),
    (('admission','direct_request','http','outcome'),'timeout'),
    (('admission','direct_request','http','error_stage'),'exec'),
    (('admission','direct_request','http','connection_established'),True),
    (('admission','direct_request','http','request_sent'),True),
    (('admission','direct_request','http','errno'),113),
    (('admission','direct_request','http','sha256'),'f'*64),
    (('recovery','pod_after_request','status','containerStatuses',0,'containerID'),'containerd://other-good'),
    (('recovery','direct_request','http','complete'),False),
    (('recovery','direct_request','http','normal_shape_verified'),False),
    (('recovery','direct_request','http','model'),'other-model'),
    (('recovery','direct_request','body_sha256'),'f'*64),
    (('recovery','direct_request','request_id_sha256'),'2'*64),
    (('failure','sequence','log_receipt'),0),
    (('admission','sequence','pod_after_request'),13),
    (('recovery','sequence','namespace_before'),0),
])
def test_unbound_or_incomplete_receipt_never_completes_scope(path,value):
    doc=receipt();change(doc,path,value);out=project_terminal_case(doc,EXPECTED)
    assert out['activation_scope_complete'] is False
    assert len(out['activation_missing'])==1 and out['activation_missing'][0].startswith('LIF09_TERMINAL_CASE_')
    assert out['activation_violations']==[]


@pytest.mark.parametrize('outcome',[200,201,302,400,401,404,'error','timeout',True])
def test_only_existing_complete_5xx_class_is_http_rejection(outcome):
    doc=receipt(server_error=True);doc['admission']['direct_request']['http']['outcome']=outcome
    assert project_terminal_case(doc,EXPECTED)['activation_scope_complete'] is False


def test_old_recovery_generation_and_success_exit_cannot_qualify():
    doc=receipt()
    for key in ('pod_before_request','pod_after_request'):
        doc['recovery'][key]['metadata']['uid']='bad-uid'
    assert project_terminal_case(doc,EXPECTED)['activation_scope_complete'] is False
    doc=receipt()
    for key in ('pod_before_log','pod_after_log'):
        doc['failure'][key]['status']['containerStatuses'][0]['state']['terminated']['exitCode']=0
    assert project_terminal_case(doc,EXPECTED)['activation_scope_complete'] is False


def test_raw_text_env_arguments_and_error_details_are_not_projected():
    import json
    doc=receipt(server_error=True);secret='SYNTHETIC_MUST_NOT_LEAVE_INPUT'
    doc['failure']['raw_error']=secret
    doc['failure']['pod_before_log']['spec']['containers'][0]['env']=[{'name':'PRIVATE','value':secret}]
    doc['admission']['direct_request']['http'].update(error_kind=secret,model=secret,stderr=secret)
    out=project_terminal_case(doc,EXPECTED)
    assert out['activation_scope_complete'] is True and secret not in json.dumps(out)


def test_baseline_is_required_and_recovery_must_not_reuse_prior_normal():
    doc=receipt();doc.pop('normal_before')
    assert project_terminal_case(doc,EXPECTED)['activation_scope_complete'] is False
    doc=receipt()
    for key in ('pod_before_request','pod_after_request'):
        doc['recovery'][key]['metadata']['uid']='prior-uid'
    assert project_terminal_case(doc,EXPECTED)['activation_scope_complete'] is False
    doc=receipt();doc['normal_before']['direct_request']['http']['normal_shape_verified']=False
    assert project_terminal_case(doc,EXPECTED)['activation_scope_complete'] is False


def test_running_current_container_cannot_reuse_last_terminated_attempt():
    doc=receipt(previous=True)
    for phase in ('failure','admission'):
        for key in (('pod_before_log','pod_after_log') if phase=='failure' else ('pod_before_request','pod_after_request')):
            status=doc[phase][key]['status']['containerStatuses'][0]
            status['state']={'running':{'startedAt':'2026-11-06T13:00:02Z'}}
    assert project_terminal_case(doc,EXPECTED)['activation_scope_complete'] is False


def test_producer_metadata_extras_are_preserved_as_input_without_leaking():
    doc=receipt();doc['failure']['log_receipt']['endpoint'].update(timestamps=True,tail_lines=160)
    doc['failure']['lineage_before']={'deployment':{'metadata':{'uid':'owned-deployment'}}}
    doc['admission']['original_primary_metadata']={'source':'existing-producer'}
    assert project_terminal_case(doc,EXPECTED)['activation_scope_complete'] is True
