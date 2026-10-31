"""Public LIF07 HTTP, fake Kubernetes metadata and no actual worker/cluster."""
from copy import deepcopy
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
import sys
import threading
import pytest

from guard.models import canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import capture_pipes
from guard.runtime.kubernetes_http import KubernetesHTTP, KubernetesBindingError, REQUEST_BINDING_PROFILE
from guard.runtime.kubernetes_initialization import KubernetesInitializationHTTP
from guard.runtime.kubernetes_worker_readiness import KubernetesWorkerReadinessHTTP, PROFILE, SCHEMA, SLOTS
from tests.test_kubernetes_request_binding import env, target
from tests.test_kubernetes_sampling import World as PodWorld

PRIVATE = 'EXAMPLE_WORKER_HTTP_PRIVATE_NOT_SAVED'


def inputs(mode='normal'):
    environment=env();environment=environment.model_copy(update={'model':environment.model.model_copy(update={
        'id':'EXAMPLE/model','revision':'a'*40,'inventory_sha256':'b'*64})}).seal()
    selected=target(environment)
    selected['model_mapping']={'schema':'guard.model-request-mapping/1','environment_ref':environment.ref().model_dump(mode='json'),
        'repository_id':environment.model.id,'revision':environment.model.revision,'served_model_name':'fixture-model',
        'host_snapshot':'/EXAMPLE-host','container_mount':'/EXAMPLE-model','inventory_sha256':environment.model.inventory_sha256}
    now=utcnow();values={'readiness_environment_sha256':environment.ref().sha256,
        'readiness_target_sha256':sha256_hex(canonical_json(selected)),'readiness_session':'a'*32,
        'readiness_cohort_sha256':'c'*64,'readiness_validity_start':(now-timedelta(seconds=30)).isoformat(),
        'readiness_validity_end':(now+timedelta(minutes=3)).isoformat()}
    return environment,World(selected,mode),values


