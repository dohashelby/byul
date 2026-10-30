"""EXAMPLE 고정 HTTP/Pod stdout 수집 계약. 실제 Kubernetes·로그 기능 활성화=0."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from guard.collectors import kubernetes_log_minimization as collector
from guard.models import DataOrigin
from tests.builders import policy
from tests.test_kubernetes_request_binding import env,target
from tests.test_kubernetes_lifecycle import World as MetadataWorld,PRIVATE as POD_PRIVATE

PRIVATE='PRIVATE_LOG_CREDENTIAL_FIELD_DO_NOT_STORE'
VALUES={'allowed_log_fields':['request_id','message','exception_dump'],
    'forbidden_log_fields':[],'forbidden_marker_contexts':['error_input'],
    'required_sources':['engine-stdout'],'required_branches':['normal','error','exception_dump']}


@pytest.fixture
def context(tmp_path):
    environment=env();destination=target(environment)
    lifecycle={'schema':'guard-kubernetes-lifecycle-target/1','lab':destination['lab'],
        'run_id':destination['kubernetes']['run_id'],'environment_ref':environment.ref().model_dump(mode='json'),
        'kubernetes':{'context':destination['kubernetes']['context'],'engine':deepcopy(destination['kubernetes']['engine'])}}
    return SimpleNamespace(env=environment,target=destination,lifecycle=lifecycle,ref=policy().ref(),
        work=tmp_path/'new'/'work',values=deepcopy(VALUES))


class World:
    def __init__(self,c,mode=None):
        self.c,self.mode=c,mode;self.metadata=MetadataWorld(SimpleNamespace(env=c.env),c.lifecycle)
        self.requests=[];self.cli=[];self.reads=0;self.meta=0;self.transport=None;self.scratch=[]
    def factory(self,target,environment,**kwargs):
        assert kwargs['profile']==collector.response_adapter.VALIDATION_ERROR_PROFILE
        assert environment.model.id!='fixture-model'
        world=self
        class Transport:
            profile=kwargs['profile'];target_sha256=collector._digest(target);api_model='fixture-model'
            binding=deepcopy(target['kubernetes']);endpoint=urlsplit(target['engine']['trusted_url'])
            def __call__(self,network,requests,timeout):
                assert network=='kubernetes-probe' and len(requests)==1 and 0<timeout<=5
                request=deepcopy(requests[0]);world.requests.append(request)
                case={label:key for key,label in collector.response_adapter.CASE_IDS.items()}[request['id']]
                if world.mode=='late-http' and case=='after':raise OSError(PRIVATE)
                assert request['body']['model']==self.api_model
                rid=request['headers']['X-Request-Id'];nonce=rid.split('-')[-2]
                assert len(nonce)==32 and rid=='byul-req13-error-'+nonce+'-'+case
                if case=='primary':
                    assert request['body']=={'model':self.api_model,'messages':[{'role':'user','content':'hello'}],
                        'max_tokens':'b2error-'+nonce[:12],'stream':False}
                    body='{}';outcome=400
                else:
                    assert request['body']=={'model':self.api_model,'prompt':'hello','add_special_tokens':False,'return_token_strs':False}
                    body=json.dumps({'tokens':[3,4],'count':2,'max_model_len':16});outcome=200
                if world.mode=='normal-incomplete' and case=='before':body='{}'
                response={'id':request['id'],'outcome':outcome,'body':body,'body_bytes':len(body.encode()),
                    'capture_complete':True,'body_truncated':False,'utf8_valid':True,'eof':True}
                self.last=response
                return [response]
            def report(self):
                rows=[{'id':request['id'],'outcome':400 if request['id'].endswith('primary') else 200,
                       'binding_verified_before_after':True} for request in world.requests]
                if world.mode=='request-binding' and rows:rows[-1]['binding_verified_before_after']=False
                return {'profile':self.profile,'binding_sha256':collector._digest(self.binding),'requests':rows,
                    'cleanup_errors':[PRIVATE] if world.mode=='cleanup' else [],
                    'collection_gap':world.mode=='cleanup'}
        transport=Transport();self.transport=transport
        if self.mode=='alias':transport.api_model='other-alias'
        return transport
    def runner(self,argv,**kwargs):
        self.cli.append(deepcopy(argv))
        assert argv[1].startswith('--cache-dir=') and 0<kwargs['timeout']<=5
        scratch=Path(kwargs['env_extra']['TMPDIR']);self.scratch.append(scratch)
        assert scratch.is_dir() and scratch.parent==self.c.work
        assert not {'apply','delete','create','patch','exec'}.intersection(argv)
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False,
            'pipes_closed':True,'read_errors':[]}
        if 'get' in argv:
            self.meta+=1
            if self.mode=='source-generation' and self.reads:self.metadata.bad_owner=True
            if self.mode=='late-generation' and self.reads and any(r['id'].endswith('after') for r in self.requests):
                self.metadata.bad_owner=True
            if self.mode=='initial-generation':self.metadata.bad_owner=True
            value=self.metadata.runner([argv[0],*argv[2:]],**kwargs)
            value['capture'].update(pipes_closed=True,read_errors=[])
            return value
        assert 'logs' in argv and '--timestamps=false' in argv and '--tail=128' in argv and '--limit-bytes=65536' in argv
        assert '--namespace='+self.c.target['kubernetes']['engine']['namespace'] in argv
        assert '--container='+self.c.target['kubernetes']['engine']['container'] in argv
        assert any(a.startswith('--since-time=') for a in argv)
        assert not any(r['id'].endswith('after') for r in self.requests)
        self.reads+=1
        normal=self.requests[0]['headers']['X-Request-Id'];error=self.requests[1]['headers']['X-Request-Id']
        marker=self.requests[1]['body']['max_tokens']
        values=[{'request_id':normal,'message':'hello'},
                {'request_id':error,'message':marker,'exception_dump':'harmless diagnostic','unexpected_'+PRIVATE:PRIVATE}]
        if self.mode=='no-violation':values[1]={'request_id':error,'message':'safe'}
        if self.mode=='no-dump':values[1].pop('exception_dump')
        if self.mode=='unrelated':values[1]['request_id']='unrelated-'+error
        if self.mode=='id-in-trace':values[1].pop('request_id');values[1]['exception_dump']=error+' '+marker
        if self.mode=='quoted-id':values[1]={'literal.request_id':error,'message':marker}
        if self.mode=='duplicate-json':raw=('{"request_id":"unrelated","request_id":'+json.dumps(error)+',"message":'+json.dumps(marker)+'}\n').encode()
        elif self.mode=='plain':raw=(PRIVATE+' '+error+' '+marker).encode()
        elif self.mode=='stderr-only':raw=b''
        else:raw=('\n'.join(json.dumps(row) for row in values)+'\n').encode()
        if self.mode=='cap-prefix':
            raw+=b' '*(collector.MAX_LOG_BYTES-len(raw))
        if self.mode=='oversize':raw=b'x'*(collector.MAX_LOG_BYTES+1)
        if self.mode=='unbounded':pipe['bounded_at_source']=False
        if self.mode=='timeout':pipe.update(complete=False,timed_out=True)
        if self.mode=='reap':pipe.update(complete=False,pipes_closed=False,read_errors=['child_reap'])
        return {'returncode':1 if self.mode=='denied' else 0,'stdout_bytes':raw,
            'stderr_bytes':json.dumps(values[1]).encode(),'capture':pipe}


def run(c,mode=None,**changes):
    world=World(c,mode)
    value=collector.capture_log_minimization(c.env,c.target,c.values,source_id='engine-stdout',
        expected_policy_ref=c.ref,workdir=c.work,_runner=world.runner,_transport_factory=world.factory,**changes)
    return value,world


def test_closed_public_http_alias_metadata_real_stdout_predicates_and_privacy(context):
    c=context;value,world=run(c)
    assert [row['case_id'] for row in value['request_summaries']]==['before','primary','after']
    assert all(row['attempted'] and row['binding_verified'] for row in value['request_summaries'])
    assert len(world.requests)==3 and world.reads==1
    assert value['source_kind']=='kubernetes_pod_logs'
    assert value['analysis_binding_verified'] and value['source_generation_verified']
    assert value['field_violation_count']==1 and value['forbidden_error_input_count']==1
    assert value['violation_codes']==['FIELD_POLICY_VIOLATION','FORBIDDEN_ERROR_INPUT']
    assert value['marker_contexts_seen']==['error_input'] and value['exception_dump_verified'] is True
    assert value['branches_tested']==['error','exception_dump','normal']
    assert value['normal_control']=={'attempted':True,'succeeded':True}
    assert value['coverage_complete'] is False and value['model_inference_verified'] is False
    assert value['log_fields_seen'] is None
    assert value['policy_ref']==c.ref.model_dump(mode='json') and value['policy_values_sha256']==collector._digest(c.values)
    text=json.dumps(value)
    for secret in (PRIVATE,POD_PRIVATE,hashlib.sha256(PRIVATE.encode()).hexdigest(),
        world.requests[1]['body']['max_tokens'],world.requests[1]['headers']['X-Request-Id'],'fixture-model',c.env.model.id,'GF_'):
        assert secret not in text
    assert 'predicate_projection_verified' not in value
    assert list(c.work.iterdir())==[] and all(not path.exists() for path in world.scratch)


@pytest.mark.parametrize('mode',['unrelated','id-in-trace','quoted-id','duplicate-json','plain','stderr-only','source-generation','denied','oversize','unbounded','request-binding','alias','initial-generation'])
def test_unqualified_source_request_or_event_cannot_create_violation(context,mode):
    value,world=run(context,mode)
    assert value['violation_codes']==[] and value['field_violation_count']==value['forbidden_error_input_count']==0
    assert value['exception_dump_verified'] is None
    assert value['coverage_complete'] is False
    if mode in {'plain','stderr-only','duplicate-json','source-generation','denied','oversize','unbounded','request-binding','alias','initial-generation'}:
        assert value['analysis_binding_verified'] is False
    if mode=='alias':assert world.requests==[] and world.reads==0


@pytest.mark.parametrize('mode',['late-http','late-generation','cleanup','cap-prefix','timeout','reap'])
def test_already_qualified_predicates_survive_late_or_partial_collection_failures(context,mode):
    value,_=run(context,mode)
    assert value['field_violation_count']==value['forbidden_error_input_count']==1
    assert value['analysis_binding_verified'] and value['source_generation_verified']
    assert value['coverage_complete'] is False
    if mode in {'cleanup','reap'}:assert value['cleanup']['complete'] is False
    if mode=='late-http':assert value['normal_control']['succeeded'] is None
    if mode in {'cap-prefix','timeout','reap'}:assert value['source_reads'][0]['complete'] is False


def test_http400_without_actual_dump_field_leaves_dump_unknown(context):
    value,_=run(context,'no-dump')
    assert value['request_summaries'][1]['outcome']==400
    assert value['exception_dump_verified'] is None
    assert 'EXCEPTION_DUMP_NOT_OBSERVED' in value['gaps']
    assert value['forbidden_error_input_count']==1


def test_empty_forbidden_lists_and_uncollected_policy_sources_keep_original_scope(context):
    context.values.update(forbidden_log_fields=[],forbidden_marker_contexts=[],required_sources=['engine-stdout','collector-copy'])
    value,_=run(context,'no-violation')
    assert value['field_violation_count']==value['forbidden_error_input_count']==0
    assert 'OTHER_SOURCES_NOT_COLLECTED' in value['gaps']
    assert value['coverage_complete'] is False


@pytest.mark.parametrize('key',list(VALUES))
@pytest.mark.parametrize('invalid',[None,'field',[''],['field','field']])
def test_policy_missing_types_blank_and_duplicates_rejected_before_io(context,key,invalid):
    context.values[key]=invalid
    with pytest.raises(ValueError):run(context)
    assert not context.work.exists()


@pytest.mark.parametrize('key',list(VALUES))
def test_missing_original_policy_keys_rejected_before_io(context,key):
    context.values.pop(key)
    with pytest.raises(ValueError):run(context)
    assert not context.work.exists()


@pytest.mark.parametrize('change',['branches','marker','source','ref','env','model','lab'])
def test_invalid_scope_refs_mapping_and_target_rejected_before_io(context,change):
    c=context
    kwargs={}
    if change=='branches':c.values['required_branches']=['normal','error']
    elif change=='marker':c.values['forbidden_marker_contexts']=['other']
    elif change=='source':c.values['required_sources']=['other']
    elif change=='ref':c.ref=c.ref.model_copy(update={'kind':'ENV'})
    elif change=='env':c.target['environment_ref']['sha256']='0'*64
    elif change=='model':c.target['model_mapping']['api_model']='--flag'
    else:c.target['lab']=False
    with pytest.raises(ValueError):run(c,**kwargs)
    assert not c.work.exists()


@pytest.mark.parametrize('hook',['_runner','_transport_factory','_reader_factory'])
def test_injected_hooks_cannot_enter_declared_environment(context,hook):
    c=context;environment=c.env.model_copy(update={'data_origin':DataOrigin.DECLARED}).seal()
    destination=target(environment)
    with pytest.raises(ValueError,match='INJECTION_REQUIRES_EXAMPLE'):
        collector.capture_log_minimization(environment,destination,c.values,source_id='engine-stdout',
            expected_policy_ref=c.ref,workdir=c.work,**{hook:lambda *a,**k:None})
    assert not c.work.exists()


def test_current_unintegrated_a_source_is_zero_requests_and_zero_log_reads(context,monkeypatch):
    c=context
    def forbidden(*args,**kwargs):raise AssertionError('실제 요청은 금지')
    monkeypatch.setattr(collector.KubernetesLifecycleReader,'snapshot',forbidden)
    monkeypatch.setattr(collector.kubernetes_http.KubernetesHTTP,'__call__',forbidden)
    value=collector.capture_log_minimization(c.env,c.target,c.values,source_id='engine-stdout',expected_policy_ref=c.ref,workdir=c.work)
    assert not any(row['attempted'] for row in value['request_summaries'])
    assert value['source_reads']==[] and value['violation_codes']==[]
    assert 'A_CAPABILITY_UNAVAILABLE' in value['gaps']
    assert not c.work.exists()


def test_unconfirmed_before_control_prevents_primary_and_log_read(context):
    value,world=run(context,'normal-incomplete')
    assert len(world.requests)==2 and not any(request['id'].endswith('primary') for request in world.requests)
    assert world.reads==0 and value['source_reads']==[]
    assert value['violation_codes']==[] and value['coverage_complete'] is False


def test_owned_cache_cleanup_failure_keeps_already_verified_predicates(context,monkeypatch):
    original=collector.tempfile.TemporaryDirectory
    def temporary(*args,**kwargs):
        owned=original(*args,**kwargs);cleanup=owned.cleanup
        def failed():cleanup();raise OSError(PRIVATE)
        owned.cleanup=failed
        return owned
    monkeypatch.setattr(collector.tempfile,'TemporaryDirectory',temporary)
    value,_=run(context)
    assert value['field_violation_count']==value['forbidden_error_input_count']==1
    assert value['analysis_binding_verified'] and value['source_generation_verified']
    assert value['cleanup']['complete'] is False and 'CLEANUP_UNVERIFIED' in value['gaps']
    assert list(context.work.iterdir())==[]
