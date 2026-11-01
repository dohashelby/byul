"""EXAMPLE CLI/metadata doubles and owned local-child tests; actual Kubernetes=0."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from guard.executors import kubernetes_record_access as capture
from guard.models import DataOrigin
from guard.runtime.bounded import capture_pipes
from tests.builders import policy
from tests.test_kubernetes_request_binding import env, target as http_target
from tests.test_kubernetes_lifecycle import World as MetadataWorld, PRIVATE as POD_PRIVATE

PRIVATE='PRIVATE_LOG_TOKEN_IDENTITY_DO_NOT_PERSIST'


@pytest.fixture
def context(tmp_path):
    environment=env();http=http_target(environment)
    target={'schema':'guard-kubernetes-lifecycle-target/1','lab':True,'run_id':http['kubernetes']['run_id'],
        'environment_ref':environment.ref().model_dump(mode='json'),
        'kubernetes':{'context':http['kubernetes']['context'],'engine':deepcopy(http['kubernetes']['engine'])}}
    ref=policy().ref()
    subjects={'schema':'guard-record-access-subjects/1','environment_ref':environment.ref().model_dump(mode='json'),
        'target_sha256':capture._digest(target),'policy_ref':ref.model_dump(mode='json'),
        'subjects':[{'role':'authorized_reader','username':'example-allowed','groups':['system:authenticated']},
                    {'role':'unauthorized_reader','username':'example-unallowed','groups':['system:authenticated']}]}
    return SimpleNamespace(env=environment,target=target,ref=ref,subjects=subjects,work=tmp_path/'new'/'work')


class World:
    def __init__(self,c,mode=None):
        self.c,self.mode=c,mode
        self.metadata=MetadataWorld(SimpleNamespace(env=c.env),c.target)
        self.calls=[];self.meta_calls=0;self.who_calls=0;self.logs_calls=0;self.scratch=[]
    def runner(self,argv,**kwargs):
        self.calls.append(deepcopy(argv))
        assert argv[0]=='kubectl' and argv[1].startswith('--cache-dir=')
        root=Path(kwargs['env_extra']['TMPDIR']);self.scratch.append(root)
        assert root.is_dir() and root.parent==self.c.work
        assert root.stat().st_mode & 0o777==0o700
        assert kwargs['env_extra']=={'TMPDIR':str(root),'TMP':str(root),'TEMP':str(root)}
        assert 0<kwargs['timeout']<=5 and kwargs['max_stderr_bytes']==4096
        forbidden={'apply','delete','patch','create','replace','exec','can-i'}
        assert not forbidden.intersection(argv)
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False,
              'pipes_closed':True,'read_errors':[]}
        if 'get' in argv:
            self.meta_calls+=1
            if self.mode=='initial-binding' or self.mode=='late-binding' and self.meta_calls>=9:
                self.metadata.bad_owner=True
            if self.mode=='primary-after-binding' and self.meta_calls>=7:self.metadata.bad_owner=True
            if self.mode=='initial-binding':self.metadata.mode='initial-owner'
            result=self.metadata.runner([argv[0],*argv[2:]],**kwargs)
            result['capture'].update(pipes_closed=True,read_errors=[])
            return result
        user=next(v.split('=',1)[1] for v in argv if v.startswith('--as='))
        groups=[v.split('=',1)[1] for v in argv if v.startswith('--as-group=')]
        assert '--context='+self.c.target['kubernetes']['context'] in argv
        if 'whoami' in argv:
            self.who_calls+=1
            info={'username':user,'groups':groups}
            if self.mode=='identity-before' and self.who_calls==3:info['username']='other'
            if self.mode=='identity-after' and self.who_calls==4:info['groups']=['other']
            raw=json.dumps({'apiVersion':'authentication.k8s.io/v1','kind':'SelfSubjectReview',
                'status':{'userInfo':info},'private':PRIVATE}).encode()
            if self.mode=='identity-missing':raw=b'{}'
            return {'returncode':0,'stdout_bytes':raw,'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        assert 'logs' in argv
        self.logs_calls+=1
        engine=self.c.target['kubernetes']['engine']
        assert '--namespace='+engine['namespace'] in argv and engine['pod'] in argv
        assert '--container='+engine['container'] in argv and '--tail=1' in argv and '--limit-bytes=4096' in argv
        raw=PRIVATE.encode();rc=0
        if user=='example-unallowed':
            if self.mode=='empty':raw=b''
            if self.mode in {'denied','auth-error'}:rc=1
            if self.mode=='truncated':pipe.update(complete=False,truncated=True)
            if self.mode=='timeout':pipe.update(complete=False,timed_out=True)
            if self.mode=='reap':pipe.update(complete=False,pipes_closed=False,read_errors=['child_reap'])
            if self.mode=='oversize':raw=b'x'*4097
            if self.mode=='local-child-timeout':
                return capture_pipes([sys.executable,'-c','import time;time.sleep(2)'],timeout=.03,
                    env_extra=kwargs['env_extra'],max_stdout_bytes=4096,max_stderr_bytes=4096)
        if self.mode=='late-log' and self.logs_calls==3:raise OSError(PRIVATE)
        return {'returncode':rc,'stdout_bytes':raw,'stderr_bytes':PRIVATE.encode(),'capture':pipe}


def run(c,mode=None,**kwargs):
    world=World(c,mode)
    result=capture.capture_record_access(c.env,c.target,c.subjects,expected_policy_ref=c.ref,
        workdir=c.work,_runner=world.runner,**kwargs)
    return result,world


def test_actual_public_reader_identity_and_logs_three_cases_are_private_and_owned(context):
    c=context;result,world=run(c)
    assert [row['case_id'] for row in result['rows']]==['allowed-before','unauthorized','allowed-after']
    assert all(row['attempted'] and row['identity_verified'] and row['binding_verified'] and row['read_verified'] is True for row in result['rows'])
    assert world.logs_calls==3 and world.who_calls==6 and world.meta_calls==12
    assert result['normal_control']=={'attempted':True,'succeeded':True}
    assert result['unauthorized_read_verified'] is True
    assert result['cleanup']=={'complete':True,'collection_gap':False}
    assert result['data_origin']=='EXAMPLE' and result['policy_ref']==c.ref.model_dump(mode='json')
    assert result['subject_plan_sha256']==capture._digest(c.subjects)
    assert all(row['byte_count']==len(PRIVATE) and row['content_sha256'] is None for row in result['rows'])
    raw=json.dumps(result)
    for value in (PRIVATE,POD_PRIVATE,'example-allowed','example-unallowed','system:authenticated',hashlib.sha256(PRIVATE.encode()).hexdigest()):
        assert value not in raw
    assert c.work.is_dir() and list(c.work.iterdir())==[]
    assert all(not path.exists() for path in world.scratch)


@pytest.mark.parametrize('mode',['empty','denied','auth-error','truncated','timeout','reap','oversize','identity-before','identity-after','primary-after-binding','local-child-timeout'])
def test_incomplete_unqualified_or_client_denial_is_unknown(context,mode):
    result,world=run(context,mode)
    assert result['unauthorized_read_verified'] is None
    assert result['rows'][1]['read_verified'] is None
    assert result['rows'][1]['gaps']
    if mode=='identity-before':assert result['rows'][1]['attempted'] is False and world.logs_calls==2
    if mode in {'denied','auth-error'}:assert result['rows'][1]['returncode']==1
    if mode=='reap':assert result['cleanup']['complete'] is False
    if mode=='local-child-timeout':assert result['cleanup']['complete'] is True
    assert list(context.work.iterdir())==[]


@pytest.mark.parametrize('mode',['late-binding','late-log'])
def test_confirmed_unauthorized_read_survives_later_binding_or_child_errors(context,mode):
    result,_=run(context,mode)
    assert result['unauthorized_read_verified'] is True and result['rows'][1]['read_verified'] is True
    assert result['normal_control']['succeeded'] is None and result['cleanup']['collection_gap'] is True
    assert result['rows'][2]['attempted'] is (mode=='late-log')
    if mode=='late-log':assert result['cleanup']['complete'] is False


def test_initial_binding_failure_does_not_attempt_identity_or_log_reads(context):
    result,world=run(context,'initial-binding')
    assert world.logs_calls==world.who_calls==0
    assert all(not row['attempted'] and row['read_verified'] is None for row in result['rows'])
    assert result['normal_control']=={'attempted':False,'succeeded':None}


def test_missing_identity_prevents_actual_log_read(context):
    result,world=run(context,'identity-missing')
    assert world.logs_calls==0 and result['unauthorized_read_verified'] is None
    assert all(not row['attempted'] for row in result['rows'])


def test_pod_named_logs_with_failed_identity_never_marks_log_attempt(context):
    c=context;c.target['kubernetes']['engine']['pod']='logs'
    c.subjects['target_sha256']=capture._digest(c.target)
    result,world=run(c,'identity-missing')
    assert any('logs' in argv and 'get' in argv for argv in world.calls)
    assert world.logs_calls==0
    assert all(not row['attempted'] for row in result['rows'])
    assert result['normal_control']=={'attempted':False,'succeeded':None}
    assert result['unauthorized_read_verified'] is None


@pytest.mark.parametrize('mutation',['none','empty','extra','env','target','policy','same-user','same-role','missing-groups','empty-groups','none-groups','duplicate-groups','newline-user','flag-user','space-group','token','too-many'])
def test_invalid_subject_plans_raise_before_files_or_runner_io(context,mutation):
    c=context;plan=deepcopy(c.subjects)
    if mutation=='none':plan=None
    elif mutation=='empty':plan={}
    elif mutation=='extra':plan['verified']=True
    elif mutation=='env':plan['environment_ref']['sha256']='0'*64
    elif mutation=='target':plan['target_sha256']='0'*64
    elif mutation=='policy':plan['policy_ref']['sha256']='0'*64
    elif mutation=='same-user':plan['subjects'][1]['username']=plan['subjects'][0]['username']
    elif mutation=='same-role':plan['subjects'][1]['role']='authorized_reader'
    elif mutation=='missing-groups':plan['subjects'][0].pop('groups')
    elif mutation=='empty-groups':plan['subjects'][0]['groups']=[]
    elif mutation=='none-groups':plan['subjects'][0]['groups']=None
    elif mutation=='duplicate-groups':plan['subjects'][0]['groups']=['a','a']
    elif mutation=='newline-user':plan['subjects'][0]['username']='alice\nadmin'
    elif mutation=='flag-user':plan['subjects'][0]['username']='--token=private'
    elif mutation=='space-group':plan['subjects'][0]['groups']=['with space']
    elif mutation=='token':plan['subjects'][0]['token']=PRIVATE
    else:plan['subjects'].append(deepcopy(plan['subjects'][0]))
    def forbidden(*args,**kwargs):raise AssertionError('no I/O')
    with pytest.raises(ValueError):capture.capture_record_access(c.env,c.target,plan,
        expected_policy_ref=c.ref,workdir=c.work,_runner=forbidden)
    assert not c.work.exists()


@pytest.mark.parametrize('mutation',['lab','environment','digest','namespace','container','restart'])
def test_selected_target_substitution_is_rejected_or_unqualified(context,mutation):
    c=context
    if mutation=='lab':c.target['lab']=False
    elif mutation=='environment':c.target['environment_ref']['sha256']='0'*64
    elif mutation=='digest':c.target['kubernetes']['engine']['image']='bad'
    elif mutation=='namespace':c.target['kubernetes']['engine']['namespace']='other'
    elif mutation=='container':c.target['kubernetes']['engine']['container']='other'
    else:c.target['kubernetes']['engine']['restart_count']+=1
    # The original approved subject plan stays bound to the original target.
    with pytest.raises(ValueError):run(c)
    assert not c.work.exists()


@pytest.mark.parametrize('hook',['runner','reader'])
def test_injected_hooks_are_example_only(context,hook):
    c=context;c.env=c.env.model_copy(update={'data_origin':DataOrigin.DECLARED}).seal()
    c.target['environment_ref']=c.env.ref().model_dump(mode='json')
    c.subjects.update(environment_ref=c.env.ref().model_dump(mode='json'),target_sha256=capture._digest(c.target))
    kwargs={'_runner' if hook=='runner' else '_reader_factory':lambda *a,**k:None}
    with pytest.raises(ValueError,match='INJECTION_REQUIRES_EXAMPLE'):
        capture.capture_record_access(c.env,c.target,c.subjects,expected_policy_ref=c.ref,workdir=c.work,**kwargs)
    assert not c.work.exists()


@pytest.mark.parametrize('key,value',[('duration',0),('duration',float('inf')),('timeout',True),('timeout',6),('binary','--token=x'),('binary','kubectl\nget')])
def test_invalid_local_configuration_is_rejected_before_io(context,key,value):
    with pytest.raises(ValueError):run(context,**{key:value})
    assert not context.work.exists()


def test_cleanup_failure_preserves_qualified_unauthorized_read(context,monkeypatch):
    original=capture.tempfile.TemporaryDirectory
    def temporary(*args,**kwargs):
        owned=original(*args,**kwargs)
        def cleanup():
            original_cleanup();raise OSError(PRIVATE)
        original_cleanup=owned.cleanup
        owned.cleanup=cleanup
        return owned
    monkeypatch.setattr(capture.tempfile,'TemporaryDirectory',temporary)
    result,_=run(context)
    assert result['unauthorized_read_verified'] is True
    assert result['cleanup']=={'complete':False,'collection_gap':True}
    assert result['gaps']==['ACCESS_CLEANUP_UNVERIFIED']
    assert list(context.work.iterdir())==[]


def test_total_budget_expiry_returns_partial_unknown_without_dispatch(context,monkeypatch):
    values=iter([0,2,2,2,2])
    monkeypatch.setattr(capture,'time',SimpleNamespace(monotonic=lambda:next(values,2)))
    result,world=run(context,duration=1)
    assert not world.calls and result['unauthorized_read_verified'] is None
    assert result['gaps']==['ACCESS_BUDGET_EXHAUSTED']
    assert not any(row['attempted'] for row in result['rows'])
    assert list(context.work.iterdir())==[]