class World(PodWorld):
    def __init__(self,selected,mode):
        super().__init__(selected);self.mode=mode;self.api_round=0;self.real_probe=False;self.clock_hook=None

    def runner(self,argv,**kwargs):
        if 'exec' in argv:
            assert '-i' in argv and '-t' not in argv and '--container=probe' in argv
            spec=json.loads(kwargs['input_bytes']);request,=spec['requests']
            assert spec['timeout']<=10 and 0<float(argv[-1])<=10
            assert request['id']=='lif07-http-'+SLOTS[len(self.execs)]
            rid=request['headers']['X-Request-Id'];assert rid=='byul-lif07-http-'+'d'*32+'-'+SLOTS[len(self.execs)]
            if request['method']=='POST':
                assert request['url'].endswith('/v1/chat/completions')
                assert request['body']=={'model':'fixture-model','messages':[{'role':'user','content':'hello'}],
                    'max_tokens':1,'temperature':0,'stream':False}
            else:assert request['method']=='GET' and request['url'].endswith('/health') and 'body' not in request
            self.execs.append(deepcopy(request));self.after_exec=True
            if self.real_probe:
                return capture_pipes([sys.executable,'-I','-S','-B','-c',argv[-2],argv[-1]],**kwargs)
            slot=request['id'].removeprefix('lif07-http-')
            body=json.dumps({'model':'fixture-model','choices':[{'message':{'content':PRIVATE}}]}) if slot=='canary' else PRIVATE
            status=200
            if self.mode=='canary-timeout' and slot=='canary':status='timeout';body=''
            if self.mode=='health-refused' and slot=='health-before':status='refused';body=''
            if self.mode=='health503' and slot=='health-before':status=503
            if self.mode=='canary503' and slot=='canary':status=503
            if self.mode=='canary-bad-json' and slot=='canary':body='{'
            if self.mode=='canary-duplicate-model' and slot=='canary':body='{"model":"other","model":"fixture-model","choices":[{"message":{"content":"'+PRIVATE+'"}}]}'
            if self.mode=='canary-wrong-model' and slot=='canary':body=json.dumps({'model':'different-model','choices':[{'message':{'content':PRIVATE}}]})
            if self.mode=='canary-empty' and slot=='canary':body=json.dumps({'model':'fixture-model','choices':[{'message':{'content':''}}]})
            row={'id':request['id'],'method':request['method'],'mode':'json','outcome':status,'body':body,
                'body_bytes':len(body.encode()),'body_sha256':sha256_hex(body.encode()),'capture_complete':type(status) is int,
                'eof':type(status) is int,'utf8_valid':True,'body_truncated':False,
                'detail':PRIVATE,'headers':{'Authorization':PRIVATE},'body_preview':PRIVATE}
            if self.mode=='wrong-response-id':row['id']='lif09-before'
            if self.mode=='wrong-response-method':row['method']='DELETE'
            if self.mode=='wrong-response-mode':row['mode']='sse'
            if self.mode=='bad-outcome':row['outcome']=PRIVATE
            if self.mode=='bool-outcome':row['outcome']=True
            if self.mode=='partial-body':row['capture_complete']=False;row['body_truncated']=True
            if self.mode=='body-count':row['body_bytes']+=1
            if self.mode=='bad-hash':row['body_sha256']='f'*64
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            code=0
            if slot=='canary' and self.mode=='outer-returncode':code=1
            if slot=='canary' and self.mode=='outer-truncated':pipe['complete']=False
            if slot=='canary' and self.mode=='cleanup-after-canary':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            if self.clock_hook is not None:self.clock_hook()
            return {'returncode':code,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        result=super().runner(argv,**kwargs);value=json.loads(result['stdout_bytes'])
        if 'namespaces' in argv:
            self.api_round+=1
            if self.mode=='late-final-api' and self.api_round>=7:raise OSError(PRIVATE)
            for item in value['items']:item['kind']='Namespace'
            if self.mode=='namespace-wrong' or self.mode=='namespace-late' and self.execs:value['items'][0]['metadata']['uid']='other'
            if self.mode=='namespace-kind':value['items'][0]['kind']='ConfigMap'
            if self.mode=='namespace-duplicate':value['items'].append(deepcopy(value['items'][0]))
        elif 'get' in argv:
            value['kind']='Pod';probe=value['metadata']['name']==self.target['kubernetes']['probe']['pod']
            state=value['status']['containerStatuses'][0]
            value['spec']['containers'][0].update(env=[{'name':'PRIVATE','value':PRIVATE}],args=[PRIVATE])
            if not probe:
                ready=not(self.mode in {'unready','unknown-ready','ambiguous-ready'} or self.mode=='transition' and len(self.execs)<2)
                value['status']['conditions']=[{'type':'Ready','status':'True' if ready else 'False'}];state['ready']=ready
                if self.mode=='unknown-ready':value['status']['conditions'][0]['status']='Unknown'
                if self.mode=='ambiguous-ready':value['status']['conditions'].append({'type':'Ready','status':'True'})
                if self.mode=='engine-pending':value['status']['phase']='Pending'
                if self.mode=='engine-deleted':value['metadata']['deletionTimestamp']='2026-10-31T00:00:00Z'
                if self.mode=='engine-container-waiting':state['state']={'waiting':{'reason':'EXAMPLE'}}
                if self.mode=='engine-label':value['metadata']['labels']['guard.run']='other'
                if self.mode=='engine-image':value['spec']['containers'][0]['image']='other@sha256:'+'f'*64
                if self.mode=='engine-image-id':state['imageID']='other@sha256:'+'f'*64
                if self.mode=='engine-hostnet':value['spec']['hostNetwork']=True
                if self.mode=='engine-restart-pre' or self.mode=='engine-restart-late' and self.execs:state['restartCount']+=1
                if self.mode=='engine-start-pre':state['state']['running']['startedAt']='2026-10-27T00:00:00Z'
                if self.mode=='engine-replaced-late' and self.execs:value['metadata']['uid']='other'
            else:
                if self.mode=='probe-not-ready' or self.mode=='probe-unready-late' and self.execs:
                    value['status']['conditions']=[{'type':'Ready','status':'False'}];state['ready']=False
                if self.mode=='probe-duplicate':value['status']['conditions'].append({'type':'Ready','status':'False'})
                if self.mode=='probe-replaced-late' and len(self.execs)>=2:value['metadata']['uid']='other'
        result['stdout_bytes']=canonical_json(value);return result


def assert_safe(packet):
    assert packet['schema']==SCHEMA and packet['profile']==PROFILE and packet['data_origin']=='EXAMPLE'
    assert all(packet[k] is False for k in ('producer_authenticated','startup_causal_binding_verified','expected_ranks_verified',
        'runtime_hash_approval','upper_READY_delivery_verified','service_gate_verified','worker_model_GPU_complete','fault_or_delay_activated','whole_scope_complete'))
    assert PRIVATE not in canonical_json(packet).decode()
    def walk(value):
        if type(value) is dict:
            assert not {'body','body_sha256','body_preview','headers','detail','prompt','messages','env','argv','exception'}&set(value)
            for child in value.values():walk(child)
        elif type(value) is list:
            for child in value:walk(child)
    walk(packet)


@pytest.mark.parametrize('mode',['normal','unready','transition','unknown-ready','ambiguous-ready','probe-not-ready','probe-duplicate',
    'namespace-wrong','namespace-kind','namespace-duplicate','engine-pending','engine-deleted','engine-container-waiting',
    'engine-label','engine-image','engine-image-id','engine-hostnet','engine-restart-pre','engine-start-pre',
    'engine-replaced-late','engine-restart-late','probe-replaced-late','probe-unready-late','namespace-late',
    'canary-timeout','health-refused','health503','canary503','canary-bad-json','canary-duplicate-model','canary-wrong-model','canary-empty',
    'wrong-response-id','wrong-response-method','wrong-response-mode','bad-outcome','bool-outcome','partial-body',
    'body-count','bad-hash','outer-returncode','outer-truncated','cleanup-after-canary','late-final-api'])
def test_closed_public_worker_HTTP_sanitization_binding_and_late_failure(mode,monkeypatch):
    environment,world,values=inputs(mode)
    def forbidden(*args,**kwargs):raise AssertionError('unexpected real target or process')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    transport=KubernetesWorkerReadinessHTTP(world.target,environment,runner=world.runner)
    assert transport.report() is None and not world.calls
    packet=transport.sample(values,sample_id='d'*32);assert_safe(packet)
    complete={'normal','unready','transition','unknown-ready','canary-timeout','health-refused','health503','canary503','canary-bad-json','canary-duplicate-model','canary-wrong-model','canary-empty'}
    assert packet['sampling_complete']==(mode in complete) and packet['collection_gap']==(mode not in complete)
    assert bool(packet['cleanup_errors'])==(mode=='cleanup-after-canary')
    if mode in complete or mode in {'ambiguous-ready','late-final-api'}:assert len(world.execs)==3
    if mode=='unready':assert all(r['engine_readiness_before']=={'pod_ready':False,'container_ready':False} for r in packet['requests'])
    if mode=='transition':assert packet['requests'][0]['engine_readiness_before']['pod_ready'] is False and packet['requests'][-1]['engine_readiness_after']['pod_ready'] is True
    if mode=='unknown-ready':assert packet['requests'][0]['engine_readiness_before']['pod_ready'] is None
    if mode=='ambiguous-ready':assert all(r['engine_readiness_before'] is None for r in packet['requests'])
    if mode in {'engine-replaced-late','engine-restart-late','probe-unready-late','namespace-late'}:
        assert len(world.execs)==1 and packet['requests'][0]['outcome']==200 and packet['requests'][0]['binding_before_verified'] is True
        assert packet['requests'][0]['binding_after_verified'] is False
    if mode in {'outer-returncode','outer-truncated','probe-replaced-late','cleanup-after-canary'}:
        assert len(world.execs)==2 and packet['requests'][0]['binding_after_verified'] is True
        assert packet['requests'][1]['probe_document_validated'] is True and packet['requests'][1]['outcome']==200
    if mode in {'canary-bad-json','canary-duplicate-model','canary-wrong-model','canary-empty','canary503'}:assert packet['requests'][1]['normal_shape_verified'] is False
    if mode in {'partial-body','body-count','bad-hash'}:assert not packet['requests'][0]['probe_result_complete']
    if mode in {'wrong-response-id','wrong-response-method','wrong-response-mode','bad-outcome','bool-outcome'}:assert packet['requests'][0]['outcome'] is None and packet['requests'][0]['normal_shape_verified'] is None
    if mode=='health-refused':assert packet['requests'][0]['outcome']=='refused' and packet['requests'][0]['http_reply_complete'] is False
    original=deepcopy(packet);packet['diagnostic_codes'].append('EXAMPLE-LOCAL-MUTATION')
    assert transport.report()==original
    before=len(world.calls)
    with pytest.raises(ValueError,match='WORKER_READINESS_SAMPLE_INVALID'):transport.sample(values,sample_id='e'*32)
    assert len(world.calls)==before and transport.report()==original


@pytest.mark.parametrize('key,value',[('readiness_unknown',None),('readiness_environment_sha256','f'*64),
    ('readiness_target_sha256','f'*64),('readiness_session','../unsupported'),('readiness_cohort_sha256',True),
    ('readiness_validity_start','2026-10-31T00:00:00'),('readiness_validity_end','2020-01-01T00:00:00+00:00')])
def test_invalid_policy_never_reads_API_or_sends_probe(key,value):
    environment,world,values=inputs();values[key]=value
    transport=KubernetesWorkerReadinessHTTP(world.target,environment,runner=world.runner)
    with pytest.raises(ValueError,match='WORKER_READINESS_POLICY_INVALID'):transport.sample(values,sample_id='d'*32)
    assert not world.calls and not world.execs and transport.report() is None


@pytest.mark.parametrize('sample_id',['byul-lif09-before','../unsupported','F'*32,'0'*31,True,None])
def test_other_namespaces_and_invalid_sample_ids_cannot_send(sample_id):
    environment,world,values=inputs();transport=KubernetesWorkerReadinessHTTP(world.target,environment,runner=world.runner)
    with pytest.raises(ValueError,match='WORKER_READINESS_SAMPLE_INVALID'):transport.sample(values,sample_id=sample_id)
    assert not world.calls and transport.report() is None


@pytest.mark.parametrize('change',['lab','version032','inventory','legacy-mapping','alias','model','revision','mapping-env',
    'target-env','image-pin','duration-bool','duration-zero','binary','clock-type','clock-nan','clock-value'])
def test_constructor_gates_remain_closed_before_API(change):
    environment,world,_=inputs();kwargs={}
    if change=='lab':world.target['lab']=False
    elif change=='version032':environment=environment.model_copy(update={'engine':environment.engine.model_copy(update={'version':'0.32.0'})}).seal()
    elif change=='inventory':environment=environment.model_copy(update={'model':environment.model.model_copy(update={'inventory_sha256':None})}).seal()
    elif change=='legacy-mapping':world.target['model_mapping']={'repository_id':environment.model.id,'revision':environment.model.revision,'api_model':'fixture-model'}
    elif change=='alias':world.target['model_mapping']['served_model_name']='bad\nmodel'
    elif change=='model':world.target['model_mapping']['repository_id']='other/model'
    elif change=='revision':world.target['model_mapping']['revision']='f'*40
    elif change=='mapping-env':world.target['model_mapping']['environment_ref']['sha256']='f'*64
    elif change=='target-env':world.target['environment_ref']['sha256']='f'*64
    elif change=='image-pin':world.target['kubernetes']['engine']['image']='other@sha256:'+'f'*64
    elif change=='duration-bool':kwargs['duration']=True
    elif change=='duration-zero':kwargs['duration']=0
    elif change=='binary':kwargs['binary']=' '
    elif change=='clock-type':kwargs['clock']=True
    elif change=='clock-nan':kwargs['clock']=lambda:float('nan')
    elif change=='clock-value':kwargs['clock']=lambda:PRIVATE
    with pytest.raises(KubernetesBindingError):KubernetesWorkerReadinessHTTP(world.target,environment,runner=world.runner,**kwargs)
    assert not world.calls and not world.execs


@pytest.mark.parametrize('after',[121,-1])
def test_deadline_or_clock_regression_preserves_typed_first_document(after):
    environment,world,values=inputs();clock=[0.0]
    world.clock_hook=lambda:clock.__setitem__(0,after)
    transport=KubernetesWorkerReadinessHTTP(world.target,environment,runner=world.runner,clock=lambda:clock[0])
    packet=transport.sample(values,sample_id='d'*32);assert_safe(packet)
    assert len(world.execs)==1 and packet['requests'][0]['probe_document_validated'] and packet['requests'][0]['outcome']==200
    assert not packet['sampling_complete'] and packet['requests'][0]['transport_complete'] is False


def test_old_generic_Ready_gate_and_old_LIF09_namespace_are_distinct():
    environment,world,values=inputs('unready')
    generic=KubernetesHTTP(world.target,environment,runner=world.runner,duration=30,profile=REQUEST_BINDING_PROFILE)
    assert generic.snapshot()['collected'] is False and not world.execs
    transport=KubernetesWorkerReadinessHTTP(world.target,environment,runner=world.runner)
    with pytest.raises(KubernetesBindingError):transport('kubernetes-probe',[],1)
    with pytest.raises(TypeError):transport.sample(values,sample_id='d'*32,request={'method':'DELETE'})
    assert not world.execs
    legacy=KubernetesInitializationHTTP(world.target,environment,runner=world.runner)
    with pytest.raises(KubernetesBindingError):legacy.perform('before','byul-lif07-http-'+'d'*32+'-canary')
    assert not world.execs


def test_original_pinned_probe_real_loopback_HTTP_has_three_closed_requests_and_no_raw_save():
    environment,world,values=inputs('unready');received=[]
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def answer(self):
            payload=None
            if self.command=='POST':payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.command,self.path,payload))
            raw=(json.dumps({'model':'fixture-model','choices':[{'message':{'content':PRIVATE}}]}).encode()
                 if self.command=='POST' else PRIVATE.encode())
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.send_header('Content-Type','application/json')
            self.send_header('Connection','close');self.end_headers();self.wfile.write(raw);self.wfile.flush()
        do_GET=answer;do_POST=answer
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        world.target['engine']['trusted_url']='http://127.0.0.1:'+str(server.server_address[1])
        values['readiness_target_sha256']=sha256_hex(canonical_json(world.target));world.real_probe=True
        transport=KubernetesWorkerReadinessHTTP(world.target,environment,runner=world.runner)
        packet=transport.sample(values,sample_id='d'*32);assert_safe(packet)
        assert packet['sampling_complete'] and all(r['http_reply_complete'] for r in packet['requests'])
        assert packet['requests'][1]['normal_shape_verified'] is True
        assert [(method,path) for method,path,_ in received]==[('GET','/health'),('POST','/v1/chat/completions'),('GET','/health')]
        assert received[1][2]['max_tokens']==1 and received[1][2]['messages']==[{'role':'user','content':'hello'}]
    finally:
        server.shutdown();server.server_close();thread.join(10)
    assert not thread.is_alive()
